import html
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.ne_house_generals import LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ne_pri_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2000, 5, 9)
TITLE = "2000 primary election results"
BASE = "https://web.archive.org/web/{}id_/http://www.sos.nebraska.gov/elec/prev_elec/2000/{}.html"
PAGES = {
    ("house", "republican"): ("20170201060542", "rep_congress"),
    ("senate", "republican"): ("20170201060526", "rep_senate"),
    ("house", "democratic"): ("20170201060624", "dem_congress"),
    ("senate", "democratic"): ("20170201060617", "dem_senate"),
    ("house", "libertarian"): ("20170201060715", "lib_congress"),
    ("senate", "libertarian"): ("20170201060705", "lib_senate"),
    ("house", "natural-law"): ("20170201061058", "nat_congress"),
    ("senate", "natural-law"): ("20170201061050", "nat_senate"),
}
DISTRICTS = {"First": 1, "Second": 2, "Third": 3}
VOTERS = re.compile(
    r"(?:Nonpartisan )?(?:Republican|Democrat|Libertarian|Natural Law|Nonpartisan)(?: Voters| Democrat| Republican| Libertarian| Natural Law)?"
)


def fetch() -> None:
    for stamp, name in PAGES.values():
        raw.fetch(SOURCE, BASE.format(stamp, name), suffix=".html")


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
    rows = []
    for (chamber, party), (stamp, name) in PAGES.items():
        text = raw.cached(SOURCE, BASE.format(stamp, name)).path.read_text(errors="replace")
        table = [
            [
                html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
            ]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL | re.IGNORECASE)
        ]
        district = None
        names: list[str] = []
        sums: dict[tuple, int] = {}
        printed: dict[tuple, int] = {}
        totals: set[tuple] = set()
        for cells in table:
            filled = [cell for cell in cells if cell]
            if not filled:
                continue
            if found := re.search(r"(First|Second|Third) District", " ".join(filled)):
                district, names = DISTRICTS[found[1]], []
                continue
            if any("Senate" in cell for cell in filled) or all(VOTERS.fullmatch(cell) for cell in filled):
                continue
            if not any(cell.replace(",", "").isdigit() for cell in filled):
                if "County" in filled:
                    marks = [k for k, cell in enumerate(filled) if cell == "County"] + [len(filled)]
                    filled = filled[marks[0] + 1 : marks[1]]
                period = next(
                    p for p in range(1, len(filled) + 1) if all(filled[k] == filled[k % p] for k in range(len(filled)))
                )
                names = filled[:period]
                continue
            groups: list[tuple[str, list[int]]] = []
            for cell in filled:
                if cell.replace(",", "").isdigit():
                    groups[-1][1].append(int(cell.replace(",", "")))
                else:
                    groups.append((cell, []))
            for label, numbers in groups:
                if len(numbers) != len(names):
                    raise ValueError(f"{name} {district}: {label} {numbers} against {names}")
                if label == "TOTAL":
                    if (district, *numbers) in totals:
                        continue
                    totals.add((district, *numbers))
                for candidate, votes in zip(names, numbers, strict=True):
                    key = (district, candidate)
                    if label == "TOTAL":
                        printed[key] = printed.get(key, 0) + votes
                        continue
                    sums[key] = sums.get(key, 0) + votes
                    rows.append(
                        (
                            f"{chamber}:{party}",
                            district,
                            codes[re.sub(r"[^a-z]", "", label.lower())],
                            "WRITE-INS" if candidate == "Write In" else candidate.upper(),
                            candidate == "Write In",
                            votes,
                        )
                    )
        for key, votes in sums.items():
            if abs(votes - printed[key]) > max(10, printed[key] * 0.01):
                raise ValueError(f"{name} {key}: counties sum to {votes}, printed {printed[key]}")
            if votes != printed[key]:
                print(f"{name} {key}: counties sum to {votes}, printed {printed[key]}")
    frame = (
        pl.DataFrame(
            rows,
            schema={
                "chamber": pl.String,
                "district": pl.Int16,
                "code": pl.String,
                "candidate": pl.String,
                "writein": pl.Boolean,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .group_by("chamber", "district", "code", "candidate", "writein")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.lit(DAY.year, pl.Int32),
            day=pl.lit(DAY),
            special=pl.lit(False),
            state=pl.lit("NE"),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("NE"),
        )
    )
    print(
        frame.group_by("chamber", "district")
        .agg(pl.col("candidate").n_unique(), pl.col("code").n_unique())
        .sort("chamber", "district")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska's 2000 U.S. Senate and House primaries by county, from the Secretary of State's results pages",
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
        for stamp, name in PAGES.values():
            cs.artifact(raw.cached(SOURCE, BASE.format(stamp, name)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NE": source}, strict=True)
        return cs.write()
