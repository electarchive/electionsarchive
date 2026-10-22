import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ky_pri_county import CODES, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ky_house_generals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2000-2009/"
LANDING = "https://elect.ky.gov/results/2000-2009/Pages/default.aspx"
TITLES = {"KY": "General election results by county", "KYL": "General election results by county: state legislature"}
FILES = {
    date(2000, 11, 7): "2000/00Gen_Statewidebycounty.txt",
    date(2002, 11, 5): "2002/General%20Election/2002statebycounty.txt",
    date(2004, 11, 2): "2004/General%20Election/2004statebyCOUNTY.txt",
    date(2006, 11, 7): "2006/General%20Election/STATEwidebycounty.txt",
    date(2008, 11, 4): "2008/General%20Election/STATEwide%20by%20candidate%20by%20county%20gen%2008.txt",
}
OFFICE = re.compile(r"OFFICE: \S+\s+(U\.S\. REPRESENTATIVE|KY REPRESENTATIVE|KY SENATOR)\s+(\d+)(?:ST|ND|RD|TH)\s+DIST?\s*$")
CHAMBERS = {"U.S. REPRESENTATIVE": "house", "KY REPRESENTATIVE": "house:{}:g", "KY SENATOR": "senate:{}:g"}


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".txt")


def parse() -> None:
    rows = []
    for day, path in FILES.items():
        text = raw.cached(SOURCE, BASE + path).path.read_text(errors="replace")
        district = None
        columns: list[str] = []
        counts: dict[str, dict[str, int]] = {}
        totals: dict[str, int] = {}
        blocks: list[tuple[str, int, dict[str, dict[str, int]], dict[str, int]]] = []
        for line in text.splitlines():
            if "OFFICE:" in line:
                found = OFFICE.search(line.rstrip())
                district, columns, counts, totals = (int(found[2]) if found else None), [], {}, {}
                if found and district:
                    blocks.append((found[1], district, counts, totals))
            elif district is None:
                continue
            elif not line.strip():
                columns = []
            elif re.match(r"\s{20,}\*[A-Z]", line):
                columns = re.findall(r"\*([A-Z]+)", line)
            elif found := re.fullmatch(r"\s*(.*?[^\d,\s])\s+([\d,\s]+)", line):
                name = re.sub(r"\s+", " ", found[1].replace('"', "")).strip()
                cells = [int(value.replace(",", "")) for value in re.findall(r"[\d,]+", found[2])]
                if not columns:
                    totals[name] = sum(cells)
                    continue
                if len(cells) != len(columns):
                    cells = [0] * len(columns)
                    for match in re.finditer(r"[\d,]+", line):
                        if match.start() >= found.start(2):
                            cells[min(len(columns) - 1, max(0, match.end() - 35) // 8)] = int(match[0].replace(",", ""))
                for code, value in zip(columns, cells, strict=True):
                    counts.setdefault(name, {})[code] = value
        for office, district, counts, totals in blocks:
            sums = {name: sum(values.values()) for name, values in counts.items()}
            if {name: totals.get(name) for name in sums} != sums:
                print(f"{day} {office} {district}: counties sum to {sums}, printed {totals}, skipped")
                continue
            federal = office == "U.S. REPRESENTATIVE"
            rows.extend(
                (
                    day,
                    CHAMBERS[office].format(district),
                    district if federal else None,
                    f"21{2 * CODES.index(code) + 1:03}",
                    name,
                    votes,
                    "KY" if federal else "KYL",
                )
                for name, values in counts.items()
                for code, votes in values.items()
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "source": pl.String,
        },
        orient="row",
    ).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        special=pl.lit(False),
        state=pl.lit("KY"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.contains("(?i)write"),
        kind=pl.lit("county"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kentucky's U.S. House and legislative generals by county, 2000-2008 (State Board of Elections)",
            polity="US-KY",
        )
        sources = {key: cs.source(PUBLISHER, title) for key, title in TITLES.items()}
        for key, title in TITLES.items():
            if sources[key] in cs.minted:
                cs.add(
                    "source",
                    id=sources[key],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=LANDING,
                    license="Public record",
                )
            for path in FILES.values():
                cs.artifact(raw.cached(SOURCE, BASE + path), sources[key])
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), sources, strict=True)
        return cs.write()
