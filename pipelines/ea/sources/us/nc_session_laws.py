import re
import subprocess
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.nc_session_laws"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Carolina General Assembly"
TITLE = "Session Laws"
LAWS = {
    "2012-05-08:NC:question:wikipedia:north-carolina-amendment-1": ("2011-2012", "2011-409"),
    "2014-11-04:NC:question:wikipedia:north-carolina-criminal-defendant-may-waive-jury-trial-amendment": ("2013-2014", "2013-300"),
    "2016-03-15:NC:question:wikipedia:north-carolina-connect-nc-public-improvement-bond": ("2015-2016", "2015-280"),
    "2018-11-06:NC:question:wikipedia:north-carolina-income-tax-cap-amendment": ("2017-2018", "2018-119"),
    "2018-11-06:NC:question:wikipedia:north-carolina-judicial-selection-for-midterm-vacancies-amendment": ("2017-2018", "2018-132"),
    "2018-11-06:NC:question:wikipedia:north-carolina-legislative-appointments-to-elections-board-amendment": ("2017-2018", "2018-117"),
    "2018-11-06:NC:question:wikipedia:north-carolina-marsy-s-law-crime-victims-rights-amendment": ("2017-2018", "2018-110"),
    "2018-11-06:NC:question:wikipedia:north-carolina-right-to-hunt-and-fish-amendment": ("2017-2018", "2018-96"),
    "2018-11-06:NC:question:wikipedia:north-carolina-voter-id-amendment": ("2017-2018", "2018-128"),
    "2024-11-05:NC:question:wikipedia:citizenship-requirement-for-voting-amendment": ("2023-2024", "2024-18"),
}
QUESTION = re.compile(r"\[ \] FOR\s+\[ \] AGAINST\s+(.+?)\"", re.S)


def url(session: str, law: str) -> str:
    return f"https://www.ncleg.gov/EnactedLegislation/SessionLaws/PDF/{session}/SL{law}.pdf"


def fetch() -> None:
    for session, law in LAWS.values():
        raw.fetch(SOURCE, url(session, law), suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for contest, (session, law) in LAWS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(session, law)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        found = QUESTION.search(text)
        if found is None:
            print(f"{law}: no question")
            continue
        rows.append(
            {
                "contest": contest,
                "session": session,
                "law": law,
                "page": text.count("\f", 0, found.start()) + 1,
                "text": re.sub(r"\s+", " ", found[1]).strip(),
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "wording.parquet")
    print(frame.select("law", "page", pl.col("text").str.slice(0, 80)))


def emit() -> Path:
    wording = pl.read_parquet(PARSED / "wording.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's ballot questions for constitutional amendments and the 2016 bond, as printed in the session laws that placed them on the ballot",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.ncleg.gov/EnactedLegislation/SessionLaws/",
            license="Public record",
        )
        for session, law in LAWS.values():
            cs.artifact(raw.cached(SOURCE, url(session, law)), source)
        placed = 0
        for key, session, law, page, text in wording.iter_rows():
            entities = [
                entity
                for (entity,) in conn.execute(
                    "select entity_id from external_id where scheme = 'us.contest' and value like %s", (key + "%",)
                )
            ]
            if len(entities) != 1:
                print(f"{law}: {len(entities)} contests")
                continue
            contest = cs.survivors.get(entities[0], entities[0])
            address = f"{url(session, law)}#page={page}"
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
