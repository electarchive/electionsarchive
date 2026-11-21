"""Reading the district education council results in Elections NB's reports, from their pdftotext layout.

Each report prints, for every school district, a results table: a heading for each subdistrict and under it a line for
every candidate with the votes received (a number, `accl.` for a candidate acclaimed, or `withdrew`) and a mark for the
candidates elected. From 2012 on a recapitulation table also gives each subdistrict's electors and valid votes.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HYPHENS = str.maketrans({"‐": "-", "‑": "-", "–": "-", " ": " "})

NAMED = re.compile(r"^\s*School District (Anglophone|Francophone) (.+?)\s*/\s*District Scolaire", re.IGNORECASE)
NUMBERED = re.compile(r"^\s*SCHOOL DISTRICT (\d+) / DISTRICT SCOLAIRE \d+\s*$")
LANGUAGE = re.compile(r"^\s*\((Francophone|Anglophone) / (?:francophone|anglophone)\)\s*$")
SUBDISTRICT = re.compile(r"^\s*Sub ?[Dd]istrict\s*(?:/\s*Sous.district\s*)?(\d+)\b")
ZONE = re.compile(r"^\s*Sub ?[Dd]istricts (\d+) and (\d+) \(Zone ([A-Z])\)")
ENDS = re.compile(r"Poll by Poll|REGIONAL HEALTH|HEALTH AUTHORITY|Health Region|Positions Filled|POSITIONS FILLED")
APPOINTED = re.compile(r"Appt'd/nomm|^VACANT\b", re.IGNORECASE)
RECOUNT = re.compile(r"^\s*SCHOOL DISTRICT/DISTRICT SCOLAIRE (\d+)\s*$")
RECOUNT_SUB = re.compile(r"^\s*SUBDISTRICT/SOUS-DISTRICT (\d+)\s*$")
RECOUNT_ROW = re.compile(r"^\s+(?P<name>\S.*?)\s{2,}(?P<votes>\d+)(?:\s+(?P<mark>E))?\s*$")
CANDIDATE = re.compile(
    r"^(?P<name>\S.*?)\s{2,}(?P<sex>[MFX])\s+(?:(?P<address>.*?)\s{2,})?"
    r"(?P<votes>\d+|accl\.|withdrew/retiré|N/A)(?P<star>\s*\*)?(?:\s+(?P<mark>[EA]))?(?:\s+inc\./sort\.)?(?:\s+\*)?\s*$"
)
VACANT = re.compile(r"no candidate/aucun\(e\) candidat\(e\)")
RECAP = re.compile(
    r"^(?P<district>(?:Anglophone|Francophone) \S+(?: \S+)?)\s+(?P<sub>\d+)\s+(?P<seats>\d+)\s+(?P<nominated>\d+)\s+"
    r"(?P<elected>\d+|N/A)\s+(?P<preliminary>[\d,]+)\s+(?P<revisions>-?[\d,]+)\s+(?P<net>[\d,]+)"
    r"(?:\s+(?P<issued>[\d,]+)\s+(?P<valid>[\d,]+)\s+[\d.]+%|\s+acclaimed|\s+no candidate)"
)


@dataclass
class Candidate:
    name: str
    sex: str
    votes: int | None
    elected: bool
    withdrew: bool = False


@dataclass
class Subdistrict:
    number: str
    seats: int = 1
    candidates: list[Candidate] = field(default_factory=list)
    vacant: bool = False
    recounted: bool = False


@dataclass
class District:
    key: str
    language: str
    subdistricts: dict[str, Subdistrict] = field(default_factory=dict)


@dataclass
class Recap:
    electors: int
    valid: int | None


def lines(path: Path) -> list[str]:
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
    return done.stdout.decode("utf8", "replace").translate(HYPHENS).splitlines()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def number(text: str) -> int:
    return int(text.replace(",", ""))


def results(rows: list[str]) -> tuple[list[District], list[str]]:
    """Each district's subdistricts with their candidates, and the lines that sat in a results table but fit none."""
    found: dict[str, District] = {}
    stray: list[str] = []
    district: District | None = None
    reading = False
    skip = False
    sub: Subdistrict | None = None
    for position, row in enumerate(rows):
        text = row.strip()
        named = NAMED.match(row)
        numbered = NUMBERED.match(row)
        if named or numbered:
            if named:
                language = named.group(1).title()
                key = f"{language} {clean(named.group(2))}"
            else:
                assert numbered
                language = ""
                for ahead in rows[position + 1 : position + 4]:
                    hit = LANGUAGE.match(ahead)
                    if hit:
                        language = hit.group(1).title()
                key = f"District {int(numbered.group(1))}"
            district = found.setdefault(key, District(key, language))
            reading = False
            skip = False
            sub = None
            continue
        if district is None:
            continue
        if ENDS.search(row):
            reading = False
            skip = True
            sub = None
            continue
        if "CANDIDATE" in row and "VOTES RECEIVED" in row:
            reading = not skip
            continue
        if not reading or not text:
            continue
        zone = ZONE.match(row)
        if zone:
            label = f"{zone[1]}+{zone[2]} zone {zone[3]}"
            sub = district.subdistricts.setdefault(label, Subdistrict(label, 2))
            continue
        hit = SUBDISTRICT.match(row)
        if hit:
            label = str(int(hit.group(1)))
            sub = district.subdistricts.setdefault(label, Subdistrict(label))
            continue
        if sub is None:
            continue
        if APPOINTED.search(text):
            continue
        if VACANT.search(row) or text.startswith(("To be appointed", "À être nommé")):
            sub.vacant = True
            continue
        match = CANDIDATE.match(row)
        if match:
            if match["star"]:
                sub.recounted = True
            votes = match["votes"]
            sub.candidates.append(
                Candidate(
                    clean(match["name"]),
                    match["sex"],
                    int(votes) if votes.isdigit() else None,
                    match["mark"] is not None,
                    votes.startswith("withdrew"),
                )
            )
            continue
        if not re.match(r"^(\*|May |DISTRICT EDUCATION|Élections|CANDIDAT|Notes?|-)", text) and not re.fullmatch(
            r"[\d\s-]*", text
        ):
            stray.append(text)
    return list(found.values()), stray


def recaps(rows: list[str]) -> dict[tuple[str, int], Recap]:
    """The electors and valid votes of each subdistrict, by district name and number, from the recapitulation."""
    found: dict[tuple[str, int], Recap] = {}
    for row in rows:
        hit = RECAP.match(row.strip())
        if hit:
            valid = hit["valid"]
            found[(clean(hit["district"]), int(hit["sub"]))] = Recap(
                number(hit["net"]), number(valid) if valid else None
            )
    return found


def recounts(rows: list[str]) -> dict[tuple[str, str], list[tuple[str, int, bool]]]:
    """The judicial recount tables: each candidate's votes and whether elected, by district key and subdistrict."""
    found: dict[tuple[str, str], list[tuple[str, int, bool]]] = {}
    district = sub = None
    for row in rows:
        hit = RECOUNT.match(row)
        if hit:
            district, sub = f"District {int(hit.group(1))}", None
            continue
        hit = RECOUNT_SUB.match(row)
        if hit and district:
            sub = str(int(hit.group(1)))
            continue
        if district and sub:
            hit = RECOUNT_ROW.match(row)
            if hit and "CANDIDATE" not in row:
                found.setdefault((district, sub), []).append((clean(hit["name"]), int(hit["votes"]), bool(hit["mark"])))
    return found
