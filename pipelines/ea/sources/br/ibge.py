import csv
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "br.ibge"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MUNICIPIOS = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/"
    "municipio_2022/Brasil/BR/BR_Municipios_2022.zip"
)
CODES = "https://cdn.tse.jus.br/estatistica/sead/odsele/municipio_tse_ibge/municipio_tse_ibge.zip"
COUNTS = {"municipality": 5572, "uf": 27, "nation": 1}
TOLERANCE = 10
ELECTION = date(2022, 10, 2)


def fetch() -> None:
    raw.fetch(SOURCE, MUNICIPIOS, suffix=".zip")
    raw.fetch(SOURCE, CODES, suffix=".zip")


def parse() -> None:
    with zipfile.ZipFile(raw.cached(SOURCE, MUNICIPIOS).path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read("BR_Municipios_2022.shp")),
            shx=io.BytesIO(archive.read("BR_Municipios_2022.shx")),
            dbf=io.BytesIO(archive.read("BR_Municipios_2022.dbf")),
            encoding="utf-8",
        )
        features = [
            (str(record["CD_MUN"]), str(record["SIGLA_UF"]), json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None
        ]
    with zipfile.ZipFile(raw.cached(SOURCE, CODES).path) as archive:
        lines = list(
            csv.DictReader(
                io.StringIO(archive.read("municipio_tse_ibge.csv").decode("latin-1"), newline=""), delimiter=";"
            )
        )
    codes = pl.DataFrame(
        [(line["CD_MUNICIPIO_TSE"], line["CD_MUNICIPIO_IBGE"]) for line in lines],
        schema={"tse": pl.String, "ibge": pl.String},
        orient="row",
    )
    with connect() as conn:
        conn.execute("create temp table feature (code text, uf text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature (code, uf, geojson) from stdin") as copy:
            for row in features:
                copy.write_row(row)
        rows = conn.execute(
            """with municipality as (
                 select code, uf, st_setsrid(st_coveragesimplify(geom, %s) over (), 5880) as geom
                 from (select code, uf, st_setsrid(st_coverageclean(geom) over (), 5880) as geom from (select code, uf, st_multi(st_collectionextract(st_makevalid(st_union(st_makevalid(
                                st_force2d(st_transform(st_setsrid(st_geomfromgeojson(geojson), 4674), 5880))))), 3)) as geom
                       from feature group by code, uf) features) cleaned
               ),
               layers as (
                 select 'municipality' as layer, code, geom from municipality
                 union all
                 select 'uf', uf, st_setsrid(st_coverageunion(geom), 5880) from municipality group by uf
                 union all
                 select 'nation', 'BR', st_setsrid(st_coverageunion(geom), 5880) from municipality
               )
               select layer, code,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      case when layer <> 'nation'
                        then encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex') end
               from layers""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={"layer": pl.String, "code": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    )
    counts = dict(frame.group_by("layer").len().iter_rows())
    if counts != COUNTS or frame["geom"].null_count():
        raise ValueError(f"expected {COUNTS}, got {counts}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")
    codes.write_parquet(PARSED / "codes.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    ibge = dict(pl.read_parquet(PARSED / "codes.parquet").iter_rows())
    shapes = {
        (layer, code): (geom, point)
        for layer, code, geom, point in frame.select("layer", "code", "geom", "label_point").rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Brazil's municipalities, states, and nation from IBGE's 2022 municipal mesh",
            polity="BR",
        )
        sources = {}
        for url, publisher, title, page, license, license_url, attribution in (
            (
                MUNICIPIOS,
                "IBGE",
                "Malha Municipal Digital 2022",
                "https://www.ibge.gov.br/geociencias/organizacao-do-territorio/malhas-territoriais/15774-malhas.html",
                None,
                None,
                "Fonte: IBGE",
            ),
            (
                CODES,
                "Tribunal Superior Eleitoral",
                "Códigos oficiais de UF e municípios segundo o TSE e o IBGE",
                "https://dadosabertos.tse.jus.br/dataset/codigos-oficiais-de-uf-e-municipios-segundo-o-tse-e-o-ibge",
                "CC BY",
                "https://creativecommons.org/licenses/by/4.0/",
                "Tribunal Superior Eleitoral",
            ),
        ):
            sources[url] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=page,
                license=license,
                license_url=license_url,
                attribution=attribution,
                notes="No license stated; IBGE says its limits are approximate and not an official demarcation."
                if url == MUNICIPIOS
                else None,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])

        def geometry(area: object, key: tuple[str, str]) -> None:
            geom, point = shapes[key]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=sources[MUNICIPIOS],
            )

        geometry(cs.area("br.nation", "BR"), ("nation", "BR"))
        for code in frame.filter(pl.col("layer") == "uf")["code"]:
            geometry(cs.area("br.uf", code), ("uf", code))
        municipalities = conn.execute(
            """select e.value, a.id from area a join external_id e on e.entity_id = a.id
               where a.scheme_code = 'br.municipality' and e.scheme = 'br.tse.municipality'"""
        ).fetchall()
        missing = sorted(code for code, _ in municipalities if ("municipality", ibge.get(code, "")) not in shapes)
        if missing:
            raise ValueError(f"no IBGE shape for TSE municipalities {missing}")
        for code, area in municipalities:
            cs.add("external_id", entity_id=area, scheme="br.ibge.municipality", value=ibge[code])
            geometry(area, ("municipality", ibge[code]))
        for scheme in ("br.municipality", "br.uf"):
            cs.add("boundary_set", scheme_code=scheme, vintage="2022", valid_from=None, valid_to=None, as_of=ELECTION)
        return cs.write()
