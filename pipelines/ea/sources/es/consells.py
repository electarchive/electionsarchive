"""Consells Insulars de Mallorca, Menorca, Eivissa and Formentera, 2007 to 2023, from the Govern's result PDFs.

The Govern de les Illes Balears publishes one PDF per election on its electoral-processes site. Each holds, for each
of the four islands, the electorate, abstention, ballots cast, null, valid and blank ballots, and every list's votes,
share and seats, credited to IBESTAT. The PDFs go no finer than the island, so the island is the reporting unit. The
Formentera pages print no seat column in any year, so the votes of Formentera are stored without declared seats.

The archive already holds the island areas and reporting units of Mallorca and Menorca (the Senate's `es.isla`
scheme). It holds Eivissa and Formentera only as one joint Senate constituency, so the `geo` package adds the two
islands on their own, with outlines from geoBoundaries' Spain ADM3 set (CC BY 4.0).
"""

import json
import subprocess
from datetime import date
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.es.baleares import Block, dhondt, parse_page, party_key, split

SOURCE = "es.consells"
PAGE = "https://www.caib.es/sites/processoselectorals/ca/resultats_als_consells_insulars-64578/"
PUBLISHER = "Govern de les Illes Balears"
TITLE = "Eleccions als Consells Insulars: resultats (Direcció General d'Interior i IBESTAT)"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
REGION = str(uuid5(package.NAMESPACE, "polity\x1fES-IB"))
# Rows the archive already holds (es.ine and es.senado): the province, and the Mallorca and Menorca islands.
PROVINCE_AREA = "e9beb324-6348-5663-8077-5ebbcd4a00e1"
PROVINCE_UNIT = "48d7e463-2475-5da0-8dcd-bc5b37ffb674"
HELD = {
    "mallorca": ("6cf75192-6d84-530c-bb35-38f8ddb354ad", "0c336e4f-3789-5e38-9594-68dc6b71622a"),
    "menorca": ("8d2ae71c-066b-523e-a5eb-1370754355c1", "cefcccb7-95d9-56e4-9df8-4ead85f895ec"),
}
GEOBOUNDARIES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ESP/ADM3/"
    "geoBoundaries-ESP-ADM3.geojson"
)
SCHEME = "es.isla"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
THRESHOLD = 5
IDS = {2007: 281298, 2011: 281381, 2015: 281319, 2019: 417056, 2023: 551686}
DAYS = {
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2015: date(2015, 5, 24),
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
}
# Island: (name, statutory number of councillors).
ISLANDS = {
    "mallorca": ("Mallorca", 33),
    "menorca": ("Menorca", 13),
    "eivissa": ("Eivissa", 13),
    "formentera": ("Formentera", 13),
}
SCOPES = {"MALLORCA": "mallorca", "MENORCA": "menorca", "EIVISSA": "eivissa", "FORMENTERA": "formentera"}
# The two islands the archive holds only as the joint Senate constituency 073, coded under it.
NEW = {"eivissa": "0731", "formentera": "0732"}
# The PDF prints 43,435 valid ballots on Eivissa in 2015, but 1,070 blank and 42,355 to lists make 43,425 (and the 43,969
# ballots cast less 544 null make the same); the printed valid total is not used.
DISCREPANT = {(2015, "eivissa")}
SYSTEM = (
    "Each of the four islands elects its consell insular as a single constituency, on the day of the municipal and "
    "regional elections, by closed party lists. Seats are shared by the D'Hondt method among the lists with at least "
    "5% of the valid votes cast on the island, blank ballots counted as valid. The councils have 33 members in "
    "Mallorca and 13 in each of Menorca, Eivissa and Formentera."
)
METHOD = (
    "Counted per island, as the Govern de les Illes Balears publishes it from IBESTAT's tables. Registered voters "
    "are the electoral roll, ballots cast are the ballots in the boxes, null ballots are stored as invalid, and blank "
    "ballots are stored apart. Valid votes here are the votes to candidacies, so the ballots cast equal valid plus "
    "blank plus null. The PDF goes no finer than the island."
)
NO_SEATS = (
    " The PDF prints no seat column for Formentera, so no seats are stored for its lists; the council has 13 members."
)


def pdf_url(year: int) -> str:
    return f"{PAGE}archivopub.do?ctrl=MCRST10385ZI{IDS[year]}&id={IDS[year]}"


def fetch() -> None:
    for year in IDS:
        raw.fetch(SOURCE, pdf_url(year), suffix=".pdf")
    raw.fetch(SOURCE, GEOBOUNDARIES, suffix=".geojson")


def text(year: int) -> str:
    path = raw.cached(SOURCE, pdf_url(year)).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


class Election:
    def __init__(self, year: int, islands: dict[str, Block]) -> None:
        self.year = year
        self.islands = islands


def load(year: int) -> Election:
    islands: dict[str, Block] = {}
    for page in text(year).split("\f"):
        parsed = parse_page(page, year)
        if parsed is None:
            continue
        scope, block = parsed
        # Numbers on the pie charts can read as a legend row with no votes.
        block.rows = [row for row in block.rows if row.votes]
        islands[SCOPES[scope]] = block
    if sorted(islands) != sorted(ISLANDS):
        raise ValueError(f"{year}: islands {sorted(islands)}")
    for code, block in islands.items():
        stats, rows = block.stats, block.rows
        if (
            sum(r.votes for r in rows) != stats["candidacies"]
            or stats["registered"] - stats["abstention"] != stats["ballots"]
            or (
                (year, code) not in DISCREPANT
                and (
                    stats["ballots"] != stats["invalid"] + stats["valid"]
                    or stats["valid"] != stats["blank"] + stats["candidacies"]
                )
            )
            or stats["ballots"] != stats["invalid"] + stats["blank"] + stats["candidacies"]
        ):
            raise ValueError(f"{year} {code}: figures do not add up {stats} {sum(r.votes for r in rows)}")
        seats = sum(r.seats for r in rows)
        if code != "formentera" and seats != ISLANDS[code][1]:
            raise ValueError(f"{year} {code}: seats {seats}")
        if code == "formentera" and seats:
            raise ValueError(f"{year}: Formentera now prints seats")
    return Election(year, islands)


def source_id() -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fcaib"))


def geo_source_id() -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fgeoboundaries"))


def area_id(code: str) -> str:
    return HELD[code][0] if code in HELD else str(uuid5(package.NAMESPACE, f"{SOURCE}\x1farea\x1f{SCHEME}\x1f{code}"))


def unit_id(code: str) -> str:
    return HELD[code][1] if code in HELD else str(uuid5(package.NAMESPACE, f"{SOURCE}\x1funit\x1f{SCHEME}\x1f{code}"))


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        attribution="Font: Govern de les Illes Balears, a partir de les dades de l'Institut d'Estadística de les Illes Balears (IBESTAT).",
    )


def outlines() -> dict[str, shapely.MultiPolygon]:
    """Outlines of Eivissa and Formentera: the union of each island's municipalities in geoBoundaries."""
    features = json.loads(raw.cached(SOURCE, GEOBOUNDARIES).path.read_bytes())["features"]
    parts: dict[str, list[shapely.Geometry]] = {"eivissa": [], "formentera": []}
    for feature in features:
        shape = shapely.geometry.shape(feature["geometry"])
        x, y = shape.centroid.x, shape.centroid.y
        if not (1.0 < x < 1.7 and 38.5 < y < 39.2):
            continue
        parts["formentera" if str(feature["properties"]["shapeName"]) == "Formentera" else "eivissa"].append(
            shapely.make_valid(shape)
        )
    if {code: len(found) for code, found in parts.items()} != {"eivissa": 5, "formentera": 1}:
        raise ValueError(f"municipalities per island: { {c: len(f) for c, f in parts.items()} }")
    found: dict[str, shapely.MultiPolygon] = {}
    for code, geoms in parts.items():
        union = shapely.union_all(geoms).simplify(0.0003)
        polygons = [
            q
            for part in shapely.get_parts(union)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        found[code] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The islands of Eivissa and Formentera on their own, as island areas and reporting units, with outlines from "
        "geoBoundaries",
    )
    p.external |= {SPAIN, PROVINCE_AREA, PROVINCE_UNIT, SCHEME}
    p.add(
        "source",
        id=geo_source_id(),
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Spain ADM3 (municipalities), 2018",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), from the Instituto Geográfico Nacional's municipal boundaries",
    )
    p.artifact(raw.cached(SOURCE, GEOBOUNDARIES), geo_source_id())
    shapes = outlines()
    for code, island_code in NEW.items():
        name = ISLANDS[code][0]
        area = area_id(code)
        p.add("area", id=area, scheme_code=SCHEME)
        p.add("area_code", area_id=area, code=island_code)
        p.name(area, name, "ca", "official")
        p.add("area_parent", area_id=area, parent_id=PROVINCE_AREA)
        shape = shapes[code]
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=None,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=geo_source_id(),
        )
        p.add(
            "reporting_unit",
            id=unit_id(code),
            polity_id=SPAIN,
            scheme_code=SCHEME,
            code=island_code,
            parent_id=PROVINCE_UNIT,
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
    return p


def elections() -> list[Election]:
    return [load(year) for year in DAYS]


def labels(all_: list[Election]) -> dict[str, str]:
    """Each party's key with its name as the latest PDF printing it gives it."""
    found: dict[str, str] = {}
    for e in sorted(all_, key=lambda e: -e.year):
        for block in e.islands.values():
            for row in block.rows:
                found.setdefault(party_key(row.label), split(row.label)[1])
    return found


def registry(all_: list[Election]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The four Consells Insulars of the Balearic Islands, their electoral system and size, the Govern's result "
        "PDFs as a source, and the lists that stood in the elections of 2007 to 2023",
    )
    p.external.add(REGION)
    add_source(p)
    day = DAYS[min(DAYS)]
    system = p.id("electoral_system", "consells")
    p.add(
        "electoral_system",
        id=system,
        polity_id=REGION,
        rules={
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD},
        },
        rules_version=1,
    )
    p.name(system, "Consell Insular island D'Hondt system with a 5% threshold", "en", "official")
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM, lang="en")
    for code, (name, seats) in ISLANDS.items():
        body = p.id("body", code)
        p.add("body", id=body, polity_id=REGION, kind="council")
        p.name(body, f"Consell Insular de {name}", "ca", "native")
        p.name(body, f"Island Council of {name}", "en", "official")
        p.add("body_size", body_id=body, seats=seats, valid_from=day, valid_to=None)
    for key, name in sorted(labels(all_).items()):
        party = p.id("party", key)
        p.add("party", id=party, kind="party", polity_id=REGION)
        p.name(party, name, "ca", "official")
        p.add("external_id", entity_id=party, scheme=f"{SOURCE}.party", value=key)
    return p


def build(e: Election) -> Package:
    year, day = e.year, DAYS[e.year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} elections to the four Consells Insulars, with the votes and seats of every list on each island",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {SPAIN, REGION, *(area_id(c) for c in HELD), *(unit_id(c) for c in HELD)}
    src = source_id()
    p.artifact(raw.cached(SOURCE, pdf_url(year)), src)
    system = p.id("electoral_system", "consells")
    eid = p.id("election", day)
    p.add("election", id=eid, polity_id=REGION, kind="local", date_from=day, date_to=day, status="held")
    p.name(eid, f"{year} Balearic island council elections", "en", "official")
    p.name(eid, f"Eleccions als Consells Insulars de {year}", "ca", "native")
    path = f"es/ib/{day}-consells-insulars"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    values: list[tuple[str, str, str | None, int, int, int]] = []
    for code, (name, magnitude) in ISLANDS.items():
        block = e.islands[code]
        stats = block.stats
        contest = p.id("contest", str(day), code)
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="list_allocation",
            body_id=p.id("body", code),
            district_area_id=area_id(code),
            magnitude=magnitude,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"Consell Insular de {name}", "ca", "official")
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
        result_set = p.id("result_set", str(day), code)
        options: dict[str, str] = {}
        votes: dict[str, int] = {}
        declared: dict[str, int] = {}
        for row in sorted(block.rows, key=lambda r: -r.votes):
            key = party_key(row.label)
            if key in options:
                raise ValueError(f"{year} {code}: {key} twice")
            options[key] = p.id("option", f"{day}:{code}:{key}")
            votes[key], declared[key] = row.votes, row.seats
            p.add(
                "contest_option",
                id=options[key],
                contest_id=contest,
                kind="list",
                party_id=p.id("party", key),
                ballot_label=row.label,
            )
        note = METHOD
        if code == "formentera":
            note += NO_SEATS
        else:
            won = dhondt(votes, stats["blank"] + stats["candidacies"], magnitude)
            agree = all(won[k] == declared[k] for k in options)
            if not agree:
                note += (
                    " D'Hondt with a 5% threshold applied to these votes gives a different split from the declared "
                    "seats, which are stored as published."
                )
            print(f"{year} {code} seats={magnitude} lists={len(options)} dhondt_ok={agree}")
        p.add(
            "result_set",
            id=result_set,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=src,
            is_default=True,
            completeness=1,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        unit = unit_id(code)
        values += [
            (result_set, unit, None, REGISTERED, stats["registered"], 0),
            (result_set, unit, None, BALLOTS, stats["ballots"], 0),
            (result_set, unit, None, VALID, stats["candidacies"], 0),
            (result_set, unit, None, INVALID, stats["invalid"], 0),
            (result_set, unit, None, BLANK, stats["blank"], 0),
        ]
        for key, option in options.items():
            values.append((result_set, unit, option, VOTES, votes[key], 0))
            if declared[key]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=declared[key])
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
                "channel": pl.Int16,
            },
            orient="row",
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    all_ = elections()
    geo().write()
    registry(all_).write()
    for e in all_:
        build(e).write()


def emit() -> None:
    package.run("load", packages())
