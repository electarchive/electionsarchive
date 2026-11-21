"""Winnipeg school trustee elections from 2024 on, by voting subdivision, from the City of Winnipeg's official results.

The City of Winnipeg runs the trustee elections of its six school divisions (Louis Riel, Pembina Trails, River East
Transcona, Seven Oaks, St. James-Assiniboia, and Winnipeg) with its own civic elections. For the 2026 general election
(October 28, 2026) it publishes an official summary report, a results booklet that marks the winners, and a workbook of
every contest's votes by voting subdivision. For the by-election of June 6, 2024 (Louis Riel Ward 1) and the by-election
of October 25, 2025 (Louis Riel Ward 3 and Pembina Trails Ward 1) it publishes one official results report, which
carries the contest overview, the electors registered, the ballots cast, and the votes by voting subdivision.

The earlier trustee elections of the same divisions, back to 1971, come from the city's open data table in
`ca.mb_winnipeg`, which this pipeline builds on: the divisions, their trustee wards, bodies, and electoral systems are
that pipeline's rows, referenced by id. A trustee ward's votes are a trustee's ward-wide totals plus, from this
pipeline on, the votes counted in each voting subdivision. Each voting subdivision is its own reporting unit for each
ward it lies in; votes counted outside the city for the two wards that extend past it are one unit of their own. The
electors registered and the ballots cast are reported for a whole ward, so they sit on a ward unit that holds no votes.
A ward that elected its trustees by acclamation has a tacit result set.
"""

import functools
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import uuid5

import fastexcel
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.sources.ca import mb_winnipeg as winnipeg

SOURCE = "ca.mb_school"
SITE = "https://www.winnipeg.ca/"
RESULTS_PAGE = SITE + "city-governance/wards-elections/2026-election/official-results"
FILES = {
    "2026": {
        "summary": SITE + "media/7502",
        "booklet": SITE + "media/7503",
        "workbook": SITE + "media/7504",
    },
    "2024-06-06-by": {"report": SITE + "media/3724"},
    "2025-10-25-by": {"report": SITE + "media/6067"},
}
PAGES = {
    "2026": RESULTS_PAGE,
    "2024-06-06-by": SITE
    + "city-governance/wards-elections/2024-school-trustee-election/2024-official-election-results",
    "2025-10-25-by": SITE + "city-governance/wards-elections/2025-election/2025-election-official-results",
}
DAYS = {"2026": date(2026, 10, 28), "2024-06-06-by": date(2024, 6, 6), "2025-10-25-by": date(2025, 10, 25)}
# The divisions, by the key `ca.mb_winnipeg` gave each, with the name that pipeline prints.
DIVISIONS = {
    "Louis Riel": ("louis riel school division", "Louis Riel School Division"),
    "Pembina Trails": ("pembina trails school division", "Pembina Trails School Division"),
    "River East Transcona": ("river east transcona school division", "River East Transcona School Division"),
    "Seven Oaks": ("seven oaks school division", "Seven Oaks School Division"),
    "St. James-Assiniboia": ("st james assiniboia school division", "St. James - Assiniboia School Division"),
    "Winnipeg": ("winnipeg school division", "Winnipeg School Division"),
}
TITLE = re.compile(
    r"^School Trustee (?:for|–|-) (Louis Riel|Pembina Trails|River East Transcona|Seven Oaks|Winnipeg) Ward (\d+)"
    r"|^School Trustee (?:for|–|-) (St\. James-Assiniboia)(?: -)? (Centre|East|West) Ward"
)
SEATS = re.compile(r"\(Vote for\s+(\d+)\)")
NUMBER = r"\d[\d,]*"
SCHEME = "ca-mb.voting_subdivision"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id(name: str) -> str:
    return key("source", name)


def number(text: str) -> int:
    return int(text.replace(",", ""))


def clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


@dataclass
class Candidate:
    name: str
    votes: int | None
    elected: bool = False


@dataclass
class Race:
    division: str  # the division's name in DIVISIONS
    ward: str  # `1`, or `Centre Ward`
    seats: int
    acclaimed: bool
    candidates: list[Candidate] = field(default_factory=list)
    registered: int | None = None
    ballots: int | None = None
    units: dict[str, dict[str, int]] = field(default_factory=dict)  # voting subdivision -> candidate -> votes
    outside: dict[str, int] | None = None  # votes counted outside the city

    @property
    def ident(self) -> tuple[str, str]:
        return (self.division, self.ward)


def title_of(line: str) -> tuple[str, str] | None:
    found = TITLE.match(clean(line))
    if not found:
        return None
    if found.group(1):
        return found.group(1), found.group(2)
    return found.group(3), f"{found.group(4)} Ward"


def pages(name: str) -> list[str]:
    return [page.get_text("text", sort=True) for page in pymupdf.open(raw.cached(SOURCE, name).path)]


def fetch() -> None:
    for files in FILES.values():
        for url in files.values():
            raw.fetch(SOURCE, url, suffix=".xlsx" if url.endswith("7504") else ".pdf")
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")


def _cands(lines: list[str], pattern: re.Pattern[str]) -> list[Candidate]:
    found = []
    for line in lines:
        hit = pattern.match(line.strip())
        if hit:
            found.append(Candidate(clean(hit.group(1)), None if hit.group(2) is None else number(hit.group(2))))
    return found


def _summary(url: str) -> dict[tuple[str, str], Race]:
    """The 2026 official summary: each trustee contest's turnout figures and each candidate's votes."""
    races: dict[tuple[str, str], Race] = {}
    lines = [line for page in pages(url) for line in page.splitlines()]
    at = [i for i, line in enumerate(lines) if title_of(line)]
    for start, stop in zip(at, [*at[1:], len(lines)], strict=True):
        division, ward = title_of(lines[start]) or ("", "")
        head = lines[start]
        seats = SEATS.search(head)
        race = Race(division, ward, int(seats.group(1)) if seats else 1, "ACCLAIMED" in head)
        block = lines[start + 1 : stop]
        for line in block:
            if line.startswith(("Ward Councillor for", "Mayor", "Office of")):
                break
            ballots = re.match(rf"^Ballots Cast\s+({NUMBER}) / ({NUMBER})", line)
            if ballots:
                race.ballots, race.registered = number(ballots.group(1)), number(ballots.group(2))
            candidate = re.match(rf"^(.+?)\s{{2,}}({NUMBER})\s+(?:[\d.]+%|N/A)$", line.strip())
            if candidate:
                race.candidates.append(Candidate(clean(candidate.group(1)), number(candidate.group(2))))
        races[race.ident] = race
    return races


def _booklet(url: str) -> dict[tuple[str, str], list[Candidate]]:
    """The 2026 results booklet: the trustees each ward elected, with the votes it printed."""
    found: dict[tuple[str, str], list[Candidate]] = {}
    text = "\n".join(page for page in pages(url))
    start = text.index("Office of School Trustee")
    division = ward = None
    end = text.find("Appendices", start)
    for raw_line in text[start : end if end > 0 else len(text)].splitlines():
        line = clean(raw_line)
        if not line or line.startswith(("Office of School Trustee", "2026 Municipal")):
            continue
        head = re.match(r"^(.+?) School Division$", line)
        if head:
            division = head.group(1)
            if division not in DIVISIONS:
                raise ValueError(f"unknown division {division}")
            ward = None
            continue
        seats = re.match(r"^(Ward \d+|(?:Centre|East|West) Ward) \((\d+) trustees? elected\)", line)
        if seats and division:
            ward = seats.group(1).removeprefix("Ward ")
            found[(division, ward)] = []
            continue
        if ward is None or division is None:
            continue
        if re.fullmatch(NUMBER, line) and found[(division, ward)] and found[(division, ward)][-1].votes is None:
            found[(division, ward)][-1].votes = number(line)
            continue
        alone = re.match(rf"^ELECTED(?: ({NUMBER}))?$", line)
        if alone and found[(division, ward)]:
            found[(division, ward)][-1].elected = True
            if alone.group(1):
                found[(division, ward)][-1].votes = number(alone.group(1))
            continue
        entry = re.match(rf"^(.*?[A-Za-z].*?)(?: (ELECTED))?(?: ({NUMBER}))?$", line)
        if entry and not line.startswith(("Message", "Page")):
            found[(division, ward)].append(
                Candidate(entry.group(1), number(entry.group(3)) if entry.group(3) else None, bool(entry.group(2)))
            )
    return found


def _workbook(url: str, races: dict[tuple[str, str], Race]) -> None:
    book = fastexcel.read_excel(raw.cached(SOURCE, url).path)
    for sheet in book.sheet_names:
        rows = book.load_sheet_by_name(sheet, header_row=None).to_polars().rows()
        ident = title_of(str(rows[1][0]))
        if ident is None:
            continue
        race = races[ident]
        names = [clean(str(cell)) for cell in rows[2][1:] if cell is not None]
        if race.acclaimed:
            continue
        for row in rows[3:]:
            label = clean(str(row[0] or ""))
            if not label or row[1] is None or not re.fullmatch(NUMBER, str(row[1]).strip()):
                continue
            votes = {name: number(str(cell)) for name, cell in zip(names, row[1:], strict=False)}
            if label == "Total":
                declared = {c.name: c.votes for c in race.candidates}
                if votes != declared:
                    raise ValueError(f"{ident}: the workbook's totals {votes} differ from the summary's {declared}")
            elif label.startswith("*outside"):
                race.outside = votes
            else:
                if label in race.units:
                    raise ValueError(f"{ident}: voting subdivision {label} is listed twice")
                race.units[label] = votes


def _report(url: str) -> dict[tuple[str, str], Race]:
    """A by-election report: for each contest the overview, the turnout, and the votes by voting subdivision."""
    races: dict[tuple[str, str], Race] = {}
    contests = [page for page in pages(url) if "Official Results" in page or "Official Contest" in page]
    for page in contests:
        lines = [line for line in page.splitlines() if line.strip()]
        head = next((line for line in lines if title_of(line)), None)
        if head is None or not any(re.match(r"^Total [Vv]otes", line.strip()) for line in lines):
            continue
        division, ward = title_of(head) or ("", "")
        seats = SEATS.search(head)
        race = Race(division, ward, int(seats.group(1)) if seats else 1, False)
        for line in lines:
            line = line.strip()
            hit = re.match(rf"^(.+?)\s{{2,}}({NUMBER})(\s+ELECTED)?$", line)
            if hit and not re.match(r"^(Total|Ballots|Eligible|Registered|Undervotes|Overvotes|Turnout)", line):
                race.candidates.append(Candidate(clean(hit.group(1)), number(hit.group(2)), bool(hit.group(3))))
            for label, attr in (("Ballots [Cc]ast", "ballots"), ("(?:Eligible|Registered) [Vv]oters", "registered")):
                count = re.match(rf"^{label}:?\s+({NUMBER})$", line)
                if count:
                    setattr(race, attr, number(count.group(1)))
        races[race.ident] = race
    for page in pages(url):
        lines = [line for line in page.splitlines() if line.strip()]
        if "Official Results by Voting Subdivision" not in page and not any(
            title_of(line) and "Vote for" in line for line in lines[:3]
        ):
            continue
        head = next((line for line in lines if title_of(line)), None)
        if head is None or "Voting" not in page:
            continue
        race = races.get(title_of(head) or ("", ""))
        if race is None:
            continue
        _table(race, lines)
    return races


def _table(race: Race, lines: list[str]) -> None:
    count = len(race.candidates)
    rows = []
    for line in lines:
        hit = re.match(rf"^(\S.*?)\s+((?:{NUMBER}\s+){{{count - 1}}}{NUMBER})\s*$", line.strip())
        if hit and not line.startswith(("School Trustee", "Voting")):
            rows.append((clean(hit.group(1)), [number(cell) for cell in hit.group(2).split()]))
    total = next((cells for label, cells in rows if label == "Total"), None)
    if total is None:
        raise ValueError(f"{race.ident}: no total row")
    order: list[Candidate] = []
    for value in total:
        match = [c for c in race.candidates if c.votes == value and c not in order]
        if len(match) != 1:
            raise ValueError(f"{race.ident}: column total {value} does not name one candidate")
        order.append(match[0])
    for label, cells in rows:
        if label == "Total":
            continue
        code = re.sub(r"\*+$", "", label).strip()
        if code in race.units:
            raise ValueError(f"{race.ident}: voting subdivision {code} is listed twice")
        race.units[code] = {c.name: v for c, v in zip(order, cells, strict=True)}


@functools.cache
def races(name: str) -> list[Race]:
    files = FILES[name]
    if name == "2026":
        found = _summary(files["summary"])
        elected = _booklet(files["booklet"])
        for ident, race in found.items():
            marked = elected[ident]
            theirs = {c.name: c for c in marked}
            ours = {c.name for c in race.candidates}
            if set(theirs) != ours:
                raise ValueError(f"{ident}: the booklet's candidates {sorted(theirs)} differ from the summary's")
            for candidate in race.candidates:
                candidate.elected = theirs[candidate.name].elected
                if not race.acclaimed and theirs[candidate.name].votes != candidate.votes:
                    raise ValueError(f"{ident}: {candidate.name} has different votes in the booklet")
        _workbook(files["workbook"], found)
    else:
        found = _report(files["report"])
    for race in found.values():
        if race.division not in DIVISIONS:
            raise ValueError(f"unknown division {race.division}")
        if sum(c.elected for c in race.candidates) != race.seats:
            raise ValueError(f"{race.ident}: {sum(c.elected for c in race.candidates)} elected for {race.seats} seats")
    return sorted(found.values(), key=lambda r: (DIVISIONS[r.division][1], r.ward))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", *(f"{SOURCE}/{name}" for name in FILES)]


def parse() -> None:
    geo().write()
    for name in FILES:
        election(name).write()


def emit() -> None:
    package.run("load", packages())


def division_path(division: str) -> str:
    return f"ca/mb/schools/{winnipeg.slug(DIVISIONS[division][1])}"


def ward_area(race: Race) -> str:
    return key_of_winnipeg("area", "trustee-ward", DIVISIONS[race.division][0], winnipeg.fold(race.ward))


def key_of_winnipeg(kind: str, *parts: object) -> str:
    return winnipeg.key(kind, *parts)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The voting subdivision scheme and the City of Winnipeg's official results of its school trustee elections, "
        "2024 to 2026",
        depends=[f"{winnipeg.SOURCE}/geo", f"{winnipeg.SOURCE}/registry"],
    )
    p.external |= {winnipeg.CANADA, winnipeg.MANITOBA}
    for name, files in FILES.items():
        sid = source_id(name)
        p.add(
            "source",
            id=sid,
            kind="official_body",
            publisher="City of Winnipeg",
            title=f"Official results of the {DAYS[name]} school trustee "
            + ("by-election" if name.endswith("by") else "elections"),
            url=PAGES[name],
            license=None,
            attribution="City of Winnipeg, Office of the Senior Election Official",
        )
        for url in [*files.values(), PAGES[name]]:
            p.artifact(raw.cached(SOURCE, url), sid)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=winnipeg.MANITOBA,
        kind="precinct",
        rank=5,
        noun="voting subdivision",
        code_system=None,
    )
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def election(name: str) -> Package:
    day = DAYS[name]
    by = name.endswith("by")
    when = day.isoformat()
    tag = "by" if by else "local"
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        f"The {day} Winnipeg school trustee " + ("by-election" if by else "elections") + " by voting subdivision",
        depends=[f"{SOURCE}/geo", f"{winnipeg.SOURCE}/geo", f"{winnipeg.SOURCE}/registry"],
    )
    p.external |= {winnipeg.CANADA, winnipeg.MANITOBA}
    sid = source_id(name)
    for url in [*FILES[name].values(), PAGES[name]]:
        p.uses(raw.cached(SOURCE, url))
    close = day + timedelta(days=1)
    local: dict[str, str] = {}
    taken: set[str] = set()
    for number_, race in enumerate(races(name), start=1):
        division = race.division
        code, text = DIVISIONS[division]
        polity = winnipeg.division_polity(code)
        p.external |= {polity, winnipeg.division_body(code), winnipeg.PLURALITY, winnipeg.BLOCK}
        place = ward_area(race)
        p.external.add(place)
        if division not in local:
            local[division] = key("election", name, division)
            p.add(
                "election",
                id=local[division],
                polity_id=polity,
                kind="by_election" if by else "local",
                date_from=day,
                date_to=day,
                status="held",
            )
            p.name(
                local[division], f"{when} {text} by-election" if by else f"{day.year} {text} election", "en", "official"
            )
            p.add("slug", path=f"{division_path(division)}/{when}-{tag}", entity_id=local[division], is_current=True)
        label = f"Trustee, {'Ward ' + race.ward if race.ward.isdigit() else race.ward}"
        ward_tag = f"{number_}"
        total = key("unit", name, ward_tag, "total")
        p.add(
            "reporting_unit",
            id=total,
            polity_id=winnipeg.CANADA,
            scheme_code=SCHEME,
            code=f"{when}:{winnipeg.slug(text)}:{winnipeg.slug(race.ward)}:total",
            area_relation="equals",
            area_id=place,
            kind="aggregate",
            valid_from=day,
            valid_to=close,
        )
        p.name(total, f"{text}: {label}", "en", "official")
        contest = key("contest", name, ward_tag)
        magnitude = race.seats
        p.add(
            "contest",
            id=contest,
            election_id=local[division],
            kind="body_seats",
            office_id=None,
            body_id=winnipeg.division_body(code),
            district_area_id=place,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=winnipeg.PLURALITY if magnitude == 1 else winnipeg.BLOCK,
            default_result_kind="tacit" if race.acclaimed else "counted",
        )
        p.name(contest, f"{text}: {label}", "en", "official")
        path = f"{division_path(division)}/{when}-{tag}/trustee-{winnipeg.slug(race.ward)}"
        while path in taken:
            path += "-2"
        taken.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = key("result_set", name, ward_tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=local[division],
            contest_id=contest,
            stage="official",
            kind="tacit" if race.acclaimed else "counted",
            source_id=sid,
            status="active",
            is_default=True,
            completeness=None if race.acclaimed else 1.0,
        )
        options: dict[str, str] = {}
        for position, candidate in enumerate(race.candidates, start=1):
            seq = f"{ward_tag}:{position}"
            option = key("option", name, seq)
            options[candidate.name] = option
            person = key("person", name, seq)
            p.add("person", id=person, birth_precision=None)
            p.name(person, winnipeg.tidy(candidate.name), "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None,
                ballot_position=None,
                ballot_label=candidate.name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", name, seq),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=None,
            )
            if candidate.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            if candidate.votes is not None and not race.acclaimed:
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=candidate.votes)
        if race.acclaimed:
            continue
        if race.registered is not None:
            _value(p, result_set, total, None, 2, race.registered)
        if race.ballots is not None:
            _value(p, result_set, total, None, 3, race.ballots)
        groups = [(code_, votes, False) for code_, votes in race.units.items()]
        if race.outside is not None:
            groups.append(("outside City of Winnipeg", race.outside, True))
        for label_, votes, outside in groups:
            unit = key("unit", name, ward_tag, label_)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=winnipeg.CANADA,
                scheme_code=SCHEME,
                code=f"{when}:{winnipeg.slug(text)}:{winnipeg.slug(race.ward)}:{winnipeg.slug(label_)}",
                area_relation="within",
                area_id=place,
                kind="aggregate" if outside else "standard",
                valid_from=day,
                valid_to=close,
            )
            p.name(unit, f"{label_} ({text}: {label})", "en", "official")
            for candidate_name, value in votes.items():
                _value(p, result_set, unit, options[candidate_name], 1, value)
    return p
