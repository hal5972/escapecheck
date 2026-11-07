"""tools/sim.py（避難が間に合うかの計算）の単体テスト。

期待値は docs/03-design.md の 5 節の式から手で計算した値。テストのデータ（test_tools_helpers.make_dataset）:
自宅から避難先 A まで直線 300m（道なりの概算 390m）、杖 0.5 m/秒で移動 13.0分、日中の避難開始時間 5分、上がる時間 2分。
"""

import copy
import inspect
import re
import unittest

from tools import datasets, hazard, sim
from tools import plan_model as pm
from test_tools_helpers import add_premise, make_dataset, make_plan, supporter


def minutes(result, key):
    return [b["minutes"] for b in result["breakdown"] if b["key"] == key]


class SimTestCase(unittest.TestCase):
    near_minutes = 20

    def setUp(self):
        self._override = datasets.override(make_dataset(near_minutes=self.near_minutes))
        self._override.__enter__()

    def tearDown(self):
        self._override.__exit__(None, None, None)


class BaseCalculationTest(SimTestCase):
    def test_day_alone_breakdown(self):
        plan = make_plan()
        result = sim.run_scenario(plan, "S8", premise_ids=[])
        self.assertEqual([b["key"] for b in result["breakdown"]], ["leave", "walk", "climb"])
        self.assertEqual(minutes(result, "leave"), [5.0])
        self.assertEqual(minutes(result, "walk"), [13.0])
        self.assertEqual(minutes(result, "climb"), [2.0])
        self.assertEqual(result["need_min"], 20.0)
        self.assertEqual(result["avail_min"], 20.0)
        self.assertEqual(result["margin_min"], 0.0)
        self.assertEqual(result["verdict"], "tight")
        self.assertEqual(result["shelter_name"], "テスト避難タワー")

    def test_utterance_values_replace_assumptions(self):
        plan = make_plan({"walk_speed": "100mを3分", "time_to_leave_day": "3分くらい"})
        result = sim.run_scenario(plan, "S8", premise_ids=[])
        # 100m を 3分 = 0.555… m/秒。390m を 702秒 = 11.7分
        self.assertEqual(minutes(result, "walk"), [11.7])
        self.assertEqual(minutes(result, "leave"), [3.0])
        self.assertEqual(result["need_min"], 16.7)
        leave = [b for b in result["breakdown"] if b["key"] == "leave"][0]
        self.assertIn("src_form", leave["source_ids"])
        self.assertNotIn("asm_leave_day", result["assumptions"])

    def test_all_referenced_sources_are_registered(self):
        plan = make_plan()
        result = sim.run_scenario(plan, "S1")
        ids = set(result["assumptions"]) | set(result["avail_source_ids"])
        for b in result["breakdown"]:
            ids |= set(b["source_ids"])
        self.assertTrue(ids)
        for sid in ids:
            with self.subTest(source_id=sid):
                self.assertIn(sid, plan["sources"])
        for sid in result["assumptions"]:
            self.assertEqual(plan["sources"][sid]["kind"], "assumption")

    def test_result_is_json_serializable(self):
        import json

        plan = make_plan()
        result = sim.run_scenario(plan, "S5")
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)


class MarginBoundaryTest(unittest.TestCase):
    """余裕時間の境界: 3分以上は「間に合う」、0〜3分は「余裕が少ない」、マイナスは「間に合わない」。"""

    def check(self, near_minutes, margin, verdict):
        with datasets.override(make_dataset(near_minutes=near_minutes)):
            result = sim.run_scenario(make_plan(), "S8", premise_ids=[])
        self.assertEqual(result["need_min"], 20.0)
        self.assertEqual(result["margin_min"], margin)
        self.assertEqual(result["verdict"], verdict)

    def test_margin_zero_is_tight(self):
        self.check(20, 0.0, "tight")

    def test_margin_three_is_ok(self):
        self.check(23, 3.0, "ok")

    def test_margin_just_below_three_is_tight(self):
        self.check(22.9, 2.9, "tight")

    def test_margin_just_below_zero_is_short(self):
        self.check(19.9, -0.1, "short")

    def test_verdict_function(self):
        self.assertEqual(sim.verdict_for(3.0), "ok")
        self.assertEqual(sim.verdict_for(2.99), "tight")
        self.assertEqual(sim.verdict_for(0.0), "tight")
        self.assertEqual(sim.verdict_for(-0.01), "short")
        self.assertEqual(sim.verdict_for(None), "cannot_compute")


class ScenarioTest(SimTestCase):
    def test_s1_winter_night(self):
        result = sim.run_scenario(make_plan(), "S1")
        # 夜間 10分 + 冬 2分、暗いので 0.5 × 0.8 = 0.4 m/秒 → 390m で 16.25分 → 切り上げて 16.3分
        self.assertEqual(minutes(result, "leave"), [12.0])
        self.assertEqual(minutes(result, "walk"), [16.3])
        self.assertEqual(result["need_min"], 30.3)
        self.assertEqual(result["margin_min"], -10.3)
        self.assertEqual(result["verdict"], "short")
        for sid in ("asm_leave_night", "asm_winter_leave", "asm_dark_speed"):
            self.assertIn(sid, result["assumptions"])

    def test_s1_uses_night_utterance(self):
        result = sim.run_scenario(make_plan({"time_to_leave_night": "8分"}), "S1")
        self.assertEqual(minutes(result, "leave"), [10.0])

    def test_s2_supporter_absent_in_daytime(self):
        son = supporter("sup_son", "Cさん", 240, available_day=False, relation="長男")
        plan = make_plan({"mobility": "車いすで、介助が必要"}, supporters=[son])
        result = sim.run_scenario(plan, "S2")
        self.assertEqual(result["verdict"], "short")
        self.assertIsNone(result["need_min"])
        self.assertEqual(result["reason_code"], "NEEDS_SUPPORTER")

    def test_s2_with_daytime_supporter(self):
        son = supporter("sup_son", "Cさん", 240, available_day=False, relation="長男")
        neighbor = supporter("sup_b", "Bさん", 240, available_day=True)
        plan = make_plan({"mobility": "車いすで、介助が必要"}, supporters=[son, neighbor])
        result = sim.run_scenario(plan, "S2")
        # 支援者: 5分 + 312m / 1.0 m/秒 = 5.2分 → 10.2分で到着、声かけ 3分、一緒に 390m / 0.52 m/秒 = 12.5分、上がる 2分
        self.assertEqual(minutes(result, "supporter_travel"), [10.2])
        self.assertEqual(minutes(result, "help"), [3.0])
        self.assertEqual(minutes(result, "walk"), [12.5])
        self.assertEqual(result["need_min"], 27.7)
        self.assertEqual(result["supporter_alias"], "Bさん")

    def test_s3_route_blocked(self):
        result = sim.run_scenario(make_plan(), "S3")
        # 300m × 1.3 × 1.3 = 507m / 0.5 m/秒 = 16.9分
        self.assertEqual(minutes(result, "walk"), [16.9])
        self.assertEqual(result["need_min"], 23.9)
        self.assertIn("asm_detour_factor", result["assumptions"])

    def test_s4_independent_person_is_unchanged(self):
        neighbor = supporter("sup_b", "Bさん", 240)
        result = sim.run_scenario(make_plan(supporters=[neighbor]), "S4")
        self.assertEqual(result["need_min"], 20.0)

    def test_s4_person_needing_aid(self):
        neighbor = supporter("sup_b", "Bさん", 240)
        plan = make_plan({"mobility": "一人では立ち上がれない"}, supporters=[neighbor])
        result = sim.run_scenario(plan, "S4")
        self.assertEqual(result["verdict"], "short")
        self.assertEqual(result["reason_code"], "NEEDS_SUPPORTER")

    def test_s5_supporter_safety_at_night(self):
        neighbor = supporter("sup_b", "Bさん", 240)
        result = sim.run_scenario(make_plan(supporters=[neighbor]), "S5")
        # 支援者: 夜間 10分 + 312m / (1.0 × 0.8) = 6.5分 → 16.5分、声かけ 3分、一緒に 390m / 0.4 = 16.3分、上がる 2分
        self.assertEqual(minutes(result, "supporter_travel"), [16.5])
        self.assertEqual(minutes(result, "help"), [3.0])
        self.assertEqual(minutes(result, "walk"), [16.3])
        self.assertEqual(result["need_min"], 37.8)
        self.assertEqual(result["margin_min"], -17.8)
        self.assertEqual(result["supporter_verdict"], "short")
        self.assertEqual(result["supporter_margin_min"], -17.8)
        self.assertEqual(result["verdict"], "short")

    def test_s5_without_supporter_cannot_compute(self):
        result = sim.run_scenario(make_plan(), "S5")
        self.assertEqual(result["verdict"], "cannot_compute")
        self.assertEqual(result["reason_code"], "NO_SUPPORTER")

    def test_s6_earliest_arrival_on_the_coast(self):
        result = sim.run_scenario(make_plan(), "S6")
        self.assertEqual(result["avail_min"], 15.0)
        self.assertEqual(result["margin_min"], -5.0)
        self.assertIn("data_arrival_far_coast", result["avail_source_ids"])

    def test_s7_uses_next_shelter_when_person_cannot_climb(self):
        plan = make_plan()
        add_premise(plan, "CANNOT_CLIMB", "あのタワーの階段は急で、よう上がらん", about="A")
        result = sim.run_scenario(plan, "S7")
        # 公園 P（北 100m）は津波の対象外なので選ばない。次は B（北 500m → 650m / 0.5 = 21.7分）
        self.assertEqual(result["shelter_name"], "テスト高台")
        self.assertEqual(minutes(result, "walk"), [21.7])
        self.assertEqual(result["need_min"], 28.7)

    def test_s7_without_local_information_is_unchanged(self):
        result = sim.run_scenario(make_plan(), "S7")
        self.assertEqual(result["shelter_name"], "テスト避難タワー")
        self.assertEqual(result["need_min"], 20.0)

    def test_unknown_scenario(self):
        with self.assertRaises(ValueError):
            sim.run_scenario(make_plan(), "S9")


class PremiseTest(SimTestCase):
    def test_sleep_upstairs_is_night(self):
        plan = make_plan()
        pid = add_premise(plan, "SLEEP_UPSTAIRS", "夜は2階で寝ゆう")
        result = sim.run_scenario(plan, "S8", premise_ids=[pid])
        # 夜間 10分 + 階段 3分（冬の加算は S1 だけ）、暗いので 16.3分
        self.assertEqual(minutes(result, "leave"), [13.0])
        self.assertEqual(result["need_min"], 31.3)
        self.assertIn("asm_upstairs", result["assumptions"])

    def test_deep_sleep(self):
        plan = make_plan()
        pid = add_premise(plan, "DEEP_SLEEP", "眠れん時は薬を飲んで寝る")
        result = sim.run_scenario(plan, "S8", premise_ids=[pid])
        self.assertEqual(minutes(result, "leave"), [15.0])

    def test_none_means_all_registered_premises(self):
        plan = make_plan()
        add_premise(plan, "SLEEP_UPSTAIRS", "夜は2階で寝ゆう")
        self.assertEqual(sim.run_scenario(plan, "S8")["need_min"], 31.3)
        # premise_ids を省略すると、どのシナリオでも登録済みの前提条件を使う（try_fix と同じ条件）
        self.assertEqual(sim.run_scenario(plan, "S1")["need_min"], 33.3)
        # 空のリストなら前提条件を使わない
        self.assertEqual(sim.run_scenario(plan, "S1", premise_ids=[])["need_min"], 30.3)

    def test_premise_combined_with_s1(self):
        plan = make_plan()
        pid = add_premise(plan, "SLEEP_UPSTAIRS", "夜は2階で寝ゆう")
        result = sim.run_scenario(plan, "S1", premise_ids=[pid])
        self.assertEqual(minutes(result, "leave"), [15.0])

    def test_road_risk_applies_detour(self):
        plan = make_plan()
        pid = add_premise(plan, "ROAD_RISK", "あの道はブロック塀が多い")
        self.assertEqual(sim.run_scenario(plan, "S8", premise_ids=[pid])["need_min"], 23.9)

    def test_cannot_hear_without_caller(self):
        plan = make_plan()
        pid = add_premise(plan, "CANNOT_HEAR", "耳が遠うて、放送は聞こえん")
        result = sim.run_scenario(plan, "S8", premise_ids=[pid])
        self.assertEqual(result["verdict"], "short")
        self.assertEqual(result["reason_code"], "NO_CALLER")
        self.assertIsNone(result["margin_min"])

    def test_cannot_hear_waits_for_caller(self):
        plan = make_plan(supporters=[supporter("sup_b", "Bさん", 240, available_day=True)])
        pid = add_premise(plan, "CANNOT_HEAR", "耳が遠うて、放送は聞こえん")
        result = sim.run_scenario(plan, "S8", premise_ids=[pid])
        # 声をかける人が着いた時刻（5 + 5.2 = 10.2分）から避難を開始する（5.2 節）
        self.assertEqual(minutes(result, "supporter_travel"), [10.2])
        self.assertEqual(minutes(result, "help"), [3.0])
        self.assertEqual(minutes(result, "walk"), [13.0])
        self.assertEqual(result["need_min"], 28.2)
        self.assertEqual(result["supporter_verdict"], "short")

    def test_needs_aid_without_supporter(self):
        plan = make_plan()
        pid = add_premise(plan, "NEEDS_AID", "一人では立ち上がれん")
        result = sim.run_scenario(plan, "S8", premise_ids=[pid])
        self.assertEqual(result["reason_code"], "NEEDS_SUPPORTER")

    def test_form_fields_are_used_without_premises(self):
        # 様式の欄に書かれた事実（2階で就寝）は、前提条件が無くても計算に入る
        result = sim.run_scenario(make_plan({"sleep_place": "2階の寝室"}), "S1")
        self.assertEqual(minutes(result, "leave"), [15.0])
        # 「2階建ての1階」は2階での就寝ではない
        result = sim.run_scenario(make_plan({"sleep_place": "2階建ての家の1階"}), "S1")
        self.assertEqual(minutes(result, "leave"), [12.0])

    def test_unregistered_premise_is_rejected(self):
        with self.assertRaises(ValueError):
            sim.run_scenario(make_plan(), "S8", premise_ids=["pr_unknown"])


class DataEdgeCaseTest(unittest.TestCase):
    def test_shorter_version_is_used(self):
        from test_tools_helpers import north

        extra = datasets.ArrivalArea(
            area_id="v2025", label="テスト地点（新しい予測）", lat=north(100)[0], lon=north(100)[1],
            minutes_30cm=18, version="V2025", source_url="https://example.invalid/2025.pdf",
            page=3, status="確認済", source_name="テストの予測（2025）",
        )
        with datasets.override(make_dataset(near_minutes=20, extra_areas=[extra])):
            result = sim.run_scenario(make_plan(), "S8", premise_ids=[])
        self.assertEqual(result["avail_min"], 18.0)
        self.assertEqual(result["avail_version"], "V2025")

    def test_unconfirmed_rows_are_not_used(self):
        unconfirmed = datasets.ArrivalArea(
            area_id="maybe", label="読み取れなかった地点", lat=33.45, lon=133.44, minutes_30cm=5,
            version="V2012", source_url="https://example.invalid/2012.pdf", page=2, status="未確認",
        )
        with datasets.override(make_dataset(near_minutes=20, extra_areas=[unconfirmed])):
            self.assertEqual(sim.run_scenario(make_plan(), "S8", premise_ids=[])["avail_min"], 20.0)

    def test_cannot_compute_cases(self):
        with datasets.override(make_dataset()):
            cases = {
                "NO_HOME": make_plan(home=False),
                "SHELTER_NOT_TSUNAMI": make_plan(shelter={"shelter_id": "P", "name": "テスト公園"}),
                "SHELTER_UNKNOWN": make_plan(shelter="どこか知らない場所"),
            }
            for reason, plan in cases.items():
                with self.subTest(reason=reason):
                    result = sim.run_scenario(plan, "S8", premise_ids=[])
                    self.assertEqual(result["verdict"], "cannot_compute")
                    self.assertEqual(result["reason_code"], reason)
            home = sim.run_scenario(make_plan(shelter="自宅の2階"), "S8", premise_ids=[])
            self.assertEqual(home["reason_code"], "SHELTER_NOT_TSUNAMI")
        empty = datasets.Dataset(arrival_areas=[], shelters=make_dataset().shelters)
        with datasets.override(empty):
            self.assertEqual(sim.run_scenario(make_plan(), "S8", premise_ids=[])["reason_code"], "NO_ARRIVAL_TIME")

    def test_shelter_by_name(self):
        with datasets.override(make_dataset()):
            result = sim.run_scenario(make_plan(shelter="テスト 避難タワー"), "S8", premise_ids=[])
        self.assertEqual(result["shelter_name"], "テスト避難タワー")

    def test_unconfirmed_shelter_uses_nearest_tsunami_shelter(self):
        with datasets.override(make_dataset()):
            result = sim.run_scenario(make_plan(shelter=None), "S8", premise_ids=[])
        self.assertEqual(result["shelter_name"], "テスト避難タワー")
        self.assertIn("asm_shelter_nearest", result["assumptions"])


class TryFixTest(SimTestCase):
    def test_change_shelter(self):
        plan = make_plan()
        hazard.find_shelters(plan, "home", limit=3)
        result = sim.try_fix(plan, "S8", "CHANGE_SHELTER", "B")
        self.assertEqual(result["shelter_name"], "テスト高台")
        self.assertEqual(result["need_min"], 28.7)

    def test_change_shelter_rejects_non_tsunami_site(self):
        plan = make_plan()
        hazard.find_shelters(plan, "home", limit=3)
        plan["sources"]["data_shelter_P"] = pm.Source(kind="dataset", label="テスト公園")
        with self.assertRaisesRegex(ValueError, "津波"):
            sim.try_fix(plan, "S8", "CHANGE_SHELTER", "P")

    def test_change_shelter_to_non_tsunami_site_explains_tsunami(self):
        # 出典が登録されていなくても、理由は「津波の対象ではない」と返す（エージェントが理由を読んで修正できるように）
        with self.assertRaisesRegex(ValueError, "津波"):
            sim.try_fix(make_plan(), "S8", "CHANGE_SHELTER", "P")

    def test_change_shelter_allowed_when_plan_was_not_computable(self):
        # 計画の避難先が津波の対象外（計算できない）とき、避難先を変える案は、避難支援者の結果が初めて出ても拒否しない
        neighbor = supporter("sup_b", "Bさん", 240)
        plan = make_plan(shelter={"shelter_id": "P", "name": "テスト公園"}, supporters=[neighbor])
        add_premise(plan, "CANNOT_HEAR", "耳が遠うて、放送は聞こえん")
        hazard.find_shelters(plan, "home", limit=3)
        self.assertEqual(sim.run_scenario(plan, "S1")["verdict"], "cannot_compute")
        result = sim.try_fix(plan, "S1", "CHANGE_SHELTER", "A")
        self.assertEqual(result["shelter_name"], "テスト避難タワー")
        self.assertEqual(result["supporter_verdict"], "short")

    def test_change_shelter_rejected_when_it_puts_supporter_at_risk(self):
        # 改善案の前は間に合っていた避難支援者が、遠い避難先に変えると間に合わなくなる → 拒否
        neighbor = supporter("sup_b", "Bさん", 40)
        plan = make_plan(supporters=[neighbor])
        add_premise(plan, "CANNOT_HEAR", "耳が遠うて、放送は聞こえん")
        hazard.find_shelters(plan, "home", limit=3)
        with datasets.override(make_dataset(near_minutes=30)):
            self.assertNotEqual(sim.run_scenario(plan, "S8")["supporter_verdict"], "short")
            with self.assertRaisesRegex(ValueError, "避難支援者"):
                sim.try_fix(plan, "S8", "CHANGE_SHELTER", "C")

    def test_change_shelter_requires_registered_source(self):
        with self.assertRaises(ValueError):
            sim.try_fix(make_plan(), "S8", "CHANGE_SHELTER", "B")

    def test_change_shelter_rejects_site_the_person_cannot_climb(self):
        plan = make_plan(shelter={"shelter_id": "B", "name": "テスト高台"})
        hazard.find_shelters(plan, "home", limit=3)
        add_premise(plan, "CANNOT_CLIMB", "あのタワーの階段は急で、よう上がらん", about="A")
        with self.assertRaises(ValueError):
            sim.try_fix(plan, "S8", "CHANGE_SHELTER", "A")

    def test_bedside_prep_at_night(self):
        result = sim.try_fix(make_plan(), "S1", "BEDSIDE_PREP")
        self.assertEqual(minutes(result, "leave"), [9.0])
        self.assertEqual(result["need_min"], 27.3)

    def test_leave_time_has_a_floor(self):
        result = sim.try_fix(make_plan({"time_to_leave_night": "2分"}), "S1", "BEDSIDE_PREP")
        # 2分 + 冬 2分 − 3分 = 1分 → 下限の 2分
        self.assertEqual(minutes(result, "leave"), [2.0])

    def test_sleep_near_exit_removes_stairs(self):
        plan = make_plan({"sleep_place": "2階の寝室"})
        self.assertEqual(sim.run_scenario(plan, "S1")["need_min"], 33.3)
        result = sim.try_fix(plan, "S1", "SLEEP_NEAR_EXIT")
        self.assertEqual(minutes(result, "leave"), [10.0])
        self.assertEqual(result["need_min"], 28.3)

    def test_add_caller_rejected_when_caller_would_not_make_it(self):
        neighbor = supporter("sup_y", "Bさん", 120, age_band="60代")
        plan = make_plan({"mobility": "車いすで、介助が必要"}, supporters=[neighbor])
        with self.assertRaisesRegex(ValueError, "避難支援者"):
            sim.try_fix(plan, "S4", "ADD_CALLER", "sup_y")

    def test_add_caller_rejects_unavailable_or_unknown_supporter(self):
        son = supporter("sup_son", "Cさん", 240, available_day=False, relation="長男")
        plan = make_plan({"mobility": "車いすで、介助が必要"}, supporters=[son])
        with self.assertRaises(ValueError):
            sim.try_fix(plan, "S2", "ADD_CALLER", "sup_son")
        with self.assertRaises(ValueError):
            sim.try_fix(plan, "S2", "ADD_CALLER", "sup_nobody")

    def test_unknown_fix_type(self):
        for fix_type in ("CAR", "VERTICAL_HOME", "WAIT_AND_SEE"):
            with self.subTest(fix_type=fix_type):
                with self.assertRaises(ValueError):
                    sim.try_fix(make_plan(), "S1", fix_type)

    def test_try_fix_does_not_change_the_plan(self):
        plan = make_plan({"sleep_place": "2階の寝室"}, supporters=[supporter("sup_b", "Bさん", 240)])
        hazard.find_shelters(plan, "home", limit=3)
        before = copy.deepcopy({k: v for k, v in plan.items() if k != "sources"})
        sim.try_fix(plan, "S1", "SLEEP_NEAR_EXIT")
        sim.try_fix(plan, "S8", "CHANGE_SHELTER", "B")
        self.assertEqual({k: v for k, v in plan.items() if k != "sources"}, before)


class InterviewValueFormatTest(SimTestCase):
    """聞き取りエージェント（B）が書き込む形の値も読める（docs: B のメモの 1 節）。"""

    def test_dict_values_with_numbers(self):
        plan = make_plan({"walk_speed": {"text": "100mを3分くらい", "meters": 100, "minutes": 3},
                          "time_to_leave_day": {"text": "3分くらい", "minutes": 3}},
                         shelter={"text": "テスト避難タワー", "shelter_id": "A"})
        result = sim.run_scenario(plan, "S8", premise_ids=[])
        self.assertEqual(result["need_min"], 16.7)

    def test_dict_values_with_text_only(self):
        plan = make_plan({"walk_speed": {"text": "100mを3分くらい"}, "time_to_leave_day": {"text": "3分くらい"}},
                         shelter={"text": "テスト避難タワー"})
        result = sim.run_scenario(plan, "S8", premise_ids=[])
        self.assertEqual(result["need_min"], 16.7)
        self.assertEqual(result["shelter_name"], "テスト避難タワー")


def add_fix(plan, fix_type, target_id=None, approved=True):
    fix_id = f"fix_{fix_type.lower()}_{len(plan['fixes'])}"
    fix = pm.Fix(fix_id=fix_id, finding_id="fd_x", fix_type=fix_type, explanation_template="", result={}, saved_min=None)
    if target_id is not None:
        fix["target_id"] = target_id
    plan["fixes"].append(fix)
    if approved:
        plan["approvals"].append(pm.Approval(fix_id=fix_id, by="minsei", at="2026-11-08T10:00:00+09:00"))
    return fix_id


class ApprovedFixTest(SimTestCase):
    """承認済みの改善案は、run_scenario にも反映する（承認 → もう一度点検 → 緑の流れ）。"""

    def test_unapproved_fix_is_not_applied(self):
        plan = make_plan()
        add_fix(plan, "BEDSIDE_PREP", approved=False)
        self.assertEqual(minutes(sim.run_scenario(plan, "S1"), "leave"), [12.0])

    def test_approved_fix_is_applied(self):
        plan = make_plan()
        add_fix(plan, "BEDSIDE_PREP")
        self.assertEqual(minutes(sim.run_scenario(plan, "S1"), "leave"), [9.0])

    def test_try_fix_stacks_on_approved_fixes(self):
        plan = make_plan({"sleep_place": "2階の寝室"})
        add_fix(plan, "BEDSIDE_PREP")
        # 夜間 10 + 冬 2 + 2階 3 − 枕元 3 = 12。さらに玄関の近くで就寝: 2階の 3 が無くなり −2 → 7
        self.assertEqual(minutes(sim.run_scenario(plan, "S1"), "leave"), [12.0])
        self.assertEqual(minutes(sim.try_fix(plan, "S1", "SLEEP_NEAR_EXIT"), "leave"), [7.0])

    def test_approved_change_shelter(self):
        plan = make_plan()
        hazard.find_shelters(plan, "home", limit=3)
        add_fix(plan, "CHANGE_SHELTER", "B")
        self.assertEqual(sim.run_scenario(plan, "S8", premise_ids=[])["shelter_name"], "テスト高台")


class ApprovedCallerTest(SimTestCase):
    near_minutes = 35

    def test_approved_add_caller_in_s4(self):
        neighbor = supporter("sup_y", "Bさん", 120, age_band="60代")
        plan = make_plan({"mobility": "車いすで、介助が必要"}, supporters=[neighbor])
        add_fix(plan, "ADD_CALLER", "sup_y")
        self.assertEqual(sim.run_scenario(plan, "S4")["need_min"], 26.7)


class AddCallerAcceptedTest(SimTestCase):
    near_minutes = 35

    def test_add_caller_in_s4(self):
        neighbor = supporter("sup_y", "Bさん", 120, age_band="60代")
        plan = make_plan({"mobility": "車いすで、介助が必要"}, supporters=[neighbor])
        self.assertEqual(sim.run_scenario(plan, "S4")["reason_code"], "NEEDS_SUPPORTER")
        result = sim.try_fix(plan, "S4", "ADD_CALLER", "sup_y")
        # 支援者 60代 0.62 m/秒: 5分 + 156m / 0.62 = 4.2分 → 9.2分、声かけ 3分、一緒に 12.5分、上がる 2分
        self.assertEqual(result["need_min"], 26.7)
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(result["supporter_verdict"], "ok")


class InterfaceRuleTest(unittest.TestCase):
    def test_calculation_functions_take_no_numbers_from_outside(self):
        # S-01: 計算の MCP ツールに、Claude が数値を渡せる引数を作らない
        self.assertEqual(list(inspect.signature(sim.run_scenario).parameters), ["plan", "scenario_id", "premise_ids"])
        self.assertEqual(list(inspect.signature(sim.try_fix).parameters), ["plan", "scenario_id", "fix_type", "target_id"])
        self.assertEqual(list(inspect.signature(hazard.get_facts).parameters), ["plan", "target"])
        self.assertEqual(list(inspect.signature(hazard.find_shelters).parameters), ["plan", "target", "limit"])


class ParseTest(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(sim.parse_minutes("10分くらい"), 10.0)
        self.assertEqual(sim.parse_minutes("１５分"), 15.0)
        self.assertEqual(sim.parse_minutes(7), 7.0)
        self.assertIsNone(sim.parse_minutes("十分"))
        self.assertIsNone(sim.parse_minutes(None))

    def test_walk_speed(self):
        self.assertAlmostEqual(sim.parse_walk_speed("100mを3分"), 100 / 180)
        self.assertAlmostEqual(sim.parse_walk_speed("100メートル 4分"), 100 / 240)
        self.assertAlmostEqual(sim.parse_walk_speed({"meters": 50, "minutes": 1}), 50 / 60)
        self.assertAlmostEqual(sim.parse_walk_speed(0.6), 0.6)
        self.assertIsNone(sim.parse_walk_speed("ゆっくり"))


class BaselineTest(SimTestCase):
    near_minutes = 30

    def hole_types(self, plan):
        return {f["hole_type"] for f in sim.baseline_findings(plan)}

    def test_night_not_considered(self):
        self.assertIn("NIGHT_PREP", self.hole_types(make_plan()))

    def test_clean_plan_has_no_findings(self):
        plan = make_plan({"time_to_leave_night": "4分", "time_to_leave_day": "3分", "walk_speed": "100mを2分",
                          "mobility": "自力で歩ける"})
        self.assertEqual(self.hole_types(plan), set())

    def test_shelter_not_for_tsunami(self):
        plan = make_plan(shelter={"shelter_id": "P", "name": "テスト公園"})
        self.assertIn("SHELTER_NOT_TSUNAMI", self.hole_types(plan))

    def test_vertical_home_and_car(self):
        self.assertIn("VERTICAL_HOME", self.hole_types(make_plan(shelter="自宅の2階")))
        self.assertIn("CAR_EVAC", self.hole_types(make_plan({"route": "息子の車で高台へ行く"})))
        self.assertNotIn("CAR_EVAC", self.hole_types(make_plan({"mobility": "車いすで、介助が必要"})))

    def test_standing_up_is_not_climbing(self):
        # 「一人では立ち上がれない」は、避難先に上がれない（CANNOT_CLIMB）ではない
        plan = make_plan({"mobility": "一人では立ち上がれない。介助が必要"})
        self.assertNotIn("CANNOT_CLIMB", self.hole_types(plan))
        plan = make_plan({"notes": "膝が悪く、タワーの階段は上がれない"})
        self.assertIn("CANNOT_CLIMB", self.hole_types(plan))

    def test_baseline_does_not_read_the_talk(self):
        plan = make_plan({"time_to_leave_night": "4分", "time_to_leave_day": "3分", "walk_speed": "100mを2分",
                          "hearing_and_sight": "特になし", "mobility": "自力で歩ける"})
        pm.register_source(plan, pm.Source(kind="utterance", label="聞き取りの話", text="耳が遠うて、放送は聞こえん"))
        self.assertNotIn("CANNOT_HEAR", self.hole_types(plan))

    def test_explanations_use_placeholders(self):
        findings = sim.baseline_findings(make_plan({"route": "息子の車で高台へ行く"}))
        self.assertTrue(findings)
        for f in findings:
            with self.subTest(hole_type=f["hole_type"]):
                template = re.sub(r"\{[a-z_]+\}", "", f["explanation_template"])
                self.assertIsNone(re.search(r"[0-9０-９]", template))
                self.assertNotIn("{", f["explanation"])
                self.assertIn(f["scenario_id"], pm.SCENARIOS)
                self.assertIn(f["hole_type"], pm.HOLE_TYPES)


if __name__ == "__main__":
    unittest.main()
