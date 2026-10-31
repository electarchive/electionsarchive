import csv
import gzip
import html
import json
import re
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlparse

import httpx

from ea import coverage
from ea.raw import USER_AGENT
from ea.store import connect

REGISTRY = coverage.REPORTS / "us-portals.csv"
COLUMNS = (
    "state",
    "county_fips",
    "county",
    "platform",
    "tenant",
    "status",
    "elections",
    "first",
    "last",
    "evidence",
    "checked_at",
    "pinned",
)
CLARITY = "https://results.enr.clarityelections.com"
EV = "https://app.enhancedvoting.com/results/public"
STATIC = "https://www.livevoterturnout.com/ENR"
SEEDS = {
    "TX": "https://capitol.texas.gov/Resources/LocalLinks.aspx",
    "PA": "https://www.pa.gov/agencies/vote/contact-us/contact-your-election-officials",
}
HOP1 = re.compile(r"elect|vot|result|return|canvass", re.IGNORECASE)
HOP2 = re.compile(r"result|return|canvass|archive|past|histor|previous|prior", re.IGNORECASE)
SKIP = re.compile(
    r"\.(pdf|xlsx?|docx?|zip|csv|jpe?g|png|gif|mp4|txt)(\?|$)|mailto:|tel:|javascript:|facebook|twitter|instagram|"
    r"youtube|linkedin",
    re.IGNORECASE,
)
CHALLENGE = re.compile(r"cf-chl|Just a moment\.\.\.|captcha|Attention Required|incapsula|px-captcha", re.IGNORECASE)
SUITE = re.compile(
    r"CountySuite Elections Results|Default\.aspx\?(?:[^\"' ]*&(?:amp;)?)?PageLayout=SUMMARY", re.IGNORECASE
)

guard = threading.Lock()
locks: dict[str, threading.Lock] = {}
last: dict[str, float] = {}
robots: dict[str, urllib.robotparser.RobotFileParser] = {}
stopped: set[str] = set()
client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)


def get(url: str) -> tuple[int, bytes, str]:
    parts = urlparse(url)
    host = parts.netloc.lower()
    with guard:
        lock = locks.setdefault(host, threading.Lock())
    with lock:
        if host in stopped:
            return -3, b"", url
        if host not in robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                response = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
                text = response.text if response.status_code == 200 else ""
                parser.parse([] if "<html" in text[:500].lower() else text.splitlines())
            except httpx.HTTPError:
                parser.parse([])
            robots[host] = parser
            last[host] = time.monotonic()
        if not robots[host].can_fetch(USER_AGENT, url):
            return -2, b"", url
        time.sleep(max(0.0, 1.0 - (time.monotonic() - last.get(host, 0.0))))
        try:
            response = client.get(url)
        except httpx.HTTPError:
            last[host] = time.monotonic()
            return -1, b"", url
        last[host] = time.monotonic()
        if response.status_code in (429, 503):
            stopped.add(host)
        body = response.content
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return response.status_code, body, str(response.url)


def clarity(st: str, name: str) -> dict[str, str] | None:
    tenant = f"{CLARITY}/{st}/{name.replace(' ', '_')}/"
    status, body, _ = get(f"{tenant}elections.json")
    if status == 404:
        return None
    if status != 200:
        return {"platform": "clarity", "tenant": tenant, "status": "blocked", "evidence": f"{tenant} {status}"}
    try:
        days = [datetime.strptime(e["Date"].split()[0], "%m/%d/%Y").date() for e in json.loads(body)]
    except ValueError, KeyError, TypeError:
        days = []
    return {"platform": "clarity", "tenant": tenant, "status": "listed" if days else "empty", **span(days)}


def enhanced(st: str, slug: str) -> dict[str, str] | None:
    status, body, _ = get(f"{EV}/api/jurisdictions/{slug}")
    if status != 200 or not body:
        return None
    try:
        days = [date.fromisoformat(e["electionDate"]) for e in json.loads(body).get("elections") or []]
    except ValueError, KeyError, TypeError, AttributeError:
        days = []
    if not days:
        return None
    return {"platform": "enhanced_voting", "tenant": f"{EV}/{slug}", "status": "listed", **span(days)}


def static(st: str, name: str) -> dict[str, str] | None:
    tenant = f"{STATIC}/{re.sub(r'[^a-z]', '', name.lower())}{st.lower()}enr/"
    status, _, _ = get(tenant)
    if status != 403:
        return None
    found, misses, n = [], 0, 0
    while misses < 5 and n < 200:
        n += 1
        status, _, _ = get(f"{tenant}{n}/en/Index_{n}.html")
        if status == 200:
            found.append(n)
            misses = 0
        else:
            misses += 1
    return {
        "platform": "hosted_static",
        "tenant": tenant,
        "status": "listed" if found else "empty",
        "elections": str(len(found)),
        "first": str(found[0]) if found else "",
        "last": str(found[-1]) if found else "",
        "evidence": tenant,
    }


def span(days: list[date]) -> dict[str, str]:
    return {
        "elections": str(len(days)),
        "first": str(min(days)) if days else "",
        "last": str(max(days)) if days else "",
    }


def links(page: str, base: str) -> list[tuple[str, str]]:
    found = []
    for match in re.finditer(
        r"<(?:a|iframe)\b[^>]*?(?:href|src)\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.{0,200}?)(?:</a>|</iframe>|$)",
        page,
        re.IGNORECASE | re.DOTALL,
    ):
        text = html.unescape(re.sub(r"<[^>]+>|\s+", " ", match.group(2))).strip()
        found.append((urljoin(base, html.unescape(match.group(1).strip())), text))
    return found


def seeds(st: str, counties: dict[str, str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if st in SEEDS:
        status, body, final = get(SEEDS[st])
        page = html.unescape(html.unescape(body.decode("utf-8", "replace"))) if status == 200 else ""
        if st == "PA":
            parts = re.split(r'"dc:title":"([A-Za-z ]+) County"', page)
            for i in range(1, len(parts), 2):
                urls = [
                    u
                    for u in re.findall(r'href="(https?://[^"]+)"', parts[i + 1][:6000])
                    if not re.search(r"(^|\.)pa\.gov$", urlparse(u).netloc) and "coveo" not in u
                ]
                found.setdefault(parts[i], urls[:3])
        else:
            for url, text in links(page, final):
                if urlparse(url).netloc and "capitol.texas.gov" not in url:
                    found.setdefault(text, [url])
    for name in counties.values():
        if found.get(name):
            continue
        state_name = {
            "TX": "Texas",
            "PA": "Pennsylvania",
            "IN": "Indiana",
            "MI": "Michigan",
            "IL": "Illinois",
            "WI": "Wisconsin",
        }.get(st, st)
        try:
            response = httpx.post(
                "https://mcp.exa.ai/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "web_search_exa",
                        "arguments": {
                            "query": f"{name} County {state_name} election results",
                            "objective": f"official {name} County, {st} elections page",
                            "numResults": 3,
                        },
                    },
                },
                headers={"Accept": "application/json, text/event-stream", "User-Agent": "curl/8.9.1"},
                timeout=120,
            )
            text = "\n".join(line[5:] for line in response.text.splitlines() if line.startswith("data:"))
            urls = re.findall(r"URL: (\S+)", text.replace("\\n", "\n"))
        except httpx.HTTPError:
            urls = []
        found[name] = [u for u in urls if not re.search(rf"(^|\.)({st.lower()}\.gov|sos\.)", urlparse(u).netloc)][:3]
    return found


def crawl(starts: list[str]) -> list[dict[str, str]]:
    hits: dict[str, dict[str, str]] = {}
    queue = [(url, 0) for url in starts if not SKIP.search(url)]
    homes = {".".join(urlparse(url).netloc.lower().split(".")[-2:]) for url in starts}
    seen: set[str] = set()
    while queue and len(seen) < 15:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        status, body, final = get(url)
        page = body[:3_000_000].decode("utf-8", "replace")
        if status != 200 and (status in (202, 403) or CHALLENGE.search(page[:20000])):
            hits.setdefault("blocked", {"platform": "site", "tenant": url, "status": "blocked", "evidence": url})
            continue
        if SUITE.search(page):
            base = final.split("?")[0]
            hits.setdefault(
                "countysuite", {"platform": "countysuite", "tenant": base, "status": "lead", "evidence": final}
            )
        for target, text in links(page, final):
            host = urlparse(target).netloc.lower()
            for platform, prefix in (("clarity", CLARITY), ("enhanced_voting", EV), ("hosted_static", STATIC)):
                if target.startswith(prefix):
                    hits.setdefault(
                        platform, {"platform": platform, "tenant": target, "status": "lead", "evidence": final}
                    )
            if SKIP.search(target) or not target.startswith("http"):
                continue
            if ".".join(host.split(".")[-2:]) not in homes:
                continue
            if depth == 0 and (HOP1.search(text) or HOP1.search(target)):
                queue.append((target, 1))
            elif depth == 1 and (HOP2.search(text) or HOP2.search(target)):
                queue.append((target, 2))
    return list(hits.values())


def census(st: str, only: list[str] | None = None) -> str:
    with connect() as conn:
        counties = dict(
            conn.execute(
                """select u.code, n.text from reporting_unit u
                   join area_code c on c.area_id = u.area_id and c.code = u.code
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   join area a on a.id = u.area_id
                   join name o on o.entity_id = u.area_id and o.kind = 'official'
                   where u.scheme_code = 'us.county' and o.text like %s
                   order by u.code""",
                [f"% County, {st}"],
            ).fetchall()
        )
    if not counties:
        raise SystemExit(f"no counties found for {st}")
    checked = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: dict[tuple[str, str], dict[str, str]] = {}

    def add(fips: str, found: dict[str, str] | None) -> None:
        if found is None:
            return
        row = {column: "" for column in COLUMNS} | {"state": st, "county_fips": fips, "county": counties[fips]}
        row |= found | {"checked_at": checked}
        row["evidence"] = row["evidence"] or row["tenant"]
        key = (fips, row["platform"])
        if key not in rows or rows[key]["status"] in ("lead", "blocked"):
            rows[key] = row

    def probe(platform: str) -> None:
        for fips, name in counties.items():
            slug = f"{name.lower().replace(' ', '-')}-county-{st.lower()}"
            add(
                fips,
                {"clarity": clarity, "enhanced_voting": enhanced, "hosted_static": static}[platform](
                    st, slug if platform == "enhanced_voting" else name
                ),
            )

    probed = only or ["clarity", "enhanced_voting", "hosted_static"]
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(probe, probed))
    found = {} if only else seeds(st, counties)
    names = {name: fips for fips, name in counties.items()}
    with ThreadPoolExecutor(8) as pool:
        crawled = list(
            pool.map(lambda item: (item[0], crawl(item[1])), [(n, u) for n, u in found.items() if n in names])
        )
    for name, hits in crawled:
        for hit in hits:
            if (
                hit["platform"] in ("clarity", "enhanced_voting", "hosted_static")
                and (names[name], hit["platform"]) in rows
            ):
                continue
            add(names[name], hit)
    kept = []
    if REGISTRY.exists():
        with REGISTRY.open() as file:
            kept = [
                row
                for row in csv.DictReader(file)
                if row["state"] != st or row["pinned"] or (only and row["platform"] not in only)
            ]
    pinned = {(row["county_fips"], row["platform"]) for row in kept if row["state"] == st}
    merged = kept + [row for key, row in rows.items() if key not in pinned]
    merged.sort(key=lambda row: (row["state"], row["county_fips"], row["platform"]))
    with REGISTRY.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(merged)
    tally: dict[str, int] = {}
    for row in rows.values():
        tally[f"{row['platform']} {row['status']}"] = tally.get(f"{row['platform']} {row['status']}", 0) + 1
    return f"{st}: {len(counties)} counties; " + ", ".join(f"{key} {count}" for key, count in sorted(tally.items()))
