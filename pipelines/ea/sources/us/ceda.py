import io
import itertools
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ceda"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ACCESS = "https://scholars.csus.edu/view/fileRedirect?instCode=01CALS_USL&filePid={}&download=true"
CODEBOOK = ACCESS.format("13306594360001671")
FILES = {year: ACCESS.format(f"1330659{437 + 2025 - year}0001671") for year in range(1996, 2026)}
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_06_place_500k.zip"
SCHOOLS = [
    f"https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_06_{kind}_500k.zip" for kind in ("unsd", "elsd", "scsd")
]
ARCHIVES = {
    "06097": ("https://sonomaca.elstats3.civera.com", "Sonoma County Registrar of Voters", "SONOMA"),
    "06013": ("https://pastresults.contracostavote.gov", "Contra Costa County Elections Division", "CONTRA COSTA"),
}
ARCHIVE_TITLE = "Election database"
OFFICES = (
    "County",
    "Supervisor",
    "Director",
    "School",
    "Board of Education",
    "Governing Board",
    "Trustee",
    "District Attorney",
    "Sheriff",
)
PUBLISHER = "California State University, Sacramento, Institute for Social Research"
TITLE = "California Elections Data Archive"
PARTS = (
    "ASSESSOR|AUDITOR|CONTROLLER|TREASURER|TAX COLLECTOR|CLERK|RECORDER|REGISTRAR(?: OF VOTERS)?|CORONER|MARSHAL"
    "|PUBLIC ADMINISTRATOR|PUBLIC GUARDIAN|PUBLIC DEFENDER|SURVEYOR|SUPERINTENDENT OF SCHOOLS|SHERIFF"
    "|DISTRICT ATTORNEY|COUNTY ADMINISTRATOR|AGRICULTURAL COMMISSIONER|ROAD COMMISSIONER"
)
COUNTY = re.compile(rf"(?:COUNTY )?(?:{PARTS})(?: ?[-/] ?(?:{PARTS}))*")
CITY = {
    "MAYOR": ("mayor", "Mayor", False),
    "CITY TREASURER": ("city-treasurer", "City Treasurer", False),
    "CITY CLERK": ("city-clerk", "City Clerk", False),
    "CITY ATTORNEY": ("city-attorney", "City Attorney", False),
    "CITY AUDITOR": ("city-auditor", "City Auditor", False),
    "CITY CONTROLLER": ("city-controller", "City Controller", False),
    "CITY PROSECUTOR": ("city-prosecutor", "City Prosecutor", False),
    "CITY CLERK-ASSESSOR": ("city-clerk-assessor", "City Clerk-Assessor", False),
    "POLICE CHIEF": ("police-chief", "Police Chief", False),
    "RENT CONTROL BOARD": ("rent-control-board", "Rent Control Board", True),
}
FILL = {"city", "joint", "union", "school", "district", "elementary", "high", "unified", "the", "of", "schools"}
SKIPPED = re.compile(r"(?i)-\s*(?:R|P|RECALL|PROPOSED)$|\((?:PROPOSED|RECALL)\)|\bRECALL\b|\bPROPOSED\b")
METHOD = (
    "Votes and winners are as compiled by the California Elections Data Archive from the county election offices; "
    "for a district that crosses county lines, the votes are its sums across the counties. Write-in votes are one "
    "total per contest."
)
PRECINCT = (
    "Where the county's election database adds up to those totals for every candidate, the votes are the county's "
    "own precinct results instead, summed across voting methods."
)
RCV = "This contest was decided by ranked-choice voting; the archive shows the votes as the data archive reports them."
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def database(county: str, year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "no ballot questions", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": office} for office in OFFICES]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return f"{ARCHIVES[county][0]}/api/download_search.csv?" + urlencode(
        {"search": json.dumps(search, separators=(",", ":"))}
    )


def fetch() -> None:
    raw.fetch(SOURCE, CODEBOOK, suffix=".pdf")
    for county in ARCHIVES:
        for year in range(2000, 2026):
            raw.fetch(SOURCE, database(county, year), suffix=".csv")
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for url in [PLACES, *SCHOOLS]:
        raw.fetch(SOURCE, url, suffix=".zip")


def _kind(text: str) -> str | None:
    text = text.lower()
    if "unified" in text:
        return "unified"
    if re.search(r"\bhigh\b", text):
        return "high"
    return "elementary" if "elementary" in text else None


def _core(text: str) -> str:
    text = re.sub(r"\bmt\.? ", "mount ", text.lower())
    return " ".join(word for word in re.findall(r"[a-z0-9]+", text) if word not in FILL)


def _city(text: str) -> str:
    text = re.sub(r"(?i)^city of |\s+city$", "", text.strip())
    return re.sub(r"[^a-z0-9]", "", text.lower())


def parse() -> None:
    frames = []
    for year, url in FILES.items():
        frame = pl.read_excel(raw.cached(SOURCE, url).path, sheet_name=f"Candidates {year}", infer_schema_length=0)
        frames.append(frame.rename({column: column.upper() for column in frame.columns}))
    frame = (
        pl.concat(frames, how="diagonal")
        .select(
            pl.col("MULTI_RACEID").alias("race"),
            pl.col("MULTI_CANDID").alias("candidate"),
            pl.col("JUR").alias("jur"),
            pl.col("CSD").alias("csd"),
            pl.col("CNTYNAME").str.strip_chars().str.to_uppercase().alias("county"),
            pl.col("PLACE").str.strip_chars().alias("place"),
            pl.col("OFFICE").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars().alias("office"),
            pl.col("AREA").str.strip_chars().alias("area"),
            (pl.col("TERM").str.to_uppercase() == "SHORT").alias("remainder"),
            pl.col("VOTE#").cast(pl.Float64).cast(pl.Int16).alias("magnitude"),
            pl.concat_str(
                pl.col("FIRST").str.strip_chars(), pl.col("LAST").str.strip_chars(), separator=" ", ignore_nulls=True
            ).alias("name"),
            (pl.col("INCUMB") == "Y").alias("incumbent"),
            pl.col("INDIVTOTAL_VOTES").cast(pl.Float64).cast(pl.Int64).alias("votes"),
            pl.col("TOTAL_WRITEIN").cast(pl.Float64, strict=False).cast(pl.Int64).alias("writeins"),
            pl.col("NEWELECTED").alias("elected"),
            (pl.col("RCV").fill_null("0") != "0").alias("rcv"),
            pl.col("DATE").str.slice(0, 10).str.to_date().alias("day"),
        )
        .unique(["race", "candidate"], keep="first", maintain_order=True)
        .filter(
            pl.col("csd") == "0",
            ~pl.col("office").str.contains(SKIPPED.pattern),
            ~pl.col("place").str.contains(SKIPPED.pattern),
        )
    )
    shapes: list[tuple[str, str, str, str]] = []
    for url in [PLACES, *SCHOOLS]:
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            if record is None or shape is None or (url == PLACES and record["LSAD"] not in ("25", "43")):
                continue
            shapes.append(
                (
                    ("" if url == PLACES else "sd") + str(record["GEOID"]),
                    str(record["NAME"]),
                    str(record["LSAD"]) if url == PLACES else "SD",
                    json.dumps(shape.__geo_interface__),
                )
            )
    with connect() as conn:
        counties = dict(
            conn.execute(
                """select upper(regexp_replace(n.text, ' County$', '')), c.code from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
                   where a.scheme_code = 'us.county' and c.code like '06%%'"""
            ).fetchall()
        )
        places = pl.DataFrame(
            conn.execute(
                """with shapes as (
                     select code, name, lsad, st_multi(st_collectionextract(st_makevalid(st_transform(
                       st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, name, lsad, geojson)
                   ), counties as materialized (
                     select c.code, g.geom from area a join area_code c on c.area_id = a.id
                     join area_geometry g on g.area_id = a.id
                     where a.scheme_code = 'us.county' and c.code like '06%%'
                   )
                   select s.code, s.name, s.lsad, encode(st_asewkb(s.geom), 'hex'),
                     encode(st_asewkb(st_pointonsurface(s.geom)), 'hex'),
                     array(select k.code from counties k
                           where k.geom && s.geom and st_area(st_intersection(k.geom, s.geom)) > 0.01 * st_area(s.geom))
                   from shapes s""",
                [
                    [code for code, _, _, _ in shapes],
                    [text for _, text, _, _ in shapes],
                    [lsad for _, _, lsad, _ in shapes],
                    [geojson for _, _, _, geojson in shapes],
                ],
            ).fetchall(),
            schema=["code", "name", "lsad", "geom", "point", "counties"],
            orient="row",
        )
    cities: dict[str, str] = {}
    schools: dict[str, list[tuple[str | None, str, list[str]]]] = {}
    for code, text, lsad, _, _, within in places.iter_rows():
        if lsad == "SD":
            schools.setdefault(_core(text), []).append((_kind(text), code, within))
        else:
            for part in re.findall(r"[^()]+", text):
                cities[_city(part)] = code
    labels = []
    missing: dict[str, int] = {}
    for jur, office, place, county in frame.select("jur", "office", "place", "county").unique().iter_rows():
        found = None
        suffix = None
        parts = re.split(r" ?- ?", place, maxsplit=1)
        tries = [(place, None)]
        if len(parts) == 2 and (_kind(parts[0]) is not None or re.fullmatch(r"(?i)at[- ]large", parts[1])):
            tries.append((parts[0], parts[1]))
        fips = counties.get(county)
        if jur == "1" and fips is not None:
            if office == "COUNTY SUPERVISOR":
                found = (fips, "board-of-supervisors", "Board of Supervisors", True)
            elif office == "MAYOR" and fips == "06075":
                found = ("0667000", "mayor", "Mayor", False)
            elif office == "SUPERIOR JUDGE":
                found = (fips, "superior-court-judge", "Superior Court Judge", False)
            elif COUNTY.fullmatch(
                text := office.replace("COLLEECTOR", "COLLECTOR")
                .replace("ADMINSTRATOR", "ADMINISTRATOR")
                .replace("MARSHALL", "MARSHAL")
            ):
                text = re.sub(r" ?([-/]) ?", "-", text.removeprefix("COUNTY "))
                key = next(
                    (base for base in ("sheriff", "district-attorney") if slug(text).startswith(base)), slug(text)
                )
                found = (fips, key, re.sub(r"\bOf\b", "of", "-".join(part.title() for part in text.split("-"))), False)
        elif jur == "2" and (office in CITY or re.fullmatch(r"(?:CITY|TOWN) COUNCIL", office)):
            for text, suffix in tries:
                if (code := cities.get(_city(text))) is not None:
                    found = (code, *CITY[office]) if office in CITY else (code, "council", office.title(), True)
                    break
            else:
                missing[f"city {place}"] = 1
        elif (
            jur == "3"
            and re.fullmatch(r"(?:SCHOOL )?BOARD MEMBER", office)
            and not re.search(r"(?i)college|\bCCD\b", place)
        ):
            if re.search(r"(?i)(?:county|co\.) (?:office|board) of education", place):
                if fips is not None:
                    found = (fips, "board-of-education", "Board of Education", True)
                    suffix = parts[1] if len(parts) == 2 else None
            else:
                for text, suffix in tries:
                    options = [option for option in schools.get(_core(text), []) if fips in option[2]]
                    kind = _kind(text)
                    typed = [option for option in options if option[0] == kind] if kind else options
                    typed = typed or [option for option in options if option[0] is None]
                    if len(typed) == 1:
                        found = (typed[0][1], "board-of-education", "Board of Education", True)
                        break
                else:
                    missing[f"school {place}"] = 1
        if found is not None:
            code, key, title, body = found
            labels.append(
                {
                    "jur": jur,
                    "office": office,
                    "place": place,
                    "county": county,
                    "code": code,
                    "key": key,
                    "title": title,
                    "body": body,
                    "suffix": suffix,
                }
            )
    frame = (
        frame.join(pl.DataFrame(labels, infer_schema_length=None), on=["jur", "office", "place", "county"])
        .with_columns(area=pl.coalesce("area", "suffix"))
        .drop("suffix")
        .with_columns(pl.col("race").min().over("code", "key", "day", "area", "remainder", "magnitude"))
    )
    print(f"kept {frame['race'].n_unique()} races; unmatched: {len(missing)} places")
    parts = []
    for county, (_, _, jurisdiction) in ARCHIVES.items():
        for year in range(2000, 2026):
            part = pl.read_csv(
                raw.cached(SOURCE, database(county, year)).path, infer_schema=False, truncate_ragged_lines=True
            )
            if part.height == 0:
                continue
            parts.append(
                part.filter(
                    pl.col("division_type") == "Precinct",
                    pl.col("candidate_name").is_not_null(),
                    ~pl.col("vote_channel").fill_null("").str.ends_with("- Total"),
                    ~pl.col("candidate_name").str.contains(
                        r"(?i)^total |^registered voters$|^unknown candidate$|^blank"
                        r"|^(?:over|under)[ /]*(?:votes?)?(?:[ /]*(?:over|under)[ /]*votes?)?$"
                    ),
                ).select(
                    pl.lit(county).alias("fips"),
                    pl.col("election_date").str.slice(0, 10).str.to_date().alias("day"),
                    pl.col("contest_id").alias("contest"),
                    pl.col("candidate_name").str.strip_chars().alias("ballot"),
                    pl.concat_str(
                        pl.lit(f"{year}:CA:{county}:{jurisdiction}:"),
                        pl.col("division_name").str.strip_chars().str.to_uppercase(),
                    ).alias("unit"),
                    pl.col("votes").cast(pl.Int64, strict=False),
                )
            )
    reported = (
        pl.concat(parts)
        .filter(~pl.col("votes").is_null().any().over("fips", "day", "contest"))
        .with_columns(write=pl.col("ballot").str.contains(r"(?i)^write"))
        .group_by("fips", "day", "contest", "ballot", "write", "unit")
        .agg(pl.col("votes").sum())
    )
    sums = reported.group_by("fips", "day", "contest", "ballot", "write").agg(pl.col("votes").sum())
    signature = pl.col("votes").cast(pl.String).sort().str.join("|")
    names = {jurisdiction: county for county, (_, _, jurisdiction) in ARCHIVES.items()}
    matched = (
        frame.group_by("race")
        .agg(
            pl.col("county").n_unique().alias("counties"),
            pl.first("county", "day", "writeins"),
            signature.alias("signature"),
        )
        .filter(pl.col("counties") == 1, pl.col("county").is_in(list(names)))
        .with_columns(pl.col("county").replace_strict(names).alias("fips"))
        .filter(pl.len().over("fips", "day", "signature") == 1)
        .join(
            sums.group_by("fips", "day", "contest")
            .agg(
                pl.col("votes").filter(~pl.col("write")).cast(pl.String).sort().str.join("|").alias("signature"),
                pl.col("votes").filter(pl.col("write")).sum().alias("writes"),
            )
            .filter(pl.len().over("fips", "day", "signature") == 1),
            on=["fips", "day", "signature"],
        )
        .filter(pl.col("writes") == pl.col("writeins"))
        .select("race", "contest")
    )
    options = pl.concat(
        [
            frame.select("race", "candidate", "votes")
            .join(matched, on="race")
            .filter(pl.len().over("race", "votes") == 1)
            .join(
                sums.filter(~pl.col("write")).filter(pl.len().over("contest", "votes") == 1),
                on=["contest", "votes"],
            )
            .select("race", "contest", "candidate", "ballot"),
            sums.filter(pl.col("write"))
            .join(matched, on="contest")
            .select("race", "contest", pl.lit("write-ins").alias("candidate"), "ballot"),
        ]
    )
    complete = (
        frame.group_by("race")
        .agg(pl.len().alias("candidates"))
        .join(
            options.filter(pl.col("candidate") != "write-ins").group_by("race").agg(pl.len().alias("found")),
            on="race",
        )
        .filter(pl.col("candidates") == pl.col("found"))
        .select("race")
    )
    precincts = (
        reported.join(options.join(complete, on="race"), on=["contest", "ballot"])
        .group_by("race", "candidate", "fips", "unit")
        .agg(pl.col("votes").sum())
    )
    print(f"{precincts['race'].n_unique()} races by precinct")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    precincts.write_parquet(PARSED / "precincts.parquet")
    places.filter(pl.col("code").is_in(frame["code"].implode())).write_parquet(PARSED / "places.parquet")
    (PARSED / "missing.txt").write_text("\n".join(sorted(missing)))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    places = pl.read_parquet(PARSED / "places.parquet")
    grouped = {race: rows for (race,), rows in pl.read_parquet(PARSED / "precincts.parquet").group_by("race")}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's county, city, and school district elections, 1996–2025",
            polity="US-CA",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="academic",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://hdl.handle.net/10211.3/210187",
                license=None,
            )
        cs.artifact(raw.cached(SOURCE, CODEBOOK), source)
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        counted: dict[str, UUID] = {}
        stored: set[str] = set()
        for county, (url, publisher, _) in ARCHIVES.items():
            counted[county] = cs.source(publisher, ARCHIVE_TITLE)
            cs.add(
                "source",
                id=counted[county],
                kind="official_body",
                publisher=publisher,
                title=ARCHIVE_TITLE,
                url=url,
                license="Public record",
            )
            for year in range(2000, 2026):
                if (artifact := raw.cached(SOURCE, database(county, year))).storage_key not in stored:
                    stored.add(artifact.storage_key)
                    cs.artifact(artifact, counted[county])
        wards = {
            entity
            for (entity,) in conn.execute(
                """select distinct r.contest_id from result_set r join changeset x on x.id = r.changeset_id
                   where x.pipeline = 'us.city_precincts'"""
            ).fetchall()
        }
        sets = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = %s", [f"{SOURCE}.result_set"]
            ).fetchall()
        }
        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and code like '20__:CA:06%%'"
            ).fetchall()
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '06%%'"
            ).fetchall()
        }
        minted: dict[str, date] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for url in [PLACES, *SCHOOLS]:
            cs.artifact(raw.cached(SOURCE, url), census)
        state = cs.polity("US-CA")
        polities: dict[str, tuple[UUID, str, str, str]] = {}
        for code, polity, text, path, kind in conn.execute(
            """select c.code, t.polity_id, n.text, s.path, p.kind from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and c.code like '06%%'"""
        ).fetchall():
            polities[f"sd{code}" if kind == "school_district" else code] = (
                polity,
                text,
                path,
                {"municipality": "city", "school_district": "school district"}.get(kind, "county"),
            )
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.path like 'us/ca/%%' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        held = set(
            conn.execute(
                """select x.value, extract(year from e.date_from)::int, extract(month from e.date_from)::int
                   from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join source s on s.id = r.source_id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body')
                   where x.value like 'CA:%%' and s.title <> %s""",
                [TITLE],
            ).fetchall()
        )
        held |= {
            ("CA:06075:board-of-supervisors", year, month)
            for value, year, month in held
            if value == "CA:0667000:council"
        }
        kept = dict(
            conn.execute(
                """select c.id, c.electoral_system_id from contest c join changeset x on x.id = c.changeset_id
                   join result_set r on r.contest_id = c.id join changeset y on y.id = r.changeset_id
                   where y.pipeline = %s and x.pipeline <> %s""",
                [SOURCE, SOURCE],
            ).fetchall()
        )
        parents = dict(
            conn.execute(
                """select x.value, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ '^[0-9-]+:CA(:primary)?$'"""
            ).fetchall()
        )
        for code, text, lsad, geom, point, within in places.iter_rows():
            if code in polities:
                continue
            school = code.startswith("sd")
            geoid, scheme = code.removeprefix("sd"), "us.school_district" if school else "us.place"
            polity = cs.key("us.polity", f"school:{geoid}" if school else f"place:{code}")
            path = f"us/ca/{slug(text)}"
            if path in taken:
                path = (
                    f"us/ca/{slug(text)}-{slug(polities[within[0]][1])}"
                    if school
                    else f"us/ca/{slug(text)}-{'town' if lsad == '43' else 'city'}"
                )
            if path in taken:
                raise ValueError(f"{text} would take {path}")
            taken.add(path)
            polities[code] = (polity, text, path, "school district" if school else "town" if lsad == "43" else "city")
            cs.add("polity", id=polity, kind="school_district" if school else "municipality", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area(scheme, geoid)
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=geoid, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "06"))
            name(area, f"{text}, CA")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
            if (unit := cs.unit(scheme, geoid)) in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=geoid,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "irv", "two_round")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, block, irv, and two-round systems must already exist")
        previous: dict[str, str] = {}
        races = (
            frame.group_by("race")
            .agg(
                pl.first("code", "key", "area", "remainder", "day"),
                advanced=(pl.col("elected") == "3").any(),
            )
            .filter(
                ~pl.struct("code", "key", "day").map_elements(
                    lambda row: (f"CA:{row['code']}:{row['key']}", row["day"].year, row["day"].month) in held,
                    pl.Boolean,
                )
            )
            .sort("day", "race")
        )
        for _, group in races.group_by(
            "code", "key", "area", "remainder", pl.col("day").dt.year(), maintain_order=True
        ):
            for first_round, runoff in itertools.pairwise(group.iter_rows(named=True)):
                if first_round["advanced"]:
                    previous[runoff["race"]] = first_round["race"]
        majority = set(previous) | set(previous.values())

        owners: set[UUID] = set()
        elections: set[UUID] = set()
        districts: set[str] = set()
        paths: set[str] = set()
        values: list[tuple[str, str, str, int]] = []
        for (race,), rows in frame.sort("race", "votes", descending=[False, True]).group_by(
            "race", maintain_order=True
        ):
            first = rows.row(0, named=True)
            code, key, day = first["code"], first["key"], first["day"]
            if (f"CA:{code}:{key}", day.year, day.month) in held:
                continue
            polity, place, path, word = polities[code]
            title = place.removeprefix("City of ")
            geoid = code.removeprefix("sd")
            scheme = {5: "us.county", 7: "us.place"}.get(len(code), "us.school_district")
            office = f"Mayor of {title}" if key == "mayor" else f"{title} {first['title']}"
            owner = cs.key("us.body" if first["body"] else "us.office", f"CA:{code}:{key}")
            if owner in cs.minted and owner not in owners:
                if first["body"]:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                else:
                    cs.add("office", id=owner, polity_id=polity, kind="mayor" if key == "mayor" else "other")
                name(owner, office)
            owners.add(owner)

            parent_key = next((value for value in (f"{day}:CA", f"{day}:CA:primary") if value in parents), None)
            stage = "local" if parent_key is None else parents[parent_key]
            parent = cs.key("us.election", parent_key or f"{day}:CA")
            if parent_key is None and parent not in elections:
                elections.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=state,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} California local elections")
                cs.add("slug", path=f"us/ca/{day}-local", entity_id=parent)
            election = cs.key("us.election", f"{day}:CA:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {title} {stage} election")
                cs.add("slug", path=f"{path}/{day}-{stage}", entity_id=election)

            area_text = first["area"]
            seat = None
            if area_text:
                label = area_text.lstrip("0") or area_text
                seat = (
                    f"District {label}"
                    if key == "board-of-supervisors"
                    else f"Trustee Area {label}"
                    if key == "board-of-education"
                    else f"Office {label}"
                    if key == "superior-court-judge"
                    else f"Area {label}"
                )
            partial = seat is not None and key in ("board-of-supervisors", "council", "board-of-education")
            whole = cs.area(scheme, geoid)
            unit = cs.unit(scheme, geoid)
            area = whole
            if partial:
                district = f"06:{code}:{key}:{slug(seat)}"
                area = cs.area("us.local_district", district)
                unit = cs.unit("us.local_district", district)
                if area in cs.minted and district not in districts:
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    name(area, f"{office}, {seat}")
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=district,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                    )
                districts.add(district)
            contest_key = f"ceda:{race}"
            contest = cs.key("us.contest", contest_key)
            magnitude = first["magnitude"]
            rcv = rows["rcv"].any()
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if first["body"] else "office",
                office_id=None if first["body"] else owner,
                body_id=owner if first["body"] else None,
                district_area_id=area,
                magnitude=magnitude,
                round_number=2 if race in previous else 1,
                previous_round_id=cs.key("us.contest", f"ceda:{previous[race]}") if race in previous else None,
                electoral_system_id=kept.get(contest)
                or systems[
                    "irv"
                    if rcv and magnitude == 1
                    else "block"
                    if magnitude != 1
                    else "two_round"
                    if race in majority or (len(code) == 5 and key != "board-of-education")
                    else "fptp"
                ],
                default_result_kind="counted",
                term="remainder" if first["remainder"] else "full",
            )
            name(
                contest,
                (f"{day.year} {title} mayoral election" if key == "mayor" else f"{day.year} {office} election")
                + (f", {seat}" if seat else "")
                + (" (short term)" if first["remainder"] else ""),
            )
            contest_path = (
                f"{path}/{day}-{stage}/{key}"
                + (f"-{slug(seat)}" if seat else "")
                + ("-short-term" if first["remainder"] else "")
            )
            suffix = 1
            while (
                candidate := contest_path if suffix == 1 else f"{contest_path}-{suffix}"
            ) in paths or candidate in taken:
                suffix += 1
            paths.add(candidate)
            cs.add("slug", path=candidate, entity_id=contest)
            if partial:
                cs.add("note", entity_id=contest, kind="caveat", body_md=DISTRICT.format(whole=word))
            if rcv:
                cs.add("note", entity_id=contest, kind="caveat", body_md=RCV)
            reported = None if contest in wards else grouped.get(race)
            superseded = None
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            if reported is not None:
                if contest_key in sets:
                    superseded = result_set
                    cs.add(
                        "result_set",
                        id=superseded,
                        election_id=election,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status="superseded",
                        source_id=source,
                        is_default=False,
                        completeness=1,
                    )
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:precinct")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source if reported is None else counted[reported["fips"][0]],
                is_default=True,
                supersedes_id=superseded,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD if reported is None else f"{METHOD} {PRECINCT}",
            )

            entries: list[tuple[UUID, str, int]] = []
            for candidate_id, ballot, incumbent, votes, elected in rows.select(
                "candidate", "name", "incumbent", "votes", "elected"
            ).iter_rows():
                person = cs.key(f"{SOURCE}.person", f"{race}:{candidate_id}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, ballot)
                option = cs.key("us.option", f"{contest_key}:{candidate_id}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{candidate_id}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_incumbent=incumbent,
                )
                if elected in ("1", "3"):
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if elected == "1" else "advanced",
                        seats=None,
                    )
                entries.append((option, candidate_id, votes))
            if writeins := first["writeins"]:
                option = cs.key("us.option", f"{contest_key}:write-ins")
                cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                entries.append((option, "write-ins", writeins))
            for option, candidate_id, votes in entries if result_set in cs.minted else []:
                if reported is None:
                    values.append((str(result_set), str(unit), str(option), votes))
                    continue
                for place, value in (
                    reported.filter(pl.col("candidate") == candidate_id).select("unit", "votes").iter_rows()
                ):
                    target = precincts.get(place) or cs.unit("us.precinct", place)
                    if place not in precincts:
                        minted.setdefault(place, day)
                    if area != counties[place.split(":")[2]][1]:
                        memberships.add((target, area, day))
                    values.append((str(result_set), str(target), str(option), value))
        for place, day in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place),
                polity_id=state,
                scheme_code="us.precinct",
                code=place,
                parent_id=counties[place.split(":")[2]][0],
                area_relation="within",
                area_id=counties[place.split(":")[2]][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for target, area, day in memberships:
            cs.add(
                "reporting_unit_area",
                unit_id=target,
                area_id=area,
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
