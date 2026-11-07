"""tools/hazard.py（地理の事実）の単体テスト。国土地理院の標高 API は呼ばず、置き換えて確認する。"""

import unittest
from unittest import mock

from tools import datasets, hazard
from tools import plan_model as pm
from test_tools_helpers import add_premise, make_dataset, make_plan, north, supporter


class HazardTestCase(unittest.TestCase):
    def setUp(self):
        self._override = datasets.override(make_dataset(near_minutes=20))
        self._override.__enter__()
        patcher = mock.patch.object(hazard, "_request_elevation", return_value=(2.5, "5m（レーザ）"))
        self.request = patcher.start()
        self.addCleanup(patcher.stop)
        cache = mock.patch.object(hazard, "ELEVATION_CACHE_PATH", None)
        cache.start()
        self.addCleanup(cache.stop)
        hazard.clear_elevation_cache()

    def tearDown(self):
        self._override.__exit__(None, None, None)


class GetFactsTest(HazardTestCase):
    def test_home_facts_and_sources(self):
        plan = make_plan()
        facts = hazard.get_facts(plan, "home")
        self.assertEqual(facts["target"], "home")
        self.assertEqual(facts["inundation_rank"], "5〜10m")
        self.assertEqual(facts["arrival_min"], 20.0)
        self.assertEqual(facts["arrival_version"], "V2012")
        self.assertEqual(facts["elevation_m"], 2.5)
        for sid in facts["source_ids"]:
            with self.subTest(source_id=sid):
                self.assertIn(sid, plan["sources"])
        kinds = {plan["sources"][sid]["kind"] for sid in facts["source_ids"]}
        self.assertEqual(kinds, {"dataset"})

    def test_facts_do_not_contain_coordinates(self):
        plan = make_plan()
        facts = hazard.get_facts(plan, "home")
        text = repr(facts)
        self.assertNotIn("33.45", text)
        self.assertNotIn("133.44", text)

    def test_elevation_api_down(self):
        # AC-06: 標高 API が止まっていても例外を出さず、「取得できませんでした」と返す
        self.request.side_effect = OSError("timeout")
        facts = hazard.get_facts(make_plan(), "home")
        self.assertIsNone(facts["elevation_m"])
        self.assertTrue(any("標高" in n and "取得できませんでした" in n for n in facts["notes"]))
        self.assertEqual(facts["arrival_min"], 20.0)

    def test_elevation_is_cached(self):
        plan = make_plan()
        hazard.get_facts(plan, "home")
        hazard.get_facts(plan, "home")
        self.assertEqual(self.request.call_count, 1)

    def test_outside_inundation_area(self):
        plan = make_plan()
        lat, lon = north(1000)
        plan["home"] = pm.Point(lat=lat, lon=lon)
        facts = hazard.get_facts(plan, "home")
        self.assertEqual(facts["inundation_rank"], "区域外")

    def test_supporter_target(self):
        plan = make_plan(supporters=[supporter("sup_b", "Bさん", 240)])
        facts = hazard.get_facts(plan, "sup_b")
        self.assertEqual(facts["target"], "sup_b")
        self.assertEqual(facts["arrival_min"], 20.0)

    def test_unknown_target_or_missing_location(self):
        facts = hazard.get_facts(make_plan(), "sup_nobody")
        self.assertIsNone(facts["arrival_min"])
        self.assertTrue(facts["notes"])
        facts = hazard.get_facts(make_plan(home=False), "home")
        self.assertIsNone(facts["inundation_rank"])
        self.assertTrue(facts["notes"])

    def test_versions_noted(self):
        extra = datasets.ArrivalArea(
            area_id="v2025", label="テスト地点（新しい予測）", lat=north(100)[0], lon=north(100)[1],
            minutes_30cm=24, version="V2025", source_url="https://example.invalid/2025.pdf",
            page=3, status="確認済", source_name="テストの予測（2025）",
        )
        with datasets.override(make_dataset(near_minutes=20, extra_areas=[extra])):
            facts = hazard.get_facts(make_plan(), "home")
        self.assertEqual(facts["arrival_min"], 20.0)
        self.assertEqual(facts["arrival_version"], "V2012")
        self.assertTrue(any("版" in n for n in facts["notes"]))


class ArrivalZoneTest(unittest.TestCase):
    """帯の縁（海に面した所）は、半径つきの行で表す。範囲の中だけで使い、外では一番近い地点の値を使う。"""

    def zone_dataset(self):
        from test_tools_helpers import south

        zone = datasets.ArrivalArea(
            area_id="shore", label="テストの海岸の縁", lat=south(150)[0], lon=south(150)[1], minutes_30cm=10,
            version="V2012", source_url="https://example.invalid/2012.pdf", page=1, status="確認済", radius_m=80,
        )
        return make_dataset(near_minutes=20, extra_areas=[zone])

    def test_inside_zone_uses_zone_value(self):
        from test_tools_helpers import south

        data = self.zone_dataset()
        self.assertEqual(hazard.arrival_at(south(100), data).minutes, 10)
        self.assertEqual(hazard.arrival_at(south(200), data).minutes, 10)

    def test_outside_zone_uses_nearest_point(self):
        from test_tools_helpers import south

        data = self.zone_dataset()
        # 縁の中心から 150m 離れた自宅（範囲の外）は、縁の中心の方が近くても、地点の値（20分）を使う
        self.assertEqual(hazard.arrival_at(north(0), data).minutes, 20)
        self.assertEqual(hazard.arrival_at(south(20), data).minutes, 20)

    def test_earliest_includes_zones(self):
        self.assertEqual(hazard.arrival_at(north(0), self.zone_dataset(), earliest=True).minutes, 10)


class FindSheltersTest(HazardTestCase):
    def test_only_tsunami_sites_sorted_by_distance(self):
        plan = make_plan()
        shelters = hazard.find_shelters(plan, "home", limit=3)
        self.assertEqual([s["shelter_id"] for s in shelters], ["A", "B", "C"])
        self.assertTrue(all(s["tsunami_ok"] for s in shelters))
        self.assertEqual(shelters[0]["distance_m"], 390)
        self.assertEqual(shelters[0]["distance_method"], "straight_x1.3")
        for s in shelters:
            self.assertIn(s["source_id"], plan["sources"])

    def test_limit(self):
        self.assertEqual(len(hazard.find_shelters(make_plan(), "home", limit=1)), 1)
        self.assertEqual(len(hazard.find_shelters(make_plan(), "home", limit=0)), 1)
        self.assertEqual(len(hazard.find_shelters(make_plan(), "home", limit=99)), 3)

    def test_local_notes_from_premises(self):
        plan = make_plan()
        add_premise(plan, "CANNOT_CLIMB", "あのタワーの階段は急で、よう上がらん", about="A")
        shelters = {s["shelter_id"]: s for s in hazard.find_shelters(plan, "home", limit=3)}
        self.assertEqual(shelters["A"]["local_notes"], ["あのタワーの階段は急で、よう上がらん"])
        self.assertEqual(shelters["B"]["local_notes"], [])

    def test_missing_location(self):
        self.assertEqual(hazard.find_shelters(make_plan(home=False), "home", limit=3), [])


if __name__ == "__main__":
    unittest.main()
