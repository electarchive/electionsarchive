"""Finland's parliamentary elections of 1983 to 1999 by municipality, from Statistics Finland's StatFin tables.

Table `evaa/13sw` holds the votes of each party and `evaa/13sv` the turnout, both for each constituency and for each
municipality as it was divided in the election year. The 2003 and 2007 elections are in the same tables, but the
`fi.vaalit` pipeline already holds them from the results service's pages, so they are only fetched here to find out
which municipalities that pipeline names.

The tables list each municipality under a present-day constituency group, not the constituency it voted in, so
`statfin_eduskunta.tsv` places every municipality of each election in its constituency. The placement was found by
solving for the assignment under which each constituency's voters, ballots cast, and invalid ballots add up exactly to
the tables' own constituency totals, and each party's votes to its constituency total within the voters abroad. A
build refuses any election in which they do not.
"""

import csv
import functools
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.fi import statfin_eduskunta_seats as seats
from ea.sources.fi import vaalit

SOURCE = "fi.statfin_eduskunta"
API = "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin/evaa"
VOTES_TABLE, TURNOUT_TABLE = "13sw", "13sv"
YEARS = (1983, 1987, 1991, 1995, 1999)
HELD = (2003, 2007, 2011, 2015, 2019, 2023)
DAYS = {
    1983: (date(1983, 3, 20), date(1983, 3, 21)),
    1987: (date(1987, 3, 15), date(1987, 3, 16)),
    1991: (date(1991, 3, 17), date(1991, 3, 18)),
    1995: (date(1995, 3, 19), date(1995, 3, 19)),
    1999: (date(1999, 3, 21), date(1999, 3, 21)),
}
VOTE_CONTENT = ["evaa_aanet"]
TURNOUT_CONTENT = ["evaa_ao", "evaa_aanest", "evaa_hylatyt"]


@dataclass(frozen=True)
class Query:
    table: str
    year: int

    @property
    def url(self) -> str:
        return f"{API}/{self.table}.px"

    @property
    def body(self) -> dict[str, object]:
        votes = self.table == VOTES_TABLE
        selection: list[dict[str, Any]] = [
            {"code": "timeperiod_y", "selection": {"filter": "item", "values": [str(self.year)]}},
            {"code": "sukupuoli_9_20180101", "selection": {"filter": "item", "values": ["SSS"]}},
            {"code": "kunta_109_20230101", "selection": {"filter": "all", "values": ["*"]}},
            {
                "code": "contentscode",
                "selection": {"filter": "item", "values": VOTE_CONTENT if votes else TURNOUT_CONTENT},
            },
        ]
        if votes:
            selection.insert(2, {"code": "puolue_19_20230101", "selection": {"filter": "all", "values": ["*"]}})
        return {"query": selection, "response": {"format": "json-stat2"}}


def queries() -> list[Query]:
    return [Query(table, year) for year in (*YEARS, *HELD) for table in (VOTES_TABLE, TURNOUT_TABLE)]


def _valid(path: object) -> None:
    with open(str(path), "rb") as file:
        if not json.load(file).get("value"):
            raise ValueError(f"{path} holds no values")


def fetch() -> None:
    for table in (VOTES_TABLE, TURNOUT_TABLE):
        raw.fetch(SOURCE, f"{API}/{table}.px", suffix=".json")
    for query in queries():
        raw.fetch(SOURCE, query.url, suffix=".json", body=query.body, check=_valid)
    seats.fetch()


@functools.cache
def cube(table: str, year: int) -> dict[str, Any]:
    query = Query(table, year)
    with raw.cached(SOURCE, query.url, query.body).path.open("rb") as file:
        return json.load(file)


# ---------------------------------------------------------------------------------------------------------------------
# Constituencies

CONSTITUENCIES = {
    "HEL": ("Helsingin vaalipiiri", "Helsingfors valkrets"),
    "UUS": ("Uudenmaan vaalipiiri", "Nylands valkrets"),
    "VAR": ("Varsinais-Suomen vaalipiiri", "Egentliga Finlands valkrets"),
    "SAT": ("Satakunnan vaalipiiri", "Satakunta valkrets"),
    "AHV": ("Ahvenanmaan maakunnan vaalipiiri", "Landskapet Ålands valkrets"),
    "HÄM": ("Hämeen vaalipiiri", "Tavastlands valkrets"),
    "PIR": ("Pirkanmaan vaalipiiri", "Birkalands valkrets"),
    "KYM": ("Kymen vaalipiiri", "Kymmene valkrets"),
    "ESA": ("Etelä-Savon vaalipiiri", "Södra Savolax valkrets"),
    "PSA": ("Pohjois-Savon vaalipiiri", "Norra Savolax valkrets"),
    "KAR": ("Pohjois-Karjalan vaalipiiri", "Norra Karelens valkrets"),
    "VAA": ("Vaasan vaalipiiri", "Vasa valkrets"),
    "KES": ("Keski-Suomen vaalipiiri", "Mellersta Finlands valkrets"),
    "OUL": ("Oulun vaalipiiri", "Uleåborgs valkrets"),
    "LAP": ("Lapin vaalipiiri", "Lapplands valkrets"),
}
# The table's row codes for the constituencies of 1983 to 1999, which begin with these two characters
HEADERS = {
    "01": "HEL",
    "02": "UUS",
    "03": "VAR",
    "04": "SAT",
    "05": "AHV",
    "06": "HÄM",
    "07": "PIR",
    "81": "KYM",
    "82": "ESA",
    "91": "PSA",
    "92": "KAR",
    "10": "VAA",
    "11": "KES",
    "12": "OUL",
    "13": "LAP",
}


class Table:
    """One year of a StatFin table: the value of each municipality or constituency row, by measure or party."""

    def __init__(self, table: str, year: int) -> None:
        data = cube(table, year)
        self.ids: list[str] = data["id"]
        dims = data["dimension"]
        self.area = dims["kunta_109_20230101"]["category"]
        self.parties = dims["puolue_19_20230101"]["category"] if table == VOTES_TABLE else None
        shape = data["size"]
        self.values = [None if v is None else int(v) for v in data["value"]]
        self.shape = shape

    def get(self, area: str, member: str) -> int | None:
        """The value of an area row for a party code (votes) or a content code (turnout)."""
        a = self.area["index"][area]
        if self.parties is not None:
            p = self.parties["index"][member]
            return self.values[p * self.shape[-2] + a]
        content = {"evaa_ao": 0, "evaa_aanest": 1, "evaa_hylatyt": 2}[member]
        return self.values[a * self.shape[-1] + content]

    def label(self, area: str) -> str:
        return str(self.area["label"][area])


# ---------------------------------------------------------------------------------------------------------------------
# Municipalities and constituencies

PLACEMENT = Path(__file__).with_name("statfin_eduskunta.tsv")
NUMBER = re.compile(r"^KU(\d{3}) (.*?)(?: -(\d{2}))?$")


@functools.cache
def placement() -> dict[tuple[int, str], str]:
    """The constituency of each municipality number in each election year (see `statfin_eduskunta.tsv`)."""
    with PLACEMENT.open(newline="") as file:
        return {
            (int(row["year"]), row["number"]): row["constituency"]
            for row in csv.DictReader(file, delimiter="\t")
            if row["constituency"]
        }


@dataclass(frozen=True)
class Municipality:
    number: str
    name: str
    code: str  # the table's six-digit row code
    ended: int | None


def municipalities(year: int) -> list[Municipality]:
    """The municipalities that have voters in an election year, as the table divides them."""
    turnout = Table(TURNOUT_TABLE, year)
    out = []
    for code in turnout.area["index"]:
        found = NUMBER.match(turnout.label(code))
        if found and turnout.get(code, "evaa_ao") is not None:
            ended = int(found.group(3)) if found.group(3) else None
            out.append(
                Municipality(
                    found.group(1),
                    found.group(2),
                    code,
                    None if ended is None else ended + (1900 if ended >= 50 else 2000),
                )
            )
    return out


@functools.cache
def held() -> set[str]:
    """Municipality numbers that the 2003 and later elections have, and `fi.vaalit` therefore holds as areas."""
    return {m.number for year in HELD for m in municipalities(year)}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


STATFIN = key("source", "statfin")
PUBLICATIONS = key("source", "publications")
PARLIAMENT_BODY = vaalit.PARLIAMENT
ALAND = "AHV"


def kunta(number: str) -> str:
    """The area of a municipality: `fi.vaalit`'s where it holds the municipality, else one this pipeline adds."""
    return vaalit.area("fi.kunta", number) if number in held() else key("area", "fi.kunta", number)


def vaalipiiri(code: str) -> str:
    return vaalit.area("fi.vaalipiiri", code)


def cited(years: list[int]) -> list[raw.Artifact]:
    out = [raw.cached(SOURCE, f"{API}/{table}.px") for table in (VOTES_TABLE, TURNOUT_TABLE)]
    for year in years:
        for table in (VOTES_TABLE, TURNOUT_TABLE):
            query = Query(table, year)
            out.append(raw.cached(SOURCE, query.url, query.body))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Packages

TITLE = ("Finnish parliamentary election", "Eduskuntavaalit", "Riksdagsval")
ENGLISH = {
    "HEL": "Helsinki",
    "UUS": "Uusimaa",
    "VAR": "Finland Proper",
    "SAT": "Satakunta",
    "AHV": "Åland",
    "HÄM": "Häme",
    "PIR": "Pirkanmaa",
    "KYM": "Kymi",
    "ESA": "South Savo",
    "PSA": "North Savo",
    "KAR": "North Karelia",
    "VAA": "Vaasa",
    "KES": "Central Finland",
    "OUL": "Oulu",
    "LAP": "Lapland",
}
FULL_NAMES = {
    "KOK": "Kansallinen Kokoomus",
    "SDP": "Suomen Sosialidemokraattinen Puolue",
    "KESK": "Suomen Keskusta",
    "VAS": "Vasemmistoliitto",
    "VIHR": "Vihreä liitto",
    "RKP": "Ruotsalainen kansanpuolue",
    "KD": "Suomen Kristillisdemokraatit",
    "PS": "Perussuomalaiset",
    "SKP": "Suomen Kommunistinen Puolue",
}
PARTY_NOTES = {
    "PS": (
        "The tables group lists by party as it is today. Before 1995 this row holds the votes of the Finnish Rural "
        "Party (Suomen Maaseudun Puolue), which Perussuomalaiset succeeded."
    ),
    "VAS": (
        "The tables group lists by party as it is today. In 1983 and 1987 this row holds the votes of the Finnish "
        "People's Democratic League (Suomen Kansan Demokraattinen Liitto), one of the founders of Vasemmistoliitto."
    ),
    "KD": (
        "The tables group lists by party as it is today. Before 2001 this row holds the votes of the Finnish "
        "Christian League (Suomen Kristillinen Liitto), the party's earlier name."
    ),
}
OTHERS = {"44", "99"}
REGISTERED, CAST, VALID, INVALID, VOTES = 2, 3, 4, 5, 1
CHECKED = "the table's own totals"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
METHOD = (
    "Statistics Finland's StatFin tables of the parliamentary elections (tables 13sw and 13sv) give each "
    "constituency's and each municipality's votes by party, voters, voters who voted, and invalid ballots, with the "
    "municipalities as they were divided in the election year. A municipality is the finest unit the tables publish. "
    "The tables list a municipality under a present-day constituency group, so each municipality is placed in the "
    "constituency it voted in by the one placement under which every constituency's voters, ballots cast, and invalid "
    "ballots add up to the table's own constituency totals; the placement is in the pipeline's source. Voters living "
    "abroad are counted in the constituency of their last home municipality and have no municipality of their own, so "
    "each constituency has one aggregate unit for them. Parties are Statistics Finland's party groups, which carry "
    "the votes of lists of predecessor parties."
)
SEATS = (
    " The tables hold no seats. The seats and the constituency's size are from Statistics Finland's own publication "
    "of the election, and each seat is placed on the party group that holds its list's votes."
)
PRINTED = (
    " The publication's table of persons elected by party and constituency is transcribed in the pipeline's source, "
    "and a build refuses it unless each constituency's seats add up to its size and each party's seats to its "
    "national total, as the same table prints them."
)
LISTED = (
    " The elected members are the candidates marked as elected in Statistics Finland's list of candidates, whose "
    "votes on each list add up to the list's votes in the table. The list's seats equal its elected members."
)
REMAINDER = (
    " The aggregate unit holds the abroad voters and voters who voted as the table prints them, and the invalid "
    "ballots, valid ballots, and party votes that remain of the constituency's total after its municipalities, "
    "flagged as derived."
)
INSIDE = (
    " The table counts the party votes and invalid ballots of the voters abroad in their last home municipality, "
    "although it leaves them out of that municipality's voters who voted, so a municipality's valid and invalid "
    "ballots can exceed its ballots cast. The aggregate unit holds only the abroad voters and voters who voted."
)


def parties() -> dict[str, str]:
    """Each party group the five elections give votes to, by table code, with its label."""
    found: dict[str, str] = {}
    for year in YEARS:
        table = Table(VOTES_TABLE, year)
        assert table.parties is not None
        for code, label in table.parties["label"].items():
            if code != "SSS" and any(table.get(area, code) for area in table.area["index"]):
                found[code] = str(label)
    return dict(sorted(found.items()))


def party(code: str) -> str:
    return key("party", code)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{DAYS[year][0]}" for year in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Finnish municipalities that existed in the parliamentary elections of 1983 to 1999 and not in later ones",
        depends=[f"{vaalit.SOURCE}/geo"],
    )
    p.external.add(vaalit.FINLAND)
    for artifact in cited([*YEARS, *HELD]):
        p.uses(artifact)
    names: dict[str, str] = {}
    for year in YEARS:
        for m in municipalities(year):
            if m.number not in held():
                names[m.number] = m.name
    for number, name in sorted(names.items()):
        id = kunta(number)
        p.add("area", id=id, scheme_code="fi.kunta")
        p.add("area_code", area_id=id, code=number)
        p.add("area_parent", area_id=id, parent_id=vaalit.COUNTRY)
        p.name(id, name, "fi", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Statistics Finland's StatFin tables of the parliamentary elections as a source, and their party groups",
        depends=[f"{vaalit.SOURCE}/geo"],
    )
    p.external.add(vaalit.FINLAND)
    p.add(
        "source",
        id=STATFIN,
        kind="official_body",
        publisher="Tilastokeskus",
        title="StatFin: Parliamentary elections, votes by party and turnout, municipalities as divided in each election "
        "year (tables evaa/13sw and evaa/13sv)",
        url="https://pxdata.stat.fi/PxWeb/pxweb/fi/StatFin/StatFin__evaa/",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Tilastokeskus, StatFin",
    )
    for artifact in cited(list(YEARS)):
        p.artifact(artifact, STATFIN)
    p.add(
        "source",
        id=PUBLICATIONS,
        kind="official_body",
        publisher="Tilastokeskus",
        title="Kansanedustajain vaalit: Statistics Finland's publications and web statistics of the parliamentary "
        "elections of 1983, 1987, 1991, 1995, and 1999, with the seats won by party and constituency and, for 1995 and "
        "1999, the list of candidates",
        url="https://otos.stat.fi/",
        license="CC BY 4.0",
        license_url=LICENSE_URL,
        attribution="Tilastokeskus, Suomen virallinen tilasto",
    )
    for year in YEARS:
        for artifact in seats.cited(year):
            p.artifact(artifact, PUBLICATIONS)
    for code, label in parties().items():
        if code in OTHERS:
            continue
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=vaalit.FINLAND)
        full = FULL_NAMES.get(label)
        if full:
            p.name(id, full, "fi", "official")
        p.name(id, label, "fi", "abbreviation", full is None)
        if label in PARTY_NOTES:
            p.add("note", id=key("note", id), entity_id=id, kind="context", body_md=PARTY_NOTES[label], lang="en")
    return p


def election(year: int) -> Package:
    first, last = DAYS[year]
    votes, turnout = Table(VOTES_TABLE, year), Table(TURNOUT_TABLE, year)
    assert votes.parties is not None
    depends = [f"{vaalit.SOURCE}/geo", f"{vaalit.SOURCE}/registry", f"{SOURCE}/geo", f"{SOURCE}/registry"]
    p = Package(
        f"{SOURCE}/{first}",
        SOURCE,
        f"The {TITLE[0]} of {first} by municipality from Statistics Finland's StatFin tables",
        depends=depends,
    )
    p.external.add(vaalit.FINLAND)
    for artifact in cited([year]):
        p.uses(artifact)
    for artifact in seats.cited(year):
        p.uses(artifact)
    seats.check_national(year)
    won = seats.outcomes(year)
    sizes = seats.sizes(year)
    elected: dict[str, list[seats.Member]] = {}
    list_votes: dict[tuple[str, str], int] = {}
    if year in seats.LISTS:
        members, list_votes = seats.candidates(year)
        for member in members:
            elected.setdefault(member.constituency, []).append(member)
    election_id = p.id("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=vaalit.FINLAND,
        kind="general",
        date_from=first,
        date_to=last,
        status="held",
    )
    p.name(election_id, f"{year} {TITLE[0]}", "en", "official")
    p.name(election_id, f"{TITLE[1]} {year}", "fi", "native", False)
    p.name(election_id, f"{TITLE[2]} {year}", "sv", "native", False)
    path = f"fi/{first}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    groups = {code: group for group, code in HEADERS.items()}
    codes = list(parties())
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    units = municipalities(year)
    by_constituency: dict[str, list[Municipality]] = {code: [] for code in CONSTITUENCIES}
    for m in units:
        by_constituency[placement()[(year, m.number)]].append(m)
    totals = dict.fromkeys(("registered", "cast", "invalid", "valid"), 0)
    inside = sum(votes.get(m.code, "SSS") or 0 for m in units) == votes.get("SSS", "SSS")
    off = 0
    for code, (fi, sv) in CONSTITUENCIES.items():
        group = groups[code]
        header = f"{group}0000"
        contest = p.id("contest", year, code)
        result_set = p.id("result_set", year, code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            office_id=None,
            body_id=PARLIAMENT_BODY,
            district_area_id=vaalipiiri(code),
            magnitude=sizes[code],
            round_number=1,
            previous_round_id=None,
            electoral_system_id=vaalit.key("electoral_system", "aland")
            if code == ALAND
            else vaalit.system("parliament"),
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {ENGLISH[code]} constituency", "en", "official")
        p.name(contest, fi, "fi", "native", False)
        p.name(contest, sv, "sv", "native", False)
        p.add("slug", path=f"{path}/{changeset.slug(fi)}", entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=STATFIN,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=p.id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + (INSIDE if inside else REMAINDER) + SEATS + (LISTED if year in seats.LISTS else PRINTED),
            lang="en",
        )
        p.add(
            "citation",
            id=p.id("citation", result_set),
            entity_id=result_set,
            source_id=PUBLICATIONS,
            locator={
                "files": [artifact.url for artifact in seats.cited(year)],
                "seats": seats.PAGES.get(year, "seat table"),
            },
        )
        options: dict[str, str] = {}
        for party_code in codes:
            if votes.get(header, party_code) is None:
                continue
            option = options[party_code] = p.id("option", year, code, party_code)
            label = str(votes.parties["label"][party_code])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if party_code in OTHERS else "list",
                parent_option_id=None,
                party_id=None if party_code in OTHERS else party(party_code),
                ballot_position=None,
                ballot_label=label,
            )
        for party_code, n in sorted(won.get(code, {}).items()):
            if party_code not in options:
                raise ValueError(f"{year} {code}: seats for party {party_code}, which has no votes there")
            p.add("outcome", result_set_id=result_set, option_id=options[party_code], kind="won", seats=n)
        for member in elected.get(code, []):
            if member.party not in options:
                raise ValueError(f"{year} {code}: {member.name} was elected for party {member.party}, with no votes")
            if list_votes[(code, member.party)] != votes.get(header, member.party):
                raise ValueError(
                    f"{year} {code}: the list of party {member.party} has {list_votes[(code, member.party)]} votes, "
                    f"the table {votes.get(header, member.party)}"
                )
            person = p.id("person", year, code, member.party, member.name)
            p.add("person", id=person)
            p.name(person, seats.person_name(member.name), "fi", "official")
            candidacy = p.id("candidacy", year, code, member.party, member.name)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[member.party],
                role="list_member",
                ballot_name=member.name,
                is_independent=False,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[member.party],
                candidacy_id=candidacy,
                kind="elected",
            )
        if year in seats.LISTS:
            counted = Counter(member.party for member in elected.get(code, []))
            if counted != Counter(won.get(code, {})):
                raise ValueError(f"{year} {code}: elected {dict(counted)}, seats {won.get(code)}")
        # The constituency's printed totals
        registered = turnout.get(header, "evaa_ao")
        cast = turnout.get(header, "evaa_aanest")
        invalid = turnout.get(header, "evaa_hylatyt") or 0
        valid = votes.get(header, "SSS")
        assert registered is not None and cast is not None and valid is not None
        for measure, value in ((REGISTERED, registered), (CAST, cast), (VALID, valid), (INVALID, invalid)):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for party_code, option in options.items():
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=VOTES,
                value=votes.get(header, party_code),
            )
        # The municipalities
        sums = dict.fromkeys(("registered", "cast", "invalid", "valid"), 0)
        party_sums = dict.fromkeys(options, 0)
        for m in by_constituency[code]:
            unit = p.id("unit", year, m.number)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=vaalit.FINLAND,
                scheme_code="fi.kuntalaskenta",
                code=f"{first}:{m.number}",
                area_relation="equals",
                area_id=kunta(m.number),
                kind="standard",
            )
            p.name(unit, m.name, "fi", "official")
            reg = turnout.get(m.code, "evaa_ao")
            cst = turnout.get(m.code, "evaa_aanest")
            inv = turnout.get(m.code, "evaa_hylatyt") or 0
            val = votes.get(m.code, "SSS")
            assert reg is not None and cst is not None and val is not None
            for measure, value in ((REGISTERED, reg), (CAST, cst), (VALID, val), (INVALID, inv)):
                rows.append((result_set, unit, None, measure, value, 0))
            off += val + inv != cst
            sums["registered"] += reg
            sums["cast"] += cst
            sums["invalid"] += inv
            sums["valid"] += val
            for party_code, option in options.items():
                value = votes.get(m.code, party_code)
                if value is not None:
                    rows.append((result_set, unit, option, VOTES, value, 0))
                    party_sums[party_code] += value
        # The voters abroad
        unit = p.id("unit", year, code, "abroad")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=vaalit.FINLAND,
            scheme_code="fi.kuntalaskenta",
            code=f"{first}:{code}:abroad",
            area_relation="equals",
            area_id=vaalipiiri(code),
            kind="aggregate",
        )
        p.name(unit, "Ulkomailla asuvat Suomen kansalaiset", "fi", "official")
        p.name(unit, "Finnish citizens living abroad", "en", "official", False)
        if inside != (sums["valid"] == valid):
            raise ValueError(
                f"{year} {code}: the abroad voters' party votes are inside the municipalities in only some"
            )
        counted = {REGISTERED: registered, CAST: cast, INVALID: invalid}
        for measure, member, summed in (
            (REGISTERED, "evaa_ao", sums["registered"]),
            (CAST, "evaa_aanest", sums["cast"]),
            (INVALID, "evaa_hylatyt", sums["invalid"]),
        ):
            abroad = turnout.get(group + "U000", member)
            resident = turnout.get(group + "S000", member)
            if inside and measure == INVALID:
                # The table counts the abroad voters' invalid ballots in the municipalities, and prints none apart
                if summed != invalid:
                    raise ValueError(f"{year} {code}: invalid ballots {summed} of the municipalities, {invalid}")
                continue
            if summed != (resident or 0) or (resident or 0) + (abroad or 0) != counted[measure]:
                raise ValueError(
                    f"{year} {code}: {member} municipalities {summed}, residents {resident}, abroad {abroad}, "
                    f"total {counted[measure]}"
                )
            rows.append((result_set, unit, None, measure, abroad or 0, 0))
        if inside:
            if any(votes.get(header, c) != party_sums[c] for c in options):
                raise ValueError(f"{year} {code}: party votes of the municipalities differ from the constituency's")
        else:
            abroad_valid = (cast - sums["cast"]) - (invalid - sums["invalid"])
            rows.append((result_set, unit, None, VALID, abroad_valid, 1))
            remainder = 0
            for party_code, option in options.items():
                value = (votes.get(header, party_code) or 0) - party_sums[party_code]
                if value < 0:
                    raise ValueError(f"{year} {code}: party {party_code} has fewer votes than its municipalities")
                remainder += value
                rows.append((result_set, unit, option, VOTES, value, 1))
            if remainder != abroad_valid or valid - sums["valid"] != abroad_valid:
                raise ValueError(f"{year} {code}: abroad party votes {remainder}, valid {abroad_valid}")
        totals["registered"] += registered
        totals["cast"] += cast
        totals["invalid"] += invalid
        totals["valid"] += valid
    national = {
        "registered": turnout.get("SSS", "evaa_ao"),
        "cast": turnout.get("SSS", "evaa_aanest"),
        "invalid": turnout.get("SSS", "evaa_hylatyt"),
        "valid": votes.get("SSS", "SSS"),
    }
    if totals != national:
        raise ValueError(f"{year}: constituencies add up to {totals}, the table's national row is {national}")
    p.frame("result_value", frame(rows))
    if off:
        p.accept(
            "accounting.ballots_sum",
            f"the table counts the voters abroad's party votes and invalid ballots in their home municipality but not "
            f"in its voters who voted, so valid and invalid ballots exceed the ballots cast in {off} municipalities; "
            "stored as printed",
        )
    return p


def frame(rows: list[tuple[str, str, str | None, int, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int64,
            "value": pl.Int64,
            "flags": pl.Int64,
        },
        orient="row",
    ).select(
        "result_set_id",
        "unit_id",
        "option_id",
        channel=pl.lit(0),
        measure=pl.col("measure"),
        count_round=pl.lit(0),
        flags=pl.col("flags"),
        value=pl.col("value"),
    )
