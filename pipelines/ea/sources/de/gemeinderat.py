"""Shared builder for the municipal council elections of the eastern German Länder.

Each Land has its own pipeline (`de.gemeinderat_bb`, `_st`, `_sn`, `_th`, `_mv`) that reads its statistical office's
files into `Vote` records and hands them to the builders here. A Land's packages are a `geo` package (the unit scheme
and the areas production does not hold), a `registry` package (municipality polities, council bodies, electoral
systems, parties, and the source), and one package per election day with one contest per municipality.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from functools import cache
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package
from ea.changeset import slug
from ea.package import Package
from ea.raw import Artifact
from ea.store import DATA

GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
STATES = {
    "08": ("DE-BW", "01a13bb5-c563-741b-83f7-4b7aeca76288", "01a13bb5-c563-741b-83f7-4b7b14460236"),
    "12": ("DE-BB", "01a13bb5-c563-741b-83f7-4b829f1533bb", "01a13bb5-c563-741b-83f7-4b836b8e1aa9"),
    "13": ("DE-MV", "01a13bb5-c563-741b-83f7-4b845c3bd9f1", "01a13bb5-c563-741b-83f7-4b85f8bf5685"),
    "14": ("DE-SN", "01a13bb5-c563-741b-83f7-4b86faa1ed93", "01a13bb5-c563-741b-83f7-4b87a8e71a4b"),
    "15": ("DE-ST", "01a13bb5-c563-741b-83f7-4b88a681db04", "01a13bb5-c563-741b-83f7-4b89e23fbff0"),
    "16": ("DE-TH", "01a13bb5-c563-741b-83f7-4b8a6fb78423", "01a13bb5-c563-741b-83f7-4b8b5fb2222a"),
}
KINDS = {"P": "party", "PV": "party", "LV": "bloc", "WG": "independent_group"}
# Entries that name no party: an independent candidate, or all voters' groups or all independents of a municipality
# printed as one sum, a category of local lists, the votes of a council elected by majority vote, or votes the source
# does not split by list.
NO_PARTY = {"EB", "OW", "OE", "OS", "OC", "MW", "NA"}
OPTION_KINDS = {
    "EB": "candidate",
    "OW": "others",
    "OE": "others",
    "OS": "others",
    "OC": "others",
    "MW": "others",
    "NA": "others",
}


class Entry(NamedTuple):
    """One list or candidacy on a municipality's ballot, as printed."""

    label: str
    kind: str  # P party, PV political association, LV joint list, WG voters' group, EB independent candidate, OW/OE/OS sums of voters' groups, independents, other parties
    votes: int | None
    seats: int | None
    party: str | None = None  # a party production already holds, used instead of minting one


class Result(NamedTuple):
    ags: str
    name: str
    registered: int | None
    voters: int | None
    invalid: int | None
    valid_ballots: int | None
    seats: int | None
    entries: list[Entry]


class Vote(NamedTuple):
    day: date
    formula: str
    results: list[Result]
    files: list[Artifact]
    note: str | None = None


class Land(NamedTuple):
    source: str  # the registry name, such as de.gemeinderat_bb
    code: str  # the Land's two-digit AGS prefix
    slug: str  # slug stem, such as brandenburg
    name: str  # English name
    body: str  # the council's German name as the Land's law has it
    publisher: str
    title: str
    url: str
    license: str
    license_url: str
    attribution: str
    method: str
    system_note: str


def key(land: Land, kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([land.source, kind, *(str(part) for part in parts)])))


def polity(land: Land, ags: str) -> str:
    return key(land, "polity", ags)


def iso(land: Land) -> str:
    return STATES[land.code][0]


def state(land: Land) -> str:
    return STATES[land.code][1]


def land_area(land: Land) -> str:
    return STATES[land.code][2]


def scheme(land: Land) -> str:
    return land.source


@cache
def _held() -> dict[str, str]:
    """Production's municipality areas by AGS, from the reference partition."""
    folder = DATA / "reference" / "de"
    areas = pl.read_parquet(folder / "area.parquet").filter(pl.col("scheme_code") == "de.gemeinde")
    codes = pl.read_parquet(folder / "area_code.parquet")
    found = areas.join(codes, left_on="id", right_on="area_id")
    return {str(row["code"]): str(row["id"]) for row in found.iter_rows(named=True)}


@cache
def _kreise() -> dict[str, str]:
    folder = DATA / "reference" / "de"
    areas = pl.read_parquet(folder / "area.parquet").filter(pl.col("scheme_code") == "de.kreis")
    codes = pl.read_parquet(folder / "area_code.parquet")
    found = areas.join(codes, left_on="id", right_on="area_id")
    return {str(row["code"]): str(row["id"]) for row in found.iter_rows(named=True)}


def area(land: Land, ags: str) -> str:
    """The area of a municipality: production's where it holds one, else this Land's own."""
    return _held().get(ags) or key(land, "area", ags)


def places(votes: Iterable[Vote]) -> dict[str, str]:
    """Every municipality the elections cover, by AGS, with its latest printed name."""
    names: dict[str, str] = {}
    for vote in sorted(votes, key=lambda item: item.day):
        for result in vote.results:
            names[result.ags] = result.name
    return names


def geo(land: Land, votes: Iterable[Vote]) -> Package:
    votes = list(votes)
    p = Package(
        f"{land.source}/geo",
        land.source,
        f"The reporting scheme of {land.name}'s municipal council elections and the municipalities the archive does "
        "not yet hold",
    )
    p.external |= {GERMANY, land_area(land), "de.gemeinde", *_kreise().values()}
    p.add(
        "scheme",
        code=scheme(land),
        polity_id=GERMANY,
        kind="precinct",
        rank=45,
        noun=f"{land.name} municipal council electorate",
        code_system=None,
    )
    for ags, name in sorted(places(votes).items()):
        if ags in _held():
            p.external.add(_held()[ags])
            continue
        id = area(land, ags)
        p.add("area", id=id, scheme_code="de.gemeinde")
        p.add("area_code", area_id=id, code=ags)
        p.add("area_parent", area_id=id, parent_id=_kreise().get(ags[:5], land_area(land)))
        p.name(id, name, "de", "official")
    return p


def registry(land: Land, votes: Iterable[Vote]) -> Package:
    votes = list(votes)
    p = Package(
        f"{land.source}/registry",
        land.source,
        f"The municipalities, council bodies, electoral systems, and parties of {land.name}'s municipal council "
        "elections",
        depends=[f"{land.source}/geo"],
    )
    p.external |= {GERMANY, state(land)}
    names = places(votes)
    p.external |= {_held()[ags] for ags in names if ags in _held()}
    source = key(land, "source")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=land.publisher,
        title=land.title,
        url=land.url,
        license=land.license,
        license_url=land.license_url,
        attribution=land.attribution,
    )
    cited: set[str] = set()
    for vote in votes:
        for file in vote.files:
            if file.storage_key not in cited:
                cited.add(file.storage_key)
                p.artifact(file, source)
    for ags, name in sorted(names.items()):
        id = polity(land, ags)
        p.add("polity", id=id, kind="municipality", iso_code=None)
        p.add("polity_parent", polity_id=id, parent_id=state(land))
        p.add("polity_territory", polity_id=id, area_id=area(land, ags))
        p.name(id, name, "de", "official")
        p.add("external_id", entity_id=id, scheme="de.ags", value=ags)
        body = key(land, "body", ags)
        p.add("body", id=body, polity_id=id, kind="council")
        p.name(body, f"{land.body} {name}", "de", "official")
        p.name(body, f"Municipal council of {name}", "en", "official", False)
    for formula in sorted({vote.formula for vote in votes}):
        system = key(land, "electoral_system", formula)
        p.add(
            "electoral_system",
            id=system,
            polity_id=state(land),
            valid_from=None,
            valid_to=None,
            rules={"formula": formula},
            rules_version=1,
        )
        p.add(
            "note",
            id=key(land, "note", system),
            entity_id=system,
            kind="methodology",
            body_md=land.system_note,
            lang="en",
        )
    labels: dict[str, tuple[str, str]] = {}
    for vote in votes:
        for result in vote.results:
            for entry in result.entries:
                if entry.kind in NO_PARTY or entry.party:
                    continue
                labels.setdefault(party_key(entry, result.ags), (entry.label, entry.kind))
    for folded, (text, kind) in sorted(labels.items()):
        id = party(land, folded)
        p.add("party", id=id, kind=KINDS[kind], polity_id=state(land))
        p.add("external_id", entity_id=id, scheme=f"{land.source}.list", value=folded)
        p.name(id, text, "de", "official")
    return p


def label(text: str) -> str:
    return " ".join(text.split())


def party_key(entry: Entry, ags: str) -> str:
    """Parties and joint lists are named across the Land; a voters' group is local to its municipality."""
    text = label(entry.label).casefold()
    return f"{ags}:{text}" if entry.kind == "WG" else text


def party(land: Land, folded: str) -> str:
    return key(land, "party", folded)


def election(land: Land, vote: Vote, name: str) -> Package:
    year = vote.day.year
    p = Package(
        f"{land.source}/{name}",
        land.source,
        f"The {vote.day:%B} {vote.day.day}, {year} municipal council elections in {land.name}, one contest per "
        "municipality",
        depends=[f"{land.source}/geo", f"{land.source}/registry"],
    )
    p.external |= {GERMANY, state(land)}
    source = key(land, "source")
    p.external.add(source)
    system = key(land, "electoral_system", vote.formula)
    p.external.add(system)
    for file in vote.files:
        p.uses(file)
    election_id = key(land, "election", name)
    p.add(
        "election",
        id=election_id,
        polity_id=state(land),
        kind="local",
        date_from=vote.day,
        date_to=vote.day,
        status="held",
    )
    p.name(election_id, f"{year} {land.name} municipal council elections", "en", "official")
    p.name(election_id, f"Gemeinderatswahlen {land.name} {year}", "de", "native", False)
    path = f"de/{vote.day}-{land.slug}-municipal-councils"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    taken: set[str] = set()
    parties: set[str] = set()
    if any(result.valid_ballots is not None for result in vote.results):
        p.accept(
            "accounting.votes_valid",
            "each voter has as many votes as the council has seats, so a municipality's list votes are several times "
            "its valid ballots, which the source prints separately",
        )
    unbalanced = [
        result.ags
        for result in vote.results
        if result.voters is not None
        and result.valid_ballots is not None
        and result.invalid is not None
        and result.valid_ballots + result.invalid != result.voters
    ]
    if unbalanced:
        p.accept(
            "accounting.ballots_sum",
            f"the source's own figures for {len(unbalanced)} municipalit{'y' if len(unbalanced) == 1 else 'ies'} "
            "(" + ", ".join(unbalanced[:5]) + ") do not add up: valid plus invalid ballots differ from the voters",
        )
    for result in vote.results:
        ags = result.ags
        p.external |= {polity(land, ags), key(land, "body", ags)}
        if ags in _held():
            p.external.add(_held()[ags])
        else:
            p.external.add(area(land, ags))
        contest = key(land, "contest", name, ags)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=key(land, "body", ags),
            district_area_id=area(land, ags),
            magnitude=result.seats,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {result.name} municipal council election", "en", "official")
        p.name(contest, f"Gemeinderatswahl {result.name} {year}", "de", "native", False)
        tail = slug(result.name)
        if tail in taken:
            tail = f"{tail}-{ags}"
        taken.add(tail)
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        unit = key(land, "unit", name, ags)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=GERMANY,
            scheme_code=scheme(land),
            code=f"{vote.day}:{ags}",
            area_relation="equals",
            area_id=area(land, ags),
            kind="standard",
        )
        result_set = key(land, "result_set", name, ags)
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
            id=key(land, "note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=land.method + (f" {vote.note}" if vote.note else ""),
            lang="en",
        )
        for measure, value in (
            (2, result.registered),
            (3, result.voters),
            (4, result.valid_ballots),
            (5, result.invalid),
        ):
            if value is not None:
                _value(p, result_set, unit, None, measure, value)
        for position, entry in enumerate(result.entries, start=1):
            if entry.votes is None and not entry.seats:
                continue
            option = key(land, "option", name, ags, position)
            partyless = entry.kind in NO_PARTY
            folded = party_key(entry, ags)
            if entry.party and not partyless:
                p.external.add(entry.party)
            elif not partyless:
                parties.add(folded)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=OPTION_KINDS.get(entry.kind, "list"),
                party_id=None if partyless else entry.party or party(land, folded),
                ballot_position=position,
                ballot_label=label(entry.label),
            )
            if entry.votes is not None:
                _value(p, result_set, unit, option, 1, entry.votes)
            if entry.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry.seats)
    p.external |= {party(land, folded) for folded in parties}
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


def count(text: object) -> int | None:
    """A printed whole number, or None for an empty cell, a dash, or an x."""
    if text is None:
        return None
    cell = str(text).strip().replace(".", "").replace("\xa0", "")
    if cell in ("", "-", "x", "X", "…", "."):
        return None
    return int(float(cell))
