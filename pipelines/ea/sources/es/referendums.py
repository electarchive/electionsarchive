import io
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.es.infoelectoral import APLIEXTR, DOCXL, PAGE
from ea.store import DATA, connect

SOURCE = "es.referendums"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ministerio del Interior"
TITLE = "Resultados electorales: referendos (ficheros de datos)"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
REFERENDUMS = {
    date(1976, 12, 15): (
        "197612",
        "Law for Political Reform",
        "Ley para la Reforma Política",
        "mandatory_referendum",
        "Referéndum sobre la Ley para la Reforma Política",
        (
            "Voters approved or rejected the Law for Political Reform, the last of the Fundamental Laws of the Francoist "
            "state, which opened the way to the 1977 general election. The ministry publishes it by province only."
        ),
    ),
    date(1978, 12, 6): (
        "197812",
        "Spanish Constitution",
        "Constitución Española",
        "mandatory_referendum",
        "Referéndum sobre la Constitución Española",
        "Voters ratified the Constitution passed by the Constituent Cortes. The ministry publishes it by province.",
    ),
    date(1986, 3, 12): (
        "198603",
        "Spain's membership of the North Atlantic Treaty Organization",
        "Permanencia de España en la Alianza Atlántica",
        "optional_referendum",
        "Referéndum sobre la permanencia de España en la OTAN",
        (
            "A consultative referendum under article 92 of the Constitution on whether Spain should remain in the "
            "Atlantic Alliance on the terms the Government set out."
        ),
    ),
    date(2005, 2, 20): (
        "200502",
        "Treaty establishing a Constitution for Europe",
        "Tratado por el que se establece una Constitución para Europa",
        "optional_referendum",
        "Referéndum sobre el Tratado por el que se establece una Constitución para Europa",
        (
            "A consultative referendum under article 92 of the Constitution on the Treaty establishing a Constitution "
            "for Europe."
        ),
    ),
}
ENGLISH = {
    date(1976, 12, 15): "December 1976 Spanish referendum on the Law for Political Reform",
    date(1978, 12, 6): "December 1978 Spanish constitutional referendum",
    date(1986, 3, 12): "March 1986 Spanish referendum on NATO membership",
    date(2005, 2, 20): "February 2005 Spanish referendum on the European Constitution",
}
SYSTEM = (
    "Voters answer Yes or No to one question. The ministry records Yes votes, No votes, blank ballots, and null "
    "ballots; blank ballots count as valid. The answer with more votes carries."
)
PROVINCE_METHOD = (
    "Yes and No votes, registered voters, blank ballots, and null ballots by province from the Ministry of the "
    "Interior's data files. The province rows include the votes of residents abroad (CERA). Registered voters are "
    "the count used in the tally. Valid votes here are Yes and No together; blank ballots, which the ministry counts "
    "as valid, are stored apart. The national row, where the "
    "file has one, equals the sum of the provinces."
)
PROVINCE_ONLY = (
    "The ministry's 1976 file gives results by province only, with no national row; the national figures are the "
    "sums of the provinces."
)
MESA_METHOD = (
    "Yes and No votes, registered voters, blank ballots, and null ballots by polling station (mesa) from the "
    "Ministry of the Interior's data files, with the votes of residents abroad (CERA) as one unit per province "
    "counted on the abroad channel. A mesa sits on its census section where the section code is in the 2025 INE "
    "section map, otherwise on its municipality."
)
MESA_EXACT = " The mesa figures add up to the province totals exactly."
MESA_PROVISIONAL = (
    "The ministry flags the 1986 mesa figures as not official: they come from the provisional count and exceed the "
    "official province totals by {yes:,} Yes and {no:,} No votes, so the official province set is the default."
)


def zipped(url: str, name: str) -> bytes:
    with zipfile.ZipFile(raw.cached("es.infoelectoral", url).path) as archive:
        return archive.read(name)


def lines(content: bytes) -> list[str]:
    return [line for line in content.decode("latin-1").split("\n") if line]


def section_of(field: str, year: int) -> str:
    if year < 2000:
        return f"{int(field):03d}"
    return field[:3] + field[3].strip()


def counts(yes: str, no: str, cand: str) -> tuple[int, int]:
    if int(cand) not in (0, int(yes) + int(no)):
        raise ValueError("Yes and No differ from the votes to candidacies")
    return int(yes), int(no)


def mesa(line: str, year: int) -> dict[str, int | str | bool]:
    cera = line[13:16] == "999"
    yes, no = counts(line[86:93], line[93:100], line[79:86])
    return {
        "yyyymm": line[2:8],
        "prov": line[11:13],
        "muni": line[13:16],
        "district": line[16:18],
        "section": "0000" if cera else section_of(line[18:22], year),
        "mesa": line[22],
        "registered": int(line[30:37]),
        "blank": int(line[65:72]),
        "null": int(line[72:79]),
        "yes": yes,
        "no": no,
        "cera": cera,
        "official": line[100] == "S",
    }


def fetch() -> None:
    pass


def parse() -> None:
    provinces, mesas = [], []
    for day, (stamp, *_rest) in REFERENDUMS.items():
        if stamp == "197612":
            frame = pl.read_excel(
                io.BytesIO(zipped(DOCXL + "PROV_01_197612_1.zip", "PROV_01_197612_1.xlsx")),
                engine="calamine",
                has_header=False,
            ).slice(2)
            for row in frame.iter_rows():
                code = row[1].strip().zfill(2)
                blank, null = int(row[9]), int(row[10])
                yes, no = int(row[11]), int(row[12])
                if yes + no != int(row[8]) or yes + no + blank != int(row[7]):
                    raise ValueError(f"1976 province {code}: the file's figures do not add up")
                provinces.append((day, code, int(row[5]), yes, no, blank, null))
            continue
        year = int(stamp[:4])
        totals = lines(zipped(f"{APLIEXTR}01{stamp}_TOTA.zip", f"0701{stamp[2:]}.DAT"))
        for line in totals:
            yes, no = counts(line[155:163], line[163:171], line[141:149])
            if line[9:13] == "9999":
                prov = "ES"
            elif line[11:13] != "99":
                prov = line[11:13]
            else:
                continue
            provinces.append((day, prov, int(line[85:93]), yes, no, int(line[125:133]), int(line[133:141])))
        if stamp in {"198603", "200502"}:
            for line in lines(zipped(f"{APLIEXTR}01{stamp}_MESA.zip", f"0901{stamp[2:]}.DAT")):
                if line[9:11] == "99" or line[11:13] == "99":
                    continue
                mesas.append({"day": day, **mesa(line, year)})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        provinces, schema=["day", "unit", "registered", "yes", "no", "blank", "null"], orient="row"
    ).write_parquet(PARSED / "provinces.parquet")
    pl.DataFrame(mesas).write_parquet(PARSED / "mesas.parquet")


def emit() -> Path:
    provinces = pl.read_parquet(PARSED / "provinces.parquet")
    mesas = pl.read_parquet(PARSED / "mesas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Spain's national referendums of 1976, 1978, 1986, and 2005 by province and, for 1986 and 2005, by "
                "mesa, from the Ministry of the Interior's data files"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain = cs.polity("ES")
        if conn.execute("select 1 from scheme where code = 'es.seccion'").fetchone() is None:
            raise ValueError("publish es.ine before the referendums")
        if conn.execute("select 1 from scheme where code = 'es.mesa'").fetchone() is None:
            cs.add("scheme", code="es.mesa", polity_id=spain, kind="precinct", rank=5, noun="polling station")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Reuse under Ley 37/2007 with the source cited",
            attribution="Origen de los datos: Ministerio del Interior.",
        )
        for stamp in ("197812", "198603", "200502"):
            cs.artifact(raw.cached("es.infoelectoral", f"{APLIEXTR}01{stamp}_TOTA.zip"), source)
        for stamp in ("198603", "200502"):
            cs.artifact(raw.cached("es.infoelectoral", f"{APLIEXTR}01{stamp}_MESA.zip"), source)
        cs.artifact(raw.cached("es.infoelectoral", DOCXL + "PROV_01_197612_1.zip"), source)

        system = cs.key("es.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=spain, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Spanish national referendum")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        municipalities = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        unit_rows = {}
        nation_area = cs.area("es.nation", "ES")
        nation = cs.unit("es.nation", "ES")
        unit_rows[nation] = (nation, "es.nation", "ES", None, "equals", nation_area, "standard")
        province_units = {}
        for code in sorted(set(provinces["unit"]) - {"ES"}):
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, nation, "equals", cs.area("es.provincia", code), "standard")

        values = []
        for day, (stamp, english, native, kind, native_title, about) in REFERENDUMS.items():
            key = f"{day}:referendum"
            election = cs.key("es.election", key)
            cs.add(
                "election", id=election, polity_id=spain, kind="referendum", date_from=day, date_to=day, status="held"
            )
            name(election, ENGLISH[day])
            name(election, f"{native_title} de {day.year}", "es", "native")
            path = f"es/{day}-referendum"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("es.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=kind)
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
            rows = provinces.filter(pl.col("day") == day)
            units_rows = rows.filter(pl.col("unit") != "ES")
            yes, no = int(units_rows["yes"].sum()), int(units_rows["no"].sum())
            national = rows.filter(pl.col("unit") == "ES")
            if national.height and (national["yes"][0], national["no"][0]) != (yes, no):
                raise ValueError(f"{day}: the national row differs from the sum of the provinces")
            winner = "yes" if yes > no else "no"
            sets = {f"{key}:provinces": (rows, True, "province")}
            day_mesas = mesas.filter(pl.col("day") == day)
            if day_mesas.height:
                sets[f"{key}:mesas"] = (day_mesas, False, "mesa")
            for set_key, (rows_in, default, level) in sets.items():
                result_set = cs.key(f"{SOURCE}.result_set", set_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official" if default or day_mesas["official"].all() else "preliminary",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if level == "province":
                    note = PROVINCE_METHOD + (PROVINCE_ONLY if not national.height else "")
                else:
                    mesa_yes, mesa_no = int(rows_in["yes"].sum()), int(rows_in["no"].sum())
                    if (mesa_yes, mesa_no) == (yes, no):
                        note = MESA_METHOD + MESA_EXACT
                    else:
                        note = MESA_METHOD + " " + MESA_PROVISIONAL.format(yes=mesa_yes - yes, no=mesa_no - no)
                over = rows_in.filter(
                    pl.col("yes") + pl.col("no") + pl.col("blank") + pl.col("null") > pl.col("registered")
                ).height
                if over:
                    note += f" In {over} units the ballots cast exceed the registered voters, as the file prints them."
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                cs.add("note", entity_id=result_set, kind="context", body_md=about)
                for answer, option in options.items():
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="won" if answer == winner else "lost",
                    )
                for row in rows_in.iter_rows(named=True):
                    channel = 0
                    if level == "province":
                        unit = nation if row["unit"] == "ES" else province_units[row["unit"]]
                    else:
                        cera = row["cera"]
                        yyyymm = row["yyyymm"]
                        code = f"{yyyymm}:{row['prov']}{row['muni']}{row['district']}{row['section']}{row['mesa']}"
                        unit = cs.unit("es.mesa", code)
                        if unit not in unit_rows:
                            section = f"{row['prov']}{row['muni']}{row['district']}{row['section']}"
                            if cera:
                                area, relation, channel = cs.area("es.provincia", row["prov"]), "within", ABROAD
                            elif section in sections:
                                area, relation = sections[section], "within"
                            elif row["prov"] + row["muni"] in municipalities:
                                area, relation = municipalities[row["prov"] + row["muni"]], "within"
                            else:
                                area, relation = None, "unlocated"
                            unit_rows[unit] = (
                                unit,
                                "es.mesa",
                                code,
                                province_units[row["prov"]],
                                relation,
                                area,
                                "aggregate" if cera else "standard",
                            )
                        elif cera:
                            channel = ABROAD
                    valid = row["yes"] + row["no"]
                    for option, measure, value in (
                        (options["yes"], VOTES, row["yes"]),
                        (options["no"], VOTES, row["no"]),
                        (None, REGISTERED, row["registered"]),
                        (None, BALLOTS, valid + row["blank"] + row["null"]),
                        (None, VALID, valid),
                        (None, INVALID, row["null"]),
                        (None, BLANK, row["blank"]),
                    ):
                        values.append(
                            (
                                str(result_set),
                                str(unit),
                                None if option is None else str(option),
                                measure,
                                value,
                                channel,
                            )
                        )
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
                    if unit in cs.minted
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
