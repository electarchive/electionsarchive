"""Czech regional council elections (volby do zastupitelstev krajů) 2000-2024 by polling district from volby.cz.

Each election date is one package, `cz.volby/kz-<year>`, built on `cz.volby/geo` and `cz.volby/registry`. Units,
parties, and areas reuse the keys of `ea.sources.cz.volby`, so a polling district that voted for a regional council
and a senator on the same day is one reporting unit. The 13 councils and their electoral system live in the first
package; a party that the registry does not hold lives in the first package whose lists or candidates name it, and
each package depends on the ones before it.
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
    _cities,
    _nuts,
    _overcount,
    _parties,
    _places,
    _turnout,
    _units,
    _utf8,
    area,
    clean,
    key,
    party,
    person_name,
    table,
    url,
)

REGIONAL = {
    2000: ("kz2000", "KZ2000_data_20230223_csv.zip", "KZ2000_reg_20230223_csv.zip", "KZ2000_cisel_20230223_csv.zip"),
    2004: ("kz2004", "KZ2004_data_20230223_csv.zip", "KZ2004_reg_20230223_csv.zip", "KZ2004_cisel_20230223_csv.zip"),
    2008: ("kz2008", "KZ2008_data_20230223_csv.zip", "KZ2008_reg_20230223_csv.zip", "KZ2008_cisel_20230223_csv.zip"),
    2012: ("kz2012", "KZ2012_data_20230223_csv.zip", "KZ2012_reg_20230223_csv.zip", "KZ2012_cisel_20230223_csv.zip"),
    2016: ("kz2016", "KZ2016_data_20230223_csv.zip", "KZ2016_reg_20230223_csv.zip", "KZ2016_cisel_20230223_csv.zip"),
    2020: ("kz2020", "KZ2020_data_20201004a_csv.zip", "KZ2020reg20201004a_csv.zip", "KZ2020ciselniky20200918_csv.zip"),
    2024: ("kz2024", "KZ2024data20240922_csv.zip", "KZ2024reg20240922_csv.zip", "KZ2024ciselniky20240922_csv.zip"),
}
DAYS = {
    2000: (date(2000, 11, 12), date(2000, 11, 12)),
    2004: (date(2004, 11, 5), date(2004, 11, 6)),
    2008: (date(2008, 10, 17), date(2008, 10, 18)),
    2012: (date(2012, 10, 12), date(2012, 10, 13)),
    2016: (date(2016, 10, 7), date(2016, 10, 8)),
    2020: (date(2020, 10, 2), date(2020, 10, 3)),
    2024: (date(2024, 9, 20), date(2024, 9, 21)),
}
DECLARED = {
    2008: f"{BASE}/pls/kz2008/vysledky",
    2012: f"{BASE}/pls/kz2012/vysledky",
    2016: f"{BASE}/pls/kz2016/vysledky",
    2020: f"{BASE}/pls/kz2020/vysledky",
    2024: f"{BASE}/appdata/kz2024/odata/vysledky.xml",
}
VOTES, PREFERENCE = 1, 7
FIRST = min(REGIONAL)
METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as published in the volby.cz open data. "
    "Registered voters are the electoral roll, voters are the official envelopes issued, ballots cast are the "
    "envelopes put in the box, and valid votes are the valid list votes. Each candidate's preference votes per polling "
    "district are stored on a candidate line under the list; zero counts are not stored. Polling districts in the "
    "statutory cities are placed in the whole municipality, not in its city district. Seats and elected members are "
    "as declared in the candidate register."
)
SYSTEM_METHOD = (
    "Each of the 13 regions outside Prague elects its council (45, 55, or 65 members by population) for four years "
    "from party lists in one region-wide constituency. Lists with at least 5% of the region's valid votes share the "
    "seats by D'Hondt divisors, with the first divisor raised to 1.42 (Act 130/2000, § 43); the same 5% applies to "
    "coalitions. Every allocation from 2000 to 2024 is also what plain D'Hondt gives. Voters may give preference votes "
    "to up to four candidates on the list they vote for, and candidates who pass the legal preference threshold move "
    "to the top of their list."
)
RULES: dict[str, object] = {
    "formula": "dhondt",
    "threshold": {"level": "list", "scope": "contest", "percent": 5},
    "lists": {"order": "flexible", "vacancy": "next_on_list"},
}


def fetch() -> None:
    for folder, *names in REGIONAL.values():
        for name in names:
            raw.fetch(SOURCE, url(folder, name), suffix=".zip")
    for link in DECLARED.values():
        try:
            raw.fetch(SOURCE, link, suffix=".xml")
        except httpx.HTTPStatusError as failure:
            print(f"{link}: {failure.response.status_code}", flush=True)


def packages() -> list[str]:
    return [f"cz.volby/kz-{year}" for year in REGIONAL]


def parse() -> None:
    for year in REGIONAL:
        regional(year).write()


def emit() -> None:
    package.run("load", packages())


def body(code: str) -> str:
    return key("body", "kz", code)


SYSTEM = key("electoral_system", "kz", FIRST)


def _lists(year: int) -> pl.DataFrame:
    folder, _, reg, _ = REGIONAL[year]
    return table(folder, reg, "kzrkl").filter(pl.col("STAVREG") == "0")


def _codes(year: int) -> dict[str, dict[str, str | None]]:
    """The party codes a year's registered lists and candidates name, with their cvs rows."""
    folder, _, reg, cis = REGIONAL[year]
    lists = _lists(year)
    candidates = table(folder, reg, "kzrk").join(lists.select("KRZAST", "KSTRANA"), on=["KRZAST", "KSTRANA"])
    used = set(lists["VSTRANA"]) | set(candidates["PSTRANA"]) | set(candidates["NSTRANA"])
    named = {row["VSTRANA"]: row for row in table(folder, cis, "cvs").iter_rows(named=True)}
    return {
        code: named[code] for code in used - {None, "", *NO_PARTY} if code in named and named[code].get("TYPVS") != "N"
    }


def _owned(year: int, registry: set[str]) -> dict[str, dict[str, str | None]]:
    """The parties this year's package defines: named this year, and neither in the registry nor in an earlier year."""
    earlier = {code for before in REGIONAL if before < year for code in _codes(before)}
    return {code: row for code, row in _codes(year).items() if party(code) not in registry and code not in earlier}


def _declared(year: int) -> dict[str, ElementTree.Element]:
    if year not in DECLARED:
        return {}
    data = raw.cached(SOURCE, DECLARED[year]).path.read_bytes()
    data = re.sub(rb'encoding="[^"]+"', b'encoding="utf-8"', data, count=1)
    if not _utf8(data):
        data = data.decode("cp1250").encode()
    root = ElementTree.fromstring(data)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return {element.attrib["CIS_KRZAST"]: element for element in root.iter("KRZAST")}


def _genitive(title: str) -> str:
    if title.startswith("Kraj "):
        return f"Zastupitelstvo Kraje {title.removeprefix('Kraj ')}"
    return "Zastupitelstvo " + re.sub(r"ý kraj$", "ého kraje", title)


def regional(year: int) -> Package:
    folder, data_zip, reg, cis = REGIONAL[year]
    first, last = DAYS[year]
    earlier = [f"cz.volby/kz-{before}" for before in REGIONAL if before < year]
    p = Package(
        f"cz.volby/kz-{year}",
        SOURCE,
        f"The {year} Czech regional council elections in 13 regions, with preference votes, by polling district "
        "from volby.cz",
        depends=["cz.volby/geo", "cz.volby/registry", *earlier],
    )
    p.external |= {CZECHIA, TERRITORY}
    for name in (data_zip, reg, cis):
        p.artifact(raw.cached(SOURCE, url(folder, name)), VOLBY_SOURCE)
    if year in DECLARED:
        p.artifact(raw.cached(SOURCE, DECLARED[year]), VOLBY_SOURCE)

    regions = table(folder, cis, "kzciskr")
    coco = table(folder, cis, "kzcoco").with_columns(
        OBEC_PREZ=pl.col("OBEC").replace_strict(_cities(), default=pl.col("OBEC_PREZ"))
    )
    nuts = _nuts(folder, cis)
    titles = {
        row["code"]: row["text"] for row in _places().filter(pl.col("scheme_code") == "cz.kraj").iter_rows(named=True)
    }
    if year == FIRST:
        p.add(
            "electoral_system",
            id=SYSTEM,
            polity_id=CZECHIA,
            valid_from=date(2000, 1, 1),
            valid_to=None,
            rules=RULES,
            rules_version=1,
        )
        p.add("note", id=p.id("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_METHOD, lang="en")
        for row in regions.iter_rows(named=True):
            code = nuts[row["KRAJ"]]
            p.add("body", id=body(code), polity_id=CZECHIA, kind="council")
            p.name(body(code), f"Regional Council of {titles[code]}", "en", "official")
            p.name(body(code), _genitive(titles[code]), "cs", "native", False)

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
    known = registry | {party(code) for before in REGIONAL if before <= year for code in _codes(before)}

    def party_of(code: str | None) -> str | None:
        return party(code) if code is not None and party(code) in known else None

    election = p.id("election", "kz", year)
    p.add("election", id=election, polity_id=CZECHIA, kind="general", date_from=first, date_to=last, status="held")
    p.name(election, f"{year} Czech regional elections", "en", "official")
    p.name(election, f"Volby do zastupitelstev krajů {year}", "cs", "native", False)
    path = f"cz/{first}-regional"
    p.add("slug", path=path, entity_id=election, is_current=True)

    accounting = table(folder, data_zip, "kzt6")
    votes = table(folder, data_zip, "kzt6p")
    region_of = {row["OBEC"]: row["KRZAST"] for row in coco.iter_rows(named=True)}
    units = _units(p, first, nuts, coco, accounting)
    lists = _lists(year)
    candidates = table(folder, reg, "kzrk")
    declared = _declared(year)
    sets: dict[str, str] = {}
    options: dict[tuple[str, str], str] = {}
    lines: dict[tuple[str, str, str], str] = {}
    struck: Counter[str] = Counter()
    for row in regions.sort(pl.col("KRZAST").cast(pl.Int32)).iter_rows(named=True):
        number, code = row["KRZAST"], nuts[row["KRAJ"]]
        contest = p.id("contest", "kz", year, code)
        here = candidates.filter(pl.col("KRZAST") == number)
        elected = here.filter(pl.col("MANDAT") == "A")
        if elected.height != int(row["MANDATYKRZ"]):
            raise ValueError(f"kz {year} {code}: {elected.height} elected, {row['MANDATYKRZ']} seats")
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body(code),
            district_area_id=area("cz.kraj", code),
            magnitude=int(row["MANDATYKRZ"]),
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, titles[code], "cs", "official")
        p.add("slug", path=f"{path}/{changeset.slug(titles[code])}", entity_id=contest, is_current=True)
        result_set = sets[number] = p.id("result_set", "kz", year, code)
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
        for entry in (
            lists.filter(pl.col("KRZAST") == number).sort(pl.col("KSTRANA").cast(pl.Int32)).iter_rows(named=True)
        ):
            option = options[(number, entry["KSTRANA"])] = p.id("option", "kz", year, code, entry["KSTRANA"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_of(entry["VSTRANA"]),
                ballot_position=int(entry["KSTRANA"]),
                ballot_label=clean(entry["NAZEVCELK"]),
            )
        won = Counter(elected["KSTRANA"])
        for list_number, n in sorted(won.items(), key=lambda item: int(item[0])):
            p.add("outcome", result_set_id=result_set, option_id=options[(number, list_number)], kind="won", seats=n)
        for entry in here.sort(pl.col("KSTRANA").cast(pl.Int32), pl.col("PORCISLO").cast(pl.Int32)).iter_rows(
            named=True
        ):
            option = options.get((number, entry["KSTRANA"]))
            if option is None:
                raise ValueError(f"kz {year} {code}: candidate on unregistered list {entry['KSTRANA']}")
            if entry["PLATNOST"] != "A":
                if entry["MANDAT"] == "A":
                    raise ValueError(f"kz {year} {code}: struck candidate {person_name(entry)} elected")
                struck[result_set] += 1
                continue
            tag = f"kz:{year}:{code}:{entry['KSTRANA']}:{entry['PORCISLO']}"
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
            line = lines[(number, entry["KSTRANA"], entry["PORCISLO"])] = p.id("option", tag)
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
                    "declared",
                    result_set_id=result_set,
                    option_id=line,
                    measure=PREFERENCE,
                    value=int(entry["POCHLASU"]),
                )
            if entry["MANDAT"] == "A":
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
        element = declared.get(number)
        if element is None:
            continue
        for measure, value in _turnout(next(element.iter("UCAST"))).items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for side in element.iter("STRANA"):
            option = options[(number, side.attrib["KSTRANA"])]
            figures = next(side.iter("HODNOTY_STRANA"))
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=VOTES,
                value=int(figures.attrib["HLASY"]),
            )
            if int(figures.attrib.get("MANDATY") or 0) != won.get(side.attrib["KSTRANA"], 0):
                raise ValueError(
                    f"kz {year} {code} list {side.attrib['KSTRANA']}: results and register disagree on seats"
                )
            for member in side.iter("ZASTUPITEL"):
                printed = here.filter(
                    (pl.col("KSTRANA") == side.attrib["KSTRANA"])
                    & (pl.col("PORCISLO") == member.attrib["PORADOVE_CISLO"])
                )
                if printed.height != 1 or int(printed["POCHLASU"][0] or 0) != int(member.attrib["PREDNOSTNI_HLASY"]):
                    raise ValueError(f"kz {year} {code}: preference votes of {member.attrib['PRIJMENI']} disagree")

    pairs = [(sets[region_of[row["OBEC"]]], row) for row in accounting.iter_rows(named=True)]
    for result_set, row in pairs:
        _accounting(p, result_set, units[row["ID_OKRSKY"]], row)
    _overcount(p, pairs)
    marks = [column for column in votes.columns if column.startswith("HLASY_")]
    for row in votes.iter_rows(named=True):
        number = region_of[row["OBEC"]]
        result_set, unit = sets[number], units[row["ID_OKRSKY"]]
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=options[(number, row["KSTRANA"])],
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
            line = lines.get((number, row["KSTRANA"], str(int(column.removeprefix("HLASY_")))))
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
    for result_set, n in struck.items():
        p.add(
            "note",
            id=p.id("note", result_set, "struck"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The candidate register marks {n} candidacies on these lists as void, so they are not stored. "
            "The polling-district file still counts preference marks for some of them; those marks count for no one "
            "and are left out.",
            lang="en",
        )
    return p
