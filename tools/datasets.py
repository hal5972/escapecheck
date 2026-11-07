"""data/ のデータ（津波到達時間の表、指定緊急避難場所、津波浸水想定の多角形）の読み込み。

- 到達時間の表: data/tosa_arrival_times.json（PDF から手で入力。行ごとに版・出典・ページ・状態）
- 指定緊急避難場所: data/tosa_shelters.json（国土地理院のデータを scripts/build_data.py で土佐市の分に加工）
- 津波浸水想定: data/tosa_a40_h28.geojson（国土数値情報 A40 を scripts/build_data.py で土佐市の周辺に切り出し）

読み込んだデータは変更しない（frozen）。単体テストは override() でテスト用のデータに置き換える。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from tools import geo

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ARRIVAL_PATH = DATA_DIR / "tosa_arrival_times.json"
SHELTERS_PATH = DATA_DIR / "tosa_shelters.json"
A40_PATH = DATA_DIR / "tosa_a40_h28.geojson"

#: 到達時間の表で計算に使う行の状態（PDF で確認した値だけを使う）
CONFIRMED = "確認済"


@dataclass(frozen=True)
class ArrivalArea:
    """到達時間の表の1行（ある地点で、浸水が 30cm に達するまでの時間）。"""

    area_id: str
    label: str
    lat: float
    lon: float
    minutes_30cm: float
    version: str
    source_url: str
    page: int | str | None
    status: str
    source_name: str = ""
    note: str = ""
    #: 範囲の行（帯の縁など）: この半径の中の地点にだけ使う。None は代表の地点（一番近い地点として使う）
    radius_m: float | None = None

    @property
    def point(self) -> tuple[float, float]:
        return (self.lat, self.lon)


@dataclass(frozen=True)
class ShelterRecord:
    """指定緊急避難場所の1件。"""

    shelter_id: str
    name: str
    lat: float
    lon: float
    tsunami_ok: bool
    kind: str
    address: str = ""

    @property
    def point(self) -> tuple[float, float]:
        return (self.lat, self.lon)


@dataclass(frozen=True)
class InundationArea:
    """津波浸水想定の多角形1つ（最初の輪が外側、残りが穴）と、浸水深の階級。"""

    rank: str
    rings: tuple[tuple[tuple[float, float], ...], ...]
    bbox: tuple[float, float, float, float]

    @classmethod
    def from_rings(cls, rank: str, rings: Sequence[Sequence[Sequence[float]]]) -> "InundationArea":
        frozen = tuple(tuple((float(p[0]), float(p[1])) for p in ring) for ring in rings)
        return cls(rank=rank, rings=frozen, bbox=geo.bbox_of(frozen[0]))

    def contains(self, point: tuple[float, float]) -> bool:
        lat, lon = point
        min_lon, min_lat, max_lon, max_lat = self.bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            return False
        return geo.point_in_rings(point, self.rings)


@dataclass
class Dataset:
    arrival_areas: list[ArrivalArea] = field(default_factory=list)
    shelters: list[ShelterRecord] = field(default_factory=list)
    inundation: list[InundationArea] | None = None
    arrival_info: dict = field(default_factory=dict)
    shelter_info: dict = field(default_factory=dict)
    inundation_info: dict = field(default_factory=dict)
    #: 浸水想定は大きいので、必要になったときに読み込む
    inundation_loader: Callable[[], tuple[list[InundationArea], dict]] | None = None
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def confirmed_areas(self) -> list[ArrivalArea]:
        return [a for a in self.arrival_areas if a.status == CONFIRMED]

    def shelter_by_id(self, shelter_id: str) -> ShelterRecord | None:
        for shelter in self.shelters:
            if shelter.shelter_id == shelter_id:
                return shelter
        return None

    def get_inundation(self) -> list[InundationArea] | None:
        if self.inundation is None and self.inundation_loader is not None:
            with self._lock:
                if self.inundation is None:
                    self.inundation, info = self.inundation_loader()
                    self.inundation_info = info
        return self.inundation


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_arrival(path: Path = ARRIVAL_PATH) -> tuple[list[ArrivalArea], dict]:
    raw = _read_json(path)
    if raw is None:
        return [], {}
    areas = [
        ArrivalArea(
            area_id=row["area_id"],
            label=row["label"],
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            minutes_30cm=float(row["minutes_30cm"]) if row.get("minutes_30cm") is not None else float("nan"),
            version=row["version"],
            source_url=row.get("source_url", ""),
            page=row.get("page"),
            status=row.get("status", ""),
            source_name=row.get("source_name", ""),
            note=row.get("note", ""),
            radius_m=float(row["radius_m"]) if row.get("radius_m") is not None else None,
        )
        for row in raw.get("areas", [])
        if row.get("lat") is not None and row.get("lon") is not None
    ]
    # 値が読み取れなかった行（minutes_30cm が空）は「未確認」として計算に使わない
    areas = [a if a.minutes_30cm == a.minutes_30cm else _as_unconfirmed(a) for a in areas]
    info = {k: v for k, v in raw.items() if k != "areas"}
    return areas, info


def _as_unconfirmed(area: ArrivalArea) -> ArrivalArea:
    return ArrivalArea(**{**area.__dict__, "status": "未確認"})


def _load_shelters(path: Path = SHELTERS_PATH) -> tuple[list[ShelterRecord], dict]:
    raw = _read_json(path)
    if raw is None:
        return [], {}
    shelters = [
        ShelterRecord(
            shelter_id=row["shelter_id"],
            name=row["name"],
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            tsunami_ok=bool(row["tsunami_ok"]),
            kind=row.get("kind", ""),
            address=row.get("address", ""),
        )
        for row in raw.get("shelters", [])
    ]
    info = {k: v for k, v in raw.items() if k != "shelters"}
    return shelters, info


def _load_inundation(path: Path = A40_PATH) -> tuple[list[InundationArea], dict]:
    raw = _read_json(path)
    if raw is None:
        return [], {}
    areas: list[InundationArea] = []
    for feature in raw.get("features", []):
        geometry = feature.get("geometry") or {}
        rank = (feature.get("properties") or {}).get("rank", "")
        if geometry.get("type") == "Polygon":
            polygons = [geometry["coordinates"]]
        elif geometry.get("type") == "MultiPolygon":
            polygons = geometry["coordinates"]
        else:
            continue
        for rings in polygons:
            if rings:
                areas.append(InundationArea.from_rings(rank, rings))
    return areas, dict(raw.get("metadata", {}))


_default: Dataset | None = None
_default_lock = Lock()
_override: ContextVar[Dataset | None] = ContextVar("escapecheck_dataset_override", default=None)


def load_default() -> Dataset:
    """data/ のデータを読み込む（1回だけ。浸水想定は必要になったときに読み込む）。"""
    global _default
    with _default_lock:
        if _default is None:
            arrival, arrival_info = _load_arrival()
            shelters, shelter_info = _load_shelters()
            inundation_available = A40_PATH.is_file()
            _default = Dataset(
                arrival_areas=arrival,
                shelters=shelters,
                arrival_info=arrival_info,
                shelter_info=shelter_info,
                inundation=None if inundation_available else [],
                inundation_loader=_load_inundation if inundation_available else None,
            )
        return _default


def current() -> Dataset:
    """計算に使うデータ。テストで override() されていれば、そのデータ。"""
    return _override.get() or load_default()


@contextmanager
def override(dataset: Dataset) -> Iterator[Dataset]:
    """単体テスト用: 計算に使うデータを一時的に置き換える。"""
    token = _override.set(dataset)
    try:
        yield dataset
    finally:
        _override.reset(token)
