"""Poland's 49 voivodeships of 1 June 1975 to 31 December 1998, with outlines built from gminas.

The two-tier division of 1975 (the act of 28 May 1975, Dz.U. 1975 nr 16 poz. 91) replaced the 17 voivodeships and
their powiats with 49 voivodeships made directly of gminas. It lasted until the three-tier division of 1 January 1999
(the act of 24 July 1998, Dz.U. 1998 nr 96 poz. 603). The presidential elections of 1990 and 1995, the Sejm elections
of 1991, 1993, and 1997, and the local elections of 1990, 1994, and 1998 were held on it.

No agency publishes the outlines of the 49 voivodeships. Each is drawn here as the union of its gminas:

1. The gminas of each voivodeship come from the Krajowe Biuro Wyborcze's list of the polling districts of the Sejm
   election of 21 September 1997 (`sejm1997obw.xls`), which gives each district's voivodeship number (1 to 49, the
   voivodeship part of the TERYT codes of the time), the gmina's TERYT code, and its name.
2. The outlines are the gminas of GUGiK's earliest archival set of the national boundary register (PRG, 2005), the
   oldest official gmina outlines published. Each gmina of 1997 is matched to the gmina of 2005 by name, with the
   renamings in `ALIASES`; where a name occurs more than once, the gmina of 2005 nearest the voivodeship's other gminas
   is taken, and no gmina of 2005 is taken twice except the merges in `MERGED`. The gminas of 2005 that the 1997 list
   lacks, made between September 1997 and 2005 out of gminas of one voivodeship, are in `ADDED`.
3. The gmina outlines are projected to EPSG:2180, united by voivodeship, simplified as one coverage to 10 m, and
   projected back to WGS 84.

The build checks the list against the KBW's list of gminas of the local elections of 19 June 1994 (`samo1994gm.xls`):
no gmina moves between voivodeships from 1994 to 1997. Gmina boundary changes before 1994 that moved land between
voivodeships, and boundary changes between 1999 and 2005, are not drawn; the notes on the scheme's areas say so.
"""

import io
import re
import zipfile
from collections import defaultdict
from datetime import date
from uuid import uuid5

import numpy as np
import shapefile
import shapely
import shapely.geometry
import xlrd
from pyproj import Transformer

from ea import package, raw
from ea.package import NAMESPACE, Package
from ea.sources.pl import kbw

SOURCE = "pl.wojewodztwo_1975"
GEO = f"{SOURCE}/geo"
SCHEME = "pl.wojewodztwo_1975"
POLAND = str(uuid5(NAMESPACE, "polity\x1fPL"))
COUNTRY = str(uuid5(NAMESPACE, "area:world.country@None\x1fPOL"))
FROM = date(1975, 6, 1)
UNTIL = date(1999, 1, 1)
PRG = "https://opendata.geoportal.gov.pl/prg/granice_archiwalne/PRG_jednostki_administracyjne_2005.zip"
PRG_PAGE = "https://www.geoportal.gov.pl/pl/dane/panstwowy-rejestr-granic-prg/"
PRG_LICENSE = "Free for any use (Dane PRG są dostępne bezpłatnie i do dowolnego wykorzystania)"
KBW_1997 = kbw.BASE + "1997/sejm/sejm1997obw.xls"
KBW_1994 = kbw.BASE + "1994/samo1994/samo1994gm.xls"
ACT_1975 = "https://api.sejm.gov.pl/eli/acts/DU/1975/91/text.pdf"
ACT_1998 = "https://api.sejm.gov.pl/eli/acts/DU/1998/603/text.html"
ISAP = "https://isap.sejm.gov.pl/isap.nsf/home.xsp"
TOLERANCE = 10.0
# The farthest, in meters, that a gmina matched by a repeated name may lie from its voivodeship's other gminas.
NEAR = 25_000.0

# The voivodeship numbers of the TERYT of 1975 to 1998, as the KBW's 1997 list numbers them.
VOIVODESHIPS = {
    1: ("warszawskie", "Warsaw Voivodeship"),
    2: ("bialskopodlaskie", "Biała Podlaska Voivodeship"),
    3: ("białostockie", "Białystok Voivodeship"),
    4: ("bielskie", "Bielsko-Biała Voivodeship"),
    5: ("bydgoskie", "Bydgoszcz Voivodeship"),
    6: ("chełmskie", "Chełm Voivodeship"),
    7: ("ciechanowskie", "Ciechanów Voivodeship"),
    8: ("częstochowskie", "Częstochowa Voivodeship"),
    9: ("elbląskie", "Elbląg Voivodeship"),
    10: ("gdańskie", "Gdańsk Voivodeship"),
    11: ("gorzowskie", "Gorzów Voivodeship"),
    12: ("jeleniogórskie", "Jelenia Góra Voivodeship"),
    13: ("kaliskie", "Kalisz Voivodeship"),
    14: ("katowickie", "Katowice Voivodeship"),
    15: ("kieleckie", "Kielce Voivodeship"),
    16: ("konińskie", "Konin Voivodeship"),
    17: ("koszalińskie", "Koszalin Voivodeship"),
    18: ("krakowskie", "Kraków Voivodeship"),
    19: ("krośnieńskie", "Krosno Voivodeship"),
    20: ("legnickie", "Legnica Voivodeship"),
    21: ("leszczyńskie", "Leszno Voivodeship"),
    22: ("lubelskie", "Lublin Voivodeship"),
    23: ("łomżyńskie", "Łomża Voivodeship"),
    24: ("łódzkie", "Łódź Voivodeship"),
    25: ("nowosądeckie", "Nowy Sącz Voivodeship"),
    26: ("olsztyńskie", "Olsztyn Voivodeship"),
    27: ("opolskie", "Opole Voivodeship"),
    28: ("ostrołęckie", "Ostrołęka Voivodeship"),
    29: ("pilskie", "Piła Voivodeship"),
    30: ("piotrkowskie", "Piotrków Voivodeship"),
    31: ("płockie", "Płock Voivodeship"),
    32: ("poznańskie", "Poznań Voivodeship"),
    33: ("przemyskie", "Przemyśl Voivodeship"),
    34: ("radomskie", "Radom Voivodeship"),
    35: ("rzeszowskie", "Rzeszów Voivodeship"),
    36: ("siedleckie", "Siedlce Voivodeship"),
    37: ("sieradzkie", "Sieradz Voivodeship"),
    38: ("skierniewickie", "Skierniewice Voivodeship"),
    39: ("słupskie", "Słupsk Voivodeship"),
    40: ("suwalskie", "Suwałki Voivodeship"),
    41: ("szczecińskie", "Szczecin Voivodeship"),
    42: ("tarnobrzeskie", "Tarnobrzeg Voivodeship"),
    43: ("tarnowskie", "Tarnów Voivodeship"),
    44: ("toruńskie", "Toruń Voivodeship"),
    45: ("wałbrzyskie", "Wałbrzych Voivodeship"),
    46: ("włocławskie", "Włocławek Voivodeship"),
    47: ("wrocławskie", "Wrocław Voivodeship"),
    48: ("zamojskie", "Zamość Voivodeship"),
    49: ("zielonogórskie", "Zielona Góra Voivodeship"),
}
# Rows of the 1997 list that are not gminas: voters abroad and on ships.
NOT_GMINAS = {"Zagranica", "Statki morskie"}
# Gminas of 1997 under the name they have in the 2005 set, by voivodeship where the name is not unique.
ALIASES: dict[tuple[int, str] | str, str] = {
    "Dobrzyniewo Kościelne": "Dobrzyniewo Duże",
    "Juchnowiec Dolny": "Juchnowiec Kościelny",
    "Siemiątkowo Koziebrodzkie": "Siemiątkowo",
    "Strzegowo-Osada": "Strzegowo",
    "Kolbudy Górne": "Kolbudy",
    "Słupia/Jędrzejowska/": "Słupia (Jędrzejowska)",
    "Słupia/Konecka/": "Słupia (Konecka)",
    "Krynica": "Krynica-Zdrój",
    "Piwniczna": "Piwniczna-Zdrój",
    "Rabka": "Rabka-Zdrój",
    "Brzeg gm.": "Skarbimierz",
    "Horyniec": "Horyniec-Zdrój",
    "Chmielnik Rzeszowski": "Chmielnik",
    "Stary Dzikowiec": "Dzikowiec",
    "Zbuczyn Poduchowny": "Zbuczyn",
    "Dobra /Szczecińska/": "Dobra (Szczecińska)",
    "Radomyśl": "Radomyśl nad Sanem",
    "Grążawy": "Bartniczka",
    (11, "Kostrzyn"): "Kostrzyn nad Odrą",
    (22, "Józefów"): "Józefów nad Wisłą",
    # Warsaw's gminas of 1997 and Wesoła, joined to it in 2002, are one gmina in 2005.
    "Wesoła": "Warszawa",
    # The 2005 set leaves out Bestwina and draws its land inside Czechowice-Dziedzice, also of bielskie.
    "Bestwina": "Czechowice-Dziedzice",
}
WARSAW = "Warszawa-"
# Gminas of 2005 that more than one gmina of 1997 maps to, all of the same voivodeship.
MERGED = {"1465011", "2402043"}
# Gminas of 2005 that the 1997 list lacks, with the voivodeship whose gminas they were made from.
ADDED = {
    "2413031": 14,  # Radzionków, separated from Bytom on 1 January 1998.
    "1211132": 25,  # Spytkowice (powiat nowotarski), made on 1 January 1998 in nowosądeckie.
    "0611021": 36,  # Stoczek Łukowski town; the 1997 list has the rural gmina only.
}


def fetch() -> None:
    raw.fetch(SOURCE, PRG, suffix=".zip")
    raw.fetch(SOURCE, KBW_1997, suffix=".xls")
    raw.fetch(SOURCE, KBW_1994, suffix=".xls")
    raw.fetch(SOURCE, ACT_1975, suffix=".pdf")
    raw.fetch(SOURCE, ACT_1998, suffix=".html")


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _decoded(text: str) -> str:
    data = text.encode("latin-1")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1250")


def gminas_1997() -> dict[str, tuple[int, str]]:
    """The gminas of the 1997 list by their TERYT code of the time: the voivodeship number and the name."""
    sheet = xlrd.open_workbook(str(raw.cached(SOURCE, KBW_1997).path)).sheet_by_index(0)
    head = [str(cell).replace("\n", " ") for cell in sheet.row_values(0)]
    if head[1:4] != ["Nr woj.", "TERYT gminy", "Gmina"]:
        raise ValueError(f"unexpected columns {head[:4]}")
    found: dict[str, tuple[int, str]] = {}
    for row in range(1, sheet.nrows):
        _, woj, code, name = sheet.row_values(row)[:4]
        entry = (int(woj), str(name).strip())
        if found.setdefault(str(code), entry) != entry:
            raise ValueError(f"gmina {code} is listed twice")
    return found


def check_1994(gminas: dict[str, tuple[int, str]]) -> None:
    """No gmina of the 1994 list sits in another voivodeship in 1997. The 1994 codes number voivodeship n as 2n-1,
    and twin rural gminas carry no `gm.`; gminas split or merged within a voivodeship are not compared."""
    sheet = xlrd.open_workbook(str(raw.cached(SOURCE, KBW_1994).path)).sheet_by_index(0)
    then: dict[str, set[int]] = defaultdict(set)
    for row in range(1, sheet.nrows):
        values = sheet.row_values(row)
        woj = (int(str(values[1])[:2]) + 1) // 2
        if values[0] != VOIVODESHIPS[woj][0]:
            raise ValueError(f"1994: {values[1]} is in {values[0]}, expected {VOIVODESHIPS[woj][0]}")
        then[_norm(str(values[2]))].add(woj)
    now: dict[str, set[int]] = defaultdict(set)
    for woj, name in gminas.values():
        now[_norm(name.removesuffix(" gm."))].add(woj)
    # A name gone from a voivodeship and new in another; a gmina abolished or renamed within one is not a move.
    moved = {
        name: (sorted(was), sorted(now[name]))
        for name, was in then.items()
        if name in now and was - now[name] and now[name] - was
    }
    if moved:
        raise ValueError(f"gminas in other voivodeships in 1994 and 1997: {moved}")


def gminas_2005() -> tuple[dict[str, str], dict[str, shapely.Geometry]]:
    """The gminas of the 2005 set: names and outlines in EPSG:2180 by TERYT code. The set codes each piece `10` +
    TERYT without its type digit + type + part number; an urban-rural gmina is its town (4) and rural area (5)."""
    project = Transformer.from_crs(4326, 2180, always_xy=True)
    names: dict[str, str] = {}
    pieces: dict[str, list[shapely.Geometry]] = defaultdict(list)
    with zipfile.ZipFile(raw.cached(SOURCE, PRG).path) as archive:
        stem = "PRG_jednostki_administracyjne_2005/Obszary"
        if "WGS_1984" not in archive.read(f"{stem}.prj").decode("latin-1"):
            raise ValueError("the 2005 set is not in WGS 84")
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{stem}.shp")),
            shx=io.BytesIO(archive.read(f"{stem}.shx")),
            dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
            encoding="latin-1",
        )
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
            if record is None or shape is None:
                continue
            long = str(record[0])
            if len(long) != 10 or not long.startswith("10") or long[8] not in "12345":
                raise ValueError(f"unexpected area code {long}")
            code = long[2:8] + ("3" if long[8] in "45" else long[8])
            if long[8] != "4":
                text = re.sub(r"\s*\[?cz\.\s*\d+\]?$", "", _decoded(str(record[1])).strip())
                text = re.sub(r"( \(gm\. miejska\)| - miasto| - obszar wiejski)$", "", text)
                names[code] = re.sub(r"^[Mm]\. (St\. )?", "", text).strip()
            outline = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
            pieces[code].append(
                shapely.transform(outline, lambda xy: np.column_stack(project.transform(xy[:, 0], xy[:, 1])))
            )
    return names, {code: shapely.union_all(parts) for code, parts in pieces.items()}


def membership(
    gminas: dict[str, tuple[int, str]], names: dict[str, str], outlines: dict[str, shapely.Geometry]
) -> dict[str, int]:
    """The voivodeship of each gmina of 2005."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for code, text in names.items():
        by_name[_norm(text)].append(code)
    listed = {(woj, _norm(name)) for woj, name in gminas.values()}
    candidates: dict[str, list[str]] = {}
    for old, (woj, name) in gminas.items():
        if name in NOT_GMINAS:
            continue
        if name.startswith(WARSAW):
            candidates[old] = ["1465011"]
            continue
        text = ALIASES.get((woj, name), ALIASES.get(name, name))
        base = _norm(text.removesuffix(" gm."))
        found = by_name.get(base, [])
        if text.endswith(" gm."):
            found = [code for code in found if code.endswith("2")]
        elif (woj, f"{base} gm.") in listed:
            # A town whose rural gmina of the same name is listed beside it.
            found = [code for code in found if code.endswith("1")] or found
        if not found:
            raise KeyError(f"{old} {name} has no gmina in the 2005 set")
        candidates[old] = found
    assigned: dict[str, int] = {}

    def take(code: str, woj: int, old: str) -> None:
        if assigned.setdefault(code, woj) != woj:
            raise ValueError(f"{names[code]} ({code}) falls in voivodeships {assigned[code]} and {woj}")
        if code not in MERGED:
            taken.add(code)

    taken: set[str] = set()
    for old, found in candidates.items():
        if len(found) == 1:
            take(found[0], gminas[old][0], old)
    seeds: dict[int, list[shapely.Geometry]] = defaultdict(list)
    for code, woj in assigned.items():
        seeds[woj].append(outlines[code])
    seed = {woj: shapely.union_all(parts) for woj, parts in seeds.items()}
    points = {code: shapely.point_on_surface(shape) for code, shape in outlines.items()}
    pending = []
    for old, found in candidates.items():
        if len(found) > 1:
            ranked = sorted((seed[gminas[old][0]].distance(points[code]), code) for code in found)
            pending.append((ranked[0][0] - ranked[1][0], old, ranked))
    # The surest choices first, so that a later one cannot take a gmina an earlier one holds.
    for _, old, ranked in sorted(pending):
        distance, code = next((d, c) for d, c in ranked if c not in taken)
        if distance > NEAR:
            raise ValueError(f"{old} {gminas[old][1]} matched {code}, {distance:.0f} m from its voivodeship")
        take(code, gminas[old][0], old)
    for code, woj in ADDED.items():
        if code in assigned:
            raise ValueError(f"{code} is in the 1997 list")
        assigned[code] = woj
    if missing := set(outlines) - set(assigned):
        raise ValueError(f"gminas of 2005 in no voivodeship: {sorted((code, names[code]) for code in missing)}")
    return assigned


def multipolygon(shape: shapely.Geometry) -> shapely.MultiPolygon:
    polygons = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) == 3]
    if not polygons:
        raise ValueError("an outline holds no polygon")
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)


def outlines() -> dict[int, shapely.MultiPolygon]:
    gminas = gminas_1997()
    if {woj for woj, _ in gminas.values()} != set(VOIVODESHIPS):
        raise ValueError("the 1997 list does not hold the 49 voivodeships")
    check_1994(gminas)
    names, shapes = gminas_2005()
    assigned = membership(gminas, names, shapes)
    groups: dict[int, list[shapely.Geometry]] = defaultdict(list)
    for code, woj in assigned.items():
        groups[woj].append(shapes[code])
    order = sorted(groups)
    united = [shapely.make_valid(shapely.set_precision(shapely.union_all(groups[woj]), 0.01)) for woj in order]
    simple = shapely.coverage_simplify(np.array(united, dtype=object), TOLERANCE)
    back = Transformer.from_crs(2180, 4326, always_xy=True)
    result: dict[int, shapely.MultiPolygon] = {}
    for woj, shape in zip(order, simple, strict=True):
        if shapely.is_empty(shape):
            raise ValueError(f"voivodeship {woj} simplified to nothing")
        projected = shapely.transform(shape, lambda xy: np.column_stack(back.transform(xy[:, 0], xy[:, 1])))
        result[woj] = multipolygon(shapely.set_precision(projected, 1e-7))
    return result


def area(woj: int) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, "area", SCHEME, f"{woj:02d}"])))


def geo() -> Package:
    p = Package(GEO, SOURCE, "Poland's 49 voivodeships of 1975 to 1998, with outlines united from their gminas")
    p.external |= {POLAND, COUNTRY}
    acts = p.id("source", "acts")
    kbw_source = p.id("source", "kbw")
    prg = p.id("source", "prg-2005")
    p.add(
        "source",
        id=acts,
        kind="official_body",
        publisher="Kancelaria Sejmu",
        title="Dz.U. 1975 nr 16 poz. 91 and Dz.U. 1998 nr 96 poz. 603",
        url=ISAP,
        license="Official legal texts are not subject to copyright (Prawo autorskie, art. 4)",
        license_url=ISAP,
        attribution="Kancelaria Sejmu, Internetowy System Aktów Prawnych",
        notes=(
            "The act of 28 May 1975 on the two-tier administrative division made the 49 voivodeships from 1 June "
            "1975; the act of 24 July 1998 replaced them with 16 from 1 January 1999."
        ),
    )
    p.artifact(raw.cached(SOURCE, ACT_1975), acts)
    p.artifact(raw.cached(SOURCE, ACT_1998), acts)
    p.add(
        "source",
        id=kbw_source,
        kind="official_body",
        publisher="Krajowe Biuro Wyborcze",
        title="Polling districts of the Sejm election of 21 September 1997; gminas of the local elections of 1994",
        url=kbw.PAGE,
        license="Public information (Ustawa o dostępie do informacji publicznej)",
        license_url=kbw.PAGE,
        attribution="Krajowe Biuro Wyborcze, Dane wyborcze",
        notes="The voivodeship of each gmina, from the 1997 polling district list, checked against the 1994 list.",
    )
    p.artifact(raw.cached(SOURCE, KBW_1997), kbw_source)
    p.artifact(raw.cached(SOURCE, KBW_1994), kbw_source)
    p.add(
        "source",
        id=prg,
        kind="official_body",
        publisher="Główny Urząd Geodezji i Kartografii",
        title="Państwowy Rejestr Granic: archiwalne granice jednostek administracyjnych, 2005",
        url=PRG_PAGE,
        license=PRG_LICENSE,
        license_url=PRG_PAGE,
        attribution="Główny Urząd Geodezji i Kartografii, Państwowy Rejestr Granic",
        notes=(
            "Gmina outlines of the 2005 archival set, united by the voivodeship of 1975 to 1998 each gmina belonged "
            f"to and simplified as one coverage to {TOLERANCE:.0f} m in EPSG:2180."
        ),
    )
    p.artifact(raw.cached(SOURCE, PRG), prg)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=POLAND,
        kind="administrative",
        rank=1,
        noun="voivodeship of 1975–1998",
        kinds=None,
        code_system="teryt",
    )
    p.add(
        "boundary_set",
        id=p.id("boundary_set", SCHEME, "1997"),
        scheme_code=SCHEME,
        vintage="1997",
        valid_from=FROM,
        valid_to=UNTIL,
        as_of=date(1997, 9, 21),
    )
    shapes = outlines()
    for woj, (native, english) in VOIVODESHIPS.items():
        id = area(woj)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=f"{woj:02d}", valid_from=FROM, valid_to=UNTIL)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=FROM, valid_to=UNTIL)
        p.add(
            "name",
            id=p.id("name", id, "pl"),
            entity_id=id,
            text=f"województwo {native}",
            lang="pl",
            kind="official",
            valid_from=FROM,
            valid_to=UNTIL,
            is_preferred=True,
            source_id=acts,
        )
        p.add(
            "name",
            id=p.id("name", id, "en"),
            entity_id=id,
            text=english,
            lang="en",
            kind="english",
            valid_from=FROM,
            valid_to=UNTIL,
            is_preferred=False,
            source_id=acts,
        )
        shape = shapes[woj]
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=FROM,
            valid_to=UNTIL,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=prg,
        )
        p.add(
            "note",
            id=p.id("note", id),
            entity_id=id,
            kind="caveat",
            body_md=(
                "No outline of the voivodeships of 1975 to 1998 is published. This one is the union of the gminas "
                "the electoral commission listed in the voivodeship in September 1997, drawn on the gmina outlines "
                "of the boundary register's earliest set, of 2005. Gminas that moved between voivodeships before "
                "1994, and gmina boundary changes from 1999 to 2005, are not drawn."
            ),
            lang="en",
        )
    return p
