import re
import subprocess
from datetime import UTC, date, datetime

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA

SOURCE = "us.nm_primary_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Mexico Secretary of State"
TITLE = "Primary Election Results by County"
LANDING = "https://electionstats.sos.nm.gov/"
SERVE = "https://nm.elstats2.civera.com/eng/files/serve/{id}"
FILES = {date(2004, 6, 1): 455, date(2006, 6, 6): 453, date(2010, 6, 1): 451, date(2012, 6, 5): 449}
COUNTIES = [
    "001",
    "003",
    "005",
    "006",
    "007",
    "009",
    "011",
    "013",
    "015",
    "017",
    "019",
    "021",
    "023",
    "025",
    "027",
    "028",
    "029",
    "031",
    "033",
    "035",
    "037",
    "039",
    "041",
    "043",
    "045",
    "047",
    "049",
    "051",
    "053",
    "055",
    "057",
    "059",
    "061",
]
PARTIES = {"DEMOCRAT": "democratic", "REPUBLICAN": "republican", "GREEN": "green", "LIBERTARIAN": "libertarian"}
ROW = re.compile(r"^(.*?[A-Za-z\)\.])\s+((?:\d[\d,]*\s*){34})$")
METHOD = (
    "County totals are from the Secretary of State's compilation of the State Canvassing Board's Canvass of Returns "
    "of the primary election, Primary Election Results by County; each candidate's county cells sum to the total "
    "printed in the compilation."
)


def fetch() -> None:
    for number in FILES.values():
        url = SERVE.format(id=number)
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sL", "-m", "600", "-A", "Mozilla/5.0", url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, body, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def office_of(heading: str, office: str | None) -> str | None:
    if re.fullmatch(r"DISTRICT \d+", heading) and office == "UNITED STATES REPRESENTATIVE":
        return heading
    if heading.startswith("PRESIDENT"):
        return "president"
    if heading == "UNITED STATES SENATOR":
        return "senate"
    if heading == "UNITED STATES REPRESENTATIVE":
        return heading
    return None


def parse() -> None:
    rows = []
    for day, number in FILES.items():
        path = raw.cached(SOURCE, SERVE.format(id=number)).path
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True
        ).stdout
        for page in text.split("\f"):
            lines = [line.strip() for line in page.split("\n") if line.strip()]
            head = re.match(r"(DEMOCRAT|REPUBLICAN|GREEN|LIBERTARIAN)\b", lines[0]) if lines else None
            if head is None or "Canvass" not in lines[0]:
                continue
            party = PARTIES[head[1]]
            office: str | None = None
            contest: str | None = None
            for line in lines[1:]:
                found = ROW.match(line)
                if found:
                    if contest is None:
                        continue
                    cells = [int(cell.replace(",", "")) for cell in found[2].split()]
                    if sum(cells[:-1]) != cells[-1]:
                        print(f"{day} {contest} {found[1]}: counties sum {sum(cells[:-1])}, printed {cells[-1]}")
                    for county, votes in zip(COUNTIES, cells[:-1], strict=True):
                        rows.append((str(day), contest, f"35{county}", found[1].strip(), votes))
                    continue
                if re.fullmatch(r"[A-Z][A-Z .,&'\-]+|DISTRICT \d+|POSITION \d+", line):
                    kind = office_of(line, office)
                    if kind is None:
                        office = line
                        contest = None
                    elif kind.startswith("DISTRICT"):
                        contest = f"{kind.split()[1]}:{party}"
                    elif kind == "UNITED STATES REPRESENTATIVE":
                        office = kind
                        contest = None
                    else:
                        office = line
                        contest = f"{kind}:{party}"
    frame = pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row")
    frame = frame.filter(pl.col("votes").sum().over("day", "district", "county") > 0)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("day", "district")
        .agg(pl.col("county").n_unique(), pl.col("votes").sum())
        .sort("day", "district")
    )


def emit():
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "New Mexico's U.S. House, Senate, and presidential primaries of 2004, 2006, 2010, and 2012 by county from the "
        "Secretary of State's compilation of the canvass of returns",
        PUBLISHER,
        TITLE,
        LANDING,
        "NM",
        "35",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, SERVE.format(id=number)) for number in FILES.values()],
        lambda key: METHOD,
        official=True,
    )
