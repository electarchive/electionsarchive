"""The 2017 Australian Marriage Law Postal Survey, by federal electoral division.

The Australian Bureau of Statistics published the results as one workbook of response counts by state and territory
and by federal electoral division (1800.0, released 15 November 2017). The survey was a voluntary, non-binding
statistical collection, so its contest is a plebiscite-type ballot question with no legal outcome.
"""

import re
from datetime import date
from uuid import uuid5

import polars as pl
import xlrd

from ea import package, raw
from ea.package import Package

SOURCE = "au.abs"
DAY = date(2017, 11, 15)
PAGE = "https://www.abs.gov.au/ausstats/abs@.nsf/mf/1800.0"
RESPONSE_URL = (
    "https://www.abs.gov.au/ausstats/ABS@Archive.nsf/log?openagent&"
    "australian_marriage_law_postal_survey_2017_-_response_final.xls&1800.0&Time%20Series%20Spreadsheet&"
    "916379DAEE6E960CCA2581F0001A08A9&0&2017&11.12.2017&Latest"
)
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUS"))
STATES = {
    "New South Wales": "NSW",
    "Victoria": "VIC",
    "Queensland": "QLD",
    "South Australia": "SA",
    "Western Australia": "WA",
    "Tasmania": "TAS",
    "Northern Territory": "NT",
    "Australian Capital Territory": "ACT",
}
# Measures: 1 votes, 2 registered, 3 ballots cast, 4 valid, 5 invalid.
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
METHOD = (
    "Yes and No are the responses the Australian Bureau of Statistics counted as clear; responses that were not "
    "clear (both boxes ticked, no box ticked, or an unreadable mark) are stored as invalid, so ballots are the "
    "forms returned. Registered is the eligible participants: those on the Commonwealth Electoral Roll, or with a "
    "valid application to enrol, at midnight on 24 August 2017. Divisions are the federal electoral divisions as "
    "at 24 August 2017. The Division of Lingiari includes Christmas Island and the Cocos (Keeling) Islands, "
    "Canberra includes Norfolk Island, and Fenner includes Jervis Bay. Forms were mailed from 12 September 2017 "
    "and counted if received by 7 November 2017; the results were published on 15 November 2017."
)
RULES = {"formula": "referendum", "majority": {"share": 0.5, "strict": True}}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "postal-survey")
ELECTION = key("election", DAY)
CONTEST = key("contest", DAY, "marriage-law")
RESULT_SET = key("result_set", CONTEST)
PATH = "au/2017-11-15-referendum"


def fetch() -> None:
    raw.fetch(SOURCE, RESPONSE_URL, suffix=".xls")


# Parsing --------------------------------------------------------------------------------------------------------


def _sheet(name: str) -> xlrd.sheet.Sheet:
    book = xlrd.open_workbook(raw.cached(SOURCE, RESPONSE_URL).path)
    return book.sheet_by_name(name)


def _rows(name: str) -> list[tuple[str, list[int]]]:
    """Each row's label and its counts: yes, no, total, clear, not clear, non-responding, eligible."""
    found = []
    for r in range(_sheet(name).nrows):
        cells = [c for c in _sheet(name).row_values(r) if c != ""]
        if len(cells) == 15 and isinstance(cells[0], str) and all(isinstance(c, float) for c in cells[1:]):
            label = re.sub(r"\([a-z]\)$", "", cells[0]).strip()
            found.append((label, [int(c) for c in cells[1::2]]))
    return found


def states() -> dict[str, list[int]]:
    rows = dict(_rows("Table 1"))
    return {name: rows[name] for name in STATES} | {"Australia": rows["Australia"]}


def divisions() -> list[tuple[str, str, list[int]]]:
    """(state code, division, counts) in the workbook's order."""
    sheet = _sheet("Table 2")
    state = ""
    found = []
    for r in range(7, sheet.nrows):
        cells = [c for c in sheet.row_values(r) if c != ""]
        if len(cells) == 1 and isinstance(cells[0], str) and cells[0].endswith(" Divisions"):
            state = STATES[cells[0].removesuffix(" Divisions")]
        elif len(cells) == 15 and isinstance(cells[0], str):
            name = re.sub(r"\([a-z]\)$", "", cells[0]).strip()
            if not name.endswith("(Total)") and name not in ("Australia", "Total"):
                found.append((state, name, [int(c) for c in cells[1::2]]))
    return found


def _check() -> list[int]:
    """Compare the division rows with the printed state and national totals; return the national counts."""
    printed = states()
    sums: dict[str, list[int]] = {}
    for state, _, counts in divisions():
        sums.setdefault(state, [0] * 7)
        sums[state] = [a + b for a, b in zip(sums[state], counts, strict=True)]
    for name, abbreviation in STATES.items():
        if sums[abbreviation] != printed[name]:
            raise ValueError(f"{name}: divisions sum to {sums[abbreviation]} but the state row prints {printed[name]}")
    total = [sum(column) for column in zip(*sums.values(), strict=True)]
    if total != printed["Australia"]:
        raise ValueError(f"Australia: states sum to {total} but the national row prints {printed['Australia']}")
    for label, counts in [*divisions_as_rows(), *printed.items()]:
        yes, no, total, clear, unclear, silent, eligible = counts
        if yes + no != total or total != clear or clear + unclear + silent != eligible:
            raise ValueError(f"{label}: {counts} does not add up")
    return printed["Australia"]


def divisions_as_rows() -> list[tuple[str, list[int]]]:
    return [(name, counts) for _, name, counts in divisions()]


# Packages -------------------------------------------------------------------------------------------------------


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Australian Bureau of Statistics",
        title="1800.0 Australian Marriage Law Postal Survey, 2017",
        url=PAGE,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Commonwealth of Australia (Australian Bureau of Statistics)",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Australia's 8 states and territories and the 150 federal electoral divisions of the Australian Bureau of "
        "Statistics' 2017 Australian Marriage Law Postal Survey",
    )
    p.external |= {COUNTRY, "au.state", "au.division"}
    for abbreviation, name in ((v, k) for k, v in STATES.items()):
        id = area("au.state", abbreviation)
        p.add("area", id=id, scheme_code="au.state")
        p.add("area_code", area_id=id, code=abbreviation)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
        p.name(id, abbreviation, "en", "abbreviation", False)
    for state, name, _ in divisions():
        id = area("au.division", name)
        p.add("area", id=id, scheme_code="au.division")
        p.add("area_code", area_id=id, code=name)
        p.add("area_parent", area_id=id, parent_id=area("au.state", state))
        p.name(id, name, "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The rules of the 2017 Australian Marriage Law Postal Survey, a voluntary postal survey the Australian "
        "Bureau of Statistics ran for the government",
    )
    p.external.add(AUSTRALIA)
    _source(p)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=AUSTRALIA,
        valid_from=date(2017, 9, 12),
        valid_to=date(2017, 11, 8),
        rules=RULES,
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "A voluntary postal survey of everyone on the Commonwealth Electoral Roll, not a vote under electoral "
            "law: participation was optional, the result bound no one, and the Parliament legislated afterward. "
            "The answer with more than half of the clear responses is recorded as the survey's outcome."
        ),
        lang="en",
    )
    p.artifact(raw.cached(SOURCE, RESPONSE_URL), SOURCE_ID)
    return p


def election() -> Package:
    national = _check()
    yes, no, _, clear, unclear, _, eligible = national
    p = Package(
        f"{SOURCE}/2017-marriage-survey",
        SOURCE,
        "The 2017 Australian Marriage Law Postal Survey, by federal electoral division, from the Australian Bureau "
        "of Statistics",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA, COUNTRY, "au.division"}
    p.uses(raw.cached(SOURCE, RESPONSE_URL))
    title = "2017 Australian Marriage Law Postal Survey"
    p.add(
        "election",
        id=ELECTION,
        polity_id=AUSTRALIA,
        kind="referendum",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(ELECTION, title, "en", "official")
    p.add("slug", path=PATH, entity_id=ELECTION, is_current=True)
    p.add(
        "contest",
        id=CONTEST,
        election_id=ELECTION,
        kind="ballot_question",
        district_area_id=COUNTRY,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=CONTEST, type="plebiscite")
    p.name(CONTEST, "Should the law be changed to allow same-sex couples to marry?", "en", "official")
    p.add("slug", path=f"{PATH}/marriage-law", entity_id=CONTEST, is_current=True)
    p.add(
        "result_set",
        id=RESULT_SET,
        election_id=ELECTION,
        contest_id=CONTEST,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", RESULT_SET), entity_id=RESULT_SET, kind="methodology", body_md=METHOD, lang="en")
    options = {}
    for position, answer in enumerate(("Yes", "No"), 1):
        options[answer] = key("option", answer)
        p.add(
            "contest_option",
            id=options[answer],
            contest_id=CONTEST,
            kind="answer",
            ballot_position=position,
            ballot_label=answer,
        )
    rows: list[dict[str, object]] = []
    for state, name, counts in divisions():
        unit = key("unit", name)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRALIA,
            scheme_code="au.division",
            code=f"{DAY}:{name}",
            area_relation="equals",
            area_id=area("au.division", name),
            kind="standard",
        )
        p.name(unit, name, "en", "official")
        n_yes, n_no, _, n_clear, n_unclear, _, n_eligible = counts
        for option, measure, value in (
            (options["Yes"], VOTES, n_yes),
            (options["No"], VOTES, n_no),
            (None, VALID, n_clear),
            (None, INVALID, n_unclear),
            (None, BALLOTS, n_clear + n_unclear),
            (None, REGISTERED, n_eligible),
        ):
            rows.append(
                {
                    "result_set_id": RESULT_SET,
                    "unit_id": unit,
                    "option_id": option,
                    "measure": measure,
                    "count_round": 0,
                    "value": value,
                    "channel": 0,
                    "flags": 0,
                }
            )
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "value": pl.Int64,
                "channel": pl.Int16,
                "flags": pl.Int16,
            },
        ),
    )
    p.add("declared", result_set_id=RESULT_SET, option_id=options["Yes"], measure=VOTES, value=yes)
    p.add("declared", result_set_id=RESULT_SET, option_id=options["No"], measure=VOTES, value=no)
    p.add("declared", result_set_id=RESULT_SET, option_id=None, measure=VALID, value=clear)
    p.add("declared", result_set_id=RESULT_SET, option_id=None, measure=INVALID, value=unclear)
    p.add("declared", result_set_id=RESULT_SET, option_id=None, measure=BALLOTS, value=clear + unclear)
    p.add("declared", result_set_id=RESULT_SET, option_id=None, measure=REGISTERED, value=eligible)
    for answer, option in options.items():
        p.add(
            "outcome",
            result_set_id=RESULT_SET,
            option_id=option,
            kind="won" if (answer == "Yes") == (yes > no) else "lost",
        )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2017-marriage-survey"]


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())
