"""NIS codes and boundaries for the Belgian federal election service's reporting units.

The service's XML files carry no codes. Its JSON API lists the same levels with a `nisCode` from 2004 on, and a level
joins its XML unit by election date, body, tier, and printed label. Cantons carry the NIS code of the municipality
that seats them, so a canton is placed there only as an approximate membership. The municipal councils of 1976 to
2000 predate the codes, so each of their municipalities joins the municipality of the plan then in force that bears
the same name. The boundaries are Statbel's statistical sectors, dissolved to municipalities, for the three municipal
plans of 2018, 2019, and 2025.
"""

import functools
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.be.vlaanderen import PLANS, SECTORS, TOLERANCE
from ea.sources.be.wallonie import RENUMBERED

SOURCE = "be.ibz"
STATBEL = "be.statbel"
API = "https://api.electionresults.belgium.be/election-level"
PAGE = 5000
BELGIUM = str(uuid5(package.NAMESPACE, "polity\x1fBE"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBEL"))
REGION, PROVINCE, MUNICIPALITY = "be.ibz_region", "be.ibz_province", "be.ibz_municipality"
LANGUAGES = {"French": "fr", "Dutch": "nl", "German": "de", "English": "en"}
BRUSSELS = "04000"
# the language each region prints its names in first
FIRST = {"02000": "nl", "03000": "fr", BRUSSELS: "fr"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


API_SOURCE = key("source", "api")
STATBEL_SOURCE = key("source", "statbel")


def area(scheme: str, code: str) -> str:
    """An area's id by its newest code, so a renumbered municipality keeps one area through its codes."""
    return key("area", scheme, RENUMBERED.get(code, code))


def folded(text: str) -> str:
    return " ".join(text.split()).casefold()


# ---- the API ----------------------------------------------------------------------------------------------------


def api_url(day: date) -> str:
    return f"{API}?ElectionDates={day}&Descriptions=Municipality&Descriptions=Canton&PageSize={PAGE}"


def _check(path: Path) -> None:
    levels = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(levels, list) or len(levels) >= PAGE:
        raise ValueError(f"{path.name} is not a complete list of levels")


def fetch(days: Iterable[date]) -> None:
    for day in days:
        raw.fetch(SOURCE, api_url(day), suffix=".json", check=_check)
    for name, _, _ in PLANS.values():
        raw.fetch(STATBEL, SECTORS + name, suffix=".zip")


@functools.lru_cache(maxsize=8)
def _api(day: date) -> dict[tuple[str, str], list[tuple[frozenset[tuple[str, str]], str]]]:
    """The levels of one election day with a NIS code, by body and tier, with each one's printed labels."""
    levels = json.loads(raw.cached(SOURCE, api_url(day)).path.read_text(encoding="utf-8"))
    found: dict[tuple[str, str], list[tuple[frozenset[tuple[str, str]], str]]] = defaultdict(list)
    for level in levels:
        if level.get("nisCode") is None:
            continue
        labels = frozenset((LANGUAGES[x["language"]], folded(x["label"])) for x in level["labels"])
        found[(level["electionType"], level["description"])].append((labels, str(level["nisCode"])))
    return found


def coded(day: date) -> bool:
    """Whether the API gives any NIS code on this day; before 2004 it gives none."""
    return bool(_api(day))


def api_code(day: date, kind: str, tier: str, labels: dict[str, str]) -> str | None:
    """The NIS code of the level that prints the most of the same labels, or None when none does or two codes tie."""
    mine = {(lang, folded(text)) for lang, text in labels.items() if text}
    best: dict[str, int] = {}
    for theirs, code in _api(day).get((kind, tier), []):
        score = len(mine & theirs)
        if score:
            best[code] = max(best.get(code, 0), score)
    if not best:
        return None
    top = max(best.values())
    codes = [code for code, score in best.items() if score == top]
    return codes[0] if len(codes) == 1 else None


# ---- Statbel's plans --------------------------------------------------------------------------------------------


def vintage_of(day: date) -> str:
    for vintage, (_, since, until) in PLANS.items():
        if since <= day and (until is None or day < until):
            return vintage
    return next(iter(PLANS))


def _reader(vintage: str, shapes: bool) -> tuple[shapefile.Reader, pyproj.CRS]:
    name, _, _ = PLANS[vintage]
    archive = zipfile.ZipFile(raw.cached(STATBEL, SECTORS + name).path)
    base = next(member for member in archive.namelist() if member.endswith(".shp"))[:-4]
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(base + ".shp")) if shapes else None,
        shx=io.BytesIO(archive.read(base + ".shx")) if shapes else None,
        dbf=io.BytesIO(archive.read(base + ".dbf")),
        encoding="utf-8",
    )
    return reader, pyproj.CRS.from_wkt(archive.read(base + ".prj").decode())


def _column(row: Mapping[str, object], *prefixes: str) -> str:
    return str(next(value for column, value in row.items() if column.startswith(prefixes)) or "")


@dataclass
class Town:
    code: str
    names: dict[str, str]
    province: str
    province_names: dict[str, str]
    region: str
    region_names: dict[str, str]


def _town(row: Mapping[str, object]) -> Town:
    def names(prefix: str) -> dict[str, str]:
        return {lang: str(row[f"{prefix}_{lang.upper()}"] or "").strip() for lang in ("fr", "nl", "de")}

    return Town(
        _column(row, "CNIS5_"),
        names("T_MUN"),
        _column(row, "C_PROVI", "CNIS_PROVI"),
        names("T_PROVI"),
        _column(row, "C_REGIO", "CNIS_REGIO"),
        names("T_REGIO"),
    )


@functools.cache
def towns(vintage: str) -> dict[str, Town]:
    reader, _ = _reader(vintage, False)
    found: dict[str, Town] = {}
    for record in reader.iterRecords():
        if record is not None:
            row = dict(record.as_dict())
            found.setdefault(_column(row, "CNIS5_"), _town(row))
    return found


def plain(text: str) -> str:
    """A name without accents, case, hyphens, apostrophes, or dots, as words."""
    stripped = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[-'’.]+", " ", stripped.casefold()).split())


def spellings(text: str) -> set[str]:
    """A printed name and, where it starts with the abbreviation St or Ste, the spellings it stands for."""
    name = plain(text)
    head, _, rest = name.partition(" ")
    if head in ("st", "ste"):
        return {name, *(f"{full} {rest}" for full in (("saint", "sint", "sainte") if head == "st" else ("sainte",)))}
    return {name}


@functools.cache
def _named(vintage: str) -> dict[str, set[str]]:
    """Each municipality name of a plan, in French, Dutch, or German, with the codes that bear it."""
    named: dict[str, set[str]] = defaultdict(set)
    for code, town in towns(vintage).items():
        for text in town.names.values():
            if text:
                named[plain(text)].add(code)
    return named


# the files of 1976 to 2000 print Liège's Saint-Nicolas as "St Nicolas" and Flanders' Sint-Niklaas as "St Niklaas",
# and Statbel names both Saint-Nicolas in French
PRINTED = {"st nicolas": "62093"}


def _by_name(vintage: str, labels: dict[str, str]) -> str | None:
    """The one municipality of a plan whose name a printed label gives in full, or else whose name starts or ends the
    label's words, the longest such name first. A label that fits two municipalities equally, or a French and a Dutch
    label that fit two, finds none."""
    named = _named(vintage)
    for text in labels.values():
        if text and plain(text) in PRINTED:
            return PRINTED[plain(text)]
    spelled = {name for text in labels.values() if text for name in spellings(text)}
    fits = [{code for mine in spellings(text) for code in named.get(mine, ())} for text in labels.values() if text]
    if any(fits):
        single = {next(iter(codes)) for codes in fits if len(codes) == 1}
        return next(iter(single)) if len(single) == 1 else None
    scores: dict[str, int] = {}
    for mine in spelled:
        if len(mine) < 5:
            continue
        for theirs, codes in named.items():
            if theirs.startswith(mine + " ") or mine.startswith(theirs + " "):
                for code in codes:
                    scores[code] = max(scores.get(code, 0), min(len(mine), len(theirs)))
    if not scores:
        return None
    top = max(scores.values())
    best = [code for code, score in scores.items() if score == top]
    return best[0] if len(best) == 1 else None


def town_code(day: date, kind: str, tier: str, labels: dict[str, str]) -> tuple[str | None, str]:
    """The municipality's NIS code in the plan holding on `day`, and how it was found: `api`, `name`, or `none`."""
    vintage = vintage_of(day)
    code = api_code(day, kind, tier, labels)
    if code is not None:
        return (code, "api") if code in towns(vintage) else (None, "none")
    if kind == "Municipality" and tier == "Municipality" and not coded(day):
        found = _by_name(vintage, labels)
        if found is not None:
            return found, "name"
    return None, "none"


def canton_seat(day: date, kind: str, labels: dict[str, str]) -> str | None:
    """The NIS code of the municipality a canton's code names, when the plan holding on `day` has it."""
    code = api_code(day, kind, "Canton", labels)
    return code if code is not None and code in towns(vintage_of(day)) else None


def link_area(code: str, day: date) -> str:
    assert code in towns(vintage_of(day)), code
    return area(MUNICIPALITY, code)


# ---- the geo package --------------------------------------------------------------------------------------------


def _wgs(shape: shapely.Geometry, crs: pyproj.CRS) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(crs, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _outline(p: Package, id: str, shape: shapely.Geometry, crs: pyproj.CRS, since: date, until: date | None) -> None:
    wgs = _wgs(shape, crs)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=since,
        valid_to=until,
        geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
        label_point=shapely.to_wkb(shapely.set_srid(shapely.point_on_surface(wgs), 4326), hex=True, include_srid=True),
        quality="official",
        source_id=STATBEL_SOURCE,
    )


def _label(p: Package, id: str, names: dict[str, str], first: str, seen: set[object]) -> None:
    for lang in (first, *(lang for lang in ("fr", "nl", "de") if lang != first)):
        text = names.get(lang, "").strip()
        if text and (id, lang, text) not in seen and (lang == first or text != names.get(first, "").strip()):
            seen.add((id, lang, text))
            p.name(id, text, lang, "official", lang == first)


def _trim(text: str) -> str:
    for prefix in ("Provincie ", "Province d’", "Province de ", "Province du ", "Provinz "):
        text = text.removeprefix(prefix)
    return text


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Belgium's three regions, ten provinces, and municipalities in the 2018, 2019, and 2025 municipal plans, from "
        "Statbel's statistical sectors",
    )
    p.external |= {BELGIUM, TERRITORY}
    p.add(
        "source",
        id=STATBEL_SOURCE,
        kind="official_body",
        publisher="Statbel (Algemene Directie Statistiek)",
        title="Statistical sectors (Shapefile)",
        url="https://statbel.fgov.be/en/open-data",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Statbel (Belgium)",
    )
    for code, rank, noun in ((REGION, 1, "region"), (PROVINCE, 2, "province"), (MUNICIPALITY, 3, "municipality")):
        p.add("scheme", code=code, polity_id=BELGIUM, kind="administrative", rank=rank, noun=noun, code_system="nis")
    seen: set[object] = set()
    for vintage, (name, since, until) in PLANS.items():
        p.artifact(raw.cached(STATBEL, SECTORS + name), STATBEL_SOURCE)
        for scheme in (REGION, PROVINCE, MUNICIPALITY):
            p.add(
                "boundary_set",
                id=key("boundary_set", scheme, vintage),
                scheme_code=scheme,
                vintage=vintage,
                valid_from=since,
                valid_to=until,
                as_of=since,
            )
        reader, crs = _reader(vintage, True)
        pieces: dict[str, list[shapely.Geometry]] = defaultdict(list)
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            if record is not None and shape is not None:
                pieces[_column(dict(record.as_dict()), "CNIS5_")].append(
                    shapely.geometry.shape(shape.__geo_interface__)
                )
        current = towns(vintage)
        assert set(pieces) == set(current), vintage
        codes = sorted(pieces)
        merged = [shapely.make_valid(shapely.coverage_union_all(pieces[code])) for code in codes]
        simple = list(shapely.make_valid(shapely.coverage_simplify(numpy.array(merged), TOLERANCE)))
        shapes = dict(zip(codes, simple, strict=True))
        regions: dict[str, list[shapely.Geometry]] = defaultdict(list)
        provinces: dict[str, list[shapely.Geometry]] = defaultdict(list)
        for code in codes:
            town = current[code]
            regions[town.region].append(shapes[code])
            if town.province:
                provinces[town.province].append(shapes[code])
        for code, members in sorted(regions.items()):
            id = area(REGION, code)
            names = next(t.region_names for t in current.values() if t.region == code)
            _place(p, seen, id, REGION, code, names, FIRST[code], TERRITORY, since, until)
            _outline(p, id, shapely.coverage_union_all(members), crs, since, until)
        for code, members in sorted(provinces.items()):
            id = area(PROVINCE, code)
            first = next(t for t in current.values() if t.province == code)
            names = {lang: _trim(text) for lang, text in first.province_names.items()}
            _place(p, seen, id, PROVINCE, code, names, FIRST[first.region], area(REGION, first.region), since, until)
            _outline(p, id, shapely.coverage_union_all(members), crs, since, until)
        for code in codes:
            town = current[code]
            parent = area(PROVINCE, town.province) if town.province else area(REGION, town.region)
            id = area(MUNICIPALITY, code)
            _place(p, seen, id, MUNICIPALITY, code, town.names, FIRST[town.region], parent, since, until)
            _outline(p, id, shapes[code], crs, since, until)
    return p


def _place(
    p: Package,
    seen: set[object],
    id: str,
    scheme: str,
    code: str,
    names: dict[str, str],
    first: str,
    parent: str,
    since: date,
    until: date | None,
) -> None:
    if id not in seen:
        seen.add(id)
        p.add("area", id=id, scheme_code=scheme)
    _label(p, id, names, first, seen)
    # a renumbered municipality is one area whose code differs between the 2018 plan and the later ones
    p.add("area_code", area_id=id, code=code, valid_from=since, valid_to=until)
    p.add("area_parent", area_id=id, parent_id=parent, valid_from=since, valid_to=until)
