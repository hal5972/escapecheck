"""土佐弁の聞き取りの評価。聞き取りエージェントのモデルと土佐弁の Skill の有無を、4通りで比べる。

    .venv/Scripts/python.exe eval/run_dialect_eval.py --label v1
    .venv/Scripts/python.exe eval/run_dialect_eval.py --variants sonnet+tosa,haiku+tosa --cases d01,d02

- 評価データセット: eval/dialect_cases/dialect_cases.json（15件。デモ用の架空の人物だけ。地元の話者の確認前）
- 1件ごとに、空の計画に発言を1つ渡し（POST /api/interview と同じ処理）、記入された欄を正解と比べる。判定はコードで行う
- 欄の正答率 = 正しかった確認の数 ÷ 確認の数。確認は「正解の欄」と「話していないので未確認のままの欄」の両方
- 計算（hazard・sim）は使わないので、既定でテスト用の仮の値（app/stub_backend.py）にする
- 実行の記録は eval/runs/（git の対象外）に JSON で残す
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASES_PATH = ROOT / "eval" / "dialect_cases" / "dialect_cases.json"
RUNS_DIR = ROOT / "eval" / "runs"

BASE_SKILLS = ["kobetsu-plan-form", "yasashii-kotoba"]
VARIANTS: dict[str, tuple[str, list[str]]] = {
    "sonnet+tosa": ("claude-sonnet-5-5", ["kobetsu-plan-form", "tosa-ben", "yasashii-kotoba"]),
    "sonnet": ("claude-sonnet-5-5", BASE_SKILLS),
    "haiku+tosa": ("claude-haiku-5-5", ["kobetsu-plan-form", "tosa-ben", "yasashii-kotoba"]),
    "haiku": ("claude-haiku-5-5", BASE_SKILLS),
}


def _value_text(value: Any) -> str:
    if isinstance(value, dict):
        value = " ".join(str(v) for k, v in value.items() if not str(k).endswith("_id"))
    return unicodedata.normalize("NFKC", str(value))


def _value_number(value: Any, key: str) -> float | None:
    if isinstance(value, dict) and isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool):
        return float(value[key])
    return None


def check_field(field: dict | None, rule: dict) -> tuple[bool, str]:
    """1つの欄の判定。(正しいか, 理由)。"""
    filled = bool(field) and field.get("status") == "filled"
    if not filled:
        return bool(rule.get("allow_unfilled")), "未記入"
    value = field.get("value")
    text = _value_text(value)
    for key in ("meters", "minutes"):
        if key in rule:
            got = _value_number(value, key)
            if got is None or abs(got - float(rule[key])) > 1e-9:
                return False, f"{key} が {rule[key]} ではない（{got}）"
    if rule.get("not_minutes"):
        got = _value_number(value, "minutes")
        if got is not None and got in [float(n) for n in rule["not_minutes"]]:
            return False, f"計算して換算した分（{got:g}）を記入した"
    if rule.get("any") and not any(unicodedata.normalize("NFKC", w) in text for w in rule["any"]):
        return False, "正解の語が無い"
    if rule.get("none") and any(unicodedata.normalize("NFKC", w) in text for w in rule["none"]):
        return False, "誤りの語がある"
    return True, "正しい"


def score_case(case: dict, fields: dict) -> list[dict]:
    """1件の判定の一覧。"""
    results = []
    for key, rule in case.get("expect", {}).items():
        ok, why = check_field(fields.get(key), rule)
        results.append({"field": key, "kind": "expect", "ok": ok, "why": why, "value": (fields.get(key) or {}).get("value")})
    for key in case.get("unfilled", []):
        filled = (fields.get(key) or {}).get("status") == "filled"
        results.append({"field": key, "kind": "unfilled", "ok": not filled,
                        "why": "推測で記入した" if filled else "未確認のまま", "value": (fields.get(key) or {}).get("value")})
    return results


def run_variant(name: str, cases: list[dict]) -> dict:
    from app import agents, orchestrator
    from tools import plan_model as pm

    model, skills = VARIANTS[name]
    os.environ["ESCAPECHECK_MODEL_INTERVIEW"] = model
    os.environ["ESCAPECHECK_SKILLS_INTERVIEW"] = ",".join(skills)
    runs: list[Any] = []

    def runner(session, role, prompt):
        run = agents.run_agent_sync(session, role, prompt)
        runs.append(run)
        return run

    restore = orchestrator.override(run_agent=runner)
    rows = []
    try:
        for case in cases:
            plan = pm.new_plan("Aさん", "宇佐")
            started = time.monotonic()
            error = None
            try:
                orchestrator.interview(plan, case["text"], case.get("speaker", "minsei"))
            except Exception as exc:  # noqa: BLE001  1件の失敗で全体を止めない
                error = f"{type(exc).__name__}: {exc}"
            seconds = round(time.monotonic() - started, 1)
            run = runs[-1] if runs else None
            rows.append({
                "case_id": case["case_id"], "seconds": seconds, "error": error,
                "cost_usd": getattr(run, "cost_usd", None), "checks": score_case(case, plan["fields"]),
            })
            runs.clear()
    finally:
        restore()
    return summarize_variant(name, model, skills, rows)


def summarize_variant(name: str, model: str, skills: list[str], rows: list[dict]) -> dict:
    checks = [c for r in rows for c in r["checks"]]
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    return {
        "variant": name, "model": model, "skills": skills, "rows": rows,
        "accuracy": round(sum(c["ok"] for c in checks) / len(checks), 3) if checks else None,
        "correct": f"{sum(c['ok'] for c in checks)}/{len(checks)}",
        "guessed_fills": sum(1 for c in checks if c["kind"] == "unfilled" and not c["ok"]),
        "seconds_per_case": round(sum(r["seconds"] for r in rows) / len(rows), 1) if rows else None,
        "cost_per_case_usd": round(sum(costs) / len(costs), 4) if costs else None,
        "errors": sum(bool(r["error"]) for r in rows),
    }


def rescore(recorded: dict, cases: list[dict]) -> list[dict]:
    """記録した実行の値を、今の正解の規則で判定し直す（Claude は呼び出さない。判定の誤りを直したとき用）。"""
    by_id = {c["case_id"]: c for c in cases}
    results = []
    for result in recorded["results"]:
        rows = []
        for row in result["rows"]:
            fields = {}
            for check in row["checks"]:
                value = check.get("value")
                fields[check["field"]] = {"value": value, "status": "filled" if value is not None else "unconfirmed"}
            rows.append({**row, "checks": score_case(by_id[row["case_id"]], fields)})
        results.append(summarize_variant(result["variant"], result["model"], result["skills"], rows))
    return results


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="土佐弁の聞き取りの評価を実行する")
    parser.add_argument("--label", default="dialect")
    parser.add_argument("--variants", default=",".join(VARIANTS), help="例: sonnet+tosa,haiku")
    parser.add_argument("--cases", default=None, help="例: d01,d02")
    parser.add_argument("--real-tools", action="store_true", help="計算を tools/ の実装で行う（既定はテスト用の仮の値）")
    parser.add_argument("--rescore", default=None, help="記録した実行（eval/runs/ の JSON）を、今の規則で判定し直す")
    args = parser.parse_args()

    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["case_id"] in wanted]
    if args.rescore:
        recorded = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        results = rescore(recorded, data["cases"])
        args.label = f"{recorded.get('label', 'v')}-rescored"
    else:
        if not args.real_tools:
            os.environ["ESCAPECHECK_STUB_TOOLS"] = "1"
        from app import agents

        agents.scrub_host_env()
        results = [run_variant(name, cases) for name in args.variants.split(",")]

    print(f"# 土佐弁の聞き取りの評価: label={args.label}（{len(cases)}件、{data['status'][:20]}…）")
    print("| 聞き取りエージェントのモデル | 土佐弁の Skill | 欄の正答率 | 推測で記入した欄 | 1件あたりの時間 | 1件あたりの費用 | 失敗 |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['model']} | {'有り' if 'tosa-ben' in r['skills'] else '無し'} | {r['accuracy']:.0%}（{r['correct']}） | "
              f"{r['guessed_fills']} | {r['seconds_per_case']}秒 | {r['cost_per_case_usd']}ドル | {r['errors']} |")
    print()
    for r in results:
        misses = [f"{row['case_id']}.{c['field']}（{c['why']}）" for row in r["rows"] for c in row["checks"] if not c["ok"]]
        print(f"- {r['variant']} の誤り: {', '.join(misses) or 'なし'}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-dialect-{args.label}.json"
    out.write_text(json.dumps({"label": args.label, "status": data["status"], "results": results}, ensure_ascii=False,
                              indent=1, default=str), encoding="utf-8")
    print(f"\n記録: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
