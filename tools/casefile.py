"""ケースファイル（デモ用の架空の人物のサンプルデータ、点検の評価データセット）から計画を作成する。

ケースファイルの形（JSON）:

    {
      "case_id": "yamada",
      "fictional": true,                       # 架空の人物であること（必須。false は受け付けない）
      "alias": "Aさん", "area_label": "宇佐",
      "home": {"lat": 33.4, "lon": 133.4},     # 架空の自宅の位置
      "talk": [{"id": "t1", "speaker": "minsei", "text": "..."}],   # 聞き取りの話（土佐弁）
      "form": {                                # 計画の項目。from が "form" なら紙の様式の記載、それ以外は talk の id
        "mobility": {"value": "杖をついて歩く", "from": "form"},
        "sleep_place": {"value": "1階の奥の部屋", "from": "t1", "quote": "夜は1階の奥で寝ゆう"}
      },
      "supporters": [{"supporter_id": "sup_b", "alias": "Bさん", "relation": "隣人", "age_band": "70代",
                      "available_day": true, "available_night": true, "location": {"lat": .., "lon": ..}, "from": "form"}],
      "premises": [{"code": "CANNOT_HEAR", "from": "t2", "quote": "耳が遠うて", "about": null}]   # 省略可
    }

出典 ID: 紙の様式は "src_form"、話は "utt_<id>"。引用（quote）は出典の原文にそのまま含まれることを確認する（フックと同じ規則）。
"""

from __future__ import annotations

import json
from pathlib import Path

from tools import plan_model as pm

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "data" / "samples"
EVAL_CASES_DIR = ROOT / "eval" / "redteam_cases"

FORM_SOURCE_ID = "src_form"
SPEAKER_LABELS = {"minsei": "民生委員の話", "person": "本人の話", "family": "家族の話"}


class CaseError(ValueError):
    pass


def _value_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("text") or "")
    return str(value)


def build_plan(case: dict) -> pm.Plan:
    """ケースファイルの内容から計画を作成する。引用が原文に無いときは CaseError。"""
    if case.get("fictional") is not True:
        raise CaseError(f"{case.get('case_id')}: 架空の人物であること（fictional: true）が必要です")
    plan = pm.new_plan(case.get("alias", "Aさん"), case.get("area_label", ""))
    plan["plan_id"] = f"plan_{case['case_id']}"
    if case.get("home"):
        plan["home"] = pm.Point(lat=float(case["home"]["lat"]), lon=float(case["home"]["lon"]))

    for index, utterance in enumerate(case.get("talk", []), 1):
        speaker = utterance.get("speaker", "minsei")
        pm.register_source(
            plan,
            pm.Source(kind="utterance", label=f"{SPEAKER_LABELS.get(speaker, '話')}（聞き取り {index}）",
                      text=utterance["text"], speaker=speaker),
            f"utt_{utterance.get('id', index)}",
        )

    form = case.get("form", {})
    paper_lines = [f"{pm.FIELDS.get(key, key)}: {_value_text(spec['value'])}"
                   for key, spec in form.items() if spec.get("from", "form") == "form"]
    supporter_lines = [f"避難支援者: {s.get('alias')}（{s.get('relation', '')}、{s.get('age_band', '年代未確認')}）{s.get('form_note', '')}"
                       for s in case.get("supporters", []) if s.get("from", "form") == "form"]
    if paper_lines or supporter_lines:
        pm.register_source(
            plan,
            pm.Source(kind="utterance", label="紙の様式の記載（民生委員）", text="\n".join(paper_lines + supporter_lines),
                      speaker="minsei"),
            FORM_SOURCE_ID,
        )

    def source_for(spec: dict) -> str:
        origin = spec.get("from", "form")
        sid = FORM_SOURCE_ID if origin == "form" else f"utt_{origin}"
        if sid not in plan["sources"]:
            raise CaseError(f"{case['case_id']}: 出典 {origin} がありません")
        return sid

    for key, spec in form.items():
        if key not in pm.FIELDS:
            raise CaseError(f"{case['case_id']}: 計画に無い項目です: {key}")
        sid = source_for(spec)
        quote = spec.get("quote") or _value_text(spec["value"])
        if quote not in plan["sources"][sid]["text"]:
            raise CaseError(f"{case['case_id']}: 引用が原文にありません: {key} 「{quote}」")
        plan["fields"][key] = pm.FieldValue(value=spec["value"], status="filled", source_id=sid, quote=quote)

    for spec in case.get("supporters", []):
        supporter = pm.Supporter(
            supporter_id=spec["supporter_id"], alias=spec["alias"], relation=spec.get("relation", ""),
            source_id=source_for(spec),
        )
        for key in ("age_band", "available_day", "available_night"):
            if spec.get(key) is not None:
                supporter[key] = spec[key]
        if spec.get("location"):
            supporter["location"] = pm.Point(lat=float(spec["location"]["lat"]), lon=float(spec["location"]["lon"]))
        plan["supporters"].append(supporter)

    for index, spec in enumerate(case.get("premises", []), 1):
        sid = source_for(spec)
        if spec["code"] not in pm.PREMISE_CODES:
            raise CaseError(f"{case['case_id']}: 前提条件のコードが違います: {spec['code']}")
        if spec["quote"] not in plan["sources"][sid]["text"]:
            raise CaseError(f"{case['case_id']}: 前提条件の引用が原文にありません: 「{spec['quote']}」")
        premise = pm.Premise(premise_id=f"pr_{index}_{spec['code'].lower()}", code=spec["code"], quote=spec["quote"],
                             source_id=sid)
        if spec.get("about"):
            premise["about"] = spec["about"]
        plan["premises"].append(premise)
    return plan


def load_case(path: Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def resolve_ref(ref: str) -> Path:
    """"samples/yamada" や "eval/rt01" を、ケースファイルのパスに変換する（data/samples・eval/redteam_cases の中だけ）。"""
    kind, _, name = ref.partition("/")
    if not name or "/" in name or "\\" in name or ".." in name:
        raise CaseError(f"計画の参照の形が違います: {ref}（例: samples/yamada、eval/rt01）")
    base = {"samples": SAMPLES_DIR, "eval": EVAL_CASES_DIR}.get(kind)
    if base is None:
        raise CaseError(f"計画の参照は samples/ か eval/ で始めてください: {ref}")
    path = base / f"{name}.json"
    if not path.is_file():
        raise CaseError(f"ケースファイルがありません: {ref}")
    return path


def list_refs() -> list[str]:
    refs = []
    for kind, base in (("samples", SAMPLES_DIR), ("eval", EVAL_CASES_DIR)):
        if base.is_dir():
            refs += [f"{kind}/{p.stem}" for p in sorted(base.glob("*.json")) if not p.stem.endswith(".plan")]
    return refs


def load_plan(ref: str) -> pm.Plan:
    return build_plan(load_case(resolve_ref(ref)))
