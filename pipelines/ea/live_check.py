import io
import os
import re
import threading
import time
import tomllib
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import UTC, date, datetime
from statistics import median
from urllib.parse import urlsplit

import httpx
from psycopg import Connection

from ea.live import FEEDS
from ea.sources.us import alameda_rov_live, clarity_local_live, enr_2026, lavote_live, livevoter_live, nyc_enr_live
from ea.store import connect

STATES = [
    "AK",
    "AL",
    "AR",
    "AZ",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "IA",
    "ID",
    "IL",
    "IN",
    "KS",
    "KY",
    "LA",
    "MA",
    "MD",
    "ME",
    "MI",
    "MN",
    "MO",
    "MS",
    "MT",
    "NC",
    "ND",
    "NE",
    "NH",
    "NJ",
    "NM",
    "NV",
    "NY",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VA",
    "VT",
    "WA",
    "WI",
    "WV",
    "WY",
]
ELECTION = "2026-11-03-general"
DAY = date(2026, 11, 3)
CLOSE = datetime(2026, 11, 3, 23, 0, tzinfo=UTC)
FALL = (date(2026, 10, 1), date(2026, 12, 31))
WEBS = [
    web.rstrip("/")
    for web in os.environ.get("EA_LIVE_WEB", "http://localhost:5173,https://electionsarchive.com").split(",")
    if web
]
HUBS = int(os.environ.get("EA_LIVE_HUBS", "40"))
FED = 20
TURN = 900
SLOW = 2.0
DEADLINE = 100
DATES = (
    (re.compile(r"(?<!\d)(20\d\d)-(\d\d)-(\d\d)(?!\d)"), (1, 2, 3)),
    (re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(20\d\d)(?!\d)"), (3, 1, 2)),
)
COMPACT = re.compile(r"(?<!\d)(20\d\d)_?(\d\d)_?(\d\d)(?!\d)")
TYPES = {".md": "text/markdown", ".json": "application/json"}


class Enough(BaseException):
    pass


class Off(Exception):
    pass


class Placeholder(Exception):
    pass


def dates(text: str, patterns: tuple[tuple[re.Pattern[str], tuple[int, int, int]], ...]) -> list[date]:
    found = []
    for pattern, (y, m, d) in patterns:
        for match in pattern.finditer(text):
            try:
                found.append(date(int(match[y]), int(match[m]), int(match[d])))
            except ValueError:
                pass
    return found


def readable(content: bytes) -> str:
    if content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = archive.namelist()
                content = archive.read(names[0])[:2_000_000] if names else b""
        except zipfile.BadZipFile:
            return ""
    return content[:2_000_000].decode("utf-8", "replace")


def host(feed: dict[str, object]) -> tuple[bool, str, list[tuple[str, bytes]]]:
    poller = feed.get("poller", "fsn1")
    if poller != "fsn1":
        return True, f"host: polled from {poller}", []
    requests = 0

    def count(_: httpx.Request) -> None:
        nonlocal requests
        requests += 1
        if requests > 6:
            raise Enough

    client = httpx.Client(follow_redirects=True, timeout=20, headers=enr_2026.HEADERS, event_hooks={"request": [count]})
    pages: list[tuple[str, bytes]] = []

    def store(_: str, url: str, content: bytes, suffix: str) -> None:
        pages.append((url, content))
        if not url.endswith(enr_2026.LISTINGS):
            raise Enough

    try:
        if feed["adapter"] == "us.nyc_enr":
            pages.extend((url, content) for url, (content, _) in nyc_enr_live.collect(client).items())
        elif feed["adapter"] == "us.lavote":
            pages.extend((url, content) for url, (content, _) in lavote_live.collect(client).items())
        elif feed["adapter"] == "us.livevoter":
            params = feed["params"]
            if isinstance(params, dict):
                pages.extend(
                    (url, content) for url, (content, _) in livevoter_live.collect(client, params["url"]).items()
                )
        elif feed["adapter"] == "us.alameda_live":
            pages.extend((url, content) for url, (content, _) in alameda_rov_live.collect(client).items())
        elif feed["adapter"] == "us.clarity_live":
            params = feed["params"]
            if isinstance(params, dict):
                pages.extend(
                    (url, content)
                    for url, (content, _) in clarity_local_live.collect(
                        client, params["state"], params["tenant"], params["eid"]
                    ).items()
                )
        else:
            params = feed.get("params")
            enr_2026.collect(client, {params["state"] if isinstance(params, dict) else ""}, store)
    except Enough:
        pass
    except Exception as error:
        return False, f"host: {type(error).__name__}: {str(error)[:80]}", pages
    if not pages:
        return False, "host: no result pages", pages
    results = any(not url.endswith(enr_2026.LISTINGS) for url, _ in pages)
    return True, "host: answers" if results else "host: listing only", pages


def election(feed: dict[str, object], pages: list[tuple[str, bytes]]) -> tuple[bool, str | None]:
    if feed["adapter"] == "us.enr_2026" and enr_2026.DAY != DAY:
        return False, f"election day set to {enr_2026.DAY}"
    found = []
    for url, content in pages:
        if not url.endswith(enr_2026.LISTINGS):
            found += dates(url, ((COMPACT, (1, 2, 3)), *DATES)) + dates(readable(content), DATES)
    if not found:
        return True, None
    if any(FALL[0] <= day <= FALL[1] for day in found):
        return True, f"file dated {max(day for day in found if day <= FALL[1])}"
    return False, f"file dated {max(found)}, not the {DAY} election"


def page(client: httpx.Client, url: str, timeout: float = 20) -> tuple[bool, str, float]:
    note, ttfb = "no answer", 0.0
    for attempt in range(2):
        start = time.monotonic()
        try:
            with client.stream("GET", url, timeout=timeout) as response:
                ttfb = time.monotonic() - start
                body = response.read()
        except httpx.HTTPError as error:
            note = type(error).__name__
            if attempt == 0:
                time.sleep(5)
                continue
            return False, note, ttfb
        if response.status_code == 503 and b"dev server is off" in body:
            raise Off
        if attempt == 0 and response.status_code >= 500:
            time.sleep(5)
            continue
        if response.status_code != 200:
            return False, str(response.status_code), ttfb
        wanted = TYPES.get(os.path.splitext(urlsplit(url).path)[1])
        if wanted is not None and not response.headers.get("content-type", "").startswith(wanted):
            return False, f"200 but {response.headers.get('content-type')}", ttfb
        if wanted is None and b"__sveltekit" not in body:
            raise Placeholder
        if ttfb > SLOW:
            return False, f"slow: first byte after {ttfb:.1f} s", ttfb
        return True, f"200 in {ttfb:.2f} s", ttfb
    return False, note, ttfb


def origin(web: str, paths: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    label = urlsplit(web).netloc
    with httpx.Client(follow_redirects=True) as client:
        try:
            page(client, f"{web}/live/us/{ELECTION}", 60)
            with ThreadPoolExecutor(max_workers=6) as pool:
                checked = list(pool.map(lambda item: page(client, f"{web}{item[1]}"), paths))
        except Off:
            return [("red", f"origin {label}", "the dev server is off")]
        except Placeholder:
            return [("red", f"origin {label}", "serves a placeholder page, not the app")]
    rows = [(verdict(ok), f"{name} @{label}", note) for (name, _), (ok, note, _) in zip(paths, checked, strict=True)]
    times = sorted((ttfb, path) for (_, path), (_, _, ttfb) in zip(paths, checked, strict=True))
    middle = median(t for t, _ in times)
    rows.append(
        (
            "info",
            f"origin {label}",
            f"{len(paths)} pages, median first byte {middle:.2f} s, slowest {times[-1][0]:.2f} s ({times[-1][1]})",
        )
    )
    return rows


def hubs(conn: Connection) -> tuple[list[tuple[str, str]], str]:
    found = conn.execute(
        """with fed as (select distinct contest_id from live.frame where status = 'active')
           select s.path, array_agg(cs.path order by cs.path), bool_or(c.id in (select contest_id from fed))
           from election e join slug s on s.entity_id = e.id and s.is_current
           join contest c on c.election_id = e.id join slug cs on cs.entity_id = c.id and cs.is_current
           where e.date_from = %s and s.path ~ '^us/[a-z]{2}/[^/]+/'
           group by 1 order by 1""",
        [DAY],
    ).fetchall()
    turn = int(time.time() // TURN)
    fed = [row for row in found if row[2]][:FED]
    rest = [row for row in found if not row[2]]
    start = turn * HUBS % len(rest) if rest else 0
    chosen = fed + (rest[start:] + rest[:start])[:HUBS]
    paths = []
    for hub, contests, _ in chosen:
        paths.append((f"hub {hub}", f"/live/{hub}"))
        race = contests[turn % len(contests)]
        paths.append((f"race {race}", f"/live/{race}"))
    note = f"{len(chosen)} of {len(found)} hubs this run: {len(fed)} with frames, then {min(HUBS, len(rest))} from #{start}"
    return paths, note


def verdict(ok: bool, waiting: bool = False) -> str:
    if not ok:
        return "red"
    return "yellow" if waiting else "green"


def run() -> int:
    feeds = tomllib.loads(FEEDS.read_text())["feed"]
    rows: list[tuple[str, str, str]] = []
    now = datetime.now(UTC)

    with connect() as conn:
        stored = {
            row[0]: row[1:]
            for row in conn.execute(
                """select f.id, f.enabled, f.interval_s,
                     (select max(fetched_at) from live.fetch x where x.feed_id = f.id),
                     (select status from live.fetch x where x.feed_id = f.id order by id desc limit 1),
                     (select detail from live.fetch x where x.feed_id = f.id order by id desc limit 1)
                   from live.feed f"""
            ).fetchall()
        }
        issues: dict[str, dict[str, int]] = {}
        for feed, kind, count in conn.execute(
            "select feed_id, kind, count(*) from live.issue where not resolved group by 1, 2"
        ).fetchall():
            issues.setdefault(feed, {})[kind] = count
        regressions = {
            row[0]: row[1:]
            for row in conn.execute(
                """with t as (
                     select f.k, f.feed_id, f.contest_id, f.as_of, sum(v.votes) as total,
                       array_agg(distinct v.option_id order by v.option_id) as options
                     from live.frame f join live.value v on v.frame_k = f.k
                     where f.status = 'active' group by f.k),
                   p as (
                     select *, lag(total) over w as before, lag(options) over w as had
                     from t window w as (partition by feed_id, contest_id order by as_of, k))
                   select p.feed_id, count(*) filter (where total < before), count(*) filter (where options <> had),
                     min(s.path)
                   from p left join slug s on s.entity_id = p.contest_id and s.is_current
                   where before > 0 and (total < before or options <> had) group by 1"""
            ).fetchall()
        }
        elsewhere = dict(
            conn.execute(
                """select f.feed_id, count(distinct f.contest_id) from live.frame f
                   join contest c on c.id = f.contest_id join election e on e.id = c.election_id
                   where f.status = 'active' and e.date_from <> %s group by 1""",
                [DAY],
            ).fetchall()
        )
        switches = conn.execute("select name, shown, changed_at from live.switch order by name").fetchall()
        local, sampled = hubs(conn)

    paths = [("page national", f"/live/us/{ELECTION}")]
    paths += [(f"page {state}", f"/live/us/{state.lower()}/{ELECTION}") for state in STATES]
    paths += [(f"{name} {twin[1:]}", f"{path}{twin}") for name, path in list(paths) for twin in (".md", ".json")]
    paths += local

    tested: dict[int, tuple[bool, str, list[tuple[str, bytes]]]] = {}
    with redirect_stdout(io.StringIO()):
        threads = [
            threading.Thread(target=lambda n=n, f=f: tested.update({n: host(f)}), daemon=True)
            for n, f in enumerate(feeds)
        ]
        end = time.monotonic() + DEADLINE
        for thread in threads:
            thread.start()
        with ThreadPoolExecutor(max_workers=len(WEBS)) as pool:
            served = [rows for result in pool.map(origin, WEBS, [paths] * len(WEBS)) for rows in result]
        for thread in threads:
            thread.join(max(0, end - time.monotonic()))
    hosts = [tested.get(n, (False, f"host: no answer within {DEADLINE} s", [])) for n in range(len(feeds))]

    covered: dict[str, str] = {}
    pending: dict[str, str] = {}
    before = now < CLOSE
    for feed, (host_ok, host_note, pages) in zip(feeds, hosts, strict=True):
        feed_id = feed["id"]
        notes = [host_note]
        ok = host_ok
        waiting = False
        if before and not host_ok and host_note == "host: no result pages":
            ok = waiting = True
        dated, day_note = election(feed, pages)
        if before and not dated and day_note and day_note.startswith("file dated"):
            dated = waiting = True
        ok = ok and dated
        if day_note:
            notes.append(day_note)
        if waiting:
            notes.append(f"waiting for polls to close ({CLOSE:%Y-%m-%d %H:%M} UTC)")
        row = stored.get(feed_id)
        if row is None:
            ok = False
            notes.append("not synced into live.feed")
        else:
            enabled, interval, fetched, last, detail = row
            if not enabled:
                ok = False
                notes.append("disabled")
            if fetched is None:
                ok = False
                notes.append("never polled")
            else:
                age = max(0, (now - fetched).total_seconds())
                limit = max(300, 5 * interval)
                if age > limit:
                    ok = False
                notes.append(f"last poll {int(age)} s ago")
                if last == "error":
                    ok = False
                    notes.append(f"last status error: {(detail or '').splitlines()[-1][:60] if detail else ''}")
                elif last == "rejected":
                    ok = False
                    notes.append("last status rejected")
        if feed_id in elsewhere:
            ok = False
            notes.append(f"frames for {elsewhere[feed_id]} contests of another election")
        if feed_id in regressions:
            ok = False
            dropped, changed, example = regressions[feed_id]
            notes.append(f"frames: {dropped} total drops, {changed} candidate changes (e.g. {example})")
        counts = issues.get(feed_id, {})
        if counts.get("drop"):
            ok = False
        if counts:
            notes.append("issues " + ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items())))
        rows.append((verdict(ok, waiting), feed_id, "; ".join(notes)))
        params = feed.get("params")
        if isinstance(params, dict) and "state" in params and ok:
            (pending if waiting else covered)[params["state"]] = feed_id

    for state in STATES:
        feed_id = covered.get(state)
        waiting_id = pending.get(state) if feed_id is None else None
        listed = any(isinstance(f.get("params"), dict) and f["params"].get("state") == state for f in feeds)
        if feed_id:
            reason = f"feed {feed_id}"
        elif waiting_id:
            reason = f"feed {waiting_id} waiting for polls to close"
        else:
            reason = "feed listed but red" if listed else "no statewide feed"
        rows.append(
            (verdict(feed_id is not None or waiting_id is not None, waiting_id is not None), f"state {state}", reason)
        )

    rows += served
    rows.append(("info", "hubs", sampled))
    rows += [
        ("info", f"switch {name}", f"{'on' if shown else 'off'} since {changed:%Y-%m-%d %H:%M %Z}")
        for name, shown, changed in switches
    ]

    width = max(len(row[1]) for row in rows)
    for color, name, reason in rows:
        print(f"{color.upper():5}  {name:<{width}}  {reason}")
    red = sum(1 for row in rows if row[0] == "red")
    green = sum(1 for row in rows if row[0] == "green")
    yellow = sum(1 for row in rows if row[0] == "yellow")
    print(f"\n{green} green, {yellow} yellow, {red} red")
    return 1 if red else 0
