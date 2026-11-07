"""tools の単体テストで共通に使う、架空の地点・データ・計画の作り方（テストケースは含まない）。

地点は、基準点から真北・真南にずらして作る。真北・真南なら、直線距離が指定したメートルとほぼ一致する。
"""

from __future__ import annotations

import math

from tools import datasets, geo
from tools import plan_model as pm

#: 基準点（架空の自宅。実在の住所ではない）
HOME = (33.45, 133.44)


def north(meters: float, base: tuple[float, float] = HOME) -> tuple[float, float]:
    lat, lon = base
    return (lat + math.degrees(meters / geo.EARTH_RADIUS_M), lon)


def south(meters: float, base: tuple[float, float] = HOME) -> tuple[float, float]:
    return north(-meters, base)


def square(center: tuple[float, float], half_m: float) -> tuple[tuple[float, float], ...]:
    """中心から南北・東西に half_m の正方形の輪（経度, 緯度）。"""
    lat, lon = center
    dlat = math.degrees(half_m / geo.EARTH_RADIUS_M)
    dlon = dlat / math.cos(math.radians(lat))
    return (
        (lon - dlon, lat - dlat),
        (lon + dlon, lat - dlat),
        (lon + dlon, lat + dlat),
        (lon - dlon, lat + dlat),
        (lon - dlon, lat - dlat),
    )


def make_dataset(near_minutes: float = 20, extra_areas: list[datasets.ArrivalArea] | None = None) -> datasets.Dataset:
    """テスト用のデータ。避難先 A（北 300m、津波）、B（北 500m、津波）、P（北 100m、地震だけ）、C（南 800m、津波）。"""
    areas = [
        datasets.ArrivalArea(
            area_id="near", label="テスト地点（自宅の近く）", lat=HOME[0], lon=HOME[1],
            minutes_30cm=near_minutes, version="V2012", source_url="https://example.invalid/2012.pdf",
            page=1, status="確認済", source_name="テストの予測（2012）",
        ),
        datasets.ArrivalArea(
            area_id="far_coast", label="テスト地点（遠い沿岸）", lat=south(3000)[0], lon=HOME[1],
            minutes_30cm=15, version="V2012", source_url="https://example.invalid/2012.pdf",
            page=1, status="確認済", source_name="テストの予測（2012）",
        ),
    ]
    areas += extra_areas or []
    shelters = [
        datasets.ShelterRecord("A", "テスト避難タワー", *north(300), tsunami_ok=True, kind="津波避難タワー"),
        datasets.ShelterRecord("B", "テスト高台", *north(500), tsunami_ok=True, kind="高台"),
        datasets.ShelterRecord("P", "テスト公園", *north(100), tsunami_ok=False, kind="指定緊急避難場所（津波は対象外）"),
        datasets.ShelterRecord("C", "テスト裏山", *south(800), tsunami_ok=True, kind="高台"),
    ]
    inundation = [datasets.InundationArea.from_rings("5〜10m", [square(HOME, 200)])]
    return datasets.Dataset(
        arrival_areas=areas,
        shelters=shelters,
        inundation=inundation,
        arrival_info={"name": "テストの到達時間の表"},
        shelter_info={"name": "テストの避難場所", "url": "https://example.invalid/", "version": "テスト"},
        inundation_info={"name": "テストの浸水想定", "url": "https://example.invalid/", "version": "テスト"},
    )


FORM_SOURCE_ID = "src_form"


def make_plan(fields: dict | None = None, supporters: list[pm.Supporter] | None = None,
              shelter: object = {"shelter_id": "A", "name": "テスト避難タワー"}, home: bool = True) -> pm.Plan:
    """架空の計画。項目の値は、紙の様式（民生委員の記載）を出典にする。"""
    plan = pm.new_plan("Aさん", "テスト地区")
    if home:
        plan["home"] = pm.Point(lat=HOME[0], lon=HOME[1])
    values = {"mobility": "杖をついて歩く"}
    if shelter is not None:
        values["shelter"] = shelter
    values.update(fields or {})
    lines = [f"{key}: {value}" for key, value in values.items() if value is not None]
    pm.register_source(
        plan,
        pm.Source(kind="utterance", label="紙の様式の記載（民生委員）", text="\n".join(lines), speaker="minsei"),
        FORM_SOURCE_ID,
    )
    for key, value in values.items():
        if value is None:
            continue
        quote = value["name"] if isinstance(value, dict) and "name" in value else str(value)
        plan["fields"][key] = pm.FieldValue(value=value, status="filled", source_id=FORM_SOURCE_ID, quote=quote)
    for supporter in supporters or []:
        plan["supporters"].append(supporter)
    return plan


def supporter(supporter_id: str, alias: str, meters_south: float, age_band: str = "40代",
              available_day: bool | None = None, available_night: bool | None = None,
              relation: str = "隣人") -> pm.Supporter:
    lat, lon = south(meters_south)
    sup = pm.Supporter(supporter_id=supporter_id, alias=alias, relation=relation, age_band=age_band,
                       location=pm.Point(lat=lat, lon=lon), source_id=FORM_SOURCE_ID)
    if available_day is not None:
        sup["available_day"] = available_day
    if available_night is not None:
        sup["available_night"] = available_night
    return sup


def add_premise(plan: pm.Plan, code: str, quote: str, about: str | None = None, premise_id: str | None = None) -> str:
    """聞き取りの話（発言）を出典にして、前提条件を登録する。"""
    sid = pm.register_source(plan, pm.Source(kind="utterance", label="聞き取りの話", text=quote, speaker="person"))
    pid = premise_id or f"pr_{code.lower()}"
    premise = pm.Premise(premise_id=pid, code=code, quote=quote, source_id=sid)
    if about is not None:
        premise["about"] = about
    plan["premises"].append(premise)
    return pid
