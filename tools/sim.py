"""避難が間に合うかの計算。

数値はすべてこのモジュールが算出する。Claude（レッドチームエージェント）は、どのシナリオを適用するかの選択と、結果の説明だけを担当する。
外部から数値を渡す引数は作らない（数値の決定を Claude に戻さないため）。

判定: 余裕時間 3分以上は「間に合う」、0〜3分は「余裕が少ない」、マイナスは「間に合わない」。

計算の決まり:
- 時間は 0.1分単位。移動時間は切り上げる（安全側）。所要時間は内訳の合計
- 本人の話の値（項目）があればそれを使い、無ければ仮定の値を使う。仮定は ASSUMPTIONS にあり、出典リストに「仮定」として登録する
- 項目の値の読み方（数字は半角・全角のみ。漢数字は読まない）:
  - time_to_leave_day / time_to_leave_night: 「10分くらい」→ 10分。「5〜10分」は長いほう
  - walk_speed: 「100mを3分」→ 0.56 m/秒。{"meters": 100, "minutes": 3} や数値（m/秒）も可
  - mobility: 「車いす」「介助」「一人では歩けない」「立ち上がれない」など → 1人では避難できない（避難支援者と一緒に 0.52 m/秒）。
    「杖」「歩行器」「足が悪い」など → 0.5 m/秒。「自力」「問題なし」など → 0.62 m/秒。それ以外・未確認 → 0.4 m/秒（控えめな値）
  - sleep_place: 「2階」を含む（「2階建て」は除く）→ 夜間 +3分
  - hearing_and_sight: 「耳が遠」「聞こえない」「難聴」など → 声をかける人が着いてから避難を開始する
  - health_for_evacuation: 「睡眠薬」など → 夜間 +5分
  - shelter: {"shelter_id": ..., "name": ...} または施設名。自宅・津波の対象外・データに無い場所は計算しない
  - route: {"points": [{"lat", "lon"}, ...]} なら折れ線の距離。それ以外は直線距離 × 1.3
- 前提条件（plan["premises"]）: premise_ids を省略すると登録済みの前提条件をすべて使う。[] なら使わない。
  S7 は登録済みの CANNOT_CLIMB を常に使う。try_fix は run_scenario(plan, scenario_id) と同じ条件に改善案を加える
- 承認済みの改善案（plan["approvals"] に fix_id がある plan["fixes"]）は、run_scenario と try_fix の両方に反映する
  （承認 → もう一度点検の流れ。CHANGE_SHELTER は項目の書き換えが無くても反映される）
- 項目の値は、聞き取りエージェントの形 {"text": ..., "minutes": ...}・{"text": ..., "meters": ..., "minutes": ...}・
  {"text": ..., "shelter_id": ...} も読む（数値が無ければ text を読む）
- S5 の need_min・margin_min・breakdown は、避難支援者の避難（支援者の自宅 → 本人の自宅 → 避難先）を表す
- 結果には TypedDict に無いキーを加える: avail_source_ids・avail_version（津波到達時間の出典と版）、
  shelter_id・shelter_name（計算に使った避難先）、supporter_alias（計算に使った避難支援者）
- 使った出典（仮定・データ）は、決まった ID で plan["sources"] に登録する（項目・避難支援者・前提条件は変更しない）
"""

from __future__ import annotations

import copy
import math
import re
import unicodedata
from dataclasses import dataclass, field

from tools import datasets, geo, hazard
from tools import plan_model as pm
from tools.plan_model import Breakdown, Finding, Plan, ScenarioResult, Source

#: 「間に合う」と「余裕が少ない」の境界（分）
MARGIN_OK_MIN = 3.0

# ---------------------------------------------------------------------------
# 仮定の値（出典リストに kind="assumption" で登録する）
# ---------------------------------------------------------------------------

MLIT_GUIDE = "国土交通省の資料（https://www.mlit.go.jp/common/000233464.pdf。06-facts-and-sources では「要約」）"


@dataclass(frozen=True)
class Assumption:
    source_id: str
    label: str
    value: float
    unit: str
    basis: str


ASSUMPTIONS: dict[str, Assumption] = {
    a.source_id: a
    for a in [
        Assumption("asm_leave_day", "仮定: 避難開始時間（日中）", 5, "分", f"{MLIT_GUIDE}の「避難を始めるまで 2〜5分」の長いほう"),
        Assumption("asm_leave_night", "仮定: 避難開始時間（夜間）", 10, "分", "日中の5分に、夜間の着替えと明かりの準備の5分を仮定で追加"),
        Assumption("asm_winter_leave", "仮定: 冬の夜の追加", 2, "分", "冬の夜に上着を着る時間などの仮定"),
        Assumption("asm_dark_speed", "仮定: 暗い夜道の歩行速度の倍率", 0.8, "倍", "停電で暗い夜道を歩く場合の仮定"),
        Assumption("asm_speed_independent", "仮定: 歩行速度（自力で歩ける）", 0.62, "m/秒", f"東日本大震災の避難の平均（時速 2.24km）。{MLIT_GUIDE}"),
        Assumption("asm_speed_cane", "仮定: 歩行速度（杖・歩行が困難）", 0.5, "m/秒", f"2005年のガイドラインの値。{MLIT_GUIDE}"),
        Assumption("asm_speed_unknown", "仮定: 歩行速度（聞き取れていない）", 0.4, "m/秒", f"一人暮らしの高齢者の控えめな値（豊橋市の例）。{MLIT_GUIDE}"),
        Assumption("asm_speed_with_aid", "仮定: 歩行速度（介助を受けて避難）", 0.52, "m/秒", f"東日本大震災で歩行が困難な人と一緒の避難（時速 1.88km）。{MLIT_GUIDE}"),
        Assumption("asm_speed_supporter", "仮定: 避難支援者の歩行速度", 1.0, "m/秒", f"歩行速度の目安。{MLIT_GUIDE}"),
        Assumption("asm_speed_supporter_elderly", "仮定: 避難支援者の歩行速度（60代以上・年代が未確認）", 0.62, "m/秒", f"東日本大震災の避難の平均。{MLIT_GUIDE}"),
        Assumption("asm_route_factor", "仮定: 道なりの距離（直線距離 × 1.3）", hazard.ROUTE_FACTOR, "倍", "道路のデータを使わない概算（画面に「概算」と表示する）"),
        Assumption("asm_detour_factor", "仮定: 避難経路の閉塞による迂回", 1.3, "倍", "道なりの距離にもう一度 1.3 を掛ける（シナリオ S3）"),
        Assumption("asm_climb", "仮定: 避難先で上がる時間", 2, "分", "タワー・建物の階段や高台の坂を上がる時間の仮定"),
        Assumption("asm_help", "仮定: 声をかけ、一緒に避難を開始するまで", 3, "分", "避難支援者が声をかけ、一緒に出発するまでの時間の仮定"),
        Assumption("asm_upstairs", "仮定: 2階から階段を下りる時間", 3, "分", "2階で就寝している場合に、階段を下りる時間の仮定"),
        Assumption("asm_deep_sleep", "仮定: 眠りが深く、気づくまでの時間", 5, "分", "服薬などで眠りが深い場合に、気づくまでの時間の仮定"),
        Assumption("asm_leave_floor", "仮定: 避難開始時間の下限", 2, "分", f"{MLIT_GUIDE}の「避難を始めるまで 2〜5分」の短いほう"),
        Assumption("asm_supporter_distance", "仮定: 避難支援者の自宅からの距離（場所が未確認）", 100, "m", "近所に住む前提の仮定。地図で指定すると置き換わる"),
        Assumption("asm_shelter_nearest", "仮定: 避難先が未確認のため、一番近い津波の避難場所で計算", 0, "", "指定緊急避難場所データのうち津波の対象で、一番近い場所"),
        Assumption("asm_fix_bedside", "仮定: 枕元の準備（靴・ライト・上着）で短縮", -3, "分", "改善案の効果の仮定。本人に「何分で出られそうか」を聞いて置き換える"),
        Assumption("asm_fix_sleep_near_exit", "仮定: 就寝場所を玄関の近くにして短縮", -2, "分", "改善案の効果の仮定。2階から下りる時間も無くなる"),
        Assumption("asm_fix_change_route", "仮定: 別の避難経路（閉塞のおそれが無い道）", 1.3, "倍", "直線距離 × 1.3 の概算に戻す。地図で経路を指定すると正確になる"),
        Assumption("asm_fix_alert_device", "仮定: 光や振動で知らせる機器で、揺れと津波の警報に自分で気づける", 0, "",
                   "改善案の効果の仮定。避難支援者の声かけを待たずに避難を始められる。機器の種類と入手は、市の福祉の窓口に相談する"),
    ]
}

#: 計算できないとき・1人では避難できないときの理由（画面に出す文）
REASON_LABELS: dict[str, str] = {
    "NO_HOME": "自宅の場所が未確認のため、計算できません",
    "NO_ARRIVAL_TIME": "津波到達時間の表に値が無いため、計算できません",
    "SHELTER_NOT_TSUNAMI": "避難先が、津波の指定緊急避難場所ではありません",
    "SHELTER_UNKNOWN": "避難先が、指定緊急避難場所のデータに見つかりません",
    "NO_SHELTER_AVAILABLE": "この人が利用できる津波の避難場所が見つかりません",
    "NO_SUPPORTER": "避難支援者が登録されていないため、計算できません",
    "NEEDS_SUPPORTER": "一人では避難できず、来られる避難支援者がいません",
    "NO_CALLER": "呼びかけに気づけず、声をかける人がいません",
}

NIGHT_PREMISES = {"SLEEP_UPSTAIRS", "DEEP_SLEEP"}

# ---------------------------------------------------------------------------
# 値の読み取り
# ---------------------------------------------------------------------------


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = " ".join(str(v) for v in value.values() if isinstance(v, (str, int, float)))
    return unicodedata.normalize("NFKC", str(value))


_NUMBER = r"(\d+(?:\.\d+)?)"
_RANGE_MINUTES = re.compile(_NUMBER + r"(?:\s*[〜~\-]\s*" + _NUMBER + r")?\s*分")


def parse_minutes(value: object) -> float | None:
    """「10分くらい」→ 10.0。範囲（「5〜10分」）は長いほう。数字が無ければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if isinstance(value, dict):
        # 聞き取りエージェントの形 {"text": "着替えて10分くらい", "minutes": 10}。数値が無ければ text を読む
        number = parse_minutes(value.get("minutes")) if value.get("minutes") is not None else None
        return number if number is not None else parse_minutes(value.get("text"))
    match = _RANGE_MINUTES.search(_text(value))
    if not match:
        return None
    numbers = [float(g) for g in match.groups() if g is not None]
    return max(numbers)


_SPEED_DISTANCE_FIRST = re.compile(_NUMBER + r"\s*(?:m|メートル|メーター)\D{0,6}?" + _NUMBER + r"\s*分")
_SPEED_MINUTES_FIRST = re.compile(_NUMBER + r"\s*分\D{0,4}?" + _NUMBER + r"\s*(?:m|メートル|メーター)")
_SPEED_DIRECT = re.compile(_NUMBER + r"\s*(?:m/s|m/秒|メートル毎秒)")


def parse_walk_speed(value: object) -> float | None:
    """歩行速度を m/秒で返す。「100mを3分」→ 0.556。読めなければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if 0.05 <= value <= 3 else None
    if isinstance(value, dict):
        # 聞き取りエージェントの形 {"text": "100mを3分くらい", "meters": 100, "minutes": 3}。数値が無ければ text を読む
        meters, minutes = value.get("meters"), value.get("minutes")
        if isinstance(meters, (int, float)) and isinstance(minutes, (int, float)) and meters > 0 and minutes > 0:
            return parse_walk_speed(meters / (minutes * 60))
        return parse_walk_speed(value.get("text")) if value.get("text") else None
    text = _text(value)
    match = _SPEED_DISTANCE_FIRST.search(text)
    if match:
        meters, minutes = float(match.group(1)), float(match.group(2))
    else:
        match = _SPEED_MINUTES_FIRST.search(text)
        if match:
            minutes, meters = float(match.group(1)), float(match.group(2))
        else:
            direct = _SPEED_DIRECT.search(text)
            return parse_walk_speed(float(direct.group(1))) if direct else None
    if meters <= 0 or minutes <= 0:
        return None
    return parse_walk_speed(meters / (minutes * 60))


#: 打ち消しの言い方（「車いすは使わない」「介助は不要」）。判定の前に除く（「車いす」の語だけで介助が必要と判定しない）
_NEGATED_AID = re.compile(
    r"(車いす|車椅子|介助)(は|も|を|が)?(使わ(ない|ん|ず)|使ってい?(ない|ん)|使いよらん|不要|いらな|いらん|無し|なし|必要(ない|なし|ありません))")
_NEEDS_AID = re.compile(r"車いす|車椅子|介助|(一人|ひとり|1人)では(歩け|立ち上がれ|動け|避難でき)|立ち上がれ(ない|ん)|寝たきり|歩けない|歩けん|おんぶ|背負")
_CANE = re.compile(r"杖|つえ|ステッキ|歩行器|シルバーカー|手押し車|足が悪|膝が悪|ひざが悪|腰が悪|ゆっくり")
_INDEPENDENT = re.compile(r"自力|問題(なし|ない)|普通に歩け|しっかり歩け|元気に歩")
_HEARING = re.compile(r"耳が遠|聞こえ(ない|ん|にく|づら)|難聴")
_UPSTAIRS = re.compile(r"(2|二)階(?!建)")
_DEEP_SLEEP = re.compile(r"睡眠薬|眠り薬|睡眠導入|眠剤|寝る前.{0,4}薬|薬.{0,6}(寝|眠)")


def _field(plan: Plan, key: str) -> dict | None:
    fv = plan.get("fields", {}).get(key)
    if fv and fv.get("status") == "filled" and fv.get("value") not in (None, ""):
        return fv
    return None


def _field_text(plan: Plan, key: str) -> str:
    fv = _field(plan, key)
    return _text(fv["value"]) if fv else ""


def _field_sources(plan: Plan, key: str) -> list[str]:
    fv = _field(plan, key)
    return [fv["source_id"]] if fv and fv.get("source_id") else []


def mobility_class(plan: Plan) -> str:
    """"needs_aid"（1人では避難できない）、"cane"、"independent"、"unknown"。"""
    text = _NEGATED_AID.sub("", _field_text(plan, "mobility"))
    if not text:
        return "unknown"
    if _NEEDS_AID.search(text):
        return "needs_aid"
    if _CANE.search(text):
        return "cane"
    if _INDEPENDENT.search(text):
        return "independent"
    return "unknown"


def _decade(age_band: object) -> int | None:
    match = re.search(r"(\d+)\s*代", _text(age_band))
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# 計算の条件
# ---------------------------------------------------------------------------


@dataclass
class _Cond:
    scenario_id: str
    period: str = "day"  # "day" | "night"
    winter: bool = False
    dark: bool = False
    earliest: bool = False
    detour: bool = False
    supporters: str = "planned"  # "planned" | "day_strict"（S2）| "none"（S4）
    premises: list[dict] = field(default_factory=list)
    # 改善案（承認済みのものと、try_fix で試すもの）
    bedside: bool = False
    near_exit: bool = False
    change_route: bool = False
    callers: set[str] = field(default_factory=set)
    shelter_override: str | None = None
    alert_device: bool = False

    def apply_fix(self, fix_type: str, target_id: str | None) -> None:
        if fix_type == "BEDSIDE_PREP":
            self.bedside = True
        elif fix_type == "ALERT_DEVICE":
            self.alert_device = True
        elif fix_type == "SLEEP_NEAR_EXIT":
            self.near_exit = True
        elif fix_type == "CHANGE_ROUTE":
            self.change_route = True
        elif fix_type == "ADD_CALLER" and target_id:
            self.callers.add(str(target_id))
        elif fix_type == "CHANGE_SHELTER" and target_id:
            self.shelter_override = str(target_id)


def approved_fixes(plan: Plan) -> list[dict]:
    """承認の記録（plan["approvals"]）がある改善案。run_scenario と try_fix は、これを反映して計算する。"""
    approved = {a.get("fix_id") for a in plan.get("approvals", [])}
    return [f for f in plan.get("fixes", []) if f.get("fix_id") in approved and f.get("fix_type") in pm.FIX_TYPES]


def _conditions(plan: Plan, scenario_id: str, premise_ids: list[str] | None) -> _Cond:
    if scenario_id not in pm.SCENARIOS:
        raise ValueError(f"シナリオの ID が違います: {scenario_id}（S1〜S8）")
    registered = {p["premise_id"]: p for p in plan.get("premises", [])}
    if premise_ids is None:
        chosen = list(registered.values())
    else:
        unknown = [pid for pid in premise_ids if pid not in registered]
        if unknown:
            raise ValueError(f"計画に登録されていない前提条件です: {', '.join(map(str, unknown))}")
        chosen = [registered[pid] for pid in dict.fromkeys(premise_ids)]
    if scenario_id == "S7":
        chosen += [p for p in registered.values() if p.get("code") == "CANNOT_CLIMB" and p not in chosen]
    cond = _Cond(scenario_id=scenario_id, premises=chosen)
    codes = {p.get("code") for p in chosen}
    if scenario_id == "S1":
        cond.period, cond.winter, cond.dark = "night", True, True
    elif scenario_id == "S2":
        cond.supporters = "day_strict"
    elif scenario_id == "S3":
        cond.detour = True
    elif scenario_id == "S4":
        cond.supporters = "none"
    elif scenario_id == "S5":
        night = any(s.get("available_night") is not False for s in plan.get("supporters", []))
        cond.period, cond.dark = ("night", True) if night else ("day", False)
    elif scenario_id == "S6":
        cond.earliest = True
    elif scenario_id == "S8" and codes & NIGHT_PREMISES:
        cond.period, cond.dark = "night", True
    for fix in approved_fixes(plan):
        cond.apply_fix(fix["fix_type"], fix.get("target_id"))
    return cond


@dataclass
class _Person:
    needs_aid: bool
    cannot_hear: bool
    upstairs: bool
    deep_sleep: bool
    road_risk: bool
    sources: dict[str, list[str]]  # 条件ごとの出典 ID


def _person(plan: Plan, cond: _Cond) -> _Person:
    codes: dict[str, list[str]] = {}
    for p in cond.premises:
        codes.setdefault(p.get("code", ""), []).append(p.get("source_id", ""))
    sources: dict[str, list[str]] = {}

    def flag(name: str, form: bool, form_key: str, premise_code: str) -> bool:
        ids = (_field_sources(plan, form_key) if form else []) + [s for s in codes.get(premise_code, []) if s]
        if form or premise_code in codes:
            sources[name] = ids
            return True
        return False

    return _Person(
        needs_aid=flag("needs_aid", mobility_class(plan) == "needs_aid", "mobility", "NEEDS_AID"),
        cannot_hear=flag("cannot_hear", bool(_HEARING.search(_field_text(plan, "hearing_and_sight"))), "hearing_and_sight", "CANNOT_HEAR"),
        upstairs=flag("upstairs", bool(_UPSTAIRS.search(_field_text(plan, "sleep_place"))), "sleep_place", "SLEEP_UPSTAIRS"),
        deep_sleep=flag("deep_sleep", bool(_DEEP_SLEEP.search(_field_text(plan, "health_for_evacuation"))), "health_for_evacuation", "DEEP_SLEEP"),
        road_risk=flag("road_risk", False, "route", "ROAD_RISK"),
        sources=sources,
    )


# ---------------------------------------------------------------------------
# 計算
# ---------------------------------------------------------------------------


def _ceil1(x: float) -> float:
    """0.1分単位で切り上げる（浮動小数の誤差で 13.0 が 13.1 にならないよう、先に丸める）。"""
    return math.ceil(round(x * 10, 6)) / 10


def _r1(x: float) -> float:
    return round(x + 0.0, 1)


def verdict_for(margin: float | None) -> str:
    """余裕時間（分）から判定する。3分以上 ok、0以上 3分未満 tight、マイナス short。"""
    if margin is None:
        return "cannot_compute"
    if margin >= MARGIN_OK_MIN:
        return "ok"
    if margin >= 0:
        return "tight"
    return "short"


class _Sources:
    """計算で使った出典を集め、計画の出典リストに登録する。"""

    def __init__(self, plan: Plan):
        self.plan = plan
        self.assumptions: list[str] = []

    def assume(self, key: str) -> str:
        a = ASSUMPTIONS[key]
        self.plan["sources"][a.source_id] = Source(kind="assumption", label=a.label, value=a.value, unit=a.unit, basis=a.basis)
        if a.source_id not in self.assumptions:
            self.assumptions.append(a.source_id)
        return a.source_id


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


@dataclass
class _Leg:
    minutes: float
    sources: list[str]
    label: str


def _leave_time(plan: Plan, cond: _Cond, person: _Person, src: _Sources) -> _Leg:
    key = "time_to_leave_night" if cond.period == "night" else "time_to_leave_day"
    stated = parse_minutes(_field(plan, key)["value"]) if _field(plan, key) else None
    if stated is not None:
        total, sources, parts = stated, _field_sources(plan, key), []
    else:
        a = "asm_leave_night" if cond.period == "night" else "asm_leave_day"
        total, sources, parts = ASSUMPTIONS[a].value, [src.assume(a)], []
    if cond.period == "night":
        if cond.winter:
            total += ASSUMPTIONS["asm_winter_leave"].value
            sources.append(src.assume("asm_winter_leave"))
            parts.append("冬 +2分")
        if person.upstairs and not cond.near_exit:
            total += ASSUMPTIONS["asm_upstairs"].value
            sources += person.sources.get("upstairs", []) + [src.assume("asm_upstairs")]
            parts.append("2階から下りる +3分")
        if person.deep_sleep:
            total += ASSUMPTIONS["asm_deep_sleep"].value
            sources += person.sources.get("deep_sleep", []) + [src.assume("asm_deep_sleep")]
            parts.append("眠りが深い +5分")
        if cond.bedside:
            total += ASSUMPTIONS["asm_fix_bedside"].value
            sources.append(src.assume("asm_fix_bedside"))
            parts.append("枕元の準備 −3分")
        if cond.near_exit:
            total += ASSUMPTIONS["asm_fix_sleep_near_exit"].value
            sources.append(src.assume("asm_fix_sleep_near_exit"))
            parts.append("玄関の近くで就寝 −2分")
    floor = ASSUMPTIONS["asm_leave_floor"].value
    if total < floor:
        total = floor
        sources.append(src.assume("asm_leave_floor"))
        parts.append("下限 2分")
    period = "夜間" if cond.period == "night" else "日中"
    label = f"避難開始時間（{period}" + ("。" + "、".join(parts) if parts else "") + "）"
    return _Leg(_r1(total), list(dict.fromkeys(s for s in sources if s)), label)


def _person_speed(plan: Plan, cond: _Cond, person: _Person, src: _Sources) -> tuple[float, list[str]]:
    stated = parse_walk_speed(_field(plan, "walk_speed")["value"]) if _field(plan, "walk_speed") else None
    if stated is not None:
        speed, sources = stated, _field_sources(plan, "walk_speed")
    else:
        if person.needs_aid:
            key = "asm_speed_with_aid"
        else:
            key = {"cane": "asm_speed_cane", "independent": "asm_speed_independent"}.get(mobility_class(plan), "asm_speed_unknown")
        speed = ASSUMPTIONS[key].value
        sources = _field_sources(plan, "mobility") + person.sources.get("needs_aid", []) + [src.assume(key)]
    if cond.dark:
        speed *= ASSUMPTIONS["asm_dark_speed"].value
        sources.append(src.assume("asm_dark_speed"))
    return speed, list(dict.fromkeys(s for s in sources if s))


def _route_points(plan: Plan) -> list[tuple[float, float]] | None:
    fv = _field(plan, "route")
    if not fv or not isinstance(fv["value"], dict):
        return None
    points = []
    for p in fv["value"].get("points") or []:
        point = hazard._point(p)
        if point is None:
            return None
        points.append(point)
    return points or None


def _distance(plan: Plan, home: tuple[float, float], shelter: datasets.ShelterRecord, planned: bool,
              cond: _Cond, person: _Person, src: _Sources) -> tuple[float, list[str], str]:
    points = _route_points(plan) if planned and not cond.change_route else None
    if points:
        meters = geo.polyline_m([home, *points, shelter.point])
        sources, method = _field_sources(plan, "route"), "地図で指定した経路"
    else:
        meters = geo.distance_m(home, shelter.point) * hazard.ROUTE_FACTOR
        sources, method = [src.assume("asm_route_factor")], "直線距離 × 1.3 の概算"
    if (cond.detour or person.road_risk) and not cond.change_route:
        meters *= ASSUMPTIONS["asm_detour_factor"].value
        sources += person.sources.get("road_risk", []) + [src.assume("asm_detour_factor")]
        method += "、迂回 × 1.3"
    if cond.change_route:
        sources.append(src.assume("asm_fix_change_route"))
    return meters, sources, method


def _excluded_shelters(plan: Plan, cond: _Cond, planned: datasets.ShelterRecord | None,
                       data: datasets.Dataset) -> dict[str, list[str]]:
    """前提条件 CANNOT_CLIMB で、この人が上がれないとされた避難先（shelter_id → 出典 ID）。"""
    excluded: dict[str, list[str]] = {}
    for p in cond.premises:
        if p.get("code") != "CANNOT_CLIMB":
            continue
        about = p.get("about")
        if about:
            _, record = hazard.match_shelter({"shelter_id": about, "name": about}, data)
        else:
            record = planned
        if record is not None:
            excluded.setdefault(record.shelter_id, []).append(p.get("source_id", ""))
    return excluded


def _nearest_shelter(home: tuple[float, float], data: datasets.Dataset, excluded: set[str]) -> datasets.ShelterRecord | None:
    candidates = [s for s in data.shelters if s.tsunami_ok and s.shelter_id not in excluded]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (geo.distance_m(home, s.point), s.shelter_id))


def _available_supporters(plan: Plan, cond: _Cond) -> list[dict]:
    key = "available_night" if cond.period == "night" else "available_day"
    result = []
    for s in plan.get("supporters", []):
        if s.get("supporter_id") in cond.callers and s.get(key) is not False:
            result.append(s)
        elif cond.supporters == "none":
            continue
        elif cond.supporters == "day_strict" and s.get("available_day") is not True:
            continue
        elif s.get(key) is False:
            continue
        else:
            result.append(s)
    return result


@dataclass
class _SupporterTrip:
    supporter: dict
    arrival: float  # 地震から本人の自宅に着くまで（準備＋移動）
    sources: list[str]
    meters: float
    avail_min: float
    avail_sources: list[str]


def _supporter_trip(plan: Plan, supporter: dict, home: tuple[float, float], cond: _Cond, data: datasets.Dataset,
                    home_pick: hazard.ArrivalPick, src: _Sources) -> _SupporterTrip:
    leave_key = "asm_leave_night" if cond.period == "night" else "asm_leave_day"
    leave = ASSUMPTIONS[leave_key].value
    sources = [supporter.get("source_id", ""), src.assume(leave_key)]
    if cond.period == "night" and cond.winter:
        leave += ASSUMPTIONS["asm_winter_leave"].value
        sources.append(src.assume("asm_winter_leave"))
    decade = _decade(supporter.get("age_band"))
    speed_key = "asm_speed_supporter" if decade is not None and decade < 60 else "asm_speed_supporter_elderly"
    speed = ASSUMPTIONS[speed_key].value
    sources.append(src.assume(speed_key))
    if cond.dark:
        speed *= ASSUMPTIONS["asm_dark_speed"].value
        sources.append(src.assume("asm_dark_speed"))
    point = hazard.supporter_point(supporter)
    avail, avail_sources = home_pick.minutes, [hazard.register_arrival_source(plan, home_pick.area)]
    if point is not None:
        meters = geo.distance_m(point, home) * hazard.ROUTE_FACTOR
        sources.append(src.assume("asm_route_factor"))
        pick = hazard.arrival_at(point, data, earliest=cond.earliest)
        if pick is not None and pick.minutes < avail:
            avail, avail_sources = pick.minutes, [hazard.register_arrival_source(plan, pick.area)]
    else:
        meters = ASSUMPTIONS["asm_supporter_distance"].value
        sources.append(src.assume("asm_supporter_distance"))
    arrival = leave + _ceil1(meters / speed / 60)
    return _SupporterTrip(supporter, _r1(arrival), list(dict.fromkeys(s for s in sources if s)), meters, avail, avail_sources)


def _result(cond: _Cond, verdict: str, src: _Sources, **extra) -> ScenarioResult:
    result = ScenarioResult(
        scenario_id=cond.scenario_id,
        verdict=verdict,
        need_min=extra.pop("need_min", None),
        avail_min=extra.pop("avail_min", None),
        margin_min=extra.pop("margin_min", None),
        breakdown=extra.pop("breakdown", []),
        assumptions=list(src.assumptions),
    )
    result.update({k: v for k, v in extra.items() if v is not None})
    return result


def _evaluate(plan: Plan, cond: _Cond, data: datasets.Dataset) -> ScenarioResult:
    src = _Sources(plan)
    home = hazard.home_point(plan)
    if home is None:
        return _result(cond, "cannot_compute", src, reason_code="NO_HOME")
    person = _person(plan, cond)
    # 改善案 ALERT_DEVICE: 耳が遠い人が、光や振動で知らせる機器で自分で気づけるので、声かけを待たずに避難を始める
    alert_sources: list[str] = []
    if cond.alert_device and person.cannot_hear:
        alert_sources = person.sources.get("cannot_hear", []) + [src.assume("asm_fix_alert_device")]
        person.cannot_hear = False

    # 津波到達時間（自宅）
    pick = hazard.arrival_at(home, data, earliest=cond.earliest)
    if pick is None:
        return _result(cond, "cannot_compute", src, reason_code="NO_ARRIVAL_TIME")
    avail = pick.minutes
    avail_sources = [hazard.register_arrival_source(plan, pick.area)]
    common = {"avail_min": avail, "avail_source_ids": avail_sources, "avail_version": pick.area.version}

    # 避難先
    status, planned = hazard.match_shelter((_field(plan, "shelter") or {}).get("value"), data) if _field(plan, "shelter") else ("unconfirmed", None)
    if cond.shelter_override:
        planned = data.shelter_by_id(cond.shelter_override)
        status = "unknown" if planned is None else ("ok" if planned.tsunami_ok else "not_tsunami")
    if status in ("home", "not_tsunami"):
        return _result(cond, "cannot_compute", src, reason_code="SHELTER_NOT_TSUNAMI", **common,
                       shelter_name=planned.name if planned else None)
    if status == "unknown":
        return _result(cond, "cannot_compute", src, reason_code="SHELTER_UNKNOWN", **common)
    excluded = _excluded_shelters(plan, cond, planned, data)
    shelter_sources: list[str] = []
    if status == "unconfirmed":
        shelter = _nearest_shelter(home, data, set(excluded))
        shelter_sources.append(src.assume("asm_shelter_nearest"))
        is_planned = False
    elif planned.shelter_id in excluded:
        shelter = _nearest_shelter(home, data, set(excluded))
        shelter_sources += excluded[planned.shelter_id]
        is_planned = False
    else:
        # 避難先を変える改善案のときは、地図で指定した経路（元の避難先への経路）を使わない
        shelter, is_planned = planned, not cond.shelter_override
    if shelter is None:
        return _result(cond, "cannot_compute", src, reason_code="NO_SHELTER_AVAILABLE", **common)
    shelter_sources.append(hazard.register_shelter_source(plan, shelter, data))
    common.update(shelter_id=shelter.shelter_id, shelter_name=shelter.name)

    # 本人の移動と、避難先で上がる時間
    speed, speed_sources = _person_speed(plan, cond, person, src)
    meters, distance_sources, method = _distance(plan, home, shelter, is_planned, cond, person, src)
    walk = Breakdown(key="walk", label=f"移動（約{round(meters)}m、{method}、{speed:.2f}m/秒）",
                     minutes=_ceil1(meters / speed / 60),
                     source_ids=list(dict.fromkeys(speed_sources + distance_sources + shelter_sources)))
    climb = Breakdown(key="climb", label=f"避難先で上がる（{shelter.name}）", minutes=float(ASSUMPTIONS["asm_climb"].value),
                      source_ids=[src.assume("asm_climb"), shelter_sources[-1]])
    leave = _leave_time(plan, cond, person, src)
    if alert_sources:
        leave = _Leg(leave.minutes, leave.sources + alert_sources, leave.label + "。光や振動で知らせる機器で気づく")
    help_min = float(ASSUMPTIONS["asm_help"].value)

    def together(trip: _SupporterTrip) -> tuple[float, list[Breakdown]]:
        """避難支援者が着いてから一緒に避難する場合の、出発までの時間と内訳（5.2 節）。"""
        alias = trip.supporter.get("alias", "避難支援者")
        after_help = _r1(trip.arrival + help_min)
        if person.cannot_hear or after_help >= leave.minutes:
            items = [
                Breakdown(key="supporter_travel", label=f"{alias}が準備して本人の自宅に着くまで（約{round(trip.meters)}m）",
                          minutes=trip.arrival, source_ids=trip.sources),
                Breakdown(key="help", label="声をかけ、一緒に避難を開始するまで", minutes=help_min,
                          source_ids=[src.assume("asm_help")] + person.sources.get("cannot_hear", [])),
            ]
            return after_help, items
        return leave.minutes, [Breakdown(key="leave", label=leave.label + f"。{alias}は先に到着", minutes=leave.minutes,
                                         source_ids=leave.sources)]

    supporters = _available_supporters(plan, cond)

    if cond.scenario_id == "S5":
        if not plan.get("supporters"):
            return _result(cond, "cannot_compute", src, reason_code="NO_SUPPORTER", **common)
        if not supporters:
            return _result(cond, "cannot_compute", src, reason_code="NO_SUPPORTER", **common)
        worst = None
        for s in supporters:
            trip = _supporter_trip(plan, s, home, cond, data, pick, src)
            depart, items = together(trip)
            need = _r1(depart + walk["minutes"] + climb["minutes"])
            margin = _r1(trip.avail_min - need)
            if worst is None or margin < worst[0]:
                worst = (margin, need, trip, items)
        margin, need, trip, items = worst
        verdict = verdict_for(margin)
        common.update(avail_min=trip.avail_min, avail_source_ids=trip.avail_sources)
        return _result(cond, verdict, src, need_min=need, margin_min=margin, breakdown=items + [walk, climb],
                       supporter_verdict=verdict, supporter_margin_min=margin,
                       supporter_alias=trip.supporter.get("alias"), **common)

    if person.needs_aid or person.cannot_hear:
        if not supporters:
            reason = "NEEDS_SUPPORTER" if person.needs_aid else "NO_CALLER"
            return _result(cond, "short", src, reason_code=reason, **common)
        trips = [_supporter_trip(plan, s, home, cond, data, pick, src) for s in supporters]
        trip = min(trips, key=lambda t: (t.arrival, t.supporter.get("supporter_id", "")))
        depart, items = together(trip)
        need = _r1(depart + walk["minutes"] + climb["minutes"])
        margin = _r1(avail - need)
        supporter_margin = _r1(trip.avail_min - need)
        return _result(cond, verdict_for(margin), src, need_min=need, margin_min=margin, breakdown=items + [walk, climb],
                       supporter_verdict=verdict_for(supporter_margin), supporter_margin_min=supporter_margin,
                       supporter_alias=trip.supporter.get("alias"), **common)

    leave_item = Breakdown(key="leave", label=leave.label, minutes=leave.minutes, source_ids=leave.sources)
    need = _r1(leave.minutes + walk["minutes"] + climb["minutes"])
    margin = _r1(avail - need)
    return _result(cond, verdict_for(margin), src, need_min=need, margin_min=margin,
                   breakdown=[leave_item, walk, climb], **common)


# ---------------------------------------------------------------------------
# MCP ツールから呼ぶ関数（インターフェース仕様）
# ---------------------------------------------------------------------------


def run_scenario(plan: Plan, scenario_id: str, premise_ids: list[str] | None = None) -> ScenarioResult:
    """シナリオ（"S1"〜"S8"）を計画に適用し、避難が間に合うかを計算する。

    premise_ids は plan["premises"] に登録済みの前提条件だけを受け付ける。未登録の ID は ValueError。
    省略すると登録済みの前提条件をすべて使い、[] なら使わない。
    計算できないとき（津波到達時間が無いなど）は verdict を "cannot_compute" にし、reason_code に理由を入れる。
    """
    return _evaluate(plan, _conditions(plan, scenario_id, premise_ids), datasets.current())


def try_fix(plan: Plan, scenario_id: str, fix_type: str, target_id: str | None = None) -> ScenarioResult:
    """改善案（plan_model.FIX_TYPES）を適用した場合の結果を返す。計画そのものは変更しない。

    target_id は出典リストにある避難先・避難支援者だけを受け付ける。危険な改善案は ValueError で理由を返す。
    条件は run_scenario(plan, scenario_id) と同じ（登録済みの前提条件をすべて使う）。
    """
    if fix_type not in pm.FIX_TYPES:
        raise ValueError(f"決まった種類の改善案だけを試せます: {', '.join(pm.FIX_TYPES)}（車での避難、自宅の2階への避難、様子を見る案は出せません）")
    data = datasets.current()
    cond = _conditions(plan, scenario_id, None)
    if fix_type == "CHANGE_SHELTER":
        record = data.shelter_by_id(str(target_id)) if target_id else None
        if record is None:
            raise ValueError("避難先の ID が指定緊急避難場所のデータにありません。hazard.find_shelters で取得した shelter_id を指定してください")
        if not record.tsunami_ok:
            raise ValueError(f"{record.name}は津波の指定緊急避難場所ではないため、避難先にできません")
        if hazard.shelter_source_id(record.shelter_id) not in plan.get("sources", {}):
            raise ValueError("出典リストに無い避難先です。hazard.find_shelters で取得した避難先だけを指定できます")
        _, planned = hazard.match_shelter((_field(plan, "shelter") or {}).get("value"), data) if _field(plan, "shelter") else (None, None)
        all_premises = _Cond(scenario_id=scenario_id, premises=list(plan.get("premises", [])))
        if record.shelter_id in _excluded_shelters(plan, all_premises, planned, data):
            raise ValueError(f"{record.name}は、この人が上がれないとされた避難先です（地域の情報）")
    elif fix_type == "ADD_CALLER":
        supporter = next((s for s in plan.get("supporters", []) if s.get("supporter_id") == target_id), None)
        if supporter is None or supporter.get("source_id") not in plan.get("sources", {}):
            raise ValueError("出典リストにある避難支援者（plan['supporters'] の supporter_id）だけを指定できます")
        key = "available_night" if cond.period == "night" else "available_day"
        if supporter.get(key) is False:
            raise ValueError(f"{supporter.get('alias', target_id)}は、このシナリオの時間帯には来られません")
    elif fix_type == "ALERT_DEVICE":
        if not _person(plan, cond).cannot_hear:
            raise ValueError("耳が遠い（放送や呼びかけに気づけない）前提が無い計画には、この案は使えません")
    before = _evaluate(plan, cond, data)
    cond.apply_fix(fix_type, target_id)
    after = _evaluate(plan, cond, data)
    # S-03: 避難支援者が間に合わなくなる案は出さない。
    # 新しく避難支援者を呼ぶ案（ADD_CALLER）と、改善案の前は間に合っていた（ok・tight）避難支援者が「間に合わない」になる案を止める。
    # 改善案の前が計算できなかった計画（避難先が津波の対象外など）は、避難支援者の状態が分からないので止めない（結果に表示する）
    newly_at_risk = before.get("supporter_verdict") in ("ok", "tight") or fix_type == "ADD_CALLER"
    if after.get("supporter_verdict") == "short" and newly_at_risk:
        alias = after.get("supporter_alias", "避難支援者")
        raise ValueError(f"この案では、避難支援者（{alias}）が間に合わなくなるため、出せません")
    return after


# ---------------------------------------------------------------------------
# 評価のベースライン（ルールベース。Claude を使わない）
# ---------------------------------------------------------------------------

_CAR = re.compile(r"(?<!電)車(?!いす|椅子)|自動車|軽トラ|トラック|クルマ|マイカー")
#: 避難先に上がれない記載（「立ち上がれない」は介助の話なので除く）
_CANNOT_CLIMB_TEXT = re.compile(r"(?<!立ち)(上が|あが|登|のぼ)れ(ない|ん)|階段が急|階段がきつ")

BASELINE_TEMPLATES: dict[str, str] = {
    "SHELTER_NOT_TSUNAMI": "避難先の{shelter_name}は、津波の指定緊急避難場所として確認できません。",
    "VERTICAL_HOME": "避難先が自宅になっています。津波浸水想定区域内の自宅の上の階は、津波避難ビルの代わりになりません。",
    "CAR_EVAC": "車での避難を計画しています。地震の後は、渋滞や道路の損傷で動けなくなるおそれがあります。",
    "NEEDS_AID": "一人では避難できませんが、避難支援者が決まっていません。",
    "SUPPORTER_ABSENT": "{scenario_label}には来られる避難支援者がいないため、一人では避難できません。",
    "TOO_FAR": "{scenario_label}では、避難所要時間が{need_min}分、津波到達時間が{avail_min}分で、避難先まで間に合いません。",
    "NIGHT_PREP": "夜間の避難開始時間が様式に記載されていません。{scenario_label}では避難所要時間が{need_min}分になり、津波到達時間の{avail_min}分に間に合いません。",
    "SLEEP_UPSTAIRS": "上の階で就寝しているため、{scenario_label}では下りる時間がかかり、避難所要時間が{need_min}分で間に合いません。",
    "DEEP_SLEEP": "眠りが深く、{scenario_label}では気づくのが遅れ、避難所要時間が{need_min}分で間に合いません。",
    "CANNOT_HEAR": "耳が遠く、呼びかけに気づけないおそれがあり、{scenario_label}では間に合いません。",
    "ROAD_BLOCK": "避難経路が閉塞して迂回すると、避難所要時間が{need_min}分になり、津波到達時間の{avail_min}分に間に合いません。",
    "SUPPORTER_AT_RISK": "避難支援者の{supporter_alias}が本人の自宅に寄ってから避難すると、避難支援者の避難所要時間は{need_min}分で、津波到達時間の{avail_min}分に間に合いません。",
    "SINGLE_ELDERLY_SUPPORTER": "避難支援者は{supporter_alias}の一人だけで、その人も高齢です。来られないときの代わりがいません。",
    "CANNOT_CLIMB": "様式の記載に、避難先に上がれないという内容があります。",
}


def fill_placeholders(template: str, plan: Plan, result: ScenarioResult) -> str:
    """説明文のプレースホルダーを、計算の結果の値で置き換える（ベースライン用。画面の説明文は B が同じ方法で置き換える）。"""
    values = {
        "need_min": _fmt(result["need_min"]) if result.get("need_min") is not None else "（計算できません）",
        "avail_min": _fmt(result["avail_min"]) if result.get("avail_min") is not None else "（計算できません）",
        "margin_min": _fmt(result["margin_min"]) if result.get("margin_min") is not None else "（計算できません）",
        "shelter_name": result.get("shelter_name") or _field_text(plan, "shelter") or "避難先",
        "scenario_label": f"「{pm.SCENARIOS[result['scenario_id']]}」のシナリオ",
        "supporter_alias": result.get("supporter_alias") or "避難支援者",
    }
    return re.sub(r"\{([a-z_]+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


def _is_short_with_numbers(result: ScenarioResult) -> bool:
    return result["verdict"] == "short" and result.get("need_min") is not None


def baseline_findings(plan: Plan) -> list[Finding]:
    """評価のベースライン: S1〜S7 を様式の項目だけに総当たりで適用し、見つかる問題点を返す（Claude を使わない）。

    聞き取りの中だけにある前提条件（S8）は扱わない。eval/run_eval.py の --runner baseline が使う。
    規則（様式の欄だけを読む）:
    - S2 で避難先が津波の対象外・データに無い → SHELTER_NOT_TSUNAMI（自宅なら VERTICAL_HOME）
    - 避難経路・避難支援の方法・留意事項に「車」（車いすを除く）→ CAR_EVAC
    - 1人では避難できず、避難支援者がいない → NEEDS_AID。いるが S2・S1 の時間帯に来られない → SUPPORTER_ABSENT
    - S2 か S6 が数値で「間に合わない」→ TOO_FAR
    - S1 だけが数値で「間に合わない」→ 様式に2階での就寝・睡眠薬の記載があれば SLEEP_UPSTAIRS・DEEP_SLEEP、
      夜間の避難開始時間が未記載なら NIGHT_PREP、どれにも当たらなければ TOO_FAR
    - 様式に耳が遠い記載があり、S1 か S2 が「間に合わない」→ CANNOT_HEAR
    - S3 だけが「間に合わない」→ ROAD_BLOCK。S5 で避難支援者が「間に合わない」→ SUPPORTER_AT_RISK
    - 避難支援者が1人だけで70代以上 → SINGLE_ELDERLY_SUPPORTER
    - 様式に「上がれない」「階段が急」などの記載 → CANNOT_CLIMB
    """
    work = copy.deepcopy(plan)
    work["premises"] = []
    results = {sid: run_scenario(work, sid, premise_ids=[]) for sid in ("S1", "S2", "S3", "S4", "S5", "S6", "S7")}
    for sid, source in work["sources"].items():
        plan["sources"].setdefault(sid, source)

    findings: list[Finding] = []

    def add(hole_type: str, scenario_id: str, extra_sources: list[str] | None = None) -> None:
        if any(f["hole_type"] == hole_type for f in findings):
            return
        result = results[scenario_id]
        source_ids = list(result.get("avail_source_ids", []))
        for b in result["breakdown"]:
            source_ids += b["source_ids"]
        source_ids = list(dict.fromkeys(source_ids + (extra_sources or [])))
        template = BASELINE_TEMPLATES[hole_type]
        findings.append(Finding(
            finding_id=pm.new_id("fd"),
            scenario_id=scenario_id,
            hole_type=hole_type,
            explanation_template=template,
            explanation=fill_placeholders(template, plan, result),
            result=result,
            source_ids=source_ids,
        ))

    s1, s2, s3, s5, s6 = (results[k] for k in ("S1", "S2", "S3", "S5", "S6"))
    supporters = plan.get("supporters", [])
    shelter_text = _field_text(plan, "shelter")

    if s2.get("reason_code") in ("SHELTER_NOT_TSUNAMI", "SHELTER_UNKNOWN"):
        if hazard.is_home_shelter(shelter_text):
            add("VERTICAL_HOME", "S2", _field_sources(plan, "shelter"))
        else:
            add("SHELTER_NOT_TSUNAMI", "S2", _field_sources(plan, "shelter"))

    for key in ("route", "support_method", "notes"):
        if _CAR.search(_field_text(plan, key)):
            add("CAR_EVAC", "S3", _field_sources(plan, key))

    if mobility_class(plan) == "needs_aid" and not supporters:
        add("NEEDS_AID", "S4", _field_sources(plan, "mobility"))
    elif supporters:
        for sid in ("S2", "S1"):
            if results[sid].get("reason_code") in ("NEEDS_SUPPORTER", "NO_CALLER"):
                add("SUPPORTER_ABSENT", sid)

    if _is_short_with_numbers(s2):
        add("TOO_FAR", "S2")
    elif _is_short_with_numbers(s6):
        add("TOO_FAR", "S6")

    if _is_short_with_numbers(s1) and not _is_short_with_numbers(s2):
        night_cause = False
        if _UPSTAIRS.search(_field_text(plan, "sleep_place")):
            add("SLEEP_UPSTAIRS", "S1", _field_sources(plan, "sleep_place"))
            night_cause = True
        if _DEEP_SLEEP.search(_field_text(plan, "health_for_evacuation")):
            add("DEEP_SLEEP", "S1", _field_sources(plan, "health_for_evacuation"))
            night_cause = True
        if not _field(plan, "time_to_leave_night"):
            add("NIGHT_PREP", "S1")
        elif not night_cause:
            # 夜間の避難開始時間は記載済みでも、暗い夜道では間に合わない
            add("TOO_FAR", "S1")

    if _HEARING.search(_field_text(plan, "hearing_and_sight")):
        for sid in ("S1", "S2"):
            if results[sid]["verdict"] == "short":
                add("CANNOT_HEAR", sid, _field_sources(plan, "hearing_and_sight"))

    if _is_short_with_numbers(s3) and not _is_short_with_numbers(s2):
        add("ROAD_BLOCK", "S3")

    if s5.get("supporter_verdict") == "short":
        add("SUPPORTER_AT_RISK", "S5")

    if len(supporters) == 1:
        decade = _decade(supporters[0].get("age_band"))
        if (decade is not None and decade >= 70) or "高齢" in _text(supporters[0].get("age_band")):
            add("SINGLE_ELDERLY_SUPPORTER", "S4", [supporters[0].get("source_id", "")])

    for key in ("notes", "shelter", "mobility", "route"):
        if _CANNOT_CLIMB_TEXT.search(_field_text(plan, key)):
            add("CANNOT_CLIMB", "S7", _field_sources(plan, key))
    return findings
