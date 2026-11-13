import difflib
import json
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "mx.mge"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
API = "https://cartografia.ine.mx/sige8/api/bases/?producto=bgd&entidad={}&formato=Shapefile"
MAPOTECA = "https://cartografia.ine.mx/mapoteca/"
PLAN_2017 = MAPOTECA + "distritacion2017/federal/{0:02d}/{0:02d}.zip"
INEGI = "https://gaia.inegi.org.mx/wscatgeo/v2/{}"
PORTAL = "https://cartografia.ine.mx/sige8/"
INE = "Instituto Nacional Electoral (INE)"
BGD_TITLE = "Base Geográfica Digital del Marco Geográfico Electoral, corte 15 de diciembre de 2025"
PLAN_TITLE = "Distritación federal 2017: entidad, distrito, municipio y sección (INE/CG59/2017)"
INE_LICENSE = "Public information INE publishes for download (Ley General de Transparencia, art. 70); no license stated"
INEGI_PUBLISHER = "Instituto Nacional de Estadística y Geografía (INEGI)"
INEGI_TITLE = "Catálogo Único de Claves de Áreas Geoestadísticas Estatales, Municipales y Localidades (wscatgeo v2)"
INEGI_LICENSE = "Términos de libre uso de la información del INEGI"
INEGI_TERMS = "https://www.inegi.org.mx/contenidos/inegi/doc/terminos_info.pdf"
STATES = (
    ("AGU", "Aguascalientes", "Q79952"),
    ("BCN", "Baja California", "Q58731"),
    ("BCS", "Baja California Sur", "Q46508"),
    ("CAM", "Campeche", "Q80908"),
    ("COA", "Coahuila", "Q53079"),
    ("COL", "Colima", "Q61309"),
    ("CHP", "Chiapas", "Q60123"),
    ("CHH", "Chihuahua", "Q655"),
    ("CMX", "Mexico City", "Q1489"),
    ("DUR", "Durango", "Q79918"),
    ("GUA", "Guanajuato", "Q46475"),
    ("GRO", "Guerrero", "Q60158"),
    ("HID", "Hidalgo", "Q80903"),
    ("JAL", "Jalisco", "Q13160"),
    ("MEX", "State of Mexico", "Q82112"),
    ("MIC", "Michoacán", "Q79861"),
    ("MOR", "Morelos", "Q66117"),
    ("NAY", "Nayarit", "Q79920"),
    ("NLE", "Nuevo León", "Q15282"),
    ("OAX", "Oaxaca", "Q34110"),
    ("PUE", "Puebla", "Q79923"),
    ("QUE", "Querétaro", "Q79754"),
    ("ROO", "Quintana Roo", "Q80245"),
    ("SLP", "San Luis Potosí", "Q78980"),
    ("SIN", "Sinaloa", "Q80252"),
    ("SON", "Sonora", "Q46422"),
    ("TAB", "Tabasco", "Q80914"),
    ("TAM", "Tamaulipas", "Q80007"),
    ("TLA", "Tlaxcala", "Q82681"),
    ("VER", "Veracruz", "Q60130"),
    ("YUC", "Yucatán", "Q60176"),
    ("ZAC", "Zacatecas", "Q80269"),
)
SHORT = {"COA": "Coahuila", "MEX": "México", "MIC": "Michoacán", "VER": "Veracruz", "CMX": "Ciudad de México"}


def listing(entidad: int) -> dict[str, str]:
    files = json.loads(raw.cached(SOURCE, API.format(entidad)).path.read_text())
    if len(files) != 1:
        raise ValueError(f"expected one BGD file for {entidad}, got {files}")
    return files[0]


def bgd_url(entidad: int) -> str:
    entry = listing(entidad)
    return MAPOTECA + quote(entry["path"] + entry["archivo"])


def fetch() -> None:
    raw.fetch(SOURCE, INEGI.format("mgee/"), suffix=".json")
    for entidad in range(1, 33):
        raw.fetch(SOURCE, INEGI.format(f"mgem/{entidad:02d}"), suffix=".json")
        raw.fetch(SOURCE, API.format(entidad), suffix=".json")
        raw.fetch(SOURCE, bgd_url(entidad), suffix=".7z")
        raw.fetch(SOURCE, PLAN_2017.format(entidad), suffix=".zip")


TOLERANCE = 10
SAME = 0.005
PLANS = {"2017": date(2017, 3, 15), "2023": date(2022, 12, 14)}
CUT = date(2025, 12, 15)
SNAPSHOT_2017 = date(2017, 4, 5)
LAYERS = ("SECCION", "MUNICIPIO", "DISTRITO_FEDERAL", "DISTRITO")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.upper())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def utm(prj: str) -> int:
    zone = re.search(r"UTM_Zone_(\d+)N", prj)
    if zone is None or "WGS_1984" not in prj:
        raise ValueError(f"unexpected projection {prj[:80]}")
    return 32600 + int(zone[1])


def municipios(entidad: int) -> list[dict[str, str]]:
    return json.loads(raw.cached(SOURCE, INEGI.format(f"mgem/{entidad:02d}")).path.read_text())["datos"]


def extract(archive: Path, folder: Path) -> None:
    patterns = [f"*{layer}.*" for layer in LAYERS]
    subprocess.run(["7z", "e", "-y", f"-o{folder}", str(archive), *patterns, "-r"], check=True, capture_output=True)


def layer(folder: Path, name: str) -> shapefile.Reader | None:
    stem = next((path.with_suffix("") for path in folder.iterdir() if path.name.upper() == f"{name}.SHP"), None)
    return (
        None
        if stem is None
        else shapefile.Reader(
            str(stem),
            encoding="utf-8"
            if (Path(str(stem) + ".cpg")).exists() or (Path(str(stem) + ".CPG")).exists()
            else "cp1252",
        )
    )


def number(value: object) -> int:
    if not isinstance(value, int | float):
        raise TypeError(f"expected a number, got {value!r}")
    return int(value)


def read(vintage: str, entidad: int, folder: Path) -> tuple[int, list[tuple[object, ...]], dict[int, str]]:
    prj = next(path for path in folder.iterdir() if path.name.upper() == "SECCION.PRJ").read_text()
    sections = layer(folder, "SECCION")
    towns = layer(folder, "MUNICIPIO")
    if sections is None or towns is None:
        raise ValueError(f"{vintage} {entidad}: missing layers")
    district = "distrito_f" if vintage == "2025" else "distrito"
    rows = []
    for record, shape in zip(sections.iterRecords(), sections.iterShapes(), strict=True):
        if record is None or shape is None:
            raise ValueError(f"{vintage} {entidad}: empty section record")
        fields = record.as_dict()
        if number(fields["entidad"]) != entidad:
            raise ValueError(f"{vintage} {entidad}: section {fields['seccion']} in {fields['entidad']}")
        rows.append(
            (
                vintage,
                entidad,
                number(fields["seccion"]),
                number(fields["municipio"]),
                number(fields[district]),
                number(fields["tipo"]),
                json.dumps(shape.__geo_interface__),
            )
        )
    names = {
        number(fields["municipio"]): str(fields["nombre"])
        for fields in (record.as_dict() for record in towns.iterRecords() if record is not None)
    }
    return utm(prj), rows, names


ALIASES = {
    "05007": "05007",
    "07107": "07071",
    "11032": "11032",
    "20045": "20045",
    "20208": "20208",
    "20209": "20209",
    "20316": "20318",
    "20317": "20319",
    "20549": "20549",
    "29037": "29037",
    "31095": "31095",
}
ALIASES_2017 = {"20121": "20124", "20332": "20334", "23008": "23008", "29005": "29004", "29043": "29043"}
EXPANSIONS = ((r"\bGRAL\b", "GENERAL"), (r"\bDR\b", "DOCTOR"), (r"^H ", "HEROICA "))


def expand(text: str) -> str:
    text = fold(text)
    for pattern, replacement in EXPANSIONS:
        text = re.sub(pattern, replacement, text)
    return text


def match(entidad: int, names: dict[int, str], aliases: dict[str, str]) -> dict[int, str]:
    catalog = {row["cvegeo"]: expand(row["nomgeo"]) for row in municipios(entidad)}
    found: dict[int, str] = {}
    for number, text in names.items():
        key = f"{entidad:02d}{number:03d}"
        if key in aliases:
            found[number] = aliases[key]
            continue
        wanted = expand(text)
        codes = [code for code, name in catalog.items() if name == wanted] or [
            code for code, name in catalog.items() if re.search(rf"(^| ){wanted}( |$)", name)
        ]
        if len(codes) != 1:
            raise ValueError(f"{key} {text!r} matches {codes}")
        found[number] = codes[0]
    if len(set(found.values())) != len(found):
        raise ValueError(f"{entidad}: two INE municipios match one INEGI code")
    return found


def stage(vintage: str, entidad: int, folder: Path) -> None:
    if vintage == "2025":
        extract(raw.cached(SOURCE, bgd_url(entidad)).path, folder)
        return
    with zipfile.ZipFile(raw.cached(SOURCE, PLAN_2017.format(entidad)).path) as archive:
        for member in archive.namelist():
            if Path(member).stem.upper() in LAYERS and not member.endswith("/"):
                (folder / Path(member).name).write_bytes(archive.read(member))


def parse() -> None:
    towns: list[tuple[str, int, int, str, str]] = []
    with connect() as conn:
        conn.execute(
            """create temp table piece (vintage text, entidad int, seccion int, municipio int, distrito int, tipo int,
                                        geom geometry) on commit drop"""
        )
        for entidad in range(1, 33):
            current: dict[int, str] = {}
            names: dict[int, str] = {}
            for vintage in ("2025", "2017"):
                with tempfile.TemporaryDirectory(dir=DATA / "tmp") as folder:
                    stage(vintage, entidad, Path(folder))
                    srid, rows, found = read(vintage, entidad, Path(folder))
                if vintage == "2025":
                    current = match(entidad, found, ALIASES)
                    names = found
                    mapping = current
                else:
                    mapping = {}
                    for number, text in found.items():
                        similar = (
                            number in names and difflib.SequenceMatcher(None, fold(text), fold(names[number])).ratio()
                        )
                        if similar and similar >= 0.8:
                            mapping[number] = current[number]
                        else:
                            mapping |= match(entidad, {number: text}, ALIASES_2017)
                towns.extend((vintage, entidad, number, mapping[number], found[number]) for number in sorted(found))
                conn.execute(
                    """insert into piece
                       select vintage, entidad, seccion, municipio, distrito, tipo,
                              st_transform(st_setsrid(st_force2d(st_geomfromgeojson(geojson)), %s), 6372)
                       from unnest(%s::text[], %s::int[], %s::int[], %s::int[], %s::int[], %s::int[], %s::text[])
                         as f(vintage, entidad, seccion, municipio, distrito, tipo, geojson)""",
                    [srid, *(list(column) for column in zip(*rows, strict=True))],
                )
        conn.execute(
            "create temp table town (vintage text, entidad int, municipio int, cvegeo text, name text) on commit drop"
        )
        conn.execute(
            """insert into town select * from unnest(%s::text[], %s::int[], %s::int[], %s::text[], %s::text[])""",
            [list(column) for column in zip(*towns, strict=True)],
        )
        conn.execute(
            """create temp table section on commit drop as
               select vintage, entidad, seccion, cvegeo, distrito, tipo, municipios, distritos,
                      st_setsrid(st_coveragesimplify(geom, %s) over (partition by vintage), 6372) as geom
               from (select p.vintage, p.entidad, p.seccion, min(t.cvegeo) as cvegeo, min(p.distrito) as distrito,
                            min(p.tipo) as tipo, count(distinct t.cvegeo) as municipios,
                            count(distinct p.distrito) as distritos,
                            st_multi(st_collectionextract(st_makevalid(st_union(p.geom, 0.01)), 3)) as geom
                     from piece p join town t using (vintage, entidad, municipio)
                     group by 1, 2, 3) pieces""",
            [TOLERANCE],
        )
        conn.execute(
            """create temp table layer on commit drop as with layers as (
                 select 'seccion' as layer, vintage, lpad(entidad::text, 2, '0') || lpad(seccion::text, 4, '0') as code,
                        cvegeo as parent, lpad(entidad::text, 2, '0') || lpad(distrito::text, 2, '0') as distrito, tipo,
                        greatest(municipios, distritos) as owners, geom
                 from section
                 union all
                 select 'municipio', vintage, cvegeo, left(cvegeo, 2), null, null, 1, st_setsrid(st_multi(st_union(geom, 0.01)), 6372)
                 from section where vintage = '2025' group by vintage, cvegeo
                 union all
                 select 'distrito', vintage, lpad(entidad::text, 2, '0') || lpad(distrito::text, 2, '0'),
                        lpad(entidad::text, 2, '0'), null, null, 1, st_setsrid(st_multi(st_union(geom, 0.01)), 6372)
                 from section group by vintage, entidad, distrito
                 union all
                 select 'entidad', vintage, lpad(entidad::text, 2, '0'), 'MX', null, null, 1,
                        st_setsrid(st_multi(st_union(geom, 0.01)), 6372)
                 from section where vintage = '2025' group by vintage, entidad
                 union all
                 select 'nation', vintage, 'MX', null, null, null, 1, st_setsrid(st_multi(st_union(geom, 0.01)), 6372)
                 from section where vintage = '2025' group by vintage
               )
               select * from layers"""
        )
        conn.execute("create index on layer (layer, code, vintage)")
        rows = conn.execute(
            """select l.layer, l.vintage, l.code, l.parent, l.distrito, l.tipo, l.owners, st_area(l.geom),
                      coalesce(st_area(st_symdifference(l.geom, c.geom)) / st_area(c.geom) < %s, false),
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(l.geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(l.geom), 4326)), 'hex')
               from layer l
               left join layer c on l.vintage = '2017' and c.vintage = '2025' and c.layer = l.layer and c.code = l.code""",
            [SAME],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "layer": pl.String,
            "vintage": pl.String,
            "code": pl.String,
            "parent": pl.String,
            "distrito": pl.String,
            "tipo": pl.Int64,
            "owners": pl.Int64,
            "area": pl.Float64,
            "same": pl.Boolean,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    counts = dict(
        frame.group_by("layer", "vintage")
        .len()
        .select(pl.concat_str("layer", "vintage", separator=" "), "len")
        .iter_rows()
    )
    if counts.get("distrito 2025") != 300 or counts.get("distrito 2017") != 300 or counts.get("entidad 2025") != 32:
        raise ValueError(f"unexpected counts {counts}")
    if frame["owners"].max() != 1 or frame["geom"].null_count():
        raise ValueError("a section spans two municipios or districts, or a shape is empty")
    totals = dict(
        frame.group_by("layer", "vintage")
        .agg(pl.col("area").sum())
        .select(pl.concat_str("layer", "vintage", separator=" "), "area")
        .iter_rows()
    )
    if max(totals.values()) / min(totals.values()) > 1.001:
        raise ValueError(f"layer areas disagree: {totals}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = frame.with_columns(
        pl.when(pl.col("same")).then(None).otherwise(pl.col(column)).alias(column) for column in ("geom", "label_point")
    )
    frame.drop("owners", "area").write_parquet(PARSED / "areas.parquet")
    pl.DataFrame(towns, schema=["vintage", "entidad", "municipio", "cvegeo", "name"], orient="row").write_parquet(
        PARSED / "towns.parquet"
    )


def ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    towns = pl.read_parquet(PARSED / "towns.parquet")
    states = {
        row["cve_ent"]: row["nomgeo"]
        for row in json.loads(raw.cached(SOURCE, INEGI.format("mgee/")).path.read_text())["datos"]
    }
    catalog = [row for entidad in range(1, 33) for row in municipios(entidad)]
    start, current = PLANS["2017"], PLANS["2023"]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mexico's 32 federal entities, 2,478 municipalities, 300 federal electoral districts under the 2017 "
            "and 2023 plans, and electoral sections from INE's Marco Geográfico Electoral and INEGI's catalog",
            polity="MX",
        )
        bgd = cs.source(INE, BGD_TITLE)
        cs.add(
            "source",
            id=bgd,
            kind="official_body",
            publisher=INE,
            title=BGD_TITLE,
            url=PORTAL,
            license=INE_LICENSE,
            license_url=PORTAL,
            attribution="Instituto Nacional Electoral, Marco Geográfico Electoral",
        )
        plan = cs.source(INE, PLAN_TITLE)
        cs.add(
            "source",
            id=plan,
            kind="official_body",
            publisher=INE,
            title=PLAN_TITLE,
            url=PORTAL,
            license=INE_LICENSE,
            license_url=PORTAL,
            attribution="Instituto Nacional Electoral, Marco Geográfico Electoral",
        )
        inegi = cs.source(INEGI_PUBLISHER, INEGI_TITLE)
        cs.add(
            "source",
            id=inegi,
            kind="official_body",
            publisher=INEGI_PUBLISHER,
            title=INEGI_TITLE,
            url=INEGI.format("mgem/"),
            license=INEGI_LICENSE,
            license_url=INEGI_TERMS,
            attribution="Fuente: INEGI, Catálogo Único de Claves de Áreas Geoestadísticas",
        )
        for entidad in range(1, 33):
            cs.artifact(raw.cached(SOURCE, API.format(entidad)), bgd)
            cs.artifact(raw.cached(SOURCE, bgd_url(entidad)), bgd)
            cs.artifact(raw.cached(SOURCE, PLAN_2017.format(entidad)), plan)
            cs.artifact(raw.cached(SOURCE, INEGI.format(f"mgem/{entidad:02d}")), inegi)
        cs.artifact(raw.cached(SOURCE, INEGI.format("mgee/")), inegi)

        def name(entity: UUID, text: str, lang: str, kind: str = "official", source: UUID = inegi) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
                source_id=source,
            )

        def span(begin: date | None, end: date | None, exact: bool) -> dict[str, object]:
            precision = "day" if exact else "unknown"
            return {
                "valid_from": begin,
                "valid_from_precision": precision if begin is not None else "day",
                "valid_to": end,
                "valid_to_precision": precision if end is not None else "day",
            }

        def geometry(area: UUID, geom: str, point: str | None, source: UUID, valid: dict[str, object]) -> None:
            cs.add(
                "area_geometry",
                area_id=area,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
                **valid,
            )

        mexico = cs.polity("MX")
        if mexico in cs.minted:
            raise ValueError("publish census.mx before mx.mge")
        for code, kind, rank, noun, system, kinds in (
            ("mx.nation", "administrative", 0, "country", None, None),
            ("mx.entidad", "administrative", 1, "state", "inegi", {"estado": "state", "capital": "federal capital"}),
            (
                "mx.municipio",
                "administrative",
                2,
                "municipality",
                "inegi",
                {"municipio": "municipality", "alcaldia": "borough"},
            ),
            ("mx.distrito_federal", "electoral_district", 2, "federal electoral district", "ine", None),
            ("mx.seccion", "precinct", 3, "electoral section", "ine", None),
            ("mx.casilla", "precinct", 4, "polling station", "ine", None),
        ):
            cs.add(
                "scheme", code=code, polity_id=mexico, kind=kind, rank=rank, noun=noun, code_system=system, kinds=kinds
            )

        layers = {key: group for key, group in frame.group_by("layer", "vintage")}
        nation_row = layers["nation", "2025"].row(0, named=True)
        nation = cs.area("mx.nation", "MX")
        cs.add("area", id=nation, scheme_code="mx.nation")
        cs.add("area_code", area_id=nation, code="MX", valid_from=None)
        name(nation, "Mexico", "en")
        name(nation, "México", "es", "native")
        geometry(nation, nation_row["geom"], None, bgd, span(None, None, True))
        if conn.execute("select 1 from polity_territory where polity_id = %s", [mexico]).fetchone() is None:
            cs.add("polity_territory", polity_id=mexico, area_id=nation)

        entidades: dict[str, UUID] = {}
        for row in layers["entidad", "2025"].sort("code").iter_rows(named=True):
            code = row["code"]
            iso, english, wikidata = STATES[int(code) - 1]
            area = entidades[code] = cs.area("mx.entidad", code)
            cs.add("area", id=area, scheme_code="mx.entidad")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
            cs.add(
                "area_kind",
                area_id=area,
                kind="capital" if iso == "CMX" else "estado",
                source_id=inegi,
                valid_from=None,
            )
            geometry(area, row["geom"], row["label_point"], bgd, span(None, None, True))
            polity = cs.polity(f"MX-{iso}")
            cs.add("polity", id=polity, kind="federal_unit", iso_code=f"MX-{iso}")
            cs.add("slug", path=f"mx/{iso.lower()}", entity_id=polity)
            cs.add("polity_parent", polity_id=polity, parent_id=mexico)
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add("external_id", entity_id=polity, scheme="wikidata", value=wikidata)
            for entity in (polity, area):
                name(entity, states[code], "es")
                if english != states[code]:
                    name(entity, english, "en", "english")

        shapes = {row["code"]: row for row in layers["municipio", "2025"].iter_rows(named=True)}
        numbers = {
            cvegeo: f"{entidad:02d}{municipio:03d}"
            for entidad, municipio, cvegeo in towns.filter(pl.col("vintage") == "2025")
            .select("entidad", "municipio", "cvegeo")
            .iter_rows()
        }
        towns_by_code: dict[str, UUID] = {}
        for row in sorted(catalog, key=lambda row: row["cvegeo"]):
            code = row["cvegeo"]
            area = towns_by_code[code] = cs.area("mx.municipio", code)
            cs.add("area", id=area, scheme_code="mx.municipio")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=entidades[code[:2]], valid_from=None)
            cs.add(
                "area_kind",
                area_id=area,
                kind="alcaldia" if code[:2] == "09" else "municipio",
                source_id=inegi,
                valid_from=None,
            )
            name(area, row["nomgeo"], "es")
            if code in shapes:
                geometry(area, shapes[code]["geom"], shapes[code]["label_point"], bgd, span(None, None, True))
            if code in numbers:
                cs.add("external_id", entity_id=area, scheme="mx.ine.municipio", value=numbers[code])
        missing = set(shapes) - set(towns_by_code)
        if missing:
            raise ValueError(f"INE municipios outside INEGI's catalog: {sorted(missing)}")

        districts: dict[str, UUID] = {}
        old = {row["code"]: row for row in layers["distrito", "2017"].iter_rows(named=True)}
        new = {row["code"]: row for row in layers["distrito", "2025"].iter_rows(named=True)}
        for code in sorted(old.keys() | new.keys()):
            area = districts[code] = cs.area("mx.distrito_federal", code)
            iso, english, _ = STATES[int(code[:2]) - 1]
            short = SHORT.get(iso, states[code[:2]])
            number = int(code[2:])
            cs.add("area", id=area, scheme_code="mx.distrito_federal")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=entidades[code[:2]], valid_from=None)
            name(area, f"Distrito {number} de {short}", "es", source=bgd)
            name(area, f"{english} {ordinal(number)} district", "en", "english", source=bgd)
            before, after = old.get(code), new.get(code)
            if before is not None and after is not None and before["same"]:
                geometry(area, after["geom"], after["label_point"], bgd, span(start, None, True))
                continue
            if before is not None:
                geometry(area, before["geom"], before["label_point"], plan, span(start, current, True))
            if after is not None:
                geometry(area, after["geom"], after["label_point"], bgd, span(current, None, True))

        old = {row["code"]: row for row in layers["seccion", "2017"].iter_rows(named=True)}
        new = {row["code"]: row for row in layers["seccion", "2025"].iter_rows(named=True)}
        for code in sorted(old.keys() | new.keys()):
            area = cs.area("mx.seccion", code)
            before, after = old.get(code), new.get(code)
            cs.add("area", id=area, scheme_code="mx.seccion")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            if before is not None and after is not None and before["parent"] != after["parent"]:
                cs.add(
                    "area_parent", area_id=area, parent_id=towns_by_code[before["parent"]], **span(None, current, False)
                )
                cs.add(
                    "area_parent", area_id=area, parent_id=towns_by_code[after["parent"]], **span(current, None, False)
                )
            else:
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=towns_by_code[(after or before or {})["parent"]],
                    valid_from=None,
                )
            if before is not None:
                cs.add(
                    "area_membership",
                    area_id=area,
                    parent_id=districts[before["distrito"]],
                    **span(start, current, True),
                )
            if after is not None:
                cs.add(
                    "area_membership", area_id=area, parent_id=districts[after["distrito"]], **span(current, None, True)
                )
            if before is not None and after is not None and before["same"]:
                geometry(area, after["geom"], after["label_point"], bgd, span(None, None, True))
                continue
            if before is not None:
                geometry(area, before["geom"], before["label_point"], plan, span(None, current, False))
            if after is not None:
                geometry(
                    area,
                    after["geom"],
                    after["label_point"],
                    bgd,
                    span(current, None, False),
                )

        for scheme in ("mx.entidad", "mx.municipio"):
            cs.add("boundary_set", scheme_code=scheme, vintage="2025-12", valid_from=None, valid_to=None, as_of=CUT)
        for scheme in ("mx.distrito_federal", "mx.seccion"):
            cs.add("boundary_set", scheme_code=scheme, vintage="2017", valid_from=start, valid_to=current, as_of=start)
            cs.add("boundary_set", scheme_code=scheme, vintage="2023", valid_from=current, valid_to=None, as_of=CUT)
        return cs.write()
