import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "es.ine"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEAR = 2025
SECTIONS = f"https://www.ine.es/prodyser/cartografia/seccionado_{YEAR}.zip"
AS_OF = date(YEAR, 1, 1)
VINTAGES = (2001, *range(2003, YEAR))
EARLIEST = date(VINTAGES[0], 1, 1)
DISPLACEMENT = 10
HISTORY_TITLE = f"Cartografía digitalizada de secciones censales, 1 January {VINTAGES[0]} to {YEAR - 1}"
PROJECTIONS = {
    "ED_1950_UTM_Zone_30N": 23030,
    "ED_1950_UTM_Zone_29N": 23029,
    "WGS_1984_UTM_Zone_28N": 32628,
    "WGS_1984_UTM_Zone_30N": 32630,
    "ETRS_1989_UTM_Zone_30N": 25830,
    "ETRS_1989_UTM_Zone_28N": 25828,
    "REGCAN95_UTM_Zone_28N": 4083,
}
SECTION = re.compile(r"(0[1-9]|[1-4]\d|5[0-2])\d{8}")
TOLERANCE = 10
PUBLISHER = "Instituto Nacional de Estadística"
TITLE = f"Cartografía digitalizada de secciones censales, 1 January {YEAR}"
PRODUCTS = (
    "https://www.ine.es/ss/Satellite?L=es_ES&c=Page&cid=1259952026632&p=1259952026632"
    "&pagename=ProductosYServicios%2FPYSLayout"
)
LICENSE = (
    "Free reuse, commercial or not, crediting the source and its update date, without altering the meaning of the "
    "data or implying INE endorsement"
)
COUNTS = {"seccion": 36554, "municipio": 8132, "provincia": 52, "comunidad": 19, "nation": 1}
COOFFICIAL = {"04", "09", "10", "12", "15", "16"}
COMMUNITIES = {
    "01": ("AN", "andalucia", "Andalucía", "es", "Andalusia", "Q5783"),
    "02": ("AR", "aragon", "Aragón", "es", "Aragon", "Q4040"),
    "03": ("AS", "asturias", "Principado de Asturias", "es", "Asturias", "Q3934"),
    "04": ("IB", "illes-balears", "Illes Balears", "ca", "Balearic Islands", "Q5765"),
    "05": ("CN", "canarias", "Canarias", "es", "Canary Islands", "Q5813"),
    "06": ("CB", "cantabria", "Cantabria", "es", "Cantabria", "Q3946"),
    "07": ("CL", "castilla-y-leon", "Castilla y León", "es", "Castile and León", "Q5739"),
    "08": ("CM", "castilla-la-mancha", "Castilla-La Mancha", "es", "Castile-La Mancha", "Q5748"),
    "09": ("CT", "cataluna", "Cataluña", "es", "Catalonia", "Q5705"),
    "10": ("VC", "comunitat-valenciana", "Comunitat Valenciana", "ca", "Valencian Community", "Q5720"),
    "11": ("EX", "extremadura", "Extremadura", "es", "Extremadura", "Q5777"),
    "12": ("GA", "galicia", "Galicia", "es", "Galicia", "Q3908"),
    "13": ("MD", "madrid", "Comunidad de Madrid", "es", "Community of Madrid", "Q5756"),
    "14": ("MC", "murcia", "Región de Murcia", "es", "Region of Murcia", "Q5772"),
    "15": ("NC", "navarra", "Comunidad Foral de Navarra", "es", "Navarre", "Q4018"),
    "16": ("PV", "pais-vasco", "País Vasco", "es", "Basque Country", "Q3995"),
    "17": ("RI", "la-rioja", "La Rioja", "es", "La Rioja", "Q5727"),
    "18": ("CE", "ceuta", "Ceuta", "es", "Ceuta", "Q5823"),
    "19": ("ML", "melilla", "Melilla", "es", "Melilla", "Q5831"),
}
NATIVE = {"09": ("Catalunya", "ca"), "15": ("Nafarroa", "eu"), "16": ("Euskadi", "eu")}
ARTICLES = {
    "El",
    "La",
    "Los",
    "Las",
    "el",
    "la",
    "A",
    "O",
    "As",
    "Os",
    "L'",
    "l'",
    "Els",
    "els",
    "Les",
    "les",
    "Es",
    "Sa",
    "Ses",
    "Illes",
}
PROVINCES = {
    "01": "Q81801",
    "02": "Q54889",
    "03": "Q54936",
    "04": "Q81802",
    "05": "Q55288",
    "06": "Q81803",
    "08": "Q81949",
    "09": "Q55271",
    "10": "Q81977",
    "11": "Q81978",
    "12": "Q54942",
    "13": "Q54932",
    "14": "Q81972",
    "15": "Q82119",
    "16": "Q54888",
    "17": "Q7194",
    "18": "Q82142",
    "19": "Q54925",
    "20": "Q95010",
    "21": "Q95015",
    "22": "Q55182",
    "23": "Q95025",
    "24": "Q71140",
    "25": "Q13904",
    "27": "Q95027",
    "28": "Q24004405",
    "29": "Q95028",
    "30": "Q24271891",
    "31": "Q24004404",
    "32": "Q95038",
    "33": "Q24004403",
    "34": "Q55269",
    "35": "Q95080",
    "36": "Q95086",
    "37": "Q71080",
    "38": "Q99976",
    "39": "Q31920747",
    "40": "Q55283",
    "41": "Q95088",
    "42": "Q55276",
    "43": "Q98392",
    "44": "Q54955",
    "45": "Q54929",
    "46": "Q54939",
    "47": "Q71097",
    "48": "Q93366",
    "49": "Q71113",
    "50": "Q55180",
}


def natural(text: str) -> str:
    def part(text: str) -> str:
        stem, _, article = text.rpartition(", ")
        if article not in ARTICLES:
            return text
        return f"{article}{stem}" if article.endswith("'") else f"{article} {stem}"

    return "/".join(part(piece) for piece in text.split("/"))


def vintage_url(year: int) -> str:
    return SECTIONS.replace(str(YEAR), str(year))


def fetch() -> None:
    raw.fetch(SOURCE, SECTIONS, suffix=".zip")
    for year in VINTAGES:
        raw.fetch(SOURCE, vintage_url(year), suffix=".zip")


def parse() -> None:
    sections()
    history()


def sections() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, SECTIONS).path) as archive:
        members = {name.rsplit(".", 1)[1].lower(): name for name in archive.namelist() if "." in name}
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(members["shp"])),
            shx=io.BytesIO(archive.read(members["shx"])),
            dbf=io.BytesIO(archive.read(members["dbf"])),
            encoding="cp1252",
        )
        features = [
            (
                record["CUSEC"],
                record["CUMUN"],
                record["CPRO"],
                record["CCA"],
                record["NMUN"],
                record["NPRO"],
                json.dumps(shape.__geo_interface__),
            )
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None
        ]
    with connect() as conn:
        conn.execute(
            """create temp table shape on commit drop as
               select code, municipio, provincia, comunidad, municipio_name, provincia_name,
                      st_setsrid(st_coveragesimplify(geom, %s) over (), 25830) as geom
               from (select code, municipio, provincia, comunidad, municipio_name, provincia_name,
                            st_multi(st_collectionextract(st_makevalid(st_force2d(
                              st_setsrid(st_geomfromgeojson(geojson), 25830))), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[])
                       as f(code, municipio, provincia, comunidad, municipio_name, provincia_name, geojson)) features""",
            [TOLERANCE, *(list(column) for column in zip(*features, strict=True))],
        )
        rows = conn.execute(
            """with municipios as (
                 select municipio as code, min(provincia) as parent, min(comunidad) as comunidad,
                        min(municipio_name) as name, count(distinct municipio_name) as names,
                        st_setsrid(st_multi(st_union(geom, 0.01)), 25830) as geom
                 from shape group by municipio
               ), provincias as (
                 select provincia as code, min(comunidad) as parent, min(comunidad) as comunidad,
                        min(provincia_name) as name, count(distinct provincia_name) as names,
                        st_setsrid(st_multi(st_union(geom, 0.01)), 25830) as geom
                 from shape group by provincia
               ), comunidades as (
                 select comunidad as code, 'ES' as parent, comunidad, null as name, 1 as names,
                        st_setsrid(st_multi(st_union(geom, 0.01)), 25830) as geom
                 from shape group by comunidad
               ), layers as (
                 select 'seccion' as layer, code, municipio as parent, comunidad, null as name, 1 as names, geom
                 from shape
                 union all select 'municipio', * from municipios
                 union all select 'provincia', * from provincias
                 union all select 'comunidad', * from comunidades
                 union all select 'nation', 'ES', null, null, null, 1, st_setsrid(st_multi(st_union(geom, 0.01)), 25830)
                 from comunidades
               )
               select layer, code, parent, comunidad, name, names, st_area(geom),
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from layers"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "layer": pl.String,
            "code": pl.String,
            "parent": pl.String,
            "comunidad": pl.String,
            "name": pl.String,
            "names": pl.Int64,
            "area": pl.Float64,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    if counts != COUNTS or frame["geom"].null_count() or frame["names"].max() != 1:
        raise ValueError(f"expected {COUNTS} with one name each, got {counts}")
    totals = dict(frame.group_by("layer").agg(pl.col("area").sum()).iter_rows())
    if max(totals.values()) / min(totals.values()) > 1.0001:
        raise ValueError(f"layer areas disagree: {totals}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.drop("names", "area").write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Spain, its autonomous communities, provinces, municipalities, and census sections from INE {YEAR}",
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PRODUCTS,
            license=LICENSE,
            license_url="https://www.ine.es/aviso_legal",
            attribution="Fuente: Sitio web del INE: www.ine.es",
        )
        cs.artifact(raw.cached(SOURCE, SECTIONS), source)
        earlier = cs.source(PUBLISHER, HISTORY_TITLE)
        cs.add(
            "source",
            id=earlier,
            kind="official_body",
            publisher=PUBLISHER,
            title=HISTORY_TITLE,
            url=PRODUCTS,
            license=LICENSE,
            license_url="https://www.ine.es/aviso_legal",
            attribution="Fuente: Sitio web del INE: www.ine.es",
        )
        for year in VINTAGES:
            cs.artifact(raw.cached(SOURCE, vintage_url(year)), earlier)
        spans = pl.read_parquet(PARSED / "history_spans.parquet")
        shapes = pl.read_parquet(PARSED / "history_shapes.parquet")
        names = dict(pl.read_parquet(PARSED / "history_names.parquet").iter_rows())
        spans_by_code: dict[str, list[tuple[str, str | None, date | None, date | None]]] = {}
        for code, kind, municipio, valid_from, valid_to in spans.iter_rows():
            spans_by_code.setdefault(code, []).append((kind, municipio, valid_from, valid_to))

        def geometry(area: UUID, geom: str, point: str | None, valid_from: date | None = None) -> None:
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=valid_from,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )

        spain = cs.polity("ES")
        if spain in cs.minted:
            raise ValueError("publish eu.members before es.ine")
        for code, rank, noun, kind in (
            ("es.nation", 0, "country", "administrative"),
            ("es.comunidad", 1, "autonomous community", "administrative"),
            ("es.provincia", 2, "province", "administrative"),
            ("es.municipio", 3, "municipality", "administrative"),
            ("es.seccion", 4, "census section", "precinct"),
        ):
            cs.add("scheme", code=code, polity_id=spain, kind=kind, rank=rank, noun=noun)

        layers = {layer: lines for (layer,), lines in frame.group_by("layer")}
        nation = cs.area("es.nation", "ES")
        cs.add("area", id=nation, scheme_code="es.nation")
        cs.add("area_code", area_id=nation, code="ES", valid_from=None)
        name(nation, "Spain", "en")
        name(nation, "España", "es", "native")
        if conn.execute("select 1 from polity_territory where polity_id = %s", [spain]).fetchone() is None:
            cs.add("polity_territory", polity_id=spain, area_id=nation)
        geometry(nation, layers["nation"]["geom"][0], None)

        ids: dict[tuple[str, str], UUID] = {("nation", "ES"): nation}
        parents = {"comunidad": "nation", "provincia": "comunidad", "municipio": "provincia", "seccion": "municipio"}
        for layer in ("comunidad", "provincia", "municipio", "seccion"):
            scheme = f"es.{layer}"
            for code, parent, comunidad, text, geom, point in (
                layers[layer].select("code", "parent", "comunidad", "name", "geom", "label_point").sort("code").rows()
            ):
                area = ids[(layer, code)] = cs.area(scheme, code)
                cs.add("area", id=area, scheme_code=scheme)
                if code not in spans_by_code:
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=ids[(parents[layer], parent)], valid_from=None)
                geometry(area, geom, point, AS_OF if layer == "seccion" else None)
                if layer == "seccion":
                    continue
                if layer != "comunidad":
                    name(area, natural(text), "und" if comunidad in COOFFICIAL else "es")
                    if layer == "provincia" and code in PROVINCES:
                        cs.add("external_id", entity_id=area, scheme="wikidata", value=PROVINCES[code])
                    continue
                iso, path, native, lang, english, wikidata = COMMUNITIES[code]
                polity = cs.polity(f"ES-{iso}")
                cs.add("polity", id=polity, kind="region", iso_code=f"ES-{iso}")
                cs.add("slug", path=f"es/{path}", entity_id=polity)
                cs.add("polity_parent", polity_id=polity, parent_id=spain)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add("external_id", entity_id=polity, scheme="wikidata", value=wikidata)
                for entity in (polity, area):
                    name(entity, native, lang)
                    if english != native:
                        name(entity, english, "en", "english")
                    if code in NATIVE:
                        name(entity, *NATIVE[code], "native")
        community = dict(layers["provincia"].select("code", "comunidad").iter_rows())
        for municipio in sorted(
            {m for rows in spans_by_code.values() for kind, m, _, _ in rows if kind == "parent" and m is not None}
            - {code for layer, code in ids if layer == "municipio"}
        ):
            rows = [
                (valid_from, valid_to)
                for code in spans_by_code
                if code.startswith(municipio)
                for kind, m, valid_from, valid_to in spans_by_code[code]
                if kind == "parent" and m == municipio
            ]
            first = None if any(valid_from is None for valid_from, _ in rows) else min(f for f, _ in rows if f)
            last = None if any(valid_to is None for _, valid_to in rows) else max(t for _, t in rows if t)
            area = ids[("municipio", municipio)] = cs.area("es.municipio", municipio)
            cs.add("area", id=area, scheme_code="es.municipio")
            cs.add("area_code", area_id=area, code=municipio, valid_from=first, valid_to=last)
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=ids[("provincia", municipio[:2])],
                valid_from=first,
                valid_to=last,
            )
            if municipio in names:
                name(area, natural(names[municipio]), "und" if community[municipio[:2]] in COOFFICIAL else "es")
        for code, rows in spans_by_code.items():
            area = ids.get(("seccion", code)) or cs.area("es.seccion", code)
            if ("seccion", code) not in ids:
                ids[("seccion", code)] = area
                cs.add("area", id=area, scheme_code="es.seccion")
            for kind, municipio, valid_from, valid_to in rows:
                if kind == "code":
                    cs.add("area_code", area_id=area, code=code, valid_from=valid_from, valid_to=valid_to)
                else:
                    cs.add(
                        "area_parent",
                        area_id=area,
                        parent_id=ids[("municipio", municipio or code[:5])],
                        valid_from=valid_from,
                        valid_to=valid_to,
                    )
        for code, valid_from, valid_to, geom, point in shapes.iter_rows():
            cs.add(
                "area_geometry",
                area_id=ids[("seccion", code)],
                valid_from=valid_from,
                valid_to=valid_to,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=earlier,
            )
        for scheme in ("es.comunidad", "es.provincia", "es.municipio", "es.seccion"):
            cs.add("boundary_set", scheme_code=scheme, vintage=str(YEAR), valid_from=None, valid_to=None, as_of=AS_OF)
        for year, following in zip(VINTAGES, (*VINTAGES[1:], YEAR), strict=True):
            cs.add(
                "boundary_set",
                scheme_code="es.seccion",
                vintage=str(year),
                valid_from=date(year, 1, 1),
                valid_to=date(following, 1, 1),
                as_of=date(year, 1, 1),
            )
        return cs.write()


def read_vintage(year: int) -> tuple[list[tuple[str, str, str | None, int, str]], int]:
    rows: list[tuple[str, str, str | None, int, str]] = []
    dropped = 0
    with zipfile.ZipFile(raw.cached(SOURCE, vintage_url(year)).path) as archive:
        for base in [name[:-4] for name in archive.namelist() if name.lower().endswith(".shp")]:
            members = {
                name.rsplit(".", 1)[1].lower(): name for name in archive.namelist() if name.rsplit(".", 1)[0] == base
            }
            projection = re.search(r'PROJCS\["([^"]+)"', archive.read(members["prj"]).decode("latin1"))
            if projection is None or projection[1] not in PROJECTIONS:
                raise ValueError(f"{year} {base}: unknown projection {projection and projection[1]}")
            for encoding in ("utf8", "cp1252"):
                reader = shapefile.Reader(
                    shp=io.BytesIO(archive.read(members["shp"])),
                    shx=io.BytesIO(archive.read(members["shx"])),
                    dbf=io.BytesIO(archive.read(members["dbf"])),
                    encoding=encoding,
                )
                try:
                    records = reader.records()
                except shapefile.dbfFileException:
                    continue
                break
            names = [field[0] for field in reader.fields[1:]]
            for record, shape in zip(records, reader.shapes(), strict=True):
                if record is None or shape is None or shape.shapeType == 0:
                    dropped += 1
                    continue
                values = dict(zip(names, record, strict=True))
                code = str(
                    values["CUSEC"]
                    if "CUSEC" in values
                    else f"{values['PROVMUN']}{values['DISTRITO']}{values['SECCION']}"
                )
                if not SECTION.fullmatch(code):
                    dropped += 1
                    continue
                name = values.get("NMUN")
                rows.append(
                    (
                        code,
                        code[:5],
                        name.strip() if isinstance(name, str) and name.strip() else None,
                        PROJECTIONS[projection[1]],
                        json.dumps(shape.__geo_interface__),
                    )
                )
    return rows, dropped


def stage(year: int) -> Path:
    target = PARSED / f"seccion_{year}.parquet"
    if target.exists():
        return target
    rows, dropped = read_vintage(year)
    with connect() as conn:
        conn.execute(
            """create temp table shape on commit drop as
               select code, municipio, name, st_coveragesimplify(geom, %s) over () as geom
               from (select code, municipio, name,
                            st_multi(st_collectionextract(st_makevalid(st_force2d(st_transform(
                              st_setsrid(st_geomfromgeojson(geojson), srid), 25830))), 3)) as geom
                     from unnest(%s::text[], %s::text[], %s::text[], %s::int[], %s::text[])
                       as f(code, municipio, name, srid, geojson)) features
               where not st_isempty(geom)""",
            [DISPLACEMENT, *(list(column) for column in zip(*rows, strict=True))],
        )
        out = conn.execute(
            """select code, min(municipio), min(name),
                      st_asewkb(st_setsrid(st_multi(st_collectionextract(st_makevalid(st_union(geom, 0.01)), 3)), 25830))
               from shape group by code order by code"""
        ).fetchall()
    frame = pl.DataFrame(
        out, schema={"code": pl.String, "municipio": pl.String, "name": pl.String, "geom": pl.Binary}, orient="row"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(target)
    print(f"{year}: {frame.height} sections, {dropped} records dropped for lacking a section code")
    return target


def advance(conn: Connection, on: date, final: bool) -> dict[str, int]:
    stamp = None if on == EARLIEST else on
    counts: dict[str, int] = {}
    counts["gone"] = conn.execute(
        """with gone as (delete from present p where not exists (select from cur c where c.code = p.code) returning *),
             codes as (insert into spans select code, 'code', null, code_since, %(on)s from gone),
             parents as (insert into spans select code, 'parent', municipio, municipio_since, %(on)s from gone)
           insert into shapes select code, geom_since, %(on)s, geom from gone""",
        {"on": on},
    ).rowcount
    counts["moved"] = conn.execute(
        """with old as (select p.code, p.municipio, p.municipio_since from present p join cur c using (code)
                       where c.municipio <> p.municipio),
             moved as (update present p set municipio = c.municipio, municipio_since = %(on)s
                       from cur c where c.code = p.code and c.municipio <> p.municipio returning p.code)
           insert into spans select code, 'parent', municipio, municipio_since, %(on)s from old""",
        {"on": on},
    ).rowcount
    if final:
        counts["closed"] = conn.execute(
            "insert into shapes select p.code, p.geom_since, %s, p.geom from present p join cur c using (code)", [on]
        ).rowcount
        return counts
    counts["changed"] = conn.execute(
        """with old as (select p.code, p.geom, p.geom_since from present p join cur c using (code)
                       where st_area(st_symdifference(p.geom, c.geom))
                             / greatest((st_perimeter(p.geom) + st_perimeter(c.geom)) / 2, 1) > %(limit)s),
             moved as (update present p set geom = c.geom, geom_since = %(on)s
                       from cur c where c.code = p.code and p.code in (select code from old) returning p.code)
           insert into shapes select code, geom_since, %(on)s, geom from old""",
        {"on": on, "limit": DISPLACEMENT},
    ).rowcount
    counts["new"] = conn.execute(
        """insert into present select c.code, c.municipio, %(stamp)s, %(stamp)s, c.geom, %(on)s
           from cur c where not exists (select from present p where p.code = c.code)""",
        {"stamp": stamp, "on": on},
    ).rowcount
    return counts


def history() -> None:
    current = pl.read_parquet(PARSED / "areas.parquet").filter(pl.col("layer") == "seccion")
    names: dict[str, str] = {}
    with connect() as conn:
        conn.execute(
            """create temp table present (code text primary key, municipio text, code_since date,
                 municipio_since date, geom geometry, geom_since date)"""
        )
        conn.execute("create temp table spans (code text, kind text, municipio text, valid_from date, valid_to date)")
        conn.execute("create temp table shapes (code text, valid_from date, valid_to date, geom geometry)")
        conn.execute("create temp table cur (code text, municipio text, geom geometry)")
        for year in (*VINTAGES, YEAR):
            conn.execute("truncate cur")
            if year == YEAR:
                rows = [
                    (code, municipio, None) for code, municipio in zip(current["code"], current["parent"], strict=True)
                ]
            else:
                frame = pl.read_parquet(stage(year))
                names.update({m: n for m, n in zip(frame["municipio"], frame["name"], strict=True) if n is not None})
                rows = [
                    (code, municipio, geom.hex())
                    for code, municipio, geom in zip(frame["code"], frame["municipio"], frame["geom"], strict=True)
                ]
            with conn.cursor().copy("copy cur (code, municipio, geom) from stdin") as copy:
                for row in rows:
                    copy.write_row(row)
            counts = advance(conn, date(year, 1, 1), year == YEAR)
            print(year, len(rows), counts, flush=True)
        conn.execute(
            """insert into spans select code, 'code', null::text, code_since, null::date from present
               union all select code, 'parent', municipio, municipio_since, null::date from present"""
        )
        spans = conn.execute(
            "select code, kind, municipio, valid_from, valid_to from spans order by code, kind, valid_from nulls first"
        ).fetchall()
        shapes = conn.execute(
            """select code, valid_from, valid_to,
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from shapes order by code, valid_from"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        spans,
        schema={
            "code": pl.String,
            "kind": pl.String,
            "municipio": pl.String,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
        },
        orient="row",
    ).write_parquet(PARSED / "history_spans.parquet")
    pl.DataFrame(
        shapes,
        schema={
            "code": pl.String,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    ).write_parquet(PARSED / "history_shapes.parquet")
    pl.DataFrame(list(names.items()), schema={"code": pl.String, "name": pl.String}, orient="row").write_parquet(
        PARSED / "history_names.parquet"
    )
