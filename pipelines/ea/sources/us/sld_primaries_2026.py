import csv
import json
import re
import subprocess
import time
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.enr_clarity import STATE_NAMES
from ea.sources.us.oe_primaries_2026 import (
    BASE,
    BROWSER,
    CT_COUNTIES,
    EV,
    FIPS,
    IL_CITIES,
    IL_URL,
    IN_URL,
    KS_URL,
    MD_URL,
    OK_BASE,
    RUNOFF_SYSTEMS,
    THRESHOLDS,
    elstats,
    plain,
    town_counties,
    url,
)
from ea.store import DATA, connect

SOURCE = "us.sld_primaries_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ROUNDS = [
    (
        "MI",
        date(2026, 8, 4),
        "Michigan Board of State Canvassers",
        "August 4, 2026 primary election official results by county",
        "https://mielections.us/election/results/2026PRI_CENR.html",
        "https://mielections.us/election/results/2026PRI_CENR.html#2026STATE_PRIMARY_MI_CENR_BY_COUNTY.txt",
    ),
    (
        "NV",
        date(2026, 6, 9),
        "Nevada Secretary of State",
        "June 9, 2026 primary election results by county",
        "https://silverstateelection.nv.gov/",
        url("nv", "nv-2026-primary-county-results.xml"),
    ),
    (
        "SC",
        date(2026, 6, 9),
        "South Carolina State Election Commission",
        "June 9, 2026 statewide primaries: official results by county",
        "https://scvotes.gov/",
        url("sc", "2026-06-09/detailxml.zip"),
    ),
    (
        "SC",
        date(2026, 6, 23),
        "South Carolina State Election Commission",
        "June 23, 2026 primary runoffs: official results by county",
        "https://scvotes.gov/",
        url("sc", "2026-06-23/detailxml.zip"),
    ),
    (
        "UT",
        date(2026, 6, 23),
        "Utah Lieutenant Governor's Office",
        "June 23, 2026 primary election official results",
        "https://electionresults.utah.gov/",
        f"{EV['ut'][0]}ballot-items",
    ),
    (
        "MN",
        date(2026, 8, 11),
        "Minnesota Secretary of State",
        "August 11, 2026 state primary official results by precinct",
        "https://electionresults.sos.mn.gov/",
        "https://electionresultsfiles.sos.mn.gov/20260811/allracesbyprecinct.txt",
    ),
    (
        "RI",
        date(2026, 9, 9),
        "Rhode Island Board of Elections",
        "September 9, 2026 statewide primary official results",
        "https://electionresults.ri.gov/",
        f"{EV['ri'][0]}ballot-items",
    ),
]
OPENELECTIONS = {
    "SD": ("South Dakota", "sd", date(2026, 6, 2), "20260602__sd__primary__county.csv"),
    "ND": ("North Dakota", "nd", date(2026, 6, 9), "20260609__nd__primary__county.csv"),
    "OR": ("Oregon", "or", date(2026, 5, 19), "20260519__or__primary__county.csv"),
    "KY": ("Kentucky", "ky", date(2026, 5, 19), "20260519__ky__primary__county.csv"),
    "CO": ("Colorado", "co", date(2026, 6, 30), "20260630__co__primary__county.csv"),
    "AZ": ("Arizona", "az", date(2026, 7, 21), "20260721__az__primary__county.csv"),
    "WY": ("Wyoming", "wy", date(2026, 8, 18), "20260818__wy__primary__county.csv"),
    "TX": ("Texas", "tx", date(2026, 3, 3), "20260303__tx__primary__county.csv"),
    "PA": ("Pennsylvania", "pa", date(2026, 5, 19), "20260519__pa__primary__county.csv"),
    "MO": ("Missouri", "mo", date(2026, 8, 4), "20260804__mo__primary__county.csv"),
    "TN": ("Tennessee", "tn", date(2026, 8, 6), "20260801__tn__primary__county.csv"),
    "NM": ("New Mexico", "nm", date(2026, 6, 2), "20260602__nm__primary__county.csv"),
}
ROUNDS += [
    (
        "WV",
        date(2026, 5, 12),
        "West Virginia Secretary of State",
        "May 12, 2026 primary election results by county",
        "https://sos.wv.gov/elections/election-data/historical-election-results-and-turnout",
        url("wv", "detailxml"),
    ),
    (
        "WI",
        date(2026, 8, 11),
        "Wisconsin Elections Commission",
        "August 11, 2026 partisan primary: county by county report of the canvass",
        "https://elections.wi.gov/election-result/2026-partisan-primary",
        url("wi", "County by County Report_Partisan Primary 2026_All State Contests.xlsx"),
    ),
    (
        "GA",
        date(2026, 5, 19),
        "Georgia Secretary of State",
        "May 19, 2026 general primary official results by county",
        "https://results.sos.ga.gov/",
        f"{EV['ga'][0]}ballot-items",
    ),
    (
        "GA",
        date(2026, 6, 16),
        "Georgia Secretary of State",
        "June 16, 2026 general primary runoff official results by county",
        "https://results.sos.ga.gov/",
        f"{EV['ga2'][0]}ballot-items",
    ),
    *(
        (
            "AL",
            date(2026, 5, 19) if file == "2026_Primary_Election.zip" else date(2026, 6, 16),
            "Alabama Secretary of State",
            f"{'May 19, 2026 primary' if file == '2026_Primary_Election.zip' else 'June 16, 2026 primary runoff'} election results by precinct, county files",
            "https://www.sos.alabama.gov/alabama-votes/voter/election-data",
            url("alx", f"2026-07/{file}"),
        )
        for file in ("2026_Primary_Election.zip", "2026_PRIMARY_RUNOFF_ELECTION.zip")
    ),
    (
        "ID",
        date(2026, 5, 19),
        "Idaho Secretary of State",
        "May 19, 2026 primary election official results by county",
        "https://results.voteidaho.gov/",
        f"{EV['id'][0]}ballot-items",
    ),
    (
        "IN",
        date(2026, 5, 5),
        "Indiana Secretary of State",
        "May 5, 2026 primary election results by county",
        "https://enr.indianavoters.in.gov",
        IN_URL.format("OffCatC_1040_B.json"),
    ),
    (
        "IA",
        date(2026, 6, 2),
        "Iowa Secretary of State",
        "June 2, 2026 primary election official canvass by county",
        "https://sos.iowa.gov/elections/results/",
        url("ia", "detailxlsx.zip"),
    ),
    (
        "NC",
        date(2026, 3, 3),
        "North Carolina State Board of Elections",
        "March 3, 2026 primary election results by precinct",
        "https://www.ncsbe.gov/results-data",
        url("nc", "results_pct_20260303.zip"),
    ),
    *(
        (
            state,
            day,
            "OpenElections",
            f"{state_name} 2026 primary results by county",
            f"https://github.com/openelections/openelections-data-{code}",
            BASE.format(code, file),
        )
        for state, (state_name, code, day, file) in OPENELECTIONS.items()
    ),
]
ROUNDS += [
    (
        "FL",
        date(2026, 8, 18),
        "Florida Division of Elections",
        "August 18, 2026 primary election official results by county",
        "https://results.elections.myflorida.com",
        url("fl", "extract"),
    ),
    (
        "DE",
        date(2026, 9, 15),
        "Delaware Department of Elections",
        "September 15, 2026 primary election results",
        "https://elections.delaware.gov/results/",
        url("de", "PR2026.html"),
    ),
    *(
        (
            "AR",
            day,
            "Arkansas Secretary of State",
            f"{title} results by county",
            "https://arkansas.tally-enr.com/",
            f"https://results.tally-enr.com/results/arkansas/{election}/FullDataFile.json",
        )
        for day, title, election in (
            (date(2026, 3, 3), "March 3, 2026 preferential primary", "7f77a178-af02-40ec-92db-c5cc50882c68"),
            (date(2026, 3, 31), "March 31, 2026 primary runoff", "b412bdef-f97a-45bc-b3ec-6761d28caf9e"),
        )
    ),
    (
        "CT",
        date(2026, 8, 11),
        "Connecticut Secretary of the State",
        "August 11, 2026 primary results by town",
        "https://ctemspublic.pcctg.net/",
        url("ct", "111/27708/townVotes_Electiondata"),
    ),
    *(
        (
            "OK",
            day,
            "Oklahoma State Election Board",
            f"{title} results by county",
            "https://results.okelections.gov/OKER/",
            url("ok", f"getelec/{day:%Y%m%d}"),
        )
        for day, title in (
            (date(2026, 6, 16), "June 16, 2026 primary election"),
            (date(2026, 8, 25), "August 25, 2026 primary runoff election"),
        )
    ),
]
CIVERA = {
    "NY": ("https://ny.elstats3.civera.com", {5: "senate", 14: "assembly"}),
    "VT": ("https://electionarchive.vermont.gov", {9: "senate", 8: "house"}),
}
ROUNDS += [
    (
        state,
        day,
        publisher,
        title,
        page,
        f"{CIVERA[state][0]}/api/download_search.csv?"
        + urlencode(
            {
                "search": json.dumps(
                    {
                        "global": {"years": {"from": 2026, "to": 2026}},
                        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
                        "contests": {
                            "candidates": [],
                            "divisions": [],
                            "offices": [{"id": office} for office in CIVERA[state][1]],
                        },
                        "specialElectionsOnly": False,
                        "voterStats": False,
                        "stages": [],
                    },
                    separators=(",", ":"),
                )
            }
        ),
    )
    for state, day, publisher, title, page in (
        (
            "NY",
            date(2026, 6, 23),
            "New York State Board of Elections",
            "June 23, 2026 primary election official results by county",
            "https://results.elections.ny.gov",
        ),
        (
            "VT",
            date(2026, 8, 11),
            "Vermont Secretary of State",
            "August 11, 2026 primary election official results by town",
            "https://electionarchive.vermont.gov",
        ),
    )
]
IL_FILES = [
    IL_URL.format(
        f"69-{base + 10 * number}-{number}{'TH' if 10 <= number % 100 <= 20 else {1: 'ST', 2: 'ND', 3: 'RD'}.get(number % 10, 'TH')}%20{office}-2026GP.csv"
    )
    for office, base, seats in (("SENATE", 780, 59), ("REPRESENTATIVE", 1370, 118))
    for number in range(1, seats + 1)
]
MD_PAGES = [
    MD_URL.format(f"gen_results_2026_{office}_{number}.html")
    for office, seats in ((5, 47), (6, 71))
    for number in range(1, seats + 1)
]
ROUNDS += [
    (
        "HI",
        date(2026, 8, 8),
        "Hawaii Office of Elections",
        "August 8, 2026 primary election final results by county",
        "https://elections.hawaii.gov",
        url("hi", "cch"),
    ),
    (
        "IL",
        date(2026, 3, 17),
        "Illinois State Board of Elections",
        "March 17, 2026 general primary election official results by precinct",
        "https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx",
        IL_FILES[59],
    ),
    (
        "KS",
        date(2026, 8, 4),
        "Kansas Secretary of State",
        "August 4, 2026 primary election official results by precinct",
        "https://sos.ks.gov/elections/election-results.html",
        KS_URL.format("Kansas-House-of-Representatives"),
    ),
    (
        "MD",
        date(2026, 6, 23),
        "Maryland State Board of Elections",
        "June 23, 2026 gubernatorial primary election official results by county",
        MD_URL.format(""),
        MD_PAGES[0],
    ),
    (
        "MA",
        date(2026, 9, 1),
        "Massachusetts Secretary of the Commonwealth",
        "September 1, 2026 state primary official results by town",
        "https://electionstats.state.ma.us",
        elstats.ma_primary_search(8, 2026),
    ),
]
DELIVERED = {
    "ME": (DATA / "inbox" / "manual" / "MF-148", "https://www.maine.gov/sos/elections-voting/election-results-data"),
    "MT": (DATA / "inbox" / "manual" / "MF-149", "https://electionresults.mt.gov/ResultsExport.aspx?eid=450002928"),
    "NH": (DATA / "inbox" / "manual" / "MF-150", "https://www.sos.nh.gov/2026-state-primary-election-results"),
}
DELIVERED_FILES = {
    "ME": [
        *(
            f"{office} {party} - FINAL.xlsx"
            for office in ("State Senate", "Rep to the Legislature")
            for party in ("DEM", "REP")
        ),
        "Senate District 4 - Republican.xlsx",
        "House District 58 - Republican.xlsx",
    ],
    "MT": ["Legislative Results.xlsx"],
    "NH": sorted(path.name for path in DELIVERED["NH"][0].glob("2026-sp-*.xlsx")),
}
ROUNDS += [
    (
        state,
        day,
        publisher,
        title,
        DELIVERED[state][1],
        f"{DELIVERED[state][1]}#{DELIVERED_FILES[state][0]}",
    )
    for state, day, publisher, title in (
        (
            "ME",
            date(2026, 6, 9),
            "Maine Secretary of State",
            "June 9, 2026 primary election official results by municipality",
        ),
        (
            "MT",
            date(2026, 6, 2),
            "Montana Secretary of State",
            "June 2, 2026 primary election legislative results by county",
        ),
        (
            "NH",
            date(2026, 9, 8),
            "New Hampshire Secretary of State",
            "September 8, 2026 state primary election results by town",
        ),
    )
]
SYSTEMS = {**RUNOFF_SYSTEMS, "AR": "two_round", "ME": "irv"}
MAJORITY = {**THRESHOLDS, "AR": 50}
AR_CONTESTS = "https://enr-results-api.totalresults.com/Contest/GetContestSearchList?cid=arkansas&electionID={}"
SIDECARS = {
    "CT": [
        url("ct", file) for file in ("111/27708/Lookupdata", "112/28256/Lookupdata", "112/28256/townVotes_Electiondata")
    ],
    "HI": [url("hi", county) for county in ("coh", "cok", "com")],
    "MA": [elstats.ma_primary_search(9, 2026)],
    **{state: [f"{DELIVERED[state][1]}#{name}" for name in names[1:]] for state, names in DELIVERED_FILES.items()},
}
ME_COUNTIES = [
    "AND",
    "ARO",
    "CUM",
    "FRA",
    "HAN",
    "KEN",
    "KNO",
    "LIN",
    "OXF",
    "PEN",
    "PIS",
    "SAG",
    "SOM",
    "WAL",
    "WAS",
    "YOR",
]
OK_RACE = re.compile(r"FOR STATE (SENATOR|REPRESENTATIVE) DISTRICT (\d+)")
LEGISLATIVE = re.compile(
    r"(?P<party>REP|DEM) (?:State (?P<chamber>Senate|House)|(?P<body>Senator|Representative) in General Assembly) District (?P<district>\d+)"
)
ITEMS = {
    "UT": LEGISLATIVE,
    "RI": LEGISLATIVE,
    "GA": re.compile(r"State (?P<chamber>Senate|House) - District (?P<district>\d+) - (?P<party>Rep|Dem)"),
    "ID": re.compile(
        r"State (?P<body>Senator|Representative) District (?P<district>\d+)(?: Seat (?P<seat>[AB]))? - (?P<party>Republican|Democratic)"
    ),
}
STATEWIDE = {
    "Lieutenant Governor": ("lieutenant-governor", "Nevada Lieutenant Governor"),
    "Attorney General": ("attorney-general", "Nevada Attorney General"),
    "Secretary Of State": ("secretary-of-state", "Nevada Secretary of State"),
    "State Treasurer": ("treasurer", "Nevada Treasurer"),
    "State Controller": ("controller", "Nevada Controller"),
}
PARTIES = {
    "dfl": ("dp", "MN:minnesota-democratic-farmer-labor-party", "Democratic–Farmer–Labor"),
    "democratic": ("dp", "democratic-party", "Democratic"),
    "republican": ("rp", "republican-party", "Republican"),
}


def cased(text: str) -> str:
    if not text.isupper():
        return text
    return re.sub(
        r'"(\w{1,3})"',
        lambda match: f'"{match[1].upper()}"',
        re.sub(r"\bMc(\w)", lambda match: f"Mc{match[1].upper()}", text.title()),
    )


def shared(address: str) -> None:
    try:
        raw.cached(SOURCE, address)
    except LookupError:
        try:
            held = raw.cached("us.oe_primaries_2026", address)
        except LookupError:
            raw.fetch(SOURCE, address, suffix=".json" if "okelections" in address else ".csv")
            return
        raw.store(
            SOURCE,
            address,
            held.path.read_bytes(),
            suffix=held.path.suffix,
            retrieved_at=held.retrieved_at,
            media_type=held.media_type,
        )


def fetch() -> None:
    for state, day, *_, address in ROUNDS:
        if state == "IN":
            for file in ("OffCatC_1018_B.json", "OffCatC_1040_B.json"):
                raw.fetch(SOURCE, IN_URL.format(file), suffix=".json")
            continue
        if state in (*ITEMS, "MN"):
            artifact = raw.fetch(SOURCE, address, suffix=".json" if state != "MN" else ".txt")
            if state != "MN":
                for item in json.loads(artifact.path.read_bytes())["data"]:
                    if ITEMS[state].fullmatch(item["name"][0]["text"]):
                        for attempt in range(4):
                            try:
                                raw.fetch(SOURCE, f"{address}/{item['id']}", suffix=".json")
                                break
                            except httpx.HTTPStatusError as error:
                                if error.response.status_code != 404:
                                    raise
                                time.sleep(2 * (attempt + 1))
                        else:
                            print("no detail for", state, item["name"][0]["text"])
            continue
        if state == "AR":
            election = address.split("/")[-2]
            for link in (address, AR_CONTESTS.format(election)):
                try:
                    raw.cached(SOURCE, link)
                except LookupError:
                    raw.store(
                        SOURCE,
                        link,
                        httpx.get(link, headers={**BROWSER, "Origin": "https://arkansas.tally-enr.com"}, timeout=600)
                        .raise_for_status()
                        .content,
                        suffix=".json",
                        retrieved_at=datetime.now(UTC),
                        media_type="application/json",
                    )
            continue
        if state in DELIVERED:
            for name in DELIVERED_FILES[state]:
                try:
                    raw.cached(SOURCE, f"{DELIVERED[state][1]}#{name}")
                except LookupError:
                    raw.store(
                        SOURCE,
                        f"{DELIVERED[state][1]}#{name}",
                        (DELIVERED[state][0] / name).read_bytes(),
                        suffix=".xlsx",
                        retrieved_at=datetime(2026, 10, 28, tzinfo=UTC),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            continue
        if state == "MD":
            for page in MD_PAGES:
                for link in re.findall(
                    r'href="(gen_detail_results_[^"]+)"', raw.fetch(SOURCE, page, suffix=".html").path.read_text()
                ):
                    raw.fetch(SOURCE, MD_URL.format(link.replace(" ", "%20")), suffix=".html")
            continue
        if state == "IL":
            for link in IL_FILES:
                try:
                    raw.fetch(SOURCE, link, suffix=".csv")
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 404:
                        raise
            continue
        if state == "MA":
            for listing in (address, *SIDECARS[state]):
                for election, *_ in elstats.ma_primary_rows(
                    raw.fetch(SOURCE, listing, suffix=".html").path.read_text()
                ):
                    raw.fetch(SOURCE, f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:0/", suffix=".csv")
            continue
        shared(address)
        for other in SIDECARS.get(state, []):
            shared(other)
        if state == "OK":
            token = httpx.put(
                OK_BASE + "enrapi/login/",
                json={"Username": "appuser", "Password": "X098de!22k098Mgfd"},
                headers=BROWSER,
                timeout=60,
            ).json()[1:]
            for race in json.loads(raw.cached(SOURCE, address).path.read_bytes())["races"]:
                race_url = url("ok", f"{day:%Y%m%d}/{race['raceID']}")
                if OK_RACE.fullmatch(race["raceTitle"]):
                    try:
                        raw.cached(SOURCE, race_url)
                    except LookupError:
                        raw.store(
                            SOURCE,
                            race_url,
                            httpx.get(race_url, headers={**BROWSER, "Authorization": f"Bearer {token}"}, timeout=300)
                            .raise_for_status()
                            .content,
                            suffix=".json",
                            retrieved_at=datetime.now(UTC),
                            media_type="application/json",
                        )


def parse() -> None:
    frames = []
    for state, day, *_, address in ROUNDS:
        path = raw.cached(SOURCE, address).path
        if state == "MI":
            frames.append(
                pl.read_csv(
                    path,
                    separator="\t",
                    skip_rows=1,
                    infer_schema=False,
                    quote_char=None,
                    truncate_ragged_lines=True,
                )
                .filter(pl.col("OfficeCode(text)").is_in(["9", "10"]))
                .select(
                    state=pl.lit(state),
                    day=pl.lit(day),
                    party=pl.col("PartyDescription").str.to_lowercase(),
                    chamber=pl.when(pl.col("OfficeCode(text)") == "9")
                    .then(pl.lit("senate"))
                    .otherwise(pl.lit("house")),
                    district=pl.col("DistrictCode(Text)").cast(pl.Int32).cast(pl.String),
                    candidate=pl.concat_str(
                        "CandidateFirstName",
                        "CandidateMiddleName",
                        "CandidateLastName",
                        separator=" ",
                        ignore_nulls=True,
                    ).str.replace_all(r"\s+", " "),
                    write_in=pl.col("WriteIn(W)/Uncommitted(Z)").fill_null("").is_in(["W", "Y"]),
                    county=pl.col("CountyName"),
                    votes=pl.col("CandidateVotes").str.replace_all(",", "").cast(pl.Int64),
                )
            )
            continue
        rows = []
        if state in OPENELECTIONS:
            for county, office, district, party, candidate, votes, *_ in pl.read_csv(
                path, infer_schema=False
            ).iter_rows():
                chamber = {"State House": "house", "State Representative": "house"}.get(office) or (
                    "senate" if office in ("State Senate", "State Senator") else None
                )
                party = {"REP": "republican", "R": "republican", "DEM": "democratic", "D": "democratic"}.get(
                    party, (party or "").lower()
                )
                if (
                    chamber is None
                    or party not in PARTIES
                    or candidate is None
                    or not (votes or "0").isdigit()
                    or re.search(r"(?i)write-?ins?$|^(over|under) votes$|withdrawn", candidate)
                ):
                    continue
                rows.append(
                    (
                        chamber,
                        re.sub(r"^(\d+)([AB])$", lambda match: f"{match[1]}-{match[2].lower()}", district),
                        party,
                        re.sub(r"\bMc(\w)", lambda match: f"Mc{match[1].upper()}", candidate.title())
                        if candidate.isupper()
                        else candidate,
                        county,
                        int(votes or 0),
                    )
                )
        if state == "FL":
            seen = set()
            for row in pl.read_csv(path, separator="\t", infer_schema=False, quote_char=None).iter_rows(named=True):
                key = tuple(
                    row[column]
                    for column in (
                        "RaceCode",
                        "PartyCode",
                        "Juris1num",
                        "CountyCode",
                        "CanNameLast",
                        "CanNameFirst",
                        "CanNameMiddle",
                    )
                )
                if row["RaceCode"] not in ("STS", "STR") or row["PartyCode"] not in ("REP", "DEM") or key in seen:
                    continue
                seen.add(key)
                rows.append(
                    (
                        "senate" if row["RaceCode"] == "STS" else "house",
                        row["Juris1num"].lstrip("0"),
                        "republican" if row["PartyCode"] == "REP" else "democratic",
                        " ".join(
                            part for part in (row["CanNameFirst"], row["CanNameMiddle"], row["CanNameLast"]) if part
                        ),
                        row["CountyName"],
                        int(row["CanVotes"]),
                    )
                )
        if state == "DE":
            for chamber, district, party, body in re.findall(
                r"<caption>State (Senator|Representative) District (\d+)<br>(Democratic|Republican) Party</caption>(.*?)</table>",
                path.read_text(encoding="utf-8", errors="replace"),
                flags=re.DOTALL,
            ):
                rows.extend(
                    (
                        "senate" if chamber == "Senator" else "house",
                        district,
                        party.lower(),
                        cased(candidate.strip()),
                        "",
                        int(votes.replace(",", "")),
                    )
                    for candidate, votes in re.findall(
                        r'data-label="Candidate Name">([^<]+)</td>.*?data-label="Total Votes">([\d,]+)</td>',
                        body,
                        flags=re.DOTALL,
                    )
                )
        if state == "AR":
            names = json.loads(raw.cached(SOURCE, AR_CONTESTS.format(address.split("/")[-2])).path.read_text())[
                "response"
            ]["contests"]
            for contest in json.loads(path.read_text())["ContestData"]:
                found = re.fullmatch(
                    r"(REP|DEM) STATE (REPRESENTATIVE|SENATE) +(?:DIST\.|DISTRICT) (\d+)",
                    names.get(contest["ContestID"], {}).get("contestName", "").upper(),
                )
                if found is None:
                    continue
                rows.extend(
                    (
                        "senate" if found[2] == "SENATE" else "house",
                        str(int(found[3])),
                        "republican" if found[1] == "REP" else "democratic",
                        cased(
                            names[contest["ContestID"]]["choices"][choice["ChoiceID"]]["name"].removeprefix(
                                "State Representative "
                            )
                        ),
                        fips,
                        choice["TotalVotes"],
                    )
                    for fips, location in contest["Locations"].items()
                    for choice in location["Choices"]
                )
        if state == "CT":
            totals = defaultdict(int)
            for election in ("111/27708", "112/28256"):
                lookup = json.loads(raw.cached(SOURCE, url("ct", f"{election}/Lookupdata")).path.read_text())
                counties_of = {
                    entry["TownID"]: "09" + CT_COUNTIES[lookup["counties"][entry["CountyID"]]]
                    for entry in lookup["countyTowns"]
                }
                offices = {key: value for entry in lookup["officeList"] for key, value in entry.items()}
                votes = json.loads(raw.cached(SOURCE, url("ct", f"{election}/townVotes_Electiondata")).path.read_text())
                for town, contests in votes.items():
                    for office_id, results in contests.items():
                        found = re.fullmatch(r"State (Senator|Representative) (\d+)", offices[office_id]["NM"])
                        if found is None:
                            continue
                        for result in results:
                            for candidate_id, tally in result.items():
                                candidate = lookup["candidateIds"][candidate_id]
                                party = lookup["partyIds"][candidate["P"]]["NM"]
                                if party in ("Democratic Party", "Republican Party"):
                                    totals[
                                        (
                                            "senate" if found[1] == "Senator" else "house",
                                            found[2],
                                            party.removesuffix(" Party").lower(),
                                            cased(candidate["NM"]),
                                            counties_of[town],
                                        )
                                    ] += int(tally["V"])
            rows = [(*key, votes) for key, votes in totals.items()]
        if state == "OK":
            for race in json.loads(path.read_text())["races"]:
                found = OK_RACE.fullmatch(race["raceTitle"])
                if found is None:
                    continue
                for county in json.loads(
                    raw.cached(SOURCE, url("ok", f"{day:%Y%m%d}/{race['raceID']}")).path.read_text()
                )["summaryCandResults"]:
                    rows.extend(
                        (
                            "senate" if found[1] == "SENATOR" else "house",
                            found[2],
                            "democratic" if race["raceTitle2"] == "Democrat" else race["raceTitle2"].lower(),
                            cased(candidate["candName"]),
                            f"40{2 * int(county['countyID']) - 1:03}",
                            result["totalVotes"],
                        )
                        for candidate, result in zip(race["raceCandidates"], county["candResults"], strict=True)
                    )
        if state in CIVERA:
            totals = defaultdict(int)
            for row in pl.read_csv(path, infer_schema=False).iter_rows(named=True):
                party = (row["primary_party"] or "").lower()
                if (
                    party not in PARTIES
                    or row["division_type"] not in ("County", "City/Town")
                    or re.fullmatch(
                        r"(?i)blanks?|spoiled|void|scattering|write-?ins?|(over|under) ?votes?", row["candidate_name"]
                    )
                ):
                    continue
                totals[
                    (
                        CIVERA[state][1][int(row["office_id"])],
                        re.sub(r"(\D)(\d+)$", r"\1-\2", re.sub(r"[^a-z0-9]", "", row["district_name"].lower())),
                        party,
                        row["candidate_name"],
                        row["division_name"]
                        if state == "NY"
                        else town_counties("VT")[plain(re.sub(elstats.SUFFIXES, "", row["division_name"].lower()))],
                    )
                ] += int(row["votes"] or 0)
            rows = [(*key, votes) for key, votes in totals.items()]
        if state == "HI":
            for report in (address, *SIDECARS[state]):
                text = subprocess.run(
                    ["pdftotext", "-layout", str(raw.cached(SOURCE, report).path), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                county = re.search(r"County of (\w+)", text)[1]
                for start, end in ((0, 62), (62, 121), (121, 400)):
                    found = None
                    for line in text.splitlines():
                        segment = re.sub(r"^[\d.]*%\s+", "", line[start:end].strip())
                        heading = re.fullmatch(r"State (Senator|Representative), Dist (\d+) - ([A-Z])", segment)
                        if heading is not None:
                            found = heading if heading[3] in "DR" else None
                            continue
                        if found is None:
                            continue
                        candidate = re.fullmatch(r"(.+?)\s{2,}([\d,]+)\s+[\d.]*%?", segment)
                        if candidate is not None and not candidate[1].startswith(("Blank", "Over")):
                            surname, _, given = candidate[1].partition(",")
                            given, _, suffix = given.strip().partition(", ")
                            suffix, _, nickname = suffix.partition(" (")
                            rows.append(
                                (
                                    "senate" if found[1] == "Senator" else "house",
                                    found[2],
                                    "democratic" if found[3] == "D" else "republican",
                                    " ".join(
                                        filter(
                                            None,
                                            (
                                                given,
                                                nickname and f"({nickname}",
                                                re.sub(
                                                    r"(?<=\w\w)'([A-Z])",
                                                    lambda match: f"'{match[1].lower()}",
                                                    cased(surname.strip()),
                                                ),
                                                suffix,
                                            ),
                                        )
                                    ),
                                    county,
                                    int(candidate[2].replace(",", "")),
                                )
                            )
                        elif segment and not segment.startswith(("Blank", "Over", "Number")):
                            found = None
        if state == "ME":
            for name in DELIVERED_FILES[state]:
                ranked = re.fullmatch(r"(Senate|House) District (\d+) - Republican\.xlsx", name)
                if ranked is not None:
                    grid = pl.read_excel(
                        raw.cached(SOURCE, f"{DELIVERED[state][1]}#{name}").path,
                        has_header=False,
                        infer_schema_length=0,
                    ).rows()
                    where = grid[3].index("CTY")
                    rows.extend(
                        (
                            ranked[1].lower(),
                            ranked[2],
                            "republican",
                            re.sub(
                                r"\b(Ii|Iii|Iv)$",
                                lambda match: match[1].upper(),
                                cased(
                                    re.sub(
                                        r"^(.+?), (.+?),?( JR\.?| SR\.?| II| III| IV)?$",
                                        lambda match: f"{match[2]} {match[1]}{match[3] or ''}",
                                        grid[0][column].strip(),
                                    )
                                ),
                            ),
                            f"23{2 * ME_COUNTIES.index(line[where].upper()) + 1:03}",
                            int(line[column] or 0),
                        )
                        for line in grid[4:]
                        if line[where]
                        for column in range(where + 3, len(line))
                        if grid[0][column] and grid[0][column] != "BLANK"
                    )
                    continue
                names: list[str] = []
                for line in pl.read_excel(
                    raw.cached(SOURCE, f"{DELIVERED[state][1]}#{name}").path, has_header=False, infer_schema_length=0
                ).rows():
                    if (line[0] or "").strip().upper() == "DIS":
                        names = [cell for cell in line[3:] if cell and cell not in ("BLANK", "TBC")]
                        continue
                    if not (line[0] or "").isdigit() or not line[1]:
                        continue
                    for column, candidate in enumerate(names, 3):
                        rows.append(
                            (
                                "senate" if name.startswith("State Senate") else "house",
                                line[0],
                                "democratic" if " DEM " in name else "republican",
                                re.sub(
                                    r"\b(Ii|Iii|Iv)$",
                                    lambda match: match[1].upper(),
                                    cased(
                                        re.sub(
                                            r"^(.+?), (.+?),?( JR\.?| SR\.?| II| III| IV)?$",
                                            lambda match: f"{match[2]} {match[1]}{match[3] or ''}",
                                            candidate.strip(),
                                        )
                                    ),
                                ),
                                f"23{2 * ME_COUNTIES.index(line[1].upper()) + 1:03}",
                                int(line[column] or 0),
                            )
                        )
        if state == "MT":
            for sheet, grid in pl.read_excel(path, sheet_id=0, has_header=False, infer_schema_length=0).items():
                lines = grid.rows()
                found = re.search(r"(SENATOR|REPRESENTATIVE) DISTRICT (\d+)", lines[4][0] or "")
                if found is None or sheet[:3] not in ("REP", "DEM"):
                    continue
                for line in lines[5:]:
                    if not line[1] or line[1].strip().upper() == "TOTALS":
                        continue
                    rows.extend(
                        (
                            "senate" if found[1] == "SENATOR" else "house",
                            str(int(found[2])),
                            "republican" if sheet.startswith("REP") else "democratic",
                            cased(lines[4][column].split("\n")[0].strip()),
                            line[1].replace("&", "and"),
                            int(line[column] or 0),
                        )
                        for column in range(2, len(line))
                        if lines[4][column]
                    )
        if state == "NH":
            found_rows = []
            for name in DELIVERED_FILES[state]:
                party = "d" if name.endswith(("democratic.xlsx", "democratic_0.xlsx")) else "r"
                county = re.match(r"2026-sp-house-([a-z]+)-", name)
                district = names = None
                for line in pl.read_excel(
                    raw.cached(SOURCE, f"{DELIVERED[state][1]}#{name}").path,
                    has_header=False,
                    infer_schema_length=0,
                ).rows():
                    first = (line[0] or "").strip()
                    heading = re.match(r"State Senate District (\d+)|District No\. (\d+) \(", first)
                    if heading is not None:
                        district = heading[1] or f"{county[1]}-{heading[2]}"
                        names = [cell.strip() if cell else None for cell in line[1:]] if county is not None else None
                        continue
                    if first.upper().startswith("SEPTEMBER") and county is None:
                        names = [cell.strip() if cell else None for cell in line[1:]]
                        continue
                    if not first or names is None or first.upper().startswith(("TOTALS", "STATE OF")):
                        continue
                    town = re.sub(r"\s+ward\s+\d+$", "", first.lower())
                    votes = [int((cell or "").strip() or 0) for cell in line[1 : len(names) + 1]]
                    place = next(
                        (
                            key
                            for key in (plain(town), plain(re.sub(elstats.SUFFIXES, "", town)))
                            if key in town_counties("NH")
                        ),
                        None,
                    )
                    if county is None and place is None:
                        if any(votes):
                            raise ValueError(f"unknown New Hampshire town {first}")
                        continue
                    for column, (candidate, value) in enumerate(zip(names, votes)):
                        if not candidate or candidate in ("Recount", "Write-Ins"):
                            continue
                        if column + 1 < len(names) and names[column + 1] == "Recount":
                            value = votes[column + 1]
                        ballot, comma, letter = re.sub(r"\s{2,}WRITE-IN$", "", candidate).rpartition(", ")
                        found_rows.append(
                            (
                                "senate" if county is None else "house",
                                district,
                                "democratic" if party == "d" else "republican",
                                (ballot if comma else letter).replace(", ", " ")
                                + (
                                    ""
                                    if comma and letter.strip().lower() == party and "WRITE-IN" not in candidate
                                    else " (write-in)"
                                ),
                                county[1].title() if county is not None else town_counties("NH")[place],
                                value,
                            )
                        )
            cast = defaultdict(int)
            for chamber, district, party, candidate, _, votes in found_rows:
                cast[chamber, district, party, candidate] += votes
            rows.extend(row for row in found_rows if cast[row[:4]] or not row[3].endswith("(write-in)"))
        if state == "MD":
            for page in MD_PAGES:
                for link in re.findall(r'href="(gen_detail_results_[^"]+)"', raw.cached(SOURCE, page).path.read_text()):
                    text = raw.cached(SOURCE, MD_URL.format(link.replace(" ", "%20"))).path.read_text(encoding="utf-8")
                    for head, body in re.findall(r"<thead>(.*?)</thead>\s*<tbody>(.*?)</tbody>", text, re.DOTALL):
                        names = [
                            re.sub(r"<img[^>]*>", "", re.split(r"<br\s*/?>", header)[0]).strip()
                            for header in re.findall(r'<th scope="col">(.*?)</th>', head, re.DOTALL)
                        ]
                        for county_row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL):
                            cells = [
                                re.sub(r"<[^>]*>", "", cell).strip()
                                for cell in re.findall(r"<td[^>]*>(.*?)</td>", county_row, re.DOTALL)
                            ]
                            if not cells or not cells[0] or cells[0].startswith("Total"):
                                continue
                            rows.extend(
                                (
                                    "senate" if "_2026_5_" in link else "house-of-delegates",
                                    re.sub(
                                        r"(\d)([a-z])",
                                        r"\1-\2",
                                        re.search(r"<h3>District (\w+)", text)[1].lower(),
                                    ),
                                    re.search(r"_(Democratic|Republican)_", link)[1].lower(),
                                    name,
                                    {"Baltimore City": "24510", "Baltimore County": "24005"}.get(cells[0], cells[0]),
                                    int(votes.replace(",", "")),
                                )
                                for name, votes in zip(names, cells[1:], strict=True)
                            )
        if state == "KS":
            rows = [
                (
                    "house",
                    race.removeprefix("Kansas House of Representatives "),
                    party.lower(),
                    " ".join(reversed(candidate.split(", ", 1))),
                    county,
                    votes,
                )
                for race, party, candidate, county, votes in pl.read_excel(path)
                .group_by("Race", "Party", "Candidate", "County")
                .agg(pl.col("Votes").sum())
                .iter_rows()
            ]
        if state == "IL":
            totals = defaultdict(int)
            for link in IL_FILES:
                try:
                    handle = raw.cached(SOURCE, link).path.open(newline="", encoding="utf-8-sig")
                except LookupError:
                    continue
                with handle:
                    for row in csv.DictReader(handle):
                        found = re.fullmatch(r"(\d+)\w\w (SENATE|REPRESENTATIVE)", row["ContestName"].strip())
                        party = row["PartyName"].upper().removesuffix(" PARTY")
                        if (
                            found is None
                            or party not in ("DEMOCRATIC", "REPUBLICAN")
                            or row["EISCandidateID"] == "0"
                            or re.fullmatch(r"(?i)write-?ins?", row["CandidateName"].strip())
                        ):
                            continue
                        totals[
                            (
                                "senate" if found[2] == "SENATE" else "house",
                                found[1],
                                party.lower(),
                                re.sub(
                                    r"\b(?!(?:II|III|IV)\b|\w{1,2}\")([A-Z][a-z]{0,2})([A-Z])([A-Z']*)\b",
                                    lambda match: (
                                        match[1] + (match[2] if match[1][1:] else match[2].lower()) + match[3].lower()
                                    ),
                                    row["CandidateName"].strip(),
                                )
                                if re.search(r"[A-Z]{3}", re.sub(r'"[^"]*"', "", row["CandidateName"]))
                                else row["CandidateName"].strip(),
                                IL_CITIES.get(row["JurisName"].upper(), row["JurisName"]).upper(),
                            )
                        ] += int(row["VoteCount"] or 0)
            rows = [(*key, votes) for key, votes in totals.items()]
        if state == "MA":
            for listing in (address, *SIDECARS[state]):
                for election, _, district, stage in elstats.ma_primary_rows(
                    raw.cached(SOURCE, listing).path.read_text()
                ):
                    found = re.fullmatch(
                        r"(?:(\d+)\w\w )?(.+)",
                        re.sub(
                            r"^(First|Second|Third|Fourth|Fifth) ",
                            lambda match: f"{['First', 'Second', 'Third', 'Fourth', 'Fifth'].index(match[1]) + 1}st ",
                            district,
                        ),
                    )
                    table = csv.reader(
                        raw.cached(
                            SOURCE, f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:0/"
                        ).path.open(newline="")
                    )
                    header = next(table)
                    next(table)
                    for line in table:
                        town = re.sub(
                            r"^([NSEW])\. ",
                            lambda match: {"N": "North ", "S": "South ", "E": "East ", "W": "West "}[match[1]],
                            line[0],
                        )
                        if not town or town == "TOTALS":
                            continue
                        rows.extend(
                            (
                                "house" if listing == address else "senate",
                                re.sub(r"[^a-z]|\band\b", "", found[2].lower()) + (f"-{found[1]}" if found[1] else ""),
                                stage.removesuffix(" Primary").lower(),
                                re.sub(r"\bMc([a-z])", lambda match: f"Mc{match[1].upper()}", name),
                                town_counties("MA")[plain(re.sub(elstats.SUFFIXES, "", town.lower()))],
                                int(votes.replace(",", "") or 0),
                            )
                            for name, votes in zip(header[3:], line[3:])
                            if name and name not in ("No Nomination", "All Others", "Blanks", "Total Votes Cast")
                        )
        if state == "MN":
            totals: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
            for line in path.read_text(encoding="latin-1").splitlines():
                cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
                found = (
                    re.fullmatch(r"State (Senator|Representative) District (\d+)([AB]?)", cells[4])
                    if len(cells) > 13
                    else None
                )
                if found is None or cells[10] not in ("DFL", "R"):
                    continue
                totals[
                    (
                        "senate" if found[1] == "Senator" else "house",
                        "-".join(filter(None, (found[2], found[3].lower()))),
                        "dfl" if cells[10] == "DFL" else "republican",
                        re.sub(r"\s+", " ", cells[7]),
                        f"27{2 * int(cells[1]) - 1:03}",
                    )
                ] += int(cells[13] or 0)
            rows = [(*key, votes) for key, votes in totals.items()]
        if state in ITEMS:
            for item in json.loads(path.read_bytes())["data"]:
                found = ITEMS[state].fullmatch(item["name"][0]["text"])
                if found is None:
                    continue
                try:
                    breakdown = json.loads(raw.cached(SOURCE, f"{address}/{item['id']}").path.read_bytes())[
                        "breakdownResults"
                    ]
                except LookupError:
                    if item["reportingStatus"]["totalUnits"] != 1 or item["crossCounties"]:
                        raise
                    breakdown = [{"locality": {"name": [{"text": ""}]}, **item["summaryResults"]}]
                for county in breakdown:
                    locality = county["locality"]["name"][0]["text"]
                    if state == "RI":
                        town = plain(re.sub(elstats.SUFFIXES, "", locality.lower()))
                        if town not in town_counties("RI"):
                            continue
                        locality = town_counties("RI")[town]
                    rows.extend(
                        (
                            "senate" if "Senat" in (found.groupdict().get("chamber") or found["body"]) else "house",
                            found["district"]
                            + (f":{'AB'.index(found['seat']) + 1}" if found.groupdict().get("seat") else ""),
                            "republican" if found["party"].upper().startswith("REP") else "democratic",
                            re.sub(
                                r'"(\w{1,3})"',
                                lambda match: f'"{match[1].upper()}"',
                                re.sub(r"\bMc(\w)", lambda match: f"Mc{match[1].upper()}", text.title()),
                            )
                            if text.isupper()
                            else text,
                            locality.removesuffix(" County"),
                            option["voteCount"],
                        )
                        for option in county["ballotOptions"]
                        if not option["isWriteIn"]
                        for text in [re.sub(r"\s*(\(I\)|\*)$", "", option["name"][0]["text"]).strip()]
                    )
        if state == "NV":
            for county in ElementTree.fromstring(path.read_text()).iter("County"):
                for race in county.iter("Race"):
                    found = re.fullmatch(r"State (Senate|Assembly), District\s+(\d+) \((\w+)\)", race.get("RaceTitle"))
                    office = re.fullmatch(r"(.+) \((Democratic|Republican)\)", race.get("RaceTitle"))
                    if found is None and (office is None or office[1] not in STATEWIDE):
                        continue
                    for candidate in race.iter("Candidate"):
                        last, _, first = candidate.get("CandidateName").partition(", ")
                        rows.append(
                            (
                                found[1].lower() if found else STATEWIDE[office[1]][0],
                                found[2] if found else "",
                                (found or office)[3 if found else 2].lower(),
                                f"{first} {last}".strip(),
                                county.get("Name"),
                                int(candidate.get("Votes")),
                            )
                        )
        if state == "WI":
            for sheet in pl.read_excel(path, sheet_id=0, has_header=False, infer_schema_length=0).values():
                if sheet.height < 5 or sheet.width < 4:
                    continue
                found = re.fullmatch(
                    r"(STATE SENATOR|REPRESENTATIVE TO THE ASSEMBLY) DISTRICT (\d+) - (Republican|Democratic)",
                    sheet.row(2)[0] or "",
                )
                if found is None:
                    continue
                names = sheet.row(4)
                for line in sheet.rows()[5:]:
                    if line[0] is None or line[0].startswith("Office Totals"):
                        break
                    rows.extend(
                        (
                            "senate" if found[1] == "STATE SENATOR" else "assembly",
                            found[2],
                            found[3].lower(),
                            names[column],
                            line[0].strip().title(),
                            int(line[column] or 0),
                        )
                        for column in range(3, sheet.width)
                        if names[column] and names[column] != "SCATTERING"
                    )
        if state == "IN":
            for file in ("OffCatC_1018_B.json", "OffCatC_1040_B.json"):
                category = json.loads(raw.cached(SOURCE, IN_URL.format(file)).path.read_bytes())["Root"][
                    "OfficeCategory"
                ]
                for region in category["Regions"]["Region"]:
                    races = region["Races"]["Race"]
                    for race in races if isinstance(races, list) else [races]:
                        found = re.fullmatch(r"State (Senator|Representative), District (\d+)", race["OFFICE_TITLE"])
                        candidates = race["Candidates"]["Candidate"]
                        rows.extend(
                            (
                                "senate" if found[1] == "Senator" else "house",
                                str(int(found[2])),
                                {"R": "republican", "D": "democratic"}[candidate["PARTY_ABBREV"]],
                                candidate["NAME_ON_BALLOT"],
                                race["Jurisdiction"]["JURISDICTION_NAME"],
                                int(candidate["TOTAL_VOTES"]),
                            )
                            for candidate in (candidates if isinstance(candidates, list) else [candidates])
                            if candidate["PARTY_ABBREV"] in ("R", "D")
                        )
        if state == "IA":
            with zipfile.ZipFile(path) as archive:
                book = archive.read("detail.xlsx")
            for sheet in pl.read_excel(book, sheet_id=0, has_header=False, infer_schema_length=0).values():
                found = re.fullmatch(
                    r"State (Senator|Representative) District (\d+) - (Rep|Dem)\. \(\s*\d+\)", sheet.row(0)[0] or ""
                )
                if found is None:
                    continue
                names, headers = sheet.row(1), sheet.row(2)
                columns = [
                    (names[start], next(c for c in range(start, sheet.width) if headers[c] == "Total Votes"))
                    for start in range(sheet.width)
                    if names[start] and names[start] != "Write-in"
                ]
                for line in sheet.rows()[3:]:
                    if line[0] is None or line[0].startswith("Total"):
                        break
                    rows.extend(
                        (
                            "senate" if found[1] == "Senator" else "house",
                            found[2],
                            {"Rep": "republican", "Dem": "democratic"}[found[3]],
                            name,
                            line[0],
                            int(line[column] or 0),
                        )
                        for name, column in columns
                    )
        if state == "AL":
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    sheet = pl.read_excel(archive.read(member), infer_schema_length=0, engine="calamine")
                    for line in sheet.rows():
                        found = re.fullmatch(
                            r"STATE (SENATOR|REPRESENTATIVE), DISTRICT (\d+) \((REP|DEM)\)", (line[0] or "").strip()
                        )
                        candidate = (line[2] or "").strip()
                        if found is None or re.fullmatch(r"(?i)(over|under) votes|write-in", candidate):
                            continue
                        rows.append(
                            (
                                "senate" if found[1] == "SENATOR" else "house",
                                found[2],
                                {"REP": "republican", "DEM": "democratic"}[found[3]],
                                candidate,
                                member.rsplit("-", 1)[1].removesuffix(".xls"),
                                sum(int(value or 0) for value in line[3:]),
                            )
                        )
        if state == "NC":
            totals = defaultdict(int)
            with zipfile.ZipFile(path) as archive:
                text = archive.read("results_pct_20260303.txt").decode("latin-1")
            for line in text.splitlines()[1:]:
                cells = line.split("\t")
                found = re.fullmatch(
                    r"NC (HOUSE OF REPRESENTATIVES|STATE SENATE) DISTRICT (\d+) \((REP|DEM)\)", cells[5]
                )
                if found is None or cells[6].lower().startswith("write-in"):
                    continue
                totals[
                    (
                        "senate" if found[1] == "STATE SENATE" else "house",
                        str(int(found[2])),
                        {"REP": "republican", "DEM": "democratic"}[found[3]],
                        cells[6],
                        cells[0],
                    )
                ] += int(cells[13])
            rows = [(*key, votes) for key, votes in totals.items()]
        if state in ("SC", "WV"):
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            for contest in root.iter("Contest"):
                found = re.fullmatch(
                    r"State (Senate|House of Representatives), District (\d+) - (REP|DEM)"
                    if state == "SC"
                    else r"(STATE SENATOR|HOUSE OF DELEGATES), (\d+)\w\w (?:Senatorial )?District - (REP|DEM)",
                    re.sub(r"\s+", " ", contest.get("text")),
                )
                if found is None:
                    continue
                for choice in contest.iter("Choice"):
                    if "*Decertified" in choice.get("text"):
                        continue
                    totals: dict[str, int] = defaultdict(int)
                    for vote_type in choice.iter("VoteType"):
                        for county in vote_type.iter("County"):
                            totals[county.get("name")] += int(county.get("votes"))
                    rows.extend(
                        (
                            {
                                "Senate": "senate",
                                "STATE SENATOR": "senate",
                                "HOUSE OF DELEGATES": "house-of-delegates",
                            }.get(found[1], "house"),
                            found[2] + (":2" if found[1] == "STATE SENATOR" else ""),
                            {"REP": "republican", "DEM": "democratic"}[found[3]],
                            choice.get("text"),
                            county,
                            votes,
                        )
                        for county, votes in totals.items()
                    )
        frames.append(
            pl.DataFrame(
                rows,
                schema=[
                    ("chamber", pl.String),
                    ("district", pl.String),
                    ("party", pl.String),
                    ("candidate", pl.String),
                    ("county", pl.String),
                    ("votes", pl.Int64),
                ],
                orient="row",
            ).select(
                state=pl.lit(state),
                day=pl.lit(day),
                party="party",
                chamber="chamber",
                district="district",
                candidate=pl.col("candidate").str.replace(r"(?i)\s*\(write-in\)$", ""),
                write_in=pl.col("candidate").str.contains(r"(?i)\(write-in\)$"),
                county="county",
                votes="votes",
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    contest = ["state", "day", "party", "chamber", "district"]
    frame = pl.concat(frames).with_columns(
        pl.col("candidate").str.replace_all("`", "'"),
        spelling=pl.col("candidate").str.to_lowercase().str.replace_all("`", "'"),
    )
    frame.join(
        frame.group_by(*contest, "spelling").agg(
            canonical=pl.col("candidate")
            .sort_by(pl.col("candidate").str.count_matches("[A-Z]"), descending=True)
            .first()
        ),
        on=[*contest, "spelling"],
    ).group_by(*contest, pl.col("canonical").alias("candidate"), "write_in", "county", maintain_order=True).agg(
        pl.col("votes").sum()
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 state legislative primaries and primary runoffs by county: Alabama, Arkansas, Connecticut, Delaware (district totals), Florida, Georgia, Hawaii, Idaho, Illinois, Indiana, Iowa, Kansas, Maine (ranked-choice first choices), Maryland, Massachusetts, Michigan, Minnesota, Montana, Nevada (with its statewide offices), New Hampshire, New York, North Carolina, Oklahoma, Rhode Island, South Carolina, Utah, Vermont, West Virginia, and Wisconsin from the states' official results; Arizona, Colorado, Kentucky, Missouri, New Mexico, North Dakota, Oregon, Pennsylvania, South Dakota, Tennessee, Texas, and Wyoming from OpenElections",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        counts = {"contests": 0, "candidates": 0, "matched": 0, "skipped": 0, "elsewhere": 0}
        finalists: dict[str, dict[str, object]] = defaultdict(dict)
        kept = dict(
            conn.execute(
                "select cy.id, cy.person_id from candidacy cy join changeset ch on ch.id = cy.changeset_id where ch.pipeline = %s",
                [SOURCE],
            ).fetchall()
        )
        elsewhere = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join contest co on co.id = x.entity_id
                   join changeset c on c.id = co.changeset_id
                   join entity e on e.id = co.id and e.retired_by is null
                   where x.scheme = 'us.contest' and c.pipeline = 'us.nyc_general' and x.value like '2026-%%'"""
            ).fetchall()
        }
        for state, day, publisher, title, page, address in ROUNDS:
            first = min(when for other, when, *_ in ROUNDS if other == state)
            runoff = day != first
            system = cs.key("us.electoral_system", "fptp" if state == "SD" else SYSTEMS.get(state, "fptp"))
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=page,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, address), source)
            if state == "IN":
                cs.artifact(raw.cached(SOURCE, IN_URL.format("OffCatC_1018_B.json")), source)
            for other in SIDECARS.get(state, []) + (
                [AR_CONTESTS.format(address.split("/")[-2])] if state == "AR" else []
            ):
                cs.artifact(raw.cached(SOURCE, other), source)
            if state == "OK":
                for race in json.loads(raw.cached(SOURCE, address).path.read_bytes())["races"]:
                    if OK_RACE.fullmatch(race["raceTitle"]):
                        cs.artifact(raw.cached(SOURCE, url("ok", f"{day:%Y%m%d}/{race['raceID']}")), source)
            if state == "MD":
                for page in MD_PAGES:
                    if page != address:
                        cs.artifact(raw.cached(SOURCE, page), source)
                    for link in re.findall(
                        r'href="(gen_detail_results_[^"]+)"', raw.cached(SOURCE, page).path.read_text()
                    ):
                        cs.artifact(raw.cached(SOURCE, MD_URL.format(link.replace(" ", "%20"))), source)
            if state == "IL":
                for link in IL_FILES:
                    if link != address:
                        try:
                            cs.artifact(raw.cached(SOURCE, link), source)
                        except LookupError:
                            pass
            if state == "MA":
                for listing in (address, *SIDECARS[state]):
                    for election, *_ in elstats.ma_primary_rows(raw.cached(SOURCE, listing).path.read_text()):
                        cs.artifact(
                            raw.cached(SOURCE, f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:0/"),
                            source,
                        )
            election = cs.key("us.election", f"{day}:{state}:{'runoff' if runoff else 'primary'}")
            if election in cs.minted:
                if not runoff:
                    raise ValueError(f"the {day} {state} primary election must exist")
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind="runoff",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"2026 {STATE_NAMES[state]} primary runoff election")
                cs.add("slug", path=f"us/{state.lower()}/{day}-primary-runoff", entity_id=election)
            templates = {
                value: row
                for value, *row in conn.execute(
                    """select x.value, co.kind, co.office_id, co.body_id, co.district_area_id, co.magnitude, co.term, n.text
                       from contest co join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                       left join name n on n.entity_id = co.body_id and n.is_preferred
                       where x.value ~ %s""",
                    [
                        f"^2026-11-03:{state}:((house|senate|assembly|house-of-delegates):[0-9a-z-]+(:[0-9])?:g|[a-z-]+)$"
                    ],
                ).fetchall()
            }
            counties = {}
            for unit, code, text in conn.execute(
                """select u.id, u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like %s""",
                [f"{FIPS[state]}___"],
            ).fetchall():
                counties[code] = counties[re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st "))] = unit
            if state == "NM":
                counties["donaana"] = counties["35013"]
            if state == "MO":
                counties["kansascity"] = conn.execute(
                    "select id from reporting_unit where scheme_code = 'us.place' and code = '2938000'"
                ).fetchone()[0]
                counties["stlouiscity"], counties["stlouis"] = counties["29510"], counties["29189"]
            pool: dict[tuple[str, str], set[object]] = defaultdict(set)
            for person, text in conn.execute(
                """select distinct cy.person_id, n.text from candidacy cy
                   join contest_option o on o.id = cy.option_id
                   join contest co on co.id = o.contest_id
                   join name b on b.entity_id = co.body_id and b.is_preferred and b.text !~ 'United States'
                   join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where x.value ~ %s""",
                [
                    f"^20[0-2][0-9]-[0-9-]+:{state}:(house|senate|assembly|house-of-delegates|{'|'.join(key for key, _ in STATEWIDE.values())})(:|$)"
                ],
            ).fetchall():
                words = [
                    w for w in re.sub(r"[^a-z ]", "", text.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")
                ]
                if len(words) > 1:
                    pool[(words[0], words[-1])].add(person)
            grouped = (
                frame.filter((pl.col("state") == state) & (pl.col("day") == day))
                .group_by("party", "chamber", "district", "candidate", "write_in")
                .agg(pl.col("county"), pl.col("votes"))
            )
            for (party, chamber, district), contest_rows in sorted(
                grouped.group_by("party", "chamber", "district"), key=lambda entry: entry[0]
            ):
                scope, party_key, label = PARTIES[party]
                template = templates.get(f"2026-11-03:{state}:{chamber}" + (f":{district}:g" if district else ""))
                if template is None:
                    print("no 2026 general contest for", state, chamber, district)
                    counts["skipped"] += 1
                    continue
                if f"{day}:{state}:{chamber}:{district}:{party}" in elsewhere:
                    counts["elsewhere"] += 1
                    continue
                kind, office, body, area, magnitude, term, chamber_name = template
                contest_key = (
                    f"{day}:{state}:{chamber}:{district}:{scope}" if district else f"{day}:{state}:{chamber}:{party}"
                )
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind=kind,
                    office_id=office,
                    body_id=body,
                    district_area_id=area,
                    magnitude=magnitude,
                    term=term,
                    round_number=2 if runoff else 1,
                    previous_round_id=cs.key("us.contest", contest_key.replace(str(day), str(first), 1))
                    if runoff
                    else None,
                    electoral_system_id=system,
                    default_result_kind="counted",
                    party_scope_id=cs.key("us.party", party_key),
                )
                stage = "primary runoff" if runoff else "primary"
                if district:
                    name(contest, f"2026 {chamber_name} {label} {stage}, District {district.replace(':', ', Seat ')}")
                else:
                    name(
                        contest,
                        f"2026 {next(title for key, title in STATEWIDE.values() if key == chamber)} {label} {stage}",
                    )
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-{stage.replace(' ', '-')}/"
                    + (f"state-{chamber}-{district.replace(':', '-')}-{party}" if district else f"{chamber}-{party}"),
                    entity_id=contest,
                )
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=(
                        f"Results by county from OpenElections' 2026 primary file for {OPENELECTIONS[state][0]}, a community compilation of the state's county results."
                        if state in OPENELECTIONS
                        else f"District totals from the {publisher}'s official results."
                        if state == "DE"
                        else f"Results by county from the {publisher}'s election results archive"
                        + (", town results summed to counties." if state == "VT" else ".")
                        if state in CIVERA
                        else f"Results by county from the {publisher}'s official results"
                        + (", town results summed to counties." if state in ("CT", "MA") else ".")
                        + (
                            " Maine's primaries are ranked-choice: these are first choices, municipal results summed to"
                            " counties, without the military and overseas ballots that Maine reports only by district. In Senate District 4"
                            " and House District 58 (Republican), the ranked-choice tabulation elected the first-round"
                            " leader."
                            if state == "ME"
                            else ""
                        )
                    )
                    + (
                        " Declared write-in candidates are listed by name; the candidate with the most votes is shown as the nominee."
                        if state == "MI"
                        else ""
                    ),
                )
                totals = []
                if "" in contest_rows["county"].explode().to_list():
                    held = defaultdict(set)
                    for scheme, unit in conn.execute(
                        """select distinct u.scheme_code, u.id from external_id x
                           join result_set rs on rs.contest_id = x.entity_id and rs.status = 'active'
                           join result_value v on v.result_set_k = rs.k
                           join reporting_unit u on u.k = v.unit_k and u.scheme_code in ('us.county', 'us.sldl', 'us.sldu')
                           where x.scheme = 'us.contest' and x.value ~ %s""",
                        [f"^20(22|24)-11-[0-9]+:{state}:{chamber}:{district}:g$"],
                    ).fetchall():
                        held[scheme].add(unit)
                    (counties[""],) = (
                        held["us.county"]
                        if len(held["us.county"]) == 1
                        else held["us.sldu" if chamber == "senate" else "us.sldl"]
                    )
                for candidate, write_in, by_county, votes in contest_rows.select(
                    "candidate", "write_in", "county", "votes"
                ).iter_rows():
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    words = [
                        w
                        for w in re.sub(r"[^a-z ]", "", candidate.lower()).split()
                        if w not in ("jr", "sr", "ii", "iii", "iv")
                    ]
                    candidacy = cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}")
                    earlier = kept.get(candidacy) or (
                        finalists[contest_key.split(":", 1)[1]].get(words[-1]) if runoff else None
                    )
                    found = {earlier} if earlier is not None else pool.get((words[0], words[-1]), set())
                    if len(found) == 1:
                        person = next(iter(found))
                        counts["matched"] += 1
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, candidate)
                            pool[(words[0], words[-1])].add(person)
                    if not runoff:
                        finalists[contest_key.split(":", 1)[1]][words[-1]] = person
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party_key),
                        ballot_label=f"{candidate} (write-in)" if write_in else candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                    for county, value in zip(by_county, votes, strict=True):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=counties.get(county)
                            or counties[re.sub(r"[^a-z]", "", county.lower().replace("saint ", "st "))],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
                    totals.append((sum(votes), option))
                    counts["candidates"] += 1
                totals.sort(key=lambda entry: -entry[0])
                everyone = sum(votes for votes, _ in totals)
                if (
                    not runoff
                    and state in MAJORITY
                    and state != "SD"
                    and len(totals) > 1
                    and totals[0][0] * 100 <= MAJORITY[state] * everyone
                ):
                    if len(totals) == 2 or totals[1][0] > totals[2][0]:
                        for _, option in totals[:2]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
                elif magnitude > 1:
                    seated = [entry for entry in totals[:magnitude] if entry[0] > 0]
                    if len(totals) <= magnitude or totals[magnitude - 1][0] > totals[magnitude][0]:
                        for _, option in seated:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=None)
                elif totals[0][0] > 0 and (len(totals) == 1 or totals[0][0] > totals[1][0]):
                    cs.add("outcome", result_set_id=result_set, option_id=totals[0][1], kind="won", seats=None)
                counts["contests"] += 1
        written = {str(row["id"]) for row in cs.rows.get("contest", [])}
        for entity, value in conn.execute(
            """select co.id, x.value from contest co join changeset c on c.id = co.changeset_id
               join entity e on e.id = co.id and e.retired_by is null
               join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
               where c.pipeline = %s""",
            [SOURCE],
        ).fetchall():
            if str(entity) not in written:
                print("retiring", value)
                cs.add(
                    "entity_retirement",
                    entity_id=entity,
                    reason="The primary is held by another pipeline's finer results for the same contest",
                )
        print(counts)
        return cs.write()
