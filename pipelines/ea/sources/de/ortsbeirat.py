"""Sub-municipal council elections in Germany: the Munich Bezirksausschüsse of 2020 and 2026.

The Kreisverwaltungsreferat publishes the official final result of each Bezirksausschusswahl as a ZIP of two CSV
files, one row per Stimmbezirk (voting district) and one per Stadtbezirk, and the Vermessungsamt publishes the 25
Stadtbezirk outlines as open data. Each Stadtbezirk elects its own Bezirksausschuss, so each is a polity with a
council body, and each election holds one contest per Stadtbezirk.
"""

import csv
import io
import json
import zipfile
from datetime import date
from uuid import uuid5

import numpy
import pyproj
import shapely
import shapely.geometry

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "de.ortsbeirat"
GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
BAVARIA = "01a13bb5-c563-741b-83f7-4b7cdad043bd"
MUNICH_KREIS = "09162"
ELECTIONS = {
    2020: (
        date(2020, 3, 15),
        "https://www.wahlen-muenchen.de/ergebnisse/20200315bezirksausschusswahl/BAW20amtlichesErg.zip",
        1000,
    ),
    2026: (
        date(2026, 3, 8),
        "https://www.wahlen-muenchen.de/ergebnisse/20260308oberbuergermeisterwahl/endergebnis_bezirksausschusswahlen.zip",
        100,
    ),
}
OUTLINES = (
    "https://geoportal.muenchen.de/geoserver/gsm_wfs/ows?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=gsm_wfs:vablock_stadtbezirk&outputFormat=application/json"
)
SCHEME_BEZIRK = "de.muc_stadtbezirk"
SCHEME_STIMMBEZIRK = "de.muc_stimmbezirk"
OPEN_DATA = "https://opendata.muenchen.de/"
METHOD = (
    "Counted by the Kreisverwaltungsreferat of the City of Munich and published for each Stimmbezirk (voting "
    "district, including the postal-vote districts) and each Stadtbezirk. Each voter has as many votes as the "
    "Bezirksausschuss has seats, may give a candidate up to three of them, and may spread them over several lists, "
    "so a list's votes are the sum of the votes its candidates received and are far larger than the number of "
    "voters. Registered voters are the electoral roll, voters are those who took part, and invalid ballots are the "
    "ballot papers that were invalid. The source prints no count of valid ballots, and none is stored. A list that "
    "received no votes in a voting district is printed with a dash and has no value there. The seats each list won "
    "are not in the downloadable tables and are not stored here."
)
SYSTEM_NOTE = (
    "Each voter has as many votes as the Bezirksausschuss has seats, up to three per candidate, and may split them "
    "between lists (panachage). Seats are shared among the lists by the Sainte-Laguë/Schepers method on the sum of "
    "their candidates' votes, with no threshold, and each list's seats go to its candidates in the order of their "
    "votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def polity(number: int | None = None) -> str:
    return key("polity", "muenchen" if number is None else f"stadtbezirk-{number}")


def area(number: int) -> str:
    return key("area", SCHEME_BEZIRK, number)


def kreis() -> str:
    """The id production holds for the kreisfreie Stadt München, from the reference partition."""
    import polars as pl

    folder = DATA / "reference" / "de"
    areas = pl.read_parquet(folder / "area.parquet")
    codes = pl.read_parquet(folder / "area_code.parquet")
    found = areas.join(codes, left_on="id", right_on="area_id").filter(
        (pl.col("scheme_code") == "de.kreis") & (pl.col("code") == MUNICH_KREIS)
    )
    return str(found["id"][0])


def fetch() -> None:
    for _, link, _ in ELECTIONS.values():
        raw.fetch(SOURCE, link, suffix=".zip")
    raw.fetch(SOURCE, OUTLINES, suffix=".json")


def table(year: int, name: str) -> list[list[str]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, ELECTIONS[year][1]).path)
    text = archive.read(f"baw.{name}.stimmen.csv").decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=";"))[1:]


def count(cell: str) -> int | None:
    return None if cell in ("", "-") else int(cell)


def stadtbezirke() -> dict[int, tuple[str, shapely.Geometry]]:
    data = json.loads(raw.cached(SOURCE, OUTLINES).path.read_text())
    project = pyproj.Transformer.from_crs(25832, 4326, always_xy=True)
    parts: dict[int, list[shapely.Geometry]] = {}
    names: dict[int, str] = {}
    for feature in data["features"]:
        number = int(feature["properties"]["sb_nummer"])
        names[number] = " ".join(str(feature["properties"]["sb_name"]).split())
        parts.setdefault(number, []).append(shapely.geometry.shape(feature["geometry"]))
    out: dict[int, tuple[str, shapely.Geometry]] = {}
    for number in sorted(parts):
        merged = shapely.union_all(parts[number], grid_size=0.01)
        moved = shapely.transform(merged, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
        valid = [g for g in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(g) in (3, 6)]
        shape = shapely.simplify(shapely.MultiPolygon(list(shapely.get_parts(valid))), 0.00002)
        out[number] = (names[number], shapely.set_srid(shape, 4326))
    return out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 25 Stadtbezirke of Munich, from the Landeshauptstadt München's open data",
    )
    p.external |= {kreis(), GERMANY}
    source = p.id("source", "outlines")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Landeshauptstadt München, Kreisverwaltungsreferat, GeodatenService",
        title="Stadtbezirke München (Verwaltungsgliederung)",
        url="https://opendata.muenchen.de/dataset/vablock_stadtbezirke_opendata",
        license="Datenlizenz Deutschland, Namensnennung, Version 2.0",
        license_url="https://www.govdata.de/dl-de/by-2-0",
        attribution="Landeshauptstadt München, Kreisverwaltungsreferat, GeodatenService",
    )
    artifact = raw.cached(SOURCE, OUTLINES)
    p.artifact(artifact, source)
    p.add(
        "scheme",
        code=SCHEME_BEZIRK,
        polity_id=GERMANY,
        kind="administrative",
        rank=40,
        noun="Munich city district",
        code_system=None,
    )
    p.add(
        "scheme",
        code=SCHEME_STIMMBEZIRK,
        polity_id=GERMANY,
        kind="precinct",
        rank=50,
        noun="Munich voting district",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME_BEZIRK, artifact.retrieved_at.year),
        scheme_code=SCHEME_BEZIRK,
        vintage=str(artifact.retrieved_at.year),
        as_of=artifact.retrieved_at.date(),
    )
    for number, (text, shape) in stadtbezirke().items():
        id = area(number)
        p.add("area", id=id, scheme_code=SCHEME_BEZIRK)
        p.add("area_code", area_id=id, code=str(number))
        p.add("area_parent", area_id=id, parent_id=kreis())
        p.name(id, text, "de", "official")
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=source,
        )
    return p


def label(text: str) -> str:
    return " ".join(text.split())


def party(text: str) -> str:
    return key("party", label(text).casefold())


def lists(rows: list[list[str]]) -> dict[str, dict[str, list[int]]]:
    """For each Stadtbezirk (by its printed number), its lists by printed label and the columns that carry each.

    A list printed in several columns, as some lists are in 2026 where the postal-vote votes sit in a column of
    their own, is one list, and its votes are the sum of those columns.
    """
    out: dict[str, dict[str, list[int]]] = {}
    for row in rows:
        found: dict[str, list[int]] = {}
        for i in range(10, len(row) - 2, 3):
            if row[i] and count(row[i + 1]) is not None:
                found.setdefault(label(row[i]), []).append(i + 1)
        out[row[1]] = found
    return out


def total(row: list[str], columns: list[int]) -> int:
    return sum(value for column in columns if (value := count(row[column])) is not None)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Munich as a municipality with its 25 Stadtbezirk polities and Bezirksausschüsse, the electoral system, and "
        "the parties and joint lists that stood",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {GERMANY, BAVARIA, kreis()}
    p.external |= {area(n) for n in stadtbezirke()}
    source = key("source", "wahlen")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Landeshauptstadt München, Kreisverwaltungsreferat (Wahlamt)",
        title="Wahlen in der Landeshauptstadt München: Ergebnisse",
        url="https://www.wahlen-muenchen.de/",
        license="Datenlizenz Deutschland, Namensnennung, Version 2.0",
        license_url="https://www.govdata.de/dl-de/by-2-0",
        attribution="Landeshauptstadt München, Kreisverwaltungsreferat",
    )
    for _, link, _ in ELECTIONS.values():
        p.artifact(raw.cached(SOURCE, link), source)
    muc = polity()
    p.add("polity", id=muc, kind="municipality", iso_code=None)
    p.add("polity_parent", polity_id=muc, parent_id=BAVARIA)
    p.add("polity_territory", polity_id=muc, area_id=kreis())
    p.name(muc, "Munich", "en", "official")
    p.name(muc, "München", "de", "native", False)
    p.add("external_id", entity_id=muc, scheme="de.ags", value="09162000")
    for number, (text, _) in stadtbezirke().items():
        id = polity(number)
        p.add("polity", id=id, kind="submunicipal", iso_code=None)
        p.add("polity_parent", polity_id=id, parent_id=muc)
        p.add("polity_territory", polity_id=id, area_id=area(number))
        p.name(id, f"{number} - {text}", "de", "official")
        p.name(id, f"Munich city district {number}, {text}", "en", "official", False)
        body = key("body", number)
        p.add("body", id=body, polity_id=id, kind="council")
        p.name(body, f"Bezirksausschuss {number} {text}", "de", "official")
        p.name(body, f"{text} District Committee", "en", "official", False)
    system = key("electoral_system", "sainte_lague_schepers")
    p.add(
        "electoral_system",
        id=system,
        polity_id=GERMANY,
        valid_from=date(2020, 1, 1),
        valid_to=None,
        rules={"formula": "sainte_lague_schepers"},
        rules_version=1,
    )
    p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    seen: dict[str, str] = {}
    for year in ELECTIONS:
        for entries in lists(table(year, "stadtbezirke")).values():
            for text in entries:
                seen.setdefault(text.casefold(), text)
    for folded, text in sorted(seen.items()):
        id = party(text)
        p.add(
            "party",
            id=id,
            kind="bloc" if " / " in text and not text.startswith("FREIE WÄHLER / FW München") else "party",
            polity_id=GERMANY,
        )
        p.add("external_id", entity_id=id, scheme="de.ortsbeirat.list", value=folded)
        p.name(id, text, "de", "official")
    return p


def election(year: int) -> Package:
    day, link, base = ELECTIONS[year]
    p = Package(
        f"{SOURCE}/muc-ba-{year}",
        SOURCE,
        f"The {year} Munich Bezirksausschuss elections, one contest per Stadtbezirk, by voting district",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {GERMANY, polity()}
    p.external |= {area(n) for n in stadtbezirke()}
    p.external |= {key("body", n) for n in stadtbezirke()}
    p.external.add(key("electoral_system", "sainte_lague_schepers"))
    p.external.add(key("source", "wahlen"))
    p.external |= {party(t) for entries in lists(table(year, "stadtbezirke")).values() for t in entries}
    p.uses(raw.cached(SOURCE, link))
    source = key("source", "wahlen")
    election_id = key("election", year)
    muc = polity()
    p.add("election", id=election_id, polity_id=muc, kind="local", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{year} Munich district committee elections", "en", "official")
    p.name(election_id, f"Bezirksausschusswahlen München {year}", "de", "native", False)
    path = f"de/{day}-munich-district-committees"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    stadt = table(year, "stadtbezirke")
    stimm = table(year, "stimmbezirke")
    names = {n: text for n, (text, _) in stadtbezirke().items()}
    printed = lists([row for row in stadt if row[2] == "1"])
    sets: dict[int, str] = {}
    options: dict[tuple[int, str], str] = {}
    for number, entries in sorted((int(k), v) for k, v in printed.items() if k != "162"):
        contest = key("contest", year, number)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=key("body", number),
            district_area_id=area(number),
            round_number=1,
            electoral_system_id=key("electoral_system", "sainte_lague_schepers"),
            default_result_kind="counted",
        )
        title = f"{names[number]} District Committee"
        p.name(contest, f"{year} {title}", "en", "official")
        p.name(contest, f"Bezirksausschuss {number} {names[number]} {year}", "de", "native", False)
        p.add("slug", path=f"{path}/{slug(f'{number}-{names[number]}')}", entity_id=contest, is_current=True)
        result_set = sets[number] = key("result_set", year, number)
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
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for text in entries:
            option = options[(number, text)] = key("option", year, number, text)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(text),
                ballot_label=text,
            )
    units: dict[str, str] = {}
    for row in stimm:
        if row[1] == "162":
            continue
        number = int(row[1]) // base
        unit = units[row[1]] = key("unit", year, row[1])
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=GERMANY,
            scheme_code=SCHEME_STIMMBEZIRK,
            code=f"{day}:{row[1]}",
            area_relation="within",
            area_id=area(number),
            kind="postal_district" if row[2] == "21" else "standard",
        )
        for measure, cell in ((2, row[3]), (3, row[7]), (5, row[9])):
            if measure == 2 and row[2] == "21":
                continue
            _value(p, sets[number], unit, None, measure, count(cell))
        for text, columns in printed[str(number)].items():
            found = [column for column in columns if count(row[column]) is not None]
            if found:
                _value(p, sets[number], unit, options[(number, text)], 1, total(row, found))
    for row in stadt:
        if row[1] == "162" or row[2] != "1":
            continue
        number = int(row[1])
        for measure, cell in ((2, row[3]), (3, row[7]), (5, row[9])):
            _declared(p, sets[number], None, measure, count(cell))
        for text, columns in printed[str(number)].items():
            _declared(p, sets[number], options[(number, text)], 1, total(row, columns))
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def _declared(p: Package, result_set: str, option: str | None, measure: int, value: int | None) -> None:
    if value is not None:
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/muc-ba-{year}" for year in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in ELECTIONS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())
