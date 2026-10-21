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
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Texas Legislative Council"
TITLE = "Analyses of Proposed Constitutional Amendments"
YEARS = {
    2013: date(2013, 11, 5),
    2015: date(2015, 11, 3),
    2017: date(2017, 11, 7),
    2019: date(2019, 11, 5),
    2021: date(2021, 11, 2),
    2023: date(2023, 11, 7),
    2025: date(2025, 11, 4),
}
HEADER = re.compile(r"(?m)^[ \t\f]*(?:Proposition(?: No\.)?|Amendment No\.) (\d+)\b(?![^\n]*(?:\.\.|\. \.))[^\n]*\n")
WORDING = re.compile(r"The constitutional amendment[^\n]*(?:\n(?![ \t]*\n)[^\n]*)*")
NUMBER = re.compile(r"(?i)\bproposition\s+(\d+)\b")


PRINTED = {
    (2013, 1, 12): "The constitutional amendment authorizing the legislature to provide for an exemption from ad valorem taxation of all or part of the market value of the residence homestead of the surviving spouse of a member of the armed services of the United States who is killed in action.",
    (2013, 2, 18): "The constitutional amendment eliminating an obsolete requirement for a State Medical Education Board and a State Medical Education Fund, neither of which is operational.",
    (2013, 3, 22): "The constitutional amendment to authorize a political subdivision of this state to extend the number of days that aircraft parts that are exempt from ad valorem taxation due to their location in this state for a temporary period may be located in this state for purposes of qualifying for the tax exemption.",
    (2013, 4, 28): "The constitutional amendment authorizing the legislature to provide for an exemption from ad valorem taxation of part of the market value of the residence homestead of a partially disabled veteran or the surviving spouse of a partially disabled veteran if the residence homestead was donated to the disabled veteran by a charitable organization.",
    (2013, 5, 34): "The constitutional amendment to authorize the making of a reverse mortgage loan for the purchase of homestead property and to amend lender disclosures and other requirements in connection with a reverse mortgage loan.",
    (2013, 6, 44): "The constitutional amendment providing for the creation of the State Water Implementation Fund for Texas and the State Water Implementation Revenue Fund for Texas to assist in the financing of priority projects in the state water plan to ensure the availability of adequate water resources.",
    (2013, 7, 56): "The constitutional amendment authorizing a home-rule municipality to provide in its charter the procedure to fill a vacancy on its governing body for which the unexpired term is 12 months or less.",
    (2013, 8, 60): "The constitutional amendment repealing Section 7, Article IX, Texas Constitution, which relates to the creation of a hospital district in Hidalgo County.",
    (2013, 9, 64): "The constitutional amendment relating to expanding the types of sanctions that may be assessed against a judge or justice following a formal proceeding instituted by the State Commission on Judicial Conduct.",
}


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
                    "election": YEARS[year],
                    "proposition": proposition,
                    "page": text.count("\f", 0, found.start()) + 1,
                    "text": re.sub(r"\s+", " ", wording[0]).strip(),
                }
            )
    for (year, proposition, page), wording in PRINTED.items():
        rows.append({"year": year, "election": YEARS[year], "proposition": proposition, "page": page, "text": wording})
    rows.append(
        {
            "year": 2013,
            "election": date(2014, 11, 4),
            "proposition": 1,
            "page": 9,
            "text": "The constitutional amendment providing for the use and dedication of certain money transferred to the state highway fund to assist in the completion of transportation construction, maintenance, and rehabilitation projects, not to include toll roads.",
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
        for year, election, proposition, page, text in wording.iter_rows():
            entities = contests.get((election, proposition), [])
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
