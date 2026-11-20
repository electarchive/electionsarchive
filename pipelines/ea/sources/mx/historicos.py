import io
import re
import zipfile
from datetime import date
from uuid import uuid5

import numpy
import polars as pl
import pyproj
import shapefile
import shapely
import shapely.geometry
from access_parser import AccessParser

from ea import package, raw
from ea.package import Package
from ea.sources.mx.mge import SHORT, STATES, ordinal
from ea.store import DATA

SOURCE = "mx.historicos"
MAPOTECA = "https://cartografia.ine.mx/mapoteca/"
HISTORICO = MAPOTECA + "historicos/HISTORICO_SECC_ELECT_PEF_1994%20-%20AGOSTO2026_11SEPT2026.accdb"
PLAN_2017 = MAPOTECA + "distritacion2017/federal/{0:02d}/{0:02d}.zip"
DISTRICTING = MAPOTECA + "geoelect/D2004-05C/MexicanElectoralDistricting.pdf"
TABLE = "HISTORICO_SECC_ELECT_PEF1994 - AGOSTO2026"
INE = "Instituto Nacional Electoral (INE)"
INE_LICENSE = "Public information INE publishes for download (Ley General de Transparencia, art. 70); no license stated"
PORTAL = "https://cartografia.ine.mx/sige8/"
TOLERANCE = 10.0
GRID = 0.01
# Each plan reads its sections from the columns of the federal elections it served, latest first, so a section that
# an earlier election lacks (one INE removed before it) still falls in the plan.
PLANS = {
    "1996": (date(1996, 7, 31), date(2005, 2, 11), ("Distrito_PEF2003", "Distrito_PEF2000", "Distrito_PEF1997")),
    "2005": (
        date(2005, 2, 11),
        date(2017, 3, 15),
        ("Distrito_PEF2015", "Distrito_PEF2012", "Distrito_PEF2009", "Distrito_PEF2006"),
    ),
}
SCHEME = "mx.distrito_federal"


def fetch() -> None:
    raw.fetch(SOURCE, HISTORICO, suffix=".accdb")
    raw.fetch(SOURCE, DISTRICTING, suffix=".pdf")
    for entidad in range(1, 33):
        raw.fetch(SOURCE, PLAN_2017.format(entidad), suffix=".zip")


def packages() -> list[str]:
    return [f"{SOURCE}/geo"]


def parse() -> None:
    geo().write()


def emit() -> None:
    package.run("load", packages())


def sections() -> pl.DataFrame:
    table = AccessParser(str(raw.cached(SOURCE, HISTORICO).path)).parse_table(TABLE)
    columns = ["Cve_Ent", "Seccion", *(c for _, _, cols in PLANS.values() for c in cols)]
    return pl.DataFrame({c: table[c] for c in columns}, infer_schema_length=None)


def assigned(frame: pl.DataFrame, plan: str) -> dict[tuple[int, int], int]:
    """Section to district for one plan. A section the table lists twice, because INE split it between two districts,
    has no single shape and stays out."""
    rows = frame.with_columns(pl.coalesce(PLANS[plan][2]).alias("district")).filter(pl.col("district").is_not_null())
    twice = rows.group_by("Cve_Ent", "Seccion").len().filter(pl.col("len") > 1)
    rows = rows.join(twice, on=["Cve_Ent", "Seccion"], how="anti")
    return {(int(e), int(s)): int(d) for e, s, d in rows.select("Cve_Ent", "Seccion", "district").iter_rows()}


def outlines(entidad: int) -> tuple[dict[int, shapely.Geometry], int]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, PLAN_2017.format(entidad)).path)
    members = {name.upper(): name for name in archive.namelist()}
    stem = next(name[: -len("SECCION.SHP")] for name in members if name.endswith("SECCION.SHP"))
    parts = {ext: io.BytesIO(archive.read(members[f"{stem.upper()}SECCION.{ext}"])) for ext in ("SHP", "SHX", "DBF")}
    zone = re.search(r"UTM_Zone_(\d+)N", archive.read(members[f"{stem.upper()}SECCION.PRJ"]).decode())
    if zone is None:
        raise ValueError(f"{entidad}: unexpected projection")
    reader = shapefile.Reader(shp=parts["SHP"], shx=parts["SHX"], dbf=parts["DBF"], encoding="cp1252")
    pieces: dict[int, list[shapely.Geometry]] = {}
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            raise ValueError(f"{entidad}: empty section record")
        fields = record.as_dict()
        state, number = fields["entidad"], fields["seccion"]
        if not isinstance(state, int | float) or not isinstance(number, int | float) or int(state) != entidad:
            raise ValueError(f"{entidad}: section {number} lies in {state}")
        pieces.setdefault(int(number), []).append(shapely.make_valid(shapely.geometry.shape(shape)))
    return {number: shapely.union_all(shapes, grid_size=GRID) for number, shapes in pieces.items()}, 32600 + int(
        zone[1]
    )


def fill(union: dict[int, shapely.Geometry]) -> tuple[dict[int, shapely.Geometry], float]:
    """Close each gap that one district wholly encloses, which only a section missing from the 2017 outlines can
    leave, taking back any enclave another district holds inside it. Gaps along a border between districts stay."""
    solid = {
        d: shapely.union_all(
            [shapely.Polygon(part.exterior) for part in shapely.get_parts(shape) if shapely.get_type_id(part) == 3]
        )
        for d, shape in union.items()
    }
    closed = {}
    for d, shape in solid.items():
        others = [other for e, other in union.items() if e != d and other.intersects(shape)]
        closed[d] = shapely.difference(shape, shapely.union_all(others), grid_size=GRID) if others else shape
    return closed, sum(closed[d].area - union[d].area for d in union)


def districts(
    frame: pl.DataFrame, plan: str
) -> tuple[dict[str, shapely.Geometry], dict[str, tuple[int, int, float, float]]]:
    members = assigned(frame, plan)
    found: dict[str, shapely.Geometry] = {}
    covered: dict[str, tuple[int, int, float, float]] = {}
    for entidad in range(1, 33):
        shapes, epsg = outlines(entidad)
        project = pyproj.Transformer.from_crs(epsg, 4326, always_xy=True)
        wanted = {number: district for (state, number), district in members.items() if state == entidad}
        grouped: dict[int, list[shapely.Geometry]] = {}
        for number, district in wanted.items():
            if number in shapes:
                grouped.setdefault(district, []).append(shapes[number])
        if set(grouped) != set(wanted.values()):
            raise ValueError(f"{plan} {entidad}: districts {sorted(set(wanted.values()) - set(grouped))} have no shape")
        union = {d: shapely.union_all(parts, grid_size=GRID) for d, parts in sorted(grouped.items())}
        union, enclosed = fill(union)
        simple = shapely.coverage_simplify(numpy.array(list(union.values())), TOLERANCE)
        total = sum(shape.area for shape in shapes.values())
        covered[f"{entidad:02d}"] = (
            len(wanted),
            sum(number in shapes for number in wanted),
            (sum(shape.area for shape in union.values()) - enclosed) / total,
            enclosed / total,
        )
        for district, shape in zip(union, simple, strict=True):
            moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
            polygons = [
                part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)
            ]
            found[f"{entidad:02d}{district:02d}"] = shapely.set_srid(
                shapely.MultiPolygon(list(shapely.get_parts(polygons))), 4326
            )
    if len(found) != 300:
        raise ValueError(f"{plan}: {len(found)} districts, expected 300")
    return found, covered


def hexed(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shape, hex=True, include_srid=True)


def reference() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Codes to ids for the federal districts, entities, and entity names production already holds."""
    folder = DATA / "reference" / "mx"
    areas = pl.read_parquet(folder / "area.parquet", columns=["id", "scheme_code", "name"])
    codes = pl.read_parquet(folder / "area_code.parquet", columns=["area_id", "code"])
    held = areas.join(codes, left_on="id", right_on="area_id")
    found = [
        {row["code"]: row["id"] for row in held.filter(pl.col("scheme_code") == scheme).iter_rows(named=True)}
        for scheme in (SCHEME, "mx.entidad")
    ]
    labels = {
        row["code"][:2]: row["name"].split(" de ", 1)[1]
        for row in held.filter(pl.col("scheme_code") == SCHEME).iter_rows(named=True)
    }
    return found[0], found[1], labels


def geo() -> Package:
    frame = sections()
    held, entities, labels = reference()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Federal electoral district shapes of the 1996 and 2005 districting plans (Chamber of Deputies elections "
        "1997-2015), rebuilt from INE's per-section district assignments for each federal election",
    )
    source = p.id("source", "historicos")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=INE,
        title=(
            "Histórico de secciones electorales por distrito federal, elecciones 1994-2026, with the 2017 "
            "section outlines of the Marco Geográfico Electoral"
        ),
        url=PORTAL,
        license=INE_LICENSE,
        license_url=PORTAL,
        attribution="Instituto Nacional Electoral, Marco Geográfico Electoral",
        notes=(
            "INE publishes no outline of the 1996 or 2005 federal districts. Each outline here is the union of the "
            "2017 outlines of the sections INE's historical table assigns to the district under that plan, so it is "
            "a reconstruction: a section INE removed before 2017, or created after the plan, has no outline and "
            "leaves a gap, and a section INE split between two districts is left out."
        ),
    )
    p.artifact(raw.cached(SOURCE, HISTORICO), source)
    p.artifact(raw.cached(SOURCE, DISTRICTING), source)
    for entidad in range(1, 33):
        p.artifact(raw.cached(SOURCE, PLAN_2017.format(entidad)), source)
    p.external |= {SCHEME, *held.values(), *entities.values()}
    shapes: dict[str, dict[str, shapely.Geometry]] = {}
    gaps: list[str] = []
    for plan, (begin, end, _) in PLANS.items():
        shapes[plan], covered = districts(frame, plan)
        listed = sum(n for n, *_ in covered.values())
        shaped = sum(n for _, n, *_ in covered.values())
        worst = min(covered.items(), key=lambda item: item[1][2] + item[1][3])
        share = sum(c[2] for c in covered.values()) / len(covered)
        gaps.append(
            f"{plan} plan: {shaped:,} of {listed:,} sections have an outline; the least covered entity is "
            f"{worst[0]}, where the outlines and the gaps they enclose cover "
            f"{worst[1][2] + worst[1][3]:.1%} of its 2017 area (mean over entities {share:.1%} before closing gaps)"
        )
        p.add(
            "boundary_set",
            id=p.id("boundary_set", SCHEME, plan),
            scheme_code=SCHEME,
            vintage=plan,
            valid_from=begin,
            valid_to=end,
            as_of=begin,
        )
    print("\n".join(gaps), flush=True)
    span = {code: [plan for plan in PLANS if code in shapes[plan]] for code in sorted(set().union(*shapes.values()))}
    for code, plans in span.items():
        if code in held:
            area = held[code]
            if area != str(uuid5(package.NAMESPACE, f"area:{SCHEME}@None\x1f{code}")):
                raise ValueError(f"{code}: production holds an id the loader would not mint")
        else:
            area = held[code] = str(uuid5(package.NAMESPACE, f"area:{SCHEME}@None\x1f{code}"))
            begin, end = PLANS[plans[0]][0], PLANS[plans[-1]][1]
            number = int(code[2:])
            iso, english, _ = STATES[int(code[:2]) - 1]
            p.add("area", id=area, scheme_code=SCHEME)
            p.add("area_code", area_id=area, code=code, valid_from=begin, valid_to=end)
            p.add("area_parent", area_id=area, parent_id=entities[code[:2]], valid_from=begin, valid_to=end)
            p.name(area, f"Distrito {number} de {SHORT.get(iso, labels[code[:2]])}", "es", "official")
            p.name(area, f"{english} {ordinal(number)} district", "en", "english", preferred=False)
        for plan in plans:
            shape = shapes[plan][code]
            p.add(
                "area_geometry",
                area_id=area,
                valid_from=PLANS[plan][0],
                valid_to=PLANS[plan][1],
                geom=hexed(shape),
                label_point=hexed(shapely.set_srid(shapely.point_on_surface(shape), 4326)),
                quality="synthetic",
                source_id=source,
            )
    return p
