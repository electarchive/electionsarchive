import re
import subprocess
from datetime import date, timedelta
from difflib import get_close_matches
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ri_countbook"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://elections.ri.gov/sites/g/files/xkgbur756/files/publications/Election_Publications/Countbooks/"
BOOKS = {
    date(2002, 11, 5): "2002-Count-Book.pdf",
    date(2004, 11, 2): "2004-Count-Book.pdf",
    date(2006, 11, 7): "2006-Count-Book.pdf",
    date(2010, 11, 2): "2010-Countbook.pdf",
}
COUNTIES = {
    "44001": ["barrington", "bristol", "warren"],
    "44003": ["coventry", "east greenwich", "warwick", "west greenwich", "west warwick"],
    "44005": ["jamestown", "little compton", "middletown", "newport", "portsmouth", "tiverton"],
    "44007": [
        "burrillville",
        "central falls",
        "cranston",
        "cumberland",
        "east providence",
        "foster",
        "glocester",
        "johnston",
        "lincoln",
        "north providence",
        "north smithfield",
        "pawtucket",
        "providence",
        "scituate",
        "smithfield",
        "woonsocket",
    ],
    "44009": [
        "charlestown",
        "exeter",
        "hopkinton",
        "narragansett",
        "new shoreham",
        "north kingstown",
        "richmond",
        "south kingstown",
        "westerly",
    ],
}
HEAD = re.compile(
    r"^\s*(Senate|Representative)\s+District\s+(\d+)\s+(.+?)\s*(?:-\s*[A-Za-z]+\.?|\([A-Za-z]+\))\s*$", re.IGNORECASE
)
ROW = re.compile(r"^\s*([A-Za-z][A-Za-z .']*?)?\s*\b(\d{2,5})\s+([\d,\s]+)$")
TOTAL = re.compile(r"^\s*(?!totals?\b)\S.*?\bTotals?(?: Votes)?\s+([\d,\s]+)$", re.IGNORECASE)
SUBTOTAL = re.compile(r"^\s*totals?(?: votes)?\s+([\d,\s]+)$", re.IGNORECASE)
PUBLISHER = "Rhode Island Board of Elections"
TITLE = "Primary and election count books"


def fetch() -> None:
    for name in BOOKS.values():
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def parse() -> None:
    towns = {town: fips for fips, names in COUNTIES.items() for town in names}
    rows = []
    for day, name in BOOKS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        blocks: list[tuple[str, str, dict[tuple[str | None, str], int] | None]] = []
        block = None

        def close() -> None:
            chamber, candidate, cells, _, marks = block
            if not marks:
                print(day, chamber, candidate, "no total line")
                blocks.append((chamber, candidate, None))
                return
            kept = dict(list(cells.items())[: marks[-1][0]])
            if sum(kept.values()) != marks[-1][1]:
                print(day, chamber, candidate, "precincts sum to", sum(kept.values()), "against", marks[-1][1])
            blocks.append((chamber, candidate, kept if sum(kept.values()) == marks[-1][1] else None))

        for line in text.splitlines():
            if found := HEAD.match(line):
                if block is not None:
                    close()
                office = "senate" if found[1].lower() == "senate" else "house"
                block = (f"{office}:{int(found[2])}:g", found[3].strip().upper(), {}, [None], [])
                continue
            if block is not None and re.search(r"write-in|turnout|election year", line, re.IGNORECASE):
                close()
                block = None
            if block is None:
                continue
            if found := SUBTOTAL.match(line) or TOTAL.match(line):
                total = int(found[1].split()[-1].replace(",", ""))
                since = sum(list(block[2].values())[block[4][-1][0] if block[4] else 0 :])
                if total == since and sum(block[2].values()) != total:
                    continue
                block[4].append((len(block[2]), total))
                if found.re is TOTAL:
                    close()
                    block = None
                continue
            if line.strip() and (town := get_close_matches(line.strip().lower(), towns, 1, 0.85)):
                block[3][0] = town[0]
            elif found := ROW.match(line):
                if found[1] and found[1].strip():
                    block[3][0] = next(iter(get_close_matches(found[1].strip().lower(), towns, 1, 0.85)), None)
                    if block[3][0] is None:
                        print(day, block[:2], "no county for", found[1])
                block[2][(block[3][0], found[2])] = int(found[3].split()[-1].replace(",", ""))
        if block is not None:
            close()
        located = {precinct: town for _, _, cells in blocks for town, precinct in cells or {} if town is not None}
        for chamber, candidate, cells in blocks:
            for town, precinct in [key for key in cells or {} if key[0] is None]:
                if precinct in located:
                    cells[located[precinct], precinct] = cells.pop((None, precinct))
                else:
                    print(day, chamber, candidate, "no town for precinct", precinct)
        broken = {chamber for chamber, _, cells in blocks if cells is None or any(town is None for town, _ in cells)}
        rows.extend(
            (day, chamber, f"{day.year}:RI:{towns[town]}:{town.upper()}:{precinct}", candidate, votes)
            for chamber, candidate, cells in blocks
            if chamber not in broken and cells is not None
            for (town, precinct), votes in cells.items()
        )
        print(day, len({chamber for chamber, _, _ in blocks}), "contests,", len(broken), "dropped:", sorted(broken))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    ).select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        chamber="chamber",
        day="day",
        special=pl.lit(False),
        state=pl.lit("RI"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind=pl.lit("precinct"),
        candidate="candidate",
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        votes="votes",
        source=pl.lit("RI"),
    ).write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Rhode Island's 2002, 2004, 2006, and 2010 State Senate and House generals by precinct from the "
            "Board of Elections' count books",
            polity="US-RI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.ri.gov/publications/election-publications",
            license="Public record",
        )
        for name in BOOKS.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        medsl.attach(
            cs,
            conn,
            frame,
            {"RI": source},
            grain="precinct",
            strict=True,
            authoritative="Source: the Board of Elections' count books.",
        )
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '44%%'"
            ).fetchall()
        }
        for code, day in frame.group_by("code").agg(pl.col("day").first()).iter_rows():
            unit = cs.unit("us.precinct", code)
            if str(unit) not in used or unit not in cs.minted:
                continue
            _, _, fips, town, precinct = code.split(":")
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=cs.polity("US-RI"),
                scheme_code="us.precinct",
                code=code,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
            cs.add(
                "name", entity_id=unit, text=f"{town.title()} {precinct}", lang="en", kind="official", is_preferred=True
            )
        return cs.write()
