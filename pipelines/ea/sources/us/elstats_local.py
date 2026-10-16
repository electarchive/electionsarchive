import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats
from ea.sources.us.alged import PLACES, SCHOOLS
from ea.sources.us.alged import SOURCE as ALGED
from ea.store import DATA, connect

SOURCE = "us.elstats_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = range(1996, 2026)
OFFICES = {
    "Board of Supervisors": ("board-of-supervisors", "Board of Supervisors", None),
    "County Board Member": ("county-board", "County Board", None),
    "City Council": ("council", "City Council", None),
    "Town Council": ("council", "Town Council", None),
    "School Board": ("board-of-education", "Board of Education", None),
    "Mayor": ("mayor", "Mayor", "mayor"),
    "Chairman of the Board of Supervisors": (
        "chair-of-the-board-of-supervisors",
        "Chair of the Board of Supervisors",
        "other",
    ),
    "Chairman of the School Board": ("chair-of-the-school-board", "Chair of the School Board", "other"),
    "Sheriff": ("sheriff", "Sheriff", "other"),
    "Commonwealth's Attorney": ("commonwealth-s-attorney", "Commonwealth's Attorney", "other"),
    "Commissioner of the Revenue": ("commissioner-of-the-revenue", "Commissioner of the Revenue", "other"),
    "Treasurer": ("treasurer", "Treasurer", "other"),
    "Clerk of Court": ("clerk-of-court", "Clerk of the Circuit Court", "other"),
    "Town Recorder": ("town-recorder", "Town Recorder", "other"),
}
DISTRICTS = [
    "Town",
    "Locality",
    "Board of Supervisors District",
    "County School Board District",
    "City School Board District",
]
STAGES = {"General Election": "general", "Local Election": "local", "Special Election": "special"}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "Green": "green-party",
}
METHOD = (
    "Totals are the Department of Elections' locality rows, or the sums of its precinct rows where the archive "
    "has no locality row. The archive marks winners only precinct by precinct, so the winners shown are the "
    "candidates with the most votes, as many as the contest elects."
)
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def url(year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": office} for office in OFFICES]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=elstats.TENANTS["VA"][0]) + urlencode(
        {"search": json.dumps(search, separators=(",", ":"))}
    )


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, url(year), suffix=".csv")


def parse() -> None:
    frames = []
    for year in YEARS:
        frame = pl.read_csv(raw.cached(SOURCE, url(year)).path, infer_schema=False, truncate_ragged_lines=True)
        if frame.height == 0:
            continue
        frames.append(
            frame.filter(
                pl.col("office_name").is_in(list(OFFICES))
                & pl.col("district_type").is_in(DISTRICTS)
                & pl.col("election_type").is_in(list(STAGES))
                & pl.col("division_type").is_in(["Locality", "Precinct"])
                & (pl.col("candidate_name") != "Total Votes Cast")
            ).select(
                pl.col("contest_id").alias("contest"),
                pl.col("election_date").str.slice(0, 10).str.to_date().alias("day"),
                pl.col("election_type").replace_strict(STAGES).alias("stage"),
                pl.col("office_name").alias("office"),
                pl.col("district_type").alias("type"),
                pl.col("district_name").alias("district"),
                pl.col("candidate_id").alias("candidate"),
                pl.col("candidate_name").str.replace_all(r"\s+", " ").str.strip_chars().alias("name"),
                pl.col("candidate_party_name").alias("party"),
                pl.col("division_type").alias("division"),
                pl.col("number_seats").cast(pl.Int16).alias("magnitude"),
                pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64).alias("votes"),
            )
        )
    frame = pl.concat(frames).with_columns(
        candidate=pl.when(pl.col("name").str.contains(r"(?i)^write")).then(pl.lit("write-ins")).otherwise("candidate"),
        name=pl.when(pl.col("name").str.contains(r"(?i)^write")).then(pl.lit("Write-ins")).otherwise("name"),
    )
    frame = (
        frame.filter(
            pl.col("division")
            == pl.when((pl.col("division") == "Locality").any().over("contest"))
            .then(pl.lit("Locality"))
            .otherwise(pl.lit("Precinct"))
        )
        .group_by("contest", "day", "stage", "office", "type", "district", "candidate", "name", "magnitude")
        .agg(pl.col("party").drop_nulls().first(), pl.col("votes").sum())
    )
    with zipfile.ZipFile(raw.cached(ALGED, SCHOOLS[0]).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    schools = {
        elstats.plain(str(record["NAME"]).removesuffix(" Public Schools")): str(record["GEOID"])
        for record in shapefile.Reader(dbf=io.BytesIO(parts["dbf"])).iterRecords()
        if record is not None and record["STUSPS"] == "VA"
    }
    with zipfile.ZipFile(raw.cached(ALGED, PLACES).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    towns = {
        elstats.plain(text): str(record["GEOID"])
        for record in shapefile.Reader(dbf=io.BytesIO(parts["dbf"])).iterRecords()
        if record is not None and record["STUSPS"] == "VA" and record["LSAD"] == "43"
        for text in (str(record["NAME"]), re.sub(r" (City|Town)$", "", str(record["NAME"])))
    }
    with connect() as conn:
        counties = {
            elstats.plain(text) + ("city" if int(code[2:]) >= 500 else "county"): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '51%' and u.code <> '51515'"""
            ).fetchall()
        }
    labels = []
    for contest, office, kind, district in frame.select("contest", "office", "type", "district").unique().iter_rows():
        locality, _, seat = re.sub(r"\s*\([^)]*\)", "", district).partition(" - ")
        plain = elstats.plain(re.sub(r"^Mt\.? ", "Mount ", locality))
        code = next(
            (code for key in (plain, f"{plain}city", f"{plain}county") if (code := counties.get(key)) is not None), None
        )
        if kind == "Town" and not locality.endswith("County"):
            code = towns.get(plain)
        elif code is not None and office.endswith("School Board"):
            school = next((schools[key] for key in (plain, f"{plain}city", f"{plain}county") if key in schools), None)
            code = None if school is None else f"sd{school}"
        labels.append({"contest": contest, "code": code, "seat": seat or None})
    labels = pl.DataFrame(labels, schema={"contest": pl.String, "code": pl.String, "seat": pl.String})
    missing = labels.filter(pl.col("code").is_null())
    print(f"dropped {missing.height} contests with no polity")
    frame = frame.join(labels.filter(pl.col("code").is_not_null()), on="contest")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    wanted = set(frame["code"])
    records = []
    for prefix, shapes in (("", PLACES), ("sd", SCHOOLS[0])):
        with zipfile.ZipFile(raw.cached(ALGED, shapes).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        records += [
            (prefix + str(record["GEOID"]), str(record["NAME"]), json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and shape is not None and prefix + str(record["GEOID"]) in wanted
        ]
    with connect() as conn:
        pl.DataFrame(
            conn.execute(
                """select code, name, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson)) shapes""",
                [
                    [code for code, _, _ in records],
                    [text for _, text, _ in records],
                    [geojson for _, _, geojson in records],
                ],
            ).fetchall(),
            schema=["code", "name", "geom", "point"],
            orient="row",
        ).write_parquet(PARSED / "places.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    places = pl.read_parquet(PARSED / "places.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virginia's county, city, town, and school board elections from the state's archive, 1996–2025",
            polity="US-VA",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        _, publisher, _ = elstats.TENANTS["VA"]
        source = cs.source(publisher, elstats.TITLE)
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        state = cs.polity("US-VA")
        polities: dict[str, tuple[UUID, str, str]] = {}
        for code, polity, text, path, kind in conn.execute(
            """select c.code, t.polity_id, n.text, s.path, p.kind from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and c.code like '51%'"""
        ).fetchall():
            polities[f"sd{code}" if kind == "school_district" else code] = (polity, text.removeprefix("City of "), path)
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.path like 'us/va/%%' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        for code, text, geom, point in places.iter_rows():
            if code in polities:
                continue
            school = code.startswith("sd")
            geoid, scheme = code.removeprefix("sd"), "us.school_district" if school else "us.place"
            polity = cs.key("us.polity", f"school:{geoid}" if school else f"place:{geoid}")
            path = f"us/va/{slug(text)}"
            if path in taken:
                path = f"us/va/{slug(text)}-town"
            if path in taken:
                raise ValueError(f"{text} would take {path}")
            taken.add(path)
            polities[code] = (polity, text, path)
            cs.add("polity", id=polity, kind="school_district" if school else "municipality", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area(scheme, geoid)
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=geoid, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "51"))
            name(area, f"{text}, VA")
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
            cs.add(
                "reporting_unit",
                id=cs.unit(scheme, geoid),
                polity_id=polity,
                scheme_code=scheme,
                code=geoid,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & {*systems.values(), *parties.values()}:
            raise ValueError("the systems and parties must already exist")
        covered = {
            (owner, day.year, None if precision == "year" else day.month)
            for owner, day, precision in conn.execute(
                """select coalesce(c.office_id, c.body_id), e.date_from, e.date_precision from contest c
                   join election e on e.id = c.election_id join changeset x on x.id = c.changeset_id
                   where x.pipeline = %s""",
                [ALGED],
            ).fetchall()
        }
        statewide = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'us.statewide.person' and value like 'VA:%%'"
            ).fetchall()
        }

        owners: set[UUID] = set()
        elections: set[UUID] = set()
        persons: set[UUID] = set()
        districts: set[str] = set()
        paths: set[str] = set()
        skipped = 0
        for (contest_id,), rows in frame.sort("contest", "votes", "name", descending=[False, True, False]).group_by(
            "contest", maintain_order=True
        ):
            first = rows.row(0, named=True)
            code, office, day, stage = first["code"], first["office"], first["day"], first["stage"]
            if code not in polities:
                continue
            polity, place, path = polities[code]
            key, role, kind = OFFICES[office]
            geoid = code.removeprefix("sd")
            scheme = {5: "us.county", 7: "us.place"}.get(len(code), "us.school_district")
            whole = (
                "school district"
                if code.startswith("sd")
                else "town"
                if len(code) == 7
                else "city"
                if int(code[2:]) >= 500
                else "county"
            )
            if key == "mayor":
                title = f"Mayor of {place}"
            elif key == "council":
                title = f"{place} {'Town' if len(code) == 7 else 'City'} Council"
            else:
                title = f"{place} {role}"
            owner = cs.key("us.office" if kind else "us.body", f"VA:{code}:{key}")
            if (owner, day.year, day.month) in covered or (owner, day.year, None) in covered:
                skipped += 1
                continue
            if owner in cs.minted and owner not in owners:
                if kind:
                    cs.add("office", id=owner, polity_id=polity, kind=kind)
                else:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                name(owner, title)
            owners.add(owner)

            general = day.month == 11
            parent = cs.key("us.election", f"{day}:VA")
            if parent in cs.minted and parent not in elections:
                elections.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=state,
                    kind="general" if general else "local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} Virginia {'general' if general else 'local'} elections")
                cs.add("slug", path=f"us/va/{day}-{'general' if general else 'local'}", entity_id=parent)
            kind_of = "general" if general else "local"
            election = cs.key("us.election", f"{day}:VA:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind_of,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {place} {kind_of} election")
                cs.add("slug", path=f"{path}/{day}-{kind_of}", entity_id=election)

            seat = first["seat"]
            seat = f"District {seat.lstrip('0')}" if seat and re.fullmatch(r"\d+", seat) else seat
            area, unit = cs.area(scheme, geoid), cs.unit(scheme, geoid)
            partial = seat is not None and not re.search(r"(?i)at[- ]?large", seat)
            if partial:
                district = f"51:{code}:{key}:{slug(seat)}"
                area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                if area in cs.minted and district not in districts:
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=cs.area(scheme, geoid), valid_from=None)
                    name(area, f"{title}, {seat}")
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
            contest_key = f"VA:local:{contest_id}"
            contest = cs.key("us.contest", contest_key)
            magnitude = first["magnitude"] or 1
            special = stage == "special"
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office" if kind else "body_seats",
                office_id=owner if kind else None,
                body_id=None if kind else owner,
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                default_result_kind="counted",
                term="remainder" if special else "full",
            )
            name(
                contest,
                (f"{day.year} {place} mayoral election" if key == "mayor" else f"{day.year} {title} election")
                + (f", {seat}" if seat else "")
                + (" (special)" if special else ""),
            )
            contest_path = f"{path}/{day}-{kind_of}/{key}" + (f"-{slug(seat)}" if seat else "")
            contest_path += "-special" if special else ""
            suffix = 1
            while (
                candidate := contest_path if suffix == 1 else f"{contest_path}-{suffix}"
            ) in paths or candidate in taken:
                suffix += 1
            paths.add(candidate)
            cs.add("slug", path=candidate, entity_id=contest)
            if partial:
                cs.add("note", entity_id=contest, kind="caveat", body_md=DISTRICT.format(whole=whole))
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
            rank = 0
            for candidate_id, ballot, party, votes in rows.select("candidate", "name", "party", "votes").iter_rows():
                if ballot.lower().startswith("write-in"):
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    rank += 1
                    person = statewide.get(f"VA:{candidate_id}") or cs.key(f"{SOURCE}.person", f"VA:{candidate_id}")
                    if person in cs.minted and person not in persons:
                        cs.add("person", id=person)
                        name(person, ballot)
                    persons.add(person)
                    option = cs.key("us.option", f"{contest_key}:{candidate_id}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party or ""),
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{candidate_id}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=party == "Independent",
                    )
                    if rank <= magnitude and votes > 0:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
                if result_set in cs.minted:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        print(f"skipped {skipped} contests the local elections database already holds")
        return cs.write()
