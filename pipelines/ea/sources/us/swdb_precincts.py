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
LICENSE = (
    "Pending: redistribution permission requested 2026-10-17; used on the archive owner's instruction until "
    "granted or refused"
)
NOTES = (
    "One layer per election is released onto us.precinct on the archive owner's instruction while permission "
    "is outstanding; the rest stay on us.restricted_precinct. Pull them if it is refused."
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
VEST = {"G16", "G20"}
OVERLAP = 0.02


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
        keys = {
            layer: set(
                pl.read_parquet(PARSED / f"{layer[0]}_{layer[1]}.parquet", columns=["county", "precinct"])
                .select(pl.col("county") + pl.col("precinct"))
                .to_series()
            )
            for layer in files()
        }
        matches = []
        for (code,) in conn.execute(
            """select u.code from reporting_unit u join changeset c on c.id = u.changeset_id
               where c.pipeline in ('us.medsl_precincts', 'us.oe_precincts')
                 and u.code ~ '^(2014|2016|2018|2020|2022|2024):CA:'"""
        ).fetchall():
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
            found = next(((kind, key) for kind, key in candidates if key in keys.get((election, kind), set())), None)
            if found is not None:
                matches.append((code, election, *found))
        matched = pl.DataFrame(matches, schema=["code", "election", "kind", "key"], orient="row")
        matched.write_parquet(PARSED / "matches.parquet")
        chosen = matched.group_by("election", county=pl.col("key").str.slice(0, 5), kind="kind").len()
        picks: dict[str, tuple[dict[str, str], str]] = {}
        layers = []
        for election in ELECTIONS:
            if election in VEST:
                continue
            kinds = sorted(kind for (other, kind) in keys if other == election)
            choice = dict(
                chosen.filter(pl.col("election") == election)
                .sort("len", "kind", descending=True)
                .unique("county", keep="first", maintain_order=True)
                .select("county", "kind")
                .iter_rows()
            )
            picks[election] = (choice, "srprec" if "srprec" in kinds else "mprec")
            for kind in kinds:
                layers.append(
                    pl.read_parquet(PARSED / f"{election}_{kind}.parquet", columns=["county", "precinct"])
                    .filter(pl.col("county").replace_strict(choice, default=picks[election][1]) == kind)
                    .select(election=pl.lit(election), kind=pl.lit(kind), key=pl.col("county") + pl.col("precinct"))
                )
        released = pl.concat(layers)
        conn.execute("create temp table swdb (election text, key text, day date, geom geometry)")
        conn.execute(
            """create temp table other as
               select d.day, g.geom, pc.code from unnest(%s::date[]) d(day)
               join area_geometry g on g.valid @> d.day join changeset c on c.id = g.changeset_id
               join area a on a.id = g.area_id and a.scheme_code = 'us.precinct'
               join area_parent p on p.area_id = a.id join area_code pc on pc.area_id = p.parent_id
               where c.pipeline <> %s and pc.code ~ '^06[0-9]{3}$'""",
            [[DAYS[election] for election in ELECTIONS if election not in VEST], SOURCE],
        )
        conn.execute("create index on other using gist (geom)")
        covered = conn.execute("select distinct code, day from other").fetchall()
        with conn.cursor().copy("copy swdb (election, key, day, geom) from stdin") as copy:
            for county, day in covered:
                election = next(election for election in ELECTIONS if DAYS[election] == day)
                choice, fallback = picks[election]
                kind = choice.get(county, fallback)
                for precinct, geom in (
                    pl.read_parquet(PARSED / f"{election}_{kind}.parquet", columns=["county", "precinct", "geom"])
                    .filter(pl.col("county") == county)
                    .select("precinct", "geom")
                    .iter_rows()
                ):
                    copy.write_row((election, county + precinct, day, geom))
        skipped = pl.DataFrame(
            conn.execute(
                """select s.election, s.key from swdb s join other o on o.day = s.day and st_intersects(o.geom, s.geom)
                   group by s.election, s.key
                   having sum(st_area(st_intersection(o.geom, s.geom))) > %s * min(st_area(s.geom))""",
                [OVERLAP],
            ).fetchall(),
            schema=["election", "key"],
            orient="row",
        )
        released = released.join(skipped, on=["election", "key"], how="anti")
        released.write_parquet(PARSED / "released.parquet")
        links = matched.join(released, on=["election", "kind", "key"]).select(
            "code",
            area=pl.format(
                "{}:CA:{}:{}",
                pl.col("election").replace_strict({election: str(day) for election, day in DAYS.items()}),
                pl.col("key").str.slice(0, 5),
                pl.col("key").str.slice(5),
            ),
        )
        links.write_parquet(PARSED / "links.parquet")
        print(f"{released.height} areas released, {skipped.height} left under other layers")
        for year, total in matched.group_by(pl.col("code").str.slice(0, 4)).len().sort("code").iter_rows():
            print(
                f"{year}: {links.filter(pl.col('code').str.starts_with(year)).height} of {total} matched units linked"
            )


def emit() -> Path:
    released = pl.read_parquet(PARSED / "released.parquet")
    matched = pl.read_parquet(PARSED / "matches.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's precinct shapes for every statewide primary and general 2002–2024 from the "
            "Statewide Database, one layer per election released onto us.precinct while permission is pending "
            "(VEST's stays the default for 2016 and 2020)",
            polity="US",
        )
        counties = {
            code: (area, text.rsplit(",", 1)[0])
            for code, area, text in conn.execute(
                """select c.code, a.id, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.kind = 'official' and n.is_preferred
                   where a.scheme_code = 'us.county' and c.code like '06%'"""
            ).fetchall()
        }
        days = {
            as_of
            for (as_of,) in conn.execute("select as_of from boundary_set where scheme_code = 'us.precinct'").fetchall()
        }
        restricted: dict[tuple[str, str, str], str] = {}
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
            day = DAYS[election]
            frame = (
                pl.read_parquet(PARSED / f"{election}_{kind}.parquet")
                .filter(pl.col("county").is_in(list(counties)))
                .with_columns(key=pl.col("county") + pl.col("precinct"))
                .unique("key", keep="first", maintain_order=True)
                .join(
                    released.filter((pl.col("election") == election) & (pl.col("kind") == kind)).select(
                        "key", public=pl.lit(True)
                    ),
                    on="key",
                    how="left",
                )
                .with_columns(
                    public=pl.col("public").fill_null(False),
                    old=pl.format("swdb:{}:{}:{}", pl.lit(election.lower()), pl.lit(kind), "key"),
                )
                .with_columns(
                    scheme=pl.when("public").then(pl.lit("us.precinct")).otherwise(pl.lit("us.restricted_precinct")),
                    code=pl.when("public")
                    .then(pl.format("{}:CA:{}:{}", pl.lit(str(day)), "county", "precinct"))
                    .otherwise("old"),
                )
            )
            if frame["public"].any() and day not in days:
                days.add(day)
                cs.add(
                    "boundary_set",
                    scheme_code="us.precinct",
                    vintage=str(day),
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                    as_of=day,
                )
            ids = [str(cs.area(scheme, code)) for scheme, code in frame.select("scheme", "code").iter_rows()]
            restricted |= {
                (election, kind, key): id
                for key, id, public in zip(frame["key"], ids, frame["public"], strict=True)
                if not public
            }
            cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": frame["scheme"]}))
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
                    valid_from=pl.lit(day),
                    valid_to=pl.lit(day + timedelta(days=1)),
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
            print(f"{election} {kind}: {frame.height} precincts, {frame['public'].sum()} on us.precinct")
        units = dict(
            conn.execute(
                """select u.code, u.id::text from reporting_unit u join changeset c on c.id = u.changeset_id
                   where c.pipeline in ('us.medsl_precincts', 'us.oe_precincts')
                     and u.code ~ '^(2014|2016|2018|2020|2022|2024):CA:'"""
            ).fetchall()
        )
        links = [
            (units[code], restricted[(election, kind, key)], DAYS[election])
            for code, election, kind, key in matched.iter_rows()
            if (election, kind, key) in restricted
        ]
        print(f"{len(links)} units linked to restricted areas as memberships")
        cs.frame(
            "reporting_unit_area",
            pl.DataFrame(links, schema=["unit_id", "area_id", "valid_from"], orient="row").with_columns(
                valid_to=pl.col("valid_from") + timedelta(days=1)
            ),
        )
        return cs.write()
