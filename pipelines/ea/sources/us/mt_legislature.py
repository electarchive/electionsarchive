import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mt_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Montana Secretary of State"
TITLE = "Legislative general election results by county"
LANDING = "https://sosmt.gov/elections/results/"
WAYBACK = "https://web.archive.org/web/{}id_/http://sos.mt.gov/Elections/{}"
FILES = {
    date(2000, 11, 7): ("20130328145609", "Archives/2000s/2000/2000Gen/2000-GenLeg.pdf"),
    date(2002, 11, 5): ("20090402034340", "archives/2000s/2002/2002-GenLeg.pdf"),
    date(2004, 11, 2): ("20090402033852", "archives/2000s/2004/2004-GenLeg.pdf"),
    date(2006, 11, 7): (
        "20090402034119",
        "archives/2000s/2006/elections/general/2006_Legislative_General_Final_Results.pdf",
    ),
    date(2008, 11, 4): ("20090402034316", "archives/2000s/2008/results/2008_Legislative_General.pdf"),
    date(2010, 11, 2): ("20101219162350", "Archives/2010s/2010/2010_General_Election_Offiical_Legislative_Canvass.pdf"),
}
MARK = re.compile(r"^\s*(HD|SD|House District|Senate District)\s*0*(\d+)")
ROW = re.compile(r"^\s*([A-Za-z][A-Za-z.&' ]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")
JUNK = re.compile(r"Page \d|RESULTS|CANVASS|SECRETARY|COUNTY|%", re.IGNORECASE)
PARTY = re.compile(
    r"\((?:[A-Z]{1,3})\)|\*|\b(?:REPUBLICAN|DEMOCRAT|DEMOCRATIC|INDEPENDENT|LIBERTARIAN|CONSTITUTION|GREEN|REFORM|NATURAL LAW)\b",
    re.IGNORECASE,
)


def url(day: date) -> str:
    return WAYBACK.format(*FILES[day])


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("&", "and"))


def fetch() -> None:
    for day in FILES:
        raw.fetch(SOURCE, url(day), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '30%%'"""
            ).fetchall()
        }
    rows = []
    for day in FILES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(day)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        chamber: str | None = None
        header: list[str] = []
        places: list[tuple[str, list[tuple[int, int]]]] = []
        for line in text.replace("\f", "\n").splitlines():
            if found := MARK.match(line):
                chamber = f"{'senate' if found[1][0] == 'S' else 'house'}:{int(found[2])}:g"
                line = " " * found.end() + line[found.end() :]
            match = ROW.match(line)
            if match is None or key(match[1]) not in counties and not key(match[1]).endswith(("total", "totals")):
                if line.strip() and not places and not JUNK.search(line):
                    header.append(line)
                continue
            cells = [
                (m.end(), int(m[0].replace(",", "")))
                for m in re.finditer(r"\d[\d,]*", line)
                if m.start() >= match.start(2)
            ]
            if key(match[1]) in counties:
                places.append((counties[key(match[1])], cells))
                continue
            ends = [end for end, _ in cells]
            labels: dict[int, list[str]] = {end: [] for end in ends}
            for heading in header:
                phrases = list(re.finditer(r"\S+(?: \S+)*", PARTY.sub(lambda m: " " * len(m[0]), heading)))
                if len(phrases) == len(ends):
                    for end, phrase in zip(ends, phrases, strict=True):
                        labels[end].append(phrase[0])
                    continue
                for phrase in phrases:
                    center = (phrase.start() + phrase.end()) / 2
                    column = min(ends, key=lambda end: abs(end - 1 - center))
                    if abs(column - 1 - center) < 25:
                        labels[column].append(phrase[0])
            names = [" ".join(labels[end]) for end in ends]
            sums = dict.fromkeys(ends, 0)
            placed: list[tuple[str, int, int]] = []
            for code, values in places:
                for end, value in values:
                    column = min(ends, key=lambda column: abs(column - end))
                    sums[column] += value
                    placed.append((code, column, value))
            problem = (
                "no district"
                if chamber is None
                else "a column without a name"
                if not all(names)
                else f"counties sum to {list(sums.values())}, printed {[value for _, value in cells]}"
                if list(sums.values()) != [value for _, value in cells]
                or len(placed) != len({(c, e) for c, e, _ in placed})
                else None
            )
            if problem:
                print(f"{day} {chamber}: {problem}, skipped")
            else:
                rows.extend((day, chamber, code, names[ends.index(column)], value) for code, column, value in placed)
            chamber, header, places = None, [], []
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={"day": pl.Date, "chamber": pl.String, "code": pl.String, "candidate": pl.String, "votes": pl.Int64},
        orient="row",
    ).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("MT"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("MT"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Montana's legislative generals by county, 2000-2010 (Secretary of State results, via the Wayback Machine)",
            polity="US-MT",
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
        for day in FILES:
            cs.artifact(raw.cached(SOURCE, url(day)), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MT": source}, strict=True)
        return cs.write()
