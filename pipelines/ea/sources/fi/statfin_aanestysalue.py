"""The parliamentary elections of 2003 and 2007 by voting district, from Statistics Finland's StatFin passive tables.

Tables `evaa/001_oi57_statfin_tulos1_<year>_fi` (voters and turnout) and `evaa/002_oi57_statfin_tulos2_<year>_fi` (the
votes of each party) list every voting district, each municipality, each constituency, and the whole country as rows of
one area variable. The results service's pages, which `fi.vaalit` holds, stop at the municipality. Each election here is
one package that adds, for every constituency's contest, a result set by voting district that supersedes the
municipality set, with the voting districts as `fi.aanestysalue` units inside the municipal count's units.
"""

import functools
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.fi import pages, vaalit

SOURCE = "fi.statfin_aanestysalue"
API = "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin_Passiivi/evaa"
YEARS = (2003, 2007)
VOTING_CODE, PARTY_CODE = "Äänestystiedot", "Puolueiden kannatus"
VOTING = ["Sar1", "Sar2", "Sar12", "Sar13", "Sar4", "Sar15", "Sar6", "Sar8", "Sar10", "Sar11"]
VOTES = ["Sar1", "Sar3"]


def table_url(kind: int, year: int) -> str:
    return f"{API}/00{kind}_oi57_statfin_tulos{kind}_{year}_fi.px"


def body(kind: int, measure: str = "") -> dict[str, object]:
    everything = {"filter": "all", "values": ["*"]}
    total = {"filter": "item", "values": ["S"]}
    if kind == 1:
        query = [
            {"code": "Alue", "selection": everything},
            {"code": VOTING_CODE, "selection": {"filter": "item", "values": VOTING}},
            {"code": "Sukupuoli", "selection": total},
        ]
    else:
        query = [
            {"code": "Alue", "selection": everything},
            {"code": "Puolue", "selection": everything},
            {"code": PARTY_CODE, "selection": {"filter": "item", "values": [measure]}},
            {"code": "Sukupuoli", "selection": total},
        ]
    return {"query": query, "response": {"format": "json-stat2"}}


def _valid(path: object) -> None:
    with open(str(path), "rb") as file:
        if not json.load(file).get("value"):
            raise ValueError(f"{path} holds no values")


def queries(year: int) -> list[tuple[str, dict[str, object]]]:
    """The POST queries for a year. The service answers at most 100,000 cells, so each party measure is its own."""
    url1, url2 = table_url(1, year), table_url(2, year)
    return [(url1, body(1)), *((url2, body(2, measure)) for measure in VOTES)]


def fetch() -> None:
    for year in YEARS:
        for url in (table_url(1, year), table_url(2, year)):
            raw.fetch(SOURCE, url, suffix=".json")
        for url, query in queries(year):
            raw.fetch(SOURCE, url, suffix=".json", body=query, check=_valid)


@functools.cache
def cube(kind: int, year: int, measure: str = "") -> dict[str, Any]:
    with raw.cached(SOURCE, table_url(kind, year), body(kind, measure)).path.open("rb") as file:
        return json.load(file)


# ---------------------------------------------------------------------------------------------------------------------
# The tables

REGISTERED, CAST, VALID, INVALID = 2, 3, 4, 5
VOTES_MEASURE = 1
ELECTION_DAY, ADVANCE = 1, 2
FOLDERS = {2003: "E2003", 2007: "E2007"}
DAYS = {2003: date(2003, 3, 16), 2007: date(2007, 3, 18)}
OTHERS = "99"
TOTAL_PARTY = "00"
REGISTRY = f"{SOURCE}/registry"

# The parties the tables name by abbreviation, as the results service's pages name their lists
ABBREVIATIONS: dict[int, dict[str, str]] = {
    2003: {
        "KESK": "Suomen Keskusta",
        "SDP": "Suomen Sosialidemokraattinen Puolue",
        "KOK": "Kansallinen Kokoomus",
        "VAS": "Vasemmistoliitto",
        "VIHR": "Vihreä liitto",
        "KD": "Suomen Kristillisdemokraatit",
        "RKP": "Ruotsalainen kansanpuolue",
        "PS": "Perussuomalaiset",
        "SKP": "Suomen Kommunistinen Puolue",
        "MVS": "Muutosvoimat Suomi",
        "LIB": "Liberaalit",
        "KIPU": 'Kirjava "Puolue" - Elonkehän Puolesta - KIPU',
        "EKA": "Eläkeläiset Kansan Asialla",
        "SKS": "Suomen Kansan Sinivalkoiset",
        "KTP": "Rauhan ja Sosialismin puolesta - Kommunistinen Työväenpuolue",
        "SNKY": "Suomi Nousee - Kansa Yhdistyy",
        "KA": "Köyhien Asialla",
        "YVP": "Yhteisvastuu puolue",
    },
    2007: {
        "KESK": "Suomen Keskusta",
        "SDP": "Suomen Sosialidemokraattinen Puolue",
        "KOK": "Kansallinen Kokoomus",
        "VAS": "Vasemmistoliitto",
        "VIHR": "Vihreä liitto",
        "KD": "Suomen Kristillisdemokraatit (KD)",
        "RKP": "Ruotsalainen kansanpuolue",
        "PS": "Perussuomalaiset",
        "SKP": "Suomen Kommunistinen Puolue",
        "SSP": "Suomen Senioripuolue",
        "IP": "Itsenäisyyspuolue",
        "SKS": "Suomen Kansan Sinivalkoiset",
        "LIB": "Liberaalit",
        "KA": "Köyhien Asialla",
        "KTP": "Rauhan ja Sosialismin Puolesta - Kommunistinen Työväenpuolue",
        "STP": "Suomen Työväenpuolue STP",
        "SIK": "Suomen Isänmaallinen kansanliike",
        "YVP": "Yhteisvastuu puolue",
    },
}


def members(year: int) -> dict[str, str]:
    """The list name of each party code of the table."""
    codes = {label: code for code, label in table(year).parties.items()}
    return {codes[label]: name for label, name in ABBREVIATIONS[year].items()}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "statfin-evaa")


class Cube:
    """A json-stat table as values by area and by a second variable (the measure, or the party)."""

    def __init__(self, data: dict[str, Any], variable: str) -> None:
        dims = data["dimension"]
        self.labels: dict[str, str] = dims["Alue"]["category"]["label"]
        self.areas: dict[str, int] = dims["Alue"]["category"]["index"]
        self.members: dict[str, int] = dims[variable]["category"]["index"]
        self.values: list[Any] = data["value"]
        self.width = len(self.members)

    def get(self, area: str, member: str) -> int | None:
        value = self.values[self.areas[area] * self.width + self.members[member]]
        return None if value is None else int(value)


@dataclass
class Table:
    """One election's voting and party tables, as the value of each area for each measure."""

    year: int
    voting: dict[str, dict[str, int | None]]
    votes: dict[str, dict[str, int | None]]
    advance: dict[str, dict[str, int | None]]
    labels: dict[str, str]
    parties: dict[str, str]


@functools.cache
def table(year: int) -> Table:
    first = Cube(cube(1, year), VOTING_CODE)
    labels = dict(first.labels)
    voting = {area: {m: first.get(area, m) for m in first.members} for area in first.areas}
    out: list[dict[str, dict[str, int | None]]] = []
    names: dict[str, str] = {}
    for measure in VOTES:
        data = cube(2, year, measure)
        second = Cube(data, "Puolue")
        names = {code: data["dimension"]["Puolue"]["category"]["label"][code] for code in second.members}
        if second.labels != first.labels:
            raise ValueError(f"{year}: the two tables list different areas")
        out.append({area: {m: second.get(area, m) for m in second.members} for area in second.areas})
    return Table(year, voting, out[0], out[1], labels, names)


def level(code: str) -> str:
    """Whether an area row is the country, a constituency, a municipality, or a voting district."""
    if code == "SSS":
        return "country"
    if code.startswith("VP"):
        return "constituency"
    return "municipality" if len(code) == 3 else "district"


@dataclass
class District:
    code: str  # the table's row code
    constituency: str  # the two digits that lead the code
    municipality: str
    number: str  # the district's number within its municipality, as the table prints it
    name: str


LABEL = re.compile(r"(\d{3}) (\w+)\s+(.*)")


def districts(year: int) -> list[District]:
    out = []
    for code, label in table(year).labels.items():
        if level(code) != "district":
            continue
        found = LABEL.fullmatch(label)
        if found is None or found.group(1) != code[2:5] or found.group(2) != code[5:]:
            raise ValueError(f"{year}: district {code} is labeled {label!r}")
        out.append(District(code, code[:2], code[2:5], code[5:], " ".join(found.group(3).split())))
    if len({(d.municipality, d.number) for d in out}) != len(out):
        raise ValueError(f"{year}: two districts share a number")
    return out


def municipality_names(year: int) -> dict[str, str]:
    return {
        code: " ".join(label.split()[1:]) for code, label in table(year).labels.items() if level(code) == "municipality"
    }


def constituencies(year: int) -> dict[str, str]:
    """The constituency code (`HEL`) of each number the table gives (`01`)."""
    out = {}
    for code, label in table(year).labels.items():
        if level(code) == "constituency":
            number, _, name = label.partition(" ")
            out[number] = pages.CONSTITUENCIES[name]
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Packages

METHOD = (
    "Published by Statistics Finland in its StatFin tables of the parliamentary election, from the Ministry of "
    "Justice's results: for each voting district (äänestysalue) the persons entitled to vote, the persons who voted, "
    "the rejected and accepted ballots, and the votes of each party, with the advance votes shown apart. The voters "
    "and ballots are the tables' own totals, which include Finnish citizens living abroad; the results service's "
    "pages by municipality, which the previous result set of this contest holds, leave them out. Advance votes are "
    "stored on the advance channel, and the election-day votes are the total less the advance votes. The party table "
    "names only the registered parties' own lists; every other list (joint lists and electors' associations) is one "
    "row, which is stored on the list's own option where the constituency has one such list and on one option for "
    "all the other lists where it has several. The previous set holds each list and each candidate's votes by "
    "municipality; this one holds the votes by voting district and is the contest's default."
)
LATE = (
    "In {places} the table does not divide the advance votes among the voting districts: the districts' accepted and "
    "rejected ballots and party votes are the election-day votes only, and the advance votes are stored on one "
    "aggregate unit of the municipality, which holds the municipality's row less the sum of its districts."
)


def registry() -> Package:
    p = Package(REGISTRY, SOURCE, "Statistics Finland as the source of Finland's voting district results")
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tilastokeskus",
        title="StatFin passive tables, parliamentary elections 2003 and 2007 by voting district (Statistics Finland)",
        url="https://pxdata.stat.fi/PxWeb/pxweb/fi/StatFin_Passiivi/StatFin_Passiivi__evaa/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Tilastokeskus, StatFin",
    )
    return p


def cited(year: int) -> list[raw.Artifact]:
    out = [raw.cached(SOURCE, table_url(kind, year)) for kind in (1, 2)]
    out += [raw.cached(SOURCE, url, query) for url, query in queries(year)]
    return out


def unit_id(day: date, municipality: str, number: str) -> str:
    return key("unit", day, municipality, number)


@dataclass
class Stats:
    """What the comparison of the districts with the municipalities' pages found."""

    municipalities: int = 0
    off: list[str] = field(default_factory=list)


def election(year: int) -> Package:
    day = DAYS[year]
    t = table(year)
    e = pages.load(FOLDERS[year])
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The parliamentary election of {day} by voting district from Statistics Finland's StatFin tables",
        depends=[REGISTRY, f"{vaalit.SOURCE}/geo", f"{vaalit.SOURCE}/registry", f"{vaalit.SOURCE}/{day}"],
    )
    p.external.add(vaalit.FINLAND)
    for artifact in cited(year):
        p.artifact(artifact, SOURCE_ID)
    expected = set(ABBREVIATIONS[year])
    if {label for code, label in t.parties.items() if code not in (TOTAL_PARTY, OTHERS)} != expected:
        raise ValueError(f"{year}: the table's parties are not the ones named here")
    names = municipality_names(year)
    every = districts(year)
    # Municipalities whose advance votes the table leaves out of the districts
    late = {d.municipality for d in every if t.voting[d.code]["Sar11"] is None}
    if any(t.voting[d.code]["Sar11"] is not None for d in every if d.municipality in late):
        raise ValueError(f"{year}: a municipality divides its advance votes among some districts only")
    by_group = {pages.CONSTITUENCIES[g.name]: g for g in e.groups if g.result is not None}
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    made: set[str] = set()

    def unit(municipality: str, number: str, name: str, kind: str) -> str:
        id = unit_id(day, municipality, number)
        if id not in made:
            made.add(id)
            parent = vaalit.key("unit", day, municipality)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=vaalit.FINLAND,
                scheme_code="fi.aanestysalue",
                code=f"{day}:{municipality}:{number}",
                parent_id=parent,
                area_relation="within",
                area_id=vaalit.area("fi.kunta", municipality),
                kind=kind,
            )
            p.name(id, name, "fi", "official")
        return id

    def split(
        set_id: str, unit_: str, option: str | None, measure: int, total: int | None, advance: int | None
    ) -> None:
        """A count by channel: advance and election day, or one total on the election-day channel when the table
        gives no advance count."""
        if total is None:
            raise ValueError(f"{year}: {unit_} lacks a total")
        if advance is None:
            rows.append((set_id, unit_, option, ELECTION_DAY, measure, total))
            return
        if advance < 0 or advance > total:
            raise ValueError(f"{year}: {unit_} has {advance} advance of {total}")
        rows.append((set_id, unit_, option, ADVANCE, measure, advance))
        rows.append((set_id, unit_, option, ELECTION_DAY, measure, total - advance))

    stats = Stats()
    count = 0
    for number, ck in constituencies(year).items():
        group = by_group[ck]
        assert group.result is not None
        contest = vaalit.key("contest", day, "E", ck)
        previous = vaalit.key("result_set", day, "E", ck)
        set_id = key("result_set", day, ck)
        seen: dict[str, int] = defaultdict(int)
        lists: dict[str, tuple[str, int, int | None]] = {}
        for row in group.result.rows:
            seen[row.name] += 1
            if seen[row.name] > 1:
                raise ValueError(f"{year} {ck}: two lists named {row.name!r}")
            lists[row.name] = (vaalit.key("option", day, "E", ck, "list", row.name, 1), row.votes, row.seats)
        area = f"VP{number}"
        option_of: dict[str, str] = {}
        used: set[str] = set()
        for code, name in members(year).items():
            total = t.votes[area][code]
            if name in lists:
                if lists[name][1] != total:
                    raise ValueError(f"{year} {ck}: {name} has {lists[name][1]} votes on the pages and {total} here")
                option_of[code] = lists[name][0]
                used.add(name)
            elif total:
                raise ValueError(f"{year} {ck}: the table gives {code} {total} votes where the pages have no list")
        rest = [name for name in lists if name not in used]
        others = t.votes[area][OTHERS]
        if sum(lists[name][1] for name in rest) != others:
            raise ValueError(f"{year} {ck}: the other lists have {others} votes in the table, not the pages' sum")
        if len(rest) == 1:
            option_of[OTHERS] = lists[rest[0]][0]
        elif rest:
            option_of[OTHERS] = key("option", day, ck, "others")
            p.add(
                "contest_option",
                id=option_of[OTHERS],
                contest_id=contest,
                kind="list",
                party_id=None,
                ballot_position=None,
                ballot_label="Muut",
            )
        elif others:
            raise ValueError(f"{year} {ck}: votes for other lists where the pages have none")
        p.add(
            "result_set",
            id=set_id,
            election_id=vaalit.key("election", day, "E"),
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            supersedes_id=previous,
            completeness=1.0,
        )
        mine = [d for d in every if d.constituency == number]
        munis = sorted({d.municipality for d in mine})
        p.add(
            "note",
            id=key("note", set_id),
            entity_id=set_id,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        if late.intersection(munis):
            places = ", ".join(names[m] for m in munis if m in late)
            p.add(
                "note",
                id=key("note", set_id, "advance"),
                entity_id=set_id,
                kind="caveat",
                body_md=LATE.format(places=places),
                lang="en",
            )
        # The contest's own counts, which are the constituency's row
        v = t.voting[area]
        for measure, member in ((REGISTERED, "Sar1"), (CAST, "Sar2"), (VALID, "Sar10"), (INVALID, "Sar6")):
            p.add("declared", result_set_id=set_id, option_id=None, measure=measure, value=v[member])
        declared = {option: t.votes[area][code] for code, option in option_of.items()}
        for option, value in declared.items():
            p.add("declared", result_set_id=set_id, option_id=option, measure=VOTES_MEASURE, value=value)
        for name, (option, _, seats) in lists.items():
            if seats:
                p.add("outcome", result_set_id=set_id, option_id=option, kind="won", seats=seats)
        # Each district's counts
        for d in mine:
            id = unit(d.municipality, d.number, d.name, "standard")
            dv, dp, da = t.voting[d.code], t.votes[d.code], t.advance[d.code]
            gap = d.municipality in late
            rows.append((set_id, id, None, 0, REGISTERED, _need(dv["Sar1"], d)))
            split(set_id, id, None, CAST, dv["Sar2"], dv["Sar4"])
            split(set_id, id, None, VALID, dv["Sar10"], None if gap else dv["Sar11"])
            split(set_id, id, None, INVALID, dv["Sar6"], None if gap else dv["Sar8"])
            for code, option in option_of.items():
                if gap and da[code]:
                    raise ValueError(f"{year}: {d.code} has advance votes where the table gives none for its valid")
                split(set_id, id, option, VOTES_MEASURE, dp[code], None if gap else da[code])
        # A municipality whose advance votes the districts lack
        for municipality in (m for m in munis if m in late):
            parts = [d for d in mine if d.municipality == municipality]
            id = unit(municipality, "ennakko", f"{names[municipality]}, ennakkoäänet", "aggregate")
            mv = t.voting[municipality]
            for measure, member in ((VALID, "Sar10"), (INVALID, "Sar6")):
                rest_ = _need(mv[member], municipality) - sum(t.voting[d.code][member] or 0 for d in parts)
                rows.append((set_id, id, None, ADVANCE, measure, rest_))
            for code, option in option_of.items():
                rest_ = _need(t.votes[municipality][code], municipality) - sum(
                    t.votes[d.code][code] or 0 for d in parts
                )
                rows.append((set_id, id, option, ADVANCE, VOTES_MEASURE, rest_))
        count += len(mine)
        _compare(year, group, munis, mine, late, option_of, lists, used, stats)
    if stats.municipalities != len(names):
        raise ValueError(f"{year}: {stats.municipalities} municipalities compared with their pages of {len(names)}")
    if stats.off:
        raise ValueError(f"{year}: districts differ from the municipalities' pages in {stats.off[:5]}")
    p.frame("result_value", frame(rows))
    gapped = [d for d in every if d.municipality in late]
    if gapped:
        p.accept(
            "accounting.ballots_sum",
            f"the table does not divide the advance votes of {', '.join(names[m] for m in sorted(late))} among the "
            f"voting districts: in the {len(gapped)} districts there the accepted and rejected ballots are the "
            "election-day ballots, while the voters include the advance voters; the municipality's advance ballots "
            "are on its aggregate unit",
        )
    return p


def _need(value: int | None, where: object) -> int:
    if value is None:
        raise ValueError(f"no value for {where}")
    return value


def frame(rows: list[tuple[str, str, str | None, int, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "channel": pl.Int64,
            "measure": pl.Int64,
            "value": pl.Int64,
        },
        orient="row",
    ).select(
        "result_set_id",
        "unit_id",
        "option_id",
        "channel",
        "measure",
        count_round=pl.lit(0),
        flags=pl.lit(0),
        value=pl.col("value"),
    )


def _compare(
    year: int,
    group: pages.Group,
    munis: list[str],
    mine: list[District],
    late: set[str],
    option_of: dict[str, str],
    lists: dict[str, tuple[str, int, int | None]],
    used: set[str],
    stats: Stats,
) -> None:
    """Each municipality's districts, with the aggregate for its advance votes, against its page and its table row."""
    t = table(year)
    code_of = {lists[name][0]: code for code, name in members(year).items() if name in used}
    others = [name for name in lists if name not in used]
    for page_unit in group.units:
        municipality = page_unit.code
        if municipality not in munis:
            stats.off.append(f"{municipality} is not in the table's constituency")
            continue
        stats.municipalities += 1
        parts = [d for d in mine if d.municipality == municipality]

        def total(
            code: str, member: str, source: dict[str, dict[str, int | None]], parts: list[District] = parts
        ) -> int:
            return sum(source[d.code][member] or 0 for d in parts)

        row = t.voting[municipality]
        if page_unit.turnout:
            if page_unit.turnout.registered != total("", "Sar12", t.voting):
                stats.off.append(f"{municipality} registered")
            if page_unit.turnout.cast != total("", "Sar13", t.voting):
                stats.off.append(f"{municipality} cast")
        for member in ("Sar1", "Sar2", "Sar4", "Sar6", "Sar8", "Sar10", "Sar11"):
            if municipality in late and member in ("Sar6", "Sar8", "Sar10", "Sar11"):
                continue
            if row[member] != total("", member, t.voting):
                stats.off.append(f"{municipality} {member}")
        if page_unit.result.valid is not None and page_unit.result.valid != row["Sar10"]:
            stats.off.append(f"{municipality} valid")
        for name, (option, _, _) in lists.items():
            listed = {r.name: r.votes for r in page_unit.result.rows}
            if name not in listed:
                continue
            if name in used:
                member = code_of[option]
                have = t.votes[municipality][member]
                districts_ = total("", member, t.votes)
                if listed[name] != have or (municipality not in late and have != districts_):
                    stats.off.append(f"{municipality} {name}")
        if others:
            page_others = sum(r.votes for r in page_unit.result.rows if r.name in others)
            if page_others != t.votes[municipality][OTHERS]:
                stats.off.append(f"{municipality} other lists")


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{DAYS[year]}" for year in YEARS)]


def parse() -> None:
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())
