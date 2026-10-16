import difflib
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ca.taylor"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SHAPES = "https://borealisdata.ca/api/access/datafile/449029"
TABLE = "https://borealisdata.ca/api/access/datafile/449032"
TOLERANCE = 25
LAST = date(2004, 5, 23)
NORTH = {"47", "48", "60", "61", "62"}
STOP = r"\b(the|of|de|du|des|la|le|les|city|county|comte|riding|district|provisional|town)\b"
PINS = {
    ("Q136513610", 1914): "Westminster District",
    ("Q134593613", 1966): "Lanark-Renfrew-Carleton",
    ("Q134593603", 1966): "Huron",
    ("Q134593641", 1966): "Peel South",
    ("Q3357766", 1966): "Ottawa East",
    ("Q3109255", 1987): "Acadie-Bathurst",
    ("Q134593579", 1987): "Laval-Est (East)",
    ("Q134593615", 1987): "Québec",
    ("Q136528687", 1987): "Laval-Ouest (West)",
    ("Q134593620", 1987): "Laval-Centre (Center)",
    ("Q134593535", 1987): "Bramalea-Gore-Malton",
    ("Q134593561", 1996): "New Brunswick Southwest",
    ("Q134593528", 1996): "Beauport-Montmorency-Côte-de-Beaupré-Île-d'Orléans",
    ("Q134593701", 1996): "Rivière-des-Mille-Îles",
    ("Q134593539", 1996): "Toronto-Danforth",
    ("Q134593548", 1996): "Ottawa-Orléans",
    ("Q134593478", 1996): "Thunder Bay-Superior North",
    ("Q134593494", 1996): "Ancaster-Dundas-Flamborough-Aldershot",
    ("Q134593555", 1996): "Charleswood St. James-Assiniboia",
    ("Q108412872", 1996): "Port Moody-Coquitlam-Port Coquitlam",
    ("Q134593495", 1996): "Kootenay-Boundary-Okanagan",
}
COMPASS = (("nord", "north"), ("sud", "south"), ("est", "east"), ("ouest", "west"), ("centre", "center"))


def forms(text: str) -> set[str]:
    text = re.sub(r"\([^)]*\)", " ", unicodedata.normalize("NFC", text)).lower().replace("&", " and ")
    text = re.sub(r"['’`]", "", re.sub(r"[‐-―-]+", " ", text))
    texts = [text]
    if (stripped := re.sub(r"^\s*(toronto|montr\S*al)\b\s*", "", text)) != text:
        texts.append(stripped)
    out = set()
    for text in texts:
        for variant in (
            re.sub(r"[^\x00-\x7f]", "", text),
            unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode(),
        ):
            variant = re.sub(STOP, " ", re.sub(r"\bsaint\b|\bste?\b", "st", variant))
            for french, english in COMPASS:
                variant = re.sub(rf"\b{french}\b", english, variant)
            out.add(" ".join(sorted(re.sub(r"[^a-z0-9]+", " ", variant).split())))
    return out


def fetch() -> None:
    raw.fetch(SOURCE, TABLE, suffix=".tab")
    raw.fetch(SOURCE, SHAPES, suffix=".zip")


def parse() -> None:
    table = pl.read_csv(raw.cached(SOURCE, TABLE).path, separator="\t", infer_schema=False)
    times = sorted({int(time) for time in table["time"]})
    districts = defaultdict(list)
    for geosid, time, province, name in table.select("geosid", "time", "prcode", "geoname").rows():
        districts[int(time), str(int(float(province)))].append((geosid, name, forms(re.sub(r"[^\x00-\x7f]", "", name))))
    with connect() as conn:
        areas = conn.execute(
            """select a.id, ac.code, ac.valid_from, ac.valid_to, pc.code, array_agg(distinct n.text),
                      (select array_agg(distinct e.date_from) from contest c join election e on e.id = c.election_id
                       where c.district_area_id = a.id)
               from area a join area_code ac on ac.area_id = a.id
               join area_parent ap on ap.area_id = a.id
               join area pa on pa.id = ap.parent_id and pa.scheme_code = 'ca.province'
               join area_code pc on pc.area_id = pa.id
               join name n on n.entity_id = a.id
               where a.scheme_code = 'ca.fed' and ac.valid_to <= %s
               group by 1, 2, 3, 4, 5""",
            [LAST],
        ).fetchall()
        hits, pending = {}, []
        claims = defaultdict(set)
        for area, qid, start, end, province, names, days in areas:
            time = max(t for t in times if t <= start.year)
            entry = (area, qid, time, province, sorted(names), set(days or [start]))
            pools = [districts[time, province]]
            if province in NORTH:
                pools.append([d for other in sorted(NORTH - {province}) for d in districts[time, other]])
            wanted = set().union(*(forms(name) for name in names))
            if (qid, time) in PINS:
                (hits[area],) = {(time, geosid) for geosid, _, found in pools[0] if found & forms(PINS[qid, time])}
                claims[hits[area]] |= entry[5]
                continue
            for pool in pools:
                if len(found := {geosid for geosid, _, found in pool if found & wanted}) == 1:
                    hits[area] = (time, found.pop())
                    claims[hits[area]] |= entry[5]
                    break
            else:
                pending.append((*entry, wanted))
        compass = {english for _, english in COMPASS}
        unmatched = defaultdict(list)
        for area, qid, time, province, names, days, wanted in pending:
            pool = [d for d in districts[time, province] if not claims[time, d[0]] & days]
            score, best = max(
                (
                    (max(difflib.SequenceMatcher(None, w, f).ratio() for w in wanted for f in found), geosid)
                    for geosid, _, found in pool
                ),
                default=(0.0, None),
            )
            found = next((found for geosid, _, found in pool if geosid == best), set())
            subset = {
                geosid
                for geosid, _, found in pool
                if any(
                    w and f and (set(w.split()) < set(f.split()) or set(f.split()) < set(w.split()))
                    for w in wanted
                    for f in found
                )
            }
            if score >= 0.85 and all(
                compass & set(w.split()) == compass & set(f.split())
                for w in wanted
                for f in found
                if difflib.SequenceMatcher(None, w, f).ratio() == score
            ):
                hits[area] = (time, best)
            elif len(subset) == 1:
                hits[area] = (time, subset.pop())
            else:
                unmatched[time, province].append((area, qid, names))
                continue
            claims[hits[area]] |= days
        left = []
        for (time, province), rows in sorted(unmatched.items()):
            free = [(geosid, name) for geosid, name, _ in districts[time, province] if not claims[time, geosid]]
            if len(rows) == 1 and len(free) == 1:
                print("paired", time, rows[0][2], free[0][1])
                hits[rows[0][0]] = (time, free[0][0])
            else:
                left.extend((time, province, qid, names) for _, qid, names in rows)
        print(f"{len(hits)} matched, {len(left)} unmatched")
        for row in left:
            print("unmatched", *row)
        joined, orders = {}, defaultdict(set)
        for _, _, start, _, province, _, days in areas:
            orders["prairies" if province in {"47", "48", "61"} else province].add(start)
            joined[start, province] = min(joined.get((start, province), date.max), *(days or [start]))
        dates = {
            area: (
                max(start, joined[start, province]),
                min(
                    [
                        end,
                        *(
                            later
                            for later in orders["prairies" if province in {"47", "48", "61"} else province]
                            if later > start
                        ),
                    ]
                ),
                min(days or [start]),
            )
            for area, _, start, end, province, _, days in areas
        }
        matched = [(area, time, geosid, *dates[area]) for area, (time, geosid) in hits.items()]

        spans = []
        for (time, geosid), group in pl.DataFrame(
            matched,
            schema=["area", "time", "geosid", "start", "end", "first"],
            orient="row",
        ).group_by("time", "geosid"):
            rows = group.sort("first").rows(named=True)
            if len(rows) > 1:
                print("shared", time, geosid, [(row["first"], row["area"]) for row in rows])
            for index, row in enumerate(rows):
                spans.append(
                    (
                        str(row["area"]),
                        time,
                        geosid,
                        row["start"] if index == 0 else row["first"],
                        rows[index + 1]["first"] if index + 1 < len(rows) else row["end"],
                    )
                )

        conn.execute("create temp table district (time int, geosid text, geojson text) on commit drop")
        wanted = {time for _, time, *_ in spans}
        with zipfile.ZipFile(raw.cached(SOURCE, SHAPES).path) as archive:
            for time in sorted(wanted):
                stem = f"fedshapes_cbf_20221003/CBF_RO{time}_CSRS"
                reader = shapefile.Reader(
                    shp=io.BytesIO(archive.read(f"{stem}.shp")),
                    shx=io.BytesIO(archive.read(f"{stem}.shx")),
                    dbf=io.BytesIO(archive.read(f"{stem}.dbf")),
                )
                with conn.cursor() as cursor, cursor.copy("copy district from stdin") as copy:
                    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
                        copy.write_row((time, str(record["fedid"]), json.dumps(shape.__geo_interface__)))
                print("loaded", time)
        conn.execute("create temp table span (area text, time int, geosid text, start date, stop date) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy span from stdin") as copy:
            for row in spans:
                copy.write_row(row)
        if missing := conn.execute("select time, geosid from span except select time, geosid from district").fetchall():
            raise ValueError(f"districts without shapes: {missing}")
        rows = conn.execute(
            """with cleaned as (
                 select time, geosid, st_setsrid(st_coveragesimplify(geom, %s) over (partition by time), 3348) as geom
                 from (select time, geosid, st_setsrid(st_coverageclean(geom) over (partition by time), 3348) as geom
                       from (select time, geosid, st_multi(st_collectionextract(st_makevalid(
                                      st_setsrid(st_geomfromgeojson(geojson), 3348)), 3)) as geom
                             from district) valid) clean
               ),
               shaped as (
                 select time, geosid,
                        encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_transform(geom, 4326)), 3))), 'hex') as geom,
                        encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex') as label_point
                 from cleaned where (time, geosid) in (select time, geosid from span)
               )
               select s.area, s.time, s.start, s.stop, d.geom, d.label_point
               from span s join shaped d using (time, geosid)""",
            [TOLERANCE],
        ).fetchall()
    frame = pl.DataFrame(
        rows,
        schema={
            "area": pl.String,
            "time": pl.Int32,
            "valid_from": pl.Date,
            "valid_to": pl.Date,
            "geom": pl.String,
            "label_point": pl.String,
        },
        orient="row",
    )
    if frame.height != len(spans) or frame["geom"].null_count():
        raise ValueError(f"{frame.height} shapes for {len(spans)} ridings")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "areas.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Shapes for {frame.height} federal ridings from 1867 to 2004, from Taylor, Lucas, Kirby, and Hewitt's "
                "digital boundary files of every representation order"
            ),
            polity="CA",
        )
        publisher = "Taylor, Lucas, Kirby, and Hewitt"
        title = "Canada's Federal Electoral Districts, 1867–2021: digital boundary files"
        source = cs.source(publisher, title)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=publisher,
            title=title,
            url="https://doi.org/10.5683/SP3/4E8DCR",
            license="CC0 1.0",
            license_url="http://creativecommons.org/publicdomain/zero/1.0",
            attribution=(
                "Taylor, Zack, Jack Lucas, J.P. Kirby, and Christopher Macdonald Hewitt. 2023. "
                "Canada's Federal Electoral Districts, 1867–2021. Canadian Journal of Political Science"
            ),
        )
        for url in (SHAPES, TABLE):
            cs.artifact(raw.cached(SOURCE, url), source)
        for area, start, stop, geom, point in frame.select(
            "area", "valid_from", "valid_to", "geom", "label_point"
        ).rows():
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=start,
                valid_from_precision="day",
                valid_to=stop,
                valid_to_precision="day",
                geom=geom,
                label_point=point,
                quality="academic",
                source_id=source,
            )
        starts = sorted(
            {
                start
                for (start,) in conn.execute(
                    """select distinct ac.valid_from from area_code ac join area a on a.id = ac.area_id
               where a.scheme_code = 'ca.fed' and ac.valid_to <= %s""",
                    [LAST],
                ).fetchall()
            }
        )
        generals = [
            day
            for (day,) in conn.execute(
                "select date_from from election where kind = 'general' and parent_id is null and polity_id = %s",
                [cs.polity("CA")],
            ).fetchall()
        ]
        times = sorted(set(frame["time"]))
        for start, stop in zip(starts, [*starts[1:], LAST], strict=True):
            cs.add(
                "boundary_set",
                scheme_code="ca.fed",
                vintage=str(max(time for time in times if time <= start.year)),
                valid_from=start,
                valid_from_precision="day",
                valid_to=stop,
                valid_to_precision="day",
                as_of=max(day for day in generals if start <= day < stop),
            )
        return cs.write()
