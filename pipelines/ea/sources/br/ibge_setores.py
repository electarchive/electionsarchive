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

SOURCE = "br.ibge_setores"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FOLDER = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
    "malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/setores/shp/UF/"
)
STATES = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]  # fmt: skip
PAGE = "https://www.ibge.gov.br/geociencias/organizacao-do-territorio/malhas-territoriais/15774-malhas.html"
ELECTION = date(2022, 10, 2)


def fetch() -> None:
    for state in STATES:
        raw.fetch("br.ibge_bairros", f"{FOLDER}{state}_setores_CD2022.zip", suffix=".zip")


def parse() -> None:
    rows: list[tuple[str, str, str, str, str]] = []
    with connect() as conn:
        conn.execute(
            "create temp table feature (code text, municipality text, label text, geojson text) on commit drop"
        )
        for state in STATES:
            with zipfile.ZipFile(raw.cached("br.ibge_bairros", f"{FOLDER}{state}_setores_CD2022.zip").path) as archive:
                stem = f"{state}_setores_CD2022"
                reader = shapefile.Reader(
                    shp=io.BytesIO(archive.read(f"{stem}.shp")),
                    shx=io.BytesIO(archive.read(f"{stem}.shx")),
                    dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                    encoding="utf-8",
                )
                records = reader.records()
                with_bairros = {str(record["CD_MUN"]) for record in records if record["CD_BAIRRO"]}
                with (
                    conn.cursor() as cursor,
                    cursor.copy("copy feature (code, municipality, label, geojson) from stdin") as copy,
                ):
                    for record, shape in zip(records, reader.iterShapes(), strict=True):
                        if str(record["CD_MUN"]) not in with_bairros:
                            copy.write_row(
                                (
                                    str(record["CD_SETOR"]),
                                    str(record["CD_MUN"]),
                                    str(record["NM_DIST"]),
                                    json.dumps(shape.__geo_interface__),
                                )
                            )
            rows.extend(
                conn.execute(
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
            )
            conn.execute("truncate feature")
    frame = pl.DataFrame(
        rows,
        schema={
            "code": pl.String,
            "municipality": pl.String,
            "district": pl.String,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    if frame["code"].n_unique() != frame.height:
        raise ValueError("a setor code has several shapes or districts")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "setores.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "setores.parquet").filter(pl.col("municipality") != "")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Brazil's census tracts (setores censitários) in municipalities with no bairros, from IBGE's 2022 census mesh",
            polity="BR",
        )
        source = cs.source("IBGE", "Malha de Setores Censitários 2022")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="IBGE",
            title="Malha de Setores Censitários 2022",
            url=PAGE,
            attribution="Source: IBGE, 2022 Census",
            notes="Loaded only for municipalities where IBGE's bairros mesh draws none, so polling places there can "
            "be placed in the finest official unit.",
        )
        for state in STATES:
            cs.artifact(raw.cached("br.ibge_bairros", f"{FOLDER}{state}_setores_CD2022.zip"), source)
        cs.add("scheme", code="br.setor", polity_id=cs.polity("BR"), kind="administrative", rank=4, noun="census tract")
        municipalities = dict(
            conn.execute(
                """select e.value, a.id from area a join external_id e on e.entity_id = a.id
                   where a.scheme_code = 'br.municipality' and e.scheme = 'br.ibge.municipality'"""
            ).fetchall()
        )
        missing = sorted(set(frame["municipality"]) - set(municipalities))
        if missing:
            raise ValueError(f"no municipality area for IBGE codes {missing[:10]}")
        for code, municipality, district, geom, point in frame.sort("code").iter_rows():
            area = cs.area("br.setor", code)
            cs.add("area", id=area, scheme_code="br.setor")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=municipalities[municipality], valid_from=None)
            cs.add("name", entity_id=area, text=f"{district} {code[-6:]}", lang="pt", kind="official", is_preferred=True)
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
        cs.add("boundary_set", scheme_code="br.setor", vintage="2022", valid_from=None, valid_to=None, as_of=ELECTION)
        return cs.write()
