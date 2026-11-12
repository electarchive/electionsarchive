import io
import zipfile
from datetime import date
from pathlib import Path
from typing import NamedTuple

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.br.tse import CLOSED, DAYS, FILES, FIRST
from ea.sources.br.tse import SOURCE as TSE
from ea.sources.br.tse_history import POLLING_PLACES
from ea.sources.br.tse_history import SOURCE as HISTORY
from ea.store import DATA, connect

SOURCE = "br.tse_places"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
COLUMNS = [
    "NR_TURNO",
    "SG_UF",
    "CD_MUNICIPIO",
    "NR_ZONA",
    "NR_SECAO",
    "NR_LOCAL_VOTACAO",
    "NM_LOCAL_VOTACAO",
    "NR_LATITUDE",
    "NR_LONGITUDE",
]


class Registry(NamedTuple):
    source: str
    url: str
    member: str
    opened: date
    closed: date


YEARS = {
    2022: Registry(TSE, FILES["places"], "eleitorado_local_votacao_2022.csv", DAYS[FIRST], CLOSED),
    2026: Registry(
        HISTORY, POLLING_PLACES[2026], "eleitorado_local_votacao_2026_BRASIL.csv", date(2026, 10, 4), date(2026, 10, 26)
    ),
}
BOUNDS = {"NR_LATITUDE": (-34.0, 6.0), "NR_LONGITUDE": (-74.5, -28.5)}


def parse() -> None:
    for year in YEARS:
        parse_year(year)


def parse_year(year: int) -> None:
    config = YEARS[year]
    with zipfile.ZipFile(raw.cached(config.source, config.url).path) as archive:
        registry = pl.read_csv(
            io.TextIOWrapper(archive.open(config.member), encoding="latin-1").read().encode(),
            separator=";",
            infer_schema=False,
            columns=COLUMNS,
        )
    sections = (
        registry.filter(pl.col("NR_TURNO") == "1", pl.col("SG_UF") != "ZZ")
        .with_columns(
            municipality=pl.col("CD_MUNICIPIO").str.zfill(5),
            latitude=pl.col("NR_LATITUDE").str.replace(",", ".").cast(pl.Float64),
            longitude=pl.col("NR_LONGITUDE").str.replace(",", ".").cast(pl.Float64),
        )
        .with_columns(
            section=pl.format("{}:{}:{}", "municipality", "NR_ZONA", "NR_SECAO"),
            place=pl.format("{}:{}:{}", "municipality", "NR_ZONA", "NR_LOCAL_VOTACAO"),
            located=pl.col("latitude").is_between(*BOUNDS["NR_LATITUDE"])
            & pl.col("longitude").is_between(*BOUNDS["NR_LONGITUDE"]),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    sections.select("section", "place").write_parquet(PARSED / f"sections_{year}.parquet")
    (
        sections.group_by("place")
        .agg(
            uf=pl.col("SG_UF").first(),
            municipality=pl.col("municipality").first(),
            label=pl.col("NM_LOCAL_VOTACAO").first(),
            longitude=pl.col("longitude").filter(pl.col("located")).median(),
            latitude=pl.col("latitude").filter(pl.col("located")).median(),
            points=pl.col("located").sum(),
        )
        .write_parquet(PARSED / f"places_{year}.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Brazil's 2022 and 2026 polling places, with each section's place and each place's bairro",
            polity="BR",
        )
        brazil = cs.polity("BR")
        cs.add("scheme", code="br.local_votacao", polity_id=brazil, kind="precinct", rank=3, noun="polling place")
        for year, config in YEARS.items():
            places = pl.read_parquet(PARSED / f"places_{year}.parquet")
            sections = pl.read_parquet(PARSED / f"sections_{year}.parquet")
            opened, closed = config.opened, config.closed
            source = cs.source("Tribunal Superior Eleitoral", f"Eleitorado - {year}")
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Tribunal Superior Eleitoral",
                title=f"Eleitorado - {year}",
                url=f"https://dadosabertos.tse.jus.br/dataset/eleitorado-{year}",
                license="Creative Commons Attribution",
                license_url="http://www.opendefinition.org/licenses/cc-by",
                notes="Only the place's name and coordinates are stored, never its address or telephone number.",
            )
            cs.artifact(raw.cached(config.source, config.url), source)
            existing = {
                code: (id, area)
                for code, id, area in conn.execute(
                    """select code, id, area_id from reporting_unit
                       where scheme_code = 'br.secao' and area_relation = 'within' and valid_from = %s""",
                    [opened],
                ).fetchall()
            }
            sections = sections.filter(pl.col("section").is_in(list(existing)))
            places = places.join(sections.select("place").unique(), on="place")
            place_ids = {code: str(cs.unit("br.local_votacao", code, opened)) for code in places["place"]}
            areas = {
                code: str(area)
                for code, area in conn.execute(
                    """select e.value, a.id from area a join external_id e on e.entity_id = a.id
                       where a.scheme_code = 'br.municipality' and e.scheme = 'br.tse.municipality'"""
                ).fetchall()
            }
            cs.frame(
                "reporting_unit",
                places.select(
                    id=pl.col("place").replace_strict(place_ids),
                    polity_id=pl.lit(str(brazil)),
                    scheme_code=pl.lit("br.local_votacao"),
                    code="place",
                    area_relation=pl.lit("within"),
                    area_id=pl.col("municipality").replace_strict(areas),
                    location=pl.when(pl.col("points") > 0).then(
                        pl.format("SRID=4326;POINT({} {})", "longitude", "latitude")
                    ),
                    kind=pl.lit("standard"),
                    valid_from=pl.lit(opened),
                    valid_to=pl.lit(closed),
                ),
            )
            cs.frame(
                "name",
                places.select(
                    entity_id=pl.col("place").replace_strict(place_ids),
                    text="label",
                    lang=pl.lit("pt"),
                    kind=pl.lit("official"),
                    is_preferred=pl.lit(True),
                ),
            )
            rows = conn.execute(
                """select id::text, polity_id::text, scheme_code, code, area_relation::text, area_id::text, ST_AsEWKT(location), kind::text,
                          valid_from, valid_to
                   from reporting_unit
                   where scheme_code = 'br.secao' and area_relation = 'within' and valid_from = %s and code = any(%s)""",
                [opened, sections["section"].to_list()],
            ).fetchall()
            cs.frame(
                "reporting_unit",
                pl.DataFrame(
                    rows,
                    schema={
                        "id": pl.String,
                        "polity_id": pl.String,
                        "scheme_code": pl.String,
                        "code": pl.String,
                        "area_relation": pl.String,
                        "area_id": pl.String,
                        "location": pl.String,
                        "kind": pl.String,
                        "valid_from": pl.Date,
                        "valid_to": pl.Date,
                    },
                    orient="row",
                )
                .join(sections, left_on="code", right_on="section")
                .with_columns(parent_id=pl.col("place").replace_strict(place_ids))
                .drop("place"),
            )
            conn.execute("drop table if exists located")
            conn.execute("create temp table located (unit_id uuid, point geometry(Point, 4326)) on commit drop")
            with conn.cursor() as cursor, cursor.copy("copy located from stdin") as copy:
                for place, longitude, latitude in (
                    places.filter(pl.col("points") > 0).select("place", "longitude", "latitude").iter_rows()
                ):
                    copy.write_row((place_ids[place], f"SRID=4326;POINT({longitude} {latitude})"))
            memberships = conn.execute(
                """select l.unit_id, min(a.id::text)::uuid
                   from located l
                   join area_geometry g on g.geom && l.point and ST_Covers(g.geom, l.point)
                   join area a on a.id = g.area_id and a.scheme_code in ('br.bairro', 'br.setor')
                   group by l.unit_id"""
            ).fetchall()
            cs.frame(
                "reporting_unit_area",
                pl.DataFrame(
                    [(str(unit), str(area), opened, closed, True, "point_in_polygon") for unit, area in memberships],
                    schema={
                        "unit_id": pl.String,
                        "area_id": pl.String,
                        "valid_from": pl.Date,
                        "valid_to": pl.Date,
                        "approximate": pl.Boolean,
                        "method": pl.String,
                    },
                    orient="row",
                ),
            )
        return cs.write()
