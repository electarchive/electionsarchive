"""Candidacy lists of the Croatian national minority elections 2019 and 2023, from the State Electoral Commission.

For each county, town, and municipality the commission publishes one PDF per kind of election (k_0 holds the councils'
lists and k_1 the representatives' candidacies). A PDF gives, for each minority, the summary of every candidate and the
valid lists or candidacies with their proposers and the order of their candidates. The earlier years have none.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from ea import raw

SOURCE = "hr.manjine"
SITE = "https://www.izbori.hr/arhiva-izbora"
YEARS = [2019, 2023]
UNITS_SCRIPT = SITE + "/app/dao/manjine/manjine-{year}-dao.js"


def units_url(year: int) -> str:
    return UNITS_SCRIPT.format(year=year)


def file_url(year: int, kind: int, county: str, unit: str) -> str:
    return f"{SITE}/data/manjine/{year}/kandidatura/k_{kind}_{county}_{unit}.pdf"


def _block(script: str, name: str) -> list[tuple[str, ...]]:
    start = script.index(f"var {name} = [")
    end = script.index("\n        ]", start)
    return [
        tuple(row)
        for row in re.findall(
            r"\[\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"](?:\s*,\s*['\"]([^'\"]*)['\"])?", script[start:end]
        )
    ]


def unit_files(year: int) -> list[tuple[int, str, str]]:
    """The (kind, county, unit) of every candidacy PDF the commission's site links for a year."""
    script = raw.cached(SOURCE, units_url(year)).path.read_text(encoding="utf-8")
    county_of = {code: county for code, _, county in _block(script, "grop")}
    found: set[tuple[int, str, str]] = set()
    for county, kind, *_ in _block(script, "zupVrsta"):
        found.add((int(kind), county, "0000"))
    for unit, kind, *_ in _block(script, "gioVrsta"):
        found.add((int(kind), county_of[unit], unit))
    return sorted(found, key=lambda f: (f[1], f[2], f[0]))


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, units_url(year), suffix=".js")
    urls = [file_url(y, *f) for y in YEARS for f in unit_files(y)]
    got = raw.fetch_many(SOURCE, urls, suffix=".pdf", workers=4)
    print(f"{SOURCE}: {len(got)} of {len(urls)} candidacy lists cached", flush=True)


@dataclass
class Entry:
    name: str
    deputy: str = ""
    tag: str = ""  # the proposer's short name the summary appends to a name


@dataclass
class Slate:
    proposer: str
    entries: list[Entry] = field(default_factory=list)


@dataclass
class Listing:
    """One minority's candidacies in one unit's file."""

    kind: str  # council or representative
    minority: str  # the adjective as headed, such as "SRPSKE"
    summary: list[Entry] = field(default_factory=list)
    slates: list[Slate] = field(default_factory=list)
    none: bool = False  # the file says no candidacy was submitted


HEADING = re.compile(r"^(ZBIRNA LISTA KANDIDAT(?:A|URA)|PRAVOVALJANA LISTA KANDIDATA|PRAVOVALJANA KANDIDATURA)$")
TITLE = re.compile(r"^ZA IZBOR (ČLANOVA VIJEĆA|PREDSTAVNIKA) (\S+) NACIONALNE MANJINE\b")
ROW = re.compile(r"^(\d+)\.\s+(\S.*)$")
NONE = re.compile(r"^Za izbor (?:članica/članova vijeća|predstavnice/predstavnika) - (\S+) nacionalna manjina, nema")
NAME_LABELS = {"Kandidat:", "Kandidatkinja:"}
DEPUTY_LABELS = {"Kandidat za zamjenika:", "Kandidatkinja za zamjenicu:"}


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def parse(path: Path) -> list[Listing]:
    import subprocess

    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    lines = [_clean(line) for line in text.replace("\f", "\n").split("\n")]
    out: list[Listing] = []
    current: Listing | None = None
    block = ""  # summary or valid
    title: list[str] = []
    in_title = False
    slate: Slate | None = None
    mode = ""  # proposer, names, name, deputy
    for line in lines:
        if not line:
            if mode == "proposer":
                mode = ""
            continue
        none = NONE.match(line)
        if none:
            current = Listing("council" if "vijeća" in line else "representative", none.group(1), none=True)
            out.append(current)
            block, in_title, slate, mode = "", False, None, ""
            continue
        if line.startswith(("ZA IZBOR ", "NACIONALNE MANJINE U ")) and not in_title:
            continue  # the title a page repeats above a note that no candidacy was submitted
        if HEADING.match(line):
            block = "summary" if line.startswith("ZBIRNA") else "valid"
            title, in_title, mode = [], True, ""
            slate = None
            continue
        if in_title:
            if ROW.match(line) or line == "Predlagatelj:":
                in_title = False
                head = TITLE.match(" ".join(title))
                if head is None:
                    raise ValueError(f"unreadable heading {' '.join(title)!r} in {path.name}")
                kind = "council" if head.group(1).startswith("ČLAN") else "representative"
                same = out and out[-1].kind == kind and out[-1].minority == head.group(2)
                if same:
                    current = out[-1]
                else:
                    current = Listing(kind, head.group(2))
                    out.append(current)
            else:
                title.append(line)
                continue
        if current is None:
            continue
        if line == "Predlagatelj:":
            slate = Slate("")
            current.slates.append(slate)
            mode = "proposer"
            continue
        if line in ("Kandidati/kandidatkinje:",):
            mode = "names"
            continue
        if line in NAME_LABELS:
            mode = "name"
            continue
        if line in DEPUTY_LABELS:
            mode = "deputy"
            continue
        if mode == "proposer" and slate is not None:
            slate.proposer = _clean(f"{slate.proposer} {line}")
            continue
        if block == "valid":
            if mode == "name" and slate is not None:
                slate.entries.append(Entry(line))
                mode = ""
                continue
            if mode == "deputy" and slate is not None and slate.entries:
                slate.entries[-1].deputy = line
                mode = ""
                continue
            row = ROW.match(line)
            if row and slate is None and current.slates:
                slate = current.slates[-1]  # a page break repeats the heading over the rest of a list
                mode = "names"
            if row and slate is not None:
                slate.entries.append(Entry(row.group(2)))
                continue
            if slate is not None and slate.entries and mode == "names":
                slate.entries[-1].name = _clean(f"{slate.entries[-1].name} {line}")
            continue
        row = ROW.match(line)
        if row:
            name, _, tag = row.group(2).partition(";")
            current.summary.append(Entry(_clean(name), tag=_clean(tag)))
        elif re.match(r"^Zamjeni(k|ca):", line) and current.summary:
            current.summary[-1].deputy = _clean(line.split(":", 1)[1])
    return out


def grop(year: int) -> list[tuple[str, str, str]]:
    """The (unit code, name, county) of every town and municipality the commission's site lists for a year."""
    script = raw.cached(SOURCE, units_url(year)).path.read_text(encoding="utf-8")
    return [(code, name, county) for code, name, county in _block(script, "grop")]


def listings(year: int) -> dict[tuple[str, str, str], tuple[str, list[Listing]]]:
    """The parsed candidacy files of a year by (county, unit code, file kind): the file's name and its listings."""
    out = {}
    for kind, county, unit in unit_files(year):
        try:
            artifact = raw.cached(SOURCE, file_url(year, kind, county, unit))
        except LookupError:
            continue
        out[(county, unit, str(kind))] = (file_url(year, kind, county, unit), parse(artifact.path))
    return out
