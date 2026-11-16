"""Crawl of the Ministry of Interior results site, down to each ballot box.

The site serves one JSON document per place: the island, a district, a community or municipality, a polling
station, and a ballot box. A crawl walks that tree for one election and keeps what the packages use, in one file.
"""

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx

from ea import raw
from ea.raw import USER_AGENT
from ea.store import DATA

SOURCE = "cy.moi"
BASE = "https://results.elections.moi.gov.cy"
API = f"{BASE}/api/{{lang}}/Results?electionId={{election}}&districtId={{district}}&areaId={{area}}&pollingstationId={{station}}&ballotboxId={{box}}"
WORKERS = 8
CACHE = DATA / "cache" / SOURCE


def crawl_url(election: int) -> str:
    return f"{BASE}/api/Greek/Results?electionId={election}#crawl"


def _slim(document: dict[str, Any]) -> dict[str, Any]:
    result = document["CurrentResult"]
    assert isinstance(result, dict)
    node = result["Node"]
    out: dict[str, Any] = {
        "node": [node["Id"], node["Name"], node["Type"]],
        "reg": result.get("RegisteredVotersTotal"),
        "voted": result.get("VotersVotedTotal"),
        "valid": result.get("ValidBallots"),
        "invalid": result.get("InvalidBallots"),
        "blank": result.get("BlankBallots"),
        "calc": result.get("IsCalcResult"),
        "counted": result.get("RegisteredVotersCountedPercent"),
        "c": [[row["Candidate"]["Id"], row["BallotsCount"]] for row in result["CandidateResults"]],
        "who": {
            str(row["Candidate"]["Id"]): [row["Candidate"]["Name"], row["Candidate"]["Type"], row["Candidate"]["Color"]]
            for row in result["CandidateResults"]
        },
    }
    for field, name in (("Areas", "areas"), ("PollingStations", "stations"), ("BallotBoxes", "boxes")):
        rows = document.get(field)
        if rows:
            out[name] = [[row["Id"], row["Name"], (row.get("District") or {}).get("Id")] for row in rows]
    districts = document.get("DistrictResults")
    if districts:
        out["districts"] = [
            [
                row["Node"]["Id"],
                row["Node"]["Name"],
                row["BallotBoxesCounted"],
                row["BallotBoxesTotal"],
                row["LastUpdate"],
            ]
            for row in districts
        ]
    island = document.get("IslandwideResult")
    if island:
        out["island"] = [island["BallotBoxesCounted"], island["BallotBoxesTotal"], island["LastUpdate"]]
    election = document["Election"]
    assert isinstance(election, dict)
    out["election"] = [election["Id"], election["Name"], election["ElectionType"]["Name"]]
    return out


class Crawl:
    def __init__(self, election: int) -> None:
        self.election = election
        self.lock = threading.Lock()
        CACHE.mkdir(parents=True, exist_ok=True)
        self.path = CACHE / f"{election}.jsonl"
        self.seen: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                row = json.loads(line)
                self.seen[row["q"]] = row["r"]
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)

    def get(
        self, lang: str, district: object = "", area: object = "", station: object = "", box: object = ""
    ) -> dict[str, Any]:
        query = f"{lang}|{district}|{area}|{station}|{box}"
        with self.lock:
            if query in self.seen:
                return self.seen[query]
        url = API.format(lang=lang, election=self.election, district=district, area=area, station=station, box=box)
        for attempt in range(6):
            try:
                response = self.client.get(url)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPError(str(response.status_code))
                response.raise_for_status()
                slim = _slim(response.json())
                break
            except httpx.HTTPError, KeyError, ValueError:
                if attempt == 5:
                    raise
                threading.Event().wait(2**attempt)
        with self.lock:
            self.seen[query] = slim
            with self.path.open("a") as file:
                file.write(json.dumps({"q": query, "r": slim}, ensure_ascii=False, separators=(",", ":")) + "\n")
        return slim


def _map[T, R](function: Callable[[T], R], items: list[T]) -> list[R]:
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(function, items))


def walk(election: int) -> dict[str, Any]:
    crawl = Crawl(election)
    island = crawl.get("Greek")
    districts = island.get("districts") or []
    assert isinstance(districts, list)
    english = crawl.get("english")
    out: dict[str, Any] = {"island": island, "english": {"island": english, "districts": {}}, "districts": []}
    for district, *_ in districts:
        d = crawl.get("Greek", district)
        e = crawl.get("english", district)
        english_districts = out["english"]
        assert isinstance(english_districts, dict)
        english_districts["districts"][str(district)] = e
        areas = d.get("areas") or []
        assert isinstance(areas, list)

        def area_walk(area: list[object], district: object = district) -> dict[str, Any]:
            result = crawl.get("Greek", district, area[0])
            stations = []
            for station in result.get("stations") or []:
                s = crawl.get("Greek", district, area[0], station[0])
                boxes = s.get("boxes") or []
                assert isinstance(boxes, list)
                if len(boxes) > 1:
                    unit = [crawl.get("Greek", district, area[0], station[0], box[0]) for box in boxes]
                else:
                    unit = [s] * len(boxes)
                stations.append({"station": s, "boxes": list(zip(boxes, unit, strict=True))})
            return {"area": result, "stations": stations}

        out["districts"].append({"district": d, "areas": _map(area_walk, areas)})
        print(f"{election}: district {district} done, {len(crawl.seen)} requests", flush=True)
    return out


def fetch_election(election: int) -> None:
    url = crawl_url(election)
    if url in raw.keys(SOURCE):
        return
    content = json.dumps(walk(election), ensure_ascii=False, separators=(",", ":")).encode()
    raw.store(SOURCE, url, content, suffix=".json", retrieved_at=datetime.now(UTC), media_type="application/json")
    print(f"{election}: stored {len(content)} bytes", flush=True)
