"""stdio の MCP サーバー（開発中の Claude Code から、作品と同じ計算と地理情報の関数を呼ぶ）。

起動: .mcp.json（プロジェクトの直下）から Claude Code が起動する。手で試すときは
    .venv/Scripts/python.exe -m tools.mcp_stdio

計画は、デモ用の架空の人物のサンプルデータ（data/samples）と点検の評価データセット（eval/redteam_cases）だけを、
"samples/yamada" や "eval/rt01" のような参照で指定する。座標・本当の名前は引数に取らず、結果にも含めない
（作品の MCP ツールと同じ規則。docs/03-design.md の 6.3 節）。
計算に使う数値を外から渡す引数は無い（S-01）。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import functools  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from tools import casefile, hazard, sim  # noqa: E402
from tools import plan_model as pm  # noqa: E402

server = MCPServer(
    name="escapecheck-tools",
    instructions=(
        "逃げ道の点検屋（EscapeCheck）の計算と地理情報の MCP ツール。計画は plan_ref（samples/… か eval/…）で指定する。"
        "サンプルデータと評価データセットの人物はすべて架空。数値はこのツールが算出する（推測で補わない）。"
    ),
)


def _plan(plan_ref: str) -> pm.Plan:
    return casefile.load_plan(plan_ref)


def _reasons(fn):
    """規則による拒否（ValueError）を、理由の文がクライアントに届く ToolError に変える。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:  # casefile.CaseError も含む
            raise ToolError(str(exc)) from exc

    return wrapper


@server.tool()
@_reasons
def list_plans() -> dict:
    """利用できる計画の参照（デモ用の架空の人物のサンプルデータと、点検の評価データセット）の一覧。"""
    return {"plans": casefile.list_refs(), "note": "すべて架空の人物です"}


@server.tool()
@_reasons
def get_plan(plan_ref: str) -> dict:
    """計画を、Claude に渡す形（仮名。座標を除く）で返す。項目・避難支援者・前提条件・出典リストを確認できる。"""
    return pm.for_claude(_plan(plan_ref))


@server.tool()
@_reasons
def get_facts(plan_ref: str, target: str = "home") -> dict:
    """target（"home" または避難支援者の supporter_id）の浸水深の階級・津波到達時間（版つき）・標高と出典 ID。"""
    plan = _plan(plan_ref)
    facts = hazard.get_facts(plan, target)
    return {"facts": facts, "sources": {sid: plan["sources"][sid] for sid in facts["source_ids"]}}


@server.tool()
@_reasons
def find_shelters(plan_ref: str, target: str = "home", limit: int = 3) -> dict:
    """target から近い、津波の指定緊急避難場所（距離は直線距離 × 1.3 の概算）。座標は返さない。"""
    shelters = hazard.find_shelters(_plan(plan_ref), target, limit)
    return {"shelters": [{k: v for k, v in s.items() if k != "point"} for s in shelters]}


@server.tool()
@_reasons
def run_scenario(plan_ref: str, scenario_id: str, premise_ids: list[str] | None = None) -> dict:
    """シナリオ（S1〜S8）を適用し、避難所要時間・津波到達時間・余裕時間・判定・内訳（出典 ID つき）を計算する。

    premise_ids は計画に登録済みの前提条件の ID だけ（省略すると登録済みをすべて使い、空のリストなら使わない）。
    """
    plan = _plan(plan_ref)
    result = sim.run_scenario(plan, scenario_id, premise_ids)
    return {"result": result, "verdict_label": pm.VERDICT_LABELS[result["verdict"]],
            "reason": sim.REASON_LABELS.get(result.get("reason_code", ""), "")}


@server.tool()
@_reasons
def try_fix(plan_ref: str, scenario_id: str, fix_type: str, target_id: str | None = None) -> dict:
    """改善案（CHANGE_SHELTER・ADD_CALLER・BEDSIDE_PREP・SLEEP_NEAR_EXIT・CHANGE_ROUTE）を試した結果。危険な案は理由つきで拒否する。

    CHANGE_SHELTER の target_id は find_shelters の shelter_id、ADD_CALLER は避難支援者の supporter_id。
    """
    plan = _plan(plan_ref)
    hazard.find_shelters(plan, "home", 10)  # 避難先の出典を登録する（作品では find_shelters を先に呼ぶ）
    before = sim.run_scenario(plan, scenario_id)
    after = sim.try_fix(plan, scenario_id, fix_type, target_id)
    saved = None
    if before.get("need_min") is not None and after.get("need_min") is not None and scenario_id != "S5":
        saved = round(before["need_min"] - after["need_min"], 1)
    return {"before": before, "after": after, "saved_min": saved}


@server.tool()
@_reasons
def baseline_findings(plan_ref: str) -> dict:
    """評価のルールベースのベースライン（S1〜S7 を様式の欄に総当たり。話は読まない）で見つかる問題点。"""
    findings = sim.baseline_findings(_plan(plan_ref))
    return {"findings": [{k: f[k] for k in ("scenario_id", "hole_type", "explanation")} for f in findings]}


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
