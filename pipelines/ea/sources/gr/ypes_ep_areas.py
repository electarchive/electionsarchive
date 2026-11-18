"""Where Greece's municipal units are, for placing the European Parliament polling stations.

The Ministry of Interior's results service gives every polling station of 2024 the code of the municipal unit it lies
in (`DEN_ID`), and every 2019 municipal unit it counts on its own (`den_<n>.js`), but no boundaries. The Hellenic
Statistical Authority publishes the Kallikratis municipalities and municipal units of its 2021 census as shapefiles in
the Greek grid (EPSG:2100). The two use different codes, so a Ministry unit is tied to the Authority's unit by name:

1. A name that only one of the Authority's units carries (accents, case, and a parenthetical alias aside) is that unit.
2. A name shared by several units goes to the one in the regional unit that most of the Ministry's matched units of
   the same prefecture fall in.
3. Of what is left (most are the Authority's demotic spelling of a katharevousa name, such as Αραχώβης and
   ΑΡΑΧΟΒΗΣ), a name pairs with the closest unmatched name of its regional unit, or with the only one left.
4. One unit the Ministry created after its 2014 list, Δηλεσίου, is tied by hand.

The Ministry's unit names are those of its 2014 results site, the last one to list the units.
"""

import difflib
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import raw

AUTHORITY = "https://www.statistics.gr/documents/20181/1194366/"
UNITS_FILE = f"{AUTHORITY}DHM_ENOT_2021.zip/dd281bb7-b6b9-679a-5993-f9e08a3354b2"
MUNICIPALITIES_FILE = f"{AUTHORITY}DHMOI_2021.zip/55e3ba79-3475-70b8-5f8a-b912eeee389c"
FILES = (UNITS_FILE, MUNICIPALITIES_FILE)
BY_HAND = {821: "280603"}  # Δηλεσίου, a unit of the municipality of Tanagra that the Ministry's 2014 list lacks
SIMPLIFY = 20.0  # meters in the Greek grid: the outlines are drawn for maps, not for surveys
TRANSFORM = pyproj.Transformer.from_crs(2100, 4326, always_xy=True)


@dataclass(frozen=True)
class Place:
    code: str
    greek: str
    english: str
    shape: shapely.Geometry  # SRID 4326


def fold(text: str) -> str:
    """Upper case without accents or the final sigma, with punctuation as spaces."""
    plain = "".join(c for c in unicodedata.normalize("NFD", text.upper()) if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^Α-ΩA-Z0-9]", " ", plain.replace("ς", "σ")).split())


def read(url: str, source: str) -> list[Place]:
    """The places of one of the Authority's shapefiles, with their outlines in WGS 84."""
    with zipfile.ZipFile(raw.cached(source, url).path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(member(archive, ".shp")),
            shx=io.BytesIO(member(archive, ".shx")),
            dbf=io.BytesIO(member(archive, ".dbf")),
            encoding="utf-8",
        )
        found = []
        for shape, record in zip(reader.shapes(), reader.records(), strict=True):
            if shape is None or record is None:
                raise ValueError("a place has no outline or no record")
            fields = dict(zip([f[0] for f in reader.fields[1:]], record, strict=True))
            geometry = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
            geometry = shapely.simplify(geometry, SIMPLIFY, preserve_topology=True)
            geometry = shapely.transform(geometry, lambda xy: np.column_stack(TRANSFORM.transform(xy[:, 0], xy[:, 1])))
            parts = [part for part in shapely.get_parts(geometry) if shapely.get_type_id(part) in (3, 6)]
            if not parts:
                raise ValueError(f"{fields['CODE']} has no area")
            polygons = [q for part in parts for q in shapely.get_parts(part)]
            found.append(
                Place(
                    str(fields["CODE"]),
                    str(fields["NAME_GR"]).strip(),
                    str(fields["NAME_ENG"]).strip(),
                    shapely.set_srid(shapely.MultiPolygon(polygons), 4326),
                )
            )
    return found


def member(archive: zipfile.ZipFile, suffix: str) -> bytes:
    (name,) = [n for n in archive.namelist() if n.endswith(suffix)]
    return archive.read(name)


def ministry_units(statics: dict[str, str]) -> dict[int, tuple[str, int]]:
    """Each unit of the Ministry's 2014 site with its name and prefecture, from the `den_<n>.js` files."""
    found: dict[int, tuple[str, int]] = {}
    for path, text in statics.items():
        match = re.search(r"/stat/e/den_(\d+)\.js$", path)
        if match and text.strip() != "null":
            row = json.loads(text)
            found[int(match.group(1))] = (str(row["Descr"]), int(row["PEN_ID"]))
    return found


def alias(name: str) -> str:
    return fold(re.sub(r"\(.*?\)", "", name))


def tie(ministry: dict[int, tuple[str, int]], units: list[Place]) -> dict[int, str]:
    """The Authority's code of each Ministry unit, by name."""
    names = {u.code: fold(u.greek.removeprefix("ΔΗΜΟΤΙΚΗ ΕΝΟΤΗΤΑ ")) for u in units}
    by_name: dict[str, list[str]] = defaultdict(list)
    for code, name in names.items():
        by_name[name].append(code)
    found: dict[int, str] = dict(BY_HAND)
    votes: dict[int, Counter[str]] = defaultdict(Counter)
    for number, (name, prefecture) in sorted(ministry.items()):
        if number not in found and len(by_name.get(alias(name), [])) == 1:
            found[number] = by_name[alias(name)][0]
            votes[prefecture][found[number][:2]] += 1
    region = {prefecture: count.most_common(1)[0][0] for prefecture, count in votes.items()}
    for number, (name, prefecture) in sorted(ministry.items()):
        if number in found:
            continue
        options = [c for c in by_name.get(alias(name), []) if c[:2] == region.get(prefecture)]
        if len(options) == 1:
            found[number] = options[0]
    left: dict[str | None, list[int]] = defaultdict(list)
    for number, (_, prefecture) in sorted(ministry.items()):
        if number not in found:
            left[region.get(prefecture)].append(number)
    free = [c for c in names if c not in set(found.values())]
    for group, numbers in left.items():
        codes = [c for c in free if c[:2] == group]
        pairs = sorted(
            (
                (difflib.SequenceMatcher(None, alias(ministry[n][0]), names[c]).ratio(), n, c)
                for n in numbers
                for c in codes
            ),
            reverse=True,
        )
        for ratio, number, code in pairs:
            if number in found or code in found.values():
                continue
            if ratio > 0.5 or (len(numbers) == 1 and len(codes) == 1):
                found[number] = code
    missing = sorted(set(ministry) - set(found))
    if missing:
        raise ValueError(f"no municipal unit of the Authority for the Ministry's units {missing}")
    if len(set(found.values())) != len(found):
        raise ValueError("two of the Ministry's units fall on one of the Authority's")
    return found
