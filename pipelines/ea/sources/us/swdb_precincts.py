import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.swdb_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SWDB = "https://statewidedatabase.org/pub/data/"
PUBLISHER = "Statewide Database, University of California, Berkeley"
LICENSE = "Unconfirmed: permission to redistribute requested from the Statewide Database on 2026-10-17, pending"
NOTES = (
    "Held out of public releases and default views until the Statewide Database grants permission: "
    "the shapes sit on the us.restricted_precinct scheme and link to units only as extra memberships."
)
DAYS = {
    "P02": date(2002, 3, 5),
    "G02": date(2002, 11, 5),
    "P04": date(2004, 3, 2),
    "G04": date(2004, 11, 2),
    "P06": date(2006, 6, 6),
    "G06": date(2006, 11, 7),
    "P08": date(2008, 6, 3),
    "G08": date(2008, 11, 4),
    "P10": date(2010, 6, 8),
    "G10": date(2010, 11, 2),
    "P12": date(2012, 6, 5),
    "G12": date(2012, 11, 6),
    "P14": date(2014, 6, 3),
    "G14": date(2014, 11, 4),
    "P16": date(2016, 6, 7),
    "G16": date(2016, 11, 8),
    "P18": date(2018, 6, 5),
    "G18": date(2018, 11, 6),
    "P20": date(2020, 3, 3),
    "G20": date(2020, 11, 3),
    "P22": date(2022, 6, 7),
    "G22": date(2022, 11, 8),
    "P24": date(2024, 3, 5),
    "G24": date(2024, 11, 5),
    "P26": date(2026, 6, 2),
}
ELECTIONS = list(DAYS)[:-1]
KINDS = ("srprec", "mprec")
BROKEN = {("P14", "mprec")}
TOLERANCE = 0.00002


def files() -> dict[tuple[str, str], list[str]]:
    found = {}
    for election in ELECTIONS:
        page = f"{SWDB}{election}/state/"
        names = set(re.findall(r'href="([^"?/]+_shp\.zip)"', raw.cached(SOURCE, page).path.read_text()))
        for kind in KINDS:
            if (election, kind) in BROKEN:
                found[(election, kind)] = [
                    f"{SWDB}{election}/{folder}{name}"
                    for folder in re.findall(
                        r'href="(c\d{3}/)"', raw.cached(SOURCE, f"{SWDB}{election}/").path.read_text()
                    )
                    for name in sorted(
                        set(
                            re.findall(
                                rf'href="({kind}_\d{{3}}_{election.lower()}[^"?/]*_shp\.zip)"',
                                raw.cached(SOURCE, f"{SWDB}{election}/{folder}").path.read_text(),
                            )
                        )
                    )[:1]
                ]
                continue
            candidates = sorted(
                (name for name in names if name.startswith(f"{kind}_") and election.lower() in name),
                key=lambda name: ("state" not in name, "v01" not in name, name),
            )
            if candidates:
                found[(election, kind)] = [page + candidates[0]]
    return found


def fetch() -> None:
    for election in ELECTIONS:
        raw.fetch(SOURCE, f"{SWDB}{election}/state/", suffix=".html")
    for election, _ in BROKEN:
        raw.fetch(SOURCE, f"{SWDB}{election}/", suffix=".html")
        for folder in re.findall(r'href="(c\d{3}/)"', raw.cached(SOURCE, f"{SWDB}{election}/").path.read_text()):
            raw.fetch(SOURCE, f"{SWDB}{election}/{folder}", suffix=".html")
    for urls in files().values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("create temp table prec (county text, precinct text, geojson text)")
        for (election, kind), urls in files().items():
            count = 0
            with conn.cursor().copy("copy prec (county, precinct, geojson) from stdin") as copy:
                for url in urls:
                    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                        groups: dict[str, dict[str, str]] = {}
                        for name in archive.namelist():
                            stem, _, extension = name.rpartition(".")
                            groups.setdefault(stem.lower(), {})[extension.lower()] = name
                        parts = next(group for group in groups.values() if "shp" in group and "dbf" in group)
                        reader = shapefile.Reader(
                            shp=io.BytesIO(archive.read(parts["shp"])),
                            dbf=io.BytesIO(archive.read(parts["dbf"])),
                            encoding="latin-1",
                        )
                    fields = [field[0].upper() for field in reader.fields[1:]]
                    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                        row = dict(zip(fields, list(record), strict=True))
                        precinct = str(
                            next(row[field] for field in ("SRPREC", "MPREC", "PRECINCT") if field in row) or ""
                        ).strip()
                        county = next(
                            (row[field] for field in ("FIPS_CODE", "FIPS", "COUNTY_COD", "COUNTY") if field in row),
                            url.rsplit("/", 2)[1][1:],
                        )
                        if (
                            not precinct
                            or re.fullmatch(r"(?i)water|unassign\w*|z+", precinct)
                            or shape.shapeType == shapefile.NULL
                            or not shape.points
                        ):
                            continue
                        try:
                            geometry = shape.__geo_interface__
                        except shapefile.RingSamplingError:
                            geometry = {
                                "type": "Polygon",
                                "coordinates": [
                                    shape.points[start:end]
                                    for start, end in zip(shape.parts, [*shape.parts[1:], len(shape.points)])
                                ],
                            }
                        copy.write_row(
                            (
                                f"06{int(re.sub(r'\D', '', str(county))) % 1000:03d}",
                                precinct,
                                json.dumps(geometry),
                            )
                        )
                        count += 1
            frame = pl.DataFrame(
                conn.execute(
                    """with source as (
                         select county, precinct,
                                st_union(st_makevalid(st_setsrid(st_geomfromgeojson(geojson), 4326))) as geom
                         from prec group by county, precinct),
                       simplified as (
                         select county, precinct, geom as original,
                                st_coveragesimplify(geom, %s) over (partition by county) as geom
                         from source)
                       select county, precinct, encode(st_asewkb(geom), 'hex'),
                              encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                       from (select county, precinct, st_setsrid(st_multi(st_collectionextract(st_makevalid(
                                      case when geom is null or st_isempty(geom) then original else geom end), 3)),
                                    4326) as geom
                             from simplified) shapes
                       where not st_isempty(geom)""",
                    [TOLERANCE],
                ).fetchall(),
                schema=["county", "precinct", "geom", "point"],
                orient="row",
            )
            conn.execute("truncate prec")
            frame.sort("county", "precinct").write_parquet(PARSED / f"{election}_{kind}.parquet")
            print(f"{election} {kind}: {count} features, {frame.height} precincts", flush=True)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's precinct shapes for every statewide primary and general 2002–2024 from the "
            "Statewide Database, held out of releases until permission arrives, linked to MEDSL's and "
            "OpenElections' precincts",
            polity="US",
        )
        if conn.execute("select from scheme where code = 'us.restricted_precinct'").fetchone() is None:
            cs.add(
                "scheme",
                code="us.restricted_precinct",
                polity_id=cs.polity("US"),
                kind="precinct",
                rank=3,
                noun="precinct",
            )
        counties = {
            code: (area, text.rsplit(",", 1)[0])
            for code, area, text in conn.execute(
                """select c.code, a.id, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                   where a.scheme_code = 'us.county' and c.code like '06%'"""
            ).fetchall()
        }
        keys: dict[tuple[str, str], dict[str, str]] = {}
        for (election, kind), urls in files().items():
            year = 2000 + int(election[1:])
            stage = "primary" if election[0] == "P" else "general"
            title = f"{kind.upper()} precinct shapes, {year} {stage} election"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="academic",
                publisher=PUBLISHER,
                title=title,
                url=f"{SWDB}{election}/",
                license=LICENSE,
                attribution=PUBLISHER,
                notes=NOTES,
            )
            for url in [f"{SWDB}{election}/state/", *urls]:
                cs.artifact(raw.cached(SOURCE, url), source)
            start = DAYS[election]
            end = DAYS[ELECTIONS[ELECTIONS.index(election) + 1]] if election != ELECTIONS[-1] else DAYS["P26"]
            frame = (
                pl.read_parquet(PARSED / f"{election}_{kind}.parquet")
                .filter(pl.col("county").is_in(list(counties)))
                .with_columns(key=pl.col("county") + pl.col("precinct"))
                .with_columns(code=pl.format("swdb:{}:{}:{}", pl.lit(election.lower()), pl.lit(kind), "key"))
                .unique("code", keep="first", maintain_order=True)
            )
            ids = [str(cs.area("us.restricted_precinct", code)) for code in frame["code"]]
            keys[(election, kind)] = dict(zip(frame["key"], ids, strict=True))
            cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": "us.restricted_precinct"}))
            cs.frame(
                "area_code",
                pl.DataFrame({"area_id": ids, "code": frame["code"]}).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_parent",
                pl.DataFrame(
                    {"area_id": ids, "parent_id": [str(counties[county][0]) for county in frame["county"]]}
                ).with_columns(valid_from=pl.lit(None, pl.Date)),
            )
            cs.frame(
                "area_geometry",
                pl.DataFrame({"area_id": ids, "geom": frame["geom"], "label_point": frame["point"]}).with_columns(
                    valid_from=pl.lit(start),
                    valid_to=pl.lit(end),
                    quality=pl.lit("academic"),
                    source_id=pl.lit(str(source)),
                ),
            )
            cs.frame(
                "name",
                pl.DataFrame(
                    {
                        "entity_id": ids,
                        "text": [
                            f"Precinct {precinct} ({kind.upper()}, {year} {stage}), {counties[county][1]}"
                            for precinct, county in frame.select("precinct", "county").iter_rows()
                        ],
                    }
                ).with_columns(is_preferred=pl.lit(True), lang=pl.lit("en"), kind=pl.lit("official")),
            )
            print(f"{election} {kind}: {frame.height} precincts, {start} to {end}")
        units = conn.execute(
            """select u.id::text, u.code from reporting_unit u join changeset c on c.id = u.changeset_id
               where c.pipeline in ('us.medsl_precincts', 'us.oe_precincts')
                 and u.code ~ '^(2014|2016|2018|2020|2022|2024):CA:'"""
        ).fetchall()
        links = []
        tally: dict[int, list[int]] = {}
        for unit, code in units:
            parts = code.split(":")
            year, county, name = int(parts[0]), parts[2], parts[-1].strip()
            election = f"G{year % 100:02d}"
            embedded = name.split("_")[0] if "_" in name and name.startswith(county) else None
            candidates = (
                [("srprec", embedded), ("mprec", embedded)]
                if embedded is not None
                else [
                    (kind, county + bare)
                    for kind in ("mprec", "srprec")
                    for bare in dict.fromkeys([name, re.sub(r"\.0$", "", name), re.sub(r"A$", "", name)])
                ]
            )
            area = next(
                (keys[(election, kind)][key] for kind, key in candidates if key in keys.get((election, kind), {})),
                None,
            )
            tally.setdefault(year, [0, 0])[1] += 1
            if area is not None:
                tally[year][0] += 1
                links.append((unit, area, DAYS[election]))
        for year, (matched, total) in sorted(tally.items()):
            print(f"{year}: {matched} of {total} California units linked ({matched / total:.1%})")
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(links, schema=["unit_id", "area_id", "valid_from"], orient="row").with_columns(
                valid_to=pl.col("valid_from") + timedelta(days=1)
            ),
        )
        return cs.write()
