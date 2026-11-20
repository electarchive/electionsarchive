import hashlib
import io
import itertools
import json
import re
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid5

import httpx
import polars as pl
import shapefile
import shapely
from psycopg import Connection

from ea import raw
from ea.changeset import slug
from ea.package import NAMESPACE, Draft
from ea.store import DATA, connect

SOURCE = "pl.prg"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
UNITS = "https://opendata.geoportal.gov.pl/prg/granice/00_jednostki_administracyjne.zip"
SPARQL = "https://query.wikidata.org/sparql?" + urlencode(
    {
        "query": "SELECT ?item ?code WHERE { ?item wdt:P1653 ?code . FILTER NOT EXISTS { ?item wdt:P576 ?end } }",
        "format": "json",
    }
)
ARCHIVE = "https://opendata.geoportal.gov.pl/prg/granice_archiwalne/PRG_jednostki_administracyjne_{}.zip"
# GUGiK's yearly archival sets; 2013 and 2014 are not published. Each set shows the gminas after that year's changes,
# which take effect on 1 January: Ostrowice, abolished on 2019-01-01, is in the 2018 set and not in the 2019 one.
YEARS = (*range(2005, 2013), *range(2015, 2026))
ARCHIVE_TITLE = "Państwowy Rejestr Granic: archiwalne granice jednostek administracyjnych, 2005–2025"
GAP_YEAR = 2015
# Changes first seen in the 2015 set took effect on 2013-01-01, 2014-01-01, or 2015-01-01. Where nothing dates one,
# it is dated 2013-01-01, the first day it can have taken effect, with an unknown precision. These are dated: the
# registry's own validity date for Wałbrzych's city powiat and Stepnica, and for the rest the TERYT codes of the
# polling stations in the European Parliament election of 2014-05-25 (the electoral commission's
# pe2014-gl-lis-obw.xls), which still show each in its earlier form.
GAP = {
    "0265011": (date(2013, 1, 1), "day"),
    "3204073": (date(2014, 1, 1), "day"),
    "0809102": (date(2015, 1, 1), "day"),
    "0862011": (date(2015, 1, 1), "day"),
    "2211043": (date(2015, 1, 1), "day"),
    "2601063": (date(2015, 1, 1), "day"),
    "3020013": (date(2015, 1, 1), "day"),
}
# The 2005 set leaves out Bestwina, which TERYT numbered 02 in its powiat in 1999, and draws its land inside
# Czechowice-Dziedzice; that year neither gets an outline of its own.
ABSENT = {(2005, "2402022"): "2402043"}
# Another gmina takes over a gmina's land when it covers more than this share of its outline, and an outline starts a
# new period when the gmina's area moves by more than this share from one set to the next.
OVERLAP = 0.01
CHANGED = 0.005
AS_OF = date(2026, 11, 10)
TOLERANCE = 10
PUBLISHER = "Główny Urząd Geodezji i Kartografii"
TITLE = "Państwowy Rejestr Granic: jednostki administracyjne, stan na 10 listopada 2026"
PAGE = "https://www.geoportal.gov.pl/pl/dane/panstwowy-rejestr-granic-prg/"
LICENSE = "Free for any use (Dane PRG są dostępne bezpłatnie i do dowolnego wykorzystania)"
WARSAW = "1465011"
LAYERS = {
    "wojewodztwo": "wojew",
    "powiat": "powiat",
    "gmina": "gmin",
    "dzielnica": "jednostek_ewidencyjnych",
}
VOIVODESHIPS = {
    "02": ("dolnośląskie", "Lower Silesian Voivodeship"),
    "04": ("kujawsko-pomorskie", "Kuyavian-Pomeranian Voivodeship"),
    "06": ("lubelskie", "Lublin Voivodeship"),
    "08": ("lubuskie", "Lubusz Voivodeship"),
    "10": ("łódzkie", "Łódź Voivodeship"),
    "12": ("małopolskie", "Lesser Poland Voivodeship"),
    "14": ("mazowieckie", "Masovian Voivodeship"),
    "16": ("opolskie", "Opole Voivodeship"),
    "18": ("podkarpackie", "Subcarpathian Voivodeship"),
    "20": ("podlaskie", "Podlaskie Voivodeship"),
    "22": ("pomorskie", "Pomeranian Voivodeship"),
    "24": ("śląskie", "Silesian Voivodeship"),
    "26": ("świętokrzyskie", "Świętokrzyskie Voivodeship"),
    "28": ("warmińsko-mazurskie", "Warmian-Masurian Voivodeship"),
    "30": ("wielkopolskie", "Greater Poland Voivodeship"),
    "32": ("zachodniopomorskie", "West Pomeranian Voivodeship"),
}
# Gmina and Warsaw district outlines from GeoJSON in ETRS89, simplified as one coverage in EPSG:2180.
SHAPE = """select code, name, st_setsrid(st_coveragesimplify(geom, %s) over (), 2180) as geom
           from (select code, name,
                        st_multi(st_collectionextract(st_makevalid(st_force2d(
                          st_transform(st_setsrid(st_geomfromgeojson(geojson), 4258), 2180))), 3)) as geom
                 from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson)) features"""
GMINA_KINDS = {"1": "urban", "2": "rural", "3": "urban_rural"}
SUFFIX = {"2": "gmina-wiejska", "3": "gmina-miejsko-wiejska"}


def _gmina_paths(gminy: pl.DataFrame, powiaty: dict[str, str]) -> dict[str, str]:
    paths = {
        code: f"pl/{slug(VOIVODESHIPS[code[:2]][0])}/{slug(text)}" for code, text in gminy.select("code", "name").rows()
    }
    groups: dict[str, list[str]] = {}
    for code, path in paths.items():
        groups.setdefault(path, []).append(code)
    for path, codes in groups.items():
        if len(codes) == 1:
            continue
        if len({code[:4] for code in codes}) == 1 and len({code[-1] for code in codes}) == len(codes):
            for code in codes:
                if code[-1] in SUFFIX:
                    paths[code] = f"{path}-{SUFFIX[code[-1]]}"
            continue
        for code in codes:
            paths[code] = f"{path}-{slug(powiaty[code[:4]].removeprefix('powiat '))}"
    return paths


def _get(url: str) -> Path:
    """Download a file in resumed ranges, because GUGiK's download server stalls partway through large files."""
    part = DATA / "tmp" / SOURCE / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.part"
    part.parent.mkdir(parents=True, exist_ok=True)
    total: int | None = None
    for attempt in range(60):
        have = part.stat().st_size if part.exists() else 0
        if total is not None and have >= total:
            break
        headers = {"User-Agent": raw.USER_AGENT, **({"Range": f"bytes={have}-"} if have else {})}
        try:
            with httpx.stream(
                "GET", url, headers=headers, timeout=httpx.Timeout(60, connect=30), follow_redirects=True
            ) as response:
                if response.status_code == 416 and have:
                    total = have
                    break
                response.raise_for_status()
                if have and response.status_code != 206:
                    part.unlink()
                    continue
                total = have + int(response.headers["content-length"])
                with part.open("ab") as file:
                    for chunk in response.iter_bytes(1 << 20):
                        file.write(chunk)
        except httpx.HTTPError as error:
            print(f"{url}: {type(error).__name__} at {have:,} bytes, retrying", file=sys.stderr, flush=True)
            time.sleep(min(60, 5 * (attempt + 1)))
    if total is None or part.stat().st_size != total:
        raise RuntimeError(f"{url} did not finish downloading")
    with zipfile.ZipFile(part) as archive:
        archive.namelist()
    return part


def _download(url: str) -> None:
    if url in raw.keys(SOURCE):
        return
    part = _get(url)
    raw.store(
        SOURCE, url, part.read_bytes(), suffix=".zip", retrieved_at=datetime.now(UTC), media_type="application/zip"
    )
    part.unlink()


def fetch() -> None:
    _download(UNITS)
    raw.fetch(SOURCE, SPARQL, suffix=".json")
    for year in YEARS:
        _download(ARCHIVE.format(year))


def _features(archive: zipfile.ZipFile, layer: str) -> list[tuple[str, str, str]]:
    stem = next(
        name[:-4]
        for name in archive.namelist()
        if name.lower().endswith(".shp") and LAYERS[layer] in name.lower() and "granice" in name.lower()
    )
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{stem}.shp")),
        shx=io.BytesIO(archive.read(f"{stem}.shx")),
        dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
        encoding="utf-8",
    )
    features: list[tuple[str, str, str]] = []
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            continue
        code = str(record["JPT_KOD_JE"]).replace("_", "")
        if layer == "dzielnica" and not (code.startswith("1465") and code.endswith("8")):
            continue
        features.append(
            (code, str(record["JPT_NAZWA_"]).removesuffix(" - dzielnica").strip(), json.dumps(shape.__geo_interface__))
        )
    return features


def _decoded(text: str) -> str:
    """Archival names are UTF-8 in some years and Windows-1250 in others; they are read as Latin-1 and decoded here."""
    data = text.encode("latin-1")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1250")


def _archival(year: int) -> tuple[list[tuple[str, str | None, str]], int]:
    """A vintage's gmina pieces as (TERYT code, name, GeoJSON), and the SRID of their coordinates. Before 2015 the
    sets hold one layer coded `10` + TERYT without its type digit + type + part number, where an urban-rural gmina is
    its town (type 4) and its rural area (type 5), and a piece split in two carries a part number and `[cz.N]`."""
    with zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE.format(year)).path) as archive:
        members = set(archive.namelist())
        stem = next(
            name[:-4]
            for name in sorted(members)
            if name.lower().endswith(".shp")
            and name.rsplit("/", 1)[-1].lower().startswith(("obszary", "gminy", "a03_granice_gmin"))
        )
        prj = archive.read(f"{stem}.prj").decode("latin-1")
        if prj.startswith("GEOGCS") and "WGS_1984" in prj:
            srid = 4326
        elif prj.startswith("GEOGCS") and "ETRS_1989" in prj:
            srid = 4258
        elif "false_easting" in prj.lower() and "-5300000" in prj and "0.9993" in prj:
            srid = 2180
        else:
            raise ValueError(f"{year}: unexpected projection {prj[:80]}")
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{stem}.shp")),
            shx=io.BytesIO(archive.read(f"{stem}.shx")) if f"{stem}.shx" in members else None,
            dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
            encoding="latin-1",
        )
        fields = [field[0].lower() for field in reader.fields[1:]]
        # Read by index where the set has one: 2016's shapes cannot be read in sequence past a malformed record.
        shapes = (reader.shape(n) for n in range(len(reader))) if f"{stem}.shx" in members else reader.iterShapes()
        pieces: list[tuple[str, str | None, str]] = []
        for record, shape in zip(reader.iterRecords(), shapes, strict=True):
            if record is None or shape is None:
                continue
            values = dict(zip(fields, list(record), strict=True))
            if "obszar" in values:
                long = str(values["obszar"])
                if len(long) != 10 or not long.startswith("10") or long[8] not in "12345":
                    raise ValueError(f"{year}: unexpected area code {long}")
                code, text = long[2:8] + ("3" if long[8] in "45" else long[8]), str(values["nazwa"])
                # A town (type 4) carries its own name; the gmina's name is on its rural area (type 5).
                town = long[8] == "4"
            else:
                code, text, town = str(values["jpt_kod_je"]), str(values["jpt_nazwa_"]), False
            if len(code) != 7 or not code.isdigit():
                raise ValueError(f"{year}: unexpected gmina code {code}")
            text = re.sub(r"\s*\[?cz\.\s*\d+\]?$", "", _decoded(text).strip())
            text = re.sub(r"( \(gm\. miejska\)| - miasto| - obszar wiejski)$", "", text)
            text = re.sub(r"^[Mm]\. (St\. )?", "", text).strip()
            pieces.append((code, None if town else text, json.dumps(shape.__geo_interface__)))
    return pieces, srid


def _names(pieces: list[tuple[str, str | None, str]]) -> dict[str, str]:
    names: dict[str, set[str]] = {}
    for code, text, _ in pieces:
        names.setdefault(code, set())
        if text is not None:
            names[code].add(text)
    odd = {code: texts for code, texts in names.items() if len(texts) != 1}
    if odd:
        raise ValueError(f"gminas with two names: {odd}")
    return {code: texts.pop() for code, texts in names.items()}


@dataclass
class Period:
    code: str
    start: date | None
    end: date | None


@dataclass
class Gmina:
    """One gmina through the sets: its codes, names, and outlines over time, each a half-open period."""

    periods: list[Period]
    names: list[tuple[str, date | None, date | None]]
    outlines: list[tuple[str, str, date | None, date | None]] = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.periods[-1].code

    @property
    def end(self) -> date | None:
        return self.periods[-1].end


@dataclass
class Event:
    kind: str
    date: date
    precision: str
    before: list[Gmina]
    after: list[Gmina]


def _when(year: int, code: str) -> tuple[date, str]:
    """The day a change first seen in a set took effect: gmina changes take effect on 1 January, so the first day of
    the set's year, except across the unpublished 2013 and 2014 sets."""
    if year != GAP_YEAR:
        return date(year, 1, 1), "day"
    return GAP.get(code, (date(GAP_YEAR - 2, 1, 1), "unknown"))


def _overlaps(geom: str | None, others: dict[str, str]) -> list[str]:
    """The codes whose outline covers more than OVERLAP of this outline."""
    if geom is None:
        raise ValueError("a gmina without an outline cannot be traced")
    shape = shapely.from_wkb(bytes.fromhex(geom))
    found = []
    for code, other in others.items():
        piece = shapely.from_wkb(bytes.fromhex(other))
        if shape.intersects(piece) and shape.intersection(piece).area > OVERLAP * shape.area:
            found.append(code)
    return sorted(found)


def _history(vintages: pl.DataFrame, current: pl.DataFrame) -> tuple[list[Gmina], list[Event]]:
    """Follow each gmina from the 2005 set to the current one. A code that leaves a set is the same gmina as a code
    that enters the next one when the two share their first six digits (a change of type) or their name within the
    voivodeship (a move to another powiat); any other code that leaves was abolished, and any other that enters was
    created. Outlines start a new period where a gmina's area changes by more than CHANGED."""
    sets = {
        year: {code: (name, area, geom, point) for code, name, area, geom, point in lines.drop("year").rows()}
        for (year,), lines in vintages.group_by("year")
    }
    sets[AS_OF.year] = {
        code: (name, area, geom, point)
        for code, name, area, geom, point in current.select("code", "name", "area", "geom", "label_point").rows()
    }
    years = sorted(sets)
    for (year, code), donor in ABSENT.items():
        if year not in sets:
            continue
        # Named and placed as in the next set, but without an outline of its own.
        sets[year][code] = (sets[years[years.index(year) + 1]][code][0], None, None, None)
        sets[year][donor] = (sets[year][donor][0], None, None, None)
    gminas: list[Gmina] = []
    events: list[Event] = []
    at: dict[tuple[int, str], Gmina] = {}
    for code, (name, *_) in sets[years[0]].items():
        gmina = Gmina([Period(code, None, None)], [(name, None, None)])
        gminas.append(gmina)
        at[(years[0], code)] = gmina
    for before, year in itertools.pairwise(years):
        old, new = sets[before], sets[year]
        gone, came = sorted(set(old) - set(new)), sorted(set(new) - set(old))
        pairs: dict[str, str] = {}
        for code in gone:
            match = [n for n in came if n[:6] == code[:6]] or [
                n for n in came if n[:2] == code[:2] and new[n][0] == old[code][0]
            ]
            if len(match) == 1 and match[0] not in pairs.values():
                pairs[code] = match[0]
        for code in old:
            if code in gone and code not in pairs:
                continue
            gmina = at[(before, code)]
            now = pairs.get(code, code)
            at[(year, now)] = gmina
            if now != code:
                day, precision = _when(year, now)
                gmina.periods[-1].end = day
                gmina.periods.append(Period(now, day, None))
                kind = "transferred" if now[:4] != code[:4] else "recoded"
                events.append(Event(kind, day, precision, [gmina], [gmina]))
            if new[now][0] != old[code][0]:
                day, precision = _when(year, now)
                text, start, _ = gmina.names[-1]
                gmina.names[-1] = (text, start, day)
                gmina.names.append((new[now][0], day, None))
                events.append(Event("renamed", day, precision, [gmina], [gmina]))
        outlines = {code: line[2] for code, line in new.items() if line[2] is not None}
        for code in gone:
            if code in pairs:
                continue
            day, precision = _when(year, code)
            gmina = at[(before, code)]
            gmina.periods[-1].end = day
            text, start, _ = gmina.names[-1]
            gmina.names[-1] = (text, start, day)
            heirs = [at[(year, heir)] for heir in _overlaps(old[code][2], outlines)]
            if not heirs:
                raise ValueError(f"{year}: no gmina took over {code} {old[code][0]}")
            if len(heirs) == 1:
                events.append(Event("merged", day, precision, [gmina, heirs[0]], heirs))
            else:
                events.append(Event("split", day, precision, [gmina], heirs))
        outlines = {code: line[2] for code, line in old.items() if line[2] is not None}
        for code in came:
            if code in pairs.values():
                continue
            day, precision = _when(year, code)
            gmina = Gmina([Period(code, day, None)], [(new[code][0], day, None)])
            gminas.append(gmina)
            at[(year, code)] = gmina
            donors = [at[(before, donor)] for donor in _overlaps(new[code][2], outlines)]
            if not donors:
                raise ValueError(f"{year}: {code} {new[code][0]} took no gmina's land")
            if len(donors) == 1:
                events.append(Event("split", day, precision, donors, [*donors, gmina]))
            else:
                events.append(Event("created", day, precision, donors, [gmina]))
    for gmina in gminas:
        _outlines(gmina, sets, at)
    return gminas, events


Sets = dict[int, dict[str, tuple[str, float | None, str | None, str | None]]]


def _outlines(gmina: Gmina, sets: Sets, at: dict[tuple[int, str], Gmina]) -> None:
    """Split a gmina's outlines into periods: a new one starts where its area moves by more than CHANGED from the
    last set's, and each period takes the outline of its latest set."""
    codes = {year: code for (year, code), other in at.items() if other is gmina}
    periods: list[list[tuple[int, float, str, str]]] = []
    for year in sorted(codes):
        _, area, geom, point = sets[year][codes[year]]
        if area is None or geom is None or point is None:
            continue
        if periods and abs(area - periods[-1][-1][1]) <= CHANGED * periods[-1][-1][1]:
            periods[-1].append((year, area, geom, point))
        else:
            periods.append([(year, area, geom, point)])
    for n, period in enumerate(periods):
        start = gmina.periods[0].start if n == 0 else _when(period[0][0], codes[period[0][0]])[0]
        end = gmina.end if n == len(periods) - 1 else _when(periods[n + 1][0][0], codes[periods[n + 1][0][0]])[0]
        _, _, geom, point = period[-1]
        gmina.outlines.append((geom, point, start, end))


def _vintages() -> pl.DataFrame:
    """Each archival set's gminas: the name, and the outline and label point simplified as one coverage like the
    current set's, with its area in EPSG:2180."""
    frames: list[pl.DataFrame] = []
    for year in YEARS:
        pieces, srid = _archival(year)
        names = _names(pieces)
        with connect() as conn:
            rows = conn.execute(
                """with pieces as (
                     select code, st_multi(st_collectionextract(st_makevalid(st_force2d(
                              st_transform(st_setsrid(st_geomfromgeojson(geojson), %s), 2180))), 3)) as geom
                     from unnest(%s::text[], %s::text[]) as f(code, geojson)
                   ), gminy as materialized (
                     select code, st_multi(st_collectionextract(st_makevalid(st_union(geom, 0.01)), 3)) as geom
                     from pieces group by code
                   ), simple as (
                     select code, st_setsrid(st_coveragesimplify(geom, %s) over (), 2180) as geom from gminy
                   )
                   select code, st_area(geom),
                          encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                          encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
                   from simple""",
                [srid, [code for code, _, _ in pieces], [geom for _, _, geom in pieces], TOLERANCE],
            ).fetchall()
        frame = pl.DataFrame(
            rows,
            schema={"code": pl.String, "area": pl.Float64, "geom": pl.String, "label_point": pl.String},
            orient="row",
        ).with_columns(year=pl.lit(year, pl.Int32), name=pl.col("code").replace_strict(names))
        if frame["geom"].null_count() or frame.height != len(names):
            raise ValueError(f"{year}: outlines missing")
        print(f"{SOURCE}: {year} has {frame.height:,} gminas", flush=True)
        frames.append(frame)
    return pl.concat(frames).select("year", "code", "name", "area", "geom", "label_point")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, UNITS).path) as archive:
        gminy = _features(archive, "gmina")
        districts = _features(archive, "dzielnica")
        names = {
            layer: {code: text for code, text, _ in _features(archive, layer)} for layer in ("wojewodztwo", "powiat")
        }
    pieces = [(code, text, geom) for code, text, geom in gminy if code != WARSAW] + districts
    warsaw = next(text for code, text, _ in gminy if code == WARSAW)
    with connect() as conn:
        rows = conn.execute(
            f"""with shape as materialized ({SHAPE}), units as (
                 select case when code like '1465__8' then 'dzielnica' else 'gmina' end as layer, code,
                        case when code like '1465__8' then %s else left(code, 4) end as parent, name, geom
                 from shape
                 union all
                 select 'gmina', %s, '1465', %s, st_setsrid(st_multi(st_union(geom, 0.01)), 2180)
                 from shape where code like '1465__8'
               ), powiaty as (
                 select 'powiat' as layer, left(code, 4) as code, min(left(code, 2)) as parent, null as name,
                        st_setsrid(st_multi(st_union(geom, 0.01)), 2180) as geom
                 from units where layer = 'gmina' group by left(code, 4)
               ), wojewodztwa as (
                 select 'wojewodztwo' as layer, parent as code, 'PL' as parent, null as name,
                        st_setsrid(st_multi(st_union(geom, 0.01)), 2180) as geom
                 from powiaty group by parent
               ), layers as (
                 select * from units
                 union all select * from powiaty
                 union all select * from wojewodztwa
                 union all select 'nation', 'PL', null, null, st_setsrid(st_multi(st_union(geom, 0.01)), 2180)
                 from wojewodztwa
               )
               select layer, code, parent, name, st_area(geom),
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from layers""",
            [TOLERANCE, *(list(column) for column in zip(*pieces, strict=True)), WARSAW, WARSAW, warsaw],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "layer": pl.String,
            "code": pl.String,
            "parent": pl.String,
            "name": pl.String,
            "area": pl.Float64,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    ).with_columns(
        name=pl.when(pl.col("layer") == "powiat")
        .then(pl.col("code").replace_strict(names["powiat"], default=None))
        .when(pl.col("layer") == "wojewodztwo")
        .then(pl.col("code").replace_strict(names["wojewodztwo"], default=None))
        .otherwise(pl.col("name"))
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    if (
        counts["wojewodztwo"] != 16
        or counts["dzielnica"] != 18
        or counts["powiat"] != len(names["powiat"])
        or frame.filter(pl.col("layer") != "nation")["name"].null_count()
        or frame["geom"].null_count()
    ):
        raise ValueError(f"unexpected layers: {counts}")
    totals = dict(frame.group_by("layer").agg(pl.col("area").sum()).iter_rows())
    totals["dzielnica"] = frame.filter(pl.col("code") == WARSAW)["area"][0] / totals["dzielnica"]
    totals = {layer: total for layer, total in totals.items() if layer != "dzielnica"}
    if max(totals.values()) / min(totals.values()) > 1.0001:
        raise ValueError(f"layer areas disagree: {totals}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")
    _vintages().write_parquet(PARSED / "vintages.parquet")


def _stable(conn: Connection, outlines: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep the published outline and label point where a rebuild draws the same outline to within a millimeter, so
    floating-point differences between PostGIS builds don't rewrite it. An outline is matched by its area and the
    end of its period, so the current outline still matches when an earlier one now precedes it."""
    published = {
        (str(area), end): (str(geom), None if point is None else str(point))
        for area, end, geom, point in conn.execute(
            """select g.area_id, g.valid_to, encode(st_asewkb(g.geom), 'hex'), encode(st_asewkb(g.label_point), 'hex')
               from area_geometry g join changeset c on c.id = g.changeset_id where c.pipeline = %s""",
            [SOURCE],
        )
    }
    pairs = [
        (n, published[key][0])
        for n, row in enumerate(outlines)
        if (key := (str(row["area_id"]), row["valid_to"])) in published
        and tuple(str(text).upper() for text in published[key])
        != (str(row["geom"]).upper(), str(row["label_point"]).upper())
    ]
    close = {
        int(n)
        for (n,) in conn.execute(
            """select n from unnest(%s::int[], %s::text[], %s::text[]) as p(n, new, old)
               where st_hausdorffdistance(st_transform(st_geomfromewkb(decode(new, 'hex')), 2180),
                                          st_transform(st_geomfromewkb(decode(old, 'hex')), 2180)) < 0.001""",
            [[n for n, _ in pairs], [str(outlines[n]["geom"]) for n, _ in pairs], [old for _, old in pairs]],
        )
    }
    for n in close:
        row = outlines[n]
        row["geom"], row["label_point"] = published[(str(row["area_id"]), row["valid_to"])]
    return outlines


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    items: dict[str, str] = {}
    for row in json.loads(raw.cached(SOURCE, SPARQL).path.read_text())["results"]["bindings"]:
        items.setdefault(row["code"]["value"], row["item"]["value"].rsplit("/", 1)[1])
    layers = {layer: lines.sort("code") for (layer,), lines in frame.group_by("layer")}
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Poland's {len(layers['wojewodztwo'])} voivodeships, {len(layers['powiat'])} powiats, "
                f"{len(layers['gmina'])} gminas, and Warsaw's {len(layers['dzielnica'])} districts from GUGiK's PRG, "
                f"with the gminas' codes, names, outlines, and lineage since {YEARS[0]} from its archival sets"
            ),
            polity="PL",
        )

        def name(entity: UUID, text: str, lang: str = "pl", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license=LICENSE,
            license_url=PAGE,
            attribution="Główny Urząd Geodezji i Kartografii, Państwowy Rejestr Granic",
            notes=(
                f"Gmina and Warsaw district polygons simplified as one coverage to {TOLERANCE} m in EPSG:2180; "
                "Warsaw, powiats, voivodeships, and the country are unions of them."
            ),
        )
        cs.artifact(raw.cached(SOURCE, UNITS), source)
        wikidata = cs.source("Wikidata", "Wikidata items with a TERYT code (P1653)")
        cs.add(
            "source",
            id=wikidata,
            kind="wikidata",
            publisher="Wikidata",
            title="Wikidata items with a TERYT code (P1653)",
            url="https://www.wikidata.org/wiki/Property:P1653",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        cs.artifact(raw.cached(SOURCE, SPARQL), wikidata)
        archive = cs.source(PUBLISHER, ARCHIVE_TITLE)
        cs.add(
            "source",
            id=archive,
            kind="official_body",
            publisher=PUBLISHER,
            title=ARCHIVE_TITLE,
            url=PAGE,
            license=LICENSE,
            license_url=PAGE,
            attribution="Główny Urząd Geodezji i Kartografii, Państwowy Rejestr Granic",
            notes=(
                f"GUGiK's yearly sets for {YEARS[0]} to {YEARS[-1]}, without 2013 and 2014. Gmina outlines simplified "
                f"as one coverage to {TOLERANCE} m in EPSG:2180, each dated from the first set that shows it to the "
                "first that shows a change of its area by more than "
                f"{CHANGED:.1%}; codes, names, and lineage from comparing consecutive sets."
            ),
        )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, ARCHIVE.format(year)), archive)

        gminas, events = _history(pl.read_parquet(PARSED / "vintages.parquet"), layers["gmina"])
        current = {gmina.code: gmina for gmina in gminas if gmina.end is None}
        if set(current) != set(layers["gmina"]["code"]):
            raise ValueError("the history's current gminas differ from the current set's")
        found: dict[int, UUID] = {}
        outlines: list[dict[str, object]] = []

        def geometry(
            area: UUID, geom: str, point: str | None, start: date | None = None, end: date | None = None
        ) -> None:
            outlines.append(
                {
                    "area_id": area,
                    "valid_from": start,
                    "valid_to": end,
                    "geom": geom,
                    "label_point": point,
                    "quality": "official",
                    "source_id": source if end is None else archive,
                }
            )

        def spans(gmina: Gmina, part: Callable[[str], str]) -> list[tuple[str, date | None, date | None]]:
            """Merge the gmina's code periods into periods of one value of the code's part."""
            merged: list[tuple[str, date | None, date | None]] = []
            for period in gmina.periods:
                value = part(period.code)
                if merged and merged[-1][0] == value:
                    merged[-1] = (value, merged[-1][1], period.end)
                else:
                    merged.append((value, period.start, period.end))
            return merged

        def history(gmina: Gmina, area: UUID) -> None:
            for period in gmina.periods:
                cs.add("area_code", area_id=area, code=period.code, valid_from=period.start, valid_to=period.end)
            for kind, start, end in spans(gmina, lambda code: GMINA_KINDS[code[-1]]):
                cs.add("area_kind", area_id=area, kind=kind, valid_from=start, valid_to=end)
            for powiat, start, end in spans(gmina, lambda code: code[:4]):
                cs.add("area_parent", area_id=area, parent_id=areas[("powiat", powiat)], valid_from=start, valid_to=end)
            for geom, point, start, end in gmina.outlines:
                geometry(area, geom, point, start, end)
            former(area, gmina)

        def former(entity: UUID, gmina: Gmina) -> None:
            for text, start, end in gmina.names[:-1]:
                cs.add(
                    "name",
                    entity_id=entity,
                    text=text,
                    lang="pl",
                    kind="historical",
                    is_preferred=False,
                    valid_from=start,
                    valid_to=end,
                    source_id=archive,
                )

        poland = cs.polity("PL")
        if poland in cs.minted:
            raise ValueError("publish eu.members before pl.prg")
        for code, rank, noun, kind, kinds in (
            ("pl.nation", 0, "country", "administrative", None),
            ("pl.wojewodztwo", 1, "voivodeship", "administrative", None),
            ("pl.powiat", 2, "powiat", "administrative", {"powiat": "powiat", "city": "city with powiat rights"}),
            ("pl.gmina", 3, "gmina", "administrative", {kind: kind.replace("_", "-") for kind in GMINA_KINDS.values()}),
            ("pl.dzielnica_warszawy", 4, "district of Warsaw", "administrative", None),
        ):
            cs.add(
                "scheme",
                code=code,
                polity_id=poland,
                kind=kind,
                rank=rank,
                noun=noun,
                code_system=None if code == "pl.nation" else "teryt",
                kinds=kinds,
            )

        nation = cs.area("pl.nation", "PL")
        cs.add("area", id=nation, scheme_code="pl.nation")
        cs.add("area_code", area_id=nation, code="PL", valid_from=None)
        name(nation, "Poland", "en")
        name(nation, "Polska", "pl", "native")
        geometry(nation, layers["nation"]["geom"][0], None)

        areas: dict[tuple[str, str], UUID] = {("nation", "PL"): nation}
        polities: dict[tuple[str, str], UUID] = {}
        paths: dict[tuple[str, str], str] = {}
        taken: set[str] = set()
        parents = {"wojewodztwo": "nation", "powiat": "wojewodztwo", "gmina": "powiat", "dzielnica": "gmina"}
        schemes = {
            "wojewodztwo": "pl.wojewodztwo",
            "powiat": "pl.powiat",
            "gmina": "pl.gmina",
            "dzielnica": "pl.dzielnica_warszawy",
        }
        gmina_paths = _gmina_paths(layers["gmina"], dict(layers["powiat"].select("code", "name").iter_rows()))
        for layer in ("wojewodztwo", "powiat", "gmina", "dzielnica"):
            scheme = schemes[layer]
            for code, parent, text, geom, point in (
                layers[layer].select("code", "parent", "name", "geom", "label_point").rows()
            ):
                area = areas[(layer, code)] = cs.area(scheme, code)
                cs.add("area", id=area, scheme_code=scheme)
                city = layer == "powiat" and int(code[2:]) >= 61
                if layer == "gmina":
                    gmina = current[code]
                    found[id(gmina)] = area
                    history(gmina, area)
                else:
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=areas[(parents[layer], parent)], valid_from=None)
                    geometry(area, geom, point)
                if layer == "powiat":
                    cs.add("area_kind", area_id=area, kind="city" if city else "powiat", valid_from=None)
                if layer == "wojewodztwo":
                    native, english = VOIVODESHIPS[code]
                    if text != native:
                        raise ValueError(f"voivodeship {code} is {text}, expected {native}")
                    polity = cs.polity(f"PL-{code}")
                    cs.add("polity", id=polity, kind="region", iso_code=f"PL-{code}")
                    cs.add("polity_parent", polity_id=polity, parent_id=poland)
                    path = f"pl/{slug(native)}"
                    for entity in (polity, area):
                        name(entity, f"województwo {native}")
                        name(entity, english, "en", "english")
                elif city:
                    name(area, text)
                    continue
                else:
                    polity = cs.key(f"{SOURCE}.polity", code)
                    if layer == "gmina":
                        kind, path = "municipality", gmina_paths[code]
                    else:
                        kind = "county" if layer == "powiat" else "submunicipal"
                        path = f"{paths[(parents[layer], parent)]}/{slug(text)}"
                    cs.add("polity", id=polity, kind=kind, iso_code=None)
                    cs.add(
                        "polity_parent",
                        polity_id=polity,
                        parent_id=polities.get((parents[layer], parent)) or polities[("wojewodztwo", code[:2])],
                    )
                    for entity in (polity, area):
                        name(entity, text)
                    if layer == "gmina":
                        former(polity, current[code])
                if path in taken:
                    raise ValueError(f"slug {path} taken twice")
                taken.add(path)
                paths[(layer, code)] = path
                polities[(layer, code)] = polity
                cs.add("slug", path=path, entity_id=polity)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                item = items.get(code)
                if item is not None:
                    cs.add("external_id", entity_id=polity, scheme="wikidata", value=item)
        for gmina in gminas:
            if gmina.end is None:
                continue
            area = found[id(gmina)] = cs.area("pl.gmina", gmina.code)
            cs.add("area", id=area, scheme_code="pl.gmina")
            history(gmina, area)
            name(area, gmina.names[-1][0])
        for event in events:
            members = [(gmina, "from") for gmina in event.before] + [(gmina, "to") for gmina in event.after]
            parts = [SOURCE, "area_event", event.kind, event.date.isoformat(), *(g.code + r for g, r in members)]
            event_id = uuid5(NAMESPACE, "\x1f".join(parts))
            cs.add(
                "area_event",
                id=event_id,
                kind=event.kind,
                date=event.date,
                date_precision=event.precision,
                source_id=archive,
                notes=None
                if event.precision == "day"
                else "First seen in the 2015 set; GUGiK publishes no set for 2013 or 2014, so it took effect on "
                "2013-01-01 or 2014-01-01, and the date given is the earlier.",
            )
            for gmina, role in members:
                cs.add("area_event_member", event_id=event_id, area_id=found[id(gmina)], role=role)
        for row in _stable(conn, outlines):
            cs.add("area_geometry", **row)
        # The current sets keep their ids; the gmina set of 2026 now starts where the 2025 set ends.
        held = {
            (str(scheme), str(vintage)): str(id)
            for scheme, vintage, id in conn.execute(
                """select b.scheme_code, b.vintage, b.id from boundary_set b join changeset c on c.id = b.changeset_id
                   where c.pipeline = %s""",
                [SOURCE],
            )
        }
        for scheme in schemes.values():
            dated = scheme == "pl.gmina"
            for year in [*YEARS, AS_OF.year] if dated else [AS_OF.year]:
                cs.add(
                    "boundary_set",
                    id=held.get((scheme, str(year)))
                    or str(uuid5(NAMESPACE, f"{SOURCE}\x1fboundary_set\x1f{scheme}\x1f{year}")),
                    scheme_code=scheme,
                    vintage=str(year),
                    valid_from=date(year, 1, 1) if dated else None,
                    valid_to=date(year + 1, 1, 1) if dated and year != AS_OF.year else None,
                    as_of=AS_OF if year == AS_OF.year else date(year, 1, 1),
                )
        return cs.write()
