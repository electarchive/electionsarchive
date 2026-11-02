import hashlib
import json
import time
import tomllib
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx

from ea.sources.us import enr_2026, nyc_enr_live
from ea.store import DATA, Connection, connect

FEEDS = Path(__file__).with_name("live_feeds.toml")
RAW = DATA / "live" / "raw"
DROP = 0.5


def sync() -> str:
    feeds = tomllib.loads(FEEDS.read_text())["feed"]
    with connect() as conn:
        for feed in feeds:
            conn.execute(
                """insert into live.feed (id, adapter, params, publisher, url, poller, interval_s, enabled, note)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (id) do update set adapter = excluded.adapter, params = excluded.params,
                     publisher = excluded.publisher, url = excluded.url, poller = excluded.poller,
                     interval_s = excluded.interval_s, enabled = excluded.enabled, note = excluded.note""",
                [
                    feed["id"],
                    feed["adapter"],
                    json.dumps(feed.get("params", {})),
                    feed["publisher"],
                    feed.get("url"),
                    feed.get("poller", "fsn1"),
                    feed.get("interval", 30),
                    feed.get("enabled", True),
                    feed.get("note"),
                ],
            )
        stale = conn.execute(
            "update live.feed set enabled = false where id <> all(%s) and enabled returning id",
            [[feed["id"] for feed in feeds]],
        ).fetchall()
    return f"{len(feeds)} feeds synced" + (f", {len(stale)} no longer listed disabled" if stale else "")


def poll(feed: str, adapter: str, params: dict[str, str], client: httpx.Client) -> str:
    fetched_at = datetime.now(UTC)
    pages: dict[str, tuple[bytes, str]] = {}
    if adapter == "us.enr_2026":
        enr_2026.collect(
            client, {params["state"]}, lambda _, url, content, suffix: pages.update({url: (content, suffix)})
        )
    elif adapter == "us.nyc_enr":
        pages = nyc_enr_live.collect(client)
    else:
        raise ValueError(f"unknown adapter {adapter}")
    with connect() as conn:
        if not any(not url.endswith(enr_2026.LISTINGS) for url in pages):
            record(conn, feed, fetched_at, None, None, None, "unchanged", "waiting: no result pages yet")
            return "waiting: no result pages yet"
        digest = hashlib.sha256()
        for url in sorted(pages):
            if not url.endswith(enr_2026.LISTINGS):
                digest.update(url.encode() + b"\0" + pages[url][0])
        sha = digest.hexdigest()
        last = conn.execute(
            "select sha256 from live.fetch where feed_id = %s and sha256 is not null order by id desc limit 1", [feed]
        ).fetchone()
        if last is not None and last[0] == sha:
            record(conn, feed, fetched_at, None, sha, None, "unchanged", None)
            return "unchanged"
        folder = RAW / feed / f"{fetched_at:%Y%m%dT%H%M%SZ}"
        folder.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        index = {}
        for number, (url, (content, suffix)) in enumerate(sorted(pages.items())):
            paths[url] = folder / f"{number:03d}{suffix}"
            paths[url].write_bytes(content)
            index[url] = paths[url].name
        (folder / "index.json").write_text(json.dumps(index, indent=1))
        tallies, unmatched = (
            enr_2026.plan(conn, enr_2026.table({params["state"]: paths}), lambda _, __: None)
            if adapter == "us.enr_2026"
            else nyc_enr_live.plan(conn, feed, paths)
        )
        for line in sorted(set(unmatched)):
            issue(conn, feed, "unmatched", line)
        written = 0
        frames: list[tuple[UUID, datetime, dict[tuple[UUID, UUID], int], tuple[int, int]]] = []
        for _, contest_key, contest, sums, reporting, stamp in tallies:
            as_of = datetime.fromisoformat(str(stamp)) if stamp else fetched_at
            if as_of.tzinfo is None or as_of > fetched_at + timedelta(minutes=10):
                issue(conn, feed, "stamp", f"{contest_key}: feed stamp {stamp} read as the fetch time")
                as_of = fetched_at
            if as_of.date() < enr_2026.DAY - timedelta(days=1):
                issue(conn, feed, "stamp", f"{contest_key}: feed stamp {stamp} predates the election; frame held back")
                continue
            if not any(sums.values()):
                continue
            previous = conn.execute(
                """select f.k, f.as_of from live.frame f
                   where f.feed_id = %s and f.contest_id = %s and f.status = 'active' order by f.as_of desc limit 1""",
                [feed, contest],
            ).fetchone()
            if previous is not None:
                if previous[1] > as_of:
                    continue
                stored = {
                    (unit, option): int(votes)
                    for unit, option, votes in conn.execute(
                        "select unit_id, option_id, votes from live.value where frame_k = %s", [previous[0]]
                    ).fetchall()
                }
                if stored == sums:
                    continue
                if sum(sums.values()) < DROP * sum(stored.values()):
                    issue(
                        conn,
                        feed,
                        "drop",
                        f"{contest_key}: total fell from {sum(stored.values())} to {sum(sums.values())}; frame held back",
                    )
                    continue
            frames.append((contest, as_of, sums, reporting))
        fetch = record(
            conn,
            feed,
            fetched_at,
            max((f[1] for f in frames), default=None),
            sha,
            str(folder),
            "frame" if frames else "unchanged",
            None,
        )
        for contest, as_of, sums, reporting in frames:
            k = conn.execute(
                """insert into live.frame (feed_id, contest_id, as_of, fetch_id, reporting, expected)
                   values (%s, %s, %s, %s, %s, %s)
                   on conflict (feed_id, contest_id, as_of) do update set fetch_id = excluded.fetch_id,
                     reporting = excluded.reporting, expected = excluded.expected, status = 'active'
                   returning k""",
                [feed, contest, as_of, fetch, reporting[0] or None, reporting[1] or None],
            ).fetchall()[0][0]
            conn.execute("delete from live.value where frame_k = %s", [k])
            with conn.cursor().copy("copy live.value (frame_k, unit_id, option_id, votes) from stdin") as copy:
                for (unit, option), votes in sums.items():
                    copy.write_row((k, unit, option, votes))
            written += 1
        return f"{written} frames" + (f", {len(set(unmatched))} unmatched" if unmatched else "")


def record(
    conn: Connection,
    feed: str,
    fetched_at: datetime,
    as_of: datetime | None,
    sha: str | None,
    path: str | None,
    status: str,
    detail: str | None,
) -> int:
    return conn.execute(
        """insert into live.fetch (feed_id, fetched_at, as_of, sha256, path, status, detail)
           values (%s, %s, %s, %s, %s, %s, %s) returning id""",
        [feed, fetched_at, as_of, sha, path, status, detail],
    ).fetchall()[0][0]


def issue(conn: Connection, feed: str, kind: str, detail: str) -> None:
    conn.execute(
        """insert into live.issue (feed_id, kind, detail) select %s, %s, %s
           where not exists (select from live.issue where feed_id = %s and detail = %s and not resolved)""",
        [feed, kind, detail, feed, detail],
    )


def run(poller: str, once: bool = False) -> None:
    client = httpx.Client(follow_redirects=True, timeout=30, headers=enr_2026.HEADERS)
    due: dict[str, float] = {}
    running: dict[str, Future[None]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        while True:
            with connect() as conn:
                feeds = conn.execute(
                    "select id, adapter, params, interval_s from live.feed where enabled and poller = %s order by id",
                    [poller],
                ).fetchall()
            now = time.monotonic()
            for feed, adapter, params, interval in feeds:
                if feed in running or due.get(feed, 0) > now:
                    continue
                due[feed] = now + interval
                running[feed] = pool.submit(attempt, feed, adapter, params, client)
            for feed, future in list(running.items()):
                if future.done():
                    del running[feed]
            if once:
                for future in running.values():
                    future.result()
                return
            time.sleep(2)


def attempt(feed: str, adapter: str, params: dict[str, str], client: httpx.Client) -> None:
    started = time.monotonic()
    try:
        result = poll(feed, adapter, params, client)
    except Exception:
        result = "failed"
        with connect() as conn:
            record(conn, feed, datetime.now(UTC), None, None, None, "error", traceback.format_exc()[-2000:])
    print(f"{datetime.now(UTC):%H:%M:%S} {feed}: {result} ({time.monotonic() - started:.1f} s)", flush=True)


def status() -> str:
    with connect() as conn:
        found = conn.execute(
            """select f.id, f.poller, f.enabled,
                 (select max(fetched_at) from live.fetch x where x.feed_id = f.id),
                 (select status from live.fetch x where x.feed_id = f.id order by id desc limit 1),
                 (select max(as_of) from live.frame r where r.feed_id = f.id and r.status = 'active'),
                 (select count(*) from live.frame r where r.feed_id = f.id and r.status = 'active'),
                 (select count(*) from live.issue i where i.feed_id = f.id and not i.resolved)
               from live.feed f order by f.id"""
        ).fetchall()
    lines = ["feed  poller  on  last fetch  last status  newest frame  frames  open issues"]
    for feed, poller, enabled, fetched, last, newest, frames, issues in found:
        lines.append(
            f"{feed}  {poller}  {'yes' if enabled else 'no'}  {fetched:%H:%M:%S}  {last}  {newest}  {frames}  {issues}"
            if fetched
            else f"{feed}  {poller}  {'yes' if enabled else 'no'}  never  -  -  0  {issues}"
        )
    return "\n".join(lines)
