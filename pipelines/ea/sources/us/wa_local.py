import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.medsl_state import NATIONAL
from ea.store import DATA, connect

SOURCE = "us.wa_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Washington Secretary of State"
TITLE = "Election results"
DAYS = [
    date.fromisoformat(day)
    for day in [
        "2014-08-05",
        "2014-11-04",
        "2015-08-04",
        "2015-11-03",
        "2016-08-02",
        "2016-11-08",
        "2017-08-01",
        "2017-11-07",
        "2018-08-07",
        "2018-11-06",
        "2019-08-06",
        "2019-11-05",
        "2020-08-04",
        "2020-11-03",
        "2021-08-03",
        "2021-11-02",
        "2022-08-02",
        "2022-11-08",
        "2023-08-01",
        "2023-11-07",
        "2024-08-06",
        "2024-11-05",
        "2025-08-05",
        "2025-11-04",
    ]
]
FILES = {day: f"https://results.vote.wa.gov/results/{day:%Y%m%d}/export/{day:%Y%m%d}_AllCounties.csv" for day in DAYS}
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_53_place_500k.zip"
SCHOOLS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_53_unsd_500k.zip"
LOCAL = {"City/Town", "School", "County", "Commissioner", "Other"}
NUMBER = re.compile(r"(?:SCHOOL DIST(?:RICT)?|SCH DIST|\bSD)\s*(?:NO\.?\s*|#\s*|-\s*)?(\d+)")
SCHOOL = re.compile(r"\bSCHOOLS?\b|\bSCH\b|\bS\.?D\b")
COMPASS = {"n": "north", "s": "south", "e": "east", "w": "west"}
FILLER = {
    "school",
    "schools",
    "district",
    "dist",
    "sd",
    "sch",
    "no",
    "the",
    "of",
    "community",
    "public",
    "city",
    "town",
}
SKIPPED = re.compile(
    r"PROPOSITION|WHETHER|PROP\.|AMENDMENT|CHARTER|FREEHOLDER|LEVY|BOND|PCO|PRECINCT|ADVISORY|RECALL|ANNEX|MEASURE|"
    r"\bFIRE\b|\bPORT\b|\bPARK\b|WATER|SEWER|CEMETERY|HOSPITAL|LIBRARY|AIRPORT|UTILITY|\bPUD\b|\bEMS\b|FLOOD|"
    r"TRANSPORTATION|TRANSIT|LANDS|DRAINAGE|IRRIGATION|DIKING|CONSERVATION|MOSQUITO|SOLID WASTE|HARBOR"
)
COUNTY = {
    "assessor": (r"ASSESSOR", "Assessor"),
    "auditor": (r"AUDITOR", "Auditor"),
    "clerk": (r"CLERK", "Clerk"),
    "coroner": (r"CORONER", "Coroner"),
    "prosecuting-attorney": (r"PROSECUT\w*(?: ATTORNEY)?", "Prosecuting Attorney"),
    "sheriff": (r"SHERIFF", "Sheriff"),
    "treasurer": (r"TREASURER", "Treasurer"),
    "county-executive": (r"(?:COUNTY )?EXECUTIVE", "Executive"),
}
ALIASES = {"prosecuting-attorney": ["prosecutor"], "board-of-commissioners": ["commission"]}
CITY = {
    "city-attorney": (r"CITY ATTORNEY", "City Attorney"),
    "city-clerk-treasurer": (r"CLERK[- /]*TREASURER", "Clerk-Treasurer"),
    "city-treasurer": (r"TREASURER", "Treasurer"),
    "city-clerk": (r"CLERK", "Clerk"),
}
METHOD = (
    "Votes are the county totals the Secretary of State's results site exports for every county; a contest whose "
    "district crosses county lines adds its counties together. Write-in votes are one total per contest. Winners are "
    "the leading candidates: the top two advance from a primary, and the top one wins a general election."
)
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the {whole} as a whole."
)


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".csv")
    for url in (PLACES, SCHOOLS):
        raw.fetch(SOURCE, url, suffix=".zip")


def _words(text: str) -> list[tuple[str, int]]:
    return [
        (match[0].lower(), match.end())
        for match in re.finditer(r"[A-Za-z0-9]+", text)
        if match[0].lower() not in FILLER
    ]


def _match(
    text: str, names: dict[str, list[tuple[str, list[str]]]], fips: str, anywhere: bool
) -> tuple[str, str] | None:
    text = re.sub(r"(?i)\bMt\.? ", "Mount ", text)
    words = [(COMPASS.get(word, word), end) for word, end in _words(text)]
    while words and words[0][0].isdigit():
        words = words[1:]
    for size in range(min(len(words), 5), 0, -1):
        options = names.get("".join(word for word, _ in words[:size]), [])
        inside = [code for code, within in options if fips in within]
        if len(inside) == 1 or anywhere and not inside and len(options) == 1:
            return (inside or [options[0][0]])[0], text[words[size - 1][1] :]
    return None


def _seat(tail: str, school: bool) -> tuple[str | None, bool]:
    if school:
        found = re.search(r"(?:DIRECTOR|\bDIR\b|BOARD|POSITION|\bPOS\b|MEMBER|AT[- ]LARGE)(.*)", tail)
        number = None if found is None else re.search(r"\d+", found[1])
        if found is None or number is None:
            return None, False
        label = "Position" if re.search(r"\bPOS", found[0]) else "District"
        return f"{label} {int(number[0])}", False
    ward = re.search(r"\bWARD\s*(?:NO\.?\s*|#\s*)?(\d+)", tail)
    district = re.search(r"\b(?:DISTRICT|DIST\.?|C\s?D)\s*(?:NO\.?\s*|#\s*)?(\d+)", tail)
    position = re.findall(r"(?:POSITION|\bPOS\b\.?|MEMBER|SEAT|COUNCIL\w*)\s*[,(-]?\s*(?:NO\.?\s*|#\s*)?(\d+)", tail)
    parts = [
        *([f"Ward {int(ward[1])}"] if ward else []),
        *([f"District {int(district[1])}"] if district and not ward else []),
        *([f"Position {int(position[-1])}"] if position else []),
    ]
    return " ".join(parts) or None, ward is not None or district is not None


def parse() -> None:
    frame = pl.concat(
        [
            pl.read_csv(raw.cached(SOURCE, url).path, infer_schema_length=0).with_columns(day=pl.lit(day))
            for day, url in FILES.items()
        ],
        how="diagonal",
    ).select(
        pl.col("County").str.strip_chars().alias("county"),
        pl.col("Race").str.replace_all(r"\s+", " ").str.strip_chars().alias("race"),
        pl.col("Candidate").str.replace_all(r"\s+", " ").str.strip_chars().alias("candidate"),
        pl.col("Party")
        .str.extract(r"\((?:Prefers )?(.+?)(?: Party| Party Preference)?\)")
        .str.to_uppercase()
        .replace({"DEMOCRATIC": "DEMOCRAT", "GOP": "REPUBLICAN"})
        .alias("party"),
        pl.col("Votes").cast(pl.Int64).alias("votes"),
        pl.col("JurisdictionName").str.strip_chars().alias("jurisdiction"),
        "day",
    )
    shapes: list[tuple[str, str, str, str]] = []
    for url in (PLACES, SCHOOLS):
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
                """select regexp_replace(n.text, ' County$', ''), c.code from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
                   where a.scheme_code = 'us.county' and c.code like '53%%'"""
            ).fetchall()
        )
        places = pl.DataFrame(
            conn.execute(
                """with shapes as (
                     select code, name, lsad, st_multi(st_collectionextract(st_makevalid(st_transform(
                       st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, name, lsad, geojson)
                   )
                   select s.code, s.name, s.lsad, encode(st_asewkb(s.geom), 'hex'),
                     encode(st_asewkb(st_pointonsurface(s.geom)), 'hex'),
                     array(select c.code from area a join area_code c on c.area_id = a.id
                           join area_geometry g on g.area_id = a.id
                           where a.scheme_code = 'us.county' and c.code like '53%%' and g.geom && s.geom
                             and st_area(st_intersection(g.geom, s.geom)) > 0.005 * st_area(s.geom))
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
    cities: dict[str, list[tuple[str, list[str]]]] = {}
    schools: dict[str, list[tuple[str, list[str]]]] = {}
    for code, text, lsad, _, _, within in places.iter_rows():
        target = schools if lsad == "SD" else cities
        target.setdefault("".join(word for word, _ in _words(re.sub(r"\(.*?\)", "", text))), []).append((code, within))
    labels = []
    missing: dict[str, int] = {}
    numbers: dict[tuple[str, int], str] = {}
    for county, race in frame.select("county", "race").unique().iter_rows():
        if (
            (fips := counties.get(county)) is not None
            and (number := NUMBER.search(race.upper())) is not None
            and (matched := _match(race, schools, fips, True)) is not None
        ):
            numbers[(county, int(number[1]))] = matched[0]
    for county, race, jurisdiction, day in frame.select("county", "race", "jurisdiction", "day").unique().iter_rows():
        typed = day.year >= 2016
        if typed and jurisdiction not in LOCAL:
            continue
        upper = race.upper()
        fips = counties.get(county)
        if fips is None or SKIPPED.search(upper) or re.search(r"JUDGE|COURT|JUSTICE", upper):
            continue
        found = None
        if SCHOOL.search(upper) and re.search(r"DIRECTOR|\bDIR\b|BOARD|POS|MEMBER", upper):
            number = NUMBER.search(upper)
            if (matched := _match(race, schools, fips, True)) is not None:
                code, tail = matched
            elif number is not None and number.start() == 0 and (county, int(number[1])) in numbers:
                code, tail = numbers[(county, int(number[1]))], race[number.end() :]
            else:
                missing[f"school {county}: {race}"] = 1
                continue
            seat, _ = _seat(tail.upper(), True)
            found = (code, "board-of-directors", "Board of Directors", True, seat, False)
        if (
            found is None
            and (not typed or jurisdiction in ("City/Town", "Other"))
            and (matched := _match(re.sub(r"(?i)^(?:THE )?(?:CITY|TOWN) OF ", "", race), cities, fips, False))
            is not None
        ):
            code, tail = matched
            tail = tail.upper()
            explicit = re.match(r"(?i)(?:THE )?(?:CITY|TOWN) OF ", race) is not None or "CITY" in tail or "TOWN" in tail
            if re.match(r"\W*(?:COUNTY|SCHOOL)", tail):
                pass
            elif re.search(r"\bMAYOR\b", tail) and not re.search(r"DEPUTY|PRO TEM", tail):
                found = (code, "mayor", "Mayor", False, None, False)
            elif re.search(r"COUNCIL PRESIDENT", tail):
                found = (code, "council-president", "Council President", False, None, False)
            elif re.search(r"COUNCIL", tail):
                seat, partial = _seat(tail, False)
                found = (code, "council", "Council", True, seat, partial)
            elif explicit or typed:
                key = next((key for key, (pattern, _) in CITY.items() if re.search(pattern, tail)), None)
                if key is not None:
                    found = (code, key, CITY[key][1], False, None, False)
        if found is None and (not typed or jurisdiction in ("County", "Commissioner")) and not SCHOOL.search(upper):
            text = re.sub(rf"(?i)\b{re.escape(county)}\b", " ", upper)
            bare = re.sub(
                r"\b(?:COUNTY|COUNTYWIDE|WIDE|DISTRICT|DIST|CO|OF|BOARD|ALL|NO|POSITION|POS|MEMBER|AT|LARGE|"
                r"METROPOLITAN|COMMISSIONERS?|COMMISSION|COMMRS?|COMM|COUNCIL(?:OR|MEMBER)?S?)\b|\d+|[^A-Z]",
                "",
                text,
            )
            number = re.search(r"\d+", text)
            if bare == "" and re.search(r"\bCOMM", text):
                found = (
                    fips,
                    "board-of-commissioners",
                    "Board of Commissioners",
                    True,
                    number and f"District {int(number[0])}",
                    False,
                )
            elif bare == "" and re.search(r"COUNCIL", text) and (typed or "COUNTY" in text):
                if number:
                    found = (fips, "council", "Council", True, f"District {int(number[0])}", True)
            else:
                rest = re.sub(r"\bCOUNTY(?:[- ]?WIDE)?\b|\bOF\b|\bDISTRICT\b|\bDIST\b|\d+|[^A-Z ]", " ", text)
                key = next(
                    (key for key, (pattern, _) in COUNTY.items() if re.fullmatch(rf"\s*{pattern}\s*", rest)), None
                )
                if key is not None:
                    found = (fips, key, COUNTY[key][1], False, None, False)
        if found is not None:
            code, key, title, body, seat, partial = found
            labels.append(
                {
                    "county": county,
                    "race": race,
                    "jurisdiction": jurisdiction,
                    "day": day,
                    "code": code,
                    "key": key,
                    "title": title,
                    "body": body,
                    "seat": seat,
                    "partial": partial,
                    "remainder": re.search(r"UNEXPIRED|SHORT", upper) is not None
                    and re.search(r"SHORT (?:AND|&) FULL|FULL (?:AND|&) SHORT", upper) is None,
                }
            )
    frame = frame.join(
        pl.DataFrame(labels, infer_schema_length=None), on=["county", "race", "jurisdiction", "day"], nulls_equal=True
    )
    contest = ["day", "code", "key", "seat", "remainder"]
    clashes = (
        frame.group_by(*contest, "county").agg(pl.col("race").n_unique().alias("races")).filter(pl.col("races") > 1)
    )
    frame = frame.join(clashes, on=[*contest, "county"], how="anti", nulls_equal=True)
    unseated = (
        frame.filter(pl.col("body"), pl.col("seat").is_null())
        .group_by("day", "code", "key")
        .agg(pl.col("race").n_unique().alias("races"))
    )
    frame = frame.join(unseated.filter(pl.col("races") > 1), on=["day", "code", "key"], how="anti")
    print(
        f"kept {frame.select(contest).n_unique()} contests; {clashes.height} clashing labels dropped; "
        f"unmatched: {len(missing)}"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    places.filter(pl.col("code").is_in(frame["code"].implode())).write_parquet(PARSED / "places.parquet")
    (PARSED / "missing.txt").write_text("\n".join(sorted(missing)))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    places = pl.read_parquet(PARSED / "places.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's city, town, school board, and county elections, 2014–2025",
            polity="US-WA",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://results.vote.wa.gov/results/",
            license=None,
        )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for url in (PLACES, SCHOOLS):
            cs.artifact(raw.cached(SOURCE, url), census)
        state = cs.polity("US-WA")
        polities: dict[str, tuple[UUID, str, str, str]] = {}
        for code, polity, text, path, kind in conn.execute(
            """select c.code, t.polity_id, n.text, s.path, p.kind from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and c.code like '53%%'"""
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
                   where s.path like 'us/wa/%%' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        known = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme in ('us.office', 'us.body') and value like 'WA:%%'"
            ).fetchall()
        }
        held = set(
            conn.execute(
                """select x.value, e.date_from from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join source s on s.id = r.source_id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body')
                   where x.value like 'WA:%%' and s.title <> %s""",
                [TITLE],
            ).fetchall()
        )
        parents = dict(
            conn.execute(
                """select x.value, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ '^[0-9-]+:WA(:primary)?$'"""
            ).fetchall()
        )
        for code, text, lsad, geom, point, within in places.iter_rows():
            if code in polities:
                continue
            school = code.startswith("sd")
            geoid, scheme = code.removeprefix("sd"), "us.school_district" if school else "us.place"
            polity = cs.key("us.polity", f"school:{geoid}" if school else f"place:{code}")
            path = f"us/wa/{slug(text)}"
            if path in taken:
                path = (
                    f"us/wa/{slug(text)}-{slug(polities[within[0]][1])}"
                    if school
                    else f"us/wa/{slug(text)}-{'town' if lsad == '43' else 'city'}"
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
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "53"))
            name(area, f"{text}, WA")
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
        fptp = cs.key("us.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("the fptp system must already exist")

        owners: set[UUID] = set()
        elections: set[UUID] = set()
        districts: set[str] = set()
        paths: set[str] = set()
        values: list[tuple[str, str, str, int]] = []
        contest_columns = ["day", "code", "key", "seat", "remainder"]
        for _, rows in frame.group_by(contest_columns, maintain_order=True):
            first = rows.row(0, named=True)
            code, key, day, seat = first["code"], first["key"], first["day"], first["seat"]
            owner_key = next(
                (f"WA:{code}:{alias}" for alias in [key, *ALIASES.get(key, [])] if f"WA:{code}:{alias}" in known),
                f"WA:{code}:{key}",
            )
            if (owner_key, day) in held:
                continue
            polity, place, path, word = polities[code]
            title = place
            scheme = {5: "us.county", 7: "us.place"}.get(len(code), "us.school_district")
            geoid = code.removeprefix("sd")
            if key == "mayor":
                office = f"Mayor of {title}"
            elif key == "council" and scheme == "us.place":
                office = f"{title} {'Town' if word == 'town' else 'City'} Council"
            elif key == "council":
                office = f"{title} Council"
            else:
                office = f"{title} {first['title']}"
            owner = cs.key("us.body" if first["body"] else "us.office", owner_key)
            if owner in cs.minted and owner not in owners:
                if first["body"]:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                else:
                    cs.add("office", id=owner, polity_id=polity, kind="mayor" if key == "mayor" else "other")
                name(owner, office)
            owners.add(owner)

            parent_key = f"{day}:WA:primary" if day.month == 8 else f"{day}:WA"
            stage = parents.get(parent_key, "primary" if day.month == 8 else "local")
            parent = cs.key("us.election", parent_key)
            if parent_key not in parents and parent not in elections:
                elections.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=state,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} Washington {'primary' if stage == 'primary' else 'local'} elections")
                cs.add("slug", path=f"us/wa/{day}-{stage}", entity_id=parent)
            election = cs.key("us.election", f"{day}:WA:{code}")
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
                election_path = f"{path}/{day}-{stage}"
                while election_path in taken:
                    election_path += "-wa"
                cs.add("slug", path=election_path, entity_id=election)

            whole = cs.area(scheme, geoid)
            unit = cs.unit(scheme, geoid)
            area = whole
            partial = first["partial"] and seat is not None
            if partial:
                district = f"53:{code}:{key}:{slug(seat)}"
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
            contest_key = f"wa:{day}:{code}:{key}:{slug(seat or 'at-large')}" + (":short" if first["remainder"] else "")
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if first["body"] else "office",
                office_id=None if first["body"] else owner,
                body_id=owner if first["body"] else None,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
                term="remainder" if first["remainder"] else "full",
            )
            name(
                contest,
                (f"{day.year} {title} mayoral" if key == "mayor" else f"{day.year} {office}")
                + (" primary" if stage == "primary" else " election")
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
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            totals = (
                rows.with_columns(
                    token=pl.when(pl.col("candidate").str.to_uppercase().str.contains(r"^WRITE[- ]?INS?$"))
                    .then(pl.lit("write-ins"))
                    .otherwise(pl.col("candidate").map_elements(slug, return_dtype=pl.String))
                )
                .sort("votes", descending=True)
                .group_by("token", maintain_order=True)
                .agg(pl.col("candidate").first(), pl.col("party").first(), pl.col("votes").sum())
                .sort("votes", "token", descending=[True, False])
            )
            ranked: list[tuple[int, UUID]] = []
            for token, ballot, label, votes in totals.iter_rows():
                if token == "write-ins":
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    if ballot.isupper():
                        ballot = re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), ballot.title())
                    person = cs.key(f"{SOURCE}.person", f"{contest_key}:{token}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, ballot)
                    option = cs.key("us.option", f"{contest_key}:{token}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", NATIONAL[label]) if label in NATIONAL else None,
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{token}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=label == "STATES NO",
                    )
                    ranked.append((votes, option))
                if result_set in cs.minted:
                    values.append((str(result_set), str(unit), str(option), votes))
            winners = 2 if stage == "primary" else 1
            if len(ranked) > winners and ranked[winners - 1][0] == ranked[winners][0]:
                continue
            for _, option in ranked[:winners]:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="advanced" if stage == "primary" else "elected",
                    seats=None,
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
