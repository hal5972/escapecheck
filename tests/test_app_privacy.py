"""個人情報・秘密情報の扱い（app/privacy.py）と、説明文のプレースホルダー（app/placeholders.py）のテスト。"""

import os
import unittest
from unittest import mock

from app import placeholders as ph
from app import privacy
from tools import plan_model as pm


class MaskPiiTest(unittest.TestCase):
    def test_masks_phone_numbers(self):
        for text in ("電話は090-1234-5678です", "088-852-1234 にかけて", "０９０１２３４５６７８", "(088)852-1234"):
            with self.subTest(text=text):
                masked, kinds = privacy.mask_pii(text)
                self.assertIn("phone", kinds)
                self.assertNotRegex(masked, r"\d{4}")

    def test_masks_coordinates_and_email(self):
        masked, kinds = privacy.mask_pii("家は 33.4012, 133.4421 で、メールは a.b@example.com")
        self.assertEqual(sorted(kinds), ["coordinate", "email"])
        self.assertNotIn("33.4012", masked)
        self.assertNotIn("example.com", masked)

    def test_keeps_ordinary_numbers(self):
        text = "100メートル歩くのに3分。歩行速度は0.5 m/秒。2012-12-10 の予測。"
        self.assertEqual(privacy.mask_pii(text), (text, []))


class ViewForClaudeTest(unittest.TestCase):
    def test_removes_coordinates_and_personal_keys_everywhere(self):
        plan = pm.new_plan("Aさん", "宇佐")
        plan["home"] = pm.Point(lat=33.4, lon=133.4)
        plan["supporters"].append(pm.Supporter(supporter_id="sup_b", alias="Bさん", relation="隣人",
                                               location=pm.Point(lat=33.4, lon=133.4), source_id="s"))
        plan["sources"]["s"] = pm.Source(kind="utterance", label="発言", text="連絡は090-1111-2222")
        plan["sources"]["s"]["phone"] = "090-1111-2222"  # type: ignore[typeddict-unknown-key]
        view = privacy.view_for_claude(plan)
        self.assertEqual(privacy.find_forbidden_keys(view), [])
        self.assertNotIn("1111", str(view))
        self.assertIn("home", plan)  # 元の計画は変えない

    def test_sanitize_incoming_plan_masks_source_text_and_values(self):
        plan = pm.new_plan("Aさん", "宇佐")
        plan["sources"]["s"] = pm.Source(kind="utterance", label="発言", text="息子の電話は 080-1234-5678")
        plan["fields"]["contact"] = pm.FieldValue(value="080-1234-5678", status="filled", source_id="s", quote="080-1234-5678")
        self.assertEqual(privacy.sanitize_incoming_plan(plan), ["phone"])
        self.assertNotIn("5678", plan["sources"]["s"]["text"])
        self.assertNotIn("5678", plan["fields"]["contact"]["value"])


class RedactSecretsTest(unittest.TestCase):
    def test_redacts_key_pattern_and_actual_value(self):
        # 偽のキー。コミット前の確認に引っかからないよう、実行時に組み立てる
        fake = "sk-" + "ant-api03-" + "x" * 40
        other = "sk-" + "ant-" + "abcdefghijklmnop"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": fake}):
            text = privacy.redact_secrets(f"error: key={fake} other={other}")
        self.assertNotIn(fake, text)
        self.assertNotIn(other, text)


class PlaceholderTest(unittest.TestCase):
    def result(self, need, avail, margin, **extra):
        return pm.ScenarioResult(scenario_id="S1", verdict="short" if margin < 0 else "ok", need_min=need, avail_min=avail,
                                 margin_min=margin, breakdown=[
                                     pm.Breakdown(key="leave", label="避難開始", minutes=12.0, source_ids=[]),
                                     pm.Breakdown(key="walk", label="移動", minutes=11.25, source_ids=[]),
                                 ], assumptions=[], **extra)

    def test_rounds_to_the_safe_side(self):
        values = ph.placeholder_values(self.result(25.25, 24.75, -0.5))
        self.assertEqual(values["need_min"], "26")  # 所要時間は切り上げ
        self.assertEqual(values["avail_min"], "24")  # 到達時間は切り捨て
        self.assertEqual(values["shortage_min"], "1")  # 不足は切り上げ
        self.assertIsNone(values["margin_min"])
        self.assertEqual(values["leave_min"], "12")
        self.assertEqual(values["walk_min"], "11")

    def test_margin_boundaries(self):
        # 余裕時間 0分は「余裕 0分」、3分は「余裕 3分」。マイナスのときだけ不足になる
        zero = ph.placeholder_values(self.result(20.0, 20.0, 0.0))
        self.assertEqual((zero["margin_min"], zero["shortage_min"]), ("0", None))
        three = ph.placeholder_values(self.result(17.0, 20.0, 3.0))
        self.assertEqual((three["margin_min"], three["shortage_min"]), ("3", None))
        float_error = ph.placeholder_values(self.result(17.000000001, 20.0, 2.999999999))
        self.assertEqual((float_error["need_min"], float_error["margin_min"]), ("17", "3"))

    def test_fill_replaces_all_placeholders(self):
        values = ph.placeholder_values(self.result(25.25, 20.0, -5.25))
        text = ph.fill("避難に{need_min}分、到達まで{avail_min}分。{shortage_min}分不足します。", values)
        self.assertEqual(text, "避難に26分、到達まで20分。6分不足します。")

    def test_shelter_name_only_when_linked_to_data(self):
        plan = pm.new_plan("Aさん", "宇佐")
        plan["fields"]["shelter"] = pm.FieldValue(value={"text": "近くのタワー"}, status="filled", source_id="s", quote="タワー")
        self.assertIsNone(ph.placeholder_values(None, plan)["shelter_name"])
        plan["fields"]["shelter"]["value"]["shelter_id"] = "shel_1"
        self.assertEqual(ph.placeholder_values(None, plan)["shelter_name"], "近くのタワー")

    def test_saved_min_only_when_positive(self):
        self.assertIsNone(ph.placeholder_values(None, saved_min=0.0)["saved_min"])
        self.assertEqual(ph.placeholder_values(None, saved_min=3.7)["saved_min"], "3")


if __name__ == "__main__":
    unittest.main()
