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

SOURCE = "br.ibge_bairros"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FOLDER = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
    "malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/bairros/shp/UF/"
)
STATES = [
    "AC", "AL", "AM", "AP", "BA", "CE", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP",
]  # fmt: skip
PAGE = "https://www.ibge.gov.br/geociencias/organizacao-do-territorio/malhas-territoriais/15774-malhas.html"
ELECTION = date(2022, 10, 2)


def fetch() -> None:
    for state in STATES:
        raw.fetch(SOURCE, f"{FOLDER}{state}_bairros_CD2022.zip", suffix=".zip")


def parse() -> None:
    features: list[tuple[str, str, str, str]] = []
    for state in STATES:
        with zipfile.ZipFile(raw.cached(SOURCE, f"{FOLDER}{state}_bairros_CD2022.zip").path) as archive:
            stem = f"{state}_bairros_CD2022"
            reader = shapefile.Reader(
                shp=io.BytesIO(archive.read(f"{stem}.shp")),
                shx=io.BytesIO(archive.read(f"{stem}.shx")),
                dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                encoding="utf-8",
            )
            for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                if record is not None and shape is not None:
                    features.append(
                        (
                            str(record["CD_BAIRRO"]),
                            str(record["CD_MUN"]),
                            str(record["NM_BAIRRO"]),
                            json.dumps(shape.__geo_interface__),
                        )
                    )
    with connect() as conn:
        conn.execute(
            "create temp table feature (code text, municipality text, label text, geojson text) on commit drop"
        )
        with (
            conn.cursor() as cursor,
            cursor.copy("copy feature (code, municipality, label, geojson) from stdin") as copy,
        ):
            for row in features:
                copy.write_row(row)
        rows = conn.execute(
            """select code, min(municipality), min(label),
                      encode(st_asewkb(g), 'hex'), encode(st_asewkb(st_pointonsurface(g)), 'hex')
               from (
                 select code, municipality, label,
                        st_multi(st_collectionextract(st_transform(st_setsrid(st_makevalid(st_union(
                          st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 4674))))), 4674), 4326), 3)) as g
                 from feature group by code, municipality, label
               ) merged
               group by code, g"""
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "code": pl.String,
            "municipality": pl.String,
            "label": pl.String,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    if frame["code"].n_unique() != frame.height:
        raise ValueError("a bairro code has several shapes or labels")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "bairros.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "bairros.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Brazil's bairros from IBGE's 2022 census mesh",
            polity="BR",
        )
        source = cs.source("IBGE", "Malha de Bairros (Censo Demográfico) 2022")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="IBGE",
            title="Malha de Bairros (Censo Demográfico) 2022",
            url=PAGE,
            attribution="Fonte: IBGE, Censo Demográfico 2022",
            notes="IBGE draws bairros by adjusting census tracts, so edges can differ from the municipal laws. "
            "Municipalities with no legally instituted bairros, and Tocantins and the Federal District, have none.",
        )
        for state in STATES:
            cs.artifact(raw.cached(SOURCE, f"{FOLDER}{state}_bairros_CD2022.zip"), source)
        cs.add(
            "scheme", code="br.bairro", polity_id=cs.polity("BR"), kind="administrative", rank=3, noun="neighborhood"
        )
        municipalities = dict(
            conn.execute(
                """select e.value, a.id from area a join external_id e on e.entity_id = a.id
                   where a.scheme_code = 'br.municipality' and e.scheme = 'br.ibge.municipality'"""
            ).fetchall()
        )
        missing = sorted(set(frame["municipality"]) - set(municipalities))
        if missing:
            raise ValueError(f"no municipality area for IBGE codes {missing[:10]}")
        for code, municipality, label, geom, point in frame.sort("code").iter_rows():
            area = cs.area("br.bairro", code)
            cs.add("area", id=area, scheme_code="br.bairro")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=municipalities[municipality], valid_from=None)
            cs.add("name", entity_id=area, text=label, lang="pt", kind="official", is_preferred=True)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=source,
            )
        cs.add("boundary_set", scheme_code="br.bairro", vintage="2022", valid_from=None, valid_to=None, as_of=ELECTION)
        return cs.write()
