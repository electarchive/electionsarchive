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

SOURCE = "us.in_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = "https://enr.indianavoters.in.gov/archive/{year}General/download/AllOfficeResults.csv"
DAYS = {
    2018: date(2018, 11, 6),
    2019: date(2019, 11, 5),
    2020: date(2020, 11, 3),
    2022: date(2022, 11, 8),
    2023: date(2023, 11, 7),
    2024: date(2024, 11, 5),
}
SUBDIVISIONS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_18_cousub_500k.zip"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_18_place_500k.zip"
DISTRICTS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_18_unsd_500k.zip"
GENERIC = re.compile(
    r"(?i)\b(?:community|consolidated|school|schools|corporation|corp|metropolitan|district|of|the|board|united|"
    r"township|town|city|member|seat|at[- ]large|\d+)\b|[^A-Za-z ]"
)
PUBLISHER = "Indiana Election Division"
TITLE = "Election night reporting results archive"
METHOD = (
    "Votes are the Indiana Election Division's certified results by precinct from its results archive; counties "
    "that reported a contest only as a county total are counted as one county unit."
)


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, FILES.format(year=year), suffix=".csv")
    for link in (SUBDIVISIONS, PLACES, DISTRICTS):
        raw.fetch(SOURCE, link, suffix=".zip")


def _shapes(link: str) -> list[tuple[Mapping[str, object], str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, link).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    return [
        (record.as_dict(), json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
        if record is not None and shape is not None
    ]


def parse() -> None:
    townships: dict[tuple[str, str], tuple[str, str, str]] = {}
    fips: dict[str, str] = {}
    for record, geom in _shapes(SUBDIVISIONS):
        county = str(record["NAMELSADCO"]).removesuffix(" County").upper()
        fips[county] = str(record["GEOID"])[:5]
        if str(record["LSAD"]) == "44":
            townships[(county, str(record["NAME"]).upper())] = (
                str(record["GEOID"]),
                f"{record['NAME']} Township",
                geom,
            )
    places = {
        str(record["NAME"]).upper(): (str(record["GEOID"]), str(record["NAME"]), geom)
        for record, geom in _shapes(PLACES)
        if str(record["LSAD"]) in ("25", "43")
    }
    ordered = sorted(places, key=len, reverse=True)
    schools = {
        core: (str(record["GEOID"]), str(record["NAME"]), geom)
        for record, geom in _shapes(DISTRICTS)
        if len(core := re.sub(r"\s+", "", GENERIC.sub(" ", str(record["NAME"])).upper())) >= 4
    }
    cores = sorted(schools, key=len, reverse=True)
    frames = []
    for year in DAYS:
        frame = pl.read_csv(raw.cached(SOURCE, FILES.format(year=year)).path, infer_schema=False, encoding="utf8-lossy")
        frame.columns = [column.replace(" ", "").lstrip("﻿") for column in frame.columns]
        frames.append(
            frame.select(
                pl.lit(year).alias("year"),
                pl.col("Office").str.replace_all(r"\s+", " ").str.strip_chars().alias("office"),
                pl.col("OfficeCategory").alias("category"),
                pl.col("ReportingCountyName").str.to_uppercase().str.strip_chars().alias("county"),
                pl.col("DataEntryJurisdictionName").str.replace_all(r"\s+", " ").str.strip_chars().alias("place"),
                pl.col("DataEntryLevelName").alias("level"),
                pl.col("NameonBallot").str.replace_all(r"\s+", " ").str.strip_chars().alias("name"),
                pl.col("PoliticalParty").alias("party"),
                (pl.col("Winner") == "Yes").alias("winner"),
                pl.col("NumberofOfficeSeats").cast(pl.Int64, strict=False).alias("seats"),
                pl.col("TotalVotes").cast(pl.Int64, strict=False).fill_null(0).alias("votes"),
            )
        )
    lines = pl.concat(frames).filter(
        pl.col("category").str.contains(r"Town|Township|School|City|Mayor"), ~pl.col("category").str.contains("Judge")
    )
    contests = []
    for (year, office, category), rows in lines.group_by("year", "office", "category"):
        upper = str(office).upper()
        counties = rows["county"].unique().to_list()
        if category.startswith("Township"):
            stem = re.match(r"^(.+?) TOWNSHIP\b", upper)
            named = re.search(r",\s*(.+?) COUNTY\b", upper)
            county = named[1].strip() if named else counties[0] if len(counties) == 1 else None
            if stem is None or county is None or (county, stem[1].strip()) not in townships:
                continue
            scheme, code = "us.town", townships[(county, stem[1].strip())][0]
            key = {"Township Trustee": "trustee", "Township Board Member": "township-board"}.get(category, "assessor")
        elif category == "School Board Member":
            core = re.sub(r"\s+", "", GENERIC.sub(" ", re.sub(r",.*", "", str(office))).upper())
            match = next((name for name in cores if name in core), None)
            if match is None:
                continue
            scheme, code, key = "us.school_district", schools[match][0], "board-of-education"
        else:
            text = re.sub(r"[^A-Z ]", " ", upper)
            match = next((name for name in ordered if re.search(rf"\b{re.escape(name)}\b", text)), None)
            if match is None:
                continue
            scheme, code = "us.place", places[match][0]
            if "MAYOR" in upper:
                key = "mayor"
            elif "COUNCIL" in upper:
                key = "council"
            elif "CLERK" in upper:
                key = "clerk-treasurer" if "TREASURER" in upper else "clerk"
            else:
                continue
        seat = None
        found = re.search(r"(?i)\b(district \d+|ward \d+|at[- ]large)\b", str(office))
        if found:
            seat = "At-Large" if "LARGE" in found[1].upper() else found[1].title()
        seats = rows["seats"].drop_nulls()
        contests.append(
            {
                "year": year,
                "office": office,
                "category": category,
                "scheme": scheme,
                "code": code,
                "key": key,
                "body": key in ("council", "township-board", "board-of-education"),
                "seat": seat,
                "magnitude": max(seats.to_list(), default=1),
                "remainder": re.search(r"(?i)unexpired|vacancy|special", str(office)) is not None,
            }
        )
    frame = pl.DataFrame(contests, infer_schema_length=None)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "contests.parquet")
    lines.join(frame.select("year", "office", "category"), on=["year", "office", "category"]).with_columns(
        fips=pl.col("county").replace_strict(fips, default=None)
    ).write_parquet(PARSED / "lines.parquet")
    for scheme, name, table in (
        ("us.town", "towns", {entry[0]: entry for entry in townships.values()}),
        ("us.place", "places", {entry[0]: entry for entry in places.values()}),
        ("us.school_district", "schools", {entry[0]: entry for entry in schools.values()}),
    ):
        used = set(frame.filter(pl.col("scheme") == scheme)["code"].to_list())
        pl.DataFrame(
            [entry for code, entry in table.items() if code in used], schema=["code", "display", "geom"], orient="row"
        ).write_parquet(PARSED / f"{name}.parquet")
    print(
        f"{frame.height} contests: {frame.group_by('scheme').len().sort('scheme').rows()}; "
        f"{lines.select('year', 'office', 'category').unique().height} offices in the files"
    )


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet").sort(
        "year", "code", "key", "seat", "office", nulls_last=True
    )
    grouped = {
        keys: rows for keys, rows in pl.read_parquet(PARSED / "lines.parquet").group_by("year", "office", "category")
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
                 and left(c.code, 2) = '18'"""
        ).fetchall():
            polities[f"{scheme}:{code}"] = (polity, text, path)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/in/%%'").fetchall())
        taken = set(
            conn.execute(
                """select e.date_from, x.value from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join changeset g on g.id = r.changeset_id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body')
                   where x.value like 'IN:%%' and g.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        )
        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and split_part(code, ':', 2) = 'IN'"
            ).fetchall()
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and left(code, 2) = '18'"
            ).fetchall()
        }
        parents = {
            value: kind
            for value, kind in conn.execute(
                """select x.value, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ '^[0-9-]+:IN$'"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana city, town, township, and school board elections by precinct from the Election Division's results archive",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp and block systems must already exist")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for link in (SUBDIVISIONS, PLACES, DISTRICTS):
            cs.artifact(raw.cached(SOURCE, link), census)
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://enr.indianavoters.in.gov/",
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, FILES.format(year=year)), source)
        state = cs.polity("US-IN")
        towns = pl.read_parquet(PARSED / "towns.parquet")
        repeated = set(towns.filter(pl.col("display").is_duplicated())["display"].to_list())
        for scheme, frame, kind in (
            ("us.place", pl.read_parquet(PARSED / "places.parquet"), "municipality"),
            ("us.town", towns, "municipality"),
            ("us.school_district", pl.read_parquet(PARSED / "schools.parquet"), "school_district"),
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
                path = f"us/in/{slug(display)}"
                if display in repeated:
                    path = f"{path}-{slug(polities[f'us.county:{code[:5]}'][1])}"
                if path in slugs and slugs[path] != polity:
                    path = f"{path}-{'school-district' if scheme == 'us.school_district' else 'town'}"
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
                    parent_id=cs.area("us.county", code[:5]) if scheme == "us.town" else cs.area("us.state", "18"),
                )
                name(area, f"{display}, IN")
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
            scheme, code, key, seat = row["scheme"], row["code"], row["key"], row["seat"]
            day = DAYS[row["year"]]
            if f"{scheme}:{code}" not in polities:
                continue
            polity, place, path = polities[f"{scheme}:{code}"]
            if (day, f"IN:{code}:{key}") in taken:
                continue
            kind = parents[f"{day}:IN"]
            election = cs.key("us.election", f"{day}:IN:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=cs.key("us.election", f"{day}:IN"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {place} {kind} election")
                cs.add("slug", path=f"{path}/{day}-{kind}", entity_id=election)
            whole = cs.area(scheme, code)
            area = whole
            contest_key = (
                f"{day}:IN:{code}:{key}"
                + (f":{slug(seat)}" if seat else "")
                + (":remainder" if row["remainder"] else "")
            )
            number = 1
            while contest_key + (f":{number}" if number > 1 else "") in seen:
                number += 1
            contest_key += f":{number}" if number > 1 else ""
            seen.add(contest_key)
            contest_path = (
                f"{path}/{day}-{kind}/{key}"
                + (f"-{slug(seat)}" if seat else "")
                + ("-unexpired" if row["remainder"] else "")
                + (f"-{number}" if number > 1 else "")
            )
            contest = cs.key("us.contest", contest_key)
            if contest_path in slugs and slugs[contest_path] != contest:
                continue
            slugs[contest_path] = contest
            body = row["body"]
            office_title = {
                "mayor": f"Mayor of {place}",
                "council": f"{place} Council",
                "clerk": f"{place} Clerk",
                "clerk-treasurer": f"{place} Clerk-Treasurer",
                "trustee": f"{place} Trustee",
                "township-board": f"{place} Board",
                "assessor": f"{place} Assessor",
                "board-of-education": re.sub(r" (?:Community )?School Corporation$| Schools$", "", place)
                + " School Board",
            }[key]
            if f"{code}:{key}" not in owners:
                owner = owners[f"{code}:{key}"] = cs.key("us.body" if body else "us.office", f"IN:{code}:{key}")
                if owner in cs.minted:
                    if body:
                        cs.add("body", id=owner, polity_id=polity, kind="council")
                    else:
                        cs.add("office", id=owner, polity_id=polity, kind="mayor" if key == "mayor" else "other")
                    name(owner, office_title)
            if seat and seat.startswith(("Ward", "District")):
                district = f"18:{code}:{key}:{slug(seat)}"
                area = cs.area("us.local_district", district)
                if area in cs.minted and district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    name(area, f"{office_title}, {seat}")
                    cs.add(
                        "reporting_unit",
                        id=cs.unit("us.local_district", district),
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=district,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                    )
            counted = grouped[(row["year"], row["office"], row["category"])]
            totals = (
                counted.with_columns(same=pl.col("name").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", ""))
                .group_by("same")
                .agg(pl.col("name").first(), pl.col("party").first(), pl.col("winner").any(), pl.col("votes").sum())
                .sort("votes", "name", descending=[True, False])
            )
            flagged = [same for same, winner in totals.select("same", "winner").iter_rows() if winner]
            ranked = totals["votes"].to_list()
            consistent = (
                bool(flagged)
                and totals["same"].to_list()[: len(flagged)] == flagged
                and (len(ranked) == len(flagged) or ranked[len(flagged) - 1] != ranked[len(flagged)])
            )
            magnitude = len(flagged) if consistent else row["magnitude"]
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
                (f"{day.year} {place} mayoral election" if key == "mayor" else f"{day.year} {office_title} election")
                + (f", {seat}" if seat else "")
                + (" (unexpired term)" if row["remainder"] else ""),
            )
            cs.add("slug", path=contest_path, entity_id=contest)
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
            options: dict[str, UUID] = {}
            for same, choice, party, winner in totals.select("same", "name", "party", "winner").iter_rows():
                if re.match(r"(?i)write-?in|w/i$", choice) and len(choice) < 12:
                    option = options[same] = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    continue
                ballot = re.sub(r"\s*\((?:W/I|w/i)\)$", "", choice)
                person_key = (code, slug(ballot))
                if person_key not in persons:
                    persons[person_key] = cs.key(f"{SOURCE}.person", f"IN:{code}:{slug(ballot)}")
                    if persons[person_key] in cs.minted:
                        cs.add("person", id=persons[person_key])
                        name(persons[person_key], ballot)
                option = options[same] = cs.key("us.option", f"{contest_key}:{slug(ballot)}")
                party_key = {"Democratic": "democratic-party", "Republican": "republican-party"}.get(party or "")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party_key) if party_key else None,
                    ballot_label=ballot,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(ballot)}:head"),
                    person_id=persons[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=party == "Independent",
                )
                if winner and consistent and result_set in cs.minted:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            for same, level, county, fips, place_name, votes in (
                counted.with_columns(same=pl.col("name").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", ""))
                .group_by("same", "level", "county", "fips", "place")
                .agg(pl.col("votes").sum())
                .select("same", "level", "county", "fips", "place", "votes")
                .iter_rows()
            ):
                if fips is None:
                    continue
                if level == "Precinct":
                    place_code = f"{day.year}:IN:{fips}:{county}:{place_name.upper()}"
                    target = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                    if place_code not in precincts:
                        minted.setdefault(place_code, (fips, day))
                    memberships.add((target, area, day))
                else:
                    target = counties[fips][0]
                values.append((str(result_set), str(target), str(options[same]), votes))
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
