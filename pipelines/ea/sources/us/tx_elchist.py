import html
import re
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.tx_elchist"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://elections.sos.state.tx.us/"
STATEWIDE = re.compile(
    r"^(President/Vice-President|U\. ?S\. Senator|Governor|Lieutenant Governor|Attorney General|Comptroller of Public Accounts"
    r"|Commissioner of the General Land Office|Commissioner of Agriculture|Railroad Commissioner|State Treasurer"
    r"|Chief Justice, Supreme Court|Justice, Supreme Court, Place \d|Presiding Judge, Court of Criminal Appeals"
    r"|Judge, Court of Criminal Appeals Place \d)( - Unexpired Term)?$"
)
PUBLISHER = "Texas Secretary of State"
TITLE = "Texas election history: statewide returns by county, 1992–1998"
LATER = "Texas election history: statewide returns by county, 2000–2014"
SEPARATE = {"David Young"}
DAYS = {12: date(1993, 6, 5)}
OFFICES = {
    "Governor": "Governor",
    "Lieutenant Governor": "Lieutenant Governor",
    "Attorney General": "Attorney General",
    "Comptroller of Public Accounts": "Comptroller",
    "Commissioner of the General Land Office": "Land Commissioner",
    "Commissioner of Agriculture": "Commissioner of Agriculture",
    "Railroad Commissioner": "Railroad Commissioner",
    "State Treasurer": "Treasurer",
    "Chief Justice, Supreme Court": "Chief Justice of the Supreme Court",
    "Justice, Supreme Court": "Justice of the Supreme Court",
    "Presiding Judge, Court of Criminal Appeals": "Presiding Judge of the Court of Criminal Appeals",
    "Judge, Court of Criminal Appeals": "Judge of the Court of Criminal Appeals",
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "GRN": "green-party",
    "NLP": "natural-law-party",
    "UST": "TX:u-s-taxpayers-party",
    "SWP": "socialist-workers-party",
}
RUNOFF_DATE = (
    "The Secretary of State's page dates this runoff 6/2/1993. The runoff was held on Saturday, June 5, 1993, "
    "and we show it on that date."
)
ELECTIONS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 52, 53, 54, 55, 56, 57, 58, 62, 63, 69, 70, 72)
GENERALS = (82, 95, 114, 127, 141, 154, 164, 175)


def races(election: int) -> list[tuple[int, str]]:
    text = raw.cached(SOURCE, f"{SITE}elchist{election}_raceselect.htm").path.read_text(errors="replace")
    return [
        (int(found[1]), re.sub(r"\s+", " ", found[2]).strip())
        for found in re.finditer(r'<OPTION value="?(\d+)"?[^>]*>([^<]*)', text, re.IGNORECASE)
    ]


def fetch() -> None:
    raw.fetch(SOURCE, f"{SITE}index.htm", suffix=".htm")
    for election in (*ELECTIONS, *GENERALS):
        raw.fetch(SOURCE, f"{SITE}elchist{election}_raceselect.htm", suffix=".htm")
        time.sleep(1)
        for race, title in races(election):
            if STATEWIDE.match(title):
                url = f"{SITE}elchist{election}_race{race}.htm"
                try:
                    raw.cached(SOURCE, url)
                except LookupError:
                    raw.fetch(SOURCE, url, suffix=".htm")
                    time.sleep(1)


def cells(row: str, tag: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        for cell in re.findall(rf"<{tag}[^>]*>(.*?)(?=<{tag}[\s>]|</TR>|$)", row, re.IGNORECASE | re.DOTALL)
    ]


def parse() -> None:
    rows: list[dict[str, object]] = []
    for election in (*ELECTIONS, *GENERALS):
        for race, title in races(election):
            if not STATEWIDE.match(title):
                continue
            text = raw.cached(SOURCE, f"{SITE}elchist{election}_race{race}.htm").path.read_text(errors="replace")
            heading = re.search(r"<H2[^>]*>([^<]+)</H2><H3[^>]*>(\d+)/(\d+)/(\d{4})</H3>", text, re.IGNORECASE)
            if heading is None:
                raise ValueError(f"{election} {race}: no heading")
            day = DAYS.get(election, date(int(heading[4]), int(heading[2]), int(heading[3])))
            lines = re.findall(r"<TR[^>]*>(.*?)</TR>", text, re.IGNORECASE | re.DOTALL)
            headers = [cells(line, "TH") for line in lines if re.search(r"<TH", line, re.IGNORECASE)]
            data = [cells(line, "TD") for line in lines if re.search(r"<TD", line, re.IGNORECASE)]
            parties = headers[-1]
            names = headers[:-1]
            width = parties.index("Votes")
            ticket = any(cell.endswith("/") for cell in names[0][1:width]) if names else False
            candidates: list[tuple[str, str | None, str]] = []
            for column in range(1, width):
                parts = [row[column] for row in names if column < len(row) and row[column] not in ("...", "Total")]
                if ticket:
                    candidates.append(
                        (parts[0].rstrip("/").strip(), parts[1] if len(parts) > 1 else None, parties[column])
                    )
                elif "/" in " ".join(parts):
                    head, mate = " ".join(parts).split("/", 1)
                    candidates.append((head.strip(), mate.strip(), parties[column]))
                else:
                    candidates.append((" ".join(parts), None, parties[column]))
            total = next(row for row in data if row[0] == "ALL COUNTIES")
            counties = [row for row in data if row[0] != "ALL COUNTIES"]
            if len(counties) != 254:
                raise ValueError(f"{election} {race}: {len(counties)} counties")
            for column, (candidate, mate, party) in enumerate(candidates, start=1):
                votes = [int(row[column].replace(",", "") or 0) for row in counties]
                if sum(votes) != int(total[column].replace(",", "")):
                    raise ValueError(
                        f"{election} {race} {candidate}: counties sum to {sum(votes)}, not {total[column]}"
                    )
                rows.extend(
                    {
                        "election": election,
                        "name": heading[1].strip(),
                        "day": day,
                        "race": title,
                        "candidate": candidate,
                        "mate": mate,
                        "party": party,
                        "county": row[0],
                        "votes": value,
                    }
                    for row, value in zip(counties, votes, strict=True)
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("election", "name", "day", "race").agg(pl.col("candidate").n_unique()).sort("day", "race"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas statewide elections of 1992 to 1998 and generals of 2000 to 2014 by county from the Secretary "
            "of State's election history",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [
                word
                for word in re.sub(r"\([^)]*\)|'[^']*'", " ", text).replace(",", " ").split()
                if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}
            ]
            return plain(words[-1]) if words else ""

        def full(text: str) -> str:
            return plain(re.sub(r"\([^)]*\)|'[^']*'|,? (Jr|Sr)\.?$", " ", text))

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license="Public record",
        )
        later = cs.source(PUBLISHER, LATER)
        cs.add(
            "source",
            id=later,
            kind="official_body",
            publisher=PUBLISHER,
            title=LATER,
            url=SITE,
            license="Public record",
        )
        pages: dict[tuple[int, str], str] = {}
        for election in (*ELECTIONS, *GENERALS):
            for race, title in races(election):
                if STATEWIDE.match(title):
                    pages[(election, title)] = f"{SITE}elchist{election}_race{race}.htm"
        texas = cs.polity("US-TX")
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-TX'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties: dict[str, str] = {
            plain(text): str(unit)
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '48%%'"""
            ).fetchall()
        }
        missing = sorted({county for county in frame["county"] if plain(county) not in counties})
        if missing:
            raise ValueError(f"unmatched counties: {missing}")
        ((body, seat),) = conn.execute(
            """select c.body_id, c.seat_id from contest c join external_id e on e.entity_id = c.id
               where e.scheme = 'us.contest' and e.value = '1994-11-08:TX:senate'"""
        ).fetchall()
        known_elections = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")
        }
        known_offices = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        contests: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ '^(199[2-8]|20(0[0-9]|1[0-4])).*:TX:'"
            ).fetchall()
        )
        people: dict[str, set[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id join polity p on p.id = e.polity_id and p.iso_code = 'US-TX'
               join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.district_area_id = %s""",
            [area],
        ).fetchall():
            people.setdefault(full(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "two_round")}
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {*systems.values(), *parties.values()}:
            raise ValueError("the electoral systems and parties must already exist")
        firsts: dict[tuple[int, str], UUID] = {}
        filled = 0
        made: set[UUID] = set()
        for (election, label, day, race), lines in frame.sort("day", "election").group_by(
            "election", "name", "day", "race", maintain_order=True
        ):
            party = "democratic" if "Democratic" in label else "republican" if "Republican" in label else None
            stage = (
                "general"
                if "General" in label
                else "runoff"
                if "Runoff" in label
                else "special"
                if "Special" in label
                else "primary"
            )
            special = "Special" in label
            if race == "President/Vice-President" and party is not None:
                continue
            if (lines["candidate"].str.strip_chars() == "").any():
                print(f"{label}, {race}: the page names no candidates, skipped")
                continue
            unexpired = race.endswith(" - Unexpired Term")
            title = race.removesuffix(" - Unexpired Term")
            post = None
            if found := re.search(r",? Place (\d)$", title):
                post = found[1]
                title = title[: found.start()]
            office = None
            if race == "President/Vice-President":
                base = "president"
            elif "Senator" in title:
                base = "senate:special" if special else "senate"
            else:
                office = OFFICES[title]
                base = slug(office) + (f":place-{post}" if post else "") + (":special" if unexpired else "")
            base += f":{party}" if party else ""
            key = f"{day}:TX:{base}"
            election_key = f"{day}:TX" if stage == "general" else f"{day}:TX:{stage}"
            election_id = cs.key("us.election", election_key)
            if election_key not in known_elections:
                known_elections.add(election_key)
                cs.add(
                    "election",
                    id=election_id,
                    polity_id=texas,
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    election_id,
                    f"{day.year} Texas primary election"
                    if stage == "primary"
                    else f"{day:%B} {day.day}, {day.year} Texas {'special runoff' if special else 'primary runoff'} election"
                    if stage == "runoff"
                    else f"{day:%B} {day.day}, {day.year} Texas special election",
                )
                cs.add("slug", path=f"us/tx/{day}-{stage}", entity_id=election_id)
            existing = key in contests
            contest = cs.key("us.contest", key)
            if not existing:
                if office is None and not special:
                    raise ValueError(f"{key} must already exist")
                previous = firsts.get((day.year, base)) if stage == "runoff" else None
                if stage == "runoff" and previous is None:
                    raise ValueError(f"{key}: no first round")
                office_id = None
                if office is not None:
                    office_key = f"{slug(office)}:TX"
                    office_id = cs.key("us.office", office_key)
                    if office_key not in known_offices:
                        known_offices.add(office_key)
                        cs.add(
                            "office",
                            id=office_id,
                            polity_id=texas,
                            kind="governor" if office == "Governor" else "other",
                        )
                        name(office_id, f"{office} of Texas")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="body_seats" if office is None else "office",
                    office_id=office_id,
                    body_id=body if office is None else None,
                    seat_id=seat if office is None else None,
                    district_area_id=area,
                    term="remainder" if unexpired or special else "full",
                    magnitude=1,
                    round_number=2 if stage == "runoff" else 1,
                    previous_round_id=previous,
                    party_scope_id=parties["DEM" if party == "democratic" else "REP"] if party else None,
                    electoral_system_id=systems["fptp" if stage == "general" else "two_round"],
                    default_result_kind="counted",
                )
                shown = f" (Place {post})" if post else ""
                if office is None:
                    name(
                        contest,
                        f"{day.year} U.S. Senate special election{' runoff' if stage == 'runoff' else ''} in Texas",
                    )
                    cs.add("slug", path=f"us/tx/{day}-{stage}/senate", entity_id=contest)
                else:
                    name(
                        contest,
                        f"{day.year} Texas {office}{' special' if unexpired else ''} {party.title()} primary"
                        f"{' runoff' if stage == 'runoff' else ''}{shown}"
                        if party
                        else f"{day.year} Texas {office} {'special election' if unexpired else 'election'}{shown}",
                    )
                    cs.add(
                        "slug",
                        path=f"us/tx/{day}-{stage}/{slug(office)}"
                        + (f"-place-{post}" if post else "")
                        + ("-special" if unexpired else "")
                        + (f"-{party}" if party else ""),
                        entity_id=contest,
                    )
            if stage != "runoff":
                firsts[(day.year, base)] = contest
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            options: list[tuple[UUID, str, str]] = []
            outcomes: list[tuple[UUID, str, int | None]] = []
            on_file: dict[UUID, int] = {}
            promoted = not existing or election not in GENERALS
            if existing:
                options = conn.execute(
                    "select id, kind, ballot_label from contest_option where contest_id = %s and kind <> 'others'",
                    [contest],
                ).fetchall()
                for row in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where contest_id = %s and is_default and %s""",
                    [contest, promoted],
                ).fetchall():
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                (
                                    "id",
                                    "election_id",
                                    "contest_id",
                                    "stage",
                                    "kind",
                                    "status",
                                    "source_id",
                                    "as_of",
                                    "completeness",
                                    "supersedes_id",
                                ),
                                row,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
                outcomes = conn.execute(
                    """select o.option_id, o.kind, o.seats from outcome o
                       join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                    [contest],
                ).fetchall()
                on_file = dict(
                    conn.execute(
                        """select o.id, sum(v.value)::bigint from result_value v
                           join result_set rs on rs.k = v.result_set_k and rs.is_default
                           join contest_option o on o.k = v.option_k
                           where rs.contest_id = %s and v.measure = 1 group by 1""",
                        [contest],
                    ).fetchall()
                )
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for candidate, code, votes in (
                lines.group_by("candidate", "party", maintain_order=True)
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
                .iter_rows()
            ):
                matches = [option for option, _, text in options if surname(text) == surname(candidate)]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches:
                    option = matches[0]
                else:
                    probes = [full(candidate), f"{plain(candidate.split()[0])}|{surname(candidate)}"]
                    if nickname := re.search(r"[('](\w+)[)']", candidate):
                        probes.append(f"{plain(nickname[1])}|{surname(candidate)}")
                    person = next(
                        (next(iter(found)) for probe in probes if len(found := people.get(probe, set())) == 1), None
                    )
                    if candidate in SEPARATE:
                        person = None
                    if person is None:
                        person = cs.key(f"{SOURCE}.person", f"TX:{slug(candidate)}")
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                        for probe in probes:
                            people.setdefault(probe, set()).add(person)
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if code == "W-I" else "ticket" if base == "president" else "candidate",
                        party_id=parties.get(code),
                        ballot_label=candidate,
                    )
                    if code != "W-I":
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=code not in PARTIES,
                        )
                if option in totals:
                    raise ValueError(f"{key}: two candidates share an option with {candidate}")
                if option in on_file and on_file[option] != votes:
                    print(f"{key}: {candidate} has {votes}, {on_file[option]} on file")
                totals[option] = votes
                picked[candidate] = option
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=later if election in GENERALS else source,
                is_default=promoted,
                completeness=1,
            )
            if election in DAYS:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=RUNOFF_DATE)
            cs.artifact(raw.cached(SOURCE, pages[(election, race)]), later if election in GENERALS else source)
            for candidate, county, votes in lines.select("candidate", "county", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[plain(county)],
                    option_id=picked[candidate],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                ranked = sorted(totals, key=totals.__getitem__, reverse=True)
                majority = totals[ranked[0]] * 2 > sum(totals.values())
                if stage == "general" or (special and (stage == "runoff" or majority)):
                    cs.add("outcome", result_set_id=result_set, option_id=ranked[0], kind="elected", seats=1)
                elif stage == "runoff" or majority:
                    cs.add("outcome", result_set_id=result_set, option_id=ranked[0], kind="won", seats=None)
                else:
                    for option in ranked[:2]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
            filled += 1
        print(filled, "contests filled")
        return cs.write()
