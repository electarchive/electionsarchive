"""Peru's 2026 general election: president (both rounds), Senate, Chamber of Deputies, and Andean Parliament.

The Jurado Nacional de Elecciones proclaims each contest in a resolution that prints the national count by list, the
members elected in each constituency with the preference votes of each, and the seats each list won. The resolutions
are the finest official figures this module reads: they give the national vote of each presidential ticket and of each
list for the Senate's national constituency, the Senate's constituency lists in total, the Chamber of Deputies in
total, and the Andean Parliament, but they print no vote of any list in any single constituency. A constituency of
the Senate or the Chamber therefore holds the members elected, the seats each list won there, and the preference
votes of those members.

The 27 constituencies are the 25 departments with Lima split into Lima Metropolitana and Lima Provincias, plus the
Peruvians living abroad; their outlines come from geoBoundaries, and no outline of the constituency abroad exists.
"""

import json
import re
import subprocess
from collections import Counter
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "pe.eg2026"
PORTAL = "https://portal.jne.gob.pe/"
UPLOADS = "https://cdn.www.gob.pe/uploads/document/file/"
RESOLUTIONS = {
    "1135": UPLOADS + "10301733/8370513-resolucion-n-1135-2026-jne.pdf",
    "1411": UPLOADS + "10301704/8370495-resolucion-n-1411-2026-jne.pdf",
    "1412": UPLOADS + "10301653/8370457-resolucion-n-1412-2026-jne.pdf",
    "1413": UPLOADS + "10301613/8370427-resolucion-n-1413-2026-jne.pdf",
    "1625": UPLOADS + "10301582/8370407-resolucion-n-1610-2026-jne.pdf",
}
OUTLINES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/PER/"
DEPARTMENTS_URL = OUTLINES + "ADM1/geoBoundaries-PER-ADM1_simplified.geojson"
PROVINCES_URL = OUTLINES + "ADM2/geoBoundaries-PER-ADM2_simplified.geojson"
FIRST, SECOND = date(2026, 4, 12), date(2026, 6, 7)
PERU = str(uuid5(package.NAMESPACE, "polity\x1fPE"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPER"))
DISTRICT = "pe.distrito_electoral"
ELECTORS = 27325432
VOTES, REGISTERED, CAST, INVALID, BLANK, PREFERENCE = 1, 2, 3, 5, 6, 7


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", "geoboundaries")
PRESIDENCY = key("office", "president")

# The 27 constituencies of the Senate and the Chamber of Deputies (Ley 26859, art. 21), with the department outline
# each one is drawn from.
CONSTITUENCIES = {
    "amazonas": ("Amazonas", "Amazonas"),
    "ancash": ("Áncash", "Ancash"),
    "apurimac": ("Apurímac", "Apurímac"),
    "arequipa": ("Arequipa", "Arequipa"),
    "ayacucho": ("Ayacucho", "Ayacucho"),
    "cajamarca": ("Cajamarca", "Cajamarca"),
    "callao": ("Callao", "Callao"),
    "cusco": ("Cusco", "Cusco"),
    "huancavelica": ("Huancavelica", "Huancavelica"),
    "huanuco": ("Huánuco", "Huánuco"),
    "ica": ("Ica", "Ica"),
    "junin": ("Junín", "Junín"),
    "la-libertad": ("La Libertad", "La Libertad"),
    "lambayeque": ("Lambayeque", "Lambayeque"),
    "lima-metropolitana": ("Lima Metropolitana", "Lima"),
    "lima-provincias": ("Lima Provincias", "Lima"),
    "loreto": ("Loreto", "Loreto"),
    "madre-de-dios": ("Madre de Dios", "Madre de Dios"),
    "moquegua": ("Moquegua", "Moquegua"),
    "pasco": ("Pasco", "Pasco"),
    "piura": ("Piura", "Piura"),
    "puno": ("Puno", "Puno"),
    "san-martin": ("San Martín", "San Martín"),
    "tacna": ("Tacna", "Tacna"),
    "tumbes": ("Tumbes", "Tumbes"),
    "ucayali": ("Ucayali", "Ucayali"),
    "extranjero": ("Peruanos residentes en el extranjero", ""),
}
BODIES = {
    "senate": ("upper_house", "Senate", "Senado", "senate"),
    "deputies": ("lower_house", "Chamber of Deputies", "Cámara de Diputados", "chamber-of-deputies"),
    "andean": ("unicameral", "Andean Parliament (Peruvian members)", "Parlamento Andino", "andean-parliament"),
}
# The 37 lists of the election, named as the Jurado Nacional de Elecciones prints them in the presidential count;
# the Frente Popular Agrícola FIA del Perú ran for Congress and the Andean Parliament only.
PARTIES = [
    "Fuerza Popular",
    "Juntos por el Perú",
    "Renovación Popular",
    "Partido del Buen Gobierno",
    "Partido Cívico Obras",
    "Partido País para Todos",
    "Ahora Nación - AN",
    "Primero la Gente – Comunidad, Ecología, Libertad y Progreso",
    "Partido Sícreo",
    "Partido Frente de la Esperanza 2021",
    "Podemos Perú",
    "Partido Político Cooperación Popular",
    "Alianza para el Progreso",
    "Partido Aprista Peruano",
    "Partido Democrático Somos Perú",
    "Partido Patriótico del Perú",
    "Partido Político Perú Primero",
    "Alianza Electoral Venceremos",
    "Un Camino Diferente",
    "Partido Demócrata Unido Perú",
    "Partido Político Nacional Perú Libre",
    "Partido Morado",
    "Unidad Nacional",
    "Progresemos",
    "Partido Demócrata Verde",
    "Partido Político Integridad Democrática",
    "Libertad Popular",
    "Avanza País - Partido de Integración Social",
    "Partido Político Perú Acción",
    "Fuerza y Libertad",
    "Fe en el Perú",
    "Partido Democratico Federal",
    "Salvemos al Perú",
    "Partido Político PRIN",
    "Perú Moderno",
    "Partido de los Trabajadores y Emprendedores PTE - Perú",
    "Frente Popular Agrícola FIA del Perú",
]
NATIONAL_SEATS = {
    "senate": {
        "Fuerza Popular": 7,
        "Juntos por el Perú": 5,
        "Partido del Buen Gobierno": 5,
        "Renovación Popular": 5,
        "Partido Cívico Obras": 4,
        "Ahora Nación - AN": 4,
    },
    "andean": {
        "Fuerza Popular": 1,
        "Juntos por el Perú": 1,
        "Partido del Buen Gobierno": 1,
        "Renovación Popular": 1,
        "Partido Cívico Obras": 1,
    },
}
LIMA_SENATE = {"Renovación Popular": 2, "Fuerza Popular": 1, "Partido del Buen Gobierno": 1}
DEPUTIES = {
    "Fuerza Popular": 41,
    "Juntos por el Perú": 32,
    "Partido del Buen Gobierno": 18,
    "Renovación Popular": 15,
    "Partido Cívico Obras": 14,
    "Ahora Nación - AN": 10,
}
# Valid votes the resolutions print as totals, for the checks each parse makes.
TOTALS = {
    "president": (16738039, 2372895, 1056811, 20167745),
    "runoff": (18397151, 118396, 1167836, 19683383),
    "deputies": (14426665, 2629036, 3134971, 20190672),
    "andean": (13376421, 3884546, 2928494, 20189461),
}
SENATE_VALID = (13679747, 14795241)
SYSTEMS = {
    "president": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A ticket that wins more than half of the valid votes is elected; otherwise the two leading tickets go to "
            "a second round (Constitution, art. 111)."
        ),
    ),
    "list": (
        {"formula": "dhondt", "threshold": {"percent": 5, "level": "party", "scope": "national"}},
        (
            "Seats are allocated by the cifra repartidora (D'Hondt) among the lists that won 5% of the valid votes "
            "nationally and, for the Senate and the Chamber of Deputies, at least 5% of the seats of the chamber; "
            "preference votes order the candidates within a list (Ley 26859, arts. 20, 21 and 30; Ley 28360)."
        ),
    ),
}


MINOR = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "para", "por", "a", "al"}


def title(text: str) -> str:
    words = " ".join(text.split()).lower().split(" ")
    return " ".join(
        word if index and word in MINOR else word[:1].upper() + word[1:] for index, word in enumerate(words)
    )


def fold(text: str) -> str:
    return changeset.slug(text).replace("-", " ")


LISTS = {fold(name): name for name in PARTIES} | {fold("Partido Político Nacional Perú Libre"): PARTIES[20]}
LISTS[fold("Partido Politico Nacional Peru Libre")] = PARTIES[20]
LISTS[fold("Partido Politico Perú Libre")] = PARTIES[20]
LISTS[fold("Partido Poitico Nacional Peru Libre")] = PARTIES[20]
LISTS[fold("Primero la Gente - Comunidad, Ecologia, Libertad y Progreso")] = PARTIES[7]


def list_of(text: str) -> str | None:
    return LISTS.get(fold(text))


def party(name: str) -> str:
    return key("party", fold(name))


def person(name: str) -> str:
    return key("person", fold(name))


# Fetching and reading the resolutions


def fetch() -> None:
    for url in RESOLUTIONS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, DEPARTMENTS_URL, suffix=".geojson")
    raw.fetch(SOURCE, PROVINCES_URL, suffix=".geojson")


def text(number_: str) -> list[str]:
    path = raw.cached(SOURCE, RESOLUTIONS[number_]).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    if f"Resolución N.° {number_}-2026-JNE" not in out and f"Resolución N. º {number_}-2026-JNE" not in out:
        raise ValueError(f"{RESOLUTIONS[number_]} is not Resolution {number_}-2026-JNE")
    lines = [line.replace(" ", " ").rstrip() for line in out.splitlines()]
    kept: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("Jurado Nacional de Elecciones", "Resolución N.", "\x0c")) or not stripped:
            if stripped:
                continue
            kept.append("")
            continue
        if re.fullmatch(r"\d{1,2}", stripped):
            after = next((later.strip() for later in lines[index + 1 :] if later.strip()), "")
            if after.startswith("Jurado Nacional de Elecciones") or not after:
                continue
        kept.append(line)
    return kept


def between(lines: list[str], start: str, end: str, after: int = 0) -> list[str]:
    first = next(i for i, line in enumerate(lines) if i >= after and re.search(start, line))
    last = next(i for i, line in enumerate(lines) if i > first and re.search(end, line))
    return lines[first:last]


def integers(token: str) -> int:
    return int(re.sub(r"\s", "", token))


NUMERIC = re.compile(r"^[\d][\d ]*%?$|^\d[\d.]*%$|^(SÍ|NO)$")


class Row(NamedTuple):
    name: str
    figures: list[int]


def rows(lines: list[str]) -> tuple[list[Row], dict[str, int]]:
    """The lists and the totals of a table whose rows are a name followed by numbers split by wide gaps."""
    found: list[Row] = []
    totals: dict[str, int] = {}
    pending: list[str] = []
    orphan: list[int] | None = None
    for line in lines:
        stripped = line.strip()
        parts = re.split(r"\s{2,}", stripped)
        tail = parts[1:]
        if orphan is not None and stripped and (len(parts) == 1 or not NUMERIC.match(parts[0])):
            name = " ".join([*pending[-2:], stripped])
            if list_of(name) is None:
                name = " ".join([*pending[-1:], stripped])
            if list_of(name) is not None:
                found.append(Row(list_of(name) or "", orphan))
            orphan, pending = None, []
            continue
        if (
            len(parts) > 1
            and pending
            and re.fullmatch(r"\d{4}", parts[0])
            and list_of(" ".join([*pending[-1:], parts[0]])) is not None
        ):
            parts = [" ".join([*pending[-1:], parts[0]]), *parts[1:]]
            stripped, tail, pending = "  ".join(parts), parts[1:], []
        elif all(NUMERIC.match(part) for part in parts) and re.match(r"^[\d ]+$", parts[0]) and pending:
            orphan = [integers(part) for part in parts if re.fullmatch(r"[\d ]+", part)]
            continue
        if tail and all(NUMERIC.match(part) for part in tail) and re.match(r"^[\d ]+$", tail[0]):
            figures = [integers(part) for part in tail if re.fullmatch(r"[\d ]+", part)]
            name = parts[0]
            norm = fold(name)
            marker = {
                "total de votos validos": "valid",
                "votos en blanco": "blank",
                "votos nulos": "null",
                "total de votos emitidos": "cast",
                "total de electores habiles a nivel nacional": "electors",
                "total": "total",
            }.get(norm)
            if marker is not None:
                totals[marker] = figures[0]
                if marker == "total":
                    totals["total_second"] = figures[1] if len(figures) > 1 else 0
                pending = []
                continue
            for text_ in (" ".join(pending[-2:] + [name]), " ".join(pending[-1:] + [name]), name):
                if list_of(text_) is not None:
                    found.append(Row(list_of(text_) or "", figures))
                    break
            else:
                # Percent-bearing lines of other tables and wrapped totals are not lists.
                if fold(name) in ("total de electores habiles a nivel", "total de ciudadanos que votaron"):
                    totals["cast_citizens" if "ciudadanos" in fold(name) else "electors"] = figures[0]
            pending = []
        elif stripped and not tail:
            pending.append(stripped)
            pending = pending[-3:]
        else:
            pending = []
    return found, totals


class Member(NamedTuple):
    constituency: str | None
    party: str
    seats: int | None
    name: str
    votes: int
    role: str


HEADER = re.compile(r"^\s*(?:\d+\.\d+\.|\d+\))\s+CIRCUNSCRIPCI[OÓ]N ELECTORAL DE (.*?)(?:\s+\((\d+) escaños?\))?\s*$")
CANDIDATE = re.compile(r"^\s*(\d+)\.\s+(\S.*)$")
WORDS = re.compile(r"^[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ' .,\-]*$")


def code_of(label: str) -> str:
    folded = fold(label)
    if folded == "lima":
        return "lima-metropolitana"
    if folded.startswith("peruanos residentes"):
        return "extranjero"
    for code, (name, _) in CONSTITUENCIES.items():
        if fold(name) == folded:
            return code
    raise ValueError(f"{label!r} is not a constituency")


def members(lines: list[str]) -> tuple[list[Member], dict[str, int]]:
    """The members a proclamation lists under each constituency and list, with their preference votes."""
    found: list[Member] = []
    sizes: dict[str, int] = {}
    constituency: str | None = None
    current: tuple[str, int | None] | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        header = HEADER.match(line)
        if header:
            constituency = code_of(header.group(1))
            if header.group(2):
                sizes[constituency] = int(header.group(2))
            current = None
        elif (abroad := re.fullmatch(r"EXTRANJERO \((\d+) escaños?\)", stripped)) is not None:
            sizes["extranjero"] = int(abroad.group(1))
        elif (given := re.fullmatch(r"(.*?)(?:\s{2,}(\d+))?", stripped)) and list_of(given.group(1)) is not None:
            current = (list_of(given.group(1)) or "", int(given.group(2)) if given.group(2) else None)
        elif (match := CANDIDATE.match(line)) and current is not None:
            rest = match.group(2)
            name, votes, spaced = rest, None, False
            tail = re.search(r"\s{2,}(\d+)$", rest)
            if tail:
                name, votes = rest[: tail.start()].strip(), int(tail.group(1))
            while votes is None:
                i += 1
                nxt = lines[i].strip()
                if not nxt:
                    continue
                if re.fullmatch(r"\d+", nxt):
                    votes, spaced = int(nxt), True
                elif (cont := re.fullmatch(r"(.*?\S)\s{2,}(\d+)", nxt)) is not None:
                    name, votes = f"{name} {cont.group(1)}", int(cont.group(2))
                else:
                    name = f"{name} {nxt}"
            if spaced:
                after = next((later.strip() for later in lines[i + 1 :] if later.strip()), "")
                if (
                    WORDS.match(after)
                    and not CANDIDATE.match(after)
                    and list_of(after) is None
                    and not HEADER.match(after)
                    and "VOTOS" not in after
                    and not after.startswith(("PRIMER", "SEGUNDO"))
                ):
                    i = next(k for k in range(i + 1, len(lines)) if lines[k].strip())
                    name = f"{name} {after}"
            found.append(Member(constituency, current[0], current[1], " ".join(name.split()), votes, "titular"))
        i += 1
    return found, sizes


ANDEAN = re.compile(r"^\s*(?:\d+\.\s+)?(\S.*?)\s{2,}(\d+)\s{2,}(TITULAR|PRIMER SUPLENTE|SEGUNDO SUPLENTE)\s*$")


def andean(lines: list[str]) -> list[Member]:
    found: list[Member] = []
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if list_of(stripped) is not None:
            current = list_of(stripped)
        elif (match := ANDEAN.match(line)) and current is not None:
            role = {"TITULAR": "titular", "PRIMER SUPLENTE": "first", "SEGUNDO SUPLENTE": "second"}.get(
                match.group(3), "second"
            )
            found.append(Member(None, current, None, " ".join(match.group(1).split()), int(match.group(2)), role))
    return found


class Count(NamedTuple):
    votes: dict[str, int]
    valid: int
    blank: int
    invalid: int
    cast: int


def count(lines: list[str], column: int = 0) -> Count:
    found, totals = rows(lines)
    votes = {row.name: row.figures[column] for row in found}
    if len(votes) != len(found):
        raise ValueError("a list appears twice in a count")
    if sum(votes.values()) != totals.get("valid"):
        raise ValueError(f"the lists add up to {sum(votes.values())}, the count prints {totals.get('valid')}")
    return Count(votes, totals["valid"], totals.get("blank", 0), totals.get("null", 0), totals.get("cast", 0))


class Data(NamedTuple):
    president: Count
    runoff: Count
    senate: dict[str, tuple[int, int]]
    deputies: Count
    andean: Count
    national_senators: list[Member]
    constituency_senators: list[Member]
    deputies_elected: list[Member]
    deputy_sizes: dict[str, int]
    andean_elected: list[Member]


def read() -> Data:
    first = text("1135")
    president = count(between(first, r"PROCLAMAR el siguiente", r"DECLARAR que"))
    second = text("1625")
    runoff = count(between(second, r"PROCLAMAR el resultado", r"PROCLAMAR ganadora"))
    senate_lines = text("1412")
    table, totals = rows(between(senate_lines, r"Cálculo de la segunda condición", r"Como resultado de los cálculos"))
    senate = {row.name: (row.figures[0], row.figures[1]) for row in table}
    if (totals["total"], totals["total_second"]) != SENATE_VALID or len(senate) != len(PARTIES):
        raise ValueError(f"the Senate's votes by list read as {totals} for {len(senate)} lists")
    for index in (0, 1):
        if sum(pair[index] for pair in senate.values()) != SENATE_VALID[index]:
            raise ValueError("the Senate's votes do not add up to its printed total")
    national = between(senate_lines, r"DISTRITO ELECTORAL ÚNICO NACIONAL \(30", r"DISTRITO ELECTORAL MÚLTIPLE \(30")
    multiple = between(senate_lines, r"DISTRITO ELECTORAL MÚLTIPLE \(30", r"DISPONER la publicación")
    chamber = text("1411")
    deputies = count(between(chamber, r"Cálculo de la segunda condición", r"Como resultado de los cálculos"))
    elected = between(chamber, r"PROCLAMAR como diputados", r"DISPONER la publicación")
    parliament = text("1413")
    andean_votes = count(between(parliament, r"VOTOS EMITIDOS A", r"Como resultado del cálculo"))
    proclaimed = between(parliament, r"PROCLAMAR representantes", r"DISPONER la publicación")
    senators, _ = members(national)
    local, _ = members(multiple)
    deputy_members, deputy_sizes = members(elected)
    data = Data(
        president,
        runoff,
        senate,
        deputies,
        andean_votes,
        senators,
        local,
        deputy_members,
        deputy_sizes,
        andean(proclaimed),
    )
    verify(data)
    return data


def verify(data: Data) -> None:
    """Every figure the resolutions print twice or can be added up must agree."""
    for name, got in (("president", data.president), ("runoff", data.runoff), ("deputies", data.deputies)):
        if (got.valid, got.blank, got.invalid, got.cast) != TOTALS[name]:
            raise ValueError(f"{name}: totals read as {got[1:]}, expected {TOTALS[name]}")
        if got.valid + got.blank + got.invalid != got.cast:
            raise ValueError(f"{name}: valid, blank, and null votes do not add up to the votes cast")
    if (data.andean.valid, data.andean.blank, data.andean.invalid, data.andean.cast) != TOTALS["andean"]:
        raise ValueError("the Andean Parliament's totals do not match")
    if len(data.president.votes) != 36 or len(data.runoff.votes) != 2:
        raise ValueError("the presidential rounds list the wrong number of tickets")
    national = Counter(member.party for member in data.national_senators)
    if dict(national) != NATIONAL_SEATS["senate"] or len(data.national_senators) != 30:
        raise ValueError(f"the Senate's national list elected {dict(national)}")
    for member in data.national_senators:
        if member.seats != NATIONAL_SEATS["senate"][member.party]:
            raise ValueError(f"{member.name}: the list's seat count disagrees")
    sizes = Counter(member.constituency for member in data.constituency_senators)
    if set(sizes) != set(CONSTITUENCIES) or any(n != (4 if c == "lima-metropolitana" else 1) for c, n in sizes.items()):
        raise ValueError(f"the Senate's constituencies read as {dict(sizes)}")
    lima = Counter(m.party for m in data.constituency_senators if m.constituency == "lima-metropolitana")
    if dict(lima) != LIMA_SENATE:
        raise ValueError(f"Lima Metropolitana's senators read as {dict(lima)}")
    seats = Counter(member.party for member in data.deputies_elected)
    if (
        dict(seats) != DEPUTIES
        or sum(data.deputy_sizes.values()) != 130
        or set(data.deputy_sizes) != set(CONSTITUENCIES)
    ):
        raise ValueError(f"the Chamber of Deputies elected {dict(seats)} in {data.deputy_sizes}")
    elected = Counter(member.constituency for member in data.deputies_elected)
    if dict(elected) != data.deputy_sizes:
        raise ValueError("a constituency elected a different number of deputies than its seats")
    for member in data.deputies_elected:
        if not member.seats:
            raise ValueError(f"{member.name}: no seat count for the list")
    titular = Counter(m.party for m in data.andean_elected if m.role == "titular")
    if dict(titular) != NATIONAL_SEATS["andean"] or len(data.andean_elected) != 15:
        raise ValueError(f"the Andean Parliament elected {dict(titular)}")
    for votes in (data.deputies.votes, data.andean.votes):
        if set(votes) - set(PARTIES):
            raise ValueError("a count names a list that did not run")


# Packages


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{FIRST}", f"{SOURCE}/{SECOND}"]


def parse() -> None:
    data = read()
    geo().write()
    registry(data).write()
    general(data).write()
    runoff(data).write()


def emit() -> None:
    package.run("load", packages())


def area(code: str) -> str:
    return key("area", DISTRICT, code)


def _geometry(geometry: shapely.Geometry) -> shapely.Geometry:
    shape = shapely.make_valid(geometry)
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def outlines() -> dict[str, shapely.Geometry]:
    """The outline of each constituency with a territory, from geoBoundaries' departments and provinces."""
    departments = {
        fold(feature["properties"]["shapeName"]): shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(raw.cached(SOURCE, DEPARTMENTS_URL).path.read_text(encoding="utf-8"))["features"]
    }
    provinces = {
        fold(feature["properties"]["shapeName"]): shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(raw.cached(SOURCE, PROVINCES_URL).path.read_text(encoding="utf-8"))["features"]
    }
    found: dict[str, shapely.Geometry] = {}
    for code, (_, department) in CONSTITUENCIES.items():
        if department and code not in ("lima-metropolitana", "lima-provincias"):
            found[code] = departments[fold(department)]
    metropolitan = shapely.make_valid(provinces["lima"])
    found["lima-metropolitana"] = metropolitan
    rest = shapely.make_valid(departments["lima"]).difference(metropolitan)
    # Where the two sets of outlines disagree along a border the difference leaves slivers; the province keeps only
    # its own body.
    parts = [part for part in shapely.get_parts(rest) if part.area >= rest.area * 0.01]
    found["lima-provincias"] = shapely.MultiPolygon(parts) if parts else rest
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Peru's 27 constituencies for the Senate and the Chamber of Deputies in 2026, with outlines from geoBoundaries",
    )
    p.external |= {PERU, COUNTRY}
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Peru ADM1 and ADM2 (department and province outlines, simplified)",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab)",
    )
    first = p.artifact(raw.cached(SOURCE, DEPARTMENTS_URL), SHAPES_ID)
    p.artifact(raw.cached(SOURCE, PROVINCES_URL), SHAPES_ID)
    del first
    p.add(
        "scheme",
        code=DISTRICT,
        polity_id=PERU,
        kind="electoral_district",
        rank=1,
        noun="constituency",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", DISTRICT, 2026),
        scheme_code=DISTRICT,
        vintage="2026",
        as_of=raw.cached(SOURCE, DEPARTMENTS_URL).retrieved_at.date(),
    )
    shapes = outlines()
    for code, (name, _) in CONSTITUENCIES.items():
        id = area(code)
        p.add("area", id=id, scheme_code=DISTRICT)
        p.add("area_code", area_id=id, code=code)
        p.add("area_kind", area_id=id, kind="constituency", source_id=SOURCE_ID)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "es", "official")
        if code in shapes:
            shape = _geometry(shapes[code])
            p.add(
                "area_geometry",
                area_id=id,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="academic",
                source_id=SHAPES_ID,
            )
    return p


def registry(data: Data) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Peru's presidency, the Senate, the Chamber of Deputies, the Andean Parliament, the electoral systems, the "
        "lists, and the people elected in the 2026 general election",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(PERU)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Jurado Nacional de Elecciones",
        title="Resoluciones de proclamación de las Elecciones Generales 2026",
        url=PORTAL,
        license="Official public record, no license stated",
        license_url=PORTAL,
        attribution="Jurado Nacional de Elecciones",
    )
    for url in RESOLUTIONS.values():
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("office", id=PRESIDENCY, polity_id=PERU, kind="head_of_state")
    p.name(PRESIDENCY, "President of Peru", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    for code, (kind, english, spanish, _) in BODIES.items():
        id = key("body", code)
        p.add("body", id=id, polity_id=PERU, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, spanish, "es", "native", False)
    for name, (rules, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=PERU,
            valid_from=date(2026, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for name in PARTIES:
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=PERU)
        p.name(id, name, "es", "official")
    names: dict[str, str] = {}
    for member in [*data.national_senators, *data.constituency_senators, *data.deputies_elected, *data.andean_elected]:
        names.setdefault(person(member.name), member.name)
    for who in FORMULA:
        names.setdefault(person(who[1]), who[1])
    for id, name in sorted(names.items()):
        p.add("person", id=id)
        p.name(id, title(name), "es", "official")
    return p


# The winning presidential ticket, as Resolution 1625-2026-JNE proclaims it.
FORMULA = (
    ("head", "Keiko Sofia Fujimori Higuchi"),
    ("running_mate", "Luis Fernando Galarreta Velarde"),
    ("running_mate", "Miguel Angel Torres Morales"),
)


class Builder:
    """The rows every contest of one election day shares."""

    def __init__(self, p: Package, when: date, election: str, path: str) -> None:
        self.p, self.when, self.election, self.path = p, when, election, path
        self.units: set[str] = set()

    def unit(self, code: str | None) -> str:
        scheme, area_id, label = ("world.country", COUNTRY, "PE") if code is None else (DISTRICT, area(code), code)
        id = key("unit", self.when, label)
        if id not in self.units:
            self.units.add(id)
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=PERU,
                scheme_code=scheme,
                code=f"{self.when}:{label}",
                area_relation="equals",
                area_id=area_id,
                kind="abroad_mission" if code == "extranjero" else "standard",
            )
        return id

    def contest(
        self,
        name: str,
        slug: str,
        *,
        district: str,
        kind: str,
        body: str | None = None,
        magnitude: int | None = None,
        system: str | None = None,
        round_number: int = 1,
        previous: str | None = None,
        spanish: str | None = None,
    ) -> str:
        p = self.p
        id = key("contest", self.when, slug)
        p.add(
            "contest",
            id=id,
            election_id=self.election,
            kind=kind,
            office_id=PRESIDENCY if kind == "office" else None,
            body_id=key("body", body) if body else None,
            district_area_id=district,
            magnitude=magnitude,
            round_number=round_number,
            previous_round_id=previous,
            electoral_system_id=key("electoral_system", system) if system else None,
            default_result_kind="counted",
        )
        p.name(id, name, "en", "official")
        if spanish:
            p.name(id, spanish, "es", "native", False)
        p.add("slug", path=f"{self.path}/{slug}", entity_id=id, is_current=True)
        return id

    def result_set(self, contest: str, complete: bool, note: str | None = None) -> str:
        p = self.p
        id = key("result_set", contest)
        p.add(
            "result_set",
            id=id,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0 if complete else None,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=METHOD, lang="en")
        if note:
            p.add("note", id=key("note", id, "scope"), entity_id=id, kind="caveat", body_md=note, lang="en")
        return id

    def value(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        self.p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )

    def option(self, contest: str, name: str, kind: str) -> str:
        id = key("option", contest, fold(name))
        self.p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind=kind,
            party_id=party(name),
            ballot_label=name,
        )
        return id

    def accounting(self, result_set: str, unit: str, got: Count, registered: bool = True) -> None:
        if registered:
            self.value(result_set, unit, None, REGISTERED, ELECTORS)
        self.value(result_set, unit, None, CAST, got.cast)
        self.value(result_set, unit, None, 4, got.valid)
        self.value(result_set, unit, None, BLANK, got.blank)
        self.value(result_set, unit, None, INVALID, got.invalid)

    def elected(
        self,
        contest: str,
        result_set: str,
        unit: str,
        options: dict[str, str],
        chosen: list[Member],
    ) -> None:
        """The members elected, their preference votes, and the seats each list won."""
        p = self.p
        seats: Counter[str] = Counter()
        for member in chosen:
            list_option = options.get(member.party) or options.setdefault(
                member.party, self.option(contest, member.party, "list")
            )
            who = person(member.name)
            candidate = key("option", contest, "candidate", fold(member.name))
            p.add(
                "contest_option",
                id=candidate,
                contest_id=contest,
                kind="candidate",
                parent_option_id=list_option,
                ballot_label=title(member.name),
            )
            candidacy = key("candidacy", contest, fold(member.name))
            substitute = member.role in ("first", "second")
            p.add(
                "candidacy",
                id=candidacy,
                person_id=who,
                option_id=list_option,
                role="substitute" if substitute else "list_member",
                ballot_name=title(member.name),
                member_of_party_id=party(member.party),
            )
            self.value(result_set, unit, candidate, PREFERENCE, member.votes)
            if not substitute:
                seats[list_option] += 1
                p.add(
                    "outcome", result_set_id=result_set, option_id=list_option, candidacy_id=candidacy, kind="elected"
                )
        for option, n in seats.items():
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=n)


METHOD = (
    "Totals as proclaimed by the Jurado Nacional de Elecciones, which takes them from the count of the Oficina "
    "Nacional de Procesos Electorales at 100% of the tally sheets. Registered voters are the final electoral roll "
    "(Resolution 0744-2025-JNE), ballots cast are the votes emitted, and blank and null votes are stored as blank "
    "and invalid ballots. The proclamations print national totals and the members elected in each constituency, not "
    "the vote of each list in each constituency or in any smaller unit."
)
ELECTED_ONLY = (
    "The proclamation prints only the members elected in this constituency, with the preference votes each "
    "received, and not the votes of every list; no vote totals are stored for this contest."
)
TALLY = (
    "The national total of the valid votes each list won across all of the constituencies, as the proclamation prints "
    "it for the electoral threshold. The seats are decided in the constituency contests, where the votes of each list "
    "are not published."
)


def general(data: Data) -> Package:
    when = FIRST
    p = Package(
        f"{SOURCE}/{when}",
        SOURCE,
        "Peru's general election of 12 April 2026: the presidency, the Senate, the Chamber of Deputies, and the "
        "Andean Parliament, from the proclamations of the Jurado Nacional de Elecciones",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PERU, COUNTRY, "world.country"}
    for number_ in ("1135", "1411", "1412", "1413"):
        p.uses(raw.cached(SOURCE, RESOLUTIONS[number_]))
    election = key("election", when, "general")
    p.add(
        "election",
        id=election,
        polity_id=PERU,
        kind="general",
        date_from=when,
        date_to=when,
        status="held",
    )
    p.name(election, "2026 Peruvian general election", "en", "official")
    p.name(election, "Elecciones Generales 2026", "es", "native", False)
    path = f"pe/{when}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add(
        "note",
        id=key("note", election),
        entity_id=election,
        kind="context",
        body_md="Voting ran on 12 April 2026 and, at thirteen polling places in Lurín, Pachacámac, and San Juan de "
        "Miraflores, until 13 April. No ticket won half of the valid votes, so the two leading tickets went to a "
        "second round on 7 June 2026.",
        lang="en",
    )
    b = Builder(p, when, election, path)
    nation = b.unit(None)
    # President, first round
    contest = b.contest(
        "2026 Peruvian presidential election",
        "president",
        district=COUNTRY,
        kind="office",
        magnitude=1,
        system="president",
        spanish="Elección de Presidente y Vicepresidentes de la República",
    )
    result_set = b.result_set(contest, True)
    b.accounting(result_set, nation, data.president)
    advanced = set(data.runoff.votes)
    for name, votes in data.president.votes.items():
        option = b.option(contest, name, "ticket")
        b.value(result_set, nation, option, VOTES, votes)
        p.add(
            "outcome", result_set_id=result_set, option_id=option, kind="advanced" if name in advanced else "eliminated"
        )
    # Senate: the national constituency
    contest = b.contest(
        "Senate, national constituency",
        "senate",
        district=COUNTRY,
        kind="list_allocation",
        body="senate",
        magnitude=30,
        system="list",
        spanish="Senado, distrito electoral único nacional",
    )
    result_set = b.result_set(contest, True)
    options = {}
    for name, (_, national) in data.senate.items():
        options[name] = b.option(contest, name, "list")
        b.value(result_set, nation, options[name], VOTES, national)
    b.value(result_set, nation, None, 4, SENATE_VALID[1])
    b.elected(contest, result_set, nation, options, data.national_senators)
    # Senate: the constituencies, and their national total
    contest = b.contest(
        "Senate, constituency lists: national vote",
        "senate-constituencies",
        district=COUNTRY,
        kind="list_allocation",
        body="senate",
        spanish="Senado, distrito electoral múltiple: votación nacional",
    )
    result_set = b.result_set(contest, True)
    p.add("note", id=key("note", result_set, "tally"), entity_id=result_set, kind="caveat", body_md=TALLY, lang="en")
    for name, (multiple, _) in data.senate.items():
        b.value(result_set, nation, b.option(contest, name, "list"), VOTES, multiple)
    b.value(result_set, nation, None, 4, SENATE_VALID[0])
    constituencies(
        b, data.constituency_senators, "senate", {c: 4 if c == "lima-metropolitana" else 1 for c in CONSTITUENCIES}
    )
    # Chamber of Deputies
    contest = b.contest(
        "Chamber of Deputies: national vote",
        "chamber-of-deputies",
        district=COUNTRY,
        kind="list_allocation",
        body="deputies",
        spanish="Cámara de Diputados: votación nacional",
    )
    result_set = b.result_set(contest, True)
    p.add("note", id=key("note", result_set, "tally"), entity_id=result_set, kind="caveat", body_md=TALLY, lang="en")
    b.accounting(result_set, nation, data.deputies)
    for name, votes in data.deputies.votes.items():
        b.value(result_set, nation, b.option(contest, name, "list"), VOTES, votes)
    constituencies(b, data.deputies_elected, "deputies", data.deputy_sizes)
    # Andean Parliament
    contest = b.contest(
        "Andean Parliament, Peruvian members",
        "andean-parliament",
        district=COUNTRY,
        kind="list_allocation",
        body="andean",
        magnitude=5,
        system="list",
        spanish="Representantes peruanos ante el Parlamento Andino",
    )
    result_set = b.result_set(contest, True)
    b.accounting(result_set, nation, data.andean)
    options = {}
    for name, votes in data.andean.votes.items():
        options[name] = b.option(contest, name, "list")
        b.value(result_set, nation, options[name], VOTES, votes)
    b.elected(contest, result_set, nation, options, data.andean_elected)
    return p


def constituencies(b: Builder, chosen: list[Member], body: str, sizes: dict[str, int]) -> None:
    english, slug = BODIES[body][1], BODIES[body][3]
    for code, (name, _) in CONSTITUENCIES.items():
        here = [member for member in chosen if member.constituency == code]
        if len(here) != sizes[code]:
            raise ValueError(f"{english}, {name}: {len(here)} members for {sizes[code]} seats")
        contest = b.contest(
            f"{english}, {name}",
            f"{slug}-{code}",
            district=area(code),
            kind="body_seats",
            body=body,
            magnitude=sizes[code],
            spanish=f"{BODIES[body][2]}, {name}",
        )
        result_set = b.result_set(contest, False, ELECTED_ONLY)
        b.elected(contest, result_set, b.unit(code), {}, here)


def runoff(data: Data) -> Package:
    when = SECOND
    p = Package(
        f"{SOURCE}/{when}",
        SOURCE,
        "Peru's presidential runoff of 7 June 2026 from the proclamation of the Jurado Nacional de Elecciones",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{FIRST}"],
    )
    p.external |= {PERU, COUNTRY, "world.country"}
    p.uses(raw.cached(SOURCE, RESOLUTIONS["1625"]))
    election = key("election", when, "runoff")
    p.add(
        "election",
        id=election,
        polity_id=PERU,
        kind="runoff",
        date_from=when,
        date_to=when,
        status="held",
    )
    p.name(election, "2026 Peruvian presidential election, second round", "en", "official")
    p.name(election, "Segunda Elección Presidencial 2026", "es", "native", False)
    path = f"pe/{when}-runoff"
    p.add("slug", path=path, entity_id=election, is_current=True)
    b = Builder(p, when, election, path)
    nation = b.unit(None)
    first = key("contest", FIRST, "president")
    p.external.add(first)
    contest = b.contest(
        "2026 Peruvian presidential election, second round",
        "president",
        district=COUNTRY,
        kind="office",
        magnitude=1,
        system="president",
        round_number=2,
        previous=first,
        spanish="Segunda Elección Presidencial",
    )
    result_set = b.result_set(contest, True)
    b.accounting(result_set, nation, data.runoff)
    winner = max(data.runoff.votes, key=lambda name: data.runoff.votes[name])
    options = {}
    for name, votes in data.runoff.votes.items():
        options[name] = b.option(contest, name, "ticket")
        b.value(result_set, nation, options[name], VOTES, votes)
        p.add(
            "outcome", result_set_id=result_set, option_id=options[name], kind="elected" if name == winner else "lost"
        )
    if winner != "Fuerza Popular":
        raise ValueError(f"{winner} won the runoff, not the ticket the proclamation names")
    for role, who in FORMULA:
        candidacy = key("candidacy", contest, fold(who))
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person(who),
            option_id=options[winner],
            role=role,
            ballot_name=title(who),
            member_of_party_id=party(winner),
        )
    return p
