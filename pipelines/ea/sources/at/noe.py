"""Lower Austrian Landtag elections, 1993 to 2023, by municipality from the state government's election site.

The Land Niederösterreich publishes each Landtag election as a fixed-width text file with one line per municipality,
followed by district, electoral-district, and state sums, and as a state-level page with the totals and the seats.
Municipality shapes come from the Federal Office of Metrology and Surveying (BEV), which publishes the cadastral
communities with their municipality codes.
"""

import functools
import html
import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry
import shapely.ops

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.noe"
BASE = "https://www.noe.gv.at/wahlen"
BEV_SOURCE = "at.bev"
BEV = "https://data.bev.gv.at/download/Verwaltungsgrenzen/shp/20251001/VGD_Niederoesterreich_20251001.zip"
BEV_DAY = date(2025, 10, 1)
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))
TOLERANCE = 10.0
LAND_CODE = "3"


@dataclass(frozen=True)
class Edition:
    """One Lower Austrian Landtag election, the file with its results by municipality, and its state-level page."""

    year: int
    day: date
    file: str
    page: str | None

    @property
    def key(self) -> str:
        return f"lt-{self.year}"

    @property
    def txt(self) -> str:
        return f"{BASE}/{self.file}.txt"

    @property
    def url(self) -> str | None:
        return None if self.page is None else f"{BASE}/{self.page}/l.htm"


EDITIONS = [
    Edition(1993, date(1993, 5, 16), "lw93", None),
    Edition(1998, date(1998, 3, 22), "lw98", "L19981"),
    Edition(2003, date(2003, 3, 30), "lw03", "L20031"),
    Edition(2008, date(2008, 3, 9), "lw08", "L20081"),
    Edition(2013, date(2013, 3, 3), "lw13", "L20131"),
    Edition(2018, date(2018, 1, 28), "lw18", "L20181"),
    Edition(2023, date(2023, 1, 29), "lw23", "L20231"),
]
LATEST = EDITIONS[-1]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", "shapes")
LANDTAG = key("body", "landtag")
LAND = key("area", "at-3.land", LAND_CODE)
MEASURES = ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"))
SCHEMES = (("at-3.land", 1, "state"), ("at-3.bezirk", 2, "political district"), ("at-3.gemeinde", 3, "municipality"))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.key}" for e in EDITIONS)]


def fetch() -> None:
    raw.fetch(BEV_SOURCE, BEV, suffix=".zip")
    for edition in EDITIONS:
        raw.fetch(SOURCE, edition.txt, suffix=".txt")
        if edition.url:
            raw.fetch(SOURCE, edition.url, suffix=".htm")


def parse() -> None:
    geo().write()
    registry().write()
    for edition in EDITIONS:
        election(edition).write()


def emit() -> None:
    package.run("load", packages())


def number(text: str) -> int:
    return int(text.replace(".", "").replace("\xa0", "").strip())


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("ß", "ss")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\bsankt\b|\bst\b\.?", "st", text))


@dataclass
class Row:
    name: str
    registered: int
    valid: int
    invalid: int
    parties: list[tuple[int, str, int]]

    @property
    def cast(self) -> int:
        return self.valid + self.invalid


@dataclass
class Table:
    municipalities: list[tuple[str, int, Row]]
    districts: dict[str, Row]
    state: Row


SLOT_AT = 65
SLOT_WIDTH = 27


@functools.cache
def table(edition: Edition) -> Table:
    """Read the results file: a line per municipality, then the political-district, electoral-district, and state sums.

    A municipality belongs to the political district whose sum line follows it. The four statutory cities come first
    and have no sum line of their own; they are districts in themselves.
    """
    text = raw.cached(SOURCE, edition.txt).path.read_bytes().decode("cp1252")
    municipalities: list[tuple[str, int, Row]] = []
    districts: dict[str, Row] = {}
    state: Row | None = None
    group = 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        found = re.match(r"^(.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s", line[:66] + " ")
        assert found is not None, line
        parties = []
        for slot in range(max(0, (len(line) - SLOT_AT) // SLOT_WIDTH + 1)):
            chunk = line[SLOT_AT + slot * SLOT_WIDTH : SLOT_AT + (slot + 1) * SLOT_WIDTH]
            cell = re.match(r"^(\S+)\s+(\d+)(?:\s|$)", chunk)
            if cell:
                parties.append((slot + 1, cell[1], int(cell[2])))
        row = Row(found[1].strip(), int(found[2]), int(found[3]), int(found[4]), parties)
        if row.name.startswith("Bezirk "):
            districts[row.name.removeprefix("Bezirk ")] = row
            group += 1
        elif row.name.startswith("Wahlkreis "):
            continue
        elif row.name.startswith("Land "):
            state = row
        else:
            municipalities.append((row.name, group, row))
    assert state is not None
    return Table(municipalities, districts, state)


@dataclass
class Declared:
    registered: int
    cast: int
    valid: int
    invalid: int
    votes: dict[str, int]
    seats: dict[str, int]


@functools.cache
def declared(edition: Edition) -> Declared:
    """The state totals, each list's votes, and the seats, as the state-level page prints them."""
    if edition.url is None:
        state = table(edition).state
        votes = {label: v for _, label, v in state.parties}
        return Declared(state.registered, state.cast, state.valid, state.invalid, votes, {})
    page = raw.cached(SOURCE, edition.url).path.read_bytes().decode("cp1252")
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).replace("\xa0", " ").split())
    head = re.search(r"Wahlberecht\. Abgegeben Ungültig Gültig ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)", text)
    assert head is not None, f"{edition.key}: no totals on the state page"
    dots = re.search(r" \.{3,}", text)
    assert dots is not None, f"{edition.key}: no list names on the state page"
    labels: list[str] = []
    for token in reversed(text[: dots.start()].split()):
        if re.match(r"[+\-\d]", token):
            break
        labels.insert(0, token)
    figures = re.findall(r"[\d.]+(?= )", text[dots.end() :])[: len(labels)]
    seats = re.search(r"Mandatsverteilung:(.*?)(?: ©|$)", text)
    assert seats is not None, f"{edition.key}: no seats on the state page"
    won = {label: int(count) for label, count in re.findall(r"(\S+): (\d+)", seats[1])}
    return Declared(
        number(head[1]),
        number(head[2]),
        number(head[4]),
        number(head[3]),
        {label: number(figure) for label, figure in zip(labels, figures, strict=True)},
        won,
    )


PERSISTENT = [
    (re.compile(r"^(övp|vpnö)$", re.IGNORECASE), "ÖVP"),
    (re.compile(r"^spö$", re.IGNORECASE), "SPÖ"),
    (re.compile(r"^fpö$", re.IGNORECASE), "FPÖ"),
    (re.compile(r"^(grüne|gr\.alt\.)$", re.IGNORECASE), "GRÜNE"),
    (re.compile(r"^kpö$", re.IGNORECASE), "KPÖ"),
    (re.compile(r"^neos$", re.IGNORECASE), "NEOS"),
    (re.compile(r"^lif$", re.IGNORECASE), "LIF"),
]


def party_key(edition: Edition, label: str) -> str:
    for pattern, name in PERSISTENT:
        if pattern.search(label):
            return name
    return f"{label}@{edition.key}"


def party(edition: Edition, label: str) -> str:
    return key("party", party_key(edition, label))


def slots(edition: Edition) -> dict[int, str]:
    """The list printed in each column of the results file."""
    found: dict[int, str] = {}
    for _, _, row in table(edition).municipalities:
        for position, label, _ in row.parties:
            found.setdefault(position, label)
    return found


@dataclass
class Shapes:
    land: shapely.Geometry
    districts: dict[str, tuple[str, shapely.Geometry]]
    municipalities: dict[str, tuple[str, str, shapely.Geometry]]


def _clean(text: str) -> str:
    return re.sub(r"\((\w+)\)", r" (\1)", text).strip()


@functools.cache
def shapes() -> Shapes:
    """Municipality, political-district, and state shapes from the BEV's cadastral communities."""
    archive = zipfile.ZipFile(raw.cached(BEV_SOURCE, BEV).path)
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read("VGD_Niederoesterreich.shp")),
        shx=io.BytesIO(archive.read("VGD_Niederoesterreich.shx")),
        dbf=io.BytesIO(archive.read("VGD_Niederoesterreich.dbf")),
        encoding="utf-8",
    )
    grouped: dict[str, list[shapely.Geometry]] = defaultdict(list)
    names: dict[str, tuple[str, str]] = {}
    district_names: dict[str, str] = {}
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        assert record is not None and shape is not None
        props = record.as_dict()
        grouped[str(props["GKZ"])].append(shapely.geometry.shape(shape.__geo_interface__))
        names[str(props["GKZ"])] = (str(props["PG"]), str(props["BKZ"]))
        district_names[str(props["BKZ"])] = _clean(str(props["PB"]))
    codes = sorted(grouped)
    merged = [shapely.union_all(shapely.make_valid(numpy.array(grouped[code]))) for code in codes]
    simple = shapely.make_valid(shapely.coverage_simplify(numpy.array(merged), TOLERANCE))
    to_wgs = pyproj.Transformer.from_crs(31287, 4326, always_xy=True).transform
    projected = dict(zip(codes, simple, strict=True))
    by_district: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for code, shape in projected.items():
        by_district[names[code][1]].append(shape)
    districts = {
        bkz: (district_names[bkz], shapely.ops.transform(to_wgs, shapely.union_all(parts)))
        for bkz, parts in sorted(by_district.items())
    }
    land = shapely.ops.transform(to_wgs, shapely.union_all(list(projected.values())))
    places = {code: (names[code][0], names[code][1], shapely.ops.transform(to_wgs, projected[code])) for code in codes}
    return Shapes(land, districts, places)


def _multi(shape: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = _multi(shape)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=None,
        valid_to=None,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=SHAPES_ID,
    )


@functools.cache
def codes() -> tuple[dict[str, str], dict[str, list[str]]]:
    """Each file's municipality names matched to the BEV's municipality codes, and each code's names, newest first.

    The 2023 file matches the BEV's names. An earlier name that differs is paired with the one municipality of its
    political district that no name of that file claims, and any name left over stops the build.
    """
    places = shapes().municipalities
    by_name = {norm(name): code for code, (name, _, _) in places.items()}
    assert len(by_name) == len(places)
    district_of = {code: bkz for code, (_, bkz, _) in places.items()}
    names: dict[str, list[str]] = defaultdict(list)
    matched: dict[tuple[int, str], str] = {}
    for edition in sorted(EDITIONS, key=lambda e: e.year, reverse=True):
        rows = table(edition).municipalities
        groups: dict[int, list[tuple[str, str | None]]] = defaultdict(list)
        for name, group, _ in rows:
            groups[group].append((name, by_name.get(norm(name))))
        claimed = {code for found in groups.values() for _, code in found if code}
        for group, found in groups.items():
            known = [district_of[code] for _, code in found if code]
            unknown = [name for name, code in found if code is None]
            if not unknown:
                continue
            district = max(set(known), key=known.count)
            free = sorted(c for c, d in district_of.items() if d == district and c not in claimed)
            assert len(free) == len(unknown), f"{edition.key}: {unknown} against {free}"
            if len(unknown) > 1:
                free = sorted(free, key=lambda c: places[c][0])
                unknown = sorted(unknown, key=norm)
            for name, code in zip(unknown, free, strict=True):
                matched[(edition.year, name)] = code
                claimed.add(code)
        for name, _, _ in rows:
            code = matched.get((edition.year, name)) or by_name[norm(name)]
            matched[(edition.year, name)] = code
            if name not in names[code]:
                names[code].append(name)
    return {f"{year}:{name}": code for (year, name), code in matched.items()}, dict(names)


def code_of(edition: Edition, name: str) -> str:
    return codes()[0][f"{edition.year}:{name}"]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Lower Austria's political districts and municipalities, coded as the Federal Office of Metrology and "
        "Surveying codes them, with its boundaries of 1 October 2025",
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SHAPES_ID,
        kind="official_body",
        publisher="Bundesamt für Eich- und Vermessungswesen",
        title="Verwaltungsgrenzen (VGD), Stichtag 01.10.2025: Niederösterreich",
        url="https://data.bev.gv.at/download/Verwaltungsgrenzen/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© BEV - Bundesamt für Eich- und Vermessungswesen",
    )
    p.artifact(raw.cached(BEV_SOURCE, BEV), SHAPES_ID)
    for code, rank, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=AUSTRIA, kind="administrative", rank=rank, noun=noun, code_system=None)
        p.add(
            "boundary_set",
            id=key("boundary_set", code, BEV_DAY.year),
            scheme_code=code,
            vintage=str(BEV_DAY),
            as_of=BEV_DAY,
        )
    found = shapes()
    p.add("area", id=LAND, scheme_code="at-3.land")
    p.add("area_code", area_id=LAND, code=LAND_CODE)
    p.add("area_parent", area_id=LAND, parent_id=COUNTRY)
    p.name(LAND, "Niederösterreich", "de", "official")
    p.name(LAND, "Lower Austria", "en", "official", False)
    _outline(p, LAND, found.land)
    for bkz, (name, shape) in found.districts.items():
        id = key("area", "at-3.bezirk", bkz)
        p.add("area", id=id, scheme_code="at-3.bezirk")
        p.add("area_code", area_id=id, code=bkz)
        p.add("area_parent", area_id=id, parent_id=LAND)
        p.name(id, name, "de", "official")
        _outline(p, id, shape)
    names = codes()[1]
    for gkz, (name, bkz, shape) in sorted(found.municipalities.items()):
        id = key("area", "at-3.gemeinde", gkz)
        p.add("area", id=id, scheme_code="at-3.gemeinde")
        p.add("area_code", area_id=id, code=gkz)
        p.add("area_parent", area_id=id, parent_id=key("area", "at-3.bezirk", bkz))
        p.name(id, name, "de", "official")
        for other in names.get(gkz, []):
            if other != name:
                p.name(id, other, "de", "historical", False)
        _outline(p, id, shape)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Lower Austrian Landtag and the lists on its ballots, as the state government prints them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Land Niederösterreich",
        title="Land Niederösterreich - Landtagswahlen",
        url=f"{BASE}/",
        attribution="Land Niederösterreich",
    )
    for edition in EDITIONS:
        p.artifact(raw.cached(SOURCE, edition.txt), SOURCE_ID)
        if edition.url:
            p.artifact(raw.cached(SOURCE, edition.url), SOURCE_ID)
    p.add("body", id=LANDTAG, polity_id=AUSTRIA, kind="unicameral")
    p.name(LANDTAG, "Lower Austrian Landtag", "en", "official")
    p.name(LANDTAG, "Niederösterreichischer Landtag", "de", "native", False)
    seen: set[str] = set()
    for edition in EDITIONS:
        for label in slots(edition).values():
            id = party(edition, label)
            if id in seen:
                continue
            seen.add(id)
            p.add("party", id=id, kind="party", polity_id=AUSTRIA)
            p.add("external_id", entity_id=id, scheme="at.noe.party", value=party_key(edition, label))
            p.name(id, label, "de", "official")
    return p


METHOD = (
    "Results by municipality (Gemeinde) from the state government's results file, with the state totals and the "
    "seats from its state-level page. The file prints registered voters, valid votes, and invalid votes; ballots "
    "cast are their sum. The state totals the source prints can exceed the sum of its municipality lines."
)


def election(edition: Edition) -> Package:
    data = table(edition)
    state = declared(edition)
    day, year = edition.day, edition.year
    p = Package(
        f"{SOURCE}/{edition.key}",
        SOURCE,
        f"The {day} Lower Austrian Landtag election by municipality from the state government",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.uses(raw.cached(SOURCE, edition.txt))
    if edition.url:
        p.uses(raw.cached(SOURCE, edition.url))
    id = key("election", edition.key)
    p.add("election", id=id, polity_id=AUSTRIA, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Lower Austrian Landtag election", "en", "official")
    p.name(id, f"Landtagswahl {year}", "de", "native", False)
    path = f"at/{day}-niederoesterreich-landtag"
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
    p.name(contest, f"{year} Lower Austrian Landtag", "en", "official")
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
    labels = slots(edition)
    options: dict[int, str] = {}
    for position, label in sorted(labels.items()):
        option = options[position] = key("option", edition.key, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(edition, label),
            ballot_position=position,
            ballot_label=label,
        )
    by_label = {label: position for position, label in labels.items()}
    for name in state.votes:
        assert name in by_label, f"{edition.key}: {name} of the state page is on no column of the file"
    for field, measure in (("registered", 2), ("cast", 3), ("valid", 4), ("invalid", 5)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=getattr(state, field))
    for name, votes in state.votes.items():
        p.add("declared", result_set_id=result_set, option_id=options[by_label[name]], measure=1, value=votes)
        if state.seats.get(name):
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[by_label[name]],
                kind="won",
                seats=state.seats[name],
            )
    for name in state.seats:
        assert name in by_label, f"{edition.key}: seats for {name}, which is on no column of the file"
    rows = [row for _, _, row in data.municipalities]
    sums = {f: sum(getattr(r, f) for r in rows) for f in ("registered", "cast", "valid", "invalid")}
    gaps = [
        f"{value:,} {field} against {getattr(state, field):,}"
        for field, value in sums.items()
        if value != getattr(state, field)
    ]
    if gaps:
        p.accept(
            "accounting.declared",
            "the municipality rows add up to " + ", ".join(gaps) + " on the state line the source prints; "
            "the source's state line holds ballots that none of its municipality lines carries",
        )
        p.add(
            "note",
            id=key("note", result_set, "state"),
            entity_id=result_set,
            kind="caveat",
            body_md="The state totals the source prints are slightly larger than the sum of its municipality lines: "
            + ", ".join(gaps)
            + ". The source does not say which ballots the state line adds beyond its municipality lines.",
            lang="en",
        )
    unlisted: list[str] = []
    for name, _, row in data.municipalities:
        gkz = code_of(edition, name)
        unit = key("unit", edition.key, gkz)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code="at-3.gemeinde",
            code=f"{day}:{gkz}",
            area_relation="equals",
            area_id=key("area", "at-3.gemeinde", gkz),
            kind="standard",
        )
        p.name(unit, name, "de", "official")
        for measure, field in MEASURES:
            if field == "registered" and row.registered == 0 and row.cast > 0:
                unlisted.append(name)
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
                value=getattr(row, field),
            )
        for position, _, votes in row.parties:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[position],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
    if unlisted:
        p.add(
            "note",
            id=key("note", result_set, "registered"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The file prints no registered voters (zero) for {len(unlisted)} "
            f"{'municipality' if len(unlisted) == 1 else 'municipalities'} that counted ballots, so their "
            "registered voters are left out rather than stored as zero.",
            lang="en",
        )
    over = [r for r in rows if r.registered and r.cast > r.registered]
    if over:
        p.accept(
            "accounting.turnout",
            f"{len(over)} {'municipality' if len(over) == 1 else 'municipalities'} counted more ballots than the "
            "file lists registered voters, as printed",
        )
    return p
