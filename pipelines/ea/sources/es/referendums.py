import io
import json
import re
import unicodedata
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


BOE = "Boletín Oficial del Estado"
BOE_LICENSE = "https://www.boe.es/informacion/aviso_legal/index.php"
ARE = "https://hauteskundeak.euskadi.eus/ab12aAREWar/resultado/search"
ARE_PAGE = "https://hauteskundeak.euskadi.eus/webelec00-app/es/ab12aAREWar/?locale=es"
ARE_TITLE = "Archivo de resultados electorales (ARE): Referéndum Estatuto de Autonomía 1979"
ARE_BODY: dict[str, object] = {"idEleccion": "37", "idDatosBase": "2", "nivelDetalle": "5", "indMarcarTodos": 1}
VALID_MAJORITY = (
    "The statute carried only if Yes won a majority of the valid votes in each province, under article 151.2 of the "
    "Constitution and Organic Law 2/1980."
)
STATUTES: dict[
    str, tuple[date, str, str, str, str, str, str, str, str, bool, tuple[tuple[str, int, int, int, int, int, int], ...]]
] = {
    "basque-country": (
        date(1979, 10, 25),
        "16",
        "1979 Basque Statute of Autonomy referendum",
        "Referéndum del Proyecto de Estatuto de Autonomía para el País Vasco de 1979",
        "Statute of Autonomy for the Basque Country",
        "Proyecto de Estatuto de Autonomía para el País Vasco",
        "Acuerdo de la Junta Electoral Central por el que se hace público el resumen de la votación en el Referéndum del Proyecto de Estatuto de Autonomía para el País Vasco (BOE 269, 9 November 1979)",
        "https://www.boe.es/boe/dias/1979/11/09/pdfs/A26027-26027.pdf",
        "Voters in Álava, Guipúzcoa, and Vizcaya approved or rejected the draft Statute of Autonomy (the Statute of "
        "Gernika), put to them by Real Decreto-ley 13/1979. " + VALID_MAJORITY,
        True,
        (
            ("01", 174930, 110609, 92535, 10023, 6363, 1688),
            ("20", 507002, 302847, 278399, 12290, 9018, 3140),
            ("48", 883609, 507980, 460905, 25216, 16038, 5821),
        ),
    ),
    "andalusia-initiative": (
        date(1980, 2, 28),
        "01",
        "1980 Andalusian autonomy initiative referendum",
        "Referéndum de iniciativa autonómica de Andalucía de 1980",
        "Ratification of the autonomy initiative under article 151 of the Constitution",
        "Ratificación de la iniciativa autonómica prevista en el artículo 151 de la Constitución",
        "Acuerdo de 24 de abril de 1980, de la Junta Electoral Central, por el que se hacen públicos los resultados del referéndum de iniciativa autonómica de Andalucía (BOE 115, 13 May 1980)",
        "https://www.boe.es/boe/dias/1980/05/13/pdfs/A10346-10346.pdf",
        (
            "Voters in the eight Andalusian provinces ratified or rejected the autonomy initiative of article 151.1 of the "
            "Constitution, convened by Real Decreto 145/1980. The initiative needed Yes from an absolute majority of the "
            "registered voters in every province. Yes reached 42.31 percent of the registered voters in Almería, so the "
            "Junta Electoral Central declared the ratification rejected, although Yes led everywhere. Organic Law 12/1980 "
            "later let the Cortes substitute the initiative in Almería."
        ),
        False,
        (
            ("04", 279300, 142769, 118186, 11092, 12307, 1184),
            ("11", 664109, 407493, 367065, 13412, 24147, 2869),
            ("14", 521027, 362616, 312419, 20339, 27528, 2355),
            ("18", 535926, 335060, 283777, 20491, 29012, 1780),
            ("21", 307943, 186748, 165976, 6808, 12897, 1067),
            ("23", 468804, 296417, 234746, 29610, 29876, 2304),
            ("29", 661825, 392379, 346819, 20822, 22020, 2718),
            ("41", 991422, 720338, 643299, 29864, 42423, 4750),
        ),
    ),
    "galicia": (
        date(1980, 12, 21),
        "12",
        "1980 Galician Statute of Autonomy referendum",
        "Referéndum sobre el proyecto de Estatuto de Autonomía para Galicia de 1980",
        "Statute of Autonomy for Galicia",
        "Proyecto de Estatuto de Autonomía para Galicia",
        "Acuerdo de 8 de enero de 1981, de la Junta Electoral Central, por el que se hacen públicos los resultados oficiales del referéndum sobre el proyecto de Estatuto de Autonomía para Galicia (BOE 9, 10 January 1981)",
        "https://www.boe.es/boe/dias/1981/01/10/pdfs/A00573-00573.pdf",
        "Voters in A Coruña, Lugo, Ourense, and Pontevedra approved or rejected the draft Statute of Autonomy put to "
        "them by Real Decreto 2400/1980. " + VALID_MAJORITY,
        True,
        (
            ("15", 844268, 270827, 196736, 56102, 12068, 5921),
            ("27", 334412, 65716, 46981, 13588, 3105, 2042),
            ("32", 355397, 75226, 58265, 12056, 2945, 1960),
            ("36", 638821, 202449, 148574, 39702, 10263, 3910),
        ),
    ),
    "andalusia": (
        date(1981, 10, 20),
        "01",
        "1981 Andalusian Statute of Autonomy referendum",
        "Referéndum sobre el proyecto de Estatuto de Autonomía para Andalucía de 1981",
        "Statute of Autonomy for Andalusia",
        "Proyecto de Estatuto de Autonomía para Andalucía",
        "Acuerdo de 16 de diciembre de 1981, de la Junta Electoral Central, por el que se hacen públicos los resultados oficiales del referéndum sobre el proyecto de Estatuto de Autonomía para Andalucía (BOE 302, 18 December 1981)",
        "https://www.boe.es/boe/dias/1981/12/18/pdfs/A29661-29661.pdf",
        "Voters in the eight Andalusian provinces approved or rejected the draft Statute of Autonomy (the Statute of "
        "Carmona) put to them by Real Decreto 1835/1981. " + VALID_MAJORITY,
        True,
        (
            ("04", 285699, 126775, 109426, 12228, 4141, 980),
            ("11", 689367, 344537, 314679, 16842, 9957, 3059),
            ("14", 531456, 329055, 294850, 23101, 9437, 1667),
            ("18", 547285, 280213, 243890, 25809, 8084, 2430),
            ("21", 299136, 155959, 142072, 7891, 5086, 910),
            ("23", 481521, 277287, 239077, 28760, 7618, 1832),
            ("29", 684292, 354375, 316210, 25160, 9880, 3125),
            ("41", 1025080, 562402, 512373, 30399, 15670, 3960),
        ),
    ),
    "andalusia-reform": (
        date(2007, 2, 18),
        "01",
        "2007 Andalusian Statute of Autonomy referendum",
        "Referéndum sobre la reforma del Estatuto de Autonomía de Andalucía de 2007",
        "Reform of the Statute of Autonomy for Andalusia",
        "Reforma del Estatuto de Autonomía para Andalucía",
        "Resolución de 8 de marzo de 2007, de la Junta Electoral Central, de declaración oficial de los resultados del referéndum sobre reforma del Estatuto de Autonomía de Andalucía (BOE 64, 15 March 2007)",
        "https://www.boe.es/boe/dias/2007/03/15/pdfs/A11237-11238.pdf",
        (
            "Voters in Andalusia ratified or rejected the reform of the Statute of Autonomy passed by the Cortes Generales, "
            "put to them by Decreto 2/2007 of the Junta de Andalucía. The reform carried with a majority of the valid votes."
        ),
        True,
        (
            ("04", 444202, 140985, 119707, 16108, 4372, 798),
            ("11", 952169, 291127, 251467, 28901, 8801, 1958),
            ("14", 639725, 257816, 220793, 26337, 8507, 2179),
            ("18", 711533, 256885, 222716, 24301, 8014, 1854),
            ("21", 384488, 134458, 120844, 9163, 3805, 646),
            ("23", 529033, 230614, 206047, 17904, 5637, 1026),
            ("29", 1065869, 341146, 295214, 33707, 10522, 1703),
            ("41", 1459053, 564802, 484156, 51534, 18195, 10917),
        ),
    ),
}
STATUTE_TOTALS = {
    "basque-country": (1565541, 921436, 831839, 47529, 31419, 10649),
    "galicia": (2172898, 614218, 450556, 121448, 28381, 13833),
    "andalusia": (4543836, 2430603, 2172577, 170190, 69873, 17963),
    "andalusia-reform": (6186072, 2217833, 1920944, 207955, 67853, 21081),
}
STATUTE_METHOD = (
    "Registered voters, voters, Yes and No votes, blank ballots, and null ballots by province as the Junta Electoral "
    "Central declared them in the Boletín Oficial del Estado, read from the gazette page and checked against its "
    "printed totals. Ballots cast are the voters the gazette prints. Valid votes here are Yes and No together; blank "
    "ballots are stored apart."
)
STATUTE_UNEVEN = (
    " The gazette prints no total row, and in {provinces} the printed voters differ from the sum of Yes, No, blank, "
    "and null ballots ({gaps}); the figures are stored as printed."
)
ARE_METHOD = (
    "Registered voters, voters, Yes and No votes, blank ballots, and null ballots by municipality from the Basque "
    "Government's electoral results archive (ARE), which names each municipality as it is today. The municipalities "
    "add up exactly to the Junta Electoral Central's province figures."
)
ARE_UNEVEN = (
    " In {count} municipalities the archive's voters differ from the sum of Yes, No, blank, and null ballots; the "
    "figures are stored as the archive prints them."
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
    for spec in STATUTES.values():
        raw.fetch(SOURCE, spec[7], suffix=".pdf")
    raw.fetch(SOURCE, ARE, suffix=".json", body=ARE_BODY)


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
    statutes = []
    for key, spec in STATUTES.items():
        for prov, registered, voters, yes, no, blank, null in spec[10]:
            statutes.append((key, prov, registered, voters, yes, no, blank, null))
        if key in STATUTE_TOTALS:
            sums = tuple(sum(int(row[i]) for row in spec[10]) for i in range(1, 7))
            if sums != STATUTE_TOTALS[key]:
                raise ValueError(f"{key}: the provinces do not add up to the gazette's totals")
    pl.DataFrame(
        statutes, schema=["statute", "unit", "registered", "voters", "yes", "no", "blank", "null"], orient="row"
    ).write_parquet(PARSED / "statutes.parquet")
    municipalities = []
    for row in json.loads(raw.cached(SOURCE, ARE, ARE_BODY).path.read_bytes())["resultadosPorNivelDetalle"]:
        overall = row["resultadoGlobal"]
        votes = {party["siglas"]: party["votosPartido"] for party in row["resultadosPorPartido"]}
        if votes["SI"] + votes["NO"] != overall["votosCandidatura"]:
            raise ValueError(f"{row['desc']}: Yes and No differ from the votes to candidacies")
        municipalities.append(
            (
                row["desc"],
                overall["censoElectoral"],
                overall["totalVotos"],
                votes["SI"],
                votes["NO"],
                overall["votosBlancos"],
                overall["votosNulos"],
            )
        )
    pl.DataFrame(
        municipalities, schema=["name", "registered", "voters", "yes", "no", "blank", "null"], orient="row"
    ).write_parquet(PARSED / "basque_1979.parquet")


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
                "mesa, from the Ministry of the Interior's data files, and the Basque, Galician, and Andalusian statute "
                "referendums of 1979 to 2007 by province from the Junta Electoral Central's declarations in the BOE"
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
        statutes = pl.read_parquet(PARSED / "statutes.parquet")
        basque = pl.read_parquet(PARSED / "basque_1979.parquet")
        are = cs.source("Gobierno Vasco", ARE_TITLE)
        cs.add(
            "source",
            id=are,
            kind="official_body",
            publisher="Gobierno Vasco",
            title=ARE_TITLE,
            url=ARE_PAGE,
            license=None,
            license_url=None,
            attribution="Fuente: Gobierno Vasco, euskadi.eus.",
        )
        cs.artifact(raw.cached(SOURCE, ARE, ARE_BODY), are)
        statute_system = cs.key("es.electoral_system", "statute-referendum")
        cs.add("electoral_system", id=statute_system, polity_id=spain, rules={"formula": "referendum"}, rules_version=1)
        name(statute_system, "Spanish statute of autonomy referendum")
        cs.add("note", entity_id=statute_system, kind="methodology", body_md=SYSTEM)

        def fold(text: str) -> str:
            text = "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))
            return re.sub(r"[^A-Z]", "", text)

        basque_codes: dict[str, set[str]] = {}
        for code, text in conn.execute(
            "select c.code, n.text from area a join area_code c on c.area_id = a.id join name n on n.entity_id = a.id"
            " where a.scheme_code = 'es.municipio' and left(c.code, 2) in ('01', '20', '48')"
        ).fetchall():
            for part in {text, *re.split(r"[/-]", text)}:
                basque_codes.setdefault(fold(part), set()).add(code)

        def basque_code(label: str) -> str:
            for part in (label, *re.split(r"[/-]", label)):
                found = basque_codes.get(fold(part), set())
                if len(found) == 1:
                    return next(iter(found))
            raise ValueError(f"no single Basque municipality is named {label}")

        for statute, (
            day,
            community,
            english_title,
            native_title,
            english,
            native,
            gazette,
            gazette_url,
            about,
            carried,
            _rows,
        ) in STATUTES.items():
            key = f"{day}:referendum:{statute}"
            boe = cs.source(BOE, gazette)
            cs.add(
                "source",
                id=boe,
                kind="official_body",
                publisher=BOE,
                title=gazette,
                url=gazette_url,
                license="Public record: official gazette, free to reuse",
                license_url=BOE_LICENSE,
                attribution="Fuente: Boletín Oficial del Estado.",
            )
            cs.artifact(raw.cached(SOURCE, gazette_url), boe)
            election = cs.key("es.election", key)
            cs.add(
                "election", id=election, polity_id=spain, kind="referendum", date_from=day, date_to=day, status="held"
            )
            name(election, english_title)
            name(election, native_title, "es", "native")
            path = f"es/{day}-referendum-{statute.split('-')[0]}"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("es.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=statute_system,
                district_area_id=cs.area("es.comunidad", community),
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
            rows = statutes.filter(pl.col("statute") == statute)
            uneven = rows.filter(pl.col("yes") + pl.col("no") + pl.col("blank") + pl.col("null") != pl.col("voters"))
            province_note = STATUTE_METHOD
            if uneven.height:
                names = {
                    code: text
                    for code, text in conn.execute(
                        "select c.code, n.text from area a join area_code c on c.area_id = a.id"
                        " join name n on n.entity_id = a.id and n.is_preferred"
                        " where a.scheme_code = 'es.provincia' order by n.lang = 'en'"
                    ).fetchall()
                }
                gaps = [
                    f"{names[row['unit']]} {row['voters'] - row['yes'] - row['no'] - row['blank'] - row['null']:+,}"
                    for row in uneven.iter_rows(named=True)
                ]
                province_note += STATUTE_UNEVEN.format(
                    provinces=", ".join(names[code] for code in uneven["unit"]), gaps=", ".join(gaps)
                )
            sets = {f"{key}:provinces": (rows, True, province_note, boe)}
            if statute == "basque-country":
                off = basque.filter(
                    pl.col("yes") + pl.col("no") + pl.col("blank") + pl.col("null") != pl.col("voters")
                ).height
                sets[f"{key}:municipalities"] = (
                    basque.with_columns(unit=pl.col("name").map_elements(basque_code, return_dtype=pl.String)),
                    False,
                    ARE_METHOD + (ARE_UNEVEN.format(count=off) if off else ""),
                    are,
                )
            for set_key, (rows_in, default, note, set_source) in sets.items():
                result_set = cs.key(f"{SOURCE}.result_set", set_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=set_source,
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
                        kind="won" if (answer == "yes") == carried else "lost",
                    )
                for row in rows_in.iter_rows(named=True):
                    code = row["unit"]
                    if default:
                        unit = province_units.get(code)
                        if unit is None:
                            unit = province_units[code] = cs.unit("es.provincia", code)
                            area = cs.area("es.provincia", code)
                            unit_rows[unit] = (unit, "es.provincia", code, nation, "equals", area, "standard")
                    else:
                        unit = cs.unit("es.municipio", code)
                        if unit not in unit_rows:
                            area = municipalities.get(code)
                            unit_rows[unit] = (
                                unit,
                                "es.municipio",
                                code,
                                province_units[code[:2]],
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
        staged = {(str(row["entity_id"]), row["body_md"]) for row in cs.rows["note"]}
        for note, entity, body in conn.execute(
            """select n.id, n.entity_id, n.body_md from note n
               join changeset w on w.id = n.changeset_id and w.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if (str(entity), body) not in staged:
                cs.add("note_drop", note_id=note, entity_id=entity, body_md=body, reason="the note was rewritten")
        minted = [row for row in unit_rows.values() if row[0] in cs.minted]
        if minted:
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
                        for unit, scheme, code, parent, relation, area, kind in minted
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
