"""Cantonal government (Regierungsrat) elections of Swiss cantons, by municipality.

Thurgau is the first canton built: its Staatskanzlei publishes every Regierungsrat election from 2004 to 2025 as
open data (CC0 1.0), one row per political municipality, with the candidates' votes. Municipalities, the canton, and
the canton's polity are read from the Swiss reference partition (`data/reference/ch/`), where the Federal Statistical
Office's municipal reporting units and swisstopo's areas are held.
"""

import csv
import io
import time
from dataclasses import dataclass
from datetime import date
from functools import cache
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "ch.kantonsregierung"
REFERENCE = DATA / "reference" / "ch"
PORTAL = "https://data.tg.ch/api/v2/catalog/datasets/sk-stat-{number}/exports/csv?use_labels=true"
DATASETS = (101, 102, 103, 104, 105, 106, 107, 108, 109, 128, 149)
BY_ELECTION = {"Ersatzwahl_Regierungsrat"}
NO_UNIT = {"9999"}  # Auslandschweizer: the portal lists the row with zeros only


@dataclass(frozen=True)
class Canton:
    iso: str
    slug: str
    noun: str
    office_en: str
    office_de: str
    datasets: tuple[int, ...]


CANTONS = {
    "TG": Canton(
        "CH-TG",
        "thurgau",
        "Thurgau",
        "Member of the Government Council of Thurgau",
        "Mitglied des Regierungsrates des Kantons Thurgau",
        DATASETS,
    ),
}

METHOD = (
    "Counted per political municipality and published by the Staatskanzlei of the canton as open data. Registered "
    "voters are the municipal roll, ballots cast are the ballot papers handed in (wzabgegeben), and valid ballots are "
    "the counted ballots (wzgueltig). Each voter has as many votes as the government has seats, so candidate votes "
    "are votes for names on a ballot, and scattered votes (Vereinzelte) are votes for persons who did not stand. "
    "Blank lines and invalid votes within a counted ballot are not stored. The first round is shown; the canton "
    "publishes no second-round data in this dataset."
)

RULES_BLOCK = {"formula": "block"}
SYSTEM_NOTE = (
    "Majority vote in one round with a second round when seats stay open: at the first round a candidate needs the "
    "absolute majority (half of the valid votes divided by the number of seats, rounded up); at the second round the "
    "candidates with the most votes win the remaining seats."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def url(number: int) -> str:
    return PORTAL.format(number=number)


def fetch() -> None:
    for canton in CANTONS.values():
        for number in canton.datasets:
            for attempt in range(5):
                try:
                    raw.fetch(SOURCE, url(number), suffix=".csv")
                    break
                except httpx.HTTPError:
                    if attempt == 4:
                        raise
                    time.sleep(5 * (attempt + 1))


def table(number: int) -> list[dict[str, str]]:
    text = raw.cached(SOURCE, url(number)).path.read_bytes().decode("utf-8-sig")
    rows = csv.DictReader(io.StringIO(text), delimiter=";")
    return [{name.lower(): (value or "").strip() for name, value in row.items()} for row in rows]


def day(text: str) -> date:
    if "." in text:
        d, m, y = text.split(".")
        return date(int(y), int(m), int(d))
    return date.fromisoformat(text)


@dataclass(frozen=True)
class Reference:
    polity: str
    area: str
    units: dict[str, str]


@cache
def reference(iso: str) -> Reference:
    polities = pl.read_parquet(REFERENCE / "polity.parquet").filter(pl.col("iso_code") == iso)
    areas = pl.read_parquet(REFERENCE / "area.parquet").filter(pl.col("scheme_code") == "ch.canton")
    codes = pl.read_parquet(REFERENCE / "area_code.parquet")
    area = areas.join(codes, left_on="id", right_on="area_id").filter(pl.col("code") == iso.split("-")[1])
    units = pl.read_parquet(REFERENCE / "reporting_unit.parquet").filter(
        (pl.col("scheme_code") == "ch.commune") & (pl.col("pipeline") == "ch.bfs") & pl.col("valid_from").is_null()
    )
    return Reference(
        str(polities["id"].item()),
        str(area["id"].item()),
        {str(code): str(id) for code, id in zip(units["code"], units["id"], strict=True)},
    )


def packages() -> list[str]:
    keys = [f"{SOURCE}/registry"]
    for code, canton in CANTONS.items():
        for number in canton.datasets:
            keys.append(f"{SOURCE}/{code.lower()}-{_stamp(number)}")
    return keys


@cache
def _stamp(number: int) -> str:
    return str(day(table(number)[0]["wahlsonntag"]))


def parse() -> None:
    registry().write()
    for code, canton in CANTONS.items():
        for number in canton.datasets:
            government(code, number).write()


def emit() -> None:
    package.run("load", packages())


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "kantonsregierung", "majorz")


def office(code: str) -> str:
    return key("office", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Cantonal government offices and the majority-vote system for the Regierungsrat elections",
    )
    for code, canton in CANTONS.items():
        p.external.add(reference(canton.iso).polity)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Staatskanzlei des Kantons Thurgau",
        title="Regierungsratswahlen Kanton Thurgau (Open Government Data)",
        url="https://data.tg.ch/",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Kanton Thurgau, Staatskanzlei",
    )
    for canton in CANTONS.values():
        for number in canton.datasets:
            p.artifact(raw.cached(SOURCE, url(number)), SOURCE_ID)
    for code, canton in CANTONS.items():
        id = office(code)
        p.add("office", id=id, polity_id=reference(canton.iso).polity, kind="other")
        p.name(id, canton.office_en, "en", "official")
        p.name(id, canton.office_de, "de", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=reference("CH-TG").polity,
        valid_from=date(1900, 1, 1),
        valid_to=None,
        rules=RULES_BLOCK,
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def person_name(row: dict[str, str], index: int) -> str:
    return f"{row.get(f'kandvorname_{index}', '')} {row[f'kandname_{index}']}".strip()


def government(code: str, number: int) -> Package:
    canton = CANTONS[code]
    ref = reference(canton.iso)
    rows = [row for row in table(number) if row["bfs_nr_gemeinde"].zfill(4) not in NO_UNIT]
    held = day(rows[0]["wahlsonntag"])
    seats = int(rows[0]["anzmandate"])
    by_election = rows[0]["geschaeft"] in BY_ELECTION or seats == 1
    stamp = f"{code.lower()}-{held}"
    p = Package(
        f"{SOURCE}/{stamp}",
        SOURCE,
        f"The {held} {canton.noun} {'by-election' if by_election else 'general election'} for the cantonal government, "
        "by municipality",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {ref.polity, ref.area, office(code)}
    if "stimmen_ungueltig" in rows[0] and seats == 1 and held.year >= 2025:
        p.accept(
            "accounting.votes_valid",
            "Candidate and scattered votes fall short of the valid ballots by exactly the invalid votes "
            "(Stimmen_Ungueltig) the source reports in each municipality.",
        )
    p.uses(raw.cached(SOURCE, url(number)))
    election = key("election", stamp)
    p.add(
        "election",
        id=election,
        polity_id=ref.polity,
        kind="by_election" if by_election else "general",
        date_from=held,
        date_to=held,
        status="held",
    )
    path = f"ch/{held}-{canton.slug}-government"
    p.add("slug", path=path, entity_id=election, is_current=True)
    label = "by-election" if by_election else "election"
    p.name(election, f"{held.year} {canton.noun} cantonal government {label}", "en", "official")
    p.name(election, f"Regierungsratswahl {canton.noun} {held.year}", "de", "native", False)
    contest = key("contest", stamp)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=office(code),
        district_area_id=ref.area,
        magnitude=seats,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{held.year} {canton.noun} cantonal government {label}", "en", "official")
    p.add("slug", path=f"{path}/members", entity_id=contest, is_current=True)
    result_set = key("result_set", stamp)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")

    options: dict[int, str] = {}
    first = rows[0]
    index = 1
    while f"kandname_{index}" in first:
        if first[f"kandname_{index}"] and f"stimmen_{index}" in first:
            name = person_name(first, index)
            person = key("person", stamp, index)
            p.add("person", id=person)
            p.name(person, name, "de", "official")
            option = options[index] = key("option", stamp, index)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=int(first[f"kandid_{index}"] or index),
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", stamp, index),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
            )
        index += 1
    scatter = key("option", stamp, "scatter") if "stimmen_vereinzelt" in first else None
    if scatter:
        p.add("contest_option", id=scatter, contest_id=contest, kind="others", ballot_label="Vereinzelte")

    accounting = (
        (2, "stimmberechtigte"),
        (3, "wzabgegeben"),
        (4, "wzgueltig"),
        (5, "wzungueltig"),
        (6, "wzleer"),
    )
    seen: set[str] = set()
    for row in rows:
        commune = row["bfs_nr_gemeinde"].zfill(4)
        if commune in seen:
            raise ValueError(f"{stamp}: municipality {commune} twice")
        seen.add(commune)
        unit = ref.units[commune]
        p.external.add(unit)
        for measure, column in accounting:
            if row.get(column, "") != "":
                _value(p, result_set, unit, None, measure, int(row[column]))
        for index, option in options.items():
            _value(p, result_set, unit, option, 1, int(row[f"stimmen_{index}"] or 0))
        if scatter:
            _value(p, result_set, unit, scatter, 1, int(row["stimmen_vereinzelt"] or 0))
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )
