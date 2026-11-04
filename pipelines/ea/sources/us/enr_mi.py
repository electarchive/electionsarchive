import json
from collections.abc import Callable
from pathlib import Path

import httpx

from ea.sources.us.enr_2026 import DAY, EV_OFFICES, TAGS, get, office_name

API = "https://app.enhancedvoting.com/results/public/api/"
COUNTIES = {
    "26021": "berrien-county-mi",
    "26023": "branch-county-mi",
    "26035": "clare-county-mi",
    "26041": "delta-county-mi",
    "26057": "gratiot-county-mi",
    "26065": "ingham-county-mi",
    "26069": "iosco-county-mi",
    "26073": "isabella-county-mi",
    "26077": "kalamazoo-county-mi",
    "26081": "kent-county-mi",
    "26085": "lake-county-mi",
    "26089": "leelanau-county-mi",
    "26111": "midland-county-mi",
    "26115": "monroe-county-mi",
    "26123": "newaygo-county-mi",
    "26133": "osceola-county-mi",
    "26145": "saginaw-county-mi",
    "26147": "st.-clair-county-mi",
    "26149": "st.-joseph-county-mi",
    "26155": "shiawassee-county-mi",
    "26159": "van-buren-county-mi",
}
ELECTIONS: dict[str, str] = {}


def collect(client: httpx.Client, store: Callable[[str, str, bytes, str], None]) -> None:
    missing = []
    for county in COUNTIES.values():
        if county not in ELECTIONS:
            listing = get(client, f"{API}jurisdictions/{county}")
            ids = (
                [
                    e["publicElectionId"]
                    for e in json.loads(listing)["elections"]
                    if e["electionDate"] == DAY.isoformat()
                ]
                if listing
                else []
            )
            if not ids:
                missing.append(county)
                continue
            ELECTIONS[county] = ids[0]
        url = f"{API}elections/{county}/{ELECTIONS[county]}/ballot-items?summary=true"
        content = get(client, url)
        if content is None:
            missing.append(county)
        else:
            store("MI", url, content, ".json")
    if missing:
        print(f"MI Enhanced Voting counties with no {DAY} results: {len(missing)} {' '.join(missing)}")


def rows(url: str, path: Path):
    county = next(fips for fips, slug in COUNTIES.items() if f"/elections/{slug}/" in url)
    for item in json.loads(path.read_bytes())["data"]:
        found = EV_OFFICES.fullmatch(office_name(item["name"][0]["text"]))
        if found is None:
            continue
        status = item.get("reportingStatus") or {}
        for option in item["summaryResults"]["ballotOptions"]:
            yield {
                "office": "senate" if found["senate"] else "governor" if found["governor"] else "house",
                "contest": "",
                "district": found["district"] or "",
                "party": (((option["party"] or {}).get("name") or [{}])[0].get("text") or "").strip("()"),
                "county": county,
                "candidate": TAGS.sub("", option["name"][0]["text"]).strip(),
                "write_in": bool(option["isWriteIn"]),
                "votes": int(option["voteCount"] or 0),
                "as_of": status.get("asOf"),
                "reporting": status.get("reportingUnits"),
                "total": status.get("totalUnits"),
                "scope": found["party0"] or found["party1"] or "",
            }
