import re
import time
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.chicago_boe import fold
from ea.store import DATA, connect

SOURCE = "us.electionsfl"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
REPORTS = "https://enr.electionsfl.org/{county}/{election}/Reports"
EXPORT = re.compile(
    r"https://s3\.amazonaws\.com/results\.voterfocus\.com/enr/exports/reports/{county}/{election}/"
    r"(?:PrecinctResults|CandidateResultsbyPrecinct)[^\"]+\.csv"
)
JACKSONVILLE = {
    1229: date(2014, 12, 16),
    1245: date(2015, 2, 17),
    1251: date(2015, 3, 24),
    1276: date(2015, 5, 19),
    2085: date(2019, 3, 19),
    2125: date(2019, 5, 14),
    3089: date(2021, 12, 7),
    3124: date(2022, 2, 22),
    3353: date(2023, 3, 21),
    3385: date(2023, 5, 16),
}
COLUMNS = {
    "Contest": "contest",
    "Candidate_Issue": "candidate",
    "Candidate Issue": "candidate",
    "Party": "party",
    "PrecinctCode": "precinct",
    "Precinct Name": "precinct",
    "TotalVotes": "votes",
    "Total Votes": "votes",
}


CDX = "https://web.archive.org/cdx/search/cdx?url=enr.electionsfl.org/*&fl=original&collapse=urlkey&limit=200000"
FIPS = dict(
    zip(
        [
            "ALA",
            "BAK",
            "BAY",
            "BRA",
            "BRE",
            "BRO",
            "CAL",
            "CHA",
            "CIT",
            "CLA",
            "CLL",
            "CLM",
            "DES",
            "DIX",
            "DUV",
            "ESC",
            "FLA",
            "FRA",
            "GAD",
            "GIL",
            "GLA",
            "GUL",
            "HAM",
            "HAR",
            "HEN",
            "HER",
            "HIG",
            "HIL",
            "HOL",
            "IND",
            "JAC",
            "JEF",
            "LAF",
            "LAK",
            "LEE",
            "LEO",
            "LEV",
            "LIB",
            "MAD",
            "MAN",
            "MRN",
            "MRT",
            "DAD",
            "MON",
            "NAS",
            "OKA",
            "OKE",
            "ORA",
            "OSC",
            "PAL",
            "PAS",
            "PIN",
            "POL",
            "PUT",
            "STJ",
            "STL",
            "SAN",
            "SAR",
            "SEM",
            "SUM",
            "SUW",
            "TAY",
            "UNI",
            "VOL",
            "WAK",
            "WAL",
            "WAS",
        ],
        [f"12{n:03d}" for n in [*range(1, 25, 2), *range(27, 86, 2), 86, *range(87, 134, 2)]],
        strict=True,
    )
)


def elections() -> list[tuple[str, int]]:
    text = raw.cached(SOURCE, CDX).path.read_text()
    found = re.findall(r"enr\.electionsfl\.org(?::80)?/([A-Za-z]{3})/(?:Summary/)?(\d+)", text)
    return sorted({(county.upper(), int(number)) for county, number in found if county.upper() in FIPS})


def export(county: str, election: int) -> str | None:
    try:
        page = raw.cached(SOURCE, REPORTS.format(county=county, election=election)).path.read_text(errors="replace")
    except LookupError:
        return None
    found = re.search(EXPORT.pattern.format(county=county, election=election), page)
    return found[0] if found else None


def fetch() -> None:
    raw.fetch(SOURCE, CDX, suffix=".txt")
    for county, election in elections():
        url = REPORTS.format(county=county, election=election)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            try:
                raw.fetch(SOURCE, url, suffix=".html")
            except httpx.HTTPStatusError:
                time.sleep(1)
                continue
            time.sleep(1)
        if (link := export(county, election)) is not None:
            raw.fetch(SOURCE, link, suffix=".csv")


def parse() -> None:
    with connect() as conn:
        names = dict(
            conn.execute(
                """select c.code, upper(regexp_replace(n.text, ' County$', '')) from area a
                   join area_code c on c.area_id = a.id join polity_territory t on t.area_id = a.id
                   join name n on n.entity_id = t.polity_id and n.is_preferred and n.lang = 'en'
                   where a.scheme_code = 'us.county' and c.code like '12___'"""
            ).fetchall()
        )
    frames = []
    for county, election in elections():
        link = export(county, election)
        if link is None:
            continue
        page = raw.cached(SOURCE, REPORTS.format(county=county, election=election)).path.read_text(errors="replace")
        title = re.search(r"<h1>\s*([^<]+)", page)
        held = re.search(r"Election Date:\s*(\d+)/(\d+)/(\d{4})", page)
        if title is None or held is None:
            raise ValueError(f"{county} {election}: no title or date on the Reports page")
        frame = pl.read_csv(raw.cached(SOURCE, link).path, infer_schema=False)
        frame = frame.select(
            [pl.col(column).alias(name) for column, name in COLUMNS.items() if column in frame.columns]
        )
        if set(frame.columns) != {"contest", "candidate", "party", "precinct", "votes"}:
            print(f"{county} {election}: unread columns {frame.columns}")
            continue
        frames.append(
            frame.with_columns(
                pl.col("contest").str.strip_chars().str.replace_all(r"\s+", " "),
                pl.col("candidate").str.strip_chars(),
                pl.col("precinct").str.strip_chars(),
                pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
                county=pl.lit(county),
                fips=pl.lit(FIPS[county]),
                county_name=pl.lit(names[FIPS[county]]),
                election=pl.lit(election),
                day=pl.lit(date(int(held[3]), int(held[1]), int(held[2]))),
                title=pl.lit(re.sub(r"\s+", " ", title[1]).strip()),
            ).unique(maintain_order=True)
        )
    frame = pl.concat(frames, how="diagonal")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.group_by("county").agg(pl.col("election").n_unique().alias("elections"), pl.len()).sort("county"))


RUNOFFS = {("DUV", 1276): 1251, ("DUV", 2125): 2085, ("DUV", 3124): 3089, ("DUV", 3385): 3353}
PLACE = "1235000"
COUNTY_OFFICES = {
    "sheriff": "Sheriff",
    "supervisor of elections": "Supervisor of Elections",
    "property appraiser": "Property Appraiser",
    "tax collector": "Tax Collector",
}
PARTIES = {"DEM": "democratic-party", "REP": "republican-party", "LPF": "libertarian-party"}
METHOD = (
    "Precinct results from the county Supervisor of Elections' election night reporting exports (VR Systems). "
    "Jacksonville elects its mayor, council, and Duval County's constitutional officers in a first election open to "
    "every candidate; a seat no candidate won with a majority goes to the top two in the general election."
)


def seat(contest: str) -> tuple[str, str | None] | None:
    text = contest.lower()
    if text == "mayor":
        return "mayor", None
    if text in COUNTY_OFFICES:
        return text, None
    if found := re.match(r"city council\s*-?\s*at[- ]large - group (\d+)$", text):
        return "council", f"at-large-{found[1]}"
    if found := re.match(r"city council (?:- )?district (\d+)(?: district \d+)?$", text):
        return "council", f"district-{found[1]}"
    return None


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet").filter(
        pl.col("county") == "DUV", pl.col("election").is_in(list(JACKSONVILLE))
    )
    seats = {contest: seat(contest) for contest in frame["contest"].unique()}
    frame = frame.with_columns(
        office=pl.col("contest").replace_strict({k: v[0] for k, v in seats.items() if v}, default=None),
        seat=pl.col("contest").replace_strict({k: v[1] for k, v in seats.items() if v}, default=None),
    ).filter(pl.col("office").is_not_null())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Jacksonville mayor and City Council and Duval County's constitutional officers 2015-2023 by precinct "
                "from the Duval County Supervisor of Elections, general elections chained to their first elections"
            ),
            polity="US-FL",
        )
        cs.scope_units("us.precinct", "%:FL:12031:DUVAL:%")

        def name(
            entity: UUID, text: str, reason: str = "Renamed to match the Supervisor of Elections' returns"
        ) -> None:
            found = conn.execute("select id, text from name where entity_id = %s and is_preferred", [entity]).fetchone()
            if found is not None and found[1] == text:
                return
            if found is not None:
                cs.add("name_drop", name_id=found[0], entity_id=entity, text=found[1], reason=reason)
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        system = cs.key("us.electoral_system", "two_round")
        if system in cs.minted:
            raise ValueError("the two-round system must already exist")
        publisher = "Duval County Supervisor of Elections"
        title = "Election night reporting precinct results (CSV exports)"
        source = cs.source(publisher, title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=publisher,
            title=title,
            url="https://enr.electionsfl.org/DUV/",
            license="Public record",
        )
        for number in JACKSONVILLE:
            cs.artifact(raw.cached(SOURCE, REPORTS.format(county="DUV", election=number)), source)
            cs.artifact(raw.cached(SOURCE, str(export("DUV", number))), source)
        polities = {}
        for code in (PLACE, "12031"):
            found = conn.execute(
                "select t.polity_id from area_code c join polity_territory t on t.area_id = c.area_id where c.code = %s",
                [code],
            ).fetchone()
            if found is None:
                raise ValueError(f"no polity holds area {code}")
            polities[code] = found[0]
        found = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = '12031'"
        ).fetchone()
        if found is None:
            raise ValueError("Duval County's unit is missing")
        county_unit, county_area = found
        current = dict(conn.execute("select entity_id, path from slug where is_current").fetchall())
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/fl/%%'").fetchall()}
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "completeness")
        people: dict[tuple[str, str], UUID] = {}
        preferred: dict[UUID, str] = {}
        for person, ballot, text in conn.execute(
            """select cy.person_id, coalesce(cy.ballot_name, o.ballot_label), n.text
               from candidacy cy join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id join election e on e.id = k.election_id
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where e.polity_id = any(%s) order by e.date_from""",
            [list(polities.values())],
        ).fetchall():
            words = fold(ballot)
            people[(words[0], words[-1])] = person
            preferred[person] = text
        renamed: set[UUID] = set()
        elections: dict[tuple[date, str], tuple[UUID, str]] = {}
        firsts: dict[tuple[str, int, str, str | None], UUID] = {}
        minted: set[str] = set()
        values: list[tuple[str, str, str, int]] = []
        report = {"taken over": 0, "new": 0, "unmatched options": 0}

        def place(entity: UUID, path: str) -> str:
            if current.get(entity) == path:
                return path
            base, n = path, 1
            while path in taken:
                n += 1
                path = f"{base}-{n}"
            taken.add(path)
            cs.add("slug", path=path, entity_id=entity)
            current[entity] = path
            return path

        def election(day: date, runoff: bool, code: str) -> tuple[UUID, str]:
            if (day, code) in elections:
                return elections[(day, code)]
            kind = "runoff" if runoff else "local"
            parent = cs.key("us.election", f"{day}:FL")
            if parent in cs.minted:
                cs.add(
                    "election",
                    id=parent,
                    polity_id=cs.polity("US-FL"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} Florida {kind} elections")
                place(parent, f"us/fl/{day}-{kind}")
            keys = [f"{day:%Y-%m}:FL:{code}", f"{day}:FL:{code}"] + ([f"{day.year}:FL:{code}"] if code != PLACE else [])
            found = None
            for key in keys:
                found = conn.execute(
                    "select entity_id from external_id where scheme = 'us.election' and value = %s", [key]
                ).fetchone()
                if found is not None and found[0] not in {entity for entity, _ in elections.values()}:
                    break
                found = None
            entity = found[0] if found else cs.key("us.election", f"{day}:FL:{code}")
            cs.add(
                "election",
                id=entity,
                polity_id=polities[code],
                parent_id=parent,
                kind=kind,
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            label = "Jacksonville" if code == PLACE else "Duval County"
            stem = "jacksonville" if code == PLACE else "duval-county"
            name(entity, f"{day.year} {label} {'general' if runoff else 'first'} election")
            elections[(day, code)] = (entity, place(entity, f"us/fl/{stem}/{day}-{kind}"))
            return elections[(day, code)]

        last_round = {
            (county, year, office, seat_): day
            for county, year, office, seat_, day in frame.group_by(
                "county", pl.col("day").dt.year().alias("year"), "office", "seat"
            )
            .agg(pl.col("day").max())
            .iter_rows()
        }
        for (county, number, day, office, seat_), rows in frame.sort("day", "office", "seat").group_by(
            "county", "election", "day", "office", "seat", maintain_order=True
        ):
            runoff = (county, number) in RUNOFFS
            code = PLACE if office in ("mayor", "council") else "12031"
            election_id, path = election(day, runoff, code)
            stem = (
                "mayor"
                if office == "mayor"
                else f"council-{seat_}"
                if office == "council"
                else office.replace(" ", "-")
            )
            if office == "mayor":
                existing = [
                    f"alged:{PLACE}_{day.year}_{day.month}_jacksonville_FL_Mayor_mayor_1",
                    f"wikipedia:{day}:FL:{PLACE}:mayor",
                ]
            elif office == "council":
                number_ = seat_.rsplit("-", 1)[1]
                label = f"at-large {number_}" if seat_.startswith("at-large") else number_
                existing = [f"alged:{PLACE}_{day.year}_{day.month}_jacksonville_FL_City Council_{label}_1"]
            elif office == "sheriff" and last_round[(county, day.year, office, seat_)] == day:
                existing = [f"alged:12031_{day.year}_NA_duval_FL_Sheriff_000_1"]
            else:
                existing = []
            found = conn.execute(
                "select entity_id, value from external_id where scheme = 'us.contest' and value = any(%s)", [existing]
            ).fetchone()
            contest_key = found[1] if found else f"electionsfl:{day}:FL:{code}:{stem}"
            contest = cs.key("us.contest", contest_key)
            report["taken over" if found else "new"] += 1
            if office == "council":
                owner = cs.key("us.body", f"FL:{PLACE}:council")
            else:
                owner = cs.key("us.office", f"FL:{code}:{'mayor' if office == 'mayor' else stem}")
            if owner in cs.minted and owner not in renamed:
                renamed.add(owner)
                cs.add("office", id=owner, polity_id=polities[code], kind="other")
                name(owner, f"Duval County {COUNTY_OFFICES[office]}")
            previous = None
            if runoff:
                previous = firsts.get((county, RUNOFFS[(county, number)], office, seat_))
            else:
                firsts[(county, number, office, seat_)] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="body_seats" if office == "council" else "office",
                office_id=None if office == "council" else owner,
                body_id=owner if office == "council" else None,
                district_area_id=cs.area("us.local_district", f"12:{PLACE}:council:{seat_}")
                if seat_ is not None and seat_.startswith("district")
                else cs.area("us.place", PLACE)
                if code == PLACE
                else county_area,
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
                term="full",
            )
            title = (
                "mayoral" if office == "mayor" else "City Council" if office == "council" else COUNTY_OFFICES[office]
            )
            where = "Jacksonville" if code == PLACE else "Duval County"
            suffix = ""
            if seat_ is not None:
                suffix = (
                    f", At-Large Group {seat_.rsplit('-', 1)[1]}"
                    if seat_.startswith("at-large")
                    else f", District {seat_.rsplit('-', 1)[1]}"
                )
            name(contest, f"{day.year} {where} {title} {'runoff' if runoff else 'election'}{suffix}")
            place(contest, f"{path}/{stem}")
            prior = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchone()
            if prior is not None and prior[5] != source:
                cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False, status="superseded")
            else:
                prior = None
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
                supersedes_id=prior[0] if prior else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            options: list[tuple[list[str], UUID, UUID | None, UUID | None]] = []
            for option, label, person, candidacy in conn.execute(
                """select o.id, o.ballot_label, cy.person_id, cy.id from contest_option o
                   left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   where o.contest_id = %s and o.kind = 'candidate'""",
                [contest],
            ).fetchall():
                options.append((fold(label), option, person, candidacy))
            totals = (
                rows.group_by("candidate", "party", maintain_order=True)
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
            )
            chosen: dict[str, UUID] = {}
            ranked: list[tuple[int, UUID]] = []
            write_ins = 0
            for candidate, party, votes in totals.iter_rows():
                if candidate.upper() == "WRITE-IN":
                    option = cs.key("us.option", f"{contest_key}:write_in")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    chosen[candidate] = option
                    write_ins += votes
                    continue
                label = candidate
                words = fold(label)
                match = next((entry for entry in options if entry[0][-1] == words[-1]), None) or next(
                    (
                        entry
                        for entry in options
                        if entry[0][0] == words[0]
                        and (
                            set(entry[0][1:]) & set(words[1:])
                            or SequenceMatcher(None, entry[0][-1], words[-1]).ratio() >= 0.8
                        )
                    ),
                    None,
                )
                option, person, candidacy = (None, None, None) if match is None else match[1:]
                if match is not None:
                    options.remove(match)
                person = person or people.get((words[0], words[-1]))
                if person is None:
                    person = cs.key(f"{SOURCE}.person", f"{county}:{words[0]}:{words[-1]}")
                    people[(words[0], words[-1])] = person
                    if str(person) not in minted:
                        minted.add(str(person))
                        cs.add("person", id=person)
                        name(person, label)
                elif person not in renamed and set(fold(preferred.get(person, label))) < set(words):
                    renamed.add(person)
                    name(person, label, "The Supervisor of Elections' returns give the full name")
                option = option or cs.key("us.option", f"{contest_key}:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", PARTIES[party]) if party in PARTIES else None,
                    ballot_label=label,
                )
                cs.add(
                    "candidacy",
                    id=candidacy or cs.key("us.candidacy", f"{contest_key}:{slug(label)}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=label,
                    is_independent=party == "NPA",
                )
                chosen[candidate] = option
                ranked.append((votes, option))
            report["unmatched options"] += len(options)
            total = sum(votes for votes, _ in ranked) + write_ins
            decided = runoff or ranked[0][0] * 2 > total or len(ranked) == 1
            for rank, (_, option) in enumerate(ranked):
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=("elected" if decided else "advanced") if rank < (1 if decided else 2) else "lost",
                )
            for precinct, candidate, votes in rows.select("precinct", "candidate", "votes").iter_rows():
                code_ = f"{day}:FL:12031:DUVAL:{precinct}"
                unit = cs.unit("us.precinct", code_)
                if unit in cs.minted and code_ not in minted:
                    minted.add(code_)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=cs.polity("US-FL"),
                        scheme_code="us.precinct",
                        code=code_,
                        parent_id=county_unit,
                        area_relation="within",
                        area_id=county_area,
                        kind="standard",
                        valid_from=day,
                        valid_to=date.fromordinal(day.toordinal() + 1),
                    )
                values.append((str(result_set), str(unit), str(chosen[candidate]), votes))
        print(report)
        cs.frame(
            "result_value",
            pl.DataFrame(values, schema=["result_set_id", "unit_id", "option_id", "value"], orient="row")
            .group_by("result_set_id", "unit_id", "option_id")
            .agg(pl.col("value").sum())
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
