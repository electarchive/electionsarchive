import csv
import io
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "mx.ine"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

SICEEF = "http://siceef.ine.mx/BD/"
COMPUTOS = {
    2018: ("https://computos2018.ine.mx/20180708_2130_CW.zip", "20180708_2130_CW_presidencia.zip"),
    2024: ("https://computos2024.ine.mx/20240608_2030_COMPUTOS.zip", "20240608_2030_COMPUTOS_PRES.zip"),
}
SICEEF_YEARS = (1994, 2000, 2006, 2012)
TOTAL_GAPS = {(2012, "12", "7", "52", "1994", "B"): 1, (2012, "14", "3", "94", "2375", "B"): -2}

KEYS = ("state", "district", "municipality", "section", "casilla")
NONVOTES = {"NUM_VOTOS_CAN_NREG", "CNR", "CANDIDATO/A NO REGISTRADO/A", "NUM_VOTOS_NULOS", "VN", "VOTOS NULOS"}
UNREGISTERED = {"NUM_VOTOS_CAN_NREG", "CNR", "CANDIDATO/A NO REGISTRADO/A"}
TRAILING = {
    "TOTAL_VOTOS",
    "TOTAL_VOTOS_CALCULADOS",
    "LISTA_NOMINAL",
    "LISTA_NOMINAL_CASILLA",
    "ESTATUS_ACTA",
    "TEPJF",
    "OBSERVACIONES",
    "RUTA_ACTA",
    "MECANISMOS_TRASLADO",
    "FECHA_HORA",
    "",
}

INE = "Instituto Nacional Electoral (INE)"
INE_TITLE = "Cómputos distritales de la elección de Presidencia de la República, por casilla (1994-2024)"
INE_PAGE = "https://siceef.ine.mx/"
INE_LICENSE = "Public information INE publishes for download (Ley General de Transparencia, art. 70); no license stated"
TEPJF = "Tribunal Electoral del Poder Judicial de la Federación, Sala Superior"
CAMARA = "Cámara de Diputados del H. Congreso de la Unión"
PUBLIC = "Official public record; no license stated"
DICTAMENES = {
    1994: (
        "https://cronica.diputados.gob.mx/Debates/56/1er/Ord1/L56A1P107-02.pdf",
        CAMARA,
        (
            "Dictamen de la Comisión de Gobernación y Puntos Constitucionales que califica la elección de Presidente "
            "de los Estados Unidos Mexicanos de 1994, Colegio Electoral (Diario de los Debates, LVI Legislatura)"
        ),
        31,
    ),
    2000: (
        "https://www.te.gob.mx/transparencia/media/pdf/94b09cce3205872.pdf",
        TEPJF,
        (
            "Dictamen relativo al cómputo final de la elección de Presidente de los Estados Unidos Mexicanos, a la "
            "declaración de validez de la elección y a la de Presidente electo (2 de agosto de 2000)"
        ),
        17,
    ),
    2006: (
        "https://www.ordenjuridico.gob.mx/Federal/PJ/TEPJF/Dictamen/08092006%281%29.pdf",
        TEPJF,
        (
            "Dictamen relativo al cómputo final de la elección de Presidente de los Estados Unidos Mexicanos, "
            "declaración de validez de la elección y de Presidente electo (Diario Oficial de la Federación, "
            "8 de septiembre de 2006)"
        ),
        16,
    ),
    2012: (
        "https://te.gob.mx/media/SentenciasN/pdf/Superior/SUP-EEP-0001-2012.pdf",
        TEPJF,
        (
            "SUP-EEP-1/2012: cómputo final, calificación jurisdiccional de la elección, declaración de validez y de "
            "Presidente electo de los Estados Unidos Mexicanos (31 de agosto de 2012)"
        ),
        71,
    ),
    2018: (
        "https://www.te.gob.mx/media/SentenciasN/pdf/Superior/SUP-EEP-0001-2018.pdf",
        TEPJF,
        (
            "SUP-EEP-1/2018: dictamen relativo al cómputo de la elección de Presidente de los Estados Unidos Mexicanos, "
            "a la declaración de validez de la elección y a la de Presidente electo (8 de agosto de 2018)"
        ),
        9,
    ),
    2024: (
        "https://te.gob.mx/media/files/DICTAMEN-v2.pdf",
        TEPJF,
        (
            "Cómputo final de la elección, declaración de validez y declaración de Presidenta electa de los Estados "
            "Unidos Mexicanos (14 de agosto de 2024)"
        ),
        32,
    ),
}
DAYS = {
    1994: date(1994, 8, 21),
    2000: date(2000, 7, 2),
    2006: date(2006, 7, 2),
    2012: date(2012, 7, 1),
    2018: date(2018, 7, 1),
    2024: date(2024, 6, 2),
}
PARTIES = {
    "PAN": ("Q851087", "Partido Acción Nacional", "National Action Party"),
    "PRI": ("Q682969", "Partido Revolucionario Institucional", "Institutional Revolutionary Party"),
    "PRD": ("Q767010", "Partido de la Revolución Democrática", "Party of the Democratic Revolution"),
    "PVEM": ("Q383718", "Partido Verde Ecologista de México", "Ecologist Green Party of Mexico"),
    "PT": ("Q540320", "Partido del Trabajo", "Labor Party"),
    "MC": ("Q10920094", "Movimiento Ciudadano", "Citizens' Movement"),
    "NA": ("Q2080372", "Nueva Alianza", "New Alliance Party"),
    "MORENA": ("Q15717618", "Morena", "National Regeneration Movement"),
    "PES": ("Q6064040", "Partido Encuentro Social", "Social Encounter Party"),
    "PPS": ("Q753446", "Partido Popular Socialista", "Popular Socialist Party"),
    "PFCRN": (
        "Q267886",
        "Partido del Frente Cardenista de Reconstrucción Nacional",
        "Party of the Cardenist Front of National Reconstruction",
    ),
    "PARM": ("Q753453", "Partido Auténtico de la Revolución Mexicana", "Authentic Party of the Mexican Revolution"),
    "PDM": ("Q3979015", "Partido Demócrata Mexicano", "Mexican Democratic Party"),
    "PCD": ("Q2058323", "Partido de Centro Democrático", "Democratic Center Party"),
    "DS": ("Q2021880", "Democracia Social", "Social Democracy"),
    "PSN": ("Q2173623", "Partido de la Sociedad Nacionalista", "Party of the Nationalist Society"),
    "PAS": ("Q2016845", "Partido Alianza Social", "Social Alliance Party"),
    "PASC": (
        "Q1803295",
        "Partido Alternativa Socialdemócrata y Campesina",
        "Social Democratic and Peasant Alternative",
    ),
}
ALLIANCES = {
    "AC": ("Q4732347", "Alianza por el Cambio", "Alliance for Change", ("PAN", "PVEM")),
    "AM2000": ("Q5668629", "Alianza por México", "Alliance for Mexico", ("PRD", "PT", "MC", "PSN", "PAS")),
    "APM": ("Q4732384", "Alianza por México", "Alliance for Mexico", ("PRI", "PVEM")),
    "PBT": ("Q495025", "Coalición por el Bien de Todos", "Coalition for the Good of All", ("PRD", "PT", "MC")),
    "CPM": ("Q5780148", "Compromiso por México", "Commitment to Mexico", ("PRI", "PVEM")),
    "MP": ("Q54823817", "Movimiento Progresista", "Progressive Movement", ("PRD", "PT", "MC")),
    "PMF": ("Q39082201", "Por México al Frente", "For Mexico to the Front", ("PAN", "PRD", "MC")),
    "TPM": ("Q47002791", "Todos por México", "Everyone for Mexico", ("PRI", "PVEM", "NA")),
    "JHH": ("Q45355857", "Juntos Haremos Historia", "Together We Will Make History", ("PT", "MORENA", "PES")),
    "FCM": ("Q120064818", "Fuerza y Corazón por México", "Strength and Heart for Mexico", ("PAN", "PRI", "PRD")),
    "SHH": ("Q123506661", "Sigamos Haciendo Historia", "Let's Keep Making History", ("PVEM", "PT", "MORENA")),
}
PERSONS = {
    "Q2108989": ("Diego Fernández de Cevallos", "male"),
    "Q298149": ("Ernesto Zedillo Ponce de León", "male"),
    "Q2527875": ("Marcela Lombardo Otero", "female"),
    "Q922837": ("Cuauhtémoc Cárdenas Solórzano", "male"),
    "Q2591506": ("Rafael Aguilar Talamantes", "male"),
    "Q2458498": ("Álvaro Pérez Treviño", "male"),
    "Q55179": ("Pablo Emilio Madero Belden", "male"),
    "Q2320159": ("Cecilia Soto González", "female"),
    "Q1909814": ("Jorge González Torres", "male"),
    "Q190572": ("Vicente Fox Quesada", "male"),
    "Q966913": ("Francisco Labastida Ochoa", "male"),
    "Q2174052": ("Manuel Camacho Solís", "male"),
    "Q1956741": ("Porfirio Muñoz Ledo", "male"),
    "Q2479628": ("Gilberto Rincón Gallardo", "male"),
    "Q57269": ("Felipe Calderón Hinojosa", "male"),
    "Q713557": ("Roberto Madrazo Pintado", "male"),
    "Q318508": ("Andrés Manuel López Obrador", "male"),
    "Q2169311": ("Roberto Campa Cifrián", "male"),
    "Q3369131": ("Patricia Mercado Castro", "female"),
    "Q466819": ("Josefina Vázquez Mota", "female"),
    "Q296741": ("Enrique Peña Nieto", "male"),
    "Q391024": ("Gabriel Quadri de la Torre", "male"),
    "Q2894271": ("Ricardo Anaya Cortés", "male"),
    "Q3186168": ("José Antonio Meade Kuribreña", "male"),
    "Q2370402": ("Margarita Zavala Gómez del Campo", "female"),
    "Q5480056": ("Jaime Rodríguez Calderón", "male"),
    "Q5771800": ("Claudia Sheinbaum Pardo", "female"),
    "Q65430971": ("Xóchitl Gálvez Ruiz", "female"),
    "Q16582362": ("Jorge Álvarez Máynez", "male"),
}
CANDIDATES: dict[int, list[tuple[str, str | None, str | None, int | None, dict[str, str]]]] = {
    1994: [
        ("Q298149", "PRI", None, 17160011, {"PRI": "PRI"}),
        ("Q2108989", "PAN", None, 9142679, {"PAN": "PAN"}),
        ("Q922837", "PRD", None, 5838021, {"PRD": "PRD"}),
        ("Q2320159", "PT", None, 967916, {"PT": "PT"}),
        ("Q1909814", "PVEM", None, 327519, {"PVEM": "PVEM"}),
        ("Q2591506", "PFCRN", None, 298279, {"PFCRN": "PFCRN"}),
        ("Q2458498", "PARM", None, 193135, {"PARM": "PARM"}),
        ("Q2527875", "PPS", None, 166904, {"PPS": "PPS"}),
        ("Q55179", "PDM", None, 98258, {"UNO_PDM": "UNO-PDM"}),
    ],
    2000: [
        ("Q190572", None, "AC", 15988545, {"AC": "Alianza por el Cambio"}),
        ("Q966913", "PRI", None, 13574677, {"PRI": "PRI"}),
        ("Q922837", None, "AM2000", 6259018, {"AM": "Alianza por México"}),
        ("Q2479628", "DS", None, 592072, {"DSPPN": "DS"}),
        ("Q2174052", "PCD", None, 208257, {"PCD": "PCD"}),
        ("Q1956741", "PARM", None, 157119, {"PARM": "PARM"}),
    ],
    2006: [
        ("Q57269", "PAN", None, 14916927, {"PAN": "PAN"}),
        ("Q318508", None, "PBT", 14683096, {"PBT": "Coalición por el Bien de Todos"}),
        ("Q713557", None, "APM", 9237000, {"APM": "Alianza por México"}),
        ("Q3369131", "PASC", None, 1124280, {"ASDC": "Alternativa"}),
        ("Q2169311", "NA", None, 397550, {"NVA_ALIANZA": "Nueva Alianza"}),
    ],
    2012: [
        ("Q296741", None, "CPM", 19158592, {"PRI": "PRI", "PVEM": "PVEM", "PRI_PVEM": "PRI-PVEM"}),
        (
            "Q318508",
            None,
            "MP",
            15848827,
            {
                "PRD": "PRD",
                "PT": "PT",
                "MC": "MC",
                "PRD_PT_MC": "PRD-PT-MC",
                "PRD_PT": "PRD-PT",
                "PRD_MC": "PRD-MC",
                "PT_MC": "PT-MC",
            },
        ),
        ("Q466819", "PAN", None, 12732630, {"PAN": "PAN"}),
        ("Q391024", "NA", None, 1146085, {"NVA_ALIANZA": "Nueva Alianza"}),
    ],
    2018: [
        (
            "Q318508",
            None,
            "JHH",
            30110327,
            {
                "MORENA": "MORENA",
                "PT": "PT",
                "ENCUENTRO SOCIAL": "PES",
                "PT_MORENA_PES": "PT-MORENA-PES",
                "PT_MORENA": "PT-MORENA",
                "PT_PES": "PT-PES",
                "MORENA_PES": "MORENA-PES",
            },
        ),
        (
            "Q2894271",
            None,
            "PMF",
            12607779,
            {
                "PAN": "PAN",
                "PRD": "PRD",
                "MOVIMIENTO CIUDADANO": "MC",
                "PAN_PRD_MC": "PAN-PRD-MC",
                "PAN_PRD": "PAN-PRD",
                "PAN_MC": "PAN-MC",
                "PRD_MC": "PRD-MC",
            },
        ),
        (
            "Q3186168",
            None,
            "TPM",
            9288750,
            {
                "PRI": "PRI",
                "PVEM": "PVEM",
                "NUEVA ALIANZA": "NA",
                "PRI_PVEM_NA": "PRI-PVEM-NA",
                "PRI_PVEM": "PRI-PVEM",
                "PRI_NA": "PRI-NA",
                "PVEM_NA": "PVEM-NA",
            },
        ),
        ("Q5480056", None, None, 2960461, {"CAND_IND_02": "Candidato independiente"}),
        ("Q2370402", None, None, None, {"CAND_IND_01": "Candidata independiente"}),
    ],
    2024: [
        (
            "Q5771800",
            None,
            "SHH",
            35924519,
            {
                "MORENA": "MORENA",
                "PVEM": "PVEM",
                "PT": "PT",
                "PVEM_PT_MORENA": "PVEM-PT-MORENA",
                "PVEM_PT": "PVEM-PT",
                "PVEM_MORENA": "PVEM-MORENA",
                "PT_MORENA": "PT-MORENA",
            },
        ),
        (
            "Q65430971",
            None,
            "FCM",
            16502697,
            {
                "PAN": "PAN",
                "PRI": "PRI",
                "PRD": "PRD",
                "PAN_PRI_PRD": "PAN-PRI-PRD",
                "PAN_PRI": "PAN-PRI",
                "PAN_PRD": "PAN-PRD",
                "PRI_PRD": "PRI-PRD",
            },
        ),
        ("Q16582362", "MC", None, 6204710, {"MC": "MC"}),
    ],
}
LINE_PARTIES = {"MOVIMIENTO CIUDADANO": "MC", "NUEVA ALIANZA": "NA", "ENCUENTRO SOCIAL": "PES"}
DECLARED = {
    1994: (18279, 990414, 35201415),
    2000: (32457, 789828, 37601973),
    2006: (298204, 900373, 41557430),
    2012: (20625, 1236857, 50143616),
    2018: (31979, 1602578, 56601874),
    2024: (83114, 1400144, 60115184),
}
CANDIDATURAS = {2018: "presidencia_candidaturas_2018.csv", 2024: "PRES_CANDIDATURAS_2024.csv"}
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
EARLY, ABROAD, PRISON = 2, 4, 7
WINNERS = {1994: "Q298149", 2000: "Q190572", 2006: "Q57269", 2012: "Q296741", 2018: "Q318508", 2024: "Q5771800"}
SYSTEM = (
    "The President of Mexico is elected by a plurality of the national vote in a single round, for a six-year term "
    "with no reelection. The Sala Superior of the electoral tribunal (TEPJF) makes the final count, declares the "
    "election valid, and declares the president-elect; before 1996 the Chamber of Deputies did so as an electoral "
    "college."
)
NATIONAL_METHOD = (
    "The national result as declared: {declarer} final count of the district counts, after the rulings on "
    "challenges, transcribed from page {page} of the document. Valid votes are the total less the null votes."
)
CASILLA_METHOD = (
    "Polling-station (casilla) results from INE's district count files. The casillas add to {total:,} votes, "
    "against {declared:,} in the declared final count, which reflects the annulments and recounts ordered on "
    "challenge; the declared set is the default. Casillas sit within their electoral section as INE's 2017 and "
    "2023 section maps draw it, or within their municipality or state where the section is no longer mapped."
)
FUSION = (
    " From 2012 the ballot lets a voter mark one or more parties of a coalition; each marked combination is "
    "counted as its own line under the coalition's candidate, and the candidate's votes are the sum of the lines."
)
ANNULLED = " The file marks {count:,} casillas as annulled ({votes:,} votes still printed for them)."
ZAVALA = (
    "Margarita Zavala withdrew her independent candidacy in May 2018 after the ballots were printed. The tribunal "
    "treated her box as blank, so the declared count gives her no votes; INE's casilla file prints the marks in "
    "her box in its own column."
)


def siceef_url(year: int) -> str:
    return f"{SICEEF}Presidente{year}.csv"


def fetch() -> None:
    for year in SICEEF_YEARS:
        raw.fetch(SOURCE, siceef_url(year), suffix=".csv")
    for url, _ in COMPUTOS.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    for url, *_ in DICTAMENES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def clean(value: str) -> str:
    return value.strip().removeprefix("=").strip("'\"")


def number(value: str) -> int:
    value = clean(value).replace(",", "")
    return int(value) if value not in {"", "-", "N/A"} else 0


def siceef(year: int) -> tuple[list[str], list[dict[str, str]]]:
    text = raw.cached(SOURCE, siceef_url(year)).path.read_bytes().decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    rows = [
        {
            "state": row["ID_ESTADO"],
            "district": row["ID_DISTRITO"],
            "municipality": row["ID_MUNICIPIO"],
            "section": row["SECCION"],
            "casilla": row["CASILLA"],
            **row,
        }
        for row in reader
    ]
    columns = list(reader.fieldnames or [])
    return columns[columns.index("CASILLA") + 1 :], rows


def computos(year: int) -> tuple[list[str], list[dict[str, str]], int]:
    url, inner = COMPUTOS[year]
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as outer, zipfile.ZipFile(io.BytesIO(outer.read(inner))) as pres:
        name = next(n for n in pres.namelist() if n.upper().endswith((f"PRES_{year}.CSV", "PRESIDENCIA.CSV")))
        lines = pres.read(name).decode("latin-1").splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("CLAVE_CASILLA"))
    summary = next(i for i, line in enumerate(lines) if line.startswith("ACTAS_ESPERADAS"))
    printed = dict(zip(lines[summary].split("|"), lines[summary + 1].split("|"), strict=True))
    reader = csv.DictReader(io.StringIO("\n".join(lines[head:])), delimiter="|")
    columns = list(reader.fieldnames or [])
    state = "ID_ESTADO" if "ID_ESTADO" in columns else "ID_ENTIDAD"
    district = "ID_DISTRITO" if "ID_DISTRITO" in columns else "ID_DISTRITO_FEDERAL"
    rows = []
    for row in reader:
        rows.append(
            {
                "state": clean(row[state]),
                "district": clean(row[district]),
                "municipality": "",
                "section": clean(row["SECCION"]),
                "casilla": clean(row["TIPO_CASILLA"]) + clean(row["ID_CASILLA"]) + clean(row["EXT_CONTIGUA"]),
                **row,
            }
        )
    start = columns.index("TIPO_ACTA" if "TIPO_ACTA" in columns else "NUM_ACTA_IMPRESO") + 1
    return columns[start:], rows, number(printed["TOTAL_VOTOS"])


def table(year: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    printed = None
    if year in COMPUTOS:
        columns, rows, printed = computos(year)
    else:
        columns, rows = siceef(year)
    options = [c for c in columns if c not in TRAILING]
    total = "TOTAL_VOTOS" if "TOTAL_VOTOS" in columns else "TOTAL_VOTOS_CALCULADOS"
    roll = next((c for c in ("LISTA_NOMINAL", "LISTA_NOMINAL_CASILLA") if c in columns), None)
    units, votes = [], []
    for row in rows:
        counts = {option: number(row[option]) for option in options}
        gap = TOTAL_GAPS.get((year, *(clean(row[k]) for k in KEYS)), 0)
        if sum(counts.values()) - number(row[total]) != gap:
            raise ValueError(
                f"{year} {[row[k] for k in KEYS]}: options add to {sum(counts.values())}, total {row[total]}"
            )
        unit = {k: clean(row[k]).lstrip("0") or "0" if k != "casilla" else clean(row[k]) for k in KEYS}
        units.append(
            unit
            | {
                "total": sum(counts.values()),
                "printed_total": number(row[total]),
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
    units_frame = pl.DataFrame(units, schema_overrides={"registered": pl.Int64})
    duplicates = units_frame.group_by(KEYS).len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(f"{year}: duplicate casillas {duplicates.head()}")
    if printed is not None and printed != units_frame["printed_total"].sum():
        raise ValueError(f"{year}: casillas add to {units_frame['total'].sum()}, the file prints {printed}")
    return units_frame, pl.DataFrame(votes)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in (*SICEEF_YEARS, *COMPUTOS):
        units, votes = table(year)
        units.write_parquet(PARSED / f"presidente_{year}_casillas.parquet")
        votes.write_parquet(PARSED / f"presidente_{year}_votes.parquet")
        national = votes.group_by("option").agg(pl.col("votes").sum()).sort("votes", descending=True)
        print(year, units.height, "casillas", units["total"].sum(), "votes", dict(national.rows()))


def casilla_code(year: int, value: str) -> str:
    if year == 2018:
        kind, number, extension = value[0], value[1:5], value[5:]
    elif year == 2024:
        kind, number, extension = value[0], value[1:3], value[3:]
    else:
        match = re.fullmatch(r"([A-Z])(\d*)(?:C(\d+))?", value)
        if match is None:
            raise ValueError(f"{year}: casilla {value!r}")
        kind, number, extension = match.group(1), match.group(2) or "1", match.group(3) or "0"
    if kind == "B":
        return "B"
    return f"{kind}{int(number)}" + (f"C{int(extension)}" if int(extension) else "")


def lines_of(year: int) -> dict[str, str]:
    return {column: qid for qid, *_, columns in CANDIDATES[year] for column in columns}


def check_candidaturas(year: int) -> None:
    url, inner = COMPUTOS[year]
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as outer, zipfile.ZipFile(io.BytesIO(outer.read(inner))) as pres:
        name = next(n for n in pres.namelist() if n.endswith(CANDIDATURAS[year]))
        rows = list(csv.DictReader(io.StringIO(pres.read(name).decode("latin-1").split("\n", 1)[1]), delimiter="|"))
    printed = {LINE_PARTIES.get(row["PARTIDO_CI"], row["PARTIDO_CI"]): row["CANDIDATURA_PROPIETARIA"] for row in rows}
    ours = {LINE_PARTIES.get(column, column): qid for column, qid in lines_of(year).items()}
    independents = sorted(code for code in printed if code.startswith("CI_"))
    ours |= {code: ours.pop(f"CAND_IND_{i:02d}") for i, code in enumerate(independents, 1)}
    grouped: dict[str, set[str]] = {}
    for code, person in printed.items():
        grouped.setdefault(person, set()).add(code)
    if sorted(map(sorted, grouped.values())) != sorted(
        map(sorted, ({code for code, qid in ours.items() if qid == q} for q in set(ours.values())))
    ):
        raise ValueError(f"{year}: candidate columns differ from {CANDIDATURAS[year]}")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential elections of Mexico (1994-2024): the declared national counts from the electoral "
            "tribunal and the 1994 electoral college, and INE's district count files by polling station",
            polity="MX",
        )
        for scheme in ("mx.entidad", "mx.municipio", "mx.seccion", "mx.casilla"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.ine")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        ine = cs.source(INE, INE_TITLE)
        cs.add(
            "source",
            id=ine,
            kind="official_body",
            publisher=INE,
            title=INE_TITLE,
            url=INE_PAGE,
            license=INE_LICENSE,
            license_url=INE_PAGE,
            attribution="Instituto Nacional Electoral, cómputos distritales",
        )
        for year in SICEEF_YEARS:
            cs.artifact(raw.cached(SOURCE, siceef_url(year)), ine)
        for url, _ in COMPUTOS.values():
            cs.artifact(raw.cached(SOURCE, url), ine)
        declarers: dict[int, UUID] = {}
        for year, (url, publisher, title, _) in DICTAMENES.items():
            source = declarers[year] = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=PUBLIC,
            )
            cs.artifact(raw.cached(SOURCE, url), source)

        office = cs.key("mx.office", "president")
        cs.add("office", id=office, polity_id=mexico, kind="head_of_state")
        name(office, "President of Mexico")
        name(office, "Presidente de los Estados Unidos Mexicanos", "es", "native")
        cs.add("external_id", entity_id=office, scheme="wikidata", value="Q628004")
        system = cs.key("mx.electoral_system", "president")
        cs.add("electoral_system", id=system, polity_id=mexico, rules={"formula": "fptp"}, rules_version=1)
        name(system, "Mexican presidential plurality system")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        parties: dict[str, UUID] = {}

        def party(abbrev: str) -> UUID:
            if abbrev not in parties:
                item, native, english = PARTIES[abbrev]
                parties[abbrev] = cs.key("mx.party", item)
                if parties[abbrev] in cs.minted:
                    cs.add("party", id=parties[abbrev], kind="party", polity_id=mexico)
                    name(parties[abbrev], native, "es")
                    name(parties[abbrev], english, "en", "english")
                    name(parties[abbrev], abbrev, "es", "abbreviation")
                    cs.add("external_id", entity_id=parties[abbrev], scheme="wikidata", value=item)
            return parties[abbrev]

        persons: dict[str, UUID] = {}

        def person(item: str) -> UUID:
            if item not in persons:
                persons[item] = cs.key("mx.person", item)
                if persons[item] in cs.minted:
                    full, gender = PERSONS[item]
                    cs.add("person", id=persons[item], gender=gender)
                    name(persons[item], full)
                    cs.add("external_id", entity_id=persons[item], scheme="wikidata", value=item)
            return persons[item]

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
        unit_rows: dict[UUID, tuple[str, str, UUID | None, str, UUID, str]] = {
            nation_unit: ("mx.nation", "MX", None, "equals", cs.area("mx.nation", "MX"), "standard")
        }
        state_units: dict[str, UUID] = {}
        for code in range(1, 33):
            state = f"{code:02d}"
            unit = state_units[state] = cs.unit("mx.entidad", state)
            unit_rows[unit] = ("mx.entidad", state, nation_unit, "equals", cs.area("mx.entidad", state), "standard")

        values: list[tuple[str, str, str | None, int, int, int]] = []
        frames: list[pl.DataFrame] = []
        for year, day in DAYS.items():
            election = cs.key("mx.election", f"{day}:general")
            cs.add("election", id=election, polity_id=mexico, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} Mexican general election")
            name(election, f"Elecciones federales de {year}", "es", "native")
            path = f"mx/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("mx.contest", f"{day}:president")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                magnitude=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"{year} Mexican presidential election")
            name(contest, f"Elección de Presidente de los Estados Unidos Mexicanos de {year}", "es", "native")
            cs.add("slug", path=f"{path}/president", entity_id=contest)

            alliances: dict[str, UUID] = {}
            lines: dict[str, UUID] = {}
            heads: dict[str, UUID] = {}
            for position, (item, own, coalition, _, columns) in enumerate(CANDIDATES[year], 1):
                alliance = None
                if coalition is not None:
                    qid, native, english, members = ALLIANCES[coalition]
                    alliance = alliances[coalition] = cs.key("mx.alliance", f"{year}:{coalition}")
                    cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=election)
                    name(alliance, native, "es")
                    name(alliance, english, "en", "english")
                    cs.add("external_id", entity_id=alliance, scheme="wikidata", value=qid)
                    for member in members:
                        cs.add("alliance_member", alliance_id=alliance, party_id=party(member))
                option = heads[item] = cs.key("mx.option", f"{day}:president:{item}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if own is None else party(own),
                    alliance_id=alliance,
                    ballot_position=position,
                    ballot_label=PERSONS[item][0],
                )
                candidacy = cs.key("mx.candidacy", f"{day}:president:{item}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person(item),
                    option_id=option,
                    role="head",
                    ballot_name=PERSONS[item][0],
                    is_independent=own is None and coalition is None,
                )
                nominators = [own] if own is not None else list(ALLIANCES[coalition][3]) if coalition else []
                for abbrev in nominators:
                    cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party(abbrev), kind="nominated")
                if len(columns) == 1:
                    lines[next(iter(columns))] = option
                    continue
                for column, label in columns.items():
                    line = lines[column] = cs.key("mx.option", f"{day}:president:{item}:{column}")
                    single = LINE_PARTIES.get(column, column)
                    cs.add(
                        "contest_option",
                        id=line,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=option,
                        party_id=party(single) if single in PARTIES else None,
                        alliance_id=None if single in PARTIES else alliance,
                        ballot_label=label,
                    )
            write_in = cs.key("mx.option", f"{day}:president:unregistered")
            cs.add(
                "contest_option",
                id=write_in,
                contest_id=contest,
                kind="write_in",
                ballot_label="Candidatos no registrados",
            )

            def make_set(label: str, default: bool, source: UUID, completeness: float, note: str) -> UUID:
                result_set = cs.key("mx.result_set", f"{day}:president:{label}")
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

            unregistered, null, total = DECLARED[year]
            declared = {item: votes for item, *_, votes, _ in CANDIDATES[year] if votes is not None}
            if sum(declared.values()) + unregistered + null != total:
                raise ValueError(f"{year}: the declared figures do not add to {total}")
            url, publisher, _, page = DICTAMENES[year]
            declarer = "the electoral tribunal's Sala Superior" if publisher == TEPJF else "the Chamber of Deputies'"
            national = make_set(
                "national", True, declarers[year], 1, NATIONAL_METHOD.format(declarer=declarer, page=page)
            )
            cs.add("citation", entity_id=national, source_id=declarers[year], locator={"page": page})
            if year == 2018:
                cs.add("note", entity_id=national, kind="caveat", body_md=ZAVALA)
            unit = str(nation_unit)
            for item, votes in declared.items():
                values.append((str(national), unit, str(heads[item]), VOTES, votes, 0))
            values.append((str(national), unit, str(write_in), VOTES, unregistered, 0))
            for measure, value in ((BALLOTS, total), (VALID, total - null), (INVALID, null)):
                values.append((str(national), unit, None, measure, value, 0))
            winner = WINNERS[year]
            if max(declared, key=lambda item: declared[item]) != winner:
                raise ValueError(f"{year}: the declared winner is not the leader")
            cs.add(
                "outcome",
                result_set_id=national,
                option_id=heads[winner],
                kind="elected",
                seats=1,
            )
            if year == 2018:
                cs.add("outcome", result_set_id=national, option_id=heads["Q2370402"], kind="withdrew", seats=None)

            if year in CANDIDATURAS:
                check_candidaturas(year)
            casillas = pl.read_parquet(PARSED / f"presidente_{year}_casillas.parquet")
            votes = pl.read_parquet(PARSED / f"presidente_{year}_votes.parquet")
            casillas = casillas.filter((pl.col("casilla") != "") | (pl.col("total") > 0))
            columns = lines_of(year)
            unknown = set(votes.filter(pl.col("kind") == "list")["option"]) - set(columns)
            if unknown:
                raise ValueError(f"{year}: columns without a candidate: {sorted(unknown)}")
            got = int(casillas["total"].sum())
            annulled = casillas.filter((pl.col("status") == "Anulada") | (pl.col("tepjf") == "Anulada"))
            note = CASILLA_METHOD.format(total=got, declared=total)
            if year >= 2012:
                note += FUSION
            if annulled.height:
                note += ANNULLED.format(count=annulled.height, votes=int(annulled["total"].sum()))
            casilla_set = make_set("casilla", False, ine, min(got / total, 1), note)
            placed = []
            for row in casillas.iter_rows(named=True):
                state = f"{int(row['state']):02d}"
                district = int(row["district"]) if row["district"].isdigit() else 0
                section = int(row["section"])
                letter = row["casilla"][:1] or "X"
                code = f"{year}:{state}-{district:02d}-{section:04d}-{casilla_code(year, row['casilla'] or 'X')}"
                channel, kind, area = 0, "standard", None
                if letter in "MV" or section == 0:
                    channel, kind = ABROAD, "aggregate"
                elif letter == "A":
                    channel, kind = EARLY, "aggregate"
                elif letter == "P":
                    channel, kind = PRISON, "prison"
                else:
                    area = sections.get(f"{state}{section:04d}")
                    if area is None and row["municipality"] not in ("", "0"):
                        area = towns.get(f"{state}{int(row['municipality']):03d}")
                unit_id = cs.unit("mx.casilla", code)
                if unit_id in unit_rows:
                    raise ValueError(f"{year}: casilla {code} appears twice")
                unit_rows[unit_id] = (
                    "mx.casilla",
                    code,
                    state_units[state],
                    "within",
                    area or cs.area("mx.entidad", state),
                    kind,
                )
                placed.append((*(row[k] for k in KEYS), str(unit_id), channel))
            units = pl.DataFrame(
                placed,
                schema=[*((k, pl.String) for k in KEYS), ("unit_id", pl.String), ("channel", pl.Int16)],
                orient="row",
            )
            located = casillas.join(units, on=KEYS)
            joined = votes.join(units, on=KEYS)
            options = {column: str(line) for column, line in lines.items()}
            parts = [
                located.filter(pl.col("registered") > 0).select(
                    "unit_id",
                    "channel",
                    option_id=pl.lit(None, pl.String),
                    measure=pl.lit(REGISTERED),
                    value="registered",
                ),
                located.select(
                    "unit_id", "channel", option_id=pl.lit(None, pl.String), measure=pl.lit(BALLOTS), value="total"
                ),
                joined.filter(pl.col("kind") == "invalid").select(
                    "unit_id", "channel", option_id=pl.lit(None, pl.String), measure=pl.lit(INVALID), value="votes"
                ),
                joined.filter(pl.col("kind") != "invalid")
                .group_by("unit_id", "channel")
                .agg(value=pl.col("votes").sum())
                .select("unit_id", "channel", option_id=pl.lit(None, pl.String), measure=pl.lit(VALID), value="value"),
                joined.filter((pl.col("kind") != "invalid") & (pl.col("votes") != 0)).select(
                    "unit_id",
                    "channel",
                    option_id=pl.when(pl.col("kind") == "unregistered")
                    .then(pl.lit(str(write_in)))
                    .otherwise(pl.col("option").replace_strict(options, default=None)),
                    measure=pl.lit(VOTES),
                    value="votes",
                ),
            ]
            frames.append(
                pl.concat(
                    [
                        part.select(
                            result_set_id=pl.lit(str(casilla_set)),
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
            print(year, casillas.height, "casillas", got, "votes against", total, "declared")

        minted = [
            {
                "id": str(unit),
                "polity_id": str(mexico),
                "scheme_code": scheme,
                "code": code,
                "parent_id": None if parent is None else str(parent),
                "area_relation": relation,
                "area_id": str(area),
                "kind": kind,
            }
            for unit, (scheme, code, parent, relation, area, kind) in unit_rows.items()
            if unit in cs.minted
        ]
        if minted:
            cs.frame("reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String}))
        national = pl.DataFrame(
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
        )
        cs.frame(
            "result_value",
            pl.concat([national, *frames]).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
