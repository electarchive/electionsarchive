import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.mx import ine
from ea.store import DATA, connect

SOURCE = "mx.diputados"
RAW = ine.SOURCE
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

KINDS = ("MR", "RP")
# siceef.ine.mx no longer serves the 1991 district file to scripts; the Internet Archive's copy of 30 May 2022 is
# the file as INE published it (its casillas add to 24,143,540 votes, against the 24,149,001 the acuerdo declares
# for the lists).
MR_1991 = "https://web.archive.org/web/20220530194439id_/http://siceef.ine.mx/BD/DiputadosMR1991.csv"
DOF_1991 = "https://dof.gob.mx/nota_detalle.php?codigo=4748643&fecha=01/10/1991"
DOF_1991_ERRATA = "https://dof.gob.mx/nota_detalle.php?codigo=4750567&fecha=04/10/1991"
# INE's count site for the 2015 extraordinary election in Aguascalientes district 1 is offline; the Internet Archive's
# copy of its summary (cut of 9 December 2015) gives the district's total vote and actas to check the file against.
AGS_2015_COUNT = (
    "https://web.archive.org/web/20160520000120id_/"
    "http://computos2015-ags.ine.mx:80/JSON/DIPUTADO/NACIONAL/diputados_nacional.json"
)
DOF_2003 = "https://dof.gob.mx/nota_detalle.php?codigo=690614&fecha=22/09/2003"
DIFFERS = {
    (2006, "7", "6", "2", "8", "B"),
    (2006, "7", "6", "86", "1238", "E01"),
    (2006, "15", "35", "89", "4452", "E01"),
    (2006, "28", "2", "32", "1119", "C02"),
    (2006, "32", "2", "20", "699", "B"),
    (2012, "5", "1", "2", "10", "C04"),
    (2012, "15", "10", "34", "1306", "B"),
    (2012, "21", "6", "115", "1306", "B"),
}
# Rows no reading makes consistent, left out of the count; the district's casilla set says so in its note.
EXCLUDED = {
    (1991, "MR", "21", "4", "69", "10", "C"): (
        "INE's file prints one acta of this district, a contiguous casilla of section 10 in Huaquechula marked "
        "annulled, whose lines add to 221 votes against a printed total of 18; no reading of the row is consistent, "
        "so it is left out of the casilla and district counts."
    ),
}
# The 1991 file prints the type of contiguous and extraordinary casillas without their number, so these rows repeat
# the state, district, municipality, section, and casilla of an earlier row. Each is a separate acta with its own
# counts (no two are identical), kept in file order as -r2, -r3, as mx.senado codes its 1991 casillas.
REPEATS = {(1991, "MR"): 197}
# The 2003 list file prints two actas for special casilla 1 of section 1494 in Tonalá, Chiapas district 7, both
# marked SMR1 (the ballot for district deputies). The district file holds only the first, and every other special
# casilla has one SMR and one SRP acta (761 of each, but 760 SRP in the list file), so the second row is this
# casilla's acta for the list ballot, mislabeled. It is read as SRP1.
RELABELED = {(2003, "RP", "7", "7", "97", "1494", "SMR1", 2): "SRP1"}


def siceef_url(kind: str, year: int) -> str:
    return f"{ine.SICEEF}Diputados{kind}{year}.csv"


def kinds(year: int) -> tuple[str, ...]:
    return tuple(ELECTIONS[year].get("files", dict.fromkeys(KINDS)))


def file_url(kind: str, year: int) -> str:
    return ELECTIONS[year].get("files", {}).get(kind) or siceef_url(kind, year)


def fetch() -> None:
    for year, config in ELECTIONS.items():
        for kind in kinds(year):
            raw.fetch(RAW, file_url(kind, year), suffix=".csv")
        for url in (config["acuerdo"][0], *config.get("also", ())):
            raw.fetch(RAW, url, suffix=".pdf" if url.endswith(".pdf") else ".html")
        for rerun in config.get("extraordinary", {}).values():
            raw.fetch(RAW, rerun["count"], suffix=".json")


def read(kind: str, year: int) -> tuple[list[str], list[dict[str, str]]]:
    encoding = ELECTIONS[year].get("encoding", "utf-8-sig")
    text = raw.cached(RAW, file_url(kind, year)).path.read_bytes().decode(encoding, errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = [
        {
            "state": row["ID_ESTADO"],
            "district": row["ID_DISTRITO"],
            "municipality": row["ID_MUNICIPIO"],
            "section": row["SECCION"],
            "casilla": row["CASILLA"],
            "circunscripcion": row["CIRCUNSCRIPCION"],
            **row,
        }
        for row in reader
    ]
    columns = list(reader.fieldnames or [])
    return columns[columns.index("CASILLA") + 1 :], rows


def unit_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(ine.clean(row[k]).lstrip("0") or "0" if k != "casilla" else ine.clean(row[k]) for k in ine.KEYS)


def table(kind: str, year: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    columns, rows = read(kind, year)
    options = [c for c in columns if c not in ine.TRAILING]
    total = "TOTAL_VOTOS"
    roll = "LISTA_NOMINAL" if "LISTA_NOMINAL" in columns else None
    units, votes = [], []
    seen: dict[tuple[object, ...], int] = {}
    excluded: set[tuple[object, ...]] = set()
    relabeled = 0
    for row in rows:
        counts = {option: ine.number(row[option]) for option in options}
        key = (year, *unit_key(row))
        if (year, kind, *key[1:]) in EXCLUDED:
            excluded.add((year, kind, *key[1:]))
            continue
        if (sum(counts.values()) != ine.number(row[total])) != (key in DIFFERS):
            raise ValueError(f"{year} {kind} {key}: options add to {sum(counts.values())}, total {row[total]}")
        unit = {k: ine.clean(row[k]).lstrip("0") or "0" if k != "casilla" else ine.clean(row[k]) for k in ine.KEYS}
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            relabel = RELABELED.get((year, kind, *key[1:], seen[key]))
            if relabel is not None:
                relabeled += 1
            unit["casilla"] = relabel or f"{unit['casilla']}~{seen[key]}"
        unit["circunscripcion"] = ine.clean(row["circunscripcion"])
        units.append(
            unit
            | {
                "total": sum(counts.values()),
                "registered": ine.number(row[roll]) if roll else None,
                "status": ine.clean(row.get("ESTATUS_ACTA") or ""),
                "tepjf": ine.clean(row.get("TEPJF") or ""),
                # An acta of an extraordinary election, which INE's files print in place of the annulled count.
                "extraordinary": (row.get("RUTA_ACTA") or "").startswith(f"JE{year}E"),
            }
        )
        for option, value in counts.items():
            votes.append(
                unit
                | {
                    "option": option,
                    "kind": "unregistered"
                    if option in ine.UNREGISTERED
                    else "invalid"
                    if option in ine.NONVOTES
                    else "list",
                    "votes": value,
                }
            )
    repeats = sum(count - 1 for count in seen.values()) - relabeled
    if repeats != REPEATS.get((year, kind), 0):
        raise ValueError(f"{year} {kind}: {repeats} rows repeat an earlier casilla")
    if excluded != {key for key in EXCLUDED if key[:2] == (year, kind)}:
        raise ValueError(f"{year} {kind}: excluded rows {sorted(excluded)}")
    frame = pl.DataFrame(units, schema_overrides={"registered": pl.Int64})
    duplicates = frame.group_by(ine.KEYS).len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(f"{year} {kind}: duplicate casillas {duplicates.head()}")
    return frame, pl.DataFrame(votes)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in sorted(ELECTIONS):
        for kind in kinds(year):
            try:
                units, votes = table(kind, year)
            except ValueError as error:
                print("FAILED", year, kind, str(error)[:300])
                continue
            units.write_parquet(PARSED / f"{kind.lower()}_{year}_casillas.parquet")
            votes.write_parquet(PARSED / f"{kind.lower()}_{year}_votes.parquet")
            national = votes.group_by("option").agg(pl.col("votes").sum()).sort("votes", descending=True)
            print(year, kind, units.height, "casillas", units["total"].sum(), "votes", dict(national.rows()))
    from ea.sources.mx import diputados_packages

    diputados_packages.parse()


# Convergencia took the name Movimiento Ciudadano in 2011, and the Partido Socialdemócrata was Alternativa
# Socialdemócrata y Campesina until 2008: each is the same party under its name of the day.
EXTRA_PARTIES = {
    "PH": ("Q17624582", "Partido Humanista", "Humanist Party"),
    "CONV": ("Q10920094", "Convergencia", "Convergence"),
    "PSD": ("Q1803295", "Partido Socialdemócrata", "Social Democratic Party"),
}
PARTIES = ine.PARTIES | EXTRA_PARTIES
# Parties of 1991 that mx.senado keys by abbreviation; the Chamber's 1991 lists name the same parties.
ABBREVIATED = {
    "PRT": ("Partido Revolucionario de los Trabajadores", "Revolutionary Workers' Party"),
    "PEM": ("Partido Ecologista de México", "Ecologist Party of Mexico"),
}
INDEPENDENT = "IND"
CPEUM = "Constitución Política de los Estados Unidos Mexicanos"
CPEUM_URL = "https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf"
BODY_SINCE = date(1988, 9, 1)
MR_TITLE = "Cómputos distritales de la elección de Diputados por mayoría relativa, por casilla (1994-2015)"
THRESHOLD = 2

ELECTIONS: dict[int, dict] = {
    # 2006 and 2003 are built as packages (ea.sources.mx.diputados_packages); the changeset emit leaves them out.
    2006: {
        "day": date(2006, 7, 2),
        "packaged": True,
        "encoding": "latin-1",
        "columns": {
            "PAN": ("PAN",),
            "APM": ("PRI", "PVEM"),
            "PBT": ("PRD", "PT", "CONV"),
            "NVA_ALIANZA": ("NA",),
            "ASDC": ("PSD",),
        },
        "acuerdo": (
            "https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/86346/CGe230806ap2.pdf",
            (
                "CG163/2006: Acuerdo del Consejo General del Instituto Federal Electoral por el que se efectúa el "
                "cómputo total, se declara la validez de la elección de Diputados por el Principio de Representación "
                "Proporcional y se asignan a los partidos políticos Acción Nacional, Revolucionario Institucional, de "
                "la Revolución Democrática, del Trabajo, Verde Ecologista de México, Convergencia, Nueva Alianza y "
                "Alternativa Socialdemócrata y Campesina, los Diputados que por este principio les corresponden de "
                "acuerdo con la votación obtenida por cada uno de ellos en el proceso electoral federal del año dos "
                "mil seis"
            ),
            {"votes": 35, "seats": 45},
        ),
        # The acuerdo counts each coalition's list as one line.
        "votes": {
            "PAN": 13845122,
            "APM": 11676598,
            "PBT": 12013360,
            "NA": 1883494,
            "PSD": 850985,
            "unregistered": 128825,
            "null": 1037578,
            "total": 41435962,
        },
        "seats": {
            "PAN": (17, 19, 10, 11, 12),
            "APM": (14, 12, 14, 8, 10),
            "PBT": (7, 7, 14, 18, 14),
            "NA": (2, 2, 1, 2, 2),
            "PSD": (0, 0, 1, 1, 2),
        },
        # What the district counts in INE's file give by plurality, and what the acuerdo's table of majority seats
        # (considerando 36) declares; one district differs.
        "district_winners": {"PAN": 137, "CONV–PRD–PT": 99, "PRI–PVEM": 64},
        "winners_note": (
            " By plurality the counts in INE's file give the PAN 137 districts, the Por el Bien de Todos coalition "
            "99, and the Alianza por México 64; the Consejo General's table of majority seats (CG163/2006, "
            "considerando 36) declares 137, 98, and 65, so the file's count differs from the declared result in one "
            "district."
        ),
    },
    2003: {
        "day": date(2003, 7, 6),
        "packaged": True,
        "encoding": "latin-1",
        "columns": {
            "PAN": ("PAN",),
            "PRI": ("PRI",),
            "PRD": ("PRD",),
            "PT": ("PT",),
            "PVEM": ("PVEM",),
            "CONV": ("CONV",),
            "PSN": ("PSN",),
            "PAS": ("PAS",),
            "MP": ("MP",),
            "PLM": ("PLM",),
            "FC": ("FC",),
            "APT": ("PRI", "PVEM"),
        },
        "acuerdo": (
            DOF_2003,
            (
                "CG220/2003: Acuerdo del Consejo General del Instituto Federal Electoral por el que se efectúa el "
                "cómputo total, se declara la validez de la elección de Diputados por el principio de "
                "Representación Proporcional y se asignan a los partidos políticos nacionales los Diputados que por "
                "este principio les corresponden (Diario Oficial de la Federación, 22 de septiembre de 2003)"
            ),
            {"votes": "considerando 16", "seats": "considerando 29", "annulled": "considerando 31"},
        ),
        # Considerando 16 distributes the votes marked for the Alianza para Todos coalition between the PRI and the
        # PVEM, and takes the party counts as the base of the allocation.
        "votes": {
            "PAN": 8264646,
            "PRI": 9294527,
            "PRD": 4741382,
            "PT": 643781,
            "PVEM": 1653362,
            "CONV": 607909,
            "PSN": 72567,
            "PAS": 198677,
            "MP": 244035,
            "PLM": 109219,
            "FC": 124511,
            "unregistered": 16498,
            "null": 903664,
            "total": 26874778,
        },
        "seats": {
            "PAN": (16, 16, 14, 13, 12),
            "PRI": (16, 15, 14, 9, 10),
            "PRD": (4, 4, 7, 13, 13),
            "PT": (1, 2, 1, 1, 0),
            "PVEM": (3, 3, 2, 3, 3),
            "CONV": (0, 0, 2, 1, 2),
        },
        "annulled": {
            ("16", 5): "SUP-REC-034/2003",
            ("05", 6): "SUP-REC-009/2003 and SUP-REC-010/2003",
        },
        "national_method": (
            "National vote for the party lists as INE's Consejo General declared it in its acuerdo on the "
            "proportional allocation (considerando 16), after the district counts and the rulings on challenges. "
            "The votes marked for the Alianza para Todos coalition are distributed between the PRI and the PVEM, as "
            "the acuerdo does before it allocates seats."
        ),
        "seats_method": (
            "Seats by party as the Consejo General assigned them to this circunscripción, from the final table in "
            "considerando 29 of its acuerdo (Diario Oficial de la Federación, 22 September 2003). The acuerdo "
            "deducts ten list seats from the PRI under the limit on over-representation (considerando 24)."
        ),
    },
    2015: {
        "day": date(2015, 6, 7),
        "columns": {
            "PAN": ("PAN",),
            "PRI": ("PRI",),
            "PRD": ("PRD",),
            "PVEM": ("PVEM",),
            "PT": ("PT",),
            "MC": ("MC",),
            "NVA_ALIANZA": ("NA",),
            "MORENA": ("MORENA",),
            "PH": ("PH",),
            "ES": ("PES",),
            "PAN_NVA_ALIANZA": ("PAN", "NA"),
            "PRI_PVEM": ("PRI", "PVEM"),
            "PRD_PT": ("PRD", "PT"),
            "CAND_IND1": (INDEPENDENT,),
            "CAND_IND2": (INDEPENDENT,),
        },
        "acuerdo": (
            "https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/79670/CGex201508-23_ap_unico.pdf",
            (
                "INE/CG804/2015: Acuerdo del Consejo General del Instituto Nacional Electoral por el que se efectúa "
                "el cómputo total, se declara la validez de la elección de Diputados por el principio de "
                "representación proporcional y se asignan a los partidos políticos nacionales los Diputados que "
                "les corresponden para el periodo 2015-2018"
            ),
            {"votes": 14, "seats": 64},
        ),
        "votes": {
            "PAN": 8377535,
            "PRI": 11636957,
            "PRD": 4335321,
            "PT": 1134101,
            "PVEM": 2757170,
            "MC": 2431063,
            "NA": 1486626,
            "MORENA": 3345712,
            "PH": 856716,
            "PES": 1325032,
            "unregistered": 52371,
            INDEPENDENT: 225029,
            "null": 1900449,
            "total": 39864082,
        },
        "seats": {
            "PAN": (13, 15, 8, 8, 9),
            "PRI": (10, 11, 10, 6, 11),
            "PRD": (2, 3, 6, 8, 8),
            "PVEM": (2, 4, 7, 3, 2),
            "MC": (7, 2, 1, 3, 2),
            "NA": (2, 2, 2, 2, 2),
            "MORENA": (2, 2, 5, 8, 4),
            "PES": (2, 1, 1, 2, 2),
        },
        # INE's district count file holds, for this district, the actas of the extraordinary election (paths
        # JE2015E) and not the annulled June count.
        "extraordinary": {
            ("01", 1): {
                "day": date(2015, 12, 6),
                "count": AGS_2015_COUNT,
                "site": "http://computos2015-ags.ine.mx/",
                "title": (
                    "Cómputos distritales 2015, elección extraordinaria de la diputación de mayoría relativa del "
                    "Distrito 01 de Aguascalientes (Jesús María)"
                ),
                "page": 59,
                "casilla_note": (
                    " For this district the file holds the actas of the extraordinary election of 6 December 2015, "
                    "which INE's archived count summary for that election confirms (109,084 votes in 445 actas)."
                ),
                "annulled": (
                    "The Sala Regional Monterrey of the electoral tribunal annulled this district's election "
                    "(SM-JIN-35/2015) and the Sala Superior confirmed the ruling (SUP-REC-503/2015), so no deputy was "
                    "elected here on 7 June; the Consejo General's acuerdo on the list seats records the annulment "
                    "(page 59). The seat was filled by the extraordinary election of 6 December 2015. INE's district "
                    "count file holds only the extraordinary election's actas for this district, so the annulled June "
                    "count is not stored here."
                ),
                "rerun": (
                    "Held after the electoral tribunal annulled the 7 June 2015 election of the deputy for "
                    "Aguascalientes district 1 (Sala Regional Monterrey, SM-JIN-35/2015, confirmed by the Sala "
                    "Superior in SUP-REC-503/2015). Its result does not enter the 2015 list-seat allocation, which "
                    "counts the June vote."
                ),
            },
        },
    },
    2012: {
        "day": date(2012, 7, 1),
        "columns": {
            "PAN": ("PAN",),
            "PRI": ("PRI",),
            "PRD": ("PRD",),
            "PVEM": ("PVEM",),
            "PT": ("PT",),
            "MC": ("MC",),
            "NVA_ALIANZA": ("NA",),
            "PRI_PVEM": ("PRI", "PVEM"),
            "PRD_PT_MC": ("PRD", "PT", "MC"),
            "PRD_PT": ("PRD", "PT"),
            "PRD_MC": ("PRD", "MC"),
            "PT_MC": ("PT", "MC"),
        },
        "acuerdo": (
            "https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/86836/CGe230812ap_2.pdf",
            (
                "CG582/2012: Acuerdo del Consejo General del Instituto Federal Electoral por el que se efectúa el "
                "cómputo total, se declara la validez de la elección de Diputados por el principio de representación "
                "proporcional y se asignan a los partidos políticos nacionales: Acción Nacional, Revolucionario "
                "Institucional, de la Revolución Democrática, del Trabajo, Verde Ecologista de México, Movimiento "
                "Ciudadano y Nueva Alianza, los Diputados que por este principio les corresponden de acuerdo con la "
                "votación obtenida por cada uno de ellos en el Proceso Electoral Federal 2011-2012"
            ),
            {"votes": 47, "seats": 71},
        ),
        "votes": {
            "PAN": 12960875,
            "PRI": 15960086,
            "PRD": 9194637,
            "PT": 2294460,
            "PVEM": 3054718,
            "MC": 2000524,
            "NA": 2041608,
            "unregistered": 41556,
            "null": 2494300,
            "total": 50042764,
        },
        "seats": {
            "PAN": (14, 18, 11, 9, 10),
            "PRI": (11, 10, 10, 7, 11),
            "PRD": (5, 5, 10, 13, 11),
            "PT": (2, 2, 2, 3, 2),
            "PVEM": (2, 3, 4, 3, 3),
            "MC": (4, 0, 1, 3, 1),
            "NA": (2, 2, 2, 2, 2),
        },
    },
    2009: {
        "day": date(2009, 7, 5),
        "columns": {
            "PAN": ("PAN",),
            "PRI": ("PRI",),
            "PRD": ("PRD",),
            "PVEM": ("PVEM",),
            "PT": ("PT",),
            "CONVERGENCIA": ("CONV",),
            "NVA_ALIANZA": ("NA",),
            "PSD": ("PSD",),
            "PRIMERO_MEXICO": ("PRI", "PVEM"),
            "SALVEMOS_MEXICO": ("PT", "CONV"),
        },
        "acuerdo": (
            "https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/91140/CGe210809apunico.pdf",
            (
                "CG426/2009: Acuerdo del Consejo General del Instituto Federal Electoral por el que se efectúa el "
                "cómputo total, se declara la validez de la elección de Diputados por el principio de representación "
                "proporcional y se asignan a los partidos políticos: Acción Nacional, Revolucionario Institucional, de "
                "la Revolución Democrática, del Trabajo, Verde Ecologista de México, Convergencia y Nueva Alianza, los "
                "Diputados que por este principio les corresponden de acuerdo con la votación obtenida por cada uno de "
                "ellos en el Proceso Electoral Federal del año dos mil nueve"
            ),
            {"votes": 50, "seats": 68},
        ),
        "votes": {
            "PAN": 9714180,
            "PRI": 12809395,
            "PRD": 4228623,
            "PT": 1268151,
            "PVEM": 2326045,
            "CONV": 854311,
            "NA": 1186875,
            "PSD": 358485,
            "unregistered": 56819,
            "null": 1875107,
            "total": 34677991,
        },
        "seats": {
            "PAN": (18, 17, 15, 11, 12),
            "PRI": (11, 12, 12, 7, 11),
            "PRD": (3, 3, 7, 10, 9),
            "PT": (2, 1, 1, 4, 2),
            "PVEM": (4, 4, 2, 4, 3),
            "CONV": (0, 1, 1, 2, 2),
            "NA": (2, 2, 2, 2, 1),
        },
    },
    1991: {
        "day": date(1991, 8, 18),
        "files": {"MR": MR_1991},
        "encoding": "latin-1",
        "place": "state",
        "columns": {
            abbrev: (abbrev,) for abbrev in ("PAN", "PRI", "PPS", "PRD", "PFCRN", "PARM", "PDM", "PRT", "PEM", "PT")
        },
        "acuerdo": (
            DOF_1991,
            (
                "Acuerdo del Consejo General del Instituto Federal Electoral relativo al cómputo total de la elección "
                "de todas las listas de diputados electos según el principio de representación proporcional y "
                "asignación a los partidos políticos nacionales de los 200 diputados que les corresponden por el "
                "principio de representación proporcional (Diario Oficial de la Federación, 1 de octubre de 1991)"
            ),
            {"votes": "considerando I", "seats": "considerando VI"},
        ),
        "also": (DOF_1991_ERRATA,),
        "national_method": (
            "National vote for the party lists as the Consejo General of the Instituto Federal Electoral declared it in "
            "its acuerdo on the proportional allocation (considerando I), the sum of the five circunscripción counts "
            "after the arithmetic corrections the acuerdo makes to the totals of the 1st and 5th circunscripción "
            "actas. The acuerdo gives no separate count of votes for unregistered candidates."
        ),
        "seats_method": (
            "Seats by party as the Consejo General assigned them to this circunscripción, from the summary of the "
            "assignment in considerando VI of its acuerdo (Diario Oficial de la Federación, 1 October 1991); the "
            "fe de erratas of 4 October 1991 corrects only working figures and wording."
        ),
        "casilla_note": (
            " In 1991 the file prints the type of contiguous and extraordinary casillas without their number, so some "
            "rows repeat another row's section and casilla; each is a separate acta with its own counts, kept in file "
            "order as -r2, -r3. The 1991 sections are numbered within each municipality and the file's municipality "
            "codes predate today's, so each casilla is placed only in its state."
        ),
        "missing": {
            ("05", 5): (
                "INE's 1991 district count file has no rows for this district, so the contest has no results yet."
            ),
        },
        "rp_system": "deputies-rp-1991",
        "circunscripciones": {
            1: "Distrito Federal, Puebla, Tlaxcala",
            2: (
                "Aguascalientes, Chihuahua, Coahuila, Durango, Guanajuato, Hidalgo, Querétaro, San Luis Potosí, "
                "Zacatecas"
            ),
            3: "Campeche, Chiapas, Nuevo León, Quintana Roo, Tabasco, Tamaulipas, Veracruz, Yucatán",
            4: "Baja California, Baja California Sur, Colima, Jalisco, Michoacán, Nayarit, Sinaloa, Sonora",
            5: "Guerrero, Estado de México, Morelos, Oaxaca",
        },
        "votes": {
            "PAN": 4097795,
            "PRI": 14215695,
            "PPS": 415517,
            "PRD": 1914649,
            "PFCRN": 1010921,
            "PARM": 495953,
            "PDM": 250490,
            "PRT": 137144,
            "PEM": 338324,
            "PT": 263951,
            "null": 1008562,
            "total": 24149001,
        },
        "seats": {
            "PAN": (14, 22, 13, 19, 11),
            "PRI": (5, 6, 7, 6, 6),
            "PPS": (3, 2, 3, 1, 3),
            "PRD": (9, 5, 7, 9, 11),
            "PFCRN": (6, 3, 5, 3, 6),
            "PARM": (3, 2, 5, 2, 3),
        },
    },
}
TURNOUT = (
    "INE's district count files print more votes than the nominal roll for these casillas; the counts are stored as "
    "the files print them"
)
SEATS_ONLY = (
    "Each circunscripción's set holds the list seats the Consejo General assigned there as outcomes; the list vote is "
    "stored once, in the national set"
)


CIRCUNSCRIPCIONES = {
    1: "Baja California, Baja California Sur, Chihuahua, Durango, Jalisco, Nayarit, Sinaloa, Sonora",
    2: "Aguascalientes, Coahuila, Guanajuato, Nuevo León, Querétaro, San Luis Potosí, Tamaulipas, Zacatecas",
    3: "Campeche, Chiapas, Oaxaca, Quintana Roo, Tabasco, Veracruz, Yucatán",
    4: "Ciudad de México, Guerrero, Morelos, Puebla, Tlaxcala",
    5: "Colima, Hidalgo, Estado de México, Michoacán",
}
MR_RULES = {"formula": "fptp"}
RP_RULES = {
    "formula": "hare",
    "threshold": {"percent": THRESHOLD, "level": "party", "scope": "national"},
    "lists": {"order": "closed", "vacancy": "next_on_list"},
}
MR_SYSTEM = (
    "Each of the 300 single-member federal districts elects one deputy by plurality of the votes cast, with no "
    "runoff. The Chamber of Deputies has 500 members: these 300, and 200 more elected by proportional "
    "representation from closed regional lists."
)
RP_SYSTEM = (
    "The 200 list seats are filled from closed lists in five regional constituencies (circunscripciones "
    "plurinominales) of 40 seats each. A party takes part in the allocation if it reaches 2 percent of the national "
    "vote (3 percent from 2015) and runs district candidates in at least 200 districts. Seats go by the natural "
    "quotient and largest remainders on the national vote, subject to two limits: no party may hold more than "
    "300 seats from both tiers, and none may hold a share of the Chamber more than 8 points above its share of the "
    "national vote. The model records the formula and the threshold; the two limits are in the declared seats."
)
RP_SYSTEMS = {
    "deputies-rp-1991": (
        "Mexican federal regional closed-list proportional system (1991)",
        {
            "formula": "hare",
            "threshold": {"percent": 1.5, "level": "party", "scope": "national"},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "In 1991 the 200 list seats were filled from closed lists in five regional constituencies "
            "(circunscripciones plurinominales) of 40 seats each, under the 1990 electoral code (COFIPE). A party took "
            "part if it reached 1.5 percent of the vote and ran district candidates in at least 200 districts. The "
            "constitution's governability rules (article 54, fraction IV) were applied first: a party with between 60 "
            "and 70 percent of the national vote whose district seats fell short of that share, the PRI in 1991, "
            "received list seats up to its share of the Chamber, spread over the circunscripciones by its votes there. "
            "The other parties shared each circunscripción's remaining seats by the formula of first proportionality: "
            "a rectified quotient for the first two seats, then a unit quotient, then the largest remainders. The "
            "model records the threshold and a largest-remainder formula; the governability rule and the 1991 formula "
            "are in the declared seats."
        ),
    ),
}
MR_METHOD = (
    "Polling-station (casilla) counts from INE's district count files, summed to each district. The winner is the "
    "line or coalition candidate with the most votes in the district count, which is the count on which the "
    "district council awards the certificate of majority. Votes marked for two or more parties of one coalition "
    "are counted as that coalition's candidate. The file prints no candidate names."
)
CASILLA_NOTE = (
    "Polling-station results from INE's district count file for the district. Each line is a column of the file: "
    "a party, or a combination of the parties of one coalition that a voter marked."
)
RP_NATIONAL_METHOD = (
    "National vote for the party lists as INE's Consejo General declared it in its acuerdo on the proportional "
    "allocation (page {page} of the document), after the district counts, the rulings on challenges, and the "
    "recomposition ordered by the electoral tribunal. Votes marked for several parties of a coalition are "
    "distributed among them."
)
RP_SEATS_METHOD = (
    "Seats by party as the Consejo General assigned them to this circunscripción, from the table on page {page} of "
    "its acuerdo."
)
REGISTERED, BALLOTS, VALID, INVALID, VOTES = ine.REGISTERED, ine.BALLOTS, ine.VALID, ine.INVALID, ine.VOTES
# The 1996 districting plan took effect on this day (mx.historicos dates its outlines from it); elections before it,
# such as 1991, used the 1978 plan, whose districts the archive has no outlines for.
PLANS_FROM = date(1996, 7, 31)
RP_CASILLA_NOTE = (
    "Polling-station results of the list ballot from INE's file of the district counts for proportional "
    "representation. A special casilla's two actas, the one for voters inside the district (whose ballot counts for "
    "the district and the list) and the one for voters from outside it (list only), are added together. Votes marked "
    "for several parties of a coalition are distributed among those parties as the district count does: in each "
    "district the sum of such votes is split equally, and any remainder goes to the coalition's parties with the most "
    "votes in the district. Each casilla takes an equal share of its own marks, and the votes left over when a "
    "casilla's marks do not divide evenly are placed, casilla by casilla in the file's order, with the parties still "
    "owed votes, so each district adds up to the district count's distribution. These are the counts before the "
    "electoral tribunal's annulments, so they add up to slightly more than the Consejo General's declared national "
    "vote, which is the default result."
)


def distribute(
    rows: list[tuple[str, str, int]], columns: dict[str, tuple[str, ...]], rank: dict[str, int]
) -> dict[tuple[str, str], int]:
    """One district's list votes by casilla and party. `rows` holds each casilla's votes by file column, in file
    order; `rank` the district's votes for each party alone. A column that names several parties is split as the
    district count splits it (LGIPE art. 311, and the 2008 COFIPE before it): the district's sum equally, any remainder
    to the parties with the most votes, ties by abbreviation. Each casilla takes the equal part of its own marks, and
    the votes its marks leave over go to the parties still owed the most, so every party's district sum is its share
    of the district count."""
    out: dict[tuple[str, str], int] = {}
    combined: dict[str, list[tuple[str, int]]] = {}
    for unit, column, votes in rows:
        members = columns[column]
        if len(members) == 1:
            out[(unit, members[0])] = out.get((unit, members[0]), 0) + votes
        elif votes:
            combined.setdefault(column, []).append((unit, votes))
    for column, marks in combined.items():
        members = sorted(columns[column], key=lambda party: (-rank.get(party, 0), party))
        size = len(members)
        total = sum(votes for _, votes in marks)
        owed = {party: total // size + (index < total % size) for index, party in enumerate(members)}
        for unit, votes in marks:
            for party in members:
                out[(unit, party)] = out.get((unit, party), 0) + votes // size
                owed[party] -= votes // size
        for unit, votes in marks:
            for party in sorted(members, key=lambda party: -owed[party])[: votes % size]:
                out[(unit, party)] += 1
                owed[party] -= 1
        if any(owed.values()):
            raise ValueError(f"{column}: the distribution leaves {owed}")
    return out


def count_summary(path: Path) -> dict[str, object]:
    """The summary an INE count site serves as a script, `var data = {...}`, read as data."""
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text.removeprefix("var data =").strip().removesuffix(";"))


def locator(at: int | str) -> dict[str, int | str]:
    """A page of an acuerdo's PDF, or a section of one the Diario Oficial prints as text."""
    return {"page": at} if isinstance(at, int) else {"section": at}


def groups_of(columns: dict[str, tuple[str, ...]], present: list[str]) -> list[list[str]]:
    parent = {column: column for column in present}

    def find(column: str) -> str:
        while parent[column] != column:
            parent[column] = parent[parent[column]]
            column = parent[column]
        return column

    for column in present:
        members = set(columns[column])
        if len(members) < 2:
            continue
        for other in present:
            if set(columns[other]) <= members:
                parent[find(other)] = find(column)
    result: dict[str, list[str]] = {}
    for column in present:
        result.setdefault(find(column), []).append(column)
    return list(result.values())


def district_frame(
    joined: pl.DataFrame,
    located: pl.DataFrame,
    options: dict[str, str],
    write_in: str,
    casilla_set: str,
    district_unit: str,
    district_set: str,
) -> pl.DataFrame:
    empty = pl.lit(None, pl.String)
    parts = [
        located.filter(pl.col("registered") > 0).select(
            "unit_id", "channel", option_id=empty, measure=pl.lit(REGISTERED), value="registered"
        ),
        located.select("unit_id", "channel", option_id=empty, measure=pl.lit(BALLOTS), value="total"),
        joined.filter(pl.col("kind") == "invalid").select(
            "unit_id", "channel", option_id=empty, measure=pl.lit(INVALID), value="votes"
        ),
        joined.filter(pl.col("kind") != "invalid")
        .group_by("unit_id", "channel")
        .agg(value=pl.col("votes").sum())
        .select("unit_id", "channel", option_id=empty, measure=pl.lit(VALID), value="value"),
        joined.filter((pl.col("kind") != "invalid") & (pl.col("votes") != 0)).select(
            "unit_id",
            "channel",
            option_id=pl.when(pl.col("kind") == "unregistered")
            .then(pl.lit(write_in))
            .otherwise(pl.col("option").replace_strict(options, default=None)),
            measure=pl.lit(VOTES),
            value="votes",
        ),
    ]
    rows = pl.concat(
        [
            part.select(
                unit_id="unit_id",
                option_id="option_id",
                measure=pl.col("measure").cast(pl.Int16),
                value=pl.col("value").cast(pl.Int64),
                channel=pl.col("channel").cast(pl.Int16),
            )
            for part in parts
        ]
    )
    casilla = rows.with_columns(result_set_id=pl.lit(casilla_set))
    district = (
        rows.group_by("option_id", "measure", "channel")
        .agg(value=pl.col("value").sum())
        .with_columns(result_set_id=pl.lit(district_set), unit_id=pl.lit(district_unit))
    )
    return pl.concat([casilla, district.select(casilla.columns)])


def emit() -> Path:
    years = sorted(year for year in ELECTIONS if not ELECTIONS[year].get("packaged"))
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Mexican Chamber of Deputies ({', '.join(map(str, years))}): the 300 district contests of each "
            "election by polling station from INE's district counts, and the 200 list seats by circunscripción from "
            "the Consejo General's acuerdo",
            polity="MX",
        )
        cs.accept("accounting.turnout", TURNOUT)
        cs.accept("result_set.empty", SEATS_ONLY)
        for scheme in ("mx.entidad", "mx.seccion", "mx.casilla", "mx.distrito_federal"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.diputados")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        ine_source = cs.source(ine.INE, MR_TITLE)
        cs.add(
            "source",
            id=ine_source,
            kind="official_body",
            publisher=ine.INE,
            title=MR_TITLE,
            url=ine.INE_PAGE,
            license=ine.INE_LICENSE,
            license_url=ine.INE_PAGE,
            attribution="Instituto Nacional Electoral, cómputos distritales",
        )
        law = cs.source(ine.CAMARA, CPEUM)
        cs.add(
            "source", id=law, kind="official_body", publisher=ine.CAMARA, title=CPEUM, url=CPEUM_URL, license=ine.PUBLIC
        )

        body = cs.key("mx.body", "chamber-of-deputies")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=mexico, kind="lower_house")
            name(body, "Chamber of Deputies of Mexico")
            name(body, "Cámara de Diputados del H. Congreso de la Unión", "es", "native")
            cs.add("body_size", body_id=body, seats=500, valid_from=BODY_SINCE)
            cs.add("citation", entity_id=body, source_id=law, locator={"article": 52})
        mr_system = cs.key("mx.electoral_system", "deputies-mr")
        if mr_system in cs.minted:
            cs.add("electoral_system", id=mr_system, polity_id=mexico, rules=MR_RULES, rules_version=1)
            name(mr_system, "Mexican federal district plurality system")
            cs.add("note", entity_id=mr_system, kind="methodology", body_md=MR_SYSTEM)
        rp_system = cs.key("mx.electoral_system", "deputies-rp")
        if rp_system in cs.minted:
            cs.add("electoral_system", id=rp_system, polity_id=mexico, rules=RP_RULES, rules_version=1)
            name(rp_system, "Mexican federal regional closed-list proportional system")
            cs.add("note", entity_id=rp_system, kind="methodology", body_md=RP_SYSTEM)
        rp_systems = {"deputies-rp": rp_system}

        def system_of(key: str) -> UUID:
            if key not in rp_systems:
                label, rules, text = RP_SYSTEMS[key]
                rp_systems[key] = cs.key("mx.electoral_system", key)
                if rp_systems[key] in cs.minted:
                    cs.add("electoral_system", id=rp_systems[key], polity_id=mexico, rules=rules, rules_version=1)
                    name(rp_systems[key], label)
                    cs.add("note", entity_id=rp_systems[key], kind="methodology", body_md=text)
            return rp_systems[key]

        parties: dict[str, UUID] = {}

        def party(abbrev: str) -> UUID:
            if abbrev in ABBREVIATED and abbrev not in parties:
                native, english = ABBREVIATED[abbrev]
                parties[abbrev] = cs.key("mx.party", abbrev)
                if parties[abbrev] in cs.minted:
                    cs.add("party", id=parties[abbrev], kind="party", polity_id=mexico)
                    name(parties[abbrev], native, "es")
                    name(parties[abbrev], english, "en", "english")
                    name(parties[abbrev], abbrev, "es", "abbreviation")
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

        states = {
            code: text
            for code, text in conn.execute(
                "select c.code, n.text from area a join area_code c on c.area_id = a.id "
                "join name n on n.entity_id = a.id and n.is_preferred where a.scheme_code = 'mx.entidad'"
            ).fetchall()
        }

        def district_contest(
            key: str,
            election: UUID,
            path: str,
            year: int,
            ss: str,
            dd: int,
            area: UUID | None,
            tag: tuple[str, str] = ("", ""),
        ) -> UUID:
            contest = cs.key("mx.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=1,
                electoral_system_id=mr_system,
                default_result_kind="counted",
            )
            name(contest, f"{year} Chamber of Deputies, {states[ss]}, district {dd}{tag[0]}")
            name(contest, f"Diputación federal {year}, {states[ss]}, distrito {dd}{tag[1]}", "es", "native")
            cs.add("slug", path=f"{path}/{slug(states[ss])}-district-{dd}", entity_id=contest)
            return contest

        # Extraordinary elections, by day: the election and its path.
        reruns: dict[date, tuple[UUID, str]] = {}

        def rerun_election(rerun: dict, election: UUID, year: int, ss: str, dd: int) -> tuple[UUID, str]:
            held: date = rerun["day"]
            if held in reruns:
                return reruns[held]
            by_election = cs.key("mx.election", f"{held}:extraordinary")
            path = f"mx/{held}-by-election"
            if by_election in cs.minted:
                cs.add(
                    "election",
                    id=by_election,
                    polity_id=mexico,
                    kind="by_election",
                    date_from=held,
                    date_to=held,
                    status="held",
                    rerun_of_id=election,
                )
                name(by_election, f"{year} extraordinary election of the deputy for {states[ss]} district {dd}")
                name(
                    by_election,
                    f"Elección extraordinaria de diputación federal de {year}, {states[ss]}, distrito {dd}",
                    "es",
                    "native",
                )
                cs.add("slug", path=path, entity_id=by_election)
                cs.add("note", entity_id=by_election, kind="methodology", body_md=rerun["rerun"])
            count = cs.source(ine.INE, rerun["title"])
            cs.add(
                "source",
                id=count,
                kind="official_body",
                publisher=ine.INE,
                title=rerun["title"],
                url=rerun["site"],
                license=ine.INE_LICENSE,
            )
            cs.artifact(raw.cached(RAW, rerun["count"]), count)
            cs.add("citation", entity_id=by_election, source_id=count, locator={"section": "totVot"})
            reruns[held] = (by_election, path)
            return reruns[held]

        nation_unit = cs.unit("mx.nation", "MX")
        unit_rows: dict[UUID, tuple[str, str, UUID | None, str, UUID, str]] = {
            nation_unit: ("mx.nation", "MX", None, "equals", cs.area("mx.nation", "MX"), "standard")
        }
        state_units: dict[str, UUID] = {}
        for code in range(1, 33):
            state = f"{code:02d}"
            unit = state_units[state] = cs.unit("mx.entidad", state)
            unit_rows[unit] = ("mx.entidad", state, nation_unit, "equals", cs.area("mx.entidad", state), "standard")
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
        rows: list[tuple[str, str, str | None, int, int, int]] = []
        frames: list[pl.DataFrame] = []

        def locate(
            year: int, row: dict[str, str], codes: set[str] | None, by_state: bool
        ) -> tuple[str, int, str, UUID | None]:
            """A casilla's unit code, channel, kind, and area. A special casilla's two actas (SMR and SRP) share its
            code. Without `codes`, a code that recurs is left as it is."""
            state = f"{int(row['state']):02d}"
            district = int(row["district"]) if row["district"].isdigit() else 0
            section = int(row["section"])
            letter = row["casilla"][:1] or "X"
            printed, _, repeat = row["casilla"].partition("~")
            code = f"{year}:{state}-{district:02d}-{section:04d}-{ine.casilla_code(year, re.sub(r'^S(?:MR|RP)', 'S', printed) or 'X')}"
            # As in mx.senado: a section number that recurs in the district under another municipality (1991
            # numbers sections within each municipality) carries the municipality, and a repeated row its order.
            if codes is not None:
                if code in codes:
                    code = f"{code}-m{int(row['municipality'] or 0)}"
                if repeat:
                    code = f"{code}-r{repeat}"
                codes.add(code)
            channel, kind, area = 0, "standard", None
            if letter in "MV" or section == 0:
                channel, kind = ine.ABROAD, "aggregate"
            elif letter == "A":
                channel, kind = ine.EARLY, "aggregate"
            elif letter == "P":
                channel, kind = ine.PRISON, "prison"
            elif not by_state:
                area = sections.get(f"{state}{section:04d}")
                if area is None and row["municipality"] not in ("", "0"):
                    area = towns.get(f"{state}{int(row['municipality']):03d}")
            return code, channel, kind, area

        def list_casillas(
            year: int,
            columns: dict[str, tuple[str, ...]],
            by_state: bool,
            options: dict[str, UUID],
            write_in: UUID | None,
            result_set: str,
            declared: dict[str, int],
            unit_rows: dict[UUID, tuple[str, str, UUID | None, str, UUID, str]],
        ) -> pl.DataFrame:
            """The list ballot by casilla from INE's file for proportional representation, on the national list
            contest's party options, with the votes for several parties of a coalition distributed among them."""
            votes = pl.read_parquet(PARSED / f"rp_{year}_votes.parquet")
            casillas = pl.read_parquet(PARSED / f"rp_{year}_casillas.parquet")
            casillas = casillas.filter((pl.col("casilla") != "") | (pl.col("total") > 0))
            unknown = set(votes.filter(pl.col("kind") == "list")["option"]) - set(columns)
            if unknown:
                raise ValueError(f"{year} list file: columns without a party: {sorted(unknown)}")
            placed, printed = [], {}
            for row in casillas.iter_rows(named=True):
                state = f"{int(row['state']):02d}"
                code, channel, kind, area = locate(year, row, None, by_state)
                printed.setdefault(code, []).append(row["casilla"])
                unit_id = cs.unit("mx.casilla", code)
                if unit_id not in unit_rows:
                    unit_rows[unit_id] = (
                        "mx.casilla",
                        code,
                        state_units[state],
                        "within",
                        area or cs.area("mx.entidad", state),
                        kind,
                    )
                placed.append((*(row[k] for k in ine.KEYS), str(unit_id), channel))
            # Only a special casilla's two actas share a code.
            for code, labels in printed.items():
                if len(labels) > 1 and sorted(label[:3] for label in labels) != ["SMR", "SRP"]:
                    raise ValueError(f"{year} list file: casilla {code} appears as {labels}")
            units = pl.DataFrame(
                placed,
                schema=[*((k, pl.String) for k in ine.KEYS), ("unit_id", pl.String), ("channel", pl.Int16)],
                orient="row",
            )
            located = casillas.join(units, on=ine.KEYS)
            joined = votes.join(units, on=ine.KEYS)
            channels = dict(units.select("unit_id", "channel").unique().iter_rows())
            parties: dict[tuple[str, str], int] = {}
            lists = joined.filter(pl.col("kind") == "list")
            for _, group in lists.group_by("state", "district", maintain_order=True):
                rank: dict[str, int] = {}
                for column, count in group.group_by("option").agg(pl.col("votes").sum()).iter_rows():
                    if len(columns[column]) == 1:
                        rank[columns[column][0]] = rank.get(columns[column][0], 0) + count
                for key, count in distribute(
                    list(group.select("unit_id", "option", "votes").iter_rows()), columns, rank
                ).items():
                    parties[key] = parties.get(key, 0) + count
            missing = {party for _, party in parties} - set(options)
            if missing:
                raise ValueError(f"{year} list file: parties without a list: {sorted(missing)}")
            empty = pl.lit(None, pl.String)
            by_unit = located.group_by("unit_id").agg(
                registered=pl.col("registered").fill_null(0).sum(), total=pl.col("total").sum()
            )
            invalid = joined.filter(pl.col("kind") == "invalid").group_by("unit_id").agg(value=pl.col("votes").sum())
            valid = joined.filter(pl.col("kind") != "invalid").group_by("unit_id").agg(value=pl.col("votes").sum())
            unregistered = (
                joined.filter(pl.col("kind") == "unregistered").group_by("unit_id").agg(value=pl.col("votes").sum())
            )
            if write_in is None and unregistered["value"].sum():
                raise ValueError(f"{year} list file: votes for unregistered candidates without a write-in option")
            party_rows = pl.DataFrame(
                [(unit, str(options[party]), count) for (unit, party), count in parties.items() if count],
                schema={"unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            )
            parts = [
                by_unit.filter(pl.col("registered") > 0).select(
                    "unit_id", option_id=empty, measure=pl.lit(REGISTERED), value="registered"
                ),
                by_unit.select("unit_id", option_id=empty, measure=pl.lit(BALLOTS), value="total"),
                invalid.select("unit_id", option_id=empty, measure=pl.lit(INVALID), value="value"),
                valid.select("unit_id", option_id=empty, measure=pl.lit(VALID), value="value"),
                unregistered.filter(pl.col("value") != 0).select(
                    "unit_id",
                    option_id=pl.lit(None if write_in is None else str(write_in)),
                    measure=pl.lit(VOTES),
                    value="value",
                ),
                party_rows.select("unit_id", "option_id", measure=pl.lit(VOTES), value="value"),
            ]
            frame = pl.concat(
                [
                    part.select(
                        result_set_id=pl.lit(result_set),
                        unit_id="unit_id",
                        option_id=pl.col("option_id").cast(pl.String),
                        measure=pl.col("measure").cast(pl.Int16),
                        value=pl.col("value").cast(pl.Int64),
                        channel=pl.col("unit_id").replace_strict(channels, return_dtype=pl.Int16),
                    )
                    for part in parts
                ]
            )
            # Each casilla's votes for the lists and unregistered candidates add to its valid votes.
            cast = frame.filter(pl.col("measure") == VOTES).group_by("unit_id").agg(pl.col("value").sum())
            short = valid.join(cast, on="unit_id", how="left").filter(
                pl.col("value") != pl.col("value_right").fill_null(0)
            )
            if short.height:
                raise ValueError(f"{year} list file: {short.height} casillas whose votes do not add to the valid")
            national = dict(
                frame.filter(pl.col("measure") == VOTES).group_by("option_id").agg(pl.col("value").sum()).iter_rows()
            )
            gaps = {
                party: national.get(str(options[party]), 0) - count
                for party, count in declared.items()
                if party != INDEPENDENT
            }
            print(year, "list casillas", units["unit_id"].n_unique(), "units; file less declared", gaps, flush=True)
            return frame

        def plan_areas(day: date) -> dict[str, UUID]:
            """The federal district areas, by code, whose outline is valid on the day: the 1996, 2005, 2017, or 2023
            plan. Before the 1996 plan there are none."""
            if day < PLANS_FROM:
                return {}
            return {
                str(code): id
                for code, id in conn.execute(
                    """select c.code, a.id from area a join area_code c on c.area_id = a.id
                       where a.scheme_code = 'mx.distrito_federal' and (c.valid is null or c.valid @> %s::date)
                         and exists (select from area_geometry g where g.area_id = a.id and g.valid @> %s::date)""",
                    [day, day],
                ).fetchall()
            }

        for year in years:
            config = ELECTIONS[year]
            day: date = config["day"]
            columns: dict[str, tuple[str, ...]] = config["columns"]
            url, title, pages = config["acuerdo"]
            for kind in kinds(year):
                cs.artifact(raw.cached(RAW, file_url(kind, year)), ine_source)
            acuerdo = cs.source(ine.INE, title)
            cs.add(
                "source", id=acuerdo, kind="official_body", publisher=ine.INE, title=title, url=url, license=ine.PUBLIC
            )
            for document in (url, *config.get("also", ())):
                cs.artifact(raw.cached(RAW, document), acuerdo)

            election = cs.key("mx.election", f"{day}:general")
            path = f"mx/{day}-general"
            if election in cs.minted:
                cs.add(
                    "election", id=election, polity_id=mexico, kind="general", date_from=day, date_to=day, status="held"
                )
                name(election, f"{year} Mexican general election")
                name(election, f"Elecciones federales de {year}", "es", "native")
                cs.add("slug", path=path, entity_id=election)

            def make_set(
                contest: UUID,
                key: str,
                default: bool,
                source: UUID,
                completeness: float,
                note: str,
                day: date = day,
                election: UUID = election,
            ) -> UUID:
                result_set = cs.key("mx.result_set", f"{day}:dip:{key}")
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

            alliances: dict[str, UUID] = {}

            def alliance_of(
                members: list[str], day: date = day, election: UUID = election, alliances: dict[str, UUID] = alliances
            ) -> UUID:
                key = "-".join(members)
                if key not in alliances:
                    alliances[key] = cs.key("mx.alliance", f"{day}:{key}")
                    cs.add("alliance", id=alliances[key], kind="electoral_alliance", election_id=election)
                    name(alliances[key], "–".join(members) + " coalition")
                    name(alliances[key], "Coalición " + "–".join(members), "es", "native")
                    for member in members:
                        cs.add("alliance_member", alliance_id=alliances[key], party_id=party(member))
                return alliances[key]

            votes = pl.read_parquet(PARSED / f"mr_{year}_votes.parquet")
            casillas = pl.read_parquet(PARSED / f"mr_{year}_casillas.parquet")
            casillas = casillas.filter((pl.col("casilla") != "") | (pl.col("total") > 0))
            unknown = set(votes.filter(pl.col("kind") == "list")["option"]) - set(columns)
            if unknown:
                raise ValueError(f"{year}: columns without a party: {sorted(unknown)}")

            placed = []
            codes: set[str] = set()
            by_state = config.get("place") == "state"
            for row in casillas.iter_rows(named=True):
                state = f"{int(row['state']):02d}"
                code, channel, kind, area = locate(year, row, codes, by_state)
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
                placed.append((*(row[k] for k in ine.KEYS), str(unit_id), channel))
            units = pl.DataFrame(
                placed,
                schema=[*((k, pl.String) for k in ine.KEYS), ("unit_id", pl.String), ("channel", pl.Int16)],
                orient="row",
            )
            located = casillas.join(units, on=ine.KEYS)
            joined = votes.join(units, on=ine.KEYS)
            totals = (
                joined.filter(pl.col("kind") == "list")
                .group_by("state", "district", "option")
                .agg(pl.col("votes").sum())
            )

            # A district whose file rows are the actas of an extraordinary election, and only such a district, is one
            # the configuration names.
            extraordinary: dict[tuple[str, int], dict] = config.get("extraordinary", {})
            marked = located.group_by("state", "district").agg(
                every=pl.col("extraordinary").all(), some=pl.col("extraordinary").any()
            )
            for state, district, every, some in marked.iter_rows():
                listed = (f"{int(state):02d}", int(district)) in extraordinary
                if every != listed or some != listed:
                    raise ValueError(f"{year} {state}-{district}: extraordinary actas do not match the configuration")

            winners: dict[str, int] = {}
            rerun_winners: dict[str, int] = {}
            rerun_alliances: dict[date, dict[str, UUID]] = {}
            decided = 0
            plan = plan_areas(day)

            def district_area(ss: str, dd: int, plan: dict[str, UUID] = plan, year: int = year) -> UUID | None:
                if year < PLANS_FROM.year:
                    return None
                if f"{ss}{dd:02d}" not in plan:
                    raise ValueError(f"{year}: no federal district area {ss}{dd:02d} with an outline on the day")
                return plan[f"{ss}{dd:02d}"]

            by_district = {key: group for key, group in totals.group_by(["state", "district"])}
            for (state, district), group in sorted(
                by_district.items(), key=lambda item: (int(item[0][0]), int(item[0][1]))
            ):
                ss, dd = f"{int(state):02d}", int(district)
                held, held_election, held_path, held_alliances, tally = day, election, path, alliances, winners
                tag = ("", "")
                rerun = extraordinary.get((ss, dd))
                if rerun is not None:
                    # The annulled June contest keeps its place without results; the extraordinary election holds
                    # the file's count.
                    june = district_contest(
                        f"{day}:dip:mr:{ss}{dd:02d}", election, path, year, ss, dd, district_area(ss, dd)
                    )
                    cs.add("note", entity_id=june, kind="methodology", body_md=rerun["annulled"])
                    cs.add("citation", entity_id=june, source_id=acuerdo, locator=locator(rerun["page"]))
                    held = rerun["day"]
                    held_election, held_path = rerun_election(rerun, election, year, ss, dd)
                    held_alliances = rerun_alliances.setdefault(held, {})
                    tally = rerun_winners
                    tag = (" (extraordinary election)", " (elección extraordinaria)")
                    summary = count_summary(raw.cached(RAW, rerun["count"]).path)
                    actas = located.filter((pl.col("state") == state) & (pl.col("district") == district))
                    printed = (int(str(summary["totVot"]).replace(",", "")), int(str(summary["totActMR"])))
                    if (actas["total"].sum(), actas.height) != printed:
                        raise ValueError(
                            f"{year} {ss}-{dd}: {actas['total'].sum()} votes in {actas.height} actas, {printed}"
                        )
                contest_key = f"{held}:dip:mr:{ss}{dd:02d}"
                area = district_area(ss, dd)
                contest = district_contest(contest_key, held_election, held_path, year, ss, dd, area, tag)
                totals_by = {r["option"]: r["votes"] for r in group.iter_rows(named=True) if r["votes"] > 0}
                standing = {tuple(sorted(g)): sum(totals_by[c] for c in g) for g in groups_of(columns, list(totals_by))}
                # Equal totals below first place take the order of their column names, so a rebuild orders them alike.
                ordered = sorted(standing.items(), key=lambda item: (-item[1], item[0]))
                if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
                    raise ValueError(f"{year} {states[ss]} {dd}: tie for first place")
                district_unit = cs.unit("mx.distrito_federal", f"{year}:{ss}-{dd:02d}")
                # The district's unit is its area where the archive holds the plan's outline, else within its state.
                unit_rows[district_unit] = (
                    "mx.distrito_federal",
                    f"{year}:{ss}-{dd:02d}",
                    state_units[ss],
                    "within" if area is None else "equals",
                    area or cs.area("mx.entidad", ss),
                    "aggregate",
                )
                district_set = make_set(
                    contest, f"mr:{ss}{dd:02d}:district", True, ine_source, 1, MR_METHOD, held, held_election
                )
                left_out = [f" {text}" for key, text in EXCLUDED.items() if key[:4] == (year, "MR", state, district)]
                held_note = [] if rerun is None else [rerun["casilla_note"]]
                casilla_note = "".join([CASILLA_NOTE, config.get("casilla_note", ""), *left_out, *held_note])
                casilla_set = make_set(
                    contest, f"mr:{ss}{dd:02d}:casilla", False, ine_source, 1, casilla_note, held, held_election
                )
                options: dict[str, str] = {}
                for position, (lines, _) in enumerate(ordered, 1):
                    members = sorted({m for c in lines for m in columns[c]} - {INDEPENDENT})
                    head = cs.key("mx.option", f"{contest_key}:{'+'.join(lines)}")
                    cs.add(
                        "contest_option",
                        id=head,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party(members[0]) if len(members) == 1 else None,
                        alliance_id=alliance_of(members, held, held_election, held_alliances)
                        if len(members) > 1
                        else None,
                        ballot_position=position,
                        ballot_label="–".join(members) if members else "Candidatura independiente",
                    )
                    if position == 1:
                        cs.add("outcome", result_set_id=district_set, option_id=head, kind="elected", seats=1)
                        label = "–".join(members) if members else INDEPENDENT
                        tally[label] = tally.get(label, 0) + 1
                    for column in lines:
                        if len(lines) == 1:
                            options[column] = str(head)
                            continue
                        line = cs.key("mx.option", f"{contest_key}:{'+'.join(lines)}:{column}")
                        mine = [m for m in columns[column] if m != INDEPENDENT]
                        # A line that combines parties of the coalition belongs to the coalition's alliance.
                        cs.add(
                            "contest_option",
                            id=line,
                            contest_id=contest,
                            kind="candidate",
                            parent_option_id=head,
                            party_id=party(mine[0]) if len(mine) == 1 else None,
                            alliance_id=alliance_of(members, held, held_election, held_alliances)
                            if len(mine) > 1
                            else None,
                            ballot_label="–".join(mine),
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
                mine_votes = joined.filter((pl.col("state") == state) & (pl.col("district") == district))
                mine_units = located.filter((pl.col("state") == state) & (pl.col("district") == district))
                frames.append(
                    district_frame(
                        mine_votes,
                        mine_units,
                        options,
                        str(write_in),
                        str(casilla_set),
                        str(district_unit),
                        str(district_set),
                    )
                )
                decided += 1
            # Districts the count file lacks keep their contest, with no result set, so coverage can name the gap.
            for (ss, dd), text in config.get("missing", {}).items():
                if (str(int(ss)), str(dd)) in by_district:
                    raise ValueError(f"{year} {states[ss]} {dd}: listed as missing but the file has it")
                contest = district_contest(
                    f"{day}:dip:mr:{ss}{dd:02d}", election, path, year, ss, dd, district_area(ss, dd)
                )
                cs.add("note", entity_id=contest, kind="methodology", body_md=text)
                print(year, "contest without results", states[ss], dd, contest)

            list_system = system_of(config.get("rp_system", "deputies-rp"))
            national = cs.key("mx.contest", f"{day}:dip:rp")
            cs.add(
                "contest",
                id=national,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=cs.area("mx.nation", "MX"),
                magnitude=200,
                electoral_system_id=list_system,
                default_result_kind="counted",
            )
            name(national, f"{year} Chamber of Deputies, proportional representation lists")
            name(national, f"Diputaciones federales {year}, representación proporcional", "es", "native")
            cs.add("slug", path=f"{path}/proportional", entity_id=national)
            declared = config["votes"]
            unregistered, null, total = declared.get("unregistered"), declared["null"], declared["total"]
            listed = {
                abbrev: count for abbrev, count in declared.items() if abbrev not in ("unregistered", "null", "total")
            }
            if sum(listed.values()) + (unregistered or 0) + null != total:
                raise ValueError(f"{year}: the declared figures do not add to {total}")
            rp_options: dict[str, UUID] = {}
            for position, abbrev in enumerate(sorted(listed, key=lambda a: -listed[a]), 1):
                rp_options[abbrev] = cs.key("mx.option", f"{day}:dip:rp:{abbrev}")
                cs.add(
                    "contest_option",
                    id=rp_options[abbrev],
                    contest_id=national,
                    kind="list",
                    party_id=party(abbrev) if abbrev != INDEPENDENT else None,
                    ballot_position=position,
                    ballot_label=abbrev if abbrev != INDEPENDENT else "Candidaturas independientes",
                )
            national_set = make_set(
                national,
                "rp:national",
                True,
                acuerdo,
                1,
                config.get("national_method") or RP_NATIONAL_METHOD.format(page=pages["votes"]),
            )
            cs.add("citation", entity_id=national_set, source_id=acuerdo, locator=locator(pages["votes"]))
            unit = str(nation_unit)
            for abbrev, count in listed.items():
                rows.append((str(national_set), unit, str(rp_options[abbrev]), VOTES, count, 0))
            rp_write_in: UUID | None = None
            if unregistered is not None:
                rp_write_in = cs.key("mx.option", f"{day}:dip:rp:unregistered")
                cs.add(
                    "contest_option",
                    id=rp_write_in,
                    contest_id=national,
                    kind="write_in",
                    ballot_label="Candidatos no registrados",
                )
                rows.append((str(national_set), unit, str(rp_write_in), VOTES, unregistered, 0))
            for measure, value in ((BALLOTS, total), (VALID, total - null), (INVALID, null)):
                rows.append((str(national_set), unit, None, measure, value, 0))
            if "RP" in kinds(year):
                rp_set = make_set(national, "rp:casilla", False, ine_source, 1, RP_CASILLA_NOTE)
                frames.append(
                    list_casillas(year, columns, by_state, rp_options, rp_write_in, str(rp_set), listed, unit_rows)
                )
            seat_total = 0
            for number in range(1, 6):
                contest = cs.key("mx.contest", f"{day}:dip:rp:{number}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    magnitude=40,
                    electoral_system_id=list_system,
                    allocation_parent_id=national,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Chamber of Deputies, proportional representation, circunscripción {number}")
                name(contest, f"Diputaciones federales {year}, circunscripción {number}", "es", "native")
                cs.add("slug", path=f"{path}/proportional-{number}", entity_id=contest)
                members = config.get("circunscripciones", CIRCUNSCRIPCIONES)[number]
                cs.add("note", entity_id=contest, kind="methodology", body_md=f"States: {members}.")
                seat_set = make_set(
                    contest,
                    f"rp:{number}",
                    True,
                    acuerdo,
                    1,
                    config.get("seats_method") or RP_SEATS_METHOD.format(page=pages["seats"]),
                )
                cs.add("citation", entity_id=seat_set, source_id=acuerdo, locator=locator(pages["seats"]))
                circ_options: dict[str, UUID] = {}
                for position, abbrev in enumerate(listed, 1):
                    circ_options[abbrev] = cs.key("mx.option", f"{day}:dip:rp:{number}:{abbrev}")
                    cs.add(
                        "contest_option",
                        id=circ_options[abbrev],
                        contest_id=contest,
                        kind="list",
                        party_id=party(abbrev) if abbrev != INDEPENDENT else None,
                        ballot_position=position,
                        ballot_label=abbrev,
                    )
                won = 0
                for abbrev, per in config["seats"].items():
                    if per[number - 1]:
                        won += per[number - 1]
                        cs.add(
                            "outcome",
                            result_set_id=seat_set,
                            option_id=circ_options[abbrev],
                            kind="won",
                            seats=per[number - 1],
                        )
                if won != 40:
                    raise ValueError(f"{year}: circunscripción {number} seats add to {won}, not 40")
                seat_total += won
            print(
                year, "district winners", dict(sorted(winners.items(), key=lambda item: -item[1])), decided, seat_total
            )
            if rerun_winners:
                print(year, "extraordinary winners", rerun_winners)

        minted = [
            {
                "id": str(unit_id),
                "polity_id": str(mexico),
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
        national_values = pl.DataFrame(
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
            pl.concat([national_values, *(frame.select(national_values.columns) for frame in frames)]).with_columns(
                count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
            ),
        )
        return cs.write()
