"""Algeria's wilayas (48 from 1984, 58 from 2019, 69 from 2026) and its 1,541 communes.

The commune shapes come from the OCHA COD-AB dataset. Which commune belongs to which wilaya comes from the commune
lists that Loi 19-12 (JO 78/2019) and Loi 26-06 (JO 25/2026) print for each wilaya they touch. A wilaya's shape is
the union of its communes.
"""

import collections
import difflib
import functools
import io
import re
import subprocess
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import shapefile
import shapely
from shapely.geometry import shape

from ea import package, raw
from ea.package import Package

SOURCE = "dz.boundaries"
COD = (
    "https://data.humdata.org/dataset/3d17274d-0812-4b21-b87a-4854af4eb244/resource/"
    "3350e893-bc67-49a3-98e9-9ccd6ab8b85f/download/dza_admin_boundaries.shp.zip"
)
L84_09 = "https://www.joradp.dz/FTP/JO-FRANCAIS/1984/F1984006.pdf"
L19_12 = "https://www.joradp.dz/FTP/JO-FRANCAIS/2019/F2019078.pdf"
L26_06 = "https://www.joradp.dz/FTP/JO-FRANCAIS/2026/F2026025.pdf"
ALGERIA = str(uuid5(package.NAMESPACE, "polity\x1fDZ"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fDZA"))
JORADP = "Secrétariat général du Gouvernement, Journal officiel de la République algérienne (JORADP)"
# the pages of each issue that print the commune lists; each page has two columns
PAGES = {L19_12: range(12, 16), L26_06: range(4, 10)}
FIRST = date(1984, 2, 4)
SECOND = date(2019, 12, 18)
THIRD = date(2026, 4, 5)
VINTAGES = ((1984, FIRST), (2019, SECOND), (2026, THIRD))
COMMUNES = 1541
COUNTS = {1984: 48, 2019: 58, 2026: 69}
# The wilayas of Loi 84-09, by number, with the dataset's spelling of each.
OLD = {
    1: ("Adrar", "Adrar"),
    2: ("Chlef", "Chlef"),
    3: ("Laghouat", "Laghouat"),
    4: ("Oum El Bouaghi", "Oum El Bouaghi"),
    5: ("Batna", "Batna"),
    6: ("Béjaïa", "Bejaia"),
    7: ("Biskra", "Biskra"),
    8: ("Béchar", "Bechar"),
    9: ("Blida", "Blida"),
    10: ("Bouira", "Bouira"),
    11: ("Tamanrasset", "Tamanrasset"),
    12: ("Tébessa", "Tebessa"),
    13: ("Tlemcen", "Tlemcen"),
    14: ("Tiaret", "Tiaret"),
    15: ("Tizi Ouzou", "Tizi Ouzou"),
    16: ("Alger", "Alger"),
    17: ("Djelfa", "Djelfa"),
    18: ("Jijel", "Jijel"),
    19: ("Sétif", "Setif"),
    20: ("Saïda", "Saida"),
    21: ("Skikda", "Skikda"),
    22: ("Sidi Bel Abbès", "Sidi Bel Abbes"),
    23: ("Annaba", "Annaba"),
    24: ("Guelma", "Guelma"),
    25: ("Constantine", "Constantine"),
    26: ("Médéa", "Medea"),
    27: ("Mostaganem", "Mostaganem"),
    28: ("M'Sila", "M'Sila"),
    29: ("Mascara", "Mascara"),
    30: ("Ouargla", "Ouargla"),
    31: ("Oran", "Oran"),
    32: ("El Bayadh", "El Bayadh"),
    33: ("Illizi", "Illizi"),
    34: ("Bordj Bou Arréridj", "Bordj Bou Arrer"),
    35: ("Boumerdès", "Boumerdes"),
    36: ("El Tarf", "El-Tarf"),
    37: ("Tindouf", "Tindouf"),
    38: ("Tissemsilt", "Tissemsilt"),
    39: ("El Oued", "El Oued"),
    40: ("Khenchela", "Khenchela"),
    41: ("Souk Ahras", "Souk-Ahras"),
    42: ("Tipaza", "Tipaza"),
    43: ("Mila", "Mila"),
    44: ("Aïn Defla", "Ain-Defla"),
    45: ("Naâma", "Naama"),
    46: ("Aïn Témouchent", "Ain-Temouchent"),
    47: ("Ghardaïa", "Ghardaia"),
    48: ("Relizane", "Relizane"),
}
# A law prints each wilaya's communes under its article: Loi 84-09 numbered the 48 wilayas from article 4 on, so
# article N is wilaya N-4, and each new wilaya is an article "52 bis" and a number, which is wilaya 49 plus that number.
# Each new wilaya is carved out of one earlier wilaya, or two for the 2026 Djelfa split, and the earlier wilaya's own
# article is reprinted with the communes it keeps. The communes of one earlier wilaya must equal the communes its
# articles print, so a mistake in this table fails the check in `assign`.
ROOTS = {
    2019: {0: 1, 1: 1, 2: 7, 3: 8, 4: 11, 5: 11, 6: 30, 7: 33, 8: 39, 9: 47},
    2026: {10: 3, 11: 5, 12: 7, 13: 12, 14: 13, 15: 14, 16: 17, 17: 17, 18: 26, 19: 28, 20: 32},
}
HEADER = re.compile(
    r"Art\.?\s*(\d+)\.?\s*(?:(bis)\.?\s*(\d+)?)?\s*\.?\s*—\s*Les\s+(?:(?!Art)[^()])*?\((\d+)\s*\)\s*communes\s+suivantes",
    re.IGNORECASE | re.DOTALL,
)
ITEM = re.compile(r"^(\d+)\.?\s+(\S.*?)\s*[;.]?$")
DATE_LINE = re.compile(
    r"^\d+\s+(Rabie|Chaoual|Joumada|Rajab|Dhou|Moharram|Safar|Ramadhan|Chaâbane|avril|décembre)\b", re.IGNORECASE
)
# The dataset names El Meniaa by its other name, El Golea.
ALIASES = {"El Meniaâ": "Golea"}
Key = tuple[int, int | None]


class Commune(NamedTuple):
    name: str
    wilaya: str
    shape: shapely.Geometry


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def fetch() -> None:
    raw.fetch(SOURCE, COD, suffix=".zip")
    for url in (L84_09, L19_12, L26_06):
        raw.fetch(SOURCE, url, suffix=".pdf")


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("’", "'").replace("ʼ", "'"))
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in folded if not unicodedata.combining(c)).lower())


def _columns(path: Path, pages: range) -> list[str]:
    lines: list[str] = []
    for page in pages:
        for left in (0, 297):
            lines.extend(
                subprocess.run(
                    ["pdftotext", "-f", str(page), "-l", str(page), "-x", str(left), "-y", "0", "-W", "298"]
                    + ["-H", "842", str(path), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.splitlines()
            )
    return lines


@functools.cache
def lists(url: str) -> dict[Key, list[str]]:
    """The commune lists a law prints, by article and `bis` number, in the law's own spelling."""
    lines = _columns(raw.cached(SOURCE, url).path, PAGES[url])
    # a header can wrap over lines, so find the headers in the joined text and then walk the lines
    marks = [(m.start(), m) for m in HEADER.finditer("\n".join(lines))]
    found: dict[Key, list[str]] = {}
    printed: dict[Key, int] = {}
    current: Key | None = None
    position = 0
    for line in lines:
        for start, mark in marks:
            if position <= start < position + len(line) + 1:
                current = (int(mark.group(1)), int(mark.group(3) or 0) if mark.group(2) else None)
                found[current] = []
                printed[current] = int(mark.group(4))
        position += len(line) + 1
        item = ITEM.match(line.strip())
        if (
            item
            and current is not None
            and not DATE_LINE.match(line.strip())
            and len(line.strip()) < 60
            and int(item.group(1)) == len(found[current]) + 1
        ):
            found[current].append(item.group(2).replace("»", "").strip(" ;."))
    for article, names in found.items():
        if len(names) != printed[article]:
            raise ValueError(f"article {article} of {url} prints {printed[article]} communes; read {len(names)}")
    return found


def number(article: Key) -> int:
    """The wilaya number an article prints the communes of."""
    n, bis = article
    return n - 4 if bis is None else 49 + bis


def _dataset(name: str) -> shapefile.Reader:
    archive = zipfile.ZipFile(raw.cached(SOURCE, COD).path)
    return shapefile.Reader(
        shp=io.BytesIO(archive.read(f"{name}.shp")),
        shx=io.BytesIO(archive.read(f"{name}.shx")),
        dbf=io.BytesIO(archive.read(f"{name}.dbf")),
        encoding="utf-8",
    )


@functools.cache
def communes() -> dict[str, Commune]:
    """Each commune of the dataset by its p-code, with its dataset wilaya and its shape."""
    reader = _dataset("dza_admin2")
    found: dict[str, Commune] = {}
    for record, outline in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or outline is None:
            raise ValueError("the commune dataset holds a record without a shape")
        row = record.as_dict()
        geometry = shapely.make_valid(shape(outline.__geo_interface__))
        found[str(row["adm2_pcode"])] = Commune(str(row["adm2_name"]), str(row["adm1_name"]), geometry)
    if len(found) != COMMUNES:
        raise ValueError(f"the dataset holds {len(found)} communes, and the law counts {COMMUNES}")
    return found


@functools.cache
def arabic() -> dict[str, str]:
    rows = [record.as_dict() for record in _dataset("dza_admin1").records() if record is not None]
    return {str(r["adm1_name"]): str(r["adm1_name1"]) for r in rows if r["adm1_name1"]}


def assign(
    law: dict[Key, list[str]], roots: dict[int, int], before: dict[str, int]
) -> tuple[dict[str, int], dict[str, str]]:
    """Give each commune a law prints its wilaya, checking that the lists cover exactly the wilayas they split.

    Returns the new wilaya of each commune listed and the law's spelling of its name. A name the dataset spells
    differently is matched to the closest unclaimed commune among the communes of the wilaya the article splits.
    """
    families: dict[int, list[Key]] = collections.defaultdict(list)
    for article in law:
        root = number(article) if article[1] is None else roots[article[1]]
        families[root].append(article)
    where: dict[str, int] = {}
    spelled: dict[str, str] = {}
    book = communes()
    for root, articles in sorted(families.items()):
        pool = [code for code, wilaya in before.items() if wilaya == root]
        names = [(number(article), name) for article in articles for name in law[article]]
        if len(pool) != len(names):
            raise ValueError(f"wilaya {root} holds {len(pool)} communes and its articles print {len(names)}")
        left = set(pool)
        rest: list[tuple[int, str]] = []
        for wilaya, name in names:
            target = plain(ALIASES.get(name, name))
            same = [code for code in sorted(left) if plain(book[code].name) == target]
            if same:
                left.discard(same[0])
                where[same[0]], spelled[same[0]] = wilaya, name
            else:
                rest.append((wilaya, name))
        pairs = sorted(
            (
                (difflib.SequenceMatcher(None, plain(book[code].name), plain(name)).ratio(), i, code)
                for i, (_, name) in enumerate(rest)
                for code in left
            ),
            reverse=True,
        )
        taken: set[int] = set()
        for ratio, i, code in pairs:
            if i in taken or code not in left or ratio < 0.6:
                continue
            taken.add(i)
            left.discard(code)
            where[code], spelled[code] = rest[i][0], rest[i][1]
        # the one name and the one commune that remain pair up, because their counts matched and nothing else is left
        unpaired = [i for i in range(len(rest)) if i not in taken]
        if len(unpaired) != len(left) or len(left) > 1:
            unmatched = sorted(book[c].name for c in left)
            raise ValueError(f"wilaya {root}: {[rest[i][1] for i in unpaired]} do not pair with {unmatched}")
        for i in unpaired:
            (code,) = left
            print(f"forced pair in wilaya {root}: {rest[i][1]!r} is the dataset's {book[code].name!r}")
            where[code], spelled[code] = rest[i][0], rest[i][1]
    return where, spelled


@functools.cache
def membership() -> tuple[dict[int, dict[str, int]], dict[str, str], dict[int, dict[Key, list[str]]]]:
    """The wilaya of every commune in each vintage, the law spelling of each name, and the lists read."""
    old = {wilaya[1]: number for number, wilaya in OLD.items()}
    book = communes()
    first = {code: old[row.wilaya] for code, row in book.items()}
    second_law, third_law = lists(L19_12), lists(L26_06)
    moved, spelled = assign(second_law, ROOTS[2019], first)
    second = {code: moved.get(code, wilaya) for code, wilaya in first.items()}
    moved_again, again = assign(third_law, ROOTS[2026], second)
    third = {code: moved_again.get(code, wilaya) for code, wilaya in second.items()}
    spelled |= again
    found = {1984: first, 2019: second, 2026: third}
    for vintage, wilayas in found.items():
        if len(set(wilayas.values())) != COUNTS[vintage]:
            raise ValueError(f"{vintage}: {len(set(wilayas.values()))} wilayas, and the law counts {COUNTS[vintage]}")
    for law, vintage in ((second_law, 2019), (third_law, 2026)):
        for article, names in law.items():
            members = [code for code, wilaya in found[vintage].items() if wilaya == number(article)]
            if len(members) != len(names):
                raise ValueError(
                    f"{vintage}: wilaya {number(article)} holds {len(members)}, article prints {len(names)}"
                )
    return found, spelled, {2019: second_law, 2026: third_law}


def _multi(geometry: shapely.Geometry) -> shapely.Geometry:
    parts = [part for part in shapely.get_parts(shapely.make_valid(geometry)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _outline(p: Package, id: str, geometry: shapely.Geometry, source: str, start: date, end: date | None) -> None:
    geometry = _multi(geometry)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=start,
        valid_to=end,
        geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=source,
    )


def _spans[T](values: dict[int, T]) -> list[tuple[T, date, date | None]]:
    """Merge consecutive vintages that hold the same value into spans of dates."""
    out: list[tuple[T, date, date | None]] = []
    ends = {1984: SECOND, 2019: THIRD, 2026: None}
    for vintage, start in VINTAGES:
        if vintage not in values:
            continue
        if out and out[-1][0] == values[vintage] and out[-1][2] == start:
            out[-1] = (out[-1][0], out[-1][1], ends[vintage])
        else:
            out.append((values[vintage], start, ends[vintage]))
    return out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Algeria's wilayas in their 48, 58, and 69 versions and its 1,541 communes, from the commune lists of "
        "Loi 19-12 and Loi 26-06 and the OCHA COD-AB commune shapes",
    )
    p.external |= {ALGERIA, TERRITORY}
    found, spelled, laws = membership()
    book = communes()
    cod, l84, l19, l26 = (key("source", name) for name in ("cod-ab", "l84-09", "l19-12", "l26-06"))
    p.add(
        "source",
        id=cod,
        kind="official_body",
        publisher="OCHA Field Information Services Section, from UNHCR and GAUL",
        title="Algeria - Subnational Administrative Boundaries (COD-AB), version 01",
        url="https://data.humdata.org/dataset/cod-ab-dza",
        license="CC BY-IGO 3.0",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="OCHA Field Information Services Section (FISS); UNHCR (from GAUL)",
        notes="Valid for use from 2021-01-20; the shapes were last edited on 2020-04-28.",
    )
    for id, url, title in (
        (l84, L84_09, "Loi n° 84-09 du 4 février 1984 relative à l'organisation territoriale du pays (JO n° 6)"),
        (l19, L19_12, "Loi n° 19-12 du 11 décembre 2019 modifiant et complétant la loi n° 84-09 (JO n° 78)"),
        (l26, L26_06, "Loi n° 26-06 du 4 avril 2026 modifiant et complétant la loi n° 84-09 (JO n° 25)"),
    ):
        p.add("source", id=id, kind="official_body", publisher=JORADP, title=title, url=url)
        p.artifact(raw.cached(SOURCE, url), id)
    p.artifact(raw.cached(SOURCE, COD), cod)
    for code, rank, noun, system in (
        ("dz.wilaya", 1, "wilaya", "wilaya_number"),
        ("dz.commune", 2, "commune", "cod_ab"),
    ):
        p.add("scheme", code=code, polity_id=ALGERIA, kind="administrative", rank=rank, noun=noun, code_system=system)
    for vintage, start in VINTAGES:
        end = {1984: SECOND, 2019: THIRD, 2026: None}[vintage]
        p.add(
            "boundary_set",
            id=key("boundary_set", "dz.wilaya", vintage),
            scheme_code="dz.wilaya",
            vintage=str(vintage),
            valid_from=start,
            valid_to=end,
            as_of=start,
        )
    p.add(
        "boundary_set",
        id=key("boundary_set", "dz.commune", 2021),
        scheme_code="dz.commune",
        vintage="2021",
        valid_from=FIRST,
        as_of=date(2021, 1, 20),
    )
    # the wilayas: each one's communes in each vintage give its shape, its code spans, and its parent spans
    members: dict[int, dict[int, frozenset[str]]] = collections.defaultdict(dict)
    for vintage, wilayas in found.items():
        for code, wilaya in wilayas.items():
            members[wilaya].setdefault(vintage, frozenset())
            members[wilaya][vintage] |= {code}
    names: dict[int, str] = {number: name for number, (name, _) in OLD.items()}
    for vintage, law in laws.items():
        for article, list_ in law.items():
            if article[1] is not None:
                names[number(article)] = list_[0]
    wilaya_of = {wilaya: area("dz.wilaya", f"{wilaya:02d}") for wilaya in members}
    for wilaya, by in sorted(members.items()):
        id = wilaya_of[wilaya]
        p.add("area", id=id, scheme_code="dz.wilaya")
        p.name(id, names[wilaya], "fr", "official")
        if wilaya in OLD and (native := arabic().get(OLD[wilaya][1])):
            p.name(id, native, "ar", "native")
        for _, start, end in _spans({v: True for v in by}):
            p.add("area_code", area_id=id, code=f"{wilaya:02d}", valid_from=start, valid_to=end)
            p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=start, valid_to=end)
        for codes, start, end in _spans(by):
            outline = shapely.union_all([book[c].shape for c in sorted(codes)])
            _outline(p, id, outline, cod, start, end)
    # the communes: the same commune in every vintage, moving between wilayas
    for code, row in sorted(book.items()):
        id = area("dz.commune", code)
        p.add("area", id=id, scheme_code="dz.commune")
        p.add("area_code", area_id=id, code=code, valid_from=FIRST)
        law = spelled.get(code)
        p.name(id, law or str(row.name), "fr", "official" if law else "transliteration")
        if law and law != row.name:
            p.name(id, str(row.name), "fr", "transliteration", preferred=False)
        for wilaya, start, end in _spans({v: wilaya_of[found[v][code]] for v in found}):
            p.add("area_parent", area_id=id, parent_id=wilaya, valid_from=start, valid_to=end)
        _outline(p, id, row.shape, cod, FIRST, None)
    # lineage: each new wilaya was created from the wilaya whose communes it took, and that wilaya's shape changed
    for vintage, law in laws.items():
        source = l19 if vintage == 2019 else l26
        start = SECOND if vintage == 2019 else THIRD
        before = found[1984 if vintage == 2019 else 2019]
        for bis, root in sorted(ROOTS[vintage].items()):
            new = 49 + bis
            event = p.id("event", "created", new)
            p.add("area_event", id=event, kind="created", date=start, source_id=source)
            p.add("area_event_member", event_id=event, area_id=wilaya_of[new], role="to")
            sources = {before[c] for c in found[vintage] if found[vintage][c] == new}
            for origin in sorted(sources):
                p.add("area_event_member", event_id=event, area_id=wilaya_of[origin], role="from")
        for root in sorted(set(ROOTS[vintage].values())):
            event = p.id("event", "boundary_changed", vintage, root)
            p.add("area_event", id=event, kind="boundary_changed", date=start, source_id=source)
            p.add("area_event_member", event_id=event, area_id=wilaya_of[root], role="to")
    return p


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())
