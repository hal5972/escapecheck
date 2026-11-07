"""data/raw/ の元データから、土佐市の分のデータを作成する（標準ライブラリだけを使う）。

    .venv/Scripts/python.exe -I scripts/build_data.py

入力（data/raw/。git の対象外。ダウンロードは依頼者の了承を得て 2026-11-07 に実施）:
- data/raw/gsi_shelters/39205_2.csv: 国土地理院 指定緊急避難場所データ（土佐市、市町村コード 39205）
  https://hinanmap.gsi.go.jp/hinanjocp/hinanbasho/koukaidate.html（国土地理院コンテンツ利用規約と「ご利用上の注意」に同意して利用）
- data/raw/a40/A40-16_39_GML.zip: 国土数値情報 津波浸水想定（A40）高知県、平成28年
  https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A40.html（高知県はオープンデータ。商用利用可・再配信可）

出力:
- data/tosa_shelters.json: 名前・住所・座標・災害の種類ごとの対象・津波の対象か・種類（施設名からの推定）
- data/tosa_a40_h28.geojson: 土佐市とその周辺の矩形に入る多角形と、浸水深の階級

A40 は、同じ zip に入っているシェープファイル（.shp・.dbf）を読む。GML（211MB）と同じデータで、読み込みが速いため。
座標は小数第6位（約0.1m）に丸める。
"""

from __future__ import annotations

import csv
import io
import json
import re
import struct
import sys
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SHELTER_CSV = RAW / "gsi_shelters" / "39205_2.csv"
A40_ZIP = RAW / "a40" / "A40-16_39_GML.zip"
OUT_SHELTERS = ROOT / "data" / "tosa_shelters.json"
OUT_A40 = ROOT / "data" / "tosa_a40_h28.geojson"

#: 土佐市の津波浸水想定区域（宇佐・新居・用石）を含む矩形（経度・緯度）。
#: 東の端は仁淀川の河口の西（川の東は高知市）、南の端は浦ノ内湾の北岸。周辺の市の一部を含む
TOSA_BBOX = (133.400, 33.415, 133.482, 33.515)  # (最小経度, 最小緯度, 最大経度, 最大緯度)

HAZARD_COLUMNS = ["洪水", "崖崩れ、土石流及び地滑り", "高潮", "地震", "津波", "大規模な火事", "内水氾濫", "火山現象"]

SHELTER_INFO = {
    "name": "国土地理院 指定緊急避難場所データ（土佐市）",
    "url": "https://hinanmap.gsi.go.jp/hinanjocp/hinanbasho/koukaidate.html",
    "version": "2025-11-25 更新（2026-11-07 にダウンロード）",
    "terms": "国土地理院コンテンツ利用規約（https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html）と「ご利用上の注意」",
    "notice": [
        "市町村長が指定した指定緊急避難場所を、各市町村が登録したデータです。最新でない場合や未掲載の場合があるため、最新の状況は土佐市に確認してください",
        "指定緊急避難場所は災害の種類ごとに指定されています。この作品では「津波」の対象の場所だけを避難先にします",
        "指定緊急避難場所（緊急に避難する場所）と指定避難所（一定期間滞在する施設）は異なります",
    ],
    "processing": "土佐市の分の名前・住所・座標・災害の種類ごとの対象だけを残し、種類（タワー・屋上・高台など）を施設名から推定して加えた",
}

A40_INFO = {
    "name": "国土数値情報 津波浸水想定（A40）高知県",
    "url": "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A40.html",
    "version": "平成28年",
    "credit": "出典: 国土交通省国土数値情報ダウンロードサイト（https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A40.html）。土佐市とその周辺の範囲に切り出して加工",
    "processing": "シェープファイルの多角形のうち、外接矩形が TOSA_BBOX と重なるものを残し、座標を小数第6位に丸め、浸水深の階級を短い表記（rank）にした",
    "bbox": list(TOSA_BBOX),
    "note": "高知県の令和7年（2025年）の新しい予測は含まれていない",
}


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def shelter_kind(name: str, tsunami_ok: bool) -> str:
    if not tsunami_ok:
        return "指定緊急避難場所（津波は対象外）"
    n = _nfkc(name)
    if "タワー" in n:
        return "津波避難タワー"
    if "屋上" in n:
        return "津波避難ビル（屋上）"
    return "高台など（施設名からの推定）"


def build_shelters() -> dict:
    raw = SHELTER_CSV.read_bytes().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    shelters = []
    for row in rows:
        hazards = [h for h in HAZARD_COLUMNS if (row.get(h) or "").strip() == "1"]
        tsunami_ok = "津波" in hazards
        shelters.append({
            "shelter_id": row["共通ID"].strip(),
            "name": _nfkc(row["施設・場所名"]).strip(),
            "address": row["住所"].strip(),
            "lat": round(float(row["緯度"]), 6),
            "lon": round(float(row["経度"]), 6),
            "hazards": hazards,
            "tsunami_ok": tsunami_ok,
            "kind": shelter_kind(row["施設・場所名"], tsunami_ok),
        })
    return {**SHELTER_INFO, "count": len(shelters), "tsunami_count": sum(s["tsunami_ok"] for s in shelters),
            "shelters": shelters}


def rank_label(text: str) -> str:
    """「5m以上 ～ 10m未満」→「5〜10m」、「0.01m以上 ～ 0.3m未満」→「0.3m未満」、「20m以上」→「20m以上」。"""
    numbers = re.findall(r"\d+(?:\.\d+)?", _nfkc(text))
    if len(numbers) >= 2:
        low, high = numbers[0], numbers[1]
        return f"{high}m未満" if float(low) <= 0.01 else f"{low}〜{high}m"
    if len(numbers) == 1:
        return f"{numbers[0]}m以上"
    return text.strip()


def _read_dbf(data: bytes) -> list[dict[str, str]]:
    count = struct.unpack("<I", data[4:8])[0]
    header_len = struct.unpack("<H", data[8:10])[0]
    record_len = struct.unpack("<H", data[10:12])[0]
    fields = []
    for offset in range(32, header_len - 1, 32):
        descriptor = data[offset:offset + 32]
        if descriptor[0] == 0x0D:
            break
        fields.append((descriptor[:11].split(b"\x00")[0].decode("ascii"), descriptor[16]))
    records = []
    for i in range(count):
        record = data[header_len + i * record_len: header_len + (i + 1) * record_len]
        pos, values = 1, {}
        for name, length in fields:
            values[name] = record[pos:pos + length].decode("cp932", errors="replace").strip()
            pos += length
        records.append(values)
    return records


def _signed_area(ring: list[tuple[float, float]]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:])) / 2


def _in_ring(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < xi + (lat - yi) * (xj - xi) / (yj - yi):
            inside = not inside
        j = i
    return inside


def _polygons(parts: list[list[tuple[float, float]]]) -> list[list[list[tuple[float, float]]]]:
    """シェープファイルの輪（外側は時計回り、穴は反時計回り）を、外側ごとの多角形にまとめる。"""
    outers = [[ring] for ring in parts if _signed_area(ring) <= 0]
    holes = [ring for ring in parts if _signed_area(ring) > 0]
    if not outers:  # 向きが逆のデータへの備え
        outers, holes = [[ring] for ring in parts], []
    for hole in holes:
        lon, lat = hole[0]
        target = next((poly for poly in outers if _in_ring(lon, lat, poly[0])), outers[0])
        target.append(hole)
    return outers


def build_a40() -> dict:
    with zipfile.ZipFile(A40_ZIP) as archive:
        shp = archive.read("A40-16_39.shp")
        attributes = _read_dbf(archive.read("A40-16_39.dbf"))
    min_lon, min_lat, max_lon, max_lat = TOSA_BBOX
    features = []
    offset, index = 100, 0
    while offset < len(shp):
        _, length = struct.unpack(">II", shp[offset:offset + 8])
        content = shp[offset + 8: offset + 8 + length * 2]
        offset += 8 + length * 2
        record_index, index = index, index + 1
        if struct.unpack("<i", content[:4])[0] != 5:  # Polygon だけ
            continue
        xmin, ymin, xmax, ymax = struct.unpack("<4d", content[4:36])
        if xmax < min_lon or xmin > max_lon or ymax < min_lat or ymin > max_lat:
            continue
        num_parts, num_points = struct.unpack("<2i", content[36:44])
        starts = list(struct.unpack(f"<{num_parts}i", content[44:44 + 4 * num_parts])) + [num_points]
        coords = struct.unpack(f"<{2 * num_points}d", content[44 + 4 * num_parts: 44 + 4 * num_parts + 16 * num_points])
        points = [(round(coords[2 * k], 6), round(coords[2 * k + 1], 6)) for k in range(num_points)]
        parts = [points[starts[p]:starts[p + 1]] for p in range(num_parts)]
        polygons = _polygons(parts)
        original = attributes[record_index].get("A40_003", "")
        geometry = (
            {"type": "Polygon", "coordinates": polygons[0]}
            if len(polygons) == 1
            else {"type": "MultiPolygon", "coordinates": polygons}
        )
        features.append({"type": "Feature", "properties": {"rank": rank_label(original), "A40_003": original},
                         "geometry": geometry})
    return {"type": "FeatureCollection", "metadata": {**A40_INFO, "feature_count": len(features)}, "features": features}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    missing = [p for p in (SHELTER_CSV, A40_ZIP) if not p.is_file()]
    if missing:
        print("元データがありません（data/raw/ に置いてください）: " + ", ".join(str(p.relative_to(ROOT)) for p in missing))
        return 1
    shelters = build_shelters()
    OUT_SHELTERS.write_text(json.dumps(shelters, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT_SHELTERS.relative_to(ROOT)}: {shelters['count']}件（津波の対象 {shelters['tsunami_count']}件）")
    a40 = build_a40()
    OUT_A40.write_text(json.dumps(a40, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    size_mb = OUT_A40.stat().st_size / 1_000_000
    print(f"{OUT_A40.relative_to(ROOT)}: {a40['metadata']['feature_count']}件、{size_mb:.1f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
