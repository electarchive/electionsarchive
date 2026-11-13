import contextlib
import hashlib
import io
import json
import time
import tomllib
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx

from ea.sources.us import (
    alameda_rov_live,
    as_live,
    civicapi,
    clarity_local_live,
    county_live,
    dc_boe_live,
    enr_2026,
    guam_live,
    lavote_live,
    livevoter_live,
    mp_cec_live,
    npr_live,
    nv_independent,
    nyc_enr_live,
    vi_live,
    webb_live,
)
from ea.store import DATA, Connection, connect

FEEDS = Path(__file__).with_name("live_feeds.toml")
RAW = DATA / "live" / "raw"
DROP = 0.5
FAILED = {202, 401, 403, 408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


class LastGood(httpx.BaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport()
        self.kept: dict[tuple[str, str, bytes], tuple[list[tuple[str, str]], bytes]] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, str(request.url), request.read())
        try:
            response = self.inner.handle_request(request)
            content = response.read()
        except httpx.TransportError:
            if key not in self.kept:
                raise
            return self.replay(key, request)
        headers = [(k, v) for k, v in response.headers.multi_items() if k not in ("content-encoding", "content-length")]
        if response.status_code == 200 and content:
            self.kept[key] = (headers, content)
        elif key in self.kept and (response.status_code in FAILED or response.status_code == 200):
            return self.replay(key, request)
        return httpx.Response(response.status_code, headers=headers, content=content, request=request)

    def replay(self, key: tuple[str, str, bytes], request: httpx.Request) -> httpx.Response:
        headers, content = self.kept[key]
        return httpx.Response(200, headers=headers, content=content, request=request)


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
    elif adapter == "us.civicapi":
        civicapi.collect(client, params["state"], lambda url, content, suffix: pages.update({url: (content, suffix)}))
    elif adapter == "us.npr":
        npr_live.collect(client, params["state"], lambda url, content, suffix: pages.update({url: (content, suffix)}))
    elif adapter == "us.nv_independent":
        nv_independent.collect(client, lambda url, content, suffix: pages.update({url: (content, suffix)}))
    elif adapter == "us.nyc_enr":
        pages = nyc_enr_live.collect(client)
    elif adapter == "us.dc_boe_live":
        pages = dc_boe_live.collect(client)
    elif adapter == "us.guam_live":
        pages = guam_live.collect(client)
    elif adapter == "us.mp_cec_live":
        pages = mp_cec_live.collect(client)
    elif adapter == "us.vi_live":
        pages = vi_live.collect(client)
    elif adapter == "us.lavote":
        pages = lavote_live.collect(client)
    elif adapter == "us.livevoter":
        pages = livevoter_live.collect(client, params["url"])
    elif adapter == "us.alameda_live":
        pages = alameda_rov_live.collect(client)
    elif adapter == "us.clarity_live":
        pages = clarity_local_live.collect(client, params["state"], params["tenant"], params["eid"])
    elif adapter == "us.county_live":
        pages = county_live.collect(client, params["kind"], params["url"])
    elif adapter == "us.webb_live":
        pages = webb_live.collect(client)
    elif adapter == "us.as_live":
        pages = as_live.collect(client)
    else:
        raise ValueError(f"unknown adapter {adapter}")
    with connect() as conn:
        if not any(not url.endswith(enr_2026.LISTINGS) for url in pages):
            record(conn, feed, fetched_at, None, None, None, "unchanged", "waiting: no result pages yet")
            return "waiting: no result pages yet"
        digest = hashlib.sha256(params.get("revision", "").encode())
        if "state" in params:
            published = conn.execute(
                "select count(*) from external_id where scheme = 'us.contest' and value like %s",
                [f"{enr_2026.DAY}:{params['state']}:%"],
            ).fetchall()
            digest.update(f"published={published}".encode())
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
        partial = folder / ".index.json.part"
        partial.write_text(json.dumps(index, indent=1))
        partial.replace(folder / "index.json")
        tallies, unmatched = (
            enr_2026.plan(conn, enr_2026.table({params["state"]: paths}), lambda _, __: None)
            if adapter == "us.enr_2026"
            else enr_2026.plan(conn, civicapi.frame(params["state"], paths), lambda _, __: None)
            if adapter == "us.civicapi"
            else enr_2026.plan(conn, npr_live.frame(params["state"], paths), lambda _, __: None)
            if adapter == "us.npr"
            else enr_2026.plan(conn, nv_independent.frame(paths), lambda _, __: None)
            if adapter == "us.nv_independent"
            else enr_2026.plan(conn, lavote_live.frame(paths), lambda _, __: None, also=("ceda",))
            if adapter == "us.lavote"
            else enr_2026.plan(conn, livevoter_live.frame(params["county"], paths), lambda _, __: None, also=("ceda",))
            if adapter == "us.livevoter"
            else enr_2026.plan(conn, alameda_rov_live.frame(paths), lambda _, __: None, also=("ceda",))
            if adapter == "us.alameda_live"
            else enr_2026.plan(
                conn, clarity_local_live.frame(params["state"], params["tenant"], paths), lambda _, __: None
            )
            if adapter == "us.clarity_live"
            else enr_2026.plan(
                conn,
                county_live.frame(params["kind"], params["state"], params["county"], paths),
                lambda _, __: None,
                also=("ceda",) if params["state"] == "CA" else (),
            )
            if adapter == "us.county_live"
            else webb_live.plan(conn, paths)
            if adapter == "us.webb_live"
            else as_live.plan(conn, paths)
            if adapter == "us.as_live"
            else dc_boe_live.plan(conn, paths)
            if adapter == "us.dc_boe_live"
            else guam_live.plan(conn, paths)
            if adapter == "us.guam_live"
            else mp_cec_live.plan(conn, paths)
            if adapter == "us.mp_cec_live"
            else vi_live.plan(conn, paths)
            if adapter == "us.vi_live"
            else nyc_enr_live.plan(conn, feed, paths)
        )
        expected = npr_live.expectations(conn, params["state"], paths) if adapter == "us.npr" else {}
        if adapter == "us.enr_2026" and enr_2026.county_only(params["state"], paths):
            tallies = [tally for tally in tallies if enr_2026.countywide(tally[1])]
        conn.execute(
            """update live.issue set resolved = true
               where feed_id = %s and kind = 'unmatched' and not resolved and detail <> all(%s)""",
            [feed, sorted(set(unmatched))],
        )
        for line in sorted(set(unmatched)):
            issue(conn, feed, "unmatched", line)
        written = 0
        frames: list[
            tuple[UUID, datetime, dict[tuple[UUID, UUID, int], int], tuple[int, int], dict[UUID, tuple[float, int]]]
        ] = []
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
            units = {unit for unit, _, _ in sums}
            told = {unit: value for unit, value in expected.get(contest_key, {}).items() if unit in units}
            previous = conn.execute(
                """select f.k, f.as_of from live.frame f
                   where f.feed_id = %s and f.contest_id = %s and f.status = 'active' order by f.as_of desc limit 1""",
                [feed, contest],
            ).fetchone()
            if previous is not None:
                if previous[1] > as_of:
                    continue
                stored = held(conn, previous[0])
                known = (
                    {
                        unit: (float(share), int(total))
                        for unit, share, total in conn.execute(
                            "select unit_id, share, expected from live.expectation where frame_k = %s", [previous[0]]
                        ).fetchall()
                    }
                    if adapter == "us.npr"
                    else {}
                )
                if stored == sums and known == told:
                    continue
                if sum(sums.values()) < DROP * sum(stored.values()):
                    issue(
                        conn,
                        feed,
                        "drop",
                        f"{contest_key}: total fell from {sum(stored.values())} to {sum(sums.values())}; frame held back",
                    )
                    continue
            frames.append((contest, as_of, sums, reporting, told))
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
        for contest, as_of, sums, reporting, told in frames:
            conn.execute(
                "delete from live.frame where feed_id = %s and contest_id = %s and as_of = %s", [feed, contest, as_of]
            )
            k = conn.execute(
                """insert into live.frame (feed_id, contest_id, as_of, fetch_id, reporting, expected)
                   values (%s, %s, %s, %s, %s, %s) returning k""",
                [feed, contest, as_of, fetch, reporting[0] or None, reporting[1] or None],
            ).fetchall()[0][0]
            write(conn, k, sums)
            if told:
                with conn.cursor().copy("copy live.expectation (frame_k, unit_id, share, expected) from stdin") as copy:
                    for unit, (share, total) in told.items():
                        copy.write_row((k, unit, share, total))
            written += 1
        return f"{written} frames" + (f", {len(set(unmatched))} unmatched" if unmatched else "")


def backfill(feed: str | None = None) -> str:
    counts: dict[str, int] = {}
    with connect() as conn:
        fetches = conn.execute(
            """select x.id, d.params->>'state', x.path from live.fetch x join live.feed d on d.id = x.feed_id
               where d.adapter = 'us.enr_2026' and x.status = 'frame' and x.path is not null
                 and (%s::text is null or x.feed_id = %s)
               order by x.id""",
            [feed, feed],
        ).fetchall()
        for fetch, state, path in fetches:
            for k, verdict, sums in rebuilt(conn, fetch, state, Path(path)):
                counts[verdict] = counts.get(verdict, 0) + 1
                if verdict == "split":
                    conn.execute("delete from live.value where frame_k = %s", [k])
                    write(conn, k, sums)
            conn.commit()
    return f"{len(fetches)} fetches: " + ", ".join(
        f"{count} frames {verdict}" for verdict, count in sorted(counts.items())
    )


def held(conn: Connection, k: int) -> dict[tuple[UUID, UUID, int], int]:
    return {
        (unit, option, channel): int(votes)
        for unit, option, channel, votes in conn.execute(
            "select unit_id, option_id, channel, votes from live.value where frame_k = %s", [k]
        ).fetchall()
    }


def rebuilt(
    conn: Connection, fetch: int, state: str, folder: Path
) -> list[tuple[int, str, dict[tuple[UUID, UUID, int], int]]]:
    frames = conn.execute("select k, contest_id from live.frame where fetch_id = %s", [fetch]).fetchall()
    if not (folder / "index.json").exists():
        return [(k, "without raw files", {}) for k, _ in frames]
    paths = {url: folder / name for url, name in json.loads((folder / "index.json").read_text()).items()}
    with contextlib.redirect_stdout(io.StringIO()):
        tallies, _ = enr_2026.plan(conn, enr_2026.table({state: paths}), lambda _, __: None)
    found = {contest: sums for _, _, contest, sums, _, _ in tallies}
    verdicts = []
    for k, contest in frames:
        stored = held(conn, k)
        kept = {(unit, option) for unit, option, _ in stored}
        sums = {key: votes for key, votes in found.get(contest, {}).items() if key[:2] in kept}
        verdicts.append(
            (
                k,
                "not rebuilt"
                if not sums
                else "with other totals"
                if enr_2026.totals(sums) != enr_2026.totals(stored)
                else "without a split"
                if not any(channel for _, _, channel in sums)
                else "already split"
                if sums == stored
                else "split",
                sums,
            )
        )
    return verdicts


def write(conn: Connection, k: int, sums: dict[tuple[UUID, UUID, int], int]) -> None:
    with conn.cursor().copy("copy live.value (frame_k, unit_id, option_id, channel, votes) from stdin") as copy:
        for (unit, option, channel), votes in sums.items():
            copy.write_row((k, unit, option, channel, votes))


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


QUIET_CAP_S = 240


def run(poller: str, once: bool = False) -> None:
    client = httpx.Client(follow_redirects=True, timeout=30, headers=enr_2026.HEADERS, transport=LastGood())
    due: dict[str, float] = {}
    quiet: dict[str, int] = {}
    running: dict[str, Future[str]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool, ThreadPoolExecutor(max_workers=6) as fallback:
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
                due[feed] = now + min(interval * 2 ** (quiet.get(feed, 0) // 10), max(interval, QUIET_CAP_S))
                running[feed] = (fallback if adapter == "us.civicapi" else pool).submit(
                    attempt, feed, adapter, params, client
                )
            for feed, future in list(running.items()):
                if future.done():
                    del running[feed]
                    quiet[feed] = quiet.get(feed, 0) + 1 if future.result() == "unchanged" else 0
            if once:
                for future in running.values():
                    future.result()
                return
            time.sleep(2)


def attempt(feed: str, adapter: str, params: dict[str, str], client: httpx.Client) -> str:
    started = time.monotonic()
    try:
        result = poll(feed, adapter, params, client)
    except Exception:
        result = "failed"
        with connect() as conn:
            record(conn, feed, datetime.now(UTC), None, None, None, "error", traceback.format_exc()[-2000:])
    print(f"{datetime.now(UTC):%H:%M:%S} {feed}: {result} ({time.monotonic() - started:.1f} s)", flush=True)
    return result


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
