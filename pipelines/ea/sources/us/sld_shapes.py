import io
import json
import re
import time
import zipfile
from datetime import date
from pathlib import Path

import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.sld_shapes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "U.S. Census Bureau"
TIGER = "https://www2.census.gov/geo/tiger/"


def listings() -> dict[str, str]:
    pages = {}
    for kind in ("SLDL", "SLDU"):
        pages[f"2000:{kind}"] = f"{TIGER}TIGER2010/{kind}/2000/"
        pages[f"2010:{kind}"] = f"{TIGER}TIGER2010/{kind}/2010/"
        for year in range(2011, 2027):
            pages[f"{year}:{kind}"] = f"{TIGER}TIGER{year}/{kind}/"
    for year in (2008, 2009):
        pages[f"{year}"] = f"{TIGER}TIGER{year}/"
    return pages


def files() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for key, page in listings().items():
        text = raw.cached(SOURCE, page).path.read_text()
        if key in ("2008", "2009"):
            for folder in sorted(set(re.findall(r'href="(\d{2}_[A-Z_]+/)"', text))):
                state = raw.cached(SOURCE, page + folder).path.read_text()
                for name in sorted(set(re.findall(rf"tl_{key}_\d{{2}}_sld[lu]\.zip", state))):
                    found.setdefault(f"{key}:{name[-8:-4].upper()}", []).append(page + folder + name)
        else:
            found[key] = [
                page + name for name in sorted(set(re.findall(r"tl_\d{4}_\d{2}_sld[lu](?:00|10)?\.zip", text)))
            ]
    return found


def fetch() -> None:
    for key, page in listings().items():
        polite(page, ".html")
        if key in ("2008", "2009"):
            text = raw.cached(SOURCE, page).path.read_text()
            for folder in sorted(set(re.findall(r'href="(\d{2}_[A-Z_]+/)"', text))):
                polite(page + folder, ".html")
    for urls in files().values():
        for url in urls:
            polite(url, ".zip")


def polite(url: str, suffix: str) -> None:
    for attempt in range(8):
        try:
            raw.fetch(SOURCE, url, suffix=suffix)
            return
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 429 or attempt == 7:
                raise
            time.sleep(30 * 2**attempt)


TOLERANCE = 0.00005
UNITS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"]
TEENS = ["tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth"]
TEENS += ["eighteenth", "nineteenth"]
ORDINALS = {word: index + 1 for index, word in enumerate(UNITS)} | {
    word: index + 10 for index, word in enumerate(TEENS)
}
for tens, word in ((20, "twent"), (30, "thirt"), (40, "fort")):
    ORDINALS[f"{word}ieth"] = tens
    for index, unit in enumerate(UNITS):
        ORDINALS[f"{word}y-{unit}"] = tens + index + 1


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("create temp table sld (st text, name text, lsy text, geojson text)")
        for key, urls in files().items():
            frames = []
            for url in urls:
                if not zipfile.is_zipfile(raw.cached(SOURCE, url).path):
                    print(f"{url}: not a zip, skipped", flush=True)
                    continue
                with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                    parts = {
                        name.rsplit(".", 1)[1].lower(): archive.read(name)
                        for name in archive.namelist()
                        if name.rsplit(".", 1)[-1].lower() in ("shp", "shx", "dbf")
                    }
                reader = shapefile.Reader(
                    shp=io.BytesIO(parts["shp"]),
                    shx=io.BytesIO(parts["shx"]),
                    dbf=io.BytesIO(parts["dbf"]),
                    encoding="latin-1",
                )
                fields = [re.sub(r"(00|10)$", "", field[0].upper()) for field in reader.fields[1:]]
                kind = key.rsplit(":", 1)[1]
                with conn.cursor().copy("copy sld (st, name, lsy, geojson) from stdin") as copy:
                    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                        if record is None or shape is None:
                            continue
                        row = dict(zip(fields, list(record), strict=True))
                        st = str(row[f"{kind}ST"]).strip()
                        if shape.shapeType == shapefile.NULL or not shape.points or "ZZZ" in st:
                            continue
                        copy.write_row(
                            (
                                st,
                                str(row["NAMELSAD"] or "").strip(),
                                str(row.get("LSY") or "").strip(),
                                json.dumps(shape.__geo_interface__),
                            )
                        )
                frame = pl.DataFrame(
                    conn.execute(
                        """with source as (
                             select st, min(name) as name, min(lsy) as lsy,
                                    st_union(st_transform(st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)) as geom
                             from sld group by st),
                           simplified as (
                             select st, name, lsy, geom as original, st_coveragesimplify(geom, %s) over () as geom
                             from source),
                           shapes as (
                             select st, name, lsy, st_multi(st_collectionextract(st_makevalid(
                                      case when geom is null or st_isempty(geom) then original else geom end), 3)) as geom
                             from simplified)
                           select st, name, lsy, encode(st_asewkb(geom), 'hex'),
                                  encode(st_asewkb(st_pointonsurface(geom)), 'hex'),
                                  st_area(geom::geography), st_x(st_centroid(geom)), st_y(st_centroid(geom))
                           from shapes where not st_isempty(geom)""",
                        [TOLERANCE],
                    ).fetchall(),
                    schema=["st", "name", "lsy", "geom", "point", "area", "x", "y"],
                    orient="row",
                ).with_columns(state=pl.lit(re.search(r"tl_\d{4}_(\d{2})_", url)[1]), url=pl.lit(url))
                conn.execute("truncate sld")
                frames.append(frame)
            year, kind = key.split(":")
            out = pl.concat(frames).with_columns(year=pl.lit(int(year)), kind=pl.lit(kind))
            out.write_parquet(PARSED / f"{year}_{kind}.parquet")
            print(f"{key}: {out.height} districts in {out['state'].n_unique()} states", flush=True)


def emit() -> Path:
    frame = pl.concat([pl.read_parquet(path) for path in sorted(PARSED.glob("*.parquet"))])
    labels = []
    for st, name in frame.select("st", "name").iter_rows():
        found = set()
        coded = re.fullmatch(r"0*([1-9]\d*)([A-Z])?|0*([A-Z])", st)
        if coded and coded[1]:
            found.add(str(int(coded[1])) + (f"-{coded[2].lower()}" if coded[2] else ""))
        elif coded:
            found.add(coded[3].lower())
        text = re.sub(
            r"(state |general )?((house|senate|senatorial|representative|legislative|assembly|delegate) )?(sub)?"
            r"districts?|county|no\.",
            " ",
            name.lower(),
        )
        words, numbers = [], []
        for token in re.findall(r"\d+(?:st|nd|rd|th)?|[a-z]+(?:-[a-z]+)*", text):
            if token[0].isdigit():
                numbers.append(int(re.match(r"\d+", token)[0]))
            elif token in ORDINALS:
                numbers.append(ORDINALS[token])
            else:
                words += [word for word in token.split("-") if word not in ("and", "of", "the")]
        if numbers[2:] or (not words and not numbers) or re.search(r"\b\d+[a-z]\b", text):
            labels.append(sorted(found))
            continue
        number = "-".join([str(numbers[0])] + [chr(96 + n) for n in numbers[1:] if n <= 26]) if numbers else ""
        found.add("-".join(part for part in ("".join(words), number) if part))
        labels.append(sorted(found))
    frame = frame.with_columns(labels=pl.Series(labels, dtype=pl.List(pl.String)))
    cycles = {
        "VA": (1991, 2001, 2011, 2022),
        "NJ": (1991, 2001, 2011, 2022),
        "LA": (1991, 2002, 2011, 2022),
        "ME": (1994, 2004, 2012, 2022),
        "NH": (1992, 2004, 2012, 2022),
        "GA": (1992, 2004, 2012, 2022),
        "MT": (1994, 2004, 2012, 2022),
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative district shapes from the Census TIGER/Line Shapefiles, 2000–2026",
            polity="US",
        )
        areas = {
            (scheme.removeprefix("us.").upper(), fips, code.split(":", 1)[1]): (area, code.split(":", 1)[0])
            for area, scheme, code, fips in conn.execute(
                """select a.id, a.scheme_code, ac.code, sc.code from area a join area_code ac on ac.area_id = a.id
                   join area_parent p on p.area_id = a.id join area s on s.id = p.parent_id and s.scheme_code = 'us.state'
                   join area_code sc on sc.area_id = s.id
                   where a.scheme_code in ('us.sldl', 'us.sldu')"""
            ).fetchall()
        }
        sources = {}
        for release in sorted({int(re.search(r"TIGER(\d{4})", url)[1]) for url in frame["url"].unique()}):
            title = f"TIGER/Line Shapefiles, {release} State Legislative Districts"
            sources[release] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[release],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"{TIGER}TIGER{release}/",
                license="Public domain",
            )
        cached = set()
        for key, urls in files().items():
            for url in [listings().get(key) or listings()[key.split(":")[0]], *urls] if urls else []:
                if url not in cached and (url.endswith("/") or zipfile.is_zipfile(raw.cached(SOURCE, url).path)):
                    cached.add(url)
                    cs.artifact(raw.cached(SOURCE, url), sources[int(re.search(r"TIGER(\d{4})", urls[0])[1])])
        rows = []
        counts: dict[str, int] = {}
        for (kind, state), group in frame.group_by("kind", "state"):
            vintages = sorted(group["year"].unique())
            begins: list[date] = []
            previous = None
            abbr = next((abbr for (_, fips, _), (_, abbr) in areas.items() if fips == state), "")
            for year in vintages:
                sessions = group.filter(pl.col("year") == year)["lsy"].str.extract(r"(\d{4})").drop_nulls()
                session = max(int(sessions.mode().max()) if sessions.len() else year, previous or 0)
                floor = previous if previous is not None else (1990 if year == 2000 else 2001)
                gap = [start for start in cycles.get(abbr, (1992, 2002, 2012, 2022)) if floor < start <= session]
                begins.append(date(gap[0] if gap else session, 1, 1))
                previous = session
            claims: dict[tuple[int, str], list[dict]] = {}
            for row in group.iter_rows(named=True):
                for label in row["labels"]:
                    if (kind, state, label) in areas:
                        claims.setdefault((row["year"], label), []).append(row)
            matched: dict[str, dict[int, dict]] = {}
            for (year, label), hits in claims.items():
                if len(hits) == 1:
                    matched.setdefault(label, {})[vintages.index(year)] = hits[0]
            for label, seen in matched.items():
                area = areas[(kind, state, label)][0]
                run: list[int] = []
                for index in sorted(seen) + [None]:
                    if run and index is not None and index == run[-1] + 1:
                        a, b = seen[run[-1]], seen[index]
                        near = abs(a["area"] - b["area"]) <= 0.005 * max(a["area"], b["area"]) and (
                            ((a["x"] - b["x"]) * 111_000 * 0.75) ** 2 + ((a["y"] - b["y"]) * 111_000) ** 2 < 200**2
                        )
                        if near:
                            run.append(index)
                            continue
                    end = begins[run[-1] + 1] if run and run[-1] + 1 < len(begins) else None
                    if run and (end is None or end > begins[run[0]]):
                        last = seen[run[-1]]
                        rows.append(
                            {
                                "area_id": str(area),
                                "valid_from": begins[run[0]],
                                "valid_from_precision": "year",
                                "valid_to": end,
                                "valid_to_precision": "year",
                                "geom": last["geom"],
                                "label_point": last["point"],
                                "source_id": str(sources[int(re.search(r"TIGER(\d{4})", last["url"])[1])]),
                            }
                        )
                    run = [index] if index is not None else []
            counts[f"{kind}:{state}"] = len(matched)
        out = pl.DataFrame(rows).with_columns(quality=pl.lit("official"))
        cs.frame("area_geometry", out)
        print(f"{out.height} shapes on {out['area_id'].n_unique()} districts of {len(areas)}")
        for key in sorted(counts):
            print(key, counts[key])
        return cs.write()
