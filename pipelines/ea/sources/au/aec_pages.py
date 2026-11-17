"""The 1999 referendum by division and the by-elections of 1994 to 2000, from the AEC's own web pages.

The Australian Electoral Commission publishes no machine-readable results for these polls. Its 1999 referendum report
prints both questions' results per division, and its by-election pages print each candidate's first preference votes
with the division's formal, informal, and total votes and its enrolment. The 1998 Newcastle supplementary election is
already part of the 1998 election statistics, so it is not repeated here.
"""

import functools
import re
from datetime import date
from html import unescape
from typing import NamedTuple

from ea import changeset, raw
from ea.package import Package
from ea.sources.au import aec as base
from ea.sources.au.aec import (
    AEC,
    AUSTRALIA,
    COUNTRY,
    HOUSE,
    HOUSE_SYSTEM,
    NO_PARTY,
    REFERENDUM_SYSTEM,
    SOURCE,
    STATES,
    Values,
    area,
    key,
)

SITE = "https://www.aec.gov.au"
REPORT = f"{SITE}/Elections/referendums/1999_Referendum_Reports_Statistics"
BY_ELECTIONS = f"{SITE}/Elections/supplementary_by_elections"
REFERENDUM_DAY = date(1999, 11, 6)
CD_1998 = date(1998, 10, 3)


class Question(NamedTuple):
    slug: str
    page: str
    title: str


QUESTIONS = [
    Question("republic", f"{REPORT}/summary_republic.htm", "Constitution Alteration (Establishment of Republic) 1999"),
    Question("preamble", f"{REPORT}/summary_preamble.htm", "Constitution Alteration (Preamble) 1999"),
]


class By(NamedTuple):
    day: date
    division: str
    state: str
    page: str


BYS = [
    By(date(1994, 1, 29), "Werriwa", "NSW", f"{BY_ELECTIONS}/1994/Werriwa.htm"),
    By(date(1994, 3, 12), "Fremantle", "WA", f"{BY_ELECTIONS}/1994/Fremantle.htm"),
    By(date(1994, 3, 19), "Bonython", "SA", f"{BY_ELECTIONS}/1994/Bonython.htm"),
    By(date(1994, 3, 26), "Mackellar", "NSW", f"{BY_ELECTIONS}/1994/Mackellar.htm"),
    By(date(1994, 3, 26), "Warringah", "NSW", f"{BY_ELECTIONS}/1994/Warringah.htm"),
    By(date(1994, 11, 19), "Kooyong", "VIC", f"{BY_ELECTIONS}/1994/Kooyong.htm"),
    By(date(1995, 3, 25), "Canberra", "ACT", f"{BY_ELECTIONS}/1995/Canberra.htm"),
    By(date(1995, 4, 8), "Wentworth", "NSW", f"{BY_ELECTIONS}/1995/Wentworth.htm"),
    By(date(1996, 6, 15), "Blaxland", "NSW", f"{BY_ELECTIONS}/1996/Blaxland.htm"),
    By(date(1996, 10, 19), "Lindsay", "NSW", f"{BY_ELECTIONS}/1996/Lindsay.htm"),
    By(date(1997, 2, 1), "Fraser", "ACT", f"{BY_ELECTIONS}/1997/Fraser.htm"),
    By(date(1999, 11, 6), "Holt", "VIC", f"{BY_ELECTIONS}/1999/holt.htm"),
    By(date(2000, 8, 12), "Isaacs", "VIC", f"{BY_ELECTIONS}/2000/isaacs.htm"),
]
ENROLMENT = f"{REPORT}/Statistical_Results.htm"
# The preamble summary prints 78 737 electors for Forde; the republic summary, the report's enrolment table, and the
# preamble's own Queensland total all give 78 734, so the enrolment table's figure is stored.
ENROLMENT_FIXES = {("preamble", "Forde"): 78734}
DAYS = sorted({REFERENDUM_DAY, *(by.day for by in BYS)})
MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
STATE_HEADINGS = {
    "New South Wales": "NSW",
    "Victoria": "VIC",
    "Queensland": "QLD",
    "Western Australia": "WA",
    "South Australia": "SA",
    "Tasmania": "TAS",
    "Australian Capital Territory": "ACT",
    "Northern Territory": "NT",
}
REFERENDUM_METHOD = (
    "Yes, No, formal, informal, and total votes and enrolment per division, as the Australian Electoral Commission "
    "prints them in the summary of results of its 1999 referendum report. Each division is one unit; the report's "
    "polling place results were published only on a CD-ROM, so votes are not broken down further."
)
BY_METHOD = (
    "First preference votes per candidate, with the division's formal, informal, and total votes and its "
    "enrolment, as the Australian Electoral Commission prints them on its page for this by-election. The division "
    "is one unit; the distribution of preferences is not stored, and the outcome is the member the Divisional "
    "Returning Officer declared."
)
NUMBER = r"\d{1,3}(?: \d{3})*"


def fetch() -> None:
    for question in QUESTIONS:
        raw.fetch(SOURCE, question.page, suffix=".htm")
    raw.fetch(SOURCE, ENROLMENT, suffix=".htm")
    for by in BYS:
        raw.fetch(SOURCE, by.page, suffix=".htm")


def urls(day: date) -> list[str]:
    found = [*(question.page for question in QUESTIONS), ENROLMENT] if day == REFERENDUM_DAY else []
    return found + [by.page for by in BYS if by.day == day]


def text(url: str) -> str:
    page = base._text(raw.cached(SOURCE, url).path.read_bytes())
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    flat = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", page))).strip()
    start = flat.index("Updated:")
    return flat[start : flat.index("Connect with us", start)]


def number(tokens: str) -> int:
    return int(tokens.replace(" ", ""))


@functools.cache
def division_codes() -> dict[tuple[str, str], str]:
    """(state, lower-case name) -> the au.division code the geo package gives each division named in 1993 to 1998."""
    codes: dict[tuple[str, str], str] = {}
    for code, (state, names) in base._divisions().items():
        for day, name in names:
            if day <= CD_1998:
                found = codes.setdefault((state, _plain(name)), code)
                if found != code:
                    raise ValueError(f"{state} {name} is division {found} and {code}")
    return codes


def _plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def division_code(state: str, name: str) -> str:
    return division_codes()[(state, _plain(name))]


# The 1999 referendum ---------------------------------------------------------------------------------------------


class Row(NamedTuple):
    name: str
    enrolment: int
    yes: int
    no: int
    formal: int
    informal: int
    total: int


ROW = re.compile(
    r"([A-Za-z][A-Za-z'.\- ]*?) (Yes|No) ((?:\d+ )+?)\d+\.\d\d ((?:\d+ )+?)\d+\.\d\d ((?:\d+ )+?)\d+\.\d\d "
    r"((?:\d+ )+?)\d+\.\d\d ((?:\d+ )+?)\d+\.\d\d"
)


def _row(match: re.Match[str]) -> Row:
    name = match.group(1).strip()
    head = match.group(3).split()
    no, formal, informal, total = (number(match.group(i).strip()) for i in range(4, 8))
    splits = []
    for cut in range(1, len(head)):
        yes_tokens = head[cut:]
        if any(len(token) != 3 for token in yes_tokens[1:]) or any(len(t) != 3 for t in head[1:cut]):
            continue
        yes = int("".join(yes_tokens))
        if yes + no == formal:
            splits.append(Row(name, int("".join(head[:cut])), yes, no, formal, informal, total))
    if len(splits) != 1:
        raise ValueError(f"{name}: cannot split {match.group(3)!r} into enrolment and yes votes")
    row = splits[0]
    if row.formal + row.informal != row.total:
        raise ValueError(f"{name}: formal {row.formal} and informal {row.informal} do not make {row.total}")
    return row


def _fixed(question: Question, row: Row) -> Row:
    roll = ENROLMENT_FIXES.get((question.slug, row.name))
    if roll is None:
        return row
    if not re.search(rf"\b{row.name} {roll // 1000} {roll % 1000:03d}\b", text(ENROLMENT)):
        raise ValueError(f"{row.name}: the enrolment table does not print {roll}")
    return row._replace(enrolment=roll)


@functools.cache
def summary(question: Question) -> tuple[dict[str, list[Row]], dict[str, Row], Row]:
    """Division rows per state, the state total rows, and the national total row of one question."""
    body = text(question.page)
    first = body.index(" New South Wales Division Result")
    national = next(_row(m) for m in ROW.finditer(body[:first]) if m.group(1).strip() == "National Total")
    by_state: dict[str, list[Row]] = {}
    totals: dict[str, Row] = {}
    headings = list(re.finditer(r"(" + "|".join(STATE_HEADINGS) + r") Division Result", body))
    for index, heading in enumerate(headings):
        state = STATE_HEADINGS[heading.group(1)]
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        segment = body[heading.end() : end]
        rows = [_row(m) for m in ROW.finditer(segment)]
        rows = [_fixed(question, row) for row in rows]
        total = [row for row in rows if row.name.endswith(" Total")]
        if len(total) != 1:
            raise ValueError(f"{question.slug} {state}: {len(total)} total rows")
        totals[state] = total[0]
        by_state[state] = [row for row in rows if not row.name.endswith(" Total")]
    if sorted(by_state) != sorted(STATES):
        raise ValueError(f"{question.slug}: states {sorted(by_state)}")
    for state, rows in by_state.items():
        for field in Row._fields[1:]:
            summed = sum(getattr(row, field) for row in rows)
            if summed != getattr(totals[state], field):
                raise ValueError(
                    f"{question.slug} {state} {field}: divisions {summed}, total {getattr(totals[state], field)}"
                )
    for field in Row._fields[1:]:
        summed = sum(getattr(row, field) for row in totals.values())
        if summed != getattr(national, field):
            raise ValueError(f"{question.slug} national {field}: states {summed}, total {getattr(national, field)}")
    return by_state, totals, national


class Divisions:
    """One reporting unit per division and day, shared by every contest of a package."""

    def __init__(self, p: Package, day: date) -> None:
        self.p = p
        self.day = day
        self.ids: dict[str, str] = {}

    def unit(self, code: str, name: str) -> str:
        if code in self.ids:
            return self.ids[code]
        id = self.ids[code] = key("unit", f"{self.day}:{code}:DIVISION")
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=AUSTRALIA,
            scheme_code="au.division",
            code=f"{self.day}:{code}",
            area_relation="equals",
            area_id=area("au.division", code),
            kind="aggregate",
        )
        self.p.name(id, name, "en", "official")
        return id


def _accounts(values: Values, result_set: str, unit: str, formal: int, informal: int, total: int, roll: int) -> None:
    values.add(result_set, unit, None, 4, formal)
    values.add(result_set, unit, None, 5, informal)
    values.add(result_set, unit, None, 3, total)
    values.add(result_set, unit, None, 2, roll)


def _result_set(p: Package, election: str, contest: str, method: str) -> str:
    return base._result_set(p, election, contest, method)


def referendum(p: Package, election: str, path: str, divisions: Divisions, values: Values) -> int:
    for question in QUESTIONS:
        contest = key("contest", f"page:{REFERENDUM_DAY}", "referendum", question.slug)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=COUNTRY,
            round_number=1,
            electoral_system_id=REFERENDUM_SYSTEM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="mandatory_referendum")
        p.name(contest, question.title, "en", "official")
        p.add("slug", path=f"{path}/{question.slug}", entity_id=contest, is_current=True)
        result_set = _result_set(p, election, contest, REFERENDUM_METHOD)
        options = {}
        for spot, answer in enumerate(("Yes", "No"), 1):
            option = options[answer] = key("option", f"page:{REFERENDUM_DAY}", question.slug, answer)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=spot,
                ballot_label=answer,
            )
        by_state, _, national = summary(question)
        for state, rows in by_state.items():
            for row in rows:
                unit = divisions.unit(division_code(state, row.name), row.name)
                values.add(result_set, unit, options["Yes"], 1, row.yes)
                values.add(result_set, unit, options["No"], 1, row.no)
                _accounts(values, result_set, unit, row.formal, row.informal, row.total, row.enrolment)
        for label, measure, value in (
            ("Yes", 1, national.yes),
            ("No", 1, national.no),
            (None, 4, national.formal),
            (None, 5, national.informal),
            (None, 3, national.total),
            (None, 2, national.enrolment),
        ):
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=options[label] if label else None,
                measure=measure,
                value=value,
            )
        winner = "Yes" if national.yes > national.no else "No"
        for label, option in options.items():
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if label == winner else "lost")
    return len(QUESTIONS)


# By-elections ----------------------------------------------------------------------------------------------------


class Candidate(NamedTuple):
    label: str
    name: str
    party: str
    votes: int
    marked: bool


class Result(NamedTuple):
    candidates: list[Candidate]
    formal: int
    informal: int
    total: int
    enrolment: int
    declared: str


CANDIDATE = re.compile(r"(#?)([A-Za-z][^#\d]*?) (" + NUMBER + r") \d{1,3}\.\d\d(?= |$)")
PARTY = re.compile(r"[A-Z]{2,4}")


def _date(body: str, by: By) -> date:
    found = {
        date(int(y), MONTHS[m], int(d))
        for d, m, y in re.findall(r"(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})", body)
    }
    if by.day not in found:
        raise ValueError(f"{by.page}: {by.day} is not on the page")
    return by.day


@functools.cache
def by_result(by: By) -> Result:
    body = text(by.page)
    _date(body, by)
    start = body.index("Votes %", body.index("Candidates")) + len("Votes %")
    end = re.compile(r" FORMAL ", re.IGNORECASE).search(body, start)
    if end is None:
        raise ValueError(f"{by.page}: no formal row")
    listed = body[start : end.start()]
    tabular = "Candidates Party Votes" in body
    candidates = []
    for match in CANDIDATE.finditer(listed):
        marked, label, votes = match.group(1) == "#", match.group(2).strip(), number(match.group(3))
        party = ""
        if tabular:
            words = label.split(" ")
            if len(words) > 1 and PARTY.fullmatch(words[-1]):
                party, label = words[-1], " ".join(words[:-1])
        else:
            bracket = re.fullmatch(r"(.*?) \(([A-Z]+)\)", label)
            if bracket:
                label, party = bracket.group(1), bracket.group(2)
        candidates.append(Candidate(label, label, party, votes, marked))
    tail = body[end.start() :]
    counts = re.match(
        rf" FORMAL ({NUMBER}) [\d.]+ INFORMAL ({NUMBER}) [\d.]+ TOTAL ({NUMBER}) [\d.]+", tail, re.IGNORECASE
    )
    if counts is None:
        raise ValueError(f"{by.page}: no formal, informal, and total votes")
    formal, informal, total = (number(counts.group(i)) for i in (1, 2, 3))
    roll = re.search(rf"(?:A total of|There were) ({NUMBER}) electors (?:were )?enrolled", body)
    if roll is None:
        raise ValueError(f"{by.page}: no enrolment")
    declared = re.search(r"declared (.+?) (?:\([A-Z]+\) )?as the new Member", body, re.IGNORECASE)
    if declared is None:
        raise ValueError(f"{by.page}: no declared member")
    result = Result(candidates, formal, informal, total, number(roll.group(1)), declared.group(1).strip())
    if sum(c.votes for c in candidates) != formal:
        raise ValueError(f"{by.page}: candidates sum to {sum(c.votes for c in candidates)}, formal is {formal}")
    if formal + informal != total:
        raise ValueError(f"{by.page}: formal {formal} and informal {informal} do not make {total}")
    return result


def _person(label: str, declared: str) -> str:
    """The candidate's name in given-name order; pages that print surnames only borrow the declared member's name."""
    if ", " in label:
        surname, _, given = label.partition(", ")
        return base.person_name(given, surname)
    if label.isupper() and " " not in label:
        if declared.upper().endswith(" " + label):
            return declared
        return label.title()
    return label


def _winner(result: Result, by: By) -> int:
    marked = [i for i, c in enumerate(result.candidates) if c.marked]
    surname = result.declared.split()[-1].upper()
    named = [
        i
        for i, c in enumerate(result.candidates)
        if (c.label.partition(",")[0] if "," in c.label else c.label.split()[-1]).upper() == surname
    ]
    if len(named) != 1 or (marked and marked != named):
        raise ValueError(f"{by.page}: marked {marked}, named {named} for {result.declared}")
    return named[0]


def by_election(
    p: Package, by: By, election: str, path: str, divisions: Divisions, values: Values, known: set[str]
) -> int:
    code = division_code(by.state, by.division)
    result = by_result(by)
    contest = key("contest", f"page:{by.day}", "house", code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=HOUSE,
        district_area_id=area("au.division", code),
        magnitude=1,
        round_number=1,
        electoral_system_id=HOUSE_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{by.division} ({by.state})", "en", "official")
    p.add("slug", path=f"{path}/house-{changeset.slug(by.division)}", entity_id=contest, is_current=True)
    result_set = _result_set(p, election, contest, BY_METHOD)
    unit = divisions.unit(code, by.division)
    winner = _winner(result, by)
    for position, candidate in enumerate(result.candidates, 1):
        tag = f"{code}:{position}"
        party = base._party_of(candidate.party, known)
        option = key("option", f"page:{by.day}", "house", tag)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=candidate.label,
        )
        person = key("person", f"page:{by.day}", tag)
        p.add("person", id=person)
        p.name(person, _person(candidate.label, result.declared), "en", "official")
        p.add(
            "candidacy",
            id=key("candidacy", f"page:{by.day}", tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.label,
            member_of_party_id=party,
            is_independent=candidate.party in NO_PARTY,
        )
        values.add(result_set, unit, option, 1, candidate.votes)
        if position - 1 == winner:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    _accounts(values, result_set, unit, result.formal, result.informal, result.total, result.enrolment)
    p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=result.formal)
    p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=result.informal)
    return 1


# Packages --------------------------------------------------------------------------------------------------------


def _title(bys: list[By]) -> str:
    names = [by.division for by in bys]
    year = bys[0].day.year
    if len(names) == 1:
        return f"{year} {names[0]} by-election"
    return f"{year} {', '.join(names[:-1])} and {names[-1]} by-elections"


def election(day: date) -> Package:
    bys = [by for by in BYS if by.day == day]
    titles = []
    if day == REFERENDUM_DAY:
        titles.append("1999 Australian republic and preamble referendum, by division")
    if bys:
        titles.append(f"{_title(bys)}, by division")
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {' and the '.join(titles)}, from the Australian Electoral Commission's web pages",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    for url in urls(day):
        p.artifact(raw.cached(SOURCE, url), AEC)
    divisions = Divisions(p, day)
    values = Values()
    if day == REFERENDUM_DAY:
        election_id = key("election", day)
        path = f"au/{day}-referendum"
        p.add(
            "election",
            id=election_id,
            polity_id=AUSTRALIA,
            kind="referendum",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        p.name(election_id, "1999 Australian republic referendum", "en", "official")
        p.add("slug", path=path, entity_id=election_id, is_current=True)
        referendum(p, election_id, path, divisions, values)
    if bys:
        election_id = key("election", day) if day != REFERENDUM_DAY else key("election", day, "by_election")
        path = f"au/{day}-by-election"
        p.add(
            "election",
            id=election_id,
            polity_id=AUSTRALIA,
            kind="by_election",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        p.name(election_id, _title(bys), "en", "official")
        p.add("slug", path=path, entity_id=election_id, is_current=True)
        known = base._parties()
        for by in bys:
            by_election(p, by, election_id, path, divisions, values, known)
    p.frame("result_value", values.frame())
    return p


def totals() -> list[str]:
    """How each poll's stored totals compare with the totals the AEC prints."""
    lines = []
    for question in QUESTIONS:
        _, _, national = summary(question)
        lines.append(
            f"1999 {question.slug}: yes {national.yes:,}, no {national.no:,}, informal {national.informal:,}; "
            "divisions sum to the printed state and national totals"
        )
    for by in BYS:
        result = by_result(by)
        lines.append(f"{by.day} {by.division}: formal {result.formal:,} equals the sum of candidates")
    return lines
