"""Czech elections to the European Parliament (2004-2024) by polling district from volby.cz.

Each election date is one package, `cz.volby/ep-<year>`, built on `cz.volby/geo` and `cz.volby/registry`. Czechia
votes as one nationwide constituency; the contest holds the Czech seats in the European Parliament. Units, parties,
and areas reuse the keys of `ea.sources.cz.volby`. A party the registry does not hold lives in the first package whose
lists or candidates name it, and each package depends on the ones before it.
"""

import re
from collections import Counter
from datetime import date
from xml.etree import ElementTree

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.cz.volby import (
    BASE,
    CZECHIA,
    NO_PARTY,
    SOURCE,
    TERRITORY,
    VOLBY_SOURCE,
    _accounting,
    _nuts,
    _overcount,
    _parties,
    _turnout,
    _units,
    _utf8,
    clean,
    key,
    party,
    person_name,
    table,
    url,
)

EUROPEAN = {
    2004: ("ep2004", "EP2004_data_20230223_csv.zip", "EP2004_reg_20230223_csv.zip", "EP2004_cisel_20230223_csv.zip"),
    2009: ("ep2009", "EP2009_data_20230223_csv.zip", "EP2009_reg_20230223_csv.zip", "EP2009_cisel_20230223_csv.zip"),
    2014: ("ep2014", "EP2014_data_20230223_csv.zip", "EP2014_reg_20230223_csv.zip", "EP2014_cisel_20230223_csv.zip"),
    2019: ("ep2019", "EP2019data20190526_csv.zip", "EP2019reg20190526_csv.zip", "EP2019ciselniky20190513_csv.zip"),
    2024: ("ep2024", "EP2024data20240609_csv.zip", "EP2024reg20240609_csv.zip", "EP2024ciselniky20240609_csv.zip"),
}
DAYS = {
    2004: (date(2004, 6, 11), date(2004, 6, 12)),
    2009: (date(2009, 6, 5), date(2009, 6, 6)),
    2014: (date(2014, 5, 23), date(2014, 5, 24)),
    2019: (date(2019, 5, 24), date(2019, 5, 25)),
    2024: (date(2024, 6, 7), date(2024, 6, 8)),
}
DECLARED = {year: f"{BASE}/pls/ep{year}/vysledky" for year in EUROPEAN}
VOTES, PREFERENCE = 1, 7
FIRST = min(EUROPEAN)
METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as published in the volby.cz open data. "
    "Registered voters are the electoral roll, voters are the official envelopes issued, ballots cast are the "
    "envelopes put in the box, and valid votes are the valid list votes. Each candidate's preference votes per polling "
    "district are stored on a candidate line under the list; zero counts are not stored. Polling districts in the "
    "statutory cities are placed in the whole municipality, not in its city district. Polling districts abroad are "
    "placed outside any area. Seats and elected members are as declared in the candidate register."
)
SYSTEM_METHOD = (
    "Czechia elects its members of the European Parliament for five years from party lists in one nationwide "
    "constituency. Lists with at least 5% of the valid votes share the seats by D'Hondt divisors. Voters may give "
    "preference votes to up to two candidates on the list they vote for, and a candidate with at least 5% of the "
    "list's votes moves to the top of the list. The Czech delegation held 24 seats in 2004, 22 from 2009, 21 from "
    "2014, and 21 from 2024 after the redistribution that followed Brexit."
)
RULES: dict[str, object] = {
    "formula": "dhondt",
    "threshold": {"level": "list", "scope": "national", "percent": 5},
    "lists": {"order": "flexible", "vacancy": "next_on_list"},
}
BODY = key("body", "ep")
SYSTEM = key("electoral_system", "ep", FIRST)


def fetch() -> None:
    for folder, *names in EUROPEAN.values():
        for name in names:
            raw.fetch(SOURCE, url(folder, name), suffix=".zip")
    for link in DECLARED.values():
        try:
            raw.fetch(SOURCE, link, suffix=".xml")
        except httpx.HTTPStatusError as failure:
            print(f"{link}: {failure.response.status_code}", flush=True)


def packages() -> list[str]:
    return [f"cz.volby/ep-{year}" for year in EUROPEAN]


def parse() -> None:
    for year in EUROPEAN:
        european(year).write()


def emit() -> None:
    package.run("load", packages())


def _lists(year: int) -> pl.DataFrame:
    folder, _, reg, _ = EUROPEAN[year]
    return table(folder, reg, "eprkl").filter(pl.col("STAVREG") == "0")


def _codes(year: int) -> dict[str, dict[str, str | None]]:
    """The party codes a year's registered lists and candidates name, with their cvs rows."""
    folder, _, reg, cis = EUROPEAN[year]
    lists = _lists(year)
    candidates = table(folder, reg, "eprk").join(lists.select("ESTRANA"), on="ESTRANA")
    used = set(lists["VSTRANA"]) | set(candidates["PSTRANA"]) | set(candidates["NSTRANA"])
    named = {row["VSTRANA"]: row for row in table(folder, cis, "cvs").iter_rows(named=True)}
    return {
        code: named[code] for code in used - {None, "", *NO_PARTY} if code in named and named[code].get("TYPVS") != "N"
    }


def _owned(year: int, registry: set[str]) -> dict[str, dict[str, str | None]]:
    """The parties this year's package defines: named this year, and neither in the registry nor in an earlier year."""
    earlier = {code for before in EUROPEAN if before < year for code in _codes(before)}
    return {code: row for code, row in _codes(year).items() if party(code) not in registry and code not in earlier}


def _declared(year: int) -> ElementTree.Element:
    data = raw.cached(SOURCE, DECLARED[year]).path.read_bytes()
    data = re.sub(rb'encoding="[^"]+"', b'encoding="utf-8"', data, count=1)
    if not _utf8(data):
        data = data.decode("cp1250").encode()
    root = ElementTree.fromstring(data)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    national = next(root.iter("CR"), None)
    if national is None:
        raise LookupError(f"ep {year}: the results file has no national block")
    return national


def european(year: int) -> Package:
    folder, data_zip, reg, cis = EUROPEAN[year]
    first, last = DAYS[year]
    earlier = [f"cz.volby/ep-{before}" for before in EUROPEAN if before < year]
    p = Package(
        f"cz.volby/ep-{year}",
        SOURCE,
        f"The {year} Czech elections to the European Parliament, with preference votes, by polling district from "
        "volby.cz",
        depends=["cz.volby/geo", "cz.volby/registry", *earlier],
    )
    p.external |= {CZECHIA, TERRITORY}
    for name in (data_zip, reg, cis):
        p.artifact(raw.cached(SOURCE, url(folder, name)), VOLBY_SOURCE)
    p.artifact(raw.cached(SOURCE, DECLARED[year]), VOLBY_SOURCE)

    coco = table(folder, cis, "epcoco")
    nuts = _nuts(folder, cis)
    if year == FIRST:
        p.add(
            "electoral_system",
            id=SYSTEM,
            polity_id=CZECHIA,
            valid_from=date(2004, 1, 1),
            valid_to=None,
            rules=RULES,
            rules_version=1,
        )
        p.add("note", id=p.id("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_METHOD, lang="en")
        p.add("body", id=BODY, polity_id=CZECHIA, kind="unicameral")
        p.name(BODY, "European Parliament (Czech seats)", "en", "official")
        p.name(BODY, "Evropský parlament (české mandáty)", "cs", "native", False)

    registry = _parties()
    for code, row in sorted(_owned(year, registry).items(), key=lambda item: int(item[0])):
        id = party(code)
        coalition = row.get("TYPVS") == "K" or (row.get("TYPVS") is None and int(row.get("POCSTR_SLO") or 1) > 1)
        p.add("party", id=id, kind="bloc" if coalition else "party", polity_id=CZECHIA)
        p.add("external_id", entity_id=id, scheme="cz.volby.party", value=code)
        p.name(id, clean(row["NAZEVCELK"]), "cs", "official")
        if row.get("ZKRATKAV30"):
            p.name(id, clean(row["ZKRATKAV30"]), "cs", "short", False)
        if row.get("ZKRATKAV8"):
            p.name(id, clean(row["ZKRATKAV8"]), "cs", "abbreviation", False)
    known = registry | {party(code) for before in EUROPEAN if before <= year for code in _codes(before)}

    def party_of(code: str | None) -> str | None:
        return party(code) if code is not None and party(code) in known else None

    election = p.id("election", "ep", year)
    p.add("election", id=election, polity_id=CZECHIA, kind="general", date_from=first, date_to=last, status="held")
    p.name(election, f"{year} Czech European Parliament election", "en", "official")
    p.name(election, f"Volby do Evropského parlamentu {year}", "cs", "native", False)
    path = f"cz/{first}-european"
    p.add("slug", path=path, entity_id=election, is_current=True)

    accounting = table(folder, data_zip, "ept2")
    votes = table(folder, data_zip, "ept2p")
    units = _units(p, first, nuts, coco, accounting)
    lists = _lists(year)
    candidates = table(folder, reg, "eprk")
    declared = _declared(year)
    elected = candidates.filter(pl.col("MANDAT") == "A")
    seats = sum(int(side.attrib.get("MANDATY") or 0) for side in declared.iter("MANDATY_STRANA"))
    if elected.height != seats:
        raise ValueError(f"ep {year}: {elected.height} elected in the register, {seats} seats declared")
    contest = p.id("contest", "ep", year)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=BODY,
        district_area_id=TERRITORY,
        magnitude=seats,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    title = "European Parliament"
    p.name(contest, title, "en", "official")
    p.name(contest, "Evropský parlament", "cs", "native", False)
    p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
    result_set = p.id("result_set", "ep", year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=VOLBY_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    lines: dict[tuple[str, str], str] = {}
    for entry in lists.sort(pl.col("ESTRANA").cast(pl.Int32)).iter_rows(named=True):
        option = options[entry["ESTRANA"]] = p.id("option", "ep", year, entry["ESTRANA"])
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party_of(entry["VSTRANA"]),
            ballot_position=int(entry["ESTRANA"]),
            ballot_label=clean(entry["NAZEVCELK"]),
        )
    won = Counter(elected["ESTRANA"])
    for list_number, n in sorted(won.items(), key=lambda item: int(item[0])):
        p.add("outcome", result_set_id=result_set, option_id=options[list_number], kind="won", seats=n)
    struck = 0
    for entry in candidates.sort(pl.col("ESTRANA").cast(pl.Int32), pl.col("PORCISLO").cast(pl.Int32)).iter_rows(
        named=True
    ):
        option = options.get(entry["ESTRANA"])
        if option is None:
            continue
        if entry["PLATNOST"] != "A":
            if entry["MANDAT"] == "A":
                raise ValueError(f"ep {year}: struck candidate {person_name(entry)} elected")
            struck += 1
            continue
        tag = f"ep:{year}:{entry['ESTRANA']}:{entry['PORCISLO']}"
        person = p.id("person", tag)
        p.add("person", id=person)
        p.name(person, clean(f"{entry['JMENO']} {entry['PRIJMENI']}"), "cs", "official")
        member = entry["PSTRANA"]
        candidacy = p.id("candidacy", tag)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=int(entry["PORCISLO"]),
            ballot_name=person_name(entry),
            member_of_party_id=party_of(member),
            is_independent=member in NO_PARTY,
        )
        line = lines[(entry["ESTRANA"], entry["PORCISLO"])] = p.id("option", tag)
        p.add(
            "contest_option",
            id=line,
            contest_id=contest,
            kind="candidate",
            parent_option_id=option,
            ballot_position=int(entry["PORCISLO"]),
            ballot_label=person_name(entry),
        )
        if int(entry["POCHLASU"] or 0):
            p.add(
                "declared", result_set_id=result_set, option_id=line, measure=PREFERENCE, value=int(entry["POCHLASU"])
            )
        if entry["MANDAT"] == "A":
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")

    for measure, value in _turnout(next(declared.iter("UCAST"))).items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for side in declared.iter("STRANA"):
        number = side.attrib["ESTRANA"]
        figures = next(side.iter("HLASY_STRANA"))
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=options[number],
            measure=VOTES,
            value=int(figures.attrib["HLASY"]),
        )
        mandates = next(side.iter("MANDATY_STRANA"), None)
        if int(mandates.attrib["MANDATY"] if mandates is not None else 0) != won.get(number, 0):
            raise ValueError(f"ep {year} list {number}: results and register disagree on seats")

    pairs = [(result_set, row) for row in accounting.iter_rows(named=True)]
    for _, row in pairs:
        _accounting(p, result_set, units[row["ID_OKRSKY"]], row)
    _overcount(p, pairs)
    marks = [column for column in votes.columns if column.startswith("HLASY_")]
    for row in votes.iter_rows(named=True):
        unit = units[row["ID_OKRSKY"]]
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=options[row["ESTRANA"]],
            channel=0,
            measure=VOTES,
            count_round=0,
            flags=0,
            value=int(row["POC_HLASU"]),
        )
        for column in marks:
            value = int(row[column] or 0)
            if not value:
                continue
            line = lines.get((row["ESTRANA"], str(int(column.removeprefix("HLASY_")))))
            if line is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=line,
                channel=0,
                measure=PREFERENCE,
                count_round=0,
                flags=0,
                value=value,
            )
    if struck:
        p.add(
            "note",
            id=p.id("note", result_set, "struck"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The candidate register marks {struck} candidacies on these lists as void, so they are not "
            "stored. The polling-district file may still count preference marks for some of them; those marks "
            "count for no one and are left out.",
            lang="en",
        )
    return p
