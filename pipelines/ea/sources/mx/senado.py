import hashlib
import html
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import package, raw
from ea.package import NAMESPACE, Draft, canonical
from ea.sources.mx import ine
from ea.sources.mx.ine import (
    ABROAD,
    ALLIANCES,
    BALLOTS,
    EARLY,
    INVALID,
    KEYS,
    NONVOTES,
    PARTIES,
    PRISON,
    REGISTERED,
    TRAILING,
    UNREGISTERED,
    VALID,
    VOTES,
    casilla_code,
    clean,
    number,
)
from ea.store import DATA, connect

SOURCE = "mx.senado"
RAW = ine.SOURCE
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

SICEEF_STATE = {
    1991: "Senadores1991",
    1994: "Senadores1994",
    2000: "SenadoresMR2000",
    2006: "SenadoresMR2006",
    2012: "SenadoresMR2012",
}
SICEEF_LIST = {1997: "SenadoresRP1997", 2000: "SenadoresRP2000", 2006: "SenadoresRP2006", 2012: "SenadoresRP2012"}
COMPUTOS = {
    2018: (ine.COMPUTOS[2018][0], "20180708_2130_CW_senadurias.zip", "senadurias.csv"),
    2024: (ine.COMPUTOS[2024][0], "20240608_2030_COMPUTOS_SEN.zip", "SEN_2024.csv"),
}
LIST_ACTAS = {"2018": {"4": "state", "5": "list"}, "2024": {"3EMR": "state", "3ERP": "list"}}
SHORT_TOTALS = {}
SEATS_PAGE = "http://siceef.ine.mx/camsenadores.html"
ACUERDOS = {
    2018: (
        "https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/175912/AnteproyectoAsignacionSen-21-08-2018-PUNTO5.pdf",
        (
            "Anteproyecto de acuerdo del Consejo General del INE por el que se efectúa el cómputo total, se declara la validez "
            "de la elección de senadores por el principio de representación proporcional y se asignan las senadurías para el "
            "periodo 2018-2024 (sesión de 23 de agosto de 2018, INE/CG1180/2018)"
        ),
    ),
    2024: (
        "https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/176369/CGex202408-23-ap-5.pdf",
        (
            "INE/CG2130/2024: acuerdo del Consejo General por el que se efectúa el cómputo total, se declara la validez de la "
            "elección de senadurías por el principio de representación proporcional y se asignan las senadurías para el "
            "periodo 2024-2030 (23 de agosto de 2024)"
        ),
    ),
}
# INE's document repository now refuses scripts (403), so each acuerdo is read from the Internet Archive's copy of
# its address; the source keeps INE's address.
ACUERDO_COPIES = {
    2018: "20250610042556",
    2024: "20240825114438",
}


KEY = f"{SOURCE}/all"

# The Senate's own rosters of the senators who took their seats, read from the Internet Archive's copies because
# senado.gob.mx answers scripts with a bot check. The LXIV Legislature's page for each state (captured in the first
# week of September 2018) prints each senator's principle of election; the LXVI Legislature's roster by
# parliamentary group (captured on 6 September 2024) prints each senator's state. The state codes are INE's.
SENATE = "Senado de la República"
ARCHIVED = "https://web.archive.org/web/{stamp}id_/{url}"
ROSTER_2018 = "http://www.senado.gob.mx/64/senadores/por_entidad_federativa/{slug}"
ROSTER_PAGES_2018 = {
    1: ("aguascalientes", "20180907120015"),
    2: ("baja_california", "20180907161803"),
    3: ("baja_california_sur", "20180906013223"),
    4: ("campeche", "20180907160550"),
    5: ("coahuila", "20180907160558"),
    6: ("colima", "20180907160453"),
    7: ("chiapas", "20180907124010"),
    8: ("chihuahua", "20180907161807"),
    9: ("ciudad_de_mexico", "20180907160553"),
    10: ("durango", "20180907160418"),
    11: ("guanajuato", "20180907160458"),
    12: ("guerrero", "20180907160423"),
    13: ("hidalgo", "20180905022943"),
    14: ("jalisco", "20180907161818"),
    15: ("mexico", "20180907160604"),
    16: ("michoacan", "20180907161822"),
    17: ("morelos", "20180907161828"),
    18: ("nayarit", "20180907161833"),
    19: ("nuevo_leon", "20180907160503"),
    20: ("oaxaca", "20180907160509"),
    21: ("puebla", "20180906013348"),
    22: ("queretaro", "20180907160609"),
    23: ("quintana_roo", "20180907161838"),
    24: ("san_luis_potosi", "20180907160614"),
    25: ("sinaloa", "20180907161843"),
    26: ("sonora", "20180907161848"),
    27: ("tabasco", "20180907160619"),
    28: ("tamaulipas", "20180907160428"),
    29: ("tlaxcala", "20180907160624"),
    30: ("veracruz", "20180907160629"),
    31: ("yucatan", "20180907160433"),
    32: ("zacatecas", "20180907160513"),
}
ROSTER_2024 = "https://www.senado.gob.mx/66/senadores/por_grupo_parlamentario"
ROSTER_STAMP_2024 = "20240906164727"
ROSTER_STATES_2024 = {
    "Coahuila de Zaragoza": 5,
    "Estado de México": 15,
    "Michoacán de Ocampo": 16,
    "Veracruz de Ignacio de la Llave": 30,
}
# The LVIII Legislature's roster by parliamentary group prints, for each senator, the principle of election and the
# state. The Internet Archive's earliest copy of each group's page, captured from November 2000 to August 2001; the
# Convergencia group's lists only a list senator. Each group's party, as INE's columns name it.
ROSTER_2000 = "http://www.senado.gob.mx:80/comunicacion/senadores/{group}.html"
GROUP_PAGES_2000 = {
    "pri": ("PRI", "20001109041100"),
    "pan": ("PAN", "20010124045800"),
    "prd": ("PRD", "20001211205200"),
    "pvem": ("PVEM", "20010825140022"),
    "pcd": ("MC", "20001211195400"),
}
STATES_2000 = {"DISTRITO_FEDERAL": 9, "ESTADO_DE_MEXICO": 15}
ROSTERS = {
    2000: (
        "Integrantes por grupo parlamentario de la LVIII Legislatura",
        "http://www.senado.gob.mx/comunicacion/senadores/",
        (
            "Read from the Internet Archive's earliest copies of the page for each parliamentary group, captured "
            "from November 2000 to August 2001; each prints the senator's principle of election and state."
        ),
    ),
    2018: (
        "Senadoras y senadores de la LXIV Legislatura por entidad federativa (principio de elección)",
        "http://www.senado.gob.mx/64/senadores/por_entidad_federativa",
        (
            "Read from the Internet Archive's copies of the page for each state, captured from 5 to 7 September "
            "2018, in the first week of the legislature; senado.gob.mx refuses scripted requests."
        ),
    ),
    2024: (
        "Senadoras y senadores de la LXVI Legislatura por grupo parlamentario",
        ROSTER_2024,
        (
            "Read from the Internet Archive's copy of 6 September 2024, in the first week of the legislature; "
            "senado.gob.mx refuses scripted requests."
        ),
    ),
}
PRINCIPLES = {"Mayoria Relativa": "majority", "Primera Minoria": "minority"}
SEATED = (
    "The seats are the declared ones, as the Senate's roster of the senators who took their seats records them: "
    "for {state} it lists {names} ({source}). Among the candidates INE registered, these are two of {winner}'s "
    "and one of {runner_up}'s, so {winner} holds the two majority seats and {runner_up} the first-minority seat, "
    "the seats the ranking of this count also gives."
)
SEATED_GROUPS = (
    "The seats are the declared ones, as the Senate's roster of the senators who took their seats records them: "
    "for {state} it lists {names} ({source}), with their principle of election. By their parliamentary groups, the "
    "two elected by majority sit for {winner} and the one elected by first minority for {runner_up}, the seats the "
    "ranking of this count also gives."
)


def roster_urls(year: int) -> dict[int, str]:
    if year == 2000:
        return {
            i: ARCHIVED.format(stamp=stamp, url=ROSTER_2000.format(group=group))
            for i, (group, (_, stamp)) in enumerate(GROUP_PAGES_2000.items())
        }
    if year == 2018:
        return {
            code: ARCHIVED.format(stamp=stamp, url=ROSTER_2018.format(slug=slug))
            for code, (slug, stamp) in ROSTER_PAGES_2018.items()
        }
    return {0: ARCHIVED.format(stamp=ROSTER_STAMP_2024, url=ROSTER_2024)}


def siceef_url(name: str) -> str:
    return f"{ine.SICEEF}{name}.csv"


def acuerdo_url(year: int) -> str:
    return ARCHIVED.format(stamp=ACUERDO_COPIES[year], url=ACUERDOS[year][0])


def ine_file(url: str) -> raw.Artifact:
    """INE's file at `url` in the cache, or the Internet Archive's copy of it where the cache holds only that (as
    for Senadores1994 and the acuerdos)."""
    try:
        return raw.cached(RAW, url)
    except LookupError:
        copies = sorted(key for key in raw.keys(RAW) if key.startswith(ARCHIVED[:24]) and key.endswith(f"id_/{url}"))
        if not copies:
            raise
        return raw.cached(RAW, copies[-1])


def fetch() -> None:
    for name in (*SICEEF_STATE.values(), *SICEEF_LIST.values()):
        raw.fetch(RAW, siceef_url(name), suffix=".csv")
    for url, *_ in COMPUTOS.values():
        raw.fetch(RAW, url, suffix=".zip")
    raw.fetch(RAW, SEATS_PAGE, suffix=".html")
    for year in ACUERDOS:
        raw.fetch(RAW, acuerdo_url(year), suffix=".pdf")
    for year in ROSTERS:
        for url in roster_urls(year).values():
            raw.fetch(SOURCE, url, suffix=".html")


def read_siceef(name: str) -> tuple[list[str], list[dict[str, str]]]:
    import csv

    text = ine_file(siceef_url(name)).path.read_bytes().decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    rows = [
        {
            "state": row["ID_ESTADO"],
            "district": row["ID_DISTRITO"],
            "municipality": row["ID_MUNICIPIO"],
            "section": row["SECCION"],
            "casilla": row["CASILLA"],
            "acta": "both",
            **row,
        }
        for row in reader
    ]
    columns = list(reader.fieldnames or [])
    return columns[columns.index("CASILLA") + 1 :], rows


def read_computos(year: int) -> tuple[list[str], list[dict[str, str]], int]:
    import csv

    url, inner, member = COMPUTOS[year]
    with zipfile.ZipFile(raw.cached(RAW, url).path) as outer, zipfile.ZipFile(io.BytesIO(outer.read(inner))) as sen:
        name = next(n for n in sen.namelist() if n.endswith(member))
        lines = sen.read(name).decode("latin-1").splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("CLAVE_CASILLA"))
    summary = next(i for i, line in enumerate(lines) if line.startswith("ACTAS_ESPERADAS"))
    printed = dict(zip(lines[summary].split("|"), lines[summary + 1].split("|"), strict=True))
    reader = csv.DictReader(io.StringIO("\n".join(lines[head:])), delimiter="|")
    columns = list(reader.fieldnames or [])
    state = "ID_ESTADO" if "ID_ESTADO" in columns else "ID_ENTIDAD"
    district = "ID_DISTRITO" if "ID_DISTRITO" in columns else "ID_DISTRITO_FEDERAL"
    acta = "TIPO_ACTA" if "TIPO_ACTA" in columns else "NUM_ACTA_IMPRESO"
    kinds = LIST_ACTAS[str(year)]
    rows = [
        {
            "state": clean(row[state]),
            "district": clean(row[district]),
            "municipality": "",
            "section": clean(row["SECCION"]),
            "casilla": clean(row["TIPO_CASILLA"]) + clean(row["ID_CASILLA"]) + clean(row["EXT_CONTIGUA"]),
            "acta": kinds.get(clean(row[acta]), "both"),
            **row,
        }
        for row in reader
    ]
    return columns[columns.index(acta) + 1 :], rows, number(printed["TOTAL_VOTOS"])


def tabulate(year: int, columns: list[str], rows: list[dict[str, str]]) -> tuple[pl.DataFrame, pl.DataFrame]:
    options = [c for c in columns if c not in TRAILING]
    total = "TOTAL_VOTOS" if "TOTAL_VOTOS" in columns else "TOTAL_VOTOS_CALCULADOS"
    roll = next((c for c in ("LISTA_NOMINAL", "LISTA_NOMINAL_CASILLA") if c in columns), None)
    units, votes, short = [], [], []
    for row in rows:
        counts = {option: number(row[option]) for option in options}
        total_here = number(row[total])
        if sum(counts.values()) != total_here:
            short.append((*(clean(row[k]) for k in KEYS), sum(counts.values()), total_here))
        unit = {k: clean(row[k]).lstrip("0") or "0" if k != "casilla" else clean(row[k]) for k in KEYS}
        unit["acta"] = row["acta"]
        units.append(
            unit
            | {
                "total": sum(counts.values()),
                "printed_total": total_here,
                "registered": number(row[roll]) if roll else None,
                "status": clean(row.get("ESTATUS_ACTA") or row.get("OBSERVACIONES") or ""),
                "tepjf": clean(row.get("TEPJF") or ""),
            }
        )
        for option, value in counts.items():
            votes.append(
                unit
                | {
                    "option": option,
                    "kind": "unregistered" if option in UNREGISTERED else "invalid" if option in NONVOTES else "list",
                    "votes": value,
                }
            )
    SHORT_TOTALS[year] = short
    seen: dict[tuple[str, ...], int] = {}
    for unit, vote_start in zip(units, range(0, len(votes), len(options)), strict=True):
        key = (*(unit[k] for k in KEYS), unit["acta"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            unit["casilla"] = f"{unit['casilla']}~{seen[key]}"
            for vote in votes[vote_start : vote_start + len(options)]:
                vote["casilla"] = unit["casilla"]
    units_frame = pl.DataFrame(units, schema_overrides={"registered": pl.Int64})
    duplicates = units_frame.group_by([*KEYS, "acta"]).len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(f"{year}: duplicate casillas {duplicates.head()}")
    return units_frame, pl.DataFrame(votes)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[int, str, str | None]] = [(year, "state", name) for year, name in SICEEF_STATE.items()]
    jobs += [(year, "list", name) for year, name in SICEEF_LIST.items()]
    jobs += [(year, "all", None) for year in COMPUTOS]
    for year, ballot, name in jobs:
        if name is None:
            columns, rows, printed = read_computos(year)
        else:
            (columns, rows), printed = read_siceef(name), None
        units, votes = tabulate(year, columns, rows)
        if printed is not None and printed != units["printed_total"].sum():
            raise ValueError(f"{year}: casillas add to {units['printed_total'].sum()}, the file prints {printed}")
        units.write_parquet(PARSED / f"{ballot}_{year}_casillas.parquet")
        votes.write_parquet(PARSED / f"{ballot}_{year}_votes.parquet")
        national = votes.group_by("option").agg(pl.col("votes").sum()).sort("votes", descending=True)
        print(year, ballot, units.height, "casillas", units["total"].sum(), "votes", dict(national.rows()))
        short = SHORT_TOTALS[year]
        print("  casillas whose options differ from the printed total:", len(short), short[:5])


DAYS = {
    1991: date(1991, 8, 18),
    1994: ine.DAYS[1994],
    1997: date(1997, 7, 6),
    **{year: day for year, day in ine.DAYS.items() if year >= 2000},
}
NEW_ELECTIONS = (1991, 1997)
EXTRA_PARTIES = {
    "PRT": ("Partido Revolucionario de los Trabajadores", "Revolutionary Workers' Party"),
    "PEM": ("Partido Ecologista de México", "Ecologist Party of Mexico"),
}
COLUMN_PARTY = {
    **{abbrev: abbrev for abbrev in (*PARTIES, *EXTRA_PARTIES)},
    "UNO_PDM": "PDM",
    "PC": "PFCRN",
    "DSPPN": "DS",
    "ASDC": "PASC",
    "NVA_ALIANZA": "NA",
    "NUEVA ALIANZA": "NA",
    "MOVIMIENTO CIUDADANO": "MC",
    "ENCUENTRO SOCIAL": "PES",
}
COLUMN_ALLIANCE = {"AC": "AC", "AM": "AM2000", "APM": "APM", "PBT": "PBT"}
STATE_SEATS = {1991: (1,), 1994: (2, 1)}
LIST_SEATS = {
    1997: {"PAN": 9, "PRI": 13, "PRD": 8, "PT": 1, "PVEM": 1},
    2000: {"AC": 13, "PRI": 13, "AM": 5, "PCD": 1},
    2006: {"PAN": 11, "APM": 10, "PBT": 10, "NVA_ALIANZA": 1},
    2012: {"PAN": 9, "PRI": 11, "PRD": 6, "PVEM": 2, "PT": 2, "MC": 1, "NVA_ALIANZA": 1},
    2018: {"PAN": 6, "PRI": 6, "PRD": 2, "PT": 1, "PVEM": 2, "MOVIMIENTO CIUDADANO": 2, "MORENA": 13},
    2024: {"PAN": 6, "PRI": 4, "PT": 2, "PVEM": 3, "MC": 3, "MORENA": 14},
}
CANDIDATURAS = {2018: "senadurias_candidaturas_2018.csv", 2024: "SEN_CANDIDATURAS_2024.csv"}
THRESHOLDS = {1997: 2, 2000: 2, 2006: 2, 2012: 2, 2018: 3, 2024: 3}

INE_SENADO = "Cómputos distritales de las elecciones de Senadores, por casilla (1991-2024)"
SEATS_TITLE = (
    "Atlas de Resultados de las Elecciones Federales 1991-2015: Cámara de Senadores (distribución por partido)"
)

SYSTEM_1991 = "One senator per state is elected by plurality for a six-year term; the chamber renews by halves."
SYSTEM_STATE = (
    "Each state elects three senators from lists of two formulas: the list with the most votes takes both seats and "
    "the list in second place takes the third (primera minoría). Voters mark one list; where a coalition is "
    "registered, a mark for any of its parties counts for the coalition's list. This archive's engine has no "
    "first-minority rule, so the contest carries no electoral system."
)
SYSTEM_LIST = (
    "Thirty-two senators are elected from national closed party lists by proportional representation (natural "
    "quotient and largest remainder), among parties with at least {threshold} percent of the national vote."
)
STATE_METHOD = (
    "The state's count as the district counts reported it: {votes:,} votes on {casillas:,} casilla records. The "
    "seats follow from this count under the constitutional rule ({rule}); the archive derived them, since INE's "
    "state declarations (constancias de mayoría y de primera minoría) are not in the bulk files. {check}"
)
SIMPLE_RULE = "the list with the most votes wins the seat"
MINORITY_RULE = "the list with the most votes wins two seats and the list in second place wins the third"
CASILLA_METHOD = (
    "Polling-station (casilla) results from INE's district count files, {votes:,} votes on {casillas:,} casilla "
    "records. Casillas sit within their electoral section as INE's 2017 and 2023 section maps draw it, or within "
    "their municipality or state where the section is no longer mapped."
)
FUSION = (
    " A mark for several parties of a coalition is counted as its own line under the coalition's list, and the "
    "list's votes are the sum of its lines."
)
LIST_METHOD = (
    "The national result of the party-list ballot as the district counts reported it: {votes:,} votes. The seats "
    "are the declared assignment ({source}); a coalition's seats are those of its member parties together."
)
SAME_BALLOT = (
    " The ballot is the same as the state contests': one mark counts for the state list and for the national list, "
    "so the votes are the state contests' votes added up, plus the special casillas' separate list-only records."
)
STATE_ONLY = " Votes are stored by state; the casilla records are in the state contests."
SEATS_METHOD = (
    "Seats by party as INE's Atlas de Resultados prints them (Cámara de Senadores, {year}); the page's counts are "
    "for the whole chamber, not by state."
)
SEATS_CHECK = (
    "The seats derived for all {count} states give {derived} to the coalitions and parties below; INE's Atlas "
    "prints {declared} for the chamber as a whole."
)


def members(column: str) -> tuple[str, ...]:
    if column in COLUMN_ALLIANCE:
        return ALLIANCES[COLUMN_ALLIANCE[column]][3]
    if column in COLUMN_PARTY:
        return (COLUMN_PARTY[column],)
    if column.startswith("CAND_IND"):
        return (column,)
    return tuple(COLUMN_PARTY[part] for part in column.split("_"))


def known_alliance(year: int, group: tuple[str, ...]) -> str | None:
    return next(
        (code for code, (*_, parts) in ALLIANCES.items() if ALLIANCE_YEARS[code] == year and set(parts) == set(group)),
        None,
    )


ALLIANCE_YEARS = {
    "AC": 2000,
    "AM2000": 2000,
    "APM": 2006,
    "PBT": 2006,
    "CPM": 2012,
    "MP": 2012,
    "PMF": 2018,
    "TPM": 2018,
    "JHH": 2018,
    "FCM": 2024,
    "SHH": 2024,
}


def formulas(columns: list[str], totals: dict[str, int]) -> list[tuple[tuple[str, ...], list[str]]]:
    parent: dict[str, str] = {}

    def find(party: str) -> str:
        parent.setdefault(party, party)
        while parent[party] != party:
            parent[party] = parent[parent[party]]
            party = parent[party]
        return party

    for column in columns:
        parts = members(column)
        find(parts[0])
        if len(parts) > 1 and (totals.get(column, 0) > 0 or column in COLUMN_ALLIANCE):
            for other in parts[1:]:
                parent[find(other)] = find(parts[0])
    groups: dict[str, tuple[list[str], list[str]]] = {}
    for column in columns:
        parts = members(column)
        entry = groups.setdefault(find(parts[0]), ([], []))
        entry[1].append(column)
        entry[0].extend(part for part in parts if part not in entry[0])
    return [(tuple(parties), cols) for parties, cols in groups.values()]


def candidaturas(year: int) -> dict[tuple[int, str], str]:
    import csv

    url, inner, _ = COMPUTOS[year]
    with zipfile.ZipFile(raw.cached(RAW, url).path) as outer, zipfile.ZipFile(io.BytesIO(outer.read(inner))) as sen:
        name = next(n for n in sen.namelist() if n.endswith(CANDIDATURAS[year]))
        text = sen.read(name).decode("latin-1").split("\n", 1)[1]
    result = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        state = row.get("ESTADO") or row["ID_ENTIDAD"]
        result[int(state), row["PARTIDO_CI"]] = " / ".join(
            part.strip() for part in row["CANDIDATURA_PROPIETARIA"].split(",")
        )
    return result


SUMMARY = (
    "Elections to the Senate of Mexico (1991-2024): the 32 state contests and the national list, by casilla, with "
    "the party-list seats as declared and the state seats of 2000, 2018 and 2024 as the Senate's rosters record them"
)


TURNOUT = (
    "INE's district count files print more ballots than the nominal roll for these casillas; the counts are stored "
    "as the files print them"
)


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package mx.senado/all from the parsed tables. It reads the database, through Draft, so the rows it
    rebuilds keep the ids the changeset path gave them; `declare` then gives the state contests of 2018 and 2024
    their declared seats, as `package_from_export` does."""
    from ea.changeset import slug

    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="MX")
        cs.accept("accounting.turnout", TURNOUT)
        for scheme in ("mx.entidad", "mx.municipio", "mx.seccion", "mx.casilla"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.senado")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        ine_source = cs.source(ine.INE, INE_SENADO)
        cs.add(
            "source",
            id=ine_source,
            kind="official_body",
            publisher=ine.INE,
            title=INE_SENADO,
            url=ine.INE_PAGE,
            license=ine.INE_LICENSE,
            license_url=ine.INE_PAGE,
            attribution="Instituto Nacional Electoral, cómputos distritales",
        )
        for name_ in (*SICEEF_STATE.values(), *SICEEF_LIST.values()):
            cs.artifact(ine_file(siceef_url(name_)), ine_source)
        for url, *_ in COMPUTOS.values():
            cs.artifact(raw.cached(RAW, url), ine_source)
        seats_source = cs.source(ine.INE, SEATS_TITLE)
        cs.add(
            "source",
            id=seats_source,
            kind="official_body",
            publisher=ine.INE,
            title=SEATS_TITLE,
            url=SEATS_PAGE,
            license=ine.INE_LICENSE,
            license_url=ine.INE_PAGE,
        )
        cs.artifact(raw.cached(RAW, SEATS_PAGE), seats_source)
        acuerdo_sources: dict[int, UUID] = {}
        for year, (url, title) in ACUERDOS.items():
            source = acuerdo_sources[year] = cs.source(ine.INE, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=ine.INE,
                title=title,
                url=url,
                license=ine.PUBLIC,
            )
            cs.artifact(ine_file(url), source)

        body = cs.key("mx.body", "senado")
        cs.add("body", id=body, polity_id=mexico, kind="upper_house")
        name(body, "Senate of Mexico")
        name(body, "Cámara de Senadores del H. Congreso de la Unión", "es", "native")
        cs.add("body_size", body_id=body, seats=64, valid_from=date(1988, 9, 1), valid_to=date(1994, 8, 31))
        cs.add("body_size", body_id=body, seats=128, valid_from=date(1994, 9, 1))

        systems: dict[str, UUID] = {}

        def system(key: str, rules: dict[str, object], title: str, body_md: str) -> UUID:
            if key not in systems:
                systems[key] = cs.key("mx.electoral_system", key)
                if systems[key] in cs.minted:
                    cs.add("electoral_system", id=systems[key], polity_id=mexico, rules=rules, rules_version=1)
                    name(systems[key], title)
                    cs.add("note", entity_id=systems[key], kind="methodology", body_md=body_md)
            return systems[key]

        parties: dict[str, UUID] = {}

        def party(abbrev: str) -> UUID:
            if abbrev not in parties:
                if abbrev in PARTIES:
                    item, native, english = PARTIES[abbrev]
                    parties[abbrev] = cs.key("mx.party", item)
                else:
                    native, english = EXTRA_PARTIES[abbrev]
                    item = None
                    parties[abbrev] = cs.key("mx.party", abbrev)
                if parties[abbrev] in cs.minted:
                    cs.add("party", id=parties[abbrev], kind="party", polity_id=mexico)
                    name(parties[abbrev], native, "es")
                    name(parties[abbrev], english, "en", "english")
                    name(parties[abbrev], abbrev, "es", "abbreviation")
                    if item is not None:
                        cs.add("external_id", entity_id=parties[abbrev], scheme="wikidata", value=item)
            return parties[abbrev]

        alliances: dict[str, UUID] = {}

        def alliance(election: UUID, year: int, group: tuple[str, ...]) -> UUID:
            code = known_alliance(year, group)
            key = f"{year}:{code}" if code is not None else f"{year}:{'+'.join(sorted(group))}"
            if key not in alliances:
                alliances[key] = cs.key("mx.alliance", key)
                if alliances[key] in cs.minted:
                    cs.add("alliance", id=alliances[key], kind="electoral_alliance", election_id=election)
                    if code is not None:
                        qid, native, english, _ = ALLIANCES[code]
                        name(alliances[key], native, "es")
                        name(alliances[key], english, "en", "english")
                        cs.add("external_id", entity_id=alliances[key], scheme="wikidata", value=qid)
                    else:
                        name(alliances[key], "-".join(group), "es")
                    for member in group:
                        cs.add("alliance_member", alliance_id=alliances[key], party_id=party(member))
            return alliances[key]

        names = {
            code: {kind: text for kind, text in rows}
            for code, rows in (
                (
                    code,
                    conn.execute(
                        "select n.kind::text, n.text from area a join area_code c on c.area_id = a.id "
                        "join name n on n.entity_id = a.id where a.scheme_code = 'mx.entidad' and c.code = %s",
                        (f"{code:02d}",),
                    ).fetchall(),
                )
                for code in range(1, 33)
            )
        }
        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
            ).fetchall()
        )
        towns = dict(
            conn.execute(
                "select x.value, x.entity_id from external_id x join area a on a.id = x.entity_id "
                "where x.scheme = 'mx.ine.municipio' and a.scheme_code = 'mx.municipio'"
            ).fetchall()
        )
        nation_unit = cs.unit("mx.nation", "MX")
        state_units: dict[int, UUID] = {code: cs.unit("mx.entidad", f"{code:02d}") for code in range(1, 33)}
        unit_rows: dict[UUID, tuple[str, str, UUID, str, UUID, str]] = {}
        codes: set[str] = set()
        placed: dict[tuple[int, str, str, str, str, str], tuple[str, int]] = {}
        frames: list[pl.DataFrame] = []
        derived: dict[int, dict[tuple[str, ...], int]] = {}

        def locate(year: int, casillas: pl.DataFrame) -> pl.DataFrame:
            rows = []
            for row in casillas.iter_rows(named=True):
                key = (year, *(row[k] for k in KEYS))
                if key not in placed:
                    state = int(row["state"])
                    district = int(row["district"]) if row["district"].isdigit() else 0
                    section = int(row["section"])
                    letter = row["casilla"][:1] or "X"
                    base, _, repeat = (row["casilla"] or "X").partition("~")
                    special = re.fullmatch(r"S(M|MR|RP)(\d*)", base)
                    tail = f"S{special.group(1)}{int(special.group(2) or 1)}" if special else casilla_code(year, base)
                    code = f"{year}:{state:02d}-{district:02d}-{section:04d}-{tail}"
                    if code in codes:
                        code = f"{code}-m{int(row['municipality'] or 0)}"
                    if repeat:
                        code = f"{code}-r{repeat}"
                    codes.add(code)
                    channel, kind, area = 0, "standard", None
                    if letter in "MV" or section == 0:
                        channel, kind = ABROAD, "aggregate"
                    elif letter == "A":
                        channel, kind = EARLY, "aggregate"
                    elif letter == "P":
                        channel, kind = PRISON, "prison"
                    else:
                        area = sections.get(f"{state:02d}{section:04d}")
                        if area is None and row["municipality"] not in ("", "0"):
                            area = towns.get(f"{state:02d}{int(row['municipality']):03d}")
                    unit = cs.unit("mx.casilla", code)
                    if unit in unit_rows:
                        raise ValueError(f"{year}: casilla {code} appears twice")
                    unit_rows[unit] = (
                        "mx.casilla",
                        code,
                        state_units[state],
                        "within",
                        area or cs.area("mx.entidad", f"{state:02d}"),
                        kind,
                    )
                    placed[key] = (str(unit), channel)
                rows.append((*(row[k] for k in KEYS), *placed[key]))
            return pl.DataFrame(
                rows,
                schema=[*((k, pl.String) for k in KEYS), ("unit_id", pl.String), ("channel", pl.Int16)],
                orient="row",
            )

        def block(
            year: int,
            kind: str,
            casillas: pl.DataFrame,
            votes: pl.DataFrame,
            optmap: pl.DataFrame,
            sets: pl.DataFrame,
        ) -> None:
            units = locate(year, casillas)
            located = casillas.join(units, on=KEYS).join(sets, on="state")
            joined = votes.join(units, on=KEYS).join(optmap, on=["state", "option"], how="left").join(sets, on="state")
            none = pl.lit(None, pl.String)
            chosen = pl.col("default_set") if kind == "default" else pl.col("casilla_set")
            parts = [
                located.filter(pl.col("registered") > 0).select(
                    chosen.alias("set"),
                    "unit_id",
                    "channel",
                    option_id=none,
                    measure=pl.lit(REGISTERED),
                    value="registered",
                ),
                located.select(
                    chosen.alias("set"), "unit_id", "channel", option_id=none, measure=pl.lit(BALLOTS), value="total"
                ),
                joined.filter(pl.col("kind") == "invalid").select(
                    chosen.alias("set"), "unit_id", "channel", option_id=none, measure=pl.lit(INVALID), value="votes"
                ),
                joined.filter(pl.col("kind") != "invalid")
                .group_by(chosen.alias("set"), "unit_id", "channel")
                .agg(value=pl.col("votes").sum())
                .select("set", "unit_id", "channel", option_id=none, measure=pl.lit(VALID), value="value"),
                joined.filter((pl.col("kind") != "invalid") & (pl.col("votes") != 0)).select(
                    chosen.alias("set"), "unit_id", "channel", option_id="line_id", measure=pl.lit(VOTES), value="votes"
                ),
            ]
            frames.append(
                pl.concat(
                    [
                        part.select(
                            result_set_id="set",
                            unit_id="unit_id",
                            option_id="option_id",
                            measure=pl.col("measure").cast(pl.Int16),
                            value=pl.col("value").cast(pl.Int64),
                            channel=pl.col("channel").cast(pl.Int16),
                        )
                        for part in parts
                    ]
                )
            )

        def totals_at(
            votes: pl.DataFrame, casillas: pl.DataFrame, optmap: pl.DataFrame, sets: pl.DataFrame, nation: bool
        ) -> None:
            mapped = votes.join(optmap, on=["state", "option"], how="left").join(sets, on="state")
            lines = mapped.filter((pl.col("kind") != "invalid") & (pl.col("votes") != 0))
            level = ["state", "default_set"]
            cased = casillas.join(sets, on="state")
            frames_by_measure = [
                (VOTES, lines.group_by([*level, "formula_id"]).agg(value=pl.col("votes").sum())),
                (VALID, mapped.filter(pl.col("kind") != "invalid").group_by(level).agg(value=pl.col("votes").sum())),
                (INVALID, mapped.filter(pl.col("kind") == "invalid").group_by(level).agg(value=pl.col("votes").sum())),
                (BALLOTS, cased.group_by(level).agg(value=pl.col("total").sum())),
                (
                    REGISTERED,
                    cased.filter(pl.col("registered") > 0).group_by(level).agg(value=pl.col("registered").sum()),
                ),
            ]
            for measure, frame in frames_by_measure:
                if "formula_id" not in frame.columns:
                    frame = frame.with_columns(formula_id=pl.lit(None, pl.String))
                for entity, grouped in (
                    ([pl.col("state").cast(pl.Int64).replace_strict(unit_text, return_dtype=pl.String)], frame),
                    *(
                        [
                            (
                                [pl.lit(str(nation_unit))],
                                frame.group_by("default_set", "formula_id").agg(pl.col("value").sum()),
                            )
                        ]
                        if nation
                        else []
                    ),
                ):
                    frames.append(
                        grouped.select(
                            result_set_id="default_set",
                            unit_id=entity[0],
                            option_id="formula_id",
                            measure=pl.lit(measure, pl.Int16),
                            value=pl.col("value").cast(pl.Int64),
                            channel=pl.lit(0, pl.Int16),
                        )
                    )

        unit_text = {code: str(unit) for code, unit in state_units.items()}
        for year in sorted(DAYS):
            day = DAYS[year]
            election = cs.key("mx.election", f"{day}:general")
            path = f"mx/{day}-general"
            if election in cs.minted:
                cs.add(
                    "election", id=election, polity_id=mexico, kind="general", date_from=day, date_to=day, status="held"
                )
                name(election, f"{year} Mexican general election")
                name(election, f"Elecciones federales de {year}", "es", "native")
                cs.add("slug", path=path, entity_id=election)
            single = year in COMPUTOS
            data: dict[str, tuple[pl.DataFrame, pl.DataFrame]] = {}
            for kind, available in (("state", year in SICEEF_STATE or single), ("list", year in SICEEF_LIST or single)):
                if available:
                    ballot = "all" if single else kind
                    casillas = pl.read_parquet(PARSED / f"{ballot}_{year}_casillas.parquet")
                    votes = pl.read_parquet(PARSED / f"{ballot}_{year}_votes.parquet")
                    if single:
                        casillas = casillas.filter(pl.col("acta").is_in(["both", kind]))
                        votes = votes.filter(pl.col("acta").is_in(["both", kind]))
                    data[kind] = (casillas, votes)
            names_of = candidaturas(year) if year in CANDIDATURAS else {}

            def set_of(
                contest: UUID,
                label: str,
                default: bool,
                source: UUID,
                completeness: float,
                note: str,
                year: int = year,
                election: UUID = election,
            ) -> UUID:
                result_set = cs.key("mx.result_set", f"{day}:senate:{label}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=completeness,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                return result_set

            if "state" in data:
                casillas, votes = data["state"]
                columns = list(dict.fromkeys(votes.filter(pl.col("kind") == "list")["option"]))
                unregistered = list(dict.fromkeys(votes.filter(pl.col("kind") == "unregistered")["option"]))
                invalid = list(dict.fromkeys(votes.filter(pl.col("kind") == "invalid")["option"]))
                pattern = STATE_SEATS.get(year, (2, 1))
                seats_system = None
                if sum(pattern) == 1:
                    seats_system = system(
                        "fptp", {"formula": "fptp"}, "Mexican Senate plurality system, 1991", SYSTEM_1991
                    )
                mapping, sets_rows = [], []
                state_totals = votes.group_by("state", "option").agg(pl.col("votes").sum())
                state_sum = votes.filter(pl.col("kind") != "invalid").group_by("state").agg(pl.col("votes").sum())
                state_count = casillas.group_by("state").len()
                year_derived: dict[tuple[str, ...], int] = {}
                for code in range(1, 33):
                    state = str(code)
                    english = names[code].get("english") or names[code]["official"]
                    spanish = names[code]["official"]
                    contest = cs.key("mx.contest", f"{day}:senate:{code:02d}")
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="body_seats",
                        body_id=body,
                        district_area_id=cs.area("mx.entidad", f"{code:02d}"),
                        magnitude=sum(pattern),
                        electoral_system_id=seats_system,
                        default_result_kind="counted",
                    )
                    name(contest, f"{year} Mexican Senate election in {english}")
                    name(contest, f"Elección de Senadores de {spanish} de {year}", "es", "native")
                    cs.add("slug", path=f"{path}/senate/{slug(english)}", entity_id=contest)
                    if sum(pattern) > 1:
                        cs.add("note", entity_id=contest, kind="methodology", body_md=SYSTEM_STATE)
                    totals = {
                        row["option"]: row["votes"]
                        for row in state_totals.filter(pl.col("state") == state).iter_rows(named=True)
                    }
                    ranked: list[tuple[int, tuple[str, ...], UUID]] = []
                    groups = formulas(columns, totals)
                    for position, (group, cols) in enumerate(groups, 1):
                        label = (
                            ALLIANCES[COLUMN_ALLIANCE[cols[0]]][1]
                            if len(cols) == 1 and cols[0] in COLUMN_ALLIANCE
                            else "-".join(group)
                        )
                        if names_of:
                            found = {
                                names_of.get((code, member)) for member in group if member in {*COLUMN_PARTY.values()}
                            }
                            found.discard(None)
                            if found:
                                label = f"{label}: {' | '.join(sorted(name for name in found if name is not None))}"
                        formula = cs.key("mx.option", f"{day}:senate:{code:02d}:{'+'.join(cols)}")
                        cs.add(
                            "contest_option",
                            id=formula,
                            contest_id=contest,
                            kind="list",
                            party_id=party(group[0]) if len(group) == 1 and group[0] in COLUMN_PARTY.values() else None,
                            alliance_id=alliance(election, year, group) if len(group) > 1 else None,
                            ballot_position=position,
                            ballot_label=label,
                        )
                        for column in cols:
                            line = formula
                            if len(cols) > 1:
                                line = cs.key("mx.option", f"{day}:senate:{code:02d}:{column}")
                                line_group = members(column)
                                cs.add(
                                    "contest_option",
                                    id=line,
                                    contest_id=contest,
                                    kind="list",
                                    parent_option_id=formula,
                                    party_id=party(line_group[0]) if len(line_group) == 1 else None,
                                    ballot_label=column.replace("_", "-"),
                                )
                            mapping.append((state, column, str(line), str(formula)))
                        ranked.append((sum(totals.get(column, 0) for column in cols), group, formula))
                    write_in = cs.key("mx.option", f"{day}:senate:{code:02d}:unregistered")
                    cs.add(
                        "contest_option",
                        id=write_in,
                        contest_id=contest,
                        kind="write_in",
                        ballot_label="Candidatos no registrados",
                    )
                    for column in unregistered:
                        mapping.append((state, column, str(write_in), str(write_in)))
                    for column in invalid:
                        mapping.append((state, column, None, None))
                    ranked.sort(key=lambda item: item[0], reverse=True)
                    cut = len(pattern)
                    if len({item[0] for item in ranked[: cut + 1]}) != len(ranked[: cut + 1]):
                        raise ValueError(f"{year} {code}: tied votes {[item[:2] for item in ranked[: cut + 1]]}")
                    count = int(state_count.filter(pl.col("state") == state)["len"][0])
                    total = int(state_sum.filter(pl.col("state") == state)["votes"][0])
                    check = ""
                    default_set = set_of(
                        contest,
                        f"{code:02d}:state",
                        True,
                        ine_source,
                        1,
                        STATE_METHOD.format(
                            votes=total,
                            casillas=count,
                            rule=SIMPLE_RULE if len(pattern) == 1 else MINORITY_RULE,
                            check=check,
                        ).strip(),
                    )
                    casilla_set = set_of(
                        contest,
                        f"{code:02d}:casilla",
                        False,
                        ine_source,
                        1,
                        CASILLA_METHOD.format(votes=total, casillas=count) + (FUSION if year >= 2012 else ""),
                    )
                    for (_, group, formula), seats in zip(ranked, pattern, strict=False):
                        cs.add("outcome", result_set_id=default_set, option_id=formula, kind="elected", seats=seats)
                        year_derived[group] = year_derived.get(group, 0) + seats
                    sets_rows.append((state, str(default_set), str(casilla_set)))
                derived[year] = year_derived
                sets = pl.DataFrame(sets_rows, schema=["state", "default_set", "casilla_set"], orient="row")
                optmap = pl.DataFrame(
                    mapping,
                    schema=[
                        ("state", pl.String),
                        ("option", pl.String),
                        ("line_id", pl.String),
                        ("formula_id", pl.String),
                    ],
                    orient="row",
                )
                block(year, "casilla", casillas, votes, optmap, sets)
                totals_at(votes, casillas, optmap, sets, False)
                print(year, "state contests", casillas.height, "casillas")

            if "list" in data:
                casillas, votes = data["list"]
                columns = list(dict.fromkeys(votes.filter(pl.col("kind") == "list")["option"]))
                unregistered = list(dict.fromkeys(votes.filter(pl.col("kind") == "unregistered")["option"]))
                invalid = list(dict.fromkeys(votes.filter(pl.col("kind") == "invalid")["option"]))
                threshold = THRESHOLDS[year]
                list_system = system(
                    f"list-{threshold}",
                    {
                        "formula": "hare",
                        "threshold": {"percent": threshold, "level": "party", "scope": "national"},
                        "lists": {"order": "closed", "vacancy": "substitute"},
                    },
                    f"Mexican Senate national list, {threshold} percent threshold",
                    SYSTEM_LIST.format(threshold=threshold),
                )
                contest = cs.key("mx.contest", f"{day}:senate:list")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=cs.area("mx.nation", "MX"),
                    magnitude=32,
                    electoral_system_id=list_system,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Mexican Senate national list election")
                name(contest, f"Elección de Senadores por representación proporcional de {year}", "es", "native")
                cs.add("slug", path=f"{path}/senate/national-list", entity_id=contest)
                lines: dict[str, UUID] = {}
                for position, column in enumerate(columns, 1):
                    group = members(column)
                    lines[column] = cs.key("mx.option", f"{day}:senate:list:{column}")
                    label = (
                        ALLIANCES[COLUMN_ALLIANCE[column]][1] if column in COLUMN_ALLIANCE else column.replace("_", "-")
                    )
                    cs.add(
                        "contest_option",
                        id=lines[column],
                        contest_id=contest,
                        kind="list",
                        party_id=party(group[0]) if len(group) == 1 and not column.startswith("CAND") else None,
                        alliance_id=alliance(election, year, group)
                        if len(group) > 1 and (column in COLUMN_ALLIANCE or "_" not in column)
                        else None,
                        ballot_position=position,
                        ballot_label=label,
                    )
                write_in = cs.key("mx.option", f"{day}:senate:list:unregistered")
                cs.add(
                    "contest_option",
                    id=write_in,
                    contest_id=contest,
                    kind="write_in",
                    ballot_label="Candidatos no registrados",
                )
                mapping: list[tuple[str, str, str | None, str | None]] = [
                    ("0", column, str(line), str(line)) for column, line in lines.items()
                ]
                mapping += [("0", column, str(write_in), str(write_in)) for column in unregistered]
                mapping += [("0", column, None, None) for column in invalid]
                got = int(votes.filter(pl.col("kind") != "invalid")["votes"].sum())
                if year in ACUERDOS:
                    declarer, locator, source = acuerdo_sources[year], {"page": 1}, ACUERDOS[year][1].split(":")[0]
                else:
                    declarer, locator, source = seats_source, None, SEATS_TITLE
                note = LIST_METHOD.format(votes=got, source=source) + (SAME_BALLOT if single else "")
                default_set = set_of(
                    contest, "list:national", True, declarer, 1, note + (STATE_ONLY if not single else "")
                )
                if locator is not None:
                    cs.add("citation", entity_id=default_set, source_id=declarer, locator=locator)
                total_seats = 0
                for column, seats in LIST_SEATS[year].items():
                    cs.add("outcome", result_set_id=default_set, option_id=lines[column], kind="elected", seats=seats)
                    total_seats += seats
                if total_seats != 32:
                    raise ValueError(f"{year}: list seats add to {total_seats}")
                sets_rows = [(str(code), str(default_set), None) for code in range(1, 33)]
                if not single:
                    casilla_set = set_of(
                        contest,
                        "list:casilla",
                        False,
                        ine_source,
                        1,
                        CASILLA_METHOD.format(votes=got, casillas=casillas.height) + (FUSION if year >= 2012 else ""),
                    )
                    sets_rows = [(str(code), str(default_set), str(casilla_set)) for code in range(1, 33)]
                sets = pl.DataFrame(sets_rows, schema=["state", "default_set", "casilla_set"], orient="row")
                optmap = pl.DataFrame(
                    [
                        (str(code), column, line, formula)
                        for code in range(1, 33)
                        for _, column, line, formula in mapping
                    ],
                    schema=[
                        ("state", pl.String),
                        ("option", pl.String),
                        ("line_id", pl.String),
                        ("formula_id", pl.String),
                    ],
                    orient="row",
                )
                if not single:
                    block(year, "casilla", casillas, votes, optmap, sets)
                totals_at(votes, casillas, optmap, sets, True)
                print(year, "list contest", casillas.height, "casillas", got, "votes")

        for year, tally in derived.items():
            print(year, "derived seats", {"+".join(group): seats for group, seats in tally.items()})

        minted = [
            {
                "id": str(unit),
                "polity_id": str(mexico),
                "scheme_code": scheme,
                "code": code,
                "parent_id": str(parent),
                "area_relation": relation,
                "area_id": str(area),
                "kind": kind,
            }
            for unit, (scheme, code, parent, relation, area, kind) in unit_rows.items()
            if unit in cs.minted
        ]
        if minted:
            cs.frame("reporting_unit", pl.DataFrame(minted))
        cs.frame(
            "result_value",
            pl.concat(frames).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        out = cs.write()
    return declare(out)


def fold(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    words = re.sub(r"[^A-Z ]", " ", plain).split()
    return ["MARIA" if word == "MA" else word for word in words]


def same_word(a: str, b: str) -> bool:
    return a == b or len(a) > 3 and len(b) > 3 and SequenceMatcher(None, a, b).ratio() >= 0.85


def same_person(seated: list[str], registered: list[str]) -> bool:
    """A senator's printed name names a registered candidate when all its words but one match words of the
    candidate's name (the rosters drop second surnames and the candidate lists carry a few misspellings)."""
    found = sum(any(same_word(word, other) for other in registered) for word in seated)
    return found >= min(3, len(seated)) and found >= len(seated) - 1


def roster(year: int) -> dict[int, tuple[str, str, list[tuple[str, str | None]]]]:
    """Each state's seated senators as the Senate's roster prints them: {code: (state, url, [(name, principle)])}.
    The 2024 roster prints no principle of election."""
    found: dict[int, tuple[str, str, list[tuple[str, str | None]]]] = {}
    if year == 2018:
        for code, url in roster_urls(year).items():
            text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8")
            info = text[text.index('<a name="info"></a>') :]
            state = re.search(r'alt=="([^"]+)"', info)
            if state is None:
                raise ValueError(f"2018: no state heading on {url}")
            senators = re.findall(
                r'<a href="/64/senador/[^"]+">Sen\. ([^<]+)</a></strong>\s*</h3><br>\s*<p class="text-left">\s*'
                r"(Mayoria Relativa|Primera Minoria)",
                info,
            )
            found[code] = (
                html.unescape(state.group(1)),
                url,
                [(html.unescape(n).strip(), PRINCIPLES[p]) for n, p in senators],
            )
        return found
    (url,) = roster_urls(year).values()
    text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8")
    slugs = {slug: code for code, (slug, _) in ROSTER_PAGES_2018.items()}
    for name, state in re.findall(
        r'data_id-senador="\d+".*?class="nombre-sen[^"]*"><a [^>]*>([^<]+)</a>.*?<span class="estado">([^<]*)</span>',
        text,
        flags=re.DOTALL,
    ):
        state = html.unescape(state).strip()
        if state == "Lista Nacional":
            continue
        code = ROSTER_STATES_2024.get(state) or slugs["_".join(fold(state)).lower()]
        found.setdefault(code, (state, url, []))[2].append((html.unescape(name).strip(), None))
    return found


def computos_zip(year: int) -> Path:
    """INE's cómputos archive for the year. mx.computos caches the same files under the same addresses, so a
    checkout whose mx.ine cache lacks them reads that copy."""
    url = COMPUTOS[year][0]
    try:
        return raw.cached(RAW, url).path
    except LookupError:
        return raw.cached("mx.computos", url).path


def registered(year: int) -> dict[tuple[int, str], list[list[str]]]:
    """The candidates each party registered in each state, propietarios and suplentes, from INE's candidate file:
    {(state, party): [name words]}. A formula without a propietario prints "Sin candidatura" in its place."""
    import csv

    _, inner, _ = COMPUTOS[year]
    with zipfile.ZipFile(computos_zip(year)) as outer, zipfile.ZipFile(io.BytesIO(outer.read(inner))) as sen:
        name = next(n for n in sen.namelist() if n.endswith(CANDIDATURAS[year]))
        text = sen.read(name).decode("latin-1").split("\n", 1)[1]
    found: dict[tuple[int, str], list[list[str]]] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        key = (int(row.get("ESTADO") or row["ID_ENTIDAD"]), row["PARTIDO_CI"])
        names = found.setdefault(key, [])
        for column in ("CANDIDATURA_PROPIETARIA", "CANDIDATURA_SUPLENTE"):
            for person in row[column].split(","):
                words = fold(person)
                if words and person.strip() != "Sin candidatura" and words not in names:
                    names.append(words)
    return found


Seated = dict[str, list[tuple[str, str | None]]]


def principle(text: str) -> str | None:
    words = fold(text)
    return {("MAYORIA", "RELATIVA"): "majority", ("PRIMERA", "MINORIA"): "minority"}.get(tuple(words[:2]))


def group_seats(day: str, keys: pl.DataFrame, lists: pl.DataFrame) -> dict[int, tuple[str, list[str], Seated | str]]:
    """Match each state's seated senators of 2000, as the LVIII Legislature's pages by parliamentary group print
    them, to the list of the state's contest that holds their group's party. Gives {code: (state, [pages],
    {option_id: [(name, principle)]})}, or the reason in place of the seats when the pages do not settle them."""
    tops = set(lists["id"].to_list())
    prefix = f"{day}:senate:"
    parties: dict[int, dict[str, set[str]]] = {}
    for value, entity in (
        keys.filter((pl.col("scheme") == "mx.option") & pl.col("value").str.starts_with(prefix))
        .select("value", "entity_id")
        .iter_rows()
    ):
        code, _, columns = str(value).removeprefix(prefix).partition(":")
        if code.isdigit() and entity in tops:
            parties.setdefault(int(code), {})[str(entity)] = {
                party for column in columns.split("+") for party in members(column)
            }
    slugs = {slug: code for code, (slug, _) in ROSTER_PAGES_2018.items()}
    senators: dict[int, list[tuple[str, str, str, str, str]]] = {}
    for url, (party, _) in zip(roster_urls(2000).values(), GROUP_PAGES_2000.values(), strict=True):
        text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
        for name, printed, state in re.findall(
            r"SEN\. (.+?) Elecci[oó]n: (.+?) Entidad Federativa: (.*?) (?:Fecha|SUPLENTE|Estudios)", text
        ):
            elected = principle(printed)
            if elected is None:
                continue
            key = "_".join(fold(state))
            code = STATES_2000.get(key) or slugs[key.lower()]
            senators.setdefault(code, []).append((name.strip(), elected, party, url, state.strip()))
    found: dict[int, tuple[str, list[str], Seated | str]] = {}
    for code, rows in sorted(senators.items()):
        pages = sorted({row[3] for row in rows})
        # The pages spell a few states differently ("Colíma", "Estado de México"); the note takes the usual one.
        state = Counter(row[4] for row in rows).most_common(1)[0][0]
        if len(rows) != 3:
            found[code] = (state, pages, f"the roster lists {len(rows)} senators")
            continue
        held: Seated = {}
        for name, elected, party, _, _ in rows:
            lists_of = [option for option, held_by in parties[code].items() if party in held_by]
            if len(lists_of) != 1:
                held = {}
                found[code] = (state, pages, f"{name}'s party {party} is on {len(lists_of)} lists")
                break
            held.setdefault(lists_of[0], []).append((name, elected))
        if held:
            found[code] = (state, pages, held)
    return found


def seated(year: int, options: dict[int, list[tuple[str, str]]]) -> dict[int, tuple[str, list[str], Seated | str]]:
    """Match each state's seated senators to the lists of its contest ({code: [(option_id, ballot_label)]}),
    through the propietarios and suplentes INE registered for each party of a list. Gives {code: (state, url,
    {option_id: [(name, principle)]})}, or the reason in place of the seats when the roster does not settle them:
    it lists other than three senators (one on leave in its first week), or a senator names no registered
    candidate. Where the casilla reading splits one coalition's formula into several lists, each naming the
    coalition's candidates, the senator sits on the list with the fewest registered candidates, the one of the
    formula's own party."""
    people = registered(year)
    found: dict[int, tuple[str, list[str], Seated | str]] = {}
    for code, (state, url, senators) in roster(year).items():
        if len(senators) != 3:
            found[code] = (state, [url], f"the roster lists {len(senators)} senators")
            continue
        pools = {
            option: [
                person
                for party in dict.fromkeys(label.partition(": ")[0].split("-"))
                for person in people.get((code, party), [])
            ]
            for option, label in options[code]
        }
        held: Seated = {}
        for name, elected in senators:
            lists = sorted(
                (len(pool), option)
                for option, pool in pools.items()
                if any(same_person(fold(name), person) for person in pool)
            )
            if not lists or len(lists) > 1 and lists[0][0] == lists[1][0]:
                held = {}
                found[code] = (state, [url], f"{name} names a registered candidate on {len(lists)} lists")
                break
            held.setdefault(lists[0][1], []).append((name, elected))
        if held:
            found[code] = (state, [url], held)
    return found


def derived_id(table: str, row: dict[str, object]) -> str:
    """The id the changeset path and Draft give a row of a table without a natural key (note, citation): an md5
    of the table, the pipeline, and the row's columns as Postgres prints them."""
    parts = [table, SOURCE]
    for column in sorted(row):
        value = row[column]
        parts += [column, chr(30) if value is None else str(value)]
    digest = hashlib.md5(chr(31).join(parts).encode()).hexdigest()
    return str(UUID(digest[:12] + "8" + digest[13:16] + "8" + digest[17:]))


def jsonb_text(value: dict[str, str]) -> str:
    """A flat JSON object as Postgres prints a jsonb value: shorter keys first."""
    return json.dumps(dict(sorted(value.items(), key=lambda item: (len(item[0]), item[0]))), ensure_ascii=False)


def artifact_row(source: str, artifact: raw.Artifact) -> dict[str, object]:
    """A source_artifact row with the id Draft gives a new one."""
    return {
        "id": str(uuid5(NAMESPACE, f"artifact\x1f{source}\x1f{artifact.storage_key}")),
        "source_id": source,
        "url": artifact.url,
        "sha256": artifact.sha256,
        "retrieved_at": artifact.retrieved_at,
        "storage_key": artifact.storage_key,
        "media_type": artifact.media_type,
        "bytes": artifact.bytes,
    }


def recite(artifacts: pl.DataFrame) -> pl.DataFrame:
    """Point each source_artifact row whose file the raw cache lacks at the copy of its address the cache holds.
    The published rows cite two such files: a copy of the seats page, which carries a token that changes with each
    request, so no later copy matches it, and an empty response from INE's repository for the 2024 acuerdo, read
    since from the Internet Archive's copy."""
    rows = []
    for row in artifacts.to_dicts():
        if not (DATA / str(row["storage_key"])).exists():
            artifact = ine_file(str(row["url"]))
            print(f"{row['url']}: cites {artifact.storage_key} in place of {row['storage_key']}", flush=True)
            row = artifact_row(str(row["source_id"]), artifact)
        rows.append(row)
    return pl.DataFrame(rows, schema=artifacts.schema)


def declare(out: Path) -> Path:
    """Give the state contests of 2018 and 2024 the seats the Senate's rosters record, in place of the seats derived
    from the casilla count, in the package built at `out`: replace the outcome rows where the two differ, cite the
    roster on each result set, and say so in its methodology note. A state whose roster does not settle its three
    seats keeps the derived seats and its note."""
    keys = pl.read_parquet(out / "external_id.parquet")
    lists = pl.read_parquet(out / "contest_option.parquet").filter(
        pl.col("parent_option_id").is_null() & (pl.col("kind") == "list")
    )
    tables = {
        table: pl.read_parquet(out / f"{table}.parquet")
        for table in ("outcome", "note", "source", "source_artifact", "citation")
    }
    tables["source_artifact"] = recite(tables["source_artifact"])
    contests = dict(keys.filter(pl.col("scheme") == "mx.contest").select("value", "entity_id").iter_rows())
    sets = dict(keys.filter(pl.col("scheme") == "mx.result_set").select("value", "entity_id").iter_rows())
    outcomes, notes = tables["outcome"], tables["note"]
    added: dict[str, list[dict[str, object]]] = {"source": [], "source_artifact": [], "citation": []}
    replaced_sets: list[str] = []
    replaced_notes: dict[str, str] = {}
    new_outcomes: list[dict[str, object]] = []
    for year, (title, page, about) in ROSTERS.items():
        day = DAYS[year].isoformat()
        source = str(uuid5(NAMESPACE, f"source\x1f{SENATE}\x1f{title}"))
        added["source"].append(
            {
                "id": source,
                "kind": "official_body",
                "publisher": SENATE,
                "title": title,
                "url": page,
                "license": ine.PUBLIC,
                "notes": about,
            }
        )
        for url in roster_urls(year).values():
            added["source_artifact"].append(artifact_row(source, raw.cached(SOURCE, url)))
        options = {
            code: list(
                lists.filter(pl.col("contest_id") == str(contests[f"{day}:senate:{code:02d}"]))
                .select("id", "ballot_label")
                .iter_rows()
            )
            for code in range(1, 33)
        }
        labels = {option: label for rows in options.values() for option, label in rows}
        unsettled, changed = [], []
        found = group_seats(day, keys, lists) if year == 2000 else seated(year, options)
        for code, (state, pages, held) in sorted(found.items()):
            if isinstance(held, str):
                unsettled.append(f"{state} ({held})")
                continue
            result_set = str(sets[f"{day}:senate:{code:02d}:state"])
            seats = {option: len(people) for option, people in held.items()}
            if sorted(seats.values()) != [1, 2]:
                raise ValueError(f"{year} {state}: the roster seats {seats}")
            before = dict(
                outcomes.filter(pl.col("result_set_id") == result_set).select("option_id", "seats").iter_rows()
            )
            if before != seats:
                # The notes say the declared seats are the ones the count's ranking gives; a state where they
                # differ needs its own wording.
                changed.append(state)
                continue
            winner = next(option for option, n in seats.items() if n == 2)
            runner_up = next(option for option, n in seats.items() if n == 1)
            principles = {option: {elected for _, elected in people} for option, people in held.items()}
            if principles[winner] - {"majority", None} or principles[runner_up] - {"minority", None}:
                raise ValueError(f"{year} {state}: the roster's principles of election {principles}")
            replaced_sets.append(result_set)
            new_outcomes += [
                {"result_set_id": result_set, "option_id": option, "kind": "elected", "seats": n}
                for option, n in seats.items()
            ]
            names = [name for people in held.values() for name, _ in people]
            text = (SEATED_GROUPS if year == 2000 else SEATED).format(
                state=state,
                source=title,
                names=", ".join(names[:-1]) + f" and {names[-1]}",
                winner=labels[winner].partition(": ")[0],
                runner_up=labels[runner_up].partition(": ")[0],
            )
            note = notes.filter(
                (pl.col("entity_id") == result_set)
                & (pl.col("kind") == "methodology")
                & pl.col("body_md").str.starts_with("The state's count")
            )
            if note.height != 1:
                raise ValueError(f"{year} {state}: {note.height} methodology notes on the state's result set")
            body = str(note["body_md"][0])
            kept = body[: body.index(" The seats follow from this count")]
            replaced_notes[str(note["id"][0])] = f"{kept} {text}"
            for url in pages:
                locator = {"url": url, "state": state}
                added["citation"].append(
                    {
                        "id": derived_id(
                            "citation",
                            {"entity_id": result_set, "source_id": source, "locator": jsonb_text(locator)},
                        ),
                        "entity_id": result_set,
                        "source_id": source,
                        "locator": canonical(locator),
                    }
                )
        print(
            f"{year}: {32 - len(unsettled) - len(changed)} state contests take the roster's seats; left derived: "
            f"{unsettled}, and where the roster's seats differ from the derived ones: {changed}",
            flush=True,
        )
    tables["outcome"] = pl.concat(
        [
            outcomes.filter(~pl.col("result_set_id").is_in(replaced_sets)),
            pl.DataFrame(new_outcomes).select(
                pl.col(column).cast(kind) if column in new_outcomes[0] else pl.lit(None, kind).alias(column)
                for column, kind in outcomes.schema.items()
            ),
        ]
    )
    rows = notes.to_dicts()
    for row in rows:
        if row["id"] in replaced_notes:
            row["body_md"] = replaced_notes[row["id"]]
            row["id"] = derived_id(
                "note", {"entity_id": row["entity_id"], "kind": row["kind"], "body_md": row["body_md"]}
            )
    tables["note"] = pl.DataFrame(rows, schema=notes.schema)
    for table, new in added.items():
        frame = tables[table]
        extra = pl.DataFrame(new).select(
            pl.col(column).cast(kind) if column in new[0] else pl.lit(None, kind).alias(column)
            for column, kind in frame.schema.items()
        )
        tables[table] = pl.concat([frame.filter(~pl.col("id").is_in(extra["id"].to_list())), extra])
    manifest = json.loads((out / "package.json").read_text())
    before = {code: n for code, n, _ in package.check(out)}
    for table, frame in tables.items():
        frame.write_parquet(out / f"{table}.parquet", statistics=False)
    counts = {**manifest["tables"], **{table: frame.height for table, frame in tables.items()}}
    with connect() as conn:
        # As a Draft package does, the manifest cites each file with its url and sha256, which the inbox verifies
        # the cache against.
        cited = package._cited(conn, package._adopted(conn, SOURCE)[1], tables["source_artifact"])
    built = package._seal(KEY, SOURCE, SUMMARY, out, counts, manifest["adopts"], cited, None)
    after = {code: n for code, n, _ in package.check(out)}
    if after != before:
        raise ValueError(f"{KEY}: the declared seats move the check findings from {before} to {after}")
    built["accepted"] = manifest["accepted"]
    (out / "package.json").write_text(json.dumps(built, indent=1) + "\n")
    return out


def package_from_export() -> Path:
    """Build mx.senado/all from the archive's published rows (`ea package export`) with the one change `build`
    makes to them, the declared seats of 2018 and 2024 (`declare`). It reads none of INE's casilla files, so it
    runs where they are not cached."""
    return declare(package.export(SOURCE))
