"""India's 2009 and 2014 Lok Sabha general elections in eleven states, and the 2019 one in Uttar Pradesh, by polling
station, from the compilation of Form 20 returns by Jensenius, Chhibber, Alam, Gupta, and Somanathan.

The dataset ("Indian election data for polling stations and villages: National elections 2009-2019", Harvard
Dataverse, doi:10.7910/DVN/KKOWNJ, version 1.0, CC BY 4.0) holds one file per state and year with a row per polling
station: the parliamentary constituency, the assembly segment, the station's number, the votes for each candidate in
the order of the constituency's ballot, the votes for "None of the above" (from 2014), and the total. The authors
scraped the Form 20 sheets the Election Commission posted, parsed or typed them, and corrected the rows that did not
add up. The supplementary candidate lists give each candidate's ballot number, name, party abbreviation, and final
position; for 2009 Uttar Pradesh they also say, for each segment, whether its columns follow the ballot order or the
final positions. The mergefiles that link stations to census villages are not loaded.

Form 20 Part I holds the votes cast on the machines, so the postal ballots, counted for the whole constituency, are
not in these files.
"""

import csv
import functools
import importlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "in.jensenius"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
DOI = "doi:10.7910/DVN/KKOWNJ"
SITE = "https://dataverse.harvard.edu"
DATASET = f"{SITE}/dataset.xhtml?persistentId={DOI}"
METADATA = f"{SITE}/api/datasets/:persistentId/versions/1.0?persistentId={DOI}"
FILE = SITE + "/api/access/datafile/{}"
VOTES, VALID = 1, 4


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def other(pipeline: str, kind: str, *parts: object) -> str:
    """An id another pipeline minted, derived the way that pipeline derives it."""
    return str(uuid5(package.NAMESPACE, "\x1f".join([pipeline, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "dataverse")
FPTP = key("electoral_system", "fptp")
# The Lok Sabha that in.eci_stat holds, and the 2019 general election that in.form20_wb holds.
BODY = other("in.eci_stat", "body", "lok-sabha")
ELECTION_2019 = other("in.form20_wb", "election", 2019)


@dataclass(frozen=True)
class State:
    code: str  # the dataset's abbreviation
    census: str  # the 2011 census state code, which the Local Government Directory's constituency codes carry
    name: str
    plan: str  # the delimitation plan in.geo files the state's constituencies under


STATES = {
    s.code: s
    for s in (
        State("AP", "28", "Andhra Pradesh", "2008"),
        State("BH", "10", "Bihar", "2008"),
        State("GJ", "24", "Gujarat", "2008"),
        State("HP", "02", "Himachal Pradesh", "2008"),
        State("JH", "20", "Jharkhand", "pre"),
        State("MP", "23", "Madhya Pradesh", "2008"),
        State("PJ", "03", "Punjab", "2008"),
        State("RJ", "08", "Rajasthan", "2008"),
        State("UK", "05", "Uttarakhand", "2008"),
        State("UP", "09", "Uttar Pradesh", "2008"),
        State("WB", "19", "West Bengal", "2008"),
    )
}

# The Dataverse file id of each state's polling station file, by year.
PS_FILES = {
    2009: {
        "AP": 11587740,
        "BH": 11587757,
        "GJ": 11587773,
        "HP": 11587765,
        "JH": 11587777,
        "MP": 11587760,
        "PJ": 11587741,
        "RJ": 11587743,
        "UK": 11587767,
        "UP": 11587749,
        "WB": 11587738,
    },
    2014: {
        "AP": 11587733,
        "BH": 11587775,
        "GJ": 11587759,
        "HP": 11587739,
        "JH": 11587753,
        "MP": 11587764,
        "PJ": 11587771,
        "RJ": 11587742,
        "UK": 11587734,
        "UP": 11587746,
        "WB": 11587750,
    },
    2019: {"UP": 11587769},
}
CANDIDATES = {"2009": 11587938, "2009-UP": 11587936, "2014": 11587939, "2019": 11587942}
README, CODEBOOK = 11587880, 11587881


def urls() -> list[tuple[str, str]]:
    found = [(METADATA, ".json"), (FILE.format(README), ".rtf"), (FILE.format(CODEBOOK), ".pdf")]
    found += [(FILE.format(id), ".tab") for id in CANDIDATES.values()]
    found += [(FILE.format(id), ".tab") for files in PS_FILES.values() for id in files.values()]
    return found


def fetch() -> None:
    for url, suffix in urls():
        raw.fetch(SOURCE, url, suffix=suffix)


# ---- reading the files ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    number: int  # the ballot number (Cand_no)
    name: str
    party: str
    position: int  # the final position in the constituency


@dataclass
class Station:
    code: str  # the station's number as the file prints it, spaces collapsed
    total: int | None
    nota: int | None
    votes: dict[int, int]  # by ballot number


@dataclass
class Segment:
    number: int
    name: str
    stations: list[Station] = field(default_factory=list)


@dataclass
class Seat:
    state: str
    number: int
    candidates: dict[int, Candidate] = field(default_factory=dict)  # by ballot number
    segments: dict[int, Segment] = field(default_factory=dict)  # the segments stored
    left: dict[int, tuple[Segment, str]] = field(default_factory=dict)  # the segments left out, and why
    caveats: list[str] = field(default_factory=list)


def _text(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8")


def _rows(id: int) -> list[dict[str, str]]:
    """A file's rows as text, as Dataverse serves its tab-separated copy."""
    reader = csv.DictReader(io.StringIO(_text(FILE.format(id))), delimiter="\t")
    return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def _int(text: str) -> int | None:
    text = text.strip()
    if text in ("", "NA"):
        return None
    if not re.fullmatch(r"-?\d+(\.0+)?", text):
        raise ValueError(f"not a count: {text!r}")
    return int(float(text)) if "." in text else int(text)


STATE_NAMES = {s.name: s.code for s in STATES.values()}


@functools.cache
def candidate_lists(year: int) -> dict[tuple[str, int], list[dict[str, str]]]:
    """The candidate list rows of each state's constituencies, by state code and constituency number."""
    found: dict[tuple[str, int], list[dict[str, str]]] = {}
    files = [CANDIDATES[str(year)]] + ([CANDIDATES["2009-UP"]] if year == 2009 else [])
    for id in files:
        for row in _rows(id):
            code = STATE_NAMES.get(row["State_name"])
            if code is None or code not in PS_FILES[year]:
                continue
            if year == 2009 and code == "UP" and id != CANDIDATES["2009-UP"]:
                continue
            found.setdefault((code, int(row["PC_id"])), []).append(row)
    return found


def _station_code(text: str) -> str:
    return " ".join(text.split())


# A cell that is not a whole number, with the value the station's printed total proves: 2009 Madhya Pradesh,
# segment 48 (Maharajpur), station 30 prints "0.3" for candidate 14, and its other cells add to 344 of 347.
CORRECTIONS = {(2009, "MP", 48, "30", 14): 3}


@dataclass
class _Row:
    station: Station
    columns: dict[int, int]  # the votes by column of the file
    positional: bool  # the columns follow the final positions, not the ballot numbers


@functools.cache
def seats(year: int, state: str) -> dict[int, Seat]:
    """Each constituency of a state's file, with its candidates and its stations' votes by ballot number."""
    lists = candidate_lists(year)
    by_position: dict[tuple[int, int], bool] = {}
    if year == 2009 and state == "UP":
        for (code, pc), rows in lists.items():
            for row in rows:
                if code == "UP":
                    by_position[(pc, int(row["AC_no"]))] = row["Ordered_by_position"] == "1"
    found: dict[int, Seat] = {}
    rows: dict[int, dict[int, list[_Row]]] = {}
    names: dict[int, str] = {}
    seen: Counter[tuple[int, str]] = Counter()
    for row in _rows(PS_FILES[year][state]):
        if STATES[state].census != f"{int(row['State_no2011']):02d}":
            raise ValueError(f"{year} {state}: a row of state {row['State_no2011']}")
        pc, ac = int(row["PC_no"]), int(row["AC_no"])
        names.setdefault(ac, row["AC_name"])
        code = _station_code(row["PS_id"])
        seen[(ac, code)] += 1
        if seen[(ac, code)] > 1:
            code = f"{code}#{seen[(ac, code)]}"
        columns: dict[int, int] = {}
        for k, value in row.items():
            if not k.startswith("Votes_cand"):
                continue
            column = int(k.removeprefix("Votes_cand"))
            fixed = CORRECTIONS.get((year, state, ac, row["PS_id"], column))
            number = fixed if fixed is not None else _int(value)
            if number is not None:
                columns[column] = number
        station = Station(code, _int(row["Votes_total"]), _int(row["Votes_NOTA"]), {})
        found.setdefault(pc, Seat(state, pc))
        rows.setdefault(pc, {}).setdefault(ac, []).append(_Row(station, columns, by_position.get((pc, ac), False)))
    for pc, seat in found.items():
        _assign(year, seat, _listed(year, state, pc, lists.get((state, pc), [])), rows[pc], names)
    return found


Listed = tuple[str, str, int]  # a candidate's name, party, and final position


def _listed(year: int, state: str, pc: int, rows: list[dict[str, str]]) -> dict[Listed, set[int]]:
    """Each candidate of the list with the ballot numbers the list gives it (more than one where it repeats rows)."""
    if not rows:
        raise ValueError(f"{year} {state} {pc}: no candidate list")
    found: dict[Listed, set[int]] = {}
    for row in rows:
        who = (" ".join(row["Cand_name"].split()), row["Cand_party"].strip(), int(row["Cand_position"]))
        found.setdefault(who, set()).add(int(row["Cand_no"]))
    positions = Counter(who[2] for who in found)
    if any(n > 1 for n in positions.values()):
        raise ValueError(f"{year} {state} {pc}: two candidates share a position")
    return found


def _fit(rows: list[_Row], n: int) -> str | None:
    """Why a segment's columns cannot be the constituency's ballot of n candidates, or None when they can be."""
    used = max((c for r in rows for c in r.columns), default=0)
    voted = max((c for r in rows for c, v in r.columns.items() if v), default=0)
    if voted > n:
        return f"its rows carry votes in {voted} columns, for a ballot of {n} candidates"
    if used < n:
        return f"its rows carry {used} columns, for a ballot of {n} candidates"
    return None


def _assign(
    year: int, seat: Seat, listed: dict[Listed, set[int]], segments: dict[int, list[_Row]], names: dict[int, str]
) -> None:
    """Give each station's columns to the candidates: by ballot number, or by final position in the segments the
    authors flag as ordered that way.

    A segment whose columns cannot be the constituency's ballot (votes beyond its last candidate, or fewer columns than
    candidates, as when a segment's rows come from another contest's sheet) is left out and named in the note. Where
    the list gives two or more candidates the same set of ballot numbers, the candidates take those numbers in the
    order of their final positions against the order of the columns' sums; columns whose sums tie are not stored.
    """
    n = len(listed)
    given = sorted({k for owned in listed.values() for k in owned})
    misnumbered = None
    if given != list(range(1, n + 1)):
        misnumbered = (
            f"the authors' candidate list numbers its {n} candidates {', '.join(map(str, given))}, which do not "
            "name the file's columns"
        )
    kept: dict[int, list[_Row]] = {}
    for ac, rows in sorted(segments.items()):
        segment = Segment(ac, names[ac], [r.station for r in rows])
        why = misnumbered or _fit(rows, n)
        if why:
            seat.left[ac] = (segment, why)
        else:
            kept[ac] = rows
            seat.segments[ac] = segment
    numbers: dict[int, Listed] = {}
    groups: dict[frozenset[int], list[Listed]] = {}
    for who, owned in listed.items():
        groups.setdefault(frozenset(owned), []).append(who)
    sums: Counter[int] = Counter()
    for rows in kept.values():
        for r in rows:
            if not r.positional:
                sums.update(r.columns)
    for owned, people in groups.items():
        if misnumbered:
            for k, who in zip(sorted(owned), sorted(people, key=lambda w: w[2]), strict=True):
                numbers[k] = who
            continue
        if len(owned) != len(people):
            raise ValueError(f"{year} {seat.state} {seat.number}: candidates {people} share ballot numbers {owned}")
        if len(people) == 1:
            numbers[next(iter(owned))] = people[0]
            continue
        named = " and ".join(_title(w[0]) for w in sorted(people, key=lambda w: w[2]))
        listed_as = ", ".join(map(str, sorted(owned)))
        if len({sums[k] for k in owned}) != len(owned):
            seat.caveats.append(
                f"The authors' candidate list gives {named} the same ballot numbers ({listed_as}), and those columns "
                "add to the same total, so neither candidate's polling-station votes are stored."
            )
            for k, who in zip(sorted(owned), sorted(people, key=lambda w: w[2]), strict=True):
                numbers[-k] = who
            continue
        for k, who in zip(sorted(owned, key=lambda k: -sums[k]), sorted(people, key=lambda w: w[2]), strict=True):
            numbers[k] = who
        seat.caveats.append(
            f"The authors' candidate list gives {named} the same ballot numbers ({listed_as}); each takes the column "
            "whose polling-station sum ranks as the list's final positions do."
        )
    seat.candidates = {
        abs(k): Candidate(abs(k), w[0], w[1], w[2]) for k, w in sorted(numbers.items(), key=lambda x: abs(x[0]))
    }
    if len(seat.candidates) != n:
        raise ValueError(f"{year} {seat.state} {seat.number}: a ballot number belongs to two candidates")
    stored = {k for k in numbers if k > 0}
    tied = {-k for k in numbers if k < 0}
    by_position = {w[2]: abs(k) for k, w in numbers.items()}
    for rows in kept.values():
        for r in rows:
            for column, value in r.columns.items():
                k = by_position.get(column) if r.positional else column
                if k in stored:
                    r.station.votes[k] = value
                elif value and k not in tied:
                    raise ValueError(f"{year} {seat.state} {seat.number}: column {column} has votes and no candidate")


def _title(text: str) -> str:
    return " ".join(w.capitalize() if w.isupper() and len(w) > 1 else w for w in text.split())


# ---- places ----------------------------------------------------------------------------------------------------------


def _geo_id(scheme: str, code: str, plan: str) -> str:
    return other("in.geo", "area", scheme, code, plan)


@functools.cache
def _geo() -> tuple[dict[str, tuple[str, str | None, str]], dict[str, tuple[str, str]]]:
    """in.geo's parliamentary seats (name, reservation, plan) and assembly seats (name, plan) by code."""
    geo = importlib.import_module("ea.sources.in.geo")
    acs, _, _ = geo.assembly()
    return (
        {code: (s.name, s.kind, s.plan) for code, s in geo.parliament().items()},
        {code: (s.name, s.plan) for code, s in acs.items()},
    )


def pc_code(state: State, number: int) -> str:
    return f"{state.census}{number:02d}"


def ac_code(state: State, number: int) -> str:
    """in.geo keeps the Telangana segments of undivided Andhra Pradesh (1 to 119) under Telangana's code, 36."""
    census = "36" if state.code == "AP" and number <= 119 else state.census
    return f"{census}{number:03d}"


def pc_area(state: State, number: int) -> str:
    code = pc_code(state, number)
    pcs, _ = _geo()
    if code not in pcs or pcs[code][2] != state.plan:
        raise ValueError(f"in.geo has no parliamentary constituency {code} on the plan {state.plan}")
    return _geo_id("in.pc", code, state.plan)


def ac_area(state: State, number: int) -> str | None:
    """The segment's area in in.geo, or None where the layer has no feature for it."""
    code = ac_code(state, number)
    _, acs = _geo()
    found = acs.get(code)
    return _geo_id("in.ac", code, found[1]) if found else None


def pc_name(state: State, number: int) -> tuple[str, str | None]:
    pcs, _ = _geo()
    name, kind, _ = pcs[pc_code(state, number)]
    return name, kind


def ac_name(state: State, number: int, printed: str) -> str:
    _, acs = _geo()
    found = acs.get(ac_code(state, number))
    if found:
        return found[0]
    return _title(re.sub(r"\s*\((SC|ST)\)\s*$", "", printed, flags=re.IGNORECASE))


# ---- packages --------------------------------------------------------------------------------------------------------

ELECTION_DAY = 1
GENERALS = {
    2009: (key("election", 2009), "fifteenth", date(2009, 4, 16), date(2009, 5, 13), "five"),
    2014: (key("election", 2014), "sixteenth", date(2014, 4, 7), date(2014, 5, 12), "nine"),
}
METHOD = (
    "Polling-station votes from the compilation of Form 20 returns by Jensenius, Chhibber, Alam, Gupta, and "
    "Somanathan (Harvard Dataverse, doi:10.7910/DVN/KKOWNJ, CC BY 4.0), who scraped the Form 20 sheets the Election "
    "Commission of India posted, parsed or typed them, and corrected rows that did not add up. Each station is nested "
    "in its assembly segment. The votes are those cast on the voting machines (Form 20 Part I); the postal ballots, "
    "counted for the whole constituency, are not included, so the stations add up to less than the declared total. "
    "Candidates, parties, and who was elected come from the authors' candidate lists (final position 1 is the member "
    "elected)."
)


def package_key(year: int, state: str) -> str:
    return f"{SOURCE}/ls-{year}-{state.lower()}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(package_key(y, s) for y, files in PS_FILES.items() for s in files)]


def party(code: str) -> str:
    return key("party", code)


def _artifacts(p: Package, ids: list[int]) -> None:
    for id in ids:
        p.artifact(raw.cached(SOURCE, FILE.format(id)), SOURCE_ID)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The polling-station compilation of the 2009, 2014, and 2019 Lok Sabha general elections as a source, the "
        "2009 and 2014 general elections, and the parties of the candidates it covers",
    )
    p.external.add(INDIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="academic",
        publisher="Francesca R. Jensenius, Pradeep Chhibber, Sanjeer Alam, Pranav Gupta, and Madhavan Somanathan",
        title="Indian election data for polling stations and villages: National elections 2009-2019 (version 1.0)",
        url="https://doi.org/10.7910/DVN/KKOWNJ",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution=(
            "Jensenius, F. R., Chhibber, P., Alam, S., Gupta, P., and Somanathan, M. (2025). Indian election data "
            "for polling stations and villages: National elections 2009-2019. Harvard Dataverse, V1, "
            "doi:10.7910/DVN/KKOWNJ; described in Scientific Data (2025)."
        ),
        notes=(
            "Compiled from the Form 20 sheets the Election Commission of India posted. The polling-station files and "
            "the candidate lists are read; the mergefiles that link stations to 2011 census villages are not."
        ),
    )
    p.artifact(raw.cached(SOURCE, METADATA), SOURCE_ID)
    _artifacts(p, [README, CODEBOOK, *CANDIDATES.values()])
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=INDIA,
        valid_from=date(1951, 1, 1),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Each constituency elects one member by plurality of the votes.",
        lang="en",
    )
    for year, (id, ordinal, first, last, phases) in GENERALS.items():
        p.add(
            "election",
            id=id,
            polity_id=INDIA,
            kind="general",
            date_from=first,
            date_to=last,
            date_precision="day",
            status="held",
        )
        p.name(id, f"{year} Indian general election", "en", "official")
        p.name(id, f"General Elections, {year} ({ordinal.capitalize()} Lok Sabha)", "en", "short", False)
        p.add("slug", path=f"in/{year}-general", entity_id=id, is_current=True)
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="context",
            body_md=f"The general election to the {ordinal} Lok Sabha, polled in {phases} phases from "
            f"{first:%-d %B} to {last:%-d %B %Y}.",
            lang="en",
        )
    codes: set[str] = set()
    for year, files in PS_FILES.items():
        for state in files:
            for seat in seats(year, state).values():
                if seat.segments:
                    codes |= {c.party for c in seat.candidates.values() if c.party != "IND"}
    for code in sorted(codes):
        p.add("party", id=party(code), kind="party", polity_id=INDIA)
        p.name(party(code), code, "en", "abbreviation")
    return p


def _segment_list(items: list[tuple[int, str]]) -> str:
    return "; ".join(f"{number} {name}" for number, name in items)


def election(year: int, code: str) -> Package:
    state = STATES[code]
    p = Package(
        package_key(year, code),
        SOURCE,
        f"The {year} Lok Sabha general election in {state.name} by polling station, from the compilation of Form 20 "
        "returns by Jensenius, Chhibber, and colleagues",
        depends=[f"{SOURCE}/registry", "in.geo/geo"],
    )
    # The polling-station scheme is in production already (in.form20_wb and in.form20_s3waas declare it).
    p.external |= {INDIA, BODY, "in.ps"}
    general = GENERALS[year][0] if year in GENERALS else ELECTION_2019
    if year not in GENERALS:
        p.external.add(general)
    _artifacts(p, [PS_FILES[year][code]])
    units: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    off: list[str] = []
    for seat in sorted(seats(year, code).values(), key=lambda s: s.number):
        if seat.segments:
            off += _contest(p, year, state, general, seat, units, values)
    if off:
        p.accept(
            "outcomes.winner",
            "The member the candidate list declares elected does not lead the polling-station votes the file holds in "
            + "; ".join(off)
            + ". The stations hold the machine votes of the segments stored, without the postal ballots.",
        )
    p.frame("reporting_unit", pl.DataFrame(units, infer_schema_length=None))
    p.frame("result_value", pl.DataFrame(values, infer_schema_length=None))
    return p


def _contest(
    p: Package,
    year: int,
    state: State,
    general: str,
    seat: Seat,
    units: list[dict[str, object]],
    values: list[dict[str, object]],
) -> list[str]:
    tag = f"{year}:{state.census}:{seat.number}"
    contest = key("contest", tag)
    name, _ = pc_name(state, seat.number)
    district = pc_area(state, seat.number)
    p.add(
        "contest",
        id=contest,
        election_id=general,
        kind="body_seats",
        body_id=BODY,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=FPTP,
        default_result_kind="counted",
    )
    p.name(contest, f"{name}, {state.name}", "en", "official")
    p.add(
        "slug",
        path=f"in/{year}-general/{slug(state.name)}/{seat.number}-{slug(name)}",
        entity_id=contest,
        is_current=True,
    )
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=general,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
    )
    options: dict[int, str] = {}
    for c in seat.candidates.values():
        option, person = key("option", tag, c.number), key("person", tag, c.number)
        coded = c.party != "IND"
        p.add("person", id=person)
        p.name(person, _title(c.name), "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(c.party) if coded else None,
            ballot_position=c.number,
            ballot_label=_title(c.name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, c.number),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=party(c.party) if coded else None,
            is_independent=True if not coded else None,
        )
        won = c.position == 1
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost", seats=int(won))
        options[c.number] = option
    nota = key("option", tag, "nota") if year >= 2014 else None
    if nota:
        p.add("contest_option", id=nota, contest_id=contest, kind="none_of_the_above", ballot_label="NOTA")
    sums: Counter[int] = Counter()
    unlocated: list[tuple[int, str]] = []
    differ = 0
    stations = 0
    for segment in sorted(seat.segments.values(), key=lambda s: s.number):
        label = ac_name(state, segment.number, segment.name)
        area = ac_area(state, segment.number)
        if area is None:
            unlocated.append((segment.number, label))
        unit = key("unit", tag, segment.number)
        units.append(
            {
                "id": unit,
                "polity_id": INDIA,
                "scheme_code": "in.ac",
                "code": f"{year}:{state.census}:{segment.number}",
                "area_relation": "equals" if area else "within",
                "area_id": area or district,
                "kind": "standard",
            }
        )
        p.name(unit, f"{segment.number} {label}", "en", "official")
        for station in segment.stations:
            stations += 1
            ps = key("unit", tag, segment.number, station.code)
            units.append(
                {
                    "id": ps,
                    "polity_id": INDIA,
                    "scheme_code": "in.ps",
                    "code": f"{year}:{state.census}:{segment.number}:{station.code}",
                    "parent_id": unit,
                    "area_relation": "within",
                    "area_id": area or district,
                    "kind": "standard",
                }
            )
            for number, votes in sorted(station.votes.items()):
                values.append(_value(result_set, ps, options[number], votes))
                sums[number] += votes
            if nota and station.nota is not None:
                values.append(_value(result_set, ps, nota, station.nota))
            if station.total is not None and station.total != sum(station.votes.values()) + (station.nota or 0):
                differ += 1
    method = METHOD
    stored = len(seat.segments)
    method += (
        f" The file holds {stored + len(seat.left)} segments of this constituency, with "
        f"{stations + sum(len(x.stations) for x, _ in seat.left.values()):,} stations"
    )
    if seat.left:
        method += (
            f"; {stored} are stored and {len(seat.left)} left out, because their columns cannot be this "
            "constituency's ballot: "
            + "; ".join(
                f"{n} {ac_name(state, n, s.name)} ({len(s.stations)} stations; {why})"
                for n, (s, why) in sorted(seat.left.items())
            )
            + "."
        )
    else:
        method += ", all stored."
    if unlocated:
        method += (
            " The constituency layer has no outline for segment(s) "
            + _segment_list(unlocated)
            + ", so their stations are placed within the constituency."
        )
    if differ:
        method += (
            f" At {differ} of the {stations} stations, the candidates' votes (and NOTA) do not add to the total the "
            "file prints; the candidates' votes are stored as the file gives them, and the totals are not stored."
        )
    method += "".join(f" {caveat}" for caveat in seat.caveats)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    winner = next(c for c in seat.candidates.values() if c.position == 1)
    top = max(sums.values(), default=0)
    if sums[winner.number] < top:
        return [f"{name} ({_title(winner.name)}, {sums[winner.number]:,} against {top:,})"]
    return []


def _value(result_set: str, unit: str, option: str | None, value: int) -> dict[str, object]:
    return {
        "result_set_id": result_set,
        "unit_id": unit,
        "option_id": option,
        "channel": ELECTION_DAY,
        "measure": VOTES,
        "count_round": 0,
        "flags": 0,
        "value": value,
    }


def parse() -> None:
    registry().write()
    for year, files in PS_FILES.items():
        for state in files:
            election(year, state).write()


def emit() -> None:
    package.run("load", packages())
