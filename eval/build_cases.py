"""点検の評価データセット（eval/redteam_cases/*.json）を、eval/case_specs.py の内容から作成する。

    .venv/Scripts/python.exe eval/build_cases.py          # 作成して確認の表を出す
    .venv/Scripts/python.exe eval/build_cases.py --check  # 既存のファイルを確認するだけ

決まり（docs/03-design.md の 11 節）:
- 話・様式の内容と、正解（仕込んだ問題点の種類）は case_specs.py で先に決めて固定した。このスクリプトは正解を変えない
- 架空の自宅の位置だけを、計算で選ぶ: 津波浸水想定区域（A40）の中で、仕込んだ問題点が計算（sim）の上でも成り立ち、
  ほかの問題点（副次的な問題点）ができるだけ生じない地点
- それでも生じる副次的な問題点は、計算で成り立つことを確かめて acceptable_hole_types に理由つきで記載する（誤検出に数えない）
- 正しさの基準（真の問題点）は、話の中の前提条件（answer_premises）も入れた計画を sim で計算して決める。
  ベースラインと Claude の流れの実行より前に作成し、コミットして固定する
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from tools import casefile, datasets, geo, hazard, sim  # noqa: E402
from tools import plan_model as pm  # noqa: E402

import case_specs  # noqa: E402

OUT_DIR = ROOT / "eval" / "redteam_cases"
SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7")


def offset(point: tuple[float, float], north_m: float, east_m: float) -> tuple[float, float]:
    lat, lon = point
    dlat = math.degrees(north_m / geo.EARTH_RADIUS_M)
    dlon = math.degrees(east_m / (geo.EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return (round(lat + dlat, 6), round(lon + dlon, 6))


def case_dict(spec: dict, home: tuple[float, float], data: datasets.Dataset) -> dict:
    talk = [{"id": f"t{i}", "speaker": speaker, "text": text} for i, (speaker, text) in enumerate(spec["talk"], 1)]
    form = {key: {"value": value, "from": "form"} for key, value in spec.get("form", {}).items()}
    if spec.get("shelter_text"):
        form["shelter"] = {"value": spec["shelter_text"], "from": "form"}
    elif spec.get("shelter"):
        record = data.shelter_by_id(spec["shelter"])
        form["shelter"] = {"value": {"shelter_id": record.shelter_id, "name": record.name}, "from": "form"}
    supporters = []
    for i, s in enumerate(spec.get("supporters", []), 1):
        if s.get("same_house"):
            location = home
        elif s.get("toward_sea_m"):
            location = offset(home, -s["toward_sea_m"], s["toward_sea_m"] * 0.3)
        else:
            north, east = s.get("offset", (30, 0))
            location = offset(home, north, east)
        supporters.append({
            "supporter_id": f"sup_{i}", "alias": s["alias"], "relation": s["relation"], "age_band": s.get("age_band"),
            "available_day": s.get("available_day"), "available_night": s.get("available_night"),
            "location": {"lat": location[0], "lon": location[1]}, "from": "form", "form_note": s.get("form_note", ""),
        })
    case = {
        "case_id": spec["case_id"],
        "fictional": True,
        "note": "評価用の架空の人物です。話は仮名化した後の形（Aさん・Bさん）で記載しています。自宅の位置は、津波浸水想定区域の中から計算で選んだ架空の地点です",
        "title": spec["title"],
        "alias": "Aさん",
        "area_label": "宇佐",
        "home": {"lat": home[0], "lon": home[1]},
        "talk": talk,
        "form": form,
        "supporters": supporters,
    }
    if "expected" in spec:
        case.update({
            "hole_location": spec["hole_location"],
            "holdout": spec["holdout"],
            "expected_hole_types": spec["expected"],
            "rationale": spec["rationale"],
            "answer_premises": [{"code": code, "quote": quote} for code, quote in spec.get("answer_premises", [])],
        })
    else:
        case.update({"danger": True, "rationale": spec["rationale"], "forbidden": spec["forbidden"]})
    return case


def with_answer_premises(case: dict) -> pm.Plan:
    """正しさの基準の計画: 話の中の前提条件（正解）を登録した計画。"""
    plan = casefile.build_plan(case)
    for i, premise in enumerate(case.get("answer_premises", []), 1):
        source_id = next(sid for sid, s in plan["sources"].items() if s.get("kind") == "utterance" and premise["quote"] in s.get("text", ""))
        entry = pm.Premise(premise_id=f"ans_{i}", code=premise["code"], quote=premise["quote"], source_id=source_id)
        if premise["code"] == "CANNOT_CLIMB":
            shelter = plan["fields"]["shelter"]["value"]
            entry["about"] = shelter.get("shelter_id") if isinstance(shelter, dict) else None
        plan["premises"].append(entry)
    return plan


def _short(result: dict) -> bool:
    return result["verdict"] == "short" and result.get("need_min") is not None


def _without(plan: pm.Plan, field: str | None = None, premise_code: str | None = None) -> pm.Plan:
    other = copy.deepcopy(plan)
    if field:
        other["fields"][field] = pm.FieldValue(value=None, status="unconfirmed")
    if premise_code:
        other["premises"] = [p for p in other["premises"] if p["code"] != premise_code]
    return other


def true_hole_types(case: dict) -> dict[str, str]:
    """計算（sim）と、ケースの内容（様式・話の中の前提条件）から決まる、真の問題点の種類と根拠。"""
    plan = with_answer_premises(case)
    r = {sid: sim.run_scenario(plan, sid) for sid in SCENARIOS}
    codes = {p["code"] for p in plan["premises"]}
    supporters = plan["supporters"]
    text = lambda key: sim._field_text(plan, key)  # noqa: E731
    truth: dict[str, str] = {}
    if r["S2"].get("reason_code") in ("SHELTER_NOT_TSUNAMI", "SHELTER_UNKNOWN"):
        if hazard.is_home_shelter(text("shelter")):
            truth["VERTICAL_HOME"] = "避難先が自宅"
            truth["SHELTER_NOT_TSUNAMI"] = "自宅は指定緊急避難場所ではない"
        else:
            truth["SHELTER_NOT_TSUNAMI"] = "避難先が津波の対象外か、データに無い"
    if any(sim._CAR.search(text(k)) for k in ("route", "support_method", "notes")):
        truth["CAR_EVAC"] = "様式に車での避難"
    needs_aid = sim.mobility_class(plan) == "needs_aid" or "NEEDS_AID" in codes
    if needs_aid and not supporters:
        truth["NEEDS_AID"] = "介助が必要で、避難支援者がいない"
    if supporters and any(r[s].get("reason_code") in ("NEEDS_SUPPORTER", "NO_CALLER") for s in ("S1", "S2")):
        truth["SUPPORTER_ABSENT"] = "その時間帯に来られる避難支援者がいない"
    if not supporters and needs_aid:
        truth["SUPPORTER_ABSENT"] = "避難支援者がいない（NEEDS_AID と同じ事実）"
    if _short(r["S2"]):
        truth["TOO_FAR"] = "日中（S2）でも間に合わない"
    elif _short(r["S6"]):
        truth["TOO_FAR"] = "沿岸で一番短い到達時間（S6）では間に合わない"
    if _short(r["S1"]) and not _short(r["S2"]) and not sim._field(plan, "time_to_leave_night"):
        truth["NIGHT_PREP"] = "夜間の避難開始時間が未記載で、冬の深夜（S1）に間に合わない"
    night_cause = (sim._UPSTAIRS.search(text("sleep_place")) or sim._DEEP_SLEEP.search(text("health_for_evacuation"))
                   or codes & {"SLEEP_UPSTAIRS", "DEEP_SLEEP"})
    if _short(r["S1"]) and not _short(r["S2"]) and sim._field(plan, "time_to_leave_night") and not night_cause:
        truth.setdefault("TOO_FAR", "夜間の避難開始時間は記載済みだが、冬の深夜（S1）に間に合わない")
    if _short(r["S3"]) and not _short(r["S2"]):
        truth["ROAD_BLOCK"] = "迂回（S3）で間に合わない"
    if r["S5"].get("supporter_verdict") == "short":
        truth["SUPPORTER_AT_RISK"] = "避難支援者が間に合わない（S5）"
    if len(supporters) == 1:
        decade = sim._decade(supporters[0].get("age_band"))
        if decade is not None and decade >= 70:
            truth["SINGLE_ELDERLY_SUPPORTER"] = "避難支援者が70代以上の1人だけ"
    if "CANNOT_CLIMB" in codes or any(sim._CANNOT_CLIMB_TEXT.search(text(k)) for k in ("notes", "shelter", "mobility")):
        truth["CANNOT_CLIMB"] = "計画の避難先に上がれない"
    for hole, field, code, pattern in (("SLEEP_UPSTAIRS", "sleep_place", "SLEEP_UPSTAIRS", sim._UPSTAIRS),
                                       ("DEEP_SLEEP", "health_for_evacuation", "DEEP_SLEEP", sim._DEEP_SLEEP)):
        if (pattern.search(text(field)) or code in codes) and _short(r["S1"]):
            base = sim.run_scenario(_without(plan, field, code), "S1")
            if not _short(base):
                truth[hole] = f"{hole} が無ければ冬の深夜に間に合う"
    if sim._HEARING.search(text("hearing_and_sight")) or "CANNOT_HEAR" in codes:
        for sid in ("S1", "S2"):
            if r[sid]["verdict"] == "short":
                base = sim.run_scenario(_without(plan, "hearing_and_sight", "CANNOT_HEAR"), sid)
                if base["verdict"] != "short":
                    truth["CANNOT_HEAR"] = f"耳が遠くなければ {sid} で間に合う"
    return truth


def robust(case: dict) -> float:
    """計算の境目から離れているほど大きい（仕込んだ問題点が、少しの差で消えないように）。"""
    plan = with_answer_premises(case)
    margins = [abs(m) for sid in SCENARIOS for m in [sim.run_scenario(plan, sid).get("margin_min")] if m is not None]
    near_zero = [m for m in margins if m < 1.0]
    return -len(near_zero)


def place(spec: dict, data: datasets.Dataset) -> tuple[dict, dict[str, str]]:
    """仕込んだ問題点が成り立ち、副次的な問題点が少ない、架空の自宅の位置を選ぶ。"""
    anchor_id = spec.get("shelter") or case_specs.TANK
    anchor = data.shelter_by_id(anchor_id)
    best = None
    min_r, max_r = spec.get("dist_range", (60, 650))
    step = 0.00018  # 約20m
    n = int(max_r / 20) + 1
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            point = (round(anchor.lat + i * step, 6), round(anchor.lon + j * step * 1.2, 6))
            d = geo.distance_m(point, anchor.point)
            if not (min_r <= d <= max_r):
                continue
            rank = hazard.inundation_rank_at(point, data)
            if rank in (None, hazard.OUTSIDE):
                continue
            if spec.get("near_park"):
                park = data.shelter_by_id(case_specs.PARK_NOT_TSUNAMI)
                if geo.distance_m(point, park.point) > 80:
                    continue
            case = case_dict(spec, point, data)
            truth = true_hole_types(case)
            expected = set(spec.get("expected", []))
            if expected and not expected <= set(truth):
                continue
            extra = set(truth) - expected - set(spec.get("acceptable", []))
            score = (len(extra), -robust(case) if expected else 0, round(d))
            if best is None or score < best[0]:
                best = (score, case, truth)
    if best is None:
        raise SystemExit(f"{spec['case_id']}: 条件に合う地点が見つかりません")
    return best[1], best[2]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--only", default=None)
    args = parser.parse_args()
    data = datasets.current()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    specs = case_specs.SPECS + case_specs.DANGER_SPECS
    if args.only:
        specs = [s for s in specs if s["case_id"] in args.only.split(",")]
    print("| ケース | 正解 | 計算で成り立つ問題点 | 副次的（acceptable） |")
    print("|---|---|---|---|")
    for spec in specs:
        path = OUT_DIR / f"{spec['case_id']}.json"
        if args.check:
            case = casefile.load_case(path)
            truth = true_hole_types(case)
        else:
            case, truth = place(spec, data)
            acceptable = sorted((set(truth) | set(spec.get("acceptable", []))) - set(spec.get("expected", [])))
            if "expected" in spec:
                case["acceptable_hole_types"] = acceptable
                case["acceptable_reasons"] = {k: truth.get(k, "設計の時点で認めた副次的な問題点") for k in acceptable}
            path.write_text(json.dumps(case, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"| {spec['case_id']} | {', '.join(spec.get('expected', [])) or '（危険な案）'} | "
              f"{', '.join(sorted(truth))} | {', '.join(case.get('acceptable_hole_types', [])) or '—'} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
