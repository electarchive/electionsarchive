import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ne_house_generals import BASE, LANDING, PUBLISHER
from ea.sources.us.ne_leg_pri_2014 import PATH, TITLE
from ea.sources.us.ne_primaries import COUNTIES
from ea.store import DATA, connect

SOURCE = "us.ne_pri_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2014, 5, 13)
CELL = re.compile(
    r"(?<![A-Za-z])(TOTALS|TOTAL|Total|"
    + "|".join(sorted(map(re.escape, COUNTIES), key=len, reverse=True))
    + r")(?![A-Za-z])(?:\s*\d+ precincts)?((?:\s+\d[\d,]*)+)"
)
DISTRICTS = {"One": 1, "Two": 2, "Three": 3}


def fetch() -> None:
    raw.fetch(SOURCE, BASE + PATH, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + PATH).path), "-"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    start = next(k for k, line in enumerate(lines) if line.strip() == "Member of the United States Senate")
    end = next(k for k in range(start, len(lines)) if lines[k].strip() == "State Constitutional Offices")
    rows = []
    chamber, party, district = "senate", "", None
    names: list[str] = []
    printed: dict[str, int] = {}
    sums: dict[str, int] = {}

    def check() -> None:
        if names and printed != sums:
            raise ValueError(f"{chamber} {district} {party}: counties sum to {sums}, printed {printed}")

    for line in lines[start:end]:
        if line.strip() == "Member of the U.S. House of Representatives":
            check()
            chamber, names = "house", []
        elif found := re.match(r"\s*District (One|Two|Three)\b", line):
            check()
            district, names = DISTRICTS[found[1]], []
        elif found := re.match(r"\s*(Republican|Democratic|Libertarian) Party Nomination", line):
            check()
            party, names, printed, sums = found[1].lower(), [], {}, {}
        elif line.lstrip().startswith("Candidate"):
            names = list(dict.fromkeys(n for n in re.split(r"\s{2,}", line.strip()) if n != "Candidate"))
        elif names:
            for cell in CELL.finditer(line):
                numbers = [int(v.replace(",", "")) for v in cell[2].split()]
                if cell[1] in ("TOTALS", "TOTAL", "Total") and len(numbers) == 3 * len(names):
                    printed = dict(zip(names, numbers[2::3], strict=True))
                    continue
                totals = []
                while numbers:
                    if len(numbers) >= 3 and numbers[0] + numbers[1] == numbers[2]:
                        totals.append(numbers[2])
                        numbers = numbers[3:]
                    elif len(numbers) >= 2 and numbers[0] == numbers[1]:
                        totals.append(numbers[1])
                        numbers = numbers[2:]
                    else:
                        break
                if numbers or len(totals) != len(names):
                    raise ValueError(f"{chamber} {district} {party}: {line!r} against {names}")
                if cell[1] in ("TOTALS", "TOTAL", "Total"):
                    printed = dict(zip(names, totals, strict=True))
                    continue
                code = codes[re.sub(r"[^a-z]", "", cell[1].lower())]
                for name, votes in zip(names, totals, strict=True):
                    sums[name] = sums.get(name, 0) + votes
                    rows.append(
                        (
                            DAY.year,
                            f"{chamber}:{party}",
                            DAY,
                            False,
                            "NE",
                            district,
                            code,
                            name.upper(),
                            1,
                            False,
                            votes,
                        )
                    )
    check()
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(kind=pl.lit("county"), source=pl.lit("NE"))
    print(
        frame.group_by("chamber", "district").agg(pl.col("candidate").n_unique(), pl.len()).sort("chamber", "district")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's 2014 U.S. Senate and House primaries by county, from the Board of State Canvassers' "
            "official report",
            polity="US-NE",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, BASE + PATH), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NE": source}, strict=True)
        return cs.write()
