import html
import re
import time
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import canvass, medsl_statewide
from ea.store import DATA

SOURCE = "us.ri_archive"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://web.archive.org/web/2012id_/http://www.elections.state.ri.us/elections/results/"
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Election results by city and town"
PAGES = {
    "1988-11-08": {
        "1988/governor.php": ["Governor", "Lieutenant Governor"],
        "1988/other.php": ["Secretary of State", "Attorney General", "General Treasurer"],
    },
    "1990-11-06": {
        "1990/governor.php": ["Governor", "Lieutenant Governor"],
        "1990/other.php": ["Secretary of State", "Attorney General", "General Treasurer"],
    },
    "1994-11-08": {
        "https://web.archive.org/web/2013id_/http://www.elections.ri.gov/elections/results/1994/ltgovernor.php": [
            "Lieutenant Governor"
        ],
        "1994/sos.php": ["Secretary of State"],
        "1994/ag.php": ["Attorney General"],
        "1994/treasurer.php": ["General Treasurer"],
    },
    "1998-11-03": {
        "1998/ltgovcomm.php": ["Lieutenant Governor"],
        "1998/soscomm.php": ["Secretary of State"],
        "1998/agcomm.php": ["Attorney General"],
        "1998/gtcomm.php": ["General Treasurer"],
    },
    "2002-11-05": {
        "2002/generalelection/ltgov.php": ["Lieutenant Governor"],
        "2002/generalelection/sos.php": ["Secretary of State"],
        "2002/generalelection/ag.php": ["Attorney General"],
        "2002/generalelection/generaltreas.php": ["General Treasurer"],
    },
}
SPLITS = {"1988/governor.php": [2, 3], "1988/other.php": [2, 2, 3]}
MISPRINTED = {("1994-11-08", "JEFFREY CURTIS JOHNSON"): 20104}
HELD = {("1988-11-08", "General Treasurer")}
TOWN_PAGES = [
    ("barrington", "sumbarr"),
    ("bristol", "sumbris"),
    ("burrillville", "sumburr"),
    ("central falls", "sumcen"),
    ("charlestown", "sumchar"),
    ("coventry", "sumcov"),
    ("cranston", "sumcran"),
    ("cumberland", "sumcumb"),
    ("east greenwich", "sumegreen"),
    ("east providence", "sumep"),
    ("exeter", "sumext"),
    ("foster", "sumfos"),
    ("glocester", "sumgloc"),
    ("hopkinton", "sumhop"),
    ("jamestown", "sumjames"),
    ("johnston", "sumjohn"),
    ("lincoln", "sumlin"),
    ("little compton", "sumlcom"),
    ("middletown", "summid"),
    ("narragansett", "sumnarr"),
    ("new shoreham", "sumnshore"),
    ("newport", "sumnewp"),
    ("north kingstown", "sumnking"),
    ("north providence", "sumnprov"),
    ("north smithfield", "sumnsmith"),
    ("pawtucket", "sumpaw"),
    ("portsmouth", "sumport"),
    ("providence", "sumprov"),
    ("richmond", "sumrich"),
    ("scituate", "sumscit"),
    ("smithfield", "sumsmith"),
    ("south kingstown", "sumsking"),
    ("tiverton", "sumtiv"),
    ("warren", "sumwarr"),
    ("warwick", "sumwarw"),
    ("west greenwich", "sumwgreen"),
    ("west warwick", "sumwwar"),
    ("westerly", "sumwest"),
    ("woonsocket", "sumwoon"),
]
PAGES_2006 = "2006/generalelection/{}.php"
OFFICES_2006 = {
    "LIEUTENANT GOVERNOR": "Lieutenant Governor",
    "SECRETARY OF STATE": "Secretary of State",
    "ATTORNEY GENERAL": "Attorney General",
    "GENERAL TREASURER": "General Treasurer",
}
PARTIES = {
    "REP": "REPUBLICAN",
    "DEM": "DEMOCRATIC",
    "GRE": "GREEN",
    "GRN": "GREEN",
    "COO": "COOL MOOSE",
    "REF": "REFORM",
    "LIB": "LIBERTARIAN",
    "IND": "INDEPENDENT",
    "MOD": "MODERATE",
    "COOL MOOSE": "COOL MOOSE",
    "CM": "COOL MOOSE",
    "REPUBLICAN": "REPUBLICAN",
    "DEMOCRAT": "DEMOCRATIC",
    "INDEPENDENT": "INDEPENDENT",
    "GREEN": "GREEN",
    "LIBERTARIAN": "LIBERTARIAN",
    "REFORM": "REFORM",
    "MODERATE": "MODERATE",
}
METHOD = (
    "Town totals come from the Board of Elections' results pages, archived from its site, and are summed to counties; "
    "each county's towns are checked against the statewide total printed on the same page."
)


def url(page: str) -> str:
    return page if page.startswith("http") else BASE + page


def cells(page: str) -> list[list[str]]:
    text = raw.cached(SOURCE, url(page)).path.read_text(errors="replace")
    table = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL | re.IGNORECASE):
        values = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        ]
        if values := [value for value in values if value]:
            table.append(values)
    return table


def person(label: str, party: str | None = None) -> tuple[str, str]:
    label = label.strip(" ,")
    if party is None:
        match = re.search(
            r"[\s,(]+\(?(REP|DEM|GRE|GRN|REF|LIB|IND|MOD|CM|COO|Rep|Dem|Gre|Grn|Ref|Lib|Ind|Mod|Coo)\.?\)?$", label
        )
        if match is None:
            raise ValueError(f"no party in {label!r}")
        party, label = match[1], label[: match.start()]
    return re.sub(r"\s+", " ", label.strip(" ,")).upper(), PARTIES[party.upper() if party.upper() in PARTIES else party]


def fetch() -> None:
    addresses = [url(page) for pages in PAGES.values() for page in pages]
    addresses += [BASE + PAGES_2006.format(page) for page in ["topticket", *(page for _, page in TOWN_PAGES)]]
    for address in addresses:
        try:
            raw.cached(SOURCE, address)
        except LookupError:
            for attempt in range(8):
                try:
                    raw.fetch(SOURCE, address, suffix=".php")
                    break
                except httpx.TransportError:
                    if attempt == 7:
                        raise
                    time.sleep(10 * (attempt + 1))
            time.sleep(2)


def parse() -> None:
    county = {re.sub(r"[^a-z]", "", town.lower()): code for code, towns in canvass.TOWNS.items() for town in towns}
    if len(county) != 39:
        raise ValueError(f"{len(county)} towns")
    rows: list[tuple[str, str, str, str, str, int]] = []
    for day, pages in PAGES.items():
        for page, offices in pages.items():
            table = cells(page)
            start = next(n for n, row in enumerate(table) if row[0].replace(" ", "") in ("CITY/TOWN", "COMMUNITY"))
            heads = table[start][1:]
            if page in SPLITS or page.endswith("other.php"):
                start += 1
                heads = table[start]
            names = [person(head) for head in heads]
            body = [
                row
                for row in table[start + 1 :]
                if re.fullmatch(r"[A-Z .']+", row[0]) or row[0].startswith("STATE TOTAL")
            ]
            counts = SPLITS.get(page) or [len(names) // len(offices)] * len(offices)
            owner = [office for office, count in zip(offices, counts, strict=True) for _ in range(count)]
            if len(owner) != len(names):
                raise ValueError(f"{page}: {len(names)} candidates for {counts}")
            totals = next(row for row in body if row[0].startswith("STATE TOTAL"))
            totals = [int(value.replace(",", "")) for value in totals[1:]]
            seen = set()
            for row in body:
                if row[0].startswith("STATE TOTAL"):
                    continue
                key = re.sub(r"[^a-z]", "", row[0].lower())
                seen.add(key)
                votes = [int(value.replace(",", "")) for value in row[1:]]
                if len(votes) != len(names):
                    raise ValueError(f"{page} {row[0]}: {len(votes)} values for {len(names)} candidates")
                for index, (name, party) in enumerate(names):
                    rows.append((day, owner[index], name, party, county[key], votes[index]))
            if len(seen) != 39:
                raise ValueError(f"{page}: {len(seen)} towns")
            for index, (name, party) in enumerate(names):
                summed = sum(row[5] for row in rows if row[0] == day and row[1] == owner[index] and row[2] == name)
                if summed != MISPRINTED.get((day, name), totals[index]):
                    raise ValueError(f"{page} {name}: towns sum to {summed}, printed {totals[index]}")
    day = "2006-11-07"
    statewide: dict[tuple[str, str], int] = {}
    heading = None
    for line in cells(PAGES_2006.format("topticket")):
        if line[0] in OFFICES_2006:
            heading = OFFICES_2006[line[0]]
        elif heading and len(line) == 5 and line[0] != "Candidate":
            statewide[(heading, re.sub(r"\s+", " ", line[0]).upper())] = int(
                line[4].split("-")[0].replace(",", "").strip()
            )
        elif len(line) == 1 and line[0] not in OFFICES_2006:
            heading = None
    for (town, _), (_, page) in zip(((t, p) for t, p in TOWN_PAGES), TOWN_PAGES, strict=True):
        heading = None
        for line in cells(PAGES_2006.format(page)):
            if len(line) == 1 and line[0] in OFFICES_2006:
                heading = OFFICES_2006[line[0]]
            elif len(line) == 1:
                heading = None
            elif heading and len(line) == 5 and line[0] != "Candidate":
                name, party = person(line[0], line[1])
                rows.append(
                    (
                        day,
                        heading,
                        name,
                        party,
                        county[re.sub(r"[^a-z]", "", town)],
                        int(line[4].split("-")[0].replace(",", "").strip()),
                    )
                )
    for (office, name), total in statewide.items():
        summed = sum(row[5] for row in rows if row[0] == day and row[1] == office and row[2] == name)
        if summed != total:
            raise ValueError(f"2006 {office} {name}: towns sum to {summed}, printed {total}")
    if not statewide:
        raise ValueError("no 2006 statewide offices")
    swapped = {"JOHN M. CARLEVALE": "GREEN", "CHARLES J. FOGARTY": "DEMOCRATIC"}
    rows = [
        (
            row[0],
            row[1],
            row[2],
            swapped[row[2]]
            if row[0] == "1998-11-03" and row[1] == "Lieutenant Governor" and row[2] in swapped
            else row[3],
            row[4],
            row[5],
        )
        for row in rows
    ]
    frame = (
        pl.DataFrame(rows, schema=["day", "title", "candidate", "party", "county", "votes"], orient="row")
        .filter(pl.col("title") != "Governor")
        .filter(~pl.struct("day", "title").is_in([{"day": d, "title": t} for d, t in HELD]))
        .group_by("day", "title", "candidate", "party", "county")
        .agg(pl.col("votes").sum())
        .select(
            year=pl.col("day").str.slice(0, 4).cast(pl.Int32),
            day="day",
            state=pl.lit("RI"),
            title="title",
            post=pl.lit(None, pl.String),
            special=pl.lit(False),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code=pl.lit("county"),
            county="county",
            candidate="candidate",
            party="party",
            writein=pl.lit(False),
            measure=pl.lit(1, pl.Int16),
            votes=pl.col("votes").cast(pl.Int64),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(5),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "title", "candidate", "party")
        .agg(pl.col("votes").sum())
        .sort("year", "title", "votes")
        .rows()
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elections.ri.gov/elections/results/",
                license="Public record",
            )
        for pages in PAGES.values():
            for page in pages:
                cs.artifact(raw.cached(SOURCE, url(page)), source)
        cs.artifact(raw.cached(SOURCE, BASE + PAGES_2006.format("topticket")), source)
        for _, page in TOWN_PAGES:
            cs.artifact(raw.cached(SOURCE, BASE + PAGES_2006.format(page)), source)
        return {"RI": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Rhode Island's lieutenant governor, secretary of state, attorney general, and general treasurer "
        "of 1990, 1994, 1998, 2002, and 2006 by county from the Board of Elections' town results",
        cite=cite,
        method=METHOD,
    )
