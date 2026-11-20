"""Alberta Senate nominee elections, 2004, 2012, and 2021.

Alberta's electors choose nominees whom the Prime Minister is asked to summon to the Senate to fill the province's
vacancies. Each elector marks up to as many candidates as there are nominees to choose, and the candidates with the
most votes are selected, so each election is one province-wide contest decided by block vote.

Elections Alberta publishes poll-by-poll workbooks for the 2004 and 2012 votes, held with the provincial general
elections, with one sheet per electoral division, and a tabulated summary by electoral division whose provincial
totals are stored here as declared. The 2021 vote, held with the municipal elections, was counted by the
municipalities, and Elections Alberta publishes it by municipality, where a municipality that conducted the vote
for a summer village, First Nation, improvement district, or special area reports their ballots in its own total.

The 1989 and 1998 votes were also run with the municipal elections, but Elections Alberta publishes no results for
them, so they are left out.
"""

import re
import unicodedata
from datetime import date, timedelta
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "ca.ab_senate"
UPLOADS = "https://www.elections.ab.ca/wp-content/uploads/"
PAGE = "https://www.elections.ab.ca/elections/run-as-a-senate-candidate/historical-senate-election-results/"
RESULTS_2021 = "https://officialresults.elections.ab.ca/orResultsPGESenate67S.cfm?MODE=exProv&EventId=67"
# Production holds Canada, Alberta, the province's area, the electoral divisions, the 2012 division units, and the
# parties below under the ids their first pipelines gave them.
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
ALBERTA = "01a13fb6-eab7-7501-a0f9-4907b2d3060e"
ALBERTA_AREA = "01a13fb6-eab7-7501-a0f9-48fa1f7bbf15"
PARTIES = {
    "PC": "1714886d-d33d-589f-9302-ce8c3ab5e641",
    "WAP": "809584df-5a13-55b0-b870-a34b60b5874f",
    "EPA": "e7747a41-cb43-5ceb-a03a-2562c9a0a428",
    "CPC": "01a13fb6-eb6e-74dc-99cc-2a93ca3560f7",
    "PPC": "01a13fb6-eb6e-74dc-99cc-2a9817e40ef8",
}
# Parties production lacks: source-local keys.
LOCAL_PARTIES = {"AA": "Alberta Alliance Party"}
INDEPENDENT = "IND"


class Vote:
    def __init__(self, day: date, seats: int, poll: str | None, summary: str | None, suffix: str) -> None:
        self.day = day
        self.seats = seats
        self.poll = poll
        self.summary = summary
        self.suffix = suffix


VOTES = {
    2004: Vote(date(2004, 11, 22), 4, "2004SNEPollByPollResults.xls", "2004SNETabulatedSummary.xls", ".xls"),
    2012: Vote(date(2012, 4, 23), 3, "2012SNEPollByPollResults.xlsx", "2012SNETabulatedSummary.xlsx", ".xlsx"),
    2021: Vote(date(2021, 10, 18), 3, None, None, ".xlsx"),
}
# The map each provincial vote used, and the date the division areas of that map take effect.
MAPS = {2004: ("2003", date(2004, 11, 22)), 2012: ("2010", date(2012, 4, 23))}
TURNOUT_OVER = {
    2004: "The 2004 poll-by-poll workbook prints more ballots than names on the list at three polls (Calgary-Mackay "
    "36, Edmonton-Manning 3, and Edmonton-Mill Creek 14), as the source states them; electors who registered at "
    "the poll are not added to its list.",
}
NAME_FIXES = {"Mccormack": "McCormack"}
POLL_METHOD = (
    "Counted by the returning officers of the electoral divisions at the polls of the provincial general election, "
    "as published by Elections Alberta poll by poll. Each elector could mark up to {seats} candidates, so the "
    "candidates' votes add up to more than the valid ballots. Registered voters are the names on the list of "
    "electors; ballots cast add the valid, spoiled, declined, and rejected ballots, and invalid ballots add the "
    "spoiled, declined, and rejected ones. A declined ballot is one an elector returned to the officials without "
    "marking it. Advance polls, special (mail-in) ballots, and some mobile polls are reported for the whole "
    "division and have no names on the list."
)
MUNICIPAL_METHOD = (
    "Counted by the municipalities at the polls of the 2021 municipal elections, and published by Elections Alberta "
    "by municipality. Each elector could mark up to 3 candidates, so the candidates' votes add up to more than the "
    "ballots. Ballots cast are the electors who voted. Blank ballots are those rejected as blank, which is how an "
    "elector declines the ballot under the Local Authorities Election Act, and invalid ballots are those rejected "
    "for marking more than 3 candidates or for an unclear intent. Elections Alberta publishes no count of valid "
    "ballots or of electors by municipality. A municipality that ran the vote for a summer village, First Nation, "
    "improvement district, or special area reports those ballots in its own total."
)
SYSTEM_NOTE = (
    "Each elector marks up to as many candidates as there are nominees to choose, and the candidates with the most "
    "votes are selected as nominees, whom the Prime Minister is asked to summon to the Senate to fill vacancies "
    "relating to Alberta."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


EA_SOURCE = key("source", "elections-alberta")
OFFICE = key("office", "senate-nominee")
SYSTEM = key("electoral_system", "block")


def url(year: int, which: str) -> str:
    vote = VOTES[year]
    if year == 2021:
        return RESULTS_2021
    name = vote.poll if which == "poll" else vote.summary
    assert name is not None
    return UPLOADS + name


def files(year: int) -> list[str]:
    return [RESULTS_2021] if year == 2021 else [url(year, "poll"), url(year, "summary")]


def fetch() -> None:
    for year, vote in VOTES.items():
        for link in files(year):
            raw.fetch(SOURCE, link, suffix=vote.suffix)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{vote.day}" for vote in VOTES.values())]


def parse() -> None:
    registry().write()
    for year in VOTES:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def title(text: str) -> str:
    words = " ".join(part.capitalize() for part in re.split(r"\s+", text.strip()))
    words = re.sub(r"(?<=[-'.(])([a-z])", lambda m: m.group(1).upper(), words)
    for wrong, right in NAME_FIXES.items():
        words = re.sub(rf"\b{wrong}\b", right, words)
    return words


def number(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip().split("\n")[0].replace(",", "")
    if text == "":
        return None
    return int(float(text))


def sheets(year: int, which: str) -> dict[str, pl.DataFrame]:
    path = raw.cached(SOURCE, url(year, which)).path
    return pl.read_excel(path, sheet_id=0, has_header=False, engine="calamine")


def party_id(code: str) -> str | None:
    if code == INDEPENDENT:
        return None
    if code in PARTIES:
        return PARTIES[code]
    return key("party", code)


class Candidate:
    def __init__(self, label: str, party: str, column: int) -> None:
        self.label = label
        self.party = party
        self.column = column


def _poll_candidates(header: tuple[object, ...]) -> list[Candidate]:
    """The candidates of a poll-by-poll sheet: given names, surname, and party, one line each."""
    found = []
    for column, cell in enumerate(header):
        if column < 3:
            continue
        text = str(cell or "")
        if text.replace("\n", " ").strip().upper().startswith("VALID"):
            break
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        found.append(Candidate(title(" ".join(lines[:-1])), lines[-1], column))
    return found


def _municipal_candidates(header: tuple[object, ...]) -> list[Candidate]:
    """The candidates of the 2021 workbook, printed as surname, given names, and party."""
    found = []
    for column, cell in enumerate(header):
        if column < 3 or str(cell).startswith("Rejected"):
            continue
        words = str(cell).split()
        surname, given, party = words[0], words[1:-1], words[-1]
        found.append(Candidate(title(" ".join([*given, surname])), party, column))
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Senate nominee office, its block-vote system, Elections Alberta as a source, and the schemes of the "
        "polls and municipalities that reported Alberta's Senate nominee elections",
    )
    p.external |= {ALBERTA}
    p.add(
        "source",
        id=EA_SOURCE,
        kind="official_body",
        publisher="Elections Alberta",
        title="Senate election results",
        url=PAGE,
    )
    for year in VOTES:
        for link in files(year):
            p.artifact(raw.cached(SOURCE, link), EA_SOURCE)
    p.add("office", id=OFFICE, polity_id=ALBERTA, kind="other")
    p.name(OFFICE, "Senate nominee for Alberta", "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ALBERTA,
        valid_from=None,
        valid_to=None,
        rules={"formula": "block"},
        rules_version=1,
    )
    p.name(SYSTEM, "Alberta Senate nominee block vote", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for code, text in LOCAL_PARTIES.items():
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=ALBERTA)
        p.name(id, text, "en", "official")
        p.name(id, code, "en", "abbreviation", False)
    p.add(
        "scheme",
        code="ca-ab.poll",
        polity_id=ALBERTA,
        kind="precinct",
        rank=4,
        noun="poll",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca-ab.municipality",
        polity_id=ALBERTA,
        kind="administrative",
        rank=3,
        noun="municipality",
        code_system=None,
    )
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def _declare(p: Package, result_set: str, option: str | None, measure: int, value: int | None) -> None:
    if value is not None:
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)


def _poll_kind(name: str) -> str:
    upper = name.upper()
    if upper.startswith("SPECIAL BALLOT"):
        return "postal_district"
    if upper.startswith("ADVANCE"):
        return "aggregate"
    if upper.startswith("MOBILE"):
        return "mobile"
    return "standard"


def _division_units(p: Package, year: int) -> dict[int, str]:
    """The division units: production holds those of the 2012 general election; the 2004 ones are minted here."""
    day = VOTES[year].day
    vintage, start = MAPS[year]
    units: dict[int, str] = {}
    for ed in range(1, 88 if year == 2012 else 84):
        area = str(uuid5(package.NAMESPACE, f"area:ca.ab@{start}\x1f{vintage}:{ed}"))
        p.external.add(area)
        if year == 2012:
            unit = str(uuid5(package.NAMESPACE, f"unit:ca.ab@{day}\x1f{day}:{ed}"))
            p.external.add(unit)
        else:
            p.external.add("ca.ab")
            unit = key("unit", day, ed)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ALBERTA,
                scheme_code="ca.ab",
                code=f"{day}:{ed}",
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        units[ed] = unit
    return units


def _areas(year: int) -> dict[int, str]:
    vintage, start = MAPS[year]
    return {
        ed: str(uuid5(package.NAMESPACE, f"area:ca.ab@{start}\x1f{vintage}:{ed}"))
        for ed in range(1, 88 if year == 2012 else 84)
    }


def _summary(year: int) -> tuple[list[str], dict[str, int]]:
    """The provincial totals of the tabulated summary: each candidate's votes by column, and the ballot accounting."""
    frame = next(iter(sheets(year, "summary").values()))
    rows = frame.rows()
    total = next(row for row in rows if str(row[1] or "").strip().upper() == "TOTAL")
    if year == 2004:
        header = next(row for row in rows if str(row[3] or "").strip() and row[0] is None)
        names = [str(cell) for cell in header[3:13]]
        votes = [number(cell) or 0 for cell in total[3:13]]
        figures = {
            "registered": number(total[2]) or 0,
            "valid": number(total[14]) or 0,
            "spoiled": number(total[15]) or 0,
            "declined": number(total[16]) or 0,
            "rejected": number(total[18]) or 0,
        }
    else:
        header = rows[0]
        names = [str(cell) for cell in header[3:16]]
        votes = [number(cell) or 0 for cell in total[3:16]]
        figures = {
            "registered": number(total[2]) or 0,
            "valid": number(total[17]) or 0,
            "spoiled": number(total[18]) or 0,
            "declined": number(total[19]) or 0,
            "rejected": number(total[20]) or 0,
        }
    return names, {**{f"votes:{index}": value for index, value in enumerate(votes)}, **figures}


def _election(p: Package, year: int) -> tuple[str, str, str]:
    vote = VOTES[year]
    day = vote.day
    election = key("election", day)
    p.add("election", id=election, polity_id=ALBERTA, kind="special", date_from=day, date_to=day, status="held")
    p.name(election, f"{year} Alberta Senate nominee election", "en", "official")
    path = f"ca/ab/{day}-senate-nominee"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=OFFICE,
        district_area_id=ALBERTA_AREA,
        magnitude=vote.seats,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Alberta Senate nominee election", "en", "official")
    p.add("slug", path=f"{path}/senate-nominees", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=EA_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    return election, contest, result_set


def _options(
    p: Package, year: int, contest: str, result_set: str, candidates: list[Candidate], totals: list[int]
) -> list[str]:
    """One option and candidacy for each candidate, with the declared total and the outcome."""
    vote = VOTES[year]
    order = sorted(range(len(candidates)), key=lambda index: -totals[index])
    if totals[order[vote.seats - 1]] == totals[order[vote.seats]]:
        raise ValueError(f"{year}: a tie for the last nominee")
    chosen = set(order[: vote.seats])
    options = []
    for index, candidate in enumerate(candidates):
        tag = f"{vote.day}:{slug(candidate.label)}"
        person = key("person", tag)
        p.add("person", id=person, birth_precision=None)
        p.name(person, candidate.label, "en", "official")
        party = party_id(candidate.party)
        if party is not None and candidate.party in PARTIES:
            p.external.add(party)
        option = key("option", tag)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=index + 1,
            ballot_label=candidate.label,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.label,
            member_of_party_id=party,
            is_independent=candidate.party == INDEPENDENT,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if index in chosen else "lost")
    return options


def election(year: int) -> Package:
    return _municipal(year) if year == 2021 else _polls(year)


def _polls(year: int) -> Package:
    vote = VOTES[year]
    day = vote.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} Alberta Senate nominee election, held with the provincial general election, by poll",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {ALBERTA, ALBERTA_AREA}
    for link in files(year):
        p.uses(raw.cached(SOURCE, link))
    if year in TURNOUT_OVER:
        p.accept("accounting.turnout", TURNOUT_OVER[year])
    _, contest, result_set = _election(p, year)
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=POLL_METHOD.format(seats=vote.seats),
        lang="en",
    )
    names, declared = _summary(year)
    workbook = sheets(year, "poll")
    first = next(iter(workbook.values()))
    candidates = _poll_candidates(first.row(0))
    summary_names = [re.sub(r"\s+", " ", name.split("\n")[0] if "\n" in name else name).strip() for name in names]
    if len(summary_names) != len(candidates):
        raise ValueError(f"{year}: the summary lists {len(summary_names)} candidates, the polls {len(candidates)}")
    totals = [declared[f"votes:{index}"] for index in range(len(candidates))]
    options = _options(p, year, contest, result_set, candidates, totals)
    for index, option in enumerate(options):
        _declare(p, result_set, option, 1, totals[index])
    _declare(p, result_set, None, 2, declared["registered"])
    _declare(p, result_set, None, 4, declared["valid"])
    invalid = declared["spoiled"] + declared["declined"] + declared["rejected"]
    _declare(p, result_set, None, 5, invalid)
    _declare(p, result_set, None, 3, declared["valid"] + invalid)
    divisions = _division_units(p, year)
    areas = _areas(year)
    for sheet, frame in workbook.items():
        ed = int(sheet)
        header = frame.row(0)
        if [c.label for c in _poll_candidates(header)] != [c.label for c in candidates]:
            raise ValueError(f"{year} division {ed}: the candidates differ from the first sheet")
        columns = {str(cell or "").replace("\n", " ").strip().upper(): index for index, cell in enumerate(header)}
        seen: set[str] = set()
        for row in frame.rows()[1:]:
            code = str(row[0] or "").strip()
            name = str(row[1] or "").strip()
            if not code or code.startswith("V=") or name.upper() == "TOTAL":
                continue
            if code in seen:
                raise ValueError(f"{year} division {ed}: poll {code} appears twice")
            seen.add(code)
            unit = key("unit", day, ed, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ALBERTA,
                scheme_code="ca-ab.poll",
                code=f"{day}:{ed}:{code}",
                parent_id=divisions[ed],
                area_relation="within",
                area_id=areas[ed],
                kind=_poll_kind(name),
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
            p.name(unit, f"{code} {title(name)}", "en", "official")
            registered = number(row[2])
            if registered or _poll_kind(name) == "standard":
                _value(p, result_set, unit, None, 2, registered)
            for candidate, option in zip(candidates, options, strict=True):
                _value(p, result_set, unit, option, 1, number(row[candidate.column]) or 0)
            valid = number(row[columns["V"]]) or 0
            invalid = sum(number(row[columns[c]]) or 0 for c in ("S", "D", "R"))
            _value(p, result_set, unit, None, 4, valid)
            _value(p, result_set, unit, None, 5, invalid)
            _value(p, result_set, unit, None, 3, valid + invalid)
    return p


def _municipal(year: int) -> Package:
    vote = VOTES[year]
    day = vote.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} Alberta Senate nominee election, held with the municipal elections, by municipality",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {ALBERTA, ALBERTA_AREA}
    p.uses(raw.cached(SOURCE, RESULTS_2021))
    _, contest, result_set = _election(p, year)
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=MUNICIPAL_METHOD,
        lang="en",
    )
    frame = next(iter(sheets(year, "results").values()))
    rows = frame.rows()
    header_at = next(index for index, row in enumerate(rows) if str(row[0] or "").strip() == "Jurisdiction")
    header = rows[header_at]
    candidates = _municipal_candidates(header)
    blank_column = next(i for i, cell in enumerate(header) if str(cell).startswith("Rejected as Blank"))
    other_column = next(i for i, cell in enumerate(header) if str(cell).startswith("Rejected for Other"))
    total = next(row for row in rows if str(row[0] or "").startswith("Provincial Total"))
    totals = [number(total[candidate.column]) or 0 for candidate in candidates]
    options = _options(p, year, contest, result_set, candidates, totals)
    for index, option in enumerate(options):
        _declare(p, result_set, option, 1, totals[index])
    _declare(p, result_set, None, 3, number(total[2]))
    _declare(p, result_set, None, 6, number(total[blank_column]))
    _declare(p, result_set, None, 5, number(total[other_column]))
    seen: set[str] = set()
    for row in rows[header_at + 1 :]:
        name = str(row[0] or "").strip()
        if not name or name.startswith("Provincial Total"):
            continue
        code = slug(name)
        if code in seen:
            raise ValueError(f"{year}: municipality {name} appears twice")
        seen.add(code)
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ALBERTA,
            scheme_code="ca-ab.municipality",
            code=f"{day}:{code}",
            area_relation="within",
            area_id=ALBERTA_AREA,
            kind="standard",
            valid_from=day,
            valid_to=day + timedelta(days=1),
        )
        p.name(unit, title(name), "en", "official")
        included = re.sub(r"\s+", " ", str(row[1] or "")).strip().rstrip(",")
        if included:
            p.add(
                "note",
                id=key("note", unit),
                entity_id=unit,
                kind="context",
                body_md=f"Includes the results of {included}.",
                lang="en",
            )
        _value(p, result_set, unit, None, 3, number(row[2]))
        for candidate, option in zip(candidates, options, strict=True):
            _value(p, result_set, unit, option, 1, number(row[candidate.column]) or 0)
        _value(p, result_set, unit, None, 6, number(row[blank_column]))
        _value(p, result_set, unit, None, 5, number(row[other_column]))
    return p
