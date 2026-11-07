"""プロセス内の MCP ツール（app/mcp_inprocess.py）のテスト。計算はテスト用の仮の値（StubBackend）。

フックが動かなかった場合に備えて、ツールの本体も書き込む直前に確認し直すことを確かめる（S-02・S-01・S-03・F-11）。
サンプルデータはすべてデモ用の架空の人物。
"""

import asyncio
import json
import unittest

from app import mcp_inprocess, privacy
from app.audit import AuditLog
from app.session import PlanSession
from app.stub_backend import StubBackend, sample_plan
from tools import plan_model as pm

TEXT = ("Aさんは杖ついちゅう。100メートル歩くのに三分くらいかかる。夜は2階で寝ゆう。耳が遠うて、放送は聞こえんがよ。"
        "避難先は避難タワーにしちゅう。")


def make_session(role="redteam"):
    plan = sample_plan()
    pm.register_source(plan, pm.Source(kind="utterance", label="民生委員の発言", text=TEXT, speaker="minsei"), "src_u")
    plan["fields"]["walk_speed"] = pm.FieldValue(value={"text": "100mを3分", "meters": 100, "minutes": 3},
                                                 status="filled", source_id="src_u", quote="100メートル歩くのに三分くらいかかる")
    # 避難先はテスト用の避難タワーA（遠いほう）。高台B に変えると時間が短縮される
    plan["fields"]["shelter"] = pm.FieldValue(value={"name": "避難タワー", "shelter_id": "shel_stub_tower"},
                                              status="filled", source_id="src_u", quote="避難先は避難タワーにしちゅう")
    session = PlanSession(plan=plan, role=role, audit=AuditLog(), backend=StubBackend())
    return session, mcp_inprocess.make_tools(session)


def call(tools, server, name, args):
    result = asyncio.run(tools[server][name].handler(args))
    text = result["content"][0]["text"]
    return result.get("is_error", False), (text if result.get("is_error") else json.loads(text))


class ToolSetTest(unittest.TestCase):
    def test_each_agent_gets_only_its_tools(self):
        session, _ = make_session()
        _, interview = mcp_inprocess.build_servers(session, "interview")
        _, redteam = mcp_inprocess.build_servers(session, "redteam")
        _, verify = mcp_inprocess.build_servers(session, "verify")
        self.assertIn("mcp__plan__set_fields", interview)
        self.assertNotIn("mcp__plan__add_finding", interview)
        self.assertIn("mcp__sim__run_scenario", redteam)
        self.assertNotIn("mcp__plan__set_fields", redteam)
        self.assertEqual(sorted(verify), ["mcp__plan__get_plan", "mcp__plan__get_sources"])
        for names in (interview, redteam, verify):
            self.assertNotIn("mcp__plan__apply_fix", names)  # 反映と完成は人の操作（API）だけ
            self.assertNotIn("mcp__plan__finalize", names)


class ToolBodyChecksTest(unittest.TestCase):
    def test_set_fields_rejects_hallucinated_quote_even_without_hook(self):
        session, tools = make_session("interview")
        is_error, text = call(tools, "plan", "set_fields", {
            "items": [{"field": "hearing_and_sight", "value": "目が悪い", "source_id": "src_u", "quote": "目が悪うて見えん"}]})
        self.assertTrue(is_error)
        self.assertIn("原文", text)
        self.assertEqual(session.plan["fields"]["hearing_and_sight"]["status"], "unconfirmed")

    def test_set_fields_writes_valid_item(self):
        session, tools = make_session("interview")
        is_error, data = call(tools, "plan", "set_fields", {
            "items": [{"field": "mobility", "value": "杖を使う", "source_id": "src_u", "quote": "杖ついちゅう"}]})
        self.assertFalse(is_error)
        self.assertEqual(data["written"], ["mobility"])
        self.assertEqual(session.plan["fields"]["mobility"]["quote"], "杖ついちゅう")

    def test_get_plan_has_no_coordinates(self):
        _, tools = make_session()
        _, data = call(tools, "plan", "get_plan", {})
        self.assertEqual(privacy.find_forbidden_keys(data), [])

    def test_find_shelters_result_has_no_coordinates(self):
        session, tools = make_session()
        _, data = call(tools, "hazard", "find_shelters", {"target": "home", "limit": 2})
        self.assertEqual(privacy.find_forbidden_keys(data), [])
        self.assertTrue(session.known_shelters)

    def test_unknown_target_is_rejected(self):
        _, tools = make_session()
        is_error, _ = call(tools, "hazard", "get_facts", {"target": "33.4,133.4"})
        self.assertTrue(is_error)


class RedTeamFlowTest(unittest.TestCase):
    def test_premise_finding_and_fix_with_placeholders(self):
        session, tools = make_session()
        is_error, data = call(tools, "plan", "add_premise",
                              {"premise_code": "SLEEP_UPSTAIRS", "quote": "夜は2階で寝ゆう", "source_id": "src_u"})
        self.assertFalse(is_error)
        premise_id = data["premise_id"]
        _, result = call(tools, "sim", "run_scenario", {"scenario_id": "S8", "premise_ids": [premise_id]})
        self.assertIn(result["verdict"], pm.VERDICT_LABELS)
        is_error, data = call(tools, "plan", "add_finding", {
            "scenario_id": "S8", "hole_type": "SLEEP_UPSTAIRS", "source_ids": ["src_u"],
            "explanation": "上の階で寝ているので、家を出るまで{leave_min}分かかります。避難に{need_min}分かかります。"})
        self.assertFalse(is_error, data)
        finding = session.plan["findings"][0]
        self.assertEqual(finding["result"], result)  # 説明の数値は、直前の計算の結果から埋める
        self.assertNotIn("{", finding["explanation"])
        self.assertIn(str(int(result["breakdown"][0]["minutes"] + 0.5)), finding["explanation"])

    def test_add_finding_with_number_is_rejected(self):
        session, tools = make_session()
        call(tools, "sim", "run_scenario", {"scenario_id": "S1"})
        is_error, text = call(tools, "plan", "add_finding", {
            "scenario_id": "S1", "hole_type": "NIGHT_PREP", "source_ids": ["src_u"], "explanation": "夜は12分かかります。"})
        self.assertTrue(is_error)
        self.assertEqual(session.plan["findings"], [])

    def _short_finding(self, session):
        session.plan["findings"].append(pm.Finding(
            finding_id="fnd_x", scenario_id="S1", hole_type="TOO_FAR", explanation_template="説明", explanation="説明",
            result=pm.ScenarioResult(scenario_id="S1", verdict="short", need_min=30.0, avail_min=25.0, margin_min=-5.0,
                                     breakdown=[], assumptions=[]),
            source_ids=["src_u"]))

    def test_propose_fix_records_code_computed_saving(self):
        session, tools = make_session()
        self._short_finding(session)
        call(tools, "hazard", "find_shelters", {"target": "home", "limit": 2})
        is_error, data = call(tools, "plan", "propose_fix", {
            "finding_id": "fnd_x", "fix_type": "CHANGE_SHELTER", "target_id": "shel_stub_hill",
            "explanation": "{shelter_name}に変えると、{saved_min}分早く着きます。"})
        self.assertFalse(is_error, data)
        fix = session.plan["fixes"][0]
        self.assertGreater(fix["saved_min"], 0)
        self.assertIn("テスト用の高台B", fix["explanation"])

    def test_propose_fix_rejects_dangerous_wording(self):
        session, tools = make_session()
        self._short_finding(session)
        is_error, text = call(tools, "plan", "propose_fix", {
            "finding_id": "fnd_x", "fix_type": "BEDSIDE_PREP", "explanation": "揺れが収まってから様子を見て、車で避難します。"})
        self.assertTrue(is_error)
        self.assertIn("危険", text)
        self.assertEqual(session.plan["fixes"], [])

    def test_propose_fix_rejects_unknown_shelter(self):
        session, tools = make_session()
        self._short_finding(session)
        call(tools, "hazard", "find_shelters", {"target": "home", "limit": 2})
        is_error, _ = call(tools, "plan", "propose_fix", {
            "finding_id": "fnd_x", "fix_type": "CHANGE_SHELTER", "target_id": "shel_school", "explanation": "避難先を変えます。"})
        self.assertTrue(is_error)

    def test_apply_fix_tool_needs_approval(self):
        session, tools = make_session()
        self._short_finding(session)
        call(tools, "hazard", "find_shelters", {"target": "home", "limit": 2})
        call(tools, "plan", "propose_fix", {"finding_id": "fnd_x", "fix_type": "CHANGE_SHELTER",
                                            "target_id": "shel_stub_hill", "explanation": "避難先を変えます。"})
        fix_id = session.plan["fixes"][0]["fix_id"]
        is_error, text = call(tools, "plan", "apply_fix", {"fix_id": fix_id})
        self.assertTrue(is_error)
        self.assertIn("承認", text)
        session.plan["approvals"].append(pm.Approval(fix_id=fix_id, by="minsei", at="2026-11-08T11:00:00+09:00"))
        is_error, _ = call(tools, "plan", "apply_fix", {"fix_id": fix_id})
        self.assertFalse(is_error)
        self.assertEqual(session.plan["fields"]["shelter"]["value"]["shelter_id"], "shel_stub_hill")


if __name__ == "__main__":
    unittest.main()
