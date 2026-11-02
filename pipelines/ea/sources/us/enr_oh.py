import os
import re
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import httpx

from ea.sources.us.enr_2026 import DAY

FEED = "https://liveresults.ohiosos.gov/Api/v1/download?filename=VSSC1622XmlFileBlob"
NS = "{http://grouper.ieee.org/groups/1622/groups/2/V1/1622_2-election_results_reporting.xsd}"
TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
OFFICES = {
    "Governor and Lieutenant Governor": "governor",
    "Attorney General": "attorney-general",
    "Auditor of State": "auditor",
    "Secretary of State": "secretary-of-state",
    "Treasurer of State": "treasurer",
    "U.S. Senator": "senate",
}
SEAT = re.compile(r"(Representative to Congress|State Senator|State Representative) - District (\d+)")


def collect(client: httpx.Client, get: Callable[..., bytes | None], store: Callable[[str, bytes], None]) -> None:
    content = get(client, FEED)
    if content is None or b"<ElectionReport" not in content[:2000]:
        print("OH: no results feed")
        return
    if b'IsTest="true"' in content[:2000] and not os.environ.get("ENR_2026_OH_TEST"):
        print("OH: results feed is still the test file")
        return
    store(FEED, content)


def text(node: ElementTree.Element | None) -> str:
    found = None if node is None else node.find(f"{NS}LanguageString")
    return " ".join((found.text or "").split()) if found is not None else ""


def rows(url: str, path: Path, urls: dict[str, Path]):
    root = ElementTree.parse(path).getroot()
    election = root.find(f"{NS}Election")
    if election is None or election.get("Date") != DAY.isoformat():
        return
    counties = {unit.get("ObjectId"): unit for unit in root.iter(f"{NS}GpUnit") if unit.get("Type") == "County"}
    parties = {party.get("ObjectId"): party.get("Abbreviation") or "" for party in root.iter(f"{NS}Party")}
    people = {
        candidate.get("ObjectId"): (text(candidate.find(f"{NS}BallotName")), candidate.findtext(f"{NS}PartyId"))
        for candidate in root.iter(f"{NS}Candidate")
    }
    as_of = root.get("GeneratedDate")
    for contest in root.iter(f"{NS}Contest"):
        name = " ".join((contest.get("Name") or "").split())
        seat = SEAT.fullmatch(name)
        measure = re.match(r"State Issue (\d+)", name)
        if name in OFFICES:
            office, district = OFFICES[name], ""
        elif seat:
            office = "house" if seat[1] != "State Senator" else "senate"
            district = str(int(seat[2])) + ("" if seat[1] == "Representative to Congress" else ":g")
        elif measure:
            office, district = "question", f"issue-{measure[1]}"
        elif name.startswith("Justice of the Supreme Court"):
            office, district = "state", ""
        else:
            continue
        places = {
            count.findtext(f"{NS}GpUnitId")
            for count in contest.iter(f"{NS}VoteCounts")
            if count.findtext(f"{NS}GpUnitId") in counties
        }
        reporting = sum(int(counties[place].get("SubUnitsReported") or 0) for place in places)
        total = sum(int(counties[place].get("TotalSubUnits") or 0) for place in places)
        for selection in contest.findall(f"{NS}BallotSelection"):
            if selection.get(TYPE) == "BallotMeasureSelection":
                candidate, party = (selection.get("Selection") or "").title(), ""
            else:
                head = selection.findtext(f"{NS}CandidateId")
                candidate, party_id = people.get(head, ("", None))
                party = parties.get(party_id, "")
            for count in selection.iter(f"{NS}VoteCounts"):
                place = count.findtext(f"{NS}GpUnitId")
                if place not in counties or count.get("Type") != "Total":
                    continue
                yield {
                    "office": office,
                    "contest": f"{name} {contest.get('ObjectId')}" if office == "state" else "",
                    "district": district,
                    "party": "" if party in ("None", "-") else party,
                    "county": counties[place].get("Name"),
                    "candidate": candidate,
                    "write_in": selection.get("IsWriteIn") == "true",
                    "votes": int(count.get("Count") or 0),
                    "as_of": as_of,
                    "reporting": reporting,
                    "total": total,
                }
