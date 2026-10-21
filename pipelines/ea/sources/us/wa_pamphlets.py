import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.wa_pamphlets"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Washington Secretary of State"
TITLE = "Voters' Pamphlet"
HOST = "https://www.sos.wa.gov"
PAMPHLETS = {
    date(2016, 11, 8): "https://web.archive.org/web/2017/https://www.sos.wa.gov/_assets/elections/voters%20pamphlet%202016.pdf",
    date(2019, 11, 5): f"{HOST}/sites/default/files/2022-05/-ED02-%2520Chelan%2520FINAL%2520low%2520res.pdf",
    date(2020, 11, 3): f"{HOST}/sites/default/files/2022-05/--ed02chelan%252cdouglas%252cgrant%252ckittitas.pdf",
    date(2021, 11, 2): f"{HOST}/sites/default/files/2022-05/--ed02-pg01-low-res-8.31.pdf",
}
ENDS = re.compile(
    r"(This tax (?:increase|decrease)[^\n]*should be:|Should (?:this|Initiative|Referendum)[^\n]*?(?:be enacted into law\?|be:))"
)
START = re.compile(r"(Initiative Measure No\. \d+ concerns|The legislature [a-z]+[ ,]|concerning )")
KINDS = {
    "advisory": re.compile(r"(?i)Advisory Vote(?: No\.)? (\d+)"),
    "initiative": re.compile(r"(?i)Initiative (?:Measure )?(?:No\. )?(\d+)"),
    "referendum": re.compile(r"(?i)Referendum (?:Measure |Bill )?(?:No\. )?(\d+)"),
    "sjr": re.compile(r"(?i)Senate Joint Resolution(?: No\.)? (\d+)"),
}


def fetch() -> None:
    for link in PAMPHLETS.values():
        raw.fetch(SOURCE, link, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day, link in PAMPHLETS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, link).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        advisory = 0
        for number, page in enumerate(text.split("\f"), 1):
            lines = page.split("\n")
            for index, line in enumerate(lines):
                if (end := ENDS.search(line)) is None:
                    continue
                column = max(0, end.start() - 2)
                buffer: list[str] = []
                cursor = index
                while cursor > 0:
                    cursor -= 1
                    if all(lines[max(cursor - gap, 0)][column:].strip() == "" for gap in range(6)) and cursor < index - 5:
                        break
                    buffer.append(lines[cursor][column:].split("   ")[0].strip())
                joined = re.sub(r"\s+", " ", " ".join(reversed(buffer))).strip()
                found = START.search(joined)
                if found is None and not end[0].startswith("Should"):
                    continue
                statement = f"{joined[found.start() if found is not None else 0 :]} {end[0]}"
                if end[0].startswith("This tax"):
                    kind = "advisory"
                    prefix = re.findall(r"(\d+) Advisory Vote No\.", joined[: found.start()])
                    advisory = int(prefix[-1]) if prefix else advisory + 1 if advisory else 0
                    measure = advisory
                elif "Initiative Measure No." in statement[:40]:
                    kind = "initiative"
                    measure = int(re.search(r"No\. (\d+)", statement)[1])
                else:
                    kind = "sjr" if "constitutional amendment" in end[0] else "referendum"
                    if (head := KINDS[kind].search(page)) is None:
                        continue
                    measure = int(head[1])
                if kind == "sjr" and statement.startswith("concerning "):
                    statement = f"The legislature has proposed a constitutional amendment {statement}"
                rows.append({"day": day, "kind": kind, "number": measure, "page": number, "text": statement, "url": link})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "wording.parquet")
    print(frame.select("day", "kind", "number", "page").sort("day", "kind", "number"))


def emit() -> Path:
    wording = pl.read_parquet(PARSED / "wording.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's ballot titles of advisory votes, referenda, and constitutional amendments from the Secretary of State's voters' pamphlets",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.wa.gov/elections/data-research/election-results-and-voters-pamphlets",
            license="Public record",
        )
        for link in PAMPHLETS.values():
            cs.artifact(raw.cached(SOURCE, link), source)
        contests: dict[tuple[date, str, int], list[object]] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:WA:question:%%'"""
        ):
            for kind, pattern in KINDS.items():
                if (found := pattern.search(name)) is not None:
                    contests.setdefault((date.fromisoformat(key[:10]), kind, int(found[1])), []).append(entity)
                    break
        placed = 0
        rows = list(wording.iter_rows())
        for day in {row[0] for row in rows}:
            numbers = sorted(number for when, kind, number in contests if when == day and kind == "advisory")
            advisory = [index for index, row in enumerate(rows) if row[0] == day and row[1] == "advisory"]
            if len(numbers) == len(advisory):
                for index, number in zip(advisory, numbers):
                    rows[index] = (*rows[index][:2], number, *rows[index][3:])
        for day, kind, number, page, statement, link in rows:
            entities = contests.get((day, kind, number), [])
            if len(entities) != 1:
                print(f"{day} {kind} {number}: {len(entities)} contests")
                continue
            contest = cs.survivors.get(entities[0], entities[0])
            address = f"{link}#page={page}"
            for body_kind, body in (("text", statement), ("link", None)):
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{body_kind}\x1fen\x1f{source}\x1f{address}"),
                    contest_id=contest,
                    kind=body_kind,
                    lang="en",
                    body=body,
                    url=address,
                    source_id=source,
                    locator={"page": page},
                )
            placed += 1
        print(f"{placed} of {wording.height} measures placed")
        return cs.write()
