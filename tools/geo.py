"""地理の計算（純粋関数）: 2点間の距離、折れ線の長さ、点が多角形の中か、地理院タイルの座標。

座標は (緯度, 経度) の組で受け取る。多角形の輪は (経度, 緯度) の点の並び（GeoJSON と同じ順）。
距離は球面の大円距離（ハバーサイン公式）。土佐市の範囲（数km）では、測地線との差は避難の計算に影響しない大きさ。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

#: 地球の平均半径（メートル）
EARTH_RADIUS_M = 6371008.8

LatLon = tuple[float, float]
Ring = Sequence[tuple[float, float]]  # (経度, 緯度)


def distance_m(a: LatLon, b: LatLon) -> float:
    """2点間の直線距離（メートル）。"""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1 - h)))


def polyline_m(points: Sequence[LatLon]) -> float:
    """折れ線の長さ（メートル）。点が1つ以下なら 0。"""
    return sum(distance_m(points[i], points[i + 1]) for i in range(len(points) - 1))


def bbox_of(ring: Ring) -> tuple[float, float, float, float]:
    """輪の外接矩形 (最小経度, 最小緯度, 最大経度, 最大緯度)。"""
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lons), min(lats), max(lons), max(lats))


def _in_ring(lon: float, lat: float, ring: Ring) -> bool:
    """点が輪の内側か（偶奇判定）。境界上の点の扱いは決めない（10m の格子に対して影響しない）。"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = xi + (lat - yi) * (xj - xi) / (yj - yi)
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_rings(point: LatLon, rings: Iterable[Ring]) -> bool:
    """点が多角形（最初の輪が外側、残りが穴）の中か。"""
    lat, lon = point
    rings = list(rings)
    if not rings or not _in_ring(lon, lat, rings[0]):
        return False
    return not any(_in_ring(lon, lat, hole) for hole in rings[1:])


def tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """緯度経度を、地理院タイル（Web メルカトル）のタイル番号 (x, y) に変換する。"""
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (min(max(x, 0), n - 1), min(max(y, 0), n - 1))
