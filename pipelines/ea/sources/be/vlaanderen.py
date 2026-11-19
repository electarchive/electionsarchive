"""Flemish local election results, 2006-2024, from the Flemish government's election pages.

The workbooks hold the results of the municipal, provincial, Antwerp district, and (in the facility municipalities)
OCMW council elections, one workbook per body and year, and for 2024 the finer counts by polling station and counting
office. Statbel's statistical sectors supply the municipal and provincial boundaries for each municipal plan, and the city of Antwerp's district layer the district outlines.
"""

import functools
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import fastexcel
import numpy
import polars as pl
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.be import kieskring

SOURCE = "be.vlaanderen"
STATBEL = "be.statbel"
ANTWERP = "be.antwerpen"
DISTRICTS = (
    "https://geodata.antwerpen.be/arcgissql/rest/services/P_Portal/portal_publiek2/MapServer/109/query"
    "?where=1%3D1&outFields=districtcode%2Cdistrictnaam%2Cafkorting&outSR=31370&f=geojson"
)
UP = "https://assets.vlaanderen.be/raw/upload/"
BELGIUM = str(uuid5(package.NAMESPACE, "polity\x1fBE"))
TOLERANCE = 10.0
SECTORS = "https://statbel.fgov.be/sites/default/files/files/opendata/Statistische%20sectoren/"
# plan: sector file, first day the plan holds, first day of the next plan
PLANS: dict[str, tuple[str, date, date | None]] = {
    "2018": ("sh_statbel_statistical_sectors_20180101.shp.zip", date(1977, 1, 1), date(2019, 1, 1)),
    "2019": ("sh_statbel_statistical_sectors_20190101.shp.zip", date(2019, 1, 1), date(2025, 1, 1)),
    "2025": ("sh_statbel_statistical_sectors_3812_20250101.shp.zip", date(2025, 1, 1), None),
}
# the plan whose municipalities held each election
ELECTION_PLAN = {
    "2006-10-08": "2018",
    "2012-10-14": "2018",
    "2018-10-14": "2019",
    "2019-06-16": "2019",
    "2024-10-13": "2025",
}
# Borsbeek joined Antwerp as its tenth district on 2025-01-01
BORSBEEK = "11247"
FLEMISH = {"10000", "20001", "30000", "40000", "70000"}

FILES: dict[str, dict[str, str]] = {
    "2006-10-08": {
        "CG": "v1732196586/repositories-prd/Gemeenteraadsverkiezingen_8_oktober_2006_j017nx.xlsx",
        "PR": "v1732196587/repositories-prd/Provincieraadsverkiezingen_8_oktober_2006_orvkjt.xlsx",
        "DI": "v1732196588/repositories-prd/Stadsdistrictsraadsverkiezingen_8_oktober_2006_xzrbka.xlsx",
    },
    "2012-10-14": {
        "CG": "v1718363050/gemeente-2012-12-17T21_28_08_ikmzln.xlsx",
        "DI": "v1718363049/stadsdistrict-2012-10-15T21_00_23_na84lz.xlsx",
        "PR": "v1718363047/provincie-2012-10-16T11_49_14_peizbf.xlsx",
    },
    "2018-10-14": {
        "CG": "v1699019526/resultaten_verkiezing_gemeenteraad_20181014_na_20190702_bldgwf.xlsx",
        "PR": "v1699019528/resultaten_verkiezing_provincieraad_20181014_neq9d8.xlsx",
        "DI": "v1699019530/resultaten_verkiezing_stadsdistrictsraad_20181014_b0r6cf.xlsx",
    },
    "2024-10-13": {
        "CG": "v1736329746/repositories-prd/Resultaten_verkiezingen_gemeenteraad_he5ge6.xlsx",
        "PR": "v1729147811/Resultaten_provincieraadsverkiezingen_13_oktober_2024_2_ybpuzk.xlsx",
        "DI": "v1729064050/Resultaten_stadsdistrictsraadsverkiezingen_13_oktober_2024_iahro9.xlsx",
        "CS": "v1729064048/Resultaten_OCMWraadsverkiezingen_13_oktober_2024_kv1arg.xlsx",
    },
}
# 2024 counts by polling station and counting office, by province
FINE_PROVINCES = ("Antwerpen", "Limburg", "Oost-Vlaanderen", "Vlaams-Brabant", "West-Vlaanderen")
FINE: dict[str, dict[str, str]] = {
    "CG": {
        "Limburg": "v1730966596/repositories-prd/Fijnmazige_resultaten_gemeenteraadsverkiezingen_13_oktober_2024_Limburg_bgjzmf.xlsx",
        "Vlaams-Brabant": "v1730966600/repositories-prd/Fijnmazige_resultaten_gemeenteraadsverkiezingen_13_oktober_2024_Vlaams-Brabant_c7qgrv.xlsx",
        "Antwerpen": "v1736329390/repositories-prd/Fijnmazige_resultaten_gemeenteraadsverkiezingen_13_oktober_2024_Antwerpen_ajoi5y.xlsx",
        "Oost-Vlaanderen": "v1736329394/repositories-prd/Fijnmazige_resultaten_gemeenteraadsverkiezingen_13_oktober_2024_Oost-Vlaanderen_d9g6vj.xlsx",
        "West-Vlaanderen": "v1736329396/repositories-prd/Fijnmazige_resultaten_gemeenteraadsverkiezingen_13_oktober_2024_West-Vlaanderen_hltazz.xlsx",
    },
    "CS": {
        "Limburg": "v1730966605/repositories-prd/Fijnmazige_resultaten_OCMW-raadsverkiezingen_13_oktober_2024_Limburg_vcj0md.xlsx",
        "Vlaams-Brabant": "v1730966606/repositories-prd/Fijnmazige_resultaten_OCMW-raadsverkiezingen_13_oktober_2024_Vlaams-Brabant_kqinic.xlsx",
    },
    "PR": {
        "Antwerpen": "v1730966608/repositories-prd/Fijnmazige_resultaten_provincieraadsverkiezingen_13_oktober_2024_Antwerpen_cfjie8.xlsx",
        "Limburg": "v1730966611/repositories-prd/Fijnmazige_resultaten_provincieraadsverkiezingen_13_oktober_2024_Limburg_eixtaz.xlsx",
        "Oost-Vlaanderen": "v1730966614/repositories-prd/Fijnmazige_resultaten_provincieraadsverkiezingen_13_oktober_2024_Oost-Vlaanderen_yul68i.xlsx",
        "Vlaams-Brabant": "v1730966616/repositories-prd/Fijnmazige_resultaten_provincieraadsverkiezingen_13_oktober_2024_Vlaams-Brabant_xnvt6r.xlsx",
        "West-Vlaanderen": "v1730966618/repositories-prd/Fijnmazige_resultaten_provincieraadsverkiezingen_13_oktober_2024_West-Vlaanderen_nusroz.xlsx",
    },
    "DI": {
        "Antwerpen": "v1730966620/repositories-prd/Fijnmazige_resultaten_stadsdistrictsraadsverkiezingen_13_oktober_2024_Antwerpen_lifbzk.xlsx",
    },
}


def urls() -> list[str]:
    found = [UP + name for files in FILES.values() for name in files.values()]
    found += [UP + name for files in FINE.values() for name in files.values()]
    return found


def fetch() -> None:
    for url in urls():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for name, _, _ in PLANS.values():
        raw.fetch(STATBEL, SECTORS + name, suffix=".zip")
    raw.fetch(ANTWERP, DISTRICTS, suffix=".geojson")
    kieskring.fetch(SOURCE)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def days() -> list[str]:
    return sorted({*FILES, "2019-06-16"})


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


# --- geography --------------------------------------------------------------------------------------------------


def _wgs(shape: shapely.Geometry, crs: pyproj.CRS) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(crs, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


@dataclass
class Municipality:
    code: str
    names: dict[str, str]
    province: str
    shape: shapely.Geometry


@dataclass
class Plan:
    municipalities: list[Municipality]
    provinces: dict[str, tuple[dict[str, str], shapely.Geometry]]
    crs: pyproj.CRS


@functools.cache
def plan(vintage: str) -> Plan:
    name, _, _ = PLANS[vintage]
    archive = zipfile.ZipFile(raw.cached(STATBEL, SECTORS + name).path)
    base = next(member for member in archive.namelist() if member.endswith(".shp"))[:-4]
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(base + ".shp")),
        shx=io.BytesIO(archive.read(base + ".shx")),
        dbf=io.BytesIO(archive.read(base + ".dbf")),
        encoding="utf-8",
    )
    crs = pyproj.CRS.from_wkt(archive.read(base + ".prj").decode())
    pieces: dict[str, list[shapely.Geometry]] = defaultdict(list)
    info: dict[str, dict[str, object]] = {}
    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
        if record is None or shape is None:
            continue
        row: dict[str, object] = dict(record.as_dict())
        province = str(row.get("C_PROVI") or row.get("CNIS_PROVI"))
        if province not in FLEMISH:
            continue
        row["C_PROVI"] = province
        code = next(str(value) for column, value in row.items() if column.startswith("CNIS5_"))
        pieces[code].append(shapely.geometry.shape(shape.__geo_interface__))
        info[code] = row
    codes = sorted(pieces)
    merged = [shapely.make_valid(shapely.coverage_union_all(pieces[code])) for code in codes]
    simple = list(shapely.make_valid(shapely.coverage_simplify(numpy.array(merged), TOLERANCE)))
    municipalities = [
        Municipality(
            code,
            {"nl": str(info[code]["T_MUN_NL"]), "fr": str(info[code]["T_MUN_FR"])},
            str(info[code]["C_PROVI"]),
            shape,
        )
        for code, shape in zip(codes, simple, strict=True)
    ]
    provinces: dict[str, tuple[dict[str, str], shapely.Geometry]] = {}
    for province in sorted({m.province for m in municipalities}):
        row = next(info[m.code] for m in municipalities if m.province == province)
        union = shapely.coverage_union_all([m.shape for m in municipalities if m.province == province])
        provinces[province] = (
            {
                "nl": str(row["T_PROVI_NL"]).removeprefix("Provincie "),
                "fr": str(row["T_PROVI_FR"]).removeprefix("Province d’").removeprefix("Province de ")
                if "Province" in str(row["T_PROVI_FR"])
                else str(row["T_PROVI_FR"]),
            },
            union,
        )
    return Plan(municipalities, provinces, crs)


@functools.cache
def antwerp_districts() -> dict[str, shapely.Geometry]:
    """The outlines of the Antwerp districts in Belgian Lambert 72, by the NIS code the results workbooks give each
    district. The city publishes one outline per district as it stands today, Borsbeek included."""
    names = {}
    for found in districts("DI").values():
        names.update({name.upper().replace("-", " "): nis for nis, name in found.items()})
    collection = json.loads(raw.cached(ANTWERP, DISTRICTS).path.read_bytes())
    codes: list[str] = []
    shapes: list[shapely.Geometry] = []
    for feature in collection["features"]:
        codes.append(names[str(feature["properties"]["districtnaam"]).strip().replace("-", " ")])
        shapes.append(shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
    if sorted(codes) != sorted(set(names.values())):
        raise ValueError("the city's districts are not the workbooks' districts")
    simple = shapely.make_valid(shapely.coverage_simplify(numpy.array(shapes), TOLERANCE))
    return dict(zip(codes, simple, strict=True))


STATBEL_SOURCE = key("source", "statbel")
ANTWERP_SOURCE = key("source", ANTWERP)
DECREE_SOURCE = key("source", "kieskring")
VLAANDEREN_SOURCE = key("source", SOURCE)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def district_area(day: str, code: str) -> str:
    return key("area", "be.kieskring", day, code)


def district_members(day: str) -> dict[str, list[str]]:
    """The codes of the municipalities in each provincial council district of an election, by the NIS code that the
    results workbook gives the district."""
    current = plan(ELECTION_PLAN[day])
    if day == "2024-10-13":
        # the decree's 2025 annex lists the merged Beveren-Kruibeke-Zwijndrecht, so the published district totals fix
        # the assignment
        owned: dict[str, list[str]] = defaultdict(list)
        for code in sorted(PR_DISTRICT_OF):
            owned[PR_DISTRICT_OF[code]].append(code)
        return dict(owned)
    by_name = kieskring.members(day, {m.code: m.names["nl"] for m in current.municipalities})
    found: dict[str, list[str]] = {}
    for nis in districts("PR")[day]:
        owner = [name for name, codes in by_name.items() if nis in codes]
        if len(owner) != 1:
            raise ValueError(f"{day}: district {nis} is not in exactly one decree district")
        found[nis] = by_name[owner[0]]
    if sorted(code for codes in found.values() for code in codes) != sorted(m.code for m in current.municipalities):
        raise ValueError(f"{day}: the districts do not partition the municipalities")
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Flanders' provinces and municipalities in the 2018, 2019, and 2025 municipal plans, from Statbel's "
        "statistical sectors, with the provincial council districts (the unions of the municipalities that the "
        "decrees assign to them) and the Antwerp district council areas",
    )
    p.external.add(BELGIUM)
    p.add(
        "source",
        id=STATBEL_SOURCE,
        kind="official_body",
        publisher="Statbel (Algemene Directie Statistiek)",
        title="Statistical sectors (Shapefile)",
        url="https://statbel.fgov.be/en/open-data",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Statbel (Belgium)",
    )
    for code, rank, kind, noun, system in (
        ("be.provincie", 1, "administrative", "province", "nis"),
        ("be.kieskring", 2, "electoral_district", "provincial council district", None),
        ("be.gemeente", 3, "administrative", "municipality", "nis"),
        ("be.district", 4, "electoral_district", "Antwerp district", "nis"),
    ):
        p.add("scheme", code=code, polity_id=BELGIUM, kind=kind, rank=rank, noun=noun, code_system=system)
    seen: set[object] = set()
    for vintage, (name, since, until) in PLANS.items():
        current = plan(vintage)
        artifact = raw.cached(STATBEL, SECTORS + name)
        p.artifact(artifact, STATBEL_SOURCE)
        for scheme in ("be.provincie", "be.gemeente"):
            p.add(
                "boundary_set",
                id=key("boundary_set", scheme, vintage),
                scheme_code=scheme,
                vintage=vintage,
                valid_from=since,
                valid_to=until,
                as_of=since,
            )
        for code, (names, union) in current.provinces.items():
            _place(p, seen, "be.provincie", code, names, None, union, current.crs, since, until)
        for town in current.municipalities:
            parent = area("be.provincie", town.province)
            _place(p, seen, "be.gemeente", town.code, town.names, parent, town.shape, current.crs, since, until)
    p.add(
        "source",
        id=DECREE_SOURCE,
        kind="official_body",
        publisher="Vlaamse overheid",
        title="Composition of the provincial council districts (decree annexes)",
        url="https://codex.vlaanderen.be/",
        attribution="Vlaamse overheid, Vlaamse Codex and Vlaams Parlement",
    )
    for url, _ in kieskring.DECREES.values():
        p.artifact(raw.cached(SOURCE, url), DECREE_SOURCE)
    council_days = sorted(FILES)
    for index, day in enumerate(council_days):
        since = date.fromisoformat(day)
        until = date.fromisoformat(council_days[index + 1]) if index + 1 < len(council_days) else None
        for nis, name in districts("PR")[day].items():
            id = district_area(day, nis)
            p.add("area", id=id, scheme_code="be.kieskring")
            p.add("area_code", area_id=id, code=nis, valid_from=since, valid_to=until)
            p.name(id, name, "nl", "official")
        current = plan(ELECTION_PLAN[day])
        shapes = {m.code: m.shape for m in current.municipalities}
        for nis, codes in district_members(day).items():
            wgs = _wgs(shapely.coverage_union_all([shapes[code] for code in codes]), current.crs)
            p.add(
                "area_geometry",
                area_id=district_area(day, nis),
                valid_from=since,
                valid_to=until,
                geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(wgs), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=STATBEL_SOURCE,
            )
    p.add(
        "source",
        id=ANTWERP_SOURCE,
        kind="official_body",
        publisher="Stad Antwerpen",
        title="District (Antwerp city GIS layer)",
        url="https://portaal-stadantwerpen.opendata.arcgis.com",
        license="Gratis open data licentie Stad Antwerpen",
        license_url="http://opendata.antwerpen.be/gratis-open-data-licentie",
        attribution="© Stad Antwerpen",
    )
    p.artifact(raw.cached(ANTWERP, DISTRICTS), ANTWERP_SOURCE)
    latest: dict[str, str] = {}
    for names in districts("DI").values():
        latest.update(names)
    shapes = antwerp_districts()
    crs = plan("2019").crs
    for nis, name in sorted(latest.items()):
        id = area("be.district", nis)
        p.add("area", id=id, scheme_code="be.district")
        p.add("area_code", area_id=id, code=nis)
        p.add("area_parent", area_id=id, parent_id=area("be.gemeente", "11002"))
        p.name(id, name, "nl", "official")
    for vintage, (_, since, until) in PLANS.items():
        p.add(
            "boundary_set",
            id=key("boundary_set", "be.district", vintage),
            scheme_code="be.district",
            vintage=vintage,
            valid_from=since,
            valid_to=until,
            as_of=since,
        )
        for nis in sorted(latest):
            if nis == BORSBEEK and vintage != "2025":
                continue
            wgs = _wgs(shapes[nis], crs)
            p.add(
                "area_geometry",
                area_id=area("be.district", nis),
                valid_from=since,
                valid_to=until,
                geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(wgs), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=ANTWERP_SOURCE,
            )
    return p


def _place(
    p: Package,
    seen: set[object],
    scheme: str,
    code: str,
    names: dict[str, str],
    parent: str | None,
    shape: shapely.Geometry | None,
    crs: pyproj.CRS | None,
    since: date | None,
    until: date | None,
) -> str:
    id = area(scheme, code)
    if id not in seen:
        seen.add(id)
        p.add("area", id=id, scheme_code=scheme)
    for language, text in names.items():
        if (id, language, text) not in seen:
            seen.add((id, language, text))
            p.name(id, text, language, "official", language == "nl")
    p.add("area_code", area_id=id, code=code, valid_from=since, valid_to=until)
    if parent is not None:
        p.add("area_parent", area_id=id, parent_id=parent, valid_from=since, valid_to=until)
    if shape is not None and crs is not None:
        wgs = _wgs(shape, crs)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=since,
            valid_to=until,
            geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(wgs), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=STATBEL_SOURCE,
        )
    return id


# --- result workbooks -------------------------------------------------------------------------------------------


def sheets(url: str) -> dict[str, pl.DataFrame]:
    """Read every sheet of a workbook as strings, with the header row found by its NIS column."""
    workbook = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    out: dict[str, pl.DataFrame] = {}
    for sheet in workbook.sheet_names:
        frame = workbook.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()
        rows = frame.rows()
        top = next(
            i for i, row in enumerate(rows) if any(str(c or "").strip().lower() in ("nis", "nis code") for c in row)
        )
        seen: dict[str, int] = {}
        names: list[str] = []
        for j, cell in enumerate(rows[top]):
            name = re.sub(r"\s+", " ", str(cell or f"col{j}")).strip().lower()
            seen[name] = seen.get(name, 0) + 1
            names.append(name if seen[name] == 1 else f"{name}#{seen[name]}")
        out[sheet] = frame.slice(top + 1).rename(dict(zip(frame.columns, names, strict=True)))
    return out


def first(frame: pl.DataFrame, *candidates: str) -> str:
    for name in candidates:
        if name in frame.columns:
            return name
    raise KeyError(f"none of {candidates} in {frame.columns}")


def whole(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars().cast(pl.Float64, strict=False).round(0).cast(pl.Int64, strict=False)


@dataclass
class Body:
    entities: pl.DataFrame  # nis, name, seats, registered, ballots, blank
    lists: pl.DataFrame  # nis, nr, name, cat1, cat2, votes, seats
    cands: pl.DataFrame  # nis, nr, pos, name, votes, elected, successor


def body(day: str, kind: str) -> Body:
    url = UP + FILES[day][kind]
    book = sheets(url)
    year = day[:4]
    if year == "2006":
        lists, cands = book["Party results"], next(v for k, v in book.items() if k.startswith("Candidate"))
        label = first(lists, "gemeente", "district", "verkiezing")
        lists = lists.filter(pl.col("nis").str.contains(r"^\d+$"))
        out_lists = lists.select(
            nis=pl.col("nis"),
            name=pl.col(label),
            nr=whole("lijstnummer"),
            list=pl.col("lijst").str.strip_chars(),
            cat1=whole("vollediglijststemmen"),
            cat2=whole("onvollediglijststemmen"),
            votes=whole("stemcijfer"),
        ).rename({"list": "label"})
        out_cands = cands.filter(pl.col("nis").str.contains(r"^\d+$")).select(
            nis=pl.col("nis"),
            nr=whole("lijstnummer"),
            pos=whole("kandidaat volgnummer"),
            name=pl.concat_str(
                pl.col("voornaam").fill_null("").str.strip_chars(),
                pl.col("familienaam").fill_null("").str.strip_chars(),
                separator=" ",
            ).str.strip_chars(),
            votes=whole("voorkeurstemmen"),
            elected=pl.col("verkozen").str.to_lowercase() == "true",
            successor=pl.lit(None, pl.Int64),
        )
        seats = out_cands.group_by("nis", "nr").agg(seats=pl.col("elected").sum().cast(pl.Int64))
        out_lists = out_lists.join(seats, on=["nis", "nr"], how="left").with_columns(pl.col("seats").fill_null(0))
        names = out_lists.select("nis", "name").unique("nis").sort("nis")
        entities = names.join(
            out_lists.group_by("nis").agg(seats=pl.col("seats").sum()), on="nis", how="left"
        ).with_columns(registered=pl.lit(None, pl.Int64), ballots=pl.lit(None, pl.Int64), blank=pl.lit(None, pl.Int64))
        return Body(entities, out_lists.drop("name"), out_cands)
    if year == "2012":
        general = book["EntiteitResultaten"].filter(pl.col("nis").is_not_null())
        general = general.with_columns(pl.col("nis").str.replace(r"^D", ""))
        lists, cands = book["Lijstresultaten"], book["Kandidaatresultaten"]
        strip = pl.col("nis").str.replace(r"^D", "")
        out_lists = lists.filter(pl.col("nis").is_not_null()).select(
            nis=strip,
            nr=whole("lijstnummer"),
            label=pl.col("lijst").str.strip_chars(),
            cat1=whole("vollediglijststemmen"),
            cat2=whole("onvollediglijststemmen"),
        )
        out_lists = out_lists.with_columns(votes=pl.col("cat1") + pl.col("cat2"))
        out_cands = cands.filter(pl.col("nis").is_not_null()).select(
            nis=strip,
            nr=whole("lijstnummer"),
            pos=whole("volgnummer"),
            name=pl.col("naam").str.strip_chars(),
            votes=whole("voorkeurstemmen"),
            elected=pl.col("verkozen").str.to_lowercase() == "true",
            successor=pl.lit(None, pl.Int64),
        )
        seats = out_cands.group_by("nis", "nr").agg(seats=pl.col("elected").sum().cast(pl.Int64))
        out_lists = out_lists.join(seats, on=["nis", "nr"], how="left").with_columns(pl.col("seats").fill_null(0))
        entities = general.select(
            nis=pl.col("nis"),
            name=pl.col("verkiezing").str.strip_chars(),
            registered=whole("stemgerechtigden"),
            ballots=whole("opkomst"),
            blank=whole("ongeldig+blanco"),
        ).join(out_lists.group_by("nis").agg(seats=pl.col("seats").sum()), on="nis", how="left")
        return Body(entities, out_lists, out_cands)
    general = book["algemeen"].filter(pl.col("nis").str.contains(r"^\d+$"))
    lists, cands = book["lijsten"].filter(pl.col("nis").str.contains(r"^\d+$")), book["kandidaten"]
    if set(cands["nis"].drop_nulls()) == {"D"}:
        # the provincial workbook's candidate rows carry a status letter after the date and no seat count, unlike
        # its header
        c = cands.columns
        names = [*c[:2], "status", "nis", c[3], *c[5:]]
        cands = cands.rename(dict(zip(c, names, strict=True)))
    cands = cands.filter(pl.col("nis").str.contains(r"^\d+$"))
    district = first(general, "kieskring", "kieskring (pr-district)")
    entities = general.select(
        nis=pl.col("nis"),
        name=pl.col(district).str.strip_chars(),
        seats=whole("mandaten"),
        registered=whole("ingeschreven kiezers"),
        ballots=whole(first(general, "totaal stembiljetten", "stembiljetten")),
        blank=whole("blanco&ongeldig"),
    )
    out_lists = lists.select(
        nis=pl.col("nis"),
        nr=whole(first(lists, "lijstnr")),
        label=pl.col("lijst").str.strip_chars(),
        cat1=whole(first(lists, "lijststemmen cat.1", "stembiljetten cat.1", "stemb cat 1")),
        cat2=whole(first(lists, "lijststemmen cat.2", "stembiljetten cat.2", "stemb cat 2")),
        votes=whole("stemcijfer"),
        seats=whole(first(lists, "verkregen zetels")),
    )
    out_cands = cands.select(
        nis=pl.col("nis"),
        nr=whole("lijstnr"),
        pos=whole("volgnr"),
        name=pl.col("naam stembiljet").str.strip_chars(),
        votes=whole("naamstemmen"),
        elected=whole("verkozen").is_not_null(),
        successor=whole("opvolger"),
    )
    return Body(entities, out_lists, out_cands)


# --- registry ---------------------------------------------------------------------------------------------------

REGISTERED, BALLOTS, VALID, INVALID, PREFERENCE = 2, 3, 4, 5, 7
# kind -> slug, English and Dutch body names, English and Dutch contest titles
BODIES: dict[str, tuple[str, str, str, str, str]] = {
    "CG": ("municipal-councils", "Municipal councils", "Gemeenteraden", "Municipal council of", "Gemeenteraad van"),
    "PR": (
        "provincial-councils",
        "Provincial councils",
        "Provincieraden",
        "Provincial council, district of",
        "Provincieraad, kieskring",
    ),
    "DI": (
        "district-councils",
        "Antwerp district councils",
        "Districtsraden van Antwerpen",
        "District council of",
        "Districtsraad van",
    ),
    "CS": (
        "ocmw-councils",
        "Social welfare (OCMW) councils",
        "OCMW-raden",
        "Social welfare council of",
        "OCMW-raad van",
    ),
}
# lists that carry the label of one party; cartels and local lists stay unmatched
PARTIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "vooruit": ("Vooruit", "Vooruit", ("spa", "vooruit")),
    "cdv": ("CD&V", "CD&V", ("cdv",)),
    "nva": ("N-VA", "N-VA", ("nva",)),
    "openvld": ("Open Vld", "Open Vld", ("openvld", "vld")),
    "groen": ("Groen", "Groen", ("groen",)),
    "vlaamsbelang": ("Vlaams Belang", "Vlaams Belang", ("vlaamsbelang",)),
    "pvda": ("PVDA", "PVDA", ("pvda",)),
}
PARTY_BY_LABEL = {label: code for code, (_, _, labels) in PARTIES.items() for label in labels}
SPA_NAME = "sp.a"


def folded(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", changeset.slug(text))


def party_of(label: str) -> str | None:
    code = PARTY_BY_LABEL.get(folded(label))
    return key("party", code) if code else None


def body_id(kind: str) -> str:
    return key("body", kind)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Flemish council bodies, the parties behind the main lists, and the source of the Flemish local election "
        "results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BELGIUM)
    p.add(
        "source",
        id=VLAANDEREN_SOURCE,
        kind="official_body",
        publisher="Vlaamse overheid",
        title="Vlaanderen kiest: resultaten van de lokale en provinciale verkiezingen",
        url="https://www.vlaanderen.be/vlaanderen-kiest/resultaten",
        attribution="Vlaamse overheid, Vlaanderen kiest",
    )
    p.add(
        "scheme",
        code="be.bureau",
        polity_id=BELGIUM,
        kind="precinct",
        rank=5,
        noun="counting unit",
        code_system=None,
    )
    for url in urls():
        p.artifact(raw.cached(SOURCE, url), VLAANDEREN_SOURCE)
    for kind, (_, english, dutch, _, _) in BODIES.items():
        id = body_id(kind)
        p.add("body", id=id, polity_id=BELGIUM, kind="council")
        p.name(id, english, "en", "official")
        p.name(id, dutch, "nl", "native", False)
    for code, (english, dutch, _) in PARTIES.items():
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=BELGIUM)
        p.name(id, english, "en", "official")
        if dutch != english:
            p.name(id, dutch, "nl", "native", False)
    p.name(key("party", "vooruit"), SPA_NAME, "nl", "historical", False)
    return p


# --- elections --------------------------------------------------------------------------------------------------

# the 2024 provincial council district of each municipality; its sums of list votes match the published district
# totals for every list, and no other assignment does
PR_DISTRICTS_2024: dict[str, str] = {
    "11002": (
        "11001 11002 11004 11005 11008 11009 11013 11016 11018 11021 11022 11023 11024 11025 11029 11030 "
        "11035 11037 11038 11039 11040 11044 11050 11052 11053 11054 11055 11057 "
    ),
    "12025": ("12002 12005 12007 12009 12014 12021 12025 12026 12029 12035 12040 12041 "),
    "13040": (
        "13001 13002 13003 13004 13006 13008 13010 13011 13012 13013 13014 13016 13017 13019 13021 13023 "
        "13025 13029 13031 13035 13036 13037 13040 13044 13046 13049 13053 "
    ),
    "23027": (
        "23002 23003 23009 23016 23025 23027 23033 23038 23039 23044 23045 23047 23050 23052 23060 23062 "
        "23064 23077 23081 23086 23088 23094 23096 23097 23098 23099 23100 23101 23102 23103 23104 23105 "
        "23106 "
    ),
    "24062": (
        "24001 24007 24008 24009 24011 24014 24016 24020 24028 24033 24038 24041 24043 24045 24048 24054 "
        "24055 24059 24062 24066 24086 24094 24104 24107 24109 24130 24133 24134 24135 24137 "
    ),
    "31005": ("31003 31004 31005 31006 31012 31022 31033 31040 31042 31043 "),
    "33011": (
        "32003 32006 32010 32011 32030 33011 33016 33021 33029 33037 33039 33040 33041 35002 35005 35006 "
        "35011 35013 35014 35029 38002 38008 38014 38016 38025 "
    ),
    "34022": (
        "34002 34003 34009 34013 34022 34023 34025 34027 34040 34041 34042 34043 36006 36007 36008 36010 "
        "36011 36012 36015 36019 37002 37010 37011 37017 37020 37021 37022 "
    ),
    "41002": (
        "41002 41011 41018 41024 41027 41034 41048 41063 41081 41082 45035 45041 45059 45060 45061 45062 "
        "45063 45064 45065 45068 "
    ),
    "44021": (
        "43002 43005 43007 43010 43014 43018 44013 44019 44020 44021 44052 44064 44081 44083 44084 44085 "
        "44086 44087 44088 "
    ),
    "46021": ("42003 42004 42006 42008 42010 42011 42023 42025 42026 42028 46020 46021 46024 46025 46029 46030 "),
    "71022": ("71002 71004 71011 71016 71017 71020 71024 71034 71037 71045 71053 71066 71067 71070 71071 71072 "),
    "72021": ("72003 72004 72018 72020 72021 72030 72037 72038 72039 72041 72042 72043 "),
    "73083": ("73001 73022 73028 73042 73066 73098 73107 73109 73110 73111 "),
}
PR_DISTRICT_OF = {nis: district for district, codes in PR_DISTRICTS_2024.items() for nis in codes.split()}
RERUN = "2019-06-16"
RERUN_NIS = "73006"
METHOD = (
    "The Flemish government publishes, for each council, the votes for every list (the ballots cast for it, which "
    "add up to the valid ballots), each candidate's preference votes, the seats each list won, and the candidates "
    "elected. Blank and invalid ballots are published together, so they are stored as invalid ballots."
)


def districts(kind: str) -> dict[str, dict[str, str]]:
    """The district names of one body, by election day and NIS code."""
    return {
        day: dict(body(day, kind).entities.select("nis", "name").iter_rows()) for day in FILES if kind in FILES[day]
    }


def election(day: str) -> Package:
    rerun = day == RERUN
    kinds = ["CG"] if rerun else list(FILES[day])
    year = day[:4]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day} municipal council election in Bilzen, held again after the court annulled the 14 October 2018 "
        "vote there"
        if rerun
        else f"The {day} Flemish local elections: " + ", ".join(BODIES[k][1].lower() for k in kinds),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([f"{SOURCE}/2018-10-14"] if rerun else [])],
    )
    p.external.add(BELGIUM)
    for kind in kinds:
        p.uses(raw.cached(SOURCE, UP + FILES["2018-10-14" if rerun else day][kind]))
    election_id = key("election", day)
    base = f"be/{day}-local-flanders"
    p.add(
        "election",
        id=election_id,
        polity_id=BELGIUM,
        kind="by_election" if rerun else "local",
        date_from=date.fromisoformat(day),
        date_to=date.fromisoformat(day),
        status="held",
        rerun_of_id=key("election", "2018-10-14") if rerun else None,
    )
    if rerun:
        p.name(election_id, "2019 Bilzen municipal election", "en", "official")
        p.name(election_id, "Buitengewone gemeenteraadsverkiezing Bilzen 2019", "nl", "native", False)
        base = f"be/{day}-bilzen"
    else:
        p.name(election_id, f"{year} Flemish local elections", "en", "official")
        p.name(election_id, f"Lokale en provinciale verkiezingen {year} in Vlaanderen", "nl", "native", False)
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    p.add("note", id=key("note", election_id), entity_id=election_id, kind="methodology", body_md=METHOD, lang="en")
    plan_codes = {m.code for m in plan(ELECTION_PLAN[day]).municipalities}
    for kind in kinds:
        build_body(p, day, kind, election_id, base, plan_codes, only=RERUN_NIS if rerun else None)
    if day in ACCEPTED:
        p.accept(*ACCEPTED[day])
    return p


ACCEPTED = {
    "2012-10-14": (
        "accounting.votes_valid",
        (
            "Erpe-Mere (41082): the workbook's list votes total 14,088, more than its 14,228 ballots less 475 blank "
            "and invalid ballots; both figures are as published"
        ),
    ),
    "2024-10-13": (
        "accounting.declared",
        (
            "Deerlijk (34009): the municipal workbook carries the corrections the electoral dispute council made to "
            "six candidates' preference votes, which the counts by polling station published earlier do not"
        ),
    ),
    "2018-10-14": (
        "accounting.turnout",
        "Herstappe (73028): the workbook gives 72 ballots cast and 71 registered voters; both figures are as published",
    ),
}


@dataclass
class Made:
    contest: str
    tag: str
    unit: str
    entity: dict[str, object]
    lists: dict[int, str]
    cands: dict[tuple[int, int], str]
    area: str
    votes: dict[int, int]
    pref: dict[tuple[int, int], int]


def build_body(
    p: Package, day: str, kind: str, election_id: str, base: str, plan_codes: set[str], only: str | None
) -> None:
    source_day = "2018-10-14" if day == RERUN else day
    data = body(source_day, kind)
    entities, lists, cands = data.entities, data.lists, data.cands
    if only is not None:
        entities, lists, cands = (frame.filter(pl.col("nis") == only) for frame in (entities, lists, cands))
    elif day == "2018-10-14" and kind == "CG":
        entities, lists, cands = (frame.filter(pl.col("nis") != RERUN_NIS) for frame in (entities, lists, cands))
    slug, _, _, english, dutch = BODIES[kind]
    if kind in ("CG", "CS"):
        missing = set(entities["nis"]) - plan_codes
        if missing:
            raise ValueError(f"{day} {kind}: NIS codes {sorted(missing)} are not in the municipal plan")
    persons = cands.group_by("nis", "nr").agg(rows=pl.struct(pl.all()))
    by_list = {(r["nis"], r["nr"]): r["rows"] for r in persons.iter_rows(named=True)}
    made: dict[str, Made] = {}
    list_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in lists.iter_rows(named=True):
        list_rows[row["nis"]].append(row)
    for entity in entities.iter_rows(named=True):
        nis, name = entity["nis"], entity["name"]
        area_id = {
            "CG": area("be.gemeente", nis),
            "CS": area("be.gemeente", nis),
            "DI": area("be.district", nis),
            "PR": district_area(source_day, nis),
        }[kind]
        tag = f"{day}:{kind}:{nis}"
        contest, result_set, unit = (key(part, tag) for part in ("contest", "result_set", "unit"))
        rows = list_rows.get(nis, [])
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body_id(kind),
            district_area_id=area_id,
            magnitude=entity["seats"],
            round_number=1,
            default_result_kind="counted" if rows else "tacit",
        )
        p.name(contest, f"{english} {name}", "en", "official")
        p.name(contest, f"{dutch} {name}", "nl", "native", False)
        p.add("slug", path=f"{base}/{slug}/{nis}-{changeset.slug(name)}", entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted" if rows else "tacit",
            source_id=VLAANDEREN_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0 if rows else None,
        )
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BELGIUM,
            scheme_code="be.bureau",
            code=f"{tag}",
            area_relation="equals",
            area_id=area_id,
            kind="standard",
        )
        accounting(p, result_set, unit, entity)
        known = made[nis] = Made(contest, tag, unit, entity, {}, {}, area_id, {}, {})
        for row in rows:
            nr = int(str(row["nr"]))
            option = known.lists[nr] = key("option", tag, nr)
            known.votes[nr] = int(str(row["votes"]))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_of(str(row["label"])),
                ballot_position=nr,
                ballot_label=row["label"],
            )
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=row["votes"],
            )
            if row["seats"]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row["seats"])
            for person_row in by_list.get((nis, nr), []):
                pos = int(person_row["pos"])
                who = key("person", tag, nr, pos)
                candidate = known.cands[nr, pos] = key("option", tag, nr, pos, "candidate")
                if person_row["votes"] is not None:
                    known.pref[nr, pos] = int(person_row["votes"])
                p.add("person", id=who)
                p.name(who, person_row["name"], "nl", "official")
                p.add(
                    "contest_option",
                    id=candidate,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=option,
                    ballot_position=pos,
                    ballot_label=person_row["name"],
                )
                candidacy = key("candidacy", tag, nr, pos)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=who,
                    option_id=option,
                    role="list_member",
                    list_position=pos,
                    ballot_name=person_row["name"],
                )
                if person_row["votes"] is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=candidate,
                        channel=0,
                        measure=PREFERENCE,
                        count_round=0,
                        flags=0,
                        value=person_row["votes"],
                    )
                if person_row["elected"]:
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
    if day == "2024-10-13" and kind in FINE:
        fine(p, kind, made, election_id)


def fine(p: Package, kind: str, made: dict[str, Made], election_id: str) -> None:
    """Add the 2024 counts by polling station and counting office as a second, finer result set of each contest."""
    units: dict[tuple[str, str], str] = {}
    valid: Counter[str] = Counter()

    def target(nis: str) -> Made:
        return made[PR_DISTRICT_OF[nis] if kind == "PR" else nis]

    def value(known: Made, unit: str, option: str | None, measure: int, count: int) -> None:
        p.add(
            "result_value",
            result_set_id=key("result_set", known.tag, "fine"),
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=count,
        )

    for url in FINE[kind].values():
        book = sheets(UP + url)
        for part, word, column in (("Dig", "Stembureau", "nr stembureau"), ("Pap", "Telbureau", "nr telbureau")):
            if f"{part}-{word}-Stats" not in book:
                continue
            for r in book[f"{part}-{word}-Stats"].iter_rows(named=True):
                nis = r["nis code"]
                known = target(nis)
                code = f"{part[0]}{r[column]}"
                place = r.get("naam geografisch deelgebied")
                label = f"{(r.get('gemeente') or r.get('stadsdistrict'))}, {word.lower()} {r[column]}" + (
                    f" ({place})" if place else ""
                )
                id = units[known.tag, f"{nis}:{code}"] = key("unit", known.tag, nis, code)
                p.add(
                    "reporting_unit",
                    id=id,
                    polity_id=BELGIUM,
                    scheme_code="be.bureau",
                    code=f"{known.tag}:{nis}:{code}",
                    parent_id=known.unit,
                    area_relation="within",
                    area_id=known.area if kind == "DI" else area("be.gemeente", nis),
                    kind="aggregate" if part == "Pap" else "standard",
                )
                p.name(id, label, "nl", "official")
                for measure, name in (
                    (BALLOTS, "totaal # stemmen"),
                    (VALID, "# geldige stemmen"),
                    (INVALID, "# blanco en ongeldige stemmen"),
                ):
                    count = whole_value(r[name])
                    if count is not None:
                        value(known, id, None, measure, count)
                        if measure == VALID:
                            valid[known.contest] += count
            for r in book[f"{part}-{word}-Lijsten"].iter_rows(named=True):
                nis = r["nis code"]
                known = target(nis)
                count = whole_value(r["stemcijfer"])
                if count:
                    unit = units[known.tag, f"{nis}:{part[0]}{r[column]}"]
                    value(known, unit, known.lists[whole_value(r["nr lijst"]) or 0], 1, count)
            for r in book[f"{part}-{word}-Kandidaten"].iter_rows(named=True):
                nis = r["nis code"]
                known = target(nis)
                count = whole_value(r["# naamstemmen"])
                if count:
                    unit = units[known.tag, f"{nis}:{part[0]}{r[column]}"]
                    option = known.cands[whole_value(r["nr lijst"]) or 0, whole_value(r["volgnr kandidaat"]) or 0]
                    value(known, unit, option, PREFERENCE, count)
    for known in made.values():
        if known.contest not in valid:
            continue
        rs = key("result_set", known.tag, "fine")
        entity = known.entity
        official = int(str(entity["ballots"] or 0)) - int(str(entity["blank"] or 0))
        p.add(
            "result_set",
            id=rs,
            election_id=election_id,
            contest_id=known.contest,
            stage="official",
            kind="counted",
            source_id=VLAANDEREN_SOURCE,
            status="active",
            is_default=False,
            completeness=round(min(valid[known.contest] / official, 1.0), 6) if official else None,
        )
        declared = [(None, VALID, official), (None, BALLOTS, entity["ballots"]), (None, INVALID, entity["blank"])]
        declared += [(option, 1, known.votes[nr]) for nr, option in known.lists.items()]
        declared += [(known.cands[c], PREFERENCE, count) for c, count in known.pref.items()]
        for option, measure, count in declared:
            if count is not None:
                p.add("declared", result_set_id=rs, option_id=option, measure=measure, value=count)


def whole_value(text: str | None) -> int | None:
    return None if text is None or not text.strip() else round(float(text))


def accounting(p: Package, result_set: str, unit: str, entity: dict[str, object]) -> None:
    ballots, blank = entity["ballots"], entity["blank"]
    figures = {REGISTERED: entity["registered"], BALLOTS: ballots, INVALID: blank}
    if isinstance(ballots, int) and isinstance(blank, int) and ballots > 0:
        figures[VALID] = ballots - blank
    for measure, value in figures.items():
        if value is not None and (measure == REGISTERED or ballots):
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


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        election(day).write()


def emit() -> None:
    package.run("load", packages())
