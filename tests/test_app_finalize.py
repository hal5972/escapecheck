"""完成のチェック（コードによる点検）のテスト。

- AC-03: 検証エージェントの未解決の項目がある間は「完成」にできない
- F-12: 必須の欄が埋まっているか「未確認」の印があるか、すべての欄に有効な出典があるか、
  赤のシナリオが改善されたか「本人・家族と相談する」の印があるか

計算はテスト用の偽の計算（FakeBackend）で置き換える。サンプルデータはすべてデモ用の架空の人物。
"""

import asyncio
import unittest

from app import hooks, orchestrator
from app.audit import AuditLog
from app.session import PlanSession
from tools import plan_model as pm

UTTERANCE = (
    "Aさんは一人暮らし。杖ついちゅう。100メートル歩くのに三分くらいかかる。夜は2階で寝ゆう。"
    "昼は家におって、出るまで5分くらい。夜は着替えて10分くらいかかる。耳はよう聞こえる。"
    "隣のBさんが声をかけてくれる。Bさんが一緒に宇佐のタワーまで行く。タワーへは県道を通る。電話は家の電話。"
)

QUOTES = {
    "household": ("一人暮らし", "一人暮らし"),
    "mobility": ("杖を使って歩く", "杖ついちゅう"),
    "walk_speed": ({"text": "100mを3分くらい", "meters": 100, "minutes": 3}, "100メートル歩くのに三分くらいかかる"),
    "sleep_place": ("2階", "夜は2階で寝ゆう"),
    "time_to_leave_day": ({"text": "5分くらい", "minutes": 5}, "出るまで5分くらい"),
    "time_to_leave_night": ({"text": "着替えて10分くらい", "minutes": 10}, "夜は着替えて10分くらいかかる"),
    "hearing_and_sight": ("よく聞こえる", "耳はよう聞こえる"),
    "supporters": ("隣のBさん", "隣のBさんが声をかけてくれる"),
    "support_method": ("Bさんが声をかけ、一緒に避難する", "Bさんが一緒に宇佐のタワーまで行く"),
    "shelter": ({"text": "宇佐のタワー"}, "宇佐のタワーまで行く"),
    "route": ("県道を通る", "タワーへは県道を通る"),
    "contact": ("家の電話", "電話は家の電話"),
}


def make_complete_plan():
    plan = pm.new_plan("Aさん", "宇佐")
    pm.register_source(plan, pm.Source(kind="utterance", label="民生委員の発言", text=UTTERANCE, speaker="minsei"), "src_utt1")
    for field, (value, quote) in QUOTES.items():
        plan["fields"][field] = pm.FieldValue(value=value, status="filled", source_id="src_utt1", quote=quote)
    return plan


class FakeBackend:
    """シナリオごとの判定を決めておく、テスト用の偽の計算。"""

    def __init__(self, verdicts=None):
        self.verdicts = verdicts or {}

    def run_scenario(self, plan, scenario_id, premise_ids=None):
        verdict = self.verdicts.get(scenario_id, "ok")
        margin = {"ok": 5.0, "tight": 1.0, "short": -4.0, "cannot_compute": None}[verdict]
        return pm.ScenarioResult(
            scenario_id=scenario_id,
            verdict=verdict,
            need_min=None if margin is None else 15.0 - margin,
            avail_min=None if margin is None else 15.0,
            margin_min=margin,
            breakdown=[],
            assumptions=[],
        )

    def get_facts(self, plan, target="home"):
        raise AssertionError("not used")

    def find_shelters(self, plan, target="home", limit=3):
        return []

    def try_fix(self, plan, scenario_id, fix_type, target_id=None):
        raise AssertionError("not used")


def ack(plan, issue_id):
    for issue in plan["issues"]:
        if issue["issue_id"] == issue_id:
            issue["acknowledged"] = True
            return
    plan["issues"].append(pm.Issue(issue_id=issue_id, message="確認済み", severity="要確認", acknowledged=True))


class CheckFinalizeTest(unittest.TestCase):
    def test_complete_plan_can_be_finalized(self):
        self.assertEqual(hooks.check_finalize(make_complete_plan(), FakeBackend()), [])

    def test_unacknowledged_verify_issue_blocks(self):
        # AC-03: 検証エージェントの「要確認」が残る間は完成にできない
        plan = make_complete_plan()
        plan["issues"].append(
            pm.Issue(issue_id="llm_1", field="walk_speed", message="歩行速度の出典を確認してください", severity="要確認",
                     acknowledged=False)
        )
        reasons = hooks.check_finalize(plan, FakeBackend())
        self.assertTrue(reasons)
        self.assertTrue(any("歩行速度の出典を確認してください" in r for r in reasons))
        plan["issues"][0]["acknowledged"] = True
        self.assertEqual(hooks.check_finalize(plan, FakeBackend()), [])

    def test_unconfirmed_required_field_blocks_until_acknowledged(self):
        plan = make_complete_plan()
        plan["fields"]["sleep_place"] = pm.FieldValue(value=None, status="unconfirmed")
        reasons = hooks.check_finalize(plan, FakeBackend())
        self.assertTrue(any(pm.FIELDS["sleep_place"] in r for r in reasons))
        ack(plan, "chk_unconfirmed_sleep_place")
        self.assertEqual(hooks.check_finalize(plan, FakeBackend()), [])

    def test_short_scenario_blocks_until_acknowledged(self):
        plan = make_complete_plan()
        backend = FakeBackend({"S1": "short"})
        reasons = hooks.check_finalize(plan, backend)
        self.assertTrue(any(pm.SCENARIOS["S1"] in r for r in reasons))
        ack(plan, "chk_short_S1")
        self.assertEqual(hooks.check_finalize(plan, backend), [])

    def test_scenario_that_cannot_be_computed_needs_acknowledgement(self):
        plan = make_complete_plan()
        backend = FakeBackend({"S5": "cannot_compute"})
        self.assertTrue(hooks.check_finalize(plan, backend))
        ack(plan, "chk_cannot_S5")
        self.assertEqual(hooks.check_finalize(plan, backend), [])

    def test_field_with_invalid_source_blocks_even_if_acknowledged(self):
        plan = make_complete_plan()
        plan["fields"]["route"]["source_id"] = "src_missing"
        for issue in hooks.code_issues(plan, FakeBackend()):
            ack(plan, issue["issue_id"])
        reasons = hooks.check_finalize(plan, FakeBackend())
        self.assertTrue(any(pm.FIELDS["route"] in r for r in reasons))

    def test_field_whose_quote_is_not_in_text_blocks(self):
        plan = make_complete_plan()
        plan["fields"]["hearing_and_sight"]["quote"] = "耳が遠い"
        reasons = hooks.check_finalize(plan, FakeBackend())
        self.assertTrue(any(pm.FIELDS["hearing_and_sight"] in r for r in reasons))

    def test_code_issue_ids_are_stable(self):
        # 人が付けた確認の印を、もう一度検証しても失わないように、ID は毎回同じにする
        plan = make_complete_plan()
        plan["fields"]["contact"] = pm.FieldValue(value=None, status="unconfirmed")
        backend = FakeBackend({"S2": "short"})
        first = [i["issue_id"] for i in hooks.code_issues(plan, backend)]
        second = [i["issue_id"] for i in hooks.code_issues(plan, backend)]
        self.assertEqual(first, second)
        self.assertIn("chk_unconfirmed_contact", first)
        self.assertIn("chk_short_S2", first)


class OrchestratorFinalizeTest(unittest.TestCase):
    def setUp(self):
        self._restore = orchestrator.override(backend=FakeBackend())

    def tearDown(self):
        self._restore()

    def test_finalize_rejects_and_keeps_status(self):
        plan = make_complete_plan()
        plan["issues"].append(pm.Issue(issue_id="llm_1", message="要確認の項目", severity="要確認", acknowledged=False))
        with self.assertRaises(orchestrator.FinalizeRejected) as ctx:
            orchestrator.finalize(plan)
        self.assertTrue(ctx.exception.reasons)
        self.assertEqual(plan["status"], "draft")

    def test_finalize_sets_final_status(self):
        plan = orchestrator.finalize(make_complete_plan())
        self.assertEqual(plan["status"], "final")


class FinalizeToolHookTest(unittest.TestCase):
    """plan.finalize の MCP ツールも、PreToolUse フックが同じ点検で拒否する。"""

    def test_hook_denies_finalize_with_open_issue(self):
        plan = make_complete_plan()
        plan["issues"].append(pm.Issue(issue_id="llm_1", message="要確認の項目", severity="要確認", acknowledged=False))
        audit = AuditLog()
        session = PlanSession(plan=plan, role="verify", audit=audit, backend=FakeBackend())
        hook_map = hooks.build_hooks(session)
        data = {"hook_event_name": "PreToolUse", "tool_name": "mcp__plan__finalize", "tool_input": {},
                "tool_use_id": "t", "session_id": "s", "transcript_path": "", "cwd": ""}
        outputs = [asyncio.run(cb(data, "t", {"signal": None})) for m in hook_map["PreToolUse"] for cb in m.hooks]
        self.assertTrue([o for o in outputs if o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"])
        self.assertEqual(audit.entries(plan_id=plan["plan_id"])[-1]["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
