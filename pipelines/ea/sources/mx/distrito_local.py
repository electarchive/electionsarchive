import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.sources.mx import mge
from ea.store import DATA, connect

SOURCE = "mx.distrito_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE


def fetch() -> None:
    mge.fetch()


def records(entidad: int, name: str) -> list[dict]:
    with tempfile.TemporaryDirectory(dir=DATA / "tmp") as folder:
        subprocess.run(
            ["7z", "e", "-y", f"-o{folder}", str(raw.cached(mge.SOURCE, mge.bgd_url(entidad)).path), f"*/{name}.*"],
            check=True,
            capture_output=True,
        )
        path = next(p for p in Path(folder).iterdir() if p.suffix.lower() == ".dbf")
        reader = shapefile.Reader(dbf=path.open("rb"))
        return [record.as_dict() for record in reader.iterRecords() if record is not None]


def parse() -> None:
    sections: list[tuple[str, str]] = []
    expected: dict[str, int] = {}
    for entidad in range(1, 33):
        state = f"{entidad:02d}"
        for fields in records(entidad, "SECCION"):
            if mge.number(fields["entidad"]) != entidad:
                raise ValueError(f"{state}: section {fields['seccion']} in {fields['entidad']}")
            sections.append(
                (f"{state}{mge.number(fields['seccion']):04d}", f"{state}{mge.number(fields['distrito_l']):02d}")
            )
        districts = {mge.number(fields["distrito_l"]) for fields in records(entidad, "DISTRITO_LOCAL")}
        if districts != set(range(1, len(districts) + 1)):
            raise ValueError(f"{state}: local districts {sorted(districts)}")
        expected[state] = len(districts)
    frame = pl.DataFrame(sections, schema=["seccion", "distrito"], orient="row")
    if frame["seccion"].n_unique() != frame.height:
        raise ValueError("a section appears twice")
    with connect() as conn:
        conn.execute("create temp table member (seccion text, distrito text) on commit drop")
        conn.execute(
            "insert into member select * from unnest(%s::text[], %s::text[])",
            [frame["seccion"].to_list(), frame["distrito"].to_list()],
        )
        rows = conn.execute(
            """select m.distrito, count(*), count(g.geom),
                      encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(
                        st_union(st_transform(g.geom, 6372), 0.01), 4326)), 3))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(st_union(st_transform(g.geom, 6372), 0.01)), 4326)), 'hex')
               from member m
               left join (select c.code, g.geom from area a join area_code c on c.area_id = a.id
                          join area_geometry g on g.area_id = a.id and upper_inf(g.valid)
                          where a.scheme_code = 'mx.seccion') g on g.code = m.seccion
               group by 1 order by 1"""
        ).fetchall()
    districts = pl.DataFrame(rows, schema=["code", "sections", "shapes", "geom", "label_point"], orient="row")
    short = districts.filter(pl.col("sections") != pl.col("shapes"))
    if short.height:
        raise ValueError(f"sections without a current shape: {short.select('code', 'sections', 'shapes').rows()}")
    counts = dict(districts.group_by(pl.col("code").str.slice(0, 2)).len().iter_rows())
    if counts != expected:
        raise ValueError(f"district counts {counts} differ from the DISTRITO_LOCAL layers {expected}")
    PARSED.mkdir(parents=True, exist_ok=True)
    districts.drop("shapes").write_parquet(PARSED / "districts.parquet")
    frame.write_parquet(PARSED / "sections.parquet")
    print({state: counts[state] for state in sorted(counts)}, sum(counts.values()), frame.height)


def emit() -> Path:
    districts = pl.read_parquet(PARSED / "districts.parquet")
    sections = pl.read_parquet(PARSED / "sections.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Mexico's {districts.height} local electoral districts, the state legislatures' plurality "
            "districts, from INE's Marco Geográfico Electoral",
            polity="MX",
        )
        if conn.execute("select 1 from scheme where code = 'mx.seccion'").fetchone() is None:
            raise ValueError("publish mx.mge before mx.distrito_local")
        bgd = cs.source(mge.INE, mge.BGD_TITLE)
        mexico = cs.polity("MX")
        cs.add(
            "scheme",
            code="mx.distrito_local",
            polity_id=mexico,
            kind="electoral_district",
            rank=2,
            noun="local electoral district",
            code_system="ine",
            kinds=None,
        )
        states = {
            code: text
            for code, text in conn.execute(
                "select c.code, n.text from area a join area_code c on c.area_id = a.id "
                "join name n on n.entity_id = a.id and n.is_preferred where a.scheme_code = 'mx.entidad'"
            ).fetchall()
        }
        section_ids = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
            ).fetchall()
        )

        def name(entity: UUID, text: str, lang: str, kind: str) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
                source_id=bgd,
            )

        areas: dict[str, UUID] = {}
        for row in districts.iter_rows(named=True):
            code = row["code"]
            iso, english, _ = mge.STATES[int(code[:2]) - 1]
            number = int(code[2:])
            area = areas[code] = cs.area("mx.distrito_local", code)
            cs.add("area", id=area, scheme_code="mx.distrito_local")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("mx.entidad", code[:2]), valid_from=None)
            name(area, f"Distrito local {number} de {mge.SHORT.get(iso, states[code[:2]])}", "es", "official")
            name(area, f"{english} {mge.ordinal(number)} local district", "en", "english")
            cs.add(
                "area_geometry",
                area_id=area,
                geom=row["geom"],
                label_point=row["label_point"],
                quality="official",
                source_id=bgd,
                valid_from=None,
                valid_to=None,
            )
        for seccion, distrito in sections.iter_rows():
            cs.add(
                "area_membership",
                area_id=section_ids[seccion],
                parent_id=areas[distrito],
                valid_from=None,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
            )
        cs.add(
            "boundary_set",
            scheme_code="mx.distrito_local",
            vintage="2025-12",
            valid_from=None,
            valid_to=None,
            as_of=mge.CUT,
        )
        return cs.write()
