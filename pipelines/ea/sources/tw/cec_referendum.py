"""Taiwan's national referendums (公民投票) and constitutional amendment referendum, and the one local referendum, from
the Central Election Commission's election database. The Commission's referendum subject lists each case with its
theme id; the results sit in profiles and tickets files at nation, county, township, village, and polling station
level. The cases of 2004 stop at the township, and those of 2008 have polling stations but no village files. One
package per voting day holds every case voted that day. The packages share `tw.cec/geo` and `tw.cec/registry`, which
call `extend_geo` and `registry` here."""

import functools
import json
from datetime import date
from typing import NamedTuple

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.tw.cec import CEC, SOURCE, TAIWAN, area, key
from ea.sources.tw.cec_township import STATION, TOWN, VILLAGE, at, fold

ROOT = "https://db.cec.gov.tw/static/referendums/"
LISTS = ("N", "L")
SYSTEM = key("electoral_system", "referendum")
SYSTEM_START = date(2003, 12, 31)
METHOD = (
    "Each voter on the roll receives a ballot with the question and marks agree (同意) or disagree (不同意). A "
    "national referendum is valid when at least a quarter of the voters on the roll cast an agree or disagree "
    "vote, since 2019 counted among the voters on the roll of the whole country, and it passes when the agree "
    "votes are more than half of the valid votes and at least a quarter of the voters on the roll (a fifth of "
    "them, and a majority of the valid votes, for the cases before the 2019 amendment of the Referendum Act). "
    "The referendum on the constitutional amendment of 2022 passes when the agree votes are more than half of "
    "the voters on the roll."
)
NOTE = (
    "Agree and disagree votes, valid and invalid ballots, ballots cast, and registered voters at each {unit}, as "
    "published by the Central Election Commission. Voters registered is the number on the roll. National totals "
    "are the Commission's own."
)
MEASURES = ((2, "votable_population"), (3, "vote_ticket"), (4, "valid_ticket"), (5, "invalid_ticket"))
ANSWERS = (("agree_ticket", "Agree", "同意"), ("disagree_ticket", "Disagree", "不同意"))


class Case(NamedTuple):
    scope: str
    type: str
    theme: str
    number: int
    day: str
    topic: str
    levels: tuple[str, ...]
    result: str
    county: tuple[str, str, str] | None


def url(kind: str, case: Case, level: str, code: str = "00_000_00_000_0000") -> str:
    return f"{ROOT}data/{kind}/{case.type}/{case.theme}/{level}/{code}.json"


def list_url(scope: str) -> str:
    return f"{ROOT}list/{scope}.json"


@functools.cache
def cases() -> list[Case]:
    found: list[Case] = []
    for scope in LISTS:
        data = json.loads(raw.fetch(SOURCE, list_url(scope), suffix=".json").path.read_text(encoding="utf-8"))
        for entry in data:
            for item in entry["theme_items"]:
                if not item["has_data"]:
                    raise ValueError(f"{item['theme_id']}: the Commission lists no data")
                local = scope == "L"
                found.append(
                    Case(
                        scope,
                        item["case_type"],
                        item["theme_id"],
                        int(item["case_no"]),
                        item["vote_date"],
                        str(item["topic"]).strip(),
                        tuple(item["data_prof_seq"]),
                        item["vote_result"],
                        (item["prv_code"], item["city_code"], fold(entry["area_name"])) if local else None,
                    )
                )
    return sorted(found, key=lambda c: (c.day, c.scope, c.number))


def top(case: Case) -> str:
    return "C" if case.scope == "L" else "N"


def counties(case: Case) -> list[str]:
    """The county codes a case reports, read from its county-level profiles."""
    data = json.loads(raw.fetch(SOURCE, url("profiles", case, "C"), suffix=".json").path.read_text(encoding="utf-8"))
    return sorted({f"{row['prv_code']}_{row['city_code']}_00_000_0000" for row in next(iter(data.values()))})


def urls(case: Case) -> list[str]:
    out = [url(kind, case, level) for kind in ("profiles", "tickets") for level in ("N", "C") if level in case.levels]
    for code in counties(case):
        for kind in ("profiles", "tickets"):
            out.extend(url(kind, case, level, code) for level in ("D", "L", "T") if level in case.levels)
    return list(dict.fromkeys(out))


def fetch() -> None:
    for scope in LISTS:
        raw.fetch(SOURCE, list_url(scope), suffix=".json")
    for case in cases():
        missing = raw.fetch_many(SOURCE, urls(case), suffix=".json", workers=8)
        if len(missing) != len(urls(case)):
            raise ValueError(f"{case.day} case {case.number}: some files did not download")


# --- reading the files

SHORT = {
    ("N", 1): "strengthening national defense against the missile threat",
    ("N", 2): "cross-strait peace and stability negotiations",
    ("N", 3): "the treatment of the assets of political parties",
    ("N", 4): "holding state leaders liable for misuse of special funds",
    ("N", 5): "applying to rejoin the United Nations under the name Taiwan",
    ("N", 6): "applying to rejoin the United Nations under a pragmatic name",
    ("N", 7): "lowering thermal power output by one percent a year",
    ("N", 8): "halting new coal-fired power plants and expansions",
    ("N", 9): "keeping the ban on food from the Fukushima region",
    ("N", 10): "limiting marriage in the Civil Code to a man and a woman",
    ("N", 11): "keeping same-sex education out of primary and junior high schools",
    ("N", 12): "protecting same-sex couples outside the Civil Code's marriage chapter",
    ("N", 13): "joining international sports events as Taiwan",
    ("N", 14): "same-sex marriage under the Civil Code",
    ("N", 15): "gender equality education in the Gender Equity Education Act",
    ("N", 16): "repealing the nuclear-free homeland clause of the Electricity Act",
    ("N", 17): "restarting the Fourth Nuclear Power Plant",
    ("N", 18): "banning pork containing ractopamine",
    ("N", 19): "holding referendums on the day of national elections",
    ("N", 20): "relocating the third LNG terminal away from the Datan algal reef",
    ("N", 21): "continued operation of the Maanshan Nuclear Power Plant",
    ("A", 1): "lowering the voting age to 18 by constitutional amendment",
    ("L", 1): "a Hsinchu City ordinance on wastewater management",
}


def english(case: Case) -> str:
    return SHORT[(case.type, case.number)]


@functools.cache
def index() -> frozenset[str]:
    return frozenset(raw.keys(SOURCE))


@functools.cache
def rows(case: Case, kind: str, level: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    if level in ("N", "C"):
        data = json.loads(raw.cached(SOURCE, url(kind, case, level)).path.read_text(encoding="utf-8"))
        return [row for part in data.values() for row in part]
    for county in counties(case):
        address = url(kind, case, level, county)
        if address in index():
            data = json.loads(raw.cached(SOURCE, address).path.read_text(encoding="utf-8"))
            out.extend(row for part in data.values() for row in part)
    return out


def artifacts(case: Case) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, address) for address in urls(case) if address in index()]


class Places(NamedTuple):
    county: dict[tuple[str, ...], str]
    township: dict[tuple[str, ...], str]
    village: dict[tuple[str, ...], str]


@functools.cache
def places(case: Case) -> Places:
    county: dict[tuple[str, ...], str] = {
        (str(r["prv_code"]), str(r["city_code"])): fold(r["area_name"]) for r in rows(case, "profiles", "C")
    }
    township = {at(r, TOWN): fold(r["area_name"]) for r in rows(case, "profiles", "D")}
    village = {at(r, VILLAGE): fold(r["area_name"]) for r in rows(case, "profiles", "L")}
    return Places(county, township, village)


def geo_sets() -> tuple[set[str], set[tuple[str, str]], set[tuple[str, str, str]]]:
    counties_: set[str] = set()
    townships: set[tuple[str, str]] = set()
    villages: set[tuple[str, str, str]] = set()
    for case in cases():
        place = places(case)
        for code, name in place.township.items():
            counties_.add(place.county[(code[0], code[1])])
            townships.add((place.county[(code[0], code[1])], name))
        for code, name in place.village.items():
            c, t = place.county[(code[0], code[1])], place.township[code[:3]]
            villages.add((c, t, name))
    return counties_, townships, villages


def extend_geo(counties_: set[str], townships: set[tuple[str, str]], villages: set[tuple[str, str, str]]) -> None:
    more = geo_sets()
    counties_ |= more[0]
    townships |= more[1]
    villages |= more[2]


def packages() -> list[str]:
    return [f"tw.cec/referendum-{day}" for day in sorted({case.day for case in cases()})]


def label(day: str) -> str:
    return date.fromisoformat(day).strftime("%-d %B %Y")


# --- the packages


def registry(p: Package) -> None:
    """Add the referendum electoral system and the cited files to the registry package."""
    cited: dict[str, raw.Artifact] = {}
    for case in cases():
        for artifact in [*artifacts(case), raw.cached(SOURCE, list_url(case.scope))]:
            cited.setdefault(artifact.storage_key, artifact)
    for artifact in cited.values():
        p.artifact(artifact, CEC)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=TAIWAN,
        valid_from=SYSTEM_START,
        valid_to=None,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.name(SYSTEM, "Taiwanese referendum", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD, lang="en")


def total(row: dict[str, object], field: str) -> int:
    return int(str(row[field]))


def election(day: str) -> Package:
    year = day[:4]
    when = date.fromisoformat(day)
    todays = [case for case in cases() if case.day == day]
    p = Package(
        f"tw.cec/referendum-{day}",
        SOURCE,
        f"The referendums Taiwan voted on {label(day)}, by polling station, village, or township from the Central "
        "Election Commission",
        depends=["tw.cec/geo", "tw.cec/registry"],
    )
    p.external.add(TAIWAN)
    for case in todays:
        for artifact in artifacts(case):
            p.uses(artifact)
    unit_rows: dict[str, dict[str, object]] = {}
    values: list[tuple[str, str, str | None, int, int]] = []
    differences: list[str] = []
    unplaced: list[str] = []
    for scope in sorted({case.scope for case in todays}, reverse=True):
        group = [case for case in todays if case.scope == scope]
        local = scope == "L"
        elect = key("election", f"RF{scope}-{day}")
        path = f"tw/{day}-{'local-' if local else ''}referendum"
        amendment = {case.type for case in group} == {"A"}
        p.add("election", id=elect, polity_id=TAIWAN, kind="referendum", date_from=when, date_to=when, status="held")
        p.add("slug", path=path, entity_id=elect, is_current=True)
        if local:
            title = f"{label(day)} Hsinchu City referendum"
            native = "新竹市公民投票"
        elif amendment:
            title = f"{year} Taiwanese constitutional amendment referendum"
            native = "憲法修正案複決"
        else:
            title = f"{label(day)} Taiwanese national referendum{'s' if len(group) > 1 else ''}"
            native = "全國性公民投票"
        p.name(elect, title, "en", "official")
        p.name(elect, native, "zh", "native", False)
        for case in group:
            tag = f"{case.type}{case.number}"
            place = places(case)
            contest = key("contest", f"RF-{day}", tag)
            result_set = key("result_set", f"RF-{day}", tag)
            if case.county:
                district = area("tw.county", case.county[2])
            else:
                district = key("area", "tw.nation", "TW")
            p.add(
                "contest",
                id=contest,
                election_id=elect,
                kind="ballot_question",
                district_area_id=district,
                round_number=1,
                electoral_system_id=SYSTEM,
                default_result_kind="counted",
            )
            p.add("ballot_question", contest_id=contest, type="referendum")
            name = f"{year} referendum on {english(case)}"
            p.name(contest, name[0].upper() + name[1:], "en", "official")
            p.name(contest, case.topic, "zh", "native", False)
            slug = f"{'amendment' if case.type == 'A' else 'case'}-{case.number}"
            p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
            options = []
            for position, (_, label_en, label_zh) in enumerate(ANSWERS, 1):
                option = key("option", f"RF-{day}", tag, position)
                options.append(option)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label_zh,
                )
                p.name(option, label_en, "en", "english")
                p.name(option, label_zh, "zh", "native", False)
            p.add(
                "result_set",
                id=result_set,
                election_id=elect,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=CEC,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            finest = "T" if "T" in case.levels else "L" if "L" in case.levels else "D"
            unit_name = {"T": "polling station", "L": "village", "D": "township"}[finest]
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=NOTE.format(unit=unit_name),
                lang="en",
            )
            top_row = rows(case, "profiles", top(case))[0]
            ticket = rows(case, "tickets", top(case))[0]
            passed = ticket["vote_result"] == "Y"
            if case.result != ticket["vote_result"]:
                raise ValueError(f"{day} {tag}: the list and the ticket file disagree on the result")
            agree_votes, disagree_votes = total(top_row, "agree_ticket"), total(top_row, "disagree_ticket")
            p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won" if passed else "lost")
            p.add("outcome", result_set_id=result_set, option_id=options[1], kind="lost" if passed else "won")
            for option, field in zip(options, ("agree_ticket", "disagree_ticket"), strict=True):
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=total(top_row, field))
            for measure, field in MEASURES:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=None,
                    measure=measure,
                    value=total(top_row, field),
                )
            sums = {"agree_ticket": 0, "disagree_ticket": 0, **{field: 0 for _, field in MEASURES}}
            unit_scheme = "tw.station" if finest == "T" else "tw.village" if finest == "L" else "tw.township"
            for r in rows(case, "profiles", finest):
                code = at(r, STATION if finest == "T" else VILLAGE if finest == "L" else TOWN)
                county = place.county[(code[0], code[1])]
                township = place.township[code[:3]]
                village_code = code[:4]
                if finest == "L" or (finest == "T" and village_code[3] != "0000" and village_code in place.village):
                    where = area("tw.village", county, township, place.village[village_code])
                else:
                    where = area("tw.township", county, township)
                    if finest == "T" and village_code[3] != "0000":
                        unplaced.append(f"{county}{township} village {village_code[3]} station {code[4]}")
                ucode = f"RF:{scope}:{day}:{'_'.join(code)}"
                unit = key("unit", ucode)
                unit_rows.setdefault(
                    unit,
                    {
                        "id": unit,
                        "polity_id": TAIWAN,
                        "scheme_code": unit_scheme,
                        "code": ucode,
                        "area_relation": "equals" if finest != "T" else "within",
                        "area_id": where,
                        "kind": "aggregate" if finest != "T" else "standard",
                    },
                )
                for option, field in zip(options, ("agree_ticket", "disagree_ticket"), strict=True):
                    values.append((result_set, unit, option, 1, total(r, field)))
                    sums[field] += total(r, field)
                for measure, field in MEASURES:
                    values.append((result_set, unit, None, measure, total(r, field)))
                    sums[field] += total(r, field)
            for field, added in sums.items():
                if added != total(top_row, field):
                    differences.append(
                        f"case {tag} ({day}): the {unit_name} figures add up to {added:,} for {field}, and "
                        f"the Commission's national total is {total(top_row, field):,}"
                    )
            print(
                f"{day} {tag}: agree {sums['agree_ticket']:,}/{agree_votes:,} disagree "
                f"{sums['disagree_ticket']:,}/{disagree_votes:,} valid {sums['valid_ticket']:,}/"
                f"{total(top_row, 'valid_ticket'):,} registered {sums['votable_population']:,}/"
                f"{total(top_row, 'votable_population'):,}"
            )
    if unplaced:
        p.add(
            "note",
            id=key("note", "unplaced", f"RF-{day}"),
            entity_id=key("election", f"RF{todays[0].scope}-{day}"),
            kind="caveat",
            body_md="The Commission's village file does not name the village of these polling stations, so each is "
            "placed in its township: " + "; ".join(unplaced) + ".",
            lang="en",
        )
    if differences:
        p.accept(
            "accounting.declared",
            "The Commission's totals differ from its own unit figures. " + "; ".join(differences[:20]),
        )
        p.add(
            "note",
            id=key("note", "differences", f"RF-{day}"),
            entity_id=key("election", f"RF{todays[0].scope}-{day}"),
            kind="caveat",
            body_md="The Commission's published totals differ from its own unit figures here: "
            + "; ".join(differences)
            + ". The unit figures are stored as published.",
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
    for day in sorted({case.day for case in cases()}):
        election(day).write()
