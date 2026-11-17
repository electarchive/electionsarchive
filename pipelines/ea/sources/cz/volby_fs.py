"""Federal Assembly elections of 1990 and 1992 in the Czech Republic by polling district from the volby.cz pages.

The Federal Assembly of Czechoslovakia had two chambers, the Chamber of the People and the Chamber of the Nations. In
1990 and 1992 each was elected by list voting in the Czech and the Slovak Republic separately, and volby.cz publishes
the Czech part only: eight electoral regions (the regions of 1960 to 2000), each polling district, every candidate
list with its candidates and preference votes, and the seat allocation tables. The pages share the layout of the
Czech National Council pages of the same days, so this module reads them with the readers in `volby_ps`. Each chamber
and year is one package, `cz.volby/fs-<chamber>-<year>`, built on `cz.volby/geo`, `cz.volby/registry`,
`cz.volby/ps-1996` (which holds the regions), and the `cz.volby/cnr-<year>` package of the same days, whose polling
districts these packages reuse. Parties and bodies are created by the first package that needs them.
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

DAYS = {1990: (date(1990, 6, 8), date(1990, 6, 9)), 1992: (date(1992, 6, 5), date(1992, 6, 6))}
ORDER = [("sl", 1990), ("sn", 1990), ("sl", 1992), ("sn", 1992)]
CHAMBERS = {
    "sl": ("Chamber of the People", "Sněmovna lidu", "lower_house", "chamber-of-the-people"),
    "sn": ("Chamber of the Nations", "Sněmovna národů", "upper_house", "chamber-of-the-nations"),
}
METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as printed on the volby.cz results pages "
    "for the territory of the Czech Republic; the Slovak part of the election is not on those pages. Registered "
    "voters are the voters on the roll, voters are the envelopes issued, ballots cast are the envelopes put in the "
    "box, and valid votes are the valid list votes. Each candidate's preference votes are the totals for the "
    "electoral region, as the pages print them. Polling districts are placed in their municipality; where the "
    "municipality's code no longer exists, in its district or region."
)
RULES: dict[str, object] = {
    "formula": "hagenbach_bischoff_quota",
    "remainders": "national",
    "threshold": {"level": "list", "scope": "national", "percent": 5},
    "lists": {"order": "flexible", "vacancy": "next_on_list"},
}
VALID = {1990: (date(1990, 1, 1), date(1992, 1, 1)), 1992: (date(1992, 1, 1), date(1993, 1, 1))}


def page(chamber: str, year: int) -> str:
    return f"{chamber}{year}"


def tag(chamber: str, year: int) -> str:
    return f"fs_{chamber}-{year}"


def system(year: int) -> str:
    return key("electoral_system", "fs", year)


def method(year: int) -> str:
    return (
        "The Federal Assembly of Czechoslovakia had two chambers elected on the same days by list voting in the Czech "
        "and the Slovak Republic, each republic voting separately. In the Czech Republic the deputies of each chamber "
        "were elected in eight electoral regions (the regions of 1960 to 2000) from candidate lists. A list took part "
        "in the first scrutiny when it won 5% of the valid votes in either republic. The first scrutiny gave each "
        "region's seats to the lists by the quota of the region's valid votes divided by its seats plus one, and "
        "the second scrutiny gave the seats still open to the lists' remainders in the republic. The regions' seat "
        "counts were set by dividing each region's valid votes by the republic's mandate number. Voters could mark "
        f"preference votes, and a candidate with enough of them moved up the list. The mandate of {year} lasted "
        + ("two years." if year == 1990 else "until the federation ended on 31 December 1992.")
    )


def packages() -> list[str]:
    return [f"cz.volby/{tag(c, y)}" for c, y in ORDER]


def fetch() -> None:
    for chamber, year in ORDER:
        fetch_year(page(chamber, year))
    for year in DAYS:  # the Czech National Council pages list the polling districts these packages reuse
        reuse = f"cnr{year}"
        raw.fetch(SOURCE, link(reuse, "u53"), suffix=".html")
        raw.fetch_many(
            SOURCE,
            [link(reuse, "u5311", xkraj=k, xokres=o) for k, o in okres_list(reuse)],
            suffix=".html",
            workers=12,
        )
        raw.fetch_many(
            SOURCE,
            [link(reuse, "u53111", xkraj=k, xokres=o, xobec=c) for k, o, c in obec_list(reuse)],
            suffix=".html",
            workers=12,
        )


def obec_list(code: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for k, o in okres_list(code):
        found += re.findall(r"u53111\?xkraj=(\d+)&xokres=(\d+)&xobec=(\d+)", read(code, "u5311", xkraj=k, xokres=o))
    return found


def parse() -> None:
    for chamber, year in ORDER:
        build(chamber, year).write()


def emit() -> None:
    package.run("load", packages())


def names(code: str) -> dict[str, str]:
    """The name each list is known by on one results page: its full name where printed, else the printed name."""
    _, printed = national(code)
    full = full_names(code)
    return {number: full.get(number, printed[number][0]) for number in printed}


def units_of(code: str) -> set[tuple[str, int]]:
    return {(c, int(n)) for _, _, c, n in okrsek_list(code)}


def build(chamber: str, year: int) -> Package:
    first, last = DAYS[year]
    code = page(chamber, year)
    me = (chamber, year)
    before = ORDER[: ORDER.index(me)]
    english, czech, kind, slug = CHAMBERS[chamber]
    p = Package(
        f"cz.volby/{tag(chamber, year)}",
        SOURCE,
        f"The {year} Federal Assembly {english} election in the Czech Republic by polling district from the volby.cz "
        "results pages",
        depends=[
            "cz.volby/geo",
            "cz.volby/registry",
            "cz.volby/ps-1996",
            *(f"cz.volby/cnr-{y}" for y in sorted({y for _, y in [*before, me]})),
            *(f"cz.volby/{tag(c, y)}" for c, y in before),
        ],
    )
    p.external |= {CZECHIA, TERRITORY}
    found, group, _ = placement(code)
    body = key("body", "fs", chamber)
    if not any(c == chamber for c, _ in before):
        p.add("body", id=body, polity_id=CZECHIA, kind=kind)
        p.name(body, f"Federal Assembly, {english}", "en", "official")
        p.name(body, f"Federální shromáždění, {czech}", "cs", "native", False)
    if not any(y == year for _, y in before):
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
            body_md=method(year),
            lang="en",
        )
    election = key("election", "fs", chamber, year)
    p.add("election", id=election, polity_id=CZECHIA, kind="general", date_from=first, date_to=last, status="held")
    p.name(election, f"{year} Federal Assembly {english} election (Czech Republic)", "en", "official")
    p.name(election, f"Volby do {czech} Federálního shromáždění {year}", "cs", "native", False)
    path = f"cz/{first}-federal-{slug}"
    p.add("slug", path=path, entity_id=election, is_current=True)

    total, printed = national(code)
    by_kraj = regional(code)
    declared_kraj = regions(code)
    seats_kraj, seats_list = seats(code)
    short = short_names(code)
    mine = names(code)
    earlier = {}
    for c, y in before:
        for number, name in names(page(c, y)).items():
            earlier.setdefault(name, (page(c, y), number))
    party_of: dict[str, str] = {}
    for number in sorted(printed):
        home = earlier.get(mine[number])
        if home is not None:
            party_of[number] = p.id("party", "fs", *home)
            continue
        party_of[number] = p.id("party", "fs", code, number)
        p.add("party", id=party_of[number], kind="party", polity_id=CZECHIA)
        p.add("external_id", entity_id=party_of[number], scheme="cz.volby.fs", value=f"{code}:{number}")
        p.name(party_of[number], mine[number], "cs", "official")
        if printed[number][0] != mine[number]:
            p.name(party_of[number], printed[number][0], "cs", "short", False)
        if number in short:
            p.name(party_of[number], short[number], "cs", "abbreviation", False)

    pages = list_pages(code)
    for name in (*STATIC, "u32"):
        query = {"xpl": 0, "xtr": 2} if name == "u32" else {}
        p.artifact(raw.cached(SOURCE, link(code, name, **query)), VOLBY_SOURCE)
    for v, k in pages:
        p.artifact(raw.cached(SOURCE, link(code, "u321", xpl=0, xtr=2, xvstrana=v, xkraj=k)), VOLBY_SOURCE)
    for k, o in okres_list(code):
        for name in ("u5311", "u5312"):
            p.artifact(raw.cached(SOURCE, link(code, name, xkraj=k, xokres=o)), VOLBY_SOURCE)

    roster = {(number, k): candidates(code, number, k) for number, k in pages}
    won: Counter[str] = Counter()
    for (_, k), rows_ in roster.items():
        won[k] += sum(1 for row in rows_ if row["elected"])
    contest_of: dict[str, str] = {}
    set_of: dict[str, str] = {}
    option_of: dict[tuple[str, str], str] = {}
    for k, title in KRAJE.items():
        contest = contest_of[k] = key("contest", "fs", chamber, year, k)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            district_area_id=area(KRAJ_SCHEME, k),
            magnitude=won[k],
            round_number=1,
            electoral_system_id=system(year),
            default_result_kind="counted",
        )
        p.name(contest, title, "cs", "official")
        p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
        result_set = set_of[k] = key("result_set", "fs", chamber, year, k)
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
            option = option_of[(k, number)] = key("option", "fs", chamber, year, k, number)
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
            at = f"fs:{chamber}:{year}:{k}:{number}:{row['position']}"
            person = p.id("person", at)
            p.add("person", id=person)
            p.name(person, str(row["name"]), "cs", "official")
            candidacy = p.id("candidacy", at)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=row["position"],
                ballot_name=row["name"],
            )
            line = p.id("option", at)
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
        raise ValueError(f"{code}: {sum(won.values())} elected, {sum(seats_kraj.values())} seats printed")
    for k, expected in seats_kraj.items():
        if won[k] != expected:
            p.add(
                "note",
                id=key("note", set_of[k], "second"),
                entity_id=set_of[k],
                kind="caveat",
                body_md=f"The seat table prints {expected} seats for this region, while the candidate lists mark "
                f"{won[k]} deputies elected in it. The seats match the table for each list. The contest holds the "
                f"{won[k]} seats marked elected.",
                lang="en",
            )
    for number, expected in seats_list.items():
        got = sum(n for (_, here), n in elected.items() if here == number)
        if got != expected:
            raise ValueError(f"{code} list {number}: {got} elected, {expected} seats printed")

    owned = units_of(f"cnr{year}")
    for c, y in before:
        if y == year:
            owned |= units_of(page(c, y))
    stats: dict[str, dict[int, int]] = {k: Counter() for k in KRAJE}
    sums: dict[str, Counter[str]] = {k: Counter() for k in KRAJE}
    per_okres: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    pairs: list[tuple[str, dict[str, str | None]]] = []
    for k, o, c, n in okrsek_list(code):
        accounting, votes, _ = tally(code, k, o, c, n)
        p.artifact(raw.cached(SOURCE, link(code, "u531111", xkraj=k, xokres=o, xobec=c, xokrsek=n)), VOLBY_SOURCE)
        unit_code = f"{first}:{c}:{int(n)}"
        unit = key("unit", unit_code)
        if (c, int(n)) not in owned:
            place = found.get(c) or group.get((k, o)) or area(KRAJ_SCHEME, k)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CZECHIA,
                scheme_code="cz.okrsek",
                code=unit_code,
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
            raise ValueError(f"{code} region {k}: units sum to {dict(stats[k])}, region prints {declared_kraj[k]}")
        if dict(sums[k]) != by_kraj[k]:
            raise ValueError(f"{code} region {k}: units' list votes differ from the region's")
    summed = {m: sum(stats[k][m] for k in KRAJE) for m in total}
    if summed != total:
        raise ValueError(f"{code}: regions sum to {summed}, the country prints {total}")
    for k, o in okres_list(code):
        want: Counter[str] = Counter()
        for cells in rows(read(code, "u5312", xkraj=k, xokres=o)):
            if len(cells) >= 9 and numbers(cells[:2]) and numbers(cells[3:5]):
                want.update({"2": whole(cells[3]), "14": whole(cells[4]), "3": whole(cells[6]), "4": whole(cells[7])})
            elif len(cells) >= 4 and re.fullmatch(r"\d\d", cells[0]) and re.fullmatch(r"[\d ]+", cells[2]):
                for number, _, count, _ in [cells[:4]] + ([cells[5:9]] if len(cells) >= 9 else []):
                    if count:
                        want[f"list{number}"] += whole(count)
        have = Counter({name: count for name, count in per_okres[(k, o)].items() if count or name in want})
        if {name: count for name, count in want.items() if count} != {n: c for n, c in have.items() if c}:
            raise ValueError(f"{code} district {k}/{o}: units differ from the district's printed totals")
    return p
