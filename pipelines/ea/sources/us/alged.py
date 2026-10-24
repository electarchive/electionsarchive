import io
import json
import math
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.alged"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://osf.io/download/tbwzd/"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_place_500k.zip"
SCHOOLS = [
    f"https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_{kind}_500k.zip" for kind in ("unsd", "elsd", "scsd")
]
PUBLISHER = "de Benedictis-Kessner, Lee, Velez, and Warshaw"
TITLE = "American Local Government Elections Database"
STATES = {"Utah": "UT", "Maryland": "MD"}
WORDS = {"21": "Borough", "37": "Municipality", "43": "Town", "47": "Village", "53": "City and Borough"}
BOARDS = {
    **dict.fromkeys(["AZ", "CA", "IA", "MS", "VA", "WI"], "Board of Supervisors"),
    **dict.fromkeys(["AL", "AR", "TN"], "County Commission"),
    "IL": "County Board",
    "KY": "Fiscal Court",
    "LA": "Parish Council",
    "NJ": "Board of County Commissioners",
    "NY": "County Legislature",
    "TX": "Commissioners Court",
}
EXECUTIVES = {
    "AR": "County Judge",
    "KY": "County Judge/Executive",
    "LA": "Parish President",
    "TN": "County Mayor",
    "TX": "County Judge",
}
PROSECUTORS = {
    **dict.fromkeys(["CT", "DE", "IL", "MD", "VT"], "State's Attorney"),
    **dict.fromkeys(["AR", "IN", "MI", "MO", "OH", "WA", "WV"], "Prosecuting Attorney"),
    **dict.fromkeys(["AZ", "IA", "KS", "MN", "MT", "NE", "ND", "UT"], "County Attorney"),
    "FL": "State Attorney",
    "SC": "Solicitor",
    "VA": "Commonwealth's Attorney",
}
METHOD = (
    "Votes and winners are as compiled by the American Local Government Elections Database from local and state "
    "returns; write-in votes are not included. The database prints names in lowercase, so the capitalization shown "
    "is the archive's. Party, gender, and race in the database are statistical estimates and are not recorded here."
)
TACIT = "The database records no vote count for this race, which had a single candidate or was otherwise uncontested."
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)
MONTH = "The database gives only the month of this election, not the day."
YEAR = "The database gives only the year of this election."


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")
    for url in [PLACES, *SCHOOLS]:
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    frame = pl.read_csv(raw.cached(SOURCE, URL).path, infer_schema=False).select(
        pl.col("ledb_candid").alias("candidate"),
        "full_name",
        "fips",
        "geo_name",
        pl.col("state_abb").replace(STATES).alias("state"),
        pl.col("office_consolidated").alias("office"),
        pl.col("year").cast(pl.Int32),
        pl.col("month").cast(pl.Int32, strict=False),
        "district",
        "contest",
        pl.col("votes").cast(pl.Float64).cast(pl.Int64),
        pl.col("n_winners").cast(pl.Int16).alias("magnitude"),
        (pl.col("winner") == "win").alias("won"),
    )
    frame = frame.filter(
        pl.col("fips").str.contains(r"^(\d{5}|\d{7})$"),
        ~((pl.col("state") == "NC") & (pl.col("year") >= 2014)),
        ~((pl.col("fips") == "06075") & (pl.col("office") == "County Legislature")),
    ).with_columns(
        fips=pl.when(pl.col("office") == "School Board").then(pl.lit("sd") + pl.col("fips")).otherwise("fips")
    )
    seats = ["Mayor", "Sheriff", "Prosecutor", "County Executive"]
    contests = (
        frame.filter(pl.col("office").is_in(seats))
        .group_by("contest")
        .agg(
            pl.len().alias("rows"),
            pl.col("votes").sum().alias("votes"),
            pl.first("fips", "office", "year", "month"),
        )
    )
    cycles = {
        key: sorted(set(rows["year"])) for key, rows in contests.filter(pl.col("votes") > 5).group_by("fips", "office")
    }
    phantoms = []
    for contest, rows, votes, fips, office, year, month in contests.select(
        "contest", "rows", "votes", "fips", "office", "year", "month"
    ).iter_rows():
        years = cycles.get((fips, office), [])
        if rows == 1 and (votes or 0) <= 1 and month is not None and len(years) >= 3:
            step = math.gcd(*[other - years[0] for other in years[1:]])
            if step >= 2 and (year - years[0]) % step:
                phantoms.append(contest)
    frame = frame.filter(~pl.col("contest").is_in(phantoms))
    wanted = set(frame.filter(pl.col("fips").str.len_chars() > 5)["fips"])
    records: list[tuple[dict[str, object], str]] = []
    for url in [PLACES, *SCHOOLS]:
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        prefix = "" if url == PLACES else "sd"
        records += [
            (
                {
                    **record.as_dict(),
                    "GEOID": prefix + str(record["GEOID"]),
                    "LSAD": record["LSAD"] if url == PLACES else "SD",
                },
                json.dumps(shape.__geo_interface__),
            )
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and shape is not None and prefix + str(record["GEOID"]) in wanted
        ]
    with connect() as conn:
        places = pl.DataFrame(
            conn.execute(
                """select code, name, state, lsad, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, state, lsad, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[])
                           as f(code, name, state, lsad, geojson)) shapes""",
                [
                    [str(record["GEOID"]) for record, _ in records],
                    [str(record["NAME"]) for record, _ in records],
                    [str(record["STUSPS"]) for record, _ in records],
                    [str(record["LSAD"]) for record, _ in records],
                    [geojson for _, geojson in records],
                ],
            ).fetchall(),
            schema=["code", "name", "state", "lsad", "geom", "point"],
            orient="row",
        )
    missing = frame.filter((pl.col("fips").str.len_chars() > 5) & ~pl.col("fips").is_in(places["code"].implode()))
    print(f"dropped {missing['contest'].n_unique()} contests in {missing['fips'].n_unique()} places missing from 2020")
    frame = frame.filter((pl.col("fips").str.len_chars() == 5) | pl.col("fips").is_in(places["code"].implode()))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"contest": phantoms}, schema={"contest": pl.String}).write_parquet(PARSED / "phantoms.parquet")
    frame.write_parquet(PARSED / "candidates.parquet")
    places.write_parquet(PARSED / "places.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    places = pl.read_parquet(PARSED / "places.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayors, city councils, county boards and executives, sheriffs, prosecutors, and school boards, 1989–2021",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.17605/OSF.IO/MV5E6",
            license="CC BY 4.0",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for url in [PLACES, *SCHOOLS]:
            cs.artifact(raw.cached(SOURCE, url), census)
        cs.add(
            "scheme",
            code="us.school_district",
            polity_id=cs.polity("US"),
            kind="administrative",
            rank=3,
            noun="school district",
        )
        states = {
            iso[3:]: (polity, text)
            for iso, polity, text in conn.execute(
                """select p.iso_code, p.id, n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        }
        polities: dict[str, tuple[UUID, str, str, str]] = {}
        cities: dict[tuple[str, str], str] = {}
        for code, polity, text, path, kind in conn.execute(
            """select c.code, t.polity_id, n.text, s.path, p.kind from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district')"""
        ).fetchall():
            code = f"sd{code}" if kind == "school_district" else code
            polities[code] = (
                polity,
                text,
                path,
                {"municipality": "City", "school_district": "School District"}.get(kind, "County"),
            )
            if len(code) == 5 and text.startswith("City of "):
                cities[(path.split("/")[1], text.removeprefix("City of "))] = code
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.path like 'us/%%' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        aliases: dict[str, str] = {}
        for code, text, usps, lsad, geom, point in places.iter_rows():
            text = re.sub(r"(?: city| (?:consolidated|unified|metro|metropolitan) government)? \(balance\)$", "", text)
            if (usps.lower(), text) in cities:
                aliases[code] = cities[(usps.lower(), text)]
                continue
            if code in polities:
                continue
            school = code.startswith("sd")
            geoid, scheme = code.removeprefix("sd"), "us.school_district" if school else "us.place"
            polity = cs.key("us.polity", f"school:{geoid}" if school else f"place:{code}")
            path = f"us/{usps.lower()}/{slug(text)}"
            if path in taken:
                path = f"us/{usps.lower()}/{slug(text)}-{slug(WORDS.get(lsad, 'City'))}"
            if path in taken:
                raise ValueError(f"{text}, {usps} would take {path}")
            taken.add(path)
            polities[code] = (polity, text, path, WORDS.get(lsad, "School District" if school else "City"))
            cs.add("polity", id=polity, kind="school_district" if school else "municipality", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=states[usps][0])
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area(scheme, geoid)
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=geoid, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", geoid[:2]))
            name(area, f"{text}, {usps}")
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
            if (unit := cs.unit(scheme, geoid)) not in cs.minted:
                continue
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
        cs.add(
            "boundary_set",
            scheme_code="us.school_district",
            vintage="2020",
            valid_from=None,
            valid_to=None,
            as_of=date(2020, 1, 1),
        )
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp and block systems must already exist")

        frame = frame.with_columns(fips=pl.col("fips").replace(aliases))
        owners: set[UUID] = set()
        elections: set[UUID] = set()
        persons: set[UUID] = set()
        districts: set[str] = set()
        paths: set[str] = set()
        merged = {
            value
            for (value,) in conn.execute(
                """select h.value from external_id_history h join entity_merge m on m.entity_id = h.entity_id
                   where h.scheme = 'us.contest' and h.value like 'alged:%'
                   union
                   select x.value from external_id x join contest c on c.id = x.entity_id
                   join changeset s on s.id = c.changeset_id
                   where x.scheme = 'us.contest' and x.value like 'alged:%' and s.pipeline = 'us.wards'"""
            )
        }
        for (contest_id,), rows in frame.sort("contest", "votes", descending=[False, True]).group_by(
            "contest", maintain_order=True
        ):
            # us.wards owns these: San Francisco's merged county supervisor copies, and citywide seats ALGED numbers as districts
            if f"alged:{contest_id}" in merged:
                continue
            first = rows.row(0, named=True)
            code, usps, office, year, month = (
                first["fips"],
                first["state"],
                first["office"],
                first["year"],
                first["month"],
            )
            if code not in polities:
                continue
            polity, place, path, word = polities[code]
            city = len(code) == 5 and word == "City"
            place = place.removeprefix("City of ") if city else place
            geoid = code.removeprefix("sd")
            scheme = {5: "us.county", 7: "us.place"}.get(len(code), "us.school_district")
            if office == "Mayor":
                key, title, kind = "mayor", f"Mayor of {place}", "mayor"
            elif office == "City Council":
                board = {
                    "Village": "Village Board of Trustees",
                    "Municipality": "Assembly",
                    "City and Borough": "Assembly",
                }.get(word, f"{word} Council")
                key, title, kind = "council", f"{place} {board}", None
            elif office == "County Legislature":
                board = "City Council" if city else BOARDS.get(usps, "Board of Commissioners")
                key, title, kind = (
                    "council" if board == "City Council" else slug(board),
                    f"{place} {board}",
                    None,
                )
            elif office == "County Executive":
                role = EXECUTIVES.get(usps, "County Executive")
                key, title, kind = slug(role), f"{place} {role}", "other"
            elif office == "School Board":
                key, title, kind = "board-of-education", f"{place} Board of Education", None
            elif office == "Sheriff":
                key, title, kind = "sheriff", f"{place} Sheriff", "other"
            else:
                role = PROSECUTORS.get(usps, "District Attorney")
                key, title, kind = slug(role), f"{place} {role}", "other"
            title = re.sub(r"\b(County|Parish|City) \1\b", r"\1", title)
            owner = cs.key("us.office" if kind else "us.body", f"{usps}:{code}:{key}")
            if owner in cs.minted and owner not in owners:
                if kind:
                    cs.add("office", id=owner, polity_id=polity, kind=kind)
                else:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                name(owner, title)
            owners.add(owner)

            if month is None:
                day, precision, part = date(year, 1, 1), "year", f"{year}"
            elif month == 11 and usps != "LA":
                day = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
                precision, part = "day", day.isoformat()
            else:
                day, precision, part = date(year, month, 1), "month", f"{year}-{month:02}"
            parent = None
            general = month == 11 and year % 2 == 0
            if precision == "day":
                parent = cs.key("us.election", f"{day}:{usps}")
                if parent in cs.minted and parent not in elections:
                    elections.add(parent)
                    cs.add(
                        "election",
                        id=parent,
                        polity_id=states[usps][0],
                        kind="general" if general else "local",
                        date_from=day,
                        date_to=day,
                        date_precision="day",
                        status="held",
                    )
                    name(parent, f"{year} {states[usps][1]} {'general' if general else 'local'} elections")
                    cs.add(
                        "slug", path=f"us/{usps.lower()}/{day}-{'general' if general else 'local'}", entity_id=parent
                    )
            stage = "general" if general else "local"
            election = cs.key("us.election", f"{part}:{usps}:{code}")
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
                    date_precision=precision,
                    status="held",
                )
                name(election, f"{year} {place} {'general' if general else 'local'} election")
                cs.add("slug", path=f"{path}/{part}-{stage}", entity_id=election)
                if precision != "day":
                    cs.add("note", entity_id=election, kind="caveat", body_md=MONTH if precision == "month" else YEAR)

            text = first["district"] or ""
            seat = None
            if office in ("City Council", "County Legislature", "School Board") and text not in (
                first["geo_name"],
                "NA",
            ):
                label = re.sub(r"^synthetic ", "", text)
                label = re.sub(r"^at-large [a-z]\b", "at-large", label)
                if re.fullmatch(r"0*(\d+[a-z]?|[a-z])", label):
                    seat = f"District {label.lstrip('0').upper()}"
                elif label:
                    seat = re.sub(r"\b([a-z])", lambda found: found[1].upper(), label)
            remainder = "special" in text
            partial = seat is not None and not re.search(r"(?i)at[- ]?large", seat)
            whole = cs.area(scheme, geoid)
            unit = cs.unit(scheme, geoid)
            area = whole
            if partial:
                district = f"{geoid[:2]}:{code}:{key}:{slug(seat)}"
                area = cs.area("us.local_district", district)
                unit = cs.unit("us.local_district", district)
                if area in cs.minted and district not in districts:
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
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
            contest_key = f"alged:{contest_id}"
            contest = cs.key("us.contest", contest_key)
            magnitude = first["magnitude"]
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
                term="remainder" if remainder else "full",
            )
            name(
                contest,
                (f"{year} {place} mayoral election" if key == "mayor" else f"{year} {title} election")
                + (f", {seat}" if seat else "")
                + (" (special)" if remainder else ""),
            )
            contest_path = f"{path}/{part}-{stage}/{key}" + (f"-{slug(seat)}" if seat else "")
            contest_path += "-special" if remainder else ""
            suffix = 1
            while (
                candidate := contest_path if suffix == 1 else f"{contest_path}-{suffix}"
            ) in paths or candidate in taken:
                suffix += 1
            paths.add(candidate)
            cs.add("slug", path=candidate, entity_id=contest)
            if partial:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md=DISTRICT.format(whole=word.lower()),
                )
            tacit = rows.select(pl.col("votes").max() <= 1).item()
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=TACIT if tacit else METHOD)
            for candidate_id, full_name, votes, won in rows.select(
                "candidate", "full_name", "votes", "won"
            ).iter_rows():
                person = cs.key(f"{SOURCE}.person", candidate_id)
                ballot = re.sub(r"\b([a-z])", lambda found: found[1].upper(), full_name)
                ballot = re.sub(r"\bMc([a-z])", lambda found: f"Mc{found[1].upper()}", ballot)
                ballot = re.sub(r"\b(Ii|Iii|Iv)\b", lambda found: found[1].upper(), ballot)
                if person in cs.minted and person not in persons:
                    cs.add("person", id=person)
                    name(person, ballot)
                persons.add(person)
                option = cs.key("us.option", f"{contest_key}:{candidate_id}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=ballot)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{candidate_id}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                )
                if won:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
                if not tacit and result_set in cs.minted:
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
        return cs.write()
