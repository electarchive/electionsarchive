"""New Caledonia's provincial elections (the three provincial assemblies and the Congress), from the
Haut-commissariat de la République en Nouvelle-Calédonie."""

import itertools
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote
from uuid import uuid5

import pymupdf
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "fr.nc_provinciales"
SITE = "https://www.nouvelle-caledonie.gouv.fr"
FILES = f"{SITE}/contenu/telechargement"
PROVINCES = ("S", "N", "I")


def _file(path: str) -> str:
    folder, name = path.rsplit("/", 1)
    return f"{FILES}/{folder}/{quote(name)}"


# One by-polling-station table per province and year, and the printed results that go with it.
BUREAUX = {
    (2026, "S"): _file("13530/112411/file/PROVINCIALES_2026_PSUD_Résultats_BV.pdf"),
    (2026, "N"): _file("13531/112416/file/PROVINCIALES_2026_PNORD_Résultats_BV.pdf"),
    (2026, "I"): _file("13532/112421/file/PROVINCIALES_2026_PIL_Résultats_BV.pdf"),
    (2019, "S"): _file("5543/42792/file/20190513 PS par bv.pdf"),
    (2019, "N"): _file("5542/42788/file/20190513 PN par bv.pdf"),
    (2019, "I"): _file("5541/42784/file/20190513 PIL par bv.pdf"),
}
PROCLAMATION_2019 = _file("5547/42814/file/Proclamation des résultats 2019.pdf")
COMPOSITION = {
    (2026, "S"): (
        _file("13487/112082/file/PROVINCIALES_2026_PSUD_Résultats.pdf"),
        _file("13488/112087/file/PROVINCIALES_2026_PSUD_Composition.pdf"),
    ),
    (2026, "N"): (
        _file("13496/112182/file/PROVINCIALES_2026_PNORD_Résultats.pdf"),
        _file("13497/112187/file/PROVINCIALES_2026_PNORD_Composition.pdf"),
    ),
    (2026, "I"): (
        _file("13490/112108/file/PROVINCIALES_2026_PIL_Résultats.pdf"),
        _file("13491/112113/file/PROVINCIALES_2026_PIL_Composition.pdf"),
    ),
}
PAGES = {
    2026: f"{SITE}/Actions-de-l-Etat/Elections/Elections-2026/Elections-provinciales-2026/Resultats",
    2019: f"{SITE}/Actions-de-l-Etat/Elections/Elections-2019/Provinciales-2019/RESULTATS",
}


OSM_RELATIONS = {"S": 3222337, "N": 3222336, "I": 379252}
NOMINATIM = (
    "https://nominatim.openstreetmap.org/lookup?osm_ids=R{id}&format=geojson&polygon_geojson=1&polygon_threshold=0.0005"
)


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for url in [*BUREAUX.values(), PROCLAMATION_2019, *(u for pair in COMPOSITION.values() for u in pair)]:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for relation in OSM_RELATIONS.values():
        raw.fetch(SOURCE, NOMINATIM.format(id=relation), suffix=".json")


@dataclass
class Row:
    commune: str
    number: int
    name: str
    registered: int
    voters: int
    abstentions: int
    invalid: int
    blank: int
    valid: int
    votes: list[int]


def _int(text: str) -> int:
    return int(re.sub(r"[\s\u202f\xa0]", "", text))


def _pages(path: str) -> list[list[tuple[float, float, float, str]]]:
    """The words of each page with their boxes turned upright; the 2019 tables for the South and North are printed
    sideways."""
    pages = []
    for page in pymupdf.open(path):
        out = []
        for x0, y0, x1, y1, text, *_ in page.get_text("words"):
            shape = pymupdf.Rect(x0, y0, x1, y1) * page.rotation_matrix
            out.append((shape.x0, shape.y0, shape.x1, text))
        pages.append(out)
    return pages


def _pair(before: list[str], percent: float, abstentions: int) -> tuple[int, int, list[str]]:
    """Registered voters and voters from the tokens before the first percentage. A name can run into the registered
    figure, and a figure of thousands is printed with a space, so each reading is tried against the printed turnout
    percentage and abstentions."""
    for take in (1, 2):
        if len(before) < take or not all(re.fullmatch(r"\d+", t) for t in before[-take:]):
            continue
        if take == 2 and len(before[-1]) != 3:
            continue
        voters = _int("".join(before[-take:]))
        rest = before[:-take]
        glued = re.fullmatch(r"(\D.*?)(\d+)", rest[-1]) if rest else None
        options: list[tuple[int, list[str]]] = []
        for size in (1, 2):
            if len(rest) >= size and all(re.fullmatch(r"\d+", t) for t in rest[-size:]):
                if size == 2 and len(rest[-1]) != 3:
                    continue
                options.append((_int("".join(rest[-size:])), rest[:-size]))
        if glued:
            options.append((int(glued.group(2)), [*rest[:-1], glued.group(1)]))
        for registered, name in options:
            if registered and registered - voters == abstentions and round(100 * voters / registered, 2) == percent:
                return registered, voters, name
    raise ValueError(f"cannot read the registered voters and voters from {before} at {percent}% and {abstentions}")


def bureaux(path: str) -> list[Row]:
    """Every polling station of a by-station table. The table can run over several pages, and only the first has the
    column headings."""
    pages = _pages(path)
    number_at = next(x0 for x0, _, _, text in pages[0] if text == "n°")
    return [row for words in pages for row in _page(words, number_at)]


def _page(words: list[tuple[float, float, float, str]], number_at: float) -> list[Row]:
    heading = [y0 for x0, y0, _, text in words if text == "Commune"]
    top = min(heading) + 1.5 if heading else 0.0
    lines: dict[int, list[tuple[float, str]]] = defaultdict(list)
    for x0, y0, _, text in words:
        if y0 > top:
            for n, piece in enumerate(re.split(r"[\u202f\xa0]", text)):
                lines[round(y0 * 4)].append((x0 + n * 0.01, piece))
    groups: list[list[tuple[float, str]]] = []
    for key in sorted(lines):
        line = sorted(lines[key])
        starts = any(
            re.fullmatch(r"\d+", t)
            and number_at - 3 <= x <= number_at + 6
            and any(x2 < number_at - 3 for x2, _ in line)
            for x, t in line
        )
        if starts:
            groups.append(line)
        elif groups and not any(t.endswith("%") for _, t in groups[-1]):
            # a name that wraps carries its figures on the second line
            groups[-1].extend(line)
    rows = []
    for group in groups:
        commune = " ".join(t for x, t in group if x < number_at - 3)
        number = next(int(t) for x, t in group if number_at - 3 <= x <= number_at + 6 and t.isdigit())
        tokens = [t for x, t in group if x > number_at + 6]
        percents = [i for i, t in enumerate(tokens) if t.endswith("%")]
        # The figure before each percentage after the first is abstentions, invalid, blank, valid, then each list.
        figures = [_int("".join(tokens[a + 1 : b])) for a, b in itertools.pairwise(percents)]
        percent = float(tokens[percents[0]].rstrip("%").replace(",", "."))
        registered, voters, name = _pair(tokens[: percents[0]], percent, figures[0])
        abstentions, invalid, blank, valid, *votes = figures
        rows.append(Row(commune, number, " ".join(name), registered, voters, abstentions, invalid, blank, valid, votes))
    return rows


# France's polity row was written by fr.interieur, so its id is not the derived one.
COUNTRY = "01a13b8a-b1bc-7045-aa86-d470ebdf31b2"
NOUVELLE_CALEDONIE = "01a13b8a-b1d1-71c3-af58-ab744b7f7ecd"  # the department-level area of fr.interieur
# The commune areas that fr.interieur holds, by INSEE code.
COMMUNES = {
    "98801": "01a13b8a-b236-7126-be61-86cf0a280dec",
    "98802": "01a13b8a-b236-7126-be61-86d0ddba4f3e",
    "98803": "01a13b8a-b236-7126-be61-86d16b850ae1",
    "98804": "01a13b8a-b236-7126-be61-86d2ff559b38",
    "98805": "01a13b8a-b236-7126-be61-86d3bd484faf",
    "98806": "01a13b8a-b236-7126-be61-86d42c60f9fd",
    "98807": "01a13b8a-b236-7126-be61-86d51176befc",
    "98808": "01a13b8a-b236-7126-be61-86d6254c7343",
    "98809": "01a13b8a-b236-7126-be61-86d74955fc6f",
    "98810": "01a13b8a-b236-7126-be61-86d8240deb3a",
    "98811": "01a13b8a-b236-7126-be61-86d947a318a5",
    "98812": "01a13b8a-b236-7126-be61-86dae8e9a8e7",
    "98813": "01a13b8a-b236-7126-be61-86dbaa4695ba",
    "98814": "01a13b8a-b236-7126-be61-86dcea5054a7",
    "98815": "01a13b8a-b236-7126-be61-86dd07be0592",
    "98816": "01a13b8a-b236-7126-be61-86de46dfd5f9",
    "98817": "01a13b8a-b236-7126-be61-86df4aacb0e5",
    "98818": "01a13b8a-b236-7126-be61-86e0f0f4a436",
    "98819": "01a13b8a-b236-7126-be61-86e13522ca24",
    "98820": "01a13b8a-b236-7126-be61-86e2151a4f1f",
    "98821": "01a13b8a-b236-7126-be61-86e3bd58a07b",
    "98822": "01a13b8a-b236-7126-be61-86e4914608cb",
    "98823": "01a13b8a-b236-7126-be61-86e52d885a3d",
    "98824": "01a13b8a-b236-7126-be61-86e6cd42dee1",
    "98825": "01a13b8a-b236-7126-be61-86e717d54167",
    "98826": "01a13b8a-b236-7126-be61-86e8c038dfc3",
    "98827": "01a13b8a-b236-7126-be61-86e99570b190",
    "98828": "01a13b8a-b236-7126-be61-86ea47911777",
    "98829": "01a13b8a-b236-7126-be61-86eb9ade583a",
    "98830": "01a13b8a-b236-7126-be61-86ec2b03faac",
    "98831": "01a13b8a-b236-7126-be61-86ed053c31c2",
    "98832": "01a13b8a-b236-7126-be61-86eee02ec0a2",
    "98833": "01a13b8a-b236-7126-be61-86efdfbd67bb",
}
# The communes as the by-station tables spell them. Some names are cut short where they run into the next column.
INSEE = {
    "Bouloupa": "98802",
    "Boulouparis": "98802",
    "Bourail": "98803",
    "Bélep": "98801",
    "Canala": "98804",
    "Dumbéa": "98805",
    "Farino": "98806",
    "Hienghène": "98807",
    "Houailou": "98808",
    "Ile des Pi": "98809",
    "Ile des Pins": "98809",
    "Kaala Gomen": "98810",
    "Koné": "98811",
    "Kouaoua": "98833",
    "Koumac": "98812",
    "La Foa": "98813",
    "Lifou": "98814",
    "Maré": "98815",
    "Moindou": "98816",
    "Mont-Dor": "98817",
    "Mont-Dore": "98817",
    "Nouméa": "98818",
    "Ouvéa": "98820",
    "Ouégoa": "98819",
    "Païta": "98821",
    "Poindimié": "98822",
    "Ponérihouen": "98823",
    "Pouembout": "98825",
    "Poum": "98826",
    "Pouébo": "98824",
    "Poya Nord": "98827",
    "Poya Sud": "98827",
    "Sarraméa": "98828",
    "Thio": "98829",
    "Touho": "98830",
    "Voh": "98831",
    "Yaté": "98832",
}
DAYS = {2019: date(2019, 5, 12), 2026: date(2026, 6, 28)}
# Province letter: (area code, seats in the assembly, seats of those in the Congress, English and French names)
PROVINCE = {
    "S": ("SUD", 40, 32, "South Province", "Province Sud"),
    "N": ("NORD", 22, 15, "North Province", "Province Nord"),
    "I": ("ILES", 14, 7, "Loyalty Islands Province", "Province des îles Loyauté"),
}
# The lists of 2026 in the order of the by-station columns, with the seats each won, from the composition tables.
LISTS_2026 = {
    "S": [
        ("UN AUTRE MONDE EST POSSIBLE !", 5),
        ("KANAKY POUR TOUS", 7),
        ("FRANCE CALEDONIE, UNE PATRIE", 0),
        ("UNE PROVINCE POUR TOUS, UN PAYS SOLIDAIRE UN AVENIR PARTAGE", 0),
        ("SOUVERAINETE KANAKY NOUVELLE-CALEDONIE", 0),
        ("FAIRE PAYS", 0),
        ("POUR UNE CALEDONIE FRANCAISE", 0),
        ("NOUS, REUNIS !", 0),
        ("LES LOYALISTES ET LE RASSEMBLEMENT-FORTS ET UNIS", 28),
        ("UNIS POUR LE PAYS", 0),
        ("UN ESPOIR POUR DEMAIN", 0),
    ],
    "N": [
        ("AGISSONS ENSEMBLE POUR LE NORD", 3),
        ("ALTERNATIVE NORD POUR UN PAYS SOUVERAIN", 0),
        ("UC FLNKS", 10),
        ("FAIRE PAYS", 0),
        ("UNION NATIONALE POUR L'INDEPENDANCE (UNI)", 9),
    ],
    "I": [
        ("PALIKA ÎLES", 2),
        ("UN NOUVEAU SOUFFLE", 0),
        ("BASELAIA", 0),
        ("NATION AUTOCHTONE", 6),
        ("UC FLNKS ÎLES", 6),
        ("S'ENGAGER ET OEUVRER POUR LA PROSPERITE DE NOTRE PROVINCE ET LA SOUVERAINETE DE NOTRE PAYS", 0),
        ("UNION POUR CONSTRUIRE DANS LE CONSENSUS", 0),
    ],
}
# Where the definitive composition tables and the by-station tables differ, the by-station figures are the ones
# stored. The North's table is one ballot over: 26,504 voters and 207 blank ballots, against 26,503 and 206.
NORTH_2026_NOTE = (
    "The by-station table for the North Province sums to 26,504 voters and 207 blank ballots, one more of each than "
    "the definitive table of results (26,503 and 206). Registered voters, invalid ballots, valid votes, and every "
    "list's votes agree."
)
METHOD = (
    "Counted per polling station and published by the Haut-commissariat de la République en Nouvelle-Calédonie. "
    "Registered voters are the special electoral roll for the provincial elections, voters are the ballots cast, and "
    "valid votes are the votes for lists. Each province elects its assembly by one round of list proportional "
    "representation with the highest average; a list needs at least 5% of the registered voters to take part in the "
    "allocation. The Congress seats (32 for the South, 15 for the North, 7 for the Loyalty Islands) are allocated "
    "first, then the remaining seats of the assembly; the members elected to the Congress also sit in their "
    "assembly."
)
SYSTEM_NOTE = (
    "Provincial assemblies are elected province by province from closed lists. Seats go to the lists that reach 5% "
    "of the registered voters, by the highest average (D'Hondt), under the organic law of 19 March 1999 (n° 99-209). "
    "The seats that carry membership of the Congress (32 for the South, 15 for the North, 7 for the Loyalty Islands, "
    "54 in all) are allocated first, then the remaining seats of the assembly, each time among the same lists. The "
    "5% of registered voters threshold is not part of the machine-readable rules."
)
HC_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fhaut-commissariat"))
OSM_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fosm-provinces"))
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1f1999"))
SYSTEM_SINCE = date(1999, 3, 19)
CONGRESS = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fbody\x1fcongress"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def province_area(letter: str) -> str:
    return key("area", "fr.nc_province", PROVINCE[letter][0])


def assembly(letter: str) -> str:
    return key("body", "assembly", letter)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=HC_SOURCE,
        kind="official_body",
        publisher="Haut-commissariat de la République en Nouvelle-Calédonie",
        title="Élections provinciales: résultats par bureau de vote",
        url=f"{SITE}/Actions-de-l-Etat/Elections",
        license="Licence ouverte 2.0 (Etalab)",
        license_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
        attribution="Haut-commissariat de la République en Nouvelle-Calédonie",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "New Caledonia's three provinces, with their outlines from OpenStreetMap, and the polling-station scheme of "
        "the provincial elections",
    )
    p.external |= {COUNTRY, NOUVELLE_CALEDONIE}
    p.add(
        "source",
        id=OSM_SOURCE,
        kind="community",
        publisher="OpenStreetMap contributors",
        title="Administrative boundaries of the provinces of New Caledonia",
        url="https://www.openstreetmap.org/",
        license="ODbL 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="© OpenStreetMap contributors",
    )
    p.add(
        "scheme",
        code="fr.nc_province",
        polity_id=COUNTRY,
        kind="administrative",
        rank=2,
        noun="province",
        code_system=None,
    )
    p.add(
        "scheme",
        code="fr.nc_bureau",
        polity_id=COUNTRY,
        kind="precinct",
        rank=5,
        noun="polling station",
        code_system=None,
    )
    retrieved = max(raw.cached(SOURCE, NOMINATIM.format(id=r)).retrieved_at for r in OSM_RELATIONS.values())
    p.add(
        "boundary_set",
        id=key("boundary_set", "fr.nc_province", retrieved.year),
        scheme_code="fr.nc_province",
        vintage=str(retrieved.year),
        as_of=retrieved.date(),
    )
    for letter, relation in OSM_RELATIONS.items():
        artifact = raw.cached(SOURCE, NOMINATIM.format(id=relation))
        p.artifact(artifact, OSM_SOURCE)
        code, _, _, english, french = PROVINCE[letter]
        id = province_area(letter)
        p.add("area", id=id, scheme_code="fr.nc_province")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=NOUVELLE_CALEDONIE)
        p.name(id, french, "fr", "official")
        p.name(id, english, "en", "english", False)
        feature = json.loads(artifact.path.read_text())["features"][0]
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=OSM_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The three provincial assemblies and the Congress of New Caledonia, and the electoral system of their elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {COUNTRY, NOUVELLE_CALEDONIE}
    _source(p)
    for letter, (_, seats, _, english, french) in PROVINCE.items():
        id = assembly(letter)
        p.add("body", id=id, polity_id=COUNTRY, kind="council")
        p.name(id, f"Assembly of the {english}", "en", "official")
        p.name(id, f"Assemblée de la {french[0].lower()}{french[1:]}", "fr", "native", False)
        p.add("body_size", body_id=id, seats=seats, valid_from=date(1989, 6, 11), valid_to=None)
    p.add("body", id=CONGRESS, polity_id=COUNTRY, kind="unicameral")
    p.name(CONGRESS, "Congress of New Caledonia", "en", "official")
    p.name(CONGRESS, "Congrès de la Nouvelle-Calédonie", "fr", "native", False)
    p.add("body_size", body_id=CONGRESS, seats=54, valid_from=SYSTEM_SINCE, valid_to=None)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=COUNTRY,
        valid_from=SYSTEM_SINCE,
        valid_to=None,
        rules={"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", text)


@dataclass
class Proclaimed:
    registered: int
    voters: int
    blank: int
    invalid: int
    valid: int
    lists: list[tuple[str, int]]
    seats: dict[str, int]
    elected: dict[str, list[str]]


NUMBERED = re.compile(r"^\d{1,2}(?:\s*-\s*|\.\s+)(?=[^\W\d])")


def _figure(text: str) -> int:
    return int(text.replace(".", ""))


def proclamation() -> dict[str, Proclaimed]:
    """The 2019 proclamation: each province's figures, list votes, seats, and elected members."""
    text = "\n".join(page.get_text() for page in pymupdf.open(raw.cached(SOURCE, PROCLAMATION_2019).path))
    out: dict[str, Proclaimed] = {}
    sections = {
        "I": "RESULTATS DE LA PROVINCE DES ILES",
        "N": "RESULTATS DE LA PROVINCE NORD",
        "S": "RESULTATS DE LA PROVINCE SUD",
    }
    starts = sorted((text.index(head), letter) for letter, head in sections.items())
    for number, (at, letter) in enumerate(starts):
        part = text[at : starts[number + 1][0] if number + 1 < len(starts) else len(text)]

        def figure(label: str, part: str = part) -> int:
            found = re.search(label + r"\s*:\s*([\d.]+)", part)
            if found is None:
                raise ValueError(f"the proclamation has no {label}")
            return _figure(found.group(1))

        votes = part[part.index("NOMBRE DE VOIX") : part.index("ATTRIBUTION DES SIEGES AU CONGRES")]
        votes = votes.split("\n", 1)[1]
        lines = [
            line.strip() for line in votes.split("\n") if line.strip() and not re.fullmatch(r"\d{1,2}", line.strip())
        ]
        flat = " ".join(lines)
        lists = [(" ".join(name.split()), _figure(count)) for name, count in re.findall(r"(.+?)\s*:\s*([\d.]+)", flat)]
        names = [name for name, _ in lists]
        seats: dict[str, int] = defaultdict(int)
        for block in re.findall(r"ATTRIBUTION DES SIEGES.*?(?=Ont été élus)", part, re.DOTALL):
            for count, name in re.findall(r"(\d+) sièges? pour la liste\s+(.+?)\s*(?=|$)", block, re.DOTALL):
                seats[_match(" ".join(name.split()).rstrip("."), names)] += int(count)
        elected: dict[str, list[str]] = defaultdict(list)
        current = None
        header: list[str] = []
        for line in part.split("\n"):
            line = line.strip()
            if re.match(r"Candidats?\s", line):
                header = [re.sub(r"^Candidats?\s+(?:de la|pour la|de)?\s*liste\s+", "", line)]
                current = None
            elif header and line and not NUMBERED.match(line):
                header.append(line)
            elif NUMBERED.match(line):
                if current is None:
                    current = _match(" ".join(" ".join(header).split()), names)
                    header = []
                elected[current].append(NUMBERED.sub("", line))
        out[letter] = Proclaimed(
            figure("Nombre d’électeurs inscrits"),
            figure("Nombre de votants"),
            figure("Nombre de bulletins blancs"),
            figure("Nombre de bulletins nuls"),
            figure("Nombre de suffrages exprimés"),
            lists,
            dict(seats),
            dict(elected),
        )
    return out


def _match(name: str, names: list[str]) -> str:
    """The list a seat or member heading names. The text shortens and respells a list's name between sections."""
    plain = _plain(name)
    found = [other for other in names if _plain(other).startswith(plain[:22]) or plain.startswith(_plain(other)[:22])]
    if len(found) != 1:
        raise ValueError(f"{name!r} matches {found}")
    return found[0]


def dhondt(votes: list[int], registered: int, seats: int) -> list[int]:
    """Seats by the highest average among the lists with at least 5% of the registered voters."""
    eligible = [i for i, count in enumerate(votes) if count * 20 >= registered]
    won = [0] * len(votes)
    for _ in range(seats):
        best = max(eligible, key=lambda i: (votes[i] / (won[i] + 1), votes[i]))
        won[best] += 1
    return won


def _rows(year: int, letter: str) -> list[Row]:
    rows = bureaux(raw.cached(SOURCE, BUREAUX[year, letter]).path.as_posix())
    seen = {(row.commune, row.number) for row in rows}
    if len(seen) != len(rows):
        raise ValueError(f"{year} {letter}: a polling station appears twice")
    return rows


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} provincial elections of New Caledonia, by polling station, from the Haut-commissariat",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(COUNTRY)
    p.artifact(raw.cached(SOURCE, PAGES[year]), HC_SOURCE)
    for (when, _), url in BUREAUX.items():
        if when == year:
            p.artifact(raw.cached(SOURCE, url), HC_SOURCE)
    declared = proclamation() if year == 2019 else {}
    if year == 2019:
        p.artifact(raw.cached(SOURCE, PROCLAMATION_2019), HC_SOURCE)
        p.accept(
            "accounting.declared",
            "The proclamation prints 1,326 blank ballots for the South Province, which is 30 fewer than its own figures "
            "imply: 72,907 voters less 592 invalid and 70,959 valid ballots leaves 1,356, the sum of the by-station "
            "table. The printed figure is kept as declared.",
        )
    else:
        for pair in COMPOSITION.values():
            for url in pair:
                p.artifact(raw.cached(SOURCE, url), HC_SOURCE)
    election_id = key("election", year)
    p.add("election", id=election_id, polity_id=COUNTRY, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{year} New Caledonia provincial elections", "en", "official")
    p.name(election_id, f"Élections provinciales de {year} en Nouvelle-Calédonie", "fr", "native", False)
    path = f"fr/{day}-nc-provincial"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    used: set[str] = set()
    for letter, (_, seats, _, english, french) in PROVINCE.items():
        rows = _rows(year, letter)
        names = [name for name, _ in declared[letter].lists] if declared else [n for n, _ in LISTS_2026[letter]]
        totals = [sum(row.votes[i] for row in rows) for i in range(len(names))]
        registered = sum(row.registered for row in rows)
        if declared:
            proclaimed = declared[letter]
            if totals != [count for _, count in proclaimed.lists] or registered != proclaimed.registered:
                raise ValueError(f"{year} {letter}: the by-station sums differ from the proclamation")
            won = [proclaimed.seats.get(name, 0) for name in names]
        else:
            won = [count for _, count in LISTS_2026[letter]]
        congress = PROVINCE[letter][2]
        # The Congress seats are allocated first, then the other seats of the assembly, each by the highest average.
        expected = [
            a + b
            for a, b in zip(
                dhondt(totals, registered, congress), dhondt(totals, registered, seats - congress), strict=True
            )
        ]
        if sum(won) != seats or won != expected:
            raise ValueError(f"{year} {letter}: seats {won} do not follow from the votes")
        contest = key("contest", year, letter)
        label = f"Assemblée de la {french[0].lower()}{french[1:]}"
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=assembly(letter),
            district_area_id=province_area(letter),
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Assembly of the {english}", "en", "official")
        p.name(contest, label, "fr", "native", False)
        p.add("slug", path=f"{path}/{changeset.slug(label)}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, letter)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=HC_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        note = METHOD + (" " + NORTH_2026_NOTE if (year, letter) == (2026, "N") else "")
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        options = []
        for place, name in enumerate(names, 1):
            option = key("option", year, letter, place)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                ballot_position=place,
                ballot_label=name,
            )
            if won[place - 1]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won[place - 1])
        if declared:
            for name, option in zip(names, options, strict=True):
                for candidate in declared[letter].elected.get(name, []):
                    person = key("person", year, letter, name, candidate)
                    p.add("person", id=person, birth_precision=None)
                    p.name(person, candidate, "fr", "official")
                    candidacy = key("candidacy", year, letter, name, candidate)
                    p.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
            proclaimed = declared[letter]
            for measure, value in (
                (2, proclaimed.registered),
                (3, proclaimed.voters),
                (4, proclaimed.valid),
                (5, proclaimed.invalid),
                (6, proclaimed.blank),
            ):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            for option, (_, count) in zip(options, proclaimed.lists, strict=True):
                p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=count)
        for row in rows:
            code = INSEE[row.commune]
            tag = f"{code}{row.commune[-1] if row.commune.startswith('Poya') else ''}"
            unit = key("unit", year, tag, row.number)
            used.add(COMMUNES[code])
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=COUNTRY,
                scheme_code="fr.nc_bureau",
                code=f"{day}:{tag}:{row.number}",
                area_relation="within",
                area_id=COMMUNES[code],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
            p.name(unit, f"{row.commune}, bureau {row.number}: {row.name}", "fr", "official")
            p.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=province_area(letter),
                valid_from=day,
                valid_to=day + timedelta(days=1),
                approximate=False,
                partial=False,
                method="official_list",
            )
            for measure, value in (
                (2, row.registered),
                (3, row.voters),
                (4, row.valid),
                (5, row.invalid),
                (6, row.blank),
            ):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
            for option, count in zip(options, row.votes, strict=True):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=count,
                )
    p.external |= used
    return p
