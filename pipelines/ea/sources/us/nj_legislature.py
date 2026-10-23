import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.nj_federal import COUNTIES
from ea.store import DATA, connect

SOURCE = "us.nj_legislature"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.nj.gov/state/elections/assets/pdf/election-results/"
LANDING = "https://www.nj.gov/state/elections/election-information-results.shtml"
PUBLISHER = "New Jersey Division of Elections"
TITLE = "Official list: candidate returns for State Senate and General Assembly"
FILES = {
    (date(2001, 11, 6), "senate"): "2001/2001-general-elect-state-senate-tallies.pdf",
    (date(2001, 11, 6), "general-assembly"): "2001/2001-general-elect-gen-assembly-tallies.pdf",
    (date(2003, 11, 4), "senate"): "2003/2003gen-elect-st_senate_candidate_tally.pdf",
    (date(2003, 11, 4), "general-assembly"): "2003/2003gen-elect_assembly_candidate_tally.pdf",
    (date(2005, 11, 8), "general-assembly"): "2005/2005_Official_General_Assembly_tallies.pdf",
    (date(2007, 11, 6), "senate"): "2007/2007-official-general-election-tallies(ss)-12.3.07.pdf",
    (date(2007, 11, 6), "general-assembly"): "2007/2007-official-general-election-tallies(ga)-12.12.07.pdf",
    (date(2009, 11, 3), "general-assembly"): "2009/2009-official-gen-elect-gen-assembly-tallies-120109.pdf",
    (date(2011, 11, 8), "senate"): "2011/2011-official-gen-elect-state-senate-results-050817.pdf",
    (date(2011, 11, 8), "general-assembly"): "2011/2011-official-gen-elect-gen-assembly-results-050817.pdf",
    (date(2013, 11, 5), "senate"): "2013/2013-official-general-election-results-state-senate.pdf",
    (date(2013, 11, 5), "general-assembly"): "2013/2013-official-general-election-results-general-assembly.pdf",
    (date(2015, 11, 3), "general-assembly"): "2015/2015-official-ge-results-nj-general-assembly.pdf",
}
UNITS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth"]
ORDINALS = {
    word.lower(): number
    for number, word in enumerate(
        [
            *UNITS,
            *[
                "Tenth",
                "Eleventh",
                "Twelfth",
                "Thirteenth",
                "Fourteenth",
                "Fifteenth",
                "Sixteenth",
                "Seventeenth",
                "Eighteenth",
                "Nineteenth",
            ],
            "Twentieth",
            *(f"Twenty-{unit}" for unit in UNITS),
            "Thirtieth",
            *(f"Thirty-{unit}" for unit in UNITS),
            "Fortieth",
        ],
        start=1,
    )
}
DISTRICT = re.compile(r"^\s*([\w-]+) Legislative District", re.IGNORECASE)
LINE = re.compile(r"(?:^|\s)(" + "|".join(COUNTIES) + r")(?:\s*\(part\))?\s.*?([\d,]+)\s*$", re.IGNORECASE)
HEADING = re.compile(
    r"^(?:Name|Official|Candidate|For |Party|General Assembly|State Senate|\(bracketed|\d)", re.IGNORECASE
)


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def parse() -> None:
    rows = []
    for (day, chamber), path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        district: int | None = None
        column = 0
        name: str | None = None
        lines: list[tuple[str, int]] = []
        candidates: dict[int, list[tuple[str, str, int]]] = {}
        broken: set[int] = set()
        for line in text.splitlines():
            if found := re.search(r"\bCounty\s+Slogan\b", line):
                column = found.start()
            if found := DISTRICT.match(line):
                district, name, lines = ORDINALS.get(found[1].lower()), None, []
            elif district is None:
                continue
            elif (found := re.search(r"\sTotal\s+([\d,]+)\s*$", line)) and (name is not None or lines):
                if name is None or sum(votes for _, votes in lines) != int(found[1].replace(",", "")):
                    print(
                        f"{day} {chamber} {district}: {name} counties sum to {sum(v for _, v in lines)}, total {found[1]}"
                    )
                    broken.add(district)
                else:
                    candidates.setdefault(district, []).extend((code, name, votes) for code, votes in lines)
                name, lines = None, []
            else:
                head = re.split(r"\s+\*|\s+\(w\)|\s{2,}", line.strip(), maxsplit=1)[0]
                head = re.sub(r"\s+(?:\d+|P\.?\s?O\.?)\s.*$", "", re.sub(r"\s\([A-Z. ]+\)(?=\s)", "", head))
                if (
                    not lines
                    and line[:1].isalpha()
                    and not HEADING.match(line)
                    and not re.search(r"[()]|\bNJ\b,?\s*\d{5}", head)
                ):
                    name = head
                if name is not None and (found := LINE.search(line[max(0, column - 12) :])):
                    lines.append((COUNTIES[found[1].upper()], int(found[2].replace(",", ""))))
        for district, entries in candidates.items():
            if district in broken:
                print(f"{day} {chamber} {district}: skipped")
                continue
            rows.extend(
                (day, f"{chamber}:{district}:g", code, name.upper().replace("DEL PILAR RIVAS", "DELPILARRIVAS"), votes)
                for code, name, votes in entries
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    ).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("NJ"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("NJ"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey's State Senate and General Assembly generals by county, 2001-2015 (Division of Elections official lists)",
            polity="US-NJ",
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
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"NJ": source}, strict=True)
        return cs.write()
