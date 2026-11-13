import io
import json
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile
import shapely

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "jp.mlit"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://nlftp.mlit.go.jp/ksj/gml/data/N03"
LANDING = "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html"
PUBLISHER = "Ministry of Land, Infrastructure, Transport and Tourism"
TITLE = "National Land Numerical Information, administrative areas (N03), 2007-2026"
LAW = "https://laws.e-gov.go.jp/law/325AC1000000100"
LAW_XML = "https://laws.e-gov.go.jp/api/1/lawdata/325AC1000000100"
LAW_PUBLISHER = "Digital Agency, e-Gov Law Search"
LAW_TITLE = "Public Offices Election Act (Act No. 100 of 1950)"
TOLERANCE = 0.0001
CHANGED = 0.002
VINTAGES = {
    "2007": ("2007", "070401", date(2007, 4, 1), True),
    "2009": ("2008", "090320", date(2009, 3, 20), True),
    "2010": ("2009", "100329", date(2010, 3, 29), True),
    "2012": ("2012", "120401", date(2012, 4, 1), True),
    "2013": ("2013", "130401", date(2013, 4, 1), True),
    "2014": ("2014", "140401", date(2014, 4, 1), False),
    "2016": ("2016", "160101", date(2016, 1, 1), False),
    "2017": ("2017", "170101", date(2017, 1, 1), False),
    "2019": ("2019", "190101", date(2019, 1, 1), False),
    "2021": ("2021", "20210101", date(2021, 1, 1), False),
    "2022": ("2022", "20220101", date(2022, 1, 1), False),
    "2024": ("2024", "20240101", date(2024, 1, 1), False),
    "2025": ("2025", "20250101", date(2025, 1, 1), False),
    "2026": ("2026", "20260101", date(2026, 1, 1), False),
}
RECODED = {"01439": "01472", "01488": "01520"}
RECODED_IN_ERROR = "2013"
TERRITORIES = {"01695", "01696", "01697", "01698", "01699", "01700"}
PREFECTURES = (
    ("北海道", "Hokkaido"),
    ("青森県", "Aomori"),
    ("岩手県", "Iwate"),
    ("宮城県", "Miyagi"),
    ("秋田県", "Akita"),
    ("山形県", "Yamagata"),
    ("福島県", "Fukushima"),
    ("茨城県", "Ibaraki"),
    ("栃木県", "Tochigi"),
    ("群馬県", "Gunma"),
    ("埼玉県", "Saitama"),
    ("千葉県", "Chiba"),
    ("東京都", "Tokyo"),
    ("神奈川県", "Kanagawa"),
    ("新潟県", "Niigata"),
    ("富山県", "Toyama"),
    ("石川県", "Ishikawa"),
    ("福井県", "Fukui"),
    ("山梨県", "Yamanashi"),
    ("長野県", "Nagano"),
    ("岐阜県", "Gifu"),
    ("静岡県", "Shizuoka"),
    ("愛知県", "Aichi"),
    ("三重県", "Mie"),
    ("滋賀県", "Shiga"),
    ("京都府", "Kyoto"),
    ("大阪府", "Osaka"),
    ("兵庫県", "Hyogo"),
    ("奈良県", "Nara"),
    ("和歌山県", "Wakayama"),
    ("鳥取県", "Tottori"),
    ("島根県", "Shimane"),
    ("岡山県", "Okayama"),
    ("広島県", "Hiroshima"),
    ("山口県", "Yamaguchi"),
    ("徳島県", "Tokushima"),
    ("香川県", "Kagawa"),
    ("愛媛県", "Ehime"),
    ("高知県", "Kochi"),
    ("福岡県", "Fukuoka"),
    ("佐賀県", "Saga"),
    ("長崎県", "Nagasaki"),
    ("熊本県", "Kumamoto"),
    ("大分県", "Oita"),
    ("宮崎県", "Miyazaki"),
    ("鹿児島県", "Kagoshima"),
    ("沖縄県", "Okinawa"),
)
DESIGNATED = {
    "01100": "札幌市",
    "04100": "仙台市",
    "11100": "さいたま市",
    "12100": "千葉市",
    "14100": "横浜市",
    "14130": "川崎市",
    "14150": "相模原市",
    "15100": "新潟市",
    "22100": "静岡市",
    "22130": "浜松市",
    "23100": "名古屋市",
    "26100": "京都市",
    "27100": "大阪市",
    "27140": "堺市",
    "28100": "神戸市",
    "33100": "岡山市",
    "34100": "広島市",
    "40100": "北九州市",
    "40130": "福岡市",
    "43100": "熊本市",
}
BLOCKS = (
    ("北海道", "Hokkaido", (1,)),
    ("東北", "Tohoku", (2, 3, 4, 5, 6, 7)),
    ("北関東", "Kita-Kanto", (8, 9, 10, 11)),
    ("南関東", "Minami-Kanto", (12, 14, 19)),
    ("東京都", "Tokyo", (13,)),
    ("北陸信越", "Hokuriku-Shinetsu", (15, 16, 17, 18, 20)),
    ("東海", "Tokai", (21, 22, 23, 24)),
    ("近畿", "Kinki", (25, 26, 27, 28, 29, 30)),
    ("中国", "Chugoku", (31, 32, 33, 34, 35)),
    ("四国", "Shikoku", (36, 37, 38, 39)),
    ("九州", "Kyushu", (40, 41, 42, 43, 44, 45, 46, 47)),
)
BLOCKS_FROM = date(1996, 10, 20)
KINDS = {
    "city": "city",
    "designated_city": "designated city",
    "special_ward": "special ward",
    "town": "town",
    "village": "village",
}
SCHEMA = {
    "layer": pl.String,
    "code": pl.String,
    "parent": pl.String,
    "name": pl.String,
    "kind": pl.String,
    "geom": pl.Binary,
}


def urls(vintage: str) -> list[str]:
    folder, stem, _, per_prefecture = VINTAGES[vintage]
    if per_prefecture:
        return [f"{BASE}/N03-{folder}/N03-{stem}_{prefecture:02d}_GML.zip" for prefecture in range(1, 48)]
    return [f"{BASE}/N03-{folder}/N03-{stem}_GML.zip"]


def fetch() -> None:
    raw.fetch(SOURCE, LAW_XML, suffix=".xml")
    for vintage in VINTAGES:
        for url in urls(vintage):
            raw.fetch(SOURCE, url, suffix=".zip")


def ward(code: str) -> bool:
    return code[2] == "1" and not code.startswith("13")


def kind(code: str, name: str) -> str:
    if code in DESIGNATED:
        return "designated_city"
    if code.startswith("131"):
        return "special_ward"
    return {"市": "city", "町": "town", "村": "village"}[name[-1]]


def read(vintage: str) -> list[tuple[str, str, str, shapely.Geometry]]:
    prefectures = {name: f"{number:02d}" for number, (name, _) in enumerate(PREFECTURES, 1)}
    cities = {(code[:2], name): code for code, name in DESIGNATED.items()}
    features: dict[str, tuple[str, str, list[shapely.Geometry]]] = {}
    for url in urls(vintage):
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            names = archive.namelist()
            stem = next(
                name.removesuffix(".shp")
                for name in names
                if name.endswith(".shp") and not name.endswith("_prefecture.shp")
            )
            encoding = archive.read(f"{stem}.cpg").decode().strip() if f"{stem}.cpg" in names else "cp932"
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(f"{stem}.shp")),
                shx=io.BytesIO(archive.read(f"{stem}.shx")),
                dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                encoding=encoding,
            )
            fields = [field.name for field in reader.fields[1:]]
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                if record is None or shape is None:
                    continue
                row = {key: str(value or "").strip() for key, value in zip(fields, record, strict=True)}
                code = row["N03_007"]
                if not code or code.endswith("000") or code in TERRITORIES or shape.shapeType == shapefile.NULL:
                    continue
                if prefectures[row["N03_001"]] != code[:2]:
                    raise ValueError(f"{vintage} {code}: prefecture {row['N03_001']}")
                parts = [row.get(f"N03_00{n}", "") for n in (3, 4, 5)]
                if ward(code):
                    city, name = (parts[1], parts[2]) if parts[2] else (parts[0], parts[1])
                    entry = (cities[code[:2], city], name)
                else:
                    entry = (code[:2], (parts[2] or parts[1] or parts[0]).removesuffix("入会地"))
                known = features.setdefault(code, (*entry, []))
                if known[:2] != entry:
                    raise ValueError(f"{vintage} {code}: {known[:2]} and {entry}")
                known[2].append(shapely.from_geojson(json.dumps(shape.__geo_interface__)))
    return [
        (code, parent, name, shapely.make_valid(shapely.union_all(parts)))
        for code, (parent, name, parts) in sorted(features.items())
    ]


def polygons(geometry: shapely.Geometry) -> shapely.Geometry:
    parts = list(shapely.get_parts(shapely.make_valid(geometry)))
    polygons_: list[shapely.Polygon] = []
    while parts:
        part = parts.pop()
        if isinstance(part, shapely.Polygon):
            if not part.is_empty:
                polygons_.append(part)
        elif isinstance(part, (shapely.MultiPolygon, shapely.GeometryCollection)):
            parts.extend(shapely.get_parts(part))
    return shapely.MultiPolygon(polygons_)


def merge(members: list[shapely.Geometry]) -> shapely.Geometry:
    expected = sum(shapely.area(members))
    try:
        merged = shapely.coverage_union_all(members)
        if abs(shapely.area(merged) - expected) <= 1e-6 * expected:
            return merged
    except shapely.errors.GEOSException:
        pass
    merged = shapely.union_all(shapely.make_valid(members), grid_size=1e-7)
    if abs(shapely.area(merged) - expected) > 1e-3 * expected:
        raise ValueError(f"union of {len(members)} areas covers {shapely.area(merged)}, not {expected}")
    return merged


def layers(vintage: str) -> pl.DataFrame:
    features = read(vintage)
    simplified = shapely.coverage_simplify([geometry for *_, geometry in features], TOLERANCE)
    rows: list[tuple[str, str, str, str, str | None, shapely.Geometry]] = []
    pieces: dict[str, list[shapely.Geometry]] = {}
    wards: dict[str, list[shapely.Geometry]] = {}
    for (code, parent, name, _), geometry in zip(features, simplified, strict=True):
        pieces.setdefault(code[:2], []).append(geometry)
        if ward(code):
            wards.setdefault(parent, []).append(geometry)
            rows.append(("ward", code, parent, name, None, geometry))
        else:
            rows.append(("municipality", code, parent, name, None, geometry))
    for code, members in wards.items():
        rows.append(("municipality", code, code[:2], DESIGNATED[code], "designated_city", merge(members)))
    prefectures = {code: merge(members) for code, members in pieces.items()}
    if len(prefectures) != 47:
        raise ValueError(f"{vintage}: {len(prefectures)} prefectures")
    for code, geometry in prefectures.items():
        rows.append(("prefecture", code, "JP", PREFECTURES[int(code) - 1][0], None, geometry))
    for number, (native, _, members) in enumerate(BLOCKS, 1):
        geometry = merge([prefectures[f"{member:02d}"] for member in members])
        rows.append(("block", f"{number:02d}", "JP", native, None, geometry))
    rows.append(("nation", "JP", "", "日本", None, merge(list(prefectures.values()))))
    return pl.DataFrame(
        [(*row[:5], shapely.to_wkb(polygons(row[5]))) for row in rows], schema=SCHEMA, orient="row"
    ).with_columns(pl.lit(vintage).alias("vintage"))


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for vintage in VINTAGES:
        path = PARSED / f"n03_{vintage}.parquet"
        if not path.exists():
            frame = layers(vintage)
            counts = dict(frame.group_by("layer").len().iter_rows())
            print(vintage, counts)
            frame.write_parquet(path)
    frames = pl.concat([pl.read_parquet(PARSED / f"n03_{vintage}.parquet") for vintage in VINTAGES])
    full = {
        (layer, code, name): max((other for other in names if other.startswith(name)), key=len)
        for layer, code, names in frames.group_by(["layer", "code"]).agg(pl.col("name").unique()).iter_rows()
        for name in names
    }
    frames = frames.with_columns(
        pl.Series("name", [full[key] for key in frames.select("layer", "code", "name").iter_rows()])
    )
    frames = frames.with_columns(
        pl.Series(
            "kind",
            [
                kind(code, name) if layer == "municipality" else None
                for layer, code, name in frames.select("layer", "code", "name").iter_rows()
            ],
            dtype=pl.String,
        )
    )
    order = list(VINTAGES)
    recoded = pl.col("code").replace(RECODED)
    frames = frames.with_columns(
        pl.when(pl.col("vintage") == RECODED_IN_ERROR).then(recoded).otherwise(pl.col("code")).alias("printed"),
        recoded.alias("code"),
    )
    versions: list[dict[str, object]] = []
    spans: list[dict[str, object]] = []
    for (layer, code), group in frames.sort("vintage").group_by(["layer", "code"], maintain_order=True):
        present = group["vintage"].to_list()
        first, last = order.index(present[0]), order.index(present[-1])
        if present != order[first : last + 1]:
            raise ValueError(f"{layer} {code} skips a vintage: {present}")
        ends = VINTAGES[order[last + 1]][2] if last + 1 < len(order) else None
        printed = group.group_by("printed").agg(pl.col("vintage").min().alias("first"), pl.col("vintage").max())
        for printed_code, start, end in printed.sort("first").iter_rows():
            following = order.index(end) + 1
            spans.append(
                {
                    "layer": layer,
                    "area": code,
                    "code": printed_code,
                    "valid_from": None if start == order[0] else VINTAGES[start][2],
                    "valid_to": VINTAGES[order[following]][2] if following < len(order) else None,
                }
            )
        kept: shapely.Geometry | None = None
        current: dict[str, object] | None = None
        for vintage, parent, name, kind_, wkb in group.select("vintage", "parent", "name", "kind", "geom").rows():
            geometry = shapely.from_wkb(wkb)
            moved = kept is None or (
                shapely.area(shapely.symmetric_difference(kept, geometry))
                > CHANGED * max(shapely.area(kept), shapely.area(geometry))
            )
            same = current is not None and (current["parent"], current["name"], current["kind"]) == (
                parent,
                name,
                kind_,
            )
            if not moved and same and current is not None:
                continue
            start = None if vintage == order[0] else VINTAGES[vintage][2]
            if current is not None:
                current["valid_to"] = start
            if moved:
                kept = geometry
            current = {
                "layer": layer,
                "code": code,
                "vintage": vintage,
                "valid_from": start,
                "valid_to": ends,
                "parent": parent,
                "name": name,
                "kind": kind_,
                "geom": shapely.to_wkb(shapely.set_srid(kept, 4326), hex=True, include_srid=True)
                if moved
                else versions[-1]["geom"],
                "label_point": shapely.to_wkb(
                    shapely.set_srid(max(shapely.get_parts(kept), key=shapely.area).representative_point(), 4326),
                    hex=True,
                    include_srid=True,
                ),
                "new_geometry": moved,
            }
            versions.append(current)
    pl.DataFrame(spans).write_parquet(PARSED / "spans.parquet")
    pl.DataFrame(versions).write_parquet(PARSED / "versions.parquet")


def runs(rows: list[dict[str, object]], field: str) -> list[tuple[object, object, object]]:
    merged: list[list[object]] = []
    for row in rows:
        if merged and merged[-1][0] == row[field]:
            merged[-1][2] = row["valid_to"]
        else:
            merged.append([row[field], row["valid_from"], row["valid_to"]])
    return [(value, start, end) for value, start, end in merged]


def emit() -> Path:
    versions = pl.read_parquet(PARSED / "versions.parquet").sort("layer", "code", "vintage")
    spans = pl.read_parquet(PARSED / "spans.parquet")
    order = list(VINTAGES)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan, its prefectures, municipalities, designated-city wards, and House of Representatives "
            "proportional blocks from MLIT N03 administrative areas, 2007-2026",
            polity="JP",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="「国土数値情報（行政区域データ）」（国土交通省）を加工して作成",
        )
        for vintage in VINTAGES:
            for url in urls(vintage):
                cs.artifact(raw.cached(SOURCE, url), source)
        law = cs.source(LAW_PUBLISHER, LAW_TITLE)
        cs.add(
            "source",
            id=law,
            kind="official_body",
            publisher=LAW_PUBLISHER,
            title=LAW_TITLE,
            url=LAW,
            license="Public domain (Copyright Act art. 13)",
            license_url="https://laws.e-gov.go.jp/law/345AC0000000048#Mp-At_13",
            attribution="e-Gov法令検索",
        )
        cs.artifact(raw.cached(SOURCE, LAW_XML), law)

        def name(entity: UUID, text: str, lang: str, kind_: str = "official", **valid: object) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind_,
                is_preferred=kind_ == "official",
                source_id=source,
                **valid,
            )

        def span(start: object, end: object, exact: bool = False) -> dict[str, object]:
            return {
                "valid_from": start,
                "valid_from_precision": "day" if start is None or exact else "unknown",
                "valid_to": end,
                "valid_to_precision": "unknown" if end is not None else "day",
            }

        japan = cs.polity("JP")
        cs.add("polity", id=japan, kind="sovereign_state", iso_code="JP")
        name(japan, "Japan", "en")
        name(japan, "日本", "ja", "native")
        cs.add("slug", path="jp", entity_id=japan)
        cs.add("external_id", entity_id=japan, scheme="wikidata", value="Q17")
        for code, kind_, rank, noun, system, kinds in (
            ("jp.nation", "administrative", 0, "country", None, None),
            ("jp.prefecture", "administrative", 1, "prefecture", "jis_x0401", None),
            ("jp.municipality", "administrative", 2, "municipality", "jis_x0402", KINDS),
            ("jp.ward", "administrative", 3, "ward", "jis_x0402", None),
            ("jp.hor_block", "electoral_district", 1, "proportional representation block", None, None),
        ):
            cs.add(
                "scheme", code=code, polity_id=japan, kind=kind_, rank=rank, noun=noun, code_system=system, kinds=kinds
            )
        schemes = {
            "nation": "jp.nation",
            "prefecture": "jp.prefecture",
            "municipality": "jp.municipality",
            "ward": "jp.ward",
            "block": "jp.hor_block",
        }

        areas: dict[tuple[str, str], UUID] = {}
        for layer, area_code in spans.select("layer", "area").unique().sort("layer", "area").iter_rows():
            area = areas[layer, area_code] = cs.area(schemes[layer], area_code)
            cs.add("area", id=area, scheme_code=schemes[layer])
        for layer, area_code, code, start, end in spans.select(
            "layer", "area", "code", "valid_from", "valid_to"
        ).iter_rows():
            if layer == "block":
                start = BLOCKS_FROM
            cs.add("area_code", area_id=areas[layer, area_code], code=code, **span(start, end, layer == "block"))
        nation = areas["nation", "JP"]
        cs.add("polity_territory", polity_id=japan, area_id=nation)
        for (layer, code), group in versions.group_by(["layer", "code"], maintain_order=True):
            area = areas[layer, code]
            rows = group.to_dicts()
            for text, start, end in runs(rows, "name"):
                cs.add(
                    "name",
                    entity_id=area,
                    text=text,
                    lang="ja",
                    kind="official",
                    is_preferred=True,
                    source_id=source,
                    **span(start, end),
                )
            if layer == "municipality":
                for kind_, start, end in runs(rows, "kind"):
                    cs.add("area_kind", area_id=area, kind=kind_, source_id=source, **span(start, end))
            if layer != "nation":
                for parent, start, end in runs(rows, "parent"):
                    owner = (
                        nation
                        if layer in ("prefecture", "block")
                        else areas["municipality" if layer == "ward" else "prefecture", str(parent)]
                    )
                    cs.add("area_parent", area_id=area, parent_id=owner, **span(start, end))
            shapes = [row for row in rows if row["new_geometry"]]
            for index, row in enumerate(shapes):
                cs.add(
                    "area_geometry",
                    area_id=area,
                    geom=row["geom"],
                    label_point=row["label_point"],
                    quality="official",
                    source_id=source,
                    **span(
                        row["valid_from"],
                        shapes[index + 1]["valid_from"] if index + 1 < len(shapes) else row["valid_to"],
                    ),
                )
        for number, (native, english, members) in enumerate(BLOCKS, 1):
            block = areas["block", f"{number:02d}"]
            name(block, english, "en", "english")
            for member in members:
                cs.add(
                    "area_membership",
                    area_id=areas["prefecture", f"{member:02d}"],
                    parent_id=block,
                    **span(BLOCKS_FROM, None, True),
                )
        name(nation, "Japan", "en", "english")
        for number, (native, english) in enumerate(PREFECTURES, 1):
            code = f"{number:02d}"
            polity = cs.polity(f"JP-{code}")
            cs.add("polity", id=polity, kind="region", iso_code=f"JP-{code}")
            cs.add("slug", path=f"jp/{english.lower()}", entity_id=polity)
            cs.add("polity_parent", polity_id=polity, parent_id=japan)
            cs.add("polity_territory", polity_id=polity, area_id=areas["prefecture", code])
            name(polity, native, "ja")
            name(polity, english, "en", "english")
            name(areas["prefecture", code], english, "en", "english")
        for index, vintage in enumerate(order):
            start = VINTAGES[vintage][2]
            end = VINTAGES[order[index + 1]][2] if index + 1 < len(order) else None
            for scheme in ("jp.prefecture", "jp.municipality", "jp.ward", "jp.hor_block"):
                cs.add(
                    "boundary_set",
                    scheme_code=scheme,
                    vintage=start.isoformat(),
                    valid_from=None if index == 0 else start,
                    valid_to=end,
                    as_of=start,
                )
        return cs.write()
