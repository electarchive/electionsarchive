import io
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, sld_shapes
from ea.store import DATA, connect

SOURCE = "us.sld_shapes_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "TIGER/Line Shapefiles, 2010 State Legislative Districts"
STATES = {
    state: (abbr, date(1992, 1, 1), date(2002, 1, 1)) for state, abbr in (("25", "MA"), ("50", "VT"), ("33", "NH"))
}
VT_HOUSE = {
    "A": "addison",
    "AR": "addisonrutland",
    "B": "bennington",
    "BR": "benningtonrutland",
    "C": "chittenden",
    "CA": "caledonia",
    "CAW": "caledoniawashington",
    "EC": "essexcaledonia",
    "F": "franklin",
    "FGI": "franklingrandisle",
    "GI": "grandisle",
    "L": "lamoille",
    "OG": "orange",
    "OGC": "orangecaledonia",
    "OL": "orleans",
    "OLC": "orleanscaledonia",
    "OLF": "orleansfranklin",
    "R": "rutland",
    "RB": "rutlandbennington",
    "RY": "rutlandwindsor",
    "W": "washington",
    "WA": "washingtonaddison",
    "WC": "washingtonchittenden",
    "WL": "washingtonlamoille",
    "X": "windham",
    "XB": "windhambennington",
    "Y": "windsor",
    "YOG": "windsororange",
    "YR": "windsorrutland",
}
VT_SENATE = {
    "ADD": "addison",
    "BEN": "bennington",
    "CAL": "caledonia",
    "CGI": "grandisle",
    "CHI": "chittenden",
    "E-O": "essexorleans",
    "FRA": "franklin",
    "LAM": "lamoille",
    "ORA": "orange",
    "RUT": "rutland",
    "WAS": "washington",
    "WDM": "windham",
    "WSR": "windsor",
}


def fetch() -> None:
    sld_shapes.fetch()


def parse() -> None:
    frame = pl.concat(
        pl.read_parquet(sld_shapes.PARSED / f"2000_{kind}.parquet").with_columns(kind=pl.lit(kind))
        for kind in ("SLDL", "SLDU")
    ).filter(pl.col("state").is_in(list(STATES)))
    with zipfile.ZipFile(raw.cached("us.ma_leg", elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False)
    gazetteer = gazetteer.rename({column: column.strip() for column in gazetteer.columns}).filter(
        pl.col("USPS") == "MA"
    )
    points: dict[str, tuple[float, float]] = {}
    for name, lat, lon in gazetteer.select("NAME", "INTPTLAT", "INTPTLONG").iter_rows():
        points.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), (float(lon), float(lat)))
    matches: list[tuple[str, str, str]] = []
    with connect() as conn:
        areas = {
            (scheme, state, code.split(":", 1)[1])
            for scheme, state, code in conn.execute(
                """select a.scheme_code, split_part(ac.code, ':', 1), ac.code from area a
                   join area_code ac on ac.area_id = a.id where a.scheme_code in ('us.sldl', 'us.sldu')
                   and split_part(ac.code, ':', 1) in ('MA', 'VT', 'NH')"""
            ).fetchall()
        }
        for (fips, kind), group in frame.group_by("state", "kind"):
            abbr = STATES[fips][0]
            scheme = f"us.{kind.lower()}"
            if abbr == "NH" and kind == "SLDU":
                labels = {st: str(int(st.removeprefix("S"))) for st in group["st"]}
            elif abbr == "VT" and kind == "SLDU":
                labels = {st: VT_SENATE[st] for st in group["st"] if st in VT_SENATE}
            elif abbr == "VT":
                labels = {}
                for st in group["st"]:
                    found = re.fullmatch(r"([A-Z]+)-?(\d)?(\d)?", st)
                    if st == "710":
                        labels[st] = "chittenden-7-j"
                    elif found and found[1] in VT_HOUSE:
                        number = f"-{found[2] or 1}" + (f"-{chr(96 + int(found[3]))}" if found[3] else "")
                        options = [VT_HOUSE[found[1]] + number, VT_HOUSE[found[1]] + number + "-a", VT_HOUSE[found[1]]]
                        labels[st] = next((o for o in options if (scheme, abbr, o) in areas), options[0])
            elif abbr == "MA":
                chamber = "house" if kind == "SLDL" else "senate"
                every = sorted(
                    {
                        value.split(":")[3]
                        for (value,) in conn.execute(
                            "select value from external_id where scheme = 'us.contest' and value ~ %s",
                            [f"^2000-11-07:MA:{chamber}:[0-9a-z-]+:g$"],
                        ).fetchall()
                    },
                    key=lambda label: (
                        re.sub(r"-\d+$", "", label),
                        int(m[1]) if (m := re.search(r"-(\d+)$", label)) else 0,
                    ),
                )
                codes = sorted(group["st"], key=int)
                if chamber == "house":
                    labels = dict(zip(codes, every, strict=True))
                else:
                    towns: dict[str, set[str]] = {}
                    for label, units in conn.execute(
                        """select split_part(x.value, ':', 4), array_agg(distinct split_part(u.code, '::', 2))
                           from external_id x join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                           join result_value v on v.result_set_k = r.k
                           join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                           where x.scheme = 'us.contest' and x.value ~ '^2000-11-07:MA:senate:[0-9a-z-]+:g$'
                           group by 1"""
                    ).fetchall():
                        towns[label] = set()
                        for unit in units:
                            town = re.sub(r"(\s+(ward|pct|precinct|\d+[a-z]?|[a-z]{1,2}))+$", "", unit.lower())
                            for short, long in (("n", "north"), ("s", "south"), ("e", "east"), ("w", "west")):
                                town = re.sub(rf"^{short}\.? ", f"{long} ", town)
                            towns[label].add(elstats.plain(town))
                    conn.execute("create temp table if not exists ma (code text, geom geometry)")
                    conn.execute("truncate ma")
                    for st, geom in group.select("st", "geom").iter_rows():
                        conn.execute(
                            "insert into ma values (%s, st_setsrid(st_geomfromewkb(decode(%s, 'hex')), 4326))",
                            [st, geom],
                        )
                    inside: dict[str, set[str]] = {code: set() for code in codes}
                    for town, (lon, lat) in points.items():
                        for (code,) in conn.execute(
                            "select code from ma where st_contains(geom, st_setsrid(st_point(%s, %s), 4326))",
                            [lon, lat],
                        ).fetchall():
                            inside[code].add(town)
                    labels = {}
                    for strict in (True, False):
                        scores = sorted(
                            (
                                len(got & have) / (len(got | have) if strict else len(got)),
                                code,
                                label,
                            )
                            for code, got in inside.items()
                            for label, have in towns.items()
                            if got and code not in labels and label not in labels.values()
                        )
                        for score, code, label in reversed(scores):
                            if score >= 0.5 and code not in labels and label not in labels.values():
                                labels[code] = label
                    left = [code for code in codes if code not in labels]
                    rest = [label for label in every if label not in labels.values()]
                    if len(left) == 1 and len(rest) == 1:
                        labels[left[0]] = rest[0]
            else:
                continue
            for st, label in labels.items():
                if (scheme, abbr, label) in areas:
                    matches.append((fips, kind, st, label))
                else:
                    print(f"{abbr} {kind} {st}: no area {label}")
    PARSED.mkdir(parents=True, exist_ok=True)
    keyed = pl.DataFrame(matches, schema=["state", "kind", "st", "label"], orient="row")
    out = frame.join(keyed, on=["state", "kind", "st"])
    out.write_parquet(PARSED / "matches.parquet")
    print(out.group_by("state", "kind").len().sort("state", "kind"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "matches.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Massachusetts, Vermont, and New Hampshire Senate district shapes for the 1990s plans from the Census 2000 state legislative districts",
            polity="US",
        )
        source = cs.source(sld_shapes.PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=sld_shapes.PUBLISHER,
                title=TITLE,
                url=f"{sld_shapes.TIGER}TIGER2010/",
                license="Public domain",
            )
        for url in sorted(frame["url"].unique()):
            cs.artifact(raw.cached(sld_shapes.SOURCE, url), source)
        areas = {
            (scheme, code): area
            for area, scheme, code in conn.execute(
                """select a.id, a.scheme_code, ac.code from area a join area_code ac on ac.area_id = a.id
                   where a.scheme_code in ('us.sldl', 'us.sldu') and split_part(ac.code, ':', 1) in ('MA', 'VT', 'NH')"""
            ).fetchall()
        }
        rows = []
        for row in frame.iter_rows(named=True):
            abbr, begin, end = STATES[row["state"]]
            area = areas[(f"us.{row['kind'].lower()}", f"{abbr}:{row['label']}")]
            taken = conn.execute(
                "select 1 from area_geometry where area_id = %s and valid && daterange(%s, %s)", [area, begin, end]
            ).fetchone()
            if taken:
                continue
            rows.append(
                {
                    "area_id": str(area),
                    "valid_from": begin,
                    "valid_from_precision": "year",
                    "valid_to": end,
                    "valid_to_precision": "year",
                    "geom": row["geom"],
                    "label_point": row["point"],
                    "source_id": str(source),
                }
            )
        out = pl.DataFrame(rows).with_columns(quality=pl.lit("official"))
        cs.frame("area_geometry", out)
        print(f"{out.height} shapes")
        return cs.write()
