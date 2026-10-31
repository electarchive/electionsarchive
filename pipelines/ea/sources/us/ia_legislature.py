import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ia_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Iowa Secretary of State"
TITLE = "General election canvass summary"
LANDING = "https://sos.iowa.gov/elections/results/"
WAYBACK = "https://web.archive.org/web/{}id_/http://sos.iowa.gov/elections/pdf/{}"
FILES = {
    date(2002, 11, 5): ("20120425022326", "2002/results/Canvass%20Summary.pdf"),
    date(2004, 11, 2): ("20120425022503", "2004/general/CanvassSummary.pdf"),
    date(2008, 11, 4): ("20120425023228", "2008/OfficialCanvass2008General.pdf"),
    date(2012, 11, 6): ("20121224062841", "2012/general/canvsummary.pdf"),
}
OFFICE = re.compile(r"^\s*State (Senator|Representative)\s*(?:District\s+0*(\d+))?\s*$")
DISTRICT = re.compile(r"^\s*District\s+0*(\d+)\s*$")
ROW = re.compile(r"^\s*([A-Za-z][A-Za-z.' ]*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*$")
JUNK = re.compile(r"P\s?a\s?g\s?e|Prepared|Canvass|SECRETARY|ELECTION:|November|\d+-\d+ of \d+", re.IGNORECASE)
PARTY = re.compile(
    r"\b(?:Democratic|Democrat|Republican|Party|Libertarian|Independent|Green|Constitution|Reform|Nominated|by|Petition|"
    r"Grassroots|For|Life|Socialist|Workers|Natural|Law|Iowa|Pirate|Other|No|Votes|Total|Totals)\b",
    re.IGNORECASE,
)


def url(day: date) -> str:
    return WAYBACK.format(*FILES[day])


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


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
                   where u.scheme_code = 'us.county' and u.code like '19%%'"""
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
        office: str | None = None
        chamber: str | None = None
        header: list[str] = []
        places: list[tuple[str, list[tuple[int, int]]]] = []
        pending: str | None = None
        for line in text.replace("\f", "\n").splitlines():
            if found := OFFICE.match(line):
                office, chamber, header, places, pending = found[1], None, [], [], None
                if found[2]:
                    chamber = f"{'senate' if office == 'Senator' else 'house'}:{int(found[2])}:g"
                continue
            if office is None:
                continue
            if chamber is None and (found := DISTRICT.match(line)):
                chamber = f"{'senate' if office == 'Senator' else 'house'}:{int(found[1])}:g"
                continue
            match = ROW.match(line)
            if match is None:
                if line.strip() and not places and pending is None and not JUNK.search(line):
                    header.append(line)
                continue
            label = re.sub(r"\s+", " ", match[1]).strip()
            cells = [
                (m.end(), int(m[0].replace(",", "")))
                for m in re.finditer(r"\d[\d,]*", line)
                if m.start() >= match.start(2)
            ]
            words = label.split(" ")
            county = next(
                (counties[key(" ".join(words[:n]))] for n in (3, 2, 1) if key(" ".join(words[:n])) in counties), None
            )
            if county is not None and label.lower().endswith("election day"):
                pending = county
                continue
            if county is not None:
                places.append((county, cells))
                continue
            if label.lower() == "total election day":
                pending = "total"
                continue
            if label.lower() not in ("total", "totals"):
                continue
            if pending not in (None, "total"):
                places.append((pending, cells))
                pending = None
                continue
            ends = [end for end, _ in cells]
            labels: dict[int, list[str]] = {end: [] for end in ends}
            for heading in header:
                kept = re.sub(
                    r"(?i)\bwrite-?\s?ins?\b|\bscattering\b",
                    lambda m: "WRITE-INS".ljust(len(m[0]))[: len(m[0])],
                    heading,
                )
                kept = re.sub(r"(?i)\b(?:under|over)\s+votes\b", lambda m: "BLANKS".ljust(len(m[0])), kept)
                phrases = list(re.finditer(r"\S+(?: \S+)*", PARTY.sub(lambda m: " " * len(m[0]), kept)))
                if len(phrases) == len(ends):
                    for end, phrase in zip(ends, phrases, strict=True):
                        labels[end].append(phrase[0])
                    continue
                for phrase in phrases:
                    center = (phrase.start() + phrase.end()) / 2
                    column = min(ends, key=lambda end: abs(end - 1 - center))
                    if abs(column - 1 - center) < 25:
                        labels[column].append(phrase[0])
            names = [
                "WRITE-INS"
                if any(t.startswith("WRITE") for t in labels[end])
                else ""
                if any(t.startswith("BLANKS") for t in labels[end])
                else " ".join(labels[end])
                for end in ends
            ]
            sums = dict.fromkeys(ends, 0)
            placed: list[tuple[str, int, int]] = []
            for code, values in places:
                for end, value in values:
                    column = min(ends, key=lambda column: abs(column - end))
                    sums[column] += value
                    placed.append((code, column, value))
            named = [name for name in names[:-1] if name]
            problem = (
                "no district"
                if chamber is None
                else "no candidate"
                if not named or names[0] == "WRITE-INS"
                else f"counties sum to {list(sums.values())}, printed {[value for _, value in cells]}"
                if list(sums.values()) != [value for _, value in cells]
                or len(placed) != len({(c, e) for c, e, _ in placed})
                else None
            )
            if problem:
                print(f"{day} {chamber}: {problem}, skipped")
            else:
                rows.extend(
                    (day, chamber, code, names[ends.index(column)], names[ends.index(column)] == "WRITE-INS", value)
                    for code, column, value in placed
                    if names[ends.index(column)] and column != ends[-1]
                )
            office, chamber, header, places, pending = None, None, [], [], None
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
        state=pl.lit("IA"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("IA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's legislative generals by county, 2002, 2004, 2008, and 2012 (Secretary of State canvass summaries, via the Wayback Machine)",
            polity="US-IA",
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
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"IA": source}, strict=True)
        return cs.write()
