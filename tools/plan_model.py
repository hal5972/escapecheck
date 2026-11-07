"""個別避難計画のデータ構造と出典リスト（インターフェース仕様）。

このファイルの名前・キー・関数のシグネチャは、計算（tools/）・サーバー（app/）・画面（web/）の間のインターフェース仕様。変更するときは、すべての利用箇所を合わせて変更する。

方針:
- 計画は JSON に変換できる dict。端末（ブラウザ）が保持し、リクエストごとにサーバーへ送られる
- 氏名・電話番号・座標は、Claude に渡すデータ（for_claude）から除外する（仮名化）
- すべての項目の値に出典 ID を付け、出典は plan["sources"]（出典リスト）に登録する
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, Literal, NotRequired, TypedDict

SCHEMA_VERSION = "1"

# ---------------------------------------------------------------------------
# コードと表示名の一覧（表示名は画面にそのまま出る）
# ---------------------------------------------------------------------------

#: 計画の項目。値は FieldValue。画面の表示順に並べる
FIELDS: dict[str, str] = {
    "household": "世帯構成（一人暮らし・夫婦など）",
    "mobility": "移動の状況（杖・車いす・介助の要否）",
    "walk_speed": "歩行速度（例: 100mを3分）",
    "sleep_place": "就寝場所（階数・玄関からの距離）",
    "time_to_leave_day": "避難開始時間（日中）",
    "time_to_leave_night": "避難開始時間（夜間）",
    "hearing_and_sight": "聴覚・視覚（避難に関わること）",
    "health_for_evacuation": "避難に関わる心身の状況（服薬、医療機器など）",
    "supporters": "避難支援者",
    "support_method": "避難支援の方法",
    "shelter": "避難先（指定緊急避難場所）",
    "route": "避難経路",
    "contact": "連絡手段",
    "welfare_services": "利用している福祉サービス",
    "notes": "留意事項",
}

#: 点検のシナリオ
SCENARIOS: dict[str, str] = {
    "S1": "冬の深夜（午前3時）",
    "S2": "平日の日中（家族・避難支援者が不在）",
    "S3": "避難経路の閉塞",
    "S4": "避難支援者が来られない",
    "S5": "避難支援者の安全確保",
    "S6": "津波の早期到達（短い方の到達時間）",
    "S7": "避難先に上がれない",
    "S8": "聞き取りの中だけにある前提条件",
}

#: 聞き取りの中だけにある前提条件のコード（S8。値の変え方は sim.py が持つ）
PREMISE_CODES: dict[str, str] = {
    "SLEEP_UPSTAIRS": "2階で就寝している",
    "CANNOT_HEAR": "耳が遠く、防災行政無線や呼びかけに気づけない",
    "DEEP_SLEEP": "服薬などで眠りが深い",
    "CANNOT_CLIMB": "避難先の階段を上がれない",
    "NEEDS_AID": "一人では立ち上がれない・歩けない",
    "ROAD_RISK": "避難経路にブロック塀などがあり、閉塞のおそれがある",
}

#: 問題点の種類のコード（評価データセットの正解にも使う）
HOLE_TYPES: dict[str, str] = {
    "NIGHT_PREP": "夜間の避難開始時間を考慮していない",
    "SUPPORTER_ABSENT": "その時間帯に避難支援者がいない",
    "SHELTER_NOT_TSUNAMI": "避難先が津波の指定緊急避難場所ではない",
    "TOO_FAR": "歩行速度では津波到達時間に間に合わない",
    "SUPPORTER_AT_RISK": "避難支援者自身の避難が間に合わない",
    "CANNOT_CLIMB": "避難先に上がれない",
    "SLEEP_UPSTAIRS": "2階で就寝しており、下りるのに時間がかかる",
    "CANNOT_HEAR": "呼びかけに気づけない",
    "DEEP_SLEEP": "眠りが深く、気づくのが遅れる",
    "NEEDS_AID": "一人では避難できないが、避難支援者がいない",
    "SINGLE_ELDERLY_SUPPORTER": "避難支援者が1人だけで、その人も高齢",
    "ROAD_BLOCK": "避難経路が閉塞すると間に合わない",
    "VERTICAL_HOME": "津波浸水想定区域内の自宅2階などへの垂直避難を計画している",
    "CAR_EVAC": "車での避難を計画している",
}

#: 改善案の種類（sim.try_fix が受け付けるものだけ）
FIX_TYPES: dict[str, str] = {
    "CHANGE_SHELTER": "避難先を変更する",
    "ADD_CALLER": "声かけをする人を追加する",
    "BEDSIDE_PREP": "枕元に靴・ライト・上着を用意する",
    "SLEEP_NEAR_EXIT": "就寝場所を玄関の近くにする",
    "CHANGE_ROUTE": "避難経路を変更する",
    "ALERT_DEVICE": "光や振動で知らせる機器を枕元に置く",
}

#: 判定の表示名（色だけで伝えないため、必ず文字で表示する）
VERDICT_LABELS: dict[str, str] = {
    "ok": "間に合う",
    "tight": "余裕が少ない",
    "short": "間に合わない",
    "cannot_compute": "計算できない",
}

#: 出典の種類
SourceKind = Literal["utterance", "dataset", "tool", "assumption", "map_tap"]

#: 判定（余裕時間 3分以上: ok、0〜3分: tight、マイナス: short）
Verdict = Literal["ok", "tight", "short", "cannot_compute"]

FieldStatus = Literal["filled", "unconfirmed"]

# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------


class Source(TypedDict):
    """出典リストの1件。"""

    kind: SourceKind
    label: str  # 画面に出す短い名前（例: 「民生委員の発言（11/8 10:42）」「国土地理院 指定緊急避難場所データ」）
    text: NotRequired[str]  # kind が utterance のとき: 発言の原文（仮名化した後）
    speaker: NotRequired[Literal["minsei", "person", "family"]]
    at: NotRequired[str]  # ISO 8601
    name: NotRequired[str]  # kind が dataset のとき: データの名称
    version: NotRequired[str]  # データ・予測の版
    url: NotRequired[str]
    value: NotRequired[float | int | str]  # kind が dataset・tool・assumption のとき: その値
    unit: NotRequired[str]
    basis: NotRequired[str]  # kind が assumption のとき: 仮定の根拠


class FieldValue(TypedDict):
    """計画の項目の値。"""

    value: Any
    status: FieldStatus
    source_id: NotRequired[str]  # status が filled のときは必須
    quote: NotRequired[str]  # 出典が utterance のときは必須（原文にそのまま含まれる引用）


class Point(TypedDict):
    lat: float
    lon: float


class Supporter(TypedDict):
    supporter_id: str  # 例: "sup_b"
    alias: str  # 仮名（例: 「Bさん」）
    relation: str  # 例: 「隣人」「長男」
    age_band: NotRequired[str]  # 例: 「70代」
    available_day: NotRequired[bool]
    available_night: NotRequired[bool]
    location: NotRequired[Point]  # サーバー内でだけ使う。Claude に渡さない
    source_id: str


class Premise(TypedDict):
    """聞き取りの中だけにある前提条件（S8）。"""

    premise_id: str
    code: str  # PREMISE_CODES のキー
    quote: str  # 原文にそのまま含まれる引用
    source_id: str  # utterance の出典
    about: NotRequired[str]  # 前提条件の対象（避難先の shelter_id、道路の名称など）


class Breakdown(TypedDict):
    """所要時間の内訳の1行（タイムラインの1区間）。"""

    key: Literal["leave", "walk", "climb", "help", "supporter_travel"]
    label: str
    minutes: float
    source_ids: list[str]


class ScenarioResult(TypedDict):
    """sim.run_scenario の結果。数値はすべてコードが算出する。"""

    scenario_id: str
    verdict: Verdict
    need_min: float | None  # 避難所要時間
    avail_min: float | None  # 津波到達時間
    margin_min: float | None  # 余裕時間（マイナスは不足）
    breakdown: list[Breakdown]
    assumptions: list[str]  # 使った仮定の出典 ID
    supporter_verdict: NotRequired[Verdict]  # S5 などで避難支援者を確認したとき
    supporter_margin_min: NotRequired[float | None]
    reason_code: NotRequired[str]  # 計算できないときの理由（例: "NO_ARRIVAL_TIME"）


class Finding(TypedDict):
    """レッドチームエージェントが見つけた問題点。"""

    finding_id: str
    scenario_id: str
    hole_type: str  # HOLE_TYPES のキー
    explanation_template: str  # 数値は {need_min} {avail_min} {margin_min} {shelter_name} などのプレースホルダーで書く
    explanation: NotRequired[str]  # コードがプレースホルダーを埋めた文（画面に出す）
    result: ScenarioResult
    source_ids: list[str]


class Fix(TypedDict):
    """改善案。"""

    fix_id: str
    finding_id: str
    fix_type: str  # FIX_TYPES のキー
    target_id: NotRequired[str]  # 避難先の shelter_id、避難支援者の supporter_id など（出典リストにあるものだけ）
    explanation_template: str
    explanation: NotRequired[str]
    result: ScenarioResult  # 改善した場合の結果
    saved_min: float | None  # 短縮できる時間（コードが算出する）


class Approval(TypedDict):
    fix_id: str
    by: Literal["minsei", "person"]
    at: str


class Issue(TypedDict):
    """検証エージェントの指摘。"""

    issue_id: str
    field: NotRequired[str]
    message: str
    severity: Literal["要確認", "誤り"]
    acknowledged: bool  # 人が確認したか


class Plan(TypedDict):
    schema_version: str
    plan_id: str
    alias: str  # Claude に渡す仮名（例: 「Aさん」）
    area_label: str  # 例: 「宇佐」
    home: NotRequired[Point]  # サーバー内でだけ使う。Claude に渡さない
    fields: dict[str, FieldValue]
    supporters: list[Supporter]
    premises: list[Premise]
    findings: list[Finding]
    fixes: list[Fix]
    approvals: list[Approval]
    issues: list[Issue]
    sources: dict[str, Source]
    status: Literal["draft", "checked", "final"]


class Shelter(TypedDict):
    shelter_id: str
    name: str
    tsunami_ok: bool  # 津波の指定緊急避難場所か
    kind: str  # 例: 「津波避難タワー」「高台」「津波避難ビル」
    distance_m: float | None  # 直線距離 × 係数の概算（distance_method に記載）
    distance_method: str  # 例: "straight_x1.3"
    elevation_m: NotRequired[float | None]
    local_notes: list[str]  # 地域の情報（前提条件の引用）
    point: NotRequired[Point]  # サーバー内でだけ使う
    source_id: str


class Facts(TypedDict):
    """hazard.get_facts の結果。"""

    target: str  # "home" や避難支援者の supporter_id
    inundation_rank: str | None  # 浸水深の階級（例: 「5〜10m」）
    arrival_min: float | None  # 津波到達時間（浸水30cm。予測の版が複数あるときは短い方）
    arrival_version: str | None
    elevation_m: float | None
    source_ids: list[str]
    notes: list[str]  # 例: 「標高: 取得できませんでした（国土地理院の標高 API が停止しています）」


# ---------------------------------------------------------------------------
# 補助関数（純粋関数）
# ---------------------------------------------------------------------------


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def new_plan(alias: str, area_label: str) -> Plan:
    """空の計画を作成する。すべての項目は「未確認」から始める。"""
    return Plan(
        schema_version=SCHEMA_VERSION,
        plan_id=new_id("plan"),
        alias=alias,
        area_label=area_label,
        fields={key: FieldValue(value=None, status="unconfirmed") for key in FIELDS},
        supporters=[],
        premises=[],
        findings=[],
        fixes=[],
        approvals=[],
        issues=[],
        sources={},
        status="draft",
    )


def register_source(plan: Plan, source: Source, source_id: str | None = None) -> str:
    """出典を出典リストに登録し、出典 ID を返す。"""
    sid = source_id or new_id("src")
    plan["sources"][sid] = source
    return sid


def for_claude(plan: Plan) -> dict[str, Any]:
    """Claude に渡すデータを作る。座標を除外する（氏名・電話番号は、そもそも計画に含めない）。"""
    view = copy.deepcopy(plan)
    view.pop("home", None)
    for supporter in view.get("supporters", []):
        supporter.pop("location", None)
    return view
