import re
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.wa_initiatives"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Washington Secretary of State"
TITLE = "Initiatives and Referenda Filed"
YEARS = range(2008, 2026)
KINDS = ("l", "p")
ENTRY = re.compile(r"\d{1,2}/\d{1,2}/\d{4} (?:IL|IP)-(\d+) ")
BALLOT = re.compile(
    r"(Initiative (?:Measure )?No\. \d+ concerns .+?)\s+Ballot Measure Summary\s+(.+?)\s+View Complete Text"
)


def url(year: int, kind: str) -> str:
    return f"https://voter.votewa.gov/Initiatives.aspx?y={year}&t={kind}"


def fetch() -> None:
    for year in YEARS:
        for kind in KINDS:
            raw.fetch(SOURCE, url(year, kind), suffix=".html")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year in YEARS:
        for kind in KINDS:
            html = raw.cached(SOURCE, url(year, kind)).path.read_text(errors="replace")
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&#160;", " ").replace("&nbsp;", " ")
            starts = list(ENTRY.finditer(text))
            for index, found in enumerate(starts):
                end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
                body = BALLOT.search(text[found.end() : end])
                if body is None:
                    continue
                rows.append(
                    {
                        "year": year,
                        "kind": kind,
                        "number": int(re.search(r"No\. (\d+)", body[1])[1]),
                        "title": body[1].strip(),
                        "summary": body[2].strip(),
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
            summary="Washington's initiatives and their ballot titles and summaries from the Secretary of State's initiatives and referenda filings",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.wa.gov/elections/initiatives-referenda",
            license="Public record",
        )
        for year in YEARS:
            for kind in KINDS:
                cs.artifact(raw.cached(SOURCE, url(year, kind)), source)
        contests: dict[int, list[tuple[date, object]]] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:WA:question:%%'"""
        ):
            if "Advisory" not in name and (found := re.search(r"Initiative (?:Measure )?(?:No\. )?(\d+)", name)) is not None:
                contests.setdefault(int(found[1]), []).append((date.fromisoformat(key[:10]), entity))
        placed = 0
        for year, kind, number, title, summary in wording.iter_rows():
            entities = contests.get(number, [])
            if len(entities) != 1 or abs(entities[0][0].year - year) > 3:
                continue
            contest = cs.survivors.get(entities[0][1], entities[0][1])
            address = url(year, kind)
            for body_kind, body in (("text", title), ("summary", summary), ("link", None)):
                cs.add(
                    "ballot_question_text",
                    id=uuid5(
                        NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{body_kind}\x1fen\x1f{source}\x1f{address}"
                    ),
                    contest_id=contest,
                    kind=body_kind,
                    lang="en",
                    body=body,
                    url=address,
                    source_id=source,
                )
            placed += 1
            print(f"{entities[0][0]} Initiative {number} <- {title[:70]}")
        print(f"{placed} initiatives placed")
        return cs.write()
