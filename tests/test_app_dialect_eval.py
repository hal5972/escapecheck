"""土佐弁の聞き取りの評価（eval/run_dialect_eval.py）の判定と、評価データセットの整合のテスト。Claude は呼び出さない。"""

import importlib.util
import json
import unittest
from pathlib import Path

from tools import plan_model as pm

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_dialect_eval", ROOT / "eval" / "run_dialect_eval.py")
dialect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dialect)


def filled(value):
    return pm.FieldValue(value=value, status="filled", source_id="s")


class CheckFieldTest(unittest.TestCase):
    def test_any_and_none(self):
        rule = {"any": ["聞こえな", "遠"], "none": ["よく聞こえ"]}
        self.assertTrue(dialect.check_field(filled("耳が遠く、放送が聞こえない"), rule)[0])
        self.assertFalse(dialect.check_field(filled("よく聞こえる"), rule)[0])  # 「よう聞かん」の取り違え
        self.assertFalse(dialect.check_field(filled("問題なし"), rule)[0])

    def test_dataset_none_words_do_not_reject_correct_negations(self):
        # 判定の誤りの再発防止: 「よく聞こえない」「よく見えない」は正しい答え（none の語に含まれてはいけない）
        data = json.loads((ROOT / "eval" / "dialect_cases" / "dialect_cases.json").read_text(encoding="utf-8"))
        rules = {c["case_id"]: c["expect"]["hearing_and_sight"] for c in data["cases"] if c["case_id"] in ("d02", "d10")}
        self.assertTrue(dialect.check_field(filled("耳が遠く、防災無線はよく聞こえない"), rules["d02"])[0])
        self.assertFalse(dialect.check_field(filled("耳はよく聞こえる"), rules["d02"])[0])
        self.assertTrue(dialect.check_field(filled("目がよく見えない"), rules["d10"])[0])
        self.assertFalse(dialect.check_field(filled("目はよく見える"), rules["d10"])[0])

    def test_rescore_uses_recorded_values(self):
        cases = [{"case_id": "x1", "expect": {"sleep_place": {"any": ["2階"]}}, "unfilled": ["walk_speed"]}]
        recorded = {"results": [{"variant": "v", "model": "m", "skills": [], "rows": [
            {"case_id": "x1", "seconds": 1.0, "error": None, "cost_usd": 0.01, "checks": [
                {"field": "sleep_place", "value": "2階で寝ている"}, {"field": "walk_speed", "value": None}]}]}]}
        result = dialect.rescore(recorded, cases)[0]
        self.assertEqual(result["correct"], "2/2")

    def test_numbers(self):
        rule = {"meters": 100, "minutes": 5}
        self.assertTrue(dialect.check_field(filled({"text": "100mに5分", "meters": 100, "minutes": 5}), rule)[0])
        self.assertFalse(dialect.check_field(filled({"text": "100mに3分", "meters": 100, "minutes": 3}), rule)[0])
        self.assertFalse(dialect.check_field(filled("100mに5分"), rule)[0])  # 数値つきの形で記入していない

    def test_converted_minutes_are_wrong(self):
        # 「夜はその倍」を 10分に換算して記入したら誤り（数値はコードが出す）
        rule = {"allow_unfilled": True, "any": ["倍"], "not_minutes": [10]}
        self.assertFalse(dialect.check_field(filled({"text": "昼の倍", "minutes": 10}), rule)[0])
        self.assertTrue(dialect.check_field(filled({"text": "昼の倍くらい"}), rule)[0])
        self.assertTrue(dialect.check_field(None, rule)[0])

    def test_unfilled_is_wrong_unless_allowed(self):
        self.assertFalse(dialect.check_field(None, {"any": ["2階"]})[0])
        self.assertFalse(dialect.check_field(pm.FieldValue(value=None, status="unconfirmed"), {"any": ["2階"]})[0])

    def test_score_case_counts_guessed_fills(self):
        case = {"expect": {"sleep_place": {"any": ["2階"]}}, "unfilled": ["walk_speed"]}
        fields = {"sleep_place": filled("2階"), "walk_speed": filled("ゆっくり")}
        results = dialect.score_case(case, fields)
        self.assertEqual([r["ok"] for r in results], [True, False])
        self.assertEqual(results[1]["why"], "推測で記入した")


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "eval" / "dialect_cases" / "dialect_cases.json").read_text(encoding="utf-8"))

    def test_fifteen_fictional_cases_with_valid_fields(self):
        cases = self.data["cases"]
        self.assertEqual(len(cases), 15)
        self.assertEqual(len({c["case_id"] for c in cases}), 15)
        for case in cases:
            with self.subTest(case=case["case_id"]):
                self.assertTrue(case["expect"])
                self.assertTrue(set(case["expect"]) | set(case.get("unfilled", [])) <= set(pm.FIELDS))
                self.assertFalse(set(case["expect"]) & set(case.get("unfilled", [])))
                self.assertIn(case["speaker"], ("minsei", "person", "family"))
                self.assertLessEqual(len(case["text"]), 2000)
                for rule in case["expect"].values():
                    self.assertTrue(rule.get("any") or "meters" in rule or "minutes" in rule)

    def test_speech_recognition_cases_are_pseudonymized_and_valid(self):
        data = json.loads(dialect.DATASETS["asr"].read_text(encoding="utf-8"))
        cases = data["cases"]
        self.assertTrue(cases)
        self.assertEqual(len({c["case_id"] for c in cases}), len(cases))
        for case in cases:
            with self.subTest(case=case["case_id"]):
                self.assertTrue(case["expect"])
                self.assertTrue(set(case["expect"]) | set(case.get("unfilled", [])) <= set(pm.FIELDS))
                self.assertFalse(set(case["expect"]) & set(case.get("unfilled", [])))
                if "asked_field" in case:
                    self.assertIn(case["asked_field"], pm.FIELDS)
                # アプリと同じく、人名は仮名にしてある（サンプルの人名を送らない）
                for name in ("山田", "西村"):
                    self.assertNotIn(name, case["text"])

    def test_variants_compare_models_and_tosa_skill(self):
        self.assertEqual(len(dialect.VARIANTS), 4)
        with_skill = [name for name, (_, skills) in dialect.VARIANTS.items() if "tosa-ben" in skills]
        self.assertEqual(sorted(with_skill), ["haiku+tosa", "sonnet+tosa"])


if __name__ == "__main__":
    unittest.main()
