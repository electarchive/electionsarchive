"""Crawl the results sites of Portugal's Ministry of Internal Administration into one zip per election.

Each site is a single-page app that reads static JSON files: one list of children and one results file per territory
and organ. The crawl walks the territory tree from the top and keeps every file byte for byte in a zip named by its
path under the site, so the raw cache holds one artifact per election rather than tens of thousands.
"""

import base64
import io
import json
import re
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ea import raw

HOST = "https://www.eleicoes.mai.gov.pt"
WORKERS = 12


@dataclass(frozen=True)
class Site:
    name: str  # the folder under HOST
    family: str  # "proto" (2009-2019), "ng" (2021-2025), "ps" (2025 local and 2026 presidential), "eu09"
    organs: tuple[str, ...]  # the site's election ids, such as AR or CM, AM, AF
    roots: tuple[str, ...] = ("GLOBAL-990000", "LOCAL-500000", "FOREIGN-600000", "FOREIGN-800000")


# The sites whose candidate lists the bundle keeps: every election from 2019 on, except the presidential ones, whose
# candidates are the options themselves.
CANDIDATE_SITES = {
    "legislativas2019",
    "legislativas2022",
    "legislativas2024",
    "legislativas2025",
    "europeias2019",
    "europeias2024",
    "autarquicas2021",
    "autarquicas2025",
    "regionais2019",
    "regionais2023",
    "regionais2024",
    "regionais-madeira2024",
    "regionais2025",
}
TOTALS = {"GLOBAL-990000", "LOCAL-500000", "FOREIGN-600000"}


def bundle_url(site: Site) -> str:
    return f"{HOST}/{site.name}/#static-files.zip"


def _get(client: httpx.Client, url: str) -> bytes | None:
    for attempt in range(6):
        try:
            response = client.get(url)
        except httpx.TransportError:
            time.sleep(2 + attempt * 3)
            continue
        if response.status_code == 404:
            return None
        if response.status_code >= 500 or response.status_code in (403, 429):  # the site throttles bursts with a 403
            time.sleep(2 + attempt * 3)
            continue
        response.raise_for_status()
        body = response.content
        if body.lstrip()[:1] not in (b"{", b"["):
            return None  # the site answers some missing files with an HTML or "Not found" page
        return body
    raise RuntimeError(f"{url}: no answer after retries")


def _paths(site: Site) -> tuple[Callable[[str], str], Callable[[str, str], str]]:
    if site.family == "proto":
        return (
            lambda key: f"static-data/territory-children/TERRITORY-CHILDREN-{key}.json",
            lambda key, organ: f"static-data/territory-results/TERRITORY-RESULTS-{key}-{organ}.json",
        )
    if site.family == "eu09":
        return (lambda key: f"data/LocalTerritoryChilds/{key}", lambda key, _: f"data/GlobalAndLocalResults/{key}")
    return (
        lambda key: f"assets/static/territory-children/territory-children-{key}.json",
        lambda key, organ: f"assets/static/territory-results/territory-results-{key}-{organ}.json",
    )


def _read(artifact: raw.Artifact) -> dict[str, bytes]:
    with zipfile.ZipFile(artifact.path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _add_candidates(site: Site, files: dict[str, bytes]) -> None:
    headers = {"User-Agent": raw.USER_AGENT}
    with (
        httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client,
        ThreadPoolExecutor(WORKERS) as pool,
    ):
        _crawl_candidates(site, files, lambda path: (path, _get(client, f"{HOST}/{site.name}/{path}")), pool)


def crawl(site: Site) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    base = f"{HOST}/{site.name}/"
    headers = {"User-Agent": raw.USER_AGENT}
    with (
        httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client,
        ThreadPoolExecutor(WORKERS) as pool,
    ):

        def get(path: str) -> tuple[str, bytes | None]:
            return path, _get(client, base + path)

        if site.family == "ps":
            _crawl_ps(site, files, get, pool)
            if site.name in CANDIDATE_SITES:
                _crawl_candidates(site, files, get, pool)
            return files
        children, results = _paths(site)
        level = list(site.roots)
        seen: set[str] = set()
        while level:
            seen |= set(level)
            wanted = [results(key, organ) for key in level for organ in site.organs]
            # Parishes and consular posts are leaves; only a key ending in 00 can have children.
            wanted += [children(key) for key in level if key.endswith("00")]
            found = dict(pool.map(get, wanted))
            following: list[str] = []
            for key in level:
                body = found.get(children(key))
                if body is None:
                    continue
                for child in json.loads(body):
                    if child["territoryKey"] not in seen:
                        following.append(child["territoryKey"])
            for path, body in found.items():
                if body is not None:
                    files[path] = body
            level = sorted(set(following))
        if site.name in CANDIDATE_SITES:
            _crawl_candidates(site, files, get, pool)
    return files


def _crawl_ps(
    site: Site,
    files: dict[str, bytes],
    get: Callable[[str], tuple[str, bytes | None]],
    pool: ThreadPoolExecutor,
) -> None:
    _, body = get("app-settings.json")
    if body is None:
        raise LookupError(f"{site.name} has no app-settings.json")
    files["app-settings.json"] = body
    election = json.loads(body)["electionId"]
    _, body = get(f"assets/static/election/data-electionId={election}.json")
    if body is None:
        raise LookupError(f"{site.name} has no election data")
    files[f"assets/static/election/data-electionId={election}.json"] = body
    data = json.loads(body)["data"]
    organs = {organ["key"]: organ["id"] for organ in data["organs"]}
    tops = data["mainTerritories"]
    top = next((t for t in tops if t["typeId"] == 10), None) or tops[0]
    level = [int(top["id"])]
    leaves: set[int] = set()
    while level:
        wanted = [
            f"assets/static/result/territory-electionId={election}-territoryId={territory}-organId={organs[organ]}.json"
            for territory in level
            for organ in site.organs
        ]
        wanted += [
            f"assets/static/territory/children-electionId={election}-territoryId={territory}.json"
            for territory in level
            if territory not in leaves
        ]
        found = dict(pool.map(get, wanted))
        following: list[int] = []
        for territory in level:
            body = found.get(f"assets/static/territory/children-electionId={election}-territoryId={territory}.json")
            if body is None:
                continue
            for child in json.loads(body)["data"] or []:
                following.append(int(child["id"]))
                if child.get("final"):
                    leaves.add(int(child["id"]))
        for path, content in found.items():
            if content is not None:
                files[path] = content
        level = sorted(set(following))


def has_candidates(names: list[str]) -> bool:
    return any("candidates" in name.lower() or "/candidate/" in name for name in names)


def candidate_name(site: Site, organ: str, territory: str | None, page: int, elected: bool) -> str:
    """The path of one page of a site's candidate lists, as its script builds it."""
    if site.family == "proto":
        parts = ["PARTIES-CANDIDATES", organ, *([territory] if territory else []), f"PAGE-{page}"]
        return "static-data/candidates/" + "-".join([*parts, f"ELECTED-{str(elected).upper()}"]) + ".json"
    parts = ["parties-candidates", organ, *([territory] if territory else []), str(page), str(elected).lower()]
    return "assets/static/candidates/" + "-".join(parts) + ".json"


def _territories(site: Site, files: dict[str, bytes]) -> dict[str, list[str | None]]:
    """The territories whose candidate lists are published, by organ: the places that elect each organ."""
    found = {
        match.group(1)
        for path in files
        if "results" in path.lower() and (match := re.search(r"((?:LOCAL|FOREIGN)-\d{6})-[A-Za-z]+\.json$", path))
    }
    found -= TOTALS
    wide = sorted(key for key in found if key.endswith("0000"))
    out: dict[str, list[str | None]] = {}
    for organ in site.organs:
        if organ == "AR":
            out[organ] = list(wide)
        elif organ == "CM":
            out[organ] = sorted(key for key in found if key.endswith("00") and not key.endswith("0000"))
        elif organ in ("PE", "EUR"):
            out[organ] = [None]
        elif organ in ("RM", "MD"):
            out[organ] = ["LOCAL-300000"]
        elif organ == "RA":
            out[organ] = ["LOCAL-400000"]  # one list of every circle and the compensation list
    return out


def _page_count(body: bytes | None) -> int:
    if not body:
        return 0
    data = json.loads(body)
    data = data["data"] if isinstance(data.get("data"), dict) else data  # the 2025-2026 sites wrap the page
    return int(data.get("numberOfPages") or 0)


def _crawl_candidates(
    site: Site,
    files: dict[str, bytes],
    get: Callable[[str], tuple[str, bytes | None]],
    pool: ThreadPoolExecutor,
) -> None:
    if site.family == "ps":
        _crawl_ps_candidates(site, files, get, pool)
        return
    for organ, territories in _territories(site, files).items():
        first = [
            candidate_name(site, organ, territory, 1, elected) for territory in territories for elected in (False, True)
        ]
        found = dict(pool.map(get, first))
        for path, body in found.items():
            if body is not None:
                files[path] = body
        more = []
        for territory in territories:
            pages = _page_count(found.get(candidate_name(site, organ, territory, 1, False)))
            for elected in (False, True):
                more += [candidate_name(site, organ, territory, page, elected) for page in range(2, pages + 1)]
        for path, body in pool.map(get, more):
            if body is not None:
                files[path] = body


def ps_name(election: int, option: str, page: int, elected: bool) -> str:
    """The path of one page of a 2025-2026 site's candidates for one list or candidacy, across every territory."""
    encoded = base64.b64encode(option.encode()).decode().replace("+", "_", 1).replace("/", "%23", 1)
    return (
        f"assets/static/candidate/candidates-electionId={election}-territoryId=null-votingOptionName={encoded}"
        f"-candidatesElected={str(elected).lower()}-page={page}.json"
    )


def _crawl_ps_candidates(
    site: Site,
    files: dict[str, bytes],
    get: Callable[[str], tuple[str, bytes | None]],
    pool: ThreadPoolExecutor,
) -> None:
    election = json.loads(files["app-settings.json"])["electionId"]
    _, body = get(f"assets/static/votingOptions/votingOptions-electionId={election}.json")
    if body is None:
        return
    names = sorted({str(item["name"]) for item in json.loads(body)["data"] or [] if item.get("name")})
    first = [ps_name(election, name, 1, elected) for name in names for elected in (False, True)]
    found = dict(pool.map(get, first))
    more = []
    for name in names:
        pages = _page_count(found.get(ps_name(election, name, 1, False)))
        for elected in (False, True):
            more += [ps_name(election, name, page, elected) for page in range(2, pages + 1)]
    found |= dict(pool.map(get, more))
    for path, content in found.items():
        if content is not None:
            files[path.replace("%23", "#")] = content


def store(site: Site) -> raw.Artifact:
    try:
        cached = raw.cached("pt.mai", bundle_url(site))
    except LookupError:
        cached = None
    wants = site.name in CANDIDATE_SITES
    if cached is not None:
        if not wants:
            return cached
        with zipfile.ZipFile(cached.path) as archive:
            if has_candidates(archive.namelist()):
                return cached
    started = datetime.now(UTC)
    files = crawl(site) if cached is None else _read(cached)
    if cached is not None:
        _add_candidates(site, files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[path])
    print(f"pt.mai: {site.name}: {len(files):,} files", flush=True)
    return raw.store(
        "pt.mai", bundle_url(site), buffer.getvalue(), suffix=".zip", retrieved_at=started, media_type="application/zip"
    )
