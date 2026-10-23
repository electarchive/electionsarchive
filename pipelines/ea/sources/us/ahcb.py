import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl
import shapefile

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.ahcb"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HOME = "https://digital.newberry.org/ahcb/"
COUNTIES = "https://digital.newberry.org/ahcb/downloads/gis/US_AtlasHCB_Counties_Gen0001.zip"
FIELDS = ["ID", "VERSION", "NAME", "FULL_NAME", "STATE_TERR", "FIPS", "CNTY_TYPE", "CHANGE", "CITATION"]
STATES: set[str] | None = None
NOT_COUNTY = re.compile(
    r"_nca\d*$|^dcs_|Res\.?$|Reserve|Indian lands|Western Reserve|Mare Island|Sec\. the|Terr\. Part|Disp\.|Claim"
    r"|Settlement|Province|Colony$|Mil Res|Spanish|Indian Terr\.|ANNEXATION|TERRITORY"
)
RECODED = {"02270": "02158", "46113": "46102", "12025": "12086"}
CENSUS_FROM = date(2001, 1, 1)
SEAM = {
    "51005": date(2001, 7, 1),
    "08001": date(2001, 11, 15),
    "08013": date(2001, 11, 15),
    "08059": date(2001, 11, 15),
    "08123": date(2001, 11, 15),
    "08014": date(2001, 11, 15),
    "02230": date(2007, 6, 20),
    "02105": date(2007, 6, 20),
    "02130": date(2008, 5, 19),
    "02198": date(2008, 5, 19),
    "02195": date(2008, 6, 1),
    "02275": date(2008, 6, 1),
    "51019": date(2013, 7, 1),
    "02063": date(2019, 1, 2),
    "02066": date(2019, 1, 2),
}
ENDED = {
    "51560": date(2001, 7, 1),
    "02232": date(2007, 6, 20),
    "02201": date(2008, 5, 19),
    "02280": date(2008, 6, 1),
    "51515": date(2013, 7, 1),
    "02261": date(2019, 1, 2),
}
CT_REGIONS_FROM = date(2022, 1, 1)
STATE_NAME = re.compile(r" (Colony|Republic)$")
DATES = """
with units as (select k from reporting_unit where scheme_code in ('us.county', 'us.former_county')),
sets as (select r.k, e.date_from from result_set r join election e on e.id = r.election_id
         join polity p on p.id = e.polity_id
         where e.date_from < %s and (p.iso_code = 'US' or p.iso_code like 'US-%%'))
select date_from, count(*) from sets
where exists (select from result_value v where v.result_set_k = sets.k and v.unit_k in (select k from units))
group by 1
"""


def fetch() -> None:
    raw.fetch(SOURCE, HOME, suffix=".html")
    raw.fetch(SOURCE, COUNTIES, suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw.cached(SOURCE, COUNTIES).path) as archive:
        members = {
            name.rsplit(".", 1)[1].lower(): name for name in archive.namelist() if name.endswith((".shp", ".dbf"))
        }
        with (
            archive.open(members["shp"]) as shp,
            archive.open(members["dbf"]) as dbf,
            connect() as conn,
        ):
            reader = shapefile.Reader(shp=shp, dbf=dbf, encoding="latin-1")
            conn.execute(
                """create temp table version (id text, version int, name text, full_name text, state_terr text,
                     fips text, cnty_type text, change text, citation text, start_date date, end_date date,
                     geojson text)"""
            )
            with conn.cursor().copy("copy version from stdin") as copy:
                for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                    if record is None or shape is None:
                        continue
                    row = record.as_dict()
                    copy.write_row(
                        (
                            *(str(row[field]).strip() if row[field] is not None else None for field in FIELDS),
                            row["START_DATE"],
                            row["END_DATE"],
                            json.dumps(shape.__geo_interface__),
                        )
                    )
            frame = pl.DataFrame(
                conn.execute(
                    """with shaped as (
                         select v.*, st_multi(st_collectionextract(st_makevalid(st_transform(
                                  st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from version v)
                       select id, version, name, full_name, state_terr, nullif(fips, '') as fips, cnty_type,
                              change, citation, start_date, end_date,
                              encode(st_asewkb(geom), 'hex') as geom,
                              encode(st_asewkb(st_pointonsurface(geom)), 'hex') as point,
                              md5(st_asbinary(geom)) as shape
                       from shaped where not st_isempty(geom) order by id, version"""
                ).fetchall(),
                schema=[field.lower() for field in FIELDS] + ["start", "end", "geom", "point", "shape"],
                orient="row",
            )
    if frame.select("id", "version").is_duplicated().any():
        raise ValueError("an AHCB ID repeats a version")
    frame.write_parquet(PARSED / "versions.parquet")
    print(f"{frame.height} versions of {frame['id'].n_unique()} counties", flush=True)


@dataclass(frozen=True)
class Version:
    ahcb: str
    version: int
    name: str
    full_name: str | None
    state_terr: str
    code: str | None
    cnty_type: str
    start: date
    end: date
    geom: str
    point: str
    shape: str


@dataclass
class Segment:
    lo: date
    hi: date
    key: tuple[str, ...]
    parent: str
    rows: list[Version]
    names: list[Version]


def proper(text: str) -> str:
    base = re.sub(r"\s*\(.*$", "", text).strip()
    return re.sub(
        r"[A-Za-z']+",
        lambda m: m[0] if re.match(r"^Mc[A-Z]", m[0]) else m[0][0].upper() + m[0][1:].lower(),
        base,
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "versions.parquet").filter(
        (pl.col("id").str.contains("_") | (pl.col("id") == "dcs"))
        & ~pl.col("id").str.contains(NOT_COUNTY.pattern)
        & ~pl.col("name").str.contains(NOT_COUNTY.pattern)
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="US county boundaries in force on each date from the Newberry Atlas of Historical County Boundaries, with the 2022 Census shapes from 2001",
            polity="US",
        )
        cs.manifest["scope"] = {
            "polity": "US",
            "replaces": {"area_geometry": ["us.census"], "area_parent": ["us.census"]},
        }
        newberry = cs.source("Newberry Library", "Atlas of Historical County Boundaries")
        cs.add(
            "source",
            id=newberry,
            kind="academic",
            publisher="Newberry Library",
            title="Atlas of Historical County Boundaries",
            url=HOME,
            license="CC BY-NC-SA 2.5 (as shipped with the 2016 files)",
            license_url="https://creativecommons.org/licenses/by-nc-sa/2.5/",
            attribution="Atlas of Historical County Boundaries, Dr. William M. Scholl Center for American History and Culture, Newberry Library",
        )
        for url in (HOME, COUNTIES):
            cs.artifact(raw.cached(SOURCE, url), newberry)

        states: dict[str, tuple[UUID, str, str]] = {
            code: (area, name, iso.removeprefix("US-"))
            for code, area, name, iso in conn.execute(
                """select c.code, a.id, n.text, p.iso_code from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   join polity_territory t on t.area_id = a.id join polity p on p.id = t.polity_id
                   where a.scheme_code = 'us.state'"""
            ).fetchall()
        }
        by_name = {name: code for code, (_, name, _) in states.items()}
        state_of_area = {area: code for code, (area, _, _) in states.items()}
        points = frame.select("id", "version", "point").to_dict(as_series=False)
        within: dict[tuple[str, int], str] = {
            (ahcb, version): code
            for ahcb, version, code in conn.execute(
                """select v.id, v.version, coalesce(
                     (select c.code from area_geometry g join area a on a.id = g.area_id and a.scheme_code = 'us.state'
                      join area_code c on c.area_id = a.id
                      where g.valid @> current_date and st_intersects(g.geom, v.p) limit 1),
                     (select c.code from area_geometry g join area a on a.id = g.area_id and a.scheme_code = 'us.state'
                      join area_code c on c.area_id = a.id
                      where g.valid @> current_date order by st_distance(g.geom, v.p) limit 1))
                   from (select id, version, st_geomfromewkb(decode(p, 'hex')) as p
                         from unnest(%s::text[], %s::int[], %s::text[]) as x(id, version, p)) v""",
                [points["id"], points["version"], points["point"]],
            ).fetchall()
        }
        census: dict[UUID, tuple[str, str, str, UUID, UUID | None]] = {
            area: (code, geom, point, source_id, parent)
            for area, code, geom, point, source_id, parent in conn.execute(
                """select a.id, c.code, encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex'),
                          g.source_id, p.parent_id
                   from area a join area_code c on c.area_id = a.id
                   join area_geometry g on g.area_id = a.id join changeset s on s.id = g.changeset_id
                   left join area_parent p on p.area_id = a.id and p.valid @> current_date
                   where a.scheme_code = 'us.county' and g.quality = 'official' and (
                     s.pipeline = 'us.census' and lower_inf(g.valid) and upper_inf(g.valid)
                     or s.pipeline = %s and (upper_inf(g.valid) or upper(g.valid) = %s and c.code like '090%%'))""",
                [SOURCE, CT_REGIONS_FROM],
            ).fetchall()
        }
        census_by_code = {row[0]: area for area, row in census.items()}
        short: dict[UUID, str] = dict(
            conn.execute(
                """select n.entity_id, n.text from name n join area a on a.id = n.entity_id
                   where a.scheme_code = 'us.county' and n.kind = 'short'"""
            ).fetchall()
        )

        versions: dict[UUID, list[Version]] = {}
        homes: dict[UUID, str] = {}
        for (ahcb,), rows in frame.sort("id", "version").group_by("id", maintain_order=True):
            last = rows.row(-1, named=True)
            home = within[last["id"], last["version"]]
            if STATES is not None and home not in STATES:
                continue
            code = RECODED.get(last["fips"], last["fips"]) if last["fips"] is not None else None
            if code is not None:
                area = cs.area("us.county", code)
                cs.add("external_id", entity_id=area, scheme="ahcb", value=ahcb)
            else:
                area = cs.key("ahcb", str(ahcb))
            homes[area] = home
            versions.setdefault(area, []).extend(
                Version(
                    ahcb=row["id"],
                    version=row["version"],
                    name=row["name"],
                    full_name=row["full_name"],
                    state_terr=row["state_terr"],
                    code=code,
                    cnty_type=row["cnty_type"],
                    start=row["start"],
                    end=row["end"],
                    geom=row["geom"],
                    point=row["point"],
                    shape=row["shape"],
                )
                for row in rows.iter_rows(named=True)
            )

        unions: list[tuple[int, str]] = []
        unioned: dict[int, int] = {}
        segments: dict[UUID, list[Segment]] = {}
        for area, rows in versions.items():
            cuts = sorted({row.start for row in rows} | {row.end + timedelta(days=1) for row in rows})
            run = segments.setdefault(area, [])
            for lo, hi in pairwise(cuts):
                covering = [row for row in rows if row.start <= lo <= row.end]
                if not covering:
                    continue
                key = tuple(sorted(row.shape for row in covering))
                first = covering[0]
                parent = by_name.get(STATE_NAME.sub("", first.state_terr)) or within[first.ahcb, first.version]
                if run and run[-1].key == key and run[-1].hi == lo and run[-1].parent == parent:
                    run[-1].hi = hi
                    run[-1].names.append(first)
                    continue
                if len(covering) > 1:
                    unioned[id(covering)] = len(unioned)
                    unions += [(unioned[id(covering)], row.geom) for row in covering]
                run.append(Segment(lo=lo, hi=hi, key=key, parent=parent, rows=covering, names=[first]))
        joined: dict[int, tuple[str, str]] = {
            key: (geom, point)
            for key, geom, point in conn.execute(
                """select k, encode(st_asewkb(g), 'hex'), encode(st_asewkb(st_pointonsurface(g)), 'hex')
                   from (select k, st_multi(st_collectionextract(st_makevalid(
                           st_union(st_geomfromewkb(decode(h, 'hex')))), 3)) as g
                         from unnest(%s::int[], %s::text[]) as x(k, h) group by k) u""",
                [[key for key, _ in unions], [geom for _, geom in unions]],
            ).fetchall()
        }

        problems: list[str] = []
        restaged: set[UUID] = set()
        counts = {"areas": 0, "new": 0, "shapes": 0, "parents": 0, "names": 0}
        for area, run in segments.items():
            code = run[-1].rows[0].code
            current = census.get(area)
            if current is not None:
                seam = SEAM.get(current[0], CENSUS_FROM)
                if run[-1].hi == CENSUS_FROM:
                    run[-1].hi = seam
                else:
                    problems.append(f"{current[0]}: Census shape, but AHCB ends {run[-1].hi}")
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=seam,
                    valid_to=CT_REGIONS_FROM if current[0].startswith("090") else None,
                    geom=current[1],
                    label_point=current[2],
                    quality="official",
                    source_id=current[3],
                )
                restaged.add(area)
            elif run[-1].hi == CENSUS_FROM:
                if code is not None and code in ENDED:
                    run[-1].hi = ENDED[code]
                else:
                    problems.append(f"{code or run[-1].rows[0].ahcb}: in AHCB on 2000-12-31, no Census shape")
            new = area in cs.minted
            counts["areas"] += 1
            counts["new"] += new
            if new:
                cs.add("area", id=area, scheme_code="us.county")
                if code is not None:
                    cs.add("area_code", area_id=area, code=code, valid_from=run[0].lo, valid_to=run[-1].hi)
            for segment in run:
                geom, point = (
                    joined[unioned[id(segment.rows)]]
                    if len(segment.rows) > 1
                    else (segment.rows[0].geom, segment.rows[0].point)
                )
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=segment.lo,
                    valid_to=segment.hi,
                    geom=geom,
                    label_point=point,
                    quality="academic",
                    source_id=newberry,
                )
                counts["shapes"] += 1

            if new or any(segment.parent != homes[area] for segment in run):
                parents: list[tuple[str, date | None, date | None]] = []
                for segment in run:
                    if parents and parents[-1][0] == segment.parent and parents[-1][2] == segment.lo:
                        parents[-1] = (segment.parent, parents[-1][1], segment.hi)
                    else:
                        parents.append((segment.parent, segment.lo, segment.hi))
                parents[0] = (parents[0][0], None, parents[0][2])
                if current is not None:
                    parents[-1] = (parents[-1][0], parents[-1][1], None)
                    if current[4] is not None and parents[-1][0] != state_of_area.get(current[4]):
                        problems.append(f"{current[0]}: last AHCB state {parents[-1][0]} is not its parent today")
                for parent, lo, hi in parents:
                    cs.add("area_parent", area_id=area, parent_id=states[parent][0], valid_from=lo, valid_to=hi)
                    counts["parents"] += 1

            labels: list[tuple[str, date, date]] = []
            for segment in run:
                for row in segment.names:
                    base = proper(row.full_name or row.name)
                    kind = "" if base.endswith(row.cnty_type) else f" {row.cnty_type}"
                    label = f"{base}{kind}, {states[segment.parent][2]}"
                    if labels and labels[-1][0] == label:
                        labels[-1] = (label, labels[-1][1], segment.hi)
                    else:
                        labels.append((label, max(row.start, segment.lo), segment.hi))
            last = run[-1].names[-1]
            if new:
                cs.add("name", entity_id=area, text=labels[-1][0], lang="en", kind="official", is_preferred=True)
                cs.add(
                    "name",
                    entity_id=area,
                    text=proper(last.full_name or last.name),
                    lang="en",
                    kind="short",
                    is_preferred=False,
                )
                labels = labels[:-1]
            today = re.sub(r"[^a-z]", "", f"{short.get(area, '')}{last.cnty_type}".lower())
            for label, lo, hi in labels:
                if not new and re.sub(r"[^a-z]", "", label.split(",")[0].lower()) == today:
                    continue
                cs.add(
                    "name",
                    entity_id=area,
                    text=label,
                    lang="en",
                    kind="historical",
                    is_preferred=False,
                    valid_from=lo,
                    valid_to=hi,
                    source_id=newberry,
                )
                counts["names"] += 1

        for code, seam in SEAM.items():
            area = census_by_code.get(code)
            if area is not None and area not in restaged and (STATES is None or code[:2] in STATES):
                _, geom, point, source_id, _ = census[area]
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=seam,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=source_id,
                )

        counties = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'us.county'"
            ).fetchall()
        )
        placed: dict[tuple[str, str], set[UUID]] = {}
        for row in frame.iter_rows(named=True):
            code = RECODED.get(row["fips"], row["fips"]) if row["fips"] is not None else None
            area = (
                counties.get(code) or uuid5(NAMESPACE, f"area:us.county@None\x1f{code}")
                if code is not None
                else uuid5(NAMESPACE, f"external_id:ahcb\x1f{row['id']}")
            )
            parent = by_name.get(STATE_NAME.sub("", row["state_terr"])) or within[row["id"], row["version"]]
            st = states[parent][2]
            placed.setdefault((st, re.sub(r"[^a-z]", "", proper(row["name"]).lower())), set()).add(area)
            if code is not None:
                placed.setdefault((st, code), set()).add(area)
        loaded = set(counties.values()) | set(segments)
        matched: list[str] = []
        for unit, code, polity, parent, counted, relation, unit_area, kind, valid_from, valid_to in conn.execute(
            """select id, code, polity_id, parent_id, counted_with_id, area_relation::text, area_id, kind::text,
                      valid_from, valid_to
               from reporting_unit where scheme_code = 'us.former_county' order by code"""
        ).fetchall():
            st, name = code.split(":", 1)
            base = name.removesuffix("special")
            found = placed.get((st, RECODED.get(base, base))) or placed.get((st, base.removesuffix("county"))) or set()
            if len(found) > 1 and unit_area in found:
                found = {unit_area}
            if len(found) != 1 or not found <= loaded:
                continue
            (area,) = found
            target = "within" if name.endswith("special") else "equals"
            if (area, target) == (unit_area, relation):
                continue
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=polity,
                scheme_code="us.former_county",
                code=code,
                parent_id=parent,
                counted_with_id=counted,
                area_relation=target,
                area_id=area,
                kind=kind,
                valid_from=valid_from,
                valid_to=valid_to,
            )
            matched.append(code)

        if STATES is None:
            cuts = sorted(
                {*frame["start"].to_list(), *(end + timedelta(days=1) for end in frame["end"].to_list())}
                | {*SEAM.values(), *ENDED.values(), CENSUS_FROM, CT_REGIONS_FROM}
            )
            busiest: dict[int, tuple[int, date]] = {}
            for day, sets in conn.execute(DATES, [CENSUS_FROM]).fetchall():
                if sets >= busiest.get(day.year, (0, day))[0]:
                    busiest[day.year] = (sets, day)
            for year in range(CENSUS_FROM.year, date.today().year + 1):
                november = date(year, 11, 2)
                busiest[year] = (0, november + timedelta(days=(1 - november.weekday()) % 7))
            chosen: list[tuple[int, date]] = []
            for year in sorted(busiest):
                day = busiest[year][1]
                if not chosen or any(chosen[-1][1] < cut <= day for cut in cuts):
                    chosen.append((year, day))
            for index, (year, day) in enumerate(chosen):
                cs.add(
                    "boundary_set",
                    id=uuid5(NAMESPACE, f"boundary_set\x1fus.county@{day.isoformat()}"),
                    scheme_code="us.county",
                    vintage=day.isoformat(),
                    valid_from=date(year, 1, 1) if index else None,
                    valid_to=date(chosen[index + 1][0], 1, 1) if index + 1 < len(chosen) else None,
                    as_of=day,
                )
            print(f"{len(chosen)} boundary sets")

        print(counts, f"{len(matched)} former counties placed: {' '.join(matched)}", sep="\n")
        for problem in problems:
            print(problem)
        return cs.write()
