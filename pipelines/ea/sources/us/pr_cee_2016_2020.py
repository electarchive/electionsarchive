import html
import io
import re
import subprocess
import unicodedata
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.pr_cee_2016_2020"
VERSION = "1"
PUBLISHER = "Puerto Rico State Elections Commission"
URL16 = "https://electionspuertorico.org/datos/2016/Unidades.xls"
URL20 = "https://electionspuertorico.org/datos/2020/escrutinio/Colegios.zip"
BALLOTS = "https://www.ceepur.org/Elecciones/Papeletas.html"
CONTESTS = {
    (2016, "governor"): {
        "GOB_PNP": "rossello",
        "GOB_PPD": "bernier",
        "GOB_PIP": "santiago",
        "GOB_PPT": "bernabe",
        "GOB_IND_1": "cidre",
        "GOB_IND_2": "lugaro",
        "GOB_OTR": "write-ins",
    },
    (2016, "house:0"): {
        "COM_PNP": "gonzalez",
        "COM_PPD": "ferrer",
        "COM_PIP": "rodriguez",
        "COM_PPT": "nogales",
        "COM_OTR": "scattered",
    },
    (2020, "governor"): {
        "GOB_PNP": "pierluisi",
        "GOB_PPD": "delgado",
        "GOB_MVC": "lugaro",
        "GOB_PIP": "dalmau",
        "GOB_PD": "vazquez",
        "GOB_IND": "molina",
    },
    (2020, "house:0"): {
        "COM_PNP": "gonzalez",
        "COM_PPD": "acevedo",
        "COM_MVC": "jordan",
        "COM_PD": "henriquez",
        "COM_PIP": "pinero",
        "COM_OTR": "scattered",
    },
}
DAYS = {2016: date(2016, 11, 8), 2020: date(2020, 11, 3)}


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn").strip()


def fetch() -> None:
    for url, suffix, media_type in (
        (URL16, ".xls", "application/vnd.ms-excel"),
        (URL20, ".zip", "application/zip"),
        (BALLOTS, ".html", "text/html"),
    ):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, body, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media_type)


def emit() -> Path:
    frames = {}
    sheet = pl.read_excel(raw.cached(SOURCE, URL16).path, has_header=False, read_options={"header_row": None})
    head = [str(name) for name in sheet.row(0)]
    frames[2016] = [
        {name: value for name, value in zip(head, row, strict=False) if name != "None"}
        for row in sheet.rows()[1:]
        if row[0] is not None and str(row[0]).isdigit()
    ]
    with zipfile.ZipFile(raw.cached(SOURCE, URL20).path) as archive:
        frames[2020] = pl.read_csv(io.BytesIO(archive.read("estatal.csv"))).to_dicts()
    page = html.unescape(
        re.sub(r"<[^>]+>", " ", raw.cached(SOURCE, BALLOTS).path.read_text(encoding="utf-8", errors="replace"))
    )
    names = {
        int(number): fold(name)
        for number, name in re.findall(r"\b(\d{3})\s+([A-ZÁÉÍÓÚÑa-záéíóúñü. ]+?)\s+(?:Legislativa|\s)", page)
    }
    with connect() as conn:
        municipios = {
            fold(text): (unit, area)
            for text, unit, area in conn.execute(
                """select n.text, u.id, u.area_id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '72%%'"""
            ).fetchall()
        }
        if len(municipios) != 78 or set(range(1, 111)) - names.keys() or set(names.values()) - municipios.keys():
            raise ValueError("precinct listing does not resolve to the 78 municipios")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico's 2016 governor and Resident Commissioner elections by electoral unit and 2020 by voting center (colegio)",
            polity="US-PR",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-PR'").fetchone()
        sources = {}
        units = {}
        for year, rows in frames.items():
            day = DAYS[year]
            title = f"{year} General Election official count (escrutinio) by {'electoral unit' if year == 2016 else 'voting center'}"
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"https://electionspuertorico.org/datos/{year}/",
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, URL16 if year == 2016 else URL20), sources[year])
            cs.artifact(raw.cached(SOURCE, BALLOTS), sources[year])
            for row in rows:
                suffix = f"{int(row['UNIKEY']):02}" + (f":{int(row['COLKEY']):02}" if year == 2020 else "")
                code = f"{year}:PR:{int(row['PREKEY']):03}:{suffix}"
                units[code] = cs.unit("us.precinct", code)
                if units[code] in cs.minted:
                    unit, area = municipios[names[int(row["PREKEY"])]]
                    cs.add(
                        "reporting_unit",
                        id=units[code],
                        polity_id=state,
                        scheme_code="us.precinct",
                        code=code,
                        parent_id=unit,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
        for (year, section), columns in CONTESTS.items():
            day = DAYS[year]
            key = f"{day}:PR:{section}"
            contest, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [key],
            ).fetchone()
            labels = conn.execute(
                "select id, translate(lower(ballot_label), 'áéíóúñü', 'aeiounu') from contest_option where contest_id = %s",
                [contest],
            ).fetchall()
            options = {}
            for column, needle in columns.items():
                found = [option for option, label in labels if needle in label]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {needle}")
                options[column] = found[0]
            official = conn.execute(
                "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
                [contest],
            ).fetchone()[0]
            counted = sum(int(row[column] or 0) for row in frames[year] for column in columns)
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{'unit' if year == 2016 else 'colegio'}")
            set_columns = (
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
                f"select {', '.join(set_columns)} from result_set where contest_id = %s and is_default and id <> %s",
                [contest, result_set],
            ).fetchall():
                cs.add("result_set", **dict(zip(set_columns, prior, strict=True)), is_default=False)
            for option, outcome_kind, seats in conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
                [contest, result_set],
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome_kind, seats=seats)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=round(float(counted) / float(official), 4),
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Votes by "
                + ("electoral unit" if year == 2016 else "voting center")
                + " from the State Elections Commission's official count. Units sit under the municipality of their precinct, from the precinct numbering the Commission used on the 2020 ballots.",
            )
            for row in frames[year]:
                suffix = f"{int(row['UNIKEY']):02}" + (f":{int(row['COLKEY']):02}" if year == 2020 else "")
                unit = units[f"{year}:PR:{int(row['PREKEY']):03}:{suffix}"]
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
