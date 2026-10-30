import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.oh_county_ballots_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
INBOX = DATA / "inbox" / "manual" / "MF-370"
HAMILTON = "https://www.hamiltoncountyohio.gov/government/departments/board_of_elections"
HAMILTON_FILE = INBOX / "hamilton" / "November-2026-Ballot-Issues.pdf"
PUBLISHER = "Hamilton County Board of Elections"
TITLE = "November 3, 2026 General Election ballot issues"


def fetch() -> None:
    try:
        raw.cached(SOURCE, HAMILTON)
    except LookupError:
        raw.store(
            SOURCE,
            HAMILTON,
            HAMILTON_FILE.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(HAMILTON_FILE.stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, HAMILTON).path), "-"], capture_output=True, text=True, check=True
    ).stdout.replace("\f", "\n")
    blocks = re.split(r"^\s*\d+ (?=[A-Z][A-Z ()&,'-]+\n)", text, flags=re.M)[1:]
    rows = []
    for block in blocks:
        head, _, rest = block.partition("\n")
        entity, _, rest = rest.strip().partition("\n")
        body = re.split(r"\n\s*(?:FOR|AGAINST|YES|NO)\b", rest)[0]
        body = re.sub(r"A majority (?:affirmative|yes) vote is necessary for passage\.", "", body)
        body = re.sub(r"\s+", " ", body).strip()
        if body and re.match(r"(City|Village|Township|Hamilton County|.*School District|.*Library)", entity.strip()):
            rows.append({"county": "Hamilton", "kind": head.strip(), "entity": entity.strip(), "text": body})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "issues.parquet")
    print(frame.height)


def keys(name: str) -> set[str]:
    name = re.sub(r"^(City|Village|Township) of (.+)$", lambda m: f"{m[2]} {m[1]}" if m[1] == "Township" else m[2], name)
    return {ceda._core(name), ceda._city(name)}


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "issues.parquet")
    with connect() as conn:
        contests: dict[str, list[tuple[UUID, str, str]]] = {}
        for contest, name, note in conn.execute(
            """select c.id, n.text, coalesce(t.body_md, '') from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value like %s
               join election e on e.id = c.election_id
               join name n on n.entity_id = e.polity_id and n.is_preferred and n.lang = 'en'
               left join note t on t.entity_id = c.id and t.kind = 'context'""",
            [f"{DAY}:OH:%:question:%"],
        ).fetchall():
            for key in keys(name):
                contests.setdefault(key, []).append((contest, name, note))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Official ballot language for Hamilton County, Ohio local issues on the November 3, 2026 ballot",
            polity="US-OH",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=HAMILTON)
        cs.artifact(raw.cached(SOURCE, HAMILTON), source)
        kept = 0
        used: set[UUID] = set()
        for row in frame.iter_rows(named=True):
            options = [c for k in keys(row["entity"]) for c in contests.get(k, []) if c[0] not in used]
            wanted = "income tax" if "INCOME" in row["kind"] else "bond" if "BOND" in row["kind"] else "tax levy"
            narrowed = [c for c in options if c[2].lower().startswith(wanted)]
            if "CHARTER" in row["kind"]:
                narrowed = [c for c in options if c[2].lower().startswith("miscellaneous")]
            for word in ("additional", "renewal", "replacement", "decrease", "increase"):
                if len(narrowed) > 1 and word in row["kind"].lower():
                    narrowed = [c for c in narrowed if word in c[2].lower()] or narrowed
            if len({c[0] for c in narrowed}) != 1:
                print("no match:", row["entity"], row["kind"], len(narrowed))
                continue
            contest = narrowed[0][0]
            used.add(contest)
            kept += 1
            cs.add("citation", entity_id=contest, source_id=source)
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["text"],
                url=HAMILTON,
                source_id=source,
            )
        print(f"{kept} of {frame.height} issues")
        return cs.write()
