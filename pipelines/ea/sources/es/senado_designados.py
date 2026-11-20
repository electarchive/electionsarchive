"""Spain's senators who were not elected by popular vote: the 41 the King appointed in 1977 and those the regional
parliaments have designated since 1980.

The Senate's open-data servlet publishes the senators of each legislature (`tipoFich=6`) with the kind of their
mandate: elected, designated by a named regional parliament, or appointed by the King. It also publishes each
senator's record (`tipoFich=1`), which gives every credential with its designation date, its end, the senator it
replaced, and the senator's party. The servlet returns an empty file or an error for about a third of the records,
mostly of senators who sat before 2011, so those senators carry no party or dates here. The Senate's list of senators
since 1977, read from an archived copy because its pages refuse scripted requests, links each name to its record.
The royal appointees are those the Constituent legislature's roster lists as appointed; Real Decreto 1352/1977 of
15 June appointed them.

Each legislature becomes one indirect election dated by the day its Senate was constituted, with one contest per
regional parliament on the Senate's seats. Each senator the roster lists as designated by that parliament is an
option declared elected, with no votes (result kind `appointed`), including those designated during the legislature
to fill a vacancy. The archive's tenure table is not part of the package format, so the designations are recorded as
these outcomes, and each option's note gives the designation and end dates the record prints.
"""

import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import httpx

from ea import package, raw
from ea.changeset import slug
from ea.names import display
from ea.package import Package

SOURCE = "es.senado_designados"
SERVLET = "https://www.senado.es/web/ficopendataservlet"
CATALOGUE = "https://www.senado.es/web/relacionesciudadanos/datosabiertos/catalogodatos/index.html"
SENATORS_URL = f"{SERVLET}?tipoFich=10"
INDEX_URL = (
    "https://web.archive.org/web/20261006194304id_/https://www.senado.es/web/composicionorganizacion/senadores/"
    "composicionsenado/senadoresdesde1977/consultaorden/index.html"
)
DECREE = "Real Decreto 1352/1977, de 15 de junio, por el que se designan Senadores"
DECREE_PAGE = "https://www.boe.es/diario_boe/txt.php?id=BOE-A-1977-13966"
DECREE_PDF = "https://www.boe.es/boe/dias/1977/06/16/pdfs/A13568-13569.pdf"
DECREE_DAY = date(1977, 6, 15)
ROYAL_SEATS = 41
LEGISLATURES = range(16)

SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
# Rows other pipelines wrote, by the keys their changesets derive ids from: the Senate (es.cortes), the country's
# area, and the autonomous communities' areas (es.ine).
SENADO = str(uuid5(package.NAMESPACE, "external_id:es.body\x1fsenado"))
NATION = str(uuid5(package.NAMESPACE, "area:es.nation@None\x1fES"))


def community(code: str) -> str:
    return str(uuid5(package.NAMESPACE, f"area:es.comunidad@None\x1f{code}"))


# The day each legislature's Senate was constituted (Diario de Sesiones del Senado; es.cortes for I to XV).
CONSTITUTED = {
    0: date(1977, 7, 13),
    1: date(1979, 3, 23),
    2: date(1982, 11, 24),
    3: date(1986, 7, 15),
    4: date(1989, 11, 21),
    5: date(1993, 7, 6),
    6: date(1996, 4, 27),
    7: date(2000, 4, 5),
    8: date(2004, 4, 2),
    9: date(2008, 4, 1),
    10: date(2011, 12, 13),
    11: date(2016, 1, 13),
    12: date(2016, 11, 17),
    13: date(2019, 5, 21),
    14: date(2019, 12, 3),
    15: date(2023, 8, 17),
}
ROMAN = ["Constituent", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV"]
ORDINAL = {
    1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th", 8: "8th", 9: "9th", 10: "10th", 11: "11th",
    12: "12th", 13: "13th", 14: "14th", 15: "15th",
}  # fmt: skip
# Each designating parliament as the rosters name it: its community's INE code and its English name.
PARLIAMENTS = {
    "Parlamento de Andalucía": ("01", "Parliament of Andalusia"),
    "Cortes de Aragón": ("02", "Cortes of Aragon"),
    "Junta General del Principado de Asturias": ("03", "General Junta of the Principality of Asturias"),
    "Parlamento de las Illes Balears": ("04", "Parliament of the Balearic Islands"),
    "Parlamento de Canarias": ("05", "Parliament of the Canary Islands"),
    "Parlamento de Cantabria": ("06", "Parliament of Cantabria"),
    "Cortes de Castilla y León": ("07", "Cortes of Castile and León"),
    "Cortes de Castilla-La Mancha": ("08", "Cortes of Castilla-La Mancha"),
    "Parlamento de Cataluña": ("09", "Parliament of Catalonia"),
    "Corts Valencianes": ("10", "Valencian Corts"),
    "Asamblea de Extremadura": ("11", "Assembly of Extremadura"),
    "Parlamento de Galicia": ("12", "Parliament of Galicia"),
    "Asamblea de Madrid": ("13", "Assembly of Madrid"),
    "Asamblea Regional de Murcia": ("14", "Regional Assembly of Murcia"),
    "Parlamento de Navarra": ("15", "Parliament of Navarre"),
    "Parlamento Vasco": ("16", "Basque Parliament"),
    "Parlamento de La Rioja": ("17", "Parliament of La Rioja"),
}
# Senators whose name a legislature's roster spells otherwise than the list of senators since 1977.
SPELLED = {
    ("AZCÁRATE FLÓREZ", "JUSTINO"): "AZCÁRATE FLÓREZ, JUSTINO DE",
    ("OLARRA UGARTEMENDÍA", "LUIS"): "OLARRA UGARTEMENDIA, LUIS",
    ("LLORÉNS BARGÉS", "CÉSAR"): "LLORENS BARGÉS, CÉSAR",
    ("COMPANYS I SANFELIÚ", "RAMON"): "COMPANYS I SANFELIU, RAMON",
    ("VILÀ GALÁN", "SARA MERCÉ"): "VILÀ GALÁN, SARA",
}
LINK = re.compile(r'<a title="Acceso a la ficha de ([^"]+)" href="[^"]*\?legis=(\d+)&(?:amp;)?id1=(\d+)">')
META = re.compile(r"<meta[^>]*>")
DAY = re.compile(r"(\d\d)/(\d\d)/(\d{4})")
LEFT = re.compile(r"Baja \((.*)\): (\d\d/\d\d/\d{4})")
CATALAN_AND = re.compile(r"(?<=\S) I (?=\S)")

REGIONAL_METHOD = (
    "Under article 69.5 of the Constitution each autonomous community's parliament designates one senator, plus one "
    "for every million residents, by the rule its own statute sets; there is no popular vote, and the Senate prints "
    "no count. This contest lists every senator the Senate's roster of the legislature shows as designated by this "
    "parliament, each declared elected, including senators designated during the legislature to fill a vacancy or "
    "after a new regional parliament was elected, so it can hold more senators than the parliament's seats at any one "
    "time. The election is dated by the day the legislature's Senate was constituted; each senator's own designation "
    "date, where the Senate's record of the senator prints it, is in the note on the option."
)
ROYAL_METHOD = (
    "Under the Ley 1/1977 para la Reforma Política the King appointed up to a fifth as many senators as were elected. "
    "Real Decreto 1352/1977 of 15 June appointed 41, who sat in the Constituent legislature with the 207 senators "
    "elected that day. There was no vote; the names are those the Senate's roster of the Constituent legislature "
    "lists as appointed by the King."
)
MISSING_RECORD = (
    "The Senate's open-data servlet returned no record for this senator, so the designation date and party are not "
    "known here."
)


@dataclass(frozen=True)
class Credential:
    legislature: int
    number: str
    kind: str
    place: str
    designated: date | None
    left: date | None
    reason: str
    replaces: str
    party: tuple[str, str] | None


@dataclass(frozen=True)
class Senator:
    idweb: str
    given: str
    surnames: str
    gender: str | None
    credentials: tuple[Credential, ...]


def legislature_url(number: int) -> str:
    return f"{SERVLET}?tipoFich=6&legis={number}"


def record_url(idweb: str, legislature: int) -> str:
    return f"{SERVLET}?tipoFich=1&cod={idweb}&legis={legislature}"


def text(node: ET.Element | None) -> str:
    return "" if node is None or node.text is None else node.text.strip()


def plain(value: str) -> str:
    return " ".join(META.sub("", value).split()).rstrip(".")


def day(value: str) -> date | None:
    found = DAY.search(value)
    return None if found is None else date(int(found[3]), int(found[2]), int(found[1]))


def roster(number: int) -> list[dict[str, str]]:
    """The senators the Senate lists for one legislature, with the kind of their mandate."""
    root = ET.parse(raw.cached(SOURCE, legislature_url(number)).path).getroot()
    return [{child.tag: text(child) for child in row} for row in root.iter("senador")]


def index() -> dict[str, list[tuple[str, int]]]:
    """Each name the Senate's list of senators since 1977 shows, with the idweb and last legislature it links to."""
    page = raw.cached(SOURCE, INDEX_URL).path.read_text(encoding="utf-8")
    found: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for name, legislature, idweb in LINK.findall(page):
        found[html.unescape(name).strip()].add((idweb, int(legislature)))
    return {name: sorted(links) for name, links in found.items()}


def appointed() -> dict[tuple[str, str], set[int]]:
    """The names of every senator the rosters list as designated or appointed, with their legislatures."""
    names: dict[tuple[str, str], set[int]] = defaultdict(set)
    for number in LEGISLATURES:
        for row in roster(number):
            if row["procedTipo"].startswith("Designa"):
                names[(row["apellidos"], row["nombre"])].add(number)
    return names


def identities() -> dict[tuple[str, str], tuple[str, int]]:
    links = index()
    out: dict[tuple[str, str], tuple[str, int]] = {}
    for surnames, given in sorted(appointed()):
        found = links.get(SPELLED.get((surnames, given), f"{surnames}, {given}"), [])
        if len(found) != 1:
            raise ValueError(f"{surnames}, {given}: {len(found)} records in the Senate's list since 1977")
        out[(surnames, given)] = found[0]
    return out


def fetch() -> None:
    raw.fetch(SOURCE, SENATORS_URL, suffix=".xml")
    raw.fetch(SOURCE, INDEX_URL, suffix=".html")
    raw.fetch(SOURCE, DECREE_PAGE, suffix=".html")
    raw.fetch(SOURCE, DECREE_PDF, suffix=".pdf")
    for number in LEGISLATURES:
        raw.fetch(SOURCE, legislature_url(number), suffix=".xml")
    failed = []
    for idweb, legislature in sorted(set(identities().values())):
        try:
            raw.fetch(SOURCE, record_url(idweb, legislature), suffix=".xml")
        except httpx.HTTPStatusError as error:
            failed.append(f"{idweb} ({error.response.status_code})")
    if failed:
        print(f"{len(failed)} records the Senate's servlet did not serve: {', '.join(failed)}")


def record(idweb: str, legislature: int) -> tuple[raw.Artifact | None, tuple[Credential, ...], str | None]:
    """A senator's record, its credentials, and the sex it prints; nothing when the servlet served no record."""
    try:
        artifact = raw.cached(SOURCE, record_url(idweb, legislature))
    except LookupError:
        return None, (), None
    if not artifact.bytes:
        return None, (), None
    root = ET.parse(artifact.path).getroot()
    if text(root.find("datosPersonales/idweb")) != idweb:
        raise ValueError(f"record {idweb}: the file is another senator's")
    credentials = []
    for term in root.iter("legislatura"):
        number = int(text(term.find("numLeg")))
        for node in term.iter("credencial"):
            row = {child.tag: text(child) for child in node}
            left = LEFT.search(row.get("bajaLiteral", ""))
            siglas, name = row.get("partidoSiglas", ""), plain(row.get("partidoNombre", ""))
            party_name = re.sub(r"\s*\([^()]*\)$", "", name) if name.endswith(f"({siglas})") else name
            credentials.append(
                Credential(
                    legislature=number,
                    number=row.get("numCred", ""),
                    kind=row.get("procedTipo", ""),
                    place=plain(row.get("procedLiteral", "")).split(":", 1)[-1].strip(),
                    designated=day(row.get("procedFecha", "")),
                    left=None if left is None else day(left[2]),
                    reason="" if left is None else left[1],
                    replaces=row.get("sustitucionLiteral", "").strip(),
                    party=(siglas, party_name) if siglas and party_name else None,
                )
            )
    sex = {"V": "male", "M": "female"}.get(text(root.find("datosPersonales/sexo")))
    return artifact, tuple(credentials), sex


@dataclass(frozen=True)
class Seat:
    legislature: int
    senator: str
    parliament: str
    printed: str
    gender: str | None


def seats() -> tuple[list[Seat], dict[str, Senator], dict[str, raw.Artifact]]:
    """Every designation or appointment the rosters list, the senators who hold them, and the records read."""
    ids = identities()
    found: list[Seat] = []
    for number in LEGISLATURES:
        for row in roster(number):
            kind = row["procedTipo"]
            if not kind.startswith("Designa"):
                continue
            idweb = ids[(row["apellidos"], row["nombre"])][0]
            place = row["procedLiteral"].split(":", 1)[-1].strip()
            if kind == "Designación real":
                if number != 0:
                    raise ValueError(f"{idweb}: appointed by the King in legislature {number}")
                parliament = ""
            elif place in PARLIAMENTS:
                parliament = place
            else:
                raise ValueError(f"{idweb}: designated by {place!r}, which is not a known parliament")
            gender = {"Designada": "female", "Designado": "male"}.get(kind)
            found.append(Seat(number, idweb, parliament, f"{row['nombre']} {row['apellidos']}", gender))
    senators: dict[str, Senator] = {}
    artifacts: dict[str, raw.Artifact] = {}
    for (surnames, given), (idweb, last) in sorted(ids.items(), key=lambda item: item[1]):
        if idweb in senators:
            continue
        artifact, credentials, sex = record(idweb, last)
        if artifact is not None:
            artifacts[idweb] = artifact
        genders = {seat.gender for seat in found if seat.senator == idweb and seat.gender} | ({sex} if sex else set())
        if len(genders) > 1:
            raise ValueError(f"{idweb}: the roster and the record disagree on the senator's sex")
        senators[idweb] = Senator(idweb, given, surnames, next(iter(genders), None), credentials)
    keys = [(seat.legislature, seat.senator, seat.parliament) for seat in found]
    if len(keys) != len(set(keys)):
        raise ValueError("a roster lists one designation twice")
    return found, senators, artifacts


def designations(senator: Senator, seat: Seat) -> list[Credential]:
    """The record's credentials for one designation: its legislature, by the same parliament."""
    return [
        c
        for c in senator.credentials
        if c.legislature == seat.legislature and c.kind in ("D", "R") and c.place == (seat.parliament or c.place)
    ]


def person_name(printed: str) -> str:
    """A name the Senate prints in capitals, in ordinary case, keeping the Catalan "i" between surnames lowercase."""
    return CATALAN_AND.sub(" i ", display(" ".join(printed.split()), "es"))


def spoken(value: date) -> str:
    return f"{value.day} {value:%B %Y}"


def option_note(senator: Senator, seat: Seat) -> str:
    held = designations(senator, seat)
    if not senator.credentials:
        return MISSING_RECORD
    if not held:
        return "The Senate's record of this senator prints no credential for this designation."
    lines = []
    for c in sorted(held, key=lambda c: (c.designated or date.min, c.number)):
        line = (
            f"Credential {c.number}: designated on {spoken(c.designated)}" if c.designated else f"Credential {c.number}"
        )
        verb, _, whom = (part.strip() for part in c.replaces.partition(":"))
        if whom and verb not in ("Sustituye a", "Sustituido por", "Sustituida por"):
            raise ValueError(f"{senator.idweb}: unknown substitution {c.replaces!r}")
        if whom and verb == "Sustituye a":
            line += f", replacing {person_name(whom)}"
        if c.left is not None:
            line += f"; left the Senate on {spoken(c.left)} ({c.reason})"
        if whom and verb != "Sustituye a":
            line += f"; replaced by {person_name(whom)}"
        lines.append(line + ".")
    return " ".join(lines)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SENATE_SOURCE = key("source", "senado")
DECREE_SOURCE = key("source", "decree")


def party_key(party: tuple[str, str]) -> str:
    return key("party", party[0], party[1])


def party_of(senator: Senator, seat: Seat) -> tuple[str, str] | None:
    held = [c for c in designations(senator, seat) if c.party]
    held.sort(key=lambda c: (c.designated or date.min, c.number))
    return held[-1].party if held else None


def packages() -> list[str]:
    return [
        f"{SOURCE}/registry",
        f"{SOURCE}/1977-royal",
        *(f"{SOURCE}/legislature-{n:02d}" for n in LEGISLATURES if n),
    ]


def parse() -> None:
    found, senators, artifacts = seats()
    royal = [seat for seat in found if not seat.parliament]
    if len(royal) != ROYAL_SEATS:
        raise ValueError(f"the Constituent roster lists {len(royal)} royal appointees, not {ROYAL_SEATS}")
    covered = sum(1 for seat in found if seat.parliament and designations(senators[seat.senator], seat))
    regional = sum(1 for seat in found if seat.parliament)
    print(f"{regional} designations; the Senate's records date {covered} of them; {len(royal)} royal appointees")
    registry(found, senators, artifacts).write()
    appointees(royal, senators).write()
    for number in LEGISLATURES:
        if number:
            legislature(number, [seat for seat in found if seat.legislature == number], senators).write()


def emit() -> None:
    package.run("load", packages())


def sources(p: Package) -> None:
    p.add(
        "source",
        id=SENATE_SOURCE,
        kind="official_body",
        publisher="Senado de España",
        title="Datos abiertos: senadores desde 1977, composición por legislatura y fichas de senadores",
        url=CATALOGUE,
        license="Public record: official parliamentary information",
        license_url="https://www.senado.es/web/aviso_legal/index.html",
        attribution="Senado de España",
    )
    p.add(
        "source",
        id=DECREE_SOURCE,
        kind="official_body",
        publisher="Agencia Estatal Boletín Oficial del Estado",
        title=DECREE,
        url=DECREE_PAGE,
        license="Public record: official legislation, free to reuse",
        license_url="https://www.boe.es/informacion/aviso_legal/index.php",
    )


def registry(found: list[Seat], senators: dict[str, Senator], artifacts: dict[str, raw.Artifact]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The senators appointed by the King in 1977 and designated by Spain's regional parliaments since 1980, with "
        "their parties, from the Senate's open data",
    )
    p.external.add(SPAIN)
    sources(p)
    p.artifact(raw.cached(SOURCE, SENATORS_URL), SENATE_SOURCE)
    p.artifact(raw.cached(SOURCE, INDEX_URL), SENATE_SOURCE)
    for idweb in sorted(artifacts):
        p.artifact(artifacts[idweb], SENATE_SOURCE)
    p.artifact(raw.cached(SOURCE, DECREE_PAGE), DECREE_SOURCE)
    p.artifact(raw.cached(SOURCE, DECREE_PDF), DECREE_SOURCE)
    used = {seat.senator for seat in found}
    parties: dict[str, tuple[str, str]] = {}
    for seat in found:
        party = party_of(senators[seat.senator], seat)
        if party is not None:
            parties[party_key(party)] = party
    for id, (siglas, name) in sorted(parties.items()):
        p.add("party", id=id, kind="party", polity_id=SPAIN)
        p.name(id, name, "es", "official")
        if siglas != name:
            p.name(id, siglas, "es", "abbreviation", False)
    for idweb in sorted(used):
        senator = senators[idweb]
        person = key("person", idweb)
        p.add("person", id=person, gender=senator.gender)
        p.name(person, person_name(f"{senator.given} {senator.surnames}"), "es", "official")
        p.add("external_id", entity_id=person, scheme=f"{SOURCE}.idweb", value=idweb)
    return p


def contest_rows(
    p: Package,
    election: str,
    path: str,
    label: str,
    area: str,
    magnitude: int | None,
    method: str,
    source: str,
    seats_: list[Seat],
    senators: dict[str, Senator],
    tag: str,
) -> None:
    contest = key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=SENADO,
        district_area_id=area,
        magnitude=magnitude,
        round_number=1,
        default_result_kind="appointed",
    )
    p.name(contest, label, "en", "official")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="appointed",
        source_id=source,
        status="active",
        is_default=True,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    for position, seat in enumerate(sorted(seats_, key=lambda s: (s.printed.split(" ")[-1], s.printed)), 1):
        senator = senators[seat.senator]
        option = key("option", tag, seat.senator)
        party = party_of(senator, seat)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None if party is None else party_key(party),
            ballot_position=position,
            ballot_label=None if party is None else party[1],
        )
        if seat.parliament:
            p.add(
                "note",
                id=key("note", option),
                entity_id=option,
                kind="context",
                body_md=option_note(senator, seat),
                lang="en",
            )
        p.add(
            "candidacy",
            id=key("candidacy", tag, seat.senator),
            person_id=key("person", seat.senator),
            option_id=option,
            role="head",
            ballot_name=seat.printed,
            is_independent=False,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")


def appointees(royal: list[Seat], senators: dict[str, Senator]) -> Package:
    p = Package(
        f"{SOURCE}/1977-royal",
        SOURCE,
        "The 41 senators King Juan Carlos I appointed by Real Decreto 1352/1977 of 15 June, from the Senate's roster "
        "of the Constituent legislature",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({SPAIN, SENADO, NATION})
    p.artifact(raw.cached(SOURCE, legislature_url(0)), SENATE_SOURCE)
    election = key("election", "royal")
    path = f"es/{DECREE_DAY}-royal-senators"
    p.add(
        "election",
        id=election,
        polity_id=SPAIN,
        kind="indirect",
        date_from=DECREE_DAY,
        date_to=DECREE_DAY,
        status="held",
    )
    p.name(election, "1977 royal appointment of senators", "en", "official")
    p.name(election, "Designación real de senadores de 1977", "es", "official", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add(
        "citation",
        id=key("citation", election),
        entity_id=election,
        source_id=DECREE_SOURCE,
        locator={"boe": "BOE-A-1977-13966", "issue": 143, "date": "1977-06-16", "pages": "13568-13569"},
    )
    contest_rows(
        p,
        election,
        f"{path}/senate",
        "Senators appointed by the King",
        NATION,
        ROYAL_SEATS,
        ROYAL_METHOD,
        DECREE_SOURCE,
        royal,
        senators,
        "royal",
    )
    return p


def legislature(number: int, found: list[Seat], senators: dict[str, Senator]) -> Package:
    constituted = CONSTITUTED[number]
    by_parliament: dict[str, list[Seat]] = defaultdict(list)
    for seat in found:
        by_parliament[seat.parliament].append(seat)
    p = Package(
        f"{SOURCE}/legislature-{number:02d}",
        SOURCE,
        f"The senators Spain's regional parliaments designated for the {ROMAN[number]} Legislature "
        f"({constituted.year}), from the Senate's open data",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({SPAIN, SENADO})
    p.artifact(raw.cached(SOURCE, legislature_url(number)), SENATE_SOURCE)
    election = key("election", number)
    path = f"es/{constituted}-designated-senators"
    p.add(
        "election",
        id=election,
        polity_id=SPAIN,
        kind="indirect",
        date_from=constituted,
        date_to=constituted,
        status="held",
    )
    p.name(
        election,
        f"Senators designated by the regional parliaments, {ORDINAL[number]} Legislature ({constituted.year})",
        "en",
        "official",
    )
    p.name(
        election,
        f"Senadores designados por las Asambleas Legislativas, {ROMAN[number]} Legislatura",
        "es",
        "official",
        False,
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    for parliament, group in sorted(by_parliament.items(), key=lambda item: PARLIAMENTS[item[0]][0]):
        code, english = PARLIAMENTS[parliament]
        area = community(code)
        p.external.add(area)
        contest_rows(
            p,
            election,
            f"{path}/{slug(parliament)}",
            f"Senators designated by the {english}",
            area,
            None,
            REGIONAL_METHOD,
            SENATE_SOURCE,
            group,
            senators,
            f"{number}:{code}",
        )
    return p
