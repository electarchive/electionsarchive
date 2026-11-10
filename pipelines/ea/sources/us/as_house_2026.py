import difflib
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.as_precincts import AS_OF_2026, CLOUD, PUBLISHER
from ea.store import connect

SOURCE = "us.as_house_2026"
VERSION = "1"
URL = CLOUD + "2026/11/04083335/2026-midterm-election-results-district-representatives.pdf"
TITLE = "2026 midterm election results by district (House of Representatives, election-night sheet)"
NOTE = "Votes for each candidate as printed on the Election Office's unofficial sheet from the November 3, 2026 election, with its ELECTED marks stored as the outcomes. The Office gave the sheet to KHJ News, and this copy comes from KHJ's Talanei site, because the Office's own site doesn't post results. The Office will release official results after certification. The sheet has no Swains Island seat, which the island's voters fill at a meeting."


def read(path: Path) -> dict[int, list[tuple[str, int, bool]]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True).stdout
    districts: dict[int, list[tuple[str, int, bool]]] = {}
    number = 0
    for line in text.splitlines():
        header = re.match(r"\s*District\s+No\.?\s*(\d+)", line, re.IGNORECASE)
        if header:
            number = int(header[1])
            districts[number] = []
            continue
        row = re.match(r"\s*CANDIDATE\s+\d+\s+(.+?)\s+(\d[\d,]*)\s*(ELECTED)?\s*$", line)
        if row:
            districts[number].append((row[1], int(row[2].replace(",", "")), row[3] is not None))
    return districts


def norm(label: str) -> str:
    return re.sub(r"[^a-z ]", "", label.lower().replace("’", "").replace("'", ""))


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        body = subprocess.run(["curl", "-skL", "-m", "120", URL], capture_output=True, check=True).stdout
        raw.store(SOURCE, URL, body, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    districts = read(artifact.path)
    if sorted(districts) != list(range(1, 18)):
        raise ValueError(f"districts read: {sorted(districts)}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="American Samoa's 2026 House of Representatives election, the Election Office's unofficial count by district with the sheet's ELECTED marks",
            polity="US-AS",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        cs.artifact(artifact, source)
        contests = conn.execute(
            """select (regexp_match(x.value, ':district-(\\d+)-'))[1]::int, c.id, c.election_id from external_id x
               join contest c on c.id = x.entity_id
               where x.scheme = 'us.contest' and x.value like '2026-11-03:AS:60:house:district-%'"""
        ).fetchall()
        if len(contests) != 17:
            raise ValueError(f"{len(contests)} house contests")
        for number, contest, election in sorted(contests):
            options = conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s", [contest]
            ).fetchall()
            rows = districts[number]
            if len(rows) != len(options):
                raise ValueError(f"district {number}: {len(rows)} sheet rows, {len(options)} options")
            scored = sorted(
                (
                    (difflib.SequenceMatcher(None, norm(name), norm(label)).ratio(), r, o)
                    for r, (name, _, _) in enumerate(rows)
                    for o, (_, label) in enumerate(options)
                ),
                reverse=True,
            )
            paired: dict[int, int] = {}
            used = set()
            for score, r, o in scored:
                if r in paired or o in used:
                    continue
                if score < 0.6:
                    raise ValueError(f"district {number}: {rows[r][0]} vs {options[o][1]} scores {score:.2f}")
                paired[r] = o
                used.add(o)
            print(f"d{number}: " + "; ".join(f"{rows[r][0]} -> {options[o][1]}" for r, o in paired.items()))
            result_set = cs.key(f"{SOURCE}.result_set", f"{number}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=source,
                as_of=AS_OF_2026,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            unit = cs.unit("us.precinct", f"2026-11-03:AS:d{number:02d}")
            for r, o in paired.items():
                _, votes, elected = rows[r]
                option = options[o][0]
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
                if elected:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
        return cs.write()
