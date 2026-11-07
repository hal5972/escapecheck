"""Agent SDK のプロセス内の MCP サーバー（docs/03-design.md の 6.3 節）。

MCP ツールは座標と本当の名前を引数に取らない。「home」や避難支援者の supporter_id のような呼び名を受け取り、
サーバーが PlanSession の計画から座標を取得して tools/ の関数（app/backend.py）を呼ぶ。
数値は tools/ の関数が出す。計算の MCP ツールに、Claude が値を外部から渡す引数は無い（S-01）。

ツールの本体も、書き込む直前に app/hooks.py と同じ確認を行う（フックが動かなかった場合の備え）。
エージェントごとに使える MCP ツールを絞る（TOOL_SETS）。
"""

from __future__ import annotations

import copy
import json
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool

from app import hooks, privacy
from app import placeholders as ph
from app.audit import now_iso
from app.session import PlanSession
from tools import plan_model as pm

#: エージェントごとに使える MCP ツール（docs/03-design.md の 6.1 節。plan.set_supporter と hazard.find_shelters
#: の聞き取りエージェントへの追加は B の判断: 避難支援者と避難先を、話から計画の形に入れるため）
TOOL_SETS: dict[str, dict[str, list[str]]] = {
    "interview": {"plan": ["get_plan", "set_fields", "set_supporter"], "hazard": ["get_facts", "find_shelters"]},
    "redteam": {
        "plan": ["get_plan", "get_sources", "add_premise", "add_finding", "propose_fix"],
        "sim": ["run_scenario", "try_fix"],
        "hazard": ["find_shelters"],
    },
    "verify": {"plan": ["get_plan", "get_sources"]},
}


class ToolsNotReady(RuntimeError):
    """tools/ の関数がまだ実装されていない（NotImplementedError）。"""


def _ok(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}


def _error(reasons: list[str] | str) -> dict[str, Any]:
    text = reasons if isinstance(reasons, str) else "\n".join(reasons)
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def shelter_view(shelter: pm.Shelter) -> dict[str, Any]:
    """Claude に渡す避難先（座標を除く）。"""
    return {k: v for k, v in shelter.items() if k != "point"}


def scenario_event(scenario_id: str) -> str:
    return f"「{pm.SCENARIOS.get(scenario_id, scenario_id)}」のシナリオで点検しています"


def _call_backend(session: PlanSession, name: str, *args: Any) -> Any:
    try:
        return getattr(session.backend, name)(*args)
    except NotImplementedError as exc:
        session.flags.add("tools_not_ready")
        raise ToolsNotReady("計算のモジュールがまだ準備されていません。点検を終えてください") from exc


def _valid_target(session: PlanSession, target: Any) -> bool:
    if target == "home":
        return True
    return target in {s.get("supporter_id") for s in session.plan.get("supporters", [])}


# ---------------------------------------------------------------------------
# 計画を変更する処理（MCP ツールと orchestrator の両方から使う）
# ---------------------------------------------------------------------------


def write_fields(plan: pm.Plan, items: list[dict[str, Any]]) -> list[str]:
    """確認済みの items を計画に書き込み、書き込んだ欄の一覧を返す。"""
    written = []
    for item in items:
        value = pm.FieldValue(value=item["value"], status="filled", source_id=item["source_id"])
        if item.get("quote"):
            value["quote"] = item["quote"]
        plan["fields"][item["field"]] = value
        written.append(item["field"])
    return written


def apply_fix(plan: pm.Plan, fix_id: str, backend: Any) -> list[str]:
    """承認済みの改善案を計画に反映する。反映できなければ理由の一覧を返す（F-11）。

    CHANGE_SHELTER は避難先の欄を書き換える。そのほかの改善案は欄を書き換えず、
    sim.run_scenario が plan["approvals"] にある改善案の効き目を反映する（A との取り決め）。
    """
    reasons = hooks.check_apply_fix(plan, fix_id)
    if reasons:
        return reasons
    fix = hooks.find_fix(plan, fix_id)
    if fix["fix_type"] == "CHANGE_SHELTER":
        try:
            shelters = backend.find_shelters(plan, "home", 20)
        except NotImplementedError as exc:
            raise ToolsNotReady(str(exc)) from exc
        shelter = next((s for s in shelters if s["shelter_id"] == fix.get("target_id")), None)
        if shelter is None:
            return ["改善案の避難先が見つかりません。もう一度点検してください"]
        plan["fields"]["shelter"] = pm.FieldValue(
            value={"text": shelter["name"], "shelter_id": shelter["shelter_id"]},
            status="filled",
            source_id=shelter["source_id"],
        )
    return []


# ---------------------------------------------------------------------------
# MCP ツール
# ---------------------------------------------------------------------------


def make_tools(session: PlanSession) -> dict[str, dict[str, SdkMcpTool[Any]]]:
    plan = session.plan

    # --- plan ---

    @tool("get_plan", "現在の計画（仮名化済み）を返す。座標は含まない。", {"type": "object", "properties": {}})
    async def get_plan(args: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            return _ok(privacy.view_for_claude(plan))

    @tool(
        "get_sources",
        "出典リストの中身（発言の原文、データの値と版、仮定の根拠）を返す。source_ids を省略するとすべて返す。",
        {"type": "object", "properties": {"source_ids": {"type": "array", "items": {"type": "string"}}}},
    )
    async def get_sources(args: dict[str, Any]) -> dict[str, Any]:
        ids = args.get("source_ids") or list(plan["sources"])
        with session.lock:
            view = privacy.view_for_claude(plan)["sources"]
            return _ok({sid: view[sid] for sid in ids if sid in view})

    @tool(
        "set_fields",
        "計画の欄に値を書き込む。すべての項目に出典 ID（source_id）が必要。出典が発言なら、quote に発言の原文からそのまま抜き出した引用を入れる。"
        "話していない値・数値は書き込まない（聞き取れていない欄は書き込まず、未確認のままにする）。",
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "enum": list(pm.FIELDS)},
                            "value": {"type": ["string", "object", "number"],
                                      "description": "文字列。walk_speed は {text, meters, minutes}、time_to_leave_day/night は {text, minutes}、shelter は {text, shelter_id}"},
                            "source_id": {"type": "string"},
                            "quote": {"type": "string", "description": "発言の原文からそのまま抜き出した引用"},
                        },
                        "required": ["field", "value", "source_id"],
                    },
                }
            },
            "required": ["items"],
        },
    )
    async def set_fields(args: dict[str, Any]) -> dict[str, Any]:
        items = args.get("items")
        with session.lock:
            shelter_ids = set(session.known_shelters) if session.known_shelters else None
            reasons = hooks.check_set_fields(plan, items, shelter_ids)
            if reasons:
                return _error(reasons)
            written = write_fields(plan, items)
        return _ok({"written": written})

    @tool(
        "set_supporter",
        "避難支援者を計画に追加・更新する。supporter_id を省略すると新しく追加する（alias は仮名。例: Bさん）。"
        "根拠の発言の source_id と、原文からそのまま抜き出した quote が必要。",
        {
            "type": "object",
            "properties": {
                "supporter_id": {"type": "string"},
                "alias": {"type": "string"},
                "relation": {"type": "string", "description": "例: 隣人、長男"},
                "age_band": {"type": "string", "description": "例: 70代（話したときだけ）"},
                "available_day": {"type": "boolean"},
                "available_night": {"type": "boolean"},
                "source_id": {"type": "string"},
                "quote": {"type": "string"},
            },
            "required": ["source_id", "quote"],
        },
    )
    async def set_supporter(args: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            reasons = hooks.check_set_supporter(plan, args)
            if reasons:
                return _error(reasons)
            supporter = next((s for s in plan["supporters"] if s["supporter_id"] == args.get("supporter_id")), None)
            if supporter is None:
                supporter = pm.Supporter(supporter_id=pm.new_id("sup"), alias=args["alias"],
                                         relation=args.get("relation", ""), source_id=args["source_id"])
                plan["supporters"].append(supporter)
            for key in ("alias", "relation", "age_band", "available_day", "available_night"):
                if args.get(key) is not None:
                    supporter[key] = args[key]
            supporter["source_id"] = args["source_id"]
            return _ok({k: v for k, v in supporter.items() if k != "location"})

    @tool(
        "add_premise",
        "聞き取りの話の中だけにある前提条件（S8）を計画に追加する。quote は発言の原文からそのまま抜き出す。"
        "about には、対象の避難先の shelter_id や道路の呼び名を入れる（ある場合だけ）。",
        {
            "type": "object",
            "properties": {
                "premise_code": {"type": "string", "enum": list(pm.PREMISE_CODES)},
                "quote": {"type": "string"},
                "source_id": {"type": "string"},
                "about": {"type": "string"},
            },
            "required": ["premise_code", "quote", "source_id"],
        },
    )
    async def add_premise(args: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            reasons = hooks.check_add_premise(plan, args)
            if reasons:
                return _error(reasons)
            premise = pm.Premise(premise_id=pm.new_id("prem"), code=args["premise_code"], quote=args["quote"],
                                 source_id=args["source_id"])
            if args.get("about"):
                premise["about"] = args["about"]
            plan["premises"].append(premise)
        session.add_event(f"話の中の前提条件を見つけました: {pm.PREMISE_CODES[args['premise_code']]}")
        return _ok({"premise_id": premise["premise_id"]})

    @tool(
        "add_finding",
        "シナリオで見つかった問題点を計画に追加する。explanation に数値を直接書かない。数値は {need_min} {avail_min} "
        "{margin_min} {shortage_min} {leave_min} {walk_min} {climb_min} {help_min} {supporter_margin_min} "
        "{supporter_shortage_min} {shelter_name} のプレースホルダーで書く（コードが計算の結果で置き換える）。"
        "結果は、同じセッションで直前に sim.run_scenario で計算したものを使う。",
        {
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string", "enum": list(pm.SCENARIOS)},
                "hole_type": {"type": "string", "enum": list(pm.HOLE_TYPES)},
                "explanation": {"type": "string"},
                "source_ids": {"type": "array", "items": {"type": "string"}},
                "premise_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["scenario_id", "hole_type", "explanation", "source_ids"],
        },
    )
    async def add_finding(args: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            reasons = hooks.check_add_finding(plan, args)
            if reasons:
                return _error(reasons)
            try:
                result = hooks.scenario_result_for(session, args["scenario_id"], args.get("premise_ids"))
            except NotImplementedError as exc:
                session.flags.add("tools_not_ready")
                raise ToolsNotReady("計算のモジュールがまだ準備されていません。点検を終えてください") from exc
            except ValueError as exc:
                return _error(f"計算できませんでした: {exc}")
            reasons = hooks.check_add_finding(plan, args, result)
            if reasons:
                return _error(reasons)
            values = ph.placeholder_values(result, plan)
            finding = pm.Finding(
                finding_id=pm.new_id("fnd"),
                scenario_id=args["scenario_id"],
                hole_type=args["hole_type"],
                explanation_template=args["explanation"],
                explanation=ph.fill(args["explanation"], values),
                result=copy.deepcopy(result),
                source_ids=list(args["source_ids"]),
            )
            plan["findings"].append(finding)
        session.add_event(f"「{pm.SCENARIOS[args['scenario_id']]}」のシナリオで問題点を見つけました")
        return _ok({"finding_id": finding["finding_id"], "verdict": result["verdict"], "explanation": finding["explanation"]})

    @tool(
        "propose_fix",
        "問題点への改善案を追加する（1つの問題点に3つまで）。fix_type は決まった種類だけ。効き目は sim.try_fix でコードが計算する。"
        "explanation に数値を直接書かない（{saved_min} {need_min} {margin_min} {shelter_name} などのプレースホルダーで書く）。"
        "危険な改善案（車での避難、様子を見る、浸水が想定される自宅の上の階への避難、津波の対象でない避難先）は出さない。",
        {
            "type": "object",
            "properties": {
                "finding_id": {"type": "string"},
                "fix_type": {"type": "string", "enum": list(pm.FIX_TYPES)},
                "target_id": {"type": "string", "description": "CHANGE_SHELTER は shelter_id、ADD_CALLER は supporter_id"},
                "explanation": {"type": "string"},
            },
            "required": ["finding_id", "fix_type", "explanation"],
        },
    )
    async def propose_fix(args: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            known = session.known_shelters or None
            reasons = hooks.check_propose_fix(plan, args, known_shelters=known)
            if reasons:
                return _error(reasons)
            finding = hooks.find_finding(plan, args["finding_id"])
            try:
                result = _call_backend(session, "try_fix", plan, finding["scenario_id"], args["fix_type"], args.get("target_id"))
            except ValueError as exc:
                return _error(f"この改善案は使えません: {exc}")
            reasons = hooks.check_propose_fix(plan, args, result, known)
            if reasons:
                return _error(reasons)
            saved = hooks.saved_minutes(finding["result"], result)
            name = known[args["target_id"]]["name"] if known and args.get("target_id") in known else None
            values = ph.placeholder_values(result, plan, saved_min=saved, shelter_name=name)
            fix = pm.Fix(
                fix_id=pm.new_id("fix"),
                finding_id=finding["finding_id"],
                fix_type=args["fix_type"],
                explanation_template=args["explanation"],
                explanation=ph.fill(args["explanation"], values),
                result=copy.deepcopy(result),
                saved_min=saved,
            )
            if args.get("target_id"):
                fix["target_id"] = args["target_id"]
            plan["fixes"].append(fix)
        return _ok({"fix_id": fix["fix_id"], "verdict": result["verdict"], "explanation": fix["explanation"]})

    @tool("apply_fix", "承認済みの改善案を計画に反映する（承認の記録が無ければフックが拒否する）。",
          {"type": "object", "properties": {"fix_id": {"type": "string"}}, "required": ["fix_id"]})
    async def apply_fix_tool(args: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            reasons = apply_fix(plan, args.get("fix_id", ""), session.backend)
        return _error(reasons) if reasons else _ok({"applied": args.get("fix_id")})

    @tool("finalize", "計画に完成の印を付ける（コードによる点検で1つでも欠ければフックが拒否する）。",
          {"type": "object", "properties": {}})
    async def finalize_tool(args: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            reasons = hooks.check_finalize(plan, session.backend)
            if reasons:
                return _error(reasons)
            plan["status"] = "final"
        return _ok({"status": "final"})

    # --- hazard ---

    @tool(
        "get_facts",
        "対象（\"home\" か避難支援者の supporter_id）の地点の浸水深の階級・津波到達時間（版つき）・標高と出典 ID を返す。座標は受け取らない。",
        {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
    )
    async def get_facts(args: dict[str, Any]) -> dict[str, Any]:
        target = args.get("target", "home")
        if not _valid_target(session, target):
            return _error("target は \"home\" か、計画の避難支援者の supporter_id にしてください")
        with session.lock:
            facts = _call_backend(session, "get_facts", plan, target)
        return _ok(facts)

    @tool(
        "find_shelters",
        "対象（\"home\" か supporter_id）から近い、津波の指定緊急避難場所を近い順に返す（距離は直線距離×係数の概算）。",
        {
            "type": "object",
            "properties": {"target": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 5}},
            "required": ["target"],
        },
    )
    async def find_shelters(args: dict[str, Any]) -> dict[str, Any]:
        target = args.get("target", "home")
        if not _valid_target(session, target):
            return _error("target は \"home\" か、計画の避難支援者の supporter_id にしてください")
        limit = max(1, min(int(args.get("limit") or 3), 5))
        with session.lock:
            shelters = _call_backend(session, "find_shelters", plan, target, limit)
            for shelter in shelters:
                session.known_shelters[shelter["shelter_id"]] = shelter
        session.add_event("近くの津波避難場所を調べています")
        return _ok([shelter_view(s) for s in shelters])

    # --- sim ---

    @tool(
        "run_scenario",
        "シナリオ（S1〜S8）を計画に当てはめ、避難が間に合うかをコードで計算する。premise_ids は plan.add_premise で登録した前提条件だけ。"
        "値を外から渡す引数は無い。",
        {
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string", "enum": list(pm.SCENARIOS)},
                "premise_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["scenario_id"],
        },
    )
    async def run_scenario(args: dict[str, Any]) -> dict[str, Any]:
        scenario_id = args["scenario_id"]
        session.add_event(scenario_event(scenario_id))
        with session.lock:
            try:
                result = _call_backend(session, "run_scenario", plan, scenario_id, args.get("premise_ids") or None)
            except ValueError as exc:
                return _error(f"計算できませんでした: {exc}")
            session.last_results[scenario_id] = result
        return _ok(result)

    @tool(
        "try_fix",
        "改善案（fix_type）を当てはめた場合の結果をコードで計算する。計画そのものは変更しない。危険な改善案は理由つきで拒否される。",
        {
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string", "enum": list(pm.SCENARIOS)},
                "fix_type": {"type": "string", "enum": list(pm.FIX_TYPES)},
                "target_id": {"type": "string"},
            },
            "required": ["scenario_id", "fix_type"],
        },
    )
    async def try_fix(args: dict[str, Any]) -> dict[str, Any]:
        with session.lock:
            try:
                result = _call_backend(session, "try_fix", plan, args["scenario_id"], args["fix_type"], args.get("target_id"))
            except ValueError as exc:
                return _error(f"この改善案は使えません: {exc}")
        return _ok(result)

    return {
        "plan": {
            "get_plan": get_plan,
            "get_sources": get_sources,
            "set_fields": set_fields,
            "set_supporter": set_supporter,
            "add_premise": add_premise,
            "add_finding": add_finding,
            "propose_fix": propose_fix,
            "apply_fix": apply_fix_tool,
            "finalize": finalize_tool,
        },
        "hazard": {"get_facts": get_facts, "find_shelters": find_shelters},
        "sim": {"run_scenario": run_scenario, "try_fix": try_fix},
    }


def build_servers(session: PlanSession, role: str) -> tuple[dict[str, Any], list[str]]:
    """エージェントの役割ごとの MCP サーバーと、自動で許可する MCP ツールの名前（mcp__plan__set_fields など）。"""
    tools_by_server = make_tools(session)
    servers: dict[str, Any] = {}
    allowed: list[str] = []
    for server, names in TOOL_SETS[role].items():
        servers[server] = create_sdk_mcp_server(
            name=server, version="1.0.0", tools=[tools_by_server[server][n] for n in names]
        )
        allowed += [f"mcp__{server}__{n}" for n in names]
    return servers, allowed


def approval_record(fix_id: str, by: str) -> pm.Approval:
    return pm.Approval(fix_id=fix_id, by=by, at=now_iso())  # type: ignore[typeddict-item]
