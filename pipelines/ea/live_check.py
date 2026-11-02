import io
import os
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import UTC, datetime

import httpx

from ea.live import FEEDS
from ea.sources.us import enr_2026, nyc_enr_live
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
WEB = os.environ.get("EA_LIVE_WEB", "http://localhost:5173")


class Enough(BaseException):
    pass


def host(feed: dict[str, object]) -> tuple[bool, str]:
    requests = 0

    def count(_: httpx.Request) -> None:
        nonlocal requests
        requests += 1
        if requests > 4:
            raise Enough

    client = httpx.Client(follow_redirects=True, timeout=20, headers=enr_2026.HEADERS, event_hooks={"request": [count]})
    seen: list[str] = []

    def store(_: str, url: str, content: bytes, suffix: str) -> None:
        seen.append(url)
        raise Enough

    try:
        if feed["adapter"] == "us.nyc_enr":
            seen.extend(nyc_enr_live.collect(client))
        else:
            params = feed.get("params")
            enr_2026.collect(client, {params["state"] if isinstance(params, dict) else ""}, store)
    except Enough:
        pass
    except Exception as error:
        return False, f"host: {type(error).__name__}: {str(error)[:80]}"
    return (bool(seen), "host: answers" if seen else "host: no result pages")


def page(client: httpx.Client, url: str) -> tuple[bool, str]:
    for attempt in range(2):
        try:
            response = client.get(url)
        except httpx.HTTPError as error:
            if attempt == 0:
                time.sleep(5)
                continue
            return False, f"{type(error).__name__}"
        if response.status_code == 503 and "dev server is off" in response.text:
            print("the dev server is off; stopping", file=sys.stderr)
            raise SystemExit(2)
        if response.status_code == 200:
            return True, "200"
        if attempt == 0 and response.status_code >= 500:
            time.sleep(5)
            continue
        return False, str(response.status_code)
    return False, "no answer"


def verdict(ok: bool) -> str:
    return "green" if ok else "red"


def run() -> int:
    feeds = tomllib.loads(FEEDS.read_text())["feed"]
    rows: list[tuple[str, str, str, str]] = []
    now = datetime.now(UTC)

    with redirect_stdout(io.StringIO()), ThreadPoolExecutor(max_workers=8) as pool:
        hosts = list(pool.map(host, feeds))

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

    covered: dict[str, str] = {}
    for feed, (host_ok, host_note) in zip(feeds, hosts, strict=True):
        feed_id = feed["id"]
        notes = [host_note]
        ok = host_ok
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
                age = (now - fetched).total_seconds()
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
        counts = issues.get(feed_id, {})
        if counts:
            notes.append("issues " + ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items())))
        rows.append((verdict(ok), feed_id, "; ".join(notes), "feed"))
        params = feed.get("params")
        if isinstance(params, dict) and "state" in params and ok:
            covered[params["state"]] = feed_id

    for state in STATES:
        feed_id = covered.get(state)
        listed = any(isinstance(f.get("params"), dict) and f["params"].get("state") == state for f in feeds)
        reason = f"feed {feed_id}" if feed_id else ("feed listed but red" if listed else "no statewide feed")
        rows.append((verdict(feed_id is not None), f"state {state}", reason, "state"))

    with httpx.Client(timeout=60, follow_redirects=True) as client:
        urls = [("page national", f"{WEB}/live/us/{ELECTION}")]
        urls += [(f"page {state}", f"{WEB}/live/us/{state.lower()}/{ELECTION}") for state in STATES]
        for name, url in urls:
            ok, note = page(client, url)
            rows.append((verdict(ok), name, note, "page"))

    width = max(len(row[1]) for row in rows)
    for color, name, reason, _ in rows:
        print(f"{color.upper():5}  {name:<{width}}  {reason}")
    red = sum(1 for row in rows if row[0] == "red")
    print(f"\n{len(rows) - red} green, {red} red")
    return 1 if red else 0
