import functools
import io
import re
import zipfile
from collections import Counter
from datetime import date, timedelta
from uuid import uuid5
from xml.etree import ElementTree

import httpx
import numpy
import polars as pl
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "cz.volby"
BASE = "https://volby.gov.cz"
RUIAN = "https://services.cuzk.gov.cz/shp/stat/epsg-5514/1.zip"
CZECHIA = "ddcd9ce1-9a7c-5226-96a7-a80d2ad155c6"
TERRITORY = "73017ae9-d08b-5393-ad66-71a4653290e7"
ABROAD = "999997"
PRAGUE = "554782"
PRAGUE_REGIONS = {"1100", "3100"}
NO_PARTY = {"99"}
TOLERANCE = 10.0
ACCOUNTING = ((2, "VOL_SEZNAM"), (14, "VYD_OBALKY"), (3, "ODEVZ_OBAL"), (4, "PL_HL_CELK"))
DECLARED_ACCOUNTING = ((2, "ZAPSANI_VOLICI"), (14, "VYDANE_OBALKY"), (3, "ODEVZDANE_OBALKY"), (4, "PLATNE_HLASY"))
CHAMBER_METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as published in the volby.cz open data. "
    "Registered voters are the electoral roll, voters are the official envelopes issued, ballots cast are the "
    "envelopes put in the box, and valid votes are the valid list votes. The source also holds each candidate's "
    "preference votes per polling district; they are not stored here yet. Polling districts in Prague and in the "
    "statutory cities are placed in the whole municipality, not in its city district. Polling districts abroad count "
    "toward the region the source assigns them to."
)
PRESIDENT_METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as published in the volby.cz open data. "
    "Registered voters are the electoral roll, voters are the official envelopes issued, ballots cast are the "
    "envelopes put in the box, and valid votes are the valid votes for candidates."
)

CHAMBER = {
    2002: ("ps2002", "PS2002_data_20230224_csv.zip", "PS2002_reg_20230428_csv.zip", "PS2002_cisel_20230224_csv.zip"),
    2006: ("ps2006", "PS2006_data_20230224_csv.zip", "PS2006_reg_20230428_csv.zip", "PS2006_cisel_20230224_csv.zip"),
    2010: ("ps2010", "PS2010_data_20230224_csv.zip", "PS2010_reg_20230428_csv.zip", "PS2010_cisel_20230224_csv.zip"),
    2013: ("ps2013", "PS2013_data_20230224_csv.zip", "PS2013_reg_20230428_csv.zip", "PS2013_cisel_20230224_csv.zip"),
    2017: ("ps2017nss", "PS2017data20171122_csv.zip", "PS2017reg20171122_csv.zip", "PS2017ciselniky20170905_csv.zip"),
    2021: ("ps2021", "PS2021_data_20211010_csv.zip", "PS2021reg20211111_csv.zip", "PS2021ciselniky20211006_csv.zip"),
    2025: ("ps2025", "PS2025data20251005_csv.zip", "PS2025reg20251005_csv.zip", "PS2025ciselniky20251005_csv.zip"),
}
CHAMBER_DAYS = {
    2002: (date(2002, 6, 14), date(2002, 6, 15)),
    2006: (date(2006, 6, 2), date(2006, 6, 3)),
    2010: (date(2010, 5, 28), date(2010, 5, 29)),
    2013: (date(2013, 10, 25), date(2013, 10, 26)),
    2017: (date(2017, 10, 20), date(2017, 10, 21)),
    2021: (date(2021, 10, 8), date(2021, 10, 9)),
    2025: (date(2025, 10, 3), date(2025, 10, 4)),
}
PRESIDENT = {
    2013: (
        "prez2013",
        "PREZ2013data20130126_csv.zip",
        "PREZ2013reg20130126_csv.zip",
        "PREZ2013ciselniky20130126_csv.zip",
    ),
    2018: (
        "prez2018",
        "PREZ2018data20180127_csv_kolo2.zip",
        "PREZ2018reg20180127_csv.zip",
        "PREZ2018ciselniky20171215_csv.zip",
    ),
    2023: (
        "prez2023nss",
        "PREZ2023_data_20230222_csv_NSS.zip",
        "PREZ2023reg20230222_csv_NSS.zip",
        "PREZ2023ciselniky20230109_csv.zip",
    ),
}
PRESIDENT_DAYS = {
    2013: ((date(2013, 1, 11), date(2013, 1, 12)), (date(2013, 1, 25), date(2013, 1, 26))),
    2018: ((date(2018, 1, 12), date(2018, 1, 13)), (date(2018, 1, 26), date(2018, 1, 27))),
    2023: ((date(2023, 1, 13), date(2023, 1, 14)), (date(2023, 1, 27), date(2023, 1, 28))),
}
SENATE = {
    "senat_vse": ("SENATdata20250118_csv.zip", "SENATreg20250118_csv.zip", "SENATciselniky20250118_csv.zip"),
    "se2026": ("SE2026data20261017_csv.zip", "SE2026reg20261017_csv.zip", "SE2026ciselniky20261017_csv.zip"),
}
SENATE_STAMPS = {"se2026": "20261009"}
SENATE_MAP_FROM = "20140101"
SENATE_METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as published in the volby.cz open data. "
    "Registered voters are the electoral roll, voters are the official envelopes issued, ballots cast are the "
    "envelopes put in the box, and valid votes are the valid votes for candidates. The open data dates only the "
    "first round; the second round is dated one week later, the interval the Senate election law sets. District "
    "names are the current ones, and district boundaries have been redrawn since 1996."
)


def url(folder: str, name: str) -> str:
    return f"{BASE}/opendata/{folder}/{name}"


def declared_url(folder: str) -> str:
    return f"{BASE}/pls/{folder.removesuffix('nss')}/vysledky"


def fetch() -> None:
    raw.fetch("cz.cuzk", RUIAN, suffix=".zip")
    for files in [*CHAMBER.values(), *PRESIDENT.values()]:
        folder, *names = files
        for name in names:
            raw.fetch(SOURCE, url(folder, name), suffix=".zip")
        try:
            raw.fetch(SOURCE, declared_url(folder), suffix=".xml")
        except httpx.HTTPStatusError as failure:
            print(f"{declared_url(folder)}: {failure.response.status_code}", flush=True)
    for folder, names in SENATE.items():
        for name in names:
            raw.fetch(SOURCE, url(folder, name), suffix=".zip")


def table(folder: str, name: str, member: str) -> pl.DataFrame:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url(folder, name)).path)
    found = sorted(
        (n for n in archive.namelist() if re.fullmatch(rf"(?:.*/)?{member}\.csv", n, re.IGNORECASE)),
        key=lambda n: ("csv_od" not in n.lower(), n),
    )
    if not found:
        raise LookupError(f"{name} has no {member}.csv: {archive.namelist()}")
    data = archive.read(found[0])
    utf8 = data.startswith(b"\xef\xbb\xbf") or _utf8(data)
    text = data.decode("utf-8-sig" if utf8 else "cp1250")
    separator = ";" if text.split("\n", 1)[0].count(";") > text.split("\n", 1)[0].count(",") else ","
    return pl.read_csv(io.StringIO(text), separator=separator, infer_schema_length=0)


def _utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def packages() -> list[str]:
    return [
        "cz.volby/geo",
        "cz.volby/registry",
        *(f"cz.volby/ps-{year}" for year in CHAMBER),
        *(f"cz.volby/prez-{year}" for year in PRESIDENT),
        *(f"cz.volby/se-{_day(day)}" for day in _senate_days()),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for year in CHAMBER:
        chamber(year).write()
    for year in PRESIDENT:
        president(year).write()
    for day in _senate_days():
        senate(day).write()


def emit() -> None:
    package.run("load", packages())


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def _shapes(name: str) -> tuple[list[dict[str, object]], list[shapely.Geometry]]:
    archive = zipfile.ZipFile(raw.cached("cz.cuzk", RUIAN).path)
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"1/{name}.shp")),
        shx=io.BytesIO(archive.read(f"1/{name}.shx")),
        dbf=io.BytesIO(archive.read(f"1/{name}.dbf")),
        encoding="cp1250",
    )
    records: list[dict[str, object]] = [dict(record.as_dict()) for record in reader.records() if record is not None]
    shapes = [shapely.geometry.shape(shape.__geo_interface__) for shape in reader.shapes() if shape is not None]
    return records, shapes


def geo() -> Package:
    p = Package(
        "cz.volby/geo",
        SOURCE,
        "Czechia's 14 regions, 77 districts, 6,258 municipalities, and current polling districts from ČÚZK's RÚIAN "
        "statistical boundaries",
    )
    p.external |= {CZECHIA, TERRITORY}
    artifact = raw.cached("cz.cuzk", RUIAN)
    p.add(
        "source",
        id=RUIAN_SOURCE,
        kind="official_body",
        publisher="Český úřad zeměměřický a katastrální",
        title="RÚIAN statistical units (Shapefile, S-JTSK)",
        url="https://services.cuzk.gov.cz/shp/stat/epsg-5514/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© ČÚZK, RÚIAN",
    )
    p.artifact(artifact, RUIAN_SOURCE)
    for code, rank, kind, noun, system in (
        ("cz.kraj", 1, "administrative", "region", "nuts"),
        ("cz.okres", 2, "administrative", "district", "lau"),
        ("cz.obec", 3, "administrative", "municipality", "ruian"),
        ("cz.mestska_cast", 4, "administrative", "city district", "ruian"),
        ("cz.okrsek", 5, "precinct", "polling district", None),
    ):
        p.add("scheme", code=code, polity_id=CZECHIA, kind=kind, rank=rank, noun=noun, code_system=system)
        p.add(
            "boundary_set",
            id=key("boundary_set", code, artifact.retrieved_at.year),
            scheme_code=code,
            vintage=str(artifact.retrieved_at.year),
            as_of=artifact.retrieved_at.date(),
        )
    p.uses(raw.cached(SOURCE, url(*_latest_councils())))
    towns, simple = _obce()

    def place(scheme: str, code: str, text: str, parent: str, shape: shapely.Geometry) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "cs", "official")
        _outline(p, id, shape)

    regions, _ = _shapes("VUSC_P")
    districts, _ = _shapes("OKRESY_P")
    nuts = {str(row["KOD"]): str(row["NUTS3_KOD"]) for row in regions}
    lau = {str(row["KOD"]): str(row["LAU1_KOD"]) for row in districts}
    for row in regions:
        members = [shape for town, shape in zip(towns, simple, strict=True) if town["VUSC_KOD"] == row["KOD"]]
        place("cz.kraj", str(row["NUTS3_KOD"]), str(row["NAZEV"]), TERRITORY, shapely.coverage_union_all(members))
    for row in districts:
        members = [shape for town, shape in zip(towns, simple, strict=True) if town["OKRES_KOD"] == row["KOD"]]
        place(
            "cz.okres",
            str(row["LAU1_KOD"]),
            str(row["NAZEV"]),
            area("cz.kraj", nuts[str(row["VUSC_KOD"])]),
            shapely.coverage_union_all(members),
        )
    for town, shape in zip(towns, simple, strict=True):
        place("cz.obec", str(town["KOD"]), str(town["NAZEV"]), area("cz.okres", lau[str(town["OKRES_KOD"])]), shape)
    names = {str(town["KOD"]): str(town["NAZEV"]) for town in towns}
    precincts = list(zip(*_okrsky(), strict=True))
    wards = _city_districts()
    for code, text in sorted(wards.items()):
        members = [shape for row, shape in precincts if str(row["MOMC_KOD"]) == code]
        parent = {str(row["OBEC_KOD"]) for row, _ in precincts if str(row["MOMC_KOD"]) == code}
        place("cz.mestska_cast", code, text, area("cz.obec", parent.pop()), shapely.union_all(members))
    for row, shape in precincts:
        ward = str(row["MOMC_KOD"])
        place(
            "cz.okrsek",
            f"{row['OBEC_KOD']}:{row['CISLO']}",
            f"{names[str(row['OBEC_KOD'])]}, okrsek {row['CISLO']}",
            area("cz.mestska_cast", ward) if ward in wards else area("cz.obec", str(row["OBEC_KOD"])),
            shape,
        )
    return p


def _latest_councils() -> tuple[str, str]:
    """The folder and code-list file of the newest municipal election's open data."""
    from ea.sources.cz import volby_kv  # volby_kv builds on this module

    folder, _, _, cis = volby_kv.MUNICIPAL[max(volby_kv.MUNICIPAL)]
    return folder, cis


def _city_districts() -> dict[str, str]:
    """The city districts and city precincts that have a council, by RÚIAN code, with the name the open data gives.

    RÚIAN draws a district only as the group of polling districts that carry its code, and the open data's council
    list names the councils, so a district without a council has no name here and its polling districts stay in the
    city.
    """
    folder, cis = _latest_councils()
    codes = {str(row["MOMC_KOD"]) for row in _okrsky()[0] if row["MOMC_KOD"]}
    return {
        row["KODZASTUP"]: clean(row["NAZEVZAST"])
        for row in table(folder, cis, "kvcoco").iter_rows(named=True)
        if row["KODZASTUP"] in codes
    }


def _wgs(shape: shapely.Geometry) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(5514, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = _wgs(shape)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=RUIAN_SOURCE,
    )


@functools.cache
def _obce() -> tuple[list[dict[str, object]], list[shapely.Geometry]]:
    rows, outlines = _shapes("OBCE_P")
    return rows, list(shapely.make_valid(shapely.coverage_simplify(numpy.array(outlines), TOLERANCE)))


@functools.cache
def _okrsky() -> tuple[list[dict[str, object]], list[shapely.Geometry]]:
    rows, outlines = _shapes("VO_P")
    return rows, list(shapely.make_valid(shapely.coverage_simplify(numpy.array(outlines), TOLERANCE)))


def _senate_districts() -> dict[str, list[shapely.Geometry]]:
    ranges: dict[str, list[tuple[str, str, list[tuple[int, int]]]]] = {}
    coco = _senate_table("cis", "secoco")
    for row in coco.filter(pl.col("DATUMVOLEB") >= SENATE_MAP_FROM).iter_rows(named=True):
        spans = [(row[f"MINOKRSEK{i}"], row[f"MAXOKRSEK{i}"]) for i in range(1, 10)]
        spans.append((row["MINOKRSE10"], row["MAXOKRSE10"]))
        held = [(int(low), int(high)) for low, high in spans if low and low != "0"]
        ranges.setdefault(row["OBEC"], []).append((row["DATUMVOLEB"], row["OBVOD"], held))
    members: dict[str, list[shapely.Geometry]] = {}
    for row, shape in zip(*_okrsky(), strict=True):
        options = ranges.get(str(row["MOMC_KOD"]) or str(row["OBEC_KOD"])) or ranges[str(row["OBEC_KOD"])]
        number = int(str(row["CISLO"]))
        held = [(day, obvod) for day, obvod, spans in options if not spans or any(a <= number <= b for a, b in spans)]
        if not held:
            newest = max(day for day, _, _ in options)
            (obvod,) = {obvod for day, obvod, _ in options if day == newest}
            held = [(newest, obvod)]
        members.setdefault(max(held)[1], []).append(shape)
    covered = {str(row["OBEC_KOD"]) for row in _okrsky()[0]}
    latest = {row["OBEC"]: row["OBVOD"] for row in coco.sort("DATUMVOLEB").iter_rows(named=True)}
    for row, shape in zip(*_obce(), strict=True):
        if str(row["KOD"]) not in covered:
            print(
                f"cz.senat: {row['KOD']} {row['NAZEV']} has no polling district, placed whole in {latest[str(row['KOD'])]}"
            )
            members.setdefault(latest[str(row["KOD"])], []).append(shape)
    return members


def registry() -> Package:
    p = Package(
        "cz.volby/registry",
        SOURCE,
        "Czech parties, the Chamber of Deputies, the presidency, and their electoral systems from volby.cz",
        depends=["cz.volby/geo"],
    )
    p.external |= {CZECHIA, TERRITORY}
    p.add(
        "source",
        id=VOLBY_SOURCE,
        kind="official_body",
        publisher="Český statistický úřad",
        title="Volby.cz open data",
        url="https://volby.gov.cz/opendata/opendata.htm",
        license="ČSÚ open data terms: free reuse with the source acknowledged",
        license_url="https://volby.gov.cz/opendata/opendata.htm",
        attribution="Český statistický úřad, volby.cz",
    )
    p.add(
        "source",
        id=COLORS_SOURCE,
        kind="community",
        publisher="electionsarchive.com",
        title="Brand colors of Czech parties, taken from their logos and websites",
        url="https://electionsarchive.com",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="electionsarchive.com",
    )
    for folder, *names in [*CHAMBER.values(), *PRESIDENT.values()]:
        for name in names:
            p.artifact(raw.cached(SOURCE, url(folder, name)), VOLBY_SOURCE)
        try:
            p.artifact(raw.cached(SOURCE, declared_url(folder)), VOLBY_SOURCE)
        except LookupError:
            pass
    for folder, names in SENATE.items():
        for name in names:
            p.artifact(raw.cached(SOURCE, url(folder, name)), VOLBY_SOURCE)
    p.add("body", id=CHAMBER_BODY, polity_id=CZECHIA, kind="lower_house")
    p.name(CHAMBER_BODY, "Chamber of Deputies", "en", "official")
    p.name(CHAMBER_BODY, "Poslanecká sněmovna", "cs", "native", False)
    p.add("body", id=SENATE_BODY, polity_id=CZECHIA, kind="upper_house")
    p.name(SENATE_BODY, "Senate", "en", "official")
    p.name(SENATE_BODY, "Senát", "cs", "native", False)
    p.add(
        "scheme",
        code="cz.senat",
        polity_id=CZECHIA,
        kind="electoral_district",
        rank=2,
        noun="Senate district",
        code_system=None,
    )
    for row in _senate_table("cis", "secobv").unique("OBVOD", keep="last").iter_rows(named=True):
        id = area("cz.senat", row["OBVOD"])
        p.add("area", id=id, scheme_code="cz.senat")
        p.add("area_code", area_id=id, code=row["OBVOD"])
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, clean(row["NAZEV_OBV"]), "cs", "official")
    artifact = raw.cached("cz.cuzk", RUIAN)
    p.add(
        "boundary_set",
        id=key("boundary_set", "cz.senat", artifact.retrieved_at.year),
        scheme_code="cz.senat",
        vintage=str(artifact.retrieved_at.year),
        as_of=artifact.retrieved_at.date(),
    )
    for obvod, shapes in sorted(_senate_districts().items(), key=lambda item: int(item[0])):
        _outline(p, area("cz.senat", obvod), shapely.union_all(shapes, grid_size=0.01))
    p.add("office", id=PRESIDENCY, polity_id=CZECHIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Czech Republic", "en", "official")
    p.name(PRESIDENCY, "Prezident republiky", "cs", "native", False)
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=CZECHIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    used: set[str] = set()
    for year, (folder, _, reg, cis) in sorted(CHAMBER.items()):
        lists = table(folder, reg, "psrkl")
        candidates = table(folder, reg, "psrk")
        used |= set(lists["VSTRANA"]) | set(candidates["PSTRANA"]) | set(candidates["NSTRANA"])
    senators = _senate_table("reg", "serk")
    used |= set(senators["VSTRANA"]) | set(senators["PSTRANA"]) | set(senators["NSTRANA"])
    named: dict[str, dict[str, str | None]] = {}
    for year, (folder, _, _, cis) in sorted(CHAMBER.items()):
        for row in table(folder, cis, "cvs").iter_rows(named=True):
            named[row["VSTRANA"]] = row
    for row in _senate_table("cis", "cvs").iter_rows(named=True):
        named.setdefault(row["VSTRANA"], row)
    for code in sorted(used - {None, *NO_PARTY}, key=int):
        row = named.get(code)
        if row is None or row.get("TYPVS") == "N":
            continue
        id = party(code)
        p.add("party", id=id, kind="bloc" if row.get("TYPVS") == "K" else "party", polity_id=CZECHIA)
        p.add("external_id", entity_id=id, scheme="cz.volby.party", value=code)
        p.name(id, clean(row["NAZEVCELK"]), "cs", "official")
        if row.get("ZKRATKAV30"):
            p.name(id, clean(row["ZKRATKAV30"]), "cs", "short", False)
        if row.get("ZKRATKAV8"):
            p.name(id, clean(row["ZKRATKAV8"]), "cs", "abbreviation", False)
        if code in COLORS:
            p.add(
                "party_color", party_id=id, color=COLORS[code], valid_from=None, valid_to=None, source_id=COLORS_SOURCE
            )
    return p


def party(code: str) -> str:
    return key("party", code)


COLORS_SOURCE = key("source", "cz.volby.party_colors")
COLORS = {
    "1": "#ffd500",
    "5": "#4cae3a",
    "7": "#ec5b00",
    "11": "#29348e",
    "13": "#2e86c1",
    "47": "#e2001a",
    "53": "#1e50a2",
    "81": "#f5a623",
    "102": "#f5a623",
    "137": "#2a9d8f",
    "144": "#1b9bd7",
    "166": "#00a651",
    "177": "#4cae3a",
    "270": "#f5c400",
    "720": "#000000",
    "721": "#6b2c91",
    "724": "#a6192e",
    "759": "#ec5b00",
    "768": "#252262",
    "792": "#6c8ebf",
    "1114": "#1f4e9c",
    "1178": "#0057b8",
    "1245": "#b5121b",
    "1298": "#c8102e",
    "1327": "#1b4f9c",
    "1350": "#1fa99a",
}


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def person_name(row: dict[str, str | None]) -> str:
    return clean(" ".join(part or "" for part in (row["TITULPRED"], row["JMENO"], row["PRIJMENI"], row["TITULZA"])))


def _parties() -> set[str]:
    path = package.folder("cz.volby/registry") / "build" / "party.parquet"
    return set(pl.read_parquet(path)["id"]) if path.exists() else set()


def _nuts(folder: str, cis: str) -> dict[str, str]:
    latest, _, _, current = CHAMBER[max(CHAMBER)]
    return {
        row["NUMNUTS"]: row["NUTS"]
        for codes in (table(folder, cis, "cnumnuts"), table(latest, current, "cnumnuts"))
        for row in codes.iter_rows(named=True)
    }


def _places() -> pl.DataFrame:
    build = package.folder("cz.volby/geo") / "build"
    return (
        pl.read_parquet(build / "area_code.parquet")
        .join(pl.read_parquet(build / "area.parquet"), left_on="area_id", right_on="id")
        .join(pl.read_parquet(build / "name.parquet"), left_on="area_id", right_on="entity_id")
        .select("scheme_code", "code", "text")
    )


def _units(
    p: Package,
    day: date,
    nuts: dict[str, str],
    coco: pl.DataFrame,
    data: pl.DataFrame,
) -> dict[str, str]:
    places_built = _places()
    towns = set(places_built.filter(pl.col("scheme_code") == "cz.obec")["code"])
    districts = set(places_built.filter(pl.col("scheme_code") == "cz.okres")["code"])
    places = {row["OBEC"]: row for row in coco.iter_rows(named=True)}
    units: dict[str, str] = {}
    for row in data.iter_rows(named=True):
        code = f"{day}:{row['OBEC']}:{row['OKRSEK']}"
        unit = key("unit", code)
        units[row["ID_OKRSKY"]] = unit
        town = places.get(row["OBEC"], {})
        okres = row.get("OKRES") or town.get("OKRES")
        if row["OBEC"] == ABROAD:
            relation, place, kind = "unlocated", None, "abroad_mission"
        elif row["OBEC"] in towns:
            relation, place, kind = "within", area("cz.obec", row["OBEC"]), "standard"
        elif town.get("OBEC_PREZ") in towns:
            relation, place, kind = "within", area("cz.obec", town["OBEC_PREZ"]), "standard"
        elif okres is not None and nuts.get(okres) in districts:
            relation, place, kind = "within", area("cz.okres", nuts[okres]), "standard"
        elif town.get("KRAJ") in PRAGUE_REGIONS:
            relation, place, kind = "within", area("cz.obec", PRAGUE), "standard"
        elif nuts.get(town.get("KRAJ") or "") is not None:
            relation, place, kind = "within", area("cz.kraj", nuts[town["KRAJ"]]), "standard"
        else:
            relation, place, kind = "within", TERRITORY, "standard"
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CZECHIA,
            scheme_code="cz.okrsek",
            code=code,
            area_relation=relation,
            area_id=place,
            kind=kind,
        )
    return units


def _number(value: str | None) -> int | None:
    return None if value in (None, "") else int(value)


def _accounting(p: Package, result_set: str, unit: str, row: dict[str, str | None]) -> None:
    for measure, column in ACCOUNTING:
        value = _number(row.get(column))
        if value is not None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )


def _overcount(p: Package, rows: list[tuple[str, dict[str, str | None]]]) -> None:
    over: Counter[str] = Counter()
    for result_set, row in rows:
        valid, cast = _number(row.get("PL_HL_CELK")), _number(row.get("ODEVZ_OBAL"))
        if valid is not None and cast is not None and valid > cast:
            over[result_set] += 1
    if not over:
        return
    p.accept(
        "accounting.ballots_sum",
        f"volby.cz prints more valid votes than envelopes in the box in {over.total()} polling districts; "
        "stored as printed",
    )
    for result_set, n in over.items():
        p.add(
            "note",
            id=key("note", result_set, "overcount"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {n} polling district{'s' if n > 1 else ''}, volby.cz prints more valid votes than envelopes "
            "put in the box. The figures are stored as printed.",
            lang="en",
        )


def _certificates(p: Package, rows: list[tuple[str, dict[str, str | None]]]) -> None:
    over: Counter[str] = Counter()
    for result_set, row in rows:
        cast, registered = _number(row.get("ODEVZ_OBAL")), _number(row.get("VOL_SEZNAM"))
        if cast is not None and registered is not None and cast > registered:
            over[result_set] += 1
    if not over:
        return
    p.accept(
        "accounting.turnout",
        f"{over.total()} polling districts took more ballots than their roll lists, from voters with a voting "
        "certificate, who may vote in any polling district of their Senate district",
    )
    for result_set, n in over.items():
        p.add(
            "note",
            id=key("note", result_set, "certificates"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {n} polling district{'s' if n > 1 else ''}, more ballots were cast than voters were on the "
            "roll. Voters with a voting certificate may vote in any polling district of their Senate district, so "
            "the figures are stored as printed.",
            lang="en",
        )


def _revised(p: Package, folder: str, sets: list[str]) -> None:
    if not folder.endswith("nss"):
        return
    p.accept(
        "accounting.declared",
        "the polling-district file is the count as revised after the Supreme Administrative Court's ruling, "
        "while the results service still prints the original count",
    )
    for result_set in sets:
        p.add(
            "note",
            id=key("note", result_set, "revised"),
            entity_id=result_set,
            kind="caveat",
            body_md="These figures are the count as revised after the Supreme Administrative Court ruled on "
            "election complaints, as published in the volby.cz open data. The results service on volby.cz "
            "still prints the original count, which differs slightly in some regions.",
            lang="en",
        )


def _declared_xml(folder: str) -> ElementTree.Element | None:
    try:
        artifact = raw.cached(SOURCE, declared_url(folder))
    except LookupError:
        return None
    data = artifact.path.read_bytes()
    if not data.lstrip().startswith(b"<?xml"):
        return None
    data = re.sub(rb'encoding="[^"]+"', b'encoding="utf-8"', data, count=1)
    if not _utf8(data):
        data = data.decode("cp1250").encode()
    root = ElementTree.fromstring(data)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def _turnout(element: ElementTree.Element) -> dict[int, int]:
    return {measure: int(element.attrib[column]) for measure, column in DECLARED_ACCOUNTING if column in element.attrib}


def chamber(year: int) -> Package:
    folder, data_zip, reg, cis = CHAMBER[year]
    first, last = CHAMBER_DAYS[year]
    p = Package(
        f"cz.volby/ps-{year}",
        SOURCE,
        f"The {year} Czech Chamber of Deputies election by polling district from volby.cz",
        depends=["cz.volby/geo", "cz.volby/registry"],
    )
    p.external.add(CZECHIA)
    for name in (data_zip, reg, cis):
        p.uses(raw.cached(SOURCE, url(folder, name)))
    election = key("election", "ps", year)
    p.add("election", id=election, polity_id=CZECHIA, kind="general", date_from=first, date_to=last, status="held")
    p.name(election, f"{year} Czech parliamentary election", "en", "official")
    p.name(election, f"Volby do Poslanecké sněmovny {year}", "cs", "native", False)
    path = f"cz/{first}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)
    system = next(
        id
        for id, since, until, rules, _ in SYSTEMS
        if rules["formula"] != "two_round" and since <= first and (until is None or first < until)
    )
    accounting = table(folder, data_zip, "pst4")
    votes = table(folder, data_zip, "pst4p")
    lists = {row["KSTRANA"]: row for row in table(folder, reg, "psrkl").iter_rows(named=True)}
    candidates = table(folder, reg, "psrk")
    regions = {row["VOLKRAJ"]: row for row in table(folder, cis, "psvolkr").iter_rows(named=True)}
    nuts = _nuts(folder, cis)
    places = {row["OBEC"]: row for row in table(folder, cis, "pscoco").iter_rows(named=True)}
    abroad: dict[str, str] = {}
    try:
        for row in table(folder, cis, "psrzvo").iter_rows(named=True):
            if row.get("VOLKRAJ"):
                abroad[row["OKRSEK"]] = row["VOLKRAJ"]
    except LookupError:
        pass
    units = _units(p, first, nuts, table(folder, cis, "pscoco"), accounting)
    region_of: dict[str, str] = {
        str(row["ID_OKRSKY"]): str(
            abroad[row["OKRSEK"]] if row["OBEC"] == ABROAD and abroad else places[row["OBEC"]]["VOLKRAJ"]
        )
        for row in accounting.iter_rows(named=True)
    }
    votes = votes.with_columns(REGION=pl.col("ID_OKRSKY").replace_strict(region_of))
    titles = {
        row["code"]: row["text"] for row in _places().filter(pl.col("scheme_code") == "cz.kraj").iter_rows(named=True)
    }
    declared = _declared_xml(folder)
    declared_regions: dict[str, ElementTree.Element] = {}
    if declared is not None:
        for element in declared.iter("KRAJ"):
            number = element.attrib.get("CIS_KRAJ") or element.attrib.get("VOLKRAJ")
            if number is not None and "CIS_KRAJ" in element.attrib:
                declared_regions[number] = element
    known = _parties()
    seats = Counter(row["VOLKRAJ"] for row in candidates.filter(pl.col("MANDAT") == "A").iter_rows(named=True))
    sets: dict[str, str] = {}
    options: dict[tuple[str, str], str] = {}
    for number, region in sorted(regions.items(), key=lambda item: int(item[0])):
        if number not in set(region_of.values()):
            continue
        code = nuts[region["KRAJ"]]
        contest = key("contest", "ps", year, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=CHAMBER_BODY,
            district_area_id=area("cz.kraj", code),
            magnitude=seats[number],
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        title = titles[code]
        p.name(contest, title, "cs", "official")
        p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
        result_set = sets[number] = key("result_set", "ps", year, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VOLBY_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=CHAMBER_METHOD,
            lang="en",
        )
        here = candidates.filter(pl.col("VOLKRAJ") == number)
        running = sorted(set(here["KSTRANA"]) | set(votes.filter(pl.col("REGION") == number)["KSTRANA"]), key=int)
        for list_number in running:
            entry = lists[list_number]
            option = options[(number, list_number)] = key("option", "ps", year, number, list_number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(entry["VSTRANA"]) if party(entry["VSTRANA"]) in known else None,
                ballot_position=int(list_number),
                ballot_label=clean(entry["NAZEVCELK"]),
            )
        won = Counter(row["KSTRANA"] for row in here.filter(pl.col("MANDAT") == "A").iter_rows(named=True))
        for list_number, n in won.items():
            p.add("outcome", result_set_id=result_set, option_id=options[(number, list_number)], kind="won", seats=n)
        for row in here.iter_rows(named=True):
            tag = f"ps:{year}:{number}:{row['KSTRANA']}:{row['PORCISLO']}"
            person = key("person", tag)
            p.add("person", id=person, birth_precision=None)
            p.name(person, clean(f"{row['JMENO']} {row['PRIJMENI']}"), "cs", "official")
            member = row["PSTRANA"]
            candidacy = key("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[(number, row["KSTRANA"])],
                role="list_member",
                list_position=int(row["PORCISLO"]),
                ballot_name=person_name(row),
                member_of_party_id=party(member) if member is not None and party(member) in known else None,
                is_independent=member in NO_PARTY,
            )
            if row["MANDAT"] == "A":
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[(number, row["KSTRANA"])],
                    candidacy_id=candidacy,
                    kind="elected",
                )
        element = declared_regions.get(number)
        if element is not None:
            for measure, value in _turnout(next(element.iter("UCAST"))).items():
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            for side in element.iter("STRANA"):
                figures = next(side.iter("HODNOTY_STRANA"))
                option = options.get((number, side.attrib["KSTRANA"]))
                if option is None:
                    continue
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=option,
                    measure=1,
                    value=int(figures.attrib["HLASY"]),
                )
    if declared is not None and not declared_regions:
        totals: dict[str, dict[int, int]] = {}
        turnout = next(declared.iter("UCAST"), None)
        if turnout is not None:
            for element in turnout.iter("KRAJ"):
                totals[element.attrib["VOLKRAJ"]] = _turnout(next(element.iter("HODNOTY_UCAST"), element))
        for number, figures in totals.items():
            for measure, value in figures.items():
                p.add("declared", result_set_id=sets[number], option_id=None, measure=measure, value=value)
        for side in declared.iter("STRANA"):
            for element in side.iter("KRAJ"):
                number = element.attrib["VOLKRAJ"]
                option = options.get((number, side.attrib["KSTRANA"]))
                if option is None:
                    continue
                figures = next(element.iter("HODNOTY_STRANA"))
                p.add(
                    "declared",
                    result_set_id=sets[number],
                    option_id=option,
                    measure=1,
                    value=int(figures.attrib["HLASY"]),
                )
    pairs = [(sets[region_of[row["ID_OKRSKY"]]], row) for row in accounting.iter_rows(named=True)]
    for result_set, row in pairs:
        _accounting(p, result_set, units[row["ID_OKRSKY"]], row)
    _overcount(p, pairs)
    for row in votes.iter_rows(named=True):
        number = str(row["REGION"])
        p.add(
            "result_value",
            result_set_id=sets[number],
            unit_id=units[row["ID_OKRSKY"]],
            option_id=options[(number, row["KSTRANA"])],
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=int(row["POC_HLASU"]),
        )
    _revised(p, folder, list(sets.values()))
    return p


def president(year: int) -> Package:
    folder, data_zip, reg, cis = PRESIDENT[year]
    p = Package(
        f"cz.volby/prez-{year}",
        SOURCE,
        f"The {year} Czech presidential election, both rounds, by polling district from volby.cz",
        depends=["cz.volby/geo", "cz.volby/registry"],
    )
    p.external |= {CZECHIA, TERRITORY}
    for name in (data_zip, reg, cis):
        p.uses(raw.cached(SOURCE, url(folder, name)))
    data = table(folder, data_zip, "pet1")
    if "ID_OKRSKY" not in data.columns:
        data = data.with_columns(ID_OKRSKY=pl.concat_str("KOLO", "OBEC", "OKRSEK", separator=":"))
    candidates = table(folder, reg, "perk").sort(pl.col("CKAND").cast(pl.Int32))
    declared = _declared_xml(folder)
    national = {}
    turnout = {}
    if declared is not None:
        national = {element.attrib["PORADOVE_CISLO"]: element.attrib for element in declared.iter("KANDIDAT")}
        turnout = {element.attrib["KOLO"]: _turnout(element) for element in declared.iter("UCAST")}
    system = key("electoral_system", "president", 2013)
    previous: str | None = None
    pairs: list[tuple[str, dict[str, str | None]]] = []
    sets: list[str] = []
    people: dict[str, str] = {}
    for row in candidates.iter_rows(named=True):
        person = people[row["CKAND"]] = key("person", f"prez:{year}:{row['CKAND']}")
        p.add("person", id=person, gender={"0": "male", "1": "female"}.get(row["POHLAVI"] or ""))
        p.name(person, clean(f"{row['JMENO']} {row['PRIJMENI']}"), "cs", "official")
    for index, (first, last) in enumerate(PRESIDENT_DAYS[year]):
        round_number = index + 1
        election = key("election", "prez", year, round_number)
        p.add(
            "election",
            id=election,
            polity_id=CZECHIA,
            kind="general" if round_number == 1 else "runoff",
            date_from=first,
            date_to=last,
            status="held",
        )
        path = f"cz/{first}-presidential" + ("" if round_number == 1 else "-runoff")
        p.add("slug", path=path, entity_id=election, is_current=True)
        suffix = "" if round_number == 1 else ", second round"
        p.name(election, f"{year} Czech presidential election{suffix}", "en", "official")
        p.name(election, f"Volba prezidenta republiky {year}, {round_number}. kolo", "cs", "native", False)
        contest = key("contest", "prez", year, round_number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=PRESIDENCY,
            district_area_id=TERRITORY,
            magnitude=1,
            round_number=round_number,
            previous_round_id=previous,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        previous = contest
        p.name(
            contest,
            f"{year} Czech presidential election, {'first' if round_number == 1 else 'second'} round",
            "en",
            "official",
        )
        p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
        result_set = key("result_set", "prez", year, round_number)
        sets.append(result_set)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VOLBY_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=PRESIDENT_METHOD,
            lang="en",
        )
        field = f"ZVOLEN_K{round_number}"
        running = [row for row in candidates.iter_rows(named=True) if round_number == 1 or row["ZVOLEN_K1"] == "2"]
        options: dict[str, str] = {}
        for row in running:
            option = options[row["CKAND"]] = key("option", "prez", year, round_number, row["CKAND"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=int(row["CKAND"]),
                ballot_label=person_name(row),
            )
            candidacy = key("candidacy", f"prez:{year}:{round_number}:{row['CKAND']}")
            p.add(
                "candidacy",
                id=candidacy,
                person_id=people[row["CKAND"]],
                option_id=option,
                role="head",
                ballot_name=person_name(row),
                is_independent=row["PSTRANA"] in NO_PARTY,
            )
            status = row[field]
            kind = {"1": "elected", "2": "advanced"}.get(status or "", "eliminated" if round_number == 1 else "lost")
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            figures = national.get(row["CKAND"], {})
            if f"HLASY_{round_number}KOLO" in figures:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=option,
                    measure=1,
                    value=int(figures[f"HLASY_{round_number}KOLO"]),
                )
        for measure, value in turnout.get(str(round_number), {}).items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        rows = data.filter(pl.col("KOLO") == str(round_number))
        units = _units(p, first, _nuts(folder, cis), table(folder, cis, "pecoco"), rows)
        pairs.extend((result_set, row) for row in rows.iter_rows(named=True))
        for row in rows.iter_rows(named=True):
            unit = units[row["ID_OKRSKY"]]
            _accounting(p, result_set, unit, row)
            for number, option in options.items():
                value = _number(row.get(f"HLASY_{int(number):02d}"))
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
    _overcount(p, pairs)
    _revised(p, folder, sets)
    return p


def _senate_table(part: str, member: str) -> pl.DataFrame:
    index = {"data": 0, "reg": 1, "cis": 2}[part]
    frames = []
    for folder, names in SENATE.items():
        try:
            frame = table(folder, names[index], member)
        except LookupError:
            continue
        if "DATUMVOLEB" not in frame.columns and folder in SENATE_STAMPS:
            frame = frame.with_columns(DATUMVOLEB=pl.lit(SENATE_STAMPS[folder]))
        frames.append(frame)
    return pl.concat(frames, how="diagonal_relaxed")


def _cities() -> dict[str, str]:
    cities: dict[str, str] = {}
    for folder, _, _, cis in [*CHAMBER.values(), *PRESIDENT.values()]:
        member = "pscoco" if folder.startswith("ps") else "pecoco"
        for row in table(folder, cis, member).iter_rows(named=True):
            if row["OBEC_PREZ"] and row["OBEC_PREZ"] != row["OBEC"]:
                cities[row["OBEC"]] = row["OBEC_PREZ"]
    return cities


def _senate_days() -> list[str]:
    return sorted(set(_senate_table("reg", "serk")["DATUMVOLEB"]))


def _day(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


def senate(stamp: str) -> Package:
    first = _day(stamp)
    listed = _senate_table("cis", "sedatumvoleb").filter(pl.col("DATUMVOLEB") == stamp)
    if listed.is_empty():
        last, regular = first + timedelta(1) if first.weekday() == 4 else first, True
    else:
        calendar = listed.row(0, named=True)
        *_, (dd, mm, yyyy) = re.findall(r"(\d{2})\.(\d{2})\.(\d{4})?", calendar["TEXT_CZ"]) or [("", "", "")]
        last = date(int(yyyy), int(mm), int(dd)) if yyyy else first
        regular = calendar["TYPVOLEB"] == "0"
    p = Package(
        f"cz.volby/se-{first}",
        SOURCE,
        f"The Czech Senate {'election' if regular else 'by-election'} of {first}, both rounds, by polling district "
        "from volby.cz",
        depends=["cz.volby/geo", "cz.volby/registry"],
    )
    p.external |= {CZECHIA, TERRITORY}
    for folder, names in SENATE.items():
        for name in names:
            p.uses(raw.cached(SOURCE, url(folder, name)))
    data = _senate_table("data", "set5").filter(pl.col("DATUMVOLEB") == stamp)
    data = data.with_columns(ID_OKRSKY=pl.concat_str("KOLO", "OBEC", "OKRSEK", separator=":"))
    candidates = (
        _senate_table("reg", "serk")
        .filter(pl.col("DATUMVOLEB") == stamp)
        .sort(pl.col("OBVOD").cast(pl.Int32), pl.col("CKAND").cast(pl.Int32))
    )
    coco = (
        _senate_table("cis", "secoco")
        .filter(pl.col("DATUMVOLEB") == stamp)
        .with_columns(OBEC_PREZ=pl.col("OBEC").replace_strict(_cities(), default=pl.col("OBEC_PREZ")))
    )
    names = {row["OBVOD"]: clean(row["NAZEV_OBV"]) for row in _senate_table("cis", "secobv").iter_rows(named=True)}
    folder, (_, _, cis) = next(iter(SENATE.items()))
    nuts = _nuts(folder, cis)
    known = _parties()
    word = "senate" if regular else "senate-by-election"
    people: dict[tuple[str, str], str] = {}
    for row in candidates.iter_rows(named=True):
        person = people[(row["OBVOD"], row["CKAND"])] = key("person", f"se:{first}:{row['OBVOD']}:{row['CKAND']}")
        p.add("person", id=person)
        p.name(person, clean(f"{row['JMENO']} {row['PRIJMENI']}"), "cs", "official")
    pairs: list[tuple[str, dict[str, str | None]]] = []
    previous: dict[str, str] = {}
    for round_number in (1, 2):
        start, end = (first, last) if round_number == 1 else (first + timedelta(7), last + timedelta(7))
        running = candidates.filter(pl.col("PLATNOST") == "A" if round_number == 1 else pl.col("ZVOLEN_K1") == "2")
        if running.is_empty():
            continue
        election = key("election", "se", first, round_number)
        p.add(
            "election",
            id=election,
            polity_id=CZECHIA,
            kind=("general" if regular else "by_election") if round_number == 1 else "runoff",
            date_from=start,
            date_to=end,
            status="held",
        )
        path = f"cz/{start}-{word}" + ("" if round_number == 1 else "-runoff")
        p.add("slug", path=path, entity_id=election, is_current=True)
        suffix = "" if round_number == 1 else ", second round"
        title = f"{first.year} Czech Senate election" if regular else f"Czech Senate by-election of {first:%-d %B %Y}"
        p.name(election, f"{title}{suffix}", "en", "official")
        native = "Volby do Senátu" if regular else "Doplňovací volby do Senátu"
        p.name(election, f"{native} {first.year}, {round_number}. kolo", "cs", "native", False)
        rows = data.filter(pl.col("KOLO") == str(round_number))
        units = _units(p, start, nuts, coco, rows)
        options: dict[tuple[str, str], str] = {}
        sets: dict[str, str] = {}
        for district in sorted(set(running["OBVOD"]), key=int):
            contest = key("contest", "se", first, round_number, district)
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=SENATE_BODY,
                district_area_id=area("cz.senat", district),
                magnitude=1,
                round_number=round_number,
                previous_round_id=previous.get(district),
                electoral_system_id=SENATE_SYSTEM,
                default_result_kind="counted",
            )
            previous[district] = contest
            p.name(contest, names[district], "cs", "official")
            p.add(
                "slug",
                path=f"{path}/{district}-{changeset.slug(names[district])}",
                entity_id=contest,
                is_current=True,
            )
            result_set = sets[district] = key("result_set", "se", first, round_number, district)
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=VOLBY_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=SENATE_METHOD,
                lang="en",
            )
            for row in running.filter(pl.col("OBVOD") == district).iter_rows(named=True):
                tag = f"se:{first}:{round_number}:{district}:{row['CKAND']}"
                option = options[(district, row["CKAND"])] = key("option", tag)
                nominator = party(row["VSTRANA"])
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=nominator if nominator in known else None,
                    ballot_position=int(row["CKAND"]),
                    ballot_label=person_name(row),
                )
                member = row["PSTRANA"]
                p.add(
                    "candidacy",
                    id=key("candidacy", tag),
                    person_id=people[(district, row["CKAND"])],
                    option_id=option,
                    role="head",
                    ballot_name=person_name(row),
                    member_of_party_id=party(member) if member is not None and party(member) in known else None,
                    is_independent=member in NO_PARTY,
                )
                status = row[f"ZVOLEN_K{round_number}"]
                kind = {"1": "elected", "2": "advanced"}.get(
                    status or "", "eliminated" if round_number == 1 else "lost"
                )
                p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=option,
                    measure=1,
                    value=int(row[f"HLASY_K{round_number}"]),
                )
        columns = [column for column in rows.columns if column.startswith("HLASY_")]
        for row in rows.iter_rows(named=True):
            district = row["OBVOD"]
            result_set = sets[district]
            unit = units[row["ID_OKRSKY"]]
            pairs.append((result_set, row))
            _accounting(p, result_set, unit, row)
            for column in columns:
                value = _number(row[column])
                option = options.get((district, str(int(column.removeprefix("HLASY_")))))
                if option is None:
                    if value:
                        raise ValueError(f"{stamp} round {round_number} district {district}: {column} has no candidate")
                    continue
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
    _overcount(p, pairs)
    _certificates(p, pairs)
    return p


RUIAN_SOURCE = key("source", "cz.cuzk")
VOLBY_SOURCE = key("source", SOURCE)
CHAMBER_BODY = key("body", "chamber")
PRESIDENCY = key("office", "president")
SENATE_BODY = key("body", "senate")
SENATE_SYSTEM = key("electoral_system", "senate", 1996)
SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        key("electoral_system", "ps", 2002),
        date(2002, 1, 1),
        date(2021, 1, 1),
        {
            "formula": "dhondt",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "Seats are apportioned among the 14 regions by the Republic mandate number, then allocated within each region "
            "by D'Hondt among the lists that passed the national threshold: 5% for a party, 10% for a coalition of two, "
            "15% for three, and 20% for four or more. Preference votes move a candidate to the top of the list once they "
            "reach 5% of the list's votes in the region (10% in 2002)."
        ),
    ),
    (
        key("electoral_system", "ps", 2021),
        date(2021, 1, 1),
        None,
        {
            "formula": "imperiali_quota",
            "remainders": "national",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "After the Constitutional Court's 2021 ruling, seats are apportioned among the 14 regions by the Republic "
            "mandate number and allocated within each region by the Imperiali quota, with the remaining seats allocated "
            "nationally by largest remainders. The threshold is 5% for a party, 8% for a coalition of two, and 11% for "
            "three or more. Preference votes move a candidate to the top once they reach 5% of the list's votes in the "
            "region."
        ),
    ),
    (
        key("electoral_system", "president", 2013),
        date(2012, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "Direct election since 2013: a candidate who wins a majority of valid votes in the first round is elected; "
            "otherwise the two leading candidates go to a second round two weeks later."
        ),
    ),
    (
        SENATE_SYSTEM,
        date(1996, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "One senator per district, elected for six years, with a third of the 81 districts voting every two years. "
            "A candidate who wins a majority of valid votes in the first round is elected; otherwise the two leading "
            "candidates go to a second round a week later."
        ),
    ),
]
