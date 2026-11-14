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

SOURCE = "mx.revocacion_mandato"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

SITE = "https://computosrm2022.ine.mx/"
SUMMARY = f"{SITE}assets/JSON/REVOCACION_MANDATO/NACIONAL/Revocacion_Mandato_NACIONAL.json"
ZIP = f"{SITE}20220411_1845_REVOCACION_MANDATO_2022.zip"
INNER = "20220411_1845_COMPUTOS_RM2022.csv"
DAY = date(2022, 4, 10)
TITLE = "Cómputos de la Revocación de Mandato 2022, por acta de casilla"
LICENSE = "Public information INE publishes for download (Ley General de Transparencia, art. 70); no license stated"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
ABROAD = 4
REVOKE = "QUE_SE_LE_REVOQUE_EL_MANDATO_POR_PÉRDIDA_DE_LA_CONFIANZA"
REMAIN = "QUE_SIGA_EN_LA_PRESIDENCIA_DE_LA_REPÚBLICA"
NULL = "NULOS"
ANSWERS = (
    (REVOKE, "revoke", "Que se le revoque el mandato por pérdida de la confianza", "Revoke the mandate"),
    (REMAIN, "remain", "Que siga en la Presidencia de la República", "Remain in office"),
)
SYSTEM = (
    "A single question put to every voter on the national roll: whether the President's mandate is revoked for loss "
    "of confidence or the President remains in office until the end of the term. The result binds only when at "
    "least 40 percent of the roll votes and a majority votes for revocation."
)
NATIONAL = (
    "The national count as INE's Cómputos de la Revocación de Mandato 2022 system published it at 18:45 on 11 April "
    "2022, with all 57,449 actas counted. Valid votes are the votes for the two answers; null votes are invalid. "
    "The registered figure is the official nominal list, which includes casillas the councils did not approve."
)
CASILLA = (
    "Polling-station (casilla) results from INE's acta-level count file at 18:45 on 11 April 2022: {actas:,} actas, "
    "{total:,} votes, matching the national count. {recounted:,} actas are recounts, {missing} casillas have no "
    "ballots counted (package not received or without ballots), and one acta covers the vote abroad. Casillas sit "
    "within their electoral section as INE's section map draws it."
)
OUTCOME = (
    "Remain in office won {remain:,} votes to {revoke:,}. Turnout was {turnout:.2f} percent of the nominal list, "
    "below the 40 percent the Constitution requires for the result to bind, so the vote was not binding."
)


def fetch() -> None:
    raw.fetch(SOURCE, ZIP, suffix=".zip")
    raw.fetch(SOURCE, SUMMARY, suffix=".json")


def rows() -> tuple[list[dict[str, str]], dict[str, str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, ZIP).path) as archive:
        lines = archive.read(next(n for n in archive.namelist() if n.endswith(INNER))).decode("latin-1").splitlines()
    summary = next(i for i, line in enumerate(lines) if line.startswith("ACTAS_ESPERADAS"))
    printed = dict(zip(lines[summary].split(","), lines[summary + 1].split(","), strict=True))
    head = next(i for i, line in enumerate(lines) if line.startswith("CLAVE_CASILLA"))
    body = [row for row in csv.DictReader(io.StringIO("\n".join(lines[head:]))) if row["CLAVE_CASILLA"]]
    return body, printed


def national() -> dict[str, int]:
    data = json.loads(raw.cached(SOURCE, SUMMARY).path.read_text(encoding="utf-8"))
    answers = {row["idPartido"]: row["total"] for row in data["votacionPartidosConDistribucion"]}
    result = {
        "revoke": answers[110],
        "remain": answers[111],
        "null": answers[91],
        "registered": data["listaNominalOficial"],
        "total": data["totalVotos"],
        "actas": data["totalActas"],
    }
    if result["revoke"] + result["remain"] + result["null"] != result["total"]:
        raise ValueError("the national answers do not add to the national total")
    return result


def parse() -> None:
    body, printed = rows()
    units, votes = [], []
    for row in body:
        counts = {column: ine.number(row[column]) for column in (REVOKE, REMAIN, NULL)}
        total = ine.number(row["TOTAL_VOTOS_CALCULADOS"])
        if sum(counts.values()) != total:
            raise ValueError(f"{row['CLAVE_CASILLA']}: answers add to {sum(counts.values())}, total {total}")
        unit = {
            "state": ine.clean(row["ID_ENTIDAD"]),
            "district": ine.clean(row["ID_DISTRITO_FEDERAL"]),
            "section": ine.clean(row["SECCIÓN"]),
            "casilla": ine.clean(row["TIPO_CASILLA"]) + ine.clean(row["ID_CASILLA"]),
        }
        units.append(
            unit
            | {
                "total": total,
                "registered": ine.number(row["LISTA_NOMINAL"]),
                "observations": ine.clean(row["OBSERVACIONES"]),
                "document": ine.clean(row["TIPO_DOCUMENTO"]),
                **counts,
            }
        )
        for column, value in counts.items():
            votes.append(unit | {"option": column, "votes": value})
    frame = pl.DataFrame(units)
    if frame.group_by("state", "district", "section", "casilla").len().filter(pl.col("len") > 1).height:
        raise ValueError("duplicate casillas")
    if frame["total"].sum() != ine.number(printed["TOTAL_VOTOS"]) or frame.height != int(printed["ACTAS_COMPUTADAS"]):
        raise ValueError("the casillas do not add to the totals the file prints")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "casillas.parquet")
    pl.DataFrame(votes).write_parquet(PARSED / "votes.parquet")
    print(frame.height, "casillas", frame.select(pl.col(REVOKE, REMAIN, NULL, "total", "registered").sum()).row(0))


def emit() -> Path:
    casillas = pl.read_parquet(PARSED / "casillas.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    declared = national()
    sums = casillas.select(pl.col(REVOKE, REMAIN, NULL, "total").sum()).row(0)
    if sums != (declared["revoke"], declared["remain"], declared["null"], declared["total"]):
        raise ValueError(f"casillas add to {sums}, the national count is {declared}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mexico's presidential recall referendum of 10 April 2022: the national count and INE's acta "
            "count by polling station",
            polity="MX",
        )
        for scheme in ("mx.entidad", "mx.seccion", "mx.casilla"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.revocacion_mandato")

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

        system = cs.key("mx.electoral_system", "revocacion-mandato")
        cs.add("electoral_system", id=system, polity_id=mexico, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Mexican presidential recall referendum")
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

        election = cs.key("mx.election", f"{DAY}:revocacion-mandato")
        cs.add("election", id=election, polity_id=mexico, kind="referendum", date_from=DAY, date_to=DAY, status="held")
        name(election, "2022 Mexican presidential recall referendum")
        name(election, "Revocación de Mandato 2022", "es", "native")
        path = f"mx/{DAY}-revocacion-mandato"
        cs.add("slug", path=path, entity_id=election)
        contest = cs.key("mx.contest", f"{DAY}:revocacion-mandato")
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            electoral_system_id=system,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="recall_question")
        name(contest, "Recall of the President of Mexico, Andrés Manuel López Obrador")
        name(contest, "Revocación de mandato del Presidente de la República", "es", "native")
        cs.add("slug", path=f"{path}/question", entity_id=contest)
        options: dict[str, UUID] = {}
        for position, (column, key, native, english) in enumerate(ANSWERS, 1):
            options[column] = cs.key("mx.option", f"{DAY}:revocacion-mandato:{key}")
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
            result_set = cs.key("mx.result_set", f"{DAY}:revocacion-mandato:{label}")
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
                kind="won" if column == REMAIN else "lost",
                notes=OUTCOME.format(remain=declared["remain"], revoke=declared["revoke"], turnout=turnout)
                if column == REMAIN
                else None,
            )
        unit = str(nation_unit)
        values = [
            (str(national_set), unit, str(options[REVOKE]), VOTES, declared["revoke"], 0),
            (str(national_set), unit, str(options[REMAIN]), VOTES, declared["remain"], 0),
            (str(national_set), unit, None, REGISTERED, declared["registered"], 0),
            (str(national_set), unit, None, BALLOTS, declared["total"], 0),
            (str(national_set), unit, None, VALID, declared["total"] - declared["null"], 0),
            (str(national_set), unit, None, INVALID, declared["null"], 0),
        ]

        recounted = casillas.filter(pl.col("document") == "Acta de paquete recontado").height
        missing = casillas.filter(pl.col("total") == 0).height
        casilla_set = make_set(
            "casilla",
            False,
            CASILLA.format(
                actas=casillas.height, total=int(casillas["total"].sum()), recounted=recounted, missing=missing
            ),
        )
        placed = []
        for row in casillas.iter_rows(named=True):
            state = f"{int(row['state']):02d}"
            district, section = int(row["district"]), int(row["section"])
            letter = row["casilla"][:1]
            number = int(row["casilla"][1:])
            code = f"2022RM:{state}-{district:02d}-{section:04d}-{'B' if letter == 'B' else f'{letter}{number}'}"
            channel, kind, area, parent = 0, "standard", None, None
            if state == "00":
                channel, kind, area, parent = ABROAD, "aggregate", cs.area("mx.nation", "MX"), nation_unit
            else:
                area = sections.get(f"{state}{section:04d}") or cs.area("mx.entidad", state)
                parent = state_units[state]
            unit_id = cs.unit("mx.casilla", code)
            if unit_id in unit_rows:
                raise ValueError(f"casilla {code} appears twice")
            unit_rows[unit_id] = ("mx.casilla", code, parent, "within", area, kind)
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
