"""The City of Buenos Aires's elections of the Head of Government, the Legislature, and the Community Boards from
1996 to 2013, from the historical tables of the City's statistics office (Instituto de Estadística y Censos).

The office publishes one spreadsheet per series, with one sheet per election. Up to 2007 the units are the 28
electoral districts (circunscripciones electorales) of the City, plus the foreign residents' tables in 1996 and 2000;
from 2009 they are the 15 comunas. The later elections, from 2015, come from the electoral authority itself in
`ar.caba`, whose City, comunas, offices, and bodies these packages reuse.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel

from ea import package, raw
from ea.package import Package
from ea.sources.ar import caba

SOURCE = "ar.idecba"
SITE = "https://www.estadisticaciudad.gob.ar/eyc"
UPLOADS = f"{SITE}/wp-content/uploads/"
ARGENTINA = caba.ARGENTINA
CE_SCHEME = "ar.caba_circunscripcion"
CIRCUNSCRIPCIONES = range(1, 29)
FOREIGN = "extranjeros"

# The spreadsheets, each named by its file in the office's data bank.
FILES = {
    "AX31": "2018/04/PC_EL_AX31.xls",  # Head of Government 1996 and 2000, by electoral district and foreign tables
    "AX06": "2018/04/PC_EL_AX06.xls",  # Head of Government, first round 2003 and 2007, by electoral district
    "AX11": "2018/04/PC_EL_AX11.xls",  # Head of Government, second round 2003 and 2007, by electoral district
    "AX13": "2018/04/PC_EL_AX13.xls",  # Legislature 2003, 2005, and 2007, by electoral district
    "AX12": "2021/05/PC_EL_AX12.xlsx",  # Legislature 2009 to 2025, by comuna
    "AX20": "2019/05/PC_EL_AX20.xlsx",  # Head of Government, first round 2011 to 2023, by comuna
    "AX20_2v": "2015/05/PC_EL_AX20_2v.xls",  # Head of Government, second round 2011 and 2015, by comuna
    "AX19": "2023/12/PC_EL_AX19.xlsx",  # Community Boards 2011 to 2023, by comuna
}
PAGES = {
    "AX31": "banco-datos-historico/votos-emitidos-para-jefe-y-vicejefe-de-gobierno-por-clasificacion-del-voto-y-partido-politico-o-alianza-segun-circunscripcion-electoral-y-mesas-para-extranjeros-ciudad-de-buenos-aires-anos-1996-2000/",
    "AX06": "banco-datos-historico/votos-emitidos-para-jefe-y-vicejefe-de-gobierno-por-circunscripcion-electoral-segun-clasificacion-del-voto-y-partido-politico-o-alianza-primera-vuelta-ciudad-de-buenos-aires-anos-2003-2007/",
    "AX11": "banco-datos-historico/votos-emitidos-para-jefe-y-vicejefe-de-gobierno-por-clasificacion-del-voto-y-partido-politico-o-alianza-segun-circunscripcion-electoral-segunda-vuelta-ciudad-de-buenos-aires-anos-2003-2007/",
    "AX13": "banco-datos-historico/votos-emitidos-para-legisladores-de-la-ciudad-por-circunscripcion-electoral-segun-clasificacion-del-voto-y-partido-politico-o-alianza-ciudad-de-buenos-aires-anos-20032007/",
    "AX12": "banco-datos/votos-emitidos-para-legisladores-de-la-ciudad-por-comuna-segun-clasificacion-del-voto-y-partido-politico-o-alianza-ciudad-de-buenos-aires-anos-20112015/",
    "AX20": "banco-datos/votos-emitidos-para-jefe-y-vicejefe-de-gobierno-por-comuna-segun-clasificacion-del-voto-y-partido-politico-o-alianza-primera-vuelta-ciudad-de-buenos-aires-ano-2011-2015/",
    "AX20_2v": "banco-datos/votos-emitidos-para-jefe-y-vicejefe-de-gobierno-por-clasificacion-del-voto-y-partido-politico-o-alianza-segun-comuna-segunda-vuelta-ciudad-de-buenos-aires-anos-20112015/",
    "AX19": "banco-datos/votos-emitidos-para-junta-comunal-de-la-ciudad-por-comuna-segun-clasificacion-del-voto-y-partido-politico-o-alianza-ciudad-de-buenos-aires-anos-2011-2023/",
}


def url(name: str) -> str:
    return UPLOADS + FILES[name]


@dataclass(frozen=True)
class Held:
    key: str
    day: date
    kind: str
    tables: tuple[tuple[str, str, str], ...]  # (cargo, spreadsheet, sheet)


HELD = [
    Held("1996-general", date(1996, 6, 30), "general", (("head", "AX31", "1996"),)),
    Held("2000-general", date(2000, 5, 7), "general", (("head", "AX31", "2000"),)),
    Held("2003-general", date(2003, 8, 24), "general", (("head", "AX06", "2003"), ("legislature", "AX13", "2003"))),
    Held("2003-runoff", date(2003, 9, 14), "runoff", (("head", "AX11", "2003"),)),
    Held("2005-general", date(2005, 10, 23), "general", (("legislature", "AX13", "2005"),)),
    Held("2007-general", date(2007, 6, 3), "general", (("head", "AX06", "2007"), ("legislature", "AX13", "2007"))),
    Held("2007-runoff", date(2007, 6, 24), "runoff", (("head", "AX11", "2007"),)),
    Held("2009-general", date(2009, 6, 28), "general", (("legislature", "AX12", "2009"),)),
    Held(
        "2011-general",
        date(2011, 7, 10),
        "general",
        (("head", "AX20", "2011"), ("legislature", "AX12", "2011"), ("junta", "AX19", "2011")),
    ),
    Held("2011-runoff", date(2011, 7, 31), "runoff", (("head", "AX20_2v", "2011"),)),
    Held("2013-general", date(2013, 10, 27), "general", (("legislature", "AX12", "2013"),)),
]
FIRST_ROUND = {"2003-runoff": "2003-general", "2007-runoff": "2007-general", "2011-runoff": "2011-general"}
# The 2011 second-round table names the tickets by their candidates; these are the lists that ran them in the first.
TICKETS = {
    "Fórmula Mauricio MACRI - María Eugenia VIDAL": "Pro - Propuesta Republicana",
    "Fórmula Daniel FILMUS - Carlos TOMADA": "Frente para la Victoria",
}


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, url(name), suffix="." + FILES[name].rsplit(".", 1)[1])


# Parsing -----------------------------------------------------------------------------------------------------------


@dataclass
class Table:
    units: list[str]  # "1".."28" (electoral districts), "1".."15" (comunas), and "extranjeros"
    accounting: dict[str, dict[str, int]]  # label (cast, valid, blank, null) -> unit -> value
    parties: list[tuple[str, dict[str, int]]]  # printed name -> unit -> votes, only where the table prints a number
    totals: dict[str, int]  # the table's own City totals, by label or party name
    note: str
    derived: set[tuple[str, str]] = field(default_factory=set)  # (label, unit) figures computed from the others
    shifted: set[str] = field(default_factory=set)  # units whose blank and null ballots the table prints a row up
    misprinted: dict[str, int] = field(default_factory=dict)  # lists whose printed City total their units contradict


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def _number(value: object) -> int | None:
    text = _text(value)
    if text in ("", "-"):
        return None
    return int(float(text))


def _label(text: str, column: int) -> str | None:
    folded = fold(text)
    if column == 1 or folded == "total":
        return "cast" if column == 1 else "valid"
    if folded.startswith(("voto positivo", "votos positivos")):
        return "valid"
    if folded.startswith(("voto en blanco", "votos en blanco")):
        return "blank"
    if folded.startswith(("voto nulo", "votos nulos")):
        return "null"
    return None


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"\s+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip().casefold()


def _unit(text: str) -> str | None:
    if text.isdigit():
        return text
    if fold(text).startswith("mesas para extranjeros"):
        return FOREIGN
    return None


def table(name: str, sheet: str) -> Table:
    reader = fastexcel.read_excel(raw.cached(SOURCE, url(name)).path)
    frame = reader.load_sheet_by_name(sheet, header_row=None).to_polars()
    grid = [list(row) for row in frame.iter_rows()]
    note = next(_text(row[0]) for row in grid if _text(row[0]).startswith("Nota"))
    accounting: dict[str, dict[str, int]] = {}
    parties: list[tuple[str, dict[str, int]]] = []
    totals: dict[str, int] = {}
    units: list[str] = []
    header = next(
        (r for r, row in enumerate(grid) if [_text(v) for v in row[2:] if _text(v)][:2] == ["1", "2"] and len(row) > 3),
        None,
    )
    if header is not None:
        # Parties in rows and units in columns, with the City total in the second column.
        columns = {c: _text(v) for c, v in enumerate(grid[header]) if c >= 2 and _text(v)}
        units = list(columns.values())
        for row in grid[header + 1 :]:
            text = _text(row[0])
            if not text:
                continue
            if text.startswith(("Nota", "Fuente")):
                break
            values = {unit: _number(row[c]) for c, unit in columns.items()}
            total = _number(row[1])
            label = "cast" if text == "Total" else _label(text, 2)
            if label is not None:
                accounting[label] = {u: v for u, v in values.items() if v is not None}
                if total is not None:
                    totals[label] = total
            else:
                found = {u: v for u, v in values.items() if v is not None}
                if found:
                    parties.append((text, found))
                    if total is not None:
                        totals[text] = total
    else:
        # Units in rows and parties in columns, with the City total in the first data row.
        first = next(r for r, row in enumerate(grid) if r > 0 and _text(row[0]) == "Total")
        labels: dict[int, str] = {}
        for c in range(1, len(grid[first])):
            above = [_text(grid[r][c]) for r in range(1, first) if _text(grid[r][c])]
            if above:
                labels[c] = above[-1]
        for row in grid[first:]:
            text = _text(row[0])
            unit = None if text == "Total" else _unit(text)
            if text != "Total" and unit is None:
                if text.startswith(("Nota", "Fuente")):
                    break
                continue
            if unit is not None:
                units.append(unit)
            for c, label in labels.items():
                value = _number(row[c])
                if value is None:
                    continue
                kind = _label(label, c)
                if unit is None:
                    totals[kind or label] = value
                elif kind is not None:
                    accounting.setdefault(kind, {})[unit] = value
                else:
                    found = next((votes for party, votes in parties if party == label), None)
                    if found is None:
                        parties.append((label, found := {}))
                    found[unit] = value
    return Table(units, accounting, parties, totals, note)


def verified(name: str, sheet: str) -> Table:
    """The table, after checking that its parties add up to its valid votes and its votes to the ballots cast in
    every unit, and that its units add up to its own City totals."""
    t = table(name, sheet)
    for unit in t.units:
        listed = sum(votes.get(unit, 0) for _, votes in t.parties)
        if unit not in t.accounting.setdefault("valid", {}):
            t.accounting["valid"][unit] = listed
            t.derived.add(("valid", unit))
        rest = t.accounting["cast"][unit] - listed - t.accounting["blank"][unit]
        if unit not in t.accounting["null"] and t.accounting["valid"][unit] == rest:
            # The 2007 Legislature table prints the blank and null ballots of districts 14 to 28 one row up, in the
            # rows of the positive votes and of the blank ballots, and leaves the null row empty there: the two add
            # up to what remains of the ballots cast, and only read this way do the units add up to the printed City
            # totals of blank and null ballots.
            t.accounting["null"][unit] = t.accounting["blank"][unit]
            t.accounting["blank"][unit] = t.accounting["valid"][unit]
            t.accounting["valid"][unit] = listed
            t.shifted.add(unit)
            t.derived.add(("valid", unit))
    problems: list[str] = []
    for unit in t.units:
        listed = sum(votes.get(unit, 0) for _, votes in t.parties)
        valid = t.accounting["valid"][unit]
        if listed != valid:
            problems.append(f"{unit}: lists {listed} != valid {valid}")
        cast = valid + t.accounting["blank"][unit] + t.accounting["null"][unit]
        if cast != t.accounting["cast"][unit]:
            problems.append(f"{unit}: valid+blank+null {cast} != cast {t.accounting['cast'][unit]}")
    for label, values in t.accounting.items():
        if label in t.totals and sum(values.values()) != t.totals[label]:
            problems.append(f"total {label}: units {sum(values.values())} != printed {t.totals[label]}")
    for label, values in t.parties:
        if label in t.totals and sum(values.values()) != t.totals[label]:
            # Every unit adds up and so do the City's valid votes, so the list's printed total is the one in error.
            t.misprinted[label] = t.totals.pop(label)
    if problems:
        raise ValueError(f"{name} {sheet}: " + "; ".join(problems))
    return t


# Identities --------------------------------------------------------------------------------------------------------


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def ce_area(number: int | str) -> str:
    return key("area", CE_SCHEME, number)


def alliance(name: str) -> bool:
    return fold(name).startswith(("alianza", "al.", "508 -"))


def party(name: str, year: int) -> str:
    name = TICKETS.get(name, name)
    return key("party", year, fold(name)) if alliance(name) else key("party", fold(name))


IDECBA_SOURCE = key("source", "idecba")
HEAD_1996_SYSTEM = key("electoral_system", "jefe-de-gobierno-1996")
PUBLISHER = "Instituto de Estadística y Censos de la Ciudad de Buenos Aires"
METHOD = (
    "Counted by the electoral authorities and published by the City's statistics office (Instituto de Estadística "
    "y Censos, formerly the Dirección General de Estadística y Censos) in the historical tables of its data bank. "
    "Valid votes are the votes for lists (the office's positive votes), and ballots cast are valid, blank, and null "
    "ballots together. A list for which the table prints a dash in a unit is not stored there."
)
GRAIN = {
    "ce": (
        " Each of the City's 28 electoral districts (circunscripciones electorales) is one reporting unit, and its "
        "figures include the tables of the foreign residents registered in it."
    ),
    "ce+foreign": (
        " Each of the City's 28 electoral districts (circunscripciones electorales) is one reporting unit, and the "
        "tables of the foreign residents are one more unit with no area."
    ),
    "comuna": " Each comuna is one reporting unit.",
}
NOTES = {
    "1996-general": (
        " The office's table credits the count to the Ministry of the Interior. Under Law 24.620, the first Head of "
        "Government was elected by a plurality of the votes."
    ),
    "2000-general": " The table credits the count to the Tribunal Superior de Justicia, through the Ministry of the "
    "Interior.",
}
EXCLUDED = (
    " The office's tables for this election leave out the votes of the prisoners' tables and of Argentines abroad, "
    "which the authority counted apart from the comunas."
)


def caveats(t: Table) -> str:
    text = ""
    if any(label == "valid" for label, _ in t.derived) and not t.shifted:
        text += " The table prints no valid total by unit, so the valid votes are the sum of the lists' and are flagged as derived."
    if t.shifted:
        text += (
            f" In electoral districts {min(t.shifted, key=int)} to {max(t.shifted, key=int)} the table prints the blank "
            "and null ballots one row up, in the rows of the positive votes and of the blank ballots, and leaves the "
            "null row empty; they are stored read that way, which is the only reading under which the districts add "
            "up to the printed City totals, and the valid votes there are the sum of the lists' and flagged as derived."
        )
    for label, printed in t.misprinted.items():
        text += (
            f" The table prints a City total of {printed:,} for {label}, which its comunas contradict while every comuna "
            "and the City's valid votes add up, so that list has no declared City total."
        )
    return text


def grain(held: Held) -> str:
    if held.day.year <= 2000:
        return "ce+foreign"
    return "ce" if held.day.year <= 2007 else "comuna"


# Packages ----------------------------------------------------------------------------------------------------------


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The City of Buenos Aires's 28 electoral districts (circunscripciones electorales), the reporting units of its "
        "elections up to 2007",
        depends=[f"{caba.SOURCE}/geo"],
    )
    p.external.add(ARGENTINA)
    p.add("scheme", code=CE_SCHEME, polity_id=ARGENTINA, kind="electoral_district", rank=2, noun="electoral district")
    for number in CIRCUNSCRIPCIONES:
        id = ce_area(number)
        p.add("area", id=id, scheme_code=CE_SCHEME)
        p.add("area_code", area_id=id, code=str(number))
        p.add("area_parent", area_id=id, parent_id=caba.CITY)
        p.name(id, f"Circunscripción Electoral {number}", "es", "official")
        p.name(id, f"Electoral District {number}", "en", "official", False)
    return p


def _sheets() -> list[tuple[Held, str, Table]]:
    return [(held, cargo, verified(name, sheet)) for held in HELD for cargo, name, sheet in held.tables]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The City of Buenos Aires's statistics office as a source, the 1996 rules for the Head of Government, and the "
        "parties and alliances on the City's ballots from 1996 to 2013",
        depends=[f"{caba.SOURCE}/registry"],
    )
    p.external.add(ARGENTINA)
    p.add(
        "source",
        id=IDECBA_SOURCE,
        kind="official_body",
        publisher=PUBLISHER,
        title="Banco de datos: Participación ciudadana, elecciones",
        url=f"{SITE}/",
        license="Public information of the City of Buenos Aires, free reuse with the source cited (Ley 104)",
        license_url="https://www.buenosaires.gob.ar/",
        attribution=PUBLISHER,
    )
    for name in FILES:
        p.artifact(raw.cached(SOURCE, url(name)), IDECBA_SOURCE)
    p.add(
        "electoral_system",
        id=HEAD_1996_SYSTEM,
        polity_id=ARGENTINA,
        valid_from=date(1996, 1, 1),
        valid_to=date(1996, 10, 1),
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", HEAD_1996_SYSTEM),
        entity_id=HEAD_1996_SYSTEM,
        kind="methodology",
        body_md=(
            "The first Head of Government and Deputy Head, in 1996, were elected together on one ticket by a plurality "
            "of the votes, with the City as a single district, under Law 24.620, before the City's Constitution "
            "introduced the second round."
        ),
        lang="en",
    )
    seen: dict[str, tuple[str, bool]] = {}
    for held, _, t in reversed(_sheets()):
        for name, _ in t.parties:
            printed = TICKETS.get(name, name)
            seen.setdefault(party(name, held.day.year), (printed, alliance(printed)))
    for id, (name, bloc) in sorted(seen.items()):
        p.add("party", id=id, kind="bloc" if bloc else "party", polity_id=ARGENTINA)
        p.name(id, name, "es", "official")
    return p


DERIVED = 1  # result_value.flags: a figure the archive computed from the office's, not one the office prints


def _value(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int, flags: int = 0
) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        count_round=0,
        flags=flags,
        measure=measure,
        value=value,
    )


def _declared(p: Package, result_set: str, option: str | None, measure: int, value: int) -> None:
    p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)


MEASURES = {"cast": 3, "valid": 4, "null": 5, "blank": 6}
ELECTION_NAMES = {
    "general": ("{year} City of Buenos Aires election", "Elecciones de la Ciudad de Buenos Aires {year}"),
    "runoff": ("{year} City of Buenos Aires runoff election", "Segunda vuelta de la Ciudad de Buenos Aires {year}"),
}


def election(name: str) -> Package:
    held = next(h for h in HELD if h.key == name)
    year = held.day.year
    first = FIRST_ROUND.get(held.key)
    where = grain(held)
    p = Package(
        f"{SOURCE}/{held.key}",
        SOURCE,
        f"The City of Buenos Aires's {held.day:%-d %B %Y} {held.kind} by "
        + ("electoral district" if where.startswith("ce") else "comuna"),
        depends=[
            f"{caba.SOURCE}/geo",
            f"{caba.SOURCE}/registry",
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            *([f"{SOURCE}/{first}"] if first else []),
        ],
    )
    p.external.add(ARGENTINA)
    for _, file, _ in held.tables:
        p.uses(raw.cached(SOURCE, url(file)))
    election_id = key("election", held.key)
    path = f"ar/caba/{held.day}-{held.kind}"
    p.add(
        "election",
        id=election_id,
        polity_id=ARGENTINA,
        kind=held.kind,
        date_from=held.day,
        date_to=held.day,
        date_precision="day",
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    english, spanish = ELECTION_NAMES[held.kind]
    p.name(election_id, english.format(year=year), "en", "official")
    p.name(election_id, spanish.format(year=year), "es", "native", False)
    tables = {cargo: verified(file, sheet) for cargo, file, sheet in held.tables}
    units: dict[str, str] = {}
    for code in sorted({u for t in tables.values() for u in t.units}, key=lambda u: (not u.isdigit(), u.zfill(3))):
        unit = units[code] = key("unit", held.day, code)
        if code == FOREIGN:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ARGENTINA,
                scheme_code=CE_SCHEME,
                code=f"{held.day}:{code}",
                area_relation="unlocated",
                kind="aggregate",
            )
            continue
        comunas = not where.startswith("ce")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ARGENTINA,
            scheme_code="ar.caba_comuna" if comunas else CE_SCHEME,
            code=f"{held.day}:{code}",
            area_relation="equals",
            area_id=caba.area("ar.caba_comuna", code) if comunas else ce_area(code),
            kind="aggregate",
        )
    method = METHOD + GRAIN[where] + NOTES.get(held.key, "") + (EXCLUDED if where == "comuna" else "")
    for cargo, t in tables.items():
        scopes = [int(u) for u in t.units] if cargo == "junta" else [0]
        for scope in scopes:
            tag = cargo if scope == 0 else f"junta:{scope}"
            contest = key("contest", held.key, tag)
            if cargo == "head":
                en, es = caba.PLACE_NAMES["head"]
                slug, kind, body, office, magnitude = "chief-of-government", "office", None, caba.HEAD, 1
                system = HEAD_1996_SYSTEM if year == 1996 else caba.HEAD_SYSTEM
            elif cargo == "legislature":
                en, es = caba.PLACE_NAMES["legislature"]
                slug, kind, body, office, magnitude = "legislature", "list_allocation", caba.LEGISLATURE, None, 30
                system = caba.LEGISLATURE_SYSTEM
            else:
                en, es = f"Comuna {scope} Community Board", f"Junta Comunal de la Comuna {scope}"
                slug, kind, body, office, magnitude = (
                    f"comuna-{scope}-community-board",
                    "list_allocation",
                    caba.junta(scope),
                    None,
                    7,
                )
                system = caba.JUNTA_SYSTEM
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind=kind,
                office_id=office,
                body_id=body,
                district_area_id=caba.CITY if scope == 0 else caba.area("ar.caba_comuna", str(scope)),
                magnitude=magnitude,
                round_number=2 if held.kind == "runoff" else 1,
                previous_round_id=key("contest", first, tag) if first else None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            p.name(contest, en, "en", "official")
            p.name(contest, es, "es", "native", False)
            p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
            result_set = key("result_set", held.key, tag)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=IDECBA_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=method + caveats(t) + f" The office's note to the table reads: “{t.note}”",
                lang="en",
            )
            here = [str(scope)] if scope else t.units
            listed = [(n, votes) for n, votes in t.parties if any(u in votes for u in here)]
            options: dict[str, str] = {}
            for position, (printed, _) in enumerate(listed, 1):
                option = options[printed] = key("option", held.key, tag, fold(printed))
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="ticket" if cargo == "head" else "list",
                    party_id=party(printed, year),
                    ballot_position=None,
                    ballot_label=printed,
                )
            for code in here:
                unit = units[code]
                for label, values in t.accounting.items():
                    flags = DERIVED if (label, code) in t.derived else 0
                    _value(p, result_set, unit, None, MEASURES[label], values[code], flags)
                for printed, votes in listed:
                    if code in votes:
                        _value(p, result_set, unit, options[printed], 1, votes[code])
            if scope == 0:
                for label, measure in MEASURES.items():
                    if label in t.totals:
                        _declared(p, result_set, None, measure, t.totals[label])
                for printed, votes in listed:
                    _declared(p, result_set, options[printed], 1, t.totals.get(printed, sum(votes.values())))
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{held.key}" for held in HELD)]


def parse() -> None:
    geo().write()
    registry().write()
    for held in HELD:
        election(held.key).write()


def emit() -> None:
    package.run("load", packages())
