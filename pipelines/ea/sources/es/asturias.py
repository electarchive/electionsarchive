"""Junta General del Principado de Asturias elections, 1983 to 2023, from SADEI's PX-Web tables.

SADEI (Sociedad Asturiana de Estudios Económicos e Industriales) publishes one PX-Web table of votes and seats by
territorial unit for each election, compiled from the electoral boards of the Principado ("Juntas electorales del
Principado de Asturias. Elaborado por SADEI."), and one table of electors and participation for all of them. A table's
rows are the whole of Asturias, its three circunscripciones (oriental, central, occidental), its 78 concejos, and from
1991 the votes of residents abroad (CERA) of each circunscripción.

The circunscripciones are not named in the tables by their concejos. Each one is the set of concejos whose votes add
up to its row in every election (`ORIENTAL`, `OCCIDENTAL`; the central one is the rest), and the outlines are the
union of the concejos' municipal outlines.
"""

import csv
import html
import io
import json
import re
import unicodedata
from collections import Counter
from datetime import UTC, date, datetime
from functools import cache
from uuid import uuid5

import duckdb
import httpx
import polars as pl
import shapely
import shapely.ops
from shapely.geometry import shape

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "es.asturias"
BASE = "https://www.sadei.es/sadei/pxweb/es/01/19__04/"
UA = "electionsarchive/0.1 (+https://electionsarchive.com)"
CSV = "FileTypeCsvWithHeadingAndSemiColon"
PARTICIPATION = 190402
TABLES = {
    1983: 190404,
    1987: 190406,
    1991: 190408,
    1995: 190410,
    1999: 190412,
    2003: 190414,
    2007: 190416,
    2011: 190418,
    2012: 190420,
    2015: 190422,
    2019: 190424,
    2023: 190426,
}
DAYS = {
    1983: date(1983, 5, 8),
    1987: date(1987, 6, 10),
    1991: date(1991, 5, 26),
    1995: date(1995, 5, 28),
    1999: date(1999, 6, 13),
    2003: date(2003, 5, 25),
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2012: date(2012, 3, 25),
    2015: date(2015, 5, 24),
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
}
ADM3 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ESP/ADM3/"
    "geoBoundaries-ESP-ADM3.geojson"
)
SELECT = "ctl00$ContentPlaceHolderMain$VariableSelector1$VariableSelector1$"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
REGION = str(uuid5(package.NAMESPACE, "polity\x1fES-AS"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 3
PUBLISHER = "Sociedad Asturiana de Estudios Económicos e Industriales (SADEI)"
TITLE = "Elecciones autonómicas: votos a candidaturas, escaños, censo y participación (SADEI, PX-Web)"
PORTAL = "https://www.sadei.es/sadei/pxweb/es/01/19__04/"
BOARD = "Juntas electorales del Principado de Asturias"
STATUTE = "Ley Orgánica 7/1981, de 30 de diciembre, de Estatuto de Autonomía para Asturias"
CIRC = "es.asturias_circ"
CIRCS = {
    "33501": ("oriental", "Circunscripción oriental", "Eastern constituency"),
    "33502": ("central", "Circunscripción central", "Central constituency"),
    "33503": ("occidental", "Circunscripción occidental", "Western constituency"),
}
LABELS = {circ[1]: code for code, circ in CIRCS.items()}
CERA_ROWS = {
    "Residentes ausentes oriental": "33501",
    "Residentes ausentes orienta": "33501",
    "Residentes ausentes central": "33502",
    "Residentes ausentes occidental": "33503",
}
# The concejos of the eastern and western constituencies, as the tables print them; every other concejo is in the
# central one. Each set is the only one of its size whose 1983 votes for every list add up to that constituency's
# row, and the sums hold in every later election.
ORIENTAL = {
    "Amieva",
    "Cabrales",
    "Cabranes",
    "Cangas de Onís",
    "Caravia",
    "Colunga",
    "Llanes",
    "Nava",
    "Onís",
    "Parres",
    "Peñamellera Alta",
    "Peñamellera Baja",
    "Piloña",
    "Ponga",
    "Ribadedeva",
    "Ribadesella",
    "Villaviciosa",
}
OCCIDENTAL = {
    "Allande",
    "Belmonte de Miranda",
    "Boal",
    "Candamo",
    "Cangas del Narcea",
    "Castropol",
    "Coaña",
    "Cudillero",
    "Degaña",
    "Franco, El",
    "Grado",
    "Grandas de Salime",
    "Ibias",
    "Illano",
    "Muros de Nalón",
    "Navia",
    "Pesoz",
    "Pravia",
    "Salas",
    "San Martín de Oscos",
    "San Tirso de Abres",
    "Santa Eulalia de Oscos",
    "Somiedo",
    "Tapia de Casariego",
    "Taramundi",
    "Teverga",
    "Tineo",
    "Valdés",
    "Vegadeo",
    "Villanueva de Oscos",
    "Villayón",
    "Yernes y Tameza",
}
# Rows of the votes tables that sit on the other of the oriental and occidental labels. The tables of 1983 to 2012
# print the votes of each of the two on the other's row (each equals the sum of the other constituency's concejos, and
# the participation table agrees with the concejos), but their seats stay on the right row, since D'Hondt over the
# votes of each constituency gives the seats of its own row in every one of those elections. The 2015 table's votes
# are on the right rows and its seats are not (the oriental row has the western constituency's 6 seats).
FLIP = {"33501": "33503", "33503": "33501"}
VOTES_SWAPPED = {1983, 1987, 1991, 1995, 1999, 2003, 2007, 2011, 2012}
SEATS_SWAPPED = {2015}
CERA_SWAPPED = {1991, 1995, 1999, 2003, 2007, 2012, 2015}
# Elections whose constituency rows leave out the votes of residents abroad, which their own rows give.
CERA_APART = {2011}
# Constituency rows whose list votes add up to fewer than the votes to candidacies the participation table gives, and
# the votes each list's Asturias row carries beyond the sum of its constituency cells. In 1991 the central row's lists
# are 47 votes short, and the Asturias row's 1,263 votes for UPA, 47 more than its cells add up to, are the gap.
SHORT = {(1991, "33502"): 47}
ASTURIAS_OVER = {(1991, "UPA"): 47}
SYSTEM = (
    "Closed party lists in three constituencies, the eastern, central, and western circunscripciones. The Junta "
    "General del Principado de Asturias has 45 members, and the Statute of Autonomy fixes the three constituencies and "
    "leaves the number of seats of each to the electoral law ("
    + STATUTE
    + "). Seats go by the D'Hondt method to the lists with at least 3% of the valid votes cast in the constituency, "
    "blank ballots counted as valid. The electoral boards of the Principado proclaim the seats each list won, and "
    "SADEI's tables publish them; those are the declared seats stored here."
)
CIRC_METHOD = (
    "List votes, electors, ballots cast, blank ballots, null ballots, and seats of the {circ} as SADEI's tables "
    "publish them from the electoral boards' results. Valid ballots here are the votes to candidacies, with blank "
    "ballots stored apart.{extra}"
)
CONCEJO_METHOD = (
    "List votes, electors, ballots cast, blank ballots, and null ballots by concejo from SADEI's tables, on the INE "
    "municipality codes.{abroad} The concejo figures add up to {votes:,} list votes, against {official:,} in the "
    "{circ} result."
)
WITH_ABROAD = (
    " The votes of residents abroad (CERA) are one row of the constituency, on the abroad channel, and give only "
    "their list votes."
)
APART_NOTE = (
    " The tables' constituency rows leave out the votes of residents abroad, which have their own row for each "
    "constituency; the list votes here are the two rows added together, and the electors, ballots, blank, and null "
    "ballots of those residents are not split by constituency in the source, so they are left out."
)
SWAP_NOTE = (
    " SADEI's {what} table prints this constituency's {things} on the row of the {other}; the concejos' votes and the "
    "participation table show which constituency they belong to, and the {things} are stored here on that one."
)
SHORT_NOTE = (
    " The list votes here add up to {short} fewer than the votes to candidacies the participation table gives; the "
    "Asturias row's {party} votes carry the {short} votes the constituency's cells lack, and the list votes are "
    "stored as the table prints them."
)


def table_url(number_: int) -> str:
    return f"{BASE}{number_}.px"


def export_url(number_: int) -> str:
    return f"{table_url(number_)}#{CSV}"


def hidden(page: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for tag in re.findall(r'<input[^>]*type="hidden"[^>]*>', page):
        name = re.search(r'name="([^"]*)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name:
            fields[name.group(1)] = html.unescape(value.group(1)) if value else ""
    return fields


def download(number_: int) -> bytes:
    """The table with every value of every variable selected, as the site's semicolon-delimited export."""
    session = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=120)
    url = table_url(number_)
    page = session.get(url).text
    form: dict[str, str | list[str]] = dict(hidden(page))
    for name, body in re.findall(r'<select[^>]*name="([^"]*ValuesListBox)"[^>]*>(.*?)</select>', page, re.DOTALL):
        form[name] = re.findall(r'<option[^>]*value="([^"]*)"', body)
    form[SELECT + "ButtonViewTable"] = "Continuar"
    session.post(url, data=form).raise_for_status()
    got = session.get(f"{url}/table/tableViewLayout1/?downloadfile={CSV}")
    got.raise_for_status()
    if b";" not in got.content[:2000]:
        raise ValueError(f"table {number_}: the export is not a delimited file")
    return got.content


def fetch() -> None:
    raw.fetch(SOURCE, ADM3, suffix=".geojson")
    for number_ in (PARTICIPATION, *TABLES.values()):
        link = export_url(number_)
        try:
            raw.cached(SOURCE, link)
            continue
        except LookupError:
            pass
        raw.store(SOURCE, link, download(number_), suffix=".csv", retrieved_at=datetime.now(UTC), media_type="text/csv")


def ascii_fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).lower().split())


def fold_name(name: str) -> str:
    """A concejo's name with its article first, so 'Franco, El' and 'El Franco' agree."""
    plain = ascii_fold(name)
    for article in ("el", "la", "las", "los"):
        if plain.endswith(" " + article):
            return article + " " + plain[: -len(article) - 1]
    return plain


def number(text: str) -> int | None:
    return None if text in ("..", "") else int(text)


def table(number_: int) -> list[list[str]]:
    text = raw.cached(SOURCE, export_url(number_)).path.read_bytes().decode("cp1252")
    return [row for row in csv.reader(io.StringIO(text), delimiter=";") if row]


@cache
def reference() -> dict[str, dict[str, str]]:
    """The archive's concejo areas (code to name) and units (code to id), and the province's area."""
    folder = DATA / "reference" / "es"
    out: dict[str, dict[str, str]] = {}
    con = duckdb.connect()
    areas = con.execute(
        f"select c.code, a.id, a.name from '{folder / 'area.parquet'}' a"
        f" join '{folder / 'area_code.parquet'}' c on c.area_id = a.id"
        " where a.scheme_code in ('es.municipio', 'es.provincia') and c.code like '33%'"
    ).fetchall()
    out["area:es.municipio"] = {code: id for code, id, _ in areas if len(code) == 5}
    out["name:es.municipio"] = {code: name for code, _, name in areas if len(code) == 5}
    out["area:es.provincia"] = {code: id for code, id, _ in areas if len(code) == 2}
    out["unit:es.municipio"] = dict(
        con.execute(
            f"select code, id from '{folder / 'reporting_unit.parquet'}'"
            " where scheme_code = 'es.municipio' and code like '33%' and length(code) = 5 and valid_to is null"
        ).fetchall()
    )
    return out


class Results:
    """One election's table: the lists in column order, and for each territorial row its votes and seats."""

    def __init__(self, year: int) -> None:
        rows = table(TABLES[year])
        head = rows[1]
        self.year = year
        self.lists: list[tuple[str, str]] = []
        votes_at: list[int] = []
        seats_at: list[int] = []
        total_at = -1
        for at, label in enumerate(head[1:], 1):
            kind, _, rest = label.partition(" ")
            if rest == "TOTAL" and kind == "Votos":
                total_at = at
            elif rest == "TOTAL":
                continue
            elif kind == "Votos":
                abbreviation, _, name = rest.partition(" - ")
                self.lists.append((abbreviation, name or abbreviation))
                votes_at.append(at)
            elif kind == "Escaños":
                seats_at.append(at)
            else:
                raise ValueError(f"{year}: unexpected column {label!r}")
        if len(votes_at) != len(seats_at) or total_at < 0:
            raise ValueError(f"{year}: {len(votes_at)} vote columns, {len(seats_at)} seat columns")
        if len({a for a, _ in self.lists}) != len(self.lists):
            raise ValueError(f"{year}: two lists share an abbreviation")
        self.votes: dict[str, dict[str, int | None]] = {}
        self.seats: dict[str, dict[str, int | None]] = {}
        self.cand: dict[str, int | None] = {}
        for row in rows[2:]:
            self.votes[row[0]] = {a: number(row[at]) for (a, _), at in zip(self.lists, votes_at, strict=True)}
            self.seats[row[0]] = {a: number(row[at]) for (a, _), at in zip(self.lists, seats_at, strict=True)}
            self.cand[row[0]] = number(row[total_at])
        self.concejos = [name for name in self.votes if name != "ASTURIAS" and name not in LABELS | CERA_ROWS]
        self.cera: dict[str, str] = {name: CERA_ROWS[name] for name in self.votes if name in CERA_ROWS}

    def circ_row(self, code: str) -> str:
        """The row that holds a constituency's votes."""
        return CIRCS[FLIP.get(code, code) if self.year in VOTES_SWAPPED else code][1]

    def seats_row(self, code: str) -> str:
        return CIRCS[FLIP.get(code, code) if self.year in SEATS_SWAPPED else code][1]

    def circ_votes(self, code: str) -> dict[str, int | None]:
        """The constituency's list votes with the votes of residents abroad included."""
        got = dict(self.votes[self.circ_row(code)])
        return got

    def circ_seats(self, code: str) -> dict[str, int]:
        return {key: count or 0 for key, count in self.seats[self.seats_row(code)].items()}

    def members(self, code: str) -> list[str]:
        return [name for name in self.concejos if circ_of(name) == code]

    def cera_row(self, code: str) -> str | None:
        if self.year in CERA_APART:
            return None
        if self.year in CERA_SWAPPED:
            code = FLIP.get(code, code)
        return next((name for name, where in self.cera.items() if where == code), None)


def circ_of(name: str) -> str:
    if name in ORIENTAL:
        return "33501"
    return "33503" if name in OCCIDENTAL else "33502"


@cache
def participation() -> dict[str, dict[tuple[str, int], int | None]]:
    rows = table(PARTICIPATION)
    columns: list[tuple[str, int]] = []
    for label in rows[1][1:]:
        found = re.match(r"(.*?) \d+ de \w+ de (\d{4})$", label)
        if found is None:
            raise ValueError(f"participation column {label!r}")
        columns.append((found.group(1).removeprefix("- "), int(found.group(2))))
    return {row[0]: {c: number(v) for c, v in zip(columns, row[1:], strict=True)} for row in rows[2:]}


MEASURES = {
    REGISTERED: "Censo electoral",
    BALLOTS: "Votos emitidos",
    VALID: "Votos a candidaturas",
    INVALID: "Votos nulos",
    BLANK: "Votos en blanco",
}


def accounting(row: str, year: int) -> dict[int, int]:
    found = participation()[row]
    return {m: v for m, kind in MEASURES.items() if (v := found.get((kind, year))) is not None}


def party_key(abbreviation: str) -> str:
    return ascii_fold(abbreviation).replace(" ", "-")


def concejo_codes(names: list[str]) -> dict[str, str]:
    """Each concejo's INE code by the name the tables print."""
    ref = reference()["name:es.municipio"]
    by_name = {fold_name(name): code for code, name in ref.items()}
    return {name: by_name[fold_name(name)] for name in names}


def listed(res: Results, row: str) -> int:
    return sum(count or 0 for count in res.votes[row].values())


def verify(year: int) -> Results:
    """Check an election's rows against each other and against the participation table."""
    res = Results(year)
    for name in res.concejos:
        have = accounting(name, year)[VALID]
        if listed(res, name) != have:
            raise ValueError(f"{year} {name}: lists add up to {listed(res, name)}, participation table has {have}")
    for code in CIRCS:
        got = res.circ_votes(code)
        cera = res.cera_row(code)
        for key, _ in res.lists:
            home = sum(res.votes[n][key] or 0 for n in res.members(code))
            abroad = (res.votes[cera][key] or 0) if cera else 0
            if (got[key] or 0) != home + abroad:
                raise ValueError(
                    f"{year} {code} {key}: concejos and residents abroad give {home + abroad}, row {got[key]}"
                )
        have = accounting(CIRCS[code][1], year)[VALID]
        short = have - sum(count or 0 for count in got.values())
        if short != SHORT.get((year, code), 0):
            raise ValueError(f"{year} {code}: lists are {short} short of the {have} votes to candidacies")
        if sum(res.circ_seats(code).values()) == 0:
            raise ValueError(f"{year} {code}: no seats")
    for key, _ in res.lists:
        whole = sum(res.circ_votes(code)[key] or 0 for code in CIRCS)
        if year in CERA_APART:
            whole += sum(res.votes[name][key] or 0 for name in res.cera)
        over = (res.votes["ASTURIAS"][key] or 0) - whole
        if over != ASTURIAS_OVER.get((year, key), 0):
            raise ValueError(f"{year} {key}: Asturias row is {over} above its constituencies")
    if sum(sum(res.circ_seats(code).values()) for code in CIRCS) != 45:
        raise ValueError(f"{year}: seats do not add up to 45")
    return res


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {key: count for key, count in votes.items() if count * 100 >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, key) for key, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(key for *_, key in quotients[:seats])


def registry_ids(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def unit_id(code: str) -> str:
    return registry_ids("unit", CIRC, code)


def area_id(code: str) -> str:
    return registry_ids("area", CIRC, code)


def results_source() -> str:
    return registry_ids("source", "results")


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=results_source(),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PORTAL,
        attribution="Fuente: SADEI (Sociedad Asturiana de Estudios Económicos e Industriales), a partir de las "
        "Juntas electorales del Principado de Asturias.",
        notes="Each table names its source as 'Juntas electorales del Principado de Asturias. Elaborado por SADEI.' "
        "and declares no copyright in its PC-Axis metadata. SADEI's legal notice reserves the intellectual property "
        "of its portal and states no licence for reuse.",
    )


def registry(elections: list[Results]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Junta General del Principado de Asturias, its electoral system and size, SADEI's tables as a source, "
        "and the lists that stood in the elections of 1983 to 2023",
    )
    p.external.add(REGION)
    add_source(p)
    for number_ in (PARTICIPATION, *TABLES.values()):
        p.uses(raw.cached(SOURCE, export_url(number_)))
    body = p.id("body", "junta-general")
    p.add("body", id=body, polity_id=REGION, kind="unicameral")
    p.name(body, "General Junta of the Principality of Asturias", "en", "official")
    p.name(body, "Junta General del Principado de Asturias", "es", "native")
    system = p.id("electoral_system", "junta-general")
    p.add(
        "electoral_system",
        id=system,
        polity_id=REGION,
        rules={
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD},
        },
        rules_version=1,
    )
    p.name(system, "Junta General closed-list D'Hondt system with a 3% constituency threshold", "en", "official")
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM, lang="en")
    p.add("body_size", body_id=body, seats=45, valid_from=DAYS[1983], valid_to=None)
    names: dict[str, str] = {}
    for election in elections:
        for abbreviation, name in election.lists:
            names[party_key(abbreviation)] = name
    for key, text in sorted(names.items()):
        party = p.id("party", key)
        p.add("party", id=party, kind="party", polity_id=REGION)
        p.name(party, text, "es", "official")
        p.add("external_id", entity_id=party, scheme=f"{SOURCE}.party", value=key)
    return p


def outlines() -> dict[str, shapely.Geometry]:
    """Each concejo's outline by its INE code, from the municipal outlines of geoBoundaries' Spanish release."""
    data = json.loads(raw.cached(SOURCE, ADM3).path.read_bytes())
    frame = shapely.box(-7.3, 42.8, -4.4, 43.7)
    ref = reference()["name:es.municipio"]
    wanted = {fold_name(name): code for code, name in ref.items()}
    found: dict[str, shapely.Geometry] = {}
    for feature in data["features"]:
        key = fold_name(feature["properties"]["shapeName"])
        if key not in wanted:
            continue
        geometry = shape(feature["geometry"])
        if not frame.contains(geometry.representative_point()):
            continue
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if wanted[key] in found:
            raise ValueError(f"two outlines for {ref[wanted[key]]}")
        found[wanted[key]] = geometry
    if len(found) != len(wanted):
        raise ValueError(f"outlines for {len(found)} of {len(wanted)} concejos")
    return found


def multi(geometry: shapely.Geometry) -> shapely.Geometry:
    if geometry.geom_type == "Polygon":
        return shapely.MultiPolygon([geometry])
    return geometry


def geo(elections: list[Results]) -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The three circunscripciones of the Junta General del Principado de Asturias, with outlines drawn from the "
        "concejos they hold, and the reporting units of their totals and of the votes of residents abroad",
        depends=[f"{SOURCE}/registry"],
    )
    ref = reference()
    p.external |= {SPAIN, REGION, ref["area:es.provincia"]["33"], *ref["area:es.municipio"].values()}
    shapes = registry_ids("source", "geoboundaries")
    p.artifact(raw.cached(SOURCE, ADM3), shapes)
    p.add(
        "source",
        id=shapes,
        kind="academic",
        publisher="geoBoundaries (Instituto Nacional de Estadística outlines)",
        title="geoBoundaries gbOpen Spain ADM3, 2018",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Instituto Nacional de Estadística (INE), through geoBoundaries (William & Mary geoLab), CC BY 4.0",
    )
    p.add(
        "scheme",
        code=CIRC,
        polity_id=SPAIN,
        kind="electoral_district",
        rank=3,
        noun="Junta General constituency",
        code_system="sadei",
    )
    p.add(
        "boundary_set",
        id=registry_ids("boundary_set", CIRC, 2018),
        scheme_code=CIRC,
        vintage="2018",
        valid_from=None,
        valid_to=None,
        as_of=date(2018, 1, 1),
    )
    shapes_by_code = outlines()
    codes = concejo_codes(elections[0].concejos)
    for code, (key, native, english) in CIRCS.items():
        area = area_id(code)
        p.add("area", id=area, scheme_code=CIRC)
        p.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
        p.name(area, english, "en", "official")
        p.name(area, native, "es", "native")
        p.add("area_parent", area_id=area, parent_id=ref["area:es.provincia"]["33"], valid_from=None, valid_to=None)
        members = [codes[n] for n in elections[0].concejos if circ_of(n) == code]
        whole = multi(shapely.union_all([shapes_by_code[c] for c in members]))
        point = shapely.set_srid(shapely.point_on_surface(whole), 4326)
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=None,
            valid_to=None,
            geom=shapely.to_wkb(shapely.set_srid(whole, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(point, hex=True, include_srid=True),
            quality="official",
            source_id=shapes,
        )
        for member in members:
            p.add(
                "area_membership",
                area_id=ref["area:es.municipio"][member],
                parent_id=area,
                partial=False,
                valid_from=None,
                valid_to=None,
            )
        p.add(
            "reporting_unit",
            id=unit_id(code),
            polity_id=SPAIN,
            scheme_code=CIRC,
            code=code,
            parent_id=None,
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        if any(election.cera_row(code) for election in elections):
            p.add(
                "reporting_unit",
                id=unit_id(f"{code}:CERA"),
                polity_id=SPAIN,
                scheme_code=CIRC,
                code=f"{code}:CERA",
                parent_id=unit_id(code),
                area_relation="within",
                area_id=area,
                kind="aggregate",
            )
    return p


def build(election: Results) -> Package:
    ref = reference()
    year, day = election.year, DAYS[election.year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} election to the Junta General del Principado de Asturias by circunscripción and concejo, with "
        "the seats each list won",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {SPAIN, REGION}
    source = results_source()
    p.artifact(raw.cached(SOURCE, export_url(TABLES[year])), source)
    p.artifact(raw.cached(SOURCE, export_url(PARTICIPATION)), source)
    body, system = p.id("body", "junta-general"), p.id("electoral_system", "junta-general")
    eid = p.id("election", day)
    p.add("election", id=eid, polity_id=REGION, kind="general", date_from=day, date_to=day, status="held")
    p.name(eid, f"{year} Asturian regional election", "en", "official")
    p.name(eid, f"Elecciones a la Junta General del Principado de Asturias de {year}", "es", "native")
    path = f"es/as/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    values: list[tuple[str, str, str | None, int, int, int]] = []
    codes = concejo_codes(election.concejos)
    for name, code in codes.items():
        p.external.add(ref["unit:es.municipio"][code])
    p.external |= {area_id(code) for code in CIRCS}
    for code, (key, native, _) in CIRCS.items():
        contest_key = f"{day}:{code}"
        votes = election.circ_votes(code)
        seats = election.circ_seats(code)
        cera = election.cera_row(code)
        members = election.members(code)
        contest = p.id("contest", contest_key)
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="list_allocation",
            body_id=body,
            district_area_id=area_id(code),
            magnitude=sum(seats.values()),
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, native, "es", "official")
        p.add("slug", path=f"{path}/{slug(key)}", entity_id=contest, is_current=True)
        options: dict[str, str] = {}
        for abbreviation, name in election.lists:
            standing = votes[abbreviation] is not None or any(
                election.votes[n][abbreviation] is not None for n in members
            )
            if not standing:
                continue
            options[abbreviation] = p.id("option", contest_key, party_key(abbreviation))
            p.add(
                "contest_option",
                id=options[abbreviation],
                contest_id=contest,
                kind="list",
                party_id=p.id("party", party_key(abbreviation)),
                ballot_label=f"{name} ({abbreviation})" if name != abbreviation else abbreviation,
            )
        official = sum(votes[a] or 0 for a in options)
        extra = APART_NOTE if year in CERA_APART else ""
        if year in VOTES_SWAPPED and code in FLIP:
            extra += SWAP_NOTE.format(what="votes", things="list votes", other=CIRCS[FLIP[code]][1].lower())
        if year in SEATS_SWAPPED and code in FLIP:
            extra += SWAP_NOTE.format(what="votes", things="seats", other=CIRCS[FLIP[code]][1].lower())
        if (year, code) in SHORT:
            party = next(k for (y, k) in ASTURIAS_OVER if y == year)
            extra += SHORT_NOTE.format(short=SHORT[year, code], party=party)

        def make_set(label: str, default: bool, completeness: float, note: str, key_: str = contest_key) -> str:
            id_ = p.id("result_set", key_, label)
            p.add(
                "result_set",
                id=id_,
                election_id=eid,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=default,
                completeness=completeness,
            )
            p.add("note", id=p.id("note", id_), entity_id=id_, kind="methodology", body_md=note, lang="en")
            return id_

        circ_set = make_set("circ", True, 1, CIRC_METHOD.format(circ=native, extra=extra))
        unit = unit_id(code)
        if year not in CERA_APART:
            for measure, count in accounting(CIRCS[code][1], year).items():
                values.append((circ_set, unit, None, measure, count, 0))
        else:
            values.append((circ_set, unit, None, VALID, official, 0))
        for abbreviation, option in options.items():
            count = votes[abbreviation]
            if count is not None:
                values.append((circ_set, unit, option, VOTES, count, 0))
            if seats[abbreviation]:
                p.add("outcome", result_set_id=circ_set, option_id=option, kind="won", seats=seats[abbreviation])

        got = sum(election.votes[n][a] or 0 for n in members for a in options)
        if cera:
            got += sum(election.votes[cera][a] or 0 for a in options)
        concejo_set = make_set(
            "concejo",
            False,
            min(got / official, 1.0),
            CONCEJO_METHOD.format(abroad=WITH_ABROAD if cera else "", votes=got, official=official, circ=native),
        )
        for name in members:
            uid = ref["unit:es.municipio"][codes[name]]
            for measure, count in accounting(name, year).items():
                values.append((concejo_set, uid, None, measure, count, 0))
            for abbreviation, option in options.items():
                count = election.votes[name][abbreviation]
                if count is not None:
                    values.append((concejo_set, uid, option, VOTES, count, 0))
        if cera:
            uid = unit_id(f"{code}:CERA")
            values.append((concejo_set, uid, None, VALID, sum(election.votes[cera][a] or 0 for a in options), ABROAD))
            for abbreviation, option in options.items():
                count = election.votes[cera][abbreviation]
                if count is not None:
                    values.append((concejo_set, uid, option, VOTES, count, ABROAD))
        blank = accounting(CIRCS[code][1], year).get(BLANK, 0) if year not in CERA_APART else 0
        won = dhondt({a: votes[a] or 0 for a in options}, official + blank, sum(seats.values()))
        wrong = sorted(a for a in options if won[a] != seats[a])
        print(f"{year} {code} seats={sum(seats.values())} votes={official} dhondt_differs={wrong}", flush=True)
    for (short_year, short_code), short in SHORT.items():
        if short_year == year:
            p.accept(
                "accounting.votes_valid",
                f"In SADEI's {year} table the {CIRCS[short_code][1]}'s list votes add up to {short} fewer than the votes "
                "to candidacies its participation table gives, and the Asturias row's UPA votes carry the difference; "
                "the figures are stored as published.",
            )
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "channel": pl.Int16,
            },
            orient="row",
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", *(f"{SOURCE}/{year}" for year in TABLES)]


def parse() -> None:
    elections = [verify(year) for year in TABLES]
    registry(elections).write()
    geo(elections).write()
    for election in elections:
        build(election).write()


def emit() -> None:
    package.run("load", packages())
