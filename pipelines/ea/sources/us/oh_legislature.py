import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.oh_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ohio Secretary of State"
TITLE = "Official results, general election: Ohio House and Senate"
LANDING = "https://www.ohiosos.gov/elections/election-results-and-data/"
WAYBACK = "https://web.archive.org/web/{}id_/" + LANDING + "{}/"
PAGES = {
    date(2000, 11, 7): {
        "house": ("20190819100436", "2000-elections-results/ohio-house-of-representatives-november-7-2000"),
        "senate": ("20190826031306", "2000-elections-results/ohio-senate-november-7-2000"),
    },
    date(2002, 11, 5): {
        "house": ("20190823100358", "2002-elections-results/state-representative"),
        "senate": ("20190826031019", "2002-elections-results/state-senate"),
    },
    date(2004, 11, 2): {
        "house": ("20190823093536", "2004-elections-results/ohio-house-of-representatives-november-2-2004"),
        "senate": ("20190826030321", "2004-elections-results/ohio-senate-november-2-2004"),
    },
    date(2006, 11, 7): {
        "house": ("20190915073843", "2006-elections-results/ohio-house-of-representatives-november-7-2006"),
        "senate": ("20190915073923", "2006-elections-results/ohio-senate-november-7-2006"),
    },
}
MARK = re.compile(r"(?:DISTRICT NUMBER:\s*|District\s+)(\d+)", re.IGNORECASE)
NUMBER = re.compile(r"[\d,]+")


def url(day: date, chamber: str) -> str:
    return WAYBACK.format(*PAGES[day][chamber])


def fetch() -> None:
    for day, pages in PAGES.items():
        for chamber in pages:
            raw.fetch(SOURCE, url(day, chamber), suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%'"""
            ).fetchall()
        }
    rows = []
    for day, pages in PAGES.items():
        for chamber in pages:
            text = raw.cached(SOURCE, url(day, chamber)).path.read_text(errors="replace")
            district: int | None = None
            names: list[str] = []
            parts: list[tuple[str, list[int]]] = []
            total: list[int] | None = None
            blocks: list[tuple[int | None, list[str], list[tuple[str, list[int]]], list[int] | None]] = []
            for tr in [*re.findall(r"<tr.*?</tr>", text, flags=re.DOTALL), "<tr><td>District 0</td></tr>"]:
                cells = [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
                    for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.DOTALL)
                ]
                filled = [cell for cell in cells if cell]
                if not filled:
                    continue
                label, rest = filled[0], filled[1:]
                if found := MARK.fullmatch(label):
                    if district is not None and names:
                        blocks.append((district, names, parts, total))
                    district, parts, total = int(found[1]), [], None
                    names = [name.lstrip("* ") for name in rest if not NUMBER.fullmatch(name)]
                elif district is None or label.lower().startswith("percent"):
                    continue
                elif label.lower() == "county":
                    names = [name.lstrip("* ") for name in rest]
                elif rest and all(NUMBER.fullmatch(cell) for cell in rest):
                    votes = [int(cell.replace(",", "")) for cell in rest]
                    if label.lower() == "total":
                        total = votes
                    elif single := re.fullmatch(r"(.+?)\**\s+Total", label, flags=re.IGNORECASE):
                        parts.append((single[1], votes))
                        total = votes
                    else:
                        parts.append((label.rstrip("* "), votes))
                elif not names and not any(NUMBER.fullmatch(cell) for cell in filled):
                    names = [name.lstrip("* ") for name in filled]
            for district, names, parts, total in blocks:
                key = f"{chamber}:{district}:g"
                sums = [sum(votes[c] for _, votes in parts if c < len(votes)) for c in range(len(names))]
                codes = [counties.get(re.sub(r"[^a-z]", "", county.lower())) for county, _ in parts]
                problem = (
                    "no counties"
                    if not parts
                    else f"unknown county in {[county for county, _ in parts]}"
                    if None in codes
                    else "a row whose cells don't match the names"
                    if any(len(votes) != len(names) for _, votes in parts)
                    else "several counties and no total"
                    if total is None and len(parts) > 1
                    else f"counties sum to {sums}, printed {total}"
                    if total is not None and sums != total
                    else None
                )
                if problem:
                    print(f"{day} {key}: {problem}, skipped")
                    continue
                rows.extend(
                    (day, key, code, name, "write" in name.lower(), count)
                    for code, (_, votes) in zip(codes, parts, strict=True)
                    for name, count in zip(names, votes, strict=True)
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("OH"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("OH"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's legislative generals by county, 2000-2006 (Secretary of State official results pages, via the Wayback Machine)",
            polity="US-OH",
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
        for day, pages in PAGES.items():
            for chamber in pages:
                cs.artifact(raw.cached(SOURCE, url(day, chamber)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OH": source}, strict=True)
        return cs.write()
