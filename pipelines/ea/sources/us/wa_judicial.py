import html
import re
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.wa_judicial"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Washington Secretary of State"
TITLE = "Washington general election results, Supreme Court justices 2000-2012"
LANDING = "https://www.sos.wa.gov/elections/data-research/election-data-and-maps/election-results-and-voters-pamphlets"
LIVE = "https://results.vote.wa.gov/results/{day:%Y%m%d}/Supreme-Court-Justice-Position-{post}_ByCounty.html"
LEGACY = "https://web.archive.org/web/2016if_/http://www.sos.wa.gov/elections/results_report.aspx?e={election}&c=&c2=&t=&t2=&p=&p2=&y="
BREAKDOWN = "https://web.archive.org/web/2016if_/http://www.sos.wa.gov/elections/results_report_county_breakdown.aspx?e={election}&t={contest}"
LIVE_POSITIONS = {
    date(2008, 11, 4): (3, 4, 7),
    date(2010, 11, 2): (1, 5, 6),
    date(2012, 11, 6): (2, 8, 9),
}
LEGACY_ELECTIONS = {date(2000, 11, 7): 20, date(2002, 11, 5): 4}
METHOD = (
    "County totals are from the Secretary of State's official results for the general election; "
    "each candidate's county sum equals the printed state total."
)
PAUSE = 1.2
ATTEMPTS = 6
last = 0.0


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
        except httpx.TransportError:
            last = time.monotonic()
            delay *= 2
        else:
            last = time.monotonic()
            return artifact.path.read_text()
    raise RuntimeError(f"{url} kept failing")


def cells(row: str) -> list[str]:
    return [
        " ".join(html.unescape(re.sub(r"<[^>]+>", "", cell)).split())
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
    ]


def live_table(text: str) -> dict[str, dict[str, int]]:
    found = {}
    for name, body in re.findall(
        r'<div class="Name">(.*?)</div>(.*?)(?=<div class="Name">|<div id="Counties|$)', text, re.DOTALL
    ):
        found[html.unescape(name).removesuffix(" County")] = {
            html.unescape(candidate).strip(): int(votes.replace(",", ""))
            for candidate, votes in re.findall(r"</div>([^<]+)<br /><br />([\d,]+)</div>", body)
        }
    return found


def legacy_contests(text: str) -> list[tuple[str, int, dict[str, int]]]:
    found = []
    for block in re.findall(r'<div class="table-responsive">(.*?)</div>', text, re.DOTALL):
        title = re.search(r'OfficeName"><h3[^>]*>State Supreme Court Justice Position #(\d+)</h3>', block)
        link = re.search(r"breakdown\.aspx\?e=\d+&amp;t=(\d+)", block)
        if title is None or link is None:
            continue
        totals = {}
        for row in re.findall(r"<tr>(.*?)</tr>", block, re.DOTALL):
            row_cells = cells(row)
            if len(row_cells) == 4 and row_cells[2].isdigit():
                totals[row_cells[0]] = int(row_cells[2])
        found.append((title[1], int(link[1]), totals))
    return found


def legacy_table(text: str) -> dict[str, dict[str, int]]:
    found = {}
    for name, body in re.findall(
        r'OfficeName" colspan="3"><h3[^>]*>(.*?)</h3></td></tr>(.*?)(?=<tr class="info">|</table>)', text, re.DOTALL
    ):
        rows = {}
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL):
            row_cells = cells(row)
            if len(row_cells) == 3 and row_cells[2].isdigit():
                rows[row_cells[0]] = int(row_cells[2])
        found[html.unescape(name).strip()] = rows
    return found


def sources() -> list[tuple[date, str, dict[str, dict[str, int]], dict[str, int] | None, list[str]]]:
    found = []
    for day, positions in LIVE_POSITIONS.items():
        for post in positions:
            url = LIVE.format(day=day, post=post)
            text = page(url)
            if text is None:
                raise ValueError(f"no page for {url}")
            table = live_table(text)
            total = table.pop("Total")
            found.append((day, str(post), table, total, [url]))
    for day, election in LEGACY_ELECTIONS.items():
        index = LEGACY.format(election=election)
        text = page(index)
        if text is None:
            raise ValueError(f"no index for {index}")
        for post, contest, printed in legacy_contests(text):
            url = BREAKDOWN.format(election=election, contest=contest)
            body = page(url)
            if body is None:
                raise ValueError(f"no county breakdown for {url}")
            tables = legacy_table(body)
            found.append((day, post, tables, printed, [index, url]))
    return found


def fetch() -> None:
    sources()


def parse() -> None:
    with connect() as conn:
        fips = {re.sub(r"[^a-z]", "", name): code for name, code in openelections.counties(conn)["WA"].items()}
    rows = []
    urls = []
    for day, post, table, printed, pages in sources():
        urls.extend(pages)
        counties = table
        sums: dict[str, int] = {}
        for county, votes in counties.items():
            code = fips[re.sub(r"[^a-z]", "", county.lower().removesuffix(" county"))]
            for candidate, count in votes.items():
                sums[candidate] = sums.get(candidate, 0) + count
                rows.append((day, post, candidate.upper(), count, code))
        if printed is not None and sums != printed:
            raise ValueError(f"{day} position {post}: county sums {sums} differ from printed {printed}")
        print(day, post, len(counties), sums)
    frame = pl.DataFrame(
        rows,
        schema={"day": pl.Date, "post": pl.String, "candidate": pl.String, "votes": pl.Int64, "county": pl.String},
        orient="row",
    ).select(
        year=pl.col("day").dt.year().cast(pl.Int32),
        day="day",
        state=pl.lit("WA"),
        title=pl.lit("Justice of the Supreme Court"),
        post="post",
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party=pl.lit(None, pl.String),
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(39),
    ).write_parquet(PARSED / "races.parquet")
    (PARSED / "urls.txt").write_text("\n".join(urls))


def emit() -> Path:
    urls = (PARSED / "urls.txt").read_text().splitlines()

    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for url in urls:
            cs.artifact(raw.cached(SOURCE, url), source)
        return {"WA": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Washington Supreme Court justice contests of 2000-2012 by county from the Secretary of State's results",
        cite=cite,
        method=METHOD,
        absent="The results have no row for {counties} in this race; those votes are missing from these totals.",
        close="The race was within 0.5% in these totals, so no winner is recorded here; the official canvass decided it.",
    )
