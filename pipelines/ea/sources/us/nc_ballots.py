import csv
import io
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from ea import raw
from ea.store import DATA, connect

SOURCE = "us.ballots"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE / "nc"
FEED = "us.ballots.nc"
PUBLISHER = "North Carolina State Board of Elections"
LANDING = "https://www.ncsbe.gov/results-data/absentee-data"
FILE = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{day:%Y_%m_%d}/absentee_{day:%Y%m%d}.zip"
DAYS = [date(2020, 11, 3), date(2022, 11, 8), date(2024, 11, 5), date(2026, 11, 3)]
CHANNELS = {"ONE-STOP": 2, "EARLY VOTING": 2, "MAIL": 3}
STAGES = ["returned", "accepted"]
EASTERN = ZoneInfo("America/New_York")
LAST_RETURN = timedelta(days=30)
NOTE = (
    "Per-voter absentee files, loaded once per election; cumulative returned and accepted ballots per county, "
    "channel, and return date, each as of the end of that day (Eastern)"
)


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, FILE.format(day=day), suffix=".zip")


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def posted(info: zipfile.ZipInfo) -> datetime:
    return datetime(*info.date_time, tzinfo=EASTERN).astimezone(UTC)


def tally(day: date) -> tuple[Counter[tuple[str, int, str, date]], datetime, int]:
    artifact = raw.cached(SOURCE, FILE.format(day=day))
    counts: Counter[tuple[str, int, str, date]] = Counter()
    clamped = 0
    with zipfile.ZipFile(artifact.path) as archive:
        member = archive.infolist()[0]
        stamp = posted(member)
        with archive.open(member) as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, encoding="latin-1", newline="")):
                returned = row["ballot_rtn_dt"].strip()
                if not returned:
                    continue
                request = row["ballot_req_type"].strip()
                if request not in CHANNELS:
                    raise ValueError(f"{day}: unknown request type {request!r}")
                month, number, year = returned.split("/")
                when = date(int(year), int(month), int(number))
                if when > day + LAST_RETURN:
                    when = stamp.astimezone(EASTERN).date()
                    clamped += 1
                county = key(row["county_desc"])
                channel = CHANNELS[request]
                counts[(county, channel, "returned", when)] += 1
                if row["ballot_rtn_status"].strip().startswith("ACCEPTED"):
                    counts[(county, channel, "accepted", when)] += 1
    return counts, stamp, clamped


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        codes = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
    frames = []
    for day in DAYS:
        counts, stamp, clamped = tally(day)
        missing = {county for county, *_ in counts} - codes.keys()
        if missing:
            raise ValueError(f"{day}: unmatched counties {sorted(missing)}")
        if len({county for county, *_ in counts}) != 100:
            raise ValueError(f"{day}: {len({county for county, *_ in counts})} counties")
        frame = pl.DataFrame(
            [(codes[county], channel, stage, when, n) for (county, channel, stage, when), n in counts.items()],
            schema={"code": pl.String, "channel": pl.Int16, "stage": pl.String, "day": pl.Date, "n": pl.Int64},
            orient="row",
        ).sort("code", "channel", "stage", "day")
        frame = frame.with_columns(count=pl.col("n").cum_sum().over("code", "channel", "stage"))
        frames.append(frame.with_columns(election=pl.lit(day), posted=pl.lit(stamp), clamped=pl.lit(clamped)))
        print(
            f"{day}: {frame.height} county-day rows, {clamped} returns dated after the window clamped to the posting date"
        )
    pl.concat(frames).write_parquet(PARSED / "absentee.parquet")


def emit() -> str:
    frame = pl.read_parquet(PARSED / "absentee.parquet")
    with connect() as conn:
        conn.execute(
            """insert into live.feed (id, adapter, params, publisher, url, poller, interval_s, enabled, note)
               values (%s, 'us.ballots', '{"state": "NC"}', %s, %s, 'muktedir', 3600, false, %s)
               on conflict (id) do nothing""",
            [
                FEED,
                PUBLISHER,
                LANDING,
                NOTE,
            ],
        )
        elections = {
            day: conn.execute(
                """select e.id from election e join polity p on p.id = e.polity_id
                   where p.iso_code = 'US-NC' and e.date_from = %s and e.kind = 'general' and e.status = 'held'""",
                [day],
            ).fetchall()
            for day in DAYS
        }
        for day, found in elections.items():
            if len(found) != 1:
                raise ValueError(f"{day}: {len(found)} matching elections")
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '37%' and valid_to is null"
            ).fetchall()
        )
        conn.execute("delete from live.fetch where feed_id = %s and status = 'frame'", [FEED])
        total = 0
        for (day,), part in frame.partition_by("election", as_dict=True).items():
            artifact = raw.cached(SOURCE, FILE.format(day=day))
            fetch_id = conn.execute(
                """insert into live.fetch (feed_id, fetched_at, as_of, sha256, path, status, detail)
                   values (%s, %s, %s, %s, %s, 'frame', %s) returning id""",
                [
                    FEED,
                    artifact.retrieved_at,
                    part["posted"][0],
                    artifact.sha256,
                    f"raw/{SOURCE}/{artifact.path.name}",
                    f"absentee file {day}; as_of is the file's posting time",
                ],
            ).fetchall()[0][0]
            election_id = elections[day][0][0]
            rows = [
                (
                    fetch_id,
                    election_id,
                    units[row["code"]],
                    datetime.combine(row["day"], time(23, 59, 59), EASTERN),
                    row["stage"],
                    row["channel"],
                    row["count"],
                )
                for row in part.iter_rows(named=True)
            ]
            with conn.cursor() as cursor:
                cursor.executemany(
                    """insert into live.ballots (fetch_id, election_id, unit_id, as_of, stage, channel, count)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    rows,
                )
            total += len(rows)
    return f"{total} live.ballots rows from {len(DAYS)} files"
