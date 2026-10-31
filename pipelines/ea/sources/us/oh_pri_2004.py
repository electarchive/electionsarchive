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
BASE = "https://www.ohiosos.gov/elections/election-results-and-data/"
WAYBACK = "http://web.archive.org/web/2023id_/{}"
ELECTIONS = {
    date(2002, 5, 7): (
        "2002-elections-results/{}/",
        {("house", party): f"{party}-u.s.-representative" for party in ("democratic", "republican")},
    ),
    date(2004, 3, 2): (
        "2004-elections-results/{}-march-2-2004/",
        {
            (chamber, party): f"{party}-u.s.-{office}"
            for chamber, office in (("house", "representative"), ("senate", "senate"))
            for party in ("democratic", "republican")
        },
    ),
    date(2010, 5, 4): (
        "2010-elections-results/{}-may-4-2010/",
        {
            ("house", party): f"representative-to-congress---{party}"
            for party in ("democratic", "republican", "libertarian", "green", "constitution")
        }
        | {
            ("senate", party): f"u.-s.-senator---{party}"
            for party in ("democratic", "republican", "constitution", "socialist")
        },
    ),
}
PUBLISHER = "Ohio Secretary of State"
TITLE = "Official results, {} primary election"


def link(day: date, page: str) -> str:
    return WAYBACK.format(BASE + ELECTIONS[day][0].format(page))


def fetch() -> None:
    for day, (_, pages) in ELECTIONS.items():
        for page in pages.values():
            raw.fetch(SOURCE, link(day, page), suffix=".html")


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
    for day, (_, pages) in ELECTIONS.items():
        for (chamber, party), page in pages.items():
            text = raw.cached(SOURCE, link(day, page)).path.read_text(errors="replace")
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
                header = [
                    " ".join(reversed([part.strip() for part in name.strip("*").split(",", 1)])) for name in cells[1:]
                ]
                if found := re.fullmatch(r"(?:DISTRICT NUMBER:\s*|District\s+)(\d+)", cells[0], flags=re.IGNORECASE):
                    district = int(found[1])
                    names, parts = header, []
                elif cells[0].lower() == "county":
                    if header != names:
                        names, parts = header, []
                elif not names or len(cells) != 1 + len(names) or not numeric or cells[0].lower().startswith("percent"):
                    continue
                elif cells[0] and not cells[0].lower().endswith("total"):
                    parts.append((cells[0].rstrip("*"), [int(cell.replace(",", "")) for cell in cells[1:]]))
                else:
                    total = [int(cell.replace(",", "")) for cell in cells[1:]]
                    if single := re.fullmatch(r"(.+?)\**\s*Total", cells[0], flags=re.IGNORECASE):
                        parts.append((single[1], total))
                    sums = [sum(votes[c] for _, votes in parts) for c in range(len(names))]
                    if sums != total:
                        print(f"{day} {chamber} {party} {district}: {sums} by county, {total} printed, skipped")
                    else:
                        rows.extend(
                            (
                                day.year,
                                f"{chamber}:{party}",
                                day,
                                False,
                                "OH",
                                district,
                                counties[re.sub(r"[^a-z]", "", county.lower())],
                                name.upper(),
                                1,
                                "write" in name.lower(),
                                count,
                            )
                            for county, votes in parts
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
            summary="Ohio's 2002, 2004, and 2010 U.S. House and Senate primaries by county from the Secretary of State's official results",
            polity="US-OH",
        )
        frame = pl.read_parquet(PARSED / "counties.parquet")
        for day, (_, pages) in ELECTIONS.items():
            title = TITLE.format(f"{day:%B} {day.day}, {day.year}")
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=BASE + ELECTIONS[day][0].split("/")[0] + "/",
                    license="Public record",
                )
            for page in pages.values():
                cs.artifact(raw.cached(SOURCE, link(day, page)), source)
            medsl.attach(cs, conn, frame.filter(pl.col("day") == day), {"OH": source}, strict=True)
        return cs.write()
