"""Bavarian municipal council elections, 1978 to 2026, in total for the state.

The Bayerisches Landesamt für Statistik publishes the results of the Gemeinde- und Stadtratswahlen in its GENESIS
database, table 14431. For the whole state, it gives the electorate, the voters and the valid and invalid ballots for
the five elections since 2002, the weighted votes of every party, joint list, and voters' group for those five, and
the seats each won at all nine elections since 1978. Municipal figures are published for one party at a time and for
the 2026 voters only, so they are not part of these packages.
"""

import re
from datetime import date
from functools import cache
from typing import NamedTuple

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "de.gemeinderat_by"
BASE = "https://www.statistikdaten.bayern.de/genesis/online?operation=download&code="
FILES = {
    "voters": f"{BASE}14431-001z&option=xlsx",
    "ballots": f"{BASE}14431-002z&option=xlsx",
    "votes": f"{BASE}14431-003z&option=xlsx",
    "seats": f"{BASE}14431-005z&option=xlsx",
}
PAGE = "https://www.statistikdaten.bayern.de/genesis/online?operation=statistic&code=14431"
GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
BAVARIA = "01a13bb5-c563-741b-83f7-4b7cdad043bd"
BAVARIA_AREA = "01a13bb5-c563-741b-83f7-4b7dbb30d00d"
SCHEME = "de.by_gemeindewahl"
LICENSE = "Data licence Germany – attribution – version 2.0"
LICENSE_URL = "https://www.govdata.de/dl-de/by-2-0"
NOTE = (
    "Bavaria's municipalities hold their council elections on one day every six years. Each voter has as many votes "
    "as the council has seats and may give up to three to one candidate or spread them across lists. The Landesamt's "
    "weighted votes (gewichtete Stimmen) divide each ballot's votes by the number of votes it carries, so the votes "
    "of all lists add to the valid ballots. Each contest here pools the councils of all of Bavaria's municipalities, "
    "including the Landkreis-free cities, and its seats are the seats of all those councils. "
    "Joint lists (Gemeinsame Wahlvorschläge) and voters' groups (Wählergruppen) each pool many local lists, and "
    "'Ohne Wahlvorschlag' pools votes for people who stood on no list. The figures include later by-elections "
    "and corrections the Landesamt lists."
)
NOTE_EARLY = (
    "For the elections before 2002 the Landesamt's table gives the seats of each party, joint list, and voters' "
    "group, and no votes, electorate, or ballots."
)
AGGREGATES = {
    "Gemeinsame Wahlvorschläge": "joint_lists",
    "Wählergruppen": "voters_groups",
    "Ohne Wahlvorschlag": "no_list",
}
TOTAL = "Insgesamt"


class Table(NamedTuple):
    days: list[date]
    rows: dict[str, list[int | None]]


def day(text: str) -> date:
    d, m, y = text.split(".")
    return date(int(y), int(m), int(d))


def cell(value: object) -> int | None:
    return None if value in (None, "", "-") else int(float(str(value)))


@cache
def table(name: str, head: int, first: int) -> Table:
    """Read a workbook whose header row of dates is at `head`, and whose data rows begin at `first`."""
    path = raw.cached(SOURCE, FILES[name]).path
    rows = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
    days = [day(str(c)) for c in rows[head][1:] if c]
    out: dict[str, list[int | None]] = {}
    for row in rows[first:]:
        label = str(row[0] or "").strip()
        if label.startswith("____"):
            break
        if label and label != "davon:":
            out[label] = [cell(c) for c in row[1 : 1 + len(days)]]
    return Table(days, out)


@cache
def by_day(name: str) -> dict[date, list[int | None]]:
    """The rows of a table that lists one election per row: its figures in the order of its columns."""
    path = raw.cached(SOURCE, FILES[name]).path
    rows = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
    return {
        day(str(r[0])): [cell(c) for c in r[1:]]
        for r in rows[5:]
        if r[0] and re.fullmatch(r"\d\d\.\d\d\.\d{4}", str(r[0]))
    }


@cache
def votes() -> Table:
    return table("votes", 5, 6)


@cache
def seats() -> Table:
    return table("seats", 5, 6)


def days() -> list[date]:
    return seats().days


def party_key(label: str) -> str:
    return re.sub(r"\s+", " ", label).casefold()


def short(label: str) -> str:
    found = re.findall(r"\(([^()]+)\)$", label)
    return found[-1] if found else label


def group_labels() -> list[str]:
    labels = [*seats().rows, *votes().rows]
    return list(dict.fromkeys(label for label in labels if label != TOTAL))


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties, joint lists, and voters' groups of Bavaria's municipal council elections since 1978",
    )
    p.external |= {GERMANY, BAVARIA, BAVARIA_AREA}
    source = p.id("source", "lfstat")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Bayerisches Landesamt für Statistik",
        title="Gemeinde- und Stadtratswahlen in Bayern (GENESIS-Online, Tabelle 14431)",
        url=PAGE,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Bayerisches Landesamt für Statistik, Fürth",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), source)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=BAVARIA,
        kind="electoral_district",
        rank=1,
        noun="all municipal electorates",
    )
    for label in group_labels():
        if label in AGGREGATES:
            continue
        id = p.id("party", party_key(label))
        p.add("party", id=id, kind="party", polity_id=BAVARIA)
        p.add("external_id", entity_id=id, scheme="de.gemeinderat_by.party", value=party_key(label))
        p.name(id, label, "de", "official")
    return p


def election(day: date) -> Package:
    year = day.year
    p = Package(
        f"{SOURCE}/by-{year}",
        SOURCE,
        f"The {year} municipal council elections in Bavaria, in total for the state",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {GERMANY, BAVARIA, BAVARIA_AREA, p.id("source", "lfstat")}
    for url in FILES.values():
        p.uses(raw.cached(SOURCE, url))
    source = p.id("source", "lfstat")
    with_votes = day in votes().days
    seat_at = seats().days.index(day)
    vote_at = votes().days.index(day) if with_votes else 0
    labels = [
        label
        for label in group_labels()
        if (seats().rows.get(label, [None] * 9)[seat_at] or 0) > 0
        or (with_votes and (votes().rows.get(label, [None] * 5)[vote_at] or 0) > 0)
    ]
    for label in labels:
        if label not in AGGREGATES:
            p.external.add(p.id("party", party_key(label)))
    total_seats = seats().rows[TOTAL][seat_at] or 0
    election_id = p.id("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=BAVARIA,
        kind="local",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election_id, f"{day:%B} {day.day}, {year} Bavarian municipal council elections", "en", "official")
    p.name(election_id, f"Gemeinde- und Stadtratswahlen in Bayern am {day:%d.%m.%Y}", "de", "native", False)
    path = f"de/{day}-bavaria-gemeinde-stadtratswahlen"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = p.id("contest", year)
    unit = p.id("unit", year)
    result_set = p.id("result_set", year)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        district_area_id=BAVARIA_AREA,
        magnitude=total_seats,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Bavarian municipal councils, all municipalities", "en", "official")
    p.name(contest, f"Gemeinde- und Stadträte in Bayern {year}, alle Gemeinden", "de", "native", False)
    p.add("slug", path=f"{path}/all-municipalities", entity_id=contest, is_current=True)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=BAVARIA,
        scheme_code=SCHEME,
        code=f"{day}:09",
        area_relation="equals",
        area_id=BAVARIA_AREA,
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
        body_md=NOTE if with_votes else f"{NOTE} {NOTE_EARLY}",
        lang="en",
    )

    def value(option: str | None, measure: int, amount: int) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=amount,
        )

    if with_votes:
        electorate = by_day("voters")[day]
        counted = by_day("ballots")[day]
        value(None, 2, electorate[0] or 0)
        value(None, 3, electorate[1] or 0)
        value(None, 4, counted[1] or 0)
        value(None, 5, counted[2] or 0)
    ordered = sorted(
        labels,
        key=lambda label: (
            -((votes().rows.get(label) or [0] * 5)[vote_at] or 0 if with_votes else 0),
            -(seats().rows.get(label, [0] * 9)[seat_at] or 0),
            label,
        ),
    )
    for position, label in enumerate(ordered, start=1):
        option = p.id("option", year, party_key(label))
        aggregate = label in AGGREGATES
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="others" if aggregate else "list",
            party_id=None if aggregate else p.id("party", party_key(label)),
            ballot_position=position,
            ballot_label=label if aggregate else short(label),
        )
        won = (seats().rows.get(label) or [None] * 9)[seat_at] or 0
        if won:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
            value(option, 16, won)
        if with_votes:
            counted = (votes().rows.get(label) or [None] * 5)[vote_at] or 0
            if counted:
                value(option, 1, counted)
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/by-{d.year}" for d in days())]


def parse() -> None:
    registry().write()
    for d in days():
        election(d).write()


def emit() -> None:
    package.run("load", packages())
