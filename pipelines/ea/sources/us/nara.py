import html
import json
import re
import time
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nara"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
YEARS = [1789, *range(1792, 2025, 4)]
PAGE = "https://www.archives.gov/electoral-college/{year}"
PUBLISHER = "National Archives and Records Administration"
TITLE = "Electoral College results"
WIKIDATA_TITLE = "Candidates and parties in United States presidential elections"
WIKIDATA_NOTE = (
    "Tickets (candidate, running mate, and the party each represented) on Wikidata's items for United States "
    "presidential elections, the party memberships of everyone who received electoral votes, and the parties' "
    "founding, dissolution, and colors. An Electoral College option takes the party the National Archives prints "
    "for its candidate, else its Wikidata ticket's, else the person's own party on the day, for votes cast outside "
    "any ticket. A party counts only in the years Wikidata dates it to, so the 1789 vote, held before the first "
    "parties formed, carries none."
)
SPARQL = "https://query.wikidata.org/sparql?"
TICKETS_QUERY = """
SELECT ?date ?statement ?candidate ?mate ?party ?represents WHERE {
  ?election wdt:P31 wd:Q47566; wdt:P585 ?date; p:P726 ?statement.
  ?statement ps:P726 ?candidate.
  OPTIONAL { ?statement pq:P6149 ?mate }
  OPTIONAL { ?statement pq:P102 ?party }
  OPTIONAL { ?statement pq:P1268 ?represents }
}
"""
NAMES_QUERY = """
SELECT DISTINCT ?person ?name WHERE {
  ?election wdt:P31 wd:Q47566; p:P726 ?statement.
  { ?statement ps:P726 ?person } UNION { ?statement pq:P6149 ?person }
  { ?person rdfs:label ?name } UNION { ?person skos:altLabel ?name }
  FILTER(LANG(?name) = "en")
}
"""
MEMBERSHIPS_QUERY = """
SELECT DISTINCT ?person ?statement ?party ?start ?end WHERE {
  { ?election wdt:P31 wd:Q47566; p:P726 ?ticket. { ?ticket ps:P726 ?person } UNION { ?ticket pq:P6149 ?person } }
  UNION { VALUES ?person { %s } }
  ?person p:P102 ?statement. ?statement ps:P102 ?party.
  OPTIONAL { ?statement pq:P580 ?start }
  OPTIONAL { ?statement pq:P582 ?end }
}
"""
PARTIES_QUERY = """
SELECT ?party ?inception ?dissolved ?statement ?color ?start ?end WHERE {
  VALUES ?party { %s }
  OPTIONAL { ?party wdt:P571 ?inception }
  OPTIONAL { ?party wdt:P576 ?dissolved }
  OPTIONAL {
    ?party p:P465 ?statement. ?statement ps:P465 ?color.
    OPTIONAL { ?statement pq:P580 ?start }
    OPTIONAL { ?statement pq:P582 ?end }
  }
}
"""
PARTIES = {
    "federalist-party": ("Q42189", "Federalist Party"),
    "democratic-republican-party": ("Q42186", "Democratic-Republican Party"),
    "national-republican-party": ("Q1541747", "National Republican Party"),
    "anti-masonic-party": ("Q574701", "Anti-Masonic Party"),
    "nullifier-party": ("Q3440208", "Nullifier Party"),
    "democratic-party": ("Q29552", "Democratic Party"),
    "whig-party": ("Q42183", "Whig Party"),
    "know-nothing-party": ("Q550678", "Know Nothing Party"),
    "republican-party": ("Q29468", "Republican Party"),
    "southern-democrats": ("Q7569861", "Southern Democrats"),
    "constitutional-union-party": ("Q1128268", "Constitutional Union Party"),
    "liberal-republican-party": ("Q6540796", "Liberal Republican Party"),
    "peoples-party": ("Q337794", "People's Party"),
    "progressive-party-1912": ("Q1363538", "Progressive Party"),
    "progressive-party-1924": ("Q1638786", "Progressive Party"),
    "states-rights-democratic-party": ("Q1231756", "States' Rights Democratic Party"),
    "american-independent-party": ("Q465099", "American Independent Party"),
    "libertarian-party": ("Q558334", "Libertarian Party"),
    "green-party": ("Q849158", "Green Party"),
}
STAND_INS = {"Q327591": None, "Q1290162": None, "Q5440576": "federalist-party"}
LABELS = {
    "Independent": (),
    "F": ("federalist-party",),
    "Federalist": ("federalist-party",),
    "Democratic-Republican": ("democratic-republican-party",),
    "D-R": ("democratic-republican-party",),
    "Independent D-R": ("democratic-republican-party",),
    "Federalist/Independent D-R": ("federalist-party", "democratic-republican-party"),
    "National Republican": ("national-republican-party",),
    "D": ("democratic-party",),
    "Whig": ("whig-party",),
    "Whig-American": ("whig-party", "know-nothing-party"),
    "R": ("republican-party",),
    "D-P": ("democratic-party", "peoples-party"),
    "P": ("progressive-party-1912",),
}
PEOPLE = {
    "Aaron Burr": "Q179090",
    "Alfred H. Colquitt": "Q262319",
    "Barry M. Goldwater": "Q319129",
    "Benjamin Lincoln": "Q817605",
    "Bernie Sanders": "Q359442",
    "Carly Fiorina": "Q256380",
    "Charles C. Pinckney": "Q405463",
    "Charles J. Jenkins": "Q885230",
    "Colin Powell": "Q150851",
    "Daniel D. Tompkins": "Q223545",
    "David Davis": "Q374427",
    "Edward Telfair": "Q1293842",
    "Elbridge Gerry": "Q219797",
    "Elizabeth Warren": "Q434706",
    "Faith Spotted Eagle": "Q28037026",
    "George Clinton": "Q201646",
    "George W. Julian": "Q1508542",
    "George Washington": "Q23",
    "Harry F. Byrd": "Q775382",
    "Hillary Clinton": "Q6294",
    "J. Strom Thurmond": "Q324719",
    "James Armstrong": None,
    "James Iredell": "Q956007",
    "James Madison": "Q11813",
    "James Monroe": "Q11815",
    "James Ross": "Q503524",
    "Jared Ingersoll": "Q2622320",
    "John Adams": "Q11806",
    "John E. Howard": "Q653713",
    "John Hancock": "Q272774",
    "John Henry": "Q202950",
    "John Jay": "Q310847",
    "John Kasich": "Q69319",
    "John Langdon": "Q554454",
    "John M. Palmer": "Q880367",
    "John Marshall": "Q310829",
    "John Milton": "Q6248888",
    "John Rutledge": "Q370781",
    "Joseph R. Biden Jr.": "Q6279",
    "Kamala D. Harris": "Q10853588",
    "Maria Cantwell": "Q22250",
    "Nathaniel Prentice Banks": "Q714106",
    "Oliver Ellsworth": "Q515858",
    "Richard B. Cheney": "Q48259",
    "Robert G. Harper": "Q465273",
    "Robert H. Harrison": "Q437855",
    "Ron Paul": "Q15257",
    "Ronald Reagan": "Q9960",
    "Rufus King": "Q456794",
    "Samuel Adams": "Q212963",
    "Samuel Huntington": "Q887130",
    "Samuel Johnston": "Q730149",
    "Susan Collins": "Q22279",
    "Thomas A. Hendricks": "Q310843",
    "Thomas E. Bramlette": "Q886933",
    "Thomas Pinckney": "Q881165",
    "William S. Groesbeck": "Q1650370",
    "Willis B. Machen": "Q5970761",
    "Winona LaDuke": "Q118969",
}
PICKS = {
    (1872, "David Davis"): None,
    (1872, "George W. Julian"): None,
    (1872, "John M. Palmer"): None,
    (1872, "Nathaniel Prentice Banks"): "liberal-republican-party",
    (1892, "James B. Weaver"): "peoples-party",
    (1892, "James G. Field"): "peoples-party",
}
TICKETS = SPARQL + urlencode({"query": TICKETS_QUERY, "format": "json"})
NAMES = SPARQL + urlencode({"query": NAMES_QUERY, "format": "json"})
MEMBERSHIPS = SPARQL + urlencode(
    {"query": MEMBERSHIPS_QUERY % " ".join(f"wd:{qid}" for qid in PEOPLE.values() if qid), "format": "json"}
)
PARTY_ITEMS = SPARQL + urlencode(
    {"query": PARTIES_QUERY % " ".join(f"wd:{qid}" for qid, _ in PARTIES.values()), "format": "json"}
)
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
CONGRESSES = range(1, 120)
PROFILES = {
    congress: "https://history.house.gov/Congressional-Overview/Profiles/"
    f"{congress}{'th' if 10 <= congress % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(congress % 10, 'th')}/"
    for congress in CONGRESSES
}
PARTY_DIVISION = "https://www.senate.gov/history/partydiv.htm"
SIZES = {
    "house": ("Office of the Historian, U.S. House of Representatives", "Congressional Profiles"),
    "senate": ("U.S. Senate Historical Office", "Party Division"),
}
JOINED = {
    ("house", 86): (date(1959, 8, 21), 437, "Hawaii's Representative did not join Congress until August 21, 1959"),
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
    for url in (TICKETS, NAMES, MEMBERSHIPS, PARTY_ITEMS):
        raw.fetch(SOURCE, url, suffix=".json")
    for url in (*PROFILES.values(), PARTY_DIVISION):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=".html")
            time.sleep(1)


def parse() -> None:
    sizes: list[tuple[str, int, date, int]] = []
    starts = {congress: date(1787 + 2 * congress, *((3, 4) if congress <= 73 else (1, 3))) for congress in CONGRESSES}
    for congress, url in PROFILES.items():
        text = html.unescape(re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", raw.cached(SOURCE, url).path.read_text())))
        found = re.search(r"Total Membership: (\d+) Representatives", text)
        if found is None:
            raise ValueError(f"{url}: no total membership")
        sizes.append(("house", congress, starts[congress], int(found.group(1))))
        if ("house", congress) in JOINED and JOINED[("house", congress)][2] not in text:
            raise ValueError(f"{url}: the footnote on a late member is gone")
    text = html.unescape(
        re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", raw.cached(SOURCE, PARTY_DIVISION).path.read_text()))
    )
    divisions = {
        int(congress): int(seats)
        for congress, seats in re.findall(r"(\d+)(?:st|nd|rd|th) Congress \(\d{4}[^)]*\).*?Total Seats: (\d+)", text)
    }
    if divisions.keys() != set(CONGRESSES):
        raise ValueError(f"party division: Congresses {sorted(set(CONGRESSES) ^ divisions.keys())} are missing")
    sizes.extend(("senate", congress, starts[congress], seats) for congress, seats in divisions.items())
    sizes.extend((body, congress, day, seats) for (body, congress), (day, seats, _) in JOINED.items())

    votes: list[tuple[int, str, str, str, int, int]] = []
    electors: list[tuple[int, str, int, int]] = []
    printed: list[tuple[int, str, str]] = []
    for year in YEARS:
        parser = Tables()
        parser.feed(raw.cached(SOURCE, PAGE.format(year=year)).path.read_text(encoding="utf-8"))
        printed.extend(
            (year, name.strip(), label)
            for row in parser.tables[0]
            for cell, _ in row
            for name, label in re.findall(r"([^\[\];()]+?)\s*\[([^\]]+)\]", cell)
        )
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

    def bindings(url: str) -> list[dict[str, str]]:
        return [
            {
                field: value["value"].rsplit("/", 1)[-1].replace("-", "$", 1)
                if field == "statement"
                else value["value"].rsplit("/", 1)[-1]
                for field, value in binding.items()
            }
            for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]
        ]

    def year_of(row: dict[str, str], field: str) -> int | None:
        return int(row[field][:4]) if re.match(r"\d{4}-", row.get(field, "")) else None

    def words(text: str) -> list[str]:
        return [word for word in slug(text).split("-") if word not in ("jr", "sr", "ii", "iii")]

    def same(a: str, b: str) -> bool:
        x, y = words(a), words(b)
        return bool(x and y) and x[-1] == y[-1] and x[0][0] == y[0][0]

    items = {qid: key for key, (qid, _) in PARTIES.items()}
    keys: dict[str, str | None] = items | STAND_INS
    founded: dict[str, int] = {}
    dissolved: dict[str, int] = {}
    colors: set[tuple[str, str, int | None, int | None, str]] = set()
    for row in bindings(PARTY_ITEMS):
        key = items[row["party"]]
        if (start := year_of(row, "inception")) is not None:
            founded[key] = min(founded.get(key, start), start)
        if (end := year_of(row, "dissolved")) is not None:
            dissolved[key] = max(dissolved.get(key, end), end)
        if "color" in row:
            colors.add((key, f"#{row['color'].lower()}", year_of(row, "start"), year_of(row, "end"), row["statement"]))
    tickets: dict[str, tuple[int, str, set[str], set[str], set[str]]] = {}
    for row in bindings(TICKETS):
        ticket = tickets.setdefault(row["statement"], (int(row["date"][:4]), row["candidate"], set(), set(), set()))
        for field, values in zip(("mate", "party", "represents"), ticket[2:], strict=True):
            if field in row:
                values.add(row[field])
    names: dict[str, set[str]] = {}
    for row in bindings(NAMES):
        names.setdefault(row["person"], set()).add(row["name"])
    memberships: dict[str, list[tuple[str, str, int | None, int | None]]] = {}
    for row in bindings(MEMBERSHIPS):
        memberships.setdefault(row["person"], []).append(
            (row["statement"], row["party"], year_of(row, "start"), year_of(row, "end"))
        )

    def valid(key: str | None, year: int) -> bool:
        return key is None or founded.get(key, year) <= year <= dissolved.get(key, year)

    def party(qid: str) -> str | None:
        if qid not in keys:
            raise ValueError(f"Wikidata party {qid} is not in PARTIES or STAND_INS")
        return keys[qid]

    options = list(dict.fromkeys((year, office, candidate) for year, office, _, candidate, _, _ in votes))
    people: dict[tuple[int, str], str | None] = {}
    for year, _, candidate in options:
        canonical = ALIASES.get(candidate, candidate)
        matched = {
            person
            for ticket_year, head, mates, _, _ in tickets.values()
            if ticket_year == year
            for person in (head, *mates)
            if any(same(canonical, text) for text in names.get(person, ()))
        }
        if len(matched) > 1:
            raise ValueError(f"{year}: {canonical} matches several Wikidata people {sorted(matched)}")
        if not matched and canonical not in PEOPLE:
            raise ValueError(f"{year}: {canonical} is on no Wikidata ticket and not in PEOPLE")
        people[(year, canonical)] = matched.pop() if matched else PEOPLE[canonical]
    for canonical in {canonical for _, canonical in people}:
        if len({person for (_, name), person in people.items() if name == canonical}) > 1:
            raise ValueError(f"{canonical} resolves to several Wikidata people")

    labels: dict[tuple[int, str | None], tuple[str, str]] = {}
    for year, name, label in printed:
        cast = {canonical for cast_year, canonical in people if cast_year == year}
        placed = [canonical for canonical in cast if same(name, canonical)] or [
            canonical for canonical in cast if words(canonical)[-1] == words(name)[-1]
        ]
        if len(placed) != 1 or label not in LABELS:
            raise ValueError(f"{year}: cannot place the National Archives label {name} [{label}]")
        if labels.setdefault((year, people[(year, placed[0])]), (label, f"{name} [{label}]"))[0] != label:
            raise ValueError(f"{year}: {name} has several National Archives labels")

    def ticket_party(statement: str) -> tuple[str | None, list[str], list[str]] | None:
        year, head, _, members, represents = tickets[statement]
        if not represents | members:
            return None
        label, text = labels.get((year, head), ("Independent", ""))
        listed = [key for key in LABELS[label] if valid(key, year)]
        if len(LABELS[label]) == 1 and listed:
            return listed[0], [text], [statement]
        found = {party(qid) for qid in represents or members}
        if len(found) > 1:
            raise ValueError(f"{year}: ticket {statement} has several parties")
        own = found.pop()
        if not valid(own, year):
            return None
        return own, [text] if own in listed else [], [statement]

    def settle(year: int, statements: list[str]) -> tuple[str | None, list[str], list[str]] | None:
        found: dict[str | None, tuple[str | None, list[str], list[str]]] = {}
        for statement in statements:
            if (result := ticket_party(statement)) is not None:
                _, texts, cited = found.setdefault(result[0], (result[0], [], []))
                texts.extend(text for text in result[1] if text not in texts)
                cited.extend(result[2])
        if len(found) > 1:
            raise ValueError(f"{year}: tickets {statements} disagree on the party: {sorted(map(str, found))}")
        return found.popitem()[1] if found else None

    resolved: list[tuple[int, str, str, str | None, str | None, list[str], list[str]]] = []
    for year, office, candidate in options:
        canonical = ALIASES.get(candidate, candidate)
        person = people[(year, canonical)]
        headed = [statement for statement, ticket in tickets.items() if ticket[0] == year and ticket[1] == person]
        headed = [statement for statement in headed if tickets[statement][4]] or headed
        result = None
        if (printed_label := labels.get((year, person))) is not None:
            label, text = printed_label
            listed = [key for key in LABELS[label] if valid(key, year)]
            if not LABELS[label]:
                result = (None, [text], [])
            elif len(LABELS[label]) == 1 and listed:
                result = (listed[0], [text], [])
            elif len(LABELS[label]) > 1:
                result = settle(year, headed)
                if result is None or result[0] not in listed:
                    raise ValueError(f"{year}: {canonical}'s tickets do not settle {text}")
        if result is None and person is not None:
            result = settle(year, headed) or settle(
                year, [statement for statement, ticket in tickets.items() if ticket[0] == year and person in ticket[2]]
            )
        if result is None and person is not None:
            held: dict[str | None, list[str]] = {}
            for statement, qid, start, end in memberships.get(person, []):
                if (start is None or start <= year) and (end is None or year < end) and valid(party(qid), year):
                    held.setdefault(party(qid), []).append(statement)
            if (year, canonical) in PICKS:
                pick = PICKS[(year, canonical)]
                cited = held.get(pick) or [
                    statement
                    for statement, (ticket_year, head, mates, _, _) in tickets.items()
                    if ticket_year == year and person in (head, *mates)
                ]
                if pick is not None and not cited:
                    raise ValueError(f"{year}: nothing on Wikidata ties {canonical} to {pick}")
                result = (pick, [], [] if pick is None else cited)
            elif len(held) > 1:
                raise ValueError(f"{year}: {canonical} belonged to several parties {sorted(map(str, held))}")
            elif held:
                key, cited = held.popitem()
                result = (key, [], [] if key is None else cited)
        key, texts, cited = result or (None, [], [])
        resolved.append((year, office, candidate, person, key, texts, cited))

    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(votes, schema=["year", "office", "state", "candidate", "votes", "flags"], orient="row").write_parquet(
        PARSED / "votes.parquet"
    )
    pl.DataFrame(electors, schema=["year", "state", "electors", "flags"], orient="row").write_parquet(
        PARSED / "electors.parquet"
    )
    pl.DataFrame(
        resolved,
        schema={
            "year": pl.Int64,
            "office": pl.String,
            "candidate": pl.String,
            "person": pl.String,
            "party": pl.String,
            "labels": pl.List(pl.String),
            "statements": pl.List(pl.String),
        },
        orient="row",
    ).write_parquet(PARSED / "parties.parquet")
    pl.DataFrame(
        sorted(colors, key=str),
        schema={"party": pl.String, "color": pl.String, "start": pl.Int64, "end": pl.Int64, "statement": pl.String},
        orient="row",
    ).write_parquet(PARSED / "colors.parquet")
    pl.DataFrame(
        sizes, schema={"body": pl.String, "congress": pl.Int64, "start": pl.Date, "seats": pl.Int64}, orient="row"
    ).sort("body", "start").write_parquet(PARSED / "sizes.parquet")


def emit() -> Path:
    votes = pl.read_parquet(PARSED / "votes.parquet")
    electors = pl.read_parquet(PARSED / "electors.parquet")
    colors = pl.read_parquet(PARSED / "colors.parquet")
    choices = {
        (year, office, candidate): rest
        for year, office, candidate, *rest in pl.read_parquet(PARSED / "parties.parquet").iter_rows()
    }

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
            summary="Electoral College votes by state, 1789–2024, and the three contingent elections, with each "
            "option's party",
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
        wikidata = cs.source("Wikidata", WIKIDATA_TITLE)
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title=WIKIDATA_TITLE,
            url="https://www.wikidata.org/wiki/Q47566",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            notes=WIKIDATA_NOTE,
        )
        for url in (TICKETS, NAMES, MEMBERSHIPS, PARTY_ITEMS):
            cs.artifact(raw.cached(SOURCE, url), wikidata)

        us = cs.polity("US")
        parties: dict[str, UUID] = {}
        for key in sorted({party for _, party, _, _ in choices.values() if party is not None}):
            qid, text = PARTIES[key]
            parties[key] = cs.key("us.party", key)
            cs.add("external_id", entity_id=parties[key], scheme="wikidata", value=qid)
            if parties[key] not in cs.minted:
                continue
            cs.add("party", id=parties[key], kind="party", polity_id=us)
            name(parties[key], text)
            for color, start, end in colors.filter(pl.col("party") == key).select("color", "start", "end").iter_rows():
                cs.add(
                    "party_color",
                    party_id=parties[key],
                    color=color,
                    valid_from=None if start is None else date(start, 1, 1),
                    valid_from_precision="year",
                    valid_to=None if end is None else date(end, 1, 1),
                    valid_to_precision="year",
                    source_id=wikidata,
                )
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
        for (body,), rows in pl.read_parquet(PARSED / "sizes.parquet").group_by("body", maintain_order=True):
            publisher, title = SIZES[body]
            source = cs.source(publisher, title)
            url = PARTY_DIVISION if body == "senate" else PROFILES[1].rsplit("/", 2)[0] + "/"
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license="Public domain",
            )
            for page in [PARTY_DIVISION] if body == "senate" else PROFILES.values():
                cs.artifact(raw.cached(SOURCE, page), source)
            cs.add("citation", entity_id=bodies[body], source_id=source, locator={"url": url})
            starts = rows["start"].to_list()
            for start, following, seats in zip(starts, [*starts[1:], None], rows["seats"], strict=True):
                cs.add("body_size", body_id=bodies[body], seats=seats, valid_from=start, valid_to=following)

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
                            qid, party, printed, statements = choices[(year, office, candidate)]
                            person = slug(canonical)
                            if person not in persons:
                                persons[person] = cs.key("us.person", person)
                                cs.add("person", id=persons[person])
                                name(persons[person], canonical)
                                if qid is not None:
                                    cs.add("external_id", entity_id=persons[person], scheme="wikidata", value=qid)
                            options[candidate] = cs.key(f"{SOURCE}.option", f"{key}:{person}")
                            cs.add(
                                "contest_option",
                                id=options[candidate],
                                contest_id=contest,
                                kind="candidate",
                                party_id=None if party is None else parties[party],
                                ballot_label=candidate,
                            )
                            for label in printed:
                                cs.add(
                                    "citation",
                                    entity_id=options[candidate],
                                    source_id=nara,
                                    locator={"url": PAGE.format(year=year), "text": label},
                                )
                            for statement in statements:
                                cs.add(
                                    "citation",
                                    entity_id=options[candidate],
                                    source_id=wikidata,
                                    locator={"statement": statement},
                                )
                            cs.add(
                                "candidacy",
                                id=cs.key(f"{SOURCE}.candidacy", f"{key}:{person}"),
                                person_id=persons[person],
                                option_id=options[candidate],
                                role="head",
                                ballot_name=candidate,
                            )
                        if result_set not in cs.minted:
                            continue
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
