"""Ecuador's 2017 general election and presidential runoff, from the National Electoral Council's results book.

The book prints the presidential first round and runoff by canton (and by country abroad), and the National
Assembly and Andean Parliament contests by province, canton, and country. The tables are read from the book's text
layer by ``book2017``, and each table is checked against the totals the book prints under it before it is stored.

The ten provincial circumscriptions of Guayas, Manabí, and Pichincha also print each list's votes by parish. Those
rows are matched to the Council's parish codes by name within the canton, and must add up to their canton's row; they
go into a finer result set that supersedes the canton-level one. The book's "más votada por parroquia" pages are maps
that print no figures, so the presidential rounds stay at the canton.
"""

import functools
import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ec import book2017, cne

SOURCE = cne.SOURCE
ECUADOR = cne.ECUADOR
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fECU"))
DAY1, DAY2 = "2017-02-19", "2017-04-02"
DATES = {DAY1: date(2017, 2, 19), DAY2: date(2017, 4, 2)}
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = cne.VOTES, cne.REGISTERED, cne.CAST, cne.VALID, cne.INVALID, cne.BLANK
key = cne.key
area = cne.area
CNE_SOURCE = cne.CNE_SOURCE
ASSEMBLY = key("body", "national-assembly")
ANDEAN = key("body", "andean-parliament")
SYSTEM_LISTS = key("electoral_system", "assembly", "webster")
GBOPEN = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/"
ADM1 = GBOPEN + "ECU/ADM1/geoBoundaries-ECU-ADM1_simplified.geojson"
ADM2 = GBOPEN + "ECU/ADM2/geoBoundaries-ECU-ADM2_simplified.geojson"

# the first-round tickets in the order the book prints them: list number, abbreviation, and head of the ticket
FIRST = (
    ("3", "PSP", "Patricio Zuquilanda Duque"),
    ("5", "MFCS", "Iván Espinel Molina"),
    ("6", "PSC", "Cynthia Viteri Jimenez"),
    ("10", "FE", "Abdalá Bucaram"),
    ("12-2-18", "ID/UP/MUPP", "Paco Moncayo Gallegos"),
    ("19", "UE", "Washington Pesantez Muñoz"),
    ("21-23", "CREO/SUMA", "Guillermo Lasso"),
    ("35", "MPAIS", "Lenín Moreno Garcés"),
)
RUNOFF = FIRST[6:]

# areas the book names that the Council's later files do not list: province code, the book's name, the area's name
NEW = {
    ("10", "ZONA ESPECIAL ELECTORAL LAS GOLONDRINAS"): "Las Golondrinas",
    ("13", "ZONA ESPECIAL ELECTORAL LA MANGA DEL CURA"): "La Manga del Cura",
    ("26", "BELARUS"): "Belarús",
    ("26", "INDIA"): "India",
    ("26", "IRAN"): "Irán",
    ("26", "MALASIA"): "Malasia",
    ("26", "QATAR"): "Qatar",
    ("28", "NICARAGUA"): "Nicaragua",
}
PROVINCE_ALIASES = {
    "STO DGO DE LOS TSACHILAS": "23",
    "SANTO DOMINGO DE LOS TSACHILAS": "23",
    "STO DGO TSACHILAS": "23",
    "AMERICA LATINA EL CARIBE Y AFRICA": "28",
    "AMERICA LATINA CARIBE Y AFRICA": "28",
    "ESTADOS UNIDOS Y CANADA": "27",
    "EE UU CANADA": "27",
    "EE UU Y CANADA": "27",
    "EUROPA ASIA Y OCEANIA": "26",
}
PROVINCE_NAMES = {
    "23": "Santo Domingo de los Tsáchilas",
    "26": "Europa, Asia y Oceanía",
    "27": "Estados Unidos y Canadá",
    "28": "América Latina, el Caribe y África",
}
PROVINCE_ENGLISH = {
    "23": "Santo Domingo de los Tsáchilas",
    "26": "Europe, Asia, and Oceania",
    "27": "the United States and Canada",
    "28": "Latin America, the Caribbean, and Africa",
}
CANTON_ALIASES = {"FRANCISCO DE ORELLANA": "FCO DE ORELLANA"}
# the words the book abbreviates in a parish's name
ABBREVIATIONS = {
    "GRAL": "GENERAL",
    "CRNL": "CORONEL",
    "CRNEL": "CORONEL",
    "STA": "SANTA",
    "STO": "SANTO",
    "DR": "DOCTOR",
    "ARQ": "ARQUITECTO",
    "S": "SAN",
}


# a parish the Council's files cut short, by canton code: the book's spelling to the files' spelling
PARISH_ALIASES = {
    ("475", "LEONIDAS PLAZA GUTIERREZ"): "LEONIDAS PLAZA G.",
    ("445", "GENERAL ELOY ALFARO DELGADO"): "GENERAL ELOY ALFARO DELGA",
}


def spellings(name: str) -> set[str]:
    """A parish name as the book might print it: whole, with its abbreviated words spelled out, and each side of a slash."""
    found: set[str] = set()
    for part in (name, *name.split("/"), name.replace("/", " ")):
        text = book2017.norm(part)
        found.add(text)
        found.add(" ".join(ABBREVIATIONS.get(word, word) for word in text.split()))
    return {text for text in found if text}


def new_code(name: str) -> str:
    return "2017-" + cne.slug(name)


class Places:
    def __init__(self) -> None:
        self.provinces, self.cantons, self.parishes = cne.places()
        self.province_codes = {book2017.norm(name): code for code, name in self.provinces.items()}
        self.province_codes.update(PROVINCE_ALIASES)
        self.by_name: dict[str, dict[str, str]] = {}
        for code, (name, province) in self.cantons.items():
            self.by_name.setdefault(province, {})[book2017.norm(name)] = code
        self.names = {code: name for code, (name, _) in self.cantons.items()}
        self.by_canton: dict[str, list[tuple[str, set[str]]]] = {}
        for code, (name, canton) in self.parishes.items():
            self.by_canton.setdefault(canton, []).append((code, spellings(name)))
        for (province, text), name in NEW.items():
            code = new_code(name)
            self.by_name.setdefault(province, {})[text] = code
            self.names[code] = name.upper()
            self.cantons[code] = (name.upper(), province)

    def province(self, heading: str) -> str:
        text = re.sub(r"CIRCUNSCRIPCI.N.*", "", heading.replace("PROVINCIA -", "")).strip()
        text = book2017.norm(text.replace("RESULTADOS ELECTORALES", ""))
        for known, code in sorted(self.province_codes.items(), key=lambda item: -len(item[0])):
            if text == known or text.startswith(known + " "):
                return code
        raise KeyError(f"no province for {heading!r}")

    def canton(self, province: str, name: str) -> str:
        text = book2017.norm(name)
        text = CANTON_ALIASES.get(text, text)
        try:
            return self.by_name[province][text]
        except KeyError:
            raise KeyError(f"no canton {name!r} in province {province}") from None

    def parish(self, canton: str, name: str) -> str:
        """The code of the canton's parish that a name in the book spells, as the Council's files spell it."""
        found = spellings(name)
        found |= spellings(PARISH_ALIASES.get((canton, book2017.norm(name)), ""))
        codes = [code for code, names in self.by_canton.get(canton, []) if names & found]
        if len(codes) != 1:
            raise KeyError(f"{len(codes)} parishes of canton {canton} match {name!r}")
        return codes[0]

    def is_zone(self, canton: str) -> bool:
        """Whether a canton in the book is a special electoral zone, which has no parishes of its own."""
        return canton.startswith("ZONA ESPECIAL ELECTORAL")

    def parish_code(self, province: str, canton: str, name: str) -> str:
        return self.parish(self.canton(province, canton), name)

    def province_name(self, code: str) -> str:
        return PROVINCE_NAMES.get(code) or cne.title(self.provinces[code])


@functools.cache
def places() -> Places:
    return Places()


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo-2017",
        f"{SOURCE}/registry-2017",
        f"{SOURCE}/{DAY1}",
        f"{SOURCE}/{DAY2}",
    ]


def fetch() -> None:
    raw.fetch(SOURCE, book2017.BOOK_2017, suffix=".pdf")
    for url in (
        ADM1,
        ADM2,
        *(f"{GBOPEN}{iso}/ADM0/geoBoundaries-{iso}-ADM0_simplified.geojson" for iso in COUNTRIES.values()),
    ):
        raw.fetch(SOURCE, url, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    build(DAY1).write()
    build(DAY2).write()


def emit() -> None:
    package.run("load", packages())


# geoBoundaries spells these cantons differently from the Council's files: shape name to canton code
SHAPE_ALIASES = {
    "ALFREDO BAQUERIZO MORENO": "835",
    "BANOS DE AGUA SANTA": "150",
    "CARLOS JULIO AROSEMENA TOLA": "621",
    "CRNEL MARCELINO MARIDUENA": "890",
    "EMPALME": "385",
    "GNRAL ANTONIO ELIZALDE": "985",
    "LA JOYA DE LOS SACHAS": "725",
    "NOBOL": "940",
    "ORELLANA": "645",
    "PUEBLOVIEJO": "530",
    "RIOVERDE": "561",
    "SAN JACINTO DE YAGUACHI": "395",
    "SAN MIGUEL DE URCUQUI": "55",
    "SAN PEDRO DE PELILEO": "140",
    "SANTIAGO DE PILLARO": "145",
    "YANTZAZA": "670",
    "LAS GOLONDRINAS": new_code("Las Golondrinas"),
    "MANGA DEL CURA": new_code("La Manga del Cura"),
}
# the overseas countries: the geo name of each, and its ISO 3166 code, for the outline geoBoundaries publishes
COUNTRIES = {
    "ALEMANIA": "DEU", "ARGENTINA": "ARG", "AUSTRALIA": "AUS", "AUSTRIA": "AUT", "BELARUS": "BLR", "BELGICA": "BEL",
    "BOLIVIA": "BOL", "BRASIL": "BRA", "CANADA": "CAN", "CHILE": "CHL", "CHINA": "CHN", "COLOMBIA": "COL",
    "COREA SUR REPUBLICA DE": "KOR", "COSTA RICA": "CRI", "CUBA": "CUB", "EGIPTO": "EGY", "EL SALVADOR": "SLV",
    "ESPANA": "ESP", "ESTADOS UNIDOS DE AMERICA": "USA", "FEDERACION DE RUSIA": "RUS", "FRANCIA": "FRA",
    "GUATEMALA": "GTM", "HONDURAS": "HND", "HUNGRIA": "HUN", "INDIA": "IND", "INDONESIA": "IDN", "IRAN": "IRN",
    "ISRAEL": "ISR", "ITALIA": "ITA", "JAPON": "JPN", "MALASIA": "MYS", "MEXICO": "MEX", "NICARAGUA": "NIC",
    "PAISES BAJOS HOLANDA": "NLD", "PANAMA": "PAN", "PARAGUAY": "PRY", "PERU": "PER", "QATAR": "QAT",
    "REINO UNIDO DE GRAN BRETANA E IRLANDA DEL NORTE": "GBR", "REPUBLICA DOMINICANA": "DOM", "SUDAFRICA": "ZAF",
    "SUECIA": "SWE", "SUIZA": "CHE", "TURQUIA": "TUR", "URUGUAY": "URY", "VENEZUELA": "VEN",
}  # fmt: skip
SHAPE_SOURCE = key("source", "geoboundaries")


def _outline(url: str) -> list[tuple[str, shapely.Geometry]]:
    shapes = cne.artifact(url).path
    return [
        (feature["properties"]["shapeName"], shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
        for feature in json.loads(shapes.read_text(encoding="utf-8"))["features"]
    ]


def _geometry(p: Package, id: str, shape: shapely.Geometry) -> None:
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=SHAPE_SOURCE,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo-2017",
        SOURCE,
        "The special electoral zones and overseas countries that Ecuador's 2017 results book lists and the Council's "
        "later files do not, and outlines for the provinces, cantons, and overseas countries and circumscriptions",
        depends=[f"{SOURCE}/geo"],
    )
    place = places()
    p.external.update([ECUADOR, "ec.provincia", "ec.canton"])
    p.artifact(cne.artifact(book2017.BOOK_2017), CNE_SOURCE)
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen outlines of Ecuador's provinces and cantons and of the countries Ecuadorians vote in",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab); Ecuador's outlines are from the National Institute of "
        "Statistics and Censuses (INEC)",
    )
    for url in (
        ADM1,
        ADM2,
        *(f"{GBOPEN}{iso}/ADM0/geoBoundaries-{iso}-ADM0_simplified.geojson" for iso in COUNTRIES.values()),
    ):
        p.artifact(cne.artifact(url), SHAPE_SOURCE)
    for (province, _), name in sorted(NEW.items()):
        id = area("ec.canton", new_code(name))
        p.add("area", id=id, scheme_code="ec.canton")
        p.add("area_code", area_id=id, code=new_code(name))
        p.add("area_parent", area_id=id, parent_id=area("ec.provincia", province))
        p.name(id, name, "es", "official")
    # outlines
    provinces = [(book2017.norm(n), g) for n, g in _outline(ADM1)]
    codes = dict(place.province_codes)
    outlines: dict[str, shapely.Geometry] = {}
    for text, shape in provinces:
        code = codes[text]
        outlines[code] = shape
    for scheme, code, shape in (("ec.provincia", c, g) for c, g in outlines.items()):
        _geometry(p, area(scheme, code), shape)
    placed: set[str] = set()
    for name, shape in _outline(ADM2):
        text = book2017.norm(name)
        overlap = {c: shape.intersection(g).area for c, g in outlines.items()}
        province = max(overlap, key=lambda c: overlap[c])
        code = SHAPE_ALIASES.get(text) or place.by_name.get(province, {}).get(text)
        if code is None:
            continue  # a zone with no votes of its own in the book, such as El Piedrero
        if code in placed:
            raise ValueError(f"two outlines for canton {code}")
        placed.add(code)
        _geometry(p, area("ec.canton", code), shape)
    for number in ("ADM1", "ADM2"):
        scheme = "ec.provincia" if number == "ADM1" else "ec.canton"
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, 2019),
            scheme_code=scheme,
            vintage="2019",
            as_of=cne.artifact(ADM1 if number == "ADM1" else ADM2).retrieved_at.date(),
        )
    # overseas countries, and each circumscription as the union of its countries
    flags = {"26": [], "27": [], "28": []}
    for province, names in place.by_name.items():
        if province not in flags:
            continue
        for text, code in names.items():
            iso = COUNTRIES[text]
            outline = _outline(f"{GBOPEN}{iso}/ADM0/geoBoundaries-{iso}-ADM0_simplified.geojson")
            shape = shapely.union_all([g for _, g in outline])
            flags[province].append(shape)
            _geometry(p, area("ec.canton", code), shape)
            placed.add(code)
    for province, shapes in flags.items():
        _geometry(p, area("ec.provincia", province), shapely.union_all(shapes))
    missing = sorted(code for code in place.cantons if code not in placed)
    if missing:
        raise ValueError(f"no outline for cantons {missing}")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry-2017",
        SOURCE,
        "Ecuador's National Assembly, its members of the Andean Parliament, and the system that fills their seats",
    )
    p.external.add(ECUADOR)
    p.add("body", id=ASSEMBLY, polity_id=ECUADOR, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly of Ecuador", "en", "official")
    p.name(ASSEMBLY, "Asamblea Nacional del Ecuador", "es", "native", False)
    p.add("body", id=ANDEAN, polity_id=ECUADOR, kind="unicameral")
    p.name(ANDEAN, "Ecuador's members of the Andean Parliament", "en", "official")
    p.name(ANDEAN, "Parlamentarios andinos del Ecuador", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM_LISTS,
        polity_id=ECUADOR,
        rules={"formula": "sainte_lague", "lists": {"order": "open", "vacancy": "substitute"}},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM_LISTS),
        entity_id=SYSTEM_LISTS,
        kind="methodology",
        body_md=(
            "Voters mark candidates across lists, and each list's votes are the sum of its candidates' votes. Seats go "
            "to lists by the Webster method, which divides list totals by the odd numbers, and within a list the "
            "most-voted candidates win the seats."
        ),
        lang="en",
    )
    return p


METHOD = (
    "Counted by the National Electoral Council and published in its 2017 results book, which prints each province's "
    "results by canton and the overseas vote by country. Registered voters are the electors on the roll, ballots cast "
    "are the voters who took part, and null and blank ballots are counted separately. A voter in a legislative "
    "contest casts several votes, so no valid-ballot figure is stored for those contests; each list's votes are the "
    "sum of its candidates' votes."
)


class Builder:
    def __init__(self, p: Package, day: str, base: str) -> None:
        self.p = p
        self.day = day
        self.base = base
        self.place = places()
        self.units: dict[tuple[str, str, str], str] = {}
        self.paths: set[str] = set()
        self.election = key("election", day)

    def unit(
        self,
        scope: str,
        code: str,
        name: str,
        scheme: str,
        circumscription: str = "",
        partial: bool = False,
        area_id: str | None = None,
        parent: str | None = None,
    ) -> str:
        token = (scope, code, circumscription)
        if token in self.units:
            return self.units[token]
        tail = f":{circumscription}" if circumscription else ""
        unit = self.units[token] = key("unit", self.day, scope, code + tail)
        self.p.add(
            "reporting_unit",
            id=unit,
            polity_id=ECUADOR,
            scheme_code=scheme,
            code=f"{self.day}:{scope}:{code}{tail}",
            parent_id=parent,
            area_relation="within" if partial else "equals",
            area_id=area_id or area(scheme, code),
            kind="standard",
        )
        label = f"{name}, circunscripción {circumscription}" if circumscription else name
        self.p.name(unit, label, "es", "official")
        return unit

    def canton_unit(self, province: str, name: str, circumscription: str = "", partial: bool = False) -> str:
        code = self.place.canton(province, name)
        title = cne.title(self.place.names[code])
        return self.unit("canton", code, title, "ec.canton", circumscription if partial else "", partial)

    def province_unit(self, province: str) -> str:
        return self.unit("province", province, self.place.province_name(province), "ec.provincia")

    def path(self, base: str) -> str:
        path = base if base not in self.paths else f"{base}-{len(self.paths)}"
        self.paths.add(path)
        return path

    def contest(self, token: str, english: str, spanish: str, tail: str, kind: str, **fields: object) -> str:
        contest = key("contest", self.day, token)
        self.p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind=kind,
            round_number=fields.pop("round_number", 1),
            default_result_kind="counted",
            **fields,
        )
        self.p.name(contest, english, "en", "official")
        self.p.name(contest, spanish, "es", "native", False)
        self.p.add("slug", path=self.path(f"{self.base}/{tail}"), entity_id=contest, is_current=True)
        return contest

    def result_set(self, contest: str, token: str, superseded: bool = False, supersedes: str | None = None) -> str:
        result_set = key("result_set", self.day, token + ("/parish" if supersedes else ""))
        self.p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="superseded" if superseded else "active",
            is_default=not superseded,
            completeness=1.0,
            supersedes_id=supersedes,
        )
        self.p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en"
        )
        return result_set


def figures(p: Package, result_set: str, unit: str, registered: int, cast: int, blank: int, null: int) -> None:
    cne._put(p, result_set, unit, None, REGISTERED, registered)
    cne._put(p, result_set, unit, None, CAST, cast)
    cne._put(p, result_set, unit, None, BLANK, blank)
    cne._put(p, result_set, unit, None, INVALID, null)


def declare(
    p: Package, result_set: str, options: dict[str, str], votes: dict[str, int], figures_: dict[str, int]
) -> None:
    for number, value in votes.items():
        p.add("declared", result_set_id=result_set, option_id=options[number], measure=VOTES, value=value)
    for measure, name in ((REGISTERED, "registered"), (CAST, "cast"), (INVALID, "null"), (BLANK, "blank")):
        if name in figures_:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=figures_[name])


def president(b: Builder, runoff: bool) -> None:
    p, place = b.p, b.place
    tickets = RUNOFF if runoff else FIRST
    round_text = "second" if runoff else "first"
    contest = b.contest(
        "president",
        f"2017 Ecuadorian presidential election, {round_text} round",
        f"Elecciones presidenciales del Ecuador 2017, {'segunda' if runoff else 'primera'} vuelta",
        "president",
        "office",
        office_id=cne.PRESIDENCY,
        magnitude=1,
        round_number=2 if runoff else 1,
        previous_round_id=key("contest", DAY1, "president") if runoff else None,
        electoral_system_id=cne.SYSTEM_PRESIDENT,
    )
    result_set = b.result_set(contest, "president")
    options: dict[str, str] = {}
    for position, (number, sigla, name) in enumerate(tickets, 1):
        option = options[number] = key("option", b.day, "president", number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            ballot_position=position,
            ballot_label=f"{name} ({sigla}, list {number})",
        )
        person = key("person", "president", number)
        if not runoff:
            p.add("person", id=person)
            p.name(person, name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", b.day, "president", number),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
    tables = book2017.president_tables(runoff)
    count = len(tickets)
    p.accept(
        "accounting.ballots_sum",
        "the book's own canton figures disagree: valid votes plus blank and null ballots differ from the ballots cast "
        + (
            "in 116 of 269 cantons and countries, by 120 ballots at most and by 482 in all; stored as published"
            if runoff
            else "in 228 of 269 cantons and countries, by 1,041 ballots at most and by 4,927 in all; stored as published"
        ),
    )
    for table in tables:
        province = place.province(table.head)
        for row in table.units:
            unit = b.canton_unit(province, row.name)
            n = row.numbers
            for (number, _, _), votes in zip(tickets, n[:count], strict=True):
                cne._put(p, result_set, unit, options[number], VOTES, votes)
            cne._put(p, result_set, unit, None, VALID, n[count])
            figures(p, result_set, unit, n[count + 3], n[count + 4], n[count + 1], n[count + 2])
    totals = [sum(row.numbers[i] for t in tables for row in t.units) for i in range(count)]
    leader = max(range(count), key=lambda i: totals[i])
    runner = sorted(range(count), key=lambda i: -totals[i])[:2]
    for i, (number, _, _) in enumerate(tickets):
        if runoff:
            kind = "elected" if i == leader else "lost"
        else:
            kind = "advanced" if i in runner else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=options[number], kind=kind)
    printed = book2017.declared_president(runoff)
    figures_ = dict(zip(("null", "blank", "cast", "registered"), printed[count:], strict=True))
    declare(p, result_set, options, {t[0]: v for t, v in zip(tickets, printed[:count], strict=True)}, figures_)


def circumscription(head: str) -> str:
    found = re.search(r"CIRCUNSCRIPCI.N (\d+)", head)
    if found:
        return found[1]
    return "resto" if "RESTO" in head else ""


class Parishes(NamedTuple):
    rows: list[tuple[str, book2017.Row]]  # each parish's unit and row
    covered: set[str]  # the canton units whose votes the parish rows carry


def lists(
    b: Builder,
    group: book2017.Group,
    token: str,
    english: str,
    spanish: str,
    tail: str,
    body: str,
    rows: list[tuple[str, book2017.Row]],
    national: bool = False,
    parishes: Parishes | None = None,
    seats_won: Counter[str] | None = None,
) -> None:
    """One list-allocation contest: its lists, the seats and members each won, and its rows of votes.

    Where the book also prints the circumscription's parishes, a second set holds the cantons and their parishes and
    supersedes the first, which keeps the canton rows alone.
    """
    p = b.p
    won = seats_won if seats_won is not None else Counter(w.number for w in group.winners)
    seats = sum(won.values())
    contest = b.contest(
        token,
        english,
        spanish,
        tail,
        "list_allocation",
        body_id=body,
        magnitude=seats,
        electoral_system_id=SYSTEM_LISTS,
    )
    result_set = b.result_set(contest, token, superseded=bool(parishes))
    finer = b.result_set(contest, token, supersedes=result_set) if parishes else None
    options: dict[str, str] = {}
    for position, entry in enumerate(group.entries, 1):
        option = options[entry.number] = key("option", b.day, token, entry.number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=position,
            ballot_label=f"{cne.clean(entry.name)} ({entry.sigla}, list {entry.number})",
        )
    candidacies = []
    for winner in group.winners:
        name = cne.title(winner.name)
        person = key("person", b.day, token, winner.name)
        p.add("person", id=person, gender={"MASCULINO": "male", "FEMENINO": "female"}[winner.gender])
        p.name(person, name, "es", "official")
        candidacy = key("candidacy", b.day, token, winner.name)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=options[winner.number],
            role="list_member",
            ballot_name=name,
        )
        candidacies.append((winner, candidacy))
    count = len(group.entries)
    declared = {e.number: e.votes for e in group.entries}
    for target in (result_set, finer):
        if target is None:
            continue
        for number, count_won in won.items():
            p.add("outcome", result_set_id=target, option_id=options[number], kind="won", seats=count_won)
        for winner, candidacy in candidacies:
            p.add(
                "outcome",
                result_set_id=target,
                option_id=options[winner.number],
                candidacy_id=candidacy,
                kind="elected",
            )
        for unit, row in rows:
            if not (target == finer and parishes is not None and unit in parishes.covered):
                for entry, votes in zip(group.entries, row.numbers[:count], strict=True):
                    cne._put(p, target, unit, options[entry.number], VOTES, votes)
            if len(row.numbers) >= count + 5:
                n = row.numbers
                figures(p, target, unit, n[count + 3], n[count + 4], n[count + 1], n[count + 2])
        declare(p, target, options, declared, {} if national else group.info)
    if finer is None or not parishes:
        return
    p.add(
        "note",
        id=key("note", finer, "parish"),
        entity_id=finer,
        kind="methodology",
        body_md=(
            "This set reads the book's parish rows, which print each parish's votes for every list. Each list's votes "
            "sit on the parish units, or on the canton unit of a special zone that has no parishes, and the canton "
            "units keep the electors, ballots cast, null ballots, and blank ballots the book prints only by canton. "
            "It supersedes the set that holds the canton rows alone."
        ),
        lang="en",
    )
    for unit, row in parishes.rows:
        for entry, votes in zip(group.entries, row.numbers[:count], strict=True):
            cne._put(p, finer, unit, options[entry.number], VOTES, votes)


def parish_rows(
    b: Builder,
    group: book2017.Group,
    province: str,
    circ: str,
    cantons: list[tuple[str, book2017.Row]],
    shared: Counter[str],
    alias: Callable[[str], str] = lambda name: name,
) -> Parishes:
    """The reporting unit of each parish row in a circumscription's table, under the canton unit that holds it.

    `alias` turns the book's spelling of a canton into the spelling the Council's files use.
    """
    place = b.place
    unit_of = {id: row for id, row in cantons}
    by_canton = {place.canton(province, alias(row.name)): id for id, row in cantons}
    count = len(group.entries)
    found: list[tuple[str, book2017.Row]] = []
    covered: set[str] = set()
    for canton in book2017.parish_tables(group):
        code = place.canton(province, alias(canton.row.name))
        parent = by_canton[code]
        if unit_of[parent].numbers[: count + 1] != canton.row.numbers[: count + 1]:
            raise ValueError(f"{group.head}: the parish table's {canton.row.name} differs from the canton table's")
        if place.is_zone(canton.row.name):
            continue  # a special zone has no parishes of its own; its row is the canton's
        covered.add(parent)
        for row in canton.parishes:
            parish = place.parish_code(province, alias(canton.row.name), row.name)
            partial = shared[parish] > 1
            title = cne.title(place.parishes[parish][0])
            unit = b.unit(
                "parish",
                parish,
                title,
                "ec.parroquia",
                circ if partial else "",
                partial,
                area("ec.parroquia", parish),
                parent,
            )
            found.append((unit, row))
    return Parishes(found, covered)


def assembly(b: Builder) -> None:
    place = b.place
    groups = book2017.groups()
    seen: Counter[tuple[str, str]] = Counter()
    for group in groups:
        if group.page not in (158, 314) and group.head.startswith("PROVINCIA"):
            province = place.province(group.head)
            if circumscription(group.head):
                for row in group.units:
                    seen[(province, place.canton(province, row.name))] += 1
    shared: Counter[str] = Counter()  # the circumscriptions each parish appears in
    for group in groups:
        if group.parishes and group.page not in (158, 314):
            shared.update(
                place.parish_code(place.province(group.head), c.row.name, r.name)
                for c in book2017.parish_tables(group)
                for r in c.parishes
                if not place.is_zone(c.row.name)
            )
    for group in groups:
        head = group.head
        if group.page == 158:
            rows = [(b.province_unit(place.province(r.name)), r) for r in group.units]
            lists(
                b,
                group,
                "national",
                "2017 Ecuadorian National Assembly election, national seats",
                "Asambleístas nacionales del Ecuador 2017",
                "national-assembly",
                ASSEMBLY,
                rows,
                national=True,
            )
        elif group.page == 314:
            unit = b.unit("national", "EC", "Ecuador", "world.country", area_id=COUNTRY)
            info = group.info
            counts = (
                *(e.votes for e in group.entries),
                sum(e.votes for e in group.entries),
                info["blank"],
                info["null"],
                info["registered"],
                info["cast"],
            )
            row = book2017.Row(group.page, 0, "ECUADOR", counts, 0)
            lists(
                b,
                group,
                "andean",
                "2017 Ecuadorian election of members of the Andean Parliament",
                "Parlamentarios andinos del Ecuador 2017",
                "andean-parliament",
                ANDEAN,
                [(unit, row)],
            )
        elif head.startswith("PROVINCIA"):
            province = place.province(head)
            circ = circumscription(head)
            name = place.province_name(province)
            rows = [
                (b.canton_unit(province, r.name, circ, partial=seen[(province, place.canton(province, r.name))] > 1), r)
                for r in group.units
            ]
            if circ:
                label = "the rest of Pichincha" if circ == "resto" else f"circumscription {circ}"
                spanish = "resto de Pichincha" if circ == "resto" else f"circunscripción {circ}"
                english = f"2017 Ecuadorian National Assembly election, {name} provincial seats, {label}"
                spanish = f"Asambleístas provinciales de {name} 2017, {spanish}"
                tail = f"provincial/{cne.slug(name)}-{circ}"
            else:
                english = f"2017 Ecuadorian National Assembly election, {name} provincial seats"
                spanish = f"Asambleístas provinciales de {name} 2017"
                tail = f"provincial/{cne.slug(name)}"
            finer = parish_rows(b, group, province, circ, rows, shared) if circ else None
            lists(b, group, f"province/{province}/{circ}", english, spanish, tail, ASSEMBLY, rows, parishes=finer)
        else:
            province = place.province(head)
            rows = [(b.canton_unit(province, r.name), r) for r in group.units]
            where = PROVINCE_ENGLISH[province]
            lists(
                b,
                group,
                f"overseas/{province}",
                f"2017 Ecuadorian National Assembly election, overseas seats for {where}",
                f"Asambleístas del exterior de {PROVINCE_NAMES[province]} 2017",
                f"overseas/{cne.slug(PROVINCE_NAMES[province])}",
                ASSEMBLY,
                rows,
            )


def build(day: str) -> Package:
    first = day == DAY1
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Ecuador's {'2017 general election' if first else '2017 presidential runoff'}, from the National Electoral "
        "Council's results book",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/geo-2017",
            f"{SOURCE}/registry",
            f"{SOURCE}/registry-2017",
            *([] if first else [f"{SOURCE}/{DAY1}"]),
        ],
    )
    p.external.update([ECUADOR, COUNTRY, "world.country"])
    p.uses(cne.artifact(book2017.BOOK_2017))
    base = "ec/2017-02-19-general" if first else "ec/2017-04-02-presidential-runoff"
    b = Builder(p, day, base)
    if first:
        english, spanish, kind = "2017 Ecuadorian general election", "Elecciones generales del Ecuador 2017", "general"
    else:
        english = "2017 Ecuadorian presidential election, second round"
        spanish = "Elecciones presidenciales del Ecuador 2017, segunda vuelta"
        kind = "runoff"
    p.add(
        "election",
        id=b.election,
        polity_id=ECUADOR,
        kind=kind,
        date_from=DATES[day],
        date_to=DATES[day],
        status="held",
    )
    p.add("slug", path=base, entity_id=b.election, is_current=True)
    p.name(b.election, english, "en", "official")
    p.name(b.election, spanish, "es", "native", False)
    president(b, not first)
    if first:
        assembly(b)
    return p
