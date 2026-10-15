import re
import time
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nara"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = [1789, *range(1792, 2025, 4)]
PAGE = "https://www.archives.gov/electoral-college/{year}"
PUBLISHER = "National Archives and Records Administration"
TITLE = "Electoral College results"
DISPUTED = 16
LABEL = re.compile(r"\*|,? of .*|, Esq.*")
ALIASES = {
    "Barry Goldwater": "Barry M. Goldwater",
    "Donald Trump": "Donald J. Trump",
    "George McGovern": "George S. McGovern",
    "Joe Biden": "Joseph R. Biden Jr.",
    "Mike Pence": "Michael R. Pence",
    "Strom Thurmond": "J. Strom Thurmond",
}
OTHERS: dict[int, dict[tuple[str, str], list[tuple[str, int]]]] = {
    1796: {
        ("both", "Connecticut"): [("John Jay", 5)],
        ("both", "Georgia"): [("George Clinton", 4)],
        ("both", "Maryland"): [("John Henry", 2)],
        ("both", "Massachusetts"): [("Samuel Johnston", 2)],
        ("both", "North Carolina"): [("James Iredell", 3), ("George Washington", 1), ("Charles C. Pinckney", 1)],
        ("both", "Virginia"): [("George Clinton", 3), ("George Washington", 1)],
    },
    1824: {
        ("president", "Delaware"): [("William H. Crawford", 2)],
        ("president", "Georgia"): [("William H. Crawford", 9)],
        ("president", "Kentucky"): [("Henry Clay", 14)],
        ("president", "Maryland"): [("William H. Crawford", 1)],
        ("president", "Missouri"): [("Henry Clay", 3)],
        ("president", "New York"): [("William H. Crawford", 5), ("Henry Clay", 4)],
        ("president", "Ohio"): [("Henry Clay", 16)],
        ("president", "Virginia"): [("William H. Crawford", 24)],
        ("vice_president", "Connecticut"): [("Andrew Jackson", 8)],
        ("vice_president", "Delaware"): [("Henry Clay", 2)],
        ("vice_president", "Georgia"): [("Martin Van Buren", 9)],
        ("vice_president", "Maryland"): [("Andrew Jackson", 1)],
        ("vice_president", "Missouri"): [("Andrew Jackson", 3)],
        ("vice_president", "New Hampshire"): [("Andrew Jackson", 1)],
        ("vice_president", "Virginia"): [("Nathaniel Macon", 24)],
    },
    1832: {
        ("president", "South Carolina"): [("John Floyd", 11)],
        ("president", "Vermont"): [("William Wirt", 7)],
        ("vice_president", "Pennsylvania"): [("William Wilkins", 30)],
        ("vice_president", "South Carolina"): [("Henry Lee", 11)],
        ("vice_president", "Vermont"): [("Amos Ellmaker", 7)],
    },
    1836: {
        ("president", "Georgia"): [("Hugh L. White", 11)],
        ("president", "Massachusetts"): [("Daniel Webster", 14)],
        ("president", "South Carolina"): [("Willie P. Mangum", 11)],
        ("president", "Tennessee"): [("Hugh L. White", 15)],
        ("vice_president", "Georgia"): [("John Tyler", 11)],
        ("vice_president", "Maryland"): [("John Tyler", 10)],
        ("vice_president", "South Carolina"): [("John Tyler", 11)],
        ("vice_president", "Tennessee"): [("John Tyler", 15)],
        ("vice_president", "Virginia"): [("William Smith", 23)],
    },
    1860: {
        (office, state): [(candidate, votes)]
        for state, votes, ticket in (
            ("Alabama", 9, ("John C. Breckinridge", "Joseph Lane")),
            ("Arkansas", 4, ("John C. Breckinridge", "Joseph Lane")),
            ("Delaware", 3, ("John C. Breckinridge", "Joseph Lane")),
            ("Florida", 3, ("John C. Breckinridge", "Joseph Lane")),
            ("Georgia", 10, ("John C. Breckinridge", "Joseph Lane")),
            ("Kentucky", 12, ("John Bell", "Edward Everett")),
            ("Louisiana", 6, ("John C. Breckinridge", "Joseph Lane")),
            ("Maryland", 8, ("John C. Breckinridge", "Joseph Lane")),
            ("Mississippi", 7, ("John C. Breckinridge", "Joseph Lane")),
            ("North Carolina", 10, ("John C. Breckinridge", "Joseph Lane")),
            ("South Carolina", 8, ("John C. Breckinridge", "Joseph Lane")),
            ("Tennessee", 12, ("John Bell", "Edward Everett")),
            ("Texas", 4, ("John C. Breckinridge", "Joseph Lane")),
            ("Virginia", 15, ("John Bell", "Edward Everett")),
        )
        for office, candidate in zip(("president", "vice_president"), ticket, strict=True)
    },
    1872: {
        ("president", "Georgia"): [("Benjamin Gratz Brown", 6), ("Charles J. Jenkins", 2)],
        ("president", "Kentucky"): [("Benjamin Gratz Brown", 4), ("Thomas A. Hendricks", 8)],
        ("president", "Maryland"): [("Thomas A. Hendricks", 8)],
        ("president", "Missouri"): [("Benjamin Gratz Brown", 8), ("Thomas A. Hendricks", 6), ("David Davis", 1)],
        ("president", "Tennessee"): [("Thomas A. Hendricks", 12)],
        ("president", "Texas"): [("Thomas A. Hendricks", 8)],
        ("vice_president", "Georgia"): [("Alfred H. Colquitt", 5), ("Nathaniel Prentice Banks", 1)],
        ("vice_president", "Kentucky"): [("Thomas E. Bramlette", 3), ("Willis B. Machen", 1)],
        ("vice_president", "Missouri"): [("George W. Julian", 5), ("John M. Palmer", 3), ("William S. Groesbeck", 1)],
    },
    2016: {
        ("president", "Hawaii"): [("Bernie Sanders", 1)],
        ("president", "Texas"): [("Ron Paul", 1), ("John Kasich", 1)],
        ("president", "Washington"): [("Colin Powell", 3), ("Faith Spotted Eagle", 1)],
        ("vice_president", "Hawaii"): [("Elizabeth Warren", 1)],
        ("vice_president", "Texas"): [("Carly Fiorina", 1)],
        ("vice_president", "Washington"): [
            ("Elizabeth Warren", 1),
            ("Susan Collins", 1),
            ("Maria Cantwell", 1),
            ("Winona LaDuke", 1),
        ],
    },
}
UNCOUNTED = {(1872, "president", "Georgia", "Horace Greeley"): 3}
CORRECTIONS = {
    (1800, "both", "Virginia", "Aaron Burr"): 21,
    (1936, "vice_president", "Colorado", "John N. Garner"): 6,
}
CAVEATS = {
    (1800, "both"): "The National Archives table shows no votes for Aaron Burr in Virginia, but its own totals count "
    "them: all 21 Virginia electors voted for both Thomas Jefferson and Burr. We restored those 21 votes so the state "
    "rows add up to the published totals.",
    (1872, "president"): "Horace Greeley died after the election and before the electors met. The House voted not to "
    "count Georgia's 3 electoral votes for him, so they are marked as disputed and left out of the totals.",
    (1936, "vice_president"): "The National Archives table shows 9 votes for John N. Garner in Colorado, which had 6 "
    "electors, but its own totals count 6. We use 6 so the state rows add up to the published totals.",
}
GROUPED = (
    "The National Archives table groups some candidates into an Others column. Their votes by state come from the "
    "notes on the same page."
)
BLANK = {2000: ["District of Columbia"]}
CONTINGENT = {
    (1800, "both"): ("house", "House", date(1801, 2, 17), {"Thomas Jefferson": 10, "Aaron Burr": 4}, 2),
    (1824, "president"): (
        "house",
        "House",
        date(1825, 2, 9),
        {"John Quincy Adams": 13, "Andrew Jackson": 7, "William H. Crawford": 4},
        0,
    ),
    (1836, "vice_president"): (
        "senate",
        "Senate",
        date(1837, 2, 8),
        {"Richard M. Johnson": 33, "Francis Granger": 16},
        0,
    ),
}
BODIES = {
    "electoral_college": ("electoral_college", "Electoral College"),
    "house": ("lower_house", "United States House of Representatives"),
    "senate": ("upper_house", "United States Senate"),
}


class Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[tuple[str, int]]]] = []
        self.cell: list[str] | None = None
        self.colspan = 1
        self.sup = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.tables:
            self.cell = []
            self.colspan = int(dict(attrs).get("colspan") or 1)
        elif tag == "sup":
            self.sup += 1
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "sup":
            self.sup -= 1
        elif tag in ("td", "th") and self.cell is not None and self.tables and self.tables[-1]:
            self.tables[-1][-1].append((" ".join("".join(self.cell).split()), self.colspan))
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None and self.sup == 0:
            self.cell.append(data)


def count(cell: str) -> tuple[int, int]:
    found = re.match(r"\(?(\d+)", cell)
    if found is None:
        if cell.strip("-– "):
            raise ValueError(f"unreadable count {cell!r}")
        return 0, 0
    return int(found.group(1)), DISPUTED if cell.startswith("(") else 0


def fetch() -> None:
    for year in YEARS:
        try:
            raw.cached(SOURCE, PAGE.format(year=year))
        except LookupError:
            raw.fetch(SOURCE, PAGE.format(year=year), suffix=".html")
            time.sleep(1)


def parse() -> None:
    votes: list[tuple[int, str, str, str, int, int]] = []
    electors: list[tuple[int, str, int, int]] = []
    for year in YEARS:
        parser = Tables()
        parser.feed(raw.cached(SOURCE, PAGE.format(year=year)).path.read_text(encoding="utf-8"))
        table = [[text for text, _ in row] for row in parser.tables[1]]
        found: dict[tuple[str, str, str], tuple[int, int]] = {}
        if year == 1789:
            states = table[0][1:-1]
            for label, *cells, total in table[1:]:
                counts = [count(cell)[0] for cell in cells]
                if sum(counts) != count(total)[0]:
                    raise ValueError(f"1789: {label} sums to {sum(counts)}, not {total}")
                if label.startswith("Total"):
                    electors.extend((year, state, n, 0) for state, n in zip(states, counts, strict=True))
                else:
                    candidate = LABEL.sub("", label).strip()
                    found.update({("both", state, candidate): (n, 0) for state, n in zip(states, counts, strict=True)})
            votes.extend((year, *key, *value) for key, value in found.items())
            continue

        offices = [
            "both" if " and " in text else "vice_president" if "Vice" in text else "president"
            for text, span in parser.tables[1][0][2:]
            for _ in range(span)
        ]
        columns = list(zip(offices, [LABEL.sub("", label).strip() for label in table[1]], strict=True))
        sums = [0] * (len(columns) + 1)
        totals: list[int] | None = None
        others: dict[tuple[str, str], int] = {}
        participating: list[str] = []
        for cells in table[2:]:
            if len(cells) < 2 or not cells[0] or cells[0].startswith("Notes"):
                continue
            counts = [count(cell) for cell in cells[1:]]
            if cells[0].startswith("Total"):
                totals = totals or [n for n, _ in counts]
                continue
            state = cells[0].rstrip("*").strip()
            if cells[1].strip("-– ") == "":
                if any(n for n, _ in counts):
                    raise ValueError(f"{year}: {state} appointed no electors but has votes")
                continue
            participating.append(state)
            disputed = DISPUTED if any(flags & DISPUTED for _, flags in counts) else 0
            electors.append((year, state, counts[0][0], disputed))
            sums[0] += 0 if disputed else counts[0][0]
            for index, ((office, candidate), (n, flags)) in enumerate(zip(columns, counts[1:], strict=True), start=1):
                n = CORRECTIONS.get((year, office, state, candidate), n)
                sums[index] += 0 if flags & DISPUTED else n
                if candidate.startswith("Othe"):
                    others[(office, state)] = others.get((office, state), 0) + n
                else:
                    found[(office, state, candidate)] = (n, flags)
        if sums != totals:
            raise ValueError(f"{year}: column sums {sums} differ from totals {totals}")
        curated = OTHERS.get(year, {})
        if {key for key, n in others.items() if n} != set(curated):
            raise ValueError(f"{year}: others in {sorted(others)} differ from the curated breakdown")
        for (office, state), breakdown in curated.items():
            if sum(n for _, n in breakdown) != others[(office, state)]:
                raise ValueError(f"{year}: breakdown for {office} in {state} misses {others[(office, state)]}")
            found.update({(office, state, candidate): (n, 0) for candidate, n in breakdown})
        for office, candidate in dict.fromkeys((office, candidate) for office, _, candidate in found):
            for state in participating:
                found.setdefault((office, state, candidate), (0, 0))
        for (uncounted_year, office, state, candidate), n in UNCOUNTED.items():
            if uncounted_year == year:
                found[(office, state, candidate)] = (n, DISPUTED)
        votes.extend((year, *key, *value) for key, value in found.items())

    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(votes, schema=["year", "office", "state", "candidate", "votes", "flags"], orient="row").write_parquet(
        PARSED / "votes.parquet"
    )
    pl.DataFrame(electors, schema=["year", "state", "electors", "flags"], orient="row").write_parquet(
        PARSED / "electors.parquet"
    )


def emit() -> Path:
    votes = pl.read_parquet(PARSED / "votes.parquet")
    electors = pl.read_parquet(PARSED / "electors.parquet")

    with connect() as conn:
        states: dict[str, UUID] = {
            key: unit
            for text, iso, unit in conn.execute(
                """select n.text, p.iso_code, u.id from reporting_unit u
                   join polity p on p.id = u.polity_id
                   join name n on n.entity_id = p.id and n.is_preferred
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
            for key in (text, iso.removeprefix("US-"))
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Electoral College votes by state, 1789–2024, and the three contingent elections",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        nara = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=nara,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.archives.gov/electoral-college/results",
            license="Public domain",
        )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, PAGE.format(year=year)), nara)

        us = cs.polity("US")
        offices: dict[str, UUID] = {}
        for key, kind, text in (
            ("president", "head_of_state", "President of the United States"),
            ("vice_president", "other", "Vice President of the United States"),
        ):
            offices[key] = cs.key("us.office", key)
            cs.add("office", id=offices[key], polity_id=us, kind=kind)
            name(offices[key], text)
        bodies: dict[str, UUID] = {}
        for key, (kind, text) in BODIES.items():
            bodies[key] = cs.key("us.body", key)
            cs.add("body", id=bodies[key], polity_id=us, kind=kind)
            name(bodies[key], text)
        cs.add("scheme", code="us.assembly", polity_id=us, kind="synthetic", rank=0, noun="assembly")
        assemblies: dict[str, UUID] = {}
        for key in ("house", "senate"):
            assemblies[key] = cs.unit("us.assembly", key)
            cs.add(
                "reporting_unit",
                id=assemblies[key],
                polity_id=us,
                scheme_code="us.assembly",
                code=key,
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
                valid_from=None,
            )
            name(assemblies[key], BODIES[key][1])

        meetings = {1789: date(1789, 2, 4)}
        for year in YEARS[1:]:
            december = date(year, 12, 1)
            wednesday = december + timedelta((2 - december.weekday()) % 7)
            january = date(year + 1, 1, 1)
            meetings[year] = (
                wednesday
                if year <= 1884
                else january + timedelta(-january.weekday() % 7 + 7)
                if year <= 1932
                else wednesday + timedelta(12 if year < 2024 else 13)
            )
        sizes = dict(electors.group_by("year").agg(pl.col("electors").sum()).iter_rows())
        for year, following in zip(YEARS, [*YEARS[1:], None], strict=True):
            cs.add(
                "body_size",
                body_id=bodies["electoral_college"],
                seats=sizes[year],
                valid_from=meetings[year],
                valid_to=None if following is None else meetings[following],
            )

        persons: dict[str, UUID] = {}
        for year in YEARS:
            day = meetings[year]
            election = cs.key(f"{SOURCE}.election", day.isoformat())
            cs.add("election", id=election, polity_id=us, kind="indirect", date_from=day, date_to=day, status="held")
            name(election, f"{year} Electoral College vote")
            cs.add("slug", path=f"us/{day}-electoral-college", entity_id=election)
            appointed = electors.filter(pl.col("year") == year).select("state", "electors", "flags").rows()
            for office in ("both", "president", "vice_president"):
                cast = votes.filter((pl.col("year") == year) & (pl.col("office") == office))
                if cast.is_empty():
                    continue
                totals = dict(
                    cast.filter(pl.col("flags") & DISPUTED == 0)
                    .group_by("candidate", maintain_order=True)
                    .agg(pl.col("votes").sum())
                    .iter_rows()
                )
                ranked = sorted(totals, key=lambda candidate: totals[candidate], reverse=True)
                path = "vice-president" if office == "vice_president" else "president"
                title = "Vice President" if office == "vice_president" else "President"
                if (year, office) in CONTINGENT:
                    outcomes = [(candidate, "advanced", None) for candidate in CONTINGENT[(year, office)][3]]
                    if year == 1800:
                        outcomes += [
                            ("Thomas Jefferson", "tied", None),
                            ("Aaron Burr", "tied", None),
                            ("Aaron Burr", "elected", "Vice President"),
                        ]
                else:
                    winners = ranked[:2] if office == "both" else ranked[:1]
                    leaders = [totals[candidate] for candidate in ranked[: len(winners) + 1]]
                    if leaders[0] * 2 <= sizes[year] or len(set(leaders)) < len(leaders):
                        raise ValueError(f"{year} {office}: no clear winner in {totals}")
                    outcomes = [
                        (candidate, "elected", None if office != "both" else note)
                        for candidate, note in zip(winners, ("President", "Vice President"), strict=False)
                    ]
                stages = [
                    (
                        election,
                        f"{year}:{office}",
                        f"{year} Electoral College vote for {'President and Vice President' if office == 'both' else title}",
                        f"us/{day}-electoral-college/{path}",
                        "electoral_college",
                        2 if office == "both" else 1,
                        [
                            *(
                                (states[state], candidate, 11, n, flags)
                                for state, candidate, n, flags in cast.select(
                                    "state", "candidate", "votes", "flags"
                                ).iter_rows()
                            ),
                            *((states[state], None, 2, n, flags) for state, n, flags in appointed),
                            *((states[state], None, 6, 1, 0) for state in BLANK.get(year, [])),
                        ],
                        outcomes,
                    )
                ]
                if (year, office) in CONTINGENT:
                    chamber, word, held, tallies, blank = CONTINGENT[(year, office)]
                    runoff = cs.key(f"{SOURCE}.election", held.isoformat())
                    cs.add(
                        "election",
                        id=runoff,
                        polity_id=us,
                        kind="indirect",
                        date_from=held,
                        date_to=held,
                        status="held",
                    )
                    name(runoff, f"{held.year} contingent election for {title}")
                    cs.add("slug", path=f"us/{held}-contingent", entity_id=runoff)
                    stages.append(
                        (
                            runoff,
                            f"{held}:{office}",
                            f"{held.year} {word} vote for {title}",
                            f"us/{held}-contingent/{path}",
                            chamber,
                            1,
                            [
                                *((assemblies[chamber], candidate, 1, n, 0) for candidate, n in tallies.items()),
                                *([(assemblies[chamber], None, 6, blank, 0)] if blank else []),
                            ],
                            [(max(tallies, key=lambda candidate: tallies[candidate]), "elected", None)],
                        )
                    )

                previous = None
                for round_number, (parent, key, label, path, body, magnitude, values, outcomes) in enumerate(
                    stages, start=1
                ):
                    contest = cs.key(f"{SOURCE}.contest", key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=parent,
                        kind="office",
                        office_id=offices["vice_president" if office == "vice_president" else "president"],
                        body_id=bodies[body],
                        magnitude=magnitude,
                        round_number=round_number,
                        previous_round_id=previous,
                    )
                    name(contest, label)
                    cs.add("slug", path=path, entity_id=contest)
                    result_set = cs.key(f"{SOURCE}.result_set", key)
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=parent,
                        contest_id=contest,
                        stage="certified",
                        source_id=nara,
                        is_default=True,
                        completeness=1,
                    )
                    if round_number == 1 and (year, office) in CAVEATS:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEATS[(year, office)])
                    if round_number == 1 and any(grouped == office for grouped, _ in OTHERS.get(year, {})):
                        cs.add("note", entity_id=result_set, kind="methodology", body_md=GROUPED)
                    options: dict[str, UUID] = {}
                    for unit, candidate, measure, value, flags in values:
                        if candidate is not None and candidate not in options:
                            canonical = ALIASES.get(candidate, candidate)
                            person = slug(canonical)
                            if person not in persons:
                                persons[person] = cs.key("us.person", person)
                                cs.add("person", id=persons[person])
                                name(persons[person], canonical)
                            options[candidate] = cs.key(f"{SOURCE}.option", f"{key}:{person}")
                            cs.add(
                                "contest_option",
                                id=options[candidate],
                                contest_id=contest,
                                kind="candidate",
                                ballot_label=candidate,
                            )
                            cs.add(
                                "candidacy",
                                id=cs.key(f"{SOURCE}.candidacy", f"{key}:{person}"),
                                person_id=persons[person],
                                option_id=options[candidate],
                                role="head",
                                ballot_name=candidate,
                            )
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None if candidate is None else options[candidate],
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=flags,
                            value=value,
                        )
                    for candidate, kind, notes in outcomes:
                        cs.add(
                            "outcome", result_set_id=result_set, option_id=options[candidate], kind=kind, notes=notes
                        )
                    previous = contest
        return cs.write()
