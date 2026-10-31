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
SUMMIT = "https://www.summitcountyohio.gov/boe/election-60-day-issue-synopsis"
SUMMIT_FILE = INBOX / "summit" / "Election_60IssueSynopsis.pdf"
CUYAHOGA = "https://boe.cuyahogacounty.gov/"
CUYAHOGA_FILE = INBOX / "cuyahoga" / "PDF Sample Ballot Package.pdf"
CUYAHOGA_BOXES = ((5, 20, 195, 375), (5, 215, 175, 355), (5, 392, 215, 325), (6, 20, 195, 545), (6, 215, 190, 345))
SOURCES = {
    HAMILTON: (
        "Hamilton",
        HAMILTON_FILE,
        "Hamilton County Board of Elections",
        "November 3, 2026 General Election ballot issues",
    ),
    SUMMIT: (
        "Summit",
        SUMMIT_FILE,
        "Summit County Board of Elections",
        "Issue synopsis, November 3, 2026 General Election",
    ),
    CUYAHOGA: (
        "Cuyahoga",
        CUYAHOGA_FILE,
        "Cuyahoga County Board of Elections",
        "Sample ballot (Bay Village 01-A), November 3, 2026 General Election",
    ),
}


def fetch() -> None:
    for url, (_, path, _, _) in SOURCES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def pdf(url: str, mode: str) -> str:
    return subprocess.run(
        ["pdftotext", mode, str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout.replace("\f", "\n")


def hamilton() -> list[dict[str, str]]:
    rows = []
    for block in re.split(r"^\s*\d+ (?=[A-Z][A-Z ()&,'-]+\n)", pdf(HAMILTON, "-layout"), flags=re.MULTILINE)[1:]:
        head, _, rest = block.partition("\n")
        entity, _, rest = rest.strip().partition("\n")
        body = re.split(r"\n\s*(?:FOR|AGAINST|YES|NO)\b", rest)[0]
        body = re.sub(r"A majority (?:affirmative|yes) vote is necessary for passage\.", "", body)
        body = re.sub(r"\s+", " ", body).strip()
        if body and re.match(r"(City|Village|Township|Hamilton County|.*School District|.*Library)", entity.strip()):
            rows.append(
                {"url": HAMILTON, "county": "Hamilton", "kind": head.strip(), "entity": entity.strip(), "text": body}
            )
    return rows


def summit() -> list[dict[str, str]]:
    rows = []
    for block in re.split(r"^Issue \d+\s*$", pdf(SUMMIT, "-raw"), flags=re.MULTILINE)[1:]:
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        text = re.split(r"THIS LIST IS SUBJECT", "\n".join(lines))[0].split("\n")
        entity: list[str] = []
        index = 0
        while index < len(text) and not re.fullmatch(r"[A-Z ,&-]+|\([A-Z, ]+\)", text[index]):
            entity.append(text[index])
            index += 1
        kind: list[str] = []
        while index < len(text) and (
            re.fullmatch(r"[A-Z ,&-]+|\([A-Z, ]+\)", text[index])
            or text[index].startswith(("Overlaps", "Counties", "Overlap"))
        ):
            kind.append(text[index])
            index += 1
        body = re.sub(r"\s+", " ", " ".join(text[index:])).strip()
        if entity and body and not entity[0].startswith("STATE"):
            rows.append(
                {
                    "url": SUMMIT,
                    "county": "Summit",
                    "kind": " ".join(kind).replace("Overlaps:", ""),
                    "entity": " ".join(entity),
                    "text": body,
                }
            )
    return rows


def cuyahoga() -> list[dict[str, str]]:
    rows = []
    path = str(raw.cached(SOURCE, CUYAHOGA).path)
    for page, x, width, bottom in CUYAHOGA_BOXES:
        text = subprocess.run(
            [
                "pdftotext",
                "-f",
                str(page),
                "-l",
                str(page),
                "-layout",
                "-x",
                str(x),
                "-y",
                "135",
                "-W",
                str(width),
                "-H",
                str(bottom - 135),
                path,
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        flat = re.sub(r"\s+", " ", text).strip()
        head, _, body = flat.partition("A majority affirmative vote is necessary for passage.")
        kind = re.sub(r"^\d+ ", "", head)
        rows.append(
            {
                "url": CUYAHOGA,
                "county": "Cuyahoga",
                "kind": kind.split(" County of")[0].split(" Board of")[0].split(" Health and")[0],
                "entity": "Cuyahoga County",
                "text": body.strip(),
            }
        )
    return rows


def parse() -> None:
    frame = pl.DataFrame(hamilton() + summit() + cuyahoga())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "issues.parquet")
    print(frame.group_by("county").len().rows())


def keys(name: str) -> set[str]:
    name = re.sub(
        r"^(City|Village|Township) of (.+)$", lambda m: f"{m[2]} {m[1]}" if m[1] == "Township" else m[2], name
    )
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
            summary="Official ballot language for Hamilton, Summit, and Cuyahoga County, Ohio local issues on the November 3, 2026 ballot",
            polity="US-OH",
        )
        sources: dict[str, UUID] = {}
        for url, (_, _, publisher, title) in SOURCES.items():
            sources[url] = cs.source(publisher, title)
            cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        kept = 0
        used: set[UUID] = set()
        for row in frame.iter_rows(named=True):
            options = [c for k in keys(row["entity"]) for c in contests.get(k, []) if c[0] not in used]
            wanted = (
                "income tax"
                if "INCOME" in row["kind"].upper()
                else "bond"
                if "BOND" in row["kind"].upper()
                else "tax levy"
            )
            narrowed = [c for c in options if c[2].lower().startswith(wanted)]
            if "CHARTER" in row["kind"].upper():
                narrowed = [c for c in options if c[2].lower().startswith("miscellaneous")]
            for word in ("additional", "renewal", "replacement", "decrease", "increase"):
                if len(narrowed) > 1 and word in row["kind"].lower():
                    narrowed = [c for c in narrowed if word in c[2].lower()] or narrowed
            section = re.search(r"Section (\d+\.\d+)", row["text"])
            if len(narrowed) > 1 and section:
                narrowed = [c for c in narrowed if f"Section {section[1]}" in c[2]] or narrowed
            if len({c[0] for c in narrowed}) != 1:
                print("no match:", row["entity"], row["kind"], len(narrowed))
                continue
            contest = narrowed[0][0]
            used.add(contest)
            kept += 1
            cs.add("citation", entity_id=contest, source_id=sources[row["url"]])
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["text"],
                url=row["url"],
                source_id=sources[row["url"]],
            )
        print(f"{kept} of {frame.height} issues")
        return cs.write()
