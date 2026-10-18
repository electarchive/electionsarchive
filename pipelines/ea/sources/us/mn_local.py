import io
import json
import re
import zipfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mn_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = "https://electionresultsfiles.sos.mn.gov/{day}/{name}"
DAYS = tuple(
    date.fromisoformat(day)
    for day in (
        "2016-11-08",
        "2017-11-07",
        "2018-11-06",
        "2019-11-05",
        "2020-11-03",
        "2021-11-02",
        "2022-11-08",
        "2023-11-07",
        "2024-11-05",
        "2025-11-04",
    )
)
SUBDIVISIONS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_27_cousub_500k.zip"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_27_place_500k.zip"
DISTRICTS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_27_unsd_500k.zip"
COUNTIES = date(2024, 11, 5)
DIRECTORY = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory/2022/?fips=27"
RANKED = {"2743000", "2758000", "2757220", "2743252", "2706616"}
TYPES = {"ISD": "01", "SSD": "03"}
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Election results files"
METHOD = (
    "Votes are the Minnesota Secretary of State's results by precinct, from the files it publishes for each "
    "November election; a city, town, or school district that spans several counties is counted in each "
    "county's precincts."
)
UNTITLED = (
    "The Secretary of State's results name this question only by its number; the question's text is on the "
    "sample ballot, which the archive does not hold."
)


def url(day: date, name: str) -> str:
    return FILES.format(day=day.strftime("%Y%m%d"), name=name)


def fetch() -> None:
    for day in DAYS:
        for name in ("localPrct.txt", "PrctTbl.txt"):
            raw.fetch(SOURCE, url(day, name), suffix=".txt")
    raw.fetch(SOURCE, url(COUNTIES, "MCDTbl.txt"), suffix=".txt")
    for link in (SUBDIVISIONS, PLACES, DISTRICTS):
        raw.fetch(SOURCE, link, suffix=".zip")
    raw.fetch(SOURCE, DIRECTORY, suffix=".json")


def _shapes(link: str) -> list[tuple[Mapping[str, object], str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, link).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    return [
        (record.as_dict(), json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
        if record is not None and shape is not None
    ]


def _table(day: date, name: str, columns: list[str]) -> pl.DataFrame:
    frame = pl.read_csv(
        raw.cached(SOURCE, url(day, name)).path,
        separator=";",
        has_header=False,
        infer_schema=False,
        encoding="utf8-lossy",
        quote_char=None,
        truncate_ragged_lines=True,
    )
    frame = frame.select(frame.columns[: len(columns)])
    frame.columns = columns
    return frame.select(column for column in columns if not column.startswith("_"))


def parse() -> None:
    kinds = {}
    towns = []
    for record, geom in _shapes(SUBDIVISIONS):
        whole, proper = str(record["NAMELSAD"]), str(record["NAME"])
        kind = whole[len(proper) :].strip().lower()
        kinds[str(record["GEOID"])] = kind
        if kind == "township":
            towns.append((str(record["GEOID"]), f"{proper} Township", geom))
    places = [
        (str(record["GEOID"]), str(record["NAME"]), geom)
        for record, geom in _shapes(PLACES)
        if str(record["LSAD"]) == "25"
    ]
    leas = {
        row["state_leaid"].removeprefix("MN-"): row["leaid"]
        for row in json.loads(raw.cached(SOURCE, DIRECTORY).path.read_text())["results"]
    }
    schools = {str(record["GEOID"]): (str(record["NAME"]), geom) for record, geom in _shapes(DISTRICTS)}
    frames, precincts = [], []
    names = (
        _table(COUNTIES, "MCDTbl.txt", ["county", "county_name", "_mcd", "_name"])
        .select("county", "county_name")
        .unique("county")
    )
    for day in DAYS:
        table = _table(
            day,
            "localPrct.txt",
            [
                "_state",
                "county",
                "precinct",
                "office",
                "title",
                "district",
                "order",
                "name",
                "_suffix",
                "_incumbent",
                "party",
                "_reporting",
                "_total",
                "votes",
                "_share",
                "_all",
            ],
        )
        frames.append(table.with_columns(day=pl.lit(day)))
        listed = _table(
            day, "PrctTbl.txt", ["county", "precinct", "label", "_a", "_b", "_c", "_d", "_e", "mcd"]
        ).with_columns(day=pl.lit(day))
        precincts.append(listed.join(names, on="county", how="left"))
    lines = pl.concat(frames).with_columns(
        pl.col("votes").cast(pl.Int64),
        fips=pl.format("27{}", (pl.col("county").cast(pl.Int64) * 2 - 1).cast(pl.String).str.zfill(3)),
        title=pl.col("title").str.replace_all(r"\s+", " ").str.strip_chars(),
        name=pl.col("name").str.replace_all(r"\s+", " ").str.strip_chars(),
    )
    contests = []
    for (day, office, district, title), _ in lines.group_by("day", "office", "district", "title"):
        upper = str(title).upper()
        school = str(office).startswith("5")
        if str(office).startswith("6"):
            continue
        question = "QUESTION" in upper
        if school:
            match = re.search(r"\((ISD|SSD) ?#\s*(\d+)\)", upper)
            if match is None:
                continue
            code = leas.get(f"{TYPES[match[1]]}{int(match[2]):04d}")
            if code is None or code not in schools:
                continue
            scheme, key = "us.school_district", "question" if question else "board-of-education"
        else:
            counties = lines.filter(pl.col("day") == day, pl.col("office") == office, pl.col("district") == district)[
                "fips"
            ].unique()
            kind = next((kinds[f"{fips}{district}"] for fips in counties if f"{fips}{district}" in kinds), None)
            if kind == "city":
                scheme, code = "us.place", f"27{district}"
            elif kind == "township":
                if len(counties) != 1:
                    continue
                scheme, code = "us.town", f"{counties[0]}{district}"
            else:
                continue
            if code in RANKED:
                continue
            if question:
                key = "question"
            elif re.match(r"(?:SPECIAL ELECTION FOR )?MAYOR\b", upper):
                key = "mayor"
            elif re.match(r"(?:SPECIAL ELECTION FOR )?COUNCIL MEMBER\b", upper):
                key = "council"
            elif re.match(r"(?:SPECIAL ELECTION FOR )?TOWN SUPERVISOR\b", upper):
                key = "town-board"
            elif re.match(r"(?:SPECIAL ELECTION FOR )?(?:TOWN|CITY) CLERK\b(?! ?- ?TREASURER)", upper):
                key = "clerk"
            elif re.match(r"(?:SPECIAL ELECTION FOR )?(?:TOWN|CITY) (?:CLERK ?- ?)?TREASURER\b", upper):
                key = "treasurer" if "CLERK" not in upper else "clerk-treasurer"
            else:
                continue
        stem = re.sub(r"\s*\([^)]*\)", "", str(title)).strip()
        seat = None
        if not question:
            found = re.search(
                r"(?i)\b(ward \d+|district \d+|position \d+|seat [a-z0-9]+|at large)\b", stem
            ) or re.search(r"(?i)\b((?!member\b)[a-z]+ district)$", stem)
            if found:
                seat = re.sub(r"(?i)^at large$", "At-Large", found[1].title().replace("At Large", "At-Large"))
        magnitude = re.search(r"\(ELECT (\d+)\)", upper)
        contests.append(
            {
                "day": day,
                "office": office,
                "district": district,
                "title": title,
                "stem": stem,
                "scheme": scheme,
                "code": code,
                "key": key,
                "body": key in ("council", "town-board", "board-of-education"),
                "seat": seat,
                "remainder": upper.startswith("SPECIAL ELECTION"),
                "magnitude": int(magnitude[1]) if magnitude else 1,
            }
        )
    frame = pl.DataFrame(contests, infer_schema_length=None)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "contests.parquet")
    lines.join(
        frame.select("day", "office", "district", "title"), on=["day", "office", "district", "title"]
    ).write_parquet(PARSED / "lines.parquet")
    pl.concat(precincts).write_parquet(PARSED / "precincts.parquet")
    used = set(frame.filter(pl.col("scheme") == "us.school_district")["code"].to_list())
    pl.DataFrame(
        [(code, name, geom) for code, (name, geom) in schools.items() if code in used],
        schema=["code", "display", "geom"],
        orient="row",
    ).write_parquet(PARSED / "schools.parquet")
    used = set(frame.filter(pl.col("scheme") == "us.town")["code"].to_list())
    pl.DataFrame(
        [row for row in towns if row[0] in used], schema=["code", "display", "geom"], orient="row"
    ).write_parquet(PARSED / "towns.parquet")
    used = set(frame.filter(pl.col("scheme") == "us.place")["code"].to_list())
    pl.DataFrame(
        [row for row in places if row[0] in used], schema=["code", "display", "geom"], orient="row"
    ).write_parquet(PARSED / "places.parquet")
    print(
        f"{frame.height} contests: {frame.group_by('scheme').len().sort('scheme').rows()}, "
        f"{frame.filter(pl.col('key') == 'question').height} questions"
    )


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet").sort(
        "day", "code", "key", "seat", "office", nulls_last=True
    )
    lines = pl.read_parquet(PARSED / "lines.parquet")
    grouped = {keys: rows for keys, rows in lines.group_by("day", "office", "district", "title")}
    labels = {
        (day, county, precinct): (label, name)
        for day, county, precinct, label, name in pl.read_parquet(PARSED / "precincts.parquet")
        .select("day", "county", "precinct", "label", "county_name")
        .iter_rows()
    }
    with connect() as conn:
        polities: dict[str, tuple[UUID, str, str]] = {}
        for scheme, code, polity, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district')
                 and left(c.code, 2) = '27'"""
        ).fetchall():
            polities[f"{scheme}:{code}"] = (polity, text, path)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/mn/%%'").fetchall())
        taken = set(
            conn.execute(
                """select e.date_from, x.value from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join changeset g on g.id = r.changeset_id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body')
                   where x.value like 'MN:%%' and g.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        )
        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and split_part(code, ':', 2) = 'MN'"
            ).fetchall()
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and left(code, 2) = '27'"
            ).fetchall()
        }
        parents = {
            value: kind
            for value, kind in conn.execute(
                """select x.value, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ '^[0-9-]+:MN$'"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota city, township, and school district elections by precinct from the Secretary of State's results files",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            text = re.sub(
                r"\b([A-Z][A-Z'’-]+)\b", lambda match: match[1].title() if len(match[1]) > 1 else match[1], text
            )
            return re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text)

        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "referendum")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, block, and referendum systems must already exist")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for link in (SUBDIVISIONS, PLACES, DISTRICTS):
            cs.artifact(raw.cached(SOURCE, link), census)
        directory = cs.source("Urban Institute", "Education Data Portal: Common Core of Data school district directory")
        cs.add(
            "source",
            id=directory,
            kind="academic",
            publisher="Urban Institute",
            title="Education Data Portal: Common Core of Data school district directory",
            url="https://educationdata.urban.org/",
            license="ODC-By",
        )
        cs.artifact(raw.cached(SOURCE, DIRECTORY), directory)
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://electionresultsfiles.sos.mn.gov/",
            license="Public record",
        )
        for day in DAYS:
            for file in ("localPrct.txt", "PrctTbl.txt"):
                cs.artifact(raw.cached(SOURCE, url(day, file)), source)
        cs.artifact(raw.cached(SOURCE, url(COUNTIES, "MCDTbl.txt")), source)
        state = cs.polity("US-MN")
        towns = pl.read_parquet(PARSED / "towns.parquet")
        repeated = set(towns.filter(pl.col("display").is_duplicated())["display"].to_list())
        for scheme, frame, kind, parent in (
            ("us.place", pl.read_parquet(PARSED / "places.parquet"), "municipality", None),
            ("us.town", towns, "municipality", "county"),
            ("us.school_district", pl.read_parquet(PARSED / "schools.parquet"), "school_district", None),
        ):
            for code, display, geom in frame.iter_rows():
                if f"{scheme}:{code}" in polities:
                    continue
                polity = cs.key(
                    "us.polity",
                    f"school:{code}"
                    if scheme == "us.school_district"
                    else f"town:{code}"
                    if scheme == "us.town"
                    else f"place:{code}",
                )
                path = f"us/mn/{slug(display)}"
                if display in repeated:
                    path = f"{path}-{slug(polities[f'us.county:{code[:5]}'][1])}"
                if path in slugs and slugs[path] != polity:
                    path = f"{path}-{'school-district' if scheme == 'us.school_district' else 'city'}"
                if path in slugs and slugs[path] != polity:
                    raise ValueError(f"{display} would take {path}")
                slugs[path] = polity
                polities[f"{scheme}:{code}"] = (polity, display, path)
                if polity not in cs.minted:
                    continue
                cs.add("polity", id=polity, kind=kind, iso_code=None)
                name(polity, display)
                cs.add("polity_parent", polity_id=polity, parent_id=state)
                cs.add("slug", path=path, entity_id=polity)
                area = cs.area(scheme, code)
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=cs.area("us.county", code[:5]) if parent else cs.area("us.state", "27"),
                )
                name(area, f"{display}, MN")
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=None,
                    geom=geom,
                    label_point=None,
                    quality="official",
                    source_id=census,
                )
                cs.add(
                    "reporting_unit",
                    id=cs.unit(scheme, code),
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
        owners: dict[str, UUID] = {}
        elections: set[UUID] = set()
        persons: dict[tuple[str, str], UUID] = {}
        districts: set[str] = set()
        minted: dict[str, tuple[str, date]] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        values: list[tuple[str, str, str, int]] = []
        seen: set[str] = set()
        for row in contests.iter_rows(named=True):
            scheme, code, key, day, seat = row["scheme"], row["code"], row["key"], row["day"], row["seat"]
            if f"{scheme}:{code}" not in polities:
                continue
            polity, place, path = polities[f"{scheme}:{code}"]
            question = key == "question"
            if not question and (day, f"MN:{code}:{key}") in taken:
                continue
            kind = parents[f"{day}:MN"]
            election = cs.key("us.election", f"{day}:MN:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=cs.key("us.election", f"{day}:MN"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {place} {kind} election")
                cs.add("slug", path=f"{path}/{day}-{kind}", entity_id=election)
            whole = cs.area(scheme, code)
            area, unit = whole, cs.unit(scheme, code)
            label = title(row["stem"]) if question else row["stem"]
            contest_key = (
                f"{day}:MN:{code}:{key}"
                + (f":{slug(label)}" if question else "")
                + (f":{slug(seat)}" if seat else "")
                + (":special" if row["remainder"] else "")
            )
            if contest_key in seen:
                contest_key = f"{contest_key}:{row['office']}"
            seen.add(contest_key)
            contest_path = (
                f"{path}/{day}-{kind}/"
                + (slug(label) if question else key)
                + (f"-{slug(seat)}" if seat else "")
                + ("-special" if row["remainder"] else "")
            )
            contest = cs.key("us.contest", contest_key)
            if contest_path in slugs and slugs[contest_path] != contest:
                contest_path = f"{contest_path}-{row['office']}"
            if contest_path in slugs and slugs[contest_path] != contest:
                continue
            slugs[contest_path] = contest
            if question:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    district_area_id=area,
                    electoral_system_id=systems["referendum"],
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                name(contest, f"{day.year} {place}: {label}")
                cs.add("note", entity_id=contest, kind="caveat", body_md=UNTITLED)
            else:
                body = row["body"]
                office_title = {
                    "mayor": f"Mayor of {place}",
                    "council": f"{place} City Council",
                    "town-board": f"{place} Board of Supervisors",
                    "clerk": f"{place} Clerk",
                    "treasurer": f"{place} Treasurer",
                    "clerk-treasurer": f"{place} Clerk-Treasurer",
                    "board-of-education": re.sub(r" (?:Public )?School Dis(?:trict|t\.?)$", "", place)
                    + " School Board",
                }[key]
                if f"{code}:{key}" not in owners:
                    owner = owners[f"{code}:{key}"] = cs.key("us.body" if body else "us.office", f"MN:{code}:{key}")
                    if owner in cs.minted:
                        if body:
                            cs.add("body", id=owner, polity_id=polity, kind="council")
                        else:
                            cs.add("office", id=owner, polity_id=polity, kind="mayor" if key == "mayor" else "other")
                        name(owner, office_title)
                if seat and seat.startswith(("Ward", "District")):
                    district = f"27:{code}:{key}:{slug(seat)}"
                    area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                    if area in cs.minted and district not in districts:
                        districts.add(district)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                        name(area, f"{office_title}, {seat}")
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
                magnitude = row["magnitude"]
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if body else "office",
                    office_id=None if body else owners[f"{code}:{key}"],
                    body_id=owners[f"{code}:{key}"] if body else None,
                    district_area_id=area,
                    magnitude=magnitude,
                    round_number=1,
                    electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                    default_result_kind="counted",
                    term="remainder" if row["remainder"] else "full",
                )
                name(
                    contest,
                    (
                        f"{day.year} {place} mayoral election"
                        if key == "mayor"
                        else f"{day.year} {office_title} election"
                    )
                    + (f", {seat}" if seat else "")
                    + (" (special election)" if row["remainder"] else ""),
                )
            cs.add("slug", path=contest_path, entity_id=contest)
            counted = grouped[(day, row["office"], row["district"], row["title"])]
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
                counted.group_by("name")
                .agg(pl.col("party").first(), pl.col("votes").sum())
                .sort("votes", "name", descending=[True, False])
            )
            options: dict[str, UUID] = {}
            ranked: list[tuple[int, UUID]] = []
            for choice, party, votes in totals.select("name", "party", "votes").iter_rows():
                if question:
                    answer = "yes" if re.match(r"(?i)yes", choice) else "no"
                    option = options[choice] = cs.key("us.option", f"{contest_key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=1 if answer == "yes" else 2,
                        ballot_label=answer.capitalize(),
                    )
                elif party == "WI" or re.match(r"(?i)write-?in", choice):
                    option = options[choice] = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    continue
                else:
                    ballot = title(choice) if choice.isupper() else choice
                    person_key = (code, slug(ballot))
                    if person_key not in persons:
                        persons[person_key] = cs.key(f"{SOURCE}.person", f"MN:{code}:{slug(ballot)}")
                        if persons[person_key] in cs.minted:
                            cs.add("person", id=persons[person_key])
                            name(persons[person_key], ballot)
                    option = options[choice] = cs.key("us.option", f"{contest_key}:{slug(ballot)}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(ballot)}:head"),
                        person_id=persons[person_key],
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=False,
                    )
                ranked.append((votes, option))
            if result_set in cs.minted:
                cut = 1 if question else row["magnitude"]
                if question and len(ranked) != 2:
                    cut = 0
                if cut and (len(ranked) <= cut or ranked[cut - 1][0] != ranked[cut][0]):
                    for _, option in ranked[:cut]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="won" if question else "elected",
                            seats=None if question else 1,
                        )
            for choice, county, fips, precinct, votes in (
                counted.group_by("name", "county", "fips", "precinct")
                .agg(pl.col("votes").sum())
                .select("name", "county", "fips", "precinct", "votes")
                .iter_rows()
            ):
                label_name, county_name = labels.get((day, county, precinct), (precinct, None))
                place_code = f"{day.year}:MN:{fips}:{(county_name or '').upper()}:{label_name.upper()}"
                target = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                if place_code not in precincts:
                    minted.setdefault(place_code, (fips, day))
                memberships.add((target, area, day))
                values.append((str(result_set), str(target), str(options[choice]), votes))
        for place_code, (fips, day) in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place_code),
                polity_id=state,
                scheme_code="us.precinct",
                code=place_code,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=date.fromordinal(day.toordinal() + 1),
            )
        for target, area, day in memberships:
            cs.add(
                "reporting_unit_area",
                unit_id=target,
                area_id=area,
                valid_from=day,
                valid_to=date.fromordinal(day.toordinal() + 1),
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
