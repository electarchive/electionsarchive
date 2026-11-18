import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import product
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.se import val

SOURCE = "se.scb"
API = "https://api.scb.se/OV0104/v1/doris/sv/ssd/ME/ME0104"
YEARS = [1973, 1976, 1979, 1982, 1985, 1988, 1991, 1994, 1998]
WORKERS = 2

# Table path, the variable that carries the region, and the variables selected in full.
TABLES = {
    "ME0104T3": ("ME0104C", "Region"),
    "ME0104T2": ("ME0104B", "Region"),
    "ME0104T1": ("ME0104A", "Region"),
    "Riksdagsmandat": ("ME0104C", "Region"),
    "Ltmandat": ("ME0104B", "Region"),
    "Kfmandat": ("ME0104A", "Region"),
    "RiksdagsvalRostb7698": ("ME0104C", "Region"),
    "Landstingsmed": ("ME0104B", "Region"),
    "Kommunmed": ("ME0104A", "Region"),
    "ME0104T5": ("ME0104D", "Region"),
}


EP_API = "https://api.scb.se/OV0104/v1/doris/sv/ssd/ME/ME0109"
EP_TABLES = {"EUvalA": "ME0109A", "ME0109T01": "ME0109A", "EUmed": "ME0109B"}
EP_YEARS = [1995, 1999]
EP_DATES = {1995: date(1995, 9, 17), 1999: date(1999, 6, 13)}


def url(table: str) -> str:
    if table in EP_TABLES:
        return f"{EP_API}/{EP_TABLES[table]}/{table}"
    return f"{API}/{TABLES[table][0]}/{table}"


def variables(table: str) -> list[dict[str, str | list[str]]]:
    return json.loads(raw.cached(SOURCE, url(table)).path.read_text("utf-8"))["variables"]


def query(table: str, year: int) -> dict[str, object]:
    selection = []
    for variable in variables(table):
        values = [str(year)] if variable["code"] == "Tid" else variable["values"]
        selection.append({"code": variable["code"], "selection": {"filter": "item", "values": values}})
    return {"query": selection, "response": {"format": "json-stat2"}}


def _get(job: tuple[str, int]) -> None:
    table, year = job
    for attempt in range(6):
        try:
            raw.fetch(SOURCE, url(table), suffix=".json", body=query(table, year))
            return
        except httpx.TransportError, httpx.HTTPStatusError:
            time.sleep(5 * (attempt + 1))
    raw.fetch(SOURCE, url(table), suffix=".json", body=query(table, year))


def fetch() -> None:
    for table in TABLES:
        raw.fetch(SOURCE, url(table), suffix=".json")
    for table in EP_TABLES:
        raw.fetch(SOURCE, url(table), suffix=".json")
    jobs = [(table, year) for table in TABLES for year in YEARS]
    jobs += [(table, year) for table in EP_TABLES for year in EP_YEARS]
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(_get, jobs))


# Membership of municipalities in the constituencies and county councils of Skåne and Västra Götaland. Statistics
# Sweden codes municipalities by today's numbers, which do not say which of the old districts a municipality
# belonged to, so each assignment was found by solving for the unique split of the municipalities whose party
# totals add up to every published constituency (or county council) total of every election in the period.
# `parse` re-checks the sums and fails on any mismatch. The assignments are in scb_members.tsv: R_OLD is the Riksdag constituency
# in 1973 to 1991, R_NEW in 1994 and 1998 (Mullsjö and Habo moved to Jönköping County in 1998), L_OLD the county council
# in 1973 to 1994, and L_NEW in 1998.


def _members() -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for line in (Path(__file__).parent / "scb_members.tsv").read_text().splitlines()[1:]:
        table, town, code = line.split("\t")
        found.setdefault(table, {})[town] = code
    return found


MEMBERS = _members()
R_OLD, R_NEW, L_OLD, L_NEW = (MEMBERS[name] for name in ("R_OLD", "R_NEW", "L_OLD", "L_NEW"))


# Reading the tables.

CONTENTS = {
    "ME0104T3": "ME0104B6",
    "ME0104T2": "ME0104B4",
    "ME0104T1": "ME0104B1",
    "Riksdagsmandat": "ME0104C3",
    "Ltmandat": "ME0104C2",
    "Kfmandat": "ME0104C1",
    "RiksdagsvalRostb7698": "ME0104B3",
    "Landstingsmed": "ME0104A9",
    "Kommunmed": "ME0104A8",
}
VOTES_TABLE = {"R": "ME0104T3", "L": "ME0104T2", "K": "ME0104T1"}
SEATS_TABLE = {"R": "Riksdagsmandat", "L": "Ltmandat", "K": "Kfmandat"}
ELECTORATE_TABLE = {"R": "RiksdagsvalRostb7698", "L": "Landstingsmed", "K": "Kommunmed"}
DATES = {
    1973: date(1973, 9, 16),
    1976: date(1976, 9, 19),
    1979: date(1979, 9, 16),
    1982: date(1982, 9, 19),
    1985: date(1985, 9, 15),
    1988: date(1988, 9, 18),
    1991: date(1991, 9, 15),
    1994: date(1994, 9, 18),
    1998: date(1998, 9, 20),
}
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
PARTIES = ["M", "C", "FP", "KD", "MP", "S", "V", "SD", "NYD", "ÖVRIGA"]
LABELS = {
    "M": "Moderaterna",
    "C": "Centerpartiet",
    "FP": "Liberalerna",
    "KD": "Kristdemokraterna",
    "MP": "Miljöpartiet",
    "S": "Socialdemokraterna",
    "V": "Vänsterpartiet",
    "SD": "Sverigedemokraterna",
    "NYD": "Ny demokrati",
    "ÖVRIGA": "Övriga partier",
}
ABBREVIATIONS = {"FP": "L"}
Table = dict[str, dict[str, int]]


def read(table: str, year: int) -> Table:
    """One table for one election year: region code, then the second dimension's code (party or citizenship; empty
    for a table with only a region), then the figure. Cells the source leaves out are not in the result."""
    d = json.loads(raw.cached(SOURCE, url(table), query(table, year)).path.read_text("utf-8"))
    ids: list[str] = d["id"]
    codes: list[list[str]] = []
    for name in ids:
        index = d["dimension"][name]["category"]["index"]
        codes.append(list(index) if isinstance(index, list) else sorted(index, key=index.__getitem__))
    wanted = CONTENTS[table]
    found: Table = {}
    for position, combo in enumerate(product(*codes)):
        value = d["value"][position]
        coordinates = dict(zip(ids, combo, strict=True))
        if value is None or coordinates["ContentsCode"] != wanted:
            continue
        other = [c for name, c in coordinates.items() if name not in ("Region", "ContentsCode", "Tid")]
        found.setdefault(coordinates["Region"], {})[other[0] if other else ""] = int(value)
    return found


def names(table: str, year: int) -> dict[str, str]:
    d = json.loads(raw.cached(SOURCE, url(table), query(table, year)).path.read_text("utf-8"))
    return dict(d["dimension"]["Region"]["category"]["label"])


def is_town(code: str) -> bool:
    return len(code) == 4 and code.isdigit()


# Where each unit belongs.

CONSTITUENCY_OF_COUNTY = {
    "01": "02",
    "03": "03",
    "04": "04",
    "05": "05",
    "06": "06",
    "07": "07",
    "08": "08",
    "09": "09",
    "10": "10",
    "13": "15",
    "17": "21",
    "18": "22",
    "19": "23",
    "20": "24",
    "21": "25",
    "22": "26",
    "23": "27",
    "24": "28",
    "25": "29",
}
HEBY = "0331"
"""Heby, coded today in Uppsala County, which it joined in 2007; in these elections it belonged to Västmanland County."""
MOVED_IN_1998 = ("0642", "0643")
"""Mullsjö and Habo, which left Skaraborg County for Jönköping County in 1998."""


def constituency(town: str, year: int) -> str:
    moved = year >= 1998 and town in MOVED_IN_1998
    table = R_OLD if year < 1994 else R_NEW
    if town in table and not moved:
        return table[town]
    if town == "0180":
        return "01"
    return "23" if town == HEBY else CONSTITUENCY_OF_COUNTY[town[:2]]


def council(town: str, year: int) -> str:
    moved = year >= 1998 and town in MOVED_IN_1998
    table = L_OLD if year < 1998 else L_NEW
    if town == HEBY:
        return "19"
    return table[town] if town in table and not moved else town[:2]


def own_code(code: str) -> str:
    """A published region code as a plain code: 14LG is 14G, 11L is 11, VR1 is 01, VR12G is 12G. Dalarna's 20LG is the
    same county council as 20."""
    if code.startswith("VR"):
        return code[2:].zfill(2 if code[2:].isdigit() else 3)
    if code == "20LG":
        return "20"
    return code.removesuffix("LG") + "G" if code.endswith("LG") else code.removesuffix("L")


# Names as they were.

COUNTY_NAMES = {
    "01": "Stockholms län",
    "03": "Uppsala län",
    "04": "Södermanlands län",
    "05": "Östergötlands län",
    "06": "Jönköpings län",
    "07": "Kronobergs län",
    "08": "Kalmar län",
    "10": "Blekinge län",
    "12": "Skåne län",
    "13": "Hallands län",
    "14": "Västra Götalands län",
    "17": "Värmlands län",
    "18": "Örebro län",
    "19": "Västmanlands län",
    "20": "Dalarnas län",
    "21": "Gävleborgs län",
    "22": "Västernorrlands län",
    "23": "Jämtlands län",
    "24": "Västerbottens län",
    "25": "Norrbottens län",
}
OLD_COUNTIES = {
    "11": "Kristianstads län",
    "12G": "Malmöhus län",
    "14G": "Göteborgs och Bohus län",
    "15": "Älvsborgs län",
    "16": "Skaraborgs län",
}
OLD_CONSTITUENCIES = {"12G": "Fyrstadskretsen", "13G": "Malmöhus läns valkrets"}
OLD_COUNCILS = {
    "11": "Kristianstads läns landsting",
    "12G": "Malmöhus läns landsting",
    "14G": "Bohuslandstinget",
    "15": "Älvsborgs läns landsting",
    "16": "Skaraborgs läns landsting",
}
BARA = "1229"
"""A municipality of Malmöhus County that Statistics Sweden lists for the 1973 elections only; it was merged into
Svedala."""


def constituency_name(code: str, year: int, modern: dict[str, str]) -> str:
    then: dict[str, str] = {}
    if year < 1998:
        then = {
            "14": "Kristianstads läns valkrets",
            "17": "Bohusläns valkrets",
            "18": "Älvsborgs läns norra valkrets",
            "19": "Älvsborgs läns södra valkrets",
            "20": "Skaraborgs läns valkrets",
            "24": "Kopparbergs läns valkrets",
        }
    if year == 1994:
        then |= {"12": "Malmöhus läns norra valkrets", "13": "Malmöhus läns södra valkrets"}
    return then.get(code) or modern[code]


def council_name(code: str, year: int) -> str:
    if code in OLD_COUNCILS:
        return OLD_COUNCILS[code]
    if code == "20" and year < 1998:
        return "Kopparbergs läns landsting"
    return COUNTY_NAMES[code].replace(" län", " läns landsting")


# Keys. Areas, bodies, and the Riksdag that the Election Authority's pipeline already holds are referenced by its keys.

SCHEMES = {val.COUNTY, val.MUNICIPALITY, val.CONSTITUENCY}
OWN_AREAS = (
    {(val.COUNTY, code) for code in OLD_COUNTIES}
    | {(val.CONSTITUENCY, code) for code in OLD_CONSTITUENCIES}
    | {(val.MUNICIPALITY, BARA)}
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fstatistikdatabasen"))
EP_SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fstatistikdatabasen-eu"))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code) if (scheme, code) in OWN_AREAS else val.area(scheme, code)


def region_body(code: str) -> str:
    return key("body", "region", code)


def town_body(code: str) -> str:
    return key("body", "kommun", code)


RIKSDAG = key("body", "riksdag")


def party(code: str) -> str:
    return key("party", code)


def system(name: str) -> str:
    return key("electoral_system", name)


SWEDEN = val.SWEDEN
COUNTRY = val.COUNTRY
LICENSE = "CC0 1.0"


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Statistics Sweden (Statistiska centralbyrån)",
        title="Statistikdatabasen: Allmänna val, valresultat 1973–2022 (ME0104)",
        url="https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__ME__ME0104/",
        license=LICENSE,
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Statistics Sweden (SCB)",
    )
    p.add(
        "source",
        id=EP_SOURCE_ID,
        kind="official_body",
        publisher="Statistics Sweden (Statistiska centralbyrån)",
        title="Statistikdatabasen: Europaparlamentsval, valresultat 1995–2024 (ME0109)",
        url="https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__ME__ME0109/",
        license=LICENSE,
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Statistics Sweden (SCB)",
    )


# Packages.


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Sweden's former Riksdag constituencies, county councils' counties, and municipality as Statistics Sweden "
        "lists them for 1973 to 1998",
        depends=[f"{val.SOURCE}/geo"],
    )
    p.external |= {SWEDEN, COUNTRY, *SCHEMES}
    _source(p)

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "sv", "official")

    for code, text in OLD_COUNTIES.items():
        place(val.COUNTY, code, text, COUNTRY)
    for code, text in OLD_CONSTITUENCIES.items():
        place(val.CONSTITUENCY, code, text, COUNTRY)
    place(val.MUNICIPALITY, BARA, "Bara", area(val.COUNTY, "12G"))
    p.add(
        "note",
        id=key("note", area(val.MUNICIPALITY, BARA)),
        entity_id=area(val.MUNICIPALITY, BARA),
        kind="context",
        body_md="Statistics Sweden lists Bara as a municipality in the 1973 elections only; the municipality was "
        "merged with Svedala.",
        lang="en",
    )
    return p


SYSTEMS: dict[str, tuple[date, date | None, dict[str, object], str]] = {
    "riksdag-1971": (
        date(1971, 1, 1),
        date(1976, 1, 1),
        val.SYSTEMS["riksdag-1998"][2],
        (
            "The Riksdag's 350 seats: 310 fixed constituency seats are allocated within the constituencies by the "
            "modified Sainte-Laguë method (first divisor 1.4), and 40 adjustment seats bring each party's national "
            "total to its proportional share. Parties need 4% of the national vote, or 12% in a constituency for that "
            "constituency's seats."
        ),
    ),
    "riksdag-1976": (
        date(1976, 1, 1),
        date(1998, 1, 1),
        val.SYSTEMS["riksdag-1998"][2],
        val.SYSTEMS["riksdag-1998"][3],
    ),
    "ep-1995": val.SYSTEMS["ep-1995"],
    "riksdag-1998": val.SYSTEMS["riksdag-1998"],
    "region-1998": val.SYSTEMS["region-1998"],
    "kommun-1998": val.SYSTEMS["kommun-1998"],
}


def _bodies() -> tuple[dict[str, str], dict[str, str]]:
    """The county council and municipal council codes of every election, by name."""
    counties = {**COUNTY_NAMES, **OLD_COUNTIES}
    towns: dict[str, str] = {}
    for year in YEARS:
        towns |= {code: text for code, text in names("ME0104T1", year).items() if is_town(code)}
    return counties, towns


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Sweden's parties as Statistics Sweden groups them, the Riksdag, the county and municipal councils, and their "
        "electoral systems of 1971 to 2017",
    )
    p.external |= {SWEDEN}
    p.add("body", id=RIKSDAG, polity_id=SWEDEN, kind="unicameral")
    p.name(RIKSDAG, "Riksdag", "en", "official")
    p.name(RIKSDAG, "Sveriges riksdag", "sv", "native", False)
    for name, (since, until, rules, method) in SYSTEMS.items():
        id = system(name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=SWEDEN,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code in PARTIES:
        if code == "ÖVRIGA":
            continue
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=SWEDEN)
        p.name(id, LABELS[code], "sv", "official")
        p.name(id, ABBREVIATIONS.get(code, code), "sv", "abbreviation", False)
    counties, towns = _bodies()
    for code, text in sorted(counties.items()):
        p.add("body", id=region_body(code), polity_id=SWEDEN, kind="council")
        p.name(region_body(code), f"Landstingsfullmäktige, {text}", "sv", "official")
    for code, text in sorted(towns.items()):
        p.add("body", id=town_body(code), polity_id=SWEDEN, kind="council")
        p.name(town_body(code), f"Kommunfullmäktige i {text}", "sv", "official")
    return p


ELECTIONS = {
    "R": ("general", "{year} Swedish general election", "Riksdagsvalet {year}", "general"),
    "L": ("local", "{year} Swedish county council elections", "Landstingsvalen {year}", "regional"),
    "K": ("local", "{year} Swedish municipal elections", "Kommunvalen {year}", "municipal"),
}
METHOD = (
    "Statistics Sweden's election statistics, compiled from the election authorities' counts per municipality. Party "
    "votes are the valid votes. Invalid ballots (ogiltiga valsedlar) include blank ones. Valid votes and ballots cast "
    "are summed from the published figures; the electorate and the number of people who did not vote are published, "
    "and electorate, ballots cast, and non-voters add up in every municipality. Municipalities carry their present "
    "codes, and a municipality that did not yet exist is absent. Where Statistics Sweden gives no figure for a party "
    "in a municipality, none is stored."
)


class Contest(NamedTuple):
    body: str
    code: str
    name: str
    area: str
    body_id: str
    towns: list[str]
    published: dict[str, int] | None
    registered: int | None
    seats: dict[str, int]


def contests(year: int) -> tuple[list[Contest], dict[str, Table]]:
    """Every contest of one election year, from the tables."""
    tables = {t: read(t, year) for t in CONTENTS}
    modern = names("ME0104T3", year)
    towns = names("ME0104T1", year)
    found: list[Contest] = []
    # Riksdag constituencies.
    votes = tables["ME0104T3"]
    seats = tables["Riksdagsmandat"]
    published = {own_code(c): c for c in votes if c.startswith("VR") and c != "VR00"}
    members: dict[str, list[str]] = {}
    for town in sorted(c for c in votes if is_town(c)):
        members.setdefault(constituency(town, year), []).append(town)
    if set(members) != set(published):
        raise ValueError(f"{year}: constituencies {sorted(members)} differ from the published {sorted(published)}")
    for code in sorted(published):
        name = constituency_name(code, year, {own_code(c): modern[c] for c in published.values()})
        found.append(
            Contest(
                "R",
                code,
                name,
                area(val.CONSTITUENCY, code),
                RIKSDAG,
                members[code],
                votes[published[code]],
                sum(tables["RiksdagsvalRostb7698"][published[code]].values()),
                seats.get(published[code], {}),
            )
        )
    # County councils.
    votes = tables["ME0104T2"]
    seats = tables["Ltmandat"]
    published = {own_code(c): c for c in votes if not is_town(c) and c != "00L"}
    members = {}
    for town in sorted(c for c in votes if is_town(c)):
        members.setdefault(council(town, year), []).append(town)
    if set(members) != set(published):
        raise ValueError(f"{year}: county councils {sorted(members)} differ from the published {sorted(published)}")
    for code in sorted(published):
        found.append(
            Contest(
                "L",
                code,
                council_name(code, year),
                area(val.COUNTY, code),
                region_body(code),
                members[code],
                votes[published[code]],
                sum(tables["Landstingsmed"][published[code]].values()),
                seats.get(published[code], {}),
            )
        )
    # Municipal councils.
    votes = tables["ME0104T1"]
    for town in sorted(c for c in votes if is_town(c)):
        found.append(
            Contest(
                "K",
                town,
                towns[town],
                area(val.MUNICIPALITY, town),
                town_body(town),
                [town],
                None,
                None,
                tables["Kfmandat"].get(town, {}),
            )
        )
    return found, tables


def election(year: int) -> Package:
    day = DATES[year]
    found, tables = contests(year)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Sweden's Riksdag, county council, and municipal elections of {day:%-d %B %Y} by municipality from Statistics "
        "Sweden",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{val.SOURCE}/geo"],
    )
    p.external |= {SWEDEN, COUNTRY, *SCHEMES}
    for table in CONTENTS:
        p.uses(raw.cached(SOURCE, url(table), query(table, year)))
        p.uses(raw.cached(SOURCE, url(table)))
    elections: dict[str, str] = {}
    for body in "RLK":
        kind, english, swedish, word = ELECTIONS[body]
        id = elections[body] = key("election", day, body)
        p.add(
            "election",
            id=id,
            polity_id=SWEDEN,
            kind=kind,
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        p.name(id, english.format(year=year), "en", "official")
        p.name(id, swedish.format(year=year), "sv", "native", False)
        p.add("slug", path=f"se/{day}-{word}", entity_id=id, is_current=True)
    towns = sorted({t for c in found for t in c.towns})
    unit_ids = {t: key("unit", day, t) for t in towns}
    unit_names = names("ME0104T1", year)
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": [unit_ids[t] for t in towns],
                "polity_id": [SWEDEN] * len(towns),
                "scheme_code": [val.MUNICIPALITY] * len(towns),
                "code": [f"{day}:{t}" for t in towns],
                "area_relation": ["equals"] * len(towns),
                "area_id": [area(val.MUNICIPALITY, t) for t in towns],
                "kind": ["standard"] * len(towns),
            }
        ),
    )
    for t in towns:
        p.name(unit_ids[t], unit_names[t], "sv", "official")
    rows: list[tuple[str, str, str | None, int, int]] = []
    system_name = "riksdag-1971" if year < 1976 else "riksdag-1976"
    words = [(c.body, changeset.slug(c.name)) for c in found]
    for c in found:
        word = changeset.slug(c.name)
        if words.count((c.body, word)) > 1:
            word = f"{word}-{c.code}"
        contest_id = key("contest", day, c.body, c.code)
        magnitude = sum(c.seats.values())
        if c.body == "R":
            system_id = system(system_name if year < 1998 else "riksdag-1998")
        elif year >= 1998:
            system_id = system("region-1998" if c.body == "L" else "kommun-1998")
        else:
            system_id = None
        p.add(
            "contest",
            id=contest_id,
            election_id=elections[c.body],
            kind="list_allocation",
            body_id=c.body_id,
            district_area_id=c.area,
            magnitude=magnitude or None,
            round_number=1,
            electoral_system_id=system_id,
            default_result_kind="counted",
        )
        p.name(contest_id, c.name, "sv", "official")
        p.add("slug", path=f"se/{day}-{ELECTIONS[c.body][3]}/{word}", entity_id=contest_id, is_current=True)
        result_set = key("result_set", day, c.body, c.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[c.body],
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + _lumped(year, c.body),
            lang="en",
        )
        votes = tables[VOTES_TABLE[c.body]]
        electorate = tables[ELECTORATE_TABLE[c.body]]
        present = [code for code in PARTIES if any(code in votes[t] for t in c.towns) or c.seats.get(code, 0) > 0]
        options = {code: key("option", day, c.body, c.code, code) for code in present}
        for code in present:
            p.add(
                "contest_option",
                id=options[code],
                contest_id=contest_id,
                kind="others" if code == "ÖVRIGA" else "list",
                party_id=None if code == "ÖVRIGA" else party(code),
                ballot_position=None,
                ballot_label=LABELS[code],
            )
            if c.seats.get(code, 0) > 0:
                p.add("outcome", result_set_id=result_set, option_id=options[code], kind="won", seats=c.seats[code])
        blanks: list[str] = []
        for t in c.towns:
            row = votes[t]
            valid = sum(row.get(code, 0) for code in PARTIES)
            invalid = row.get("OGILTIGA", 0)
            registered = sum(electorate[t].values())
            if valid + invalid + row.get("VALSKOLKARE", 0) != registered:
                raise ValueError(f"{year} {c.body} {t}: valid, invalid, and non-voting do not add up to the electorate")
            if "OGILTIGA" not in row:
                blanks.append(t)
            for code in present:
                if code in row:
                    rows.append((result_set, unit_ids[t], options[code], VOTES, row[code]))
            rows += [
                (result_set, unit_ids[t], None, REGISTERED, registered),
                (result_set, unit_ids[t], None, CAST, valid + invalid),
                (result_set, unit_ids[t], None, VALID, valid),
                (result_set, unit_ids[t], None, INVALID, invalid),
            ]
        if blanks:
            p.add(
                "note",
                id=key("note", result_set, "invalid"),
                entity_id=result_set,
                kind="caveat",
                body_md="Statistics Sweden gives no count of invalid ballots for "
                + ", ".join(unit_names[t] for t in blanks)
                + "; with none, the electorate equals the valid votes plus the non-voters, so none are stored.",
                lang="en",
            )
        if c.published is not None:
            for code in present:
                if code in c.published:
                    p.add(
                        "declared",
                        result_set_id=result_set,
                        option_id=options[code],
                        measure=VOTES,
                        value=c.published[code],
                    )
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=None,
                measure=INVALID,
                value=c.published.get("OGILTIGA", 0),
            )
            p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=c.registered)
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0), count_round=pl.lit(0), flags=pl.lit(0)),
    )
    return p


def _lumped(year: int, body: str) -> str:
    text = ""
    last = 1994 if body == "R" else 1998
    if 1988 <= year <= last:
        text += " The Sweden Democrats' votes are counted in 'other parties' (övriga partier) in this table."
    if year == 1991 and body == "R":
        text += " New Democracy's votes are counted in 'other parties' too, and its seats are shown on its own option."
    return text


EP_TABLE = {"votes": "EUvalA", "seats": "ME0109T01", "electorate": "EUmed"}
EP_PARTIES = ["M", "C", "FP", "KD", "MP", "S", "V", "ÖVRIGA"]
EP_METHOD = (
    "Statistics Sweden's election statistics for the European Parliament, compiled from the election authorities' "
    "counts per municipality. Party votes are the valid votes; parties without a seat are summed as 'other parties' "
    "(övriga partier). Invalid ballots (ogiltiga valsedlar) include blank ones. Ballots cast are the valid and invalid "
    "ballots. The electorate is the Swedish and the foreign citizens who could vote, and it equals ballots cast plus "
    "non-voters in every municipality. Municipalities carry their present codes. Sweden elects its members in one "
    "national constituency, so each municipality's votes are stored as a unit of the national contest."
)


def ep_read(kind: str, year: int) -> Table:
    table = EP_TABLE[kind]
    d = json.loads(raw.cached(SOURCE, url(table), query(table, year)).path.read_text("utf-8"))
    ids: list[str] = d["id"]
    codes = []
    for name in ids:
        index = d["dimension"][name]["category"]["index"]
        codes.append(list(index) if isinstance(index, list) else sorted(index, key=index.__getitem__))
    wanted = {"EUvalA": "ME0109Z0", "ME0109T01": "ME0109A2", "EUmed": "ME0109B2"}[table]
    found: Table = {}
    for position, combo in enumerate(product(*codes)):
        value = d["value"][position]
        coordinates = dict(zip(ids, combo, strict=True))
        if value is None or coordinates["ContentsCode"] != wanted:
            continue
        other = [c for name, c in coordinates.items() if name not in ("Region", "ContentsCode", "Tid")]
        found.setdefault(coordinates.get("Region", ""), {})[other[0] if other else ""] = int(value)
    return found


def ep_election(year: int) -> Package:
    day = EP_DATES[year]
    votes = ep_read("votes", year)
    electorate = ep_read("electorate", year)
    seats = ep_read("seats", year)[""]
    published = votes["00"]
    towns = sorted(c for c in votes if is_town(c))
    unit_names = names("EUvalA", year)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Sweden's European Parliament election of {day:%-d %B %Y} by municipality from Statistics Sweden",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{val.SOURCE}/geo"],
    )
    p.external |= {SWEDEN, COUNTRY, *SCHEMES}
    for table in EP_TABLES:
        p.uses(raw.cached(SOURCE, url(table), query(table, year)))
        p.uses(raw.cached(SOURCE, url(table)))
    kind, english, swedish, word = val.ELECTIONS["E"]
    election_id = key("election", day, "E")
    p.add(
        "election",
        id=election_id,
        polity_id=SWEDEN,
        kind=kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, english.format(year=year), "en", "official")
    p.name(election_id, swedish.format(year=year), "sv", "native", False)
    p.add("slug", path=f"se/{day}-{word}", entity_id=election_id, is_current=True)
    unit_ids = {t: key("unit", day, t) for t in towns}
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            {
                "id": [unit_ids[t] for t in towns],
                "polity_id": [SWEDEN] * len(towns),
                "scheme_code": [val.MUNICIPALITY] * len(towns),
                "code": [f"{day}:{t}" for t in towns],
                "area_relation": ["equals"] * len(towns),
                "area_id": [area(val.MUNICIPALITY, t) for t in towns],
                "kind": ["standard"] * len(towns),
            }
        ),
    )
    for t in towns:
        p.name(unit_ids[t], unit_names[t], "sv", "official")
    contest_id = key("contest", day, "E", "00")
    p.add(
        "contest",
        id=contest_id,
        election_id=election_id,
        kind="list_allocation",
        body_id=None,
        district_area_id=COUNTRY,
        magnitude=sum(seats.values()),
        round_number=1,
        electoral_system_id=system("ep-1995"),
        default_result_kind="counted",
    )
    p.name(contest_id, "Europaparlamentet", "sv", "official")
    p.add("slug", path=f"se/{day}-{word}/{changeset.slug('Europaparlamentet')}", entity_id=contest_id, is_current=True)
    result_set = key("result_set", day, "E", "00")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest_id,
        stage="official",
        kind="counted",
        source_id=EP_SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=EP_METHOD,
        lang="en",
    )
    options = {code: key("option", day, "E", "00", code) for code in EP_PARTIES if code in published}
    for code, id in options.items():
        p.add(
            "contest_option",
            id=id,
            contest_id=contest_id,
            kind="others" if code == "ÖVRIGA" else "list",
            party_id=None if code == "ÖVRIGA" else party(code),
            ballot_position=None,
            ballot_label=LABELS[code],
        )
        if seats.get(code, 0) > 0:
            p.add("outcome", result_set_id=result_set, option_id=id, kind="won", seats=seats[code])
    unseated = {code for code, n in seats.items() if n and code not in options}
    if unseated:
        raise ValueError(f"{year}: seats for parties without votes: {sorted(unseated)}")
    rows: list[tuple[str, str, str | None, int, int]] = []
    for t in towns:
        row = votes[t]
        valid = sum(row.get(code, 0) for code in EP_PARTIES)
        invalid = row["OGILTIGA"]
        registered = sum(electorate[t].values())
        if valid + invalid + row["VALSKOLKARE"] != registered:
            raise ValueError(f"{year} {t}: valid, invalid, and non-voting do not add up to the electorate")
        rows += [(result_set, unit_ids[t], options[code], VOTES, row[code]) for code in options if code in row]
        rows += [
            (result_set, unit_ids[t], None, REGISTERED, registered),
            (result_set, unit_ids[t], None, CAST, valid + invalid),
            (result_set, unit_ids[t], None, VALID, valid),
            (result_set, unit_ids[t], None, INVALID, invalid),
        ]
    for code, id in options.items():
        p.add("declared", result_set_id=result_set, option_id=id, measure=VOTES, value=published[code])
    p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=published["OGILTIGA"])
    p.add(
        "declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=sum(electorate["00"].values())
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
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0), count_round=pl.lit(0), flags=pl.lit(0)),
    )
    return p


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{DATES[year]}" for year in YEARS),
        *(f"{SOURCE}/{EP_DATES[year]}" for year in EP_YEARS),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()
    for year in EP_YEARS:
        ep_election(year).write()


NATIONAL = {"R": "VR00", "L": "00L", "K": "00"}


def compare() -> dict[str, dict[str, object]]:
    """Each body's national totals as Statistics Sweden prints them, against the sums of the units stored."""
    out: dict[str, dict[str, object]] = {}
    for year in YEARS:
        found, tables = contests(year)
        for body in "RLK":
            votes = tables[VOTES_TABLE[body]]
            published = votes[NATIONAL[body]]
            mine: dict[str, int] = {}
            registered = 0
            seats = 0
            for c in (c for c in found if c.body == body):
                for town in c.towns:
                    for code, n in votes[town].items():
                        mine[code] = mine.get(code, 0) + n
                    registered += sum(tables[ELECTORATE_TABLE[body]][town].values())
                seats += sum(c.seats.values())
            published_seats = sum(tables[SEATS_TABLE[body]].get(NATIONAL[body], {}).values())
            electorate = sum(tables[ELECTORATE_TABLE[body]].get(NATIONAL[body].replace("VR00", "00"), {"": 0}).values())
            differ = {
                k: (mine.get(k, 0), published.get(k, 0))
                for k in {*mine, *published}
                if mine.get(k, 0) != published.get(k, 0)
            }
            out[f"{year}/{body}"] = {
                "votes_differ": differ,
                "valid": sum(v for k, v in mine.items() if k in PARTIES),
                "invalid": mine.get("OGILTIGA", 0),
                "registered": registered,
                "published_registered": electorate,
                "seats": seats,
                "published_seats": published_seats,
            }
    return out
