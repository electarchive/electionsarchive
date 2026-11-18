"""Taiwan's abolished offices and bodies from the Central Election Commission's election database: the Taiwan Provincial
Governor of 1994 (subject P9) and the National Assembly of 1996 (regional and the two indigenous constituencies) and
2005 (the task-based assembly elected from party lists), subject N0. The 1994 Taiwan Provincial Assembly (T9) is built
by `tw.cec_local`. The governor is read by township, the 1996 assembly by township within each electoral area, and the
2005 assembly by polling station. Their areas, parties, and source carry the ids they have in `tw.cec/geo` and
`tw.cec/registry`, so `tw.cec/abolished-geo` and `tw.cec/abolished-registry` add only what those packages lack."""

import functools
import json
import re
from datetime import date
from typing import NamedTuple

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.tw.cec import (
    BASE,
    CEC,
    INDEPENDENT,
    LICENSE,
    LICENSE_URL,
    NATION,
    NATION_NAME,
    PARTIES,
    PARTY_EN,
    PLACES,
    SOURCE,
    TAIWAN,
    area,
    key,
)

LIST_URL = "https://db.cec.gov.tw/static/elections/list/ELC_{}.json"
ROOT = "00_000_00_000_0000"
# subject -> (package stem, election slug, English noun, Chinese noun)
KINDS = {
    "P9": ("provincial-governor", "provincial-governor", "Taiwan Provincial Governor", "臺灣省長選舉"),
    "N0": ("national-assembly", "national-assembly", "National Assembly", "國民大會代表選舉"),
}
GOVERNOR = key("office", "provincial-governor")
ASSEMBLY = key("body", "national-assembly")
GOVERNOR_SYSTEM = key("electoral_system", "provincial-governor", 1994)
DISTRICT_SYSTEM = key("electoral_system", "national-assembly", "sntv", 1996)
LIST_SYSTEM = key("electoral_system", "national-assembly", "list", 2005)
SYSTEM_RANGE = {
    GOVERNOR_SYSTEM: (date(1994, 1, 1), date(1998, 12, 20)),
    DISTRICT_SYSTEM: (date(1991, 1, 1), date(2000, 12, 31)),
    LIST_SYSTEM: (date(2005, 1, 1), date(2005, 12, 31)),
}
METHOD = {
    GOVERNOR_SYSTEM: (
        "Each voter casts one vote for one candidate for governor of Taiwan Province. The candidate with the most "
        "votes in the province wins, with no runoff. Voters in the municipalities of Taipei and Kaohsiung, which "
        "were outside the province, did not vote."
    ),
    DISTRICT_SYSTEM: (
        "Each voter casts one vote for one candidate in the electoral area, and the candidates with the most votes "
        "fill the area's seats, except that where the law reserves seats for women, a woman with fewer votes can take "
        "the seat of a man who placed higher; the Commission marks those women as elected. Plains and mountain "
        "indigenous voters elect three delegates each in a nationwide "
        "constituency of their own. The assembly's at-large seats were assigned to parties by their shares of the "
        "vote in the regional contests and are not an election of their own in the Commission's data."
    ),
    LIST_SYSTEM: (
        "Each voter casts one vote for one party list nationwide. The 300 seats of the task-based assembly were "
        "shared among the lists that won at least 5 percent of the valid votes, in proportion to their votes."
    ),
}
NOTE = (
    "Valid votes for each {what}, and the valid and invalid ballots, ballots cast, and registered voters at each "
    "{unit}, as published by the Central Election Commission. Voters registered is the number of voters on the roll. "
    "Declared totals are the Commission's own."
)
SCHEME = "tw.abolished_district"
MEASURES = ((4, "valid_ticket"), (5, "invalid_ticket"), (3, "vote_ticket"), (2, "votable_population"))
TOWN = ("prv_code", "city_code", "dept_code")
TOWN_AREA = ("prv_code", "city_code", "area_code", "dept_code")
VILLAGE = (*TOWN, "li_code")
STATION = (*VILLAGE, "tbox_no")


class Theme(NamedTuple):
    subject: str
    type_id: str
    theme_id: str
    day: str
    name: str
    desc: str


@functools.cache
def themes() -> list[Theme]:
    found: list[Theme] = []
    for subject in KINDS:
        artifact = raw.fetch(SOURCE, LIST_URL.format(subject), suffix=".json")
        for entry in json.loads(artifact.path.read_text(encoding="utf-8")):
            for item in entry["theme_items"]:
                found.append(
                    Theme(
                        subject,
                        item["legislator_type_id"],
                        item["theme_id"],
                        item["vote_date"],
                        item["theme_name"],
                        str(item["legislator_desc"] or ""),
                    )
                )
    return sorted(found, key=lambda theme: (theme.subject, theme.day, theme.type_id))


def days(subject: str) -> list[str]:
    return sorted({theme.day for theme in themes() if theme.subject == subject})


def group(subject: str, day: str) -> list[Theme]:
    return [theme for theme in themes() if theme.subject == subject and theme.day == day]


def packages() -> list[str]:
    return [
        f"{SOURCE}/abolished-geo",
        f"{SOURCE}/abolished-registry",
        *(f"{SOURCE}/{KINDS[subject][0]}-{day}" for subject in KINDS for day in days(subject)),
    ]


def url(kind: str, theme: Theme, level: str, code: str = ROOT) -> str:
    return f"{BASE}{kind}/ELC/{theme.subject}/{theme.type_id}/{theme.theme_id}/{level}/{code}.json"


def regional(theme: Theme) -> bool:
    return theme.subject == "N0" and theme.type_id == "N1"


def party_list(theme: Theme) -> bool:
    return theme.subject == "N0" and theme.type_id == "00"


def read(source_url: str) -> list[dict[str, object]]:
    data = json.loads(raw.fetch(SOURCE, source_url, suffix=".json").path.read_text(encoding="utf-8"))
    return [row for part in data.values() for row in part]


@functools.cache
def county_codes(theme: Theme) -> dict[str, str]:
    """County code (`prv_city_00_000_0000`) -> county name, from the county-level profiles."""
    if theme.type_id in ("N2", "N3"):
        return {}
    return {
        f"{row['prv_code']}_{row['city_code']}_00_000_0000": county_name(row["area_name"])
        for row in read(url("profiles", theme, "C"))
    }


def county_name(value: object) -> str:
    return fold(value).replace("台", "臺")


def fold(value: object) -> str:
    return re.sub(r"[\s　]+", "", str(value))


def urls(theme: Theme) -> list[str]:
    if theme.type_id in ("N2", "N3"):
        return [url("tickets", theme, "N"), url("profiles", theme, "N")]
    found = [url("profiles", theme, "C")]
    if not regional(theme):
        found += [url("tickets", theme, "N"), url("profiles", theme, "N")]
    for code in county_codes(theme):
        if regional(theme):
            found.extend(url(kind, theme, level, code) for kind in ("tickets", "profiles") for level in ("A", "D"))
        elif party_list(theme):
            found.extend(url(kind, theme, level, code) for kind in ("tickets", "profiles") for level in ("D", "L", "T"))
        else:
            found.extend(url(kind, theme, "D", code) for kind in ("tickets", "profiles"))
    return found


def fetch() -> None:
    raw.fetch_many(SOURCE, [u for theme in themes() for u in urls(theme)], suffix=".json", workers=8)


@functools.cache
def index() -> frozenset[str]:
    return frozenset(raw.keys(SOURCE))


@functools.cache
def rows(theme: Theme, kind: str, level: str) -> list[dict[str, object]]:
    """Every row of a level's files, over the counties the theme reports."""
    found: list[dict[str, object]] = []
    if level in ("N", "C"):
        codes = [ROOT]
    else:
        codes = list(county_codes(theme))
    for code in codes:
        file = url(kind, theme, level, code)
        if file in index():
            data = json.loads(raw.cached(SOURCE, file).path.read_text(encoding="utf-8"))
            for part in data.values():
                found.extend(part)
    return found


def artifacts(theme: Theme) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, u) for u in urls(theme) if u in index()]


def at(row: dict[str, object], fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(row[field]) for field in fields)


def number(row: dict[str, object], field: str) -> int:
    return int(str(row[field]))


class Places(NamedTuple):
    county: dict[tuple[str, ...], str]
    township: dict[tuple[str, ...], str]
    village: dict[tuple[str, ...], str]
    district: dict[tuple[str, ...], str]  # (prv, city, area) -> the area's name


@functools.cache
def places(theme: Theme) -> Places:
    county: dict[tuple[str, ...], str] = {
        (str(row["prv_code"]), str(row["city_code"])): county_name(row["area_name"])
        for row in rows(theme, "profiles", "C")
    }
    township: dict[tuple[str, ...], str] = {}
    for kind in ("tickets", "profiles"):
        for row in rows(theme, kind, "D"):
            township.setdefault(at(row, TOWN_AREA if regional(theme) else TOWN), fold(row["area_name"]))
    village: dict[tuple[str, ...], str] = {}
    for kind in ("tickets", "profiles"):
        for row in rows(theme, kind, "L"):
            village.setdefault(at(row, VILLAGE), fold(row["area_name"]))
    district: dict[tuple[str, ...], str] = {}
    for row in rows(theme, "tickets", "A"):
        district.setdefault(at(row, ("prv_code", "city_code", "area_code")), fold(row["area_name"]))
    return Places(county, township, village, district)


def county_of(theme: Theme, code: tuple[str, ...]) -> str:
    return places(theme).county[(code[0], code[1])]


def district_id(day: str, kind: str, *parts: str) -> str:
    return key("area", SCHEME, "|".join([kind, day, *parts]))


# --- the contests


class Unit(NamedTuple):
    code: str
    area_id: str
    scheme: str
    relation: str
    kind: str
    votes: dict[int, int]
    accounts: dict[str, int] | None


class Contest(NamedTuple):
    tag: str
    district: str
    english: str
    native: str
    slug: str
    cands: list[dict[str, object]]
    totals: dict[str, object] | None
    units: list[Unit]
    seats: int


def accounts_of(row: dict[str, object] | None) -> dict[str, int] | None:
    return None if row is None else {field: number(row, field) for _, field in MEASURES}


def group_rows(
    found: list[dict[str, object]], fields: tuple[str, ...]
) -> dict[tuple[str, ...], list[dict[str, object]]]:
    out: dict[tuple[str, ...], list[dict[str, object]]] = {}
    for row in found:
        out.setdefault(at(row, fields), []).append(row)
    return out


def votes_of(found: list[dict[str, object]]) -> dict[int, int]:
    return {number(row, "cand_no"): number(row, "ticket_num") for row in found}


def contests(subject: str, day: str) -> list[Contest]:
    out: list[Contest] = []
    for theme in group(subject, day):
        p = places(theme)
        if subject == "P9":
            out.append(governor_contest(theme, p))
        elif regional(theme):
            out.extend(regional_contests(theme, p))
        elif party_list(theme):
            out.append(list_contest(theme, p))
        else:
            tickets = sorted(rows(theme, "tickets", "N"), key=lambda row: number(row, "cand_no"))
            totals = rows(theme, "profiles", "N")[0]
            out.append(
                Contest(
                    theme.type_id,
                    NATION,
                    f"{day[:4]} National Assembly election, {INDIGENOUS[theme.type_id][0]}",
                    INDIGENOUS[theme.type_id][1],
                    INDIGENOUS[theme.type_id][2],
                    tickets,
                    totals,
                    [
                        Unit(
                            f"{subject}:{day}:{theme.type_id}",
                            NATION,
                            "tw.nation",
                            "equals",
                            "aggregate",
                            votes_of(tickets),
                            accounts_of(totals),
                        )
                    ],
                    sum(1 for row in tickets if row["is_victor"] in ("*", "!")),
                )
            )
    return out


INDIGENOUS = {
    "N2": ("plains indigenous constituency", "平地原住民", "plains-indigenous"),
    "N3": ("mountain indigenous constituency", "山地原住民", "mountain-indigenous"),
}


def governor_contest(theme: Theme, p: Places) -> Contest:
    tickets = sorted(rows(theme, "tickets", "N"), key=lambda row: number(row, "cand_no"))
    totals = rows(theme, "profiles", "N")[0]
    accounts = {at(row, TOWN): row for row in rows(theme, "profiles", "D")}
    units: list[Unit] = []
    for code, found in sorted(group_rows(rows(theme, "tickets", "D"), TOWN).items()):
        county = county_of(theme, code)
        units.append(
            Unit(
                f"{theme.subject}:{theme.day}:{'_'.join(code)}",
                area("tw.township", county, p.township[code]),
                "tw.township",
                "equals",
                "aggregate",
                votes_of(found),
                accounts_of(accounts.get(code)),
            )
        )
    return Contest(
        "province",
        district_id(theme.day, "province", "臺灣省"),
        f"{theme.day[:4]} Taiwan Provincial Governor election",
        "臺灣省長選舉",
        "",
        tickets,
        totals,
        units,
        1,
    )


def regional_contests(theme: Theme, p: Places) -> list[Contest]:
    out: list[Contest] = []
    area_rows = group_rows(rows(theme, "tickets", "A"), ("prv_code", "city_code", "area_code"))
    totals = {at(row, ("prv_code", "city_code", "area_code")): row for row in rows(theme, "profiles", "A")}
    accounts = {at(row, TOWN_AREA): row for row in rows(theme, "profiles", "D")}
    towns = group_rows(rows(theme, "tickets", "D"), ("prv_code", "city_code", "area_code"))
    for code, found in sorted(area_rows.items()):
        county = p.county[(code[0], code[1])]
        found = sorted(found, key=lambda row: number(row, "cand_no"))
        units: list[Unit] = []
        for town, part in sorted(group_rows(towns.get(code, []), TOWN_AREA).items()):
            units.append(
                Unit(
                    f"{theme.subject}:{theme.day}:{theme.type_id}:{'_'.join(town)}",
                    area("tw.township", county, p.township[town]),
                    "tw.township",
                    "within",
                    "standard",
                    votes_of(part),
                    accounts_of(accounts.get(town)),
                )
            )
        number_ = p.district[code]
        out.append(
            Contest(
                f"{theme.type_id}:{'_'.join(code)}",
                district_id(theme.day, theme.type_id, county, code[2]),
                f"{theme.day[:4]} National Assembly election, {county} {number_}",
                f"{county}{number_}",
                f"n1-{'-'.join(code)}",
                found,
                totals.get(code),
                units,
                sum(1 for row in found if row["is_victor"] in ("*", "!")),
            )
        )
    return out


def list_contest(theme: Theme, p: Places) -> Contest:
    tickets = sorted(rows(theme, "tickets", "N"), key=lambda row: number(row, "cand_no"))
    totals = rows(theme, "profiles", "N")[0]
    accounts = {at(row, STATION): row for row in rows(theme, "profiles", "T")}
    units: list[Unit] = []
    for code, found in sorted(group_rows(rows(theme, "tickets", "T"), STATION).items()):
        county = county_of(theme, code)
        village = area("tw.village", county, p.township[code[:3]], p.village[code[:4]])
        units.append(
            Unit(
                f"{theme.subject}:{theme.day}:{'_'.join(code)}",
                village,
                "tw.station",
                "within",
                "standard",
                votes_of(found),
                accounts_of(accounts.get(code)),
            )
        )
    return Contest(
        "list",
        NATION,
        f"{theme.day[:4]} National Assembly election",
        "任務型國民大會代表選舉",
        "",
        tickets,
        totals,
        units,
        sum(number(row, "elected_num") for row in tickets),
    )


# --- geography


def geography() -> tuple[set[str], set[tuple[str, str]], set[tuple[str, str, str]]]:
    """The counties, townships, and villages these elections report."""
    counties: set[str] = set()
    townships: set[tuple[str, str]] = set()
    villages: set[tuple[str, str, str]] = set()
    for theme in themes():
        if theme.type_id in ("N2", "N3"):
            continue
        p = places(theme)
        counties.update(p.county.values())
        for code, name in p.township.items():
            townships.add((p.county[(code[0], code[1])], name))
        for code, name in p.village.items():
            villages.add((p.county[(code[0], code[1])], p.township[code[:3]], name))
    return counties, townships, villages


def geo() -> Package:
    """The counties, townships, villages, and electoral areas these elections use. Every area has the id it has in
    `tw.cec/geo`, so loading this package adds the areas that package lacks and leaves the others as they are."""
    p = Package(
        f"{SOURCE}/abolished-geo",
        SOURCE,
        "The Taiwanese counties, townships, villages, and electoral areas that the provincial governor and National "
        "Assembly elections report votes for",
    )
    p.external.add(TAIWAN)
    for code, rank, kind, noun in (
        ("tw.nation", 0, "administrative", "country"),
        ("tw.county", 1, "administrative", "county or city"),
        ("tw.township", 2, "administrative", "township or district"),
        ("tw.village", 3, "administrative", "village or neighborhood"),
        ("tw.station", 4, "precinct", "polling station"),
        (SCHEME, 1, "electoral_district", "province or assembly electoral area"),
    ):
        p.add("scheme", code=code, polity_id=TAIWAN, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=NATION, scheme_code="tw.nation")
    p.add("area_code", area_id=NATION, code="TW")
    p.name(NATION, NATION_NAME[0], "en", "official")
    p.name(NATION, NATION_NAME[1], "zh", "native", False)
    counties, townships, villages = geography()
    for county in sorted(counties):
        id = area("tw.county", county)
        p.add("area", id=id, scheme_code="tw.county")
        p.add("area_code", area_id=id, code=county)
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.name(id, county, "zh", "official")
        if county in PLACES:
            p.name(id, PLACES[county], "en", "official", False)
    for county, township in sorted(townships):
        id = area("tw.township", county, township)
        p.add("area", id=id, scheme_code="tw.township")
        p.add("area_code", area_id=id, code=f"{county}|{township}")
        p.add("area_parent", area_id=id, parent_id=area("tw.county", county))
        p.name(id, township, "zh", "official")
    for county, township, village in sorted(villages):
        id = area("tw.village", county, township, village)
        p.add("area", id=id, scheme_code="tw.village")
        p.add("area_code", area_id=id, code=f"{county}|{township}|{village}")
        p.add("area_parent", area_id=id, parent_id=area("tw.township", county, township))
        p.name(id, village, "zh", "official")
    for subject, day in ((s, d) for s in KINDS for d in days(s)):
        for contest in contests(subject, day):
            if contest.district == NATION:
                continue
            if subject == "P9":
                code, parent, name = "臺灣省", NATION, "臺灣省"
            else:
                theme = next(t for t in group(subject, day) if t.type_id == "N1")
                county = places(theme).county[
                    (contest.tag.split(":")[1].split("_")[0], contest.tag.split(":")[1].split("_")[1])
                ]
                code = f"{day}|{contest.native}"
                parent = area("tw.county", county)
                name = f"{contest.native} ({day[:4]})"
            p.add("area", id=contest.district, scheme_code=SCHEME)
            p.add("area_code", area_id=contest.district, code=f"{subject}|{code}")
            p.add("area_parent", area_id=contest.district, parent_id=parent)
            p.name(contest.district, name, "zh", "official")
    return p


def parties() -> set[str]:
    return {
        str(row["party_name"])
        for theme in themes()
        for row in rows(theme, "tickets", "N" if theme.type_id != "N1" else "A")
        if str(row["party_name"]) not in INDEPENDENT
    }


# --- packages


def registry() -> Package:
    """The governor's office, the assembly, their electoral systems, the parties, and the cited files."""
    p = Package(
        f"{SOURCE}/abolished-registry",
        SOURCE,
        "The Taiwan Provincial Governor, the National Assembly, their electoral systems, and the parties that ran "
        "candidates in their elections, from the Central Election Commission",
        depends=[f"{SOURCE}/abolished-geo"],
    )
    p.external.add(TAIWAN)
    p.add(
        "source",
        id=CEC,
        kind="official_body",
        publisher="Central Election Commission",
        title="Election Database (選舉資料庫)",
        url="https://db.cec.gov.tw/",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Central Election Commission, Taiwan (中央選舉委員會)",
    )
    for theme in themes():
        for artifact in artifacts(theme):
            p.artifact(artifact, CEC)
    p.add("office", id=GOVERNOR, polity_id=TAIWAN, kind="governor")
    p.name(GOVERNOR, "Governor of Taiwan Province", "en", "official")
    p.name(GOVERNOR, "臺灣省長", "zh", "native", False)
    p.add("body", id=ASSEMBLY, polity_id=TAIWAN, kind="constituent_assembly")
    p.name(ASSEMBLY, "National Assembly", "en", "official")
    p.name(ASSEMBLY, "國民大會", "zh", "native", False)
    rules: dict[str, dict[str, object]] = {
        GOVERNOR_SYSTEM: {"formula": "fptp"},
        DISTRICT_SYSTEM: {"formula": "sntv"},
        LIST_SYSTEM: {"formula": "hare", "threshold": {"percent": 5, "level": "party", "scope": "national"}},
    }
    for system, (start, end) in SYSTEM_RANGE.items():
        p.add(
            "electoral_system",
            id=system,
            polity_id=TAIWAN,
            valid_from=start,
            valid_to=end,
            rules=rules[system],
            rules_version=1,
        )
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=METHOD[system], lang="en")
    for name in sorted(parties()):
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=TAIWAN)
        p.name(id, name, "zh", "official")
        if name in PARTIES or name in PARTY_EN:
            p.name(id, PARTIES.get(name) or PARTY_EN[name], "en", "official", False)
    return p


def system_of(theme: Theme) -> str:
    return GOVERNOR_SYSTEM if theme.subject == "P9" else LIST_SYSTEM if party_list(theme) else DISTRICT_SYSTEM


def package(subject: str, day: str) -> Package:
    stem, slug, noun, _ = KINDS[subject]
    year = day[:4]
    members = group(subject, day)
    when = date.fromisoformat(day)
    p = Package(
        f"{SOURCE}/{stem}-{day}",
        SOURCE,
        f"The {year} {noun} election{'s' if subject == 'N0' and len(members) > 1 else ''} by "
        f"{'polling station' if subject == 'N0' and party_list(members[0]) else 'township'} from the Central "
        "Election Commission",
        depends=[f"{SOURCE}/abolished-geo", f"{SOURCE}/abolished-registry"],
    )
    p.external.add(TAIWAN)
    for theme in members:
        for artifact in artifacts(theme):
            p.uses(artifact)
    election = key("election", f"{subject}-{day}")
    path = f"tw/{day}-{slug}"
    p.add(
        "election",
        id=election,
        polity_id=TAIWAN,
        kind="local" if subject == "P9" else "general" if year == "1996" else "special",
        date_from=when,
        date_to=when,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} {noun} election", "en", "official")
    p.name(election, members[0].name, "zh", "native", False)
    unit_rows: dict[str, dict[str, object]] = {}
    values: list[tuple[str, str, str | None, int, int]] = []
    differences: list[str] = []
    unaccounted: list[str] = []
    for contest in contests(subject, day):
        theme = next(t for t in members if contest.tag.split(":")[0] in (t.type_id, "province", "list"))
        listed = party_list(theme)
        tag = contest.tag
        contest_id = key("contest", f"{subject}-{day}", tag)
        result_set = key("result_set", f"{subject}-{day}", tag)
        row: dict[str, object] = {
            "id": contest_id,
            "election_id": election,
            "kind": "office" if subject == "P9" else "list_allocation" if listed else "body_seats",
            "district_area_id": contest.district,
            "magnitude": contest.seats,
            "round_number": 1,
            "electoral_system_id": system_of(theme),
            "default_result_kind": "counted",
        }
        row["office_id" if subject == "P9" else "body_id"] = GOVERNOR if subject == "P9" else ASSEMBLY
        p.add("contest", **row)
        p.name(contest_id, contest.english, "en", "official")
        p.name(contest_id, contest.native if subject != "P9" else f"{members[0].name}", "zh", "native", False)
        tail = contest.slug or ("list" if listed else "province" if subject == "P9" else INDIGENOUS[theme.type_id][2])
        p.add("slug", path=f"{path}/{tail}", entity_id=contest_id, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=CEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        unit_word = "polling station" if listed else "township"
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=NOTE.format(what="party list" if listed else "candidate", unit=unit_word),
            lang="en",
        )
        option_of: dict[int, str] = {}
        for cand in contest.cands:
            no = number(cand, "cand_no")
            party_name = str(cand["party_name"])
            independent = party_name in INDEPENDENT
            party_id = None if independent else key("party", party_name)
            option = key("option", f"{subject}-{day}", tag, no)
            option_of[no] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="list" if listed else "candidate",
                party_id=party_id,
                ballot_position=no,
                ballot_label=party_name if listed else str(cand["cand_name"]),
            )
            if listed:
                if number(cand, "elected_num"):
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="won",
                        seats=number(cand, "elected_num"),
                    )
            else:
                person = key("person", f"{subject}-{day}", tag, no)
                birth = str(cand.get("cand_birthyear") or "").strip()
                p.add(
                    "person",
                    id=person,
                    birth_date=date(int(birth), 1, 1) if birth.isdigit() else None,
                    birth_precision="year" if birth.isdigit() else None,
                    gender={"1": "male", "2": "female"}.get(str(cand.get("cand_sex") or "")),
                )
                p.name(person, str(cand["cand_name"]), "zh", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", f"{subject}-{day}", tag, no),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=str(cand["cand_name"]),
                    member_of_party_id=party_id,
                    is_independent=independent,
                    is_incumbent=True if cand.get("is_current") == "Y" else None,
                )
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected" if cand["is_victor"] in ("*", "!") else "lost",
                )
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=number(cand, "ticket_num"))
        if contest.totals:
            for measure, field in MEASURES:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=None,
                    measure=measure,
                    value=number(contest.totals, field),
                )
        sums: dict[int, int] = {}
        ballots = {measure: 0 for measure, _ in MEASURES}
        have = 0
        for unit in contest.units:
            unit_id = key("unit", unit.code)
            unit_rows.setdefault(
                unit_id,
                {
                    "id": unit_id,
                    "polity_id": TAIWAN,
                    "scheme_code": unit.scheme,
                    "code": unit.code,
                    "area_relation": unit.relation,
                    "area_id": unit.area_id,
                    "kind": unit.kind,
                },
            )
            for no, count in unit.votes.items():
                sums[no] = sums.get(no, 0) + count
                values.append((result_set, unit_id, option_of[no], 1, count))
            if unit.accounts:
                have += 1
                for measure, field in MEASURES:
                    ballots[measure] += unit.accounts[field]
                    values.append((result_set, unit_id, None, measure, unit.accounts[field]))
        if contest.totals and have != len(contest.units):
            unaccounted.append(contest.english)
        for cand in contest.cands:
            no = number(cand, "cand_no")
            if sums.get(no, 0) != number(cand, "ticket_num"):
                who = cand["party_name"] if listed else cand["cand_name"]
                differences.append(
                    f"{contest.english}: the {unit_word} figures for {who} add up to {sums.get(no, 0)}, and the "
                    f"Commission's total is {cand['ticket_num']}"
                )
        if contest.totals and have == len(contest.units):
            for measure, field in MEASURES:
                if ballots[measure] != number(contest.totals, field):
                    differences.append(
                        f"{contest.english}: the {unit_word} figures add up to {ballots[measure]} for {field}, and "
                        f"the Commission's total is {contest.totals[field]}"
                    )
    if unaccounted:
        p.accept(
            "accounting.declared",
            "The Commission publishes ballots cast, invalid ballots, and registered voters only for some of these "
            "units: " + "; ".join(unaccounted[:20]),
        )
    if differences:
        p.accept(
            "accounting.declared",
            "The Commission's published totals differ from its own unit-level figures. " + "; ".join(differences[:20]),
        )
        p.add(
            "note",
            id=key("note", "differences", f"{subject}-{day}"),
            entity_id=election,
            kind="caveat",
            body_md="The Commission's published totals differ from its own unit-level figures in this election: "
            + "; ".join(differences)
            + ". The unit-level figures are stored as published.",
            lang="en",
        )
    p.frame("reporting_unit", pl.DataFrame(list(unit_rows.values())))
    facts = pl.DataFrame(
        values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
    ).filter(pl.col("value") >= 0)
    p.frame(
        "result_value",
        facts.select(
            result_set_id="result_set_id",
            unit_id="unit_id",
            option_id="option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for subject in KINDS:
        for day in days(subject):
            package(subject, day).write()
