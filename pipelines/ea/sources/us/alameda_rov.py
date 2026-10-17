import io
import json
import re
import subprocess
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.alameda_rov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Alameda County Registrar of Voters"
SWDB = "Statewide Database, University of California, Berkeley"
PLANS = {
    "2010": "https://statewidedatabase.org/pub/data/G10/c001/mprec_001_g10_v01_shp.zip",
    "2012": "https://statewidedatabase.org/pub/data/G12/c001/mprec_001_g12_v01_shp.zip",
    "2014": "https://statewidedatabase.org/pub/data/G14/c001/mprec_001_g14_v01_shp.zip",
    "2018": "https://statewidedatabase.org/pub/data/G18/c001/mprec_001_g18_v01_shp.zip",
}
A = "/acvote-assets/pdf/elections/"
CONTESTS: dict[str, tuple[str, str, str | None]] = {
    "2010-11-02:CA:berkeley:city-council:7": (
        "alged:606000_2010_11_berkeley_CA_City Council_7_1",
        A + "2010/11022010/results/rcv/berkeley/city_councilmember_district_7/"
        "november-2-2010-pass-report-berkeley-council-member-dist-7.pdf",
        A + "2010/11022010/results/rcv/berkeley/city_councilmember_district_7/"
        "november-2-2010-comprehensive-results-report-berkeley-council-member-dist-7.pdf",
    ),
    "2010-11-02:CA:oakland:city-council:4": (
        "alged:653000_2010_11_oakland_CA_City Council_4_1",
        A + "2010/11022010/results/rcv/oakland/city_councilmember_district_4/"
        "november-2-2010-rcv-oakland-city-council-district-4-pass-report.pdf",
        A + "2010/11022010/results/rcv/oakland/city_councilmember_district_4/"
        "november-2-2010-rcv-oakland-city-council-district-4-comprehensive-report.pdf",
    ),
    "2010-11-02:CA:oakland:mayor": (
        "alged:653000_2010_11_oakland_CA_Mayor_mayor_1",
        A + "2010/11022010/results/rcv/oakland/mayor/november-2-2010-pass-report-oakland-mayor.pdf",
        A + "2010/11022010/results/rcv/oakland/mayor/november-2-2010-comprehensive-results-report-oakland-mayor.pdf",
    ),
    "2010-11-02:CA:san-leandro:mayor": (
        "ceda:201000666",
        A + "2010/11022010/results/rcv/san_leandro/mayor/november-2-2010-pass-report-san-leandro-mayor.pdf",
        A + "2010/11022010/results/rcv/san_leandro/mayor/"
        "november-2-2010-comprehensive-results-report-san-leandro-mayor.pdf",
    ),
    "2012-11-06:CA:oakland:city-council:1": (
        "alged:653000_2012_11_oakland_CA_City Council_1_1",
        A + "2012/11062012/results/rcv/oakland/city_councilmember_district_1/"
        "nov-6-2012-pass-report-oakland-council-member-dist-1.pdf",
        A + "2012/11062012/results/rcv/oakland/city_councilmember_district_1/"
        "nov-6-2012-comprehensive-results-report-oak-council-member-dist-1.pdf",
    ),
    "2012-11-06:CA:oakland:city-council:3": (
        "alged:653000_2012_11_oakland_CA_City Council_3_1",
        A + "2012/11062012/results/rcv/oakland/city_councilmember_district_3/"
        "nov-6-2012-pass-report-oakland-council-member-dist-3.pdf",
        A + "2012/11062012/results/rcv/oakland/city_councilmember_district_3/"
        "nov-6-2012-comprehensive-results-report-oak-council-member-dist-3.pdf",
    ),
    "2012-11-06:CA:oakland:city-council:5": (
        "alged:653000_2012_11_oakland_CA_City Council_5_1",
        A + "2012/11062012/results/rcv/oakland/city_councilmember_district_5/"
        "nov-6-2012-pass-report-oakland-council-member-dist-5.pdf",
        A + "2012/11062012/results/rcv/oakland/city_councilmember_district_5/"
        "nov-6-2012-comprehensive-results-report-oak-council-member-dist-5.pdf",
    ),
    "2012-11-06:CA:oakland:city-council:at-large": (
        "alged:653000_2012_11_oakland_CA_City Council_synthetic at-large a_1",
        A + "2012/11062012/results/rcv/oakland/city_councilmember_district_at_large/"
        "nov-6-2012-pass-report-oakland-council-member-at-large.pdf",
        A + "2012/11062012/results/rcv/oakland/city_councilmember_district_at_large/"
        "nov-6-2012-comprehensive-results-report-oak-council-member-at-large.pdf",
    ),
    "2012-11-06:CA:oakland:school-board:3": (
        "alged:628050_2012_11_Oakland Unified_CA_School Board_3_1",
        A + "2012/11062012/results/rcv/oakland/school_director_district_3/"
        "nov-6-2012-pass-report-oakland-school-director-dist-3.pdf",
        A + "2012/11062012/results/rcv/oakland/school_director_district_3/"
        "nov-6-2012-comprehensive-results-report-oak-school-director-dist-3.pdf",
    ),
    "2012-11-06:CA:san-leandro:city-council:2": (
        "alged:668084_2012_11_san leandro_CA_City Council_2_1",
        A + "2012/11062012/results/rcv/san_leandro/city_councilmember_district_2/"
        "nov-6-2012-pass-report-san-leandro-council-member-dist-2.pdf",
        A + "2012/11062012/results/rcv/san_leandro/city_councilmember_district_2/"
        "nov-6-2012-comprehensive-results-report-sl-council-member-dist-2.pdf",
    ),
    "2012-11-06:CA:san-leandro:city-council:4": (
        "alged:668084_2012_11_san leandro_CA_City Council_4_1",
        A + "2012/11062012/results/rcv/san_leandro/city_councilmember_district_4/"
        "nov-6-2012-pass-report-san-leandro-council-member-dist-4.pdf",
        A + "2012/11062012/results/rcv/san_leandro/city_councilmember_district_4/"
        "nov-6-2012-comprehensive-results-report-sl-council-member-dist-4.pdf",
    ),
    "2014-11-04:CA:berkeley:city-council:8": (
        "alged:606000_2014_11_berkeley_CA_City Council_8_1",
        A + "2014/11042014/results/rcv/berkeley/city_council_district_8/"
        "nov-4-2014-pass-report-city-council-berkeley-dist8.pdf",
        A + "2014/11042014/results/rcv/berkeley/city_council_district_8/"
        "nov-4-2014-comprehensive-results-city-council-berk-dist8.pdf",
    ),
    "2014-11-04:CA:oakland:city-council:2": (
        "alged:653000_2014_11_oakland_CA_City Council_2_1",
        A + "2014/11042014/results/rcv/oakland/city_council_district_2/"
        "nov-4-2014-pass-report-city-council-oakland-dist2.pdf",
        A + "2014/11042014/results/rcv/oakland/city_council_district_2/"
        "nov-4-2014-comprehensive-results-city-council-oakland-dist2.pdf",
    ),
    "2014-11-04:CA:oakland:city-council:6": (
        "alged:653000_2014_11_oakland_CA_City Council_6_1",
        A + "2014/11042014/results/rcv/oakland/city_council_district_6/"
        "nov-4-2014-pass-report-city-council-oakland-dist6.pdf",
        A + "2014/11042014/results/rcv/oakland/city_council_district_6/"
        "nov-4-2014-comprehensive-results-city-council-oakland-dist6.pdf",
    ),
    "2014-11-04:CA:oakland:mayor": (
        "alged:653000_2014_11_oakland_CA_Mayor_mayor_1",
        A + "2014/11042014/results/rcv/oakland/mayor/nov-4-2014-pass-report-mayor-oakland.pdf",
        A + "2014/11042014/results/rcv/oakland/mayor/nov-4-2014-comprehensive-results-mayor-oakland.pdf",
    ),
    "2014-11-04:CA:oakland:school-board:4": (
        "alged:628050_2014_11_Oakland Unified_CA_School Board_4_1",
        A + "2014/11042014/results/rcv/oakland/school_director_district_4/"
        "nov-4-2014-pass-report-school-director-oakland-dist4.pdf",
        A + "2014/11042014/results/rcv/oakland/school_director_district_4/"
        "nov-4-2014-comprehensive-results-school-director-oakland-dist4.pdf",
    ),
    "2014-11-04:CA:san-leandro:city-council:1": (
        "alged:668084_2014_11_san leandro_CA_City Council_1_1",
        A + "2014/11042014/results/rcv/san_leandro/city_council_district_1/"
        "nov-4-2014-pass-report-city-council-san-leandro-d1.pdf",
        A + "2014/11042014/results/rcv/san_leandro/city_council_district_1/"
        "nov-4-2014-comp-results-report-city-council-san-leandro-d1.pdf",
    ),
    "2014-11-04:CA:san-leandro:mayor": (
        "alged:668084_2014_11_san leandro_CA_Mayor_mayor_1",
        A + "2014/11042014/results/rcv/san_leandro/mayor/nov-4-2014-pass-report-mayor-san-leandro.pdf",
        A + "2014/11042014/results/rcv/san_leandro/mayor/nov-4-2014-comprehensive-results-report-mayor-san-leandro.pdf",
    ),
    "2016-11-08:CA:berkeley:city-council:2": (
        "alged:606000_2016_11_berkeley_CA_City Council_2_1",
        A + "2016/11082016/results/rcv/berkeley/city_councilmember_district_2/"
        "nov-8-2016--pass-report-councilmember-dist2.pdf",
        A + "2016/11082016/results/rcv/berkeley/city_councilmember_district_2/"
        "nov-8-2016-comprehensive-results-report-councilmember-dist2.pdf",
    ),
    "2016-11-08:CA:berkeley:mayor": (
        "alged:606000_2016_11_berkeley_CA_Mayor_mayor_1",
        A + "2016/11082016/results/rcv/berkeley/mayor/nov-8-2016-pass-results-report-mayor.pdf",
        A + "2016/11082016/results/rcv/berkeley/mayor/nov-8-2016-comprehensive-results-report-mayor.pdf",
    ),
    "2016-11-08:CA:oakland:school-board:3": (
        "alged:628050_2016_11_Oakland Unified_CA_School Board_3_1",
        A + "2016/11082016/results/rcv/oakland/school_director_district_3/"
        "nov-8-2016-pass-report-school-director-dist3-oak.pdf",
        A + "2016/11082016/results/rcv/oakland/school_director_district_3/"
        "nov-8-2016-comprehensive-results-report-school-director-dist3-oak.pdf",
    ),
    "2016-11-08:CA:oakland:school-board:5": (
        "alged:628050_2016_11_Oakland Unified_CA_School Board_5_1",
        A + "2016/11082016/results/rcv/oakland/school_director_district_5/"
        "nov-8-2016-pass-report-school-director-dist5-oak.pdf",
        A + "2016/11082016/results/rcv/oakland/school_director_district_5/"
        "nov-8-2016-comprehensive-results-report-school-director-dist5-oak.pdf",
    ),
    "2018-11-06:CA:oakland:city-council:4": (
        "alged:653000_2018_11_oakland_CA_City Council_4_1",
        "/rovresults/rcv/236/OaklandCouncilD4/Pass%20Report.pdf",
        "/rovresults/rcv/236/OaklandCouncilD4/comprehensive_report.pdf",
    ),
    "2018-11-06:CA:oakland:city-council:6": (
        "alged:653000_2018_11_oakland_CA_City Council_6_1",
        "/rovresults/rcv/236/OaklandCouncilD6/Pass%20Report.pdf",
        "/rovresults/rcv/236/OaklandCouncilD6/comprehensive_report.pdf",
    ),
    "2018-11-06:CA:berkeley:city-council:1": (
        "alged:606000_2018_11_berkeley_CA_City Council_1_1",
        "/rovresults/rcv/236/BerkeleyCouncilD1/Pass%20Report.pdf",
        "/rovresults/rcv/236/BerkeleyCouncilD1/comprehensive_report.pdf",
    ),
    "2022-11-08:CA:berkeley:city-council:1": (
        "ceda:202200657",
        "/rovresults/rcv/248/Berkeley/001-CityCouncil_District_1/RcvDetailedReport.xml",
        None,
    ),
    "2022-11-08:CA:berkeley:city-council:8": (
        "ceda:202200660",
        "/rovresults/rcv/248/Berkeley/004-CityCouncil_District_8/RcvDetailedReport.xml",
        None,
    ),
    "2022-11-08:CA:oakland:mayor": (
        "ceda:202200679",
        "/rovresults/rcv/248/Oakland/001-Mayor/RcvDetailedReport.xml",
        None,
    ),
    "2022-11-08:CA:oakland:city-council:6": (
        "ceda:202200678",
        "/rovresults/rcv/248/Oakland/004-CityCouncil_District_6/RcvDetailedReport.xml",
        None,
    ),
    "2022-11-08:CA:oakland:school-board:2": (
        "ceda:202201664",
        "/rovresults/rcv/248/Oakland/006-SchoolDirector_District_2/RcvDetailedReport.xml",
        None,
    ),
    "2022-11-08:CA:oakland:school-board:4": (
        "ceda:202201665",
        "/rovresults/rcv/248/Oakland/007-SchoolDirector_District_4/RcvDetailedReport.xml",
        None,
    ),
    "2022-11-08:CA:oakland:school-board:6": (
        "ceda:202201666",
        "/rovresults/rcv/248/Oakland/008-SchoolDirector_District_6/RcvDetailedReport.xml",
        None,
    ),
    "2022-11-08:CA:san-leandro:mayor": (
        "ceda:202200687",
        "/rovresults/rcv/248/SanLeandro/001-Mayor/RcvDetailedReport.xml",
        None,
    ),
    "2022-11-08:CA:san-leandro:city-council:1": (
        "ceda:202200684",
        "/rovresults/rcv/248/SanLeandro/002-CityCouncil_District_1/RcvDetailedReport.xml",
        None,
    ),
    "2024-05-28:CA:berkeley:city-council:4": (
        "ceda:202400340",
        "/rovresults/rcv/255/001-CityCouncil_D4/RcvDetailedReport.xml",
        None,
    ),
    "2024-11-05:CA:berkeley:mayor": (
        "ceda:202400347",
        "/rovresults/rcv/252/Berkeley/001-Mayor/RcvDetailedReport.xml",
        None,
    ),
    "2024-11-05:CA:berkeley:city-council:3": (
        "ceda:202400344",
        "/rovresults/rcv/252/Berkeley/003-CityCouncil_District_3/RcvDetailedReport.xml",
        None,
    ),
    "2024-11-05:CA:berkeley:city-council:5": (
        "ceda:202400345",
        "/rovresults/rcv/252/Berkeley/004-CityCouncil_District_5/RcvDetailedReport.xml",
        None,
    ),
    "2024-11-05:CA:oakland:city-council:at-large": (
        "ceda:202400364",
        "/rovresults/rcv/252/Oakland/001-CityCouncil_AtLarge/RcvDetailedReport.xml",
        None,
    ),
    "2024-11-05:CA:oakland:city-council:1": (
        "ceda:202400365",
        "/rovresults/rcv/252/Oakland/002-CityCouncil_District_1/RcvDetailedReport.xml",
        None,
    ),
    "2024-11-05:CA:oakland:city-council:3": (
        "ceda:202400366",
        "/rovresults/rcv/252/Oakland/003-CityCouncil_District_3/RcvDetailedReport.xml",
        None,
    ),
    "2024-11-05:CA:oakland:city-council:5": (
        "ceda:202400367",
        "/rovresults/rcv/252/Oakland/004-CityCouncil_District_5/RcvDetailedReport.xml",
        None,
    ),
    "2024-11-05:CA:oakland:city-council:7": (
        "ceda:202400368",
        "/rovresults/rcv/252/Oakland/005-CityCouncil_District_7/RcvDetailedReport.xml",
        None,
    ),
    "2025-04-15:CA:oakland:mayor": (
        "ceda:202500012",
        "/rovresults/rcv/257/Oakland/001-Mayor/RcvDetailedReport.xml",
        None,
    ),
    "2025-04-15:CA:oakland:city-council:2": (
        "ceda:202500011",
        "/rovresults/rcv/257/Oakland/002-CityCouncil_District_2/RcvDetailedReport.xml",
        None,
    ),
}
IMAGES = {
    "2018-11-06:CA:oakland:city-council:4": "/rovresults/rcv/236/OaklandCouncilD4/",
    "2018-11-06:CA:oakland:city-council:6": "/rovresults/rcv/236/OaklandCouncilD6/",
    "2018-11-06:CA:berkeley:city-council:1": "/rovresults/rcv/236/BerkeleyCouncilD1/",
}
CATEGORIES = ("Exhausted by Over Votes", "Under Votes", "Exhausted Ballots")

OFFICIAL = (
    "The Registrar of Voters' ranked-choice report for the contest: the pass report through 2018 and the RCV "
    "detailed report from 2022. Ballots exhausted in a round are the report's non-transferable total, which counts "
    "blank ballots, overvotes, and exhausted ballots together. Each candidate's value at round n is their running "
    "total; an eliminated candidate has 0 at the round of their elimination."
)
PRECINCTS = (
    "The Registrar of Voters' comprehensive ranked-choice report, which repeats the round-by-round table for every "
    "precinct. Summed over the precincts, every round reproduces the pass report exactly."
)
FIRST = (
    "First choices by precinct from the Registrar of Voters' comprehensive ranked-choice report. The report "
    "tabulates each precinct as a contest of its own, eliminating whoever trails within that precinct, so only its "
    "first round matches the countywide count; the later rounds are left out. Summed over the precincts, the first "
    "round reproduces the pass report exactly."
)
DERIVED = (
    "Tabulated by electionsarchive.com from the Registrar of Voters' ballot image file, precinct by precinct. A "
    "ballot exhausts at an overvote, a skipped ranking is passed over, and blank ballots are exhausted from the first "
    "round. Each round drops the candidates the official count dropped. Summed over the precincts, every round "
    "reproduces the pass report exactly, and the first round matches the comprehensive report in every precinct."
)


def url(path: str) -> str:
    return (
        path
        if path.startswith("https://")
        else (
            "https://acvote.alamedacountyca.gov" if path.startswith("/acvote-assets") else "https://alamedacountyca.gov"
        )
        + path
    )


def fetch() -> None:
    for link in [
        *PLANS.values(),
        *(path for _, *paths in CONTESTS.values() for path in paths if path),
        *(folder + name for folder in IMAGES.values() for name in ("ballot_image.txt", "master_lookup.txt")),
    ]:
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, url(link), suffix="." + link.rsplit(".", 1)[1])
                break
            except httpx.HTTPError:
                time.sleep(10 * 2**attempt)
        else:
            raw.fetch(SOURCE, url(link), suffix="." + link.rsplit(".", 1)[1])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    official: dict[str, list[dict[str | None, int]]] = {}
    frames = []
    for key, (_, report, comprehensive) in CONTESTS.items():
        file = raw.cached(SOURCE, url(report)).path
        passes: list[dict[str, int]] = []
        if report.endswith(".xml"):
            for step in ET.parse(file).getroot().iter():
                if step.get("Textbox86") is None:
                    continue
                found = {
                    str(choice.get("choiceName2")): round(float(str(choice.get("votes"))))
                    for choice in step.iter()
                    if choice.get("choiceName2") is not None
                }
                if sum(
                    found.pop(name, 0) for name in ("Blanks", "Exhausted", "Overvotes", "Remainder Points", "Suspended")
                ) != round(float(str(step.get("nonTransferableVotes")))):
                    raise ValueError(f"{key}: the non-transferable votes do not add up")
                found["Non Transferable Total"] = round(float(str(step.get("nonTransferableVotes"))))
                passes.append(found)
        else:
            text = subprocess.run(
                ["pdftotext", "-layout", str(file), "-"], capture_output=True, text=True, check=True
            ).stdout
            for block in text.split("Final State")[1:]:
                found = {}
                for line in block.split("Total Ballots")[0].splitlines():
                    match = re.match(
                        r"^\s*(\S.*?\S)\s{2,}(?:(?:\*\* WINNER \*\*|Eliminated in pass \d+\.)\s+)?(-?\d+)(?:\s|$)", line
                    )
                    if match and match[1] not in ("Candidate", "Continuing Ballots"):
                        found[match[1]] = int(match[2])
                found["Non Transferable Total"] = sum(found.pop(name) for name in CATEGORIES)
                passes.append(found)
        labels = {}
        for label in passes[0]:
            name = re.sub(r"^WRITE-IN\s+-?\s*", "", label, flags=re.IGNORECASE)
            labels[label] = "WRITE-IN" if not name or name.casefold() == "write-in" else name
        rounds = []
        for number, found in enumerate(passes):
            tally: dict[str | None, int] = {}
            for label, votes in found.items():
                if label == "Non Transferable Total":
                    tally[None] = votes
                elif number == 0 and (labels[label] != "WRITE-IN" or votes) or votes > 0:
                    tally[labels[label]] = tally.get(labels[label], 0) + votes
            rounds.append(tally)
        if len({sum(tally.values()) for tally in rounds}) != 1:
            raise ValueError(f"{key}: the report's rounds do not add up to one ballot total")
        official[key] = rounds
        if comprehensive is None:
            continue

        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(comprehensive)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        precincts: dict[str, dict[str, dict[int, int]]] = {}
        for page in text.split("\f"):
            unit = re.search(r"Load Type: Precinct \((\d+)\)", page)
            if unit is None or "No pass results returned" in page:
                continue
            numbers = [int(number) for number in re.findall(r"Round (\d+)", page)]
            lines = page.split("\n")
            start = next(index for index, line in enumerate(lines) if re.search(r"Votes\s+%", line))
            known = {
                re.sub(r"[^\x00-\x7f]", "", " ".join(label.split())).casefold(): label
                for label in ("TOTAL", "Continuing Ballots", *CATEGORIES, *labels)
            }
            items: list[tuple[str, list[str] | None]] = []
            for line in lines[start + 1 :]:
                if not line.strip() or line.strip().startswith(("REMARKS", "file:///")):
                    continue
                match = re.match(
                    r"^\s*(.*?)\s*((?:[-+]?\d[\d,]*\*?|\d+\.\d+%|NaN)(?:\s+(?:[-+]?\d[\d,]*\*?|\d+\.\d+%|NaN))*)\s*$",
                    line,
                )
                items.append((line.strip(), None) if match is None else (match[1], match[2].split()))
            rows: list[tuple[str, list[str]]] = []
            claimed: set[int] = set()
            for index, (text, tokens) in enumerate(items):
                if tokens is None:
                    continue
                before = []
                while (
                    len(before) < 3
                    and index - len(before) > 0
                    and items[index - len(before) - 1][1] is None
                    and index - len(before) - 1 not in claimed
                ):
                    before.append(index - len(before) - 1)
                after = []
                while (
                    len(after) < 3 and index + len(after) + 1 < len(items) and items[index + len(after) + 1][1] is None
                ):
                    after.append(index + len(after) + 1)
                for h, t in sorted(
                    ((h, t) for h in range(len(before) + 1) for t in range(len(after) + 1)), key=lambda pair: sum(pair)
                ):
                    words = " ".join(
                        [*(items[i][0] for i in reversed(before[:h])), text, *(items[i][0] for i in after[:t])]
                    ).split()
                    joined = re.sub(r"[^\x00-\x7f]", "", " ".join(words).replace("- ", "-")).casefold()
                    if joined in known:
                        claimed.update(before[:h], after[:t])
                        rows.append((known[joined], tokens))
                        break
                else:
                    raise ValueError(f"{key} precinct {unit[1]}: no label for the row after {items[index - 1][0]!r}")
            target = precincts.setdefault(unit[1], {})
            for label, tokens in rows:
                tokens = [token.replace("*", "").replace(",", "").replace("+", "") for token in tokens]
                votes = (
                    [
                        int(tokens[index - 1])
                        for index, token in enumerate(tokens)
                        if token.endswith("%") or token == "NaN"
                    ]
                    if any(token.endswith("%") or token == "NaN" for token in tokens)
                    else [int(token) for token in tokens[0::2]]
                )
                if len(votes) != len(numbers):
                    raise ValueError(f"{key} precinct {unit[1]}: {label} has {len(votes)} rounds, the page {numbers}")
                target.setdefault(label, {}).update(zip(numbers, votes, strict=True))
        counts = []
        for unit, table in precincts.items():
            if not any(table["TOTAL"].values()):
                continue
            for number in range(1, len(rounds) + 1):
                found = {label: values[number] for label, values in table.items() if number in values}
                if "TOTAL" not in found:
                    continue
                if (
                    sum(votes for label, votes in found.items() if label not in ("TOTAL", "Continuing Ballots"))
                    != found["TOTAL"]
                ):
                    raise ValueError(f"{key} precinct {unit} round {number}: the rows do not add up")
                exhausted = sum(found[label] for label in CATEGORIES)
                merged: dict[str | None, int] = {None: exhausted}
                for label, votes in found.items():
                    if label in labels:
                        merged[labels[label]] = merged.get(labels[label], 0) + votes
                for label, votes in merged.items():
                    if label is None or label in rounds[number - 1] or number > 1 and label in rounds[number - 2]:
                        counts.append((unit, number, label, votes))
        counted = pl.DataFrame(
            counts,
            schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
            orient="row",
        )
        sums = counted.group_by("round", "label").agg(pl.col("votes").sum())
        matched = [
            {label: votes for _, label, votes in sums.filter(pl.col("round") == number).iter_rows() if votes}
            == {label: votes for label, votes in expected.items() if votes}
            for number, expected in enumerate(rounds, 1)
        ]
        if not matched[0]:
            raise ValueError(f"{key}: the precincts' first choices do not add up to the pass report's")
        if not all(matched):
            counted = counted.filter(pl.col("round") == 1)
        if key in IMAGES:
            lookup = raw.cached(SOURCE, url(IMAGES[key] + "master_lookup.txt")).path.read_text(encoding="latin-1")
            (contest_id,) = [line[10:17] for line in lookup.splitlines() if line.startswith("Contest")]
            names: dict[str, str] = {}
            units: dict[str, str] = {}
            for line in lookup.splitlines():
                if line.startswith("Candidate") and line[74:81] == contest_id:
                    found = labels.get(line[17:67].strip()) or ("WRITE-IN" if line[81:82] == "1" else None)
                    if found is None:
                        raise ValueError(f"{key}: no pass report row for {line[17:67].strip()}")
                    names[line[10:17]] = found
                elif line.startswith("Precinct"):
                    units[line[10:17]] = line[17:67].strip()
            ballots: dict[str, tuple[str, list[tuple[int, str | None]]]] = {}
            for line in (
                raw.cached(SOURCE, url(IMAGES[key] + "ballot_image.txt"))
                .path.read_text(encoding="latin-1")
                .splitlines()
            ):
                if line[:7] != contest_id:
                    continue
                ballots.setdefault(line[7:16], (units[line[26:33]], []))[1].append(
                    (
                        int(line[33:36]),
                        "over"
                        if line[43] == "1"
                        else None
                        if line[44] == "1" or line[36:43] == "0000000"
                        else names[line[36:43]],
                    )
                )
            derived: dict[tuple[str, int, str | None], int] = {}
            for unit, marks in ballots.values():
                ranking = [choice for _, choice in sorted(marks, key=lambda mark: mark[0])]
                for number, standing in enumerate(rounds, 1):
                    top = None
                    for choice in ranking:
                        if choice == "over":
                            break
                        if choice is not None and choice in standing:
                            top = choice
                            break
                    derived[unit, number, top] = derived.get((unit, number, top), 0) + 1
            image = pl.DataFrame(
                [
                    (unit, number, label, derived.get((unit, number, label), 0))
                    for unit in sorted({unit for unit, _ in ballots.values()})
                    for number, standing in enumerate(rounds, 1)
                    for label in {*standing, *(rounds[number - 2] if number > 1 else ())}
                ],
                schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
                orient="row",
                infer_schema_length=None,
            )
            sums = image.group_by("round", "label").agg(pl.col("votes").sum())
            for number, expected in enumerate(rounds, 1):
                if {
                    label: votes for _, label, votes in sums.filter(pl.col("round") == number).iter_rows() if votes
                } != {label: votes for label, votes in expected.items() if votes}:
                    raise ValueError(f"{key} round {number}: the ballot images do not reproduce the pass report")
            first = {(unit, label): votes for unit, _, label, votes in counted.iter_rows() if votes}
            if first != {
                (unit, label): votes for unit, number, label, votes in image.iter_rows() if number == 1 and votes
            }:
                raise ValueError(f"{key}: the ballot images' first round differs from the comprehensive report")
            counted = image
        print(key, "every round" if all(matched) or key in IMAGES else "round 1 only", counted["unit"].n_unique())
        frames.append(counted.with_columns(key=pl.lit(key), derived=pl.lit(key in IMAGES)))
    (PARSED / "official.json").write_text(
        json.dumps(
            {
                key: [[[label, votes] for label, votes in tally.items()] for tally in rounds]
                for key, rounds in official.items()
            }
        )
    )
    pl.concat(frames).write_parquet(PARSED / "tallies.parquet")

    rows = []
    with connect() as conn:
        for vintage, plan in PLANS.items():
            with zipfile.ZipFile(raw.cached(SOURCE, plan).path) as archive:
                parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
            reader = shapefile.Reader(
                shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
            )
            codes, shapes = [], []
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                if record is None or shape is None or shape.shapeType == shapefile.NULL or not shape.points:
                    continue
                codes.append(str(record[0]) + ("0" if vintage == "2010" else ""))
                shapes.append(json.dumps(shape.__geo_interface__))
            rows.extend(
                (vintage, *row)
                for row in conn.execute(
                    """select f.code, encode(st_asewkb(s.geom), 'hex'),
                         encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
                       from unnest(%s::text[], %s::text[]) as f(code, geojson)
                       cross join lateral (select st_multi(st_collectionextract(st_makevalid(
                         st_transform(st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)), 3)) as geom) s""",
                    [codes, shapes],
                ).fetchall()
            )
    pl.DataFrame(rows, schema=["plan", "code", "geom", "point"], orient="row").write_parquet(
        PARSED / "precincts.parquet"
    )


def emit() -> Path:
    official = {
        key: [{label: votes for label, votes in tally} for tally in rounds]
        for key, rounds in json.loads((PARSED / "official.json").read_text()).items()
    }
    tallies = pl.read_parquet(PARSED / "tallies.parquet")
    shapes = {
        (plan, code): (geom, point)
        for plan, code, geom, point in pl.read_parquet(PARSED / "precincts.parquet").iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oakland's, Berkeley's, and San Leandro's ranked-choice contests for mayor, city council, auditor, "
            "city attorney, and school board that went past round 1, 2010 to 2025, with each round by precinct "
            "from the comprehensive reports of 2010 to 2018",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        county = cs.area("us.county", "06001")
        (polity,) = conn.execute("select polity_id from polity_territory where area_id = %s", [county]).fetchone() or (
            None,
        )
        if polity is None:
            raise ValueError("Alameda County must already be a polity")
        plans = {}
        for vintage, link in PLANS.items():
            title = f"Consolidated precinct shapes, Alameda County, {vintage} general election"
            plans[vintage] = cs.source(SWDB, title)
            cs.add(
                "source",
                id=plans[vintage],
                kind="academic",
                publisher=SWDB,
                title=title,
                url=link,
                license=None,
            )
            cs.artifact(raw.cached(SOURCE, link), plans[vintage])
        precincts: dict[str, dict[str, UUID]] = {}
        for number, unit in conn.execute(
            """select substring(n.text from '^Precinct (\\d+), '), ru.id from reporting_unit ru
               join name n on n.entity_id = ru.area_id and n.is_preferred
               join area_parent p on p.area_id = ru.area_id
               where ru.scheme_code = 'us.precinct' and ru.code ~ '^2016:CA:[0-9]+$' and p.parent_id = %s""",
            [county],
        ).fetchall():
            precincts.setdefault("2016-11-08", {})[number] = unit
        for day, number in sorted(set(tallies.select(pl.col("key").str.slice(0, 10), "unit").iter_rows())):
            if day == "2016-11-08":
                continue
            vintage = day[:4]
            area = cs.area("us.precinct", f"{vintage}:CA:06001:{number}")
            cs.add("area", id=area, scheme_code="us.precinct")
            cs.add("area_code", area_id=area, code=f"{vintage}:CA:06001:{number}", valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=county)
            name(area, f"Precinct {number}, Alameda County")
            if (vintage, number) in shapes:
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=date.fromisoformat(day),
                    valid_to=date.fromisoformat(day) + timedelta(days=1),
                    geom=shapes[vintage, number][0],
                    label_point=shapes[vintage, number][1],
                    quality="official",
                    source_id=plans[vintage],
                )
            code = f"{day}:CA:06001:{number}"
            unit = cs.unit("us.precinct", code)
            precincts.setdefault(day, {})[number] = unit
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=polity,
                scheme_code="us.precinct",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=date.fromisoformat(day),
                valid_to=date.fromisoformat(day) + timedelta(days=1),
            )
        places = {
            "oakland": cs.area("us.place", "0653000"),
            "berkeley": cs.area("us.place", "0606000"),
            "san-leandro": cs.area("us.place", "0668084"),
        }
        people: dict[tuple[str, str], set[tuple[UUID, date, UUID]]] = {}
        for text, person, held, area in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, e.date_from, t.area_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = any(%s)""",
            [[county, *places.values(), cs.area("us.school_district", "0628050")]],
        ).fetchall():
            words = spell(text)
            people.setdefault((words[0], words[-1]), set()).add((person, held, area))

        irv = cs.key("us.electoral_system", "irv")
        if irv in cs.minted:
            raise ValueError("the IRV electoral system must already exist")
        for key, (existing, report, comprehensive) in CONTESTS.items():
            day = date.fromisoformat(key[:10])
            rounds = official[key]
            contest = cs.key("us.contest", existing)
            columns = (
                "election_id",
                "kind",
                "office_id",
                "body_id",
                "district_area_id",
                "seat_id",
                "boundary_set_id",
                "magnitude",
                "party_scope_id",
                "round_number",
                "previous_round_id",
                "allocation_parent_id",
                "rules_override",
                "default_result_kind",
                "term",
            )
            row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
            if contest in cs.minted or row is None:
                raise ValueError(f"{key}: {existing} must already exist")
            contest_row = dict(zip(columns, row, strict=True))
            cs.add("contest", id=contest, **contest_row, electoral_system_id=irv)
            election, district = contest_row["election_id"], contest_row["district_area_id"]
            previous = cs.key(f"us.{existing.split(':')[0]}.result_set", existing)
            if previous in cs.minted:
                raise ValueError(f"{key}: {existing}'s result set must already exist")
            tops = conn.execute(
                """select distinct u.id from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k where r.id = %s""",
                [previous],
            ).fetchall()
            if len(tops) != 1:
                raise ValueError(f"{key}: {existing}'s result set has {len(tops)} reporting units")
            top = tops[0][0]
            city, office = key.split(":", 3)[2:]
            place = {"oakland": "Oakland", "berkeley": "Berkeley", "san-leandro": "San Leandro"}[city]
            seat = (
                {"mayor": "Mayor", "auditor": "City Auditor", "city-attorney": "City Attorney"}.get(office)
                or ("City Council, At-Large" if office == "city-council:at-large" else None)
                or (
                    f"City Council, District {office.split(':')[1]}"
                    if office.startswith("city-council")
                    else f"School Director, District {office.split(':')[1]}"
                )
            )
            sources = []
            for path, kind in ((report, "ranked-choice report"), (comprehensive, "comprehensive ranked-choice report")):
                if path is None:
                    continue
                title = f"{day:%B} {day.day}, {day.year} election: {seat} - {place}, {kind}"
                source = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url(path),
                    license="Public record",
                )
                cs.artifact(raw.cached(SOURCE, url(path)), source)
                sources.append(source)
            if key in IMAGES:
                title = f"{day:%B} {day.day}, {day.year} election: {seat} - {place}, ballot image file"
                source = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=url(IMAGES[key] + "ballot_image.txt"),
                    license="Public record",
                )
                for file in ("ballot_image.txt", "master_lookup.txt"):
                    cs.artifact(raw.cached(SOURCE, url(IMAGES[key] + file)), source)
                sources[1] = source

            current = conn.execute(
                """select o.id, o.ballot_label, h.pipeline = %s from contest_option o
                   join entity n on n.id = o.id join changeset h on h.id = n.changeset_id
                   where o.contest_id = %s and o.kind = 'candidate'""",
                [SOURCE, contest],
            ).fetchall()
            options: dict[str, UUID] = {}
            for label in (label for label in rounds[0] if label is not None):
                if label == "WRITE-IN":
                    found = [
                        option
                        for (option,) in conn.execute(
                            "select id from contest_option where contest_id = %s and kind = 'write_in'", [contest]
                        ).fetchall()
                    ]
                    if len(found) > 1:
                        raise ValueError(f"{key}: {len(found)} write-in options")
                    options[label] = found[0] if found else cs.key("us.option", f"{key}:write_in:scattered")
                    if not found:
                        cs.add(
                            "contest_option",
                            id=options[label],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Write-in",
                        )
                    continue
                words = spell(label)
                spelled = {option: spell(text) for option, text, _ in current}
                found = [option for option, other in spelled.items() if other[-1] == words[-1]]
                if len(found) > 1 or [
                    other for other in rounds[0] if other is not None and spell(other)[-1] == words[-1]
                ] != [label]:
                    found = [option for option in found if spelled[option][0] == words[0]]
                if len(found) > 1:
                    found = [option for option, _, mine in current if mine and option in found] or found
                if len(found) == 1:
                    options[label] = found[0]
                    continue
                if found:
                    raise ValueError(f"{key}: {len(found)} options for {label}")
                display = label.title().replace("''", '"')
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, _, _ in known}
                for preferred in (
                    {person for person, held, _ in known if held == day},
                    {person for person, _, area in known if area == places[city]},
                ):
                    if len(preferred) == 1:
                        matches = preferred
                        break
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} people for {label}")
                if matches:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(display))
                    cs.add("person", id=person)
                    name(person, display)
                options[label] = cs.key("us.option", f"{key}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=display)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=display,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len(set(options.values())) != len(options):
                raise ValueError(f"{key}: two candidates share an option")

            for values in conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                     supersedes_id from result_set where id = %s""",
                [previous],
            ).fetchall():
                cs.add(
                    "result_set",
                    **dict(
                        zip(
                            (
                                "id",
                                "election_id",
                                "contest_id",
                                "stage",
                                "kind",
                                "status",
                                "source_id",
                                "as_of",
                                "completeness",
                                "supersedes_id",
                            ),
                            values,
                            strict=True,
                        )
                    ),
                    is_default=False,
                )
            result = cs.key(f"{SOURCE}.result_set", key)
            final = rounds[-1]
            winner = max((label for label in final if label is not None), key=lambda label: final[label])
            for label, option in options.items():
                for kind in ("won", "elected") if label == winner else ("lost" if label in final else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=kind)
            sets = [
                (
                    result,
                    True,
                    OFFICIAL,
                    sources[0],
                    0,
                    pl.DataFrame(
                        [
                            ("top", number, label, tally.get(label, 0))
                            for number, tally in enumerate(rounds, 1)
                            for label in rounds[0]
                            if label in tally or label in rounds[number - 2]
                        ],
                        schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
                        orient="row",
                    ),
                )
            ]
            units = {"top": str(top)}
            rows = tallies.filter(pl.col("key") == key)
            if not rows.is_empty():
                for number in rows["unit"].unique():
                    if number not in precincts[key[:10]]:
                        raise ValueError(f"{key}: no reporting unit for precinct {number}")
                    units[number] = str(precincts[key[:10]][number])
                if district is not None and office != "mayor":
                    cs.frame(
                        "reporting_unit_area",
                        pl.DataFrame(
                            {
                                "unit_id": [units[number] for number in rows["unit"].unique().sort()],
                                "area_id": str(district),
                                "valid_from": day,
                                "valid_to": day + timedelta(days=1),
                            }
                        ),
                    )
                sets.append(
                    (
                        cs.key(f"{SOURCE}.result_set", f"{key}:precincts"),
                        False,
                        DERIVED if key in IMAGES else PRECINCTS if rows.filter(pl.col("round") > 1).height else FIRST,
                        sources[1],
                        int(key in IMAGES),
                        rows.select("unit", "round", "label", "votes"),
                    )
                )

            for result_set, default, note, source, flags, values in sets:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if result_set not in cs.minted:
                    continue
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                unknown = set(values["label"].drop_nulls()) - set(options)
                if unknown:
                    raise ValueError(f"{key}: no options for {unknown}")
                frame = values.with_columns(unit_id=pl.col("unit").replace_strict(units))
                first = frame.filter(pl.col("round") == 1)
                frame = pl.concat(
                    [
                        frame,
                        first.group_by("unit", "unit_id")
                        .agg(pl.col("votes").sum())
                        .select(
                            "unit", round=pl.lit(0, pl.Int16), label=pl.lit("ballots"), votes="votes", unit_id="unit_id"
                        ),
                        first.filter(pl.col("label").is_not_null())
                        .group_by("unit", "unit_id")
                        .agg(pl.col("votes").sum())
                        .select(
                            "unit", round=pl.lit(1, pl.Int16), label=pl.lit("valid"), votes="votes", unit_id="unit_id"
                        ),
                    ]
                )
                cs.frame(
                    "result_value",
                    frame.select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id="unit_id",
                        option_id=pl.col("label").replace_strict(
                            {label: str(option) for label, option in options.items()}, default=None
                        ),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.when(pl.col("label").is_null())
                        .then(21)
                        .otherwise(pl.col("label").replace_strict({"ballots": 3, "valid": 4}, default=1))
                        .cast(pl.Int16),
                        count_round=pl.col("round").cast(pl.Int16),
                        flags=pl.lit(flags, pl.Int16),
                        value="votes",
                    ),
                )
        return cs.write()
