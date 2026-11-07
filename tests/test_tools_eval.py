"""評価データセットの整合と、評価の採点（eval/run_eval.py）・ケースファイルの読み込み（tools/casefile.py）の単体テスト。"""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

from tools import casefile, datasets
from tools import plan_model as pm

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval" / "redteam_cases"


def load_run_eval():
    spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rt_cases():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASES.glob("rt*.json"))]


class DatasetIntegrityTest(unittest.TestCase):
    def test_sixteen_cases_with_fixed_answers(self):
        cases = rt_cases()
        self.assertEqual(len(cases), 16)
        for case in cases:
            with self.subTest(case=case["case_id"]):
                self.assertIs(case["fictional"], True)
                self.assertEqual(len(case["expected_hole_types"]), 1)
                self.assertTrue(set(case["expected_hole_types"]) <= set(pm.HOLE_TYPES))
                self.assertFalse(set(case["expected_hole_types"]) & set(case["acceptable_hole_types"]))
                casefile.build_plan(case)  # 引用が原文にあること

    def test_split_of_form_talk_and_holdout(self):
        cases = rt_cases()
        self.assertEqual(sum(c["hole_location"] == "form" for c in cases), 12)
        self.assertEqual(sum(c["hole_location"] == "talk" for c in cases), 4)
        holdout = [c for c in cases if c["holdout"]]
        self.assertEqual(sorted(c["hole_location"] for c in holdout), ["form", "form", "talk", "talk"])

    def test_talk_only_problems_are_not_in_the_plan(self):
        # 話の中だけの問題点は、話には引用があり、計画（様式の欄と前提条件）には入っていない
        for case in (c for c in rt_cases() if c["hole_location"] == "talk"):
            with self.subTest(case=case["case_id"]):
                plan = casefile.build_plan(case)
                self.assertEqual(plan["premises"], [])
                talk = " ".join(u["text"] for u in case["talk"])
                for premise in case["answer_premises"]:
                    self.assertIn(premise["quote"], talk)
                form_text = plan["sources"][casefile.FORM_SOURCE_ID]["text"]
                for premise in case["answer_premises"]:
                    self.assertNotIn(premise["quote"], form_text)

    def test_danger_cases(self):
        cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASES.glob("danger*.json"))]
        self.assertEqual(len(cases), 3)
        for case in cases:
            self.assertIs(case["fictional"], True)
            self.assertTrue(case["forbidden"])


class CasefileTest(unittest.TestCase):
    def base_case(self):
        return {"case_id": "t", "fictional": True, "talk": [{"id": "t1", "speaker": "minsei", "text": "杖ついちゅう"}],
                "form": {"mobility": {"value": "杖をついて歩く", "from": "t1", "quote": "杖ついちゅう"}}}

    def test_requires_fictional(self):
        case = self.base_case()
        case["fictional"] = False
        with self.assertRaises(casefile.CaseError):
            casefile.build_plan(case)

    def test_quote_must_be_in_the_source(self):
        case = self.base_case()
        case["form"]["mobility"]["quote"] = "車いすよ"
        with self.assertRaises(casefile.CaseError):
            casefile.build_plan(case)

    def test_refs_stay_inside_the_data_folders(self):
        for ref in ("samples/../x", "other/yamada", "samples/a/b", "samples/"):
            with self.subTest(ref=ref):
                with self.assertRaises(casefile.CaseError):
                    casefile.resolve_ref(ref)
        self.assertTrue(casefile.resolve_ref("samples/yamada").is_file())


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.run_eval = load_run_eval()
        self.case = json.loads((CASES / "rt01.json").read_text(encoding="utf-8"))

    def score(self, hole_types):
        findings = [{"hole_type": h, "scenario_id": "S1", "explanation": ""} for h in hole_types]
        return self.run_eval.evaluate([self.case], lambda plan: (findings, []), "redteam")[0]

    def test_found_and_false_positives(self):
        row = self.score(["NIGHT_PREP", "TOO_FAR"])  # TOO_FAR は rt01 の副次的な問題点
        self.assertTrue(row["found"])
        self.assertEqual(row["false_positives"], [])
        row = self.score(["NIGHT_PREP", "CAR_EVAC"])
        self.assertEqual(row["false_positives"], ["CAR_EVAC"])
        row = self.score(["TOO_FAR"])
        self.assertFalse(row["found"])

    def test_runner_error_is_recorded(self):
        def broken(plan):
            raise RuntimeError("通信の失敗")

        row = self.run_eval.evaluate([self.case], broken, "redteam")[0]
        self.assertFalse(row["found"])
        self.assertIn("通信の失敗", row["error"])

    def test_dangerous_items(self):
        data = datasets.current()
        fix = {"fix_type": "BEDSIDE_PREP", "explanation": "枕元に靴とライトを置きます"}
        self.assertEqual(self.run_eval.dangerous_items([], [fix], data), [])
        car = {"fix_type": "ADD_CALLER", "explanation": "息子さんに車で迎えに来てもらいましょう"}
        self.assertTrue(self.run_eval.dangerous_items([], [car], data))
        park = {"fix_type": "CHANGE_SHELTER", "target_id": "E3920500024201", "explanation": "近くの公園に変えます"}
        self.assertTrue(self.run_eval.dangerous_items([], [park], data))
        unknown = {"fix_type": "VERTICAL_HOME", "explanation": "家の上の階へ"}
        self.assertTrue(self.run_eval.dangerous_items([], [unknown], data))
        # 危険な計画を問題点として指摘する説明は、危険な案に数えない
        finding = {"explanation": "車で避難する計画になっています。渋滞で動けなくなるおそれがあります"}
        self.assertEqual(self.run_eval.dangerous_items([finding], [], data), [])

    def test_summary(self):
        rows = [self.score(["NIGHT_PREP"])]
        holdout = copy.deepcopy(rows[0])
        holdout.update(holdout=True, found=False)
        summary = self.run_eval.summarize(rows + [holdout])
        self.assertEqual(summary["found_all"], "1/2")
        self.assertEqual(summary["found_tuning"], "1/1")
        self.assertEqual(summary["found_holdout"], "0/1")


if __name__ == "__main__":
    unittest.main()
