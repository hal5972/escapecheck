"""作品の中のフック（app/hooks.py）のテスト。

- AC-02: 出典のない欄の書き込みがフックで拒否され、記録（監査ログ）に残る
- AC-10: 原文に無い引用（ハルシネーション）、出典リストと異なる数値、説明文に直接記載した数値が拒否される
- F-11: 承認の記録が無い改善案の反映は拒否される

サンプルデータはすべてデモ用の架空の人物。
"""

import asyncio
import unittest

from app import hooks
from app.audit import AuditLog
from app.session import PlanSession
from tools import plan_model as pm


def make_plan():
    """架空の要支援者（Aさん）の計画。発言1件、データ1件、仮定1件の出典を持つ。"""
    plan = pm.new_plan("Aさん", "宇佐")
    pm.register_source(
        plan,
        pm.Source(
            kind="utterance",
            label="民生委員の発言",
            text="Aさんは杖ついちゅう。100メートル歩くのに三分くらいかかる。夜は2階で寝ゆう。",
            speaker="minsei",
            at="2026-11-08T10:42:00+09:00",
        ),
        "src_utt1",
    )
    pm.register_source(
        plan,
        pm.Source(kind="dataset", label="テスト用のデータ（津波到達時間）", name="テスト用", value=15, unit="分"),
        "src_arrival",
    )
    pm.register_source(
        plan,
        pm.Source(kind="assumption", label="仮定（歩行速度）", value=0.5, unit="m/秒", basis="2005年のガイドライン"),
        "src_speed",
    )
    return plan


class SetFieldsSourceTest(unittest.TestCase):
    """AC-02: すべての項目に出典 ID があり、出典リストにあるか。"""

    def test_rejects_item_without_source_id(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(plan, [{"field": "mobility", "value": "杖を使う"}])
        self.assertTrue(reasons)
        self.assertIn("出典", reasons[0])
        self.assertIn("mobility", reasons[0])

    def test_rejects_source_id_not_in_source_list(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan, [{"field": "mobility", "value": "杖を使う", "source_id": "src_missing", "quote": "杖ついちゅう"}]
        )
        self.assertTrue(reasons)
        self.assertIn("出典リスト", reasons[0])

    def test_rejects_unknown_field(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan, [{"field": "real_name", "value": "x", "source_id": "src_utt1", "quote": "杖ついちゅう"}]
        )
        self.assertTrue(reasons)

    def test_rejects_empty_items(self):
        self.assertTrue(hooks.check_set_fields(make_plan(), []))
        self.assertTrue(hooks.check_set_fields(make_plan(), None))

    def test_accepts_valid_utterance_item(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan, [{"field": "mobility", "value": "杖を使って歩く", "source_id": "src_utt1", "quote": "杖ついちゅう"}]
        )
        self.assertEqual(reasons, [])

    def test_rejects_whole_call_when_one_item_is_bad(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan,
            [
                {"field": "mobility", "value": "杖を使って歩く", "source_id": "src_utt1", "quote": "杖ついちゅう"},
                {"field": "sleep_place", "value": "1階"},
            ],
        )
        self.assertEqual(len(reasons), 1)
        self.assertIn("sleep_place", reasons[0])


class QuoteHallucinationTest(unittest.TestCase):
    """AC-10: 出典が発言の場合、quote が発言の原文にそのまま含まれるか。"""

    def test_rejects_quote_not_in_utterance(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan,
            [{"field": "hearing_and_sight", "value": "耳が遠い", "source_id": "src_utt1", "quote": "耳が遠うて放送は聞こえん"}],
        )
        self.assertTrue(reasons)
        self.assertIn("原文", reasons[0])

    def test_rejects_missing_quote_for_utterance(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(plan, [{"field": "mobility", "value": "杖を使う", "source_id": "src_utt1"}])
        self.assertTrue(reasons)
        self.assertIn("引用", reasons[0])

    def test_rejects_too_short_quote(self):
        # 1文字の引用はどの発言にも含まれやすく、根拠にならない
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan, [{"field": "mobility", "value": "杖を使う", "source_id": "src_utt1", "quote": "杖"}]
        )
        self.assertTrue(reasons)

    def test_ignores_spaces_and_brackets_around_quote(self):
        # 音声認識が入れた空白や、引用を囲む「」は、照合の前に取り除く
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan, [{"field": "sleep_place", "value": "2階", "source_id": "src_utt1", "quote": "「夜は 2階で寝ゆう」"}]
        )
        self.assertEqual(reasons, [])

    def test_rejects_number_in_value_that_is_not_in_quote(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan,
            [
                {
                    "field": "walk_speed",
                    "value": {"text": "100mを5分", "meters": 100, "minutes": 5},
                    "source_id": "src_utt1",
                    "quote": "100メートル歩くのに三分くらいかかる",
                }
            ],
        )
        self.assertTrue(reasons)
        self.assertIn("5", reasons[0])

    def test_ids_in_value_are_not_numbers(self):
        # 指定緊急避難場所のデータの ID（数字を含む）は、話した数値ではない
        plan = make_plan()
        plan["sources"]["src_utt1"]["text"] += "逃げる先は、宇佐の津波避難タワーにしちゅう。"
        reasons = hooks.check_set_fields(plan, [{
            "field": "shelter", "value": {"name": "宇佐津波避難タワー", "shelter_id": "E3920500119201"},
            "source_id": "src_utt1", "quote": "宇佐の津波避難タワー"}])
        self.assertEqual(reasons, [])

    def test_accepts_kanji_numeral_in_quote(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(
            plan,
            [
                {
                    "field": "walk_speed",
                    "value": {"text": "100mを3分くらい", "meters": 100, "minutes": 3},
                    "source_id": "src_utt1",
                    "quote": "100メートル歩くのに三分くらいかかる",
                }
            ],
        )
        self.assertEqual(reasons, [])


class DatasetValueTest(unittest.TestCase):
    """AC-10: 出典がデータや仮定の場合、数値が出典リストの値と一致するか。"""

    def test_rejects_value_different_from_dataset(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(plan, [{"field": "notes", "value": "津波の到達まで12分", "source_id": "src_arrival"}])
        self.assertTrue(reasons)
        self.assertIn("出典リスト", reasons[0])

    def test_accepts_value_matching_dataset(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(plan, [{"field": "notes", "value": "津波の到達まで15分", "source_id": "src_arrival"}])
        self.assertEqual(reasons, [])

    def test_rejects_value_different_from_assumption(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(plan, [{"field": "walk_speed", "value": "0.6 m/秒", "source_id": "src_speed"}])
        self.assertTrue(reasons)

    def test_accepts_value_matching_assumption(self):
        plan = make_plan()
        reasons = hooks.check_set_fields(plan, [{"field": "walk_speed", "value": "０．５ m/秒（仮定）", "source_id": "src_speed"}])
        self.assertEqual(reasons, [])


class PremiseQuoteTest(unittest.TestCase):
    """AC-10: plan.add_premise の quote が話の原文にそのまま含まれるか。"""

    def test_accepts_premise_with_quote_in_text(self):
        plan = make_plan()
        reasons = hooks.check_add_premise(
            plan, {"premise_code": "SLEEP_UPSTAIRS", "quote": "夜は2階で寝ゆう", "source_id": "src_utt1"}
        )
        self.assertEqual(reasons, [])

    def test_rejects_premise_with_hallucinated_quote(self):
        plan = make_plan()
        reasons = hooks.check_add_premise(
            plan, {"premise_code": "CANNOT_HEAR", "quote": "耳が遠うて、放送は聞こえん", "source_id": "src_utt1"}
        )
        self.assertTrue(reasons)
        self.assertIn("原文", reasons[0])

    def test_rejects_unknown_premise_code(self):
        plan = make_plan()
        reasons = hooks.check_add_premise(plan, {"premise_code": "FLY", "quote": "夜は2階で寝ゆう", "source_id": "src_utt1"})
        self.assertTrue(reasons)

    def test_rejects_premise_whose_source_is_not_an_utterance(self):
        plan = make_plan()
        reasons = hooks.check_add_premise(plan, {"premise_code": "SLEEP_UPSTAIRS", "quote": "15", "source_id": "src_arrival"})
        self.assertTrue(reasons)


class ExplanationNumberTest(unittest.TestCase):
    """AC-10: 説明文に数値を直接記載していないか（プレースホルダーの外の数値は拒否）。"""

    def test_rejects_digits_outside_placeholders(self):
        reasons = hooks.check_explanation("この計画だと、あと5分足りません。")
        self.assertTrue(reasons)
        self.assertIn("数値", reasons[0])

    def test_rejects_fullwidth_digits(self):
        self.assertTrue(hooks.check_explanation("避難に１２分かかります。"))

    def test_rejects_kanji_numerals_with_units(self):
        self.assertTrue(hooks.check_explanation("避難には十二分かかります。"))
        self.assertTrue(hooks.check_explanation("三百メートル先のタワーです。"))

    def test_accepts_placeholders(self):
        text = "夜は着替えに時間がかかり、避難に{need_min}分かかります。津波の到達まで{avail_min}分なので、{shortage_min}分不足します。"
        self.assertEqual(hooks.check_explanation(text), [])

    def test_accepts_kanji_one_in_ordinary_words(self):
        self.assertEqual(hooks.check_explanation("一人では避難を始められません。{shelter_name}まで一緒に避難する人が必要です。"), [])

    def test_rejects_unknown_placeholder(self):
        reasons = hooks.check_explanation("避難に{minutes}分かかります。")
        self.assertTrue(reasons)
        self.assertIn("minutes", reasons[0])

    def test_rejects_broken_braces(self):
        self.assertTrue(hooks.check_explanation("避難に{need_min分かかります。"))

    def test_rejects_empty_explanation(self):
        self.assertTrue(hooks.check_explanation(""))

    def test_add_finding_rejects_number_in_explanation(self):
        plan = make_plan()
        reasons = hooks.check_add_finding(
            plan,
            {"scenario_id": "S1", "hole_type": "NIGHT_PREP", "explanation": "夜は5分多くかかります。", "source_ids": ["src_utt1"]},
        )
        self.assertTrue(reasons)

    def test_add_finding_rejects_unknown_hole_type_and_scenario(self):
        plan = make_plan()
        reasons = hooks.check_add_finding(
            plan, {"scenario_id": "S9", "hole_type": "NOPE", "explanation": "説明", "source_ids": ["src_utt1"]}
        )
        self.assertEqual(len(reasons), 2)

    def test_add_finding_rejects_source_id_not_in_source_list(self):
        plan = make_plan()
        reasons = hooks.check_add_finding(
            plan,
            {"scenario_id": "S1", "hole_type": "NIGHT_PREP", "explanation": "夜は準備に時間がかかります。", "source_ids": ["src_x"]},
        )
        self.assertTrue(reasons)

    def test_add_finding_rejects_placeholder_that_contradicts_result(self):
        # 余裕があるシナリオで「不足」のプレースホルダーを使うと、結果と食い違う
        plan = make_plan()
        result = pm.ScenarioResult(
            scenario_id="S1", verdict="ok", need_min=10.0, avail_min=15.0, margin_min=5.0, breakdown=[], assumptions=[]
        )
        reasons = hooks.check_add_finding(
            plan,
            {"scenario_id": "S1", "hole_type": "NIGHT_PREP", "explanation": "{shortage_min}分不足します。", "source_ids": ["src_utt1"]},
            result=result,
        )
        self.assertTrue(reasons)
        self.assertIn("shortage_min", reasons[0])

    def test_propose_fix_rejects_number_in_explanation(self):
        plan = make_plan()
        plan["findings"].append(
            pm.Finding(
                finding_id="fnd_1", scenario_id="S1", hole_type="NIGHT_PREP", explanation_template="説明",
                result=pm.ScenarioResult(scenario_id="S1", verdict="short", need_min=18.0, avail_min=15.0,
                                         margin_min=-3.0, breakdown=[], assumptions=[]),
                source_ids=["src_utt1"],
            )
        )
        reasons = hooks.check_propose_fix(
            plan, {"finding_id": "fnd_1", "fix_type": "BEDSIDE_PREP", "explanation": "3分早く出られます。"}
        )
        self.assertTrue(reasons)

    def test_propose_fix_rejects_unknown_fix_type_and_finding(self):
        plan = make_plan()
        reasons = hooks.check_propose_fix(plan, {"finding_id": "fnd_x", "fix_type": "GO_UPSTAIRS", "explanation": "説明"})
        self.assertEqual(len(reasons), 2)


class ApplyFixApprovalTest(unittest.TestCase):
    """F-11: 祖母（民生委員）の承認の記録が無ければ、改善案を反映しない。"""

    def setUp(self):
        self.plan = make_plan()
        self.plan["fixes"].append(
            pm.Fix(
                fix_id="fix_1", finding_id="fnd_1", fix_type="BEDSIDE_PREP", explanation_template="説明",
                result=pm.ScenarioResult(scenario_id="S1", verdict="ok", need_min=12.0, avail_min=15.0,
                                         margin_min=3.0, breakdown=[], assumptions=[]),
                saved_min=3.0,
            )
        )

    def test_rejects_without_approval(self):
        reasons = hooks.check_apply_fix(self.plan, "fix_1")
        self.assertTrue(reasons)
        self.assertIn("承認", reasons[0])

    def test_accepts_with_approval(self):
        self.plan["approvals"].append(pm.Approval(fix_id="fix_1", by="minsei", at="2026-11-08T11:00:00+09:00"))
        self.assertEqual(hooks.check_apply_fix(self.plan, "fix_1"), [])

    def test_rejects_unknown_fix(self):
        self.plan["approvals"].append(pm.Approval(fix_id="fix_2", by="minsei", at="2026-11-08T11:00:00+09:00"))
        self.assertTrue(hooks.check_apply_fix(self.plan, "fix_2"))


class SdkHookCallbackTest(unittest.TestCase):
    """AC-02: Agent SDK の PreToolUse フックが拒否を返し、監査ログに記録が残る。"""

    def setUp(self):
        self.audit = AuditLog()
        self.session = PlanSession(plan=make_plan(), role="interview", audit=self.audit)
        self.hook_map = hooks.build_hooks(self.session)

    def run_hook(self, event, tool_name, tool_input, tool_response=None):
        data = {
            "hook_event_name": event,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": "toolu_test",
            "session_id": "s",
            "transcript_path": "",
            "cwd": "",
        }
        if tool_response is not None:
            data["tool_response"] = tool_response
        outputs = []
        for matcher in self.hook_map[event]:
            for callback in matcher.hooks:
                outputs.append(asyncio.run(callback(data, "toolu_test", {"signal": None})))
        return outputs

    def test_pre_tool_use_denies_write_without_source(self):
        outputs = self.run_hook("PreToolUse", "mcp__plan__set_fields", {"items": [{"field": "mobility", "value": "杖を使う"}]})
        denied = [o for o in outputs if o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertIn("出典", denied[0]["hookSpecificOutput"]["permissionDecisionReason"])

    def test_denial_is_recorded_in_audit_log(self):
        self.run_hook("PreToolUse", "mcp__plan__set_fields", {"items": [{"field": "mobility", "value": "杖を使う"}]})
        entries = self.audit.entries(plan_id=self.session.plan["plan_id"])
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["decision"], "deny")
        self.assertEqual(entry["tool"], "plan.set_fields")
        self.assertEqual(entry["agent"], "interview")
        self.assertIn("出典", entry["reason"])

    def test_pre_tool_use_allows_valid_write(self):
        outputs = self.run_hook(
            "PreToolUse",
            "mcp__plan__set_fields",
            {"items": [{"field": "mobility", "value": "杖を使う", "source_id": "src_utt1", "quote": "杖ついちゅう"}]},
        )
        self.assertFalse([o for o in outputs if o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"])

    def test_pre_tool_use_denies_builtin_tools(self):
        # 組み込みツールはオプションで外しているが、万一呼ばれてもフックで拒否する（X-02）
        for tool_name in ("Bash", "Read", "Write", "WebFetch"):
            with self.subTest(tool=tool_name):
                outputs = self.run_hook("PreToolUse", tool_name, {"command": "dir"})
                self.assertTrue([o for o in outputs if o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"])

    def test_post_tool_use_records_result(self):
        self.run_hook(
            "PostToolUse",
            "mcp__plan__get_plan",
            {},
            tool_response=[{"type": "text", "text": "{}"}],
        )
        entries = self.audit.entries(plan_id=self.session.plan["plan_id"])
        self.assertEqual(entries[-1]["decision"], "result")
        self.assertEqual(entries[-1]["tool"], "plan.get_plan")


if __name__ == "__main__":
    unittest.main()
