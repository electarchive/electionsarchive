"""Municipal Corporation of Delhi general elections, by ward, from the State Election Commission, NCT of Delhi.

The Commission posts each election as PDF or spreadsheet reports. 2007 is a spreadsheet of every candidate's votes in
each of the 272 wards. 2012, 2017, and 2022 are reports of each ward's winner and runner-up; 2017 adds a Form 18 of
every candidate's votes, without parties. The 1997 report is a scan, and the 2002 report pages hold no files."""

import difflib
import functools
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import pymupdf
import xlrd

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.bt import pdfwords

SOURCE = "in.delhi_sec"
BASE = "https://sec.delhi.gov.in/sites/default/files/"
GENERAL = "SEC/generic_multiple_files/"
TAB = "SEC/universal-tab/"
R2007 = (
    GENERAL + "winner_runner-up_report_municipal_corporation_of_delhi_election_2007.pdf",
    GENERAL + "election-result.xls",
)
R2012 = GENERAL + "2012winnerrunnerup.pdf"
R2017 = {
    "NORTH": (GENERAL + "winner-runner_up.pdf", GENERAL + "form_18.pdf"),
    "SOUTH": (GENERAL + "winner-runner_up-south.pdf", GENERAL + "form_18_south.pdf"),
    "EAST": (GENERAL + "winner-runner_up_1.pdf", GENERAL + "form_18_1.pdf"),
}
R2022 = TAB + "result_summary-ward_wise_1.pdf"
SCAN1997 = "1997-1.pdf"
ALL = [*R2007, R2012, *(f for pair in R2017.values() for f in pair), R2022, SCAN1997]


@dataclass
class Candidate:
    name: str
    party: str
    votes: int


@dataclass
class Ward:
    year: int
    corporation: str
    number: int
    name: str
    reservation: str
    candidates: list[Candidate]
    electors: int | None = None
    polled: int | None = None
    valid: int | None = None
    stations: int | None = None
    full: bool = False
    extra: dict[str, int] = field(default_factory=dict)


def url(name: str) -> str:
    return BASE + name


def fetch() -> None:
    for name in ALL:
        raw.fetch(SOURCE, url(name), suffix="." + name.rsplit(".", 1)[1])


def path(name: str) -> Path:
    return raw.DATA / raw.cached(SOURCE, url(name)).storage_key


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def party_of(text: str) -> str:
    text = clean(text).upper()
    return "INDEPENDENT" if text in {"", "IND", "IND."} else text


# ---------- 2007: the spreadsheet of every candidate in each ward ----------


@functools.cache
def ward_2007() -> list[Ward]:
    sheet = xlrd.open_workbook(path(R2007[1])).sheet_by_index(0)
    out: list[Ward] = []
    for r in range(sheet.nrows):
        row = sheet.row_values(r)
        if row[2] and row[7] and isinstance(row[21], float):
            tail = re.search(r"\(([A-Z]+)\)\s*$", str(row[2]))
            name = re.sub(r"\s*\([A-Z]+\)\s*$", "", str(row[2])).lstrip("- ").strip()
            out.append(
                Ward(
                    2007,
                    "MCD",
                    len(out) + 1,
                    name,
                    str(row[3]) or (tail[1] if tail else ""),
                    [],
                    electors=int(row[9]),
                    polled=int(row[21]),
                    stations=int(row[10]) if isinstance(row[10], float) else None,
                    full=True,
                )
            )
        if out and row[7] and isinstance(row[23], float):
            out[-1].candidates.append(Candidate(clean(str(row[7])), party_of(str(row[8])), int(row[23])))
    for ward in out:
        ward.valid = sum(c.votes for c in ward.candidates)
    return out


# ---------- 2012: winner and runner-up report ----------

Line = list[pdfwords.Word]
CORPORATION = {"NORTH": "NORTH", "SOUTH": "SOUTH", "EAST": "EAST"}


def cells(rows: list[Line], edges: Sequence[float]) -> list[str]:
    """Join the words of a ward's lines by the column each starts in, top to bottom."""
    out: list[list[str]] = [[] for _ in edges]
    for row in rows:
        for word in row:
            out[max(i for i, edge in enumerate(edges) if word.x >= edge - 1.5 or i == 0)].append(word.text)
    return [clean(" ".join(c)) for c in out]


def ward_blocks(file: Path, is_head, corporation=True) -> list[tuple[str, Line, list[Line]]]:
    """Group a report's lines into wards: each starts at a line `is_head` accepts and runs to the next one."""
    blocks: list[tuple[str, Line, list[Line]]] = []
    corp = ""
    for line in pdfwords.stream(file):
        text = " ".join(w.text for w in line)
        found = re.search(r"\b(NORTH|SOUTH|EAST) DELHI MUNICIPAL CORPORATION", text)
        if found:
            corp = found[1]
        elif is_head(line):
            blocks.append((corp, line, [line]))
        elif blocks and not re.match(r"^(General Election|Ward Wise|Ward No|Candidate|State Election)", text):
            blocks[-1][2].append(line)
    return blocks


@functools.cache
def ward_2012() -> list[Ward]:
    def head(line: Line) -> bool:
        return len(line) > 2 and line[0].x < 46 and line[0].text.isdigit() and line[1].text == "-"

    edges = [0, 170, 290, 440, 472, 575, 725, 765]
    out: list[Ward] = []
    for corp, _, lines in ward_blocks(path(R2012), head):
        ward, winner, party, votes, runner, runner_party, runner_votes, margin = cells(lines, edges)
        found = re.fullmatch(r"(\d+) - (.*?) \(([A-Z]+)\)", ward)
        if not found:
            raise ValueError(f"2012 ward {ward!r}")
        out.append(
            Ward(
                2012,
                corp,
                int(found[1]),
                found[2],
                found[3],
                [
                    Candidate(winner, party_of(party), int(votes)),
                    Candidate(runner, party_of(runner_party), int(runner_votes)),
                ],
            )
        )
        if int(votes) - int(runner_votes) != int(margin):
            raise ValueError(f"2012 ward {found[1]}: margin {margin} is not {votes} less {runner_votes}")
    return out


# ---------- 2017 and 2022: reports printed from the Commission's result portals ----------

SKIP = re.compile(
    r"^(https?:|SEC-MCD|RESULTSUMMARY|DateTime|StateElectionCommission|GeneralElection|\d+/\d+/\d+,|FORM18|"
    r"Sl\.No|Numberof|Totalnumber|WardNo\.andName|S\.No|NoWard|DetailResult|PartyName|Status|\d+/\d+$|\d+of\d+|\d\d/\d\d/\d{4})"
)


def page_lines(file: Path) -> list[tuple[int, list[tuple[float, float, str]]]]:
    """A PDF's text as lines of characters with their x extents, grouped by height and read top to bottom."""
    out: list[tuple[int, list[tuple[float, float, str]]]] = []
    for number, page in enumerate(pymupdf.open(file).pages(), start=1):
        found: list[tuple[float, float, float, str]] = []
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    for char in span["chars"]:
                        if char["c"].strip():
                            box = char["bbox"]
                            found.append(((box[1] + box[3]) / 2, box[0], box[2], char["c"]))
        found.sort()
        rows: list[list[tuple[float, float, float, str]]] = []
        for char in found:
            if rows and char[0] - rows[-1][0][0] <= 2.5:
                rows[-1].append(char)
            else:
                rows.append([char])
        for row in rows:
            out.append((number, sorted((x0, x1, c) for _, x0, x1, c in row)))
    return out


def split(line: list[tuple[float, float, str]], edges: Sequence[float]) -> list[str]:
    """Cut a line of characters at column edges, putting a space where a gap opens inside a column."""
    cols: list[str] = ["" for _ in edges]
    last: list[float | None] = [None for _ in edges]
    for x0, x1, char in line:
        i = max(i for i, edge in enumerate(edges) if x0 >= edge - 0.5 or i == 0)
        if last[i] is not None and x0 - (last[i] or 0) > 1.6:
            cols[i] += " "
        cols[i] += char
        last[i] = x1
    return cols


def portal_wards(file: Path, edges: Sequence[float], head: re.Pattern[str], ward_col: int) -> list[list[str]]:
    """Each ward of a portal report as its columns' text, a ward running from its numbered line to the next."""
    wards: list[list[str]] = []
    for _, line in page_lines(file):
        cols = split(line, edges)
        flat = "".join(char for _, _, char in line)
        if head.match(cols[ward_col].strip()) and re.fullmatch(r"\d+", cols[0].strip()):
            wards.append(cols)
        elif wards and not SKIP.match(flat):
            wards[-1] = [clean(f"{a} {b}") for a, b in zip(wards[-1], cols, strict=True)]
    return wards


def runner_split(cell: str) -> tuple[int, str]:
    found = re.fullmatch(r"(\d+)\s*(.*)", cell.replace(",", ""))
    if not found:
        raise ValueError(f"no votes in {cell!r}")
    return int(found[1]), clean(found[2])


@functools.cache
def ward_2022() -> list[Ward]:
    edges = [0, 83, 165, 208, 275, 340, 436, 500, 530]
    out: list[Ward] = []
    for cols in portal_wards(path(R2022), edges, re.compile(r"^\(\d+\)"), 1):
        serial, ward, reservation, winner, party, mixed, runner_party, runner_votes, margin = (clean(c) for c in cols)
        found = re.fullmatch(r"\((\d+)\) (.*)", ward)
        if not found or int(found[1]) != int(serial):
            raise ValueError(f"2022 ward {ward!r} at {serial}")
        votes, runner = runner_split(mixed)
        out.append(
            Ward(
                2022,
                "MCD",
                int(serial),
                found[2],
                reservation,
                [
                    Candidate(winner, party_of(party), votes),
                    Candidate(runner, party_of(runner_party), int(runner_votes)),
                ],
            )
        )
        if votes - int(runner_votes) != int(margin):
            raise ValueError(f"2022 ward {serial}: margin {margin} is not {votes} less {runner_votes}")
    return out


def form_18(corporation: str) -> dict[int, tuple[str, list[tuple[str, int]], int]]:
    """Form 18 of a 2017 corporation: each ward's name, every candidate's votes in printed order, and the valid votes."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(path(R2017[corporation][1])), "-"], capture_output=True, text=True, check=True
    ).stdout.replace("\u00ad", "-")
    wards: dict[int, tuple[str, list[tuple[str, int]], int]] = {}
    current: tuple[int, str, list[tuple[str, int]]] | None = None
    for line in text.splitlines():
        line = line.rstrip()
        head = re.search(r"Ward No\. and Name\s*:\s*(\d{3})-([NSE])\s+(.*\S)", line)
        total = re.search(r"Total number of valid votes Polled in Ward No\s+(\d{3})-([NSE])\s+(\d+)", line)
        row = re.match(r"^\s+(\d+)\s{2,}(\S.*?)\s{2,}(\d+)$", line)
        if head:
            if current:
                raise ValueError(f"ward {current[0]} has no total")
            current = (int(head[1]), clean(head[3]), [])
        elif total and current:
            if int(total[1]) != current[0]:
                raise ValueError(f"total of ward {total[1]} closes ward {current[0]}")
            wards[current[0]] = (current[1], current[2], int(total[3]))
            current = None
        elif row and current:
            if int(row[1]) != len(current[2]) + 1:
                raise ValueError(f"ward {current[0]}: candidate {row[1]} follows {len(current[2])}")
            current[2].append((clean(row[2]), int(row[3])))
    return wards


def letters(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text)


def resolve_party(known: set[str], *texts: str) -> str:
    """A party's full name from a cell that may have lost or gained letters where two columns' text overlaps."""
    stripped = {letters(name): name for name in known}
    for text in texts:
        if letters(text) in stripped:
            return stripped[letters(text)]
    for text in texts:
        ranked = sorted(
            ((difflib.SequenceMatcher(None, letters(text), key).ratio(), name) for key, name in stripped.items()),
            reverse=True,
        )
        if len(letters(text)) >= 8 and ranked[0][0] >= 0.85 and ranked[0][0] - ranked[1][0] >= 0.1:
            return ranked[0][1]
    raise ValueError(f"no party for {texts}")


@functools.cache
def ward_2017() -> list[Ward]:
    edges = [0, 70, 93, 177, 249, 328, 417, 490, 530]
    known = {c.party for w in ward_2012() + ward_2022() for c in w.candidates} - {"INDEPENDENT"}
    out: list[Ward] = []
    for corp, (summary, _) in R2017.items():
        listed = form_18(corp)
        for cols in portal_wards(path(summary), edges, re.compile(r"^\(\d{3}-[NSE]\)"), 2):
            _, _, ward, winner, party, _mixed, runner_party, runner_votes, margin = (clean(c) for c in cols)
            found = re.fullmatch(r"\((\d{3})-([NSE])\) (.*)", ward)
            if not found:
                raise ValueError(f"2017 ward {ward!r}")
            number = int(found[1])
            _, entries, valid = listed[number]
            tail = re.sub(r"\D", "", f"{runner_party}{runner_votes}{margin}")
            lead, second = entries[0][1], entries[1][1]
            if lead < second or tail != f"{second}{lead - second}":
                raise ValueError(f"2017 {corp} ward {number}: the summary and Form 18 disagree")
            texts = [party, f"{winner} {party}"]
            parties = {
                0: "INDEPENDENT" if letters(party) == "INDEPENDENT" else resolve_party(known, *texts),
                1: "INDEPENDENT" if letters(runner_party) == "INDEPENDENT" else resolve_party(known, runner_party),
            }
            out.append(
                Ward(
                    2017,
                    corp,
                    number,
                    clean(found[3]),
                    "",
                    [Candidate(n, parties.get(i, ""), v) for i, (n, v) in enumerate(entries)],
                    valid=valid,
                    full=True,
                )
            )
    return out


# ---------- packages ----------

INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIND"))
HOME = "https://sec.delhi.gov.in/"
ELECTIONS = {
    2007: (date(2007, 4, 1), date(2007, 4, 30), "month"),
    2012: (date(2012, 4, 15), date(2012, 4, 15), "day"),
    2017: (date(2017, 4, 23), date(2017, 4, 23), "day"),
    2022: (date(2022, 12, 4), date(2022, 12, 4), "day"),
}
CORPORATIONS = {
    "MCD": "Municipal Corporation of Delhi",
    "NORTH": "North Delhi Municipal Corporation",
    "SOUTH": "South Delhi Municipal Corporation",
    "EAST": "East Delhi Municipal Corporation",
}
ALIASES = {"LOK JANSHAKTI PARTY": "LOK JAN SHAKTI PARTY"}
ACCEPTED_2007 = {
    "accounting.votes_valid": "Ward 99, Moti Nagar, prints 19,177 votes polled in the 2007 spreadsheet while its "
    "candidates' votes add to 19,157; both are stored as printed and the ward's note says so"
}
METHOD = {
    2007: "Counted by the State Election Commission for each ward, as its detailed result spreadsheet prints every "
    "candidate's votes. Registered voters are the electors of the ward and valid votes are the votes polled the "
    "spreadsheet prints for the ward.",
    2012: "Reported by the State Election Commission for each ward as the winner's and the runner-up's votes. The "
    "report does not print the other candidates, so the contest holds two options, the ward's turnout is not "
    "stored, and the winner is stored as elected.",
    2017: "Counted by the State Election Commission for each ward, as its Form 18 prints every candidate's votes, "
    "and its result summary prints the party of the winner and the runner-up. The other candidates' parties are "
    "not published in those reports, so they are stored without one. Valid votes are the Form 18 total for the "
    "ward, which includes none of the above.",
    2022: "Reported by the State Election Commission for each ward as the winner's and the runner-up's votes. The "
    "report does not print the other candidates, so the contest holds two options, the ward's turnout is not "
    "stored, and the winner is stored as elected.",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SRC = key("source", SOURCE)
SYSTEM = key("electoral_system", "plurality")


def norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper().replace("SARSWATI", "SARASWATI"))


@functools.cache
def plan_2007() -> dict[int, int]:
    """Each 2012 ward number's number in 2007: the delimitation is the same, but the numbering order changed."""
    old, new = ward_2007(), ward_2012()
    by_name: dict[str, list[Ward]] = {}
    for w in new:
        by_name.setdefault(norm(w.name), []).append(w)
    out: dict[int, int] = {}
    left: list[Ward] = []
    for w in old:
        found = by_name.get(norm(w.name), [])
        if len(found) == 1:
            out[found[0].number] = w.number
        else:
            left.append(w)
    rest = [w for w in new if w.number not in out]
    for a, b in zip(sorted(left, key=lambda w: w.number), sorted(rest, key=lambda w: w.number), strict=True):
        if difflib.SequenceMatcher(None, norm(a.name), norm(b.name)).ratio() < 0.7:
            raise ValueError(f"2007 ward {a.name} does not pair with 2012 ward {b.name}")
        out[b.number] = a.number
    if sorted(out.values()) != list(range(1, 273)):
        raise ValueError("2007 and 2012 wards do not pair one to one")
    return out


def ward_code(w: Ward) -> str:
    """The code of a ward's area, shared by the elections that used the same delimitation."""
    if w.year in (2007, 2012):
        return f"2007-{(w.number if w.year == 2007 else plan_2007()[w.number]):03d}"
    if w.year == 2017:
        return f"2016-{w.corporation[0]}{w.number:03d}"
    return f"2022-{w.number:03d}"


def ward_area(w: Ward) -> str:
    return key("area", "in.mcd_ward", ward_code(w))


def corporation_area(code: str) -> str:
    return key("area", "in.mcd_corporation", code)


def party_name(name: str) -> str | None:
    name = ALIASES.get(name, name)
    return None if name in {"", "INDEPENDENT"} else name


def party_id(name: str) -> str:
    return key("party", name)


def all_wards() -> list[Ward]:
    return [*ward_2007(), *ward_2012(), *ward_2017(), *ward_2022()]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in sorted(ELECTIONS, reverse=True))]


def parse() -> None:
    geo().write()
    registry().write()
    for year in ELECTIONS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The wards of the Municipal Corporation of Delhi and its North, South, and East corporations, by delimitation",
    )
    p.external |= {INDIA, COUNTRY}
    p.add(
        "source",
        id=SRC,
        kind="official_body",
        publisher="State Election Commission, NCT of Delhi",
        title="Municipal Corporation of Delhi general election results",
        url=HOME,
        license="No license stated; published by the electoral authority for public use",
        attribution="State Election Commission, NCT of Delhi",
    )
    p.add(
        "scheme",
        code="in.mcd_corporation",
        polity_id=INDIA,
        kind="administrative",
        rank=2,
        noun="corporation",
        code_system=None,
    )
    p.add(
        "scheme", code="in.mcd_ward", polity_id=INDIA, kind="electoral_district", rank=3, noun="ward", code_system=None
    )
    for code, name in CORPORATIONS.items():
        id = corporation_area(code)
        p.add("area", id=id, scheme_code="in.mcd_corporation")
        p.add("area_code", area_id=id, code=code.lower())
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
    seen: set[str] = set()
    corp_2007 = {plan_2007()[w.number]: w.corporation for w in ward_2012()}
    for w in all_wards():
        code = ward_code(w)
        if code in seen:
            continue
        seen.add(code)
        id = ward_area(w)
        p.add("area", id=id, scheme_code="in.mcd_ward")
        p.add("area_code", area_id=id, code=code)
        parent = "MCD" if w.year == 2022 else (corp_2007[w.number] if w.year == 2007 else w.corporation)
        p.add("area_parent", area_id=id, parent_id=corporation_area(parent))
        p.name(id, w.name.title(), "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils of the Delhi municipal corporations, their electoral system, and the parties that contested",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {INDIA}
    for code, name in CORPORATIONS.items():
        id = key("body", code)
        p.add("body", id=id, polity_id=INDIA, kind="council")
        p.name(id, f"{name} council", "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=INDIA,
        valid_from=date(1997, 1, 1),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="One councillor for each ward, elected by the most votes.",
        lang="en",
    )
    names = sorted({n for w in all_wards() for c in w.candidates if (n := party_name(c.party))})
    for name in names:
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=INDIA)
        p.name(id, name.title().replace(" Of ", " of "), "en", "official")
    return p


def _value(p: Package, rs: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=rs,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def election(year: int) -> Package:
    first, last, precision = ELECTIONS[year]
    wards = {2007: ward_2007, 2012: ward_2012, 2017: ward_2017, 2022: ward_2022}[year]()
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} general election to the Delhi municipal corporations, by ward",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {INDIA, COUNTRY}
    files = {
        2007: [R2007[1]],
        2012: [R2012],
        2017: [f for pair in R2017.values() for f in pair],
        2022: [R2022],
    }[year]
    for name in files:
        p.artifact(raw.cached(SOURCE, url(name)), SRC)
    if year == 2007:
        for code, reason in ACCEPTED_2007.items():
            p.accept(code, reason)
    election_id = p.id("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=INDIA,
        kind="general",
        date_from=first,
        date_to=last,
        date_precision=precision,
        status="held",
    )
    p.add("slug", path=f"in/{year}-delhi-municipal-corporation", entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} Delhi municipal corporation general election", "en", "official")
    for w in wards:
        tag = f"{w.corporation}-{w.number}"
        area = ward_area(w)
        body = key("body", w.corporation)
        unit = p.id("unit", year, tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=INDIA,
            scheme_code="in.mcd_ward",
            code=f"{year}:{ward_code(w)}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        contest = p.id("contest", year, tag)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=body,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} council election, {w.name.title()} ward", "en", "official")
        p.add(
            "slug",
            path=f"in/{year}-delhi-municipal-corporation/{ward_code(w)}-{changeset.slug(w.name)}",
            entity_id=contest,
            is_current=True,
        )
        rs = p.id("result_set", year, tag)
        p.add(
            "result_set",
            id=rs,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SRC,
            status="active",
            is_default=True,
            completeness=1.0 if w.full else None,
        )
        note = METHOD[year]
        if year == 2007 and w.polled != w.valid:
            note += f" The spreadsheet prints {w.polled:,} votes polled here while the candidates' votes add to {w.valid:,}."
        p.add("note", id=p.id("note", rs), entity_id=rs, kind="methodology", body_md=note, lang="en")
        top = max(c.votes for c in w.candidates)
        for index, c in enumerate(w.candidates, start=1):
            option = p.id("option", year, tag, index)
            person = p.id("person", year, tag, index)
            party = party_name(c.party)
            solo = c.party == "INDEPENDENT"
            nota = c.name == "NOTA"
            p.add("person", id=person) if not nota else None
            if nota:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="none_of_the_above",
                    ballot_position=index,
                    ballot_label="NOTA",
                )
            else:
                p.name(person, c.name, "en", "official")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id(party) if party else None,
                    ballot_position=index,
                    ballot_label=c.name,
                )
                p.add(
                    "candidacy",
                    id=p.id("candidacy", year, tag, index),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=c.name,
                    member_of_party_id=party_id(party) if party else None,
                    is_independent=True if solo else None,
                )
                if c.votes == top:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="elected", seats=1)
            _value(p, rs, unit, option, 1, c.votes)
        if w.electors is not None:
            _value(p, rs, unit, None, 2, w.electors)
        if w.full:
            _value(p, rs, unit, None, 4, w.polled if year == 2007 and w.polled is not None else int(w.valid or 0))
    return p
