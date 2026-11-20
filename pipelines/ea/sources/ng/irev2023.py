"""Nigeria's presidential election of 25 February 2023 by polling unit, from the Commission's result viewing portal.

The portal (IReV) lists, for each local government area and registration area (ward), every polling unit with its
code and the scan of its Form EC8A, the statement of result of the poll that the presiding officer signs and
uploads. Its API answers `.../elections/<id>/lga` with the areas and wards and `.../elections/<id>/pus?ward=<id>`
with a ward's polling units and their documents. Each scan is a phone photograph of the form, with the numbers
written by hand: a JPEG on the portal's image cache, or a one-page PDF on DigitalOcean Spaces (the portal's own
document host has lapsed, so the Spaces copy is fetched).

The sheets were read by eye from the page images, twice and independently, by two different multimodal models, and
the two readings were compared field by field. Every disagreement over a party's votes, the total valid votes, or
whether a sheet belongs to its polling unit was settled by a third look at the sheet. A disagreement over rows 1 to
8 was settled by the sheet's own arithmetic (spoiled plus rejected plus valid equal the used ballot papers, or the
parties' votes equal row 7) where that picks one reading, and is otherwise left unread. The readings, with each
figure's box on the page image, are kept in `irev2023/<state>-<lga>.tsv`, where `?` marks a figure left unread; it
is not stored. The parties' votes should add up to the total valid votes, which row 7 and the foot of the table
both give; a sheet that fails this is stored as written, with a caveat on the result set. A sheet with no readable
figures, or a scan that is not the presidential EC8A of its own polling unit, is left out.

Areas are the Commission's: local government areas (with outlines from geoBoundaries, which take them from GRID3)
and registration areas, coded as the Commission codes them (`state/lga/ward`), and polling units coded
`state/lga/ward/unit`.
"""

import csv
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.raw import Artifact
from ea.store import DATA

SOURCE = "ng.irev"
API = "https://dolphin-app-sleqh.ondigitalocean.app/api/v1/elections/63f8f25b594e164f8146a213"
PORTAL = "https://www.inecelectionresults.ng/"
BUCKET = "https://irev-results.lon1.digitaloceanspaces.com/1292/"
SHAPES = "ng.geoboundaries"
GRID3 = "ng.grid3"
LGAS = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/NGA/ADM2/"
    "geoBoundaries-NGA-ADM2_simplified.geojson"
)
WARDS = (
    "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/GRID3_NGA_operational_wards_v4_0/FeatureServer/"
    "0/query?where=statecode%3D%27{}%27&outFields=state,statecode,lga,ward&geometryPrecision=5"
    "&maxAllowableOffset=0.0002&outSR=4326&f=geojson"
)
DAY = date(2023, 2, 25)
NIGERIA = str(uuid5(package.NAMESPACE, "polity\x1fNG"))
READINGS = Path(__file__).parent / "irev2023"
# Rows written by the `ng.inec` pipeline: the contest, its options by ballot number, its election, and the states.
INEC = "ng.inec"
TAG = "2023:president"
PARTIES = [
    "A", "AA", "AAC", "ADC", "ADP", "APC", "APGA", "APM", "APP",
    "BP", "LP", "NNPP", "NRM", "PDP", "PRP", "SDP", "YPP", "ZLP",
]  # fmt: skip
# The sheet's rows 1 to 8, by the archive's measure codes. Row 3 (ballot papers issued to the polling unit), row 4
# (unused), and row 5 (spoiled) have no measure and are kept in the readings only.
REGISTERED, VOTERS, CAST, VALID, INVALID, VOTES = 2, 14, 3, 4, 5, 1
DIGITIZED = 8
# The states sampled so far, by the Commission's two-digit code and the archive's state code.
STATES = {"06": ("BY", "Bayelsa")}
# The local government areas read so far, with geoBoundaries' name for each, and GRID3's name for each of its wards by
# the Commission's ward code.
READ = {
    ("06", "03"): (
        "Kolokuma/Opokuma",
        "Kolokuma/Opokuma",
        {
            "01": "Odi North",
            "02": "Odi Central",
            "03": "Odi South",
            "04": "Kaiama",
            "05": "Kaiama/Olobiri",
            "06": "Sampou/Kalama",
            "07": "Opokuma North",
            "08": "Opokuma South",
            "09": "Sagreia",
            "10": "Okoloba",
            "11": "Igbedi",
        },
    ),
    ("06", "05"): (
        "Ogbia",
        "Ogbia",
        {
            "01": "Ogbia-Ward 1",
            "02": "Otuokpoti-Ward 2",
            "03": "Ologi-Ward 3",
            "04": "Ayama-Ward 4",
            "05": "Okodi-Ward 5",
            "06": "Otuasega-Ward 6",
            "07": "Emeyal-Ward 7",
            "08": "Imiringi-Ward 8",
            "09": "Kolo-Ward 9",
            "10": "Oloibiri-Ward 10",
            "11": "Opume-Ward 11",
            "12": "Otakeme-Ward 12",
            "13": "Otueke-Ward 13",
        },
    ),
    ("06", "02"): (
        "Ekeremor",
        "Ekeremor",
        {
            "01": "Tarakiri",
            "02": "Oyakiri 1",
            "03": "Oyakiri 2",
            "04": "Oyakiri 3",
            "05": "Oyakiri 4",
            "06": "Oporomor 1",
            "07": "Oporomor 2-Ward 7",
            "08": "Oporomor 3",
            "09": "Oporomor 4",
            "10": "Oporomor 5",
            "11": "Iduwini 1",
            "12": "Iduwini 2",
        },
    ),
    ("06", "01"): (
        "Brass",
        "Brass",
        {
            "01": "Brass 1",
            "02": "Brass 2",
            "03": "Brass 3",
            "04": "Okpoama",
            "05": "Odioma-Diema",
            "06": "Cape Formosa",
            "07": "Kongho",
            "08": "Oginibiri",
            "09": "Minibie",
            "10": "Sangana",
        },
    ),
    ("06", "06"): (
        "Sagbama",
        "Sagbama",
        {
            "01": "Agbere",
            "02": "Angalabiri",
            "03": "Ofoni 1",
            "04": "Ofoni 2",
            "05": "Ebedebiri",
            "06": "Ossiama",
            "07": "Asamabiri",
            "08": "Odoni",
            "09": "Trofani",
            "10": "Sagbama",
            "11": "Agoro",
            "12": "Toru-Ebeni",
            "13": "Adagbabiri",
            "14": "Osekwenike",
        },
    ),
}
RENDER = 2000


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def inec(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([INEC, kind, *(str(part) for part in parts)])))


def state_area(code: str) -> str:
    return inec("area", "ng.state", code)


def lga_area(state: str, lga: str) -> str:
    return key("area", "ng.lga", f"{state}/{lga}")


def ward_area(state: str, lga: str, ward: str) -> str:
    return key("area", "ng.registration_area", f"{state}/{lga}/{ward}")


def _json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not data.get("success"):
        raise ValueError(f"{path} is not a successful answer")
    return data


def _scan(path: Path) -> None:
    head = path.read_bytes()[:8]
    if not head.startswith((b"%PDF-", b"\xff\xd8", b"\x89PNG")):
        raise ValueError("the host returned something other than a scan")


def lgas() -> list[dict]:
    return _json(raw.cached(SOURCE, f"{API}/lga").path)["data"]


def ward_url(ward: str) -> str:
    return f"{API}/pus?ward={ward}"


@dataclass(frozen=True)
class Unit:
    state: str
    lga: str
    ward: str
    unit: str
    name: str
    ward_name: str
    document: str | None

    @property
    def code(self) -> str:
        return f"{self.state}/{self.lga}/{self.ward}/{self.unit}"


def _code(text: str) -> str:
    return f"{int(text):02d}"


def document(row: dict) -> str | None:
    """The scan's URL: the copy on the Spaces bucket where the portal gives one (its own document host has lapsed),
    else the image on the portal's cache. A bare host is an upload with no file."""
    found = row.get("document") or {}
    if found.get("status") != 1:
        return None
    url = found.get("document_url") or found.get("url") or ""
    return url if re.search(r"\.(pdf|jpe?g|png)$", url, re.IGNORECASE) else None


def wards(state: str, lga: str) -> list[tuple[str, dict]]:
    """The portal id and record of each ward in a local government area."""
    for entry in lgas():
        if entry["state"]["code"] == state and _code(entry["lga"]["code"]) == lga:
            return [(w["_id"], w) for w in entry["wards"]]
    raise KeyError(f"no local government area {state}/{lga}")


def units(state: str, lga: str) -> list[Unit]:
    found: list[Unit] = []
    for id, ward in wards(state, lga):
        for row in _json(raw.cached(SOURCE, ward_url(id)).path)["data"]:
            pu = row["polling_unit"]
            parts = pu["pu_code"].split("/")
            if parts[:3] != [state, lga, _code(ward["code"])]:
                raise ValueError(f"{pu['pu_code']} is listed under ward {ward['code']} of {state}/{lga}")
            found.append(Unit(state, lga, parts[2], parts[3], pu["name"], ward["name"], document(row)))
    codes = [u.code for u in found]
    if len(set(codes)) != len(codes):
        raise ValueError(f"{state}/{lga} lists a polling unit twice")
    return sorted(found, key=lambda u: u.code)


def fetch() -> None:
    raw.fetch(SOURCE, f"{API}/lga", suffix=".json")
    raw.fetch(SHAPES, LGAS, suffix=".geojson")
    for state in {state for state, _ in READ}:
        raw.fetch(GRID3, WARDS.format(STATES[state][0]), suffix=".geojson")
    for state, lga in READ:
        for id, _ in wards(state, lga):
            raw.fetch(SOURCE, ward_url(id), suffix=".json")
        for unit in units(state, lga):
            if unit.document:
                scan(unit.document, fetching=True)


def scan(url: str, fetching: bool = False) -> Artifact:
    if fetching:
        return raw.fetch(SOURCE, url, suffix=Path(url).suffix.lower(), check=_scan)
    return raw.cached(SOURCE, url)


def image_key(artifact: Artifact) -> str:
    return f"pages/{SOURCE}/{artifact.sha256}-1.jpg"


def render(artifact: Artifact) -> tuple[Path, int, int]:
    """The scan's page as a JPEG (a PDF's page rendered with its longer side `RENDER` pixels, an image as it is), and
    the image's width and height."""
    out = DATA / image_key(artifact)
    if not out.exists() and artifact.path.suffix != ".pdf":
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["convert", str(artifact.path), "-quality", "90", str(out)], check=True, capture_output=True)
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        stem = out.with_suffix("")
        subprocess.run(
            ["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-scale-to", str(RENDER), "-jpeg", "-jpegopt",
             "quality=82", str(artifact.path), str(stem)],
            check=True,
            capture_output=True,
        )  # fmt: skip
    size = subprocess.run(["identify", "-format", "%w %h", str(out)], check=True, capture_output=True, text=True)
    width, height = (int(x) for x in size.stdout.split())
    return out, width, height


IREV_ID = key("source", "irev")
LGA_SHAPES_ID = key("source", SHAPES, "adm2")
WARD_SHAPES_ID = key("source", GRID3, "wards-v4")
METHOD = (
    "Read by eye from the scans of Form EC8A that presiding officers uploaded to the Commission's result viewing "
    "portal (IReV), one sheet per polling unit, twice and independently by two different multimodal models, with "
    "every disagreement settled by a third look at the sheet. Registered voters are row 1 of the sheet, voters are "
    "the accredited voters of row 2, invalid are the rejected ballots of row 6, valid are the total valid votes, and "
    "each candidate's votes are the figures written against the candidate's party. Ballot papers issued, unused, "
    "spoiled, and used (rows 3, 4, 5, and 8) have no measure here. A party row left blank is read as no votes. A "
    "polling unit whose scan is missing, unreadable, or not its own presidential EC8A is left out, so the set covers "
    "only part of each area. These are the polling units' own figures, not the collated declaration, which the "
    "archive holds nationally."
)


def gb_lgas() -> dict[str, shapely.Geometry]:
    found: dict[str, list[shapely.Geometry]] = {}
    for feature in json.loads(raw.cached(SHAPES, LGAS).path.read_text(encoding="utf-8"))["features"]:
        found.setdefault(feature["properties"]["shapeName"], []).append(shapely.geometry.shape(feature["geometry"]))
    return {name: shapes[0] for name, shapes in found.items() if len(shapes) == 1}


def grid3_wards(state: str) -> dict[tuple[str, str], shapely.Geometry]:
    url = WARDS.format(STATES[state][0])
    found: dict[tuple[str, str], shapely.Geometry] = {}
    for feature in json.loads(raw.cached(GRID3, url).path.read_text(encoding="utf-8"))["features"]:
        props = feature["properties"]
        place = (props["lga"], props["ward"])
        if place in found:
            raise ValueError(f"GRID3 lists {place} twice")
        found[place] = shapely.geometry.shape(feature["geometry"])
    return found


def multipolygon(shape: shapely.Geometry) -> shapely.Geometry:
    shape = shapely.make_valid(shape)
    polygons = [g for g in shapely.get_parts(shape) if isinstance(g, shapely.Polygon)]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def outline(p: Package, area: str, shape: shapely.Geometry, source: str) -> None:
    shape = multipolygon(shape)
    p.add(
        "area_geometry",
        area_id=area,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=source,
    )


def title(text: str) -> str:
    text = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", text)).strip()
    words = re.sub(r"[A-Za-z]+", lambda m: m[0].capitalize(), text)
    return re.sub(r"\b(I{1,3}|Iv|Vi{0,3})\b", lambda m: m[0].upper(), words)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", *(results_key(state, lga) for state, lga in READ)]


def results_key(state: str, lga: str) -> str:
    return f"{SOURCE}/{DAY}/{state}-{lga}"


def parse() -> None:
    geo().write()
    for state, lga in READ:
        results(state, lga).write()


def emit() -> None:
    package.run("load", packages())


def sources(p: Package) -> None:
    p.add(
        "source",
        id=IREV_ID,
        kind="official_body",
        publisher="Independent National Electoral Commission",
        title="INEC Result Viewing Portal (IReV): 2023 presidential election, Forms EC8A by polling unit",
        url=PORTAL,
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent National Electoral Commission",
    )
    p.artifact(raw.cached(SOURCE, f"{API}/lga"), IREV_ID)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Nigeria's local government areas and registration areas (wards) that hold the polling units read from the "
        "2023 presidential EC8A sheets so far, with outlines from geoBoundaries and GRID3, and the schemes for both "
        "and for polling units",
    )
    p.external |= {NIGERIA, *(state_area(code) for code, _ in STATES.values())}
    # The package replaces the loaded one whole, so an area missing here would be deleted from under the results
    # packages that place their polling units in it: every local government area with readings must be in READ.
    on_file = {tuple(path.stem.split("-")) for path in READINGS.glob("*.tsv")}
    if on_file != set(READ):
        raise ValueError(f"READ and the readings on file differ: {sorted(on_file ^ set(READ))}")
    sources(p)
    p.add(
        "source",
        id=LGA_SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Nigeria ADM2 (local government areas, simplified; from GRID3)",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab); GRID3",
    )
    p.artifact(raw.cached(SHAPES, LGAS), LGA_SHAPES_ID)
    p.add(
        "source",
        id=WARD_SHAPES_ID,
        kind="academic",
        publisher="GRID3",
        title="GRID3 NGA - Operational Wards v4.0",
        url="https://data.grid3.org/datasets/GRID3::grid3-nga-operational-wards-v4-0/about",
        license="Free to use, copy, distribute, and adapt with attribution (GRID3 terms of use)",
        attribution="GRID3 (Geo-Referenced Infrastructure and Demographic Data for Development); CIESIN",
    )
    for code, _ in STATES.values():
        p.artifact(raw.cached(GRID3, WARDS.format(code)), WARD_SHAPES_ID)
    for code, rank, kind, noun in (
        ("ng.lga", 2, "administrative", "local government area"),
        ("ng.registration_area", 3, "electoral_district", "registration area"),
        ("ng.polling_unit", 5, "precinct", "polling unit"),
    ):
        p.add("scheme", code=code, polity_id=NIGERIA, kind=kind, rank=rank, noun=noun, code_system="inec")
    for scheme in ("ng.lga", "ng.registration_area"):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme),
            scheme_code=scheme,
            vintage="geoBoundaries gbOpen (GRID3)" if scheme == "ng.lga" else "GRID3 Operational Wards v4.0",
            as_of=(
                raw.cached(SHAPES, LGAS) if scheme == "ng.lga" else raw.cached(GRID3, WARDS.format("BY"))
            ).retrieved_at.date(),
        )
    shaped = gb_lgas()
    for (state, lga), (name, gb_name, ward_names) in sorted(READ.items()):
        code, _ = STATES[state]
        id = lga_area(state, lga)
        p.add("area", id=id, scheme_code="ng.lga")
        p.add("area_code", area_id=id, code=f"{state}/{lga}")
        p.add("area_parent", area_id=id, parent_id=state_area(code))
        p.name(id, name, "en", "official")
        outline(p, id, shaped[gb_name], LGA_SHAPES_ID)
        wards_shaped = grid3_wards(state)
        listed = {_code(w["code"]): w for _, w in wards(state, lga)}
        if sorted(listed) != sorted(ward_names):
            raise ValueError(f"{state}/{lga}: the portal's wards {sorted(listed)} differ from the curated list")
        for ward, record in sorted(listed.items()):
            ward_id = ward_area(state, lga, ward)
            p.add("area", id=ward_id, scheme_code="ng.registration_area")
            p.add("area_code", area_id=ward_id, code=f"{state}/{lga}/{ward}")
            p.add("area_parent", area_id=ward_id, parent_id=id)
            p.name(ward_id, f"{title(record['name'])} ({name})", "en", "official")
            outline(p, ward_id, wards_shaped[(gb_name, ward_names[ward])], WARD_SHAPES_ID)
    return p


FIELDS = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", *PARTIES, "TOTAL"]
MEASURES = {"R1": REGISTERED, "R2": VOTERS, "R6": INVALID, "TOTAL": VALID}


@dataclass(frozen=True)
class Cell:
    """A figure as read: None for a blank cell, or for a row-1-to-8 figure that could not be read (`?`)."""

    value: int | None
    box: tuple[float, float, float, float]
    note: str


@dataclass(frozen=True)
class Sheet:
    code: str
    status: str
    note: str
    cells: dict[str, Cell]


def readings(state: str, lga: str) -> dict[str, Sheet]:
    """The settled readings of a local government area's sheets, by polling-unit code."""
    rows: dict[str, list[dict[str, str]]] = {}
    with (READINGS / f"{state}-{lga}.tsv").open(newline="") as file:
        for row in csv.DictReader(file, delimiter="\t", quoting=csv.QUOTE_NONE):
            rows.setdefault(row["code"], []).append(row)
    found: dict[str, Sheet] = {}
    for code, group in rows.items():
        status = [r for r in group if r["field"] == "STATUS"]
        if len(status) != 1:
            raise ValueError(f"{code} has {len(status)} status rows")
        cells: dict[str, Cell] = {}
        for r in group:
            if r["field"] == "STATUS":
                continue
            if r["field"] not in FIELDS or r["field"] in cells:
                raise ValueError(f"{code} has an unknown or repeated field {r['field']}")
            if r["value"] not in ("", "?") and not r["value"].isdigit():
                raise ValueError(f"{code} {r['field']} holds {r['value']!r}")
            if r["value"] == "?" and r["field"] in (*PARTIES, "TOTAL"):
                raise ValueError(f"{code} cannot be stored with its {r['field']} figure unread")
            box = (float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"]))
            cells[r["field"]] = Cell(int(r["value"]) if r["value"].isdigit() else None, box, r["note"])
        if status[0]["value"] == "ok" and sorted(cells) != sorted(FIELDS):
            raise ValueError(f"{code} does not hold every field")
        if status[0]["value"] != "ok" and cells:
            raise ValueError(f"{code} is {status[0]['value']} but holds figures")
        found[code] = Sheet(code, status[0]["value"], status[0]["note"], cells)
    return found


def votes(sheet: Sheet) -> dict[str, int]:
    """Each party's votes, a blank row counting as none."""
    return {party: sheet.cells[party].value or 0 for party in PARTIES}


def valid(sheet: Sheet) -> int | None:
    """The sheet's total valid votes: the figure under the party table, else row 7."""
    total = sheet.cells["TOTAL"].value
    return total if total is not None else sheet.cells["R7"].value


def checks(sheet: Sheet) -> list[str]:
    """The sheet's own arithmetic on the figures stored that does not hold."""
    found: list[str] = []
    total = valid(sheet)
    if total is not None and sum(votes(sheet).values()) != total:
        found.append(f"the parties' votes add up to {sum(votes(sheet).values())}, not the {total} valid votes")
    row7 = sheet.cells["R7"].value
    if row7 is not None and total is not None and row7 != total:
        found.append(f"row 7 gives {row7} valid votes and the table {total}")
    return found


def results(state: str, lga: str) -> Package:
    name = READ[(state, lga)][0]
    p = Package(
        results_key(state, lga),
        SOURCE,
        f"The 25 February 2023 presidential election in {name} local government area ({STATES[state][1]}), by "
        "polling unit, read from the EC8A sheets on the Commission's result viewing portal",
        depends=[f"{SOURCE}/geo"],
    )
    contest = inec("contest", TAG)
    election = inec("election", "2023:federal")
    options = {party: inec("option", TAG, number) for number, party in enumerate(PARTIES, start=1)}
    p.external |= {NIGERIA, contest, election, *options.values()}
    p.uses(raw.cached(SOURCE, f"{API}/lga"))
    for id, _ in wards(state, lga):
        p.uses(raw.cached(SOURCE, ward_url(id)))
    result_set = key("result_set", TAG, state, lga)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=IREV_ID,
        status="active",
        is_default=False,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    read = readings(state, lga)
    listed = units(state, lga)
    unknown = set(read) - {u.code for u in listed}
    if unknown:
        raise ValueError(f"readings for polling units the portal does not list: {sorted(unknown)}")
    accounting: list[str] = []
    for unit in listed:
        sheet = read.get(unit.code)
        if unit.document is None or sheet is None or sheet.status != "ok":
            continue
        artifact = scan(unit.document)
        artifact_id = p.artifact(artifact, IREV_ID)
        _, width, height = render(artifact)
        page = key("page", artifact.sha256)
        p.add(
            "source_page",
            id=page,
            artifact_id=artifact_id,
            page_number=1,
            image_key=image_key(artifact),
            width=width,
            height=height,
        )
        id = key("unit", unit.code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=NIGERIA,
            scheme_code="ng.polling_unit",
            code=unit.code,
            area_relation="within",
            area_id=ward_area(state, lga, unit.ward),
            kind="standard",
        )
        p.name(id, title(unit.name), "en", "official")

        def put(option: str | None, measure: int, field: str, number: int) -> None:
            x0, y0, x1, y1 = sheet.cells[field].box
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=id,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=DIGITIZED,
                value=number,
            )
            p.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=id,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=page,
                source_id=IREV_ID,
                x0=max(0.0, x0 / width),
                y0=max(0.0, y0 / height),
                x1=min(1.0, x1 / width),
                y1=min(1.0, y1 / height),
            )

        for party, number in votes(sheet).items():
            put(options[party], VOTES, party, number)
        for field, measure in MEASURES.items():
            number = sheet.cells[field].value
            if field == "TOTAL":
                number = valid(sheet)
                field = "TOTAL" if sheet.cells["TOTAL"].value is not None else "R7"
            if number is not None:
                put(None, measure, field, number)
        if checks(sheet):
            accounting.append(unit.code)
    if accounting:
        p.add(
            "note",
            id=key("note", result_set, "accounting"),
            entity_id=result_set,
            kind="caveat",
            body_md=(
                "These polling units' sheets do not add up as written, and are stored as written: "
                + ", ".join(accounting)
                + "."
            ),
            lang="en",
        )
    return p
