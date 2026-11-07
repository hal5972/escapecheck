"""結合の約束（tools/plan_model.py）のテスト。"""

import json
import unittest

from tools import plan_model as pm


class NewPlanTest(unittest.TestCase):
    def test_all_fields_start_unconfirmed(self):
        plan = pm.new_plan("Aさん", "宇佐")
        self.assertEqual(set(plan["fields"]), set(pm.FIELDS))
        for key, field in plan["fields"].items():
            with self.subTest(field=key):
                self.assertEqual(field["status"], "unconfirmed")
                self.assertIsNone(field["value"])

    def test_plan_is_json_serializable(self):
        plan = pm.new_plan("Aさん", "宇佐")
        self.assertEqual(json.loads(json.dumps(plan, ensure_ascii=False)), plan)


class RegisterSourceTest(unittest.TestCase):
    def test_registers_and_returns_id(self):
        plan = pm.new_plan("Aさん", "宇佐")
        sid = pm.register_source(plan, pm.Source(kind="utterance", label="民生委員の話", text="杖ついちゅう"))
        self.assertIn(sid, plan["sources"])
        self.assertEqual(plan["sources"][sid]["text"], "杖ついちゅう")

    def test_keeps_given_id(self):
        plan = pm.new_plan("Aさん", "宇佐")
        sid = pm.register_source(plan, pm.Source(kind="assumption", label="仮定", value=0.5, unit="m/s"), "src_speed")
        self.assertEqual(sid, "src_speed")


class ForClaudeTest(unittest.TestCase):
    def test_removes_coordinates_without_touching_original(self):
        plan = pm.new_plan("Aさん", "宇佐")
        plan["home"] = pm.Point(lat=33.4, lon=133.4)
        plan["supporters"].append(
            pm.Supporter(supporter_id="sup_b", alias="Bさん", relation="隣の人",
                          location=pm.Point(lat=33.4, lon=133.4), source_id="src_x")
        )
        view = pm.for_claude(plan)
        self.assertNotIn("home", view)
        self.assertNotIn("location", view["supporters"][0])
        self.assertIn("home", plan)
        self.assertIn("location", plan["supporters"][0])


class VocabularyTest(unittest.TestCase):
    def test_scenarios_s1_to_s8(self):
        self.assertEqual(list(pm.SCENARIOS), [f"S{i}" for i in range(1, 9)])

    def test_premise_codes_are_hole_types(self):
        # 話の中だけにある前提は、評価の穴の種類としても数えられる
        self.assertTrue(set(pm.PREMISE_CODES) - {"ROAD_RISK"} <= set(pm.HOLE_TYPES))


if __name__ == "__main__":
    unittest.main()
