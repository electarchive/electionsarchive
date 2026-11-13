import csv
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
import shapefile
import shapely

from ea import raw
from ea.changeset import Changeset
from ea.sources.jp import mlit
from ea.sources.jp.mlit import DESIGNATED, PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.hor_smd"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PARTS = Path(__file__).with_name("hor_smd_parts.tsv")
LAW_TITLE = (
    "Public Offices Election Act (Act No. 100 of 1950), annex 1: House of Representatives single-member districts"
)
CREDIT = (
    "公職選挙法別表第一、「国土数値情報（行政区域データ）」（国土交通省）、"
    "「国勢調査 小地域境界データ」（総務省統計局）、「大字・町丁目レベル位置参照情報」（国土交通省）を加工して作成"
)
LAW_API = "https://laws.e-gov.go.jp/api/2/law_data/325AC1000000100_{}?response_format=xml"
ISJ = "https://nlftp.mlit.go.jp/isj/dls/data/{version}/{prefecture}000-{version}.zip"
FORMER = "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2000/N03-001001_{prefecture}_GML.zip"
SUFFIX = re.compile(r"(町|新|[一二三四五六七八九十]+丁目|[0-9])")
ESTAT = (
    "https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A00200521{year}"
    "&code={prefecture:02d}&coordSys=1&format=shape&downloadType=5&datum=2011"
)


@dataclass(frozen=True)
class Table:
    revision: str
    effective: date
    n03: str
    census: int
    isj: str


TABLES = {
    "2022": Table("20221228_504AC0000000089", date(2022, 12, 28), "2022", 2020, "16.0b"),
}
NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def kanji(text: str) -> int:
    tens, _, ones = text.rpartition("十")
    if "十" not in text:
        return NUMERALS[text]
    return (NUMERALS[tens] if tens else 1) * 10 + (NUMERALS[ones] if ones else 0)


def law_url(table: str) -> str:
    return LAW_API.format(TABLES[table].revision)


def sentences(element: ET.Element) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for child in element:
        if re.fullmatch(r"Item|Subitem\d+", child.tag):
            sentence = child.find(f"{child.tag}Sentence")
            depth = 0 if child.tag == "Item" else int(child.tag.removeprefix("Subitem"))
            rows.append((depth, "".join(sentence.itertext()).strip() if sentence is not None else ""))
            rows.extend(sentences(child))
        elif child.tag == "TableRow":
            cells = ["".join(column.itertext()).strip() for column in child.findall("TableColumn")]
            filled = [(index, text) for index, text in enumerate(cells) if text]
            if len(filled) != 1:
                raise ValueError(f"table row with {len(filled)} filled cells: {cells}")
            index, text = filled[0]
            rows.append((0 if len(cells) == 1 else index - 1, text))
        elif child.tag in ("TableStruct", "Table"):
            rows.extend(sentences(child))
    return rows


def annex(table: str) -> list[tuple[str, int, tuple[str, ...]]]:
    root = ET.parse(raw.cached(SOURCE, law_url(table)).path).getroot()
    appendix = next(
        element for element in root.iter("AppdxTable") if element.findtext("AppdxTableTitle", "").strip() == "別表第一"
    )
    prefectures = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
    entries: list[tuple[str, int, tuple[str, ...]]] = []
    prefecture = ""
    district = 0
    path: list[str] = []
    for depth, text in sentences(appendix):
        if text in prefectures:
            prefecture, district, path = prefectures[text], 0, []
        elif found := re.fullmatch(r"第([一二三四五六七八九十]+)区", text):
            district, path = kanji(found[1]), []
        elif text:
            path = [*path[:depth], text]
            entries.append((prefecture, district, tuple(path)))
    return entries


def fetch() -> None:
    for table in TABLES:
        raw.fetch(SOURCE, law_url(table), suffix=".xml")
    for prefecture in [f"{number:02d}" for number in range(1, 48)]:
        for year in sorted({spec.census for spec in TABLES.values()}):
            raw.fetch(SOURCE, estat_url(year, prefecture), suffix=".zip")
        for version in sorted({spec.isj for spec in TABLES.values()}):
            raw.fetch(SOURCE, ISJ.format(version=version, prefecture=prefecture), suffix=".zip")
    for prefecture in sorted({row["prefecture"] for row in rows() if row["kind"] == "former"}):
        raw.fetch(SOURCE, FORMER.format(prefecture=prefecture), suffix=".zip")


def estat_url(year: int, prefecture: str) -> str:
    return ESTAT.format(year=year, prefecture=int(prefecture))


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for old, new in (("ヶ", "ケ"), ("ヵ", "カ"), ("之", "ノ"), ("桧", "檜"), ("舘", "館"), ("冨", "富")):
        text = text.replace(old, new)
    return re.sub(r"^(大字|字)", "", text.strip())


def items(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    depth = 0
    current = ""
    for char in text + "、":
        depth += {"（": 1, "）": -1}.get(char, 0)
        if char == "、" and depth == 0:
            name, _, note = current.partition("（")
            for part in name.split("及び"):
                if part:
                    found.append((part, note.rstrip("）") if part == name.split("及び")[-1] else ""))
            current = ""
        else:
            current += char
    return found


def fields(record: shapefile._Record | None) -> dict[str, str]:
    return {key: str(value or "").strip() for key, value in (record.as_dict() if record is not None else {}).items()}


@dataclass
class Municipality:
    code: str
    parent: str
    name: str
    subprefecture: str
    county: str
    city: bool


def municipalities(vintage: str) -> dict[str, Municipality]:
    frame = pl.read_parquet(mlit.PARSED / f"n03_{vintage}.parquet", columns=["layer", "code", "parent", "name"])
    attributes: dict[str, tuple[str, str]] = {}
    for url in mlit.urls(vintage):
        with zipfile.ZipFile(raw.cached(mlit.SOURCE, url).path) as archive:
            dbf = next(name for name in archive.namelist() if name.endswith(".dbf"))
            reader = shapefile.Reader(dbf=io.BytesIO(archive.read(dbf)), encoding="cp932")
            for record in reader.iterRecords():
                row = fields(record)
                attributes[row["N03_007"]] = (row["N03_002"], row["N03_003"])
    found: dict[str, Municipality] = {}
    for layer, code, parent, name in frame.filter(pl.col("layer").is_in(["municipality", "ward"])).iter_rows():
        subprefecture, county = attributes.get(code, ("", ""))
        found[code] = Municipality(code, parent, name, subprefecture, county, layer == "ward" or name[-1] in "市区")
    return found


def shapes(vintage: str) -> dict[str, shapely.Geometry]:
    frame = pl.read_parquet(mlit.PARSED / f"n03_{vintage}.parquet").filter(
        pl.col("layer").is_in(["municipality", "ward", "prefecture"])
    )
    return {
        ("P" + code if layer == "prefecture" else code): shapely.from_wkb(geom)
        for layer, code, geom in frame.select("layer", "code", "geom").iter_rows()
    }


def small_areas(year: int, prefecture: str) -> pl.DataFrame:
    rows: list[tuple[str, str, bytes]] = []
    with zipfile.ZipFile(raw.cached(SOURCE, estat_url(year, prefecture)).path) as archive:
        stem = next(name for name in archive.namelist() if name.endswith(".shp")).removesuffix(".shp")
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{stem}.shp")),
            shx=io.BytesIO(archive.read(f"{stem}.shx")),
            dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
            encoding="cp932",
        )
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            row = fields(record)
            if shape is None or row["HCODE"] != "8101" or not row["S_NAME"]:
                continue
            geometry = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
            rows.append((row["PREF"] + row["CITY"], row["S_NAME"], shapely.to_wkb(geometry)))
    return pl.DataFrame(rows, schema={"unit": pl.String, "name": pl.String, "geom": pl.Binary}, orient="row")


def rows() -> list[dict[str, str]]:
    with PARTS.open(encoding="utf-8") as file:
        return list(csv.DictReader(file, delimiter="\t"))


def parts(table: str) -> dict[tuple[str, str], list[dict[str, str]]]:
    found: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows():
        if row["table"] == table:
            found.setdefault((row["prefecture"], row["unit"]), []).append(row)
    return found


def points(version: str, prefecture: str) -> pl.DataFrame:
    with zipfile.ZipFile(raw.cached(SOURCE, ISJ.format(version=version, prefecture=prefecture)).path) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".csv"))
        frame = pl.read_csv(archive.read(name).decode("cp932").encode(), infer_schema=False)
    return frame.select(
        pl.col("市区町村コード").alias("unit"),
        pl.col("大字町丁目名").alias("name"),
        pl.col("経度").cast(pl.Float64).alias("x"),
        pl.col("緯度").cast(pl.Float64).alias("y"),
    )


def former(prefecture: str) -> dict[str, shapely.Geometry]:
    found: dict[str, list[shapely.Geometry]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, FORMER.format(prefecture=prefecture)).path) as archive:
        stem = next(name for name in archive.namelist() if name.endswith(".shp")).removesuffix(".shp")
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{stem}.shp")),
            shx=io.BytesIO(archive.read(f"{stem}.shx")),
            dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
            encoding="cp932",
        )
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            if shape is not None and shape.shapeType != shapefile.NULL:
                row = fields(record)
                name = row["N03_004"] or row["N03_003"]
                found.setdefault(norm(name), []).append(shapely.geometry.shape(shape.__geo_interface__))
    return {name: shapely.union_all(shapely.make_valid(parts_)) for name, parts_ in found.items()}


def leaves(entries: list[tuple[str, int, tuple[str, ...]]]) -> list[tuple[str, int, tuple[str, ...]]]:
    return [
        entry
        for index, entry in enumerate(entries)
        if not entry[2][0].startswith("この表中")
        and not (index + 1 < len(entries) and entries[index + 1][2][: len(entry[2])] == entry[2])
    ]


def plan(
    table: str, towns: dict[str, Municipality]
) -> tuple[dict[tuple[str, int], list[str]], dict[str, list[tuple[int, str]]]]:
    natives = {f"{number:02d}": native for number, (native, _) in enumerate(PREFECTURES, 1)}
    wholes: dict[tuple[str, int], list[str]] = {}
    splits: dict[str, list[tuple[int, str]]] = {}
    for prefecture, district, path in leaves(annex(table)):
        local = [town for town in towns.values() if town.code[:2] == prefecture]
        root = path[0]

        def one(found: list[Municipality], label: str = root) -> Municipality:
            if len(found) != 1:
                raise ValueError(f"{table} {prefecture} {district} {path}: {label} matches {len(found)}")
            return found[0]

        codes: list[str] = []
        if root.endswith("管内"):
            office = root.removeprefix(natives[prefecture]).removesuffix("管内")
            codes = [
                town.code
                for town in local
                if norm(office) in (norm(town.subprefecture), norm(town.county)) and not town.city
            ]
            if not codes or len(path) > 1:
                raise ValueError(f"{table} {prefecture} {district} {path}: subprefecture")
        elif root.endswith("郡"):
            members = [town for town in local if norm(town.county) == norm(root)]
            if len(path) > 2:
                raise ValueError(f"{table} {prefecture} {district} {path}: split town")
            codes = (
                [one([town for town in members if norm(town.name) == norm(path[1])], path[1]).code]
                if len(path) > 1
                else [town.code for town in members]
            )
        else:
            town = one([town for town in local if norm(town.name) == norm(root) and town.parent == prefecture])
            if town.code in DESIGNATED:
                wards = [ward for ward in local if ward.parent == town.code]
                if len(path) == 1:
                    codes = [ward.code for ward in wards]
                else:
                    ward = one([ward for ward in wards if norm(ward.name) == norm(path[1])], path[1])
                    if len(path) == 2:
                        codes = [ward.code]
                    else:
                        splits.setdefault(ward.code, []).append((district, path[-1]))
            elif len(path) == 1:
                codes = [town.code]
            else:
                splits.setdefault(town.code, []).append((district, path[-1]))
        wholes.setdefault((prefecture, district), []).extend(codes)
    return wholes, splits


def assign(
    table: str,
    unit: Municipality,
    described: list[tuple[int, str]],
    areas: pl.DataFrame,
    located: pl.DataFrame,
    olds: dict[str, shapely.Geometry],
) -> tuple[list[int], int, list[str]]:
    curated = parts(table).get((unit.code[:2], unit.name), [])
    lines: list[tuple[int, str, str]] = (
        [(int(row["district"]), row["kind"], row["names"]) for row in curated]
        if curated
        else [
            (district, "rest" if text.endswith("に属しない区域") else "office" if "管内" in text else "chome", text)
            for district, text in described
        ]
    )
    rest = [district for district, kind, _ in lines if kind == "rest"]
    if len(rest) != 1:
        raise ValueError(f"{table} {unit.code} {unit.name}: {len(rest)} remainders")
    remainder = rest[0]
    notes: list[str] = []
    listed: dict[str, int] = {}
    shapes_: list[tuple[shapely.Geometry, int]] = []
    for district, kind, text in lines:
        if kind == "former":
            for name, _ in items(text):
                if norm(name) not in olds:
                    raise ValueError(f"{table} {unit.code} {unit.name}: no former municipality {name}")
                shapes_.append((olds[norm(name)], district))
        elif kind == "chome":
            for name, note in items(text):
                if "限る" in note:
                    notes.append(f"{unit.name} {name}（{note}）")
                elif listed.setdefault(norm(name), district) != district:
                    raise ValueError(f"{table} {unit.code} {unit.name}: {name} in {listed[norm(name)]} and {district}")
        elif kind != "rest":
            raise ValueError(f"{table} {unit.code} {unit.name} {district}: unresolved {text[:60]}")
    polygons = shapely.from_wkb(areas["geom"].to_numpy())
    tree = shapely.STRtree(polygons)
    votes: dict[int, list[int]] = {}
    found: set[str] = set()
    for (name,), point in zip(
        located.select("name").iter_rows(),
        shapely.points(located["x"].to_numpy(), located["y"].to_numpy()),
        strict=True,
    ):
        for index in tree.query(point, predicate="within"):
            votes.setdefault(int(index), []).append(listed.get(norm(name), remainder))
            if norm(name) in listed:
                found.add(norm(name))
    chosen: list[int] = []
    for index, (name, polygon) in enumerate(zip(areas["name"], polygons, strict=True)):
        key = norm(name)
        inside = polygon.representative_point()
        prefix = [other for other in listed if key.startswith(other) and not SUFFIX.match(key[len(other) :])]
        if district := next((district for shape, district in shapes_ if shape.contains(inside)), None):
            chosen.append(district)
        elif key in listed:
            chosen.append(listed[key])
            found.add(key)
        elif prefix:
            chosen.append(listed[max(prefix, key=len)])
            found.add(max(prefix, key=len))
        elif index in votes:
            chosen.append(max(set(votes[index]), key=votes[index].count))
        else:
            chosen.append(remainder)
    missing = sorted(set(listed) - found)
    if missing:
        notes.append(f"{unit.name}: {len(missing)} listed names located nowhere: {'、'.join(missing)}")
    return chosen, remainder, notes


def build(table: str) -> pl.DataFrame:
    spec = TABLES[table]
    towns = municipalities(spec.n03)
    geometry = shapes(spec.n03)
    wholes, splits = plan(table, towns)
    listed = [code for codes in wholes.values() for code in codes] + list(splits)
    expected = [code for code, town in towns.items() if code not in DESIGNATED]
    if sorted(listed) != sorted(expected):
        missing = sorted(set(expected) - set(listed))
        doubled = sorted({code for code in listed if listed.count(code) > 1})
        extra = sorted(set(listed) - set(expected))
        raise ValueError(f"{table}: missing {missing}, doubled {doubled}, extra {extra}")
    members: dict[tuple[str, int], list[shapely.Geometry]] = {
        key: [geometry[code] for code in codes] for key, codes in wholes.items()
    }
    described: dict[tuple[str, int], list[str]] = {
        key: [towns[code].name for code in codes] for key, codes in wholes.items()
    }
    notes: list[str] = []
    errors: list[str] = []
    areas: dict[str, pl.DataFrame] = {}
    located: dict[str, pl.DataFrame] = {}
    olds: dict[str, dict[str, shapely.Geometry]] = {}
    curated = {row["prefecture"] for row in rows() if row["table"] == table and row["kind"] == "former"}
    for code, lines in splits.items():
        prefecture = code[:2]
        if prefecture not in areas:
            areas[prefecture] = small_areas(spec.census, prefecture)
            located[prefecture] = points(spec.isj, prefecture)
            olds[prefecture] = former(prefecture) if prefecture in curated else {}
        local = areas[prefecture].filter(pl.col("unit") == code)
        try:
            chosen, remainder, unit_notes = assign(
                table,
                towns[code],
                lines,
                local,
                located[prefecture].filter(pl.col("unit") == code),
                olds[prefecture],
            )
        except ValueError as error:
            errors.append(str(error))
            continue
        notes += unit_notes
        whole = geometry[code]
        polygons = shapely.from_wkb(local["geom"].to_numpy())
        taken: list[shapely.Geometry] = []
        for district in sorted(set(chosen) - {remainder}):
            indices = [index for index, other in enumerate(chosen) if other == district]
            piece = shapely.intersection(
                whole, shapely.simplify(shapely.union_all(polygons[indices], grid_size=1e-7), mlit.TOLERANCE)
            )
            taken.append(piece)
            members.setdefault((prefecture, district), []).append(piece)
        rest = shapely.difference(whole, shapely.union_all(taken)) if taken else whole
        members.setdefault((prefecture, remainder), []).append(rest)
        for district in sorted({*chosen, remainder}):
            described.setdefault((prefecture, district), []).append(f"{towns[code].name} (part)")
    for note in notes:
        print(f"{table}: {note}")
    if errors:
        raise ValueError("\n".join(errors))
    records: list[tuple[str, str, int, str, bytes]] = []
    for prefecture in sorted({key[0] for key in members}):
        districts = sorted(district for key, district in members if key == prefecture)
        if districts != list(range(1, len(districts) + 1)):
            raise ValueError(f"{table} {prefecture}: districts {districts}")
        shaped = {district: mlit.polygons(mlit.merge(members[prefecture, district])) for district in districts}
        total = sum(shapely.area(shape) for shape in shaped.values())
        whole = shapely.area(geometry["P" + prefecture])
        if abs(total - whole) > 1e-3 * whole:
            raise ValueError(f"{table} {prefecture}: districts cover {total}, prefecture {whole}")
        for district, shape in shaped.items():
            records.append(
                (
                    f"{prefecture}{district:02d}",
                    prefecture,
                    district,
                    "、".join(described[prefecture, district]),
                    shapely.to_wkb(shape),
                )
            )
    return pl.DataFrame(
        records,
        schema={
            "code": pl.String,
            "prefecture": pl.String,
            "district": pl.Int64,
            "members": pl.String,
            "geom": pl.Binary,
        },
        orient="row",
    ).with_columns(pl.lit(table).alias("table"))


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        frame = build(table)
        print(table, frame.height, "districts")
        frame.write_parquet(PARSED / f"districts_{table}.parquet")


def ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Representatives single-member districts of Japan from the Public Offices Election Act "
            "annex 1 (2022 table), drawn from N03 administrative areas and census small areas",
            polity="JP",
        )
        law = cs.source(mlit.LAW_PUBLISHER, LAW_TITLE)
        cs.add(
            "source",
            id=law,
            kind="official_body",
            publisher=mlit.LAW_PUBLISHER,
            title=LAW_TITLE,
            url=mlit.LAW,
            license="Public domain (Copyright Act art. 13)",
            license_url="https://laws.e-gov.go.jp/law/345AC0000000048#Mp-At_13",
            attribution=CREDIT,
        )
        for table in TABLES:
            cs.artifact(raw.cached(SOURCE, law_url(table)), law)
        order = sorted(TABLES, key=lambda table: TABLES[table].effective)
        districts = pl.concat([pl.read_parquet(PARSED / f"districts_{table}.parquet") for table in order])
        used = sorted(set(districts.filter(pl.col("members").str.contains("(part)", literal=True))["prefecture"]))
        for publisher, title, url, license_, license_url, attribution, links in (
            (
                "Statistics Bureau of Japan",
                "Census small-area boundaries (町丁・字等), 2020",
                "https://www.e-stat.go.jp/gis/statmap-search?page=1&type=2&aggregateUnitForBoundary=A",
                "Government of Japan Standard Terms of Use 2.0 (compatible with CC BY 4.0)",
                "https://www.e-stat.go.jp/terms-of-use",
                "出典：政府統計の総合窓口(e-Stat)「国勢調査 小地域（町丁・字等）境界データ」を加工して作成",
                [estat_url(TABLES[table].census, prefecture) for table in TABLES for prefecture in used],
            ),
            (
                mlit.PUBLISHER,
                "Location reference information, oaza and chome level (大字・町丁目レベル位置参照情報)",
                "https://nlftp.mlit.go.jp/isj/",
                "MLIT location reference information terms (free use with credit)",
                "https://nlftp.mlit.go.jp/isj/agreement.html",
                "「大字・町丁目レベル位置参照情報」（国土交通省）を加工して作成",
                [
                    ISJ.format(version=TABLES[table].isj, prefecture=prefecture)
                    for table in TABLES
                    for prefecture in used
                ],
            ),
            (
                mlit.PUBLISHER,
                "National Land Numerical Information, administrative areas (N03), 2000",
                "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2025.html",
                "CC BY 4.0",
                "https://creativecommons.org/licenses/by/4.0/",
                "「国土数値情報（行政区域データ）」（国土交通省）を加工して作成",
                [
                    FORMER.format(prefecture=prefecture)
                    for prefecture in sorted({row["prefecture"] for row in rows() if row["kind"] == "former"})
                ],
            ),
        ):
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=license_,
                license_url=license_url,
                attribution=attribution,
            )
            for link in sorted(set(links)):
                cs.artifact(raw.cached(SOURCE, link), source)
        japan = cs.polity("JP")
        cs.add(
            "scheme",
            code="jp.hor_smd",
            polity_id=japan,
            kind="electoral_district",
            rank=2,
            noun="single-member district",
        )
        for (code,), group in districts.group_by(["code"], maintain_order=True):
            rows_ = group.sort(pl.col("table").replace_strict({table: index for index, table in enumerate(order)}))
            prefecture, district = rows_["prefecture"][0], rows_["district"][0]
            first = TABLES[rows_["table"][0]].effective
            last = rows_["table"][-1]
            following = order.index(last) + 1
            end = TABLES[order[following]].effective if following < len(order) else None
            area = cs.area("jp.hor_smd", code)
            span = {"valid_from": first, "valid_from_precision": "day", "valid_to": end, "valid_to_precision": "day"}
            cs.add("area", id=area, scheme_code="jp.hor_smd")
            cs.add("area_code", area_id=area, code=code, **span)
            cs.add("area_parent", area_id=area, parent_id=cs.area("jp.prefecture", prefecture), **span)
            native, english = PREFECTURES[int(prefecture) - 1]
            for text, lang, kind in (
                (f"{native}第{district}区", "ja", "official"),
                (f"{english} {ordinal(district)} district", "en", "english"),
            ):
                cs.add(
                    "name",
                    entity_id=area,
                    text=text,
                    lang=lang,
                    kind=kind,
                    is_preferred=kind == "official",
                    source_id=law,
                    **span,
                )
            for index, (table, wkb) in enumerate(rows_.select("table", "geom").iter_rows()):
                shape = shapely.set_srid(shapely.from_wkb(wkb), 4326)
                ends = TABLES[rows_["table"][index + 1]].effective if index + 1 < rows_.height else end
                cs.add(
                    "area_geometry",
                    area_id=area,
                    geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                    label_point=shapely.to_wkb(
                        shapely.set_srid(max(shapely.get_parts(shape), key=shapely.area).representative_point(), 4326),
                        hex=True,
                        include_srid=True,
                    ),
                    quality="official",
                    source_id=law,
                    valid_from=TABLES[table].effective,
                    valid_from_precision="day",
                    valid_to=ends,
                    valid_to_precision="day",
                )
        for index, table in enumerate(order):
            start = TABLES[table].effective
            cs.add(
                "boundary_set",
                scheme_code="jp.hor_smd",
                vintage=start.isoformat(),
                valid_from=start,
                valid_to=TABLES[order[index + 1]].effective if index + 1 < len(order) else None,
                as_of=start,
            )
        return cs.write()
