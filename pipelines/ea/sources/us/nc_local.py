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
from ea.sources.us.ncsbe import FILES, PUBLISHER, TITLE
from ea.sources.us.ncsbe import SOURCE as NCSBE
from ea.store import DATA, connect

SOURCE = "us.nc_local"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
DAYS = [date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in range(2014, 2026)]
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_37_place_500k.zip"
KINDS = {"25": "City", "43": "Town", "47": "Village"}
COLUMNS = {
    "county": "county",
    "precinct": "precinct",
    "contest": "contest",
    "contest name": "contest",
    "choice": "choice",
    "party": "party",
    "choice party": "party",
    "total votes": "votes",
    "vote for": "vote_for",
}
OFFICES = {
    "board-of-commissioners-chair": (
        r"(?:COUNTY )?COMMISSION CHAIR(?:MAN)?|(?:BOARD OF )?COMMISSIONERS? CHAIR(?:MAN)?",
        "Chair of the Board of Commissioners",
    ),
    "board-of-commissioners": (
        r"(?:COUNTY )?(?:(?:BOARD|BD) OF ?(?:COUNTY )?COMMISS?I[O0]NERS?|COMMISS?I[O0]NERS?)",
        "Board of Commissioners",
    ),
    "board-of-education-chair": (
        r"(?:COUNTY )?SCHOOL BOARD CHAIR(?:MAN)?|BOARD OF EDUCATION CHAIR(?:MAN)?",
        "Chair of the Board of Education",
    ),
    "board-of-education": (
        r"(?:COUNTY[- ]?(?:WIDE )?)?(?:PUBLIC )?(?:SCHOOLS? )?(?:SYSTEM )?(?:(?:BOARD|BD) OF EDU\w*|SCHOOL BOARD)",
        "Board of Education",
    ),
    "soil-and-water": (
        r"(?:COUNTY )?SOIL (?:AND |& )?WATER CONSERVATI?ON DISTRICT SUPERVISO?R?S?",
        "Soil and Water Conservation District Board of Supervisors",
    ),
    "sheriff": (r"(?:COUNTY )?SHERIFF", "Sheriff"),
    "register-of-deeds": (r"(?:COUNTY )?REGISTER OF DEEDS", "Register of Deeds"),
    "clerk-of-superior-court": (r"(?:COUNTY )?CLERK OF (?:THE )?(?:SUPERIOR )?COURT", "Clerk of Superior Court"),
    "coroner": (r"(?:COUNTY )?CORONER", "Coroner"),
    "surveyor": (r"(?:COUNTY )?SURVEYOR", "Surveyor"),
}
BODIES = {"board-of-commissioners", "board-of-education", "soil-and-water", "council"}
MUNICIPAL = re.compile(
    r"(?:CITY |TOWN |VILLAGE )?(?:(MAYOR)|(COUNCIL)(?: ?(?:MEMBERS?|M[AE]N|WOMAN|PERSONS?))?|BOARD OF (COMMISSIONERS)"
    r"|(COMMISSIONERS?)|BOARD OF (ALDERMEN)|(ALDERM[AE]N)|BOARD OF (TRUSTEES)|(TRUSTEES?))\b\s*-?\s*(.*)"
)
BOARDS = {"COUNCIL": "{kind} Council", "COMMISSION": "Board of Commissioners", "ALDERM": "Board of Aldermen"}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "GRE": "green-party",
    "CST": "constitution-party",
}
METHOD = (
    "Totals are the sums of the precinct, early voting, absentee, and provisional rows in the State Board's "
    "precinct file. The file does not mark winners, so the winners shown are the candidates with the most votes, as "
    "many as the contest elects."
)
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def fetch() -> None:
    for day in DAYS:
        raw.fetch(NCSBE, FILES.format(day=day), suffix=".zip")
    raw.fetch(SOURCE, PLACES, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^A-Z]", "", text.upper()): (code, text)
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
        with zipfile.ZipFile(raw.cached(SOURCE, PLACES).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        records = [
            (record.as_dict(), json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None and record["LSAD"] in KINDS
        ]
        places = pl.DataFrame(
            conn.execute(
                """select code, name, kind, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, kind, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, name, kind, geojson)) shapes""",
                [
                    [str(record["GEOID"]) for record, _ in records],
                    [str(record["NAME"]) for record, _ in records],
                    [KINDS[str(record["LSAD"])] for record, _ in records],
                    [geojson for _, geojson in records],
                ],
            ).fetchall(),
            schema=["code", "name", "kind", "geom", "point"],
            orient="row",
        )
    towns = {re.sub(r"[^A-Z]", "", name.upper()): code for code, name in places.select("code", "name").iter_rows()}
    contests: list[dict[str, object]] = []
    results: list[pl.DataFrame] = []
    for day in DAYS:
        with zipfile.ZipFile(raw.cached(NCSBE, FILES.format(day=day)).path) as archive:
            text = archive.read(archive.namelist()[0]).replace(b"\x00", b"")
        first = text.split(b"\n", 1)[0]
        frame = pl.read_csv(
            io.BytesIO(text),
            separator="\t" if b"\t" in first else ",",
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
        )
        frame = frame.rename(
            {column: COLUMNS[column.strip().lower()] for column in frame.columns if column.strip().lower() in COLUMNS}
        )
        frame = frame.select(
            "county",
            "precinct",
            pl.col("contest").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("choice").str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("party").fill_null("").str.strip_chars(),
            pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
            pl.col("vote_for").cast(pl.Int16),
        ).filter(pl.col("choice").is_not_null())
        labels: list[dict[str, object]] = []
        for county, contest in frame.select("county", "contest").unique().iter_rows():
            fips, short = counties[re.sub(r"[^A-Z]", "", county.upper())]
            name = re.sub(r"\s*\(\d+ SEATS?\)", "", contest)
            name = re.sub(r"\s*-?\s*\(?\bUN-?EX\w*\b.*$", "", name)
            remainder = re.search(r"\bUN-?EX", contest) is not None
            if re.search(r"REFERENDUM|STRUCTURE|METHOD|RUNOFF|BONDS?\b|ELECTION$", name):
                continue
            code, word = fips, None
            name = re.sub(rf"^{re.escape(short.upper())}( COUNTY)?( -)? ", "", name)
            office = next(
                (
                    (key, found[1])
                    for key, (pattern, _) in OFFICES.items()
                    if (found := re.fullmatch(rf"(?:{pattern})\b\s*-?\s*(.*)", name))
                ),
                None,
            )
            if office is not None and office[1].startswith("OF "):
                continue
            prefix = re.match(r"(?:CITY|TOWN|VILLAGE) OF (.+)", name)
            if prefix:
                words = prefix[1].split(" ")
                town = next(
                    (
                        (towns[key], " ".join(words[size:]))
                        for size in range(len(words), 0, -1)
                        if (key := re.sub(r"[^A-Z]", "", " ".join(words[:size]))) in towns
                    ),
                    None,
                )
                found = MUNICIPAL.fullmatch(town[1]) if town else None
                if town is None or found is None:
                    continue
                groups = found.groups()
                code, word = town[0], next(group for group in groups[:-1] if group)
                office = ("mayor" if word == "MAYOR" else "council", groups[-1])
            if office is None:
                continue
            seat = re.sub(r"\bTWP\b", "TOWNSHIP", re.sub(r"\bDIST\b", "DISTRICT", office[1]))
            seat = re.sub(r"\b0+(\d)", r"\1", seat).strip(" -")
            seat = re.sub(
                r"\b([A-Z]+)\b",
                lambda word: word[1] if re.fullmatch(r"[IVX]+|[A-Z]", word[1]) else word[1].capitalize(),
                seat,
            )
            seat = re.sub(r"^(Members?|Seats?)$", "", re.sub(r"(?i)^at[- ]large$", "At-Large", seat)) or None
            if seat in ("Chair", "Chairman") and f"{office[0]}-chair" in OFFICES:
                office, seat = (f"{office[0]}-chair", ""), None
            labels.append(
                {
                    "county": county,
                    "contest": contest,
                    "code": code,
                    "office": office[0],
                    "seat": seat,
                    "remainder": remainder,
                    "word": next((board for board in BOARDS if word and word.startswith(board)), None),
                }
            )
        frame = frame.join(pl.DataFrame(labels, infer_schema_length=None), on=["county", "contest"])
        widths = dict(
            frame.group_by("code", "contest")
            .agg(pl.struct("county", "precinct").n_unique())
            .group_by("code")
            .agg(pl.col("county").max())
            .iter_rows()
        )
        for (code, contest), rows in frame.group_by("code", "contest"):
            first = rows.row(0, named=True)
            contests.append(
                {
                    "day": day,
                    "code": code,
                    "contest": contest,
                    "office": first["office"],
                    "seat": first["seat"],
                    "remainder": first["remainder"],
                    "word": first["word"],
                    "district": rows.select(pl.struct("county", "precinct").n_unique()).item() < 0.9 * widths[code],
                    "magnitude": rows["vote_for"].max(),
                }
            )
            results.append(
                rows.group_by("choice", "party")
                .agg(pl.col("votes").sum())
                .with_columns(day=pl.lit(day), code=pl.lit(code), contest=pl.lit(contest))
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(contests, infer_schema_length=None)
    duplicated = frame.filter(pl.struct("day", "code", "office", "seat", "remainder").is_duplicated())
    if duplicated.height:
        raise ValueError(f"contests that share a key: {duplicated.rows()[:10]}")
    frame.write_parquet(PARSED / "contests.parquet")
    pl.concat(results).write_parquet(PARSED / "results.parquet")
    places.write_parquet(PARSED / "places.parquet")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    results = pl.read_parquet(PARSED / "results.parquet")
    places = pl.read_parquet(PARSED / "places.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's county and municipal elections, 2014–2025",
            polity="US-NC",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        for day in DAYS:
            cs.artifact(raw.cached(NCSBE, FILES.format(day=day)), source)
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        cs.artifact(raw.cached(SOURCE, PLACES), census)
        us = cs.polity("US")
        state = cs.polity("US-NC")
        polities = {
            code: (polity, text, path, "county")
            for code, polity, text, path in conn.execute(
                """select c.code, t.polity_id, n.text, s.path from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id
                   where a.scheme_code = 'us.county' and c.code like '37%'"""
            ).fetchall()
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/nc/%'").fetchall()}
        for code, text, kind, geom, point in places.iter_rows():
            polity = cs.key("us.polity", f"place:{code}")
            path = f"us/nc/{slug(text)}"
            if path in taken and polity in cs.minted:
                raise ValueError(f"{text} would take {path}")
            polities[code] = (polity, text, path, kind)
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.place", code)
            cs.add("area", id=area, scheme_code="us.place")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "37"))
            name(area, f"{text}, NC")
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
                id=cs.unit("us.place", code),
                polity_id=polity,
                scheme_code="us.place",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        cs.add("electoral_system", id=systems["block"], polity_id=us, rules={"formula": "block"}, rules_version=1)
        name(systems["block"], "Block vote")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {systems["fptp"], *parties.values()}:
            raise ValueError("the fptp system and the parties must already exist")
        cs.add("scheme", code="us.local_district", polity_id=us, kind="electoral_district", rank=3, noun="district")

        owners: dict[tuple[str, str], UUID] = {}
        elections: dict[tuple[date, str], UUID] = {}
        persons: dict[tuple[str, str], UUID] = {}
        districts: set[str] = set()
        for row in contests.sort("day", "code", "office", "seat", descending=[True, False, False, False]).iter_rows(
            named=True
        ):
            day, code, office = row["day"], row["code"], row["office"]
            polity, place, path, kind = polities[code]
            town = len(code) == 7
            scheme = "us.place" if town else "us.county"
            if office == "mayor":
                title = f"Mayor of {place}"
            elif office == "council":
                title = f"{place} {BOARDS[row['word']].format(kind=kind)}"
            elif office == "soil-and-water":
                title = f"{place.removesuffix(' County')} {OFFICES[office][1]}"
            else:
                title = f"{place} {OFFICES[office][1]}"
            if (code, office) not in owners:
                owner = owners[(code, office)] = cs.key(
                    "us.body" if office in BODIES else "us.office", f"NC:{code}:{office}"
                )
                if office in BODIES:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                else:
                    cs.add("office", id=owner, polity_id=polity, kind="mayor" if office == "mayor" else "other")
                name(owner, title)
            general = day.year % 2 == 0
            kind_of = "general" if general else "local"
            if not general and (day, "37") not in elections:
                umbrella = elections[(day, "37")] = cs.key("us.election", f"{day}:NC")
                cs.add(
                    "election",
                    id=umbrella,
                    polity_id=state,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(umbrella, f"{day.year} North Carolina municipal elections")
                cs.add("slug", path=f"us/nc/{day}-local", entity_id=umbrella)
            if (day, code) not in elections:
                election = elections[(day, code)] = cs.key("us.election", f"{day}:NC:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=cs.key("us.election", f"{day}:NC"),
                    kind=kind_of,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {place} {'general' if general else 'municipal'} election")
                cs.add("slug", path=f"{path}/{day}-{kind_of}", entity_id=election)
            election = elections[(day, code)]
            whole = cs.area(scheme, code)
            unit = cs.unit(scheme, code)
            seat = row["seat"]
            if row["district"]:
                district = f"37:{code}:{office}:{slug(seat or 'whole')}"
                area = cs.area("us.local_district", district)
                unit = cs.unit("us.local_district", district)
                if district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    name(area, f"{title}, {seat}" if seat else title)
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
            contest_key = (
                f"{day}:NC:{code}:{office}"
                + (f":{slug(seat)}" if seat else "")
                + (":remainder" if row["remainder"] else "")
            )
            contest = cs.key("us.contest", contest_key)
            magnitude = row["magnitude"]
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if office in BODIES else "office",
                office_id=None if office in BODIES else owners[(code, office)],
                body_id=owners[(code, office)] if office in BODIES else None,
                district_area_id=area if row["district"] else whole,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                default_result_kind="counted",
                term="remainder" if row["remainder"] else "full",
            )
            name(
                contest,
                (f"{day.year} {place} mayoral election" if office == "mayor" else f"{day.year} {title} election")
                + (f", {seat}" if seat else "")
                + (" (unexpired term)" if row["remainder"] else ""),
            )
            cs.add(
                "slug",
                path=f"{path}/{day}-{kind_of}/{office}"
                + (f"-{slug(seat)}" if seat else "")
                + ("-unexpired" if row["remainder"] else ""),
                entity_id=contest,
            )
            if row["district"]:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md=DISTRICT.format(whole=kind.lower() if town else "county"),
                )
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

            lines = (
                results.filter(
                    (pl.col("day") == day) & (pl.col("code") == code) & (pl.col("contest") == row["contest"])
                )
                .with_columns(writein=pl.col("choice").str.contains(r"(?i)write-?\s*in"))
                .group_by(pl.when("writein").then(pl.lit("Write-ins")).otherwise("choice").alias("choice"), "writein")
                .agg(pl.col("party").first(), pl.col("votes").sum())
                .sort("votes", "choice", descending=[True, False])
            )
            rank = 0
            for choice, writein, party, votes in lines.iter_rows():
                if writein:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    rank += 1
                    if (code, slug(choice)) not in persons:
                        persons[(code, slug(choice))] = cs.key(f"{SOURCE}.person", f"{code}:{slug(choice)}")
                        cs.add("person", id=persons[(code, slug(choice))])
                        name(persons[(code, slug(choice))], choice)
                    person = persons[(code, slug(choice))]
                    option = cs.key("us.option", f"{contest_key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party),
                        ballot_label=choice,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=choice,
                        is_independent=party == "UNA",
                    )
                    if rank <= magnitude:
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
        return cs.write()
