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
    date(2014, 11, 4): "ProposedConstitutionalAmendments20132014",
    date(2023, 10, 14): "ProposedConstitutionalAmendments_Oct_14_2023",
    date(2023, 11, 18): "ProposedConstitutionalAmendments_Nov_18_2023",
    date(2024, 11, 5): "Proposed_Constitutional_Amendment_11_05_2024",
    date(2024, 12, 7): "ProposedConstitutionalAmendments2024DecSummaries",
    date(2025, 3, 29): "ProposedConstitutionalAmendments2025MarSummaries",
}
STAMPS = {
    date(2024, 12, 7): 20250701031819,
    date(2025, 3, 29): 20260627114258,
    date(2023, 10, 14): 20260430032232,
    date(2023, 11, 18): 20250306070402,
    date(2024, 11, 5): 20250301053325,
}
HEADER = re.compile(r"Proposed Amendment No\. (\d+)")
WORDING = re.compile(r"(?:Do you support|Shall)[^?]*\?")
ACT = re.compile(r"Act (\d+)[^\n]{0,60}\n.*?(Do you support[^?]*\?)", re.DOTALL)
BALLOT = re.compile(r"Ballot Language: .(Do you support[^?]*\?).")
NUMBERED = re.compile(r"([1-4])\. (Do you support[^?]*\?)")
NUMBER = re.compile(r"(?i)\b(?:amendment|ca)\s*(?:no\.?\s*)?(\d+)\b")


STOP = {"the", "of", "and", "to", "a", "an", "for", "in", "louisiana", "amendment", "fund"}


def words(text: str) -> set[str]:
    return {word.rstrip("s") for word in re.findall(r"[a-z]+", text.lower()) if word not in STOP and len(word) > 2}


def url(day: date) -> str:
    return f"https://web.archive.org/web/{STAMPS.get(day, 2023)}id_/https://www.sos.la.gov/ElectionsAndVoting/PublishedDocuments/{FILES[day]}.pdf"


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
        if day in (date(2024, 12, 7), date(2025, 3, 29)):
            path = str(raw.cached(SOURCE, url(day)).path)
            columns = [["pdftotext", "-q", path, "-"]]
            if day == date(2025, 3, 29):
                columns = [
                    ["pdftotext", "-q", "-x", str(x), "-y", "0", "-W", "669", "-H", "1770", path, "-"] for x in (0, 669)
                ]
            for command in columns:
                flat = re.sub(r"\s+", " ", subprocess.run(command, capture_output=True, text=True, check=True).stdout)
                for found in NUMBERED.finditer(flat):
                    rows.append({"day": day, "amendment": int(found[1]), "page": 1, "text": found[2]})
            continue
        headers = list(HEADER.finditer(text))
        for index, found in enumerate(headers):
            end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            wording = WORDING.search(text, found.end(), end)
            if wording is None:
                continue
            rows.append(
                {
                    "day": day,
                    "amendment": int(found[1]),
                    "page": text.count("\f", 0, found.start()) + 1,
                    "text": re.sub(r"\s+", " ", wording[0]).strip(),
                }
            )
        if day == date(2014, 11, 4):
            for found in ACT.finditer(text):
                rows.append(
                    {
                        "day": day,
                        "amendment": -int(found[1]),
                        "page": text.count("\f", 0, found.start()) + 1,
                        "text": re.sub(r"\s+", " ", found[2]).strip(),
                    }
                )
        if day == date(2020, 12, 5):
            found = BALLOT.search(text)
            rows.append({"day": day, "amendment": 1, "page": 1, "text": re.sub(r"\s+", " ", found[1]).strip()})
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
        numbers: dict[tuple[date, int], str] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:LA:question:%%'"""
        ):
            if (found := NUMBER.search(name)) is not None:
                contests.setdefault((date.fromisoformat(key[:10]), int(found[1])), []).append(entity)
                numbers[(date.fromisoformat(key[:10]), int(found[1]))] = name
        rows = wording.rows()
        acts = [row for row in rows if row[1] < 0]
        scored = sorted(
            (
                (len(words(act[3]) & words(name)) / len(words(name)), act[1], number)
                for act in acts
                for (when, number), name in numbers.items()
                if when == act[0]
            ),
            reverse=True,
        )
        mapped: dict[int, int] = {}
        for _, act, number in scored:
            if act not in mapped and number not in mapped.values():
                mapped[act] = number
        rows = [
            (day, mapped.get(amendment, amendment), page, text)
            for day, amendment, page, text in rows
            if amendment >= 0 or amendment in mapped
        ]
        placed = 0
        for day, amendment, page, text in rows:
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
