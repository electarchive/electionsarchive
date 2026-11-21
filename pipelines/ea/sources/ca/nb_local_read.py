"""Read the results of New Brunswick's quadrennial local elections from the Municipal Electoral Officer's reports."""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def lines(path: Path) -> list[tuple[int, str]]:
    """Each text line of a PDF as (page, text) with runs of blanks collapsed (needs poppler's pdftotext)."""
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return [
        (number, " ".join(line.split()))
        for number, page in enumerate(text.split("\f"), 1)
        for line in page.split("\n")
        if line.strip()
    ]


@dataclass
class Candidate:
    name: str
    sex: str
    votes: int | None  # None when acclaimed
    elected: bool
    incumbent: bool


@dataclass
class Race:
    office: str  # mayor or councillor
    ward: str  # empty for the mayor and for councillors at large
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Council:
    name: str
    page: int
    seats: dict[str, int] = field(default_factory=dict)
    polls: int = 0
    races: list[Race] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)


_SEX = {"M", "F", "X"}
_STOP = re.compile(r"^(?:Poll by Poll Results|Résultats du vote|Recounts\b)")
_RECOUNT = re.compile(
    r"^(?P<head>.+?)\s+(?P<votes>\d[\d,]*)(?P<elected>\s+(?:E\*?|\*))?\s+was / était\s+(?P<was>\d[\d,]*)"
)
_TAIL = re.compile(
    r"^(?P<head>.*?)\s+(?P<votes>accl\.|\d[\d,]*)(?P<elected>\s+[ET])?(?P<inc>\s+inc\./sort\.)?(?:\s+\*+)?$"
)
_WARD = re.compile(r"^Councillor Ward\s+(?P<ward>.+?)\s*/\s*Conseiller")
_SEATS = re.compile(r"^(?:Number of Seats / Nombre de sièges\s+)?(?P<n>\d+)\s+(?P<what>[A-Za-z].*)$")
_NOISE = re.compile(
    r"^(?:May \d+, 20\d\d|MUNICIPAL|CANDIDAT|CANDIDATE|Polling Divisions|Date of Incorporation|MAY \d+, 20\d\d|\*|"
    r"no candidate|Mayor / Maire$)",
    re.IGNORECASE,
)


def councils(rows: list[tuple[int, str]]) -> list[Council]:
    """The per-municipality candidate lists of a report's general results."""
    found: list[Council] = []
    current: Council | None = None
    race: Race | None = None
    recent: list[str] = []
    for page, text in rows:
        text = text.strip()
        if current is not None and _STOP.match(text):
            break
        if text.startswith("Date of Incorporation"):
            names = [line for line in recent if not re.match(r"(?i)^(MAY \d+, 20\d\d MUNICIPAL|MUNICIPAL )", line)]
            current = Council(names[-1] if names else "", page)
            found.append(current)
            race = None
            recent = []
            continue
        recent = [*recent[-3:], text]
        if current is None:
            continue
        if text.startswith("Number of Seats"):
            text = text.split("Nombre de sièges", 1)[1].strip()
        seat = _SEATS.match(text)
        if seat and race is None and not re.search(r"(?i)^\d+\s+\S+(\s+\S+)*\s+\d", text):
            what = seat.group("what").lower()
            if what.startswith("mayor"):
                current.seats["mayor"] = int(seat.group("n"))
            elif "ward" in what:
                current.seats["ward"] = int(seat.group("n"))
            elif what.startswith("councillor"):
                current.seats["large"] = int(seat.group("n"))
            continue
        if text.startswith("Polling Divisions"):
            digits = re.findall(r"\d+", text)
            current.polls = int(digits[-1]) if digits else 0
            continue
        if re.fullmatch(r"Mayor\s*/\s*Maire(?:,.*)?", text):
            race = Race("mayor", "")
            current.races.append(race)
            continue
        if re.fullmatch(
            r"Councillor(?: at Large)?\s*/\s*Conseiller\(ère\)(?: Général\(e\))?(?:,.*)?", text, re.IGNORECASE
        ):
            race = Race("councillor", "")
            current.races.append(race)
            continue
        ward = _WARD.match(text)
        if ward:
            race = Race("councillor", ward.group("ward").strip())
            current.races.append(race)
            continue
        if race is None:
            continue
        tail = _TAIL.match(text)
        if tail is None:
            if re.search(r"\s[MFX]\s", text) and not _NOISE.match(text) and "withdrew" not in text:
                current.unread.append(f"{page}: {text}")
            continue
        tokens = tail.group("head").split()
        spot = next((i for i, token in enumerate(tokens) if i > 0 and token in _SEX), None)
        if spot is None:
            current.unread.append(f"{page}: {text}")
            continue
        votes = tail.group("votes")
        race.candidates.append(
            Candidate(
                " ".join(tokens[:spot]),
                tokens[spot],
                None if votes == "accl." else int(votes.replace(",", "")),
                bool(tail.group("elected")),
                bool(tail.group("inc")),
            )
        )
    return found


@dataclass
class Recount:
    council: str
    race: Race
    was: dict[str, int] = field(default_factory=dict)
    winner: str | None = None  # the candidate a tie was resolved for, where the report says


def recounts(rows: list[tuple[int, str]]) -> list[Recount]:
    """The recounts at the Municipal Returning Offices, with the votes each candidate had before."""
    found: list[Recount] = []
    start = next((i for i, (_, text) in enumerate(rows) if re.match(r"^Recounts\s+Seconds", text)), None)
    if start is None:
        return found
    recent: list[str] = []
    council = ""
    current: Recount | None = None
    for _, text in rows[start + 1 :]:
        if re.match(r"(?i)^(By-Elections|Élections partielles|First-Elections)", text) or re.match(
            r"(?i)^By-Elections?\b.*Élections partielles", text
        ):
            break
        if not text.startswith("*") and ("recount at the Municipal" in text or "Judicial Recount" in text):
            names = [
                x
                for x in recent
                if not re.search(r"\d|was /|^\*|^(?:The|Les|Le|CANDIDAT|MUNICIPAL|May|June)\b", x)
                and "recount" not in x
            ]
            council = names[-1] if names else council
            current = None
            recent = []
            continue
        recent = [*recent[-3:], text]
        if re.fullmatch(r"Mayor\s*/\s*Maire(?:,.*)?", text):
            current = Recount(council, Race("mayor", ""))
            found.append(current)
            continue
        if re.fullmatch(
            r"Councillor(?: at Large)?\s*/\s*Conseiller\(ère\)(?: Général\(e\))?(?:,.*)?", text, re.IGNORECASE
        ):
            current = Recount(council, Race("councillor", ""))
            found.append(current)
            continue
        ward = _WARD.match(text)
        if ward:
            current = Recount(council, Race("councillor", ward.group("ward").strip()))
            found.append(current)
            continue
        tie = re.match(r"^\*\s*The tie vote was resolved in favour of (.+?) in accordance", text)
        if tie and current is not None:
            current.winner = tie.group(1).strip()
            for person in current.race.candidates:
                person.elected = sorted(person.name.lower().split()) == sorted(current.winner.lower().split()) or (
                    person.name.lower().split()[-1] == current.winner.lower().split()[-1]
                    and person.name.lower().split()[0] == current.winner.lower().split()[0]
                )
            continue
        hit = _RECOUNT.match(text)
        if hit and current is not None:
            tokens = hit.group("head").split()
            spot = next((i for i, token in enumerate(tokens) if i > 0 and token in _SEX), None)
            if spot is None:
                continue
            name = " ".join(tokens[:spot])
            current.race.candidates.append(
                Candidate(
                    name, tokens[spot], int(hit.group("votes").replace(",", "")), bool(hit.group("elected")), False
                )
            )
            current.was[name] = int(hit.group("was").replace(",", ""))
    return found


def aliases(name: str) -> list[str]:
    """The names a council goes by in a heading: one or two, with the status words dropped."""
    name = name.replace("‐", "-").replace("‑", "-").replace("‒", "-").replace("–", "-")
    name = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", name)
    out = []
    for part in name.split("/"):
        part = part.strip()
        part = re.sub(
            r"(?i)^(?:THE\s+)?(?:CITY|TOWN|VILLAGE|REGIONAL MUNICIPALITY|RURAL COMMUNITY|VILLE|CITÉ)\s+(?:OF|DE|D'|DU)\s*",
            "",
            part,
        )
        part = re.sub(r"(?i)\s+RURAL COMMUNITY$", "", part)
        part = re.sub(r"(?i)^COMMUNAUTÉ RURALE\s+(?:DE |D'|DU )", "", part)
        if part:
            out.append(part)
    return out


_RECAP = re.compile(
    r"^(?P<name>.+?)\s+M\d\d\s+(?P<pre>[\d,]+|---)\s+(?P<rev>-?[\d,]+|---)\s+(?P<net>[\d,]+|---)\s+(?P<rest>.*)$"
)


def recap(rows: list[tuple[int, str]]) -> dict[str, tuple[int, int | None]]:
    """Each council's electors on the final list and the electors who voted (None when all were acclaimed), by name."""
    found: dict[str, tuple[int, int | None]] = {}
    for _, text in rows:
        hit = _RECAP.match(text)
        if hit is None or hit.group("net") == "---":
            continue
        voted = re.search(r"\s([\d,]+)\s+\d+\.\d+%(?:\s+\S)?$", " " + hit.group("rest"))
        found.setdefault(
            hit.group("name"),
            (int(hit.group("net").replace(",", "")), int(voted.group(1).replace(",", "")) if voted else None),
        )
    return found
