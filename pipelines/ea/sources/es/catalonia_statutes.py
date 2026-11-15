import json
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "es.catalonia_statutes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Generalitat de Catalunya"
TITLE = "Processos electorals - Referèndums (Subdirecció General de Processos Electorals i Democràcia Directa)"
PAGE = "https://analisi.transparenciacatalunya.cat/d/dbhr-spf3"
LICENSE = "Llicència oberta d'ús d'informació – Catalunya (or CC0), per the Generalitat's legal notice"
LICENSE_URL = "https://tramits.gencat.cat/ca/ajuda/avis_legal/"
FILE = (
    "https://analisi.transparenciacatalunya.cat/resource/dbhr-spf3.json?$where=id_eleccio%3D%27{}%27"
    "%20and%20id_nivell_territorial%20in(%27PR%27,%27MU%27,%27CA%27)&$limit=5000"
)
BOE = "Boletín Oficial del Estado"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
FIELDS = ("cens_electoral", "votants", "vots_si", "vots_no", "vots_blancs", "vots_nuls")
COLUMNS = ["statute", "unit", "registered", "voters", "yes", "no", "blank", "null"]
MAJORITY = (
    "The statute carried only if Yes won a majority of the valid votes in each province, under article 151.2 of the "
    "Constitution and Organic Law 2/1980."
)
STATUTES = {
    "1979": (
        "R19791",
        date(1979, 10, 25),
        "1979 Catalan Statute of Autonomy referendum",
        "Referéndum del Proyecto de Estatuto de Autonomía para Cataluña de 1979",
        "Statute of Autonomy for Catalonia",
        "Proyecto de Estatuto de Autonomía para Cataluña",
        "Acuerdo de la Junta Electoral Central por la que se hace público el resumen de la votación en el Referéndum del Proyecto de Estatuto de Autonomía para Cataluña (BOE 269, 9 November 1979)",
        "https://www.boe.es/boe/dias/1979/11/09/pdfs/A26028-26028.pdf",
        "Voters in Barcelona, Girona, Lleida, and Tarragona approved or rejected the draft Statute of Autonomy (the "
        "Statute of Sau) put to them by Real Decreto-ley 14/1979. " + MAJORITY,
    ),
    "2006": (
        "R20061",
        date(2006, 6, 18),
        "2006 Catalan Statute of Autonomy referendum",
        "Referéndum sobre la reforma del Estatuto de Autonomía de Cataluña de 2006",
        "Reform of the Statute of Autonomy of Catalonia",
        "Reforma del Estatuto de Autonomía de Cataluña",
        "Resolución de 3 de julio de 2006, de la Junta Electoral Central, de declaración oficial de los resultados del referéndum sobre reforma del Estatuto de Autonomía de Cataluña (BOE 158, 4 July 2006)",
        "https://www.boe.es/boe/dias/2006/07/04/pdfs/A25193-25194.pdf",
        (
            "Voters in Catalonia ratified or rejected the reform of the Statute of Autonomy passed by the Cortes "
            "Generales, put to them by Decreto 170/2006 of the Generalitat de Catalunya. The reform carried with a "
            "majority of the valid votes."
        ),
    ),
}
PROVINCE_METHOD = (
    "Registered voters, voters, Yes and No votes, blank ballots, and null ballots by province from the Generalitat de "
    "Catalunya's open-data file of referendum results, which carries the Junta Electoral Central's declaration. The "
    "provinces add up to the Catalonia row of the same file. Valid votes here are Yes and No together; blank ballots "
    "are stored apart."
)
MUNICIPAL_METHOD = (
    "Registered voters, voters, Yes and No votes, blank ballots, and null ballots by municipality from the "
    "Generalitat de Catalunya's open-data file of referendum results."
)
SHORT = (
    " The {count} municipalities in the file add up to {yes:,} Yes and {no:,} No votes, {gap_yes:,} Yes and "
    "{gap_no:,} No short of the province figures; the figures are stored as the file prints them."
)
UNEVEN = (
    " In {provinces} the printed voters differ from the sum of Yes, No, blank, and null ballots ({gaps}); the "
    "figures are stored as printed."
)


def fetch() -> None:
    for spec in STATUTES.values():
        raw.fetch(SOURCE, FILE.format(spec[0]), suffix=".json")
        raw.fetch(SOURCE, spec[7], suffix=".pdf")


def parse() -> None:
    provinces, towns = [], []
    for year, spec in STATUTES.items():
        rows = json.loads(raw.cached(SOURCE, FILE.format(spec[0])).path.read_bytes())
        for row in rows:
            figures = tuple(int(row[field]) for field in FIELDS)
            match row["id_nivell_territorial"]:
                case "PR":
                    provinces.append((year, row["territori_codi"].zfill(2), *figures))
                case "MU":
                    towns.append((year, row["territori_codi"], *figures))
        total = next(row for row in rows if row["id_nivell_territorial"] == "CA")
        sums = tuple(sum(int(row[i]) for row in provinces if row[0] == year) for i in range(2, 8))
        if sums != tuple(int(total[field]) for field in FIELDS):
            raise ValueError(f"{year}: the provinces do not add up to the Catalonia row")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(provinces, schema=COLUMNS, orient="row").write_parquet(PARSED / "provinces.parquet")
    pl.DataFrame(towns, schema=COLUMNS, orient="row").write_parquet(PARSED / "municipalities.parquet")


def emit() -> Path:
    provinces = pl.read_parquet(PARSED / "provinces.parquet")
    towns = pl.read_parquet(PARSED / "municipalities.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "The Catalan statute of autonomy referendums of 25 October 1979 and 18 June 2006 by province and by "
                "municipality, from the Generalitat de Catalunya's open-data file of referendum results"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain = cs.polity("ES")
        nation = cs.unit("es.nation", "ES")
        system = conn.execute(
            "select c.electoral_system_id from contest c join slug s on s.entity_id = c.id"
            " where s.path like 'es/1979-10-25-referendum-basque/%'"
        ).fetchone()
        if system is None:
            raise ValueError("publish es.referendums before the Catalan statute referendums")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution="Font: Generalitat de Catalunya, Subdirecció General de Processos Electorals i Democràcia Directa.",
        )
        municipalities = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        names = dict(
            conn.execute(
                "select c.code, n.text from area a join area_code c on c.area_id = a.id"
                " join name n on n.entity_id = a.id and n.is_preferred"
                " where a.scheme_code = 'es.provincia' order by n.lang = 'en'"
            ).fetchall()
        )
        unit_rows = {}
        province_units = {}
        values = []
        for year, (
            election_id,
            day,
            english_title,
            native_title,
            english,
            native,
            gazette,
            gazette_url,
            about,
        ) in STATUTES.items():
            key = f"{day}:referendum:catalonia"
            cs.artifact(raw.cached(SOURCE, FILE.format(election_id)), source)
            boe = cs.source(BOE, gazette)
            cs.add(
                "source",
                id=boe,
                kind="official_body",
                publisher=BOE,
                title=gazette,
                url=gazette_url,
                license="Public record: official gazette, free to reuse",
                license_url="https://www.boe.es/informacion/aviso_legal/index.php",
                attribution="Fuente: Boletín Oficial del Estado.",
            )
            cs.artifact(raw.cached(SOURCE, gazette_url), boe)
            election = cs.key("es.election", key)
            cs.add(
                "election", id=election, polity_id=spain, kind="referendum", date_from=day, date_to=day, status="held"
            )
            name(election, english_title)
            name(election, native_title, "es", "native")
            path = f"es/{day}-referendum-catalonia"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("es.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system[0],
                district_area_id=cs.area("es.comunidad", "09"),
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            name(contest, english)
            name(contest, native, "es", "native")
            cs.add("slug", path=f"{path}/{slug(english)}", entity_id=contest)
            options = {}
            for position, (answer, label) in enumerate((("yes", "Sí"), ("no", "No")), 1):
                options[answer] = cs.key("es.option", f"{key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
            rows = provinces.filter(pl.col("statute") == year)
            local = towns.filter(pl.col("statute") == year)
            province_note = PROVINCE_METHOD
            uneven = rows.filter(pl.col("yes") + pl.col("no") + pl.col("blank") + pl.col("null") != pl.col("voters"))
            if uneven.height:
                gaps = [
                    f"{names[row['unit']]} {row['voters'] - row['yes'] - row['no'] - row['blank'] - row['null']:+,}"
                    for row in uneven.iter_rows(named=True)
                ]
                province_note += UNEVEN.format(
                    provinces=", ".join(names[code] for code in uneven["unit"]), gaps=", ".join(gaps)
                )
            municipal_note = MUNICIPAL_METHOD
            gap_yes = int(rows["yes"].sum()) - int(local["yes"].sum())
            gap_no = int(rows["no"].sum()) - int(local["no"].sum())
            if gap_yes or gap_no:
                municipal_note += SHORT.format(
                    count=local.height,
                    yes=int(local["yes"].sum()),
                    no=int(local["no"].sum()),
                    gap_yes=gap_yes,
                    gap_no=gap_no,
                )
            sets = {
                f"{key}:provinces": (rows, True, province_note),
                f"{key}:municipalities": (local, False, municipal_note),
            }
            for set_key, (rows_in, default, note) in sets.items():
                result_set = cs.key(f"{SOURCE}.result_set", set_key)
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
                cs.add("note", entity_id=result_set, kind="context", body_md=about)
                for answer, option in options.items():
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="won" if answer == "yes" else "lost",
                    )
                for row in rows_in.iter_rows(named=True):
                    code = row["unit"]
                    if default:
                        unit = province_units.get(code)
                        if unit is None:
                            unit = province_units[code] = cs.unit("es.provincia", code)
                            if unit in cs.minted:
                                unit_rows[unit] = (
                                    unit,
                                    "es.provincia",
                                    code,
                                    nation,
                                    "equals",
                                    cs.area("es.provincia", code),
                                    "standard",
                                )
                    else:
                        unit = cs.unit("es.municipio", code)
                        if unit in cs.minted and unit not in unit_rows:
                            area = municipalities.get(code)
                            unit_rows[unit] = (
                                unit,
                                "es.municipio",
                                code,
                                cs.unit("es.provincia", code[:2]),
                                "unlocated" if area is None else "equals",
                                area,
                                "standard",
                            )
                    for option, measure, value in (
                        (options["yes"], VOTES, row["yes"]),
                        (options["no"], VOTES, row["no"]),
                        (None, REGISTERED, row["registered"]),
                        (None, BALLOTS, row["voters"]),
                        (None, VALID, row["yes"] + row["no"]),
                        (None, INVALID, row["null"]),
                        (None, BLANK, row["blank"]),
                    ):
                        values.append(
                            (str(result_set), str(unit), None if option is None else str(option), measure, value, 0)
                        )
        if unit_rows:
            cs.frame(
                "reporting_unit",
                pl.DataFrame(
                    [
                        {
                            "id": str(unit),
                            "polity_id": str(spain),
                            "scheme_code": scheme,
                            "code": code,
                            "parent_id": None if parent is None else str(parent),
                            "area_relation": relation,
                            "area_id": None if area is None else str(area),
                            "kind": kind,
                        }
                        for unit, scheme, code, parent, relation, area, kind in unit_rows.values()
                    ],
                    schema_overrides={"parent_id": pl.String, "area_id": pl.String},
                ),
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
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
            ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
