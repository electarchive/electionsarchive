"""City of Winnipeg civic elections, 1964 to 2022: the mayor, the councillors, and the school trustees.

The City of Winnipeg's open data portal publishes one table of every civic election result back to 1964: a row for each
candidate of each contest, with the date, the kind of vote (general or by-election), the office, the area (the whole
city, a council ward, or a school division), the trustee ward, the votes, and whether the candidate won. The mayor and
each councillor ward are decided by plurality. A school division elects its trustees by ward or at large, and a ward
that elects several trustees is decided by block voting. A contest whose candidates all lack votes was filled by
acclamation and carries a `tacit` result set. The October 2018 ballot also carried one yes-or-no question.

The table gives each contest's results as a whole, so the reporting unit of each contest is the contest's own area.

Council wards have outlines for 1992 and later, from the portal's ward boundary datasets (the 1996, 2001, 2006, and
2011 census boundary files for the wards of the elections near each of those years, the 2014 to 2018 wards, and the
current wards). The 29 wards of 1971 to 1989 and the school divisions and trustee wards have no published outline.
"""

import functools
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ca.mb_winnipeg"
PORTAL = "https://data.winnipeg.ca/"
RESULTS = PORTAL + "resource/7753-3fjc.json?$limit=100000&$order=:id"
RESULTS_PAGE = PORTAL + "Council-Services/Winnipeg-Election-Results/7753-3fjc"
WARDS = {
    1996: "33u9-wvse",
    2001: "cq7j-f6mj",
    2006: "cxia-3gyc",
    2011: "4cgw-zam3",
    2014: "mp2r-jeav",
    2018: "t4cg-yaxs",
}
WARDS_PAGE = PORTAL + "Council-Services/Electoral-Wards/t4cg-yaxs"
# Production holds Canada and Manitoba under the ids their first pipeline gave them.
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
MANITOBA = "01a13fb6-eab7-7501-a0f9-490529e3c248"
MANITOBA_AREA = "01a13fb6-eab7-7501-a0f9-48f8d6d38062"
SYSTEM_NOTE = (
    "The City of Winnipeg elects its mayor, and each councillor for a ward, by plurality: every elector marks one "
    "candidate and the candidate with the most votes wins. A school division that elects several trustees for one "
    "ward or at large uses block voting: every elector marks up to as many candidates as there are seats, and the "
    "candidates with the most votes fill them. A contest with no more candidates than seats is filled by "
    "acclamation without a vote."
)
OFFICES = {"Office of Mayor": "mayor", "Office of Councillor": "councillor", "Office of School Trustee": "trustee"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


PORTAL_SOURCE = key("source", "portal")
PLURALITY = key("electoral_system", "plurality")
BLOCK = key("electoral_system", "block")
CITY = key("polity", "winnipeg")
CITY_AREA = key("area", "winnipeg")
COUNCIL = key("body", "winnipeg")
MAYORALTY = key("office", "winnipeg", "mayor")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def slug(text: str) -> str:
    return fold(text).replace(" ", "-")


def tidy(text: str) -> str:
    """A candidate's name with the surname's capitals folded, as in `Scott GILLINGHAM` to `Scott Gillingham`."""

    def word(token: str) -> str:
        letters = re.sub(r"[^A-Za-z]", "", token)
        if len(letters) < 2:
            return token
        if token.isupper():
            parts = re.split(r"(['’-])", token)
            return "".join(part.capitalize() if part.isalpha() or "." in part else part for part in parts)
        found = re.match(r"^(Mc|Mac)([A-Z]{2,})$", token)
        if found:
            return found.group(1) + found.group(2).capitalize()
        return token

    return " ".join(word(token) for token in text.split())


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS, suffix=".json")
    for wards in WARDS.values():
        raw.fetch(SOURCE, ward_url(wards), suffix=".json")


def ward_url(dataset: str) -> str:
    return f"{PORTAL}resource/{dataset}.json?$limit=100"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in elections())]


def parse() -> None:
    geo().write()
    registry().write()
    for name in elections():
        election(name).write()


def emit() -> None:
    package.run("load", packages())


@dataclass(frozen=True)
class Row:
    day: date
    kind: str  # general or by_election
    office: str  # mayor, councillor, trustee, or question
    area: str  # the ward, school division, or question text as the table prints it
    ward: str  # a trustee ward, when there is one
    candidate: str
    votes: int | None
    won: bool


@dataclass
class Contest:
    day: date
    kind: str
    office: str
    area: str
    ward: str
    rows: list[Row]

    @property
    def tacit(self) -> bool:
        return not any(row.votes for row in self.rows)

    @property
    def seats(self) -> int:
        return max(1, sum(row.won for row in self.rows))


@functools.cache
def rows() -> list[Row]:
    found = []
    for record in json.loads(raw.cached(SOURCE, RESULTS).path.read_text()):
        found.append(
            Row(
                day=date.fromisoformat(record["date"][:10]),
                kind="general" if record["type"] == "General Election" else "by_election",
                office=OFFICES.get(record["position"], "question"),
                area=" ".join(record["area"].split()),
                ward=" ".join(record.get("ward", "").split()),
                candidate=" ".join(record["candidate"].split()),
                votes=int(record["votes"]) if "votes" in record else None,
                won=record["won"] == "Yes",
            )
        )
    return found


@functools.cache
def contests() -> dict[date, list[Contest]]:
    grouped: dict[tuple[date, str, str, str, str], Contest] = {}
    for row in rows():
        found = grouped.setdefault(
            (row.day, row.office, row.area, row.ward, row.kind),
            Contest(row.day, row.kind, row.office, row.area, row.ward, []),
        )
        found.rows.append(row)
    days: dict[date, list[Contest]] = defaultdict(list)
    for contest in grouped.values():
        contest.rows.sort(key=lambda row: (-(row.votes or 0), row.candidate))
        days[contest.day].append(contest)
    return {day: sorted(days[day], key=lambda c: (c.office, c.area, c.ward)) for day in sorted(days)}


def election_name(day: date) -> str:
    kind = contests()[day][0].kind
    return f"{day.year}" if kind == "general" else f"{day}-by"


@functools.cache
def elections() -> dict[str, date]:
    return {election_name(day): day for day in contests()}


# School divisions, by their folded name without the number the table sometimes prints, and their latest spelling.
def division_key(text: str) -> str:
    return fold(re.sub(r"\bNo\.?\s*1\b", "", text))


@functools.cache
def divisions() -> dict[str, str]:
    found: dict[str, str] = {}
    for day in contests():
        for contest in contests()[day]:
            if contest.office == "trustee":
                text = re.sub(r"\s+No\.?\s*1$", "", contest.area)
                found[division_key(contest.area)] = re.sub(r"\s*-\s*", " - ", text) if "James" in text else text
    return dict(sorted(found.items()))


def division_path(code: str) -> str:
    return f"ca/mb/schools/{slug(divisions()[code])}"


# The boundary dataset whose ward map stands for each election, by the election's date.
def vintage(day: date) -> int | None:
    if day.year < 1992:
        return None
    for start, year in ((2018, 2018), (2014, 2014), (2010, 2011), (2006, 2006), (2002, 2001)):
        if day.year >= start:
            return year
    return 1996


def legacy(day: date) -> int:
    return 1971 if day.year < 1977 else 1977 if day.year < 1989 else 1989


@dataclass
class Ward:
    number: int
    name: str
    shape: shapely.Geometry


@functools.cache
def ward_maps() -> dict[int, dict[int, Ward]]:
    found: dict[int, dict[int, Ward]] = {}
    for year, dataset in WARDS.items():
        wards: dict[int, Ward] = {}
        for record in json.loads(raw.cached(SOURCE, ward_url(dataset)).path.read_text()):
            number = int(float(record.get("ward_id") or record["number"]))
            name = " ".join((record.get("desc") or record["name"]).split())
            shape = shapely.make_valid(shapely.geometry.shape(record["the_geom"]))
            shape = shapely.simplify(shape, 0.0001, preserve_topology=True)
            pieces = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
            wards[number] = Ward(number, name, shapely.set_srid(shapely.MultiPolygon(pieces), 4326))
        found[year] = wards
    return found


def ward_number(year: int, text: str) -> int:
    """The number of the ward a contest's area names, within the ward map of a vintage."""
    wanted = fold(text.replace("Ft.", "Fort"))
    maps = ward_maps()[year]
    exact = [n for n, w in maps.items() if fold(w.name.replace("Ft.", "Fort")) == wanted]
    near = [n for n, w in maps.items() if wanted in fold(w.name) or fold(w.name) in wanted]
    found = exact or near
    if len(found) != 1:
        raise ValueError(f"{year}: ward {text!r} matches {found}")
    return found[0]


def ward_area(day: date, text: str) -> str:
    year = vintage(day)
    if year is None:
        return key("area", "legacy-ward", legacy(day), fold(text))
    return key("area", "ward", year, ward_number(year, text))


def wards_used() -> dict[str, tuple[int | None, str, str]]:
    """Every council ward a contest is for: its area id, the boundary vintage or the legacy era, and its names."""
    found: dict[str, tuple[int | None, str, str]] = {}
    for day, list_ in contests().items():
        for contest in list_:
            if contest.office != "councillor":
                continue
            year = vintage(day)
            place = ward_area(day, contest.area)
            if year is None:
                found[place] = (None, str(legacy(day)), contest.area)
            else:
                found[place] = (ward_number(year, contest.area), str(year), contest.area)
    return found


def trustee_areas() -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for list_ in contests().values():
        for contest in list_:
            if contest.office == "trustee" and contest.ward:
                found[trustee_area(contest)] = (division_key(contest.area), contest.ward)
    return found


def trustee_area(contest: Contest) -> str:
    if not contest.ward:
        return division_area(division_key(contest.area))
    return key("area", "trustee-ward", division_key(contest.area), fold(contest.ward))


def division_area(code: str) -> str:
    return key("area", "division", code)


def division_polity(code: str) -> str:
    return key("polity", "division", code)


def division_body(code: str) -> str:
    return key("body", "division", code)


def _wkb(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


def _outline() -> shapely.Geometry:
    shape = shapely.union_all([ward.shape for ward in ward_maps()[2018].values()])
    pieces = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(pieces), 4326)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The City of Winnipeg, its council wards, and the school divisions and trustee wards of its civic elections, "
        "with the ward outlines the city publishes",
    )
    p.external |= {CANADA, MANITOBA, MANITOBA_AREA}
    p.add(
        "source",
        id=PORTAL_SOURCE,
        kind="official_body",
        publisher="City of Winnipeg",
        title="Winnipeg Open Data: election results and electoral wards",
        url=PORTAL,
        license=None,
        attribution="City of Winnipeg, Open Data Portal",
    )
    p.artifact(raw.cached(SOURCE, RESULTS), PORTAL_SOURCE)
    for dataset in WARDS.values():
        p.artifact(raw.cached(SOURCE, ward_url(dataset)), PORTAL_SOURCE)
    for code, kind, rank, noun in (
        ("ca-mb.municipality", "administrative", 3, "municipality"),
        ("ca-mb.school_division", "administrative", 3, "school division"),
        ("ca-mb.winnipeg_ward", "electoral_district", 4, "ward"),
        ("ca-mb.trustee_ward", "electoral_district", 4, "trustee ward"),
        ("ca-mb.result_unit", "precinct", 5, "reporting unit"),
    ):
        p.add("scheme", code=code, polity_id=MANITOBA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=CITY_AREA, scheme_code="ca-mb.municipality")
    p.add("area_parent", area_id=CITY_AREA, parent_id=MANITOBA_AREA)
    p.name(CITY_AREA, "Winnipeg", "en", "official")
    outline = _outline()
    p.add(
        "area_geometry",
        area_id=CITY_AREA,
        valid_from=None,
        valid_to=None,
        geom=_wkb(outline),
        label_point=_wkb(shapely.point_on_surface(outline)),
        quality="official",
        source_id=PORTAL_SOURCE,
    )
    p.add("polity", id=CITY, kind="municipality", iso_code=None)
    p.name(CITY, "Winnipeg", "en", "official")
    p.add("polity_parent", polity_id=CITY, parent_id=MANITOBA)
    p.add("polity_territory", polity_id=CITY, area_id=CITY_AREA)
    p.add("slug", path="ca/mb/winnipeg", entity_id=CITY, is_current=True)
    for place, (number, era, text) in sorted(wards_used().items()):
        p.add("area", id=place, scheme_code="ca-mb.winnipeg_ward")
        p.add("area_parent", area_id=place, parent_id=CITY_AREA)
        if number is None:
            p.name(place, f"{text} ward ({era} map)", "en", "official")
            continue
        ward = ward_maps()[int(era)][number]
        p.name(place, f"{ward.name} ward ({era} map)", "en", "official")
        if fold(text) != fold(ward.name):
            p.name(place, f"{text} ward ({era} map)", "en", "official", False)
        p.add(
            "area_geometry",
            area_id=place,
            valid_from=None,
            valid_to=None,
            geom=_wkb(ward.shape),
            label_point=_wkb(shapely.point_on_surface(ward.shape)),
            quality="official",
            source_id=PORTAL_SOURCE,
        )
    for code, text in divisions().items():
        p.add("area", id=division_area(code), scheme_code="ca-mb.school_division")
        p.add("area_parent", area_id=division_area(code), parent_id=MANITOBA_AREA)
        p.name(division_area(code), text, "en", "official")
        town = division_polity(code)
        p.add("polity", id=town, kind="school_district", iso_code=None)
        p.name(town, text, "en", "official")
        p.add("polity_parent", polity_id=town, parent_id=MANITOBA)
        p.add("polity_territory", polity_id=town, area_id=division_area(code))
        p.add("slug", path=division_path(code), entity_id=town, is_current=True)
    for place, (code, label) in sorted(trustee_areas().items()):
        p.add("area", id=place, scheme_code="ca-mb.trustee_ward")
        p.add("area_parent", area_id=place, parent_id=division_area(code))
        name = label if not label.isdigit() else f"Ward {label}"
        p.name(place, f"{divisions()[code]}: {name}", "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Winnipeg city council, mayoralty, and school division boards, and the electoral systems of their votes",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CANADA, MANITOBA}
    p.uses(raw.cached(SOURCE, RESULTS))
    for system, formula, title in (
        (PLURALITY, "fptp", "Winnipeg civic plurality voting"),
        (BLOCK, "block", "Winnipeg school trustee block voting"),
    ):
        p.add(
            "electoral_system", id=system, polity_id=MANITOBA, valid_from=None, valid_to=None,
            rules={"formula": formula}, rules_version=1,
        )  # fmt: skip
        p.name(system, title, "en", "official")
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    p.add("body", id=COUNCIL, polity_id=CITY, kind="council")
    p.name(COUNCIL, "Winnipeg city council", "en", "official")
    p.add("office", id=MAYORALTY, polity_id=CITY, kind="mayor")
    p.name(MAYORALTY, "Mayor of Winnipeg", "en", "official")
    for code, text in divisions().items():
        p.add("body", id=division_body(code), polity_id=division_polity(code), kind="council")
        p.name(division_body(code), f"{text} board of trustees", "en", "official")
    return p


def _value(p: Package, result_set: str, unit: str, option: str, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=1,
        count_round=0,
        flags=0,
        value=value,
    )


def _label(contest: Contest) -> str:
    if contest.office == "mayor":
        return "Mayor"
    if contest.office == "councillor":
        return f"Councillor, {contest.area}"
    if contest.office == "trustee":
        ward = "" if not contest.ward else f", {'Ward ' + contest.ward if contest.ward.isdigit() else contest.ward}"
        return f"Trustee{ward}"
    return "Ballot question"


def election(name: str) -> Package:
    day = elections()[name]
    list_ = contests()[day]
    by = list_[0].kind == "by_election"
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        f"The {day} Winnipeg civic "
        + ("by-election" if by else "general election")
        + ": "
        + ", ".join(
            sorted({c.office.replace("councillor", "council").replace("trustee", "school trustee") for c in list_})
        ),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CANADA, MANITOBA}
    p.uses(raw.cached(SOURCE, RESULTS))
    kind = "by_election" if by else "local"
    when = day.isoformat()
    umbrella = key("election", name)
    p.add("election", id=umbrella, polity_id=CITY, kind=kind, date_from=day, date_to=day, status="held")
    p.name(
        umbrella,
        f"{when} Winnipeg civic by-election" if by else f"{day.year} Winnipeg civic election",
        "en",
        "official",
    )
    base = f"ca/mb/winnipeg/{when}-{'by' if by else 'local'}"
    p.add("slug", path=base, entity_id=umbrella, is_current=True)
    close = day + timedelta(days=1)
    local: dict[str, str] = {}
    taken: set[str] = set()
    for number, contest in enumerate(list_, start=1):
        trustee = contest.office == "trustee"
        question = contest.office == "question"
        division = division_key(contest.area) if trustee else ""
        if trustee and division not in local:
            local[division] = key("election", name, "division", division)
            p.add(
                "election",
                id=local[division],
                polity_id=division_polity(division),
                parent_id=umbrella,
                kind=kind,
                date_from=day,
                date_to=day,
                status="held",
            )
            p.name(
                local[division],
                f"{when} {divisions()[division]} by-election" if by else f"{day.year} {divisions()[division]} election",
                "en",
                "official",
            )
            p.add(
                "slug",
                path=f"{division_path(division)}/{when}-{'by' if by else 'local'}",
                entity_id=local[division],
                is_current=True,
            )
        vote = local[division] if trustee else umbrella
        parent_path = f"{division_path(division)}/{when}-{'by' if by else 'local'}" if trustee else base
        if trustee:
            place = trustee_area(contest)
        elif contest.office == "councillor":
            place = ward_area(day, contest.area)
        else:
            place = CITY_AREA
        tag = f"{number}"
        unit = key("unit", name, tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CANADA,
            scheme_code="ca-mb.result_unit",
            code=f"{when}:{number}",
            area_relation="equals",
            area_id=place,
            kind="standard",
            valid_from=day,
            valid_to=close,
        )
        p.name(
            unit, _label(contest) if not trustee else f"{divisions()[division]}: {_label(contest)}", "en", "official"
        )
        contest_id = key("contest", name, tag)
        if question:
            ckind, magnitude = "ballot_question", None
        elif contest.office == "mayor":
            ckind, magnitude = "office", 1
        else:
            ckind, magnitude = "body_seats", contest.seats
        p.add(
            "contest",
            id=contest_id,
            election_id=vote,
            kind=ckind,
            office_id=MAYORALTY if contest.office == "mayor" else None,
            body_id=None if ckind != "body_seats" else (division_body(division) if trustee else COUNCIL),
            district_area_id=place,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=PLURALITY if (question or magnitude == 1) else BLOCK,
            default_result_kind="tacit" if contest.tacit else "counted",
        )
        if question:
            p.add("ballot_question", contest_id=contest_id, type="plebiscite")
            p.add(
                "ballot_question_text",
                id=key("question_text", name, tag),
                contest_id=contest_id,
                kind="text",
                lang="en",
                body=contest.area,
                source_id=PORTAL_SOURCE,
            )
            title = f"Winnipeg: Question: {contest.area}"
            path = f"{parent_path}/question"
        else:
            title = f"{divisions()[division] if trustee else 'Winnipeg'}: {_label(contest)}"
            path = f"{parent_path}/{contest.office}" + (
                "" if contest.office == "mayor" else f"-{slug(contest.ward or contest.area)}"
            )
        p.name(contest_id, title, "en", "official")
        while path in taken:
            path += "-2"
        taken.add(path)
        p.add("slug", path=path, entity_id=contest_id, is_current=True)
        result_set = key("result_set", name, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=vote,
            contest_id=contest_id,
            stage="official",
            kind="tacit" if contest.tacit else "counted",
            source_id=PORTAL_SOURCE,
            status="active",
            is_default=True,
            completeness=None if contest.tacit else 1.0,
        )
        for position, row in enumerate(contest.rows, start=1):
            seq = f"{tag}:{position}"
            option = key("option", name, seq)
            if question:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=row.candidate,
                )
            else:
                person = key("person", name, seq)
                p.add("person", id=person, birth_precision=None)
                p.name(person, tidy(row.candidate), "en", "official")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="candidate",
                    party_id=None,
                    ballot_position=None,
                    ballot_label=row.candidate,
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", name, seq),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row.candidate,
                    member_of_party_id=None,
                )
            if row.votes is not None and not contest.tacit:
                _value(p, result_set, unit, option, row.votes)
            if row.won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if question else "elected")
    return p
