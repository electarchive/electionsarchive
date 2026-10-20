import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.la_amendments"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Louisiana Secretary of State"
TITLE = "Proposed Constitutional Amendments"
FILES = {
    date(2012, 11, 6): "ConstitutionalAmendmentsNovember2012",
    date(2015, 10, 24): "ProposedConstitutionalAmendments2015",
    date(2016, 11, 8): "ProposedConstitutionalAmendments2016",
    date(2017, 10, 14): "ProposedConstitutionalAmendments2017",
    date(2018, 11, 6): "ProposedConstitutionalAmendments2018",
    date(2019, 10, 12): "ProposedConstitutionalAmendments2019",
    date(2020, 11, 3): "ProposedConstitutionalAmendments2020",
    date(2020, 12, 5): "ProposedConstitutionalAmendments2020Dec",
    date(2021, 11, 13): "ProposedConstitutionalAmendments2021",
    date(2022, 11, 8): "ProposedConstitutionalAmendments_November_8_2022",
}
HEADER = re.compile(r"Proposed Amendment No\. (\d+)")
WORDING = re.compile(r"(?:Do you support|Shall)[^?]*\?")
NUMBER = re.compile(r"(?i)\bamendment\s*(?:no\.?\s*)?(\d+)\b")


def url(day: date) -> str:
    return (
        f"https://web.archive.org/web/2023id_/https://www.sos.la.gov/ElectionsAndVoting/PublishedDocuments/{FILES[day]}.pdf"
    )


def fetch() -> None:
    for day in FILES:
        raw.fetch(SOURCE, url(day), suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day in FILES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(day)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for found in HEADER.finditer(text):
            wording = WORDING.search(text, found.end())
            if wording is None or wording.start() - found.end() > 900:
                continue
            rows.append(
                {
                    "day": day,
                    "amendment": int(found[1]),
                    "page": text.count("\f", 0, found.start()) + 1,
                    "text": re.sub(r"\s+", " ", wording[0]).strip(),
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "wording.parquet")
    print(frame.group_by("day").len().sort("day"))


def emit() -> Path:
    wording = pl.read_parquet(PARSED / "wording.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's ballot wording of proposed constitutional amendments from the Secretary of State's lists, read from the Internet Archive's copies",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.la.gov/ElectionsAndVoting/",
            license="Public record",
        )
        for day in FILES:
            cs.artifact(raw.cached(SOURCE, url(day)), source)
        contests: dict[tuple[date, int], list[object]] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:LA:question:%%'"""
        ):
            if (found := NUMBER.search(name)) is not None:
                contests.setdefault((date.fromisoformat(key[:10]), int(found[1])), []).append(entity)
        placed = 0
        for day, amendment, page, text in wording.iter_rows():
            entities = contests.get((day, amendment), [])
            if len(entities) != 1:
                print(f"{day} Amendment {amendment}: {len(entities)} contests")
                continue
            contest = cs.survivors.get(entities[0], entities[0])
            address = f"{url(day)}#page={page}"
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
        print(f"{placed} of {wording.height} amendments placed")
        return cs.write()
