"""Communal ballot-box votes of the City of Zurich, from Statistik Stadt Zürich's open data.

The table "Abstimmungsresultate der Stadt Zürich, seit 1933" holds every vote the city's electorate decided since 1933
at the federal, cantonal, and communal levels. Only the communal rows are loaded here: one row per question and city
district (Stadtkreis) from 2002, and one row for the whole city before. The city's own total is stored as declared.
"""

import csv
import functools
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry
import shapely.ops

from ea import package, raw
from ea.package import Package

SOURCE = "ch.zurich_votes"
TABLE = "https://data.stadt-zuerich.ch/dataset/politik_abstimmungen_seit1933/download/abstimmungen_seit1933.csv"
PAGE = "https://data.stadt-zuerich.ch/dataset/politik_abstimmungen_seit1933"
KREISE = (
    "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Stadtkreise?service=WFS&version=1.1.0&request=GetFeature"
    "&typename=adm_stadtkreise_a&outputFormat=GeoJSON&srsName=EPSG:4326"
)
KREISE_PAGE = "https://data.stadt-zuerich.ch/dataset/geo_stadtkreise"
SWITZERLAND = str(uuid5(package.NAMESPACE, "polity\x1fCH"))
# Rows production already holds: the commune of Zurich (BFS number 0261) and its reporting unit.
CITY_AREA = "01a13b73-f784-72a8-85f0-2a23e50a492d"
CITY_UNIT = "01a13b76-7ce2-7282-94c4-9507aafcb958"
SCHEME = "ch-zh.kreis"
LEVEL = "3"
VOTES, REGISTERED = 1, 2
SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1ftable"))
GEO_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fkreise"))
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1freferendum"))
FIRST = date(2002, 4, 7)
COMBINED = {
    "Kreis 1+2": ("Kreis 1", "Kreis 2"),
    "Kreis 4+5": ("Kreis 4", "Kreis 5"),
    "Kreis 7+8": ("Kreis 7", "Kreis 8"),
}
CHURCH = re.compile(r"^Neuwahl von Pfarrer(in)? ")
TIE_BREAK = re.compile(r"Stichfrage")
COUNTER = re.compile(r"Gegenvorschlag|Gegenentwurf")
INITIATIVE = re.compile(r"(Volks|Einzel)initiative")
METHOD = (
    "Counted by the city's electoral office and published by Statistik Stadt Zürich in its voting database. Registered "
    "voters are the city's eligible voters, and the answers are the ballots marked yes (Ja) and no (Nein). The source "
    "gives the turnout as a rounded percentage only, so the number of ballots cast and of blank and invalid ballots "
    "is not stored. The city's own totals are stored as declared."
)
TIE_NOTE = (
    "A tie-break question lets voters choose between the two proposals that were both put to the vote. The source "
    "gives its counts as yes and no, and the yes count is the vote for proposal A, the first one listed."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class Row:
    day: date
    text: str
    kreis: str
    registered: int | None
    yes: int
    no: int


def table() -> raw.Artifact:
    return raw.cached(SOURCE, TABLE)


def shapes() -> raw.Artifact:
    return raw.cached(SOURCE, KREISE)


def fetch() -> None:
    raw.fetch(SOURCE, TABLE, suffix=".csv")
    raw.fetch(SOURCE, KREISE, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/votes-{day}" for day in days())]


def number(text: str) -> int | None:
    return int(text) if text.strip() else None


@functools.cache
def rows() -> dict[date, dict[str, dict[str, Row]]]:
    """The communal rows by date, question text, and district; the whole city has the empty district."""
    found: dict[date, dict[str, dict[str, Row]]] = defaultdict(lambda: defaultdict(dict))
    with table().path.open(encoding="utf-8-sig", newline="") as file:
        for line in csv.DictReader(file):
            if line["Nr_Politische_Ebene"] != LEVEL or CHURCH.match(line["Abstimmungs_Text"]):
                continue
            day = date.fromisoformat(line["Abstimmungs_Datum"])
            text = clean(line["Abstimmungs_Text"])
            yes, no = number(line["Ja_Absolut"]), number(line["Nein_Absolut"])
            if yes is None or no is None:
                raise ValueError(f"{day} {text}: no counts")
            kreis = clean(line["Name_Wahlkreis_StZH"])
            if kreis in found[day][text]:
                raise ValueError(f"{day} {text} {kreis}: twice")
            found[day][text][kreis] = Row(day, text, kreis, number(line["Stimmberechtigt"]), yes, no)
    return found


def days() -> list[date]:
    return sorted(rows())


def kind(text: str) -> str:
    if TIE_BREAK.search(text):
        return "tie_break"
    if COUNTER.search(text):
        return "counter_proposal"
    if INITIATIVE.search(text):
        return "initiative"
    return "referendum"


def kreise() -> list[str]:
    names = {row.kreis for day in rows().values() for text in day.values() for row in text.values() if row.kreis}
    return sorted(names, key=lambda name: [int(part) for part in re.findall(r"\d+", name)])


def area(name: str) -> str:
    return key("area", SCHEME, name)


def unit(name: str) -> str:
    return key("unit", SCHEME, name)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The city districts (Stadtkreise) of Zurich, with the combined districts the city reports for its votes",
    )
    p.external |= {SWITZERLAND, CITY_AREA}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Statistik Stadt Zürich",
        title="Abstimmungsresultate der Stadt Zürich, seit 1933",
        url=PAGE,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Statistik Stadt Zürich, Präsidialdepartement der Stadt Zürich",
    )
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="official_body",
        publisher="GIS-Zentrum, Geomatik + Vermessung, Tiefbau- und Entsorgungsdepartement der Stadt Zürich",
        title="Stadtkreise",
        url=KREISE_PAGE,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Stadt Zürich, Geomatik + Vermessung",
    )
    p.artifact(table(), SOURCE_ID)
    p.artifact(shapes(), GEO_SOURCE)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=SWITZERLAND,
        kind="administrative",
        rank=3,
        noun="city district",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, shapes().retrieved_at.year),
        scheme_code=SCHEME,
        vintage=str(shapes().retrieved_at.year),
        valid_from=FIRST,
        valid_to=None,
        as_of=shapes().retrieved_at.date(),
    )
    found: dict[str, shapely.geometry.base.BaseGeometry] = {}
    for feature in json.loads(shapes().path.read_text("utf-8"))["features"]:
        found[feature["properties"]["bezeichnung"]] = shapely.geometry.shape(feature["geometry"])
    for combined, parts in COMBINED.items():
        found[combined] = shapely.ops.unary_union([found[part] for part in parts])
    for name in kreise():
        shape = found[name]
        if shape.geom_type == "Polygon":
            shape = shapely.geometry.MultiPolygon([shape])
        id = area(name)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=name.removeprefix("Kreis "))
        p.add("area_parent", area_id=id, parent_id=CITY_AREA)
        p.name(id, name, "de", "official")
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=FIRST,
            valid_to=None,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=GEO_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system of Zurich's communal votes and its city district reporting units",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {SWITZERLAND, CITY_UNIT}
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=SWITZERLAND,
        valid_from=date(1933, 1, 1),
        valid_to=None,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="A question put to the city's voters is decided by the votes for and against it.",
        lang="en",
    )
    for name in kreise():
        p.add(
            "reporting_unit",
            id=unit(name),
            polity_id=SWITZERLAND,
            scheme_code=SCHEME,
            code=name.removeprefix("Kreis "),
            parent_id=CITY_UNIT,
            area_relation="equals",
            area_id=area(name),
            kind="standard",
        )
    return p


def election(day: date) -> Package:
    p = Package(
        f"{SOURCE}/votes-{day}",
        SOURCE,
        f"The communal ballot-box votes of the City of Zurich on {day}",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {SWITZERLAND, CITY_AREA, CITY_UNIT}
    p.uses(table())
    election_id = key("election", day)
    p.add(
        "election", id=election_id, polity_id=SWITZERLAND, kind="referendum", date_from=day, date_to=day, status="held"
    )
    p.name(election_id, f"Zurich communal votes, {day:%-d %B %Y}", "en", "official")
    p.name(election_id, f"Gemeindeabstimmungen der Stadt Zürich vom {day:%d.%m.%Y}", "de", "native", False)
    path = f"ch/{day}-zurich-communal-votes"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values: dict[str, list[object]] = {
        name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
    }

    def fact(result_set: str, unit_id: str, option: str | None, measure: int, value: int) -> None:
        for column, item in zip(values.values(), (result_set, unit_id, option, measure, value), strict=True):
            column.append(item)

    differs: list[str] = []
    for number_, (text, by_kreis) in enumerate(sorted(rows()[day].items()), 1):
        total = by_kreis[""]
        kind_ = kind(text)
        contest = key("contest", day, text)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question",
            office_id=None,
            body_id=None,
            district_area_id=CITY_AREA,
            magnitude=None,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, text, "de", "native")
        p.add("slug", path=f"{path}/{number_}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type=kind_)
        result_set = key("result_set", day, text)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        if kind_ == "tie_break":
            p.add("note", id=key("note", contest), entity_id=contest, kind="context", body_md=TIE_NOTE, lang="en")
        labels = ("Proposal A", "Proposal B") if kind_ == "tie_break" else ("Yes", "No")
        options: list[str] = []
        for position, label in enumerate(labels, 1):
            option = key("option", day, text, label)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(option, label, "en", "official")
        districts = {name: row for name, row in by_kreis.items() if name}
        if districts:
            for name, row in districts.items():
                if row.registered is not None:
                    fact(result_set, unit(name), None, REGISTERED, row.registered)
                fact(result_set, unit(name), options[0], VOTES, row.yes)
                fact(result_set, unit(name), options[1], VOTES, row.no)
            if total.registered is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=total.registered)
            p.add("declared", result_set_id=result_set, option_id=options[0], measure=VOTES, value=total.yes)
            p.add("declared", result_set_id=result_set, option_id=options[1], measure=VOTES, value=total.no)
            sums = (
                sum(r.registered or 0 for r in districts.values()),
                sum(r.yes for r in districts.values()),
                sum(r.no for r in districts.values()),
            )
            declared = (total.registered if total.registered is not None else sums[0], total.yes, total.no)
            if sums != declared:
                differs.append(f"{text[:60]}: districts {sums}, city {declared}")
        else:
            if total.registered is not None:
                fact(result_set, CITY_UNIT, None, REGISTERED, total.registered)
            fact(result_set, CITY_UNIT, options[0], VOTES, total.yes)
            fact(result_set, CITY_UNIT, options[1], VOTES, total.no)
        winner = 0 if total.yes > total.no else 1
        for index, option in enumerate(options):
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if index == winner else "lost")
    if differs:
        p.accept(
            "accounting.declared",
            "The city's printed total differs from the sum of its districts as the source prints them (registered, "
            "yes, no): " + "; ".join(differs),
        )
        print(f"{p.key}: {len(differs)} totals differ from their districts", flush=True)
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p
