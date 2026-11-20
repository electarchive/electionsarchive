import csv
import io
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import package, raw
from ea.package import Draft
from ea.sources.mx import diputados, ine, mge
from ea.store import DATA, connect

SOURCE = "mx.congreso_local"
VERSION = "1"
KEY = f"{SOURCE}/all"
PARSED = DATA / "parsed" / SOURCE
LICENSE = (
    "Public information the institute publishes for download (Ley General de Transparencia, art. 70); no license stated"
)
INDEPENDENT = diputados.INDEPENDENT
NATIONAL = ["PAN", "PRI", "PRD", "PVEM", "PT", "MC", "MORENA"]
REGISTERED, BALLOTS, VALID, INVALID, VOTES = ine.REGISTERED, ine.BALLOTS, ine.VALID, ine.INVALID, ine.VOTES
IEPAC = "https://iepac.mx/public/micrositios/resultados-electorales/procesos-electorales/2024/"

IEE_COLIMA = "https://ieecolima.org.mx/"
IEC = "https://www.iec.org.mx/v1/"
IEC_ACUERDOS = IEC + "archivos//acuerdos/2023/"
IEEPCNL = "https://www.ieepcnl.mx/data/info/sesiones/acuerdos/2024/"

LOCAL_PARTIES = {
    "YUC": {"NAY": (None, "Nueva Alianza Yucatán", "New Alliance Yucatán")},
    "COL": {
        "PNA": (None, "Partido Nueva Alianza Colima", "New Alliance Party of Colima"),
        "PES": (None, "Partido Encuentro Solidario Colima", "Solidarity Encounter Party of Colima"),
        "FPM": (None, "Fuerza por México Colima", "Force for Mexico Colima"),
    },
    "COA": {"UDC": (None, "Unidad Democrática de Coahuila", "Democratic Unity of Coahuila")},
    "NLE": {
        "VIDA": (None, "VIDA Nuevo León", "VIDA Nuevo León"),
        "ESO": (None, "Esperanza Social Nuevo León", "Social Hope Nuevo León"),
        "PL": (None, "Partido Liberal", "Liberal Party (Nuevo León)"),
        "PES": (None, "Partido Encuentro Solidario Nuevo León", "Solidarity Encounter Party of Nuevo León"),
        "PJ": (None, "Partido Justicialista", "Justicialist Party (Nuevo León)"),
    },
}

STATES: dict[str, dict] = {
    "YUC": {
        "day": date(2024, 6, 2),
        "publisher": "Instituto Electoral y de Participación Ciudadana de Yucatán (IEPAC)",
        "page": "https://iepac.mx/public/micrositios/resultados-electorales/",
        "casillas": (IEPAC + "RESULTADOS-DIPUTACIONES-LOC.zip", "2024_DIPUTADOS_CASILLA.csv"),
        "districts": (IEPAC + "RESULTADOS-DIPUTACIONES-LOC.zip", "2024_DIPUTADOS_DISTRITO.csv"),
        "statewide": (
            IEPAC + "computos/COMPUTO_ESTATAL_DIP_LOCALES.xlsx",
            "Cómputo estatal de la elección de diputaciones 2024",
        ),
        "winners": (IEPAC + "DIPUTADOS_ganadores11SEP.xlsx", "Diputaciones electas 2024"),
        "mr": 21,
        "rp": 14,
        "body": ("Congreso del Estado de Yucatán", "Congress of Yucatán"),
        "rp_method": (
            "Votes by party from the institute's statewide count of the deputies election, by district, with the "
            "votes marked for several parties of a coalition distributed among them. Its district totals differ from "
            "the district count database; both are kept as published. Seats by party are as the institute lists the "
            "elected list deputies."
        ),
    },
    "COL": {
        "day": date(2024, 6, 2),
        "publisher": "Instituto Electoral del Estado de Colima (IEE Colima)",
        "page": IEE_COLIMA + "oficial2024.html",
        "casillas": IEE_COLIMA + "resultados%2091-12/2024/INEDIPMR2024/2024_SEE_DIP_LOC_MR_COL_CAS.csv",
        "rp_casillas": (
            IEE_COLIMA + "resultados%2091-12/2024/INEDIPRP2024/2024_SEE_DIP_LOC_RP_COL_CAS.csv",
            "Cómputos distritales de la elección de diputaciones locales de representación proporcional 2024, por casilla",
        ),
        "winners": (
            IEE_COLIMA + "acuerdos2024/ACUERDO116P.pdf",
            (
                "Acuerdo IEE/CG/A116/2024: asignación de diputaciones locales por el principio de representación "
                "proporcional, Proceso Electoral Local 2023-2024"
            ),
        ),
        "declared": {
            "mr": {
                1: "PVEM",
                2: "PT",
                3: "MORENA",
                4: "MORENA",
                5: "MORENA",
                6: "PRI",
                7: "PAN",
                8: "PT",
                9: "MORENA",
                10: "MORENA",
                11: "PT",
                12: "MORENA",
                13: "MORENA",
                14: "PVEM",
                15: "MORENA",
                16: "MORENA",
            },
            "rp": {"PAN": 2, "PRI": 2, "PVEM": 1, "MC": 1, "MORENA": 2, "PNA": 1},
            "pages": {"mr": 20, "rp": 32},
        },
        "mr": 16,
        "rp": 9,
        "body": ("Congreso del Estado de Colima", "Congress of Colima"),
        "rp_method": (
            "Votes by party and coalition line from the institute's casilla file of the proportional representation "
            "count, summed to the district and the state. Seats by party are those the Consejo General assigned in "
            "Acuerdo IEE/CG/A116/2024 (table 24, page 32)."
        ),
    },
    "COA": {
        "day": date(2023, 6, 4),
        "process": "Proceso electoral local ordinario 2023 de Coahuila",
        "publisher": "Instituto Electoral de Coahuila (IEC)",
        "page": IEC + "index.php/procesos/proceso-electoral-local-2023",
        "workbook": "iec",
        "casillas": IEC + "images/proceso2023/computos_finales/Diputaciones2023_X_Casilla.xlsx",
        "districts": IEC + "images/proceso2023/computos_finales/Diputacione2023_X_Distrito.xlsx",
        "statewide": (
            IEC_ACUERDOS + "IEC.CG.169.2023%20C%C3%B3mputo%20DIPUTACIONES.pdf",
            (
                "Acuerdo IEC/CG/169/2023: cómputo estatal de la elección de diputaciones locales, Proceso Electoral Local "
                "Ordinario 2023"
            ),
        ),
        "winners": (
            IEC_ACUERDOS + "IEC.CG.170.2023%20Asignaci%C3%B3n%20RP%20PELO2023.pdf",
            (
                "Acuerdo IEC/CG/170/2023: asignación de diputaciones por el principio de representación proporcional "
                "para integrar el Congreso del Estado de Coahuila de Zaragoza, periodo 2024-2026"
            ),
        ),
        "rp_votes": {
            "PAN": 88_967,
            "PRI": 590_174,
            "PRD": 30_875,
            "PVEM": 60_523,
            "PT": 117_573,
            "UDC": 42_333,
            "MC": 27_182,
            "MORENA": 354_480,
            "CNR": 364,
            "NULOS": 34_143,
            "V. FINAL": 1_346_614,
        },
        "declared": {
            "mr": {
                1: "PAN",
                2: "PRD",
                3: "PRI",
                4: "PRI",
                5: "PAN",
                6: "PAN",
                7: "PRI",
                8: "PAN",
                9: "PAN",
                10: "PRI",
                11: "PRI",
                12: "PRI",
                13: "PRI",
                14: "PRI",
                15: "PRD",
                16: "PRI",
            },
            "rp": {"PRI": 2, "PVEM": 1, "PT": 1, "UDC": 1, "MORENA": 4},
            "pages": {"mr": 43, "rp": 44, "votes": 14},
        },
        "mr": 16,
        "rp": 9,
        "body": ("Congreso del Estado de Coahuila de Zaragoza", "Congress of Coahuila"),
        "mr_method": (
            "Polling-station (casilla) counts from the institute's final count workbook, summed to the district. The "
            "workbook also carries the separate list-only tally sheets of the special casillas (S1RP, S2RP), which "
            "count for the proportional seats only and are left out here; with them, the sums equal the institute's "
            "district workbook. The early vote (voto anticipado) and the vote in pretrial detention (VPPP) are "
            "included, each on its own channel. Votes marked for two or more parties of one coalition are counted as "
            "that coalition's candidate. The winner and the party that nominated them are as Acuerdo IEC/CG/170/2023 "
            "lists the elected deputies (page 43)."
        ),
        "casilla_note": (
            "Polling-station results from the institute's final count workbook, district tally sheets only. Each line "
            "is a column of the workbook: a party, or a combination of the parties of one coalition that a voter marked."
        ),
        "rp_method": (
            "Votes by party in the state, as the Consejo General's statewide count prints them in Acuerdo "
            "IEC/CG/169/2023 (pages 14 and 15), with the votes marked for several parties of a coalition distributed "
            "among them. They include the list-only tally sheets of the special casillas and equal the sum of every "
            "tally sheet in the count workbook. Seats by party are those Acuerdo IEC/CG/170/2023 assigned (page 44)."
        ),
        "rp_system": (
            "The nine list seats are filled from closed statewide lists. A party needs 3 percent of the valid vote to "
            "take part; each such party first receives one seat (porcentaje específico), and the remaining seats go "
            "by the natural quotient and largest remainders, subject to the federal limits that no party may hold a "
            "share of the congress more than 8 points above or below its share of the vote (Constitución Política de "
            "los Estados Unidos Mexicanos, art. 116 II; Código Electoral para el Estado de Coahuila de Zaragoza, art. "
            "18). The model records the formula and the threshold; the first seat and the limits are in the declared "
            "seats."
        ),
    },
    "NLE": {
        "day": date(2024, 6, 2),
        "publisher": "Instituto Estatal Electoral y de Participación Ciudadana de Nuevo León (IEEPCNL)",
        "page": "https://computo24.ieepcnl.mx/",
        "workbook": "ieepcnl",
        "casillas": ("https://computo24.ieepcnl.mx/20240610_NL.zip", "NL_DIP_LOC_2024.xlsx"),
        "statewide": (
            IEEPCNL + "IEEPCNL-CG-280-2024_ocred.pdf",
            (
                "Acuerdo IEEPCNL/CG/280/2024: distribución y asignación de curules por el principio de representación "
                "proporcional para integrar el H. Congreso del Estado de Nuevo León, periodo 2024-2027, en cumplimiento "
                "de la sentencia SM-JRC-341/2024 y acumulados"
            ),
        ),
        "winners": (
            IEEPCNL + "IEEPCNL-CG-280-2024_ocred.pdf",
            (
                "Acuerdo IEEPCNL/CG/280/2024: distribución y asignación de curules por el principio de representación "
                "proporcional para integrar el H. Congreso del Estado de Nuevo León, periodo 2024-2027, en cumplimiento "
                "de la sentencia SM-JRC-341/2024 y acumulados"
            ),
        ),
        "mr_winners": (
            IEEPCNL + "IEEPCNL-CG-278-2024_ocred.pdf",
            (
                "Acuerdo IEEPCNL/CG/278/2024: modificación de los cómputos distritales de diversos distritos y "
                "distribución y asignación de curules por el principio de representación proporcional, en cumplimiento "
                "de lo resuelto por el Tribunal Electoral del Estado de Nuevo León"
            ),
        ),
        "rp_stage": "court_revised",
        "rp_votes": {
            "PAN": 517_587,
            "PRI": 345_790,
            "PRD": 23_223,
            "PVEM": 109_499,
            "PT": 77_360,
            "MC": 688_220,
            "MORENA": 533_754,
            "VIDA": 58_682,
            "ESO": 16_621,
            "PL": 17_987,
            "PES": 6_223,
            "PJ": 7_246,
            "CNR": 1_243,
            "NULOS": 88_069,
            "V. FINAL": 2_491_504,
        },
        # District 13's candidacy of the Partido Encuentro Solidario Nuevo León was cancelled before the count
        # (Acuerdo IEEPCNL/CG/255/2024), and the votes marked for it there count as votes for unregistered candidates.
        "cancelled": {13: "PES"},
        "declared": {
            "mr": {
                1: "PRI",
                2: "PRI",
                3: "PAN",
                4: "MC",
                5: "PRD",
                6: "PRD",
                7: "PRI",
                8: "PRI",
                9: "PAN",
                10: "PAN",
                11: "MC",
                12: "MORENA",
                13: "MORENA",
                14: "MC",
                15: "PAN",
                16: "PRD",
                17: "MORENA",
                18: "PAN",
                19: "PAN",
                20: "MORENA",
                21: "MC",
                22: "MC",
                23: "MC",
                24: "PRI",
                25: "MORENA",
                26: "PAN",
            },
            "rp": {"PT": 1, "PVEM": 1, "PRI": 3, "PAN": 3, "MORENA": 4, "MC": 4},
            "pages": {"mr": 43, "rp": 36, "votes": 22},
        },
        "mr": 26,
        "rp": 16,
        "body": ("H. Congreso del Estado de Nuevo León", "Congress of Nuevo León"),
        "mr_method": (
            "Polling-station (casilla) counts from the institute's district count workbook of 10 June 2024, district "
            "tally sheets only (the list-only sheets of the special casillas are left out), summed to the district; "
            "the early vote (voto anticipado) is included on its own channel. Votes marked for two or more parties of "
            "one coalition are counted as that coalition's candidate. In district 13 the votes marked for the "
            "Partido Encuentro Solidario Nuevo León count as votes for unregistered candidates, because Acuerdo "
            "IEEPCNL/CG/255/2024 cancelled its candidacy there. The Tribunal Electoral del Estado de Nuevo León later "
            "annulled casillas in several districts; the revised district totals are not in this workbook, and no "
            "winner changed. The winner and the party that nominated them are as Acuerdo IEEPCNL/CG/278/2024 lists "
            "them (table 7, pages 43 and 44)."
        ),
        "casilla_note": (
            "Polling-station results from the institute's district count workbook of 10 June 2024, district tally "
            "sheets only. Each line is a column of the workbook: a party, or a combination of the parties of one "
            "coalition that a voter marked."
        ),
        "rp_method": (
            "Votes by party in the state as Acuerdo IEEPCNL/CG/280/2024 prints them (table 9, page 22), after the "
            "casillas the courts annulled and the casilla the regional chamber restored, with the votes marked for "
            "several parties of a coalition distributed among them. The acuerdo prints 88,079 null votes and 2,402,182 "
            "valid votes in its table 10; the 88,069 of table 9 are kept, because they add up with the party votes to "
            "table 11's 2,402,192 valid votes. Seats by party are those of table 28 (page 36), which the acuerdo "
            "confirms from Acuerdo IEEPCNL/CG/278/2024."
        ),
        "rp_system": (
            "The 16 list seats go to the parties with 3 percent of the valid vote: each first receives one seat "
            "(porcentaje mínimo), and the remaining seats go by the electoral quotient and largest remainders, "
            "subject to the federal limit that no party may hold a share of the congress more than 8 points above its "
            "share of the vote (Constitución Política de los Estados Unidos Mexicanos, art. 116 II; Ley Electoral "
            "para el Estado de Nuevo León, arts. 263 to 267). A party's seats are filled partly from its list and "
            "partly by its district candidates who lost with the highest share of the vote in their district. The "
            "model records the formula and the threshold; the first seat and the limit are in the declared seats."
        ),
    },
}


def url_of(spec: str | tuple[str, str]) -> str:
    return spec if isinstance(spec, str) else spec[0]


def fetch() -> None:
    for config in STATES.values():
        for key in ("casillas", "districts", "statewide", "winners", "mr_winners", "rp_casillas"):
            if key in config:
                url = url_of(config[key])
                raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1].lower())


def member(spec: str | tuple[str, str]) -> list[str]:
    if not url_of(spec).endswith(".zip"):
        data = raw.cached(SOURCE, url_of(spec)).path.read_bytes()
    elif isinstance(spec, tuple):
        with zipfile.ZipFile(raw.cached(SOURCE, spec[0]).path) as archive:
            name = next(n for n in archive.namelist() if n.endswith(spec[1]))
            data = archive.read(name)
    text = data.decode("utf-8-sig") if data[:3] == b"\xef\xbb\xbf" else data.decode("utf-8", errors="strict")
    return text.splitlines()


def see(lines: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    head = next(i for i, line in enumerate(lines) if line.startswith(("CIRCUNSCRIPCION", "ID_ESTADO")))
    reader = csv.DictReader(io.StringIO("\n".join(lines[head:])))
    columns = list(reader.fieldnames or [])
    start = columns.index("URNA_ELECTRONICA" if "URNA_ELECTRONICA" in columns else "ACTAS_CASILLA-MEC") + 1
    return columns[start : columns.index("NUM_VOTOS_VALIDOS")], list(reader)


def parties_of(column: str) -> tuple[str, ...]:
    if column.startswith("CAND_IND"):
        return (INDEPENDENT,)
    return tuple(column.split("_"))


def rank(group: tuple[str, ...], columns: dict[str, tuple[str, ...]]) -> tuple[int, ...]:
    return tuple(sorted(NATIONAL.index(m) if m in NATIONAL else len(NATIONAL) for c in group for m in columns[c]))


def sheet_of(spec: str | tuple[str, str]) -> pl.DataFrame:
    """A count workbook's first sheet as text, from the row that names its columns."""
    path = raw.cached(SOURCE, url_of(spec)).path
    if isinstance(spec, tuple):
        with zipfile.ZipFile(path) as archive:
            data = archive.read(next(n for n in archive.namelist() if n.endswith(spec[1])))
    else:
        data = path.read_bytes()
    sheet = pl.read_excel(io.BytesIO(data), has_header=False, infer_schema_length=0)
    head = next(i for i, row in enumerate(sheet.iter_rows()) if str(row[0]).strip() in ("CLAVE_CASILLA", "cve_cas2"))
    names = [str(value).strip() for value in sheet.row(head)]
    return sheet.slice(head + 1).rename(dict(zip(sheet.columns, names, strict=True)))


def casilla_row(
    district: str, section: str, kind: str, number: int, extension: int, counts: dict[str, int], **figures: str
) -> dict[str, str]:
    """A workbook row in the field names of INE's casilla files, which parse_state reads."""
    return {
        "ID_DISTRITO_LOCAL": district,
        "SECCION": section,
        "TIPO_CASILLA": kind,
        "ID_CASILLA": str(number),
        "EXT_CONTIGUA": str(extension),
        "ESTATUS_ACTA": "",
        "NUM_VOTOS_VALIDOS": str(sum(counts.values())),
        **{option: str(value) for option, value in counts.items()},
        **figures,
    }


def iec_rows(iso: str, config: dict) -> tuple[list[str], list[dict[str, str]]]:
    """IEC's final count workbook. Special casillas have a second, list-only tally sheet (tipo S1RP), which counts
    toward the proportional seats only: it is left out of the district contests, and it is part of the totals the
    district workbook and the statewide count print, which the whole workbook is checked against."""
    sheet = sheet_of(config["casillas"])
    columns = sheet.columns[sheet.columns.index("tipo") + 1 : sheet.columns.index("cand_nreg")]
    options = [column.upper() for column in columns]
    figures = [*columns, "cand_nreg", "nulos", "total_votos"]
    numbers = sheet.with_columns(pl.col(c).map_elements(ine.number, return_dtype=pl.Int64) for c in figures)
    numbers = numbers.with_columns(pl.col("dtto_loc").cast(pl.Int64))
    district_sheet = pl.read_excel(
        raw.cached(SOURCE, config["districts"]).path, has_header=False, infer_schema_length=0
    )
    head = next(i for i, row in enumerate(district_sheet.iter_rows()) if str(row[0]).strip() == "No.")
    names = [str(value).strip().lower() for value in district_sheet.row(head)]
    printed = district_sheet.slice(head + 1).rename(dict(zip(district_sheet.columns, names, strict=True)))
    printed = printed.filter(pl.col("no.").str.contains(r"^\d+$"))
    summed = numbers.group_by("dtto_loc").agg(pl.col(c).sum() for c in figures)
    for row in printed.iter_rows(named=True):
        sums = summed.filter(pl.col("dtto_loc") == int(row["no."])).row(0, named=True)
        for column in figures:
            label = {"total_votos": "total votos"}.get(column, column.lower())
            if label in row and ine.number(row[label]) != sums[column]:
                raise ValueError(f"{iso} district {row['no.']}: {column} sums to {sums[column]}, printed {row[label]}")
    if printed.height != config["mr"]:
        raise ValueError(f"{iso}: {printed.height} districts in the district workbook")
    whole = numbers.select(pl.col(c).sum() for c in figures).row(0, named=True)
    declared = config["rp_votes"]
    for column, label in (("cand_nreg", "CNR"), ("nulos", "NULOS"), ("total_votos", "V. FINAL")):
        if whole[column] != declared[label]:
            raise ValueError(
                f"{iso}: the workbook's {column} is {whole[column]}, the statewide count {declared[label]}"
            )
    rows = []
    for row in numbers.iter_rows(named=True):
        tipo = row["tipo"].strip()
        if tipo.endswith("RP"):
            continue
        if tipo == "N/A":
            kind, number, extension = {"VA": "A", "VPPP": "P"}[row["casilla"].strip()], 1, 0
        else:
            match = re.fullmatch(r"([BCES])(\d*)(?:C(\d+))?", tipo)
            if match is None:
                raise ValueError(f"{iso}: casilla type {tipo!r}")
            kind, number, extension = match.group(1), int(match.group(2) or 1), int(match.group(3) or 0)
        rows.append(
            casilla_row(
                str(row["dtto_loc"]),
                row["seccion"],
                kind,
                number,
                extension,
                {option: row[column] for option, column in zip(options, columns, strict=True)},
                ID_ESTADO=str(mge.STATES.index(next(s for s in mge.STATES if s[0] == iso)) + 1),
                LISTA_NOMINAL=row["lista"],
                NUM_VOTOS_CAN_NREG=str(row["cand_nreg"]),
                NUM_VOTOS_NULOS=str(row["nulos"]),
                TOTAL_VOTOS=str(row["total_votos"]),
            )
        )
    return options, rows


def ieepcnl_rows(iso: str, config: dict) -> tuple[list[str], list[dict[str, str]]]:
    """IEEPCNL's per-acta count workbook. Tally sheets of type 4 are the list-only sheets of the special casillas and
    are left out of the district contests. A district's early votes (casilla A) can come on two sheets with the same
    key; they are numbered in the order of the workbook."""
    sheet = sheet_of(config["casillas"])
    columns = sheet.columns[sheet.columns.index("TIPO_ACTA") + 1 : sheet.columns.index("NO_REGISTRADAS")]
    options = [column.removeprefix("C_") for column in columns]
    cancelled: dict[int, str] = config.get("cancelled", {})
    early: dict[int, int] = {}
    rows = []
    for row in sheet.iter_rows(named=True):
        if row["TIPO_ACTA"] == "4":
            continue
        if row["TIPO_ACTA"] != "3":
            raise ValueError(f"{iso}: tally sheet type {row['TIPO_ACTA']}")
        district = int(row["ID_DISTRITO_LOCAL"])
        counts = {option: ine.number(row[column]) for option, column in zip(options, columns, strict=True)}
        unregistered = ine.number(row["NO_REGISTRADAS"])
        if district in cancelled:
            unregistered += counts[cancelled[district]]
            counts[cancelled[district]] = 0
        number = int(row["ID_CASILLA"])
        if row["TIPO_CASILLA"] == "A":
            early[district] = number = early.get(district, 0) + 1
        rows.append(
            casilla_row(
                str(district),
                "0" if row["SECCION"] == "N/A" else row["SECCION"],
                row["TIPO_CASILLA"].strip(),
                number,
                int(row["EXT_CONTIGUA"]),
                counts,
                ID_ESTADO=row["ID_ENTIDAD"],
                LISTA_NOMINAL=row["LISTA_NOMINAL"],
                NUM_VOTOS_CAN_NREG=str(unregistered),
                NUM_VOTOS_NULOS=row["NULOS"],
                TOTAL_VOTOS=row["TOTAL_VOTOS_CALCULADO"],
            )
        )
    return options, rows


def parse_state(iso: str, config: dict) -> None:
    entidad = mge.STATES.index(next(s for s in mge.STATES if s[0] == iso)) + 1
    reader = {"iec": iec_rows, "ieepcnl": ieepcnl_rows}.get(config.get("workbook", ""))
    options, rows = see(member(config["casillas"])) if reader is None else reader(iso, config)
    units, votes = [], []
    for row in rows:
        counts = {option: ine.number(row[option]) for option in options}
        unregistered, null = ine.number(row["NUM_VOTOS_CAN_NREG"]), ine.number(row["NUM_VOTOS_NULOS"])
        total = ine.number(row["TOTAL_VOTOS"])
        if (
            sum(counts.values()) != ine.number(row["NUM_VOTOS_VALIDOS"])
            or sum(counts.values()) + unregistered + null != total
        ):
            raise ValueError(
                f"{iso}: casilla {row['SECCION']} {row['TIPO_CASILLA']}{row['ID_CASILLA']} does not add up"
            )
        if int(row["ID_ESTADO"]) != entidad:
            raise ValueError(f"{iso}: row in state {row['ID_ESTADO']}")
        unit = {
            "district": int(row["ID_DISTRITO_LOCAL"]),
            "section": int(row["SECCION"]),
            "kind": row["TIPO_CASILLA"].strip(),
            "number": int(row["ID_CASILLA"]),
            "extension": int(row["EXT_CONTIGUA"]),
        }
        units.append(
            unit | {"total": total, "registered": ine.number(row["LISTA_NOMINAL"]), "status": row["ESTATUS_ACTA"]}
        )
        votes.extend(unit | {"option": option, "kind_of": "list", "votes": value} for option, value in counts.items())
        votes.append(unit | {"option": "NUM_VOTOS_CAN_NREG", "kind_of": "unregistered", "votes": unregistered})
        votes.append(unit | {"option": "NUM_VOTOS_NULOS", "kind_of": "invalid", "votes": null})
    units_frame = pl.DataFrame(units)
    keys = ["district", "section", "kind", "number", "extension"]
    if units_frame.group_by(keys).len().filter(pl.col("len") > 1).height:
        raise ValueError(f"{iso}: duplicate casillas")
    votes_frame = pl.DataFrame(votes)
    if "districts" in config and reader is None:
        check_districts(iso, config, options, votes_frame)
    statewide_frame = statewide_of(iso, config)
    elected_frame = elected_of(iso, config)
    tiers = dict(elected_frame.group_by("tier").len().iter_rows())
    if tiers != {"mr": config["mr"], "rp": config["rp"]}:
        raise ValueError(f"{iso}: winners list has {tiers}")
    folder = PARSED / iso
    folder.mkdir(parents=True, exist_ok=True)
    units_frame.write_parquet(folder / "casillas.parquet")
    votes_frame.write_parquet(folder / "votes.parquet")
    statewide_frame.write_parquet(folder / "statewide.parquet")
    elected_frame.write_parquet(folder / "elected.parquet")
    print(iso, units_frame.height, "casillas", units_frame["total"].sum(), "votes", tiers)


def check_districts(iso: str, config: dict, options: list[str], votes_frame: pl.DataFrame) -> None:
    district_options, district_rows = see(member(config["districts"]))
    if district_options != options:
        raise ValueError(f"{iso}: district file columns {district_options} differ from {options}")
    summed = (
        votes_frame.filter(~pl.col("kind").str.starts_with("A"))
        .group_by("district", "option")
        .agg(pl.col("votes").sum())
    )
    printed = pl.DataFrame(
        [
            {"district": int(row["ID_DISTRITO_LOCAL"]), "option": option, "printed": ine.number(row[option])}
            for row in district_rows
            for option in [*options, "NUM_VOTOS_CAN_NREG", "NUM_VOTOS_NULOS"]
        ]
    )
    differ = printed.join(summed, on=["district", "option"], how="full", coalesce=True).filter(
        pl.col("printed").fill_null(-1) != pl.col("votes").fill_null(-1)
    )
    if differ.height:
        raise ValueError(f"{iso}: casillas differ from the district counts {differ}")
    if len(district_rows) != config["mr"]:
        raise ValueError(f"{iso}: {len(district_rows)} districts, expected {config['mr']}")


def statewide_of(iso: str, config: dict) -> pl.DataFrame:
    if "rp_votes" in config:
        declared: dict[str, int] = config["rp_votes"]
        if sum(v for k, v in declared.items() if k != "V. FINAL") != declared["V. FINAL"]:
            raise ValueError(f"{iso}: the statewide votes do not add up to their total")
        return pl.DataFrame(
            [{"district": 0, "option": option, "votes": votes} for option, votes in declared.items()],
            schema={"district": pl.Int64, "option": pl.String, "votes": pl.Int64},
        )
    if "rp_casillas" in config:
        options, rows = see(member(config["rp_casillas"]))
        renamed = {"NUM_VOTOS_CAN_NREG": "CNR", "NUM_VOTOS_NULOS": "NULOS", "TOTAL_VOTOS": "V. FINAL"}
        frame = pl.DataFrame(
            [
                {
                    "district": int(row["ID_DISTRITO_LOCAL"]),
                    "option": renamed.get(column, column),
                    "votes": ine.number(row[column]),
                }
                for row in rows
                for column in [*options, *renamed]
            ]
        )
        frame = frame.group_by("district", "option", maintain_order=True).agg(pl.col("votes").sum())
        return pl.concat(
            [
                frame,
                frame.group_by("option", maintain_order=True)
                .agg(pl.col("votes").sum())
                .select(district=pl.lit(0, pl.Int64), option="option", votes="votes"),
            ]
        )
    sheet = pl.read_excel(raw.cached(SOURCE, url_of(config["statewide"])).path, has_header=False)
    header = sheet.row(1)
    statewide = []
    for row in sheet.iter_rows():
        label = str(row[0] or "")
        if label.startswith("D") and label[1:].isdigit() or label == "TOTALES":
            for column, value in zip(header[1:], row[1:], strict=True):
                statewide.append(
                    {"district": 0 if label == "TOTALES" else int(label[1:]), "option": column, "votes": int(value)}
                )
    statewide_frame = pl.DataFrame(statewide)
    check = statewide_frame.filter(pl.col("district") > 0).group_by("option").agg(pl.col("votes").sum())
    totals = dict(statewide_frame.filter(pl.col("district") == 0).select("option", "votes").iter_rows())
    for option, value in check.iter_rows():
        if totals[option] != value:
            raise ValueError(f"{iso}: statewide {option} districts add to {value}, printed {totals[option]}")
    for row in statewide_frame.filter(pl.col("option") == "V. FINAL").iter_rows(named=True):
        parts = statewide_frame.filter((pl.col("district") == row["district"]) & (pl.col("option") != "V. FINAL"))
        if parts["votes"].sum() != row["votes"]:
            raise ValueError(f"{iso}: statewide district {row['district']} does not add to its total")
    return statewide_frame


def elected_of(iso: str, config: dict) -> pl.DataFrame:
    if "declared" in config:
        declared = config["declared"]
        rows = [{"tier": "mr", "number": d, "name": "", "label": label} for d, label in declared["mr"].items()]
        rows += [
            {"tier": "rp", "number": 0, "name": "", "label": label}
            for label, n in declared["rp"].items()
            for _ in range(n)
        ]
        return pl.DataFrame(rows)
    winners = pl.read_excel(raw.cached(SOURCE, url_of(config["winners"])).path, has_header=False)
    elected, section = [], ""
    for row in winners.iter_rows():
        label = str(row[0] or "").strip()
        if label.startswith("DIPUTACIONES DE MAYOR"):
            section = "mr"
        elif label.startswith("DIPUTACIONES DE REPRESENTACI"):
            section = "rp"
        elif label.isdigit():
            elected.append(
                {"tier": section, "number": int(label), "name": str(row[1]).strip(), "label": str(row[3]).strip()}
            )
    return pl.DataFrame(elected)


def parse() -> None:
    for iso, config in STATES.items():
        parse_state(iso, config)
    build()


MR_RULES = {"formula": "fptp"}
RP_RULES = {
    "formula": "hare",
    "threshold": {"percent": 3, "level": "party", "scope": "contest"},
    "lists": {"order": "closed", "vacancy": "next_on_list"},
}
MR_SYSTEM = (
    "Each single-member local district elects one deputy by plurality of the votes cast, with no runoff. The "
    "state congress also has members elected by proportional representation from closed statewide lists "
    "(Constitución Política de los Estados Unidos Mexicanos, art. 116 II)."
)
RP_SYSTEM = (
    "The list seats are filled from closed statewide lists. A party needs 3 percent of the valid vote to take "
    "part, and seats go by the natural quotient and largest remainders, subject to the federal limit that no party "
    "may hold a share of the congress more than 8 points above its share of the vote (art. 116 II). The model "
    "records the formula and the threshold; the limit is in the declared seats."
)
MR_METHOD = (
    "Polling-station (casilla) counts from the institute's district count database, summed to the district; the "
    "sums equal the institute's district totals, which leave out the early-vote count (voto anticipado) that the "
    "casilla file carries and that is included here. Votes marked for two or more parties of one coalition are counted "
    "as that coalition's candidate. The winner is as the institute lists the elected deputies."
)
CASILLA_NOTE = (
    "Polling-station results from the institute's district count database. Each line is a column of the file: a "
    "party, or a combination of the parties of one coalition that a voter marked."
)


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package mx.congreso_local/all from the parsed tables. It reads the database through Draft, so the
    rows it rebuilds keep the ids the changeset path gave them."""
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mexican state legislatures: district contests by polling station and proportional seats, from the "
            "state electoral institutes (" + ", ".join(STATES) + ")",
            polity="MX",
        )
        cs.accept(
            "accounting.turnout",
            "IEPAC's casilla file prints more votes cast than the nominal roll at 10 Yucatán casillas (sections 44, "
            "123, 176, 245, 249, 250, 724, 727, 824 and 825; 1,844 against 656 at 176 C1), and IEC's count workbook "
            "does at 10 Coahuila casillas (31 C5, 85 B, 97 B, 146 C2, 327 C1, 870 C1, 1033 B, 1139 B, 1142 C1 and "
            "1437 B; 1,926 against 747 at 31 C5); both figures are stored as printed",
        )
        if conn.execute("select 1 from scheme where code = 'mx.distrito_local'").fetchone() is None:
            raise ValueError("publish mx.distrito_local before mx.congreso_local")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        parties: dict[str, UUID] = {}

        def party(abbrev: str, iso: str, polity: UUID) -> UUID:
            local = LOCAL_PARTIES.get(iso, {})
            key = f"{iso}:{abbrev}" if abbrev in local else abbrev
            if key not in parties:
                item, native, english = local[abbrev] if abbrev in local else diputados.PARTIES[abbrev]
                parties[key] = cs.key("mx.party", item or f"MX-{iso}:{abbrev}")
                if parties[key] in cs.minted:
                    cs.add(
                        "party", id=parties[key], kind="party", polity_id=polity if abbrev in local else cs.polity("MX")
                    )
                    name(parties[key], native, "es")
                    name(parties[key], english, "en", "english")
                    name(parties[key], abbrev, "es", "abbreviation")
                    if item:
                        cs.add("external_id", entity_id=parties[key], scheme="wikidata", value=item)
            return parties[key]

        # The federal count's casillas, which this pipeline's results attach to; the casillas it mints itself are
        # left out, so a rebuild writes them again.
        casilla_units = {
            code: unit
            for code, unit in conn.execute(
                "select code, id from reporting_unit where scheme_code = 'mx.casilla' and code like any(%s)",
                [sorted({f"{config['day'].year}:%" for config in STATES.values()})],
            ).fetchall()
            if unit not in cs.own_entities
        }
        by_place: dict[tuple[int, str, int, str], str] = {}
        for code in casilla_units:
            year, rest = code.split(":")
            state, _, section, letter = rest.split("-")
            by_place[(int(year), state, int(section), letter)] = code
        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[str, str, UUID | None, str, UUID, str]] = {}
        frames: list[pl.DataFrame] = []
        rows: list[tuple[str, str, str | None, int, int, int]] = []

        for iso, config in STATES.items():
            entidad = [s[0] for s in mge.STATES].index(iso) + 1
            ss = f"{entidad:02d}"
            day: date = config["day"]
            year = day.year
            folder = PARSED / iso
            polity = cs.polity(f"MX-{iso}")
            state_name = mge.STATES[entidad - 1][1]
            source = cs.source(
                config["publisher"], f"Cómputos distritales de la elección de diputaciones locales {year}"
            )
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=config["publisher"],
                title=f"Cómputos distritales de la elección de diputaciones locales {year}",
                url=config["page"],
                license=LICENSE,
                license_url=config["page"],
                attribution=config["publisher"],
            )
            for key in ("casillas", "districts"):
                if key in config:
                    cs.artifact(raw.cached(SOURCE, url_of(config[key])), source)

            documents: dict[tuple[str, str], UUID] = {}

            def document(spec: tuple[str, str]) -> UUID:
                if spec in documents:
                    return documents[spec]
                url, title = spec
                found = documents[spec] = cs.source(config["publisher"], title)
                cs.add(
                    "source",
                    id=found,
                    kind="official_body",
                    publisher=config["publisher"],
                    title=title,
                    url=url,
                    license=LICENSE,
                    license_url=config["page"],
                    attribution=config["publisher"],
                )
                cs.artifact(raw.cached(SOURCE, url), found)
                return found

            statewide_source = document(config.get("statewide") or config["rp_casillas"])
            winners_source = document(config["winners"])
            mr_source = document(config["mr_winners"]) if "mr_winners" in config else winners_source
            pages = config.get("declared", {}).get("pages", {})

            native, english = config["body"]
            body = cs.key("mx.body", f"{iso}:congreso")
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="unicameral")
                name(body, english)
                name(body, native, "es", "native")
            cs.add("body_size", body_id=body, seats=config["mr"] + config["rp"], valid_from=day)
            mr_system = cs.key("mx.electoral_system", f"{iso}:congreso-mr")
            if mr_system in cs.minted:
                cs.add("electoral_system", id=mr_system, polity_id=polity, rules=MR_RULES, rules_version=1)
                name(mr_system, f"{state_name} local district plurality system")
                cs.add("note", entity_id=mr_system, kind="methodology", body_md=MR_SYSTEM)
            rp_system = cs.key("mx.electoral_system", f"{iso}:congreso-rp")
            if rp_system in cs.minted:
                cs.add("electoral_system", id=rp_system, polity_id=polity, rules=RP_RULES, rules_version=1)
                name(rp_system, f"{state_name} statewide closed-list proportional system")
                cs.add("note", entity_id=rp_system, kind="methodology", body_md=config.get("rp_system", RP_SYSTEM))

            election = cs.key("mx.election", f"{day}:{iso}")
            path = f"mx/{iso.lower()}/{day}-general"
            if election in cs.minted:
                cs.add(
                    "election", id=election, polity_id=polity, kind="general", date_from=day, date_to=day, status="held"
                )
                name(election, f"{year} {state_name} state election")
                process = config.get("process", f"Proceso electoral local {year - 1}-{year} de {state_name}")
                name(election, process, "es", "native")
                cs.add("slug", path=path, entity_id=election)

            state_unit = cs.unit("mx.entidad", ss)

            def make_set(
                contest: UUID, key: str, default: bool, source_id: UUID, note: str, stage: str = "official"
            ) -> UUID:
                result_set = cs.key("mx.result_set", f"MX-{iso}:{day}:dip:{key}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage=stage,
                    kind="counted",
                    status="active",
                    source_id=source_id,
                    is_default=default,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                return result_set

            alliances: dict[str, UUID] = {}

            def alliance_of(members: list[str]) -> UUID:
                members = sorted(members, key=lambda m: (NATIONAL.index(m) if m in NATIONAL else len(NATIONAL), m))
                key = "+".join(members)
                if key not in alliances:
                    alliances[key] = cs.key("mx.alliance", f"{day}:{iso}:{key}")
                    if alliances[key] in cs.minted:
                        cs.add("alliance", id=alliances[key], kind="electoral_alliance", election_id=election)
                        name(alliances[key], "–".join(members) + " coalition")
                        name(alliances[key], "Coalición " + "–".join(members), "es", "native")
                        for abbrev in members:
                            cs.add("alliance_member", alliance_id=alliances[key], party_id=party(abbrev, iso, polity))
                return alliances[key]

            casillas = pl.read_parquet(folder / "casillas.parquet")
            votes = pl.read_parquet(folder / "votes.parquet")
            elected = pl.read_parquet(folder / "elected.parquet")
            placed = []
            for row in casillas.iter_rows(named=True):
                letter = ine.casilla_code(2024, f"{row['kind'][:1]}{row['number']:02d}{row['extension']:02d}")
                code = by_place.get((year, ss, row["section"], letter))
                if code is None:
                    code = f"{year}:{ss}-L{row['district']:02d}-{row['section']:04d}-{letter}"
                    unit_id = cs.unit("mx.casilla", code)
                    channel, kind, area = 0, "standard", sections.get(f"{ss}{row['section']:04d}")
                    if row["kind"][:1] == "P":
                        channel, kind, area = ine.PRISON, "prison", None
                    elif row["section"] == 0 or row["kind"][:1] == "A":
                        channel, kind, area = ine.EARLY if row["kind"][:1] == "A" else ine.ABROAD, "aggregate", None
                    unit_rows[unit_id] = (
                        "mx.casilla",
                        code,
                        state_unit,
                        "within",
                        area or cs.area("mx.entidad", ss),
                        kind,
                    )
                else:
                    unit_id = casilla_units[code]
                    channel = 0 if letter[:1] not in "MVA" else ine.EARLY if letter[:1] == "A" else ine.ABROAD
                placed.append(
                    (
                        row["district"],
                        row["section"],
                        row["kind"],
                        row["number"],
                        row["extension"],
                        str(unit_id),
                        channel,
                    )
                )
            keys = ["district", "section", "kind", "number", "extension"]
            located_units = pl.DataFrame(
                placed,
                schema=[
                    ("district", pl.Int64),
                    ("section", pl.Int64),
                    ("kind", pl.String),
                    ("number", pl.Int64),
                    ("extension", pl.Int64),
                    ("unit_id", pl.String),
                    ("channel", pl.Int16),
                ],
                orient="row",
            )
            if located_units["unit_id"].n_unique() != located_units.height:
                raise ValueError(f"{iso}: two casillas map to one unit")
            located = casillas.join(located_units, on=keys)
            joined = votes.join(located_units, on=keys).rename({"kind_of": "kind_votes"})
            columns = {o: parties_of(o) for o in votes.filter(pl.col("kind_of") == "list")["option"].unique()}

            winners: dict[str, int] = {}
            for district in range(1, config["mr"] + 1):
                contest_key = f"MX-{iso}:{day}:dip:mr:{district:02d}"
                contest = cs.key("mx.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    magnitude=1,
                    electoral_system_id=mr_system,
                    district_area_id=cs.area("mx.distrito_local", f"{ss}{district:02d}"),
                    default_result_kind="counted",
                )
                name(contest, f"{year} Congress of {state_name}, district {district}")
                name(contest, f"Diputación local {year}, {state_name}, distrito {district}", "es", "native")
                cs.add("slug", path=f"{path}/congress-district-{district}", entity_id=contest)
                mine = joined.filter(pl.col("district") == district)
                totals_by = dict(
                    mine.filter((pl.col("kind_votes") == "list") & (pl.col("votes") > 0))
                    .group_by("option")
                    .agg(pl.col("votes").sum())
                    .sort("option")
                    .iter_rows()
                )
                standing = {
                    tuple(sorted(g)): sum(totals_by[c] for c in g)
                    for g in diputados.groups_of(columns, list(totals_by))
                }
                # Options with equal votes take national parties first, then the order of their columns' names.
                ordered = sorted(standing.items(), key=lambda item: (-item[1], rank(item[0], columns), item[0]))
                if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
                    raise ValueError(f"{iso} {district}: tie for first place")
                declared = elected.filter((pl.col("tier") == "mr") & (pl.col("number") == district)).row(0, named=True)
                declared_parties = set(declared["label"].split("-"))
                district_unit = cs.unit("mx.distrito_local", f"{year}:{ss}-{district:02d}")
                unit_rows[district_unit] = (
                    "mx.distrito_local",
                    f"{year}:{ss}-{district:02d}",
                    state_unit,
                    "equals",
                    cs.area("mx.distrito_local", f"{ss}{district:02d}"),
                    "standard",
                )
                district_set = make_set(
                    contest, f"mr:{district:02d}:district", True, source, config.get("mr_method", MR_METHOD)
                )
                casilla_set = make_set(
                    contest, f"mr:{district:02d}:casilla", False, source, config.get("casilla_note", CASILLA_NOTE)
                )
                cs.add(
                    "citation",
                    entity_id=district_set,
                    source_id=mr_source,
                    locator={"page": pages["mr"]} if pages else {"row": declared["number"]},
                )
                options: dict[str, str] = {}
                for position, (group, _) in enumerate(ordered, 1):
                    members = sorted({m for c in group for m in columns[c]} - {INDEPENDENT})
                    head = cs.key("mx.option", f"{contest_key}:{'+'.join(group)}")
                    cs.add(
                        "contest_option",
                        id=head,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party(members[0], iso, polity) if len(members) == 1 else None,
                        alliance_id=alliance_of(members) if len(members) > 1 else None,
                        ballot_position=position,
                        ballot_label="–".join(members) if members else "Candidatura independiente",
                    )
                    if position == 1:
                        if not declared_parties <= set(members):
                            raise ValueError(f"{iso} {district}: count leader {members}, declared {declared_parties}")
                        cs.add("outcome", result_set_id=district_set, option_id=head, kind="elected", seats=1)
                        label = "–".join(members) or INDEPENDENT
                        winners[label] = winners.get(label, 0) + 1
                    for column in group:
                        if len(group) == 1:
                            options[column] = str(head)
                            continue
                        line = cs.key("mx.option", f"{contest_key}:{'+'.join(group)}:{column}")
                        own = [m for m in columns[column] if m != INDEPENDENT]
                        cs.add(
                            "contest_option",
                            id=line,
                            contest_id=contest,
                            kind="candidate",
                            parent_option_id=head,
                            party_id=party(own[0], iso, polity) if len(own) == 1 else None,
                            alliance_id=alliance_of(own) if len(own) > 1 else None,
                            ballot_label="–".join(own),
                        )
                        options[column] = str(line)
                write_in = cs.key("mx.option", f"{contest_key}:unregistered")
                cs.add(
                    "contest_option",
                    id=write_in,
                    contest_id=contest,
                    kind="write_in",
                    ballot_label="Candidatos no registrados",
                )
                frames.append(
                    diputados.district_frame(
                        mine.drop("kind").rename({"kind_votes": "kind"}),
                        located.filter(pl.col("district") == district),
                        options,
                        str(write_in),
                        str(casilla_set),
                        str(district_unit),
                        str(district_set),
                    )
                )
            print(iso, "district winners", winners)

            statewide = pl.read_parquet(folder / "statewide.parquet")
            listed = [
                o for o in statewide["option"].unique(maintain_order=True) if o not in ("CNR", "NULOS", "V. FINAL")
            ]
            national = cs.key("mx.contest", f"MX-{iso}:{day}:dip:rp")
            cs.add(
                "contest",
                id=national,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                magnitude=config["rp"],
                electoral_system_id=rp_system,
                district_area_id=cs.area("mx.entidad", ss),
                default_result_kind="counted",
            )
            name(national, f"{year} Congress of {state_name}, proportional representation")
            name(national, f"Diputaciones locales {year}, {state_name}, representación proporcional", "es", "native")
            cs.add("slug", path=f"{path}/congress-proportional", entity_id=national)
            rp_set = make_set(
                national, "rp", True, statewide_source, config["rp_method"], config.get("rp_stage", "official")
            )
            cs.add(
                "citation",
                entity_id=rp_set,
                source_id=winners_source,
                locator={"page": pages["rp"]} if pages else {"section": "representación proporcional"},
            )
            if "votes" in pages:
                cs.add("citation", entity_id=rp_set, source_id=statewide_source, locator={"page": pages["votes"]})
            rp_options: dict[str, UUID] = {}
            totals = dict(statewide.filter(pl.col("district") == 0).select("option", "votes").iter_rows())
            for position, abbrev in enumerate(sorted(listed, key=lambda a: -totals[a]), 1):
                rp_options[abbrev] = cs.key("mx.option", f"MX-{iso}:{day}:dip:rp:{abbrev}")
                members = [m for m in parties_of(abbrev) if m != INDEPENDENT]
                cs.add(
                    "contest_option",
                    id=rp_options[abbrev],
                    contest_id=national,
                    kind="list",
                    party_id=party(members[0], iso, polity) if len(members) == 1 else None,
                    alliance_id=alliance_of(members) if len(members) > 1 else None,
                    ballot_position=position,
                    ballot_label="–".join(members) if members else "Candidaturas independientes",
                )
            rp_write_in = cs.key("mx.option", f"MX-{iso}:{day}:dip:rp:unregistered")
            cs.add(
                "contest_option",
                id=rp_write_in,
                contest_id=national,
                kind="write_in",
                ballot_label="Candidatos no registrados",
            )
            seats = dict(elected.filter(pl.col("tier") == "rp").group_by("label").len().iter_rows())
            for abbrev, count in seats.items():
                cs.add("outcome", result_set_id=rp_set, option_id=rp_options[abbrev], kind="won", seats=count)
            print(iso, "list seats", seats)
            for row in statewide.iter_rows(named=True):
                district = row["district"]
                unit = state_unit if district == 0 else cs.unit("mx.distrito_local", f"{year}:{ss}-{district:02d}")
                option = row["option"]
                if option == "V. FINAL":
                    rows.append((str(rp_set), str(unit), None, BALLOTS, row["votes"], 0))
                elif option == "NULOS":
                    rows.append((str(rp_set), str(unit), None, INVALID, row["votes"], 0))
                else:
                    target = rp_write_in if option == "CNR" else rp_options[option]
                    rows.append((str(rp_set), str(unit), str(target), VOTES, row["votes"], 0))
            for district in sorted(set(statewide["district"])):
                part = statewide.filter(
                    (pl.col("district") == district) & ~pl.col("option").is_in(["NULOS", "V. FINAL"])
                )
                unit = state_unit if district == 0 else cs.unit("mx.distrito_local", f"{year}:{ss}-{district:02d}")
                rows.append((str(rp_set), str(unit), None, VALID, int(part["votes"].sum()), 0))

        minted = [
            {
                "id": str(unit_id),
                "polity_id": str(cs.polity("MX")),
                "scheme_code": scheme,
                "code": code,
                "parent_id": None if parent is None else str(parent),
                "area_relation": relation,
                "area_id": str(area),
                "kind": kind,
            }
            for unit_id, (scheme, code, parent, relation, area, kind) in unit_rows.items()
            if unit_id in cs.minted
        ]
        cs.frame("reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String}))
        list_values = pl.DataFrame(
            rows,
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
            pl.concat([list_values, *(frame.select(list_values.columns) for frame in frames)]).with_columns(
                count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
            ),
        )
        return cs.write()
