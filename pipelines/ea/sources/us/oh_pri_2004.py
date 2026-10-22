import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.oh_pri_2004"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2004, 3, 2)
INDEX = "https://www.ohiosos.gov/elections/election-results-and-data/2004-elections-results/{}-march-2-2004/"
WAYBACK = "http://web.archive.org/web/2023id_/{}"
PAGES = {
    ("house", "democratic"): "democratic-u.s.-representative",
    ("house", "republican"): "republican-u.s.-representative",
    ("senate", "democratic"): "democratic-u.s.-senate",
    ("senate", "republican"): "republican-u.s.-senate",
}
PUBLISHER = "Ohio Secretary of State"
TITLE = "Official results, March 2, 2004 primary election"


def link(page: str) -> str:
    return WAYBACK.format(INDEX.format(page))


def fetch() -> None:
    for page in PAGES.values():
        raw.fetch(SOURCE, link(page), suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%'"""
            ).fetchall()
        }
    rows: list[tuple] = []
    for (chamber, party), page in PAGES.items():
        text = raw.cached(SOURCE, link(page)).path.read_text(errors="replace")
        district = None if chamber == "senate" else 0
        names: list[str] = []
        parts: list[tuple[str, list[int]]] = []
        for tr in re.findall(r"<tr.*?</tr>", text, flags=re.DOTALL):
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
                for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.DOTALL)
            ]
            while cells and not cells[-1]:
                cells.pop()
            if not cells:
                continue
            numeric = all(re.fullmatch(r"[\d,]+", cell) for cell in cells[1:])
            if found := re.fullmatch(r"DISTRICT NUMBER:\s*(\d+)", cells[0], flags=re.IGNORECASE):
                district = int(found[1])
                names, parts = [], []
            elif cells[0] == "COUNTY":
                names = [" ".join(reversed([part.strip() for part in name.split(",", 1)])) for name in cells[1:]]
            elif not names or len(cells) != 1 + len(names) or not numeric:
                continue
            elif cells[0]:
                parts.append((cells[0], [int(cell.replace(",", "")) for cell in cells[1:]]))
            else:
                total = [int(cell.replace(",", "")) for cell in cells[1:]]
                sums = [sum(votes[c] for _, votes in parts) for c in range(len(names))]
                if sums != total:
                    raise ValueError(f"{chamber} {party} {district}: {sums} by county, {total} printed")
                for county, votes in parts:
                    rows.extend(
                        (
                            2004,
                            f"{chamber}:{party}",
                            DAY,
                            False,
                            "OH",
                            district,
                            counties[re.sub(r"[^a-z]", "", county.lower())],
                            name.upper(),
                            1,
                            False,
                            count,
                        )
                        for name, count in zip(names, votes, strict=True)
                    )
                names, parts = ([], []) if chamber == "house" else (names, [])
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
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
        )
        .with_columns(kind=pl.lit("county"), source=pl.lit("OH"))
        .write_parquet(PARSED / "counties.parquet")
    )
    print(len(rows), "candidate rows")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's March 2, 2004 U.S. House and Senate primaries by county from the Secretary of State's official results",
            polity="US-OH",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.ohiosos.gov/elections/election-results-and-data/2004-elections-results/",
            license="Public record",
        )
        for page in PAGES.values():
            cs.artifact(raw.cached(SOURCE, link(page)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OH": source}, strict=True)
        return cs.write()
