"""計画のデータの形と、出典の登録簿（結合の約束）。

このファイルの名前・キー・関数の引数は、並行のセッション（A 道具と計算、B エージェントとサーバー、
C 画面）の約束。変えるときは主のセッションに相談する（docs/05-build-plan.md）。
設計は docs/03-design.md の 5・6 節。

決まり:
- 計画は JSON にできる dict。端末（ブラウザ）が持ち、要求ごとにサーバーへ送られる
- 本当の名前・電話・座標は、Claude に渡す形（for_claude）から外す
- 全ての欄の値に source_id を付ける。出典は plan["sources"] に登録する
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, Literal, NotRequired, TypedDict

SCHEMA_VERSION = "1"

# ---------------------------------------------------------------------------
# 決まった言葉の一覧（コードと日本語の名前）
# ---------------------------------------------------------------------------

#: 計画の欄。値は FieldValue。画面の表示の順に並べる
FIELDS: dict[str, str] = {
    "household": "世帯（一人暮らし・夫婦など）",
    "mobility": "歩く力（杖・車いす・介助の要否）",
    "walk_speed": "歩く速さ（例: 100m に3分）",
    "sleep_place": "夜に寝る場所（何階・玄関からの近さ）",
    "time_to_leave_day": "昼に玄関を出るまでの時間",
    "time_to_leave_night": "夜に玄関を出るまでの時間",
    "hearing_and_sight": "聞こえ・見え（避難に関わること）",
    "health_for_evacuation": "避難に関わる体のこと（薬、医療機器など）",
    "supporters": "避難を手伝う人",
    "support_method": "支援の方法",
    "shelter": "逃げる先（指定緊急避難場所）",
    "route": "逃げる道",
    "contact": "連絡の手段",
    "welfare_services": "使っている福祉サービス",
    "notes": "留意点",
}

#: 崩しの筋書き（docs/03-design.md の 5.3）
SCENARIOS: dict[str, str] = {
    "S1": "冬の真夜中（午前3時）",
    "S2": "平日の昼（家族・支援者が勤めに出ている）",
    "S3": "道がふさがる",
    "S4": "支援者が来られない",
    "S5": "支援者も逃げ切れるか",
    "S6": "津波が早い（短いほうの到達時間）",
    "S7": "避難先に上がれない",
    "S8": "話の中だけにある前提",
}

#: 話の中だけにある前提のコード（S8。値の変え方は sim.py が持つ）
PREMISE_CODES: dict[str, str] = {
    "SLEEP_UPSTAIRS": "夜は2階で寝る",
    "CANNOT_HEAR": "耳が遠く、放送や呼びかけに気づけない",
    "DEEP_SLEEP": "薬などで眠りが深い",
    "CANNOT_CLIMB": "その避難先の階段を上がれない",
    "NEEDS_AID": "一人では立ち上がれない・歩けない",
    "ROAD_RISK": "道にブロック塀などがあり、ふさがるおそれ",
}

#: 穴の種類のコード（評価の正解にも使う）
HOLE_TYPES: dict[str, str] = {
    "NIGHT_PREP": "夜に玄関を出るまでの時間を考えていない",
    "SUPPORTER_ABSENT": "支援者がその時間にいない",
    "SHELTER_NOT_TSUNAMI": "避難先が津波の対象でない",
    "TOO_FAR": "歩く速さでは到達時間に間に合わない",
    "SUPPORTER_AT_RISK": "支援者が自分も逃げ切れない",
    "CANNOT_CLIMB": "避難先に上がれない",
    "SLEEP_UPSTAIRS": "2階で寝ていて下りるのに時間がかかる",
    "CANNOT_HEAR": "呼びかけに気づけない",
    "DEEP_SLEEP": "眠りが深く気づくのが遅れる",
    "NEEDS_AID": "一人では避難できないのに支援者がいない",
    "SINGLE_ELDERLY_SUPPORTER": "支援者が1人だけで、その人も高齢",
    "ROAD_BLOCK": "道がふさがると間に合わない",
    "VERTICAL_HOME": "浸水が想定される自宅の2階などに逃げる計画",
    "CAR_EVAC": "車で逃げる計画",
}

#: 直し案の種類（sim.try_fix が受け付ける物だけ。docs/03-design.md の 5.4）
FIX_TYPES: dict[str, str] = {
    "CHANGE_SHELTER": "避難先を変える",
    "ADD_CALLER": "声をかける人を足す",
    "BEDSIDE_PREP": "枕元に靴・ライト・上着を置く",
    "SLEEP_NEAR_EXIT": "寝る部屋を玄関の近くにする",
    "CHANGE_ROUTE": "別の道にする",
}

#: 出典の種類
SourceKind = Literal["utterance", "dataset", "tool", "assumption", "map_tap"]

#: 判定（余り 3分以上: ok、0〜3分: tight、マイナス: short）
Verdict = Literal["ok", "tight", "short", "cannot_compute"]

FieldStatus = Literal["filled", "unconfirmed"]

# ---------------------------------------------------------------------------
# データの形
# ---------------------------------------------------------------------------


class Source(TypedDict):
    """出典の登録簿の1件。"""

    kind: SourceKind
    label: str  # 画面に出す短い名前（例: 「民生委員の話（11/8 10:42）」「国土地理院 指定緊急避難場所データ」）
    text: NotRequired[str]  # kind が utterance のとき: 話の原文（仮の名前に置き換えた後）
    speaker: NotRequired[Literal["minsei", "person", "family"]]
    at: NotRequired[str]  # ISO 8601
    name: NotRequired[str]  # kind が dataset のとき: データの名前
    version: NotRequired[str]  # データ・予測の版
    url: NotRequired[str]
    value: NotRequired[float | int | str]  # kind が dataset・tool・assumption のとき: その値
    unit: NotRequired[str]
    basis: NotRequired[str]  # kind が assumption のとき: 仮定の根拠


class FieldValue(TypedDict):
    """計画の欄の値。"""

    value: Any
    status: FieldStatus
    source_id: NotRequired[str]  # status が filled なら必須
    quote: NotRequired[str]  # 出典が utterance なら必須（原文にそのまま含まれる抜き出し）


class Point(TypedDict):
    lat: float
    lon: float


class Supporter(TypedDict):
    supporter_id: str  # 例: "sup_b"
    alias: str  # 仮の名前（例: 「Bさん」）
    relation: str  # 例: 「隣の人」「息子」
    age_band: NotRequired[str]  # 例: 「70代」
    available_day: NotRequired[bool]
    available_night: NotRequired[bool]
    location: NotRequired[Point]  # サーバーの中だけで使う。Claude に渡さない
    source_id: str


class Premise(TypedDict):
    """話の中だけにある前提（S8）。"""

    premise_id: str
    code: str  # PREMISE_CODES のキー
    quote: str  # 原文にそのまま含まれる抜き出し
    source_id: str  # utterance の出典
    about: NotRequired[str]  # 前提が指す物（避難先の shelter_id、道の名前など）


class Breakdown(TypedDict):
    """時間の内訳の1行（時間の帯の1区切り）。"""

    key: Literal["leave", "walk", "climb", "help", "supporter_travel"]
    label: str
    minutes: float
    source_ids: list[str]


class ScenarioResult(TypedDict):
    """sim.run_scenario の結果。数字はすべてコードが出す。"""

    scenario_id: str
    verdict: Verdict
    need_min: float | None  # 要る時間
    avail_min: float | None  # 使える時間（到達時間）
    margin_min: float | None  # 余り（マイナスは足りない）
    breakdown: list[Breakdown]
    assumptions: list[str]  # 使った仮定の source_id
    supporter_verdict: NotRequired[Verdict]  # S5 などで支援者を点検したとき
    supporter_margin_min: NotRequired[float | None]
    reason_code: NotRequired[str]  # 計算できないときの理由（例: "NO_ARRIVAL_TIME"）


class Finding(TypedDict):
    """赤チーム役が見つけた穴。"""

    finding_id: str
    scenario_id: str
    hole_type: str  # HOLE_TYPES のキー
    explanation_template: str  # 数字は {need_min} {avail_min} {margin_min} {shelter_name} などの穴埋め
    explanation: NotRequired[str]  # コードが穴埋めを埋めた文（画面に出す）
    result: ScenarioResult
    source_ids: list[str]


class Fix(TypedDict):
    """直し案。"""

    fix_id: str
    finding_id: str
    fix_type: str  # FIX_TYPES のキー
    target_id: NotRequired[str]  # 避難先の shelter_id、支援者の supporter_id など（登録簿にある物だけ）
    explanation_template: str
    explanation: NotRequired[str]
    result: ScenarioResult  # 直した場合の結果
    saved_min: float | None  # 何分縮むか（コードが出す）


class Approval(TypedDict):
    fix_id: str
    by: Literal["minsei", "person"]
    at: str


class Issue(TypedDict):
    """検証役の指摘。"""

    issue_id: str
    field: NotRequired[str]
    message: str
    severity: Literal["要確認", "誤り"]
    acknowledged: bool  # 人が見たか


class Plan(TypedDict):
    schema_version: str
    plan_id: str
    alias: str  # Claude に渡す仮の名前（例: 「Aさん」）
    area_label: str  # 例: 「宇佐」
    home: NotRequired[Point]  # サーバーの中だけで使う。Claude に渡さない
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
    tsunami_ok: bool  # 津波の対象の指定緊急避難場所か
    kind: str  # 例: 「津波避難タワー」「高台」「避難ビル」
    distance_m: float | None  # 直線距離 × 係数の概算（method に書く）
    distance_method: str  # 例: "straight_x1.3"
    elevation_m: NotRequired[float | None]
    local_notes: list[str]  # 地元の知恵（前提の quote）
    point: NotRequired[Point]  # サーバーの中だけで使う
    source_id: str


class Facts(TypedDict):
    """hazard.get_facts の結果。"""

    target: str  # "home" や支援者の supporter_id
    inundation_rank: str | None  # 浸水の深さの階級（例: 「5〜10m」）
    arrival_min: float | None  # 30cm の津波の到達時間（版の違いは短いほう）
    arrival_version: str | None
    elevation_m: float | None
    source_ids: list[str]
    notes: list[str]  # 例: 「標高: 取れませんでした（国土地理院の API が止まっています）」


# ---------------------------------------------------------------------------
# 小さな手伝い（純粋な関数）
# ---------------------------------------------------------------------------


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def new_plan(alias: str, area_label: str) -> Plan:
    """空の計画を作る。全ての欄は「未確認」から始める。"""
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
    """出典を登録簿に足し、source_id を返す。"""
    sid = source_id or new_id("src")
    plan["sources"][sid] = source
    return sid


def for_claude(plan: Plan) -> dict[str, Any]:
    """Claude に渡す形にする。座標を外す（本当の名前・電話は、そもそも計画に入れない）。"""
    view = copy.deepcopy(plan)
    view.pop("home", None)
    for supporter in view.get("supporters", []):
        supporter.pop("location", None)
    return view
