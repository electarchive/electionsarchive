"""Vorarlberg Landtag elections, 1949 to 2024, by municipality from the state's election results app.

The app at apps.vorarlberg.at/wahlen loads one JSON file per election with the result of every municipality, the
postal ballots of each district, the districts, and the state, keyed by the state's five-digit municipality codes.
Municipality names come from the app's list of areas. The seat counts of 2019 and 2024 come from the 2024 seat
distribution workbook, which prints both. The outlines are the 2017 municipality outlines of geoBoundaries.
"""

import functools
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

import fastexcel
import shapely
import shapely.geometry
import shapely.ops

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.vorarlberg"
GEOBOUNDARIES = "at.vorarlberg.geoboundaries"
BASE = "https://apps.vorarlberg.at/wahlen"
DATA = f"{BASE}/data"
INDEX = f"{DATA}/wahlen.json"
AREAS = f"{DATA}/gemeinden.json"
SEATS = f"{DATA}/LT/Landtagswahl_2024_Mandatsverteilung.xlsx"
OUTLINES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/AUT/ADM3/"
    "geoBoundaries-AUT-ADM3_simplified.geojson"
)
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))

DATES = [
    date(1949, 10, 9),
    date(1954, 10, 17),
    date(1959, 10, 18),
    date(1964, 10, 18),
    date(1969, 10, 19),
    date(1974, 10, 20),
    date(1979, 10, 21),
    date(1984, 10, 21),
    date(1989, 10, 28),
    date(1994, 9, 18),
    date(1999, 9, 19),
    date(2004, 9, 19),
    date(2009, 9, 20),
    date(2014, 9, 21),
    date(2019, 10, 13),
    date(2024, 10, 13),
]
MEASURES = (("registered", 2), ("cast", 3), ("valid", 4), ("invalid", 5))
POSTAL_CHANNEL = 3
FIRST = DATES[0]


@dataclass(frozen=True)
class Edition:
    """One Landtag election and the file that holds its results."""

    day: date

    @property
    def id(self) -> str:
        return f"LT_{self.day.isoformat()}"

    @property
    def key(self) -> str:
        return f"lt-{self.day.year}"

    @property
    def json(self) -> str:
        return f"{DATA}/LT/{self.id}.json"


EDITIONS = [Edition(day) for day in DATES]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
GEO_SOURCE = key("source", "geoboundaries")
LANDTAG = key("body", "landtag")
LAND = key("area", "at-8.land", "8")
STATE = "80000"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.key}" for e in EDITIONS)]


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".json")
    raw.fetch(SOURCE, AREAS, suffix=".json")
    raw.fetch(SOURCE, SEATS, suffix=".xlsx")
    raw.fetch(GEOBOUNDARIES, OUTLINES, suffix=".geojson")
    for edition in EDITIONS:
        raw.fetch(SOURCE, edition.json, suffix=".json")


def parse() -> None:
    geo().write()
    registry().write()
    for edition in EDITIONS:
        election(edition).write()


def emit() -> None:
    package.run("load", packages())


def load(url: str, source: str = SOURCE) -> Any:
    return json.loads(raw.cached(source, url).path.read_text("utf-8-sig"))


@functools.cache
def names() -> dict[str, str]:
    """The text of each area code in the app's list; a code listed twice keeps its first text."""
    found: dict[str, str] = {}
    for row in load(AREAS)["gemeinden"]:
        found.setdefault(row["schluessel"], row["bezeichnung"])
    return found


def is_municipality(code: str) -> bool:
    return code.startswith("80") and code[3:] not in {"00", "50", "99"}


def is_postal(code: str) -> bool:
    return code.startswith("80") and code[3:] == "99"


def district_code(code: str) -> str:
    return f"{code[:3]}00"


@dataclass
class Row:
    code: str
    registered: int | None
    cast: int
    valid: int
    invalid: int
    votes: dict[str, int]


@dataclass
class Result:
    parties: list[dict[str, Any]]
    rows: list[Row]
    state: Row


def row_of(entry: Mapping[str, Any]) -> Row:
    # The 2019 file spells the registered voters' field without its second "t".
    registered = entry.get("wahlberechtigte", entry.get("wahlberechtige"))
    return Row(
        entry["schluessel"],
        registered,
        entry["abgegebeneStimmen"],
        entry["gueltigeStimmen"],
        entry["ungueltigeStimmen"],
        {party["parteiID"]: party["stimmen"] for party in entry["parteien"]},
    )


@functools.cache
def results(edition: Edition) -> Result:
    data = load(edition.json)
    entries = [row_of(entry) for entry in data["ergebnisse"]]
    state = next(row for row in entries if row.code == STATE)
    rows = [row for row in entries if is_municipality(row.code) or is_postal(row.code)]
    return Result(data["parteien"], rows, state)


@functools.cache
def seats() -> dict[date, dict[str, int]]:
    """The seats of 2024 and, in brackets, of 2019 by list, from the last row of the seat distribution workbook."""
    book = fastexcel.read_excel(str(raw.cached(SOURCE, SEATS).path))
    sheet = book.load_sheet_by_name("Mandatsverteilung", header_row=None).to_polars()
    lines = [line for line in ([str(cell) for cell in row if cell is not None] for row in sheet.rows()) if line]
    header = next(line for line in lines if line[0] == "1. Ermittlungsverfahren:")
    total = next(line for line in lines if line[0] == "1. und 2. Ermittlungsverfahren:")
    labels = header[1:]
    cells = total[1:]
    assert len(cells) == 2 * len(labels), "the seat row does not hold a current and a prior value per list"
    now = {label: int(cells[2 * i]) for i, label in enumerate(labels)}
    before = {label: int(cells[2 * i + 1].strip("()")) for i, label in enumerate(labels)}
    return {date(2024, 10, 13): now, date(2019, 10, 13): before}


def party(id: str) -> str:
    return key("party", id)


def parties() -> dict[str, list[str]]:
    """Each list's id in the files, with the texts it ran under, newest first."""
    found: dict[str, list[str]] = {}
    for edition in sorted(EDITIONS, key=lambda e: e.day, reverse=True):
        for entry in results(edition).parties:
            texts = found.setdefault(entry["parteiID"], [])
            for text in (entry["bezeichnung"], entry["kurzbezeichnung"]):
                if text not in texts:
                    texts.append(text)
    return found


def districts() -> dict[str, str]:
    return {
        code: text for code, text in names().items() if code.startswith("80") and code[3:] == "00" and code != STATE
    }


def municipalities() -> dict[str, str]:
    seen = {row.code for edition in EDITIONS for row in results(edition).rows if is_municipality(row.code)}
    return {code: names()[code] for code in sorted(seen)}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Vorarlberg's districts and municipalities as the state's election office codes them in its Landtag results, "
        "with their outlines",
    )
    p.external |= {AUSTRIA, COUNTRY}
    for code, rank, noun in (
        ("at-8.land", 1, "state"),
        ("at-8.bezirk", 2, "political district"),
        ("at-8.gemeinde", 3, "municipality"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRIA, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add("area", id=LAND, scheme_code="at-8.land")
    p.add("area_code", area_id=LAND, code="8")
    p.add("area_parent", area_id=LAND, parent_id=COUNTRY)
    p.name(LAND, "Vorarlberg", "de", "official")
    p.name(LAND, "Vorarlberg", "en", "official", False)
    for code, text in sorted(districts().items()):
        id = key("area", "at-8.bezirk", code)
        p.add("area", id=id, scheme_code="at-8.bezirk")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=LAND)
        p.name(id, text, "de", "official")
    towns = municipalities()
    for code, text in towns.items():
        id = key("area", "at-8.gemeinde", code)
        p.add("area", id=id, scheme_code="at-8.gemeinde")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "at-8.bezirk", district_code(code)))
        p.name(id, text, "de", "official")
    outlines(p, towns)
    return p


def outlines(p: Package, towns: dict[str, str]) -> None:
    """Attach the geoBoundaries 2017 outlines, matched by name, and dissolve them into districts and the state."""
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (Federal Office of Metrology and Survey, Austria)",
        title="geoBoundaries gbOpen Austria ADM3, 2017",
        url="https://www.geoboundaries.org/",
        license="CC BY-SA 2.0",
        license_url="https://creativecommons.org/licenses/by-sa/2.0/",
        attribution="geoBoundaries (William & Mary geoLab), Federal Office of Metrology and Survey, Austria",
    )
    p.artifact(raw.cached(GEOBOUNDARIES, OUTLINES), GEO_SOURCE)
    p.add(
        "boundary_set",
        id=key("boundary_set", "at-8.gemeinde", "2017"),
        scheme_code="at-8.gemeinde",
        vintage="2017",
        valid_from=FIRST,
        as_of=date(2017, 1, 1),
    )
    features = json.loads(raw.cached(GEOBOUNDARIES, OUTLINES).path.read_text("utf-8"))["features"]
    by_name: dict[str, list[Any]] = {}
    for feature in features:
        by_name.setdefault(feature["properties"]["shapeName"], []).append(shapely.geometry.shape(feature["geometry"]))
    shapes: dict[str, Any] = {}
    for code, text in towns.items():
        found = by_name.get(text, [])
        if len(found) != 1:
            raise ValueError(f"{code} {text}: {len(found)} outlines in the geoBoundaries file")
        shapes[code] = found[0]
    parts: dict[str, list[Any]] = {}
    for code, geometry in shapes.items():
        parts.setdefault(district_code(code), []).append(geometry)
    dissolved = {code: shapely.ops.unary_union(group) for code, group in parts.items()}
    units = [(key("area", "at-8.gemeinde", code), geometry) for code, geometry in shapes.items()]
    units += [(key("area", "at-8.bezirk", code), geometry) for code, geometry in dissolved.items()]
    units.append((LAND, shapely.ops.unary_union(list(dissolved.values()))))
    for area, geometry in units:
        if geometry.geom_type == "Polygon":
            geometry = shapely.geometry.MultiPolygon([geometry])
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=FIRST,
            geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=GEO_SOURCE,
        )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Vorarlberg Landtag and the lists on its ballots, as the state's election results app prints them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Amt der Vorarlberger Landesregierung",
        title="Wahlergebnisse Vorarlberg: Landtag",
        url=f"{BASE}/",
        license="Official publication; reuse terms are not stated",
        attribution="Land Vorarlberg",
    )
    for url in (INDEX, AREAS, SEATS, *(edition.json for edition in EDITIONS)):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("body", id=LANDTAG, polity_id=AUSTRIA, kind="unicameral")
    p.name(LANDTAG, "Vorarlberg Landtag", "en", "official")
    p.name(LANDTAG, "Vorarlberger Landtag", "de", "native", False)
    for id, texts in parties().items():
        p.add("party", id=party(id), kind="party", polity_id=AUSTRIA)
        p.add("external_id", entity_id=party(id), scheme="at.vorarlberg.party", value=id)
        for index, text in enumerate(texts):
            p.name(party(id), text, "de", "official", index == 0)
    return p


METHOD = (
    "Results by municipality from the state's election results app, with the state total from the same file. "
    "Postal ballots (Wahlkarten) are counted by political district, not by municipality, and appear as one "
    "postal unit per district; they carry no registered voters."
)


def election(edition: Edition) -> Package:
    day = edition.day
    year = day.year
    data = results(edition)
    p = Package(
        f"{SOURCE}/{edition.key}",
        SOURCE,
        f"The {day.isoformat()} Vorarlberg Landtag election by municipality from the state's election results app",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.uses(raw.cached(SOURCE, edition.json))
    p.uses(raw.cached(SOURCE, AREAS))
    id = key("election", edition.key)
    p.add("election", id=id, polity_id=AUSTRIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Vorarlberg Landtag election", "en", "official")
    p.name(id, f"Landtagswahl {year}", "de", "native", False)
    path = f"at/{day.isoformat()}-vorarlberg-landtag"
    p.add("slug", path=path, entity_id=id, is_current=True)
    contest = key("contest", edition.key)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=LANDTAG,
        district_area_id=LAND,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Vorarlberg Landtag", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug('Landtag')}", entity_id=contest, is_current=True)
    result_set = key("result_set", edition.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    for position, entry in enumerate(data.parties, start=1):
        option = options[entry["parteiID"]] = key("option", edition.key, entry["parteiID"])
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(entry["parteiID"]),
            ballot_position=position,
            ballot_label=entry["bezeichnung"],
        )
    state = data.state
    for field, measure in MEASURES:
        value = getattr(state, field)
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for party_id, votes in state.votes.items():
        p.add("declared", result_set_id=result_set, option_id=options[party_id], measure=1, value=votes)
    if day in seats():
        won = seats()[day]
        shorts = {entry["kurzbezeichnung"]: entry["parteiID"] for entry in data.parties}
        for short, count in won.items():
            if count and short in shorts:
                p.add("outcome", result_set_id=result_set, option_id=options[shorts[short]], kind="won", seats=count)
            elif count:
                raise ValueError(f"{edition.key}: {count} seats for {short}, which is not a list of that election")
    for field, _ in MEASURES:
        total = sum(getattr(row, field) or 0 for row in data.rows)
        declared = getattr(state, field)
        if declared is not None and total != declared:
            p.accept(
                "accounting.declared",
                f"the unit rows add up to {total:,} {field} against {declared:,} for the state in the same file",
            )
    over = [row for row in data.rows if row.registered and row.cast > row.registered]
    if over:
        p.accept(
            "accounting.turnout",
            f"{len(over)} {'municipality' if len(over) == 1 else 'municipalities'} counted more ballots than the "
            "file lists registered voters, as printed",
        )
    for row in data.rows:
        postal = is_postal(row.code)
        area_code = district_code(row.code)
        unit = key("unit", edition.key, row.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code="at-8.bezirk" if postal else "at-8.gemeinde",
            code=f"{day.isoformat()}:{row.code}",
            area_relation="within" if postal else "equals",
            area_id=key("area", "at-8.bezirk", area_code) if postal else key("area", "at-8.gemeinde", row.code),
            kind="postal_district" if postal else "standard",
        )
        p.name(unit, names()[row.code], "de", "official")
        channel = POSTAL_CHANNEL if postal else 0
        for field, measure in MEASURES:
            value = getattr(row, field)
            if value is None or (field == "registered" and postal):
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=channel,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
        for party_id, votes in row.votes.items():
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[party_id],
                channel=channel,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
    return p
