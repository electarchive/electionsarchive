"""Federal elections and by-elections of 1901 to 1992, from the Parliamentary Handbook's election results.

The Parliamentary Library's Parliamentary Handbook publishes the results of every federal election through a JSON
service: the first preference votes of each House of Representatives candidate per electoral division, with the
division's enrolment, and each Senate candidate's first preference votes per state, with the state's enrolment. The
election packages here extend the au.aec pipeline backward from the AEC's own 1993 election statistics.
"""

import functools
import json
import re
from collections import Counter, defaultdict
from datetime import date
from typing import NamedTuple
from urllib.parse import quote

from ea import changeset, raw
from ea.package import Package
from ea.sources.au import aec as base
from ea.sources.au.aec import (
    AUSTRALIA,
    COUNTRY,
    HOUSE,
    HOUSE_SYSTEM,
    SENATE,
    SENATE_GVT,
    STATE_NAMES,
    Values,
    area,
    key,
)

SOURCE = "au.aec"
API = "https://handbookapi.aph.gov.au/api/Elections"
ELECTIONS = f"{API}/Elections"
# The AEC's own election statistics start with the 1993 election (au.aec_cd and later modules).
LAST = date(1993, 1, 1)
STATE_CODES = {"NSW": "NSW", "Vic": "VIC", "Qld": "QLD", "WA": "WA", "SA": "SA", "Tas": "TAS", "ACT": "ACT", "NT": "NT"}


class Poll(NamedTuple):
    id: int
    day: date
    division: str | None  # None for a general election or a Senate election
    reps: bool
    senate: bool


def q(text: str) -> str:
    return quote(text, safe="")


def first_preferences(division: str, year: int, poll: int) -> str:
    return f"{API}/FirstPreferenceCount?divisionName={q(division)}&year={year}&electionId={poll}"


def statistics(division: str, year: int, poll: int) -> str:
    return f"{API}/DivisionStatistics?divisionName={q(division)}&year={year}&electionId={poll}"


def divisions_url(state: str, year: int) -> str:
    return f"{API}/Divisions?state={q(state)}&year={year}"


def election_url(poll: int) -> str:
    return f"{API}/Election?electionId={poll}"


def candidates_url(poll: int) -> str:
    return f"{API}/Candidates?electionId={poll}"


def members_url(year: int) -> str:
    return f"{API}/MembersElected?year={year}"


def senate_candidates_url(state: str, year: int) -> str:
    return f"{API}/SenateByCandidate?state={q(state)}&year={year}"


def senate_statistics_url(state: str, year: int) -> str:
    return f"{API}/SenateStateStatistics?state={q(state)}&year={year}"


def senators_url(year: int) -> str:
    return f"{API}/SenatorsElected?year={year}"


def load(url: str) -> object:
    return json.loads(raw.cached(SOURCE, url).path.read_bytes())


def _list(url: str) -> list[dict[str, object]]:
    rows = load(url)
    if not isinstance(rows, list):
        raise TypeError(f"{url}: not a list")
    return [row for row in rows if isinstance(row, dict)]


def _object(url: str) -> dict[str, object]:
    row = load(url)
    if not isinstance(row, dict):
        raise TypeError(f"{url}: not an object")
    return row


@functools.cache
def polls() -> list[Poll]:
    found = []
    for row in _list(ELECTIONS):
        day = date.fromisoformat(str(row["Date"])[:10])
        if day >= LAST:
            continue
        division = row["Division"]
        found.append(
            Poll(_int(row["Id"]), day, str(division) if division else None, bool(row["Reps"]), bool(row["Senate"]))
        )
    return sorted(found, key=lambda poll: (poll.day, poll.division or ""))


def generals() -> list[Poll]:
    return [poll for poll in polls() if poll.division is None]


def by_elections() -> list[Poll]:
    return [poll for poll in polls() if poll.division is not None]


def fetch() -> None:
    raw.fetch(SOURCE, ELECTIONS, suffix=".json")
    first = [election_url(poll.id) for poll in generals()]
    first += [members_url(year) for year in sorted({poll.day.year for poll in generals() if poll.reps})]
    first += [senators_url(year) for year in sorted({poll.day.year for poll in generals() if poll.senate})]
    first += [candidates_url(poll.id) for poll in polls()]
    for poll in generals():
        if poll.reps:
            first += [divisions_url(state, poll.day.year) for state in STATE_CODES]
    raw.fetch_many(SOURCE, first, suffix=".json", workers=6)
    second = []
    for poll in generals():
        year = poll.day.year
        if poll.reps:
            for name in division_names(poll):
                second += [first_preferences(name, year, poll.id), statistics(name, year, poll.id)]
        if poll.senate:
            for state in senate_states(poll):
                second += [senate_candidates_url(state, year), senate_statistics_url(state, year)]
    for poll in by_elections():
        assert poll.division is not None
        year = poll.day.year
        second += [first_preferences(poll.division, year, poll.id), statistics(poll.division, year, poll.id)]
    raw.fetch_many(SOURCE, second, suffix=".json", workers=6)


def division_names(poll: Poll) -> list[str]:
    names: list[str] = []
    for state in STATE_CODES:
        names += [str(row["Name"]) for row in _list(divisions_url(state, poll.day.year))]
    return names


def senate_states(poll: Poll) -> list[str]:
    states = _object(election_url(poll.id)).get("SenateStates")
    return [str(state) for state in states] if isinstance(states, list) else []


# Reading ---------------------------------------------------------------------------------------------------------


class Candidate(NamedTuple):
    label: str
    party: str
    votes: int
    elected: bool


class Contest(NamedTuple):
    poll: Poll
    state: str  # the AEC's state abbreviation
    name: str  # the division's name, or the state's name for a statewide House contest
    candidates: list[Candidate]
    enrolment: int
    urls: list[str]


def _int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{value!r} is not a whole number")
    return value


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _candidates(url: str) -> list[Candidate]:
    rows = _object(url).get("Candidates")
    if not isinstance(rows, list):
        raise TypeError(f"{url}: no candidates")
    found = []
    total = None
    for row in rows:
        if row["Candidate"] == "Total":
            total = _int(row["Votes"])
            continue
        found.append(
            Candidate(_text(row["Candidate"]), _text(row["Party"]), _int(row["Votes"]), row["Status"] == "Elected")
        )
    if total is None or total != sum(c.votes for c in found):
        raise ValueError(f"{url}: the total row {total} is not the sum of the candidates")
    return found


def _enrolment(url: str) -> int:
    return _int(_object(url).get("Enrolment"))


@functools.cache
def division_states(year: int) -> dict[str, str]:
    """Division name -> AEC state abbreviation for the divisions of one general election year."""
    found: dict[str, str] = {}
    for state, ab in STATE_CODES.items():
        for row in _list(divisions_url(state, year)):
            found[str(row["Name"])] = ab
    return found


def _state_of(poll: Poll, name: str) -> str:
    if name in STATE_BY_NAME:
        return STATE_BY_NAME[name]
    for year in sorted({g.day.year for g in generals() if g.reps}, key=lambda y: abs(y - poll.day.year)):
        states = division_states(year)
        if name in states:
            return states[name]
    raise ValueError(f"{poll.day} {name}: no state")


STATE_BY_NAME = {name: ab for ab, name in STATE_NAMES.items()}


@functools.cache
def house(poll: Poll) -> list[Contest]:
    year = poll.day.year
    if poll.division is not None:
        names = [(_state_of(poll, poll.division), poll.division)]
    else:
        names = [(state, name) for name, state in division_states(year).items()]
    contests = []
    for state, name in names:
        fp, stats = first_preferences(name, year, poll.id), statistics(name, year, poll.id)
        candidates = _candidates(fp)
        if not candidates:
            raise ValueError(f"{poll.day} {name}: no candidates")
        contests.append(Contest(poll, state, name, candidates, _enrolment(stats), [fp, stats]))
    return contests


@functools.cache
def senators(year: int) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for row in _list(senators_url(year)):
        found[STATE_CODES[str(row["State"])]].append(_plain(str(row["Name"])))
    return found


def _plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


@functools.cache
def senate(poll: Poll) -> list[Contest]:
    year = poll.day.year
    contests = []
    elected = senators(year)
    for state in senate_states(poll):
        ab = STATE_CODES[state]
        url, stats = senate_candidates_url(state, year), senate_statistics_url(state, year)
        rows = _list(url)
        winners = Counter(elected.get(ab, []))
        candidates = []
        for row in rows:
            label = _text(row["Candidate"])
            won = winners[_plain(label)] > 0
            if won:
                winners[_plain(label)] -= 1
            candidates.append(Candidate(label, _text(row["Party"]), _int(row["Votes"]), won))
        if +winners:
            raise ValueError(f"{poll.day} Senate {ab}: elected senators {sorted(+winners)} are not candidates")
        if not candidates:
            raise ValueError(f"{poll.day} Senate {ab}: no candidates")
        contests.append(Contest(poll, ab, STATE_NAMES[ab], candidates, _enrolment(stats), [url, stats]))
    return contests


# Places ----------------------------------------------------------------------------------------------------------


@functools.cache
def existing_codes() -> dict[tuple[str, str], str]:
    """(state, plain name) -> the au.division code the au.aec geo package gives the division of that name."""
    codes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for code, (state, names) in base._divisions().items():
        for _, name in names:
            codes[(state, _plain(name))].add(code)
    return {k: min(v, key=lambda c: (not c.isdigit(), c.zfill(6))) for k, v in codes.items()}


def statewide(contest: Contest) -> bool:
    """1901's South Australian and Tasmanian House members, and Tasmania's 1902 by-election, were elected statewide."""
    return contest.name in STATE_BY_NAME and contest.state in ("SA", "TAS")


def division_code(state: str, name: str) -> str:
    return existing_codes().get((state, _plain(name)), f"{state}-{name}")


@functools.cache
def all_divisions() -> dict[str, tuple[str, str]]:
    """au.division code -> (state, name) of every division the Handbook names, in the code the geo packages use."""
    found: dict[str, tuple[str, str]] = {}
    for poll in polls():
        if not poll.reps:
            continue
        for contest in house(poll):
            if not statewide(contest):
                found.setdefault(division_code(contest.state, contest.name), (contest.state, contest.name))
    return found


def new_divisions() -> dict[str, tuple[str, str]]:
    existing = set(base._divisions())
    return {code: entry for code, entry in all_divisions().items() if code not in existing}


# Registry --------------------------------------------------------------------------------------------------------

HANDBOOK = key("source", "handbook")
HOUSE_FPTP = key("electoral_system", "house", "fptp")
HOUSE_CONTINGENT = key("electoral_system", "house", "qld-1901")
HOUSE_BLOCK = key("electoral_system", "house", "sa-1901")
HARE_CLARK = key("electoral_system", "tas-1901")
SENATE_BLOCK = key("electoral_system", "senate", 1901)
SENATE_PREFERENTIAL_BLOCK = key("electoral_system", "senate", 1919)
SENATE_STV = key("electoral_system", "senate", 1949)
PREFERENTIAL = date(1918, 12, 14)
SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        HOUSE_FPTP,
        date(1901, 1, 1),
        PREFERENTIAL,
        {"formula": "fptp"},
        (
            "Each division elected one member by first past the post: the candidate with the most votes won. New South "
            "Wales, Victoria, and Western Australia used it under their colonial laws in 1901, and the Commonwealth "
            "Electoral Act 1902 applied it everywhere from 1903 until preferential voting began in December 1918."
        ),
    ),
    (
        HOUSE_CONTINGENT,
        date(1901, 1, 1),
        date(1903, 1, 1),
        {"formula": "supplementary"},
        (
            "In 1901 Queensland's divisions used the colony's contingent vote: voters could mark preferences, and when "
            "no candidate held an absolute majority of first preferences, all but the leading two were excluded and "
            "their ballots passed to whichever of the two they preferred."
        ),
    ),
    (
        HOUSE_BLOCK,
        date(1901, 1, 1),
        date(1903, 1, 1),
        {"formula": "block"},
        (
            "In 1901 South Australia elected its seven members of the House of Representatives statewide by block "
            "voting under its colonial law: each voter could vote for up to seven candidates, and the seven with the most "
            "votes won."
        ),
    ),
    (
        HARE_CLARK,
        date(1901, 1, 1),
        date(1903, 1, 1),
        {"formula": "stv", "quota": "hare"},
        (
            "In 1901 and 1902 Tasmania elected its five members of the House of Representatives and its six senators "
            "statewide by the Hare-Clark single transferable vote under its colonial law."
        ),
    ),
    (
        SENATE_BLOCK,
        date(1901, 1, 1),
        date(1919, 1, 1),
        {"formula": "block"},
        (
            "Each state elected its senators statewide by block voting: each voter voted for as many candidates as there "
            "were vacancies, and the candidates with the most votes won. Each mark counts as a vote, so the candidates' "
            "votes add up to several times the ballots cast."
        ),
    ),
    (
        SENATE_PREFERENTIAL_BLOCK,
        date(1919, 1, 1),
        date(1949, 1, 1),
        {"formula": "block"},
        (
            "From 1919 to 1946 each state elected its senators by preferential block voting: voters numbered every "
            "candidate, the first vacancy was filled by full preferential count, and each later vacancy by a new count "
            "with every ballot restored to full value and the elected candidates removed. The votes stored are first "
            "preferences."
        ),
    ),
    (
        SENATE_STV,
        date(1949, 1, 1),
        date(1984, 1, 1),
        {"formula": "stv", "quota": "droop"},
        (
            "From 1949 to 1983 each state elected its senators by the single transferable vote with a Droop quota. "
            "Voters numbered every candidate, and there were no group voting tickets. Surplus ballots were transferred by "
            "drawing at random the number of ballots to pass on, which the engine's rules cannot express."
        ),
    ),
]


def _party_key(label: str) -> str | None:
    if label in ("", "Independent"):
        return None
    return label


@functools.cache
def aec_party_names() -> dict[str, str]:
    """Party name -> the AEC abbreviation the au.aec registry holds it under, for names only one abbreviation uses."""
    by_name: dict[str, set[str]] = defaultdict(set)
    for ab, name in base._party_names().items():
        by_name[name.lower()].add(ab)
    return {name: next(iter(abs_)) for name, abs_ in by_name.items() if len(abs_) == 1}


# Handbook labels for parties the AEC registers under another name: the same parties, so their records are shared.
SAME_PARTY = {"Liberal Party of Australia": "LP", "Democrats": "DEM", "Nationals": "NP"}


def party_id(label: str) -> str | None:
    name = _party_key(label)
    if name is None:
        return None
    ab = SAME_PARTY.get(name) or aec_party_names().get(name.lower())
    return base.party(ab) if ab is not None else key("party", "handbook", name)


@functools.cache
def party_labels() -> list[str]:
    found: set[str] = set()
    for poll in polls():
        for contest in [
            *(house(poll) if poll.reps else []),
            *(senate(poll) if poll.senate and poll.division is None else []),
        ]:
            found |= {c.party for c in contest.candidates}
    return sorted(label for label in found if _party_key(label) is not None)


def _source_row(p: Package) -> None:
    p.add(
        "source",
        id=HANDBOOK,
        kind="official_body",
        publisher="Parliamentary Library, Parliament of Australia",
        title="Parliamentary Handbook: federal election results",
        url="https://handbook.aph.gov.au/Elections",
        license="CC BY-NC-ND 4.0",
        license_url="https://creativecommons.org/licenses/by-nc-nd/4.0/",
        attribution="© Commonwealth of Australia (Parliamentary Library)",
    )


def handbook_files() -> list[str]:
    files = [ELECTIONS]
    for poll in generals():
        files.append(election_url(poll.id))
        if poll.reps:
            files += [divisions_url(state, poll.day.year) for state in STATE_CODES]
        if poll.senate:
            files.append(senators_url(poll.day.year))
    return list(dict.fromkeys(files))


def cite(p: Package, urls: list[str], held: set[str]) -> None:
    """Cite each file once per storage key: identical responses share one artifact row, held by a dependency or by
    the first file that has it."""
    seen = set(held)
    for url in urls:
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key in seen:
            p.uses(artifact)
        else:
            seen.add(artifact.storage_key)
            p.artifact(artifact, HANDBOOK)


@functools.cache
def geo_keys() -> set[str]:
    return {raw.cached(SOURCE, url).storage_key for url in handbook_files()}


def geo() -> Package:
    divisions = new_divisions()
    p = Package(
        f"{SOURCE}/handbook-geo",
        SOURCE,
        f"The {len(divisions)} federal electoral divisions of 1901 to 1992 that the Australian Electoral Commission's "
        "results since 1993 do not name, from the Parliamentary Handbook",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA}
    _source_row(p)
    cite(p, handbook_files(), set())
    for code, (state, name) in sorted(divisions.items()):
        id = area("au.division", code)
        p.add("area", id=id, scheme_code="au.division")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("au.state", state))
        p.name(id, name, "en", "official")
    return p


def registry() -> Package:
    labels = [label for label in party_labels() if party_id(label) == key("party", "handbook", label)]
    p = Package(
        f"{SOURCE}/handbook-registry",
        SOURCE,
        f"The electoral systems of the House of Representatives and the Senate before 1984 and the {len(labels)} "
        "party labels of the Parliamentary Handbook's results of 1901 to 1992 that the AEC's party register does "
        "not hold",
        depends=[f"{SOURCE}/handbook-geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=AUSTRALIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for label in labels:
        id = key("party", "handbook", label)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.add("external_id", entity_id=id, scheme="au.handbook.party", value=label)
        p.name(id, label, "en", "official")
    return p


# Elections -------------------------------------------------------------------------------------------------------

HOUSE_METHOD = (
    "First preference votes per candidate and the enrolment of the division, as the Parliamentary Library's "
    "Parliamentary Handbook publishes them. The division is one unit. The Handbook gives informal votes and turnout "
    "only as percentages, which are not stored, and the distribution of preferences is not stored; the outcome is "
    "the member the Handbook lists as elected."
)
# By-elections whose Handbook results mark no candidate as elected; their outcomes are left unstored.
UNMARKED = {
    date(1946, 3, 30): "The Parliamentary Handbook marks no candidate as elected at this by-election, so no outcome is "
    "stored.",
}
UNOPPOSED = "The member was returned unopposed, so no votes were cast."
SENATE_METHOD = (
    "First preference votes per candidate and the enrolment of the state, as the Parliamentary Library's "
    "Parliamentary Handbook publishes them. The state is one unit. The Handbook gives no informal or total votes, "
    "and the distribution of preferences is not stored; the outcomes are the senators the Handbook lists as elected."
    "{extra}"
)
SENATE_BLOCK_NOTE = " Each voter voted for several candidates, so the votes add up to more than the ballots cast."
SENATE_TICKET_NOTE = (
    " The Handbook credits each group's above-the-line ticket votes to the group's first candidate, so the candidates' "
    "votes together hold every formal vote."
)


def house_system(contest: Contest) -> str:
    day = contest.poll.day
    if day >= PREFERENTIAL:
        return HOUSE_SYSTEM
    if day.year < 1903:
        if contest.state == "TAS":
            return HARE_CLARK
        if contest.state == "SA":
            return HOUSE_BLOCK
        if contest.state == "QLD":
            return HOUSE_CONTINGENT
    return HOUSE_FPTP


def senate_system(contest: Contest) -> str:
    day = contest.poll.day
    if day.year < 1903 and contest.state == "TAS":
        return HARE_CLARK
    if day.year < 1919:
        return SENATE_BLOCK
    if day.year < 1949:
        return SENATE_PREFERENTIAL_BLOCK
    if day.year < 1984:
        return SENATE_STV
    return SENATE_GVT


def person_name(label: str) -> str:
    surname, _, given = label.partition(", ")
    if sum(ch.isupper() for ch in surname) > len(surname) / 2:
        surname = surname.upper()
    return base.person_name(given, surname) if given else base.person_name("", surname)


class Units:
    def __init__(self, p: Package, day: date) -> None:
        self.p = p
        self.day = day
        self.ids: set[str] = set()

    def division(self, code: str, name: str) -> str:
        id = key("unit", f"{self.day}:{code}:DIVISION")
        if id not in self.ids:
            self.ids.add(id)
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

    def state(self, state: str) -> str:
        id = key("unit", f"{self.day}:{state}:STATE")
        if id not in self.ids:
            self.ids.add(id)
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=AUSTRALIA,
                scheme_code="au.state",
                code=f"{self.day}:{state}",
                area_relation="equals",
                area_id=area("au.state", state),
                kind="aggregate",
            )
            self.p.name(id, STATE_NAMES[state], "en", "official")
        return id


def _result_set(p: Package, election: str, contest: str, method: str, tacit: bool = False) -> str:
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if tacit else "counted",
        source_id=HANDBOOK,
        status="active",
        is_default=True,
        completeness=0.0 if tacit else 1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return result_set


def _options(
    p: Package, contest: Contest, contest_id: str, result_set: str, unit: str, values: Values, tag: str
) -> None:
    marked = any(c.elected for c in contest.candidates)
    for position, candidate in enumerate(contest.candidates, 1):
        here = f"{tag}:{position}"
        party = party_id(candidate.party)
        option = key("option", here)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest_id,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=candidate.label,
        )
        person = key("person", here)
        p.add("person", id=person)
        p.name(person, person_name(candidate.label), "en", "official")
        p.add(
            "candidacy",
            id=key("candidacy", here),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.label,
            member_of_party_id=party,
            is_independent=party is None,
        )
        if candidate.votes:
            values.add(result_set, unit, option, 1, candidate.votes)
        if marked:
            p.add(
                "outcome", result_set_id=result_set, option_id=option, kind="elected" if candidate.elected else "lost"
            )


def house_contest(p: Package, contest: Contest, election: str, path: str, units: Units, values: Values) -> None:
    day = contest.poll.day
    elected = sum(c.elected for c in contest.candidates)
    if statewide(contest):
        district, unit, tag = area("au.state", contest.state), units.state(contest.state), contest.state
    else:
        code = division_code(contest.state, contest.name)
        district, unit, tag = area("au.division", code), units.division(code, contest.name), code
    if elected > 1 and not statewide(contest):
        raise ValueError(f"{day} {contest.name}: {elected} members elected")
    if elected == 0 and (contest.poll.division is None or day not in UNMARKED):
        raise ValueError(f"{day} {contest.name}: no member elected")
    unopposed = all(c.votes == 0 for c in contest.candidates)
    contest_id = key("contest", f"handbook:{day}", "house", tag)
    p.add(
        "contest",
        id=contest_id,
        election_id=election,
        kind="body_seats",
        body_id=HOUSE,
        district_area_id=district,
        magnitude=max(elected, 1),
        round_number=1,
        electoral_system_id=house_system(contest),
        default_result_kind="tacit" if unopposed else "counted",
    )
    p.name(contest_id, f"{contest.name} ({contest.state})", "en", "official")
    p.add("slug", path=f"{path}/house-{changeset.slug(contest.name)}", entity_id=contest_id, is_current=True)
    result_set = _result_set(p, election, contest_id, HOUSE_METHOD, unopposed)
    if unopposed:
        if len(contest.candidates) != elected:
            raise ValueError(f"{day} {contest.name}: no votes but {len(contest.candidates)} candidates")
        p.add(
            "note",
            id=key("note", result_set, "unopposed"),
            entity_id=result_set,
            kind="context",
            body_md=UNOPPOSED,
            lang="en",
        )
    if elected == 0:
        p.add(
            "note",
            id=key("note", result_set, "unmarked"),
            entity_id=result_set,
            kind="caveat",
            body_md=UNMARKED[day],
            lang="en",
        )
    _options(p, contest, contest_id, result_set, unit, values, f"handbook:{contest.poll.id}:house:{tag}")
    if not unopposed and elected == 1:
        values.add(result_set, unit, None, 4, sum(c.votes for c in contest.candidates))
    if contest.enrolment:
        values.add(result_set, unit, None, 2, contest.enrolment)


def senate_contest(p: Package, contest: Contest, election: str, path: str, units: Units, values: Values) -> None:
    day = contest.poll.day
    elected = sum(c.elected for c in contest.candidates)
    contest_id = key("contest", f"handbook:{day}", "senate", contest.state)
    system = senate_system(contest)
    p.add(
        "contest",
        id=contest_id,
        election_id=election,
        kind="body_seats",
        body_id=SENATE,
        district_area_id=area("au.state", contest.state),
        magnitude=elected,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest_id, f"Senate, {STATE_NAMES[contest.state]}", "en", "official")
    p.add("slug", path=f"{path}/senate-{contest.state.lower()}", entity_id=contest_id, is_current=True)
    extra = SENATE_BLOCK_NOTE if system == SENATE_BLOCK else SENATE_TICKET_NOTE if system == SENATE_GVT else ""
    result_set = _result_set(p, election, contest_id, SENATE_METHOD.format(extra=extra))
    unit = units.state(contest.state)
    _options(p, contest, contest_id, result_set, unit, values, f"handbook:{contest.poll.id}:senate:{contest.state}")
    if contest.enrolment:
        values.add(result_set, unit, None, 2, contest.enrolment)


def days() -> list[date]:
    return sorted({poll.day for poll in polls()})


def _title(poll: Poll) -> str:
    if poll.reps:
        return f"{poll.day.year} Australian federal election"
    return f"{poll.day.year} Australian Senate election"


def _by_title(bys: list[Poll]) -> str:
    names = [poll.division or "" for poll in bys]
    year = bys[0].day.year
    if len(names) == 1:
        return f"{year} {names[0]} by-election"
    return f"{year} {', '.join(names[:-1])} and {names[-1]} by-elections"


def cited(day: date) -> list[str]:
    files = []
    for poll in polls():
        if poll.day != day:
            continue
        files.append(candidates_url(poll.id))
        if poll.reps:
            for contest in house(poll):
                files += contest.urls
        if poll.senate and poll.division is None:
            for contest in senate(poll):
                files += contest.urls
    return list(dict.fromkeys(files))


def election(day: date) -> Package:
    held = [poll for poll in polls() if poll.day == day]
    main = [poll for poll in held if poll.division is None]
    bys = [poll for poll in held if poll.division is not None]
    titles = [_title(main[0])] if main else []
    if bys:
        titles.append(_by_title(bys))
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {' and the '.join(titles)}, by division, from the Parliamentary Handbook",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/handbook-geo", f"{SOURCE}/handbook-registry"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    cite(p, cited(day), geo_keys())
    units = Units(p, day)
    values = Values()
    for poll in main:
        election_id = key("election", f"handbook:{day}")
        path = f"au/{day}-{'general' if poll.reps else 'senate'}"
        p.add(
            "election",
            id=election_id,
            polity_id=AUSTRALIA,
            kind="general",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        p.name(election_id, _title(poll), "en", "official")
        p.add("slug", path=path, entity_id=election_id, is_current=True)
        if poll.reps:
            for contest in house(poll):
                house_contest(p, contest, election_id, path, units, values)
        if poll.senate:
            for contest in senate(poll):
                senate_contest(p, contest, election_id, path, units, values)
    if bys:
        election_id = key("election", f"handbook:{day}", "by_election")
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
        p.name(election_id, _by_title(bys), "en", "official")
        p.add("slug", path=path, entity_id=election_id, is_current=True)
        for poll in bys:
            for contest in house(poll):
                house_contest(p, contest, election_id, path, units, values)
    p.frame("result_value", values.frame())
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/handbook-geo", f"{SOURCE}/handbook-registry", *(f"{SOURCE}/{day}" for day in days())]


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        election(day).write()


def totals() -> list[str]:
    """How each general election's elected members per party compare with the Handbook's own seat counts."""
    rows = {int(str(row["Id"])): row for row in _list(ELECTIONS)}
    lines = []
    for poll in generals():
        row = rows[poll.id]
        for chamber, contests, counts in (
            ("House", house(poll) if poll.reps else [], row.get("RepsCounts")),
            ("Senate", senate(poll) if poll.senate else [], row.get("SenateCounts")),
        ):
            if not contests:
                continue
            ours = Counter(c.party or "Independent" for contest in contests for c in contest.candidates if c.elected)
            listed = counts if isinstance(counts, list) else []
            theirs = Counter({str(x["Party"]): _int(x["Count"]) for x in listed if isinstance(x, dict)})
            seats = sum(ours.values())
            same = ours == theirs
            lines.append(
                f"{poll.day} {chamber}: {seats} elected, Handbook summary {sum(theirs.values())}"
                + ("; party counts equal" if same else f"; differs {dict((ours - theirs) + (theirs - ours))}")
            )
    return lines
