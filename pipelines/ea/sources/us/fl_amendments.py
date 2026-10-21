import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.fl_amendments"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Florida Department of State"
TITLE = "Proposed Constitutional Amendments"
BOOKLETS = {
    date(2016, 8, 30): "https://dos.fl.gov/media/696213/constitutional-amendments-2016-primary-english-booklet.pdf",
    date(2016, 11, 8): "https://dos.fl.gov/media/696216/constitutional-amendments-2016-general-english-booklet.pdf",
    date(2018, 11, 6): "https://dos.fl.gov/media/699824/constitutional-amendments-2018-general-election-english.pdf",
    date(2020, 11, 3): "https://dos.fl.gov/media/704424/constitutional-amendments-2020-general-election-english.pdf",
    date(2022, 11, 8): "https://dos.fl.gov/media/705846/eng-2022-non-booklet-format-constitutional-amend-v20220824.pdf",
    date(2024, 11, 5): "https://dos.fl.gov/media/708428/eng-2024-booklet-constitutional-amend-v20240930-final-for-publication.pdf",
}
HEADER = re.compile(r"(?m)^[ \t\f]*(?:(?:NO|No)\.[ \t]*(\d+)[ \t]*\n[ \t]*(?:CONSTITUTIONAL|Constitutional) (?:AMENDMENT|Amendment|REVISION|Revision)|(?:No)\.[ \t]*(\d+)[ \t]+Constitutional,? Amendment(?:,[^\n]*)?)[ \t]*$")
TITLES = re.compile(r"(?mi)^[ \t]*(?:ballot title:?|title -)[ \t]*")
SUMMARIES = re.compile(r"(?mi)^[ \t]*(?:ballot summary:?|summary)[ \t]*:?[ \t]*$")
ENDS = re.compile(r"(?mi)^[ \t]*(?:full text:?|text|amendment text.*|financial and state budget impact statements?.*)[ \t]*$")


def fetch() -> None:
    for link in BOOKLETS.values():
        raw.fetch(SOURCE, link, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day, link in BOOKLETS.items():
        text = subprocess.run(
            ["pdftotext", str(raw.cached(SOURCE, link).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        headers = [found for found in HEADER.finditer(text) if "...." not in text[found.start() : found.end() + 80]]
        for index, header in enumerate(headers):
            body = text[header.end() : headers[index + 1].start() if index + 1 < len(headers) else len(text)]
            title = TITLES.search(body)
            summary = SUMMARIES.search(body, title.end()) if title is not None else None
            if title is None or summary is None:
                print(f"{day} No. {header[1] or header[2]}: no title or summary")
                continue
            end = ENDS.search(body, summary.end())
            clean = lambda value: re.sub(r"\s+", " ", re.sub(r"(?m)^\s*\d{1,2}\s*$", "", value)).strip()
            rows.append(
                {
                    "day": day,
                    "number": int(header[1] or header[2]),
                    "page": text.count("\f", 0, header.start()) + 1,
                    "title": clean(body[title.end() : summary.start()]).removesuffix(".—").removesuffix("—").strip(),
                    "summary": clean(body[summary.end() : end.start() if end is not None else len(body)]),
                    "url": link,
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "wording.parquet")
    print(frame.select("day", "number", "page", pl.col("title").str.slice(0, 50), pl.col("summary").str.slice(0, 40)))


def emit() -> Path:
    wording = pl.read_parquet(PARSED / "wording.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Florida's ballot titles and summaries of proposed constitutional amendments 2016–2024 from the Division of Elections' booklets",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://dos.fl.gov/elections/forms-publications/publicationsreports/publications-archive/",
            license="Public record",
        )
        for link in BOOKLETS.values():
            cs.artifact(raw.cached(SOURCE, link), source)
        contests: dict[tuple[date, int], list[object]] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:FL:question:%%'"""
        ):
            if (found := re.search(r"(?i)\bamendment (?:no\.? ?)?(\d+)", name)) is not None:
                contests.setdefault((date.fromisoformat(key[:10]), int(found[1])), []).append(entity)
        placed = 0
        for day, number, page, title, summary, link in wording.iter_rows():
            entities = contests.get((day, number), [])
            if len(entities) != 1:
                print(f"{day} Amendment {number}: {len(entities)} contests")
                continue
            contest = cs.survivors.get(entities[0], entities[0])
            address = f"{link}#page={page}"
            for kind, body in (("text", title), ("summary", summary), ("link", None)):
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
