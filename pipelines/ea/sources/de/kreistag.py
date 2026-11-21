"""Kreistag elections in Germany's Landkreise: Bavaria's election of 8 March 2026.

The Bayerisches Landesamt für Statistik prints the final results of the Kreistag elections in its Statistische
Berichte (B7350C), for each Landkreis: registered voters, voters, invalid ballots, and, for every nomination, the
votes, the ballot-equivalent (weighted) votes, and the seats. Each Landkreis is one contest.
"""

import importlib
import re
import subprocess
from collections.abc import Callable
from datetime import date
from functools import cache
from typing import NamedTuple

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "de.kreistag"
BY_BASE = "https://www.statistik.bayern.de/mam/produkte/veroffentlichungen/statistische_berichte"
BY_FINAL = f"{BY_BASE}/b7350c_202651.pdf"
BY_PAGE = "https://www.statistik.bayern.de/wahlen/kommunalwahlen/str/index.html"
DAY = date(2026, 3, 8)
GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
BAVARIA = "01a13bb5-c563-741b-83f7-4b7cdad043bd"
REFERENCE = DATA / "reference" / "de"
SCHEME = "de.kreistagswahl"
SYSTEM_KEY = "sainte_lague_schepers"
PARTIES = {
    "csu": "CSU",
    "afd": "AfD",
    "grüne": "GRÜNE",
    "spd": "SPD",
    "freie wähler": "FREIE WÄHLER",
    "ödp": "ÖDP",
    "fdp": "FDP",
    "die linke": "Die Linke",
    "bp": "BP",
    "die partei": "Die PARTEI",
    "rep": "REP",
    "volt": "Volt",
    "diebasis": "dieBasis",
    "bsw": "BSW",
    "piraten": "PIRATEN",
    "npd": "NPD",
    "die humanisten": "Die Humanisten",
    "team todenhöfer": "Team Todenhöfer",
}
NOTE = (
    "Counted by the Gemeinde and Landkreis election authorities and compiled by the Bayerisches Landesamt für "
    "Statistik. Each voter has as many votes as the Kreistag has seats and may give up to three to one candidate "
    "(cumulating) or spread them across nominations (panachage), so a nomination's votes are not ballots. Registered "
    "voters are those entitled to vote, voters are those who took part, and invalid ballots are the ballot papers "
    "that were invalid. The source prints the ballot-equivalent (weighted) votes of each nomination but no count of "
    "valid ballots, and none is stored."
)
NOTE_RHON = " The final results include the repeat election (Nachwahl) held in the Landkreis on 5 July 2026."
SYSTEM_NOTE = (
    "Kreistag seats are shared among the nominations by the Sainte-Laguë/Schepers divisor method on their votes, "
    "which reproduces the declared seats in all 71 Landkreise in 2026. There is no threshold."
)
LICENSE = "Creative Commons Namensnennung 4.0 International"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/deed.de"
PUBLISHER = "Bayerisches Landesamt für Statistik"
HEAD = re.compile(r"^(\d{3}) Landkreis (.+?)\s*$")
TOKEN = re.compile(r"^(?:X|-|\.|\d[\d.]*|\d+,\d+)$")


class Row(NamedTuple):
    label: str
    votes: int | None
    ballots: int | None
    seats: int | None
    group: bool = False


class Kreis(NamedTuple):
    number: str
    name: str
    registered: int | None
    voters: int | None
    invalid: int | None
    seats: int
    rows: list[Row]
    valid: int | None = None


def number(token: str) -> int | None:
    if token in ("X", ".", ""):
        return None
    if token == "-":
        return 0
    return int(token.replace(".", ""))


def text() -> list[str]:
    path = raw.cached(SOURCE, BY_FINAL).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    return done.stdout.splitlines()


def split(line: str) -> tuple[str, list[str]] | None:
    """A table row as its label and its eight figure cells."""
    parts = re.split(r"\s{2,}", line.strip())
    if len(parts) < 2:
        return None
    label, rest = parts[0], " ".join(parts[1:]).split()
    if len(rest) != 8 or not all(TOKEN.match(cell) for cell in rest):
        return None
    return label, rest


@cache
def kreise() -> dict[str, Kreis]:
    lines = text()
    start = next(i for i, line in enumerate(lines) if "2. Stimmen und Sitze bei der Wahl der Stadträte" in line)
    found: dict[str, Kreis] = {}
    head: tuple[str, str] | None = None
    figures: dict[str, list[str]] = {}
    rows: list[Row] = []

    def close() -> None:
        if head is None:
            return
        registered = number(figures["Stimmberechtigte"][1])
        voters = number(figures["Wähler / Wahlbeteiligung"][1])
        invalid = number(figures["Ungültige Stimmzettel"][1])
        seats = number(figures["Wahlvorschläge zusammen"][3])
        if registered is None or voters is None or invalid is None or seats is None:
            raise ValueError(f"incomplete block for {head}")
        found["09" + head[0]] = Kreis(head[0], head[1], registered, voters, invalid, seats, rows[:])

    last = -1
    for at, line in enumerate(lines[start:], start):
        match = HEAD.match(line)
        if match or re.match(r"^\d{3} Kreisfreie Stadt", line):
            close()
            head = (match.group(1), match.group(2)) if match else None
            figures, rows = {}, []
            continue
        if head is None:
            continue
        item = split(line)
        if item is None:
            if at == last + 1 and rows and line.startswith(" ") and line.strip():
                tail = rows[-1]
                rows[-1] = Row(f"{tail.label} {line.strip()}", tail.votes, tail.ballots, tail.seats)
                last = at
            continue
        label, cells = item
        if label in (
            "Stimmberechtigte",
            "Wähler / Wahlbeteiligung",
            "Ungültige Stimmzettel",
            "Wahlvorschläge zusammen",
        ):
            figures[label] = cells
        else:
            rows.append(Row(label, number(cells[0]), number(cells[1]), number(cells[3])))
            last = at
    close()
    return found


@cache
def areas() -> dict[str, str]:
    """The id production holds for each Kreis, by its AGS."""
    found = pl.read_parquet(REFERENCE / "area.parquet").join(
        pl.read_parquet(REFERENCE / "area_code.parquet"), left_on="id", right_on="area_id"
    )
    found = found.filter((pl.col("scheme_code") == "de.kreis") & pl.col("valid_to").is_null())
    return dict(zip(found["code"].to_list(), found["id"].to_list(), strict=True))


def party_name(label: str) -> str | None:
    return PARTIES.get(label.casefold())


def short(name: str) -> str:
    return name.strip()


def fetch() -> None:
    raw.fetch(SOURCE, BY_FINAL, suffix=".pdf")
    for land in lands():
        land.fetch()


def registry() -> Package:
    pool = kreise()
    known = areas()
    p = Package(
        f"{SOURCE}/registry-by",
        SOURCE,
        "The Landkreise of Bavaria as county polities with their Kreistage, the seat-allocation rule, and the "
        "nominations of the 2026 Kreistag elections",
    )
    p.external |= {GERMANY, BAVARIA, *(known[ags] for ags in pool)}
    source = p.id("source", "by")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title="Kommunalwahlen in Bayern am 8. März 2026: Abschließende Ergebnisse",
        url=BY_PAGE,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution=PUBLISHER,
    )
    p.artifact(raw.cached(SOURCE, BY_FINAL), source)
    p.add("scheme", code=SCHEME, polity_id=BAVARIA, kind="electoral_district", rank=1, noun="Landkreis electorate")
    system = p.id("electoral_system", SYSTEM_KEY)
    p.add(
        "electoral_system",
        id=system,
        polity_id=BAVARIA,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        rules={"formula": "sainte_lague_schepers"},
        rules_version=1,
    )
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for ags, kreis in sorted(pool.items()):
        polity = p.id("polity", ags)
        p.add("polity", id=polity, kind="county", iso_code=None)
        p.add("polity_parent", polity_id=polity, parent_id=BAVARIA, valid_from=None, valid_to=None)
        p.add("polity_territory", polity_id=polity, area_id=known[ags], valid_from=None, valid_to=None)
        p.name(polity, f"Landkreis {short(kreis.name)}", "de", "official")
        p.name(polity, f"{short(kreis.name)} District", "en", "official", False)
        body = p.id("body", ags)
        p.add("body", id=body, polity_id=polity, kind="council")
        p.name(body, f"Kreistag {short(kreis.name)}", "de", "official")
        p.name(body, f"District Council of {short(kreis.name)}", "en", "official", False)
    seen: dict[str, tuple[str, str]] = {}
    for ags, kreis in pool.items():
        for row in kreis.rows:
            base = party_name(row.label)
            if base:
                seen.setdefault(base.casefold(), (base, "party"))
            else:
                seen.setdefault(f"{ags}:{row.label.casefold()}", (row.label, "independent_group"))
    for folded, (label, kind) in sorted(seen.items()):
        id = p.id("party", folded)
        p.add("party", id=id, kind=kind, polity_id=BAVARIA)
        p.add("external_id", entity_id=id, scheme="de.kreistag.group", value=f"by:{folded}")
        p.name(id, label, "de", "official")
    return p


def party_key(ags: str, label: str) -> str:
    base = party_name(label)
    return base.casefold() if base else f"{ags}:{label.casefold()}"


def election() -> Package:
    pool = kreise()
    known = areas()
    p = Package(
        f"{SOURCE}/by-2026",
        SOURCE,
        "The Kreistag elections of 8 March 2026 in the 71 Landkreise of Bavaria, one contest per Landkreis",
        depends=[f"{SOURCE}/registry-by"],
    )
    p.external |= {GERMANY, BAVARIA}
    p.external |= {known[ags] for ags in pool}
    p.external |= {p.id("polity", ags) for ags in pool}
    p.external |= {p.id("body", ags) for ags in pool}
    p.external.add(p.id("electoral_system", SYSTEM_KEY))
    p.external.add(p.id("source", "by"))
    p.external |= {p.id("party", party_key(ags, row.label)) for ags, k in pool.items() for row in k.rows}
    p.uses(raw.cached(SOURCE, BY_FINAL))
    source = p.id("source", "by")
    election_id = p.id("election", DAY.year)
    p.add("election", id=election_id, polity_id=BAVARIA, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(election_id, "March 8, 2026 Bavaria district council (Kreistag) elections", "en", "official")
    p.name(election_id, "Kreistagswahlen in Bayern am 08.03.2026", "de", "native", False)
    path = f"de/{DAY}-bayern-kreistagswahlen"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    for ags, kreis in sorted(pool.items()):
        name = short(kreis.name)
        contest = p.id("contest", ags)
        unit = p.id("unit", ags)
        result_set = p.id("result_set", ags)
        body = p.id("body", ags)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body,
            district_area_id=known[ags],
            magnitude=kreis.seats,
            round_number=1,
            electoral_system_id=p.id("electoral_system", SYSTEM_KEY),
            default_result_kind="counted",
        )
        p.name(contest, f"2026 {name} Kreistag election", "en", "official")
        p.name(contest, f"Kreistagswahl {name} 2026", "de", "native", False)
        p.add("slug", path=f"{path}/{slug(name)}-{ags[2:]}", entity_id=contest, is_current=True)
        p.add("body_size", body_id=body, seats=kreis.seats, valid_from=DAY, valid_to=None)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=p.id("polity", ags),
            scheme_code=SCHEME,
            code=f"{DAY}:{ags}",
            area_relation="equals",
            area_id=known[ags],
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
        body_md = NOTE + (NOTE_RHON if ags == "09673" else "")
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=body_md, lang="en")
        for measure, value in ((2, kreis.registered), (3, kreis.voters), (5, kreis.invalid)):
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
        ranked = sorted(kreis.rows, key=lambda r: (-(r.votes or 0), -(r.seats or 0), r.label))
        for position, row in enumerate(ranked, start=1):
            option = p.id("option", ags, row.label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=p.id("party", party_key(ags, row.label)),
                ballot_position=position,
                ballot_label=row.label,
            )
            if row.votes is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=row.votes,
                )
            if row.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=16,
                    count_round=0,
                    flags=0,
                    value=row.seats,
                )
    return p


def packages() -> list[str]:
    keys = [f"{SOURCE}/registry-by", f"{SOURCE}/by-2026"]
    for land in lands():
        keys.append(f"{SOURCE}/registry-{land.key}")
        keys.extend(f"{SOURCE}/{land.key}-{e.year}" for e in land.editions())
    return keys


def parse() -> None:
    registry().write()
    election().write()
    for land in lands():
        land_registry(land).write()
        for edition in land.editions():
            land_election(land, edition).write()


def emit() -> None:
    package.run("load", packages())


class Edition(NamedTuple):
    """One Kreistag election of a Land: the day, the source files it was read from, and its results per Kreis."""

    year: int
    day: date
    page: str
    title: str
    files: list[str]
    kreise: Callable[[], dict[str, Kreis]]
    formula: str | None = None
    note: str = ""


class Land(NamedTuple):
    key: str
    polity: str
    name: str
    publisher: str
    license: str
    license_url: str | None
    editions: Callable[[], list[Edition]]
    fetch: Callable[[], None]
    methodology: str = ""


def lands() -> list[Land]:
    from ea.sources.de import kreistag_ni, kreistag_nw, kreistag_sh

    return [kreistag_nw.LAND, kreistag_ni.LAND, kreistag_sh.LAND]


def land_party_key(land: Land, ags: str, row: Row) -> str:
    return f"{land.key}:{ags}:{row.label.casefold()}" if row.group else f"{land.key}:{row.label.casefold()}"


def land_pool(land: Land) -> dict[str, Kreis]:
    """Every Kreis that held a Kreistag election in any edition, with its latest name."""
    pool: dict[str, Kreis] = {}
    for edition in sorted(land.editions(), key=lambda e: e.year):
        pool |= edition.kreise()
    return pool


def land_registry(land: Land) -> Package:
    pool = land_pool(land)
    known = areas()
    p = Package(
        f"{SOURCE}/registry-{land.key}",
        SOURCE,
        f"The Kreise of {land.name} as county polities with their Kreistage, and the nominations of their Kreistag elections",
    )
    p.external |= {GERMANY, land.polity, *(known[ags] for ags in pool)}
    scheme = f"{SCHEME}_{land.key}"
    p.add("scheme", code=scheme, polity_id=land.polity, kind="electoral_district", rank=1, noun="Kreis electorate")
    for edition in land.editions():
        source = p.id("source", land.key, edition.year)
        p.add(
            "source",
            id=source,
            kind="official_body",
            publisher=land.publisher,
            title=edition.title,
            url=edition.page,
            license=land.license,
            license_url=land.license_url,
            attribution=land.publisher,
        )
        for url in edition.files:
            p.artifact(raw.cached(SOURCE, url), source)
        if edition.formula:
            system = p.id("electoral_system", land.key, edition.year, edition.formula)
            p.add(
                "electoral_system",
                id=system,
                polity_id=land.polity,
                valid_from=edition.day,
                valid_to=edition.day,
                rules={"formula": edition.formula},
                rules_version=1,
            )
    for ags, kreis in sorted(pool.items()):
        polity = p.id("polity", ags)
        p.add("polity", id=polity, kind="county", iso_code=None)
        p.add("polity_parent", polity_id=polity, parent_id=land.polity, valid_from=None, valid_to=None)
        p.add("polity_territory", polity_id=polity, area_id=known[ags], valid_from=None, valid_to=None)
        p.name(polity, kreis.name, "de", "official")
        body = p.id("body", ags)
        p.add("body", id=body, polity_id=polity, kind="council")
        p.name(body, f"Kreistag {kreis.name}", "de", "official")
    seen: dict[str, tuple[str, str]] = {}
    for edition in land.editions():
        for ags, kreis in edition.kreise().items():
            for row in kreis.rows:
                key = land_party_key(land, ags, row)
                seen.setdefault(key, (row.label, "independent_group" if row.group else "party"))
    for key, (label, kind) in sorted(seen.items()):
        id = p.id("party", key)
        p.add("party", id=id, kind=kind, polity_id=land.polity)
        p.add("external_id", entity_id=id, scheme="de.kreistag.group", value=key)
        p.name(id, label, "de", "official")
    return p


def land_election(land: Land, edition: Edition) -> Package:
    pool = edition.kreise()
    known = areas()
    p = Package(
        f"{SOURCE}/{land.key}-{edition.year}",
        SOURCE,
        f"The Kreistag elections of {edition.day:%B} {edition.day.day}, {edition.year} in {land.name}, one contest per Kreis",
        depends=[f"{SOURCE}/registry-{land.key}"],
    )
    accepted = getattr(importlib.import_module(f"ea.sources.de.kreistag_{land.key}"), "ACCEPTED", {})
    if edition.year in accepted:
        p.accept(*accepted[edition.year])
    p.external |= {GERMANY, land.polity}
    p.external |= {known[ags] for ags in pool}
    p.external |= {p.id("polity", ags) for ags in pool}
    p.external |= {p.id("body", ags) for ags in pool}
    p.external.add(p.id("source", land.key, edition.year))
    system = p.id("electoral_system", land.key, edition.year, edition.formula) if edition.formula else None
    if system:
        p.external.add(system)
    p.external |= {p.id("party", land_party_key(land, ags, row)) for ags, k in pool.items() for row in k.rows}
    for url in edition.files:
        p.uses(raw.cached(SOURCE, url))
    source = p.id("source", land.key, edition.year)
    scheme = f"{SCHEME}_{land.key}"
    election_id = p.id("election", land.key, edition.year)
    day = edition.day
    p.add("election", id=election_id, polity_id=land.polity, kind="local", date_from=day, date_to=day, status="held")
    p.name(
        election_id,
        f"{day:%B} {day.day}, {day.year} {land.name} district council (Kreistag) elections",
        "en",
        "official",
    )
    p.name(election_id, f"Kreistagswahlen in {land.name} am {day:%d.%m.%Y}", "de", "native", False)
    path = f"de/{day}-{slug(land.name)}-kreistagswahlen"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    for ags, kreis in sorted(pool.items()):
        name = kreis.name
        contest = p.id("contest", edition.year, ags)
        unit = p.id("unit", edition.year, ags)
        result_set = p.id("result_set", edition.year, ags)
        body = p.id("body", ags)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body,
            district_area_id=known[ags],
            magnitude=kreis.seats,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"{day.year} {name} Kreistag election", "en", "official")
        p.name(contest, f"Kreistagswahl {name} {day.year}", "de", "native", False)
        p.add("slug", path=f"{path}/{slug(name)}-{ags[2:]}", entity_id=contest, is_current=True)
        p.add("body_size", body_id=body, seats=kreis.seats, valid_from=day, valid_to=None)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=p.id("polity", ags),
            scheme_code=scheme,
            code=f"{day}:{ags}",
            area_relation="equals",
            area_id=known[ags],
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
        if edition.note:
            p.add(
                "note",
                id=p.id("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=edition.note,
                lang="en",
            )
        figures = ((2, kreis.registered), (3, kreis.voters), (5, kreis.invalid), (4, kreis.valid))
        for measure, value in figures:
            if value is None:
                continue
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
        ranked = sorted(kreis.rows, key=lambda r: (-(r.votes or 0), -(r.seats or 0), r.label))
        for position, row in enumerate(ranked, start=1):
            option = p.id("option", edition.year, ags, row.label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=p.id("party", land_party_key(land, ags, row)),
                ballot_position=position,
                ballot_label=row.label,
            )
            if row.votes is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=row.votes,
                )
            if row.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=16,
                    count_round=0,
                    flags=0,
                    value=row.seats,
                )
    return p
