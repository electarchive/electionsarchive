"""India's parliamentary (Lok Sabha) and assembly (Vidhan Sabha) constituencies and their boundaries.

The shapes come from the Local Government Directory (LGD), the register the Ministry of Panchayati Raj keeps with the
States, as two Parquet layers (`LGD_Parliament_Constituencies`, 543 features; `LGD_Assembly_Constituencies`, 4,177
features) in a public release of the india-geodata repository. Each feature carries the state, the constituency
number as the Election Commission numbers it, the name, and a `status` that says "Pre delimitation" for the states the
2008 Delimitation Orders left on their earlier plan (Arunachal Pradesh, Assam, Jharkhand, Manipur, Nagaland, and
Jammu and Kashmir). Everything else follows the plan in force from 19 February 2008.

What the layers get wrong or leave out is handled here, and counted in the package's note:

- an assembly constituency made of several polygons arrives as several features with one `AC_ID`; they are unioned;
- features with constituency number 0 and no name are gaps between constituencies, not constituencies, and are dropped;
- the layers number the constituencies of the undivided Andhra Pradesh of 2008 (294 assembly seats, 42 parliamentary
  seats, `AC_ID` and `pc_id` beginning 28), so the seats of Telangana keep that numbering here;
- Gujarat's constituencies 45 to 56 and 160 to 167, Madhya Pradesh's 205 to 208, and Sikkim's Sangha (no territory) have
  no feature, so they get no area.

Jammu and Kashmir's plan of 2022 and Assam's plan of 2023 have no open polygons. Their rosters (number, name,
reservation, parent parliamentary seat) come from the Wikipedia lists, which cite the Delimitation Commission's final
orders, and the areas carry no geometry.
"""

import functools
import re
from datetime import date
from typing import Any
from uuid import uuid5

import duckdb
import shapely
from shapely.geometry.base import BaseGeometry

from ea import package, raw
from ea.package import Package

SOURCE = "in.geo"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIND"))

RELEASE = "https://github.com/yashveeeeeeer/india-geodata/releases/download/electoral%2Fconstituencies/"
RELEASE_PAGE = "https://github.com/yashveeeeeeer/india-geodata/releases/tag/electoral/constituencies"
PARLIAMENT = f"{RELEASE}LGD_Parliament_Constituencies.parquet"
ASSEMBLY = f"{RELEASE}LGD_Assembly_Constituencies.parquet"
WIKI = "https://en.wikipedia.org/w/index.php?title={}&action=raw"
ASSAM_LIST = WIKI.format("List_of_constituencies_of_the_Assam_Legislative_Assembly")
JK_LIST = WIKI.format("List_of_constituencies_of_the_Jammu_and_Kashmir_Legislative_Assembly")
LOK_SABHA_LIST = WIKI.format("List_of_constituencies_of_the_Lok_Sabha")
WIKI_URLS = (ASSAM_LIST, JK_LIST, LOK_SABHA_LIST)

# The Delimitation of Parliamentary and Assembly Constituencies Order, 2008 came into force on 19 February 2008.
PLAN_2008 = date(2008, 2, 19)
# The Delimitation Commission's final orders: Jammu and Kashmir on 5 May 2022, Assam on 11 August 2023.
PLAN_JK = date(2022, 5, 5)
PLAN_ASSAM = date(2023, 8, 11)
# Ladakh became a Union territory without a legislature on 31 October 2019; its four assembly seats ended then.
LADAKH_ENDS = date(2019, 10, 31)
LADAKH_SEATS = {"LEH", "NUBRA", "ZANSKAR", "KARGIL"}
# The date a boundary set shows: every area in force then, which for the six states still on their earlier plan is
# that plan.
AS_OF = date(2021, 1, 1)

PRE = "Pre delimitation"
ENDS = {"01": PLAN_JK, "18": PLAN_ASSAM}
KINDS = {
    "sc": "constituency reserved for Scheduled Castes",
    "st": "constituency reserved for Scheduled Tribes",
    "bl": "constituency reserved for the Bhutia-Lepcha",
}
TITLE = (
    "Local Government Directory: {} Constituencies (LGD_{}_Constituencies), as packaged in the india-geodata release"
)
RESERVED = re.compile(r"\s*\((SC|ST|BL)\)\s*$", re.IGNORECASE)


def key(kind: str, *parts: object) -> str:
    return Package(SOURCE, SOURCE, "ids").id(kind, *parts)


def fetch() -> None:
    raw.fetch(SOURCE, PARLIAMENT, suffix=".parquet")
    raw.fetch(SOURCE, ASSEMBLY, suffix=".parquet")
    for url in WIKI_URLS:
        raw.fetch(SOURCE, url, suffix=".wikitext")


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def name_of(raw_name: str) -> tuple[str, str | None]:
    """The constituency's name without its reservation suffix, in title case, and the reservation (`sc`, `st`, `bl`)."""
    name = clean(raw_name)
    found = RESERVED.search(name)
    name = RESERVED.sub("", name)
    if name.isupper():
        name = re.sub(r"(?<=[A-Za-z])'([A-Z])", lambda m: "'" + m.group(1).lower(), name.title())
    return name, found.group(1).lower() if found else None


# Reading the layers.


class Seat:
    def __init__(self, code: str, state: str, number: int, name: str, plan: str, parent: str | None) -> None:
        self.code = code
        self.state = state
        self.number = number
        self.name, self.kind = name_of(name)
        self.plan = plan
        self.parent = parent
        self.parts: list[BaseGeometry] = []


def _shape(blob: bytes) -> BaseGeometry:
    shape = shapely.from_wkb(blob)
    return shape if shape.is_valid else shapely.make_valid(shape)


def _polygons(shape: BaseGeometry) -> BaseGeometry:
    parts = [g for g in getattr(shape, "geoms", [shape]) if g.geom_type in {"Polygon", "MultiPolygon"}]
    if not parts:
        raise ValueError("a shape has no polygon")
    merged = shapely.union_all(parts)
    flat = [g for g in getattr(merged, "geoms", [merged]) if g.geom_type == "Polygon"]
    return shapely.MultiPolygon(flat)


def _wkb(shape: BaseGeometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


def _point(shape: BaseGeometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True)


def _rows(url: str) -> list[dict[str, Any]]:
    """The features of a layer, with the geometry as WKB."""
    path = raw.cached(SOURCE, url).path
    cursor = duckdb.connect().execute(f"select * exclude (geometry), geometry::blob as geometry from '{path}'")
    names = [column[0] for column in cursor.description or []]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


@functools.cache
def parliament() -> dict[str, Seat]:
    seats: dict[str, Seat] = {}
    for row in _rows(PARLIAMENT):
        code = f"{row['pc_id']:04d}"
        if code in seats:
            raise ValueError(f"parliamentary constituency {code} appears twice")
        state = f"{row['pc_id'] // 100:02d}"
        plan = "pre" if clean(row["status"]) == PRE else "2008"
        seat = Seat(code, state, int(row["pc_no"]), row["pc_name"], plan, None)
        seat.parts.append(_shape(row["geometry"]))
        seats[code] = seat
    return seats


@functools.cache
def assembly() -> tuple[dict[str, Seat], int, list[str]]:
    """The assembly seats by `AC_ID`, the number of features dropped as gaps, and the codes without a known parent."""
    pcs = parliament()
    seats: dict[str, Seat] = {}
    gaps = 0
    orphans: list[str] = []
    for row in _rows(ASSEMBLY):
        if not row["ac_no"] or not clean(row["ac_name"]):
            gaps += 1
            continue
        code = str(row["AC_ID"])
        if int(code[2:]) != row["ac_no"]:
            # Bhadrachalam, number 119 of Telangana, carries Rampachodavaram's AC_ID 28172 in the layer.
            if (code, row["ac_no"]) != ("28172", 119):
                raise ValueError(f"AC_ID {code} does not match its number {row['ac_no']}")
            code = "36119"
        pc = f"{row['pc_id']:04d}" if row["pc_id"] else f"{row['st_code']}{row['pc_no']:02d}"
        if pc not in pcs:
            orphans.append(code)
        plan = "pre" if clean(row["status"]) == PRE else "2008"
        seat = seats.get(code)
        if seat is None:
            seat = seats[code] = Seat(
                code, code[:2], int(row["ac_no"]), row["ac_name"], plan, pc if pc in pcs else None
            )
        elif (seat.plan, seat.parent) != (plan, pc if pc in pcs else None):
            raise ValueError(f"assembly constituency {code} has parts with different plans or parents")
        seat.parts.append(_shape(row["geometry"]))
    return seats, gaps, sorted(set(orphans))


# The rosters of the two plans without shapes.


def table(text: str) -> list[list[str]]:
    """The rows of the first wikitable of the text, with row-spanning cells repeated into the rows they cover."""
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    body = text.split("{|", 1)[1].split("\n|}", 1)[0]
    rows: list[list[str]] = []
    spans: dict[int, tuple[int, str]] = {}
    for chunk in re.split(r"\n\|-[^\n]*", "\n" + body):
        cells: list[tuple[str, int]] = []
        for line in chunk.split("\n"):
            if not line.startswith(("|", "!")) or line.startswith(("|+", "|}")):
                continue
            for cell in re.split(r"\s*(?:\|\||!!)\s*", line[1:]):
                attrs, sep, value = cell.partition("|")
                if not sep or "=" not in attrs:
                    value, attrs = cell, ""
                rowspan = re.search(r"rowspan\s*=\s*\"?(\d+)", attrs)
                cells.append((clean(value.lstrip("|")), int(rowspan.group(1)) if rowspan else 1))
        if not cells:
            continue
        row: list[str] = []
        column = 0
        pending = iter(cells)
        while True:
            if column in spans:
                left, value = spans[column]
                row.append(value)
                spans[column] = (left - 1, value)
                if left - 1 == 0:
                    del spans[column]
                column += 1
                continue
            cell = next(pending, None)
            if cell is None:
                break
            row.append(cell[0])
            if cell[1] > 1:
                spans[column] = (cell[1] - 1, cell[0])
            column += 1
        rows.append(row)
    return rows


def _fold(name: str) -> str:
    return re.sub(r"[\s\u2013-]+", "", name.split("(")[0].lower())


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^==\s*{re.escape(heading)}[^=]*==\s*$", text, re.MULTILINE)
    if match is None:
        raise ValueError(f"no section {heading}")
    rest = text[match.end() :]
    end = re.search(r"^==[^=]", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


class Entry:
    def __init__(self, number: int, name: str, reserved: str | None, parent: str) -> None:
        self.number = number
        self.name = name
        self.reserved = reserved
        self.parent = parent


def _reservation(value: str) -> str | None:
    value = value.strip().upper()
    return value.lower() if value in {"SC", "ST"} else None


def lok_sabha(heading: str) -> list[tuple[int, str, str | None]]:
    text = raw.cached(SOURCE, LOK_SABHA_LIST).path.read_text(encoding="utf-8")
    found = []
    for row in table(_section(text, heading)):
        if len(row) >= 3 and row[0].isdigit():
            found.append((int(row[0]), clean(row[1]), _reservation(row[2])))
    return found


def _assam() -> tuple[list[Entry], list[tuple[int, str, str | None]]]:
    text = raw.cached(SOURCE, ASSAM_LIST).path.read_text(encoding="utf-8")
    text = _section(text, "Current constituencies")
    entries = []
    for row in table(text):
        if len(row) >= 5 and row[0].isdigit():
            entries.append(Entry(int(row[0]), row[1], _reservation(row[2]), row[4]))
    return entries, lok_sabha("Assam")


def _jk() -> tuple[list[Entry], list[tuple[int, str, str | None]]]:
    text = raw.cached(SOURCE, JK_LIST).path.read_text(encoding="utf-8")
    text = _section(text, "Constituencies")
    entries = []
    for row in table(text):
        if len(row) >= 4 and row[0].isdigit():
            entries.append(Entry(int(row[0]), row[1], None, row[3]))
    return entries, lok_sabha("Jammu and Kashmir")


# The package.


def _sources(p: Package) -> dict[str, str]:
    ids = {name: key("source", name) for name in ("pc", "ac", "wiki")}
    for name, title, url in (
        (
            "pc",
            TITLE.format("Parliament", "Parliament"),
            PARLIAMENT,
        ),
        (
            "ac",
            TITLE.format("Assembly", "Assembly"),
            ASSEMBLY,
        ),
    ):
        p.add(
            "source",
            id=ids[name],
            kind="official_body",
            publisher="Local Government Directory, Ministry of Panchayati Raj, Government of India",
            title=title,
            url=RELEASE_PAGE,
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            attribution="Local Government Directory (LGD), Government of India, via the india-geodata repository",
            notes="The layers flag the states left on their pre-2008 plan as 'Pre delimitation'.",
        )
        p.artifact(raw.cached(SOURCE, url), ids[name])
    p.add(
        "source",
        id=ids["wiki"],
        kind="community",
        publisher="Wikipedia",
        title="Lists of constituencies of the Assam and Jammu and Kashmir Legislative Assemblies and of the Lok Sabha, "
        "which cite the Delimitation Commission's final orders of 2022 and 2023",
        url="https://en.wikipedia.org/wiki/List_of_constituencies_of_the_Lok_Sabha",
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="Wikipedia contributors",
    )
    for url in WIKI_URLS:
        p.artifact(raw.cached(SOURCE, url), ids["wiki"])
    return ids


def _add_seat(
    p: Package,
    scheme: str,
    code: str,
    plan: str,
    name: str,
    kind: str | None,
    start: date | None,
    end: date | None,
    parent: str,
    source: str,
) -> str:
    id = key("area", scheme, code, plan)
    p.add("area", id=id, scheme_code=scheme)
    p.add("area_code", area_id=id, code=code, valid_from=start, valid_to=end)
    p.add("area_parent", area_id=id, parent_id=parent, valid_from=start, valid_to=end)
    p.name(id, name, "en", "official")
    if kind:
        p.add("area_kind", area_id=id, kind=kind, valid_from=start, valid_to=end, source_id=source)
    return id


def geo() -> Package:
    pcs = parliament()
    acs, gaps, orphans = assembly()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"India's {len(pcs)} parliamentary and {len(acs)} assembly constituencies on the plan of 2008 (and the earlier "
        "plan of six states) with their Local Government Directory boundaries, and the rosters of Assam's 2023 and "
        "Jammu and Kashmir's 2022 plans without shapes",
    )
    p.external |= {INDIA, COUNTRY}
    ids = _sources(p)
    p.add(
        "scheme",
        code="in.pc",
        polity_id=INDIA,
        kind="electoral_district",
        rank=1,
        noun="parliamentary constituency",
        kinds=KINDS,
        code_system="in_lgd",
    )
    p.add(
        "scheme",
        code="in.ac",
        polity_id=INDIA,
        kind="electoral_district",
        rank=2,
        noun="assembly constituency",
        kinds=KINDS,
        code_system="in_lgd",
    )

    def span(state: str, plan: str, name: str, scheme: str) -> tuple[date | None, date | None]:
        if plan == "2008":
            return PLAN_2008, None
        if scheme == "in.ac" and state == "01" and name.upper() in LADAKH_SEATS:
            return None, LADAKH_ENDS
        if scheme == "in.pc" and state == "01" and name.upper().startswith("LADAKH"):
            return None, None
        return None, ENDS.get(state)

    area: dict[tuple[str, str], str] = {}
    for scheme, seats, source in (("in.pc", pcs, ids["pc"]), ("in.ac", acs, ids["ac"])):
        for seat in sorted(seats.values(), key=lambda s: s.code):
            start, end = span(seat.state, seat.plan, seat.name, scheme)
            if seat.plan == "2008" and seat.state == "28" and scheme == "in.pc":
                pass
            parent = COUNTRY
            if scheme == "in.ac" and seat.parent:
                parent = area[("in.pc", seat.parent)]
            id = _add_seat(p, scheme, seat.code, seat.plan, seat.name, seat.kind, start, end, parent, source)
            area[(scheme, seat.code)] = id
            shape = _polygons(shapely.union_all(seat.parts))
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=start,
                valid_to=end,
                geom=_wkb(shape),
                label_point=_point(shape),
                quality="official",
                source_id=source,
            )
    # Rosters without shapes.
    for state, (entries, parliamentary) in (("18", _assam()), ("01", _jk())):
        start = PLAN_ASSAM if state == "18" else PLAN_JK
        by_name = {}
        for number, name, kind in parliamentary:
            code = f"{state}{number:02d}"
            by_name[_fold(name)] = _add_seat(p, "in.pc", code, "roster", name, kind, start, None, COUNTRY, ids["wiki"])
        for entry in entries:
            parent = by_name.get(_fold(entry.parent))
            if parent is None:
                raise ValueError(
                    f"assembly constituency {entry.number} {entry.name}: no parliamentary seat {entry.parent!r}"
                )
            _add_seat(
                p,
                "in.ac",
                f"{state}{entry.number:03d}",
                "roster",
                entry.name,
                entry.reserved,
                start,
                None,
                parent,
                ids["wiki"],
            )
    for scheme in ("in.pc", "in.ac"):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, "2008"),
            scheme_code=scheme,
            vintage="2008",
            valid_from=PLAN_2008,
            valid_to=None,
            as_of=AS_OF,
        )
    p.add(
        "note",
        id=key("note", "geo"),
        entity_id=ids["ac"],
        kind="methodology",
        body_md=(
            f"Assembly layer: {gaps} features without a number or name dropped as gaps; {len(orphans)} seats without a "
            "parliamentary seat in the parliamentary layer."
        ),
        lang="en",
    )
    return p
