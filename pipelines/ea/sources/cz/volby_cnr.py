"""Czech National Council elections of 1990 and 1992 by polling district from the volby.cz results pages.

The two elections have no open-data files, only the results service's HTML pages, which print every polling district
(okrsek) by obec, okres, and electoral region (kraj), every candidate list with its candidates and preference votes,
and the seat allocation tables. They share the page layout of the 1996 and 1998 Chamber of Deputies pages, so this
module reads them with the readers in `volby_ps`. Each election is one package, `cz.volby/cnr-<year>`, built on
`cz.volby/geo`, `cz.volby/registry`, and `cz.volby/ps-1996`, which holds the eight electoral regions of 1960 to 2000
that both councils were elected in. `cnr-1990` holds the council, its parties, and its electoral system, and
`cnr-1992` depends on it.
"""

import re
from collections import Counter, defaultdict
from datetime import date

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.cz.volby import CZECHIA, SOURCE, TERRITORY, VOLBY_SOURCE, _accounting, _overcount, area, key
from ea.sources.cz.volby_ps import (
    KRAJ_SCHEME,
    KRAJE,
    PREFERENCE,
    STATIC,
    VOTES,
    candidates,
    fetch_year,
    full_names,
    link,
    list_pages,
    national,
    numbers,
    okres_list,
    okrsek_list,
    placement,
    read,
    regional,
    regions,
    rows,
    seats,
    short_names,
    tally,
    whole,
)

CNR = {
    1990: (date(1990, 6, 8), date(1990, 6, 9)),
    1992: (date(1992, 6, 5), date(1992, 6, 6)),
}
FIRST = min(CNR)
BODY = key("body", "cnr")
METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as printed on the volby.cz results pages. "
    "Registered voters are the voters on the roll, voters are the envelopes issued, ballots cast are the envelopes "
    "put in the box, and valid votes are the valid list votes. Each candidate's preference votes are the totals for "
    "the electoral region, as the pages print them. Polling districts are placed in their municipality; where the "
    "municipality's code no longer exists, in its district or region."
)
SYSTEM_METHOD = {
    1990: (
        "200 deputies were elected for two years in eight electoral regions (the regions of 1960 to 2000) from "
        "candidate lists. A list needed 5% of the valid votes in the whole Czech Republic. The first scrutiny gave "
        "each region's seats to the lists by the quota of the region's valid votes divided by its seats plus one, and "
        "the second scrutiny gave the seats still open to the lists' remainders nationally. The regions' seat counts "
        "were set by dividing each region's valid votes by the Republic mandate number. Voters could mark preference "
        "votes, and a candidate with enough of them moved up the list."
    ),
    1992: (
        "200 deputies were elected for four years in eight electoral regions (the regions of 1960 to 2000) from "
        "candidate lists. A list needed 5% of the valid votes in the whole Czech Republic, and a coalition of "
        "lists 7% to 11% by its size. The first scrutiny gave each region's seats to the lists by the quota of the "
        "region's valid votes divided by its seats plus one, and the second scrutiny gave the seats still open to the "
        "lists' remainders nationally. The regions' seat counts were set by dividing each region's valid votes by the "
        "Republic mandate number. Voters could mark preference votes, and a candidate with enough of them moved up "
        "the list."
    ),
}
RULES: dict[str, object] = {
    "formula": "hagenbach_bischoff_quota",
    "remainders": "national",
    "threshold": {"level": "list", "scope": "national", "percent": 5},
    "lists": {"order": "flexible", "vacancy": "next_on_list"},
}
VALID = {1990: (date(1990, 1, 1), date(1992, 1, 1)), 1992: (date(1992, 1, 1), date(1996, 1, 1))}


def system(year: int) -> str:
    return key("electoral_system", "cnr", year)


def packages() -> list[str]:
    return [f"cz.volby/cnr-{year}" for year in CNR]


def fetch() -> None:
    for year in CNR:
        fetch_year(year)


def parse() -> None:
    for year in CNR:
        chamber(year).write()


def emit() -> None:
    package.run("load", packages())


def chamber(year: int) -> Package:
    first, last = CNR[year]
    p = Package(
        f"cz.volby/cnr-{year}",
        SOURCE,
        f"The {year} Czech National Council election by polling district from the volby.cz results pages",
        depends=[
            "cz.volby/geo",
            "cz.volby/registry",
            "cz.volby/ps-1996",
            *([] if year == FIRST else [f"cz.volby/cnr-{FIRST}"]),
        ],
    )
    p.external |= {CZECHIA, TERRITORY}
    found, group, _ = placement(year)
    if year == FIRST:
        p.add("body", id=BODY, polity_id=CZECHIA, kind="unicameral")
        p.name(BODY, "Czech National Council", "en", "official")
        p.name(BODY, "Česká národní rada", "cs", "native", False)
    valid_from, valid_to = VALID[year]
    p.add(
        "electoral_system",
        id=system(year),
        polity_id=CZECHIA,
        valid_from=valid_from,
        valid_to=valid_to,
        rules=RULES,
        rules_version=1,
    )
    p.add(
        "note",
        id=p.id("note", system(year)),
        entity_id=system(year),
        kind="methodology",
        body_md=SYSTEM_METHOD[year],
        lang="en",
    )
    election = key("election", "cnr", year)
    p.add("election", id=election, polity_id=CZECHIA, kind="general", date_from=first, date_to=last, status="held")
    p.name(election, f"{year} Czech National Council election", "en", "official")
    p.name(election, f"Volby do České národní rady {year}", "cs", "native", False)
    path = f"cz/{first}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)

    total, printed = national(year)
    by_kraj = regional(year)
    declared_kraj = regions(year)
    seats_kraj, seats_list = seats(year)
    full, short = full_names(year), short_names(year)
    earlier_full = full_names(FIRST) if year != FIRST else {}
    earlier = {name: no for no, name in earlier_full.items()}
    lists = sorted(printed)
    party_of: dict[str, str] = {}
    for number in lists:
        borrowed = earlier.get(full.get(number, ""))
        party_of[number] = p.id("party", "cnr", FIRST if borrowed else year, borrowed or number)
        if borrowed:
            continue
        p.add("party", id=party_of[number], kind="party", polity_id=CZECHIA)
        p.add("external_id", entity_id=party_of[number], scheme="cz.volby.cnr", value=f"{year}:{number}")
        p.name(party_of[number], full.get(number, printed[number][0]), "cs", "official")
        if number in full and printed[number][0] != full[number]:
            p.name(party_of[number], printed[number][0], "cs", "short", False)
        if number in short:
            p.name(party_of[number], short[number], "cs", "abbreviation", False)

    pages = list_pages(year)
    for name in (*STATIC, "u32"):
        query = {"xpl": 0, "xtr": 2} if name == "u32" else {}
        p.artifact(raw.cached(SOURCE, link(year, name, **query)), VOLBY_SOURCE)
    for v, k in pages:
        p.artifact(raw.cached(SOURCE, link(year, "u321", xpl=0, xtr=2, xvstrana=v, xkraj=k)), VOLBY_SOURCE)
    for k, o in okres_list(year):
        for name in ("u5311", "u5312"):
            p.artifact(raw.cached(SOURCE, link(year, name, xkraj=k, xokres=o)), VOLBY_SOURCE)

    roster = {(number, k): candidates(year, number, k) for number, k in pages}
    won: Counter[str] = Counter()
    for (_, k), rows_ in roster.items():
        won[k] += sum(1 for row in rows_ if row["elected"])
    contest_of: dict[str, str] = {}
    set_of: dict[str, str] = {}
    option_of: dict[tuple[str, str], str] = {}
    for k, title in KRAJE.items():
        contest = contest_of[k] = key("contest", "cnr", year, k)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=area(KRAJ_SCHEME, k),
            magnitude=won[k],
            round_number=1,
            electoral_system_id=system(year),
            default_result_kind="counted",
        )
        p.name(contest, title, "cs", "official")
        p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
        result_set = set_of[k] = key("result_set", "cnr", year, k)
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
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for measure, value in declared_kraj[k].items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        running = sorted({v for v, here in pages if here == k} | set(by_kraj[k]))
        for number in running:
            option = option_of[(k, number)] = key("option", "cnr", year, k, number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_of[number],
                ballot_position=int(number),
                ballot_label=printed[number][0],
            )
            if number in by_kraj[k]:
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=by_kraj[k][number])
    elected: Counter[tuple[str, str]] = Counter()
    for number, k in pages:
        option = option_of[(k, number)]
        for row in roster[(number, k)]:
            tag = f"cnr:{year}:{k}:{number}:{row['position']}"
            person = p.id("person", tag)
            p.add("person", id=person)
            p.name(person, str(row["name"]), "cs", "official")
            candidacy = p.id("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=row["position"],
                ballot_name=row["name"],
            )
            line = p.id("option", tag)
            p.add(
                "contest_option",
                id=line,
                contest_id=contest_of[k],
                kind="candidate",
                parent_option_id=option,
                ballot_position=row["position"],
                ballot_label=row["name"],
            )
            if row["preference"]:
                p.add(
                    "declared",
                    result_set_id=set_of[k],
                    option_id=line,
                    measure=PREFERENCE,
                    value=row["preference"],
                )
            if row["elected"]:
                elected[(k, number)] += 1
                p.add("outcome", result_set_id=set_of[k], option_id=option, candidacy_id=candidacy, kind="elected")
    for (k, number), n in sorted(elected.items()):
        p.add("outcome", result_set_id=set_of[k], option_id=option_of[(k, number)], kind="won", seats=n)
    if sum(won.values()) != sum(seats_kraj.values()):
        raise ValueError(f"cnr {year}: {sum(won.values())} elected, {sum(seats_kraj.values())} seats printed")
    for k, expected in seats_kraj.items():
        if won[k] != expected:
            p.add(
                "note",
                id=key("note", set_of[k], "second"),
                entity_id=set_of[k],
                kind="caveat",
                body_md=f"The seat table prints {expected} seats for this region, while the candidate lists mark "
                f"{won[k]} deputies elected in it. The seats still sum to 200 nationally and match the table for "
                f"each list. The contest holds the {won[k]} seats marked elected.",
                lang="en",
            )
    for number, expected in seats_list.items():
        got = sum(n for (_, here), n in elected.items() if here == number)
        if got != expected:
            raise ValueError(f"cnr {year} list {number}: {got} elected, {expected} seats printed")

    stats: dict[str, dict[int, int]] = {k: Counter() for k in KRAJE}
    sums: dict[str, Counter[str]] = {k: Counter() for k in KRAJE}
    per_okres: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    pairs: list[tuple[str, dict[str, str | None]]] = []
    for k, o, c, n in okrsek_list(year):
        accounting, votes, _ = tally(year, k, o, c, n)
        p.artifact(raw.cached(SOURCE, link(year, "u531111", xkraj=k, xokres=o, xobec=c, xokrsek=n)), VOLBY_SOURCE)
        code = f"{first}:{c}:{int(n)}"
        unit = key("unit", code)
        place = found.get(c) or group.get((k, o)) or area(KRAJ_SCHEME, k)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CZECHIA,
            scheme_code="cz.okrsek",
            code=code,
            area_relation="within",
            area_id=place,
            kind="standard",
        )
        for measure, value in accounting.items():
            stats[k][measure] += value
            per_okres[(k, o)][str(measure)] += value
        row: dict[str, str | None] = {
            "VOL_SEZNAM": str(accounting[2]),
            "VYD_OBALKY": str(accounting[14]),
            "ODEVZ_OBAL": str(accounting[3]),
            "PL_HL_CELK": str(accounting[4]),
        }
        pairs.append((set_of[k], row))
        _accounting(p, set_of[k], unit, row)
        for number, count in votes.items():
            sums[k][number] += count
            per_okres[(k, o)][f"list{number}"] += count
            p.add(
                "result_value",
                result_set_id=set_of[k],
                unit_id=unit,
                option_id=option_of[(k, number)],
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=count,
            )
    _overcount(p, pairs)
    p.accept(
        "accounting.declared",
        "volby.cz prints each candidate's preference votes only as a total for the electoral region, not per "
        "polling district, so those totals are declared with no polling-district values to sum",
    )
    for k in KRAJE:
        if dict(stats[k]) != declared_kraj[k]:
            raise ValueError(f"cnr {year} region {k}: units sum to {dict(stats[k])}, region prints {declared_kraj[k]}")
        if dict(sums[k]) != by_kraj[k]:
            raise ValueError(f"cnr {year} region {k}: units' list votes differ from the region's")
    summed = {m: sum(stats[k][m] for k in KRAJE) for m in total}
    if summed != total:
        raise ValueError(f"cnr {year}: regions sum to {summed}, the country prints {total}")
    for k, o in okres_list(year):
        want: Counter[str] = Counter()
        for cells in rows(read(year, "u5312", xkraj=k, xokres=o)):
            if len(cells) >= 9 and numbers(cells[:2]) and numbers(cells[3:5]):
                want.update({"2": whole(cells[3]), "14": whole(cells[4]), "3": whole(cells[6]), "4": whole(cells[7])})
            elif len(cells) >= 4 and re.fullmatch(r"\d\d", cells[0]) and re.fullmatch(r"[\d ]+", cells[2]):
                for number, _, count, _ in [cells[:4]] + ([cells[5:9]] if len(cells) >= 9 else []):
                    if count:
                        want[f"list{number}"] += whole(count)
        have = Counter({name: count for name, count in per_okres[(k, o)].items() if count or name in want})
        if {name: count for name, count in want.items() if count} != {n: c for n, c in have.items() if c}:
            raise ValueError(f"cnr {year} district {k}/{o}: units differ from the district's printed totals")
    return p
