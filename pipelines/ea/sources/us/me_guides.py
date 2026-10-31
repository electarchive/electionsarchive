import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.me_guides"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Maine Secretary of State"
TITLE = "Maine Citizen's Guide to the Referendum Election"
BASE = "https://lldc.mainelegislature.org/Open/Elections/Returns/"
GUIDES = {
    date(2012, 11, 6): "ac",
    date(2013, 11, 5): "a",
    date(2014, 11, 4): "ac",
    date(2015, 11, 3): "e",
    date(2016, 11, 8): "aa",
    date(2017, 6, 13): "c",
    date(2017, 11, 7): "c",
    date(2019, 11, 5): "a",
    date(2020, 3, 3): "c",
    date(2021, 11, 2): "e",
    date(2023, 11, 7): "a",
    date(2024, 11, 5): "v",
    date(2025, 11, 4): "g",
}
QUESTION = re.compile(
    r"Question\s+(\d+):\s+[A-Za-z'’ -]{3,45}?\s+(?=(?:Do you|Shall|Are you|Should|Would you|Will you)\b)"
)
WORDING = re.compile(r"[^?]{15,2500}\?")
NUMBER = re.compile(r"Question (\d+)")


def url(day: date) -> str:
    return f"{BASE}{day.year}/{day:%Y%m%d}/elec_{day:%Y%m%d}{GUIDES[day]}.pdf"


def fetch() -> None:
    for day in GUIDES:
        raw.fetch(SOURCE, url(day), suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day in GUIDES:
        pages = subprocess.run(
            ["pdftotext", str(raw.cached(SOURCE, url(day)).path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("\f")
        seen: set[int] = set()
        for number, page in enumerate(pages, 1):
            text = re.sub(r"\s+", " ", page.replace("“", '"').replace("”", '"').replace("’", "'"))
            for found in QUESTION.finditer(text):
                question = int(found[1])
                wording = WORDING.match(text, found.end())
                if question in seen or wording is None:
                    continue
                seen.add(question)
                rows.append({"day": day, "question": question, "page": number, "text": wording[0].strip()})
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
            summary="Maine's referendum questions as worded on the ballot, from the Secretary of State's Citizen's Guides in the Maine State Law and Legislative Reference Library's digital library",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.maine.gov/sos/cec/elec/",
            license="Public record",
        )
        for day in GUIDES:
            cs.artifact(raw.cached(SOURCE, url(day)), source)
        contests: dict[tuple[date, int], list[object]] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:ME:question:%%'"""
        ):
            if found := NUMBER.findall(name):
                contests.setdefault((date.fromisoformat(key[:10]), int(found[-1])), []).append(entity)
        placed = 0
        for day, question, page, text in wording.iter_rows():
            entities = contests.get((day, question), [])
            if len(entities) != 1:
                print(f"{day} Question {question}: {len(entities)} contests")
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
        print(f"{placed} of {wording.height} questions placed")
        return cs.write()
