import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.ma_ifv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Massachusetts Secretary of the Commonwealth"
TITLE = "Information for Voters"
BASE = "https://www.sec.state.ma.us/divisions/elections/download/"
BOOKS = {
    2002: "information-for-voters/IFV_2002-English.pdf",
    2006: "information-for-voters/IFV_2006-English.pdf",
    2008: "information-for-voters/IFV_2008-English.pdf",
    2010: "information-for-voters/IFV_2010-English.pdf",
    2012: "information-for-voters/IFV_2012-English.pdf",
    2014: "information-for-voters/IFV_2014-English.pdf",
    2016: "information-for-voters/IFV_2016-English.pdf",
    2018: "information-for-voters/IFV_2018-English.pdf",
    2020: "information-for-voters/IFV_2020-English.pdf",
    2022: "research-and-statistics/IFV_2022.pdf",
    2024: "research-and-statistics/IFV_2024.pdf",
}
QUESTION = re.compile(r"QUESTION\s+(\d+)\b")
WORDING = re.compile(r"Do you approve[^?]{20,1500}\?")
SUMMARY_BLOCK = re.compile(r"SUMMARY\s+(.{40,4000}?)\s+WHAT YOUR VOTE WILL DO")
SUMMARY_END = re.compile(r"WHAT YOUR VOTE WILL DO|A YES VOTE|STATEMENT OF FISCAL|SUMMARY\s+As required")
NUMBER = re.compile(r"Question (\d+)")


def fetch() -> None:
    for name in BOOKS.values():
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year, name in BOOKS.items():
        pages = subprocess.run(
            ["pdftotext", str(raw.cached(SOURCE, BASE + name).path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("\f")
        seen: set[int] = set()
        for number, page in enumerate(pages, 1):
            text = re.sub(r"\s+", " ", page.replace("“", '"').replace("”", '"').replace("’", "'")).strip()
            for found in WORDING.finditer(text):
                header = list(QUESTION.finditer(text, 0, found.start()))
                if not header or found.start() - header[-1].end() > 400:
                    continue
                question = int(header[-1][1])
                if question in seen:
                    continue
                seen.add(question)
                summary = ""
                for block in SUMMARY_BLOCK.finditer(text):
                    if not block[1].startswith("As required"):
                        summary = block[1].strip()
                        break
                if not summary:
                    after = text[found.end() :]
                    end = SUMMARY_END.search(after)
                    summary = after[: end.start()].strip() if end is not None else ""
                summary = re.sub(r"^SUMMARY\s+", "", summary)
                summary = summary if len(summary) > 60 else ""
                rows.append(
                    {"year": year, "question": question, "page": number, "text": found[0], "summary": summary[:4000]}
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
            summary="Massachusetts's ballot questions and the Attorney General's summaries from the Secretary of the Commonwealth's Information for Voters booklets",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sec.state.ma.us/divisions/elections/research-and-statistics/information-for-voters.htm",
            license="Public record",
        )
        for name in BOOKS.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        contests: dict[tuple[int, int], list[object]] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:MA:question:%%'"""
        ):
            if (found := NUMBER.search(name)) is not None:
                contests.setdefault((date.fromisoformat(key[:10]).year, int(found[1])), []).append(entity)
        placed = 0
        for year, question, page, text, summary in wording.iter_rows():
            entities = contests.get((year, question), [])
            if len(entities) != 1:
                print(f"{year} Question {question}: {len(entities)} contests")
                continue
            contest = cs.survivors.get(entities[0], entities[0])
            address = f"{BASE}{BOOKS[year]}#page={page}"
            for kind, body in (("text", text), ("summary", summary or None), ("link", None)):
                if kind == "summary" and body is None:
                    continue
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
