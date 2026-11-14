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
from ea.sources.us.pr_legislature import PARTIES
from ea.store import connect

SOURCE = "us.pr_cee_2016_2020"
VERSION = "1"
PUBLISHER = "Puerto Rico State Elections Commission"
URL16 = "https://electionspuertorico.org/datos/2016/Unidades.xls"
URL20 = "https://electionspuertorico.org/datos/2020/escrutinio/Colegios.zip"
BALLOTS = "https://www.ceepur.org/Elecciones/Papeletas.html"
LISTS = {year: f"https://electionspuertorico.org/datos/{year}/Precintos.xls" for year in (2016, 2020)}
BODIES = {"house": ("RPA", "RPD"), "senate": ("SNA", "SND")}
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
        *((url, ".xls", "application/vnd.ms-excel") for url in LISTS.values()),
    ):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, url], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url, body, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media_type)


def sheet(path: Path, name: str | None = None) -> list[dict]:
    frame = pl.read_excel(
        path,
        **({"sheet_name": name} if name else {}),
        has_header=False,
        read_options={"header_row": None},
    )
    columns = [str(column) for column in frame.row(0)]
    return [
        {column: value for column, value in zip(columns, row, strict=False) if column != "None"}
        for row in frame.rows()[1:]
        if row[0] is not None and str(row[0]).isdigit()
    ]


def legislative(conn, year: int, rows: list[dict], lists: dict[int, dict[int, dict]]) -> dict[str, dict]:
    day = DAYS[year]
    listing = lists[year]
    plans = {}
    for body, (large, district) in BODIES.items():
        groups: dict[frozenset, set[int]] = {}
        grouping = lists[2020] if body == "house" else listing
        for prekey, row in grouping.items():
            groups.setdefault(frozenset(str(v) for c, v in row.items() if c.startswith(district + "_")), set()).add(
                prekey
            )
        districts = [
            key
            for (key,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ %s",
                [f"^{day}:PR:{body}:[0-9]+:g$"],
            ).fetchall()
        ]
        for prefix, ps, key in [(large, set(listing), f"{day}:PR:{body}:at-large:g")] + [
            (district, ps, None) for ps in groups.values()
        ]:
            tallies: dict[tuple[str, str], int] = {}
            slots: dict[tuple[int, str], tuple[str, str]] = {}
            for row in rows:
                prekey = int(row["PREKEY"])
                if prekey not in ps:
                    continue
                for column in row:
                    if not column.startswith(prefix + "_") or column in (f"{prefix}_MVT", f"{prefix}_NVT"):
                        continue
                    label = listing[prekey].get(column) if column[-1].isdigit() else None
                    slot = (column.split("_")[1], str(label) if label else column)
                    slots[(prekey, column)] = slot
                    tallies[slot] = tallies.get(slot, 0) + int(row[column] or 0)
            counted = sorted((v for v in tallies.values() if v), reverse=True)
            found = []
            for each in [key] if key else districts:
                options = conn.execute(
                    """select o.id, o.kind, o.party_id, sum(v.value) from external_id x
                       join contest_option o on o.contest_id = x.entity_id
                       join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                       join result_value v on v.result_set_k = rs.k and v.option_k = o.k
                       where x.scheme = 'us.contest' and x.value = %s group by 1, 2, 3""",
                    [each],
                ).fetchall()
                if sorted((v for *_, v in options if v), reverse=True) == counted:
                    found.append((each, options))
            if len(found) != 1:
                raise ValueError(f"{year} {prefix} {sorted(ps)}: {len(found)} contests match")
            ((key, options),) = found
            parties = {
                code: conn.execute(
                    "select entity_id from external_id where scheme = 'us.party' and value = %s", [party]
                ).fetchone()[0]
                for code, party in PARTIES.items()
                if party
            }
            taken: dict[tuple[str, str], object] = {}
            for slot, votes in sorted(tallies.items(), key=lambda item: -item[1]):
                party = slot[0]
                fits = [
                    option
                    for option, kind, party_id, value in options
                    if value == votes
                    and option not in taken.values()
                    and (
                        kind == "write_in"
                        if party == "OTR"
                        else kind != "write_in"
                        and (party_id is None if party == "IND" else party_id == parties.get(party))
                    )
                ]
                if len(fits) == 1:
                    taken[slot] = fits[0]
                elif votes:
                    raise ValueError(f"{key}: {len(fits)} options for {slot} ({votes})")
            plans[key] = {where: taken[slot] for where, slot in slots.items() if slot in taken}
    return plans


def emit() -> Path:
    frames = {2016: sheet(raw.cached(SOURCE, URL16).path)}
    with zipfile.ZipFile(raw.cached(SOURCE, URL20).path) as archive:
        frames[2020] = pl.read_csv(io.BytesIO(archive.read("estatal.csv"))).to_dicts()
        ballots = {2016: frames[2016], 2020: pl.read_csv(io.BytesIO(archive.read("legislativa.csv"))).to_dicts()}
    lists = {
        year: {int(row["PREKEY"]): row for row in sheet(raw.cached(SOURCE, url).path, "Data Papeleta Legislativa")}
        for year, url in LISTS.items()
    }
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
            summary="Puerto Rico's 2016 governor, Resident Commissioner, and Legislative Assembly elections by electoral unit and 2020 by voting center (colegio)",
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
            for url in LISTS.values():
                cs.artifact(raw.cached(SOURCE, url), sources[year])
            for row in rows + (ballots[year] if year == 2020 else []):
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
        plans = []
        for (year, section), columns in CONTESTS.items():
            key = f"{DAYS[year]}:PR:{section}"
            labels = conn.execute(
                """select o.id, translate(lower(o.ballot_label), 'áéíóúñü', 'aeiounu') from contest_option o
                   join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchall()
            options = {}
            for column, needle in columns.items():
                found = [option for option, label in labels if needle in label]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {needle}")
                options[(None, column)] = found[0]
            plans.append((year, key, frames[year], options))
        for year, rows in ballots.items():
            plans += [(year, key, rows, options) for key, options in legislative(conn, year, rows, lists).items()]
        for year, key, rows, options in plans:
            contest, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [key],
            ).fetchone()
            values = []
            for row in rows:
                suffix = f"{int(row['UNIKEY']):02}" + (f":{int(row['COLKEY']):02}" if year == 2020 else "")
                unit = units[f"{year}:PR:{int(row['PREKEY']):03}:{suffix}"]
                for column in row:
                    option = options.get((None, column)) or options.get((int(row["PREKEY"]), column))
                    if option:
                        values.append((unit, option, int(row[column] or 0)))
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
            kept = conn.execute("select completeness from result_set where id = %s", [result_set]).fetchone()
            if kept:
                completeness = kept[0]
            else:
                official = conn.execute(
                    "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
                    [contest],
                ).fetchone()[0]
                completeness = round(float(sum(value for *_, value in values)) / float(official), 4)
            for prior in conn.execute(
                f"select {', '.join(set_columns)} from result_set where contest_id = %s and is_default and id <> %s",
                [contest, result_set],
            ).fetchall():
                cs.add("result_set", **dict(zip(set_columns, prior, strict=True)), is_default=False)
            for option, outcome_kind, seats in conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
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
                completeness=completeness,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Votes by "
                + ("electoral unit" if year == 2016 else "voting center")
                + " from the State Elections Commission's official count. Units sit under the municipality of their precinct, from the precinct numbering the Commission used on the 2020 ballots."
                + (
                    " The order of candidates on the legislative ballot varies by precinct, so each precinct's ballot positions are matched to candidates from the Commission's per-precinct candidate listing, and precincts to districts by their district candidates (2016 House districts from the 2020 listing, under the same district plan). Every candidate's total equals the official district count."
                    if ":g" in key
                    else ""
                ),
            )
            for unit, option, value in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
