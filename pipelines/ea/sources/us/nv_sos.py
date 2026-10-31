import html
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.nv_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/2016id_/http://www.nvsos.gov/SOSelectionPages/results/"
PUBLISHER = "Nevada Secretary of State"
TITLE = "Official general election results by county"
FOLDERS = {
    2002: "2002General",
    2006: "2006StateWideGeneral",
    2010: "2010STatewideGeneral",
    2014: "2014StatewideGeneral",
}
COUNTIES = [
    "CarsonCity",
    "Churchill",
    "Clark",
    "Douglas",
    "Elko",
    "Esmeralda",
    "Eureka",
    "Humboldt",
    "Lander",
    "Lincoln",
    "Lyon",
    "Mineral",
    "Nye",
    "Pershing",
    "Storey",
    "Washoe",
    "WhitePine",
]
OFFICES = {
    "lieutenant governor": "Lieutenant Governor",
    "secretary of state": "Secretary of State",
    "state treasurer": "Treasurer",
    "state controller": "Controller",
    "attorney general": "Attorney General",
}
PARTIES = {
    "D": "DEMOCRATIC",
    "R": "REPUBLICAN",
    "L": "LIBERTARIAN",
    "IA": "INDEPENDENT AMERICAN",
    "NL": "NATURAL LAW",
    "DEM": "DEMOCRATIC",
    "REP": "REPUBLICAN",
    "LIB": "LIBERTARIAN",
    "GRN": "GREEN",
    "IAP": "INDEPENDENT AMERICAN",
    "NAT": "NATURAL LAW",
    "CON": "CONSTITUTION",
    "CST": "CONSTITUTION",
}
PDF = "https://web.archive.org/web/2016id_/http://www.nvsos.gov/SOSelectionPages/results/1998General/1998GeneralElectionResults.pdf"
HEADINGS = {
    "lieutenant governor": "Lieutenant Governor",
    "secretary of state": "Secretary of State",
    "state treasurer": "Treasurer",
    "state controller": "Controller",
    "state attorney general": "Attorney General",
}
METHOD = (
    "County totals come from the Nevada Secretary of State's official county results pages. Each candidate's county "
    "votes add up to the statewide total on the Secretary of State's summary page."
)
MISSING = (
    "The Secretary of State's pages have no row for {counties} in this race; those votes are missing from these totals."
)
CLOSE = (
    "The race was within 0.5% in these totals, so no winner is recorded here; the official canvass and any recount "
    "decided it."
)


def address(year: int, page: str) -> str:
    return f"{WAYBACK}{FOLDERS[year]}/{page}.aspx"


def fetch() -> None:
    def get(url: str) -> None:
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, url, suffix=".html")
                return
            except httpx.TransportError, httpx.HTTPStatusError:
                time.sleep(10 * (attempt + 1))
        raise RuntimeError(url)

    raw.fetch(SOURCE, PDF, suffix=".pdf")
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(get, [address(year, page) for year in FOLDERS for page in [*COUNTIES, "ElectionSummary"]]))


def tokens(path: Path) -> list[str]:
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", path.read_text(errors="ignore"))
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "|", text)).replace("\xa0", " "))
    return [token.strip() for token in text.split("|") if token.strip()]


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["NV"]
    rows: list[tuple[int, str, str, str | None, int, str]] = []
    for year in FOLDERS:
        for county in COUNTIES:
            words = tokens(raw.cached(SOURCE, address(year, county)).path)
            starts = [index for index, word in enumerate(words) if "Precincts Reporting" in word]
            for position, start in enumerate(starts):
                title = OFFICES.get(words[start - 1].lower())
                if title is None:
                    continue
                end = starts[position + 1] - 1 if position + 1 < len(starts) else len(words)
                held: list[str] = []
                index = start
                while index < end:
                    word = words[index]
                    if re.fullmatch(r"\d+(?:\.\d+)?%", word):
                        votes = int(words[index + 1].replace(",", ""))
                        held = [text for text in held if text != "Incumbent"]
                        party = None
                        if len(held) > 1 and re.fullmatch(r"[A-Z]{2,4}", held[-1]):
                            party = PARTIES.get(held[-1], "")
                            if party == "" and held[-1] != "IND":
                                raise ValueError(f"{year} {county} {title}: unknown party {held[-1]}")
                            held = held[:-1]
                            party = party or None
                        name = held[-1]
                        if not re.match(r"(?i)(under|over) votes", name):
                            rows.append((year, title, name, party, votes, fips[county.lower()]))
                        held = []
                        index += 2
                        continue
                    held.append(word)
                    index += 1
    title = None
    pending: list[str] = []
    for line in subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, PDF).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines():
        found = re.fullmatch(r"(.*?)((?:\s+\d[\d,]*){18})\s*", line)
        if found is None:
            text = line.strip()
            if text.lower() in HEADINGS or re.match(r"State (Senate|Assembly)|Regent|Justice|District", text):
                title, pending = HEADINGS.get(text.lower()), []
            elif text and title and not re.match(r"State of Nevada|Carson|City Churchill|\d+$", text):
                pending.append(text)
            continue
        if title is None:
            pending = []
            continue
        label = re.sub(r"\bA(?=None)|\bA\S*@", "", " ".join([*pending, found[1].strip()]))
        pending = []
        figures = [int(value.replace(",", "")) for value in found[2].split()]
        if sum(figures[:-1]) != figures[-1]:
            raise ValueError(f"1998 {title} {label}: the counties do not add up to the printed total")
        label = re.sub(r"\s+", " ", re.sub(r"-\s+", "-", label.replace("@", ""))).strip()
        party = re.search(r"\((\w+)\)$", label)
        name = re.sub(r"\s*\(\w+\)$", "", label)
        if name.lower().startswith("none of these"):
            name = "None of these Candidates"
        for county, votes in zip(COUNTIES, figures, strict=False):
            rows.append(
                (1998, title, name, PARTIES.get(party[1], None) if party else None, votes, fips[county.lower()])
            )
    for year in FOLDERS:
        words = tokens(raw.cached(SOURCE, address(year, "ElectionSummary")).path)
        for index, word in enumerate(words):
            title = OFFICES.get(word.lower())
            if title is None or words[index + 1 : index + 3] != ["Percent", "Total Votes"]:
                continue
            cursor = index + 3 + len(COUNTIES)
            printed = []
            while cursor + 3 < len(words) and re.fullmatch(r"\d+(?:\.\d+)?%", words[cursor + 1]):
                if not re.match(r"(?i)(under|over) votes", words[cursor]):
                    printed.append(int(words[cursor + 2].replace(",", "")))
                cursor += 3 + len(COUNTIES)
            summed = sorted(
                vote
                for vote in pl.DataFrame(
                    [row for row in rows if row[0] == year and row[1] == title],
                    schema=["year", "title", "name", "party", "votes", "county"],
                    orient="row",
                )
                .group_by("name")
                .agg(pl.col("votes").sum())["votes"]
            )
            if summed != sorted(printed):
                raise ValueError(
                    f"{year} {title}: county sums {summed} differ from the printed totals {sorted(printed)}"
                )
    year = pl.col("year").cast(pl.Int32)
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "year": pl.Int32,
                "title": pl.String,
                "candidate": pl.String,
                "party": pl.String,
                "votes": pl.Int64,
                "county": pl.String,
            },
            orient="row",
        )
        .with_columns(candidate=pl.col("candidate").str.replace(r"^([^,]+), (.+)$", "$2 $1").str.to_uppercase())
        .select(
            year=year,
            day=pl.struct(year=year).map_elements(
                lambda row: date(row["year"], 11, 2 + (-date(row["year"], 11, 1).weekday()) % 7), return_dtype=pl.Date
            ),
            state=pl.lit("NV"),
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
            votes="votes",
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(len(COUNTIES)),
    ).write_parquet(PARSED / "races.parquet")
    print(
        frame.group_by("year", "title", "candidate", "party")
        .agg(pl.col("votes").sum())
        .sort("year", "title", "votes", descending=[False, False, True])
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
                url="https://www.nvsos.gov/sos/elections/election-information/election-results-archive",
                license="Public record",
            )
        for year in FOLDERS:
            for page in [*COUNTIES, "ElectionSummary"]:
                cs.artifact(raw.cached(SOURCE, address(year, page)), source)
        return {"NV": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Nevada's lieutenant governor, secretary of state, treasurer, controller, and attorney general of 1998, 2002, "
        "2006, 2010, and 2014 by county from the Secretary of State's official results",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
    )
