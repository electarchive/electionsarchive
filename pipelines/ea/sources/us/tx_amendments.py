import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.tx_amendments"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Texas Legislative Council"
TITLE = "Analyses of Proposed Constitutional Amendments"
YEARS = {
    2015: date(2015, 11, 3),
    2017: date(2017, 11, 7),
    2019: date(2019, 11, 5),
    2021: date(2021, 11, 2),
    2023: date(2023, 11, 7),
    2025: date(2025, 11, 4),
}
HEADER = re.compile(r"(?m)^[ \t\f]*(?:Proposition|Amendment No\.) (\d+)\b(?![^\n]*(?:\.\.|\. \.))[^\n]*\n")
WORDING = re.compile(r"The constitutional amendment[^\n]*(?:\n(?![ \t]*\n)[^\n]*)*")
NUMBER = re.compile(r"(?i)\bproposition\s+(\d+)\b")


def url(year: int) -> str:
    return f"https://tlc.texas.gov/docs/amendments/analyses{year % 100}.pdf"


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year in YEARS:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(year)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        seen: set[int] = set()
        for found in HEADER.finditer(text):
            proposition = int(found[1])
            wording = WORDING.search(text, found.end())
            if proposition in seen or wording is None or wording.start() - found.end() > 400:
                continue
            seen.add(proposition)
            rows.append(
                {
                    "year": year,
                    "proposition": proposition,
                    "page": text.count("\f", 0, found.start()) + 1,
                    "text": re.sub(r"\s+", " ", wording[0]).strip(),
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "wording.parquet")
    print(frame.group_by("year").len().sort("year"))


def emit() -> Path:
    wording = pl.read_parquet(PARSED / "wording.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's ballot wording of proposed constitutional amendments and links to the Legislative Council's analyses",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://tlc.texas.gov/publications",
            license="Public record",
        )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        contests: dict[tuple[date, int], list[object]] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:TX:question:%%'"""
        ):
            if (found := NUMBER.search(name)) is not None:
                contests.setdefault((date.fromisoformat(key[:10]), int(found[1])), []).append(entity)
        placed = 0
        for year, proposition, page, text in wording.iter_rows():
            entities = contests.get((YEARS[year], proposition), [])
            if len(entities) != 1:
                print(f"{year} Proposition {proposition}: {len(entities)} contests")
                continue
            contest = cs.survivors.get(entities[0], entities[0])
            address = f"{url(year)}#page={page}"
            for kind, body in (("text", text), ("link", None)):
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{kind}\x1fen\x1f{source}\x1f{address}"),
                    contest_id=contest,
                    kind=kind,
                    lang="en",
                    body=body,
                    url=address,
                    source_id=source,
                    locator={"page": page},
                )
            placed += 1
        print(f"{placed} of {wording.height} propositions placed")
        return cs.write()
