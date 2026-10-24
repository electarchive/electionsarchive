import io
import re
import zipfile
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import openelections
from ea.store import DATA, connect

SOURCE = "us.nc_stats_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Voter registration and voter history statistics by election"
BUCKET = "https://s3.amazonaws.com/dl.ncsbe.gov"


def files(keys: list[str]) -> dict[date, dict[str, str]]:
    found: dict[date, dict[str, str]] = {}
    for key in keys:
        if match := re.search(r"ENRS/(\d{4})_(\d\d)_(\d\d)/(voter|history)_stats_\d{8}\.(zip|txt)$", key):
            found.setdefault(date(int(match[1]), int(match[2]), int(match[3])), {})[match[4]] = f"{BUCKET}/{key.removeprefix(BUCKET + '/')}"
    return found


def fetch() -> None:
    keys, token = [], None
    while True:
        params = {"list-type": "2", "prefix": "ENRS/", "max-keys": "1000"} | ({"continuation-token": token} if token else {})
        body = httpx.get(BUCKET, params=params, timeout=60).text
        keys += re.findall(r"<Key>([^<]+)</Key>", body)
        token = next(iter(re.findall(r"<NextContinuationToken>([^<]+)<", body)), None)
        if token is None:
            break
    for urls in files(keys).values():
        for url in urls.values():
            raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def counts(url: str) -> pl.DataFrame | None:
    content = raw.cached(SOURCE, url).path.read_bytes()
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            name = next((n for n in archive.namelist() if n.endswith(".txt")), None)
            if name is None:
                return None
            content = archive.read(name)
    if b"\t" not in content[:500]:
        return None
    headed = content.startswith(b"county_desc")
    frame = pl.read_csv(io.BytesIO(content), separator="\t", has_header=headed, infer_schema_length=0, quote_char=None, truncate_ragged_lines=True)
    county, total = "county_desc", "total_voters"
    if not headed:
        dated = next(c for c in frame.columns if frame[c].str.contains(r"^\d\d/\d\d/\d{4}$").all())
        county, total = frame.columns[0], frame.columns[frame.columns.index(dated) - 1]
    return frame.group_by(county=pl.col(county).str.strip_chars()).agg(total=pl.col(total).str.strip_chars().cast(pl.Int64).sum())


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["NC"]
    frames = []
    for day, urls in sorted(files(list(raw._index(SOURCE)[1])).items()):
        if "history" not in urls:
            continue
        voters = counts(urls["history"])
        if voters is None:
            print(day, "no history stats")
            continue
        registered = counts(urls["voter"]) if "voter" in urls and day != date(2004, 7, 20) else None
        frame = voters.rename({"total": "voters"}).with_columns(registered=pl.lit(None, pl.Int64))
        if registered is not None:
            frame = frame.drop("registered").join(registered.rename({"total": "registered"}), on="county", how="left")
        frame = frame.with_columns(
            day=pl.lit(day),
            code=pl.col("county").map_elements(lambda name: fips[re.sub(r"[^a-z]", "", name.lower())], return_dtype=pl.String),
            history=pl.lit(urls["history"]),
            voter=pl.lit(urls["voter"] if registered is not None else None, pl.String),
        )
        print(day, frame.height, "counties", frame.select(pl.col("registered", "voters").sum()).row(0))
        frames.append(frame.drop("county"))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames, how="diagonal_relaxed").write_parquet(PARSED / "turnout.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's registered voters and voters by county for each election from the State Board of Elections' voter_stats and history_stats files",
            polity="US",
        )
        elections: dict[date, list[tuple[str, str]]] = {}
        for value, entity_id, start in conn.execute(
            """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':NC(:[a-z]+)?$'"""
        ):
            elections.setdefault(start, []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url="https://dl.ncsbe.gov/?prefix=ENRS/", license="Public record")
        for (day,), rows in frame.group_by("day", maintain_order=True):
            if day not in elections:
                continue
            for url in {*rows["history"], *rows["voter"].drop_nulls()}:
                cs.artifact(raw.cached(SOURCE, url), source)
            for value_key, election_id in elections[day]:
                result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election_id,
                    contest_id=None,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=election_id not in held,
                    completeness=round(rows.height / 100, 4),
                )
                for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                    for measure, value in ((2, registered), (14, voters)):
                        if value is None:
                            continue
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.county", code),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
