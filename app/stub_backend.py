"""テスト用の仮の値による計算（tools/ の実装が入るまでの、開発と試験のためだけのもの）。

- ESCAPECHECK_STUB_TOOLS=1 のときだけ使われる（app/backend.py）。画面や印刷物に出す数値には使わない
- 値はインターフェース仕様（tools/plan_model.py の Facts・Shelter・ScenarioResult）の形に合わせた仮の値で、実在のデータではない
- 出典にはすべて「テスト用の仮の値」と記載する
- sample_plan() はデモ用の架空の人物（Aさん）の計画。実在の人物ではない
"""

from __future__ import annotations

import copy
from typing import Any

from tools import plan_model as pm

MARGIN_OK_MIN = 3.0
STUB_LABEL = "テスト用の仮の値"

#: テスト用の仮の避難先（実在の施設ではない）
STUB_SHELTERS: list[dict[str, Any]] = [
    {"shelter_id": "shel_stub_tower", "name": "テスト用の避難タワーA", "kind": "津波避難タワー", "distance_m": 300.0, "climb_min": 2.0},
    {"shelter_id": "shel_stub_hill", "name": "テスト用の高台B", "kind": "高台", "distance_m": 200.0, "climb_min": 1.0},
]
ARRIVAL_MIN = 25.0
EARLY_ARRIVAL_MIN = 20.0
DEFAULT_SPEED = 0.5
DEFAULT_LEAVE_DAY = 5.0
DEFAULT_LEAVE_NIGHT = 10.0
HELP_MIN = 3.0


def _src(plan: pm.Plan, sid: str, **kwargs: Any) -> str:
    if sid not in plan["sources"]:
        pm.register_source(plan, pm.Source(**kwargs), sid)
    return sid


def _num(value: Any, key: str) -> float | None:
    if isinstance(value, dict) and isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool):
        return float(value[key])
    return None


class StubBackend:
    is_stub = True

    # --- hazard ---

    def get_facts(self, plan: pm.Plan, target: str = "home") -> pm.Facts:
        sid = _src(plan, "src_stub_arrival", kind="dataset", label=f"{STUB_LABEL}（津波到達時間）", name=STUB_LABEL,
                   version=STUB_LABEL, value=ARRIVAL_MIN, unit="分")
        return pm.Facts(target=target, inundation_rank=f"{STUB_LABEL}", arrival_min=ARRIVAL_MIN, arrival_version=STUB_LABEL,
                        elevation_m=None, source_ids=[sid], notes=[f"{STUB_LABEL}です。実在のデータではありません"])

    def find_shelters(self, plan: pm.Plan, target: str = "home", limit: int = 3) -> list[pm.Shelter]:
        shelters = []
        for s in sorted(STUB_SHELTERS, key=lambda x: x["distance_m"])[:limit]:
            sid = _src(plan, f"src_{s['shelter_id']}", kind="dataset", label=f"{STUB_LABEL}（避難先）", name=STUB_LABEL,
                       value=s["distance_m"], unit="m")
            notes = [p["quote"] for p in plan.get("premises", []) if p.get("about") == s["shelter_id"]]
            shelters.append(pm.Shelter(shelter_id=s["shelter_id"], name=s["name"], tsunami_ok=True, kind=s["kind"],
                                       distance_m=s["distance_m"], distance_method="straight_x1.3", elevation_m=None,
                                       local_notes=notes, source_id=sid))
        return shelters

    # --- sim ---

    def _approved_fix_types(self, plan: pm.Plan) -> list[str]:
        approved = {a["fix_id"] for a in plan.get("approvals", [])}
        return [f["fix_type"] for f in plan.get("fixes", []) if f["fix_id"] in approved]

    def _shelter(self, plan: pm.Plan, excluded: set[str]) -> dict[str, Any] | None:
        value = (plan["fields"].get("shelter") or {}).get("value")
        chosen = value.get("shelter_id") if isinstance(value, dict) else None
        candidates = [s for s in STUB_SHELTERS if s["shelter_id"] not in excluded]
        for s in candidates:
            if s["shelter_id"] == chosen:
                return s
        return min(candidates, key=lambda s: s["distance_m"]) if candidates else None

    def run_scenario(self, plan: pm.Plan, scenario_id: str, premise_ids: list[str] | None = None) -> pm.ScenarioResult:
        if scenario_id not in pm.SCENARIOS:
            raise ValueError(f"シナリオの ID が正しくありません: {scenario_id}")
        registered = {p["premise_id"]: p for p in plan.get("premises", [])}
        if premise_ids is None:  # 省略すると登録済みの前提条件をすべて使う（[] なら使わない。tools/sim.py と同じ）
            premise_ids = list(registered)
        for pid in premise_ids:
            if pid not in registered:
                raise ValueError(f"計画に登録されていない前提条件です: {pid}")
        codes = {registered[pid]["code"] for pid in premise_ids}
        premises = [registered[pid] for pid in premise_ids]
        fixes = self._approved_fix_types(plan)
        assumptions: list[str] = []
        fields = plan["fields"]

        speed_field = fields.get("walk_speed") or {}
        meters, minutes = _num(speed_field.get("value"), "meters"), _num(speed_field.get("value"), "minutes")
        if meters and minutes:
            speed, speed_src = meters / (minutes * 60), speed_field.get("source_id", "")
        else:
            speed = DEFAULT_SPEED
            speed_src = _src(plan, "src_stub_speed", kind="assumption", label=f"{STUB_LABEL}（歩行速度）", value=DEFAULT_SPEED,
                             unit="m/秒", basis=STUB_LABEL)
            assumptions.append(speed_src)
        night = scenario_id != "S2"  # S2（平日の日中）だけ日中の値を使う
        leave_key = "time_to_leave_night" if night else "time_to_leave_day"
        leave_field = fields.get(leave_key) or {}
        leave = _num(leave_field.get("value"), "minutes")
        if leave is not None:
            leave_src = leave_field.get("source_id", "")
        else:
            default = DEFAULT_LEAVE_NIGHT if night else DEFAULT_LEAVE_DAY
            leave = default
            leave_src = _src(plan, f"src_stub_{leave_key}", kind="assumption", label=f"{STUB_LABEL}（避難開始時間）",
                             value=default, unit="分", basis=STUB_LABEL)
            assumptions.append(leave_src)
        if scenario_id == "S1":
            leave += 2.0
            speed *= 0.8
        if "SLEEP_UPSTAIRS" in codes and "SLEEP_NEAR_EXIT" not in fixes:
            leave += 3.0
        if "DEEP_SLEEP" in codes:
            leave += 5.0
        if "BEDSIDE_PREP" in fixes:
            leave = max(1.0, leave - 3.0)
        if "SLEEP_NEAR_EXIT" in fixes:
            leave = max(1.0, leave - 2.0)

        excluded = {p.get("about") for p in premises if p["code"] == "CANNOT_CLIMB" and p.get("about")}
        shelter = self._shelter(plan, excluded)
        arrival = EARLY_ARRIVAL_MIN if scenario_id == "S6" else ARRIVAL_MIN
        arrival_src = _src(plan, "src_stub_arrival", kind="dataset", label=f"{STUB_LABEL}（津波到達時間）", name=STUB_LABEL,
                           version=STUB_LABEL, value=ARRIVAL_MIN, unit="分")
        if shelter is None:
            return pm.ScenarioResult(scenario_id=scenario_id, verdict="cannot_compute", need_min=None, avail_min=arrival,
                                     margin_min=None, breakdown=[], assumptions=assumptions, reason_code="NO_SHELTER")
        distance = shelter["distance_m"]
        if scenario_id == "S3" or "ROAD_RISK" in codes:
            distance *= 1.3
        walk = distance / speed / 60
        breakdown = [
            pm.Breakdown(key="leave", label="避難開始まで", minutes=round(leave, 2), source_ids=[leave_src]),
            pm.Breakdown(key="walk", label="移動", minutes=round(walk, 2), source_ids=[speed_src, f"src_{shelter['shelter_id']}"]),
            pm.Breakdown(key="climb", label="避難先で上がる", minutes=shelter["climb_min"], source_ids=[]),
        ]
        need_aid = "NEEDS_AID" in codes
        supporters = plan.get("supporters", [])
        helper = None
        if scenario_id not in ("S4",) and ("ADD_CALLER" in fixes or supporters):
            helper = next((s for s in supporters if s.get("available_night", True)), None)
        if "CANNOT_HEAR" in codes or need_aid:
            if helper is None:
                return pm.ScenarioResult(scenario_id=scenario_id, verdict="short", need_min=None, avail_min=arrival,
                                         margin_min=-1.0, breakdown=breakdown, assumptions=assumptions,
                                         reason_code="NEEDS_SUPPORTER")
            breakdown.append(pm.Breakdown(key="help", label="声かけ", minutes=HELP_MIN, source_ids=[]))
        need = sum(row["minutes"] for row in breakdown)
        margin = arrival - need
        result = pm.ScenarioResult(scenario_id=scenario_id, verdict=self._verdict(margin), need_min=round(need, 2),
                                   avail_min=arrival, margin_min=round(margin, 2), breakdown=breakdown,
                                   assumptions=assumptions + [arrival_src])
        result["shelter_id"] = shelter["shelter_id"]  # type: ignore[typeddict-unknown-key]  tools/sim.py と同じく、使った避難先
        result["shelter_name"] = shelter["name"]  # type: ignore[typeddict-unknown-key]
        if scenario_id == "S5":
            if not supporters:
                return pm.ScenarioResult(scenario_id=scenario_id, verdict="cannot_compute", need_min=None, avail_min=arrival,
                                         margin_min=None, breakdown=[], assumptions=assumptions, reason_code="NO_SUPPORTER")
            sup_margin = round(margin - HELP_MIN - 2.0, 2)
            result["supporter_margin_min"] = sup_margin
            result["supporter_verdict"] = self._verdict(sup_margin)
        return result

    @staticmethod
    def _verdict(margin: float) -> pm.Verdict:
        if margin >= MARGIN_OK_MIN:
            return "ok"
        if margin >= 0:
            return "tight"
        return "short"

    def try_fix(self, plan: pm.Plan, scenario_id: str, fix_type: str, target_id: str | None = None) -> pm.ScenarioResult:
        if fix_type not in pm.FIX_TYPES:
            raise ValueError(f"使えない改善案の種類です: {fix_type}")
        trial = copy.deepcopy(plan)
        if fix_type == "CHANGE_SHELTER":
            if target_id not in {s["shelter_id"] for s in STUB_SHELTERS}:
                raise ValueError("津波の指定緊急避難場所ではない避難先は選べません")
            shelter = next(s for s in STUB_SHELTERS if s["shelter_id"] == target_id)
            trial["fields"]["shelter"] = pm.FieldValue(value={"name": shelter["name"], "shelter_id": target_id},
                                                       status="filled", source_id=f"src_{target_id}")
        elif fix_type == "ADD_CALLER":
            if target_id not in {s["supporter_id"] for s in plan.get("supporters", [])}:
                raise ValueError("計画に無い避難支援者です")
        trial["fixes"].append(pm.Fix(fix_id="fix_trial", finding_id="", fix_type=fix_type, explanation_template="",
                                     result=pm.ScenarioResult(scenario_id=scenario_id, verdict="ok", need_min=None,
                                                              avail_min=None, margin_min=None, breakdown=[],
                                                              assumptions=[]), saved_min=None))
        trial["approvals"].append(pm.Approval(fix_id="fix_trial", by="minsei", at=""))
        result = self.run_scenario(trial, scenario_id, None)  # run_scenario(plan, scenario_id) と同じ条件
        for sid, source in trial["sources"].items():
            plan["sources"].setdefault(sid, source)
        return result


def sample_plan() -> pm.Plan:
    """デモ用の架空の人物（Aさん）の、聞き取り前の計画。名前は仮名、位置は丸めた仮の値。"""
    plan = pm.new_plan("Aさん", "宇佐")
    plan["home"] = pm.Point(lat=33.40, lon=133.44)
    return plan


#: デモ用の架空の人物（Aさん）の聞き取り。土佐弁の話し言葉（架空の「よくある例」）
SAMPLE_UTTERANCES: list[str] = [
    "Aさんは八十代で一人暮らし。杖ついちゅう。100メートル歩くのに三分くらいかかるって。夜は2階で寝ゆう。",
    "耳が遠うて、夜の放送は聞こえんがよ。隣のBさんが声をかけてくれるけんど、Bさんも70代やき。",
    "夜は着替えて出るまで10分くらいかかる。昼は5分くらい。避難先は近くの避難タワーにしちゅう。",
]
