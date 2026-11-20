"""Municipal council elections in Schleswig-Holstein: the Gemeindewahlen of 2018 and 2023.

The Statistisches Amt für Hamburg und Schleswig-Holstein collected the final results from the Amts and Gemeinde
administrations on behalf of the Landeswahlleitung. For 2018 it publishes one workbook with a row per municipality
and, for every party, voters' group, and individual candidate, the votes and the seats won by women and by men. For
2023 it publishes the seats each group won in each municipality, by sex, and no votes.
"""

import re
from datetime import date
from functools import cache
from typing import NamedTuple

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "de.gemeinderat_sh"
BASE = "https://www.statistik-nord.de/fileadmin/Dokumente/Wahlen/Schleswig-Holstein/Kommunalwahlen"
FILES = {
    2018: f"{BASE}/2018/Endg%C3%BCltige_Ergebnisse_Gemeindewahlen_SH_2018.xlsx",
    2023: f"{BASE}/2023/Endg%C3%BCltige_Ergebnisse/e_Gemeindewahlen_Sitzverteilung_nach_Geschlecht_KMWSH-2023.xlsx",
}
DAYS = {2018: date(2018, 5, 6), 2023: date(2023, 5, 14)}
PAGE = "https://www.statistik-nord.de/wahlen/wahlen-in-schleswig-holstein/kommunalwahlen"
GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
SCHLESWIG_HOLSTEIN = "01a13bb5-c55d-7311-a43d-bf02e77f66c9"
REFERENCE = DATA / "reference" / "de"
SCHEME = "de.sh_gemeindewahl"
LICENSE = "Reproduction and distribution permitted with the source acknowledged; the content may not be altered"
TYPES = {"Partei": "party", "Wählergruppe": "independent_group", "Einzelkandidat/in": "independent"}
NOTE = (
    "Counted by the municipal administrations and compiled by the Statistisches Amt für Hamburg und Schleswig-Holstein "
    "for the Landeswahlleitung. Each municipality elects its council on one day by a personalized proportional "
    "system: voters have up to three votes, depending on the size of the municipality, and the votes of a party, a "
    "voters' group (Wählergruppe), or an individual candidate are the votes for its candidates. Seats are shared by "
    "the Sainte-Laguë/Schepers method. Registered voters are those entitled to vote and voters those who took part. "
    "Where a municipality's votes equal its valid ballots, the ballots are split into valid and invalid; otherwise "
    "the source's valid and invalid figures count votes and are not stored as ballots. "
    "A group that won no votes and no seats in a municipality did not stand there and is not listed."
)
NOTE_2023 = (
    "The source for 2023 prints the seats each party, voters' group, and individual candidate won in each "
    "municipality, and the share won by women. It prints no votes, no registered voters, and no turnout, so the "
    "contests hold seat outcomes only."
)
SYSTEM_NOTE = (
    "Seats of a municipal council in Schleswig-Holstein are shared among the parties, voters' groups, and individual "
    "candidates by the Sainte-Laguë/Schepers divisor method on their votes. There is no threshold."
)


class Group(NamedTuple):
    label: str
    kind: str
    votes: int
    seats: int


class Result(NamedTuple):
    ags: str
    name: str
    amt: str
    registered: int | None
    voters: int | None
    invalid: int | None
    valid: int | None
    seats: int
    groups: list[Group]


def number(cell: str | None) -> int:
    return int(float(cell)) if cell not in (None, "", "-") else 0


@cache
def sheet(year: int) -> list[tuple[str | None, ...]]:
    path = raw.cached(SOURCE, FILES[year]).path
    return pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()


def results_2018() -> list[Result]:
    rows = sheet(2018)
    header = rows[2]
    groups = [(i, str(header[i + 1]), TYPES[str(header[i + 2])]) for i in range(13, len(header) - 2, 3)]
    out: list[Result] = []
    for row in rows[4:]:
        if not row[3] or not str(row[3]).isdigit():
            continue
        found: list[Group] = []
        for at, label, kind in groups:
            votes = number(row[at])
            won = number(row[at + 1]) + number(row[at + 2])
            if votes or won:
                found.append(Group(label, kind, votes, won))
        out.append(
            Result(
                "0" + str(row[3]),
                str(row[4]),
                str(row[2]),
                number(row[8]),
                number(row[9]),
                number(row[11]),
                number(row[12]),
                number(row[6]),
                found,
            )
        )
    return out


def results_2023() -> list[Result]:
    rows = sheet(2023)
    pool: dict[str, tuple[str, list[Group]]] = {}
    for row in rows[6:]:
        code = row[0] or ""
        if code in ("01001", "01002", "01003", "01004"):
            ags = code + "000"
        elif code.isdigit() and len(code) >= 8:
            ags = code[:5] + code[-3:]
        else:
            continue
        label = str(row[2]) if row[2] not in (None, "-") else str(row[3])
        won = number(row[4])
        _, groups = pool.setdefault(ags, (str(row[1]), []))
        groups.append(Group(label.strip(), "", 0, won))
    return [
        Result(ags, name, "", None, None, None, None, sum(g.seats for g in groups), groups)
        for ags, (name, groups) in pool.items()
    ]


@cache
def results(year: int) -> list[Result]:
    return results_2018() if year == 2018 else results_2023()


@cache
def areas() -> dict[str, str]:
    """The id production holds for each municipality, by its AGS."""
    found = pl.read_parquet(REFERENCE / "area.parquet").join(
        pl.read_parquet(REFERENCE / "area_code.parquet"), left_on="id", right_on="area_id"
    )
    found = found.filter((pl.col("scheme_code") == "de.gemeinde") & pl.col("valid_to").is_null())
    return dict(zip(found["code"].to_list(), found["id"].to_list(), strict=True))


def held(year: int) -> list[Result]:
    known = areas()
    return [r for r in results(year) if r.ags in known and r.seats]


@cache
def sizes() -> dict[str, list[tuple[int, date, date | None]]]:
    """Each council's size over the years covered, one row per period of constant size.

    A size holds from the election that set it until the next election that set a different one. Every election
    package writes the same rows for a council, so whichever loads last claims them and none overlap.
    """
    by_body: dict[str, list[tuple[date, int]]] = {}
    for year, day in sorted(DAYS.items()):
        for r in held(year):
            by_body.setdefault(r.ags, []).append((day, r.seats))
    out: dict[str, list[tuple[int, date, date | None]]] = {}
    for ags, seen in by_body.items():
        periods: list[tuple[int, date, date | None]] = []
        for day, seats in seen:
            if periods and periods[-1][0] == seats:
                continue
            if periods:
                periods[-1] = (periods[-1][0], periods[-1][1], day)
            periods.append((seats, day, None))
        out[ags] = periods
    return out


def missing(year: int) -> list[Result]:
    known = areas()
    return [r for r in results(year) if r.ags not in known]


def plain(name: str) -> str:
    return re.sub(r"\s+", " ", name.split(",")[0]).strip().title() if name.isupper() else name.split(",")[0].strip()


def official(name: str) -> str:
    return name.title() if name.isupper() else name


def party_label(label: str) -> str:
    return label.casefold()


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The municipalities of Schleswig-Holstein with their councils, the seat-allocation rules, and the parties, "
        "voters' groups, and individual candidates of the 2018 and 2023 council elections",
    )
    p.external |= {GERMANY, SCHLESWIG_HOLSTEIN}
    known = areas()
    p.external |= {known[r.ags] for year in DAYS for r in held(year)}
    source = p.id("source", "statistik-nord")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Statistisches Amt für Hamburg und Schleswig-Holstein",
        title="Kommunalwahlen in Schleswig-Holstein: endgültige Ergebnisse der Gemeindewahlen",
        url=PAGE,
        license=LICENSE,
        attribution="Statistisches Amt für Hamburg und Schleswig-Holstein",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), source)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=SCHLESWIG_HOLSTEIN,
        kind="electoral_district",
        rank=1,
        noun="municipal electorate",
    )
    system = p.id("electoral_system", "sainte_lague_schepers")
    p.add(
        "electoral_system",
        id=system,
        polity_id=SCHLESWIG_HOLSTEIN,
        valid_from=date(2008, 1, 1),
        valid_to=None,
        rules={"formula": "sainte_lague_schepers"},
        rules_version=1,
    )
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    seen: dict[str, tuple[str, str]] = {}
    names: dict[str, tuple[str, str]] = {}
    for year in DAYS:
        for result in held(year):
            names.setdefault(result.ags, (result.name, plain(result.name)))
            for group in result.groups:
                if group.kind != "independent":
                    seen.setdefault(party_label(group.label), (group.label, group.kind))
    for folded, (label, kind) in sorted(seen.items()):
        id = p.id("party", folded)
        p.add("party", id=id, kind=kind or "independent_group", polity_id=SCHLESWIG_HOLSTEIN)
        p.add("external_id", entity_id=id, scheme="de.gemeinderat_sh.group", value=folded)
        p.name(id, label, "de", "official")
    for ags, (name, short) in sorted(names.items()):
        polity = p.id("polity", ags)
        p.add("polity", id=polity, kind="municipality", iso_code=None)
        p.add("polity_parent", polity_id=polity, parent_id=SCHLESWIG_HOLSTEIN, valid_from=None, valid_to=None)
        p.add("polity_territory", polity_id=polity, area_id=known[ags], valid_from=None, valid_to=None)
        p.name(polity, official(name), "de", "official")
        p.name(polity, short, "en", "official", False)
        p.add("external_id", entity_id=polity, scheme="de.ags", value=ags)
        body = p.id("body", ags)
        p.add("body", id=body, polity_id=polity, kind="council")
        p.name(body, f"Gemeindevertretung {short}", "de", "official")
        p.name(body, f"Council of {short}", "en", "official", False)
    return p


def election(year: int) -> Package:
    day = DAYS[year]
    pool = held(year)
    p = Package(
        f"{SOURCE}/sh-{year}",
        SOURCE,
        f"The {year} municipal council elections in Schleswig-Holstein, one contest per municipality",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {GERMANY, SCHLESWIG_HOLSTEIN}
    p.external |= {p.id("polity", r.ags) for r in pool}
    p.external |= {areas()[r.ags] for r in pool}
    p.external |= {p.id("body", r.ags) for r in pool}
    p.external.add(p.id("electoral_system", "sainte_lague_schepers"))
    p.external.add(p.id("source", "statistik-nord"))
    p.external |= {p.id("party", party_label(g.label)) for r in pool for g in r.groups if g.kind != "independent"}
    if year == 2023:
        p.external |= {p.id("party", party_label(g.label)) for r in pool for g in r.groups}
    p.uses(raw.cached(SOURCE, FILES[year]))
    source = p.id("source", "statistik-nord")
    election_id = p.id("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=SCHLESWIG_HOLSTEIN,
        kind="local",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election_id, f"May {day.day}, {year} Schleswig-Holstein municipal council elections", "en", "official")
    p.name(election_id, f"Gemeindewahlen in Schleswig-Holstein am {day:%d.%m.%Y}", "de", "native", False)
    path = f"de/{day}-schleswig-holstein-gemeindewahlen"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    taken: set[str] = set()
    known = areas()
    for r in sorted(pool, key=lambda item: item.ags):
        short = plain(r.name)
        contest = p.id("contest", year, r.ags)
        unit = p.id("unit", year, r.ags)
        result_set = p.id("result_set", year, r.ags)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=p.id("body", r.ags),
            district_area_id=known[r.ags],
            magnitude=r.seats,
            round_number=1,
            electoral_system_id=p.id("electoral_system", "sainte_lague_schepers"),
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {short} council election", "en", "official")
        p.name(contest, f"Gemeindewahl {short} {year}", "de", "native", False)
        tail = slug(short)
        if f"{path}/{tail}" in taken:
            tail = f"{tail}-{r.ags[2:5]}"
        if f"{path}/{tail}" in taken:
            tail = f"{tail}-{r.ags[5:]}"
        taken.add(f"{path}/{tail}")
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        for seats, start, end in sizes()[r.ags]:
            p.add("body_size", body_id=p.id("body", r.ags), seats=seats, valid_from=start, valid_to=end)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=p.id("polity", r.ags),
            scheme_code=SCHEME,
            code=f"{day}:{r.ags}",
            area_relation="equals",
            area_id=known[r.ags],
            kind="standard",
        )
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=p.id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=NOTE if year == 2018 else NOTE_2023,
            lang="en",
        )
        if year == 2018:
            ballots = r.valid is not None and r.invalid is not None and r.voters == r.valid + r.invalid
            measures = [(2, r.registered), (3, r.voters)]
            if ballots:
                measures += [(4, r.valid), (5, r.invalid)]
            for measure, value in measures:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        for position, group in enumerate(sorted(r.groups, key=lambda g: (-g.votes, -g.seats, g.label)), start=1):
            option = p.id("option", year, r.ags, group.label)
            independent = group.kind == "independent"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="independents" if independent else "list",
                party_id=None if independent else p.id("party", party_label(group.label)),
                ballot_position=position,
                ballot_label=group.label,
            )
            if group.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=group.seats)
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=16,
                    count_round=0,
                    flags=0,
                    value=group.seats,
                )
            if year == 2018:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=group.votes,
                )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/sh-{year}" for year in DAYS)]


def parse() -> None:
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())
