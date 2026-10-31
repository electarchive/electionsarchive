import html
import json
import re
import time
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA, connect

SOURCE = "us.wa_wayback_1990s"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ELECTIONS = {3: "1992-11-03", 15: "1994-11-08", 17: "1996-11-05", 10: "1998-11-03"}
SNAPSHOT = "https://web.archive.org/web/2016if_/http://www.sos.wa.gov/elections"
PUBLISHER = "Washington Secretary of State"
TITLE = "Washington general election results, 1992-1998"
METHOD = (
    "County totals are from the Secretary of State's election results archive, read from the Internet Archive's "
    "2016 capture of each contest's county breakdown, each candidate's county sum compared with the printed state total."
)
PAUSE = 1.2
ATTEMPTS = 6
last = 0.0


def index_url(election: int) -> str:
    return f"{SNAPSHOT}/results_report.aspx?e={election}&c=&c2=&t=&t2=&p=&p2=&y="


def breakdown_url(election: int, contest: int) -> str:
    return f"{SNAPSHOT}/results_report_county_breakdown.aspx?e={election}&t={contest}"


def office(title: str) -> str | None:
    if title.startswith("President"):
        return "president"
    if title == "Governor":
        return "governor"
    if title == "U. S. Senator":
        return "senate"
    found = re.fullmatch(r"U\. S\. Representative District #(\d+)", title)
    return found[1] if found else None


def page(url: str) -> str | None:
    global last
    try:
        return raw.cached(SOURCE, url).path.read_text()
    except LookupError:
        pass
    delay = PAUSE
    for _ in range(ATTEMPTS):
        time.sleep(max(0.0, last + delay - time.monotonic()))
        try:
            artifact = raw.fetch(SOURCE, url, suffix=".html")
        except httpx.HTTPStatusError as error:
            last = time.monotonic()
            if error.response.status_code == 404:
                return None
            delay *= 2
            print(f"{error.response.status_code} for {url}; waiting {delay:.0f}s")
        except httpx.TransportError:
            last = time.monotonic()
            delay *= 2
            print(f"transport error for {url}; waiting {delay:.0f}s")
        else:
            last = time.monotonic()
            return artifact.path.read_text()
    raise RuntimeError(f"{url} kept failing")


def contests(text: str) -> list[tuple[str, str, int, dict[str, int]]]:
    found = []
    for block in re.findall(r'<div class="table-responsive">(.*?)</div>', text, re.DOTALL):
        title = re.search(r'OfficeName"><h3[^>]*>(.*?)</h3>', block)
        link = re.search(r"breakdown\.aspx\?e=\d+&amp;t=(\d+)", block)
        if title is None or link is None:
            continue
        name = html.unescape(title[1]).strip()
        key = office(name)
        if key is None:
            continue
        totals = {}
        for row in re.findall(r"<tr>(.*?)</tr>", block, re.DOTALL):
            cells = [
                html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            ]
            if len(cells) == 4 and cells[2].isdigit():
                totals[cells[0]] = int(cells[2])
        found.append((key, name, int(link[1]), totals))
    return found


def counties(text: str) -> dict[str, dict[str, int]]:
    found = {}
    for name, body in re.findall(
        r'OfficeName" colspan="3"><h3[^>]*>(.*?)</h3></td></tr>(.*?)(?=<tr class="info">|</table>)', text, re.DOTALL
    ):
        rows = {}
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL):
            cells = [
                html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            ]
            if len(cells) == 3 and cells[2].isdigit():
                rows[cells[0]] = int(cells[2])
        found[html.unescape(name).strip()] = rows
    return found


def fetch() -> None:
    missing = []
    for election in ELECTIONS:
        text = page(index_url(election))
        if text is None:
            missing.append(index_url(election))
            continue
        for _, name, contest, _ in contests(text):
            if page(breakdown_url(election, contest)) is None:
                missing.append(f"{election} {name} t={contest}")
    print(f"{len(missing)} captures missing", *missing, sep="\n")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '53%%'"""
            ).fetchall()
        }
    rows = []
    urls = []
    for election, day in ELECTIONS.items():
        text = page(index_url(election))
        if text is None:
            raise ValueError(f"no index capture for {election}")
        urls.append(index_url(election))
        for key, name, contest, printed in contests(text):
            body = page(breakdown_url(election, contest))
            if body is None:
                print(f"{day} {name}: no county capture")
                continue
            urls.append(breakdown_url(election, contest))
            table = counties(body)
            unknown = {county for county in table if re.sub(r"[^a-z]", "", county.lower()) not in codes}
            if unknown:
                raise ValueError(f"{day} {name}: unmatched counties {unknown}")
            sums: dict[str, int] = {}
            for county, votes in table.items():
                for candidate, count in votes.items():
                    sums[candidate] = sums.get(candidate, 0) + count
                    rows.append((day, key, codes[re.sub(r"[^a-z]", "", county.lower())], candidate, count))
            off = {c: (sums.get(c), printed.get(c)) for c in printed if sums.get(c) != printed.get(c)}
            print(day, name, "ties" if not off and set(sums) == set(printed) else f"differs {off}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )
    (PARSED / "urls.json").write_text(json.dumps(urls))


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "Washington's 1992-1998 president, Senate, governor, and House general elections by county from the Secretary of State's results archive",
        PUBLISHER,
        TITLE,
        "https://www.sos.wa.gov/elections/results_report.aspx",
        "WA",
        "53",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, url) for url in json.loads((PARSED / "urls.json").read_text())],
        lambda key: METHOD,
    )
