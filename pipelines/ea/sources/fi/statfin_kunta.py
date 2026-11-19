"""Finnish municipal elections 1976 to 2008 by municipality from Statistics Finland's StatFin tables 14z6 and 14z7.

Table 14z6 holds the persons entitled to vote and the persons who voted, and table 14z7 holds each party's votes and
council seats, both by the municipal division of each election year. The area variable lists every municipality code
that any election used, with the constituency rows and the national row beside them. A municipality that did not exist
in a year has a zero, and a code that no election used is empty. The national row of the years 1976 to 1988 also
includes Åland, whose municipal elections were held on other days, so the mainland municipalities here are compared with
that row less Åland. Each election is one package, `fi.statfin_kunta/<first day>`, with one `list_allocation` contest
per mainland municipality; `fi.statfin_kunta/geo` and `fi.statfin_kunta/registry` hold the municipalities and the rest.
"""

import functools
import json
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import shapely

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.fi import mml, tilastokeskus

SOURCE = "fi.statfin_kunta"
API = "https://pxdata.stat.fi/PxWeb/api/v1"
VOTING, PARTIES = "14z6", "14z7"
FINLAND = str(uuid5(package.NAMESPACE, "polity\x1fFI"))
AREA, PARTY_VAR = "kunta_128_20250101", "puolue_6_20250103"
TOTAL = "SSS"
OTHERS = "99"
REGISTERED, CAST, VALID = 2, 3, 4
VOTES = 1
GEO, REGISTRY = f"{SOURCE}/geo", f"{SOURCE}/registry"


@dataclass(frozen=True)
class Vote:
    year: int
    first: date
    last: date


VOTES_HELD = [
    Vote(1976, date(1976, 10, 17), date(1976, 10, 18)),
    Vote(1980, date(1980, 10, 19), date(1980, 10, 20)),
    Vote(1984, date(1984, 10, 21), date(1984, 10, 22)),
    Vote(1988, date(1988, 10, 16), date(1988, 10, 17)),
    Vote(1992, date(1992, 10, 18), date(1992, 10, 18)),
    Vote(1996, date(1996, 10, 20), date(1996, 10, 20)),
    Vote(2000, date(2000, 10, 22), date(2000, 10, 22)),
    Vote(2004, date(2004, 10, 24), date(2004, 10, 24)),
    Vote(2008, date(2008, 10, 26), date(2008, 10, 26)),
]
ALAND = "05"
AALAND_IN_TOTAL = {1976, 1980, 1984, 1988}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "statfin")
BOUNDARIES = key("source", "statistics-finland-boundaries")
SURVEY = key("source", "nls-municipal-division")
SYSTEM = key("electoral_system", "council")
COUNTRY = key("area", "fi.maa", "FI")


def table_url(table: str, lang: str = "en") -> str:
    return f"{API}/{lang}/StatFin/kvaa/{table}.px"


def body(table: str, year: int) -> dict[str, object]:
    query: list[dict[str, object]] = [
        {"code": "timeperiod_y", "selection": {"filter": "item", "values": [str(year)]}},
        {"code": AREA, "selection": {"filter": "all", "values": ["*"]}},
    ]
    if table == PARTIES:
        query.append({"code": PARTY_VAR, "selection": {"filter": "all", "values": ["*"]}})
        contents = ["kvaa-aanet_yht", "kvaa-lkm_val"]
    else:
        contents = ["kvaa-lkm_ao", "kvaa-lkm_aanest"]
    query.append({"code": "contentscode", "selection": {"filter": "item", "values": contents}})
    return {"query": query, "response": {"format": "json-stat2"}}


def _valid(path: object) -> None:
    with open(str(path), "rb") as file:
        if not json.load(file).get("value"):
            raise ValueError(f"{path} holds no values")


def fetch() -> None:
    for table in (VOTING, PARTIES):
        for lang in ("en", "fi", "sv"):
            raw.fetch(SOURCE, table_url(table, lang), suffix=".json")
        for vote in VOTES_HELD:
            raw.fetch(SOURCE, table_url(table), suffix=".json", body=body(table, vote.year), check=_valid)
    layer = tilastokeskus.LAYERS[0]
    for year in layer.years:
        raw.fetch(tilastokeskus.SOURCE, tilastokeskus.url(layer, year), suffix=".json", check=tilastokeskus._valid)
    mml.fetch()


# ---------------------------------------------------------------------------------------------------------------------
# Reading the tables


def _read(table: str, year: int) -> tuple[dict[str, int], dict[str, int], list[int], list[float | int | None]]:
    with raw.cached(SOURCE, table_url(table), body(table, year)).path.open("rb") as file:
        data = json.load(file)
    dims = {name: data["dimension"][name]["category"]["index"] for name in data["id"] if name != "timeperiod_y"}
    if table == VOTING:
        dims = {AREA: dims[AREA], "contentscode": dims["contentscode"]}
    areas = {code: position for code, position in dims[AREA].items()}
    parties = dims.get(PARTY_VAR, {})
    return areas, parties, data["size"], data["value"]


@functools.cache
def voting(year: int) -> dict[str, tuple[int | None, int | None]]:
    """The persons entitled to vote and the persons who voted by area code, as the table prints them."""
    areas, _, _, values = _read(VOTING, year)
    return {code: (_int(values[2 * at]), _int(values[2 * at + 1])) for code, at in areas.items()}


@functools.cache
def support(year: int) -> dict[str, dict[str, tuple[int | None, int | None]]]:
    """Each party's votes and seats by area code, with the total under the code `SSS`."""
    areas, parties, _, values = _read(PARTIES, year)
    count = len(parties)
    return {
        code: {
            party: (
                _int(values[(at * count + place) * 2]),
                _int(values[(at * count + place) * 2 + 1]),
            )
            for party, place in parties.items()
        }
        for code, at in areas.items()
    }


def _int(value: float | None) -> int | None:
    return None if value is None else int(value)


@functools.cache
def labels(table: str, lang: str, variable: str) -> dict[str, str]:
    with raw.cached(SOURCE, table_url(table, lang)).path.open("rb") as file:
        meta = json.load(file)
    (found,) = [item for item in meta["variables"] if item["code"] == variable]
    return dict(zip(found["values"], found["valueTexts"], strict=True))


def is_municipality(code: str) -> bool:
    return code != TOTAL and not code.endswith("0000")


def number(code: str) -> str:
    """The three-digit municipality code, the last three digits of the area code."""
    return code[-3:]


def clean_name(label: str) -> str:
    """The municipality's name from a label such as `223 Karjalohja -13`."""
    return re.sub(r"\s+-\d\d$", "", re.sub(r"^\d{3}\s+", "", label)).strip()


@functools.cache
def municipalities() -> dict[str, str]:
    """Every mainland municipality code the tables hold with a count in some election, by area code."""
    found: dict[str, str] = {}
    for vote in VOTES_HELD:
        for code, (registered, _) in voting(vote.year).items():
            if is_municipality(code) and code[:2] != ALAND and registered:
                found[number(code)] = code
    if len(found) != len({code for code in found.values()}):
        raise ValueError("a municipality code repeats")
    return found


def held(vote: Vote) -> list[str]:
    """The area codes of the mainland municipalities that existed in an election."""
    return sorted(
        code
        for code, (registered, _) in voting(vote.year).items()
        if is_municipality(code) and code[:2] != ALAND and registered
    )


def area(code: str) -> str:
    return key("area", "fi.kunta", number(code))


def council(code: str) -> str:
    return key("body", "council", number(code))


def party(code: str) -> str:
    return key("party", code)


# ---------------------------------------------------------------------------------------------------------------------
# Parties


# Finnish and English names for the parties whose full names are not in doubt. The rest carry the table's abbreviation.
NAMES: dict[str, tuple[str, str]] = {
    "01": ("Kansallinen Kokoomus", "National Coalition Party"),
    "02": ("Perussuomalaiset", "Finns Party"),
    "03": ("Suomen Sosialidemokraattinen Puolue", "Social Democratic Party of Finland"),
    "04": ("Suomen Keskusta", "Centre Party"),
    "05": ("Vihreä liitto", "Green League"),
    "06": ("Vasemmistoliitto", "Left Alliance"),
    "07": ("Suomen ruotsalainen kansanpuolue", "Swedish People's Party of Finland"),
    "08": ("Suomen Kristillisdemokraatit", "Christian Democrats"),
}


def abbreviation(code: str) -> str:
    return re.sub(r"\s*\(.*$", "", labels(PARTIES, "fi", PARTY_VAR)[code]).rstrip(", ").strip()


def used_parties() -> list[str]:
    seen: set[str] = set()
    for vote in VOTES_HELD:
        for code, items in support(vote.year).items():
            if code == TOTAL or not is_municipality(code):
                continue
            seen |= {p for p, (votes, _) in items.items() if p != TOTAL and votes}
    return sorted(seen)


# ---------------------------------------------------------------------------------------------------------------------
# Packages


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/{vote.first}" for vote in VOTES_HELD)]


def parse() -> None:
    geo().write()
    registry().write()
    for vote in VOTES_HELD:
        election(vote).write()


def emit() -> None:
    package.run("load", packages())


def data_artifacts(year: int | None = None) -> list[raw.Artifact]:
    years = [v.year for v in VOTES_HELD if year is None or v.year == year]
    found = [raw.cached(SOURCE, table_url(table), body(table, y)) for table in (VOTING, PARTIES) for y in years]
    if year is None:
        found += [
            raw.cached(SOURCE, table_url(table, lang)) for table in (VOTING, PARTIES) for lang in ("en", "fi", "sv")
        ]
    return found


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tilastokeskus",
        title="StatFin: Municipal elections, tables 14z6 (voting data) and 14z7 (support for parties) by the municipal "
        "division of each election year",
        url="https://pxdata.stat.fi/PxWeb/pxweb/en/StatFin/StatFin__kvaa/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Tilastokeskus (Statistics Finland), StatFin",
    )


def geo() -> Package:
    p = Package(GEO, SOURCE, "Finland's mainland municipalities of the municipal elections 1976 to 2008, with shapes")
    p.external.update([FINLAND, "fi.kunta"])
    fi, sv = labels(VOTING, "fi", AREA), labels(VOTING, "sv", AREA)
    p.add(
        "source",
        id=BOUNDARIES,
        kind="official_body",
        publisher=tilastokeskus.PUBLISHER,
        title="Tilastointialueet (Statistics Finland statistical area boundaries, 1:1,000,000, WFS)",
        url="https://www.stat.fi/org/avoindata/paikkatietoaineistot.html",
        license=tilastokeskus.LICENSE,
        license_url=tilastokeskus.LICENSE_URL,
        attribution="Tilastokeskus (Statistics Finland)",
    )
    layer = tilastokeskus.LAYERS[0]
    for year in layer.years:
        p.artifact(raw.cached(tilastokeskus.SOURCE, tilastokeskus.url(layer, year)), BOUNDARIES)
    mml.add_source(p, SURVEY)
    shapes = tilastokeskus.spans("fi.kunta")
    older = mml.spans()
    for num, code in sorted(municipalities().items()):
        id = area(code)
        p.add("area", id=id, scheme_code="fi.kunta")
        p.add("area_code", area_id=id, code=num)
        names = {
            "fi": clean_name(fi.get(code, "")),
            "sv": clean_name(sv.get(code, "")),
        }
        p.name(id, names["fi"], "fi", "official")
        if names["sv"] and names["sv"] != names["fi"]:
            p.name(id, names["sv"], "sv", "official", False)
        for since, until, shape in older.get(num, []):
            tilastokeskus.outline(p, id, SURVEY, since, until, shape)
        for since, until, shape in shapes.get(num, []):
            _outline(p, id, since, until, shape)
    return p


def _outline(p: Package, id: str, since: date, until: date | None, shape: shapely.Geometry) -> None:
    tilastokeskus.outline(p, id, BOUNDARIES, since, until, shape)


METHOD_SYSTEM = (
    "Each municipality is one constituency whose council seats go to the candidate lists by D'Hondt. A voter marks one "
    "candidate, the votes for a candidate count for the list, and the candidates of a list are ranked by their own "
    "votes. Electoral alliances (vaaliliitto) pool the votes of their lists in the seat count; the statistics here are "
    "by party."
)


def registry() -> Package:
    p = Package(REGISTRY, SOURCE, "Finnish municipal councils, parties, and the council electoral system, 1976 to 2008")
    p.external.add(FINLAND)
    source_row(p)
    for artifact in data_artifacts():
        p.artifact(artifact, SOURCE_ID)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=FINLAND,
        valid_from=date(1976, 1, 1),
        valid_to=None,
        rules={"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD_SYSTEM, lang="en")
    fi = labels(VOTING, "fi", AREA)
    for num, code in sorted(municipalities().items()):
        id = council(code)
        p.add("body", id=id, polity_id=FINLAND, kind="council")
        p.name(id, f"Municipal council of {clean_name(fi[code])}", "en", "official")
    english = labels(PARTIES, "en", PARTY_VAR)
    for code in used_parties():
        if code == OTHERS:
            continue
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=FINLAND)
        short = abbreviation(code)
        if code in NAMES:
            p.name(id, NAMES[code][0], "fi", "official")
            p.name(id, NAMES[code][1], "en", "english", False)
            p.name(id, short, "fi", "abbreviation", False)
        else:
            p.name(id, short, "fi", "abbreviation")
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="context",
            body_md=(
                f"Statistics Finland's party class `{code}`, labeled `{english[code]}` in its tables. A label that "
                "adds a party with a year, such as `+DEVA-88`, means that the class also holds that party's votes "
                "until that year."
            ),
            lang="en",
        )
    return p


TITLE = ("Finnish municipal elections", "Kunnallisvaalit", "Kommunalval")
METHOD = (
    "Statistics Finland's tables 14z6 and 14z7 give the results by municipality as the municipalities were divided in "
    "the election year. Registered voters are the persons entitled to vote, ballots cast are the persons who voted, "
    "valid ballots are the total of the party votes, and each party's votes and council seats are as the table prints "
    "them. Invalid ballots are not published. The statistics, not the municipal election boards' own returns, are the "
    "source, so the figures are the statistics' final figures."
)


def election(vote: Vote) -> Package:
    year = vote.year
    codes = held(vote)
    p = Package(
        f"{SOURCE}/{vote.first}",
        SOURCE,
        f"The {year} Finnish municipal elections in {len(codes)} mainland municipalities from Statistics Finland's StatFin tables",
        depends=[GEO, REGISTRY],
    )
    p.external.update([FINLAND, "fi.kuntalaskenta"])
    for artifact in data_artifacts(year):
        p.uses(artifact)
    election_id = key("election", vote.first)
    p.add(
        "election",
        id=election_id,
        polity_id=FINLAND,
        kind="local",
        date_from=vote.first,
        date_to=vote.last,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} {TITLE[0]}", "en", "official")
    p.name(election_id, f"{TITLE[1]} {year}", "fi", "native", False)
    p.name(election_id, f"{TITLE[2]} {year}", "sv", "native", False)
    path = f"fi/{vote.first}-municipal-by-municipality"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    if year in AALAND_IN_TOTAL:
        p.add(
            "note",
            id=key("note", election_id, "aland"),
            entity_id=election_id,
            kind="caveat",
            body_md=(
                "The tables' national total for this year also holds Åland, whose municipal elections were held on "
                "other days. Åland's municipalities are not stored here."
            ),
            lang="en",
        )
    fi = labels(VOTING, "fi", AREA)
    votes, support_by = voting(year), support(year)
    values: list[dict[str, object]] = []
    sums = {"registered": 0, "cast": 0, "valid": 0, "seats": 0}
    parties: dict[str, int] = {}
    empty: list[str] = []
    for code in codes:
        num = number(code)
        name = clean_name(fi[code])
        registered, cast = votes[code]
        tags = support_by[code]
        valid, seats = tags[TOTAL]
        contest = key("contest", vote.first, num)
        unit = key("unit", vote.first, num)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=FINLAND,
            scheme_code="fi.kuntalaskenta",
            code=f"statfin:{vote.first}:{num}",
            area_relation="equals",
            area_id=area(code),
            kind="standard",
        )
        p.name(unit, name, "fi", "official")
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=council(code),
            district_area_id=area(code),
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, name, "fi", "official")
        p.add("slug", path=f"{path}/{changeset.slug(name)}-{num}", entity_id=contest, is_current=True)
        result_set = key("result_set", vote.first, num)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        if valid is None:
            p.add(
                "note",
                id=key("note", contest, "empty"),
                entity_id=contest,
                kind="caveat",
                body_md=(
                    "The table prints the persons entitled to vote and no votes, party results, or council seats for "
                    "this municipality in this election, so only those voters are stored."
                ),
                lang="en",
            )
            empty.append(num)
        totals = [(REGISTERED, registered), (CAST, cast), (VALID, valid)]
        for measure, value in totals:
            if value is None:
                continue
            _value(values, result_set, unit, None, measure, value)
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        sums["registered"] += registered or 0
        sums["cast"] += cast or 0
        sums["valid"] += valid or 0
        sums["seats"] += seats or 0
        for tag, (count, won) in sorted(tags.items()):
            if tag == TOTAL or not count:
                continue
            option = key("option", vote.first, num, tag)
            parties[tag] = parties.get(tag, 0) + count
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if tag == OTHERS else "list",
                party_id=None if tag == OTHERS else party(tag),
                ballot_label=labels(PARTIES, "fi", PARTY_VAR)[OTHERS] if tag == OTHERS else abbreviation(tag),
            )
            _value(values, result_set, unit, option, VOTES, count)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=count)
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
    import polars as pl

    p.frame("result_value", pl.DataFrame(values))
    _compare(vote, codes, sums, empty)
    return p


def _value(
    values: list[dict[str, object]], result_set: str, unit: str, option: str | None, measure: int, value: int
) -> None:
    values.append(
        {
            "result_set_id": result_set,
            "unit_id": unit,
            "option_id": option,
            "channel": 0,
            "measure": measure,
            "count_round": 0,
            "flags": 0,
            "value": value,
        }
    )


def _compare(vote: Vote, codes: list[str], sums: dict[str, int], empty: list[str]) -> None:
    """Stop when the municipalities do not add up to the table's national row, less Åland where that row holds it."""
    votes, support_by = voting(vote.year), support(vote.year)
    aland = [code for code in votes if is_municipality(code) and code[:2] == ALAND]
    extra = vote.year in AALAND_IN_TOTAL
    national_registered, national_cast = votes[TOTAL]
    national_valid, national_seats = support_by[TOTAL][TOTAL]
    drop = {
        "registered": sum(votes[c][0] or 0 for c in aland) if extra else 0,
        "cast": sum(votes[c][1] or 0 for c in aland) if extra else 0,
        "valid": sum(support_by[c][TOTAL][0] or 0 for c in aland) if extra else 0,
        "seats": sum(support_by[c][TOTAL][1] or 0 for c in aland) if extra else 0,
    }
    expected = {
        "registered": (national_registered or 0) - drop["registered"],
        "cast": (national_cast or 0) - drop["cast"],
        "valid": (national_valid or 0) - drop["valid"],
        "seats": (national_seats or 0) - drop["seats"],
    }
    gap = expected["seats"] - sums["seats"]
    if empty and gap > 0:
        print(
            f"{vote.year}: the national row holds {gap} more seats than the municipalities, {empty} print none",
            flush=True,
        )
        expected["seats"] -= gap
    if sums != expected:
        raise ValueError(f"{vote.year}: municipalities sum to {sums}, the national row to {expected}")
    print(f"{vote.year}: {len(codes)} municipalities, {sums} match the table's national row", flush=True)
