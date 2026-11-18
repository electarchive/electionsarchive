"""Reading the National Electoral Commission's National Assembly notices in the Government Gazette.

Each notice prints, for every constituency, the candidates with their party and votes, then a line naming the
candidate declared elected. The 2015 notice (Gazette No. 1546 of 28 October 2016) is one column of text per page. The
2020 notice (Gazette No. 30B of 5 January 2021) is two columns per page, so each page is cut in half by position before
it is read.
"""

import re
import subprocess
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

REGIONS = (
    "ARUSHA",
    "DAR ES SALAAM",
    "DODOMA",
    "GEITA",
    "IRINGA",
    "KAGERA",
    "KASKAZINI PEMBA",
    "KASKAZINI UNGUJA",
    "KATAVI",
    "KIGOMA",
    "KILIMANJARO",
    "KUSINI PEMBA",
    "KUSINI UNGUJA",
    "LINDI",
    "MANYARA",
    "MARA",
    "MBEYA",
    "MJINI MAGHARIBI",
    "MOROGORO",
    "MTWARA",
    "MWANZA",
    "NJOMBE",
    "PWANI",
    "RUKWA",
    "RUVUMA",
    "SHINYANGA",
    "SIMIYU",
    "SINGIDA",
    "SONGWE",
    "TABORA",
    "TANGA",
)

# Each party's own spelling, with the ways the notices print it at the end of a candidate's line.
PARTY_TAILS = {
    "AAFP": r"AAFP",
    "ACT-Wazalendo": r"ACT(?:[\s–-]*(?i:wazalendo))?",
    "ADA-TADEA": r"(?:ADA[\s–-]*)?TADEA",
    "ADC": r"ADC",
    "AFP": r"AFP",
    "APPT-Maendeleo": r"APPT[\s–-]*MAENDELEO",
    "CCK": r"CCK",
    "CCM": r"CCM",
    "CHADEMA": r"CHADEMA",
    "CHAUMMA": r"CHAUMMA",
    "CHAUSTA": r"CHAUSTA",
    "CUF": r"CUF",
    "Demokrasia Makini": r"(?:DEMOKRASIA\s+)?MAKINI?",
    "DP": r"DP",
    "Jahazi Asilia": r"JAHAZI\s+ASILIA",
    "NCCR-Mageuzi": r"NCCR[\s–-]*(?i:mageuzi)",
    "NLD": r"NLD",
    "NRA": r"N\.?\s*R\.?\s*A",
    "SAU": r"SAU",
    "TLP": r"TLP",
    "UDP": r"UDP",
    "UMD": r"UMD",
    "UPDP": r"UPDP",
}
TAIL = re.compile(
    r"^(?P<name>.*?)[\s–-]*(?:"
    + "|".join(f"(?P<p{index}>{rx})" for index, rx in enumerate(PARTY_TAILS.values()))
    + r")\s*$",
    re.DOTALL,
)
LEADER = re.compile(r"^(?P<label>.*?\S)\s*[.…,]+[\s.…,]*(?P<votes>\d(?:[\d,]| (?=\d))*)\s*$")
WINNER = re.compile(r"^\s*Mgombe\w*?\s*aliyechagu", re.IGNORECASE)
GLUED = re.compile(r"^(?P<label>.*[A-Za-z])(?P<votes>\d[\d,]*)$")


@dataclass
class Candidate:
    name: str
    party: str
    votes: int | None
    printed: str = ""


@dataclass
class Contest:
    region: str
    name: str
    candidates: list[Candidate] = field(default_factory=list)
    winner: str = ""
    winner_party: str = ""
    lines: list[str] = field(default_factory=list)


def fold(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def region_of(text: str) -> str | None:
    """The region a header names, or None. The notices repeat a name in places."""
    words = fold(re.sub(r"^\s*\d+\.\s*", "", text).replace("MKOA WA", "").strip())
    for region in sorted(REGIONS, key=len, reverse=True):
        if words == fold(region) or words == fold(region) * 2:
            return region
    return None


def region_header(line: str) -> str | None:
    """The region a line heads, as ``MKOA WA ARUSHA``, ``1. MKOA WA ARUSHA``, or ``21. MWANZA``."""
    return region_of(line) if re.fullmatch(r"(?:\d+\.\s*)?(?:MKOA\s+WA\s+)?[A-Z ]+", line) else None


def split_party(label: str) -> tuple[str, str] | None:
    """The candidate's name and the party's own spelling, from a label such as ``NAME Names - CCM``."""
    found = TAIL.match(label.strip())
    if not found:
        return None
    names = list(PARTY_TAILS)
    party = next(names[index] for index in range(len(names)) if found.group(f"p{index}") is not None)
    return clean(found.group("name")), party


def clean(name: str) -> str:
    name = re.sub(r"\s+", " ", name.replace("’", "'")).strip(" -–—.")
    return name


def winner_of(line: str) -> tuple[str, str] | None:
    """The name and party on a line that declares a winner."""
    body = re.sub(
        r"^\s*Mgombe\w*?\s*aliyechagu\w*\s*(?:ni\s+)?(?i:ndugu)?(?=\s|[A-Z])\s*(?:ni\s+)?",
        "",
        line,
        flags=re.IGNORECASE,
    )
    body = re.sub(r"\s+Amepita\s+Bila\s+Kupingwa\s*$", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\s+wa\s+(?=[A-Z]+$)", " - ", body)
    body = re.sub(r"\s+ni\s+(CCM|CUF|CHADEMA)\s*$", r" - \1", body)
    return split_party(body)


@cache
def pdftext(path: str, first: int, last: int, x: int | None = None, width: int | None = None) -> str:
    command = ["pdftotext", "-f", str(first), "-l", str(last)]
    if x is None:
        command += ["-layout"]
    else:
        command += ["-x", str(x), "-y", "52", "-W", str(width), "-H", "718"]
    done = subprocess.run([*command, str(Path(path)), "-"], check=True, capture_output=True, text=True)
    return done.stdout


HEADER = re.compile(r"JIMBO\s*LA\s+UCHAGUZI\s+LA\s+(?:JIMBO\s+LA\s+)?(?P<name>.+?)\s*$")
PAGE = re.compile(r"GAZETI\s+LA\s+JAMHURI|^\s*\d{1,3}\s*$|^\d{1,2}\s+(?:Oktoba|Januari),?\s+\d{4}\s*$")
VOTES_ONLY = re.compile(r"^[.…\s]*(?P<votes>\d[\d,]*)\s*$")
STOP = re.compile(
    r"^\s*(?:B:\s*WABUNGE|VITI\s+MAALUM|MATOKEO\s+YA\s+UCHAGUZI\s+MKUU\s+WA\s+MADIWANI|TAARIFA\s+YA\s+KAWAIDA\s+NA\.\s*1547)"
)


def parse(lines: list[str]) -> list[Contest]:
    """Read the notice's lines into constituencies.

    A winner line closes a contest, and each header names the next contest to close. The reflowed 2020 file prints one
    header ahead of its place, so a header is matched with the next winner line that no earlier header has claimed.
    """
    contests: list[Contest] = []
    region = ""
    pending = ""
    headers: list[tuple[str, str]] = []
    block: list[Candidate] = []
    stray: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or PAGE.search(line):
            continue
        if STOP.match(line):
            break
        if pending and (HEADER.match(line) or WINNER.match(line)):
            stray.append(pending)
            pending = ""
        if not pending:
            found = region_header(line)
            if found:
                region = found
                continue
            if not region:
                continue
            header = HEADER.match(line)
            if header:
                headers.append((region, clean(header.group("name"))))
                continue
        text = f"{pending} {line}".strip() if pending else line
        if WINNER.match(text):
            found = winner_of(text)
            if not found:
                pending = text
                continue
            pending = ""
            where, name = headers.pop(0) if headers else ("", "")
            contests.append(Contest(where, name, block, found[0], found[1], stray))
            block, stray = [], []
            continue
        leader = LEADER.match(text) or (GLUED.match(text) if split_party(GLUED.sub(r"\g<label>", text)) else None)
        if leader:
            found = split_party(leader.group("label"))
            if found is None:
                stray.append(text)
            else:
                block.append(Candidate(found[0], found[1], int(re.sub(r"[, ]", "", leader.group("votes"))), text))
            pending = ""
            continue
        only = VOTES_ONLY.match(line)
        if only and pending:
            found = split_party(pending)
            if found:
                block.append(Candidate(found[0], found[1], int(only.group("votes").replace(",", "")), text))
            else:
                stray.append(text)
            pending = ""
            continue
        if len(text) > 300:
            stray.append(text)
            pending = ""
        else:
            pending = text
    if headers or block or pending:
        raise ValueError(f"unclaimed: {headers} {block} {pending!r}")
    return contests
