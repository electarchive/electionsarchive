import csv
import hashlib
import html
import io
import re
import subprocess
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from psycopg import Connection

from ea import raw

SOURCE = "us.ballots"
EASTERN = ZoneInfo("America/New_York")
FL_STATS = "https://electionfiles.floridados.gov/countyballotreportfiles/Stats_{number}_{kind}.txt"
FL_PAGE = "https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicStats"
FL_REPORTS = "https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/PublicReports"
FL_EARLY = "https://countyfilesvbm-ev.floridados.gov/VoteByMailEarlyVotingReports/ViewArchive?name=EVL_{number}.zip"
FL_PDFS = {
    "https://files.floridados.gov/media/696917/early-voting-and-vote-by-mail-report-2014-gen.pdf": (
        date(2014, 11, 4),
        "general",
    ),
    "https://files.floridados.gov/media/696918/early-voting-and-vote-by-mail-report-2014-pri.pdf": (
        date(2014, 8, 26),
        "primary",
    ),
    "https://files.floridados.gov/media/697363/early-voting-and-vote-by-mail-report-2016-gen.pdf": (
        date(2016, 11, 8),
        "general",
    ),
    "https://files.floridados.gov/media/703387/2016-pri-status-counts-all.pdf": (date(2016, 8, 30), "primary"),
    "https://files.floridados.gov/media/700669/early-voting-and-vote-by-mail-report-2018-genpdf.pdf": (
        date(2018, 11, 6),
        "general",
    ),
    "https://files.floridados.gov/media/700670/early-voting-and-vote-by-mail-report-2018-pripdf.pdf": (
        date(2018, 8, 28),
        "primary",
    ),
    "https://fldoswebumbracoprod.blob.core.windows.net/media/703948/gen-2020.pdf": (date(2020, 11, 3), "general"),
    "https://files.floridados.gov/media/706191/2022-ge-rptstatuscountsarchive-ev-vbm.pdf": (
        date(2022, 11, 8),
        "general",
    ),
    "https://files.floridados.gov/media/708648/2024gen_vbm-ev_statuscounts.pdf": (date(2024, 11, 5), "general"),
}
STATS = {"Voted Vote-by-Mail": 3, "Voted Early": 2}
ANCHORS = {"county_abl": 3, "county_evs": 2}
Row = tuple[date, str, str, int, datetime, int]


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i)^(st\.?|saint) ", "st", name).lower())


def number(text: str) -> int:
    return int(text.replace(",", "") or 0)


def stamp(text: str) -> datetime:
    return datetime.strptime(re.sub(r"\s+", " ", text.strip()), "%m/%d/%Y %I:%M%p").replace(tzinfo=EASTERN)


def kind_of(election: str) -> str | None:
    return {"General": "general", "Primary": "primary"}.get(re.sub(r"^\d{4} ", "", election.strip()))


def tsv(content: bytes) -> Iterator[Row]:
    for row in csv.DictReader(io.StringIO(content.decode("utf-8-sig")), delimiter="\t"):
        channel = STATS.get(row["StatType"])
        kind = kind_of(row["ElectionName"])
        if channel is None or kind is None or row["CountyName"] == "State Totals" or not row["CompileDate"].strip():
            continue
        day = datetime.strptime(row["ElectionDate"], "%m/%d/%Y").date()
        yield day, kind, row["CountyName"], channel, stamp(row["CompileDate"]), number(row["GrandTotal"])


def cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
        for cell in re.findall(r"(?s)<t[dh][^>]*>.*?</t[dh]>", row)
    ]


def page(content: bytes) -> Iterator[Row]:
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", content.decode("utf-8", "replace"))
    days = {
        found: datetime.strptime(day, "%m/%d/%Y").date()
        for found, day in re.findall(r"Number\s*-\s*(\d+)(?:\s|&nbsp;)*Election Date\s*-\s*(\d\d/\d\d/\d{4})", text)
    }
    for anchor, channel in ANCHORS.items():
        start = text.find(f'id="{anchor}"')
        if start < 0:
            continue
        table = re.search(r"(?s)<table.*?</table>", text[start:])
        if table is None:
            continue
        rows = [cells(row) for row in re.findall(r"(?s)<tr.*?</tr>", table.group(0))]
        if not rows or rows[0][:2] != ["County", "Election"]:
            continue
        for row in rows[1:]:
            if len(row) < 8 or not row[7]:
                continue
            found, _, election = row[1].partition(" - ")
            kind = kind_of(election)
            if found not in days or kind is None:
                continue
            yield days[found], kind, row[0], channel, stamp(row[7]), number(row[6])


def pdf(path: Path, day: date, kind: str) -> Iterator[Row]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True).stdout
    county = None
    for line in text.splitlines():
        head = re.match(r"^(\S.*?)\s{2,}(?:As Of\s+)?Republican\s", line)
        if head:
            county = None if head.group(1) == "STATE TOTAL" else head.group(1)
            continue
        row = re.match(r"^\s+(Voted Vote.by.Mail|Voted Early)\s+(?:(\d+/\d+/\d{4})\s+)?.*?\s([\d,]+)\s*$", line)
        if county is None or row is None:
            continue
        channel = 3 if "Mail" in row.group(1) else 2
        as_of = (
            datetime.strptime(row.group(2), "%m/%d/%Y").date()
            if row.group(2)
            else day + timedelta(days=15)
            if channel == 3
            else day
        )
        yield (
            day,
            kind,
            county,
            channel,
            datetime.combine(as_of, datetime.min.time(), EASTERN),
            number(row.group(3)),
        )


def early(client: httpx.Client, number: str) -> raw.Artifact:
    listing = client.get(FL_REPORTS)
    listing.raise_for_status()
    names = {
        code: cells(row)[0]
        for row in re.findall(r"(?s)<tr.*?</tr>", listing.text)
        for code in re.findall(rf"ViewReport\?name=([A-Z]{{3}})_EVL_{number}\.txt", row)
    }
    url = FL_EARLY.format(number=number)
    response = client.get(url, timeout=600)
    response.raise_for_status()
    seen: set[str] = set()
    voters: Counter[tuple[str, str, str, str]] = Counter()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for member in archive.infolist():
            with archive.open(member) as handle:
                for line in io.TextIOWrapper(handle, encoding="latin-1", newline=""):
                    row = line.rstrip("\r\n").split("\t")
                    if row[0] != "EVtrs" or row[5] in seen:
                        continue
                    seen.add(row[5])
                    voters[(names[row[1]], row[3], row[4], row[17])] += 1
    lines = ["CountyName\tElectionDate\tElectionName\tDateofEarlyVote\tVoters"]
    lines += ["\t".join([*key, str(count)]) for key, count in sorted(voters.items())]
    return raw.store(
        SOURCE,
        f"{url}#county-day",
        ("\n".join(lines) + "\n").encode(),
        suffix=".tsv",
        retrieved_at=datetime.now(UTC),
        media_type="text/tab-separated-values",
    )


def daily(content: bytes) -> Iterator[Row]:
    totals: Counter[tuple[date, str, str]] = Counter()
    rows = csv.DictReader(io.StringIO(content.decode()), delimiter="\t")
    for row in sorted(rows, key=lambda row: datetime.strptime(row["DateofEarlyVote"], "%m/%d/%Y")):
        kind = kind_of(re.sub(r" Election$", "", row["ElectionName"]))
        if kind is None:
            continue
        day = datetime.strptime(row["ElectionDate"], "%m/%d/%Y").date()
        totals[(day, kind, row["CountyName"])] += int(row["Voters"])
        voted = datetime.strptime(row["DateofEarlyVote"], "%m/%d/%Y").date()
        yield (
            day,
            kind,
            row["CountyName"],
            2,
            datetime.combine(voted, time(23, 59, 59), EASTERN),
            totals[(day, kind, row["CountyName"])],
        )


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    response = client.get(FL_PAGE)
    response.raise_for_status()
    found = sorted(set(re.findall(r"Stats_(\d+)_VbmVoted\.txt", response.text)))
    if not found:
        raise ValueError("Florida's statistics page links no Stats_<election>_VbmVoted.txt file")
    pages = {}
    for election in found:
        for kind in ("VbmVoted", "EarlyVoted"):
            url = FL_STATS.format(number=election, kind=kind)
            got = client.get(url)
            got.raise_for_status()
            pages[url] = (got.content, ".txt")
    return pages


def resolve(conn: Connection) -> tuple[dict[tuple[date, str], UUID], dict[str, UUID]]:
    elections: dict[tuple[date, str], UUID] = {}
    for value, entity in conn.execute(
        """select x.value, x.entity_id from external_id x
           where x.scheme = 'us.election' and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:FL(:primary)?$'"""
    ).fetchall():
        elections[(date.fromisoformat(value[:10]), "primary" if value.endswith(":primary") else "general")] = entity
    counties = {
        plain(text): unit
        for unit, text in conn.execute(
            """select u.id, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like '12%'"""
        ).fetchall()
    }
    return elections, counties


def write(conn: Connection, feed: str, fetch: int, rows: list[Row], *, final: bool = False) -> tuple[int, set[str]]:
    elections, counties = resolve(conn)
    held = {
        (election, unit, channel, as_of): count
        for election, unit, channel, as_of, count in conn.execute(
            """select b.election_id, b.unit_id, b.channel, b.as_of, b.count from live.ballots b
               join live.fetch x on x.id = b.fetch_id where x.feed_id = %s and b.stage = 'returned'""",
            [feed],
        ).fetchall()
    }
    latest: dict[tuple[UUID, UUID, int], tuple[datetime, int]] = {}
    for (election, unit, channel, as_of), count in held.items():
        if (election, unit, channel) not in latest or latest[(election, unit, channel)][0] < as_of:
            latest[(election, unit, channel)] = (as_of, count)
    missing: set[str] = set()
    fresh: dict[tuple[UUID, UUID, int, datetime], int] = {}
    for day, kind, county, channel, as_of, count in rows:
        election = elections.get((day, kind)) or elections.get((day, "general"))
        unit = counties.get(plain(county))
        if election is None or unit is None:
            missing.add(f"{day} {kind}" if election is None else county)
            continue
        key = (election, unit, channel, as_of)
        if key in held or key in fresh:
            continue
        if final and (election, unit, channel) in latest and latest[(election, unit, channel)][1] == count:
            continue
        fresh[key] = count
    with conn.cursor().copy(
        "copy live.ballots (fetch_id, election_id, unit_id, as_of, stage, channel, count) from stdin"
    ) as copy:
        for (election, unit, channel, as_of), count in fresh.items():
            copy.write_row((fetch, election, unit, as_of, "returned", channel, count))
    return len(fresh), missing


def archive(conn: Connection, feed: str) -> tuple[int, int, set[str]]:
    done = {
        sha
        for (sha,) in conn.execute("select sha256 from live.fetch where feed_id = %s and sha256 is not null", [feed])
    }
    held = raw.keys(SOURCE)
    captures = sorted(
        (key for key in held if key.startswith("https://web.archive.org/web/") and "PublicStats" in key),
        key=lambda key: key.split("/")[4],
    )
    sources: list[tuple[str, Callable[[Path], Iterator[Row]], bool]] = [
        (key, lambda path: page(path.read_bytes()), False) for key in captures
    ]
    sources += [(url, lambda path, found=found: pdf(path, *found), True) for url, found in FL_PDFS.items() if url in held]
    sources += [
        (key, lambda path: daily(path.read_bytes()), False) for key in sorted(held) if key.endswith("#county-day")
    ]
    fetches = written = 0
    missing: set[str] = set()
    for key, parse, final in sources:
        artifact = raw.cached(SOURCE, key)
        if artifact.sha256 in done:
            continue
        done.add(artifact.sha256)
        rows = list(parse(artifact.path))
        fetch = conn.execute(
            """insert into live.fetch (feed_id, fetched_at, as_of, sha256, path, status, detail)
               values (%s, %s, %s, %s, %s, 'frame', %s) returning id""",
            [
                feed,
                artifact.retrieved_at,
                max((row[4] for row in rows), default=None),
                artifact.sha256,
                key,
                "per-voter early voting file, totaled by county and vote day"
                if key.endswith("#county-day")
                else "archived report",
            ],
        ).fetchall()[0][0]
        count, lost = write(conn, feed, fetch, rows, final=final)
        fetches += 1
        written += count
        missing |= lost
    return fetches, written, missing


def poll(conn: Connection, feed: str, pages: dict[str, tuple[bytes, str]], fetched_at: datetime, folder: Path) -> str:
    fetches, archived, missing = archive(conn, feed)
    digest = hashlib.sha256()
    for url in sorted(pages):
        digest.update(url.encode() + b"\0" + pages[url][0])
    sha = digest.hexdigest()
    last = conn.execute(
        "select sha256 from live.fetch where feed_id = %s and path not like 'http%%' order by id desc limit 1", [feed]
    ).fetchone()
    rows = [row for content, _ in pages.values() for row in tsv(content)]
    if last is not None and last[0] == sha:
        conn.execute(
            """insert into live.fetch (feed_id, fetched_at, sha256, status) values (%s, %s, %s, 'unchanged')""",
            [feed, fetched_at, sha],
        )
        return "unchanged" + (f", {archived} archived rows from {fetches} reports" if fetches else "")
    folder.mkdir(parents=True, exist_ok=True)
    for index, url in enumerate(sorted(pages)):
        (folder / f"{index:03d}{pages[url][1]}").write_bytes(pages[url][0])
    fetch = conn.execute(
        """insert into live.fetch (feed_id, fetched_at, as_of, sha256, path, status)
           values (%s, %s, %s, %s, %s, 'frame') returning id""",
        [feed, fetched_at, max((row[4] for row in rows), default=None), sha, str(folder)],
    ).fetchall()[0][0]
    written, lost = write(conn, feed, fetch, rows)
    missing |= lost
    return (
        f"{written} ballot rows"
        + (f", {archived} archived rows from {fetches} reports" if fetches else "")
        + (f", unmatched {', '.join(sorted(missing))}" if missing else "")
    )
