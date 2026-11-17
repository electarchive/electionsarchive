"""Bolivia's municipal council elections of 1985 to 2004 from the Órgano Electoral Plurinacional's Atlas Electoral.

Tomo III prints the council results of each municipal election: by municipality for 1995, 1999, and 2004 (with the
seats each list won), and for the capital cities of the departments, with the departmental totals, before 1995. The
digital Atlas holds the 1999 count by polling table. The boundary shapes are the public-domain GeoBolivia municipal
and departmental boundaries as distributed by geoBoundaries.
"""

import functools
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import shapely

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.bo import municipal_atlas as atlas
from ea.sources.bo import municipal_places as places
from ea.sources.bo.municipal_atlas import fold
from ea.sources.bo.municipal_places import Place

SOURCE = "bo.oep_municipal"
SHAPES = "bo.geoboundaries"
BOLIVIA = str(uuid5(package.NAMESPACE, "polity\x1fBO"))
VOLUMES = "https://web.oep.org.bo/wp-content/uploads/2017/03/"
TOMO_III = {
    1: VOLUMES + "Atlas_Electoral_tomo-III_Cap_I.pdf",
    2: VOLUMES + "Atlas_Electoral_tomo-III_Cap_II.pdf",
}
INDEX = "https://web.oep.org.bo/informacion-publica/atlas-electoral-de-bolivia-tomo-iii/"
FILE_1999 = "https://atlaselectoral.oep.org.bo/descarga/1/votos_totales.csv"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/BOL/"
ADM1 = GEOBOUNDARIES + "ADM1/geoBoundaries-BOL-ADM1_simplified.geojson"
ADM3 = GEOBOUNDARIES + "ADM3/geoBoundaries-BOL-ADM3_simplified.geojson"

DAYS = {
    1985: date(1985, 7, 14),
    1987: date(1987, 12, 6),
    1989: date(1989, 12, 3),
    1991: date(1991, 12, 1),
    1993: date(1993, 12, 5),
    1995: date(1995, 12, 3),
    1999: date(1999, 12, 5),
    2004: date(2004, 12, 5),
}
VOLUME_OF = {1985: 1, 1987: 1, 1989: 1, 1991: 1, 1993: 1, 1995: 2, 1999: 2, 2004: 2}
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ACCOUNTS = {"Inscritos": REGISTERED, "Emitidos": CAST, "Válidos": VALID, "Nulos": INVALID, "Blancos": BLANK}
FILE_ACCOUNTS = {"INSCRITOS": REGISTERED, "EMITIDOS": CAST, "VALIDOS": VALID, "NULOS": INVALID, "BLANCOS": BLANK}
# The columns of the 1999 file, and the labels the printed tables give the same lists.
FILE_LISTS = {
    "ADN": "ADN",
    "CONDEPA": "CONDEPA-MP",
    "FRI": "FRI",
    "FSB": "FSB",
    "KDN": "KND",
    "MASU": "MAS-U",
    "MBL": "MBL",
    "MIR": "MIR-NM",
    "MNR": "MNR",
    "MPP": "MPP",
    "MRTKL": "MRTKL",
    "MSM": "MSM",
    "NFR": "NFR",
    "PCB": "PCB",
    "PDC": "PDC",
    "PS": "PS",
    "UCS": "UCS",
    "VR-9": "VR9",
}
# The capital cities the early tables report, as the department and the name the polling-table file gives them.
CAPITALS = {
    "Sucre": ("Chuquisaca", "Sucre"),
    "La Paz": ("La Paz", "Nuestra Señora de La Paz"),
    "El Alto": ("La Paz", "El Alto"),
    "Cochabamba": ("Cochabamba", "Cochabamba"),
    "Oruro": ("Oruro", "Oruro"),
    "Potosí": ("Potosí", "Potosí"),
    "Tarija": ("Tarija", "Tarija"),
    "Santa Cruz de la Sierra": ("Santa Cruz", "Santa Cruz de la Sierra"),
    "Trinidad": ("Beni", "Trinidad"),
    "Cobija": ("Pando", "Cobija"),
}
# A heading the seat table misprints, and the list it names.
HEADING_TYPOS = {1995: {"JCS": "UCS"}}
OTHERS = {"OTROS"}
NATIONAL = "Bolivia"


def cached(source: str, url: str) -> raw.Artifact:
    """A cached file by its address, whether it was fetched directly or as an archived copy of that address."""
    held = raw.keys(source)
    if url in held:
        return raw.cached(source, url)
    for key in sorted(held):
        if key.endswith(f"/{url}"):
            return raw.cached(source, key)
    raise LookupError(f"{url} is not in the {source} raw cache; run `ea fetch {source}` first")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def fetch() -> None:
    for url in TOMO_III.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, FILE_1999, suffix=".csv")
    for url in (ADM1, ADM3):
        raw.fetch(SHAPES, url, suffix=".geojson")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/municipal-{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# --- what the sources hold --------------------------------------------------------------------------------------


@functools.cache
def votes_table(year: int) -> tuple[atlas.Table, dict[str, list[str]]]:
    return atlas.vote_tables(SOURCE, cached(SOURCE, TOMO_III[2]).url, year)


@functools.cache
def seats_table(year: int) -> atlas.Table:
    votes, headings = votes_table(year)
    return atlas.seat_tables(
        SOURCE, cached(SOURCE, TOMO_III[2]).url, year, votes, headings, HEADING_TYPOS.get(year, {})
    )


@functools.cache
def file_text() -> str:
    return cached(SOURCE, FILE_1999).path.read_text(encoding="utf-8")


@functools.cache
def atlas_places() -> dict[tuple[str, str], Place]:
    names: dict[str, list[str]] = {}
    for year in (1995, 1999, 2004):
        for department, rows in votes_table(year)[0].items():
            for label, _ in rows[:-1]:
                if label not in names.setdefault(department, []):
                    names[department].append(label)
    found = places.resolve(names, places.file_municipalities(file_text()))
    departments, municipalities = places.read_shapes(
        cached(SHAPES, ADM1).path.read_text(encoding="utf-8"), cached(SHAPES, ADM3).path.read_text(encoding="utf-8")
    )
    del departments
    places.match_shapes(found, municipalities)
    return found


def all_places() -> list[Place]:
    return sorted({id(p): p for p in atlas_places().values()}.values(), key=lambda p: (p.department, p.name))


def place_of(department: str, name: str) -> Place:
    found = atlas_places()
    if (department, name) in found:
        return found[(department, name)]
    by_name = {(d, fold(n)): p for (d, n), p in found.items()}
    return by_name[(department, fold(name))]


# --- geography ----------------------------------------------------------------------------------------------------
ATLAS_SOURCE = key("source", "atlas")
SHAPE_SOURCE = key("source", "geoboundaries")
DEPARTMENT_SCHEME, MUNICIPALITY_SCHEME, TABLE_SCHEME = "bo.departamento", "bo.municipio", "bo.mesa"
BOUNDARY_YEAR = date(2015, 1, 1)


def _outline(p: Package, id: str, shape: shapely.Geometry) -> None:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    wgs = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
        label_point=shapely.to_wkb(shapely.set_srid(shapely.point_on_surface(wgs), 4326), hex=True, include_srid=True),
        quality="official",
        source_id=SHAPE_SOURCE,
    )


def sources(p: Package) -> None:
    p.add(
        "source",
        id=ATLAS_SOURCE,
        kind="official_body",
        publisher="Órgano Electoral Plurinacional de Bolivia",
        title="Atlas Electoral de Bolivia, Tomo III: Elecciones municipales, 1985 a 2010, and the Atlas's "
        "per-process downloads",
        url=INDEX,
        attribution="Órgano Electoral Plurinacional, Atlas Electoral de Bolivia",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bolivia's departments and the municipalities that held council elections between 1985 and 2004, with "
        "GeoBolivia's public-domain boundaries",
    )
    p.external.add(BOLIVIA)
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="official_body",
        publisher="GeoBolivia (Infraestructura de Datos Espaciales del Estado Plurinacional de Bolivia), distributed "
        "by geoBoundaries",
        title="Departmental and municipal boundaries of Bolivia, 2015 (geoBoundaries gbOpen, simplified)",
        url="https://www.geoboundaries.org/api/current/gbOpen/BOL/ADM3/",
        license="Public domain (GeoBolivia)",
        attribution="GeoBolivia; geoBoundaries gbOpen",
    )
    for url in (ADM1, ADM3):
        p.artifact(cached(SHAPES, url), SHAPE_SOURCE)
    for code, rank, kind, noun in (
        (DEPARTMENT_SCHEME, 1, "administrative", "department"),
        (MUNICIPALITY_SCHEME, 2, "administrative", "municipality"),
        (TABLE_SCHEME, 3, "precinct", "polling table"),
    ):
        p.add("scheme", code=code, polity_id=BOLIVIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for scheme in (DEPARTMENT_SCHEME, MUNICIPALITY_SCHEME):
        p.add(
            "boundary_set",
            id=key("boundary_set", scheme, 2015),
            scheme_code=scheme,
            vintage="2015",
            as_of=BOUNDARY_YEAR,
        )
    departments, _ = places.read_shapes(
        cached(SHAPES, ADM1).path.read_text(encoding="utf-8"), cached(SHAPES, ADM3).path.read_text(encoding="utf-8")
    )
    for code, name in places.DEPARTMENT_CODES.items():
        id = area(DEPARTMENT_SCHEME, code)
        p.add("area", id=id, scheme_code=DEPARTMENT_SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "es", "official")
        _outline(p, id, departments[name])
    for place in all_places():
        id = area(MUNICIPALITY_SCHEME, place.key)
        p.add("area", id=id, scheme_code=MUNICIPALITY_SCHEME)
        if place.code:
            p.add("area_code", area_id=id, code=place.code)
        p.add("area_parent", area_id=id, parent_id=area(DEPARTMENT_SCHEME, places.CODE_OF[place.department]))
        p.name(id, place.name, "es", "official")
        for other in sorted(place.names - {place.name}):
            p.name(id, other, "es", "official", False)
        if place.shape is not None:
            _outline(p, id, place.shape)
    return p


# --- registry -----------------------------------------------------------------------------------------------------
BODY = key("body", "council")
QUOTA_METHOD = (
    "Council seats were divided by the simple quotient ('cifra repartidora'): the valid votes of the municipality "
    "divided by the number of council members. Ley 716 of 13 February 1985 set the rules, and Ley 1113 of 19 "
    "October 1989 made the councils odd in size: 13 members in departmental capitals, 11 in cities of more than "
    "100,000, 7 in provincial capitals and towns of more than 10,000, and 5 in the other sections."
)
SAINTE_LAGUE_METHOD = (
    "Ley 1246 of 5 July 1991 set the Sainte-Laguë method with odd divisors (1, 3, 5, 7, ...) for council seats. "
    "Ley 1527 of 22 December 1993 gave the first list a majority of each council, but the Atlas records that it "
    "was not applied. Under Ley 1551 of 1994 councils have at most 11 members."
)
DHONDT_METHOD = (
    "The Código Electoral (Ley 1984 of 5 June 1999) divides each list's votes by the natural divisors (1, 2, 3, "
    "...) and awards council seats to the highest quotients (D'Hondt). The same method served in 2004, when "
    "citizen groups and indigenous peoples could run beside the parties for the first time."
)
PERIODS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    ("quota", date(1985, 1, 1), date(1991, 7, 5), {"formula": "hare"}, QUOTA_METHOD),
    ("sainte_lague", date(1991, 7, 5), date(1999, 6, 5), {"formula": "sainte_lague"}, SAINTE_LAGUE_METHOD),
    ("dhondt", date(1999, 6, 5), None, {"formula": "dhondt"}, DHONDT_METHOD),
]


def system_of(day: date) -> str:
    name = next(n for n, since, until, _, _ in PERIODS if since <= day and (until is None or day < until))
    return key("electoral_system", name)


def lists() -> dict[str, str]:
    """Every list label the tables print, keyed by its folded label."""
    labels: dict[str, str] = {}
    for year in DAYS:
        for council in councils(year):
            for label in council.votes:
                if label not in OTHERS:
                    labels.setdefault(fold(label), label)
    return labels


def party(label: str) -> str:
    return key("party", fold(label))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bolivia's municipal councils, their electoral systems, and the lists that ran in the 1985 to 2004 council "
        "elections, from the Atlas Electoral",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BOLIVIA)
    sources(p)
    for url in TOMO_III.values():
        p.artifact(cached(SOURCE, url), ATLAS_SOURCE)
    p.artifact(cached(SOURCE, FILE_1999), ATLAS_SOURCE)
    p.add("body", id=BODY, polity_id=BOLIVIA, kind="council")
    p.name(BODY, "Municipal council", "en", "official")
    p.name(BODY, "Concejo Municipal", "es", "native", False)
    for name, since, until, rules, method in PERIODS:
        id = key("electoral_system", name)
        p.add(
            "electoral_system", id=id, polity_id=BOLIVIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for text in sorted(lists().values()):
        id = party(text)
        p.add("party", id=id, kind="party", polity_id=BOLIVIA)
        p.name(id, text, "es", "abbreviation")
    return p


# --- council results ----------------------------------------------------------------------------------------------
@dataclass
class Mesa:
    code: str
    votes: dict[str, int]
    accounts: dict[int, int]


@dataclass
class Council:
    place: Place
    seats: int | None
    votes: dict[str, int | None]  # by printed list label; None where the table leaves the cell empty
    accounts: dict[int, int | None]
    won: dict[str, int] = field(default_factory=dict)
    mesas: list[Mesa] = field(default_factory=list)
    printed_votes: dict[str, int | None] = field(default_factory=dict)  # the municipal figures Tomo III prints
    printed_accounts: dict[int, int | None] = field(default_factory=dict)


@functools.cache
def early(year: int, subject: str) -> dict[str, dict[str, int | None]]:
    columns = atlas.CAPITALS_WITH_EL_ALTO if year >= 1989 else atlas.CAPITALS
    return atlas.grid(SOURCE, cached(SOURCE, TOMO_III[1]).url, year, subject, columns)


def _is_total(label: str) -> bool:
    return label.lower() == "total"


def align(seats: list[str], voted: list[str]) -> dict[str, str]:
    """Pair the municipalities of the seat table with those of the vote table, by name and then by order. A
    municipality the seat table leaves out (Belén de Urmiri in 1995) has no seats here."""
    names = {fold(label): label for label in voted}
    pairs = {label: names[fold(label)] for label in seats if fold(label) in names}
    left_seats = [label for label in seats if label not in pairs]
    left_votes = [label for label in voted if label not in pairs.values()]
    if left_seats and len(left_seats) != len(left_votes):
        raise ValueError(f"{len(left_seats)} seat rows and {len(left_votes)} vote rows are left unpaired")
    return {**pairs, **dict(zip(left_seats, left_votes, strict=False))}


def printed(year: int) -> list[Council]:
    """Councils of a year from the Atlas's municipal tables: 1995 and 2004 in full, and 1999's printed figures."""
    votes, _ = votes_table(year)
    seats = seats_table(year)
    out: list[Council] = []
    for department in atlas.DEPARTMENTS:
        voted = votes[department][:-1]
        seated = [(label, cells) for label, cells in seats[department][:-1]]
        paired = align([label for label, _ in seated], [label for label, _ in voted])
        won_by = {paired[label]: cells for label, cells in seated}
        for label, cells in voted:
            drawn = {name: value for name, value in cells.items() if name not in ACCOUNTS}
            accounts = {ACCOUNTS[name]: value for name, value in cells.items() if name in ACCOUNTS}
            seat_cells = won_by.get(label, {})
            size = next((v for k, v in seat_cells.items() if _is_total(k)), None)
            won: dict[str, int] = {}
            for name, value in seat_cells.items():
                if _is_total(name) or not value:
                    continue
                match = next((x for x in drawn if fold(x) == fold(name)), None)
                if match is None:
                    # the seat table names a list the vote table does not (MAR in Pando, 2004)
                    match = name
                    drawn[name] = None
                won[match] = value
            out.append(Council(place_of(department, label), size, drawn, accounts, won))
    return out


def counted_1999() -> list[Council]:
    """The 1999 councils: the polling tables of the digital Atlas, with the printed municipal figures as declared."""
    by_code = {place.code: place for place in all_places() if place.code}
    printed_by_code = {council.place.code: council for council in printed(1999)}
    tables: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in places.polling_table_rows(file_text()):
        tables[row["Codigo MUNICIPIO"]].append(row)
    out: list[Council] = []
    for code, rows in sorted(tables.items()):
        place = by_code[code]
        printed_council = printed_by_code[code]
        ran = dict(printed_council.votes)
        mesas: list[Mesa] = []
        for row in rows:
            drawn = {FILE_LISTS[name]: int(row[name] or 0) for name in FILE_LISTS}
            for label, value in drawn.items():
                if value and ran.get(label) is None:
                    ran[label] = 0  # a list with votes the printed table leaves blank
            accounts = {measure: int(row[name] or 0) for name, measure in FILE_ACCOUNTS.items()}
            mesas.append(Mesa(row["Codigo RECINTO"], drawn, accounts))
        out.append(
            Council(
                place,
                printed_council.seats,
                ran,
                printed_council.accounts,
                printed_council.won,
                mesas,
                printed_council.votes,
                printed_council.accounts,
            )
        )
    return out


def capitals(year: int) -> list[Council]:
    """The councils of the capital cities that the early tables report, with the seats the Atlas assigns."""
    voted, seated = early(year, "capitals"), early(year, "seats")
    columns = atlas.CAPITALS_WITH_EL_ALTO if year >= 1989 else atlas.CAPITALS
    out: list[Council] = []
    for column in columns:
        department, name = CAPITALS[column]
        drawn = {label: cells[column] for label, cells in voted.items() if label not in ACCOUNTS}
        accounts = {measure: voted[label][column] for label, measure in ACCOUNTS.items() if label in voted}
        won = {label: count for label, cells in seated.items() if not _is_total(label) and (count := cells[column])}
        for label in won:
            if drawn.get(label) is None:
                raise ValueError(f"{year} {column}: seats for {label} without votes")
        out.append(Council(place_of(department, name), seated["Total"][column], drawn, accounts, won))
    return out


@functools.cache
def councils(year: int) -> list[Council]:
    if year == 1999:
        return counted_1999()
    if year in (1995, 2004):
        return printed(year)
    return capitals(year)


# --- elections ----------------------------------------------------------------------------------------------------
METHOD = {
    "early": (
        "Official results of the council election in the capital city, as printed in the Atlas Electoral de Bolivia, "
        "Tomo III, with the council seats the Atlas assigns to each list. The Atlas prints no municipal results for "
        "the other municipalities before 1995, only totals by department, so the councils of the capital cities are "
        "the units held here. Votes are valid votes for each list; blank and null votes and the ballots cast are the "
        "Atlas's own counts."
    ),
    "printed": (
        "Official results of the council election in each municipality, as printed in the Atlas Electoral de "
        "Bolivia, Tomo III, with the council seats the Atlas assigns to each list. A list has a value only where the "
        "table prints one; an empty cell means the list did not run in that municipality. Votes are valid votes for "
        "each list; blank and null votes, the ballots cast, and the registered voters are the Atlas's own counts."
    ),
    "counted": (
        "Counted at each polling table (mesa) as published in the digital Atlas Electoral's votos_totales file, with "
        "the council seats from Atlas Electoral de Bolivia, Tomo III. Registered voters are the roll of the table, "
        "ballots cast are the votes issued, and valid votes are the votes for the lists; blank and null votes are "
        "counted apart. The municipal figures printed in Tomo III are stored as the declared totals."
    ),
}


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/municipal-{year}",
        SOURCE,
        f"The {year} municipal council elections in Bolivia"
        + (" by polling table" if year == 1999 else " in the capital cities" if year < 1995 else " by municipality")
        + ", from the Atlas Electoral",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BOLIVIA)
    p.uses(cached(SOURCE, TOMO_III[VOLUME_OF[year]]))
    if year == 1999:
        p.uses(cached(SOURCE, FILE_1999))
    method = METHOD["counted" if year == 1999 else "early" if year < 1995 else "printed"]
    election_id = key("election", year)
    p.add("election", id=election_id, polity_id=BOLIVIA, kind="local", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{year} Bolivian municipal elections", "en", "official")
    p.name(election_id, f"Elecciones Municipales {year}", "es", "native", False)
    base = f"bo/{day}-local"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    p.add("note", id=key("note", election_id), entity_id=election_id, kind="methodology", body_md=method, lang="en")
    system = system_of(day)
    for council in councils(year):
        place = council.place
        area_id = area(MUNICIPALITY_SCHEME, place.key)
        tag = f"{year}:{place.key}"
        contest, result_set, unit = (key(part, tag) for part in ("contest", "result_set", "unit"))
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=area_id,
            magnitude=council.seats,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"Municipal council of {place.name}", "en", "official")
        p.name(contest, f"Concejo Municipal de {place.name}", "es", "native", False)
        slug = f"{base}/{changeset.slug(place.department)}/{changeset.slug(place.name)}"
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ATLAS_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        options: dict[str, str] = {}
        for position, (label, value) in enumerate(council.votes.items(), start=1):
            if value is None and label not in council.won:
                continue
            option = options[label] = key("option", tag, fold(label))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if label in OTHERS else "list",
                party_id=None if label in OTHERS else party(label),
                ballot_position=position,
                ballot_label=label,
            )
            if label in council.won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=council.won[label])

        def put(unit_id: str, option: str | None, measure: int, value: int | None, rs: str = result_set) -> None:
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit_id,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )

        if council.mesas:
            for label, option in options.items():
                if council.printed_votes.get(label) is not None:
                    p.add(
                        "declared",
                        result_set_id=result_set,
                        option_id=option,
                        measure=VOTES,
                        value=council.printed_votes[label],
                    )
            for measure, value in council.printed_accounts.items():
                if value is not None:
                    p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            for mesa in council.mesas:
                mesa_unit = key("unit", f"{day}:{mesa.code}")
                p.add(
                    "reporting_unit",
                    id=mesa_unit,
                    polity_id=BOLIVIA,
                    scheme_code=TABLE_SCHEME,
                    code=f"{day}:{mesa.code}",
                    area_relation="within",
                    area_id=area_id,
                    kind="standard",
                )
                for measure, value in mesa.accounts.items():
                    put(mesa_unit, None, measure, value)
                for label, option in options.items():
                    put(mesa_unit, option, VOTES, mesa.votes.get(label, 0))
        else:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BOLIVIA,
                scheme_code=MUNICIPALITY_SCHEME,
                code=f"{day}:{place.key}",
                area_relation="equals",
                area_id=area_id,
                kind="standard",
            )
            for measure, value in council.accounts.items():
                put(unit, None, measure, value)
            for label, option in options.items():
                put(unit, option, VOTES, council.votes[label])
    for code, reason in ACCEPTED.get(year, {}).items():
        p.accept(code, reason)
    return p


ACCEPTED: dict[int, dict[str, str]] = {
    1991: {
        "accounting.votes_valid": "The Atlas prints list votes for La Paz (257,459) and Cochabamba (80,823) that do "
        "not add up to the printed valid votes of the two capitals (268,286 and 116,634); the figures are stored as "
        "printed.",
        "accounting.ballots_sum": "The Atlas prints 17,771 ballots cast for Santa Cruz de la Sierra against 171,050 "
        "valid, 3,117 blank, and 3,543 null votes, which add up to 177,710; the figure is stored as printed.",
    },
    1993: {
        "accounting.votes_valid": "The Atlas prints 11,389 valid votes for El Alto where its lists add up to "
        "113,890; the figure is stored as printed.",
        "accounting.ballots_sum": "The Atlas prints 121,897 ballots cast for El Alto against 11,389 valid, 5,832 "
        "null, and 2,175 blank votes, because of the same misprint in its valid votes; the figures are stored as "
        "printed.",
    },
    1995: {
        "accounting.ballots_sum": "The Atlas prints 1,826 ballots cast for Quillacollo against 17,485 valid, 267 "
        "blank, and 508 null votes, which add up to 18,260; the figure is stored as printed."
    },
    1999: {
        "accounting.turnout": "In the digital Atlas's file 40 polling tables report more ballots cast than "
        "registered voters; the file's figures are stored as published.",
        "accounting.declared": "The registered voters of 14 municipalities (Laja and 13 in Potosí) differ between the "
        "digital Atlas's polling tables and the municipal figures printed in Tomo III, by 3 to 710 voters; every "
        "list's votes and every other count agree, and the national total Tomo III prints (3,573,734) equals the "
        "polling tables' sum, so the printed municipal rows carry the misprints. The polling-table counts are stored "
        "and the printed figures are stored as declared.",
    },
}


def compare() -> list[str]:
    """Each election's loaded totals against the national totals the Atlas prints, as lines of text."""
    out: list[str] = []
    for year in DAYS:
        url = cached(SOURCE, TOMO_III[VOLUME_OF[year]]).url
        printed_national = atlas.grid(SOURCE, url, year, "dept", atlas.DEPARTMENT_COLUMNS)
        if year >= 1995:
            sums: dict[str, int] = defaultdict(int)
            for council in councils(year):
                for label, value in council.votes.items():
                    sums[label] += value or 0
                for measure, value in council.accounts.items():
                    sums[{v: k for k, v in ACCOUNTS.items()}[measure]] += value or 0
            names = {fold(label): label for label in printed_national}
            different = {
                label: (value, printed_national[names[fold(label)]][NATIONAL])
                for label, value in sums.items()
                if fold(label) in names and printed_national[names[fold(label)]][NATIONAL] != value
            }
            missing = sorted(label for label in sums if fold(label) not in names and sums[label])
            out.append(
                f"{year}: {len(sums)} figures summed over {len(councils(year))} councils; "
                f"differences {different}; not in national table {missing}"
            )
        else:
            departments = atlas.DEPARTMENTS
            different = {}
            for label, cells in printed_national.items():
                total = sum(cells[d] or 0 for d in departments)
                if total != cells[NATIONAL]:
                    different[label] = (total, cells[NATIONAL])
            out.append(f"{year}: national column against the nine department columns: differences {different}")
    return out
