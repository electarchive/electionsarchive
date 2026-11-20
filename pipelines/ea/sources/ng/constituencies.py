"""Outlines for Nigeria's senatorial districts, federal constituencies, and state constituencies.

The Commission publishes no shapes for its constituencies, but its result viewing portal (IReV) records which
constituencies each registration area (ward) belongs to: the presidential election's `.../lga` listing gives every
local government area of the country with its wards, and each ward carries the portal's ids of its senatorial
district, federal constituency, and state constituency. The portal's lists of senatorial, House of Representatives,
and House of Assembly elections (`.../elections?election_type=<type>&state_id=<n>`) name each of those ids with the
constituency's code (`SD/001/AB`, `FC/001/AB`, `SC/001/AB`) and name, the codes under which `ng.inec` holds the areas.

A constituency's outline is the union of its wards' outlines from GRID3's operational wards (v4.0). Every senatorial
district and nearly every federal constituency is made of whole local government areas, so those use the union of
all of GRID3's wards in each area, matched to the portal's area by name within the state. A local government area
that the portal splits between constituencies is divided by ward, each portal ward matched to GRID3's ward of the
same name within the area (or the closest name); the wards of the area left over go to the one constituency whose
wards are left over, if there is one. A constituency whose wards cannot all be placed is left without a shape, as is
one that a ward matched only by a close name splits into separate pieces.

The constituencies are those of the 1996 delimitation, in force for every election since 1999, so the outlines
carry no start date. ``ng.inec`` holds a few state constituencies twice (an unnamed 2019 row and a named 2023 row
under the same code); both get the outline.
"""

import json
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from functools import cache
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "ng.constituencies"
INEC = "ng.inec"
NIGERIA = str(uuid5(package.NAMESPACE, "polity\x1fNG"))
API = "https://dolphin-app-sleqh.ondigitalocean.app/api/v1"
PRESIDENTIAL = f"{API}/elections/63f8f25b594e164f8146a213/lga"
PORTAL = "https://www.inecelectionresults.ng/"
# The portal's election types, with the ward field that names the constituency and the archive's scheme.
TYPES = {
    "SEN": ("5f129a04df41d910dcdc1d52", "senatorial_district_id", "ng.senatorial"),
    "REPS": ("5f129a04df41d910dcdc1d53", "federal_constituency_id", "ng.federal"),
    "ASSEMBLY": ("5f129a04df41d910dcdc1d54", "state_constituency_id", "ng.state_constituency"),
}
STATE_IDS = range(1, 38)
WARDS = (
    "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/GRID3_NGA_operational_wards_v4_0/FeatureServer/"
    "0/query?where=statecode%3D%27{}%27&outFields=state,statecode,lga,ward,ward_alt_names,lga_alt_names"
    "&geometryPrecision=5&maxAllowableOffset=0.0002&outSR=4326&f=geojson"
)
# The portal's two-digit state code against GRID3's state code and the archive's.
STATES = {
    "01": ("AB", "AB"), "02": ("AD", "AD"), "03": ("AK", "AK"), "04": ("AN", "AN"), "05": ("BA", "BA"),
    "06": ("BY", "BY"), "07": ("BE", "BN"), "08": ("BR", "BO"), "09": ("CR", "CR"), "10": ("DE", "DT"),
    "11": ("EB", "EB"), "12": ("ED", "ED"), "13": ("EK", "EK"), "14": ("EN", "EN"), "15": ("GO", "GM"),
    "16": ("IM", "IM"), "17": ("JI", "JG"), "18": ("KD", "KD"), "19": ("KN", "KN"), "20": ("KT", "KT"),
    "21": ("KB", "KB"), "22": ("KO", "KG"), "23": ("KW", "KW"), "24": ("LA", "LA"), "25": ("NA", "NS"),
    "26": ("NI", "NG"), "27": ("OG", "OG"), "28": ("ON", "OD"), "29": ("OS", "OS"), "30": ("OY", "OY"),
    "31": ("PL", "PL"), "32": ("RI", "RV"), "33": ("SO", "SO"), "34": ("TA", "TR"), "35": ("YO", "YB"),
    "36": ("ZA", "ZM"), "37": ("FC", "FC"),
}  # fmt: skip
SHAPES_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fgrid3-wards-v4"))
PORTAL_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1firev"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def elections_url(kind: str, state: int) -> str:
    return f"{API}/elections?election_type={TYPES[kind][0]}&state_id={state}"


def fetch() -> None:
    raw.fetch(SOURCE, PRESIDENTIAL, suffix=".json")
    for kind in TYPES:
        for state in STATE_IDS:
            raw.fetch(SOURCE, elections_url(kind, state), suffix=".json")
    for grid3, _ in STATES.values():
        raw.fetch(SOURCE, WARDS.format(grid3), suffix=".geojson")


def _json(url: str) -> dict:
    return json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))


def plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = text.replace("&", "/").replace(" and ", "/")
    return re.sub(r"[^a-z0-9]", "", text)


# The state suffixes of the 2019 report's codes, which `ng.inec` keeps, against the portal's.
SUFFIX = {"LG": "LA", "NW": "NS", "ON": "OD", "ZF": "ZM", "FCT": "FC"}


def number(code: str) -> tuple[str, int, str]:
    """A constituency code as its kind, its national number, and its state; the portal writes a few without the
    second slash (`SC/076GM`) or with a trailing space."""
    match = re.fullmatch(r"(SD|FC|SC)/(\d+)/?([A-Z]{2,3})", code.strip().upper())
    if match is None:
        raise ValueError(f"constituency code {code!r}")
    kind, n, state = match.groups()
    return kind, int(n), SUFFIX.get(state, state)


@cache
def domains() -> dict[str, dict[int, tuple[str, str]]]:
    """Each portal constituency id, by election type, with its code and name."""
    found: dict[str, dict[int, tuple[str, str]]] = {}
    for kind, (_, field, _) in TYPES.items():
        seen = found.setdefault(kind, {})
        for state in STATE_IDS:
            for row in _json(elections_url(kind, state))["data"]:
                domain = row["domain"]
                entry = (domain["code"].upper(), domain["name"])
                if seen.setdefault(domain[field], entry) != entry:
                    raise ValueError(f"{kind} {domain[field]} is both {seen[domain[field]]} and {entry}")
    return found


@cache
def portal_wards() -> list[dict]:
    """Every ward of the presidential election: state code, LGA name, ward code and name, and constituency ids."""
    out: list[dict] = []
    for entry in _json(PRESIDENTIAL)["data"]:
        state = f"{int(entry['state']['code']):02d}"
        for ward in entry["wards"]:
            out.append(
                {
                    "state": state,
                    "lga": entry["lga"]["name"],
                    "lga_code": f"{int(entry['lga']['code']):02d}",
                    "ward": ward["name"],
                    "ward_code": f"{int(ward['code']):02d}",
                    **{kind: ward[field] for kind, (_, field, _) in TYPES.items()},
                }
            )
    return out


@cache
def grid3(state: str) -> list[dict]:
    rows: list[dict] = []
    for feature in _json(WARDS.format(STATES[state][0]))["features"]:
        props = feature["properties"]
        if feature["geometry"] is None:
            continue
        rows.append({**props, "shape": shapely.make_valid(shapely.geometry.shape(feature["geometry"]))})
    return rows


def _close(a: str, b: str) -> float:
    """How alike two plain names are; a name of five or more letters inside the other counts as close."""
    short, long = sorted((a, b), key=len)
    if len(short) >= 5 and short in long:
        return max(0.85, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def _pair(left: dict[str, list[str]], right: dict[str, list[str]], floor: float) -> dict[str, str]:
    """Pair keys of `left` with keys of `right` one to one: equal plain names first, then the closest names above
    `floor`, best pair first. Each side maps a key to the plain forms of its names."""
    found: dict[str, str] = {}
    taken: set[str] = set()
    for a, names in left.items():
        hits = [b for b, others in right.items() if b not in taken and set(names) & set(others)]
        if len(hits) == 1:
            found[a] = hits[0]
            taken.add(hits[0])
    scored = sorted(
        (
            (max(_close(x, y) for x in names for y in right[b]), a, b)
            for a, names in left.items()
            if a not in found
            for b in right
            if b not in taken
        ),
        reverse=True,
    )
    for score, a, b in scored:
        if score < floor:
            break
        if a in found or b in taken:
            continue
        found[a] = b
        taken.add(b)
    return found


NUMBERS = {
    word: str(i)
    for i, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
        ]
    )
} | {"eigth": "8"}
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def _words(text: str) -> str:
    """A ward name with number words and roman numerals as digits and the word `ward` dropped, as a plain form."""
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())
    words = [NUMBERS.get(w, ROMAN.get(w, w)) for w in words if w != "ward"]
    return "".join(words)


def _names(text: str, alt: str | None) -> list[str]:
    names = [text, *(re.split(r"[,;]", alt) if alt else [])]
    out = {plain(n) for n in names} | {_words(n) for n in names}
    return sorted(n for n in out if n)


def _ward_names(text: str) -> list[str]:
    """The plain forms of a portal ward's name: the whole name, the name without its bracketed part, and the
    bracketed part, each also with numbers written as digits."""
    forms = [text, re.sub(r"\(.*?\)", "", text), *re.findall(r"\((.*?)\)", text)]
    out = {plain(f) for f in forms} | {_words(f) for f in forms}
    return sorted(n for n in out if n)


def _shape(parts: list[shapely.Geometry]) -> shapely.MultiPolygon:
    """The union of ward outlines, with the hairline gaps their separate generalization leaves closed."""
    shape = shapely.union_all(parts).buffer(0.0005, join_style="mitre").buffer(-0.0005, join_style="mitre")
    shape = shapely.make_valid(shapely.simplify(shape, 0.0001))
    polygons = [g for g in shapely.get_parts(shape) if isinstance(g, shapely.Polygon) and g.area > 1e-8]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


@cache
def composition() -> tuple[dict[str, dict[int, list[shapely.Geometry]]], dict[str, set[int]], list[str]]:
    """Each constituency's ward outlines by portal id and election type, the constituencies left incomplete, and
    notes on what failed to match."""
    parts: dict[str, dict[int, list[shapely.Geometry]]] = {kind: {} for kind in TYPES}
    broken: dict[str, set[int]] = {kind: set() for kind in TYPES}
    fuzzy: dict[str, set[int]] = {kind: set() for kind in TYPES}
    notes: list[str] = []
    by_lga: dict[tuple[str, str], list[dict]] = {}
    for ward in portal_wards():
        by_lga.setdefault((ward["state"], ward["lga_code"]), []).append(ward)
    for state in sorted(STATES):
        rows = grid3(state)
        lgas = {(state, code): wards for (s, code), wards in by_lga.items() if s == state}
        grid_lgas: dict[str, list[str]] = {}
        for row in rows:
            grid_lgas.setdefault(row["lga"], _names(row["lga"], row.get("lga_alt_names")))
            grid_lgas[row["lga"]] = sorted(
                set(grid_lgas[row["lga"]]) | set(_names(row["lga"], row.get("lga_alt_names")))
            )
        portal_lgas = {code: [plain(wards[0]["lga"])] for (_, code), wards in lgas.items()}
        paired = _pair(portal_lgas, grid_lgas, 0.75)
        for (_, code), wards in sorted(lgas.items()):
            grid_name = paired.get(code)
            inside = [row for row in rows if row["lga"] == grid_name]
            for kind in TYPES:
                ids = {w[kind] for w in wards}
                if grid_name is None:
                    broken[kind] |= ids
                    continue
                if len(ids) == 1:
                    parts[kind].setdefault(ids.pop(), []).extend(row["shape"] for row in inside)
                    continue
                portal = {w["ward_code"]: _ward_names(w["ward"]) for w in wards}
                grids = {str(i): _names(row["ward"], row.get("ward_alt_names")) for i, row in enumerate(inside)}
                matched = _pair(portal, grids, 0.7)
                left = {w[kind] for w in wards if w["ward_code"] not in matched}
                rest = [row["shape"] for i, row in enumerate(inside) if str(i) not in matched.values()]
                for w in wards:
                    if w["ward_code"] in matched:
                        row = inside[int(matched[w["ward_code"]])]
                        parts[kind].setdefault(w[kind], []).append(row["shape"])
                        if not set(portal[w["ward_code"]]) & set(grids[matched[w["ward_code"]]]):
                            fuzzy[kind].add(w[kind])
                if len(left) == 1 and rest:
                    parts[kind].setdefault(left.pop(), []).extend(rest)
                elif left:
                    broken[kind] |= left
                    notes.append(
                        f"{kind} {state}/{code} {wards[0]['lga']}: wards "
                        + ", ".join(w["ward"] for w in wards if w["ward_code"] not in matched)
                        + " not found in GRID3"
                    )
        for code in sorted(set(portal_lgas) - set(paired)):
            notes.append(f"LGA {state}/{code} {portal_lgas[code][0]} not found in GRID3")
    for kind in TYPES:
        for id in sorted(fuzzy[kind] - broken[kind]):
            if fragmented(_shape(parts[kind][id])):
                broken[kind].add(id)
                notes.append(f"{kind} {domains()[kind].get(id, (id,))[0]}: split by wards matched only by a close name")
    return parts, broken, notes


def fragmented(shape: shapely.Geometry) -> bool:
    """Whether a shape falls into pieces of comparable size, as a wrongly matched ward leaves it."""
    sizes = sorted((part.area for part in shapely.get_parts(shape)), reverse=True)
    return len(sizes) > 1 and sizes[1] > 0.05 * sizes[0]


@cache
def held() -> list[dict]:
    """The constituency areas `ng.inec` holds, from the reference partition of production: id, scheme, code, state,
    and name without the state that ends it."""
    folder = DATA / "reference" / "ng"
    areas = pl.read_parquet(folder / "area.parquet").filter(pl.col("pipeline") == INEC)
    codes = pl.read_parquet(folder / "area_code.parquet")
    schemes = {scheme for _, _, scheme in TYPES.values()}
    rows: list[dict] = []
    for row in areas.join(codes, left_on="id", right_on="area_id").iter_rows(named=True):
        if row["scheme_code"] not in schemes:
            continue
        kind, n, state = number(row["code"])
        name = (
            re.sub(r"\s*\([^()]*\)$", "", row["name"] or "") if row["scheme_code"] != "ng.senatorial" else row["name"]
        )
        rows.append({"id": row["id"], "scheme": row["scheme_code"], "code": (kind, n, state), "name": name.strip()})
    return rows


def _area_names(text: str) -> list[str]:
    forms = [text, re.sub(r"\(.*?\)", "", text), *re.findall(r"\((.*?)\)", text)]
    return sorted({plain(f) for f in forms if plain(f)} | {_words(f) for f in forms if _words(f)})


@cache
def targets() -> tuple[dict[str, dict[int, list[str]]], list[str]]:
    """The `ng.inec` areas each portal constituency outlines, by election type, and the areas left unpaired.

    The portal's numbering differs from the reports' in places, so constituencies are paired by name within the
    state: one to one first, then each area still unpaired to the closest constituency when that is clearly the
    same name (the reports name a few constituencies twice, differently). An area without a name (some 2019 rows)
    takes the constituency of the named areas that share its code, when they agree, or for a senatorial district or
    federal constituency, the constituency of its code."""
    found: dict[str, dict[int, list[str]]] = {kind: {} for kind in TYPES}
    left: list[str] = []
    for kind, (_, _, scheme) in TYPES.items():
        areas = [a for a in held() if a["scheme"] == scheme]
        mine = domains()[kind]
        for state in sorted({a["code"][2] for a in areas}):
            here = [a for a in areas if a["code"][2] == state]
            named = {a["id"]: _area_names(a["name"]) for a in here if a["name"]}
            portal = {str(i): _area_names(name) for i, (code, name) in mine.items() if number(code)[2] == state}
            if len(portal) == 1 and len(named) == 1:
                paired = {next(iter(named)): next(iter(portal))}
            else:
                paired = _pair(named, portal, 0.6)
                for id in set(named) - set(paired):
                    scores = sorted(
                        ((max(_close(x, y) for x in named[id] for y in names), d) for d, names in portal.items()),
                        reverse=True,
                    )
                    if scores and scores[0][0] >= 0.85 and (len(scores) == 1 or scores[1][0] < scores[0][0]):
                        paired[id] = scores[0][1]
            for a in here:
                if not a["name"]:
                    twins = {paired.get(b["id"]) for b in here if b["name"] and b["code"] == a["code"]}
                    if len(twins) == 1 and None not in twins:
                        paired[a["id"]] = twins.pop() or ""
            if kind != "ASSEMBLY":
                # The federal and senatorial codes agree with the portal's but for names the pairing settles, so
                # an unnamed area with no named twin takes the constituency of its code when nothing else has it.
                taken = set(paired.values())
                by_code = {number(code)[:2]: str(i) for i, (code, _) in mine.items() if number(code)[2] == state}
                for a in here:
                    hit = by_code.get(a["code"][:2])
                    if not a["name"] and a["id"] not in paired and hit is not None and hit not in taken:
                        paired[a["id"]] = hit
                        taken.add(hit)
            for a in here:
                if a["id"] in paired:
                    found[kind].setdefault(int(paired[a["id"]]), []).append(a["id"])
                else:
                    left.append(f"{scheme} {'/'.join(map(str, a['code']))} {a['name']!r}")
    return found, left


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Outlines of Nigeria's senatorial districts, federal constituencies, and state constituencies, each the union "
        "of its wards' outlines from GRID3's operational wards, with the wards of each constituency from the "
        "Commission's result viewing portal",
    )
    p.external |= {NIGERIA, *(scheme for _, _, scheme in TYPES.values())}
    p.add(
        "source",
        id=PORTAL_ID,
        kind="official_body",
        publisher="Independent National Electoral Commission",
        title="INEC Result Viewing Portal (IReV): wards and constituencies of the 2023 elections",
        url=PORTAL,
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent National Electoral Commission",
    )
    p.artifact(raw.cached(SOURCE, PRESIDENTIAL), PORTAL_ID)
    for kind in TYPES:
        for state in STATE_IDS:
            p.artifact(raw.cached(SOURCE, elections_url(kind, state)), PORTAL_ID)
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="GRID3",
        title="GRID3 NGA - Operational Wards v4.0",
        url="https://data.grid3.org/datasets/GRID3::grid3-nga-operational-wards-v4-0/about",
        license="Free to use, copy, distribute, and adapt with attribution (GRID3 terms of use)",
        attribution="GRID3 (Geo-Referenced Infrastructure and Demographic Data for Development); CIESIN",
    )
    for grid, _ in STATES.values():
        p.artifact(raw.cached(SOURCE, WARDS.format(grid)), SHAPES_ID)
    parts, broken, _ = composition()
    paired, _ = targets()
    day = date(2023, 2, 25)
    for kind, (_, _, scheme) in TYPES.items():
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme),
            scheme_code=scheme,
            vintage="1996 delimitation (wards of 2023)",
            as_of=day,
        )
        for id, areas in sorted(paired[kind].items()):
            if id in broken[kind] or id not in parts[kind]:
                continue
            shape = _shape(parts[kind][id])
            for area in areas:
                p.external.add(area)
                p.add(
                    "area_geometry",
                    area_id=area,
                    geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                    label_point=shapely.to_wkb(
                        shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                    ),
                    quality="academic",
                    source_id=SHAPES_ID,
                )
    return p
