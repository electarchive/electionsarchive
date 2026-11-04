import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

from ea.sources.us.enr_2026 import EV_OFFICES, TAGS, WRITE_IN, get, office_name

CDN = "https://app.enhancedvoting.com/cdn/results/"
EXPORTS = {
    "26021": "berrien-county-mi/export-November_3_2026_General_Election",
    "26023": "branch-county-mi/export-2026NovemberGeneral",
    "26035": "clare-county-mi/export-2026_General_ClareCounty_Results",
    "26041": "delta-county-mi/export-november2026election",
    "26057": "gratiot-county-mi/export-GratiotCountyNovemberElection",
    "26065": "ingham-county-mi/export-NovemberElection11032026",
    "26069": "iosco-county-mi/export-NovemberGeneral2026",
    "26073": "isabella-county-mi/export-2026NovemberGeneral",
    "26077": "kalamazoo-county-mi/export-2026-General-Election",
    "26081": "kent-county-mi/export-11032026_20261005115447",
    "26085": "lake-county-mi/export-Nov-26-General",
    "26089": "leelanau-county-mi/export-20261103_LeelanauNovember2026General",
    "26111": "midland-county-mi/export-November_3_2026",
    "26115": "monroe-county-mi/export-November2026Election",
    "26123": "newaygo-county-mi/export-2026NovemberGeneralElection",
    "26133": "osceola-county-mi/export-November2026",
    "26145": "saginaw-county-mi/export-_20261016111322",
    "26147": "st.-clair-county-mi/export-November2026GeneralElection_20260928112331",
    "26149": "st.-joseph-county-mi/export-2026_state_general_results",
    "26155": "shiawassee-county-mi/export-11032026GeneralElection",
    "26159": "van-buren-county-mi/export-November2026",
}


def collect(client: httpx.Client, store: Callable[[str, str, bytes, str], None]) -> None:
    missing = []
    for export in EXPORTS.values():
        url = f"{CDN}{export}.zip"
        content = get(client, url)
        if content is None:
            missing.append(export.split("/")[0])
        else:
            store("MI", url, content, ".zip")
    if missing:
        print(f"MI Enhanced Voting counties with no export: {len(missing)} {' '.join(missing)}")


def rows(url: str, path: Path):
    county = next(fips for fips, export in EXPORTS.items() if url == f"{CDN}{export}.zip")
    with zipfile.ZipFile(path) as archive:
        export = json.loads(archive.read("export.json"))
    for item in export["results"]["ballotItems"]:
        found = EV_OFFICES.fullmatch(office_name(item["name"]))
        if found is None:
            continue
        for option in item["ballotOptions"]:
            yield {
                "office": "senate" if found["senate"] else "governor" if found["governor"] else "house",
                "contest": "",
                "district": found["district"] or "",
                "party": (option["politicalParty"] or "").removesuffix(" Party"),
                "county": county,
                "candidate": TAGS.sub("", option["name"]).strip(),
                "write_in": bool(WRITE_IN.search(option["name"])),
                "votes": int(option["voteCount"] or 0),
                "as_of": export["createdAt"],
                "reporting": item["precinctsReporting"],
                "total": item["precinctsParticipating"],
                "scope": found["party0"] or found["party1"] or "",
            }
