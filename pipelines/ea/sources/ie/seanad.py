"""Seanad Éireann general elections: the five vocational panels, count by count.

The Houses of the Oireachtas publish a results handbook after each Seanad general election. For each panel it prints
the candidates in alphabetical order with their number of votes, their value (each vote counts 1,000 so that
surplus transfers can be split in fractions), and the running value after every count. The parser reads the layout
text of the handbook, and every panel has to tie: the candidates, the non-transferable value, and the loss owing to
disregard of fractions add up to the value of the valid votes after every count, and as many candidates as there
are seats keep a value at the last count.

Votes are stored in whole votes, so a value of 82,166 is 82.166 votes. The package for an election holds the
election, one contest and one result set per panel, and a result per count.
"""

import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import uuid5

from ea import package, raw
from ea.package import NAMESPACE, Package

SOURCE = "ie.seanad"
HOST = "https://data.oireachtas.ie/ie/oireachtas/electoralProcess/electionResults/seanad"
# Ireland's record predates derived country ids, so the archive keeps the id it was first given
IRELAND = "01a13bd3-643c-74a1-9302-0a8df9ef9df8"
# The body Seanad Éireann, which ie.byelections created
SEANAD = "029ddab0-50cc-5934-84c7-5769974e033a"
LICENSE = "Oireachtas (Open Data) PSI Licence"
LICENSE_URL = "https://www.oireachtas.ie/en/open-data/license/"
SCHEME = "ie.seanad_panel"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED, QUOTA = 1, 2, 3, 4, 5, 21, 26
PER_VOTE = 1000

PANELS = {
    "cultural-and-educational": ("Cultural and Educational Panel", "CULTURAL AND EDUCATIONAL PANEL", 5),
    "agricultural": ("Agricultural Panel", "AGRICULTURAL PANEL", 11),
    "labour": ("Labour Panel", "LABOUR PANEL", 11),
    "industrial-and-commercial": ("Industrial and Commercial Panel", "INDUSTRIAL AND COMMERCIAL PANEL", 9),
    "administrative": ("Administrative Panel", "ADMINISTRATIVE PANEL", 7),
}
ORDINALS = [
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Eleventh",
    "Twelfth",
    "Thirteenth",
    "Fourteenth",
    "Fifteenth",
    "Sixteenth",
    "Seventeenth",
    "Eighteenth",
    "Nineteenth",
    "Twentieth",
    "Twenty-first",
    "Twenty-second",
    "Twenty-third",
    "Twenty-fourth",
    "Twenty-fifth",
    "Twenty-sixth",
    "Twenty-seventh",
    "Twenty-eighth",
    "Twenty-ninth",
    "Thirtieth",
    "Thirty-first",
    "Thirty-second",
    "Thirty-third",
    "Thirty-fourth",
    "Thirty-fifth",
    "Thirty-sixth",
    "Thirty-seventh",
    "Thirty-eighth",
    "Thirty-ninth",
    "Fortieth",
]


@dataclass(frozen=True)
class Handbook:
    year: int
    url: str
    title: str
    panels_close: date
    first_poll: date
    count_start: date
    count_end: date
    electorate: int
    poll: int


HANDBOOKS = {
    2025: Handbook(
        2025,
        f"{HOST}/2026/2026-05-20_seanad-general-election-2025-results-handbook_en.pdf",
        "Seanad General Election, January 2025, and bye-elections to the 2020-2025 Seanad: results handbook",
        date(2025, 1, 30),
        date(2025, 1, 29),
        date(2025, 1, 31),
        date(2025, 2, 3),
        1171,
        1164,
    ),
}


@dataclass
class Candidate:
    surname: str
    forename: str
    placing: int
    values: list[int] = field(default_factory=list)
    sub_panel: str | None = None

    @property
    def name(self) -> str:
        return f"{self.forename} {self.surname}"


@dataclass
class Panel:
    key: str
    valid_votes: int
    value: int
    quota: int
    seats: int
    candidates: list[Candidate]
    non_transferable: list[int]
    elected: list[int]
    counts: int


CELL = re.compile(r"[+-]?\d[\d,]*|–")
CELLS = re.compile(rf"^\s*(?:{CELL.pattern})(?:\s+(?:{CELL.pattern}))*\s*$")
NAMED = re.compile(r"^\s*(?P<name>[^\d(].*?)\s+\((?P<placing>\d+)\)\s+(?P<rest>\S.*?)\s*$")
FILLERS = {
    "Non-transferable value not effective": "non_transferable",
    "Loss of value owing to disregard of fractions": "fractions",
    "Total": "total",
}


FILLER_ORDER = ("non_transferable", "fractions", "total")


def text(artifact: raw.Artifact) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(artifact.path), "-"], check=True, capture_output=True, text=True
    ).stdout


def number(cell: str) -> int:
    return 0 if cell == "–" else int(cell.replace(",", ""))


def cells(text: str) -> list[int]:
    return [number(cell) for cell in CELL.findall(text)]


def header_counts(line: str) -> list[int] | None:
    words = line.split()
    if not words or any(word not in ORDINALS for word in words):
        return None
    return [ORDINALS.index(word) + 1 for word in words]


def panel_text(document: str, label: str) -> list[str]:
    """The lines of one panel's results, from its figures to the start of the next section."""
    lines = document.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip().startswith("Number of Valid Votes")]
    for position, start in enumerate(starts):
        heading = " ".join(lines[max(0, start - 6) : start]).upper()
        if label not in heading:
            continue
        end = starts[position + 1] - 6 if position + 1 < len(starts) else len(lines)
        for i in range(start, end):
            if lines[i].strip().startswith(("UNIVERSITY CONSTITUENCIES", "NATIONAL UNIVERSITY OF IRELAND")):
                end = i
                break
        return lines[start:end]
    raise ValueError(f"no results for {label}")


def fold(surname: str, forename: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", f"{surname}{forename}") if ch.isalpha()).casefold()


def elected_members(document: str) -> dict[str, set[str]]:
    """The panel members of the new Seanad, from the handbook's two-column list of its members."""
    lines = document.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "MEMBERS OF THE 27th SEANAD")
    end = next(i for i in range(start, len(lines)) if lines[i].strip().startswith("Industrial and Commercial Panel"))
    end = next(i for i in range(end, len(lines)) if not lines[i].strip())
    found: dict[str, set[str]] = {key: set() for key in PANELS}
    titles = {title: key for key, (title, _, _) in PANELS.items()}
    current: dict[str, str | None] = {"left": None, "right": None}
    split = 40
    for line in lines[start + 1 : end + 12]:
        for side, part in (("left", line[:split]), ("right", line[split:])):
            part = part.strip()
            if part in titles:
                current[side] = titles[part]
            elif part.endswith(("Panel", "Dublin", "Ireland", "Nominees")) or not part:
                if part and part not in titles:
                    current[side] = None
            elif current[side] and "," in part:
                surname, _, forename = part.partition(",")
                found[current[side] or ""].add(fold(surname, forename))
    return found


def figure(lines: list[str], label: str) -> int:
    for line in lines[:24]:
        if line.strip().startswith(label):
            return number(line.split()[-1])
    raise ValueError(f"no {label}")


def parse_panel(document: str, key: str) -> Panel:
    name, label, seats = PANELS[key]
    members = elected_members(document)[key]
    lines = panel_text(document, label)
    valid_votes = figure(lines, "Number of Valid Votes")
    value = figure(lines, "Value of Valid Votes")
    quota = figure(lines, "Quota")
    if figure(lines, "Number of members to be elected") != seats:
        raise ValueError(f"{name}: seats")
    candidates: list[Candidate] = []
    rows: dict[str, dict[int, int]] = {"non_transferable": {}, "fractions": {}, "total": {}}
    counts: list[int] | None = None
    first = True
    cursor = 0
    done = True

    def take(last: str) -> None:
        nonlocal cursor, done, first
        assert counts is not None
        stripped = last.strip()
        filler = next((v for k, v in FILLERS.items() if stripped.startswith(k)), None)
        if filler is not None:
            label_text = next(k for k in FILLERS if stripped.startswith(k))
            found = cells(stripped[len(label_text) :])
            width = len(counts) + (1 if first else 0)
            if len(found) != width:
                raise ValueError(f"{name}: {len(found)} cells in {last!r}, expected {width}")
            for count, amount in zip(counts, found[1:] if first else found, strict=True):
                rows[filler][count] = amount
            if filler == "total":
                done = True
                first = False
            return
        named = NAMED.match(last)
        if named and first:
            found = cells(named["rest"])
            if len(found) != len(counts) + 1:
                raise ValueError(f"{name}: {len(found)} cells in {last!r}")
            surname, _, forename = named["name"].partition(",")
            candidate = Candidate(surname.strip(), forename.strip(), int(named["placing"]))
            candidate.values = [found[1], *found[2:]]
            candidates.append(candidate)
            return
        found = cells(last)
        if len(found) != len(counts):
            raise ValueError(f"{name}: {len(found)} cells in {last!r}, expected {len(counts)}")
        if cursor < len(candidates):
            candidates[cursor].values.extend(found)
        elif cursor < len(candidates) + len(FILLER_ORDER):
            filler = FILLER_ORDER[cursor - len(candidates)]
            for count, amount in zip(counts, found, strict=True):
                rows[filler][count] = amount
            if filler == "total":
                done = True
        else:
            raise ValueError(f"{name}: more rows than candidates at {last!r}")
        cursor += 1

    for index, line in enumerate(lines):
        found = header_counts(line)
        if found is not None and done:
            counts, done, cursor = found, False, 0
            continue
        if done or counts is None or not line.strip():
            continue
        if CELLS.match(line) and all(cell[0] in "+-" for cell in CELL.findall(line)):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if following.strip():
                continue  # a line of transfers, which sits right above the row it changes
        stripped = line.strip()
        if CELLS.match(line) or any(stripped.startswith(k) for k in FILLERS) or (first and NAMED.match(line)):
            take(line)
    if not done or first:
        raise ValueError(f"{name}: the last table has no total")
    return finish(key, valid_votes, value, quota, seats, candidates, rows, members)


def finish(
    key: str,
    valid_votes: int,
    value: int,
    quota: int,
    seats: int,
    candidates: list[Candidate],
    rows: dict[str, dict[int, int]],
    members: set[str],
) -> Panel:
    name = PANELS[key][0]
    counts = len(rows["total"])
    if sorted(rows["total"]) != list(range(1, counts + 1)):
        raise ValueError(f"{name}: counts {sorted(rows['total'])}")
    if value != valid_votes * PER_VOTE:
        raise ValueError(f"{name}: value {value} of {valid_votes} votes")
    for candidate in candidates:
        if len(candidate.values) != counts:
            raise ValueError(f"{name}: {candidate.name} has {len(candidate.values)} of {counts} counts")
        # A negative cell is the value a candidate hands on in that count; the candidate holds nothing afterward
        candidate.values = [max(0, amount) for amount in candidate.values]
    loss: list[int] = []
    for count in range(1, counts + 1):
        held = sum(candidate.values[count - 1] for candidate in candidates)
        transferable = rows["non_transferable"][count] + rows["fractions"][count]
        if rows["total"][count] != value or held + transferable != value:
            raise ValueError(f"{name}: count {count} holds {held + transferable} of {value}")
        loss.append(transferable)
    elected = [i for i, candidate in enumerate(candidates) if fold(candidate.surname, candidate.forename) in members]
    if len(elected) != seats or {fold(c.surname, c.forename) for c in candidates} < members:
        raise ValueError(
            f"{name}: {len(elected)} of {len(members)} members are among the candidates, for {seats} seats"
        )
    for i in elected:
        if candidates[i].values[-1] == 0:
            raise ValueError(f"{name}: {candidates[i].name} is a member who holds nothing at the last count")
    return Panel(key, valid_votes, value, quota, seats, candidates, loss, elected, counts)


@dataclass(frozen=True)
class Summary:
    electorate: int
    poll: int
    rejected: dict[str, int]
    valid: dict[str, int]
    quota: dict[str, int]


def summary(document: str) -> Summary:
    """The statistical summary of the vocational panels: electorate, poll, and papers rejected for each panel."""
    lines = document.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "VOCATIONAL PANELS")
    end = next(i for i in range(start, len(lines)) if "UNIVERSITY CONSTITUENCIES" in lines[i])
    body = lines[start:end]
    electorate = poll = 0
    rejected: dict[str, int] = {}
    valid: dict[str, int] = {}
    quota: dict[str, int] = {}
    section = ""
    for line in body:
        stripped = line.strip()
        if stripped.startswith("Electorate"):
            electorate = number(stripped.split()[-1])
        elif stripped.startswith("Total Poll"):
            poll = number(stripped.split()[-1])
        elif stripped.startswith("BALLOT PAPERS REJECTED"):
            section = "rejected"
        elif stripped.startswith("Quota"):
            section = "quota"
        for key, (title, _, _) in PANELS.items():
            if stripped.startswith(title):
                found = cells(stripped[len(title) :])
                if section == "rejected" and len(found) == 1:
                    rejected[key] = found[0]
                elif section == "quota" and len(found) == 2:
                    quota[key], valid[key] = found
    if sorted(rejected) != sorted(PANELS) or sorted(valid) != sorted(PANELS):
        raise ValueError(f"the summary lacks panels: {rejected} {valid}")
    return Summary(electorate, poll, rejected, valid, quota)


def apostrophes(text: str) -> str:
    return text.replace("’", "'")


def proper_names(document: str, panels: dict[str, Panel]) -> None:
    """Give each candidate the surname as the panel list prints it in mixed case (the results print it in capitals)."""
    lines = document.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "PANELS OF CANDIDATES")
    titles = {label: key for key, (_, label, _) in PANELS.items()}
    listed: dict[str, list[tuple[str, str | None]]] = {key: [] for key in PANELS}
    current: str | None = None
    for line in lines[start:]:
        stripped = line.strip()
        if stripped in titles:
            current = titles[stripped]
            continue
        if stripped.startswith(("RULES FOR THE COUNTING", "BYE-ELECTIONS")):
            break
        match = re.match(r"^ {3,8}(?P<surname>[^\s,][^,]*?),(?:\s(?P<forename>\S.*?))?\s{2,}\S", line)
        if current and match:
            listed[current].append((apostrophes(match["surname"]), match["forename"]))
    for key, panel in panels.items():
        for candidate in panel.candidates:
            options = [
                surname
                for surname, forename in listed[key]
                if fold(surname, "") == fold(candidate.surname, "")
                and (forename is None or fold(forename, "")[:4] == fold(candidate.forename, "")[:4])
            ]
            if len(options) != 1:
                raise ValueError(f"{panel.key}: {candidate.surname}, {candidate.forename} is listed {options}")
            candidate.surname = options[0]


def parse_handbook(year: int) -> tuple[Summary, dict[str, Panel]]:
    handbook = HANDBOOKS[year]
    document = text(raw.cached(SOURCE, handbook.url))
    figures = summary(document)
    panels = {key: parse_panel(document, key) for key in PANELS}
    proper_names(document, panels)
    for key, panel in panels.items():
        if figures.valid[key] != panel.valid_votes or figures.quota[key] != panel.quota:
            raise ValueError(f"{key}: the summary disagrees with the panel's own figures")
        # The covering envelopes that were spoiled take papers out of the poll before the panels see them
        if handbook.poll - figures.rejected[key] < panel.valid_votes:
            raise ValueError(f"{key}: more valid votes than papers")
    if (figures.electorate, figures.poll) != (handbook.electorate, handbook.poll):
        raise ValueError("the summary disagrees with the handbook's electorate and poll")
    return figures, panels


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SYSTEM = key("electoral_system", "panels")
METHOD = (
    "Each of the five panels elects its senators by the single transferable vote. The electorate is the members of "
    "the newly elected Dáil, the outgoing Seanad, and the county and city councillors, who vote by post, with one "
    "vote on each panel. Every vote counts 1,000 so that a surplus can be split in fractions, and the count "
    "disregards the fractions of a value. The panel's candidates stand on two sub-panels, one nominated by "
    "nominating bodies and one by members of the Oireachtas, and each sub-panel is guaranteed a minimum number of "
    "seats, so a candidate with more value can be passed over when that minimum has to be met."
)


def unit(panel: str) -> str:
    return key("reporting_unit", panel)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/panels-{year}" for year in HANDBOOKS)]


def fetch() -> None:
    for handbook in HANDBOOKS.values():
        raw.fetch(SOURCE, handbook.url, suffix=".pdf")


def parse() -> None:
    geo().write()
    registry().write()
    for year in HANDBOOKS:
        panels_package(year).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The reporting units of the Seanad panels: each panel is counted as one electorate with no territory",
    )
    p.external.add(IRELAND)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=IRELAND,
        kind="synthetic",
        rank=1,
        noun="Seanad panel",
        code_system=None,
    )
    for panel, (title, _, _) in PANELS.items():
        id = unit(panel)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=IRELAND,
            scheme_code=SCHEME,
            code=panel,
            area_relation="unlocated",
            area_id=None,
            kind="aggregate",
        )
        p.name(id, title, "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The results handbooks of the Seanad general elections and the electoral system of the vocational panels",
    )
    p.external.add(IRELAND)
    for year, handbook in HANDBOOKS.items():
        p.add(
            "source",
            id=key("source", "handbook", year),
            kind="official_body",
            publisher="Houses of the Oireachtas",
            title=handbook.title,
            url=handbook.url,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution="Houses of the Oireachtas",
        )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=IRELAND,
        valid_from=date(1937, 12, 29),
        valid_to=None,
        rules={"formula": "stv", "quota": "droop", "surplus": "gregory"},
        rules_version=1,
    )
    p.name(SYSTEM, "Single transferable vote on the Seanad vocational panels", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=METHOD,
        lang="en",
    )
    return p


def decimal(value: int) -> Decimal:
    return Decimal(value) / PER_VOTE


def panels_package(year: int) -> Package:
    handbook = HANDBOOKS[year]
    figures, panels = parse_handbook(year)
    day = handbook.panels_close
    p = Package(
        f"{SOURCE}/panels-{year}",
        SOURCE,
        f"The {year} Seanad general election on the five vocational panels, count by count",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {IRELAND, SEANAD, SCHEME}
    source = key("source", "handbook", year)
    p.artifact(raw.cached(SOURCE, handbook.url), source)
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=IRELAND,
        kind="general",
        date_from=handbook.first_poll,
        date_to=day,
        status="held",
    )
    p.name(election, f"{year} Seanad general election", "en", "official")
    p.add("external_id", entity_id=election, scheme=f"{SOURCE}.election", value=str(year))
    base = f"ie/{day}-seanad-general"
    p.add("slug", path=base, entity_id=election, is_current=True)
    p.add(
        "note",
        id=key("note", election),
        entity_id=election,
        kind="context",
        body_md=(
            f"The polls for the university constituencies closed on {handbook.first_poll:%-d %B %Y} and the polls for "
            f"the five vocational panels on {day:%-d %B %Y}. The panel counts ran from {handbook.count_start:%-d %B} "
            f"to {handbook.count_end:%-d %B %Y}."
        ),
        lang="en",
    )
    for panel, (title, _, seats) in PANELS.items():
        data = panels[panel]
        contest = key("contest", year, panel)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=SEANAD,
            district_area_id=None,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Seanad election, {title}", "en", "official")
        p.add("slug", path=f"{base}/{panel}", entity_id=contest, is_current=True)
        p.add("external_id", entity_id=contest, scheme=f"{SOURCE}.contest", value=f"{year}:{panel}")
        p.add("note", id=key("note", contest), entity_id=contest, kind="methodology", body_md=METHOD, lang="en")
        result_set = key("result_set", year, panel)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1,
        )
        place = unit(panel)
        invalid = figures.poll - data.valid_votes
        accounting = (
            (REGISTERED, figures.electorate),
            (BALLOTS, figures.poll),
            (VALID, data.valid_votes),
            (INVALID, invalid),
            (QUOTA, decimal(data.quota)),
        )
        for measure, amount in accounting:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=amount,
            )
        for count in range(1, data.counts + 1):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=None,
                channel=0,
                measure=EXHAUSTED,
                count_round=count,
                flags=0,
                value=decimal(data.non_transferable[count - 1]),
            )
        for position, candidate in enumerate(data.candidates, 1):
            ref = f"{year}:{panel}:{fold(candidate.surname, candidate.forename)}"
            person = key("person", ref)
            p.add("person", id=person)
            p.name(person, candidate.name, "en", "official")
            option = key("contest_option", ref)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None,
                ballot_position=candidate.placing,
                ballot_label=f"{candidate.surname}, {candidate.forename}",
            )
            p.add(
                "candidacy",
                id=key("candidacy", ref),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=None,
                is_independent=None,
            )
            for count, amount in enumerate(candidate.values, 1):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=place,
                    option_id=option,
                    channel=0,
                    measure=VOTES,
                    count_round=count,
                    flags=0,
                    value=decimal(amount),
                )
            kind = "elected" if position - 1 in data.elected else "eliminated" if candidate.values[-1] == 0 else "lost"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    return p
