import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.sources.us import openelections
from ea.store import DATA, connect

SOURCE = "us.ballots"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE / "pa"
FEED = "us.ballots.pa"
PUBLISHER = "Pennsylvania Department of State"
LANDING = (
    "https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/voting-and-election-statistics/"
    "election-data"
)
BULK = "https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/voting-and-elections/bulk-data/"
FILES = {
    date(2024, 11, 5): (
        "general",
        BULK + "2024-general-election/mail%20ballot%20data%20summary%20-%202024%20general.xlsx",
    ),
    date(2025, 5, 20): ("primary", BULK + "2025-primary-election/mail%20ballot%20data%20summary%202025%20primary.xlsx"),
    date(2025, 11, 4): ("local", BULK + "2025-municipal-election/mail%20ballot%20data%20summary%202025%20general.xlsx"),
    date(2026, 5, 19): ("primary", BULK + "2026-general-primary/mail-ballot-data-summary-2026-primary.xlsx"),
}
HANDBOOK = (
    "https://www.pa.gov/content/dam/copapwp-pagov/en/dos/resources/voting-and-elections/election-data-handbook-2026.pdf"
)
STAGES = {
    "Total ballots": "issued",
    "Total returned ballots": "returned",
    "Total recorded ballots": "accepted",
    "Total cancelled ballots": "rejected",
    "Ballots not returned": "unreturned",
}
POSTAL = 3


def fetch() -> None:
    for _, url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    raw.fetch(SOURCE, HANDBOOK, suffix=".pdf")


def posted(path: Path) -> datetime:
    with zipfile.ZipFile(path) as book:
        text = book.read("docProps/core.xml").decode()
    stamp = re.search(r"<dcterms:modified[^>]*>([^<]+)<", text)
    if stamp is None:
        raise ValueError(f"{path.name} has no modified time")
    return datetime.fromisoformat(stamp.group(1)).astimezone(UTC)


def county_sheet(path: Path) -> pl.DataFrame:
    sheet = fastexcel.read_excel(path).load_sheet_by_idx(0, header_row=None).to_polars()
    rows = sheet.rows()
    header = [str(cell) for cell in rows[0]]
    body = []
    for row in rows[1:]:
        if row[0] is None:
            continue
        if str(row[0]).startswith("Please keep"):
            break
        body.append({name: cell for name, cell in zip(header, row, strict=True)})
    return pl.DataFrame(body, infer_schema_length=None)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        fips = openelections.counties(conn)["PA"]
    frames = []
    for day, (_, url) in FILES.items():
        artifact = raw.cached(SOURCE, url)
        sheet = county_sheet(artifact.path)
        total = sheet.filter(pl.col("County") == "TOTAL")
        counties = sheet.filter(pl.col("County") != "TOTAL")
        if counties.height != 67:
            raise ValueError(f"{day}: {counties.height} counties")
        columns = [name for name in STAGES if name in sheet.columns]
        for name in columns:
            counted = counties[name].cast(pl.Int64).sum()
            printed = int(total[name][0])
            if counted != printed:
                raise ValueError(f"{day} {name}: counties sum to {counted}, file prints {printed}")
        returned = counties["Total returned ballots"].cast(pl.Int64)
        if (
            returned
            != counties["Total recorded ballots"].cast(pl.Int64) + counties["Total cancelled ballots"].cast(pl.Int64)
        ).any():
            raise ValueError(f"{day}: returned is not recorded plus cancelled")
        if "Ballots not returned" in sheet.columns:
            left = counties["Total ballots"].cast(pl.Int64) - returned
            if (left != counties["Ballots not returned"].cast(pl.Int64)).any():
                raise ValueError(f"{day}: ballots not returned is not total minus returned")
        long = counties.select(
            pl.lit(day).alias("day"),
            pl.col("County").alias("county"),
            *[pl.col(name).cast(pl.Int64).alias(stage) for name, stage in STAGES.items() if name in sheet.columns],
        ).unpivot(index=["day", "county"], variable_name="stage", value_name="count")
        code = long["county"].str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(fips, default=None)
        long = long.with_columns(code=code, as_of=pl.lit(posted(artifact.path)), url=pl.lit(url))
        if long["code"].null_count():
            raise ValueError(
                f"{day}: unmatched counties {long.filter(pl.col('code').is_null())['county'].unique().to_list()}"
            )
        frames.append(long)
    pl.concat(frames).write_parquet(PARSED / "county.parquet")


def emit() -> str:
    frame = pl.read_parquet(PARSED / "county.parquet")
    with connect() as conn:
        conn.execute(
            """insert into live.feed (id, adapter, params, publisher, url, poller, interval_s, enabled, note)
               values (%s, 'us.ballots', '{}', %s, %s, 'muktedir', 3600, false, %s)
               on conflict (id) do nothing""",
            [
                FEED,
                PUBLISHER,
                LANDING,
                "County mail ballot summary files, loaded once per file after each election; as_of is the file's posting time",
            ],
        )
        elections = {
            day: conn.execute(
                """select e.id from election e join polity p on p.id = e.polity_id
                   where p.iso_code = 'US-PA' and e.date_from = %s and e.kind = %s and e.status = 'held'""",
                [day, kind],
            ).fetchall()
            for day, (kind, _) in FILES.items()
        }
        for day, found in elections.items():
            if len(found) != 1:
                raise ValueError(f"{day}: {len(found)} matching elections")
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '42%' and valid_to is null"
            ).fetchall()
        )
        conn.execute(
            "delete from live.fetch where feed_id = %s and status = 'frame' and detail like 'county summary %%'", [FEED]
        )
        total = 0
        for (day, url), part in frame.partition_by("day", "url", as_dict=True).items():
            artifact = raw.cached(SOURCE, url)
            as_of = part["as_of"][0]
            fetch_id = conn.execute(
                """insert into live.fetch (feed_id, fetched_at, as_of, sha256, path, status, detail)
                   values (%s, %s, %s, %s, %s, 'frame', %s) returning id""",
                [
                    FEED,
                    artifact.retrieved_at,
                    as_of,
                    artifact.sha256,
                    f"raw/{SOURCE}/{artifact.path.name}",
                    f"county summary {day}; as_of is the posting time in the file's metadata",
                ],
            ).fetchall()[0][0]
            election_id: UUID = elections[day][0][0]
            rows = [
                (fetch_id, election_id, units[row["code"]], as_of, row["stage"], POSTAL, row["count"])
                for row in part.iter_rows(named=True)
            ]
            with conn.cursor() as cursor:
                cursor.executemany(
                    """insert into live.ballots (fetch_id, election_id, unit_id, as_of, stage, channel, count)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    rows,
                )
            total += len(rows)
    return f"{total} live.ballots rows from {len(FILES)} files"
