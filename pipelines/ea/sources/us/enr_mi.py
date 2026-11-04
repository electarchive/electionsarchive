import html
import json
import re
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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
WAYNE = "https://michigan.totalvote.com/ResultsSW.aspx?cid=05&map=CTY&type="
WASHTENAW = "https://electionresults.ewashtenaw.org/electionreporting/nov2026/"
EASTERN = timezone(timedelta(hours=-5))


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
    for kind in ("ST", "CON"):
        if content := get(client, f"{WAYNE}{kind}"):
            store("MI", f"{WAYNE}{kind}", content, ".html")
    index = get(client, f"{WASHTENAW}indexcanvassreport.html") or b""
    for page, title in re.findall(r"<a href='(canvassreport\d+\.html)'>([^<]+)</a>", index.decode(errors="replace")):
        if washtenaw_office(title) is not None and (content := get(client, f"{WASHTENAW}{page}")):
            store("MI", f"{WASHTENAW}{page}", content, ".html")


def washtenaw_office(title: str) -> re.Match[str] | None:
    return EV_OFFICES.fullmatch(
        re.sub(r"^US Congress (\d+)\w* District$", r"Representative in Congress \1", title.strip())
    )


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def seat(found: re.Match[str]) -> dict[str, str]:
    return {
        "office": "senate" if found["senate"] else "governor" if found["governor"] else "house",
        "contest": "",
        "district": found["district"] or "",
        "scope": "",
    }


def wayne(path: Path):
    page = path.read_text(errors="replace")
    stamp = re.search(r"Results Last Updated:\s*(\d+/\d+/\d{4} \d+:\d+:\d+ [AP]M)", page)
    as_of = stamp and datetime.strptime(stamp[1], "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=EASTERN).isoformat()
    for race in re.split(r"<h1>(?=[^<]* - <br>VOTE FOR)", page)[1:]:
        found = EV_OFFICES.fullmatch(office_name(race.split(" - <br>")[0]))
        counted = re.search(r"Precincts Reported:\s*(\d+)\s*/\s*(\d+)", race)
        if found is None or counted is None:
            continue
        for name, party, votes in re.findall(
            r'display-results-box-d">\s*<h1>(.*?)</h1>\s*(?:<h2[^>]*>(.*?)</h2>)?.*?display-results-box-f">\s*<h1>([\d,]+)</h1>',
            race,
            re.DOTALL,
        ):
            yield {
                **seat(found),
                "party": text(party) if text(party) != "Nonpartisan" else "",
                "county": "26163",
                "candidate": re.sub(r"\s*/\s*", "/", text(name)),
                "write_in": text(name).lower() == "write-in",
                "votes": int(votes.replace(",", "")),
                "as_of": as_of,
                "reporting": int(counted[1]),
                "total": int(counted[2]),
            }


def washtenaw(path: Path):
    page = path.read_text(errors="replace")
    title = re.search(r'<font class="h2">([^<]+)</font>', page)
    found = washtenaw_office(text(title[1])) if title else None
    totals = re.search(r"<b>Totals</b></td>(.*?)</tr>", page, re.DOTALL)
    if found is None or totals is None:
        return
    stamp = re.search(r"report created:\s*\w+, (\w+ \d+, \d{4} \d+:\d+:\d+ [AP]M)", page)
    as_of = stamp and datetime.strptime(stamp[1], "%b %d, %Y %I:%M:%S %p").replace(tzinfo=EASTERN).isoformat()
    heads = re.findall(r"<th align=left>(.*?)</th>", page, re.DOTALL)[1:]
    votes = [int(cell) for cell in re.findall(r"<b>(\d+)<br>", totals[1])]
    total = re.search(r"Precincts Total:\D*(\d+)", text(page))
    full = re.search(r"Precincts Fully Counted:\D*(\d+)", text(page))
    for head, count in zip(heads, votes, strict=True):
        party = re.search(r"\((\w+)\)\s*$", text(head))
        name = "/".join(
            filter(None, (text(line) for line in re.split(r"<br>|\n", re.sub(r"\(\w+\)\s*$", "", head.strip()))))
        )
        if name == "Rejected write-ins":
            continue
        yield {
            **seat(found),
            "party": party[1] if party else "",
            "county": "26161",
            "candidate": "Write-in" if name == "Unassigned write-ins" else name,
            "write_in": party is None,
            "votes": count,
            "as_of": as_of,
            "reporting": int(full[1]) if full else 0,
            "total": int(total[1]) if total else 0,
        }


def rows(url: str, path: Path):
    if url.startswith(WAYNE):
        yield from wayne(path)
        return
    if url.startswith(WASHTENAW):
        yield from washtenaw(path)
        return
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
