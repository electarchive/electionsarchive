import json

from ea.store import ROOT

REGISTRY = json.loads((ROOT / "packages" / "db" / "src" / "projections.json").read_text())


def projection(scheme: str) -> str:
    return REGISTRY["schemes"].get(scheme.split(".")[0], REGISTRY["fallback"])


def sql(column: str, scheme: str) -> str:
    entry = REGISTRY["projections"][projection(scheme)]

    def inside(boxes: list[list[float]]) -> str:
        return " or ".join(f"(q.x between {w} and {e} and q.y between {s} and {n})" for w, s, e, n in boxes)

    cases = []
    for inset in entry.get("insets", []):
        part = "d.geom"
        if "clip" in inset:
            w, s, e, n = inset["clip"]
            part = f"ST_ClipByBox2D(d.geom, ST_MakeEnvelope({w}, {s}, {e}, {n}, 4326))"
        scale, x, y = inset["affine"]
        cases.append(
            f"when {inside(inset['within'])} then "
            f"ST_SetSRID(ST_Affine(ST_Transform({part}, {inset['srid']}), {scale}, 0, 0, {scale}, {x}, {y}), 3857)"
        )
    cases.append(f"when {inside(entry['within'])} then ST_SetSRID(ST_Transform(d.geom, {entry['srid']}), 3857)")
    return (
        f"(select ST_Collect(case {' '.join(cases)} end) from ST_Dump({column}) d"
        " cross join lateral (select (ST_XMin(d.geom) + ST_XMax(d.geom)) / 2 as x,"
        " (ST_YMin(d.geom) + ST_YMax(d.geom)) / 2 as y) q)"
    )
