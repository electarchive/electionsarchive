"""Armenia's community head elections from the Central Electoral Commission (elections.am), 2005 to 2026.

The Commission lists every community head poll on one page that only a form post returns. Each poll's page lists its
candidates and, for the whole community or for one polling station chosen by a second form post, the protocol figures
and the votes of each candidate. The region and community pickers name the places.
"""

import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import httpx

from ea import raw

SOURCE = "am.local_heads"
SITE = "https://www.elections.am"
SECTION = f"{SITE}/Elections/HeadofCommunity"
TYPE = "4"
PAGE = 1000
WORKERS = 2


def clean(text: object) -> str:
    return " ".join(html.unescape(str(text if text is not None else "")).replace("\xa0", " ").split())


def list_url(page: int) -> str:
    return f"{SECTION}#list-page={page}-size={PAGE}"


def poll_url(poll: int, station: str = "") -> str:
    return f"{SECTION}#electionId={poll}" + (f"&subDistrictId={station}" if station else "")


def stations_url(poll: int) -> str:
    return f"{SITE}/Select/GetSubDistricts?electionId={poll}&regionCode=&communityCode=forElection&subDistrictId="


def regions_url() -> str:
    return f"{SITE}/Select/GetRegionsByElectionType?electionTypeId={TYPE}&regionCode="


def communities_url(region: str) -> str:
    return f"{SITE}/Select/GetCommunitiesByElectionType?electionTypeId={TYPE}&regionCode={region}&communityCode="


class Session:
    """The Commission's form, with its antiforgery token, which every listing and poll page is a post of."""

    def __init__(self) -> None:
        self.client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=180)
        form = self.client.get(SECTION).text
        token = re.search(r'__RequestVerificationToken" type="hidden" value="([^"]+)"', form)
        if token is None:
            raise LookupError(f"{SECTION} has no form")
        self.token = token.group(1)

    def post(self, **fields: str) -> httpx.Response:
        data = {
            "__RequestVerificationToken": self.token,
            "Culture": "hy",
            "Input.ElectionTypeId": TYPE,
            "Input.ElectionId": "0",
            "Current.ElectionId": "0",
            "Input.RegionCode": "",
            "Input.CommunityCode": "",
            "Input.SubDistrictId": "",
        }
        data.update(fields)
        for attempt in range(5):
            try:
                response = self.client.post(SECTION, data=data)
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
            except httpx.TransportError:
                pass
            time_sleep(2**attempt)
        raise LookupError(f"{SECTION} did not answer {fields}")


def time_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def keep(url: str, response: httpx.Response) -> raw.Artifact:
    return raw.store(
        SOURCE,
        url,
        response.content,
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", "text/html"),
    )


def listing() -> list[tuple[date, int, str]]:
    """Every poll the Commission lists, as (day, poll number, "region - community")."""
    out: list[tuple[date, int, str]] = []
    session: Session | None = None
    page = 1
    while True:
        try:
            text = raw.cached(SOURCE, list_url(page)).path.read_text(encoding="utf-8")
        except LookupError:
            session = session or Session()
            text = keep(
                list_url(page),
                session.post(**{"ElectionPaging.PageIndex": str(page), "ElectionPaging.PageSize": str(PAGE)}),
            ).path.read_text(encoding="utf-8")
        rows = re.findall(r"<td>(\d\d)\.(\d\d)\.(\d{4})</td>\s*<td>(.*?)</td>", text, re.DOTALL)
        for day, month, year, cell in rows:
            number = re.search(r"OpenElection\((\d+)\)", cell)
            if number is not None:
                out.append(
                    (date(int(year), int(month), int(day)), int(number.group(1)), clean(re.sub("<[^>]+>", " ", cell)))
                )
        if len(rows) < PAGE:
            return sorted(out)
        page += 1


def stations(poll: int) -> list[tuple[str, str]]:
    """The polling stations of a poll, as (station id, number)."""
    import json

    found = json.loads(raw.cached(SOURCE, stations_url(poll)).path.read_text(encoding="utf-8"))
    return [(str(row["value"]), clean(row["text"])) for row in found if row["value"]]


def fetch() -> None:
    polls = listing()
    print(f"{SOURCE}: {len(polls)} polls listed", file=sys.stderr, flush=True)
    regions = raw.fetch(SOURCE, regions_url(), suffix=".json")
    import json

    for row in json.loads(regions.path.read_text(encoding="utf-8")):
        if row["value"]:
            raw.fetch(SOURCE, communities_url(str(row["value"])), suffix=".json")
    raw.fetch_many(SOURCE, [stations_url(poll) for _, poll, _ in polls], suffix=".json", workers=WORKERS)
    session = Session()

    def one(job: tuple[int, str]) -> None:
        poll, station = job
        try:
            raw.cached(SOURCE, poll_url(poll, station))
            return
        except LookupError:
            pass
        response = session.post(
            **{
                "Input.ElectionId": str(poll),
                "Current.ElectionId": str(poll),
                "Input.SubDistrictId": station,
                "Input.Tab1": "",
                "Input.Tab5": "active-tab",
            }
        )
        keep(poll_url(poll, station), response)

    jobs: list[tuple[int, str]] = []
    for _, poll, _ in polls:
        jobs.append((poll, ""))
        found = stations(poll)
        if len(found) > 1:
            jobs.extend((poll, station) for station, _ in found)
    print(f"{SOURCE}: {len(jobs)} pages to fetch", file=sys.stderr, flush=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        for count, _ in enumerate(pool.map(one, jobs), 1):
            if count % 250 == 0:
                print(f"{SOURCE}: {count}/{len(jobs)}", file=sys.stderr, flush=True)
