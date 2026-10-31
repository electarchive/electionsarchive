import re
import subprocess
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wv_hist_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/20190915id_/https://sos.wv.gov/elections/Documents/HistElecDocs/"
PUBLISHER = "West Virginia Secretary of State"
TITLE = "Historical election results"
DAYS = {1992: date(1992, 5, 12), 1996: date(1996, 5, 14), 2000: date(2000, 5, 9), 2004: date(2004, 5, 11)}
TITLES = {
    "governor": "Governor",
    "secretary-of-state": "Secretary of State",
    "auditor": "Auditor",
    "treasurer": "Treasurer",
    "commissioner-of-agriculture": "Commissioner of Agriculture",
    "attorney-general": "Attorney General",
}
HEADINGS = {
    "GOVERNOR": "governor",
    "SECRETARY OF STATE": "secretary-of-state",
    "AUDITOR": "auditor",
    "TREASURER": "treasurer",
    "STATE TREASURER": "treasurer",
    "COMMISSIONER OF AGRICULTURE": "commissioner-of-agriculture",
    "COMMISIONER OF AGRICULTURE": "commissioner-of-agriculture",
    "ATTORNEY GENERAL": "attorney-general",
}
BOOKS = {
    (1992, "democratic"): "1992/1992%20Primary%20Election%20-%20Official%20Returns%20of%20the%20Democratic%20Party.pdf",
    (1992, "republican"): "1992/1992%20Primary%20Election%20-%20Official%20Returns%20of%20the%20Republican%20Party.pdf",
    (1996, "democratic"): "1996/1996%20Primary%20Election%20-%20Official%20Returns%20of%20the%20Democratic%20Party.pdf",
    (1996, "republican"): "1996/1996%20Primary%20Election%20-%20Official%20Returns%20of%20the%20Republican%20party.pdf",
}
BLOCKS: dict[tuple[int, str], dict[str, list[list[str]]]] = {
    (1992, "democratic"): {
        "governor": [
            ["Rodger Belknap", "Larry Eugene Butcher", "Gaston Caperton", "Mario J. Palumbo", "Charlotte Jean Pritt"]
        ],
        "secretary-of-state": [["Ken Hechler", "Tom Susman"]],
        "auditor": [["Glen B. Gainer, III", "Mark Anthony Manchin"]],
        "treasurer": [["Larris Bailey"]],
        "commissioner-of-agriculture": [["Gus R. Douglass", "Frederick L. Parker", "Jeffrey K. Silverman"]],
        "attorney-general": [["Darrell McGraw", "Edward Rebrook"]],
    },
    (1992, "republican"): {
        "governor": [["Cleve Benedict", "Vernon Criss"]],
        "secretary-of-state": [["Madalin E. Jackson"]],
        "attorney-general": [["Robert James Gould"]],
    },
    (1996, "democratic"): {
        "governor": [
            ["Bob Henry Baber", "Larrie Bailey", "Louis J. Davis", "Richard E. Koon"],
            ["Jim Lees", "Joe Manchin III", "Bob Myers", "Charlotte Pritt"],
            ["Frankie Rocchetti Crumpler", "Lyle Sattes", "Fred L. Schell Worthington"],
        ],
        "secretary-of-state": [["Ken Hechler"]],
        "auditor": [["Glen B. Gainer III"]],
        "treasurer": [["John D. Perdue"]],
        "commissioner-of-agriculture": [["Gus R. Douglass"]],
        "attorney-general": [["Louis Bloom", "Darrell McGraw"]],
    },
    (1996, "republican"): {
        "governor": [["Jon McBride", "David McKinley", "Cecil H. Underwood"]],
        "secretary-of-state": [["Vernon R. Hayes, Jr.", "Henry L. Thaxton"]],
        "treasurer": [["Stan Klos"]],
        "commissioner-of-agriculture": [["Paul Nuchims"]],
        "attorney-general": [["Charlotte R. Lane"]],
    },
}
SHEETS = {
    2000: {
        "governor": "Governor",
        "secretary-of-state": "SoS",
        "auditor": "Auditor",
        "treasurer": "Treasurer",
        "commissioner-of-agriculture": "Com%20of%20Ag",
        "attorney-general": "Attorney%20General",
    },
    2004: {
        "governor": "Governor",
        "secretary-of-state": "SoS",
        "auditor": "Auditor",
        "treasurer": "State%20Treasurer",
        "commissioner-of-agriculture": "Com%20of%20Ag",
        "attorney-general": "Attorney%20General",
    },
}
SECTIONS: dict[tuple[int, str, str], list[str]] = {
    (2000, "governor", "democratic"): ["Jim Lees", "Bob Wise"],
    (2000, "governor", "republican"): [
        "Larry Eugene Butcher",
        "Donna H. McCase",
        "Joseph Oliverio",
        "Cecil H. Underwood",
    ],
    (2000, "governor", "libertarian"): ["Bob Myers"],
    (2000, "secretary-of-state", "democratic"): [
        "Joe Manchin, III",
        "Bobby Nelson",
        "Mike Oliverio",
        "Charlotte Pritt",
    ],
    (2000, "secretary-of-state", "libertarian"): ["Poochie Myers"],
    (2000, "auditor", "democratic"): ["Glen B. Gainer, III"],
    (2000, "treasurer", "democratic"): ["John D. Perdue"],
    (2000, "commissioner-of-agriculture", "democratic"): ["Gus R. Douglass"],
    (2000, "attorney-general", "democratic"): ["Darrell McGraw"],
    (2004, "governor", "democratic"): [
        "Jerry Baker Alderson",
        "James A. Baughman",
        "Louis Davis",
        'Phillip "Icky" Frye',
        "Lloyd Jackson Hamlin",
        "Jim Lees",
        "Joe Manchin, III",
        "Lacy Wright, Jr.",
    ],
    (2004, "governor", "republican"): [
        "Carroll Bowden, Sr.",
        "Rob Capehart",
        "Larry V. Faircloth",
        "Douglas E. McKinney",
        "Dan R. Moore",
        'Joseph "Joey" Oliverio',
        "James D. Radcliffe, Jr.",
        'Charles G. "Bud" Railey',
        "Richard Robb",
        "Monty Warner",
    ],
    (2004, "secretary-of-state", "democratic"): [
        "Donna J. Acord",
        "Larrie Bailey",
        "George Daugherty",
        "Ken Hechler",
        "Mike Oliverio",
        "Roger Pritt",
        "Natalie Tennant",
    ],
    (2004, "secretary-of-state", "republican"): ["Betty Ireland"],
    (2004, "auditor", "democratic"): ["Glen B. Gainer, III"],
    (2004, "auditor", "republican"): ["Lisa Thornburg"],
    (2004, "treasurer", "republican"): ["Bob Adams"],
    (2004, "commissioner-of-agriculture", "democratic"): ["Gus R. Douglass"],
    (2004, "commissioner-of-agriculture", "republican"): ["Lawrence T. Beckerle", "Andrew Yost"],
    (2004, "attorney-general", "democratic"): ["Darrell McGraw"],
    (2004, "attorney-general", "republican"): ["Hiram Lewis, IV"],
}
MISMATCHES: list[tuple[str, str, int]] = []
KNOWN: dict[tuple[str, str], int] = {
    ("1992 democratic governor", "Rodger Belknap"): 110,
    ("1992 democratic governor", "Gaston Caperton"): -18,
    ("1992 democratic treasurer", "Larris Bailey"): 20,
    ("1992 democratic commissioner-of-agriculture", "Gus R. Douglass"): 270,
    ("1992 democratic attorney-general", "Darrell McGraw"): -10,
    ("1992 republican secretary-of-state", "Madalin E. Jackson"): -36179,
    ("1996 democratic governor", "Bob Henry Baber"): 30,
    ("1996 democratic governor", "Bob Myers"): -10,
    ("2004 republican governor", "Carroll Bowden, Sr."): 10,
}
METHOD = (
    "County totals come from the Secretary of State's official returns for the primary election, each candidate's "
    "55 counties summing to the total row printed in the same document, except nine columns whose county rows differ "
    "from it (in the 1992 Democratic primary, Rodger Belknap's governor race by 110, Gaston Caperton's by -18, Larris "
    "Bailey's treasurer race by 20, Gus R. Douglass's commissioner of agriculture race by 270, and Darrell McGraw's "
    "attorney general race by -10; in the 1996 Democratic governor primary, Bob Henry Baber by 30 and Bob Myers by -10; "
    "in the 2004 Republican governor primary, Carroll Bowden, Sr. by 10; and in the 1992 Republican secretary of state "
    "primary, whose printed total of 120,519 repeats the governor primary's vote and not Madalin E. Jackson's county "
    "rows, which sum to 84,340); the county figures are kept as printed. The 2004 Democratic primary for treasurer "
    "is left out: the published sheet repeats the 2004 Republican auditor figures."
)
ROW = re.compile(r"^\s*([A-Za-z.' ]+?)\s+([\d,]+(?:\s+[\d,]+)*)\s*$")
PARTY_HEADING = re.compile(r"^\s*(Democratic|Republican|Libertarian) Primary\s*$")


def addresses() -> list[str]:
    urls = [SITE + path for path in BOOKS.values()]
    for year, offices in SHEETS.items():
        urls += [f"{SITE}{year}/{year}%20{name}%20Pri.pdf" for name in offices.values()]
    return urls


def fetch() -> None:
    for url in addresses():
        for attempt in range(4):
            try:
                raw.fetch(SOURCE, url, suffix=".pdf")
                break
            except Exception:
                time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError(url)
        time.sleep(3)


def text(url: str) -> list[str]:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()


def table(lines: list[str], names: list[str], where: str) -> list[tuple[str, str, int]]:
    counties: dict[str, list[int]] = {}
    totals: list[int] | None = None
    for line in lines:
        match = ROW.match(line)
        if match is None:
            continue
        values = [int(v.replace(",", "")) for v in match[2].split()]
        label = match[1].strip()
        if label.lower().startswith("total"):
            totals = values
            break
        if label.lower().startswith("%"):
            continue
        if label in counties:
            raise ValueError(f"{where}: {label} twice")
        counties[label] = values
    if totals is None or len(counties) != 55 or len(totals) != len(names):
        raise ValueError(f"{where}: {len(counties)} counties, totals {totals}, {len(names)} candidates")
    rows = []
    for index, name in enumerate(names):
        short = [county for county, values in counties.items() if len(values) != len(names)]
        if short:
            raise ValueError(f"{where}: rows {short} have the wrong number of cells")
        summed = sum(values[index] for values in counties.values())
        if summed != totals[index]:
            print(f"{where} {name}: counties sum to {summed}, printed total {totals[index]}")
            if KNOWN.get((where, name)) != summed - totals[index]:
                MISMATCHES.append((where, name, summed - totals[index]))
        rows += [(name, county, values[index]) for county, values in counties.items()]
    return rows


def parse() -> None:
    rows: list[tuple[int, str, str, str, str, int]] = []
    for (year, party), path in BOOKS.items():
        lines = text(SITE + path)
        used: dict[str, int] = {}
        for index, line in enumerate(lines):
            office = HEADINGS.get(line.strip().upper())
            if (
                office is None
                or next((x for x in reversed(lines[:index]) if x.strip()), "").strip().lower()
                and re.fullmatch(
                    r"total votes( for)?(\s+[\d,]+)?",
                    re.sub(r"\s+", " ", next((x for x in reversed(lines[:index]) if x.strip()), "").strip().lower()),
                )
            ):
                continue
            ahead = next(
                (j for j in range(index + 1, min(index + 14, len(lines))) if lines[j].strip().startswith("Barbour")),
                None,
            )
            if ahead is None or any(HEADINGS.get(x.strip().upper()) for x in lines[index + 1 : ahead]):
                continue
            blocks = BLOCKS[(year, party)].get(office)
            if blocks is None:
                raise ValueError(f"{year} {party} {office}: no names")
            at = used.get(office, 0)
            if at >= len(blocks):
                raise ValueError(f"{year} {party} {office}: block {at + 1} at line {index + 1} has no names")
            used[office] = at + 1
            rows += [
                (year, office, party, *row) for row in table(lines[ahead:], blocks[at], f"{year} {party} {office}")
            ]
        for office, blocks in BLOCKS[(year, party)].items():
            if used.get(office) != len(blocks):
                raise ValueError(f"{year} {party} {office}: {used.get(office)} blocks found, {len(blocks)} expected")
    for year, offices in SHEETS.items():
        for office, name in offices.items():
            lines = text(f"{SITE}{year}/{year}%20{name}%20Pri.pdf")
            starts = [
                (i, PARTY_HEADING.match(line)[1].lower()) for i, line in enumerate(lines) if PARTY_HEADING.match(line)
            ]
            parties = list(dict.fromkeys(party for _, party in starts))
            for party in parties:
                names = SECTIONS.get((year, office, party))
                if names is None:
                    print(f"{year} {office} {party}: left out")
                    continue
                first = next(i for i, p in starts if p == party)
                rows += [(year, office, party, *row) for row in table(lines[first:], names, f"{year} {party} {office}")]
    if MISMATCHES:
        raise ValueError(f"columns that do not match their printed totals: {MISMATCHES}")
    frame = pl.DataFrame(rows, schema=["year", "office", "party", "candidate", "county", "votes"], orient="row")
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", name.lower()): code
            for name, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '54%%'"""
            ).fetchall()
        }
    frame = (
        frame.with_columns(
            day=pl.col("year").replace_strict({y: d for y, d in DAYS.items()}, return_dtype=pl.Date),
            code=pl.col("county").map_elements(
                lambda c: codes[re.sub(r"[^a-z]", "", c.lower())], return_dtype=pl.String
            ),
        )
        .drop("county")
        .rename({"code": "county"})
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("year", "office", "party", "candidate", maintain_order=True).agg(pl.col("votes").sum()).rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="West Virginia's 1992, 1996, 2000, and 2004 governor, secretary of state, auditor, treasurer, "
            "commissioner of agriculture, and attorney general party primaries by county from the Secretary of "
            "State's official returns",
            polity="US",
        )

        def plain(value: str) -> str:
            return re.sub(r"[^a-z]", "", value.lower())

        def surname(value: str) -> str:
            words = [word for word in value.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        def name(entity: UUID, value: str) -> None:
            cs.add("name", entity_id=entity, text=value, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.wv.gov/elections/Pages/ElectionResults.aspx",
            license="Public record",
        )
        for url in addresses():
            cs.artifact(raw.cached(SOURCE, url), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '54%%'"
            ).fetchall()
        )
        if len(counties) != 55:
            raise ValueError("West Virginia needs 55 counties")
        west_virginia = cs.polity("US-WV")
        ((state,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-WV'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, list[int]]] = {}
        for person, label, statewide, known, years in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':WV:[a-z-]+(:special|:democratic|:republican)?$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:WV:|:president$)'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not label.split():
                continue
            people.setdefault(plain(label), set()).add(person)
            people.setdefault(f"{plain(label.split()[0])}|{surname(label)}", set()).add(person)
            people.setdefault(f"{plain(label)[:1]}.|{surname(label)}", set()).add(person)
            ranks[person] = (statewide, known, years)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {
            "democratic": cs.key("us.party", "democratic-party"),
            "republican": cs.key("us.party", "republican-party"),
            "libertarian": cs.key("us.party", "libertarian-party"),
        }
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':WV:'")
        )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':WV'")
        )
        offices = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        made: set[UUID] = set()
        for (day, office, party), lines in frame.sort("day").group_by("day", "office", "party", maintain_order=True):
            year = day.year
            key = f"{day}:WV:{office}:{party}"
            election = elections.get(f"{day}:WV:primary")
            if election is None:
                election = cs.key("us.election", f"{day}:WV:primary")
                elections[f"{day}:WV:primary"] = election
                cs.add(
                    "election",
                    id=election,
                    polity_id=west_virginia,
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day:%B} {day.day}, {year} West Virginia primary election")
                cs.add("slug", path=f"us/wv/{day}-primary", entity_id=election)
            if cs.key(f"{SOURCE}.result_set", key) not in cs.minted:
                continue
            contest = contests.get(key)
            if contest is None:
                contest = cs.key("us.contest", key)
                contests[key] = contest
                office_key = f"{office}:WV"
                if office_key not in offices:
                    raise ValueError(f"{office_key} must already exist")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", office_key),
                    district_area_id=state,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    party_scope_id=parties[party],
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} West Virginia {TITLES[office]} {party.title()} primary")
                cs.add("slug", path=f"us/wv/{day}-primary/{office}-{party}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket', 'write_in')""",
                [contest],
            ).fetchall()
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for candidate, votes in (
                lines.group_by("candidate", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                matches = [
                    option
                    for option, kind, label in options
                    if kind != "write_in" and surname(label or "") == surname(candidate)
                ]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches:
                    option = matches[0]
                else:
                    first = plain(candidate.split()[0])
                    known = (
                        people.get(plain(candidate))
                        or people.get(f"{first}|{surname(candidate)}")
                        or (people.get(f"{first}.|{surname(candidate)}", set()) if len(first) == 1 else set())
                    )
                    person = (
                        max(
                            known,
                            key=lambda one: (
                                ranks.get(one, (False, False, []))[0],
                                ranks.get(one, (False, False, []))[1],
                                -min(
                                    (abs(other - year) for other in ranks.get(one, (False, False, [year]))[2]),
                                    default=0,
                                ),
                            ),
                        )
                        if known
                        else cs.key(f"{SOURCE}.person", f"WV:{slug(candidate)}")
                    )
                    if person in cs.minted and person not in made:
                        made.add(person)
                        people.setdefault(plain(candidate), set()).add(person)
                        people.setdefault(f"{plain(candidate.split()[0])}|{surname(candidate)}", set()).add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                    elif person not in made:
                        print(f"{key}: {candidate} is joined to an existing person")
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties[party],
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                if option in totals:
                    raise ValueError(f"{key}: two columns share an option with {candidate}")
                totals[option] = votes
                picked[candidate] = option
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
            for county, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[county],
                    option_id=picked[candidate],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="won",
                seats=None,
            )
        return cs.write()
