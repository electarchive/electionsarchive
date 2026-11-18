"""Luxembourg's commune and canton shapes since before 1920, and the mergers between them.

The Administration du cadastre et de la topographie publishes the commune boundaries in force in each period (before
1920, 1920 to 1977, 1978, 1979 to 2005, 2006 to 2011, 2012 to 2014, 2015 to 2017, and 2018 to 2023) and the current
administrative boundaries, and a list of the mergers between them. Each period's file is one layer of communes with
their cantons. A commune whose shape is the same in consecutive files keeps one geometry across them, so the
geometry versions mark the dates a boundary changed.
"""

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple

import numpy
import pyproj
import shapefile
import shapely

from ea import raw
from ea.sources.lu.model import slug

SOURCE = "lu.elections"
HISTORY = "https://download.data.public.lu/resources/limites-communales-historiques-1"
CURRENT = "https://download.data.public.lu/resources/limites-administratives-du-grand-duche-de-luxembourg"
MERGERS_PDF = f"{HISTORY}/20230901-115154/fusions-communales.pdf"
DATASET = "https://data.public.lu/fr/datasets/limites-communales-historiques-1/"
SAME = 1.0  # square meters of symmetric difference below which two shapes are one


class Period(NamedTuple):
    vintage: str
    url: str
    start: date | None
    end: date | None


PERIODS = (
    Period("1919", f"{HISTORY}/20170724-132543/Limites_communales_historiques_avant_1920.zip", None, date(1920, 1, 1)),
    Period(
        "1920",
        f"{HISTORY}/20170724-132611/Limites_communales_historiques_1920-1977.zip",
        date(1920, 1, 1),
        date(1978, 1, 1),
    ),
    Period(
        "1978",
        f"{HISTORY}/20170724-132637/Limites_communales_historiques_1978.zip",
        date(1978, 1, 1),
        date(1979, 1, 1),
    ),
    Period(
        "1979",
        f"{HISTORY}/20170724-132715/Limites_communales_historiques_1979-2005.zip",
        date(1979, 1, 1),
        date(2006, 1, 1),
    ),
    Period(
        "2006",
        f"{HISTORY}/20170724-132734/Limites_communales_historiques_2006-2011.zip",
        date(2006, 1, 1),
        date(2012, 1, 1),
    ),
    Period(
        "2012",
        f"{HISTORY}/20170724-132800/Limites_communales_historiques_2012-2014.zip",
        date(2012, 1, 1),
        date(2015, 1, 1),
    ),
    Period(
        "2015",
        f"{HISTORY}/20170724-132826/Limites_communales_historiques_2015-2016.zip",
        date(2015, 1, 1),
        date(2018, 1, 1),
    ),
    Period(
        "2018",
        f"{HISTORY}/20230915-113035/limites-communales-historiques-2018-2023.zip",
        date(2018, 1, 1),
        date(2023, 9, 1),
    ),
    Period("2023", f"{CURRENT}/20261105-042000/limadmin-shp.zip", date(2023, 9, 1), None),
)
LISTS = (
    f"{CURRENT}/20240709-164127/commune-canton-district-circonscription-arrondissements.csv",
    f"{CURRENT}/20251021-085328/liste-communes-sections.csv",
)


class Merger(NamedTuple):
    """One entry of the authority's list: communes that joined, and the commune they formed or joined."""

    day: date
    sources: tuple[str, ...]
    target: str


# The authority's list of mergers, with the commune names as the period files spell them. The list's "Schengen" is the
# former commune of Remerschen, and its renamings are not mergers, so they are checked apart below.
MERGERS = (
    Merger(date(1920, 1, 1), ("Hollerich", "Rollingergrund", "Hamm", "Eich"), "Luxembourg"),
    Merger(date(1978, 1, 1), ("Asselborn", "Boevange (Clervaux)", "Hachiville", "Oberwampach"), "Wincrange"),
    Merger(date(1979, 1, 1), ("Arsdorf", "Bigonville", "Folschette", "Perlé"), "Rambrouch"),
    Merger(date(1979, 1, 1), ("Junglinster", "Rodenbourg"), "Junglinster"),
    Merger(date(1979, 1, 1), ("Harlange", "Mecher"), "Lac de la Haute-Sûre"),
    Merger(date(2006, 1, 1), ("Kautenbach", "Wilwerwiltz"), "Kiischpelt"),
    Merger(date(2006, 1, 1), ("Bastendorf", "Fouhren"), "Tandel"),
    Merger(date(2012, 1, 1), ("Esch-sur-Sûre", "Heiderscheid", "Neunhausen"), "Esch-sur-Sûre"),
    Merger(date(2012, 1, 1), ("Ermsdorf", "Medernach"), "Vallée de l'Ernz"),
    Merger(date(2012, 1, 1), ("Burmerange", "Remerschen", "Wellenstein"), "Schengen"),
    Merger(date(2012, 1, 1), ("Consthum", "Hoscheid", "Hosingen"), "Parc Hosingen"),
    Merger(date(2012, 1, 1), ("Bascharage", "Clemency"), "Käerjeng"),
    Merger(date(2012, 1, 1), ("Clervaux", "Heinerscheid", "Munshausen"), "Clervaux"),
    Merger(date(2015, 1, 1), ("Eschweiler", "Wiltz"), "Wiltz"),
    Merger(date(2018, 1, 1), ("Boevange-sur-Attert", "Tuntange"), "Helperknapp"),
    Merger(date(2018, 1, 1), ("Mompach", "Rosport"), "Rosport-Mompach"),
    Merger(date(2018, 1, 1), ("Hobscheid", "Septfontaines"), "Habscht"),
    Merger(date(2023, 9, 1), ("Grosbous", "Wahl"), "Groussbus-Wal"),
    Merger(date(2023, 9, 1), ("Bous", "Waldbredimus"), "Bous-Waldbredimus"),
)
RENAMES = (
    (date(2015, 1, 1), "Erpeldange", "Erpeldange-sur-Sûre"),
    (date(2018, 1, 1), "Redange", "Redange/Attert"),
)


class Version(NamedTuple):
    """One commune as one run of consecutive period files draws it."""

    canton: str
    start: date | None
    end: date | None
    shape: shapely.Geometry


@dataclass
class Commune:
    name: str
    versions: list[Version] = field(default_factory=list)

    def parents(self) -> list[tuple[str, date | None, date | None]]:
        """The cantons the commune lay in, with the spans of each."""
        spans: list[tuple[str, date | None, date | None]] = []
        for version in self.versions:
            if spans and spans[-1][0] == version.canton and spans[-1][2] == version.start:
                spans[-1] = (version.canton, spans[-1][1], version.end)
            else:
                spans.append((version.canton, version.start, version.end))
        return spans


@dataclass
class Atlas:
    communes: dict[str, Commune]
    cantons: dict[str, Commune]
    circonscriptions: dict[str, Commune]
    artifacts: list[raw.Artifact]


def fetch() -> None:
    """Cache the period files, the list of mergers, and the authority's lists of communes."""
    raw.fetch(SOURCE, MERGERS_PDF, suffix=".pdf")
    for url in LISTS:
        raw.fetch(SOURCE, url, suffix=".csv")
    for period in PERIODS:
        raw.fetch(SOURCE, period.url, suffix=".zip")


def _open(zf: zipfile.ZipFile, member: str) -> shapefile.Reader:
    names = zf.namelist()
    encoding = zf.read(f"{member}.cpg").decode().strip() if f"{member}.cpg" in names else "cp1252"
    return shapefile.Reader(
        shp=io.BytesIO(zf.read(f"{member}.shp")),
        dbf=io.BytesIO(zf.read(f"{member}.dbf")),
        shx=io.BytesIO(zf.read(f"{member}.shx")),
        encoding=encoding,
    )


def _layer(artifact: raw.Artifact, member: str | None) -> list[tuple[dict[str, object], shapely.Geometry]]:
    zf = zipfile.ZipFile(artifact.path)
    if member is None:
        found = [name[:-4] for name in zf.namelist() if name.endswith(".shp")]
        if len(found) != 1:
            raise ValueError(f"{artifact.url}: expected one shapefile, found {found}")
        member = found[0]
    reader = _open(zf, member)
    fields = [field[0] for field in reader.fields[1:]]
    layer: list[tuple[dict[str, object], shapely.Geometry]] = []
    for i in range(len(reader)):
        record, shape = reader.record(i), reader.shape(i)
        if record is None or shape is None:
            raise ValueError(f"{artifact.url}: row {i} is empty")
        layer.append((dict(zip(fields, record, strict=True)), shapely.force_2d(shapely.geometry.shape(shape))))
    return layer


def wgs(shape: shapely.Geometry) -> shapely.Geometry:
    """A shape in the national grid (EPSG:2169) as a valid multipolygon in WGS 84."""
    project = pyproj.Transformer.from_crs(2169, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _same(a: shapely.Geometry, b: shapely.Geometry) -> bool:
    return shapely.symmetric_difference(shapely.make_valid(a), shapely.make_valid(b)).area < SAME


def _extend(
    found: dict[str, Commune], key: str, name: str, canton: str, period: Period, shape: shapely.Geometry
) -> None:
    commune = found.setdefault(key, Commune(name))
    last = commune.versions[-1] if commune.versions else None
    if last is not None and last.end == period.start and last.canton == canton and _same(last.shape, shape):
        commune.versions[-1] = last._replace(end=period.end)
    else:
        commune.versions.append(Version(canton, period.start, period.end, shape))


def load() -> Atlas:
    """Read every period's communes and cantons, and check them against the authority's list of mergers."""
    communes: dict[str, Commune] = {}
    cantons: dict[str, Commune] = {}
    circles: dict[str, Commune] = {}
    artifacts = [raw.cached(SOURCE, MERGERS_PDF), *(raw.cached(SOURCE, url) for url in LISTS)]
    names: list[dict[str, str]] = []
    for period in PERIODS:
        artifact = raw.cached(SOURCE, period.url)
        artifacts.append(artifact)
        current = period.end is None
        layer = _layer(artifact, "LIMADM_COMMUNES" if current else None)
        names.append({slug(str(row["COMMUNE"])): str(row["COMMUNE"]) for row, _ in layer})
        if len(names[-1]) != len(layer):
            raise ValueError(f"{period.url}: a commune appears twice")
        for row, shape in layer:
            _extend(communes, slug(str(row["COMMUNE"])), str(row["COMMUNE"]), slug(str(row["CANTON"])), period, shape)
        members: dict[str, list[shapely.Geometry]] = {}
        spelling: dict[str, str] = {}
        for row, shape in layer:
            members.setdefault(slug(str(row["CANTON"])), []).append(shape)
            spelling[slug(str(row["CANTON"]))] = str(row["CANTON"])
        if current:
            official = {slug(str(row["CANTON"])): shape for row, shape in _layer(artifact, "LIMADM_CANTONS")}
            if set(official) != set(members):
                raise ValueError(f"{period.url}: the canton layer and the commune layer disagree")
            dissolved = official
        else:
            dissolved = {
                key: shapely.union_all([shapely.make_valid(shape) for shape in shapes])
                for key, shapes in members.items()
            }
        for key, shape in dissolved.items():
            _extend(cantons, key, spelling[key], key, period, shape)
        _circles(circles, artifact, layer, period)
    _check(names)
    return Atlas(communes, cantons, circles, artifacts)


def _circles(
    found: dict[str, Commune],
    artifact: raw.Artifact,
    layer: list[tuple[dict[str, object], shapely.Geometry]],
    period: Period,
) -> None:
    """The legislative constituencies of one period: the current file's own layer, or the period file's communes
    dissolved by their constituency. Files before 2006 carry no constituency column and add nothing."""
    if period.end is None:
        shapes = {
            slug(str(row["NAME"])): (str(row["NAME"]), shape) for row, shape in _layer(artifact, "LIMADM_LEGISLATIVE")
        }
    elif "CIRCONSCRI" in layer[0][0]:
        members: dict[str, list[shapely.Geometry]] = {}
        spelling: dict[str, str] = {}
        for row, shape in layer:
            key = slug(str(row["CIRCONSCRI"]))
            members.setdefault(key, []).append(shapely.make_valid(shape))
            spelling[key] = str(row["CIRCONSCRI"])
        shapes = {key: (spelling[key], shapely.union_all(parts)) for key, parts in members.items()}
    else:
        return
    if set(shapes) != {"sud", "centre", "nord", "est"}:
        raise ValueError(f"{period.vintage}: expected four constituencies, found {sorted(shapes)}")
    for key, (name, shape) in shapes.items():
        _extend(found, key, name, key, period, shape)


def _check(names: list[dict[str, str]]) -> None:
    """Every commune that appears or disappears between two files is accounted for by the list of mergers."""
    for period, before, after in zip(PERIODS[1:], names, names[1:], strict=False):
        gone = {key for key in before if key not in after}
        new = {key for key in after if key not in before}
        listed = [m for m in MERGERS if m.day == period.start]
        renamed = [(old, new_) for day, old, new_ in RENAMES if day == period.start]
        expected_gone = {slug(s) for m in listed for s in m.sources if slug(s) != slug(m.target)} | {
            slug(old) for old, _ in renamed
        }
        expected_new = {slug(m.target) for m in listed if slug(m.target) not in before} | {
            slug(new_) for _, new_ in renamed
        }
        if gone != expected_gone or new != expected_new:
            raise ValueError(
                f"{period.vintage}: the files show {sorted(gone)} leaving and {sorted(new)} arriving, the mergers "
                f"list {sorted(expected_gone)} and {sorted(expected_new)}"
            )
        for merger in listed:
            if not all(slug(s) in before for s in merger.sources) or slug(merger.target) not in after:
                raise ValueError(f"{period.vintage}: a merger into {merger.target} names a commune the files lack")
