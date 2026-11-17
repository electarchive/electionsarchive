import json
import re
import subprocess
import unicodedata
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "bo.oep"
SHAPES = "bo.geoboundaries"
PAGE = "https://web.oep.org.bo/elecciones-primarias-2019/"
UPLOADS = "https://web.oep.org.bo/wp-content/uploads/"
SEPARATA = UPLOADS + "2019/02/separata_4_resultados_elecciones_primarias_2019.pdf"
ACTA = UPLOADS + "2019/02/Acta_Computo_Nacional_Elecciones_Primarias_2019.pdf"
CANDIDATES = UPLOADS + "2019/01/Candidaturas_Finales_Elecciones_Primarias_2019.pdf"
FILES = {SEPARATA: ".pdf", ACTA: ".pdf", CANDIDATES: ".pdf"}
ARCHIVE = "https://web.archive.org/web/"
SNAPSHOTS = {
    SEPARATA: ARCHIVE + "20261009002240id_/" + SEPARATA,
    ACTA: ARCHIVE + "20261012215552id_/" + ACTA,
    CANDIDATES: ARCHIVE + "20260325190207id_/" + CANDIDATES,
}
OUTLINES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/BOL/ADM1/"
    "geoBoundaries-BOL-ADM1_simplified.geojson"
)
BOLIVIA = str(uuid5(package.NAMESPACE, "polity\x1fBO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBOL"))
DEPARTMENT = "bo.departamento"
DAY = date(2019, 1, 27)
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6

DEPARTMENTS = {
    "BO-H": "Chuquisaca",
    "BO-L": "La Paz",
    "BO-C": "Cochabamba",
    "BO-O": "Oruro",
    "BO-P": "Potosí",
    "BO-T": "Tarija",
    "BO-S": "Santa Cruz",
    "BO-B": "Beni",
    "BO-N": "Pando",
}
# Each ticket, in the order the separata prints them (the date each organization's legal personality was recognized):
# code, kind, full name, president, vice president, and the national figures the separata prints
# (registered members, ballots cast, valid, blank, null).
TICKETS = (
    (
        "MNR",
        "party",
        "Movimiento Nacionalista Revolucionario",
        "Virginio Lema Trigo",
        "Fernando Untoja Choque",
        (58377, 4871, 3669, 679, 523),
    ),
    (
        "PDC",
        "party",
        "Partido Demócrata Cristiano",
        "Jaime Paz Zamora",
        "Paola Lorena Barriga Machicao",
        (28717, 1308, 736, 286, 286),
    ),
    (
        "MAS-IPSP",
        "party",
        "Movimiento Al Socialismo - Instrumento Político por la Soberanía de los Pueblos",
        "Juan Evo Morales Ayma",
        "Álvaro Marcelo García Linera",
        (991092, 451026, 406065, 17614, 27347),
    ),
    (
        "UCS",
        "party",
        "Unidad Cívica Solidaridad",
        "Víctor Hugo Cárdenas Conde",
        "Erick Humberto Peinado Méndez",
        (38421, 1915, 1288, 299, 328),
    ),
    (
        "FPV",
        "party",
        "Frente Para la Victoria",
        "Israel Franklin Rodríguez Calle",
        "Faustino Challapa Flores",
        (31060, 2033, 1114, 483, 436),
    ),
    (
        "PAN-BOL",
        "party",
        "Partido de Acción Nacional Boliviano",
        "Ruth Yolanda Nina Juchani",
        "Leopoldo Richard Chui Torrez",
        (92210, 3480, 1966, 769, 745),
    ),
    (
        "MTS",
        "party",
        "Movimiento Tercer Sistema",
        "Félix Patzi Paco",
        "Lucia Mendieta Pérez",
        (95391, 6970, 4601, 1167, 1202),
    ),
    (
        "CC",
        "bloc",
        "Alianza Comunidad Ciudadana",
        "Carlos Diego De Mesa Gisbert",
        "Gustavo Pedraza Mérida",
        (88122, 4964, 4008, 426, 530),
    ),
    (
        "21F",
        "bloc",
        "Alianza Bolivia Dice No",
        "Óscar Miguel Ortiz Antelo",
        "Edwin Mario Rodríguez Espejo",
        (292490, 18778, 13852, 2317, 2609),
    ),
)
# The final candidate list spells two running mates differently from the separata.
LIST_SPELLINGS = {
    "lucila mendieta perez": "Lucia Mendieta Pérez",
    "eric humberto peinado mendez": "Erick Humberto Peinado Méndez",
}
# The acta de cómputo nacional (a scan) prints the national sums across all nine organizations: registered members,
# ballots cast, valid, blank, and null.
ACTA_TOTALS = (1715880, 495345, 437299, 24040, 34006)
METHOD = (
    "On 27 January 2019 the members of each party or alliance on the Padrón Electoral voted in a closed primary run by "
    "the Tribunal Supremo Electoral for that organization's presidential ticket. Each organization registered a "
    "single ticket, so each contest has one option and the vote measures turnout among members, not a choice "
    "between candidates. Registered voters are the organization's enabled members (militantes habilitados), ballots "
    "cast are the votes cast (voto emitido), and valid, blank, and null are the valid, blank, and null votes of "
    "members. Figures are by department, from the Órgano Electoral Plurinacional's information bulletin no. 4. "
    "Municipal and polling-station counts are not in the files the archive reached."
)
NAMES_NOTE = (
    "The bulletin prints the running mates as Erick Humberto Peinado Méndez (UCS) and Lucia Mendieta Pérez (MTS); "
    "the final candidate list prints them as Eric Humberto Peinado Méndez and Lucila Mendieta Pérez. The bulletin's "
    "spellings are used."
)


def _key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(code: str) -> str:
    return _key("area", DEPARTMENT, code)


SOURCE_ID = _key("source", SOURCE)
SHAPES_ID = _key("source", SHAPES)
PRESIDENCY = _key("office", "president")
SYSTEM = _key("electoral_system", "primaries")


def party(code: str) -> str:
    return _key("party", code)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/primarias-2019"]


def fetch() -> None:
    for url, suffix in FILES.items():
        raw.fetch(SOURCE, url, suffix=suffix)
    raw.fetch(SHAPES, OUTLINES, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    primaries().write()


def emit() -> None:
    package.run("load", packages())


def _cached(url: str) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return raw.cached(SOURCE, SNAPSHOTS[url])


def fold(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(stripped.lower().split())


def number(text: str) -> int:
    return int(text.replace(".", ""))


def _text(url: str) -> str:
    done = subprocess.run(
        ["pdftotext", "-layout", str(_cached(url).path), "-"], check=True, capture_output=True, text=True
    )
    return done.stdout


ROW = re.compile(
    r"(Chuquisaca|La Paz|Cochabamba|Oruro|Potosí|Tarija|Santa Cruz|Beni|Pando|Total nacional)"
    r"\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+),(\d\d)%"
)


def tables() -> dict[str, dict[str, tuple[int, int, int, int, int]]]:
    """Each ticket's department rows from the separata: department -> (registered, cast, valid, blank, null)."""
    rows = [match.groups() for match in ROW.finditer(_text(SEPARATA))]
    if len(rows) != len(TICKETS) * 10:
        raise ValueError(f"the separata has {len(rows)} department rows, expected {len(TICKETS) * 10}")
    found: dict[str, dict[str, tuple[int, int, int, int, int]]] = {}
    names = {name: code for code, name in DEPARTMENTS.items()}
    for index, (code, *_, national) in enumerate(TICKETS):
        block = rows[index * 10 : index * 10 + 10]
        if [row[0] for row in block] != [*DEPARTMENTS.values(), "Total nacional"]:
            raise ValueError(f"{code}: department rows out of order: {[row[0] for row in block]}")
        figures: dict[str, tuple[int, int, int, int, int]] = {}
        for name, valid, blank, null, cast, registered, whole, hundredths in block:
            row = (number(registered), number(cast), number(valid), number(blank), number(null))
            if row[1] != row[2] + row[3] + row[4]:
                raise ValueError(f"{code} {name}: {row[1]} ballots, {row[2]} + {row[3]} + {row[4]} valid, blank, null")
            printed = int(whole) * 100 + int(hundredths)
            if abs(round(row[1] / row[0] * 10000) - printed) > 1:
                raise ValueError(f"{code} {name}: participation {row[1] / row[0]:.4%} against the printed {printed}")
            figures[names.get(name, "national")] = row
        printed_total = figures.pop("national")
        summed = tuple(sum(row[i] for row in figures.values()) for i in range(5))
        if not (printed_total == summed == national):
            raise ValueError(f"{code}: departments sum to {summed}, the separata prints {printed_total} and {national}")
        found[code] = figures
    return found


def _check_names() -> None:
    separata = fold(_text(SEPARATA))
    listed = fold(_text(CANDIDATES))
    for _, _, _, president, mate, _ in TICKETS:
        for name in (president, mate):
            if fold(name) not in separata:
                raise ValueError(f"{name} is not in the separata")
            if fold(name) not in listed and fold(name) not in {fold(v) for v in LIST_SPELLINGS.values()}:
                raise ValueError(f"{name} is not in the final candidate list")
    for spelled in LIST_SPELLINGS:
        if spelled not in listed:
            raise ValueError(f"{spelled} is not in the final candidate list")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bolivia's nine departments, with outlines from geoBoundaries",
    )
    p.external |= {BOLIVIA, COUNTRY}
    shapes = raw.cached(SHAPES, OUTLINES)
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Bolivia ADM1 (department outlines, simplified, 2015), derived from GeoBolivia",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab); original data from GeoBolivia",
    )
    p.artifact(shapes, SHAPES_ID)
    p.add(
        "scheme",
        code=DEPARTMENT,
        polity_id=BOLIVIA,
        kind="administrative",
        rank=1,
        noun="department",
        code_system="iso_3166_2",
    )
    p.add(
        "boundary_set",
        id=_key("boundary_set", DEPARTMENT, 2015),
        scheme_code=DEPARTMENT,
        vintage="2015",
        as_of=shapes.retrieved_at.date(),
    )
    outlines = {
        feature["properties"]["shapeISO"]: shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]
    }
    if set(outlines) != set(DEPARTMENTS):
        raise ValueError(f"outlines for {sorted(outlines)}, expected {sorted(DEPARTMENTS)}")
    for code, text in DEPARTMENTS.items():
        id = area(code)
        p.add("area", id=id, scheme_code=DEPARTMENT)
        p.add("area_code", area_id=id, code=code)
        p.add("area_kind", area_id=id, kind="department", source_id=SHAPES_ID)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "es", "official")
        shape = shapely.make_valid(outlines[code])
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
            source_id=SHAPES_ID,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Bolivian presidency, the primaries' electoral system, and the parties and alliances in the 2019 primaries",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BOLIVIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Órgano Electoral Plurinacional, Tribunal Supremo Electoral",
        title="Elecciones Primarias 2019: acta de cómputo nacional y separata de resultados",
        url=PAGE,
        license="Official publication of the Órgano Electoral Plurinacional",
        license_url=PAGE,
        attribution="Órgano Electoral Plurinacional de Bolivia",
    )
    p.add("office", id=PRESIDENCY, polity_id=BOLIVIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Bolivia", "en", "official")
    p.name(PRESIDENCY, "Presidente del Estado Plurinacional de Bolivia", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=BOLIVIA,
        valid_from=date(2018, 9, 1),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=_key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Under article 29 of Law 1096 on Political Organizations, each party or alliance chooses its "
            "presidential ticket by simple majority in a primary in which only the organization's registered members "
            "vote."
        ),
        lang="en",
    )
    for code, kind, name, *_ in TICKETS:
        id = party(code)
        p.add("party", id=id, kind=kind, polity_id=BOLIVIA)
        p.add("external_id", entity_id=id, scheme="bo.oep.party", value=code)
        p.name(id, name, "es", "official")
        p.name(id, code, "es", "abbreviation", False)
    for url in FILES:
        p.artifact(_cached(url), SOURCE_ID)
    return p


def primaries() -> Package:
    p = Package(
        f"{SOURCE}/primarias-2019",
        SOURCE,
        "The 27 January 2019 presidential primaries of nine parties and alliances, by department, from the "
        "Órgano Electoral Plurinacional",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BOLIVIA)
    p.external.add(COUNTRY)
    _check_names()
    for url in FILES:
        p.uses(_cached(url))
    figures = tables()
    summed = tuple(sum(national[i] for *_, national in TICKETS) for i in range(5))
    if summed != ACTA_TOTALS:
        raise ValueError(f"the nine tickets sum to {summed}, the acta prints {ACTA_TOTALS}")
    election = _key("election", DAY)
    p.add("election", id=election, polity_id=BOLIVIA, kind="primary", date_from=DAY, date_to=DAY, status="held")
    p.name(election, "2019 Bolivian presidential primaries", "en", "official")
    p.name(election, "Elecciones Primarias de Candidaturas de los Binomios Presidenciales 2019", "es", "native", False)
    path = f"bo/{DAY}-primary"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add("note", id=_key("note", election, "names"), entity_id=election, kind="caveat", body_md=NAMES_NOTE, lang="en")
    units = {}
    for code in DEPARTMENTS:
        unit = units[code] = _key("unit", DAY, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BOLIVIA,
            scheme_code=DEPARTMENT,
            code=f"{DAY}:{code}",
            area_relation="equals",
            area_id=area(code),
            kind="standard",
        )
    for position, (code, _, name, president, mate, national) in enumerate(TICKETS, 1):
        contest = _key("contest", DAY, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=PRESIDENCY,
            district_area_id=COUNTRY,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{code} presidential primary", "en", "official")
        p.name(contest, f"Primarias de {name} ({code})", "es", "native", False)
        p.add("slug", path=f"{path}/{package_slug(code)}", entity_id=contest, is_current=True)
        result_set = _key("result_set", DAY, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=_key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        option = _key("option", DAY, code)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            party_id=party(code),
            ballot_position=1,
            ballot_label=f"{president} / {mate}",
        )
        for role, person_name in (("head", president), ("running_mate", mate)):
            person = _key("person", DAY, code, role)
            p.add("person", id=person)
            p.name(person, person_name, "es", "official")
            p.add(
                "candidacy",
                id=_key("candidacy", DAY, code, role),
                person_id=person,
                option_id=option,
                role=role,
                ballot_name=person_name,
                member_of_party_id=party(code),
                is_independent=False,
            )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=1)
        for department, (registered, cast, valid, blank, null) in figures[code].items():
            unit = units[department]
            for measure, value in (
                (REGISTERED, registered),
                (CAST, cast),
                (VALID, valid),
                (INVALID, null),
                (BLANK, blank),
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
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=valid,
            )
        registered, cast, valid, blank, null = national
        for measure, value in (
            (REGISTERED, registered),
            (CAST, cast),
            (VALID, valid),
            (INVALID, null),
            (BLANK, blank),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=valid)
    return p


def package_slug(code: str) -> str:
    return code.lower()
