import csv
import io
import json
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.mx import ine
from ea.store import DATA, connect

SOURCE = "mx.consulta_popular"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

SITE = "https://computos.cp2021.ine.mx/"
ARCHIVE = "https://web.archive.org/web/{stamp}id_/" + SITE
ZIP = ARCHIVE.format(stamp="20220413225816") + "20210802-2130_INE-CONSULTA-POPULAR-2021.zip"
SUMMARY = (
    ARCHIVE.format(stamp="20211102092805") + "assets/JSON/CONSULTA_POPULAR/NACIONAL/Consulta_Popular_NACIONAL.json"
)
INNER = "20210802-2130_COMPUTOS-INE-CP2021.csv"
DAY = date(2021, 8, 1)
TITLE = "Cómputos de la Consulta Popular 2021, por acta de mesa receptora"
LICENSE = "Public information INE publishes for download (Ley General de Transparencia, art. 70); no license stated"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
YES, NO, NULL = "OPINION_SI", "OPINION_NO", "NULOS"
ANSWERS = (
    (YES, "yes", "Sí", "Yes"),
    (NO, "no", "No", "No"),
)
SYSTEM = (
    "A single question put to every voter on the national roll, answered yes or no, on clarifying the political "
    "decisions of past years by political actors. The result binds only when at least 40 percent of the roll "
    "takes part."
)
NATIONAL = (
    "The national count as INE's Cómputos de la Consulta Popular 2021 system published it with all 57,077 actas "
    "counted (cut of 21:30 on 2 August 2021). Valid votes are the opinions for yes and no; null opinions are "
    "invalid. The registered figure is the nominal list of the polling stations the district councils approved."
)
MESA = (
    "Polling-station (mesa receptora de la consulta popular) results from INE's acta-level count file at 21:30 on "
    "2 August 2021: {actas:,} actas, {total:,} opinions, matching the national count. {recounted:,} actas are "
    "recounts and {missing} mesas have no opinions counted (not installed, or package not received or without "
    "ballots). A mesa sits in the section that hosts its territorial unit, as INE's section map draws it."
)
OUTCOME = (
    "Yes won {yes:,} opinions to {no:,}. Turnout was {turnout:.2f} percent of the nominal list, below the 40 "
    "percent the Constitution requires for the result to bind, so the consultation was not binding."
)


def fetch() -> None:
    raw.fetch(SOURCE, ZIP, suffix=".zip")
    raw.fetch(SOURCE, SUMMARY, suffix=".json")


def rows() -> tuple[list[dict[str, str]], dict[str, str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, ZIP).path) as archive:
        lines = archive.read(next(n for n in archive.namelist() if n.endswith(INNER))).decode("latin-1").splitlines()
    summary = next(i for i, line in enumerate(lines) if line.startswith("ACTAS_ESPERADAS"))
    printed = dict(zip(lines[summary].split("|"), lines[summary + 1].split("|"), strict=True))
    head = next(i for i, line in enumerate(lines) if line.startswith("CLAVE_MRCP"))
    body = [row for row in csv.DictReader(io.StringIO("\n".join(lines[head:])), delimiter="|") if row["CLAVE_MRCP"]]
    return body, printed


def national() -> dict[str, int]:
    data = json.loads(raw.cached(SOURCE, SUMMARY).path.read_text(encoding="utf-8"))
    answers = {row["idPartido"]: row["total"] for row in data["votacionPartidosConDistribucion"]}
    result = {
        "yes": answers[11],
        "no": answers[12],
        "null": answers[91],
        "registered": data["listaNominalOficial"],
        "total": data["totalVotos"],
        "actas": data["totalActas"],
    }
    if result["yes"] + result["no"] + result["null"] != result["total"]:
        raise ValueError("the national answers do not add to the national total")
    return result


def parse() -> None:
    body, printed = rows()
    units, votes = [], []
    for row in body:
        counts = {column: ine.number(row[column]) for column in (YES, NO, NULL)}
        total = ine.number(row["TOTAL_OPINIONES"])
        if sum(counts.values()) != total:
            raise ValueError(f"{row['CLAVE_MRCP']}: answers add to {sum(counts.values())}, total {total}")
        unit = {
            "state": ine.clean(row["ID_ENTIDAD"]),
            "district": ine.clean(row["ID_DISTRITO_FEDERAL"]),
            "section": ine.clean(row["SECCION_SEDE"]),
            "casilla": ine.clean(row["TIPO_MRCP"]) + ine.clean(row["ID_MRCP"]),
        }
        units.append(
            unit
            | {
                "total": total,
                "registered": ine.number(row["LISTA_NOMINAL_MRCP"]),
                "observations": ine.clean(row["OBSERVACIONES"]),
                "recount": ine.clean(row["OBSERVACIONES"]).startswith("Actas de paquetes de recuento"),
                **counts,
            }
        )
        for column, value in counts.items():
            votes.append(unit | {"option": column, "votes": value})
    frame = pl.DataFrame(units)
    if frame.group_by("state", "district", "section", "casilla").len().filter(pl.col("len") > 1).height:
        raise ValueError("duplicate casillas")
    if frame["total"].sum() != ine.number(printed["TOTAL_OPINIONES"]) or frame.height != ine.number(
        printed["ACTAS_COMPUTADAS"]
    ):
        raise ValueError("the casillas do not add to the totals the file prints")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "casillas.parquet")
    pl.DataFrame(votes).write_parquet(PARSED / "votes.parquet")
    print(frame.height, "casillas", frame.select(pl.col(YES, NO, NULL, "total", "registered").sum()).row(0))


def emit() -> Path:
    casillas = pl.read_parquet(PARSED / "casillas.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    declared = national()
    sums = casillas.select(pl.col(YES, NO, NULL, "total").sum()).row(0)
    if sums != (declared["yes"], declared["no"], declared["null"], declared["total"]):
        raise ValueError(f"casillas add to {sums}, the national count is {declared}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mexico's federal popular consultation of 1 August 2021: the national count and INE's acta "
            "count by polling station",
            polity="MX",
        )
        for scheme in ("mx.entidad", "mx.seccion", "mx.casilla"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.consulta_popular")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        source = cs.source(ine.INE, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=ine.INE,
            title=TITLE,
            url=SITE,
            license=LICENSE,
            license_url=SITE,
            attribution="Instituto Nacional Electoral, cómputos de la Revocación de Mandato 2022",
        )
        cs.artifact(raw.cached(SOURCE, ZIP), source)
        cs.artifact(raw.cached(SOURCE, SUMMARY), source)

        system = cs.key("mx.electoral_system", "consulta-popular")
        cs.add("electoral_system", id=system, polity_id=mexico, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Mexican federal popular consultation")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
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

        election = cs.key("mx.election", f"{DAY}:consulta-popular")
        cs.add("election", id=election, polity_id=mexico, kind="referendum", date_from=DAY, date_to=DAY, status="held")
        name(election, "2021 Mexican federal popular consultation")
        name(election, "Consulta Popular 2021", "es", "native")
        path = f"mx/{DAY}-consulta-popular"
        cs.add("slug", path=path, entity_id=election)
        contest = cs.key("mx.contest", f"{DAY}:consulta-popular")
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            electoral_system_id=system,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="plebiscite")
        name(contest, "Clarification of the political decisions of past years by political actors")
        name(
            contest,
            "Esclarecimiento de las decisiones políticas de años pasados por los actores políticos",
            "es",
            "native",
        )
        cs.add("slug", path=f"{path}/question", entity_id=contest)
        options: dict[str, UUID] = {}
        for position, (column, key, native, english) in enumerate(ANSWERS, 1):
            options[column] = cs.key("mx.option", f"{DAY}:consulta-popular:{key}")
            cs.add(
                "contest_option",
                id=options[column],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=native,
            )
            name(options[column], english, "en", "english")

        def make_set(label: str, default: bool, note: str) -> UUID:
            result_set = cs.key("mx.result_set", f"{DAY}:consulta-popular:{label}")
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
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            return result_set

        national_set = make_set("national", True, NATIONAL)
        turnout = 100 * declared["total"] / declared["registered"]
        for column, option in options.items():
            cs.add(
                "outcome",
                result_set_id=national_set,
                option_id=option,
                kind="won" if column == YES else "lost",
                notes=OUTCOME.format(yes=declared["yes"], no=declared["no"], turnout=turnout)
                if column == YES
                else None,
            )
        unit = str(nation_unit)
        values = [
            (str(national_set), unit, str(options[YES]), VOTES, declared["yes"], 0),
            (str(national_set), unit, str(options[NO]), VOTES, declared["no"], 0),
            (str(national_set), unit, None, REGISTERED, declared["registered"], 0),
            (str(national_set), unit, None, BALLOTS, declared["total"], 0),
            (str(national_set), unit, None, VALID, declared["total"] - declared["null"], 0),
            (str(national_set), unit, None, INVALID, declared["null"], 0),
        ]

        recounted = casillas.filter(pl.col("recount")).height
        missing = casillas.filter(pl.col("total") == 0).height
        mesa_set = make_set(
            "mesa",
            False,
            MESA.format(
                actas=casillas.height, total=int(casillas["total"].sum()), recounted=recounted, missing=missing
            ),
        )
        placed = []
        for row in casillas.iter_rows(named=True):
            state = f"{int(row['state']):02d}"
            district, section = int(row["district"]), int(row["section"])
            letter = row["casilla"][:1]
            number = int(row["casilla"][1:])
            code = f"2021CP:{state}-{district:02d}-{section:04d}-{'B' if letter == 'B' else f'{letter}{number}'}"
            channel = 0
            area = sections.get(f"{state}{section:04d}") or cs.area("mx.entidad", state)
            parent = state_units[state]
            unit_id = cs.unit("mx.casilla", code)
            if unit_id in unit_rows:
                raise ValueError(f"casilla {code} appears twice")
            unit_rows[unit_id] = ("mx.casilla", code, parent, "within", area, "standard")
            placed.append((row["state"], row["district"], row["section"], row["casilla"], str(unit_id), channel))
        units = pl.DataFrame(
            placed,
            schema=[
                ("state", pl.String),
                ("district", pl.String),
                ("section", pl.String),
                ("casilla", pl.String),
                ("unit_id", pl.String),
                ("channel", pl.Int16),
            ],
            orient="row",
        )
        keys = ["state", "district", "section", "casilla"]
        located = casillas.join(units, on=keys)
        joined = votes.join(units, on=keys)
        option_ids = {column: str(option) for column, option in options.items()}
        no_option = pl.lit(None, pl.String)
        parts = [
            located.filter(pl.col("registered") > 0).select(
                "unit_id", "channel", option_id=no_option, measure=pl.lit(REGISTERED), value="registered"
            ),
            located.select("unit_id", "channel", option_id=no_option, measure=pl.lit(BALLOTS), value="total"),
            joined.filter(pl.col("option") == NULL).select(
                "unit_id", "channel", option_id=no_option, measure=pl.lit(INVALID), value="votes"
            ),
            joined.filter(pl.col("option") != NULL)
            .group_by("unit_id", "channel")
            .agg(value=pl.col("votes").sum())
            .select("unit_id", "channel", option_id=no_option, measure=pl.lit(VALID), value="value"),
            joined.filter((pl.col("option") != NULL) & (pl.col("votes") != 0)).select(
                "unit_id",
                "channel",
                option_id=pl.col("option").replace_strict(option_ids),
                measure=pl.lit(VOTES),
                value="votes",
            ),
        ]
        frame = pl.concat(
            [
                part.select(
                    result_set_id=pl.lit(str(mesa_set)),
                    unit_id="unit_id",
                    option_id="option_id",
                    measure=pl.col("measure").cast(pl.Int16),
                    value=pl.col("value").cast(pl.Int64),
                    channel=pl.col("channel").cast(pl.Int16),
                )
                for part in parts
            ]
        )
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
        summary = pl.DataFrame(
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
            pl.concat([summary, frame]).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        print(casillas.height, "casillas", int(casillas["total"].sum()), "votes")
        return cs.write()
