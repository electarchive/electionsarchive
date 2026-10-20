import html
import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ok_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://oklahoma.gov/elections/elections-results/election-results/1998-election-results/98genst.html"
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Official results of the general election, November 3, 1998"
OFFICES = {
    "LIEUTENANT GOVERNOR": "Lieutenant Governor",
    "STATE AUDITOR AND INSPECTOR": "Auditor and Inspector",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "Superintendent of Public Instruction",
    "COMMISSIONER OF LABOR": "Commissioner of Labor",
    "INSURANCE COMMISSIONER": "Insurance Commissioner",
    "CORPORATION COMMISSIONER": "Corporation Commissioner",
}
PARTIES = {"R": "REPUBLICAN", "D": "DEMOCRATIC", "Rfm": "REFORM"}
METHOD = (
    "County totals come from the State Election Board's official results of the 1998 general election, "
    "each county row summing to its printed total."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%'"""
            ).fetchall()
        }
    text = html.unescape(raw.cached(SOURCE, URL).path.read_text()).replace("\\n", "\n").replace('\\"', '"')
    rows = []
    done: set[str] = set()
    for office, body in re.findall(r"<h3>([^<]+)</h3>\s*</span>(.*?)(?=<h3>|$)", text, flags=re.DOTALL):
        if office not in OFFICES or office in done:
            continue
        done.add(office)
        lines = re.sub(r"<[^>]+>", "", body).splitlines()
        rule = next(n for n, line in enumerate(lines) if "===" in line)
        first, second = (re.split(r"\s{2,}", lines[n].strip()) for n in (rule - 2, rule - 1))
        if len(first) != len(second) or second[-1] != "VOTES":
            raise ValueError(f"{office}: {first} and {second} do not line up")
        candidates = []
        for given, last in zip(first[:-1], second[:-1], strict=True):
            party = re.search(r"\((\w+)\)$", last)
            candidates.append((re.sub(r"\s*\(\w+\)$", "", f"{given} {last}").upper(), PARTIES[party[1]]))
        counties = 0
        total = None
        for line in lines[rule + 1 :]:
            if not (row := re.fullmatch(r"(\S.*?)\s+((?:[\d,]+\s+)+[\d,]+)\s*", line)):
                continue
            votes = [int(value.replace(",", "")) for value in row[2].split()]
            if len(votes) != len(candidates) + 1 or sum(votes[:-1]) != votes[-1]:
                raise ValueError(f"{office} {row[1]}: {votes}")
            if row[1].startswith("STATE TOTAL"):
                total = votes
                continue
            counties += 1
            rows.extend(
                (OFFICES[office], codes[re.sub(r"[^a-z]", "", row[1].lower())], name, party, value)
                for (name, party), value in zip(candidates, votes, strict=False)
            )
        if counties != 77:
            raise ValueError(f"{office}: {counties} counties")
        for index, (name, _) in enumerate(candidates):
            summed = sum(row[4] for row in rows if row[0] == OFFICES[office] and row[2] == name)
            if total is None or summed != total[index]:
                raise ValueError(f"{office} {name}: counties sum to {summed}, printed {total}")
    frame = pl.DataFrame(rows, schema=["title", "county", "candidate", "party", "votes"], orient="row").select(
        year=pl.lit(1998, pl.Int32),
        day=pl.lit("1998-11-03"),
        state=pl.lit("OK"),
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
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(77),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title", "votes").rows())


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            raise ValueError("no Oklahoma State Election Board source")
        cs.artifact(raw.cached(SOURCE, URL), source)
        return {"OK": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Oklahoma's 1998 lieutenant governor, auditor and inspector, superintendent, labor commissioner, "
        "insurance commissioner, and corporation commissioner by county from the State Election Board's official results",
        cite=cite,
        method=METHOD,
        declared={
            "1998-11-03:OK:insurance-commissioner": (
                "Carroll Fisher",
                (
                    "The race was within 0.5% in these totals. Carroll Fisher led John Crawford by 2,634 votes in the "
                    "State Election Board's official results and took office as Insurance Commissioner in January 1999."
                ),
            )
        },
    )
