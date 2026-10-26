import csv
import re
import subprocess
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.pr_cee_colegios"
VERSION = "1"
DAY = date(2024, 11, 5)
URL = "https://electionspuertorico.org/datos/2024/escrutinio/estatal.csv"
PRECINTOS = "https://ww2.ceepur.org/sites/ComisionEE/es-pr/Planificacion/Documents/Relacion_de_Precintos_Unidades_5_oct_2024.pdf"
PUBLISHER = "Puerto Rico State Elections Commission"
TITLE = "2024 General Election official count (escrutinio) by voting center"
CONTESTS = {
    "governor": (
        f"{DAY}:PR:governor",
        {"GOB_PNP": "gonz", "GOB_PPD": "ortiz", "GOB_MVC": "cordova", "GOB_PIP": "dalmau", "GOB_PD": "jimenez"},
    ),
    "commissioner": (
        f"{DAY}:PR:house:0",
        {
            "COM_PNP": "villafa",
            "COM_PPD": "hernandez",
            "COM_MVC": "lassen",
            "COM_PIP": "velazquez",
            "COM_PD": "ramirez",
            "COM_OTR": "scattered",
        },
    ),
}


def fetch() -> None:
    for url, suffix, media_type in ((URL, ".csv", "text/csv"), (PRECINTOS, ".pdf", "application/pdf")):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, body, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media_type)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn").strip()


def emit() -> Path:
    rows = list(csv.DictReader(raw.cached(SOURCE, URL).path.read_text(encoding="utf-8-sig").splitlines()))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico's 2024 governor and Resident Commissioner elections by voting center (colegio)",
            polity="US-PR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://electionspuertorico.org/datos/2024/",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        cs.artifact(raw.cached(SOURCE, PRECINTOS), source)
        listing = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, PRECINTOS).path), "-"], capture_output=True, check=True, text=True
        ).stdout
        names = {}
        for line in listing.splitlines():
            for name, number in re.findall(r"([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñü. ]+?)\s+(?:\d{1,2}\s+)?(\d{3})\s+(?:\d{1,2}\s+)?\d{1,2}\b", line):
                names.setdefault(int(number), fold(name))
        municipios = {
            fold(text): (unit, area)
            for text, unit, area in conn.execute(
                """select n.text, u.id, u.area_id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '72%%'"""
            ).fetchall()
        }
        if len(municipios) != 78 or set(range(1, 115)) - names.keys() or set(names.values()) - municipios.keys():
            raise ValueError("precinct listing does not resolve to the 78 municipios")
        (state,) = conn.execute("select id from polity where iso_code = 'US-PR'").fetchone()
        units = {}
        for row in rows:
            code = f"{DAY.year}:PR:{int(row['PREKEY']):03}:{int(row['UNIKEY']):02}:{int(row['COLKEY']):02}"
            units[code] = cs.unit("us.precinct", code)
            if units[code] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=state,
                    scheme_code="us.precinct",
                    code=code,
                    parent_id=municipios[names[int(row["PREKEY"])]][0],
                    area_relation="within",
                    area_id=municipios[names[int(row["PREKEY"])]][1],
                    kind="standard",
                    valid_from=DAY,
                    valid_to=DAY + timedelta(days=1),
                )
        for name, (key, columns) in CONTESTS.items():
            contest, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [key],
            ).fetchone()
            options = {}
            for column, needle in columns.items():
                found = [
                    option
                    for option, label in conn.execute(
                        "select id, translate(lower(ballot_label), 'áéíóúñ', 'aeioun') from contest_option where contest_id = %s",
                        [contest],
                    ).fetchall()
                    if needle in label
                ]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {needle}")
                options[column] = found[0]
            official = conn.execute(
                """select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k
                   where r.contest_id = %s and r.is_default and v.measure = 1""",
                [contest],
            ).fetchone()[0]
            counted = sum(int(row[column] or 0) for row in rows for column in columns)
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:colegio")
            columns = (
                "id",
                "election_id",
                "contest_id",
                "stage",
                "kind",
                "status",
                "source_id",
                "as_of",
                "completeness",
                "supersedes_id",
            )
            for prior in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default and id <> %s",
                [contest, result_set],
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False)
            for option, kind, seats in conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
                [contest, result_set],
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=round(float(counted) / float(official), 4),
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=f"Votes by voting center from the State Elections Commission's official count. The file's unit and center columns are used as published; columns other than the {len(columns)} listed candidates and write-ins (mixed-vote and blank counts) are not loaded.",
            )
            for row in rows:
                unit = units[f"{DAY.year}:PR:{int(row['PREKEY']):03}:{int(row['UNIKEY']):02}:{int(row['COLKEY']):02}"]
                for column, option in options.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=int(row[column] or 0),
                    )
        return cs.write()
