"""Shapes of the 43 regional constituencies (Regionalwahlkreise) of the National Council elections from 1994.

The Nationalrats-Wahlordnung 1992 (BGBl. 471/1992, §§ 2 and 3) divides the country into 9 state constituencies and
43 regional constituencies. Each regional constituency is made of whole voting districts (Stimmbezirke): political
districts (in Lower Austria and Vorarlberg administrative districts), cities with their own statute, and in Vienna
municipal districts. The Interior Ministry's brochure of the 2002 election prints which each one comprises; the law was
unchanged between 1994 and 2002.

The shapes are unions of current outlines:

- Lower Austria: municipalities from the Federal Office of Metrology and Survey (BEV), each assigned to the political
  district it belonged to in 1995. Its district of Wien-Umgebung, dissolved in 2017, is rebuilt this way, and the
  municipalities that have since changed district stay with their constituency of 1994;
- Vienna: the municipal districts' outlines from the City of Vienna;
- the other states: the political districts of geoBoundaries (`official`), except in Styria, where Fürstenfeld, a
  district of its own until 2013, now belongs to the district of Hartberg-Fürstenfeld; that district goes whole to
  constituency 6E, so 6D and 6E are `synthetic`.

The sources simplify their outlines differently, so neighbors overlap by slivers; each constituency gives up what the
ones before it hold.
"""

import json
import re
from datetime import date

import shapely
import shapely.geometry
from shapely.geometry.base import BaseGeometry

from ea import raw
from ea.package import Package
from ea.sources.at import noe, wahlkreise_1923

SOURCE = "at.bmi"
BROCHURE_2002 = "https://www.bmi.gv.at/412/nationalratswahlen/nationalratswahl_2002/files/nrw02_brosch_internet.pdf"
PLAN = date(1994, 10, 9)

DISTRICTS_OF = {
    "1A": ("Eisenstadt(Stadt)", "Rust(Stadt)", "Eisenstadt-Umgebung", "Mattersburg", "Neusiedl am See"),
    "1B": ("Güssing", "Jennersdorf", "Oberpullendorf", "Oberwart"),
    "2A": ("Klagenfurt(Stadt)", "Klagenfurt Land"),
    "2B": ("Villach(Stadt)", "Villach Land"),
    "2C": ("Feldkirchen", "Hermagor", "Spittal an der Drau"),
    "2D": ("Sankt Veit an der Glan", "Völkermarkt", "Wolfsberg"),
    "4A": ("Linz(Stadt)", "Linz-Land"),
    "4B": ("Braunau am Inn", "Ried im Innkreis", "Schärding"),
    "4C": ("Wels(Stadt)", "Eferding", "Grieskirchen", "Vöcklabruck", "Wels-Land"),
    "4D": ("Steyr(Stadt)", "Gmunden", "Kirchdorf an der Krems", "Steyr-Land"),
    "4E": ("Freistadt", "Perg", "Rohrbach", "Urfahr-Umgebung"),
    "5A": ("Salzburg(Stadt)",),
    "5B": ("Hallein", "Salzburg-Umgebung"),
    "5C": ("Sankt Johann im Pongau", "Tamsweg", "Zell am See"),
    "6A": ("Graz(Stadt)",),
    "6B": ("Graz-Umgebung", "Voitsberg"),
    "6C": ("Deutschlandsberg", "Leibnitz"),
    "6D": ("Südoststeiermark",),
    "6E": ("Hartberg-Fürstenfeld", "Weiz"),
    "6F": ("Bruck-Mürzzuschlag",),
    "6G": ("Leoben", "Liezen"),
    "6H": ("Murtal", "Murau"),
    "7A": ("Innsbruck-Stadt",),
    "7B": ("Innsbruck-Land", "Schwaz"),
    "7C": ("Kitzbühel", "Kufstein"),
    "7D": ("Imst", "Landeck", "Reutte"),
    "7E": ("Lienz",),
    "8A": ("Bregenz", "Dornbirn"),
    "8B": ("Bludenz", "Feldkirch"),
}
SYNTHETIC = {"6D", "6E"}

NIEDEROESTERREICH = {
    "301": "3B",
    "302": "3D",
    "303": "3C",
    "304": "3E",
    "305": "3C",
    "306": "3G",
    "307": "3G",
    "308": "3A",
    "309": "3B",
    "310": "3A",
    "311": "3B",
    "312": "3A",
    "313": "3B",
    "314": "3D",
    "315": "3C",
    "316": "3A",
    "317": "3F",
    "318": "3E",
    "319": "3D",
    "320": "3C",
    "321": "3D",
    "322": "3B",
    "323": "3E",
    "324": "3F",
    "325": "3B",
}
"""The regional constituency of each Lower Austrian political district, by the district's code in the 1995 results."""

VIENNA_DISTRICTS = {
    "9A": (3, 4, 5),
    "9B": (1, 6, 7, 8, 9),
    "9C": (2, 20),
    "9D": (10, 11, 12),
    "9E": (13, 14, 15, 23),
    "9F": (16, 17, 18, 19),
    "9G": (21, 22),
}


def fetch() -> None:
    raw.fetch(SOURCE, BROCHURE_2002, suffix=".pdf")
    wahlkreise_1923.fetch()
    raw.fetch(noe.BEV_SOURCE, noe.BEV, suffix=".zip")


def _fold(text: str) -> str:
    text = text.lower().replace("ö", "oe").replace("ü", "ue").replace("ä", "ae").replace("ß", "ss")
    return re.sub(r"[^a-z]", "", text)


def _lower_austria() -> dict[str, list[BaseGeometry]]:
    """The municipalities of each Lower Austrian regional constituency."""
    from ea.sources.at import bmi

    municipalities = noe.shapes().municipalities
    names: dict[str, str] = {}
    for line in bmi.lines(bmi.EDITIONS[0])[1]:
        if line.code and bmi.kind_of(line.code, line.name) == "gem" and line.code.removeprefix("G")[0] == "3":
            names[line.code.removeprefix("G")] = line.name
    moved = {_fold(name): code for code, name in names.items() if code not in municipalities}
    parts: dict[str, list[BaseGeometry]] = {}
    for code, (name, _, shape) in municipalities.items():
        bezirk = code if code in names else moved.pop(_fold(name), None)
        if bezirk is None:
            raise ValueError(f"municipality {name} ({code}) is not among the municipalities of 1995")
        parts.setdefault(NIEDEROESTERREICH[bezirk[:3]], []).append(shape)
    if moved:
        raise ValueError(f"municipalities of 1995 without an outline: {sorted(moved.values())}")
    return parts


def add(p: Package, area: dict[str, str]) -> None:
    """Add the sources and one `area_geometry` row for each of the 43 constituencies, by code such as `3A`."""
    brochure = raw.cached(SOURCE, BROCHURE_2002)
    sources = {
        "law": p.id("source", "brochure-2002"),
        "geo": p.id("source", "geoboundaries-districts"),
        "wien": p.id("source", "wien-bezirke-1994"),
        "bev": p.id("source", "bev-noe"),
    }
    p.add(
        "source",
        id=sources["law"],
        kind="official_body",
        publisher="Bundesministerium für Inneres",
        title="Nationalratswahl 2002: Einteilung der Landes- und Regionalwahlkreise (Nationalrats-Wahlordnung 1992, §§ 2 und 3)",
        url=BROCHURE_2002,
        attribution="Bundesministerium für Inneres",
    )
    p.artifact(brochure, sources["law"])
    p.add(
        "source",
        id=sources["geo"],
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Austria ADM2 (simplified), derived from the Federal Office of Metrology and Survey",
        url="https://www.geoboundaries.org/",
        license="CC BY-SA 2.0",
        license_url="https://creativecommons.org/licenses/by-sa/2.0/",
        attribution="geoBoundaries (William & Mary geoLab); original data from the Bundesamt für Eich- und Vermessungswesen",
    )
    districts = raw.cached(wahlkreise_1923.GEOBOUNDARIES, wahlkreise_1923.DISTRICTS)
    p.artifact(districts, sources["geo"])
    p.add(
        "source",
        id=sources["wien"],
        kind="official_body",
        publisher="Stadt Wien",
        title="Bezirksgrenzen Wien (BEZIRKSGRENZEOGD)",
        url="https://www.data.gv.at/katalog/dataset/bezirksgrenzen-standardisierte-gebietsgrenzen-wien",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Datenquelle: Stadt Wien – data.wien.gv.at",
    )
    wien = raw.cached(wahlkreise_1923.VIENNA_SOURCE, wahlkreise_1923.VIENNA)
    p.artifact(wien, sources["wien"])
    p.add(
        "source",
        id=sources["bev"],
        kind="official_body",
        publisher="Bundesamt für Eich- und Vermessungswesen",
        title="Verwaltungsgrenzen (VGD) Niederösterreich, Stand 1. Oktober 2025",
        url="https://data.bev.gv.at/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Datenquelle: Bundesamt für Eich- und Vermessungswesen",
    )
    p.artifact(raw.cached(noe.BEV_SOURCE, noe.BEV), sources["bev"])

    district_shapes = wahlkreise_1923._features(districts, "shapeName")
    wien_shapes = {
        int(f["properties"]["BEZNR"]): shapely.geometry.shape(f["geometry"])
        for f in json.loads(wien.path.read_text(encoding="utf-8"))["features"]
    }
    shapes: dict[str, tuple[BaseGeometry, str, str]] = {}
    for code, numbers in VIENNA_DISTRICTS.items():
        shapes[code] = (shapely.union_all([wien_shapes[n] for n in numbers]), "official", sources["wien"])
    for code, parts in _lower_austria().items():
        shapes[code] = (shapely.union_all(parts), "official", sources["bev"])
    for code, names in DISTRICTS_OF.items():
        quality = "synthetic" if code in SYNTHETIC else "official"
        shapes[code] = (shapely.union_all([district_shapes[n] for n in names]), quality, sources["geo"])
    used = [name for names in DISTRICTS_OF.values() for name in names]
    if len(used) != len(set(used)):
        raise ValueError("a political district is assigned to two constituencies")
    if set(shapes) != set(area):
        raise ValueError(f"constituencies without a shape: {sorted(set(area) - set(shapes))}")
    taken: BaseGeometry | None = None
    for code in sorted(shapes, key=lambda c: (c[0] not in "39", c)):
        shape = wahlkreise_1923._union(
            [shapes[code][0] if taken is None else shapely.difference(shapes[code][0], taken)]
        )
        taken = shape if taken is None else shapely.union(taken, shape)
        shapes[code] = (shape, shapes[code][1], shapes[code][2])
    for code in sorted(shapes):
        shape, quality, source_id = shapes[code]
        p.add(
            "area_geometry",
            area_id=area[code],
            valid_from=PLAN,
            valid_to=None,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality=quality,
            source_id=source_id,
        )
