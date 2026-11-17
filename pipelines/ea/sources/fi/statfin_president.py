"""Finland's presidential elections of 1994 and 2000, both rounds, by constituency, from Statistics Finland's StatFin.

Table 14db gives the votes for each candidate by constituency and round, with the valid and invalid ballots; table 14da
gives the persons entitled to vote and the persons who voted. The results service behind `fi.vaalit` holds the 2006
election by municipality, so the elections here begin in 1994 and stop at 2000.
"""

import functools
import json
from dataclasses import dataclass
from datetime import date

from ea import raw
from ea.package import Package
from ea.sources.fi import vaalit

SOURCE = "fi.statfin_president"
API = "https://pxdata.stat.fi/PxWeb/api/v1/en/StatFin/pvaa"
VOTES_TABLE, TURNOUT_TABLE = f"{API}/14db.px", f"{API}/14da.px"
PAGE = "https://pxdata.stat.fi/PxWeb/pxweb/en/StatFin/StatFin__pvaa/"
SCHEME = "fi.vaalipiirilaskenta"
REGISTERED, CAST, VALID, INVALID = 2, 3, 4, 5
VOTES_MEASURE = 1
VALID_CODE, INVALID_CODE = "98", "99"
WHOLE = "SSS"
REGISTRY = f"{SOURCE}/registry"

# Election days, first and second round, from Statistics Finland's release pages
YEARS = {1994: (date(1994, 1, 16), date(1994, 2, 6)), 2000: (date(2000, 1, 16), date(2000, 2, 6))}

# The constituencies of the table, by the code of the constituency that `fi.vaalit` keeps for the same area
CONSTITUENCIES = {
    "010000": "HEL",
    "020000": "UUS",
    "030000": "VAR",
    "040000": "SAT",
    "050000": "AHV",
    "060000": "HÄM",
    "070000": "PIR",
    "810000": "KYM",
    "820000": "ESA",
    "910000": "PSA",
    "920000": "KAR",
    "100000": "VAA",
    "110000": "KES",
    "120000": "OUL",
    "130000": "LAP",
}
SWAPPED = {(1994, "910000"): "920000", (1994, "920000"): "910000"}
SWAP_NOTE = (
    "Table 14da prints the persons entitled to vote and the persons who voted of the North Savo and North Karelia "
    "constituencies under each other's codes in 1994. The table's valid and invalid ballots for each constituency, "
    "which sum to the country's, equal the persons who voted of the other constituency's row exactly in both "
    "rounds, so the archive stores each constituency with the counts of the other's row."
)

METHOD = (
    "Statistics Finland's table of the votes for each candidate by constituency and round (StatFin table 14db) and its "
    "table of the persons entitled to vote and the persons who voted (table 14da). The tables give each "
    "constituency's counts and the whole country's; the constituencies are those of the election, and the votes of "
    "Finnish citizens living abroad are counted in the constituencies, as the table prints them. Valid and invalid "
    "ballots are the table's own rows for them. The table marks no candidate as elected or advancing; the candidates "
    "with votes in the second round are stored as advancing from the first round, and the candidate with the most "
    "votes in the second round as the winner, which is the election's declared result."
)


@dataclass(frozen=True)
class Table:
    """One table's query for one election year."""

    year: int
    url: str
    body: dict[str, object]


def _query(code: str, values: list[str]) -> dict[str, object]:
    return {"code": code, "selection": {"filter": "item", "values": values}}


def votes_body(year: int) -> dict[str, object]:
    return {"query": [_query("timeperiod_y", [str(year)])], "response": {"format": "json-stat2"}}


def turnout_body(year: int) -> dict[str, object]:
    return {
        "query": [
            _query("timeperiod_y", [str(year)]),
            _query("sukupuoli_9_20180101", ["SSS"]),
            _query("kunta_110_20231009", [WHOLE, *CONSTITUENCIES]),
            _query("contentscode", ["pvaa_ao", "pvaa_aanest", "pvaa_hylatyt"]),
        ],
        "response": {"format": "json-stat2"},
    }


def tables() -> list[Table]:
    return [
        Table(year, url, body(year))
        for year in YEARS
        for url, body in ((VOTES_TABLE, votes_body), (TURNOUT_TABLE, turnout_body))
    ]


def _valid(path: object) -> None:
    with open(str(path), "rb") as file:
        if not json.load(file).get("value"):
            raise ValueError(f"{path} holds no values")


def fetch() -> None:
    for table in tables():
        raw.fetch(SOURCE, table.url, suffix=".json", body=table.body, check=_valid)


def cited() -> list[raw.Artifact]:
    return [raw.cached(SOURCE, table.url, table.body) for table in tables()]


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{day}" for days in YEARS.values() for day in days)]


def parse() -> None:
    registry().write()
    for days in YEARS.values():
        for day in days:
            election(day).write()


def emit() -> None:
    from ea import package

    package.run("load", packages())


def key(kind: str, *parts: object) -> str:
    from uuid import uuid5

    from ea import package

    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "statfin")


# ---------------------------------------------------------------------------------------------------------------------
# Reading the tables


@dataclass(frozen=True)
class Cube:
    """A json-stat2 cube read into a mapping from one label per dimension to its value."""

    cells: dict[tuple[str, ...], int | None]
    labels: dict[str, dict[str, str]]
    order: list[str]


def read(table: Table) -> Cube:
    artifact = raw.cached(SOURCE, table.url, table.body)
    data = json.loads(artifact.path.read_bytes())
    order: list[str] = data["id"]
    codes: list[list[str]] = []
    labels: dict[str, dict[str, str]] = {}
    for name in order:
        category = data["dimension"][name]["category"]
        index = category["index"]
        ordered = sorted(index, key=lambda code: index[code]) if isinstance(index, dict) else list(index)
        codes.append(ordered)
        labels[name] = {code: str(category["label"][code]) for code in ordered}
    cells: dict[tuple[str, ...], int | None] = {}
    size = 1
    for column in codes:
        size *= len(column)
    values = data["value"]
    if len(values) != size:
        raise ValueError(f"{table.url}: {len(values)} values for a cube of {size}")
    for position, value in enumerate(values):
        coordinate: list[str] = []
        rest = position
        for column in reversed(codes):
            rest, at = divmod(rest, len(column))
            coordinate.append(column[at])
        cells[tuple(reversed(coordinate))] = None if value is None else int(value)
    return Cube(cells, labels, order)


@dataclass(frozen=True)
class Place:
    code: str
    votes: dict[str, int]  # candidate code: votes
    valid: int | None
    invalid: int | None
    registered: int | None
    cast: int | None


@dataclass(frozen=True)
class Round:
    names: dict[str, str]  # candidate code: name, in the table's order
    places: dict[str, Place]  # constituency code, or the whole country's: its counts


@functools.cache
def rounds(year: int) -> dict[int, Round]:
    cube = read(Table(year, VOTES_TABLE, votes_body(year)))
    turnout = read(Table(year, TURNOUT_TABLE, turnout_body(year)))
    assert cube.order[:4] == [
        "timeperiod_y",
        "vaaliehdokas_19_20231121",
        "kunta_110_20231009",
        "vaalitiedot_35_20240101",
    ]
    contents = cube.order[4]
    (_share, count_code) = sorted(cube.labels[contents], reverse=True)
    assert count_code == "pvaa_aanet", cube.labels[contents]
    people = cube.labels["vaaliehdokas_19_20231121"]
    out: dict[int, Round] = {}
    for number in (1, 2):
        names: dict[str, str] = {}
        places: dict[str, Place] = {}
        for place in (WHOLE, *CONSTITUENCIES):
            found: dict[str, int] = {}
            valid = invalid = None
            for candidate in people:
                count = cube.cells[(str(year), candidate, place, str(number), count_code)]
                if count is None:
                    continue
                if candidate == VALID_CODE:
                    valid = count
                elif candidate == INVALID_CODE:
                    invalid = count
                else:
                    found[candidate] = count
                    names[candidate] = people[candidate]
            if not found and valid is None:
                continue

            # Table 14da prints the counts of North Savo and North Karelia for 1994 under each other's code
            row = SWAPPED.get((year, place), place)

            def counted(content: str, row: str = row, number: int = number) -> int | None:
                return turnout.cells[(str(year), "SSS", row, str(number), content)]

            places[place] = Place(place, found, valid, invalid, counted("pvaa_ao"), counted("pvaa_aanest"))
        out[number] = Round(names, places)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Packages


def registry() -> Package:
    p = Package(REGISTRY, SOURCE, "Statistics Finland's StatFin tables of the presidential elections, and their scheme")
    p.external.add(vaalit.FINLAND)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tilastokeskus",
        title="StatFin: presidential elections, support for candidates and voting data by constituency (tables 14db, 14da)",
        url=PAGE,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Tilastokeskus, StatFin",
    )
    for artifact in cited():
        p.artifact(artifact, SOURCE_ID)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=vaalit.FINLAND,
        kind="precinct",
        rank=2,
        noun="constituency count",
        code_system=None,
    )
    return p


def election_id(day: date) -> str:
    return key("election", day)


def contest_id(day: date) -> str:
    return key("contest", day)


def election(day: date) -> Package:
    year = day.year
    first = YEARS[year][0]
    number = 1 if day == first else 2
    data = rounds(year)[number]
    runoff = number == 2
    depends = [f"{vaalit.SOURCE}/geo", f"{vaalit.SOURCE}/registry", REGISTRY]
    if runoff:
        depends.append(f"{SOURCE}/{first}")
    round_name = ("second" if runoff else "first") + " round"
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {round_name} of Finland's {year} presidential election by constituency from Statistics Finland's StatFin",
        depends=depends,
    )
    p.external.add(vaalit.FINLAND)
    for table in tables():
        if table.year == year:
            p.uses(raw.cached(SOURCE, table.url, table.body))
    election = election_id(day)
    p.add(
        "election",
        id=election,
        polity_id=vaalit.FINLAND,
        kind="runoff" if runoff else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    suffix = (", second round", ", 2. kierros", ", andra omgången") if runoff else ("", "", "")
    p.name(election, f"{year} Finnish presidential election{suffix[0]}", "en", "official")
    p.name(election, f"Presidentinvaali {year}{suffix[1]}", "fi", "native", False)
    p.name(election, f"Presidentval {year}{suffix[2]}", "sv", "native", False)
    path = f"fi/{day}-presidential" + ("-runoff" if runoff else "")
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = contest_id(day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=vaalit.PRESIDENCY,
        district_area_id=vaalit.COUNTRY,
        magnitude=1,
        round_number=number,
        previous_round_id=contest_id(first) if runoff else None,
        electoral_system_id=vaalit.system("president"),
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Finnish presidential election, {round_name}", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    if year == 1994:
        p.add(
            "note",
            id=key("note", result_set, "swap"),
            entity_id=result_set,
            kind="caveat",
            body_md=SWAP_NOTE,
            lang="en",
        )
    options: dict[str, str] = {}
    for code, name in data.names.items():
        tag = (year, code)
        person = key("person", *tag)
        if not runoff:
            p.add("person", id=person, birth_precision=None, gender=None)
            p.name(person, name, "fi", "official")
        option = options[code] = key("option", day, code)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=None,
            party_id=None,
            ballot_position=None,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", day, code),
            person_id=person,
            option_id=option,
            role="head",
            list_position=None,
            ballot_name=name,
            member_of_party_id=None,
        )
    whole = data.places[WHOLE]
    for measure, value in (
        (REGISTERED, whole.registered),
        (CAST, whole.cast),
        (VALID, whole.valid),
        (INVALID, whole.invalid),
    ):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for code, votes in whole.votes.items():
        p.add("declared", result_set_id=result_set, option_id=options[code], measure=VOTES_MEASURE, value=votes)
    leader = max(whole.votes, key=lambda code: whole.votes[code])
    if runoff:
        p.add("outcome", result_set_id=result_set, option_id=options[leader], kind="won", seats=1)
    else:
        for code in rounds(year)[2].places[WHOLE].votes:
            p.add("outcome", result_set_id=result_set, option_id=options[code], kind="advanced")
    values: list[dict[str, object]] = []
    for code, area_code in CONSTITUENCIES.items():
        place = data.places.get(code)
        if place is None:
            continue
        label = read(Table(year, VOTES_TABLE, votes_body(year))).labels["kunta_110_20231009"][code]
        name = label.partition(" ")[2].removesuffix(" constituency")
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=vaalit.FINLAND,
            scheme_code=SCHEME,
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=vaalit.area("fi.vaalipiiri", area_code),
            kind="standard",
        )
        p.name(unit, name, "en", "official")
        for measure, value in (
            (REGISTERED, place.registered),
            (CAST, place.cast),
            (VALID, place.valid),
            (INVALID, place.invalid),
        ):
            if value is not None:
                values.append({"unit_id": unit, "option_id": None, "measure": measure, "value": value})
        for candidate, votes in place.votes.items():
            values.append({"unit_id": unit, "option_id": options[candidate], "measure": VOTES_MEASURE, "value": votes})
    for row in values:
        p.add("result_value", result_set_id=result_set, channel=0, count_round=0, **row)
    return p
