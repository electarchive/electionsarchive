import html
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import polars as pl

from ea import raw
from ea.store import DATA, connect

SOURCE = "us.ballots"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE / "az"
FEED = "us.ballots.az"
PUBLISHER = "Arizona Secretary of State"
LANDING = "https://apps.arizona.vote/info/bps/2022-general-election/33/0"
PAGE = "apps.arizona.vote/info/bps/2022-general-election/"
ARIZONA = ZoneInfo("America/Phoenix")
ELECTION = "2022-11-08"
FIPS = {
    "Apache": "04001",
    "Cochise": "04003",
    "Coconino": "04005",
    "Gila": "04007",
    "Graham": "04009",
    "Greenlee": "04011",
    "La Paz": "04012",
    "Maricopa": "04013",
    "Mohave": "04015",
    "Navajo": "04017",
    "Pima": "04019",
    "Pinal": "04021",
    "Santa Cruz": "04023",
    "Yavapai": "04025",
    "Yuma": "04027",
}
NOTE = (
    "Ballot-processing reports for the 2022 general election, loaded once from Wayback captures of the Secretary of "
    "State page; remaining is the county's estimated total number of uncounted ballots, tabulated is the same report's "
    "ballots already tabulated, and as_of is the report's Last Updated time (Arizona time)"
)


def fetch() -> None:
    if not captures():
        raise ValueError("no cached Arizona ballot-processing captures; run ea raw pull us.ballots")


def captures() -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in sorted(raw.keys(SOURCE)) if PAGE in url]


def captured(artifact: raw.Artifact) -> str:
    stamp = re.search(r"/web/(\d{14})", artifact.url)
    if stamp is None:
        raise ValueError(f"{artifact.url} is not a Wayback capture")
    return stamp.group(1)


def cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
    ]


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    seen: dict[tuple[str, datetime], tuple[int, int | None, str]] = {}
    skipped = 0
    revised = 0
    for artifact in sorted(captures(), key=captured):
        text = artifact.path.read_text(errors="replace")
        start = text.find("<table")
        if start < 0:
            continue
        for row in re.findall(r"<tr.*?</tr>", text[start : text.find("</table>", start)], re.DOTALL):
            cell = cells(row)
            if len(cell) < 8 or cell[1] not in FIPS:
                continue
            if cell[6].strip("- ") == "":
                skipped += 1
                continue
            as_of = datetime.strptime(cell[0], "%m/%d/%Y %H:%M").replace(tzinfo=ARIZONA).astimezone(UTC)
            count = int(cell[6].replace(",", ""))
            tabulated = None if cell[2].strip("- ") == "" else int(cell[2].replace(",", ""))
            key = (FIPS[cell[1]], as_of)
            if key in seen and seen[key][:2] != (count, tabulated):
                revised += 1
            seen[key] = (count, tabulated, artifact.url)
    frame = pl.DataFrame(
        [(code, as_of, count, tabulated, url) for (code, as_of), (count, tabulated, url) in seen.items()],
        schema={
            "code": pl.String,
            "as_of": pl.Datetime("us", "UTC"),
            "count": pl.Int64,
            "tabulated": pl.Int64,
            "url": pl.String,
        },
        orient="row",
    ).sort("code", "as_of")
    frame.write_parquet(PARSED / "remaining.parquet")
    print(
        f"{frame.height} county rows with a total, {skipped} rows without one skipped, {revised} restated under an unchanged time (latest capture kept)"
    )


def emit() -> str:
    frame = pl.read_parquet(PARSED / "remaining.parquet")
    with connect() as conn:
        conn.execute(
            """insert into live.feed (id, adapter, params, publisher, url, poller, interval_s, enabled, note)
               values (%s, 'us.ballots', '{"state": "AZ", "remaining": true}', %s, %s, 'muktedir', 3600, false, %s)
               on conflict (id) do nothing""",
            [FEED, PUBLISHER, LANDING, NOTE],
        )
        found = conn.execute(
            """select e.id from election e join polity p on p.id = e.polity_id
               where p.iso_code = 'US-AZ' and e.date_from = %s and e.kind = 'general' and e.status = 'held'""",
            [ELECTION],
        ).fetchall()
        if len(found) != 1:
            raise ValueError(f"{len(found)} matching elections")
        election_id = found[0][0]
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '04%' and valid_to is null"
            ).fetchall()
        )
        conn.execute("delete from live.fetch where feed_id = %s and status = 'frame'", [FEED])
        for url, part in frame.partition_by("url", as_dict=True, maintain_order=True).items():
            artifact = raw.cached(SOURCE, url[0])
            fetch_id = conn.execute(
                """insert into live.fetch (feed_id, fetched_at, as_of, sha256, path, status, detail)
                   values (%s, %s, %s, %s, %s, 'frame', %s) returning id""",
                [
                    FEED,
                    artifact.retrieved_at,
                    part["as_of"].max(),
                    artifact.sha256,
                    f"raw/{SOURCE}/{artifact.path.name}",
                    f"Wayback capture {url[0]}; as_of is each row's Last Updated time",
                ],
            ).fetchall()[0][0]
            with conn.cursor() as cursor:
                cursor.executemany(
                    """insert into live.ballots (fetch_id, election_id, unit_id, as_of, stage, channel, count)
                       values (%s, %s, %s, %s, %s, 0, %s)""",
                    [
                        (fetch_id, election_id, units[row["code"]], row["as_of"], stage, row[column])
                        for row in part.iter_rows(named=True)
                        for stage, column in (("remaining", "count"), ("tabulated", "tabulated"))
                        if row[column] is not None
                    ],
                )
    return f"{frame.height + frame['tabulated'].count()} live.ballots rows"
