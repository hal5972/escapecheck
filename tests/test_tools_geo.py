"""tools/geo.py（距離、点が多角形の中か、タイルの座標）の単体テスト。"""

import unittest

from tools import geo
from test_tools_helpers import HOME, north, south, square


class DistanceTest(unittest.TestCase):
    def test_north_offset_matches_meters(self):
        for meters in (0, 1, 300, 2500):
            with self.subTest(meters=meters):
                self.assertAlmostEqual(geo.distance_m(HOME, north(meters)), meters, places=6)

    def test_symmetric(self):
        a, b = HOME, (33.48, 133.47)
        self.assertAlmostEqual(geo.distance_m(a, b), geo.distance_m(b, a), places=9)

    def test_known_pair_kochi_station_to_tosa_city_hall(self):
        # 高知駅（33.5672, 133.5436）と土佐市役所（33.4925, 133.4250）付近。地図上の直線距離は約14km
        self.assertAlmostEqual(geo.distance_m((33.5672, 133.5436), (33.4925, 133.4250)) / 1000, 14.0, delta=0.5)

    def test_polyline_length(self):
        points = [HOME, north(100), north(250)]
        self.assertAlmostEqual(geo.polyline_m(points), 250, places=6)
        self.assertEqual(geo.polyline_m([HOME]), 0)


class PointInPolygonTest(unittest.TestCase):
    def setUp(self):
        self.outer = square(HOME, 200)
        self.hole = square(HOME, 50)

    def test_inside_and_outside(self):
        self.assertTrue(geo.point_in_rings(HOME, [self.outer]))
        self.assertTrue(geo.point_in_rings(north(150), [self.outer]))
        self.assertFalse(geo.point_in_rings(north(250), [self.outer]))
        self.assertFalse(geo.point_in_rings(south(250), [self.outer]))

    def test_hole_is_outside(self):
        self.assertFalse(geo.point_in_rings(HOME, [self.outer, self.hole]))
        self.assertTrue(geo.point_in_rings(north(120), [self.outer, self.hole]))

    def test_bbox(self):
        min_lon, min_lat, max_lon, max_lat = geo.bbox_of(self.outer)
        self.assertLess(min_lon, HOME[1])
        self.assertGreater(max_lat, HOME[0])


class TileTest(unittest.TestCase):
    def test_tile_of_point(self):
        # 経度0・緯度0 はズーム1でタイル (1, 1)
        self.assertEqual(geo.tile_xy(0.0, 0.0, 1), (1, 1))
        # 土佐市の宇佐付近はズーム15で、x はおおむね 28530 前後（経度から計算できる値）
        x, y = geo.tile_xy(33.45, 133.44, 15)
        self.assertEqual(x, int((133.44 + 180) / 360 * 2**15))
        self.assertGreater(y, 0)


if __name__ == "__main__":
    unittest.main()
