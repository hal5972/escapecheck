"""地理情報の取得（インターフェース仕様。実装は A のセッション）。

浸水深（国土数値情報 A40 を土佐市の周辺で切り出したもの）、津波到達時間（土佐市・高知県の予測の表）、
津波の指定緊急避難場所（国土地理院）、標高（国土地理院の標高 API。結果を保存して再利用する）、距離（直線距離 × 1.3 の概算）。
すべての値に出典 ID を付け、計画の出典リストに登録する。

MCP ツールは座標を引数に取らない。target（"home" や避難支援者の supporter_id）を受け取り、計画の中の座標を参照する。
設計は docs/03-design.md の 4 節と 6.3 節。

出典 ID は値ごとに決まった形にする（同じ地点・同じ避難先なら同じ ID。登録し直すと上書きする）:
- data_arrival_<area_id>（到達時間の表の1行）、data_shelter_<shelter_id>（指定緊急避難場所）
- data_a40_<target>（target の浸水深の階級）、data_elev_<target>（target の標高）
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tools import datasets, geo
from tools.plan_model import Facts, Plan, Point, Shelter, Source

#: 道なりの距離の概算に使う係数（仮定。画面に「概算」と表示する）
ROUTE_FACTOR = 1.3

#: 到達時間の表で「近い地点」とみなす距離（これより遠ければ、沿岸で一番短い値を使う。設計の 13 節）
NEAREST_MAX_M = 2000.0

ELEVATION_URL = "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php"
ELEVATION_TIMEOUT_S = 5.0
#: 標高 API の結果を保存するファイル（data/raw/ は git の対象外。座標を含むためリポジトリに入れない）
ELEVATION_CACHE_PATH: Path | None = datasets.DATA_DIR / "raw" / "elevation_cache.json"

A40_URL = "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A40.html"
OUTSIDE = "区域外"


# ---------------------------------------------------------------------------
# 位置
# ---------------------------------------------------------------------------


def _point(value: object) -> tuple[float, float] | None:
    if isinstance(value, dict) and isinstance(value.get("lat"), (int, float)) and isinstance(value.get("lon"), (int, float)):
        return (float(value["lat"]), float(value["lon"]))
    return None


def home_point(plan: Plan) -> tuple[float, float] | None:
    return _point(plan.get("home"))


def supporter_point(supporter: dict) -> tuple[float, float] | None:
    return _point(supporter.get("location"))


def target_point(plan: Plan, target: str) -> tuple[tuple[float, float] | None, str | None]:
    """target（"home" または supporter_id）の座標。無ければ (None, 理由)。"""
    if target == "home":
        point = home_point(plan)
        return (point, None if point else "自宅の場所が未確認です（地図で指定してください）")
    for supporter in plan.get("supporters", []):
        if supporter.get("supporter_id") == target:
            point = supporter_point(supporter)
            alias = supporter.get("alias", target)
            return (point, None if point else f"{alias}の自宅の場所が未確認です")
    return (None, f"対象が計画に見つかりません: {target}")


# ---------------------------------------------------------------------------
# 津波到達時間
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArrivalPick:
    """ある地点で使う津波到達時間。"""

    minutes: float
    area: datasets.ArrivalArea
    #: 版ごとの一番近い地点（短いほうを選ぶ前の候補）
    candidates: tuple[datasets.ArrivalArea, ...]
    #: 近い地点が無く、沿岸で一番短い値を使ったか
    fallback: bool
    #: S6（沿岸で一番短い値）として選んだか
    earliest: bool


def arrival_at(point: tuple[float, float], data: datasets.Dataset, earliest: bool = False) -> ArrivalPick | None:
    """自宅の位置から版ごとに一番近い地点の値を取り、そのうち短いほうを使う。表が空なら None。

    earliest が True のとき（S6）、表のうち土佐市の沿岸で一番短い値を使う（安全側の値）。
    """
    areas = data.confirmed_areas()
    if not areas:
        return None
    shortest = min(areas, key=lambda a: (a.minutes_30cm, a.area_id))
    if earliest:
        return ArrivalPick(shortest.minutes_30cm, shortest, (shortest,), fallback=False, earliest=True)
    nearest_by_version: dict[str, tuple[float, datasets.ArrivalArea]] = {}
    for area in areas:
        d = geo.distance_m(point, area.point)
        if d > NEAREST_MAX_M:
            continue
        best = nearest_by_version.get(area.version)
        if best is None or (d, area.area_id) < (best[0], best[1].area_id):
            nearest_by_version[area.version] = (d, area)
    if not nearest_by_version:
        return ArrivalPick(shortest.minutes_30cm, shortest, (shortest,), fallback=True, earliest=False)
    candidates = tuple(sorted((a for _, a in nearest_by_version.values()), key=lambda a: (a.minutes_30cm, a.version)))
    chosen = candidates[0]
    return ArrivalPick(chosen.minutes_30cm, chosen, candidates, fallback=False, earliest=False)


def arrival_source_id(area: datasets.ArrivalArea) -> str:
    return f"data_arrival_{area.area_id}"


def register_arrival_source(plan: Plan, area: datasets.ArrivalArea) -> str:
    sid = arrival_source_id(area)
    page = f"、{area.page}ページ" if area.page not in (None, "") else ""
    plan["sources"][sid] = Source(
        kind="dataset",
        label=f"津波到達時間（{area.label}。{area.version}{page}）",
        name=area.source_name or "津波浸水予測時間図",
        version=area.version,
        url=area.source_url,
        value=area.minutes_30cm,
        unit="分",
    )
    return sid


def arrival_notes(pick: ArrivalPick) -> list[str]:
    notes = []
    if pick.fallback:
        notes.append("近くに到達時間の表の地点が無いため、土佐市の沿岸で一番短い値を使いました")
    if pick.earliest:
        notes.append("土佐市の沿岸で一番短い到達時間を使いました（安全側の値）")
    if len(pick.candidates) > 1:
        values = "、".join(f"{a.version}: {_fmt(a.minutes_30cm)}分" for a in pick.candidates)
        notes.append(f"予測の版により到達時間が異なるため、短いほうを使いました（{values}）")
    return notes


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


# ---------------------------------------------------------------------------
# 指定緊急避難場所
# ---------------------------------------------------------------------------


def shelter_source_id(shelter_id: str) -> str:
    return f"data_shelter_{shelter_id}"


def register_shelter_source(plan: Plan, record: datasets.ShelterRecord, data: datasets.Dataset) -> str:
    sid = shelter_source_id(record.shelter_id)
    info = data.shelter_info
    plan["sources"][sid] = Source(
        kind="dataset",
        label=f"指定緊急避難場所: {record.name}（{'津波の対象' if record.tsunami_ok else '津波は対象外'}）",
        name=info.get("name", "国土地理院 指定緊急避難場所データ"),
        version=info.get("version", ""),
        url=info.get("url", ""),
        value=record.name,
    )
    return sid


def normalize_name(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"[\s・の]", "", text).lower()


_HOME_SHELTER = re.compile(r"自宅|自分の家|家の(2|二)階|^(2|二)階|屋根の上|うちの(2|二)階")


def is_home_shelter(text: str) -> bool:
    """「自宅の2階」など、自宅を避難先にしているか。"""
    return bool(_HOME_SHELTER.search(unicodedata.normalize("NFKC", str(text))))


def match_shelter(value: object, data: datasets.Dataset) -> tuple[str, datasets.ShelterRecord | None]:
    """計画の避難先の値を、データの避難場所に対応付ける。

    戻り値の状態: "ok"（津波の対象）、"not_tsunami"（データにあるが津波は対象外）、"home"（自宅）、"unknown"（データに無い）。
    """
    name = None
    if isinstance(value, dict):
        shelter_id = value.get("shelter_id")
        if shelter_id:
            record = data.shelter_by_id(str(shelter_id))
            if record:
                return ("ok" if record.tsunami_ok else "not_tsunami", record)
        name = value.get("name") or value.get("text")
    else:
        name = value
    if not name:
        return ("unknown", None)
    if is_home_shelter(str(name)):
        return ("home", None)
    target = normalize_name(str(name))
    exact = [s for s in data.shelters if normalize_name(s.name) == target]
    if not exact:
        # 計画の記載が施設名を含む（「宇佐津波避難タワーの上」）、または施設名の一部（1件に決まるときだけ）
        containing = [s for s in data.shelters if normalize_name(s.name) and normalize_name(s.name) in target]
        if containing:
            longest = max(len(normalize_name(s.name)) for s in containing)
            exact = [s for s in containing if len(normalize_name(s.name)) == longest]
        elif len(target) >= 3:
            exact = [s for s in data.shelters if target in normalize_name(s.name)]
    if len(exact) != 1:
        return ("unknown", None)
    record = exact[0]
    return ("ok" if record.tsunami_ok else "not_tsunami", record)


def shelter_kind_label(record: datasets.ShelterRecord) -> str:
    return record.kind or ("指定緊急避難場所（津波）" if record.tsunami_ok else "指定緊急避難場所（津波は対象外）")


def cannot_climb_notes(plan: Plan, record: datasets.ShelterRecord, data: datasets.Dataset) -> list[str]:
    """前提条件 CANNOT_CLIMB のうち、この避難先についての引用（地域の情報）。"""
    notes = []
    planned_status, planned = match_shelter((plan.get("fields", {}).get("shelter") or {}).get("value"), data)
    for premise in plan.get("premises", []):
        if premise.get("code") != "CANNOT_CLIMB":
            continue
        about = premise.get("about")
        if about:
            status, target = match_shelter({"shelter_id": about, "name": about}, data)
            applies = target is not None and target.shelter_id == record.shelter_id
        else:
            applies = planned is not None and planned.shelter_id == record.shelter_id
        if applies:
            notes.append(premise.get("quote", ""))
    return notes


# ---------------------------------------------------------------------------
# 浸水深・標高
# ---------------------------------------------------------------------------


def inundation_rank_at(point: tuple[float, float], data: datasets.Dataset) -> str | None:
    """浸水深の階級。区域外なら "区域外"、データが無ければ None。"""
    areas = data.get_inundation()
    if not areas:
        return None
    ranks = [a.rank for a in areas if a.contains(point)]
    if not ranks:
        return OUTSIDE
    # 重なりがあれば、深いほう（並びの後ろ）を返す
    return max(ranks, key=_rank_order)


def _rank_order(rank: str) -> float:
    numbers = re.findall(r"\d+(?:\.\d+)?", unicodedata.normalize("NFKC", rank))
    return float(numbers[0]) if numbers else 0.0


_elevation_cache: dict[str, tuple[float | None, str | None]] = {}
_elevation_lock = threading.Lock()


def clear_elevation_cache() -> None:
    with _elevation_lock:
        _elevation_cache.clear()


def _cache_key(lat: float, lon: float) -> str:
    return f"{lat:.5f},{lon:.5f}"


def _load_cache_file() -> None:
    if ELEVATION_CACHE_PATH is None or not ELEVATION_CACHE_PATH.is_file():
        return
    try:
        stored = json.loads(ELEVATION_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for key, value in stored.items():
        if isinstance(value, list) and len(value) == 2:
            _elevation_cache.setdefault(key, (value[0], value[1]))


def _save_cache_file() -> None:
    if ELEVATION_CACHE_PATH is None or not ELEVATION_CACHE_PATH.parent.is_dir():
        return
    try:
        tmp = ELEVATION_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: list(v) for k, v in _elevation_cache.items()}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ELEVATION_CACHE_PATH)
    except OSError:
        pass


def _request_elevation(lat: float, lon: float) -> tuple[float | None, str | None]:
    """国土地理院の標高 API を呼ぶ。標高が無い地点（海など）は (None, None)。失敗したら例外。"""
    query = urllib.parse.urlencode({"lon": f"{lon:.6f}", "lat": f"{lat:.6f}", "outtype": "JSON"})
    request = urllib.request.Request(f"{ELEVATION_URL}?{query}", headers={"User-Agent": "EscapeCheck/0.1"})
    with urllib.request.urlopen(request, timeout=ELEVATION_TIMEOUT_S) as response:
        payload = json.loads(response.read().decode("utf-8"))
    elevation = payload.get("elevation")
    if isinstance(elevation, (int, float)):
        return (float(elevation), str(payload.get("hsrc") or ""))
    return (None, None)


def elevation_at(lat: float, lon: float) -> tuple[float | None, str | None, str | None]:
    """標高 (m)、標高データの種類、取得できなかったときの注記。同じ地点では API を再度呼ばない。"""
    key = _cache_key(lat, lon)
    with _elevation_lock:
        if not _elevation_cache:
            _load_cache_file()
        if key in _elevation_cache:
            elevation, hsrc = _elevation_cache[key]
            return (elevation, hsrc, None if elevation is not None else "標高: この地点の値がありません")
    try:
        elevation, hsrc = _request_elevation(lat, lon)
    except Exception:  # 通信の失敗・タイムアウト・応答の形の変化は、すべて「取得できない」として扱う
        return (None, None, "標高: 取得できませんでした（国土地理院の標高 API）")
    with _elevation_lock:
        _elevation_cache[key] = (elevation, hsrc)
        _save_cache_file()
    return (elevation, hsrc, None if elevation is not None else "標高: この地点の値がありません")


# ---------------------------------------------------------------------------
# MCP ツールから呼ぶ関数（インターフェース仕様）
# ---------------------------------------------------------------------------


def get_facts(plan: Plan, target: str = "home") -> Facts:
    """target の地点の浸水深の階級・津波到達時間・標高を返す。出典を plan["sources"] に登録する。

    取得できない値は None にし、notes に理由を記載する（例: 標高 API が停止している）。例外は外に出さない。
    """
    data = datasets.current()
    facts = Facts(target=target, inundation_rank=None, arrival_min=None, arrival_version=None,
                  elevation_m=None, source_ids=[], notes=[])
    point, reason = target_point(plan, target)
    if point is None:
        facts["notes"].append(reason or "位置が未確認です")
        return facts

    try:
        rank = inundation_rank_at(point, data)
    except Exception:
        rank = None
    if rank is None:
        facts["notes"].append("浸水深: 津波浸水想定のデータを読み込めませんでした")
    else:
        facts["inundation_rank"] = rank
        sid = f"data_a40_{target}"
        info = data.inundation_info
        plan["sources"][sid] = Source(
            kind="dataset",
            label="津波浸水想定（国土数値情報 A40、高知県、平成28年）",
            name=info.get("name", "国土数値情報 津波浸水想定（A40）高知県"),
            version=info.get("version", "平成28年"),
            url=info.get("url", A40_URL),
            value=rank,
        )
        facts["source_ids"].append(sid)
        if rank == OUTSIDE:
            facts["notes"].append("浸水深: 平成28年の津波浸水想定では区域外です（令和7年の新しい予測は反映していません）")

    pick = arrival_at(point, data)
    if pick is None:
        facts["notes"].append("津波到達時間: 表に値がありません")
    else:
        facts["arrival_min"] = pick.minutes
        facts["arrival_version"] = pick.area.version
        facts["source_ids"].append(register_arrival_source(plan, pick.area))
        facts["notes"].extend(arrival_notes(pick))

    elevation, hsrc, note = elevation_at(*point)
    if elevation is not None:
        facts["elevation_m"] = elevation
        sid = f"data_elev_{target}"
        plan["sources"][sid] = Source(
            kind="dataset",
            label="標高（国土地理院 標高 API）",
            name="国土地理院 標高 API",
            version=hsrc or "",
            url="https://maps.gsi.go.jp/development/elevation_s.html",
            value=elevation,
            unit="m",
        )
        facts["source_ids"].append(sid)
    if note:
        facts["notes"].append(note)
    return facts


def find_shelters(plan: Plan, target: str = "home", limit: int = 3) -> list[Shelter]:
    """target から近い、津波の指定緊急避難場所を、近い順に limit 件返す。

    距離は直線距離 × ROUTE_FACTOR の概算（distance_method に記載）。地域の情報（前提条件の引用）を local_notes に入れる。
    limit は 1〜10 に丸める。位置が未確認なら空のリストを返す。
    結果の point（座標）はサーバーの中だけで使い、Claude に渡す前に取り除く。
    """
    data = datasets.current()
    point, _ = target_point(plan, target)
    if point is None:
        return []
    limit = max(1, min(int(limit), 10))
    candidates = sorted(
        (s for s in data.shelters if s.tsunami_ok),
        key=lambda s: (geo.distance_m(point, s.point), s.shelter_id),
    )[:limit]
    results: list[Shelter] = []
    for record in candidates:
        results.append(
            Shelter(
                shelter_id=record.shelter_id,
                name=record.name,
                tsunami_ok=record.tsunami_ok,
                kind=shelter_kind_label(record),
                distance_m=round(geo.distance_m(point, record.point) * ROUTE_FACTOR),
                distance_method="straight_x1.3",
                elevation_m=None,
                local_notes=cannot_climb_notes(plan, record, data),
                point=Point(lat=record.lat, lon=record.lon),
                source_id=register_shelter_source(plan, record, data),
            )
        )
    return results
