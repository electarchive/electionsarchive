"""Newfoundland and Labrador general elections and by-elections, by polling division, from Elections NL.

The authority publishes a report for each general election and each by-election since 1972 as a PDF. The reports from
the 2003 general election on carry a text layer with a poll-by-poll table for every electoral district, and the
parsers in `nl_modern`, `nl_mid`, and `nl_bye` read them. Earlier reports are scans, and the 2019 report's historical
statistics give the province-wide totals of the elections from 1949 to 1971.
"""

import functools
import io
import re
import zipfile
from datetime import date
from typing import Final
from uuid import uuid5

import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ca import nl_bye, nl_hist, nl_mid, nl_modern
from ea.sources.ca.nl_model import (
    BY_ELECTION_FILES,
    GENERALS,
    OFFICIAL,
    SOURCE,
    Candidate,
    District,
    Election,
    Poll,
)
from ea.sources.licenses import PUBLIC_RECORD

NEWFOUNDLAND_AND_LABRADOR: Final = "01a13fb6-eab7-7501-a0f9-48fff00ea550"
PROVINCE_AREA: Final = "01a13fb6-eab7-7501-a0f9-48f20475232b"
POLLS: Final = "https://www.elections.gov.nl.ca/files/NL-Polls-2019.zip"
GIS: Final = "https://www.elections.gov.nl.ca/resources/gis/"
PLAN_1996_FROM: Final = date(1996, 2, 22)
PLAN_2007_FROM: Final = date(2007, 10, 9)
PLAN_2015_FROM: Final = date(2015, 11, 30)
PARTY_NAMES: Final = {
    "LIB": "Liberal Party of Newfoundland and Labrador",
    "PC": "Progressive Conservative Party of Newfoundland and Labrador",
    "NDP": "New Democratic Party of Newfoundland and Labrador",
    "NL ALL": "NL Alliance",
    "NLP": "Newfoundland and Labrador Party",
    "LAB": "The Labrador Party",
    "CPC": "Canadian Party of Newfoundland and Labrador",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ENL = key("source", "elections-nl")
BOUNDARY_SOURCE = key("source", "polling-divisions")
HOUSE = key("body", "house_of_assembly")
FPTP = key("electoral_system", "fptp")
SET_1996 = key("boundary_set", "1996")
SET_2007 = key("boundary_set", "2007")
SET_2015 = key("boundary_set", "2015")


def fetch() -> None:
    raw.fetch(SOURCE, POLLS, suffix=".zip")
    for file in (*GENERALS.values(), *BY_ELECTION_FILES):
        raw.fetch(SOURCE, file, suffix=".pdf")


@functools.cache
def generals() -> dict[int, Election]:
    return {**nl_mid.generals(), **nl_modern.generals()}


@functools.cache
def by_elections() -> dict[date, Election]:
    return nl_bye.by_elections()


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/ge-{year}" for year in sorted([*nl_hist.YEARS, *generals()])),
        *(f"{SOURCE}/be-{day}" for day in sorted(by_elections())),
    ]


def plan(day: date) -> str:
    if day >= PLAN_2015_FROM:
        return "2015"
    return "2007" if day >= PLAN_2007_FROM else "1996"


def area_of(day: date, name: str) -> str:
    return key("area", plan(day), changeset.slug(name))


def poll_unit(day: date, district: str, code: str) -> str:
    return key("unit", day, changeset.slug(district), code)


def _upper(word: str) -> bool:
    return len(word) > 1 and word.upper() == word and any(c.isalpha() for c in word)


def _cased(word: str) -> str:
    parts = []
    for piece in re.split(r"([-'’.])", word):
        text = piece.capitalize() if piece.isalpha() else piece
        if text.startswith("Mc") or text.startswith("Mac") and len(text) > 5:
            n = 2 if text.startswith("Mc") else 3
            text = text[:n] + text[n:].capitalize()
        parts.append(text)
    return "".join(parts)


def proper(printed: str) -> str:
    """A name in reading order with its capitals restored: `McKEOWN, Kelly` is Kelly McKeown."""
    printed = re.sub(r"\s+", " ", printed).strip()
    if "," in printed:
        surname, _, given = printed.partition(",")
        printed = f"{given.strip()} {surname.strip()}"
    return " ".join(_cased(word) if _upper(word) and "." not in word else word for word in printed.split())


def party_name(code: str, legend: dict[str, str]) -> str:
    return PARTY_NAMES.get(code) or legend.get(code) or code


def _parties(elections: list[Election]) -> dict[str, str]:
    found: dict[str, str] = {}
    for election in elections:
        for district in election.districts:
            for candidate in district.candidates:
                if candidate.party and candidate.party != "N-A":
                    found.setdefault(candidate.party, party_name(candidate.party, election.legend))
    return found


def _all() -> list[Election]:
    return [*generals().values(), *by_elections().values()]


def _plan_names(which: str) -> dict[str, tuple[str, str | None]]:
    """The districts of one plan: slug -> (name, number)."""
    names: dict[str, tuple[str, str | None]] = {}
    for election in _all():
        if plan(election.day) != which:
            continue
        for district in election.districts:
            number = None if district.number is None else str(district.number)
            known = names.get(changeset.slug(district.name))
            names[changeset.slug(district.name)] = (district.name, number or (known[1] if known else None))
    return names


def _outline(p: Package, area: str, shape: shapely.Geometry, since: date) -> None:
    shape = shapely.make_valid(shape)
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=since,
        valid_to=None,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=BOUNDARY_SOURCE,
    )


def _districts_2015() -> dict[int, shapely.Geometry]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, POLLS).path)
    parts = {
        name.rsplit(".", 1)[1]: archive.read(name)
        for name in archive.namelist()
        if name.endswith(("shp", "dbf", "shx"))
    }
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), dbf=io.BytesIO(parts["dbf"]), shx=io.BytesIO(parts["shx"]))
    fields = [f[0] for f in reader.fields[1:]]
    shapes: dict[int, list[shapely.Geometry]] = {}
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            raise ValueError("a polling division record without its shape")
        number = int(float(str(dict(zip(fields, record, strict=True))["District"])))
        shapes.setdefault(number, []).append(shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__)))
    return {number: shapely.union_all(found) for number, found in sorted(shapes.items())}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Newfoundland and Labrador's electoral districts: the 40 of the 2015 plan with outlines from Elections NL's "
        "polling division boundaries, and the 48 districts of each of the 1996 and 2007 plans by name",
    )
    p.external |= {NEWFOUNDLAND_AND_LABRADOR, PROVINCE_AREA}
    p.add(
        "source",
        id=BOUNDARY_SOURCE,
        kind="official_body",
        publisher="Elections NL",
        title="Current polling divisions, all districts (shapefile, updated May 1, 2019)",
        url=POLLS,
        license=PUBLIC_RECORD,
        license_url=GIS,
        attribution="Elections NL",
    )
    p.artifact(raw.cached(SOURCE, POLLS), BOUNDARY_SOURCE)
    p.add(
        "scheme",
        code="ca.nl",
        polity_id=NEWFOUNDLAND_AND_LABRADOR,
        kind="electoral_district",
        rank=1,
        noun="electoral district",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca.nl_poll",
        polity_id=NEWFOUNDLAND_AND_LABRADOR,
        kind="precinct",
        rank=2,
        noun="polling division",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=SET_1996,
        scheme_code="ca.nl",
        vintage="1996",
        valid_from=PLAN_1996_FROM,
        valid_to=PLAN_2007_FROM,
        as_of=PLAN_1996_FROM,
    )
    p.add(
        "boundary_set",
        id=SET_2007,
        scheme_code="ca.nl",
        vintage="2007",
        valid_from=PLAN_2007_FROM,
        valid_to=PLAN_2015_FROM,
        as_of=PLAN_2007_FROM,
    )
    p.add(
        "boundary_set",
        id=SET_2015,
        scheme_code="ca.nl",
        vintage="2015",
        valid_from=PLAN_2015_FROM,
        valid_to=None,
        as_of=PLAN_2015_FROM,
    )
    shapes = _districts_2015()
    for which, since, until in (
        ("1996", PLAN_1996_FROM, PLAN_2007_FROM),
        ("2007", PLAN_2007_FROM, PLAN_2015_FROM),
        ("2015", PLAN_2015_FROM, None),
    ):
        for slug, (name, number) in sorted(_plan_names(which).items()):
            area = key("area", which, slug)
            p.add("area", id=area, scheme_code="ca.nl")
            if number is not None:
                p.add("area_code", area_id=area, code=number, valid_from=since, valid_to=until)
            p.add("area_parent", area_id=area, parent_id=PROVINCE_AREA, valid_from=since, valid_to=until)
            p.name(area, name, "en", "official")
            if which == "2015":
                if number is None or int(number) not in shapes:
                    raise ValueError(f"no outline for the district {name!r} ({number})")
                _outline(p, area, shapes[int(number)], since)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Newfoundland and Labrador's parties, the House of Assembly, and its single-member plurality system, from "
        "Elections NL",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEWFOUNDLAND_AND_LABRADOR}
    p.add(
        "source",
        id=ENL,
        kind="official_body",
        publisher="Elections NL",
        title="Election reports: general and by-election reports with poll-by-poll results",
        url=OFFICIAL,
        license=PUBLIC_RECORD,
        license_url=None,
        attribution="Elections NL",
    )
    for election in _all():
        p.artifact(raw.cached(SOURCE, election.url), ENL)
    p.add("body", id=HOUSE, polity_id=NEWFOUNDLAND_AND_LABRADOR, kind="unicameral")
    p.name(HOUSE, "Newfoundland and Labrador House of Assembly", "en", "official")
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=NEWFOUNDLAND_AND_LABRADOR,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="One member is elected for each electoral district, by the most votes.",
        lang="en",
    )
    for code, name in sorted(_parties(_all()).items()):
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=NEWFOUNDLAND_AND_LABRADOR)
        p.add("external_id", entity_id=id, scheme="ca.nl.party", value=code)
        p.name(id, name, "en", "official")
        if code != name:
            p.name(id, code, "en", "abbreviation", False)
    return p


ACCOUNTING_NOTE: Final = (
    "Elections NL prints, for each polling division, the voters on the revised list, each candidate's votes, the "
    "rejected ballots, and the total ballots cast. Valid votes here are the sum of the candidates' votes. The "
    "advance polls and the special ballots cast at the returning office are reported for the district as a whole "
    "and stored as aggregate units. The number of electors sworn in at the polls, which the report prints beside the "
    "list, isn't stored."
)


def _kind(code: str) -> str:
    return (
        "standard"
        if code.strip()[:1].isdigit() and not re.search(r"advance|special", code, re.IGNORECASE)
        else "aggregate"
    )


def _unique(polls: list[Poll]) -> list[tuple[str, Poll]]:
    seen: dict[str, int] = {}
    found = []
    for poll in polls:
        code = re.sub(r"\s+", " ", poll.code).strip()
        seen[code] = seen.get(code, 0) + 1
        found.append((code if seen[code] == 1 else f"{code} ({seen[code]})", poll))
    return found


def _contests(p: Package, election: Election, election_id: str, path: str) -> None:
    day = election.day
    boundary_set = {"2015": SET_2015, "2007": SET_2007, "1996": SET_1996}[plan(day)]
    for district in election.districts:
        slug = changeset.slug(district.name)
        area = area_of(day, district.name)
        contest = key("contest", day, slug)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=HOUSE,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="tacit" if district.acclaimed else "counted",
            boundary_set_id=boundary_set,
        )
        p.name(contest, district.name, "en", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="tacit" if district.acclaimed else "counted",
            source_id=ENL,
            status="active",
            is_default=True,
            completeness=0.0 if district.acclaimed else 1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=ACCOUNTING_NOTE,
            lang="en",
        )
        for index, text in enumerate(district.notes):
            p.add(
                "note",
                id=key("note", result_set, "caveat", index),
                entity_id=result_set,
                kind="caveat",
                body_md=text,
                lang="en",
            )
        votes = [c.votes for c in district.candidates]
        winner = (
            next(i for i, c in enumerate(district.candidates) if c.elected)
            if district.acclaimed
            else max(range(len(votes)), key=lambda i: votes[i])
        )
        options = _candidates(p, election, district, contest, result_set, slug, winner)
        for poll_code, poll in _unique(district.polls):
            unit = poll_unit(day, district.name, poll_code)
            kind = _kind(poll_code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NEWFOUNDLAND_AND_LABRADOR,
                scheme_code="ca.nl_poll",
                code=f"{day}:{slug}:{poll_code}",
                area_relation="equals" if kind == "aggregate" else "within",
                area_id=area,
                kind=kind,
            )
            valid = sum(poll.votes)
            accounting = [
                (2, poll.electors),
                (3, None if poll.rejected is None else valid + poll.rejected),
                (4, valid),
                (5, poll.rejected),
            ]
            for measure, value in accounting:
                if value is not None:
                    _value(p, result_set, unit, None, measure, value)
            for option, count in zip(options, poll.votes, strict=True):
                _value(p, result_set, unit, option, 1, count)


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


def _candidates(
    p: Package, election: Election, district: District, contest: str, result_set: str, slug: str, winner: int
) -> list[str]:
    day = election.day
    options = []
    for index, candidate in enumerate(district.candidates):
        tag = f"{day}:{slug}:{index + 1}"
        option = key("option", tag)
        person = key("person", tag)
        options.append(option)
        independent = candidate.party in (None, "N-A")
        party = None if independent else key("party", candidate.party)
        p.add("person", id=person)
        p.name(person, proper(candidate.name), "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=index + 1,
            ballot_label=candidate.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.name,
            member_of_party_id=party,
            is_independent=independent,
        )
        lost = "withdrew" if district.acclaimed else "lost"
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if index == winner else lost)
        if not district.acclaimed:
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=candidate.votes)
    return options


def _election_package(election: Election) -> Package:
    day = election.day
    general = election.kind == "general"
    names = ", ".join(d.name for d in election.districts)
    p = Package(
        f"{SOURCE}/{'ge' if general else 'be'}-{day.year if general else day}",
        SOURCE,
        f"The {day.year} Newfoundland and Labrador general election by polling division, from Elections NL"
        if general
        else f"The by-election of {day} in {names} by polling division, from Elections NL",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEWFOUNDLAND_AND_LABRADOR, PROVINCE_AREA}
    p.uses(raw.cached(SOURCE, election.url))
    election_id = key("election", day, election.kind)
    path = f"ca/nl/{day}-{'general' if general else 'by-election'}"
    p.add(
        "election",
        id=election_id,
        polity_id=NEWFOUNDLAND_AND_LABRADOR,
        kind=election.kind,
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    title = (
        f"{day.year} Newfoundland and Labrador general election" if general else f"{names} by-election, {day:%-d %B %Y}"
    )
    p.name(election_id, title, "en", "official")
    _contests(p, election, election_id, path)
    return p


HISTORY_NOTE: Final = (
    "The authority's report on the 2019 general election prints only province-wide statistics for the elections "
    "from 1949 to 1971: each party's votes, the rejected ballots, the votes cast, the registered voters, and the "
    "candidates nominated and elected. Each party's votes are stored as a total for the province and its elected "
    "candidates as its seats. No candidate or district figures exist in the authority's reports."
)


def history(year: int) -> Package:
    stats = nl_hist.statistics(year)
    day = nl_hist.DAYS[year]
    p = Package(
        f"{SOURCE}/ge-{year}",
        SOURCE,
        f"The {year} Newfoundland general election, province-wide totals by party, from Elections NL",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEWFOUNDLAND_AND_LABRADOR, PROVINCE_AREA}
    p.uses(raw.cached(SOURCE, nl_hist.REPORT))
    election_id = key("election", day, "general")
    path = f"ca/nl/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=NEWFOUNDLAND_AND_LABRADOR,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} Newfoundland general election", "en", "official")
    contest = key("contest", day, "province")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=HOUSE,
        district_area_id=PROVINCE_AREA,
        magnitude=stats.seats,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "Newfoundland House of Assembly", "en", "official")
    p.add("slug", path=f"{path}/province", entity_id=contest, is_current=True)
    result_set = key("result_set", day, "province")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ENL,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    nominated = ", ".join(f"{party} {n}" for party, n in stats.nominated.items() if n)
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=f"{HISTORY_NOTE} Candidates nominated: {nominated}; {stats.acclaimed} elected by acclamation; "
        f"{stats.districts} districts elected {stats.seats} members.",
        lang="en",
    )
    for index, text in enumerate(stats.notes):
        p.add(
            "note",
            id=key("note", result_set, "caveat", index),
            entity_id=result_set,
            kind="caveat",
            body_md=text,
            lang="en",
        )
    unit = key("unit", day, "province")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=NEWFOUNDLAND_AND_LABRADOR,
        scheme_code="ca.nl_poll",
        code=f"{day}:province",
        area_relation="equals",
        area_id=PROVINCE_AREA,
        kind="aggregate",
    )
    valid = sum(stats.votes.values())
    for measure, value in ((2, stats.registered), (3, stats.cast), (4, valid), (5, stats.rejected)):
        _value(p, result_set, unit, None, measure, value)
    for index, (code, votes) in enumerate(stats.votes.items()):
        independent = code == "N-A"
        option = key("option", day, code)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="independents" if independent else "list",
            party_id=None if independent else key("party", code),
            ballot_position=index + 1,
            ballot_label="Independent/Non-Affiliated" if independent else code,
        )
        seats = stats.elected[code]
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if seats else "lost", seats=seats)
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
        _value(p, result_set, unit, option, 1, votes)
    return p


REASONS: Final = {
    "accounting.turnout": "Elections NL prints more ballots cast than voters on the revised list in some polling "
    "divisions, where electors were sworn in at the polls; the figures are stored as printed",
    "accounting.declared": "Elections NL's printed candidate total differs from the sum of its own poll-by-poll "
    "rows in some district; the printed total is the declared figure and each difference is noted on the result set",
    "accounting.ballots_sum": "Elections NL's printed votes-cast total differs from the sum of its printed party "
    "votes and rejected ballots; the figures are stored as printed and the difference is noted on the result set",
}


def _settle(p: Package) -> None:
    """Write a package, and accept the accounting findings the source's own figures cause."""
    found = {code for code, _, _ in package.check(p.write()) if code.startswith("accounting.")}
    if found:
        for code in sorted(found):
            p.accept(code, REASONS[code])
        p.write()


def parse() -> None:
    nl_hist.check()
    geo().write()
    registry().write()
    for year in nl_hist.YEARS:
        _settle(history(year))
    for election in generals().values():
        _settle(_election_package(election))
    for election in by_elections().values():
        _settle(_election_package(election))


def emit() -> None:
    package.run("load", packages())


__all__ = ["Candidate", "District", "Election", "Poll", "emit", "fetch", "packages", "parse"]
