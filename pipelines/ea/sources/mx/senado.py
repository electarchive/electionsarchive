import io
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
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


def siceef_url(name: str) -> str:
    return f"{ine.SICEEF}{name}.csv"


def fetch() -> None:
    for name in (*SICEEF_STATE.values(), *SICEEF_LIST.values()):
        raw.fetch(RAW, siceef_url(name), suffix=".csv")
    for url, *_ in COMPUTOS.values():
        raw.fetch(RAW, url, suffix=".zip")
    raw.fetch(RAW, SEATS_PAGE, suffix=".html")
    for url, *_ in ACUERDOS.values():
        raw.fetch(RAW, url, suffix=".pdf")


def read_siceef(name: str) -> tuple[list[str], list[dict[str, str]]]:
    import csv

    text = raw.cached(RAW, siceef_url(name)).path.read_bytes().decode("latin-1")
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


def emit() -> Path:
    from ea.changeset import slug

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Elections to the Senate of Mexico (1991-2024): the 32 state contests and the national list, by "
            "casilla, with the party-list seats as declared",
            polity="MX",
        )
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
            cs.artifact(raw.cached(RAW, siceef_url(name_)), ine_source)
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
            cs.artifact(raw.cached(RAW, url), source)

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
        return cs.write()
