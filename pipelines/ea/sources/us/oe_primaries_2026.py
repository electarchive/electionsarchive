import csv
import io
import json
import subprocess
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats
from ea.store import connect

SOURCE = "us.oe_primaries_2026"
VERSION = "1"
BASE = "https://raw.githubusercontent.com/openelections/openelections-data-{}/master/2026/{}"
OK_RACES = ("12001", "12010", "12011", "12012", "12013", "12014", "13001", "13010", "13012", "13013", "13014", "13015")
ME_FILES = {
    "US%20Senate%20DEM%20-%20FINAL": ("U.S. Senate", "", "Democratic"),
    "US%20Senate%20REP%20-%20FINAL": ("U.S. Senate", "", "Republican"),
    "Rep%20to%20Congress%20Dist%201%20FINAL": ("U.S. House", "1", "Democratic"),
    "Rep%20to%20Congress%20Dis%201%20REP%20-%20FINAL": ("U.S. House", "1", "Republican"),
    "Rep%20to%20Congress%20Dis%202%20REP%20-%20FINAL": ("U.S. House", "2", "Republican"),
    "Representative%20to%20Congress%20District%202%20-%20Democratic": ("U.S. House", "2", "Democratic"),
    "Governor%20-%20Democratic": ("Governor", "", "Democratic"),
    "Governor%20-%20Republican": ("Governor", "", "Republican"),
}
FILES = {
    "TX": ("tx", "2026-03-03", ["20260303__tx__primary__county.csv"]),
    "CA": ("ca", "2026-06-02", ["20260602__ca__primary__county.csv"]),
    "MI": ("mi", "2026-08-04", ["20260804__mi__primary__county.csv"]),
    "PA": ("pa", "2026-05-19", ["20260519__pa__primary__county.csv"]),
    "WV": ("wv", "2026-05-13", ["20260513__wv__primary__county.csv"]),
    "MS": ("ms", "2026-03-10", ["20260310__ms__democratic__primary__county.csv", "20260310__ms__republican__primary__county.csv"]),
    "SD": ("sd", "2026-06-02", ["20260602__sd__primary__county.csv"]),
    "WY": ("wy", "2026-08-18", ["20260818__wy__primary__county.csv"]),
    "OR": ("or", "2026-05-19", ["20260519__or__primary__county.csv"]),
    "KY": ("ky", "2026-05-19", ["20260519__ky__primary__county.csv"]),
    "CO": ("co", "2026-06-30", ["20260630__co__primary__county.csv"]),
    "AZ": ("az", "2026-07-21", ["20260721__az__primary__county.csv"]),
    "IL": (
        "il",
        "2026-03-17",
        [
            "69-150-UNITED%20STATES%20SENATOR-2026GP.csv",
            "69-180-GOVERNOR%20AND%20LIEUTENANT%20GOVERNOR-2026GP.csv",
            *(f"69-{240 + 10 * number}-{number}{'TH' if 10 <= number % 100 <= 20 else {1: 'ST', 2: 'ND', 3: 'RD'}.get(number % 10, 'TH')}%20CONGRESS-2026GP.csv" for number in range(1, 18)),
        ],
    ),
    "NM": ("nm", "2026-06-02", ["nm.elstats3"]),
    "NY": ("ny", "2026-06-23", ["ny.elstats3"]),
    "VT": ("vt", "2026-08-11", ["vt2.elstats"]),
    "VA": ("va", "2026-08-04", ["va2.elstats"]),
    "NC": ("nc", "2026-03-03", ["results_pct_20260303.zip"]),
    "TN": ("tn", "2026-08-01", ["20260801__tn__primary__county.csv"]),
    "MO": ("mo", "2026-08-04", ["20260804__mo__primary__county.csv"]),
    "AL": ("alx", "2026-05-19", ["2026-07/2026_Primary_Election.zip"]),
    "ND": ("nd", "2026-06-09", ["20260609__nd__primary__county.csv"]),
    "GA": ("ga", "2026-05-19", ["ballot-items"]),
    "WA": ("wa", "2026-08-04", ["ballot-items"]),
    "UT": ("ut", "2026-06-23", ["ballot-items"]),
    "ME": ("me", "2026-06-09", list(ME_FILES)),
    "NE": ("ne", "2026-05-12", ["2026_Primary_Canvass_Book.pdf"]),
    "LA": ("la", "2026-05-16", ["20260516/csv/ByParish_67538.csv", "20260516/csv/ByParish_67539.csv"]),
    "RI": ("ri", "2026-09-09", ["ballot-items"]),
    "CT": ("ct", "2026-08-11", ["111/27708/Lookupdata", "111/27708/townVotes_Electiondata", "112/28256/Lookupdata", "112/28256/townVotes_Electiondata"]),
    "AR": ("ar", "2026-03-03", ["contests", "data"]),
    "OK": ("ok", "2026-06-16", ["getelec/20260616", *(f"20260616/{race}" for race in OK_RACES)]),
    "HI": ("hi", "2026-08-08", ["cch", "coh", "cok", "com"]),
    "DE": ("de", "2026-09-15", ["PR2026.html"]),
    "MA": ("ma", "2026-09-01", ["3", "5", "6"]),
    "NJ": ("nj", "2026-06-02", ["us-senate", "us-house"]),
    "ID": ("id", "2026-05-19", ["ballot-items"]),
    "FL": ("fl", "2026-08-18", ["extract"]),
    "IN": ("in", "2026-05-05", ["OffCatC_1005_B.json"]),
    "KS": ("ks", "2026-08-04", ["United-States-Senate", "United-States-House-of-Representatives", "Governor"]),
    "MN": ("mn", "2026-08-11", ["USHouseCty.txt", "USSenateCty.txt", "GovernorPct.txt"]),
    "MD": (
        "md",
        "2026-06-23",
        [
            *(f"gen_detail_results_2026_1_1_{party}.html" for party in ("Democratic", "Republican")),
            *(f"gen_detail_results_2026_4_{number}_{party}_District%20{number}.html" for number in range(1, 9) for party in ("Democratic", "Republican")),
        ],
    ),
}
RUNOFFS = {
    "GA": ("ga2", "2026-06-16", ["ballot-items"], "2026-05-19"),
    "AL": ("alx", "2026-06-16", ["2026-07/2026_PRIMARY_RUNOFF_ELECTION.zip"], "2026-05-19"),
    "LA": ("la", "2026-06-27", ["20260627/csv/ByParish_70156.csv", "20260627/csv/ByParish_70157.csv"], "2026-05-16"),
    "OK": ("ok", "2026-08-25", ["getelec/20260825", "20260825/12001", "20260825/13010"], "2026-06-16"),
}
FIPS = {"TX": "48", "CA": "06", "MI": "26", "PA": "42", "WV": "54", "MS": "28", "SD": "46", "WY": "56", "OR": "41", "KY": "21", "CO": "08", "AZ": "04", "TN": "47", "NC": "37", "IL": "17", "NM": "35", "NY": "36", "VA": "51", "AL": "01", "ND": "38", "MD": "24", "MN": "27", "VT": "50", "KS": "20", "GA": "13", "WA": "53", "UT": "49", "IN": "18", "FL": "12", "ID": "16", "NJ": "34", "MO": "29", "MA": "25", "DE": "10", "HI": "15", "ME": "23", "OK": "40", "AR": "05", "CT": "09", "RI": "44", "LA": "22", "NE": "31"}
RUNOFF_SYSTEMS = {"GA": "two_round", "TX": "two_round", "MS": "two_round", "SD": "two_round_35", "NC": "two_round_30", "OK": "two_round", "AL": "two_round", "LA": "two_round"}
THRESHOLDS = {"GA": 50, "TX": 50, "MS": 50, "SD": 35, "NC": 30, "OK": 50, "AL": 50, "LA": 50}
ME_COUNTIES = {"AND": "23001", "ARO": "23003", "CUM": "23005", "FRA": "23007", "HAN": "23009", "KEN": "23011", "KNO": "23013", "LIN": "23015", "OXF": "23017", "PEN": "23019", "PIS": "23021", "SAG": "23023", "SOM": "23025", "WAL": "23027", "WAS": "23029", "YOR": "23031"}
RCV_WINNERS = {
    ("ME", "governor", "", "democratic"): ("hannah-m-pingree", "Hannah M. Pingree won the final round of the ranked-choice count, 111,750 votes to 86,950 for Nirav D. Shah, after three eliminations."),
    ("ME", "governor", "", "republican"): ("robert-b-charles", "Robert B. Charles won the final round of the ranked-choice count, 59,873 votes to 39,499 for Benjamin T. Midgley, after six eliminations."),
    ("ME", "house", "2", "democratic"): ("matthew-g-dunlap", "Matthew G. Dunlap won the final round of the ranked-choice count, 35,924 votes to 32,555 for Joseph M. Baldacci, after two eliminations."),
}
OFFICES = {"U.S. House": "house", "U.S. Senate": "senate", "Governor": "governor"}
TOP_TWO = {"CA", "WA"}
AT_LARGE = {"SD", "WY", "ND", "VT", "DE"}
REDISTRICTED = {"TX", "CA", "NC", "UT", "MO"}
PARTIES = {
    "REP": ("republican", "republican-party"),
    "R": ("republican", "republican-party"),
    "Republican": ("republican", "republican-party"),
    "DEM": ("democratic", "democratic-party"),
    "D": ("democratic", "democratic-party"),
    "Democratic": ("democratic", "democratic-party"),
    "LIB": ("libertarian", "libertarian-party"),
    "LBT": ("libertarian", "libertarian-party"),
    "Libertarian": ("libertarian", "libertarian-party"),
    "GRN": ("green", "green-party"),
    "Green": ("green", "green-party"),
    "Peace and Freedom": ("peace-and-freedom", "CA:peace-and-freedom-party"),
    "American Independent": ("american-independent", "american-independent-party"),
    "No Party Preference": ("", "CA:no-party-preference"),
    "NOL": ("no-labels", "no-labels-party"),
    "Prefers Democratic Party": ("democratic", "democratic-party"),
    "Prefers Republican Party": ("republican", "republican-party"),
    "Prefers Independent Party": ("", "WA:independent-party"),
    "Prefers Libertarian Party": ("libertarian", "libertarian-party"),
    "Prefers Green Party": ("green", "green-party"),
    "Prefers Constitution Party": ("constitution", "constitution-party"),
    "DFL": ("minnesota-democratic-farmer-labor", "democratic-party"),
}
ALIASES = {("GA", "athensclarke"): "clarke", ("MS", "jeffdavis"): "jeffersondavis", ("MD", "baltimorecounty"): "baltimore", ("MD", "saintmarys"): "stmarys"}
POOLED = re.compile(r"write-?ins?\b|^scattering$", re.I)
JUNK = re.compile(r"^void$|^(under|over) votes$|^\* withdrawn candidate$|^total|blank|registered voters", re.I)
ORDINALS = {1: "st", 2: "nd", 3: "rd"}
CAVEAT = "Held under the 2026 congressional map, which the archive has not yet loaded as a boundary set, so this contest is not yet tied to a district shape."
OFFICIAL = {
    "IL": ("Illinois State Board of Elections", "https://elections.il.gov/ElectionOperations/ElectionResults.aspx", "precinct files summed to counties"),
    "NC": ("North Carolina State Board of Elections", "https://www.ncsbe.gov/results-data", "precinct file summed to counties"),
    "NM": ("New Mexico Secretary of State", "https://electionresults.sos.nm.gov", "election results archive"),
    "NY": ("New York State Board of Elections", "https://results.elections.ny.gov", "election results archive"),
    "VA": ("Virginia Department of Elections", "https://historical.elections.virginia.gov", "election results archive"),
    "VT": ("Vermont Secretary of State", "https://electionarchive.vermont.gov", "election results archive, town rows summed to counties"),
    "MD": ("Maryland State Board of Elections", "https://elections.maryland.gov/elections/2026/primary_results/", "official county breakdown pages"),
    "MN": ("Minnesota Secretary of State", "https://electionresultsfiles.sos.mn.gov/20260811/", "county files and precinct file summed to counties"),
    "ME": ("Maine Secretary of State", "https://www.maine.gov/sos/cec/elec/results/index.html", "municipal and precinct files summed to counties; military and overseas ballots, reported only statewide, are not in the county figures"),
    "OK": ("Oklahoma State Election Board", "https://results.okelections.gov/OKER/", "county results from the board's results application"),
    "AR": ("Arkansas Secretary of State", "https://arkansas.tally-enr.com/", "county results from the state's election night reporting site"),
    "AL": ("Alabama Secretary of State", "https://www.sos.alabama.gov/alabama-votes/voter/election-data", "county files of precinct results summed to counties"),
    "CT": ("Connecticut Secretary of the State", "https://ctemspublic.pcctg.net/", "town results from the Election Center summed to counties"),
    "RI": ("Rhode Island Board of Elections", "https://elections.ri.gov/elections/previous-election-results", "town results summed to counties"),
    "LA": ("Louisiana Secretary of State", "https://voterportal.sos.la.gov/graphical", "parish files from the live election results site"),
    "NE": ("Nebraska Secretary of State", "https://sos.nebraska.gov/elections", "county tables in the primary canvass book"),
    "GA": ("Georgia Secretary of State", "https://results.sos.ga.gov", "county results from the state's election results site"),
    "ID": ("Idaho Secretary of State", "https://voteidaho.gov", "county results from the state's election results site"),
    "UT": ("Utah Lieutenant Governor's Office", "https://electionresults.utah.gov", "county results from the state's election results site"),
    "WA": ("Washington Secretary of State", "https://results.votewa.gov", "county results from the state's election results site"),
    "HI": ("Hawaii Office of Elections", "https://elections.hawaii.gov", "county results from the office's primary election reports"),
    "DE": ("Delaware Department of Elections", "https://elections.delaware.gov/results/", "county results from the department's election night reporting"),
    "MA": ("Massachusetts Secretary of the Commonwealth", "https://electionstats.state.ma.us", "county results from the state's election statistics archive"),
    "FL": ("Florida Division of Elections", "https://results.elections.myflorida.com", "county results extract of official results"),
    "IN": ("Indiana Secretary of State", "https://enr.indianavoters.in.gov", "county results from the state's election night reporting site"),
    "NJ": ("New Jersey Division of Elections", "https://www.nj.gov/state/elections/election-information-results.shtml", "county tables in the official primary results"),
    "KS": ("Kansas Secretary of State", "https://sos.ks.gov/elections/election-results.html", "precinct-level files summed to counties"),
}
METHOD = "Results by county from OpenElections' 2026 primary file for {state}, a community compilation of the state's county results. Candidates who received votes are listed; write-in votes are pooled."
OFFICIAL_METHOD = "Results by county from the {publisher} ({how}). Candidates who received votes are listed; write-in votes are pooled."


NC_URL = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2026_03_03/{}"
NC_CONTEST = re.compile(r"US (HOUSE OF REPRESENTATIVES DISTRICT (\d+)|SENATE) \((\w+)\)")


IL_URL = "https://elections.il.gov/Downloads/ElectionOperations/ElectionResults/ByOffice/69/{}"
IL_CONTEST = re.compile(r"(?:(\d+)(?:ST|ND|RD|TH) CONGRESS|(UNITED STATES SENATOR)|(GOVERNOR AND LIEUTENANT GOVERNOR))")
IL_CITIES = {
    "CITY OF BLOOMINGTON": "McLean",
    "CITY OF CHICAGO": "Cook",
    "CITY OF DANVILLE": "Vermilion",
    "CITY OF EAST ST. LOUIS": "St. Clair",
    "CITY OF GALESBURG": "Knox",
    "CITY OF ROCKFORD": "Winnebago",
}


ELSTATS = {"nm", "ny", "va", "vt"}
ELSTATS_OFFICES = {
    "United States Senator": "U.S. Senate",
    "United States Representative": "U.S. House",
    "Representative in Congress": "U.S. House",
    "U.S. Senate": "U.S. Senate",
    "U.S. House": "U.S. House",
    "Governor": "Governor",
}


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


TOWN_CACHE: dict[str, dict[str, str]] = {}
EV = {
    "ga": (
        "https://results.sos.ga.gov/results/public/api/elections/Georgia/GeneralPrimary51926/",
        re.compile(r"(?:(?P<senate>US Senate)|(?P<governor>Governor)|US House of Representatives - District (?P<district>\d+)) - (?P<party>Rep|Dem)"),
    ),
    "ga2": (
        "https://results.sos.ga.gov/results/public/api/elections/Georgia/06162026GeneralPrimaryRunoff/",
        re.compile(r"(?:(?P<senate>US Senate)|(?P<governor>Governor)|US House of Representatives - District (?P<district>\d+)) - (?P<party>Rep|Dem)"),
    ),
    "id": (
        "https://results.voteidaho.gov/results/public/api/elections/id/may2026/",
        re.compile(r"(?:(?P<senate>United States Senator)|(?P<governor>Governor)|United States Representative District (?P<district>\d+)) - (?P<party>Democratic|Republican|Libertarian)"),
    ),
    "ri": (
        "https://electionresults.ri.gov/results/public/api/elections/RhodeIsland/RI2026StatewidePrimary/",
        re.compile(r"(?P<party>REP|DEM) (?:(?P<senate>Senator in Congress)|(?P<governor>Governor)|Representative in Congress District (?P<district>\d+))"),
    ),
    "ut": (
        "https://electionresults.utah.gov/results/public/api/elections/utah/Primary06232026/",
        re.compile(r"(?P<party>REP|DEM) U\.S\. House District (?P<district>\d+)"),
    ),
    "wa": (
        "https://results.votewa.gov/results/public/api/elections/washington/20260804/",
        re.compile(r"U\.S\. Representative - Congressional District (?P<district>\d+)"),
    ),
}
FL_URL = "https://results.elections.myflorida.com/ResultsExtract.Asp?ElectionDate=8/18/2026&OfficialResults=Y"
AR_ELECTION = "7f77a178-af02-40ec-92db-c5cc50882c68"
CT_COUNTIES = {"Fairfield": "001", "Hartford": "003", "Litchfield": "005", "Middlesex": "007", "New Haven": "009", "New London": "011", "Tolland": "013", "Windham": "015"}
OK_BASE = "https://results.okelections.gov/OKERS/"
BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
MA_BASE = elstats.MASSACHUSETTS
MA_OFFICES = {"3": "Governor", "5": "U.S. House", "6": "U.S. Senate"}
NJ_URL = "https://www.nj.gov/state/elections/assets/pdf/election-results/2026/2026-official-primary-results-{}.pdf"
NJ_COUNTIES = ["ATLANTIC", "BERGEN", "BURLINGTON", "CAMDEN", "CAPE MAY", "CUMBERLAND", "ESSEX", "GLOUCESTER", "HUDSON", "HUNTERDON", "MERCER", "MIDDLESEX", "MONMOUTH", "MORRIS", "OCEAN", "PASSAIC", "SALEM", "SOMERSET", "SUSSEX", "UNION", "WARREN"]
NJ_ORDINALS = {word: number for number, word in enumerate(["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth", "Eleventh", "Twelfth"], 1)}
IN_URL = "https://enr.indianavoters.in.gov/archive/2026Primary/data/{}"
KS_URL = "https://sos.ks.gov/elections/26elec/2026-Primary-Election-{}-Precinct-Level-Results.xlsx"
MN_URL = "https://electionresultsfiles.sos.mn.gov/20260811/{}"
MN_OFFICES = {"U.S. Representative": "U.S. House", "U.S. Senator": "U.S. Senate", "Governor & Lt Governor": "Governor"}
MD_URL = "https://elections.maryland.gov/elections/2026/primary_results/{}"


def town_counties(state: str) -> dict[str, str]:
    if state not in TOWN_CACHE:
        found = TOWN_CACHE.setdefault(state, {})
        with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
            for line in io.TextIOWrapper(archive.open(archive.namelist()[0]), encoding="utf-8"):
                cells = line.rstrip("\n").split("\t")
                if cells[0] == state:
                    found.setdefault(plain(re.sub(elstats.SUFFIXES, "", cells[3].lower())), cells[1][:5])
        if state == "VT":
            found["essexjunction"] = "50007"
        for (former, name), fips in elstats.FORMER.items():
            if former == state:
                found[name] = fips
    return TOWN_CACHE[state]


def url(code: str, file: str) -> str:
    if code == "ne":
        return f"https://sos.nebraska.gov/sites/default/files/doc/elections/2026/{file}"
    if code == "la":
        return f"https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob={file}"
    if code == "ct":
        return f"https://ctemspublic.tgstg.net/ng-app/data/election/{file}.json"
    if code == "alx":
        return f"https://www.sos.alabama.gov/sites/default/files/election-data/{file}"
    if code == "ar":
        return f"https://enr-results-api.totalresults.com/Contest/GetContestSearchList?cid=arkansas&electionID={AR_ELECTION}" if file == "contests" else f"https://results.tally-enr.com/results/arkansas/{AR_ELECTION}/FullDataFile.json"
    if code == "ok":
        first, _, second = file.partition("/")
        return f"{OK_BASE}enrapi/getelec/{second}/SW/xx" if first == "getelec" else f"{OK_BASE}enrapi/GetCntyResults/{first}/{second}"
    if code == "me":
        return f"https://www.maine.gov/sos/sites/maine.gov.sos/files/inline-files/{file}.xlsx"
    if code == "hi":
        return f"https://elections.hawaii.gov/wp-content/results/2026%20Primary/{file}.pdf"
    if code == "de":
        return "https://elections.delaware.gov/results/enr/" + file
    if code == "ma":
        return f"{MA_BASE}search/year_from:2026/year_to:2026/office_id:{file}"
    if code == "fl":
        return FL_URL
    if code in EV:
        return EV[code][0] + file
    if code in ELSTATS:
        if file.endswith(".gov"):
            offices, elstats.OFFICES = elstats.OFFICES, ["Governor"]
            try:
                return elstats.url(file.removesuffix(".gov"), 2026)
            finally:
                elstats.OFFICES = offices
        return elstats.url(file, 2026)
    return {"nc": NC_URL, "il": IL_URL, "md": MD_URL, "mn": MN_URL, "ks": KS_URL, "in": IN_URL, "nj": NJ_URL}.get(code, BASE.format(code, "{}")).format(file)


def rows(code: str, path: Path, file: str = ""):
    if code in ELSTATS:
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                if row["election_type"].startswith("Primary") and row["division_type"] in ("County", "Locality", "City/Town"):
                    if row["office_name"] in ELSTATS_OFFICES:
                        yield {
                            "county": town_counties("VT")[plain(re.sub(elstats.SUFFIXES, "", row["division_name"].lower()))] if row["division_type"] == "City/Town" else row["division_name"].removesuffix(" County"),
                            "office": ELSTATS_OFFICES[row["office_name"]],
                            "district": row["district_name"] if row["district_name"].isdigit() else "",
                            "party": row["primary_party"],
                            "candidate": row["candidate_name"].strip(),
                            "votes": row["votes"].replace(",", "").removesuffix(".0"),
                        }
        return
    if code == "md":
        text = path.read_text(encoding="utf-8")
        office = re.search(r"<h2>([^<]*)", text)[1].strip()
        district = re.search(r"<h3>District (\d+)", text)
        head, body = text.split("<tbody>", 1)
        for county_row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
            cells = [re.sub(r"<[^>]*>", "", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", county_row, re.S)]
            if not cells or not cells[0] or cells[0].startswith("Total"):
                continue
            for header, votes in zip(re.findall(r"<th scope=\"col\">(.*?)</th>", head, re.S), cells[1:]):
                name, party = (re.sub(r"<img[^>]*>", "", part).strip() for part in re.split(r"<br\s*/?>", header)[:2])
                name = name.split(" and ")[0]
                yield {
                    "county": cells[0],
                    "office": "Governor" if office.startswith("Governor") else "U.S. House",
                    "district": district[1] if district else "",
                    "party": party,
                    "candidate": name,
                    "votes": votes.replace(",", ""),
                }
        return
    if code in EV:
        for item in json.loads(path.read_bytes())["data"]:
            found = EV[code][1].fullmatch(item["name"][0]["text"])
            if found is None:
                continue
            detail = json.loads(raw.cached(SOURCE, f"{EV[code][0]}ballot-items/{item['id']}").path.read_bytes())
            groups = found.groupdict()
            for county in detail["breakdownResults"]:
                if code == "ri" and plain(re.sub(elstats.SUFFIXES, "", county["locality"]["name"][0]["text"].lower())) not in town_counties("RI"):
                    continue
                for option in county["ballotOptions"]:
                    label = (((option["party"] or {}).get("name") or [{}])[0].get("text") or "").strip("()") or "Prefers Democratic Party"
                    yield {
                        "county": town_counties("RI")[plain(re.sub(elstats.SUFFIXES, "", county["locality"]["name"][0]["text"].lower()))] if code == "ri" else county["locality"]["name"][0]["text"].removesuffix(" County"),
                        "office": "U.S. Senate" if groups.get("senate") else "Governor" if groups.get("governor") else "U.S. House",
                        "district": groups.get("district") or "",
                        "party": (groups["party"] if groups["party"] in PARTIES else groups["party"].upper()) if "party" in groups else label,
                        "candidate": re.sub(r"\s*(\(I\)|\*)$", "", option["name"][0]["text"]).strip(),
                        "votes": str(option["voteCount"]),
                        "write_in": "Y" if option["isWriteIn"] else "",
                    }
        return
    if code == "hi":
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
        county = re.search(r"County of (\w+)", text)[1]
        for start, end in ((0, 62), (62, 122), (122, 400)):
            office = district = party = None
            for line in text.splitlines():
                segment = line[start:end].strip()
                found = re.fullmatch(r"(U\.S\. Representative, Dist (I+)|Governor) - ([NRD])", segment)
                if found is not None:
                    office, district, party = ("U.S. House", str(len(found[2])), found[3]) if found[2] else ("Governor", "", found[3])
                    continue
                if office is None:
                    continue
                candidate = re.fullmatch(r"(.+?)\s{2,}([\d,]+)\s+[\d.]+%", segment)
                if candidate is not None and not candidate[1].startswith(("Blank", "Over")):
                    surname, _, given = candidate[1].partition(",")
                    yield {"county": county, "office": office, "district": district, "party": {"R": "Republican", "D": "Democratic"}.get(party, party), "candidate": f"{given.strip()} {title(surname.strip())}".strip(), "votes": candidate[2].replace(",", "")}
                elif segment and not segment.startswith(("Blank", "Over", "Number")):
                    office = None
        return
    if code == "de":
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", path.read_text(encoding="utf-8", errors="replace")))
        for office_label, party, body in re.findall(
            r"(U\.S\. Senator|Representative in Congress) (Democratic|Republican) Party Candidate Name New Castle Kent Sussex State Percentage (.*?)(?= (?:[A-Z][A-Za-z.,' ]+?) (?:Democratic|Republican) Party Candidate Name|$)", text
        ):
            for candidate, *counts in re.findall(r"([A-Z][A-Z .'\"\-,]*?) (\d+) (\d+) (\d+) (\d+) [\d.]+", body):
                for county, votes in zip(("New Castle", "Kent", "Sussex"), counts):
                    yield {"county": county, "office": "U.S. Senate" if office_label.startswith("U.S.") else "U.S. House", "district": "0", "party": party, "candidate": candidate.strip(), "votes": votes}
        return
    if code == "ma":
        office = MA_OFFICES[next(file for file in FILES["MA"][2] if raw.cached(SOURCE, url("ma", file)).path == path)]
        for election, _, district, stage in elstats.ma_primary_rows(path.read_text()):
            table = csv.reader(raw.cached(SOURCE, f"{MA_BASE}download/{election}/precincts_include:0/").path.open(newline=""))
            header = next(table)
            next(table)
            for line in table:
                town = re.sub(r"^([NSEW])\. ", lambda m: {"N": "North ", "S": "South ", "E": "East ", "W": "West "}[m[1]], line[0])
                if not town or town == "TOTALS":
                    continue
                for name, votes in zip(header[3:], line[3:]):
                    if name and name not in ("Blanks", "Total Votes Cast"):
                        yield {
                            "county": town_counties("MA")[plain(re.sub(elstats.SUFFIXES, "", town.lower()))],
                            "office": office,
                            "district": district.split()[0][:-2] if office == "U.S. House" else "",
                            "party": stage.removesuffix(" Primary"),
                            "candidate": "Write-ins" if name == "All Others" else name,
                            "votes": votes.replace(",", ""),
                        }
        return
    if code == "nj":
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
        office, district, candidate, party = "U.S. House" if "House of Representatives" in text else "U.S. Senate", "", None, None
        for line in text.splitlines():
            found = re.match(r"(\w+) Congressional District", line)
            if found is not None:
                district = str(NJ_ORDINALS[found[1]])
                continue
            found = re.match(r"([A-Z][A-Z .'\-,]+?)(?: \(w\))?(?: \*)?\s{2,}.*\s(Democratic|Republican|Libertarian|Green|Independent)\s*$", line)
            if found is not None:
                candidate, party = found[1].strip(), found[2]
                continue
            stripped = line.strip()
            county = next((name for name in NJ_COUNTIES if stripped.startswith(name + " ")), None)
            tally = re.search(r"\s([\d,]+)$", stripped)
            if candidate is not None and county is not None and tally is not None:
                yield {"county": county.title(), "office": office, "district": district, "party": party, "candidate": candidate, "votes": tally[1].replace(",", "")}
        return
    if code == "fl":
        seen = set()
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                office = {"USS": "U.S. Senate", "USR": "U.S. House", "GOV": "Governor"}.get(row["RaceCode"])
                key = (row["RaceCode"], row["PartyCode"], row["Juris1num"], row["CountyCode"], row["CanNameLast"], row["CanNameFirst"], row["CanNameMiddle"])
                if office is None or key in seen or row["PartyCode"] not in ("REP", "DEM"):
                    continue
                seen.add(key)
                yield {
                    "county": row["CountyName"],
                    "office": office,
                    "district": row["Juris1num"].lstrip("0"),
                    "party": row["PartyCode"],
                    "candidate": " ".join(part for part in (row["CanNameFirst"], row["CanNameMiddle"], row["CanNameLast"]) if part),
                    "votes": row["CanVotes"],
                }
        return
    if code == "in":
        for region in json.loads(path.read_bytes())["Root"]["OfficeCategory"]["Regions"]["Region"]:
            races = region["Races"]["Race"]
            for race in races if isinstance(races, list) else [races]:
                for candidate in race["Candidates"]["Candidate"]:
                    yield {
                        "county": race["Jurisdiction"]["FIPS"],
                        "office": "U.S. House",
                        "district": region["MAP_JURISDICTION_NAME"],
                        "party": candidate["PARTY_ABBREV"],
                        "candidate": candidate["NAME_ON_BALLOT"],
                        "votes": str(candidate["TOTAL_VOTES"]),
                    }
        return
    if code == "ne":
        office = party = None
        candidates: list[str] = []
        previous = ""
        for line in subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout.splitlines():
            stripped = line.strip().replace("✓", "")
            if stripped.startswith("Member of the United States Senate"):
                office, party = "U.S. Senate", None
            elif stripped == "Governor":
                office, party = "Governor", None
            elif re.match(r"(Member of the U\.S\. House|Secretary of State$|State Treasurer$|Attorney General$|Auditor of Public Accounts$|Public Service Commission|Member of Board|Member of the State Board|Member of the Legislature|Lieutenant)", stripped):
                office, party = None, None
            found = re.fullmatch(r"(Republican|Democratic|Libertarian|Legal Marijuana NOW) Party Nomination(?: \(.*\) — continued| \(.*\))?", stripped)
            if found is not None:
                party = {"Republican": "Republican", "Democratic": "Democratic"}.get(found[1])
                candidates = []
            elif re.match(r"(Libertarian|Legal Marijuana NOW|Democratic|Republican) Party (did not make|Nomination)", stripped):
                party = None
            elif office is not None and party is not None:
                header = re.fullmatch(r"County\s{2,}(\S.*)", stripped)
                if header is not None:
                    last_names = re.split(r"\s{2,}", header[1].strip())
                    last_names = last_names[: last_names.index("County")] if "County" in last_names else last_names
                    first_names = re.split(r"\s{2,}", previous.strip().replace("✓", "")) if previous.strip() and not re.search(r"\d|Nomination|Page", previous) else []
                    first_names = first_names[: len(last_names)]
                    candidates = [f"{first} {last}".strip() for first, last in zip(first_names, last_names, strict=True)] if len(first_names) == len(last_names) else last_names
                elif candidates:
                    for row in re.finditer(r"([A-Za-z][A-Za-z .'-]*?)\s{2,}((?:\d[\d,]*\s+){%d}\d[\d,]*)" % (len(candidates) - 1), stripped):
                        if row[1] != "Total":
                            for candidate, votes in zip(candidates, row[2].split(), strict=True):
                                yield {"county": row[1], "office": office, "district": "", "party": party, "candidate": candidate, "votes": votes.replace(",", "")}
            if stripped:
                previous = line
        return
    if code == "la":
        with path.open(newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            for row in reader:
                for column in range(2, len(header)):
                    found = re.fullmatch(r"(.*) \((\w+)\)", header[column])
                    yield {"county": row[1], "office": "U.S. Senate", "district": "", "party": "Democratic" if row[0].endswith("Democratic Party") else "Republican", "candidate": found[1] if found is not None else header[column], "votes": row[column]}
        return
    if code == "ct":
        if file.endswith("Lookupdata"):
            return
        lookup = json.loads(raw.cached(SOURCE, url(code, file.replace("townVotes_Electiondata", "Lookupdata"))).path.read_text())
        counties_of = {entry["TownID"]: "09" + CT_COUNTIES[lookup["counties"][entry["CountyID"]]] for entry in lookup["countyTowns"]}
        offices = {key: value for entry in lookup["officeList"] for key, value in entry.items()}
        totals = {}
        for town, contests in json.loads(path.read_text()).items():
            for office_id, results in contests.items():
                found = re.fullmatch(r"Governor|Representative in Congress (\d+)", offices[office_id]["NM"])
                if found is not None:
                    for result in results:
                        for candidate_id, tally in result.items():
                            candidate = lookup["candidateIds"][candidate_id]
                            key = (counties_of[town], "Governor" if found[1] is None else "U.S. House", found[1] or "", lookup["partyIds"][candidate["P"]]["NM"].removesuffix(" Party"), candidate["NM"])
                            totals[key] = totals.get(key, 0) + int(tally["V"])
        for (fips, office, district, party, candidate), votes in totals.items():
            yield {"county": fips, "office": office, "district": district, "party": party, "candidate": candidate, "votes": str(votes)}
        return
    if code == "alx":
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                county = member.rpartition("-")[2].removesuffix(".xls").replace("_", " ")
                for row in pl.read_excel(io.BytesIO(archive.read(member)), has_header=False, engine="calamine", infer_schema_length=0).rows()[1:]:
                    found = re.fullmatch(r"(GOVERNOR|UNITED STATES SENATOR|UNITED STATES REPRESENTATIVE, (\d+)\w\w CONGRESSIONAL DISTRICT) \((DEM|REP)\)", row[0].strip())
                    if found is not None and row[2].strip() not in ("Over Votes", "Under Votes"):
                        office = {"GOVERNOR": "Governor", "UNITED STATES SENATOR": "U.S. Senate"}.get(found[1], "U.S. House")
                        yield {"county": county, "office": office, "district": found[2] or "", "party": "Democratic" if found[3] == "DEM" else "Republican", "candidate": row[2].strip(), "votes": str(sum(int(cell) for cell in row[3:] if cell and cell.isdigit()))}
        return
    if code == "ar":
        if file == "contests":
            return
        names = json.loads(raw.cached(SOURCE, url(code, "contests")).path.read_text())["response"]["contests"]
        for contest in json.loads(path.read_text())["ContestData"]:
            found = re.fullmatch(r"(REP|DEM) (Governor|U\.S\. Senate|U\.S\. Congress District (\d+))", names[contest["ContestID"]]["contestName"]) if contest["ContestID"] in names else None
            if found is not None:
                office = {"Governor": "Governor", "U.S. Senate": "U.S. Senate"}.get(found[2], "U.S. House")
                for fips, location in contest["Locations"].items():
                    for choice in location["Choices"]:
                        yield {"county": fips, "office": office, "district": found[3] or "", "party": "Republican" if found[1] == "REP" else "Democratic", "candidate": names[contest["ContestID"]]["choices"][choice["ChoiceID"]]["name"], "votes": str(choice["TotalVotes"])}
        return
    if code == "ok":
        if file.startswith("getelec"):
            return
        day, _, race = file.partition("/")
        meta = next(entry for entry in json.loads(raw.cached(SOURCE, url(code, f"getelec/{day}")).path.read_text())["races"] if str(entry["raceID"]) == race)
        found = re.fullmatch(r"FOR (GOVERNOR|UNITED STATES SENATOR|UNITED STATES REPRESENTATIVE DISTRICT (\d+))", meta["raceTitle"])
        office = {"GOVERNOR": "Governor", "UNITED STATES SENATOR": "U.S. Senate"}.get(found[1], "U.S. House")
        for county in json.loads(path.read_text())["summaryCandResults"]:
            for candidate, result in zip(meta["raceCandidates"], county["candResults"], strict=True):
                yield {"county": f"40{2 * int(county['countyID']) - 1:03}", "office": office, "district": found[2] or "", "party": "Democratic" if meta["raceTitle2"] == "Democrat" else meta["raceTitle2"], "candidate": candidate["candName"], "votes": str(result["totalVotes"])}
        return
    if code == "me":
        office, district, party = ME_FILES[file]
        sheet = pl.read_excel(path, has_header=False, infer_schema_length=0).rows()
        header = next(index for index, row in enumerate(sheet) if "CTY" in row)
        county_column = sheet[header].index("CTY")
        first = max(index for index, cell in enumerate(sheet[header]) if cell in ("DIS", "CTY", "Municipality", "TOWN", "WARD-PRECINCT")) + 1
        candidates = {}
        for column in range(first, len(sheet[header])):
            text = " ".join((sheet[0][column] or "").split())
            if text != "" and text not in ("BLANK", "TBC"):
                surname, _, given = text.partition(", ")
                given, _, suffix = given.replace(",", "").rpartition(" ") if given.endswith((" III", " JR", " SR", " II")) else (given, "", "")
                candidates[column] = f"{given} {surname} {suffix}".replace("  ", " ").strip()
        totals = {}
        for row in sheet[header + 1 :]:
            if row[county_column] in ME_COUNTIES:
                for column, candidate in candidates.items():
                    key = (ME_COUNTIES[row[county_column]], candidate)
                    totals[key] = totals.get(key, 0) + int(row[column] or 0)
        for (fips, candidate), votes in totals.items():
            yield {"county": fips, "office": office, "district": district, "party": party, "candidate": candidate, "votes": str(votes)}
        return
    if code == "ks":
        totals = {}
        for county, race, candidate, party, votes in pl.read_excel(path).select("County", "Race", "Candidate", "Party", "Votes").iter_rows():
            office = "U.S. Senate" if race == "United States Senate" else "Governor" if race.startswith("Governor") else "U.S. House"
            first, _, last = candidate.partition(", ")
            name = candidate.split(" / ")[0] if office == "Governor" else f"{last} {first}".strip() if last else candidate
            key = (county, office, race.rpartition(" ")[2] if office == "U.S. House" else "", party, name)
            totals[key] = totals.get(key, 0) + int(votes)
        for (county, office, district, party, candidate), votes in totals.items():
            yield {"county": county, "office": office, "district": district, "party": party, "candidate": candidate, "votes": str(votes)}
        return
    if code == "mn":
        totals = {}
        for line in path.read_text(encoding="latin-1").splitlines():
            cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
            found = re.fullmatch(r"(U\.S\. Representative|U\.S\. Senator|Governor & Lt Governor)(?: District \d+)?", cells[4]) if len(cells) > 13 else None
            if found is not None:
                key = (f"27{2 * int(cells[1]) - 1:03}", MN_OFFICES[found[1]], cells[5], cells[10], re.sub(r"\s+", " ", cells[7].split(" and ")[0]))
                totals[key] = totals.get(key, 0) + int(cells[13] or 0)
        for (fips, office, district, party, candidate), votes in totals.items():
            yield {"county": fips, "office": office, "district": district, "party": party, "candidate": candidate, "votes": str(votes)}
        return
    if code == "il":
        totals: dict[tuple[str, str, str, str, str], int] = {}
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                found = IL_CONTEST.fullmatch(row["ContestName"].strip())
                if found is None:
                    continue
                key = (
                    IL_CITIES.get(row["JurisName"], row["JurisName"]),
                    "U.S. House" if found[1] else "U.S. Senate" if found[2] else "Governor",
                    found[1] or "",
                    row["PartyName"].title(),
                    row["CandidateName"].strip(),
                )
                totals[key] = totals.get(key, 0) + int(row["VoteCount"])
        for (county, office, district, party, candidate), votes in totals.items():
            yield {"county": county, "office": office, "district": district, "party": party, "candidate": candidate, "votes": str(votes)}
        return
    if code != "nc":
        with path.open(newline="") as handle:
            yield from csv.DictReader(handle)
        return
    with zipfile.ZipFile(path) as archive:
        with archive.open("results_pct_20260303.txt") as handle:
            totals: dict[tuple[str, str, str, str, str], int] = {}
            for row in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"), delimiter="\t"):
                found = NC_CONTEST.fullmatch(row["Contest Name"].strip())
                if found is None:
                    continue
                key = (
                    row["County"].title(),
                    "U.S. Senate" if found[2] is None else "U.S. House",
                    found[2] or "",
                    {"REP": "REP", "DEM": "DEM", "LIB": "LIB"}.get(found[3], found[3]),
                    row["Choice"].strip(),
                )
                totals[key] = totals.get(key, 0) + int(row["Total Votes"])
            for (county, office, district, party, candidate), votes in totals.items():
                yield {
                    "county": county,
                    "office": office,
                    "district": district,
                    "party": party,
                    "candidate": candidate,
                    "votes": str(votes),
                }


def title(text: str) -> str:
    if text != text.upper():
        return text
    words = []
    for word in text.split():
        if re.fullmatch(r"(II|III|IV|VI)", word):
            words.append(word)
        elif re.match(r"MC[A-Z]{2}", word):
            words.append("Mc" + word[2:].capitalize())
        else:
            words.append(re.sub(r"[A-Za-z]+", lambda match: match[0].capitalize(), word))
    return " ".join(words)


def fetch() -> None:
    for code, _, names, *_ in [*FILES.values(), *RUNOFFS.values()]:
        for name in names:
            if code == "ma":
                for election, *_ in elstats.ma_primary_rows(raw.fetch(SOURCE, url(code, name), suffix=".html").path.read_text()):
                    raw.fetch(SOURCE, f"{MA_BASE}download/{election}/precincts_include:0/", suffix=".csv")
                continue
            if code == "ar":
                raw.store(SOURCE, url(code, name), httpx.get(url(code, name), headers={**BROWSER, "Origin": "https://arkansas.tally-enr.com"}, timeout=600).content, suffix=".json", retrieved_at=datetime.now(UTC), media_type="application/json")
                continue
            if code == "ok":
                token = httpx.put(OK_BASE + "enrapi/login/", json={"Username": "appuser", "Password": "X098de!22k098Mgfd"}, headers=BROWSER, timeout=60).json()[1:]
                raw.store(SOURCE, url(code, name), httpx.get(url(code, name), headers={**BROWSER, "Authorization": f"Bearer {token}"}, timeout=300).content, suffix=".json", retrieved_at=datetime.now(UTC), media_type="application/json")
                continue
            if code == "me":
                raw.store(SOURCE, url(code, name), httpx.get(url(code, name), headers=BROWSER, follow_redirects=True, timeout=300).content, suffix=".xlsx", retrieved_at=datetime.now(UTC), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                continue
            if code == "fl":
                raw.store(SOURCE, FL_URL, httpx.get(FL_URL, headers=BROWSER, follow_redirects=True, timeout=300).content, suffix=".txt", retrieved_at=datetime.now(UTC), media_type="text/tab-separated-values")
                continue
            raw.fetch(SOURCE, url(code, name), suffix={"hi": ".pdf", "ne": ".pdf", "ri": ".json", "ct": ".json", "alx": ".zip", "de": ".html", "ma": ".html", "nj": ".pdf", "ks": ".xlsx", "in": ".json", "ga": ".json", "ga2": ".json", "wa": ".json", "ut": ".json", "id": ".json"}.get(code) or name[name.rindex(".") :])
    fetch_ballot_items()


def fetch_ballot_items() -> None:
    for code, base in ((c, EV[c]) for c in EV):
        for item in json.loads(raw.cached(SOURCE, url(code, "ballot-items")).path.read_bytes())["data"]:
            if base[1].fullmatch(item["name"][0]["text"]):
                raw.fetch(SOURCE, f"{base[0]}ballot-items/{item['id']}", suffix=".json")


def parse() -> None:
    pass


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 U.S. House, U.S. Senate, and governor primaries in Texas, California, and Michigan, by county, from OpenElections",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state_names = dict(
            conn.execute(
                """select right(p.iso_code, 2), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$'"""
            ).fetchall()
        )
        templates = {
            value: row
            for value, *row in conn.execute(
                """select x.value, co.kind, co.office_id, co.body_id, co.district_area_id, co.seat_id,
                          co.boundary_set_id, co.magnitude, co.term, co.default_result_kind
                   from contest co join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                   where x.value ~ '^(2026-11-03|2024-11-05):[A-Z]{2}:house:[0-9]+$'
                      or x.value ~ '^(2026-11-03|2020-11-03):[A-Z]{2}:senate$'
                      or x.value ~ '^(2026-11-03|2022-11-08):[A-Z]{2}:governor$'"""
            ).fetchall()
        }
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "irv", "two_round", "two_round_35", "two_round_30", "top_two")}
        counties: dict[tuple[str, str], UUID] = {}
        for code, text, id in conn.execute(
            """select u.code, n.text, u.id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ %s order by u.code""",
            [f"^({'|'.join(FIPS.values())})"],
        ).fetchall():
            counties.setdefault((code[:2], plain(text)), id)
            counties[(code[:2], code)] = id
            if int(code[2:]) >= 500:
                counties[(code[:2], plain(text) + "city")] = id
        counties[("29", "kansascity")] = conn.execute("select id from reporting_unit where scheme_code = 'us.place' and code = '2938000'").fetchone()[0]
        pool: dict[tuple[str, str], set[UUID]] = defaultdict(set)
        for state, person, text in conn.execute(
            """select split_part(x.value, ':', 2), cy.person_id, n.text
               from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ ('^20(1[4-9]|2[0-9])-[0-9-]+:(' || %s || '):(house|senate|governor)')""",
            ['|'.join(FILES)],
        ).fetchall():
            pool[(state, slug(text))].add(person)
        persons: dict[tuple[str, str], UUID] = {}
        parties: dict[str, UUID] = {}

        def party_of(label: str | None) -> UUID | None:
            if label not in PARTIES:
                return None
            key = PARTIES[label][1]
            if key not in parties:
                parties[key] = cs.key("us.party", key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind="party", polity_id=cs.polity("US"))
                    name(parties[key], {"no-labels-party": "No Labels Party"}[key])
            return parties[key]

        def person_of(state: str, text: str) -> UUID:
            if (state, slug(text)) not in persons:
                found = pool.get((state, slug(text)), set())
                if len(found) == 1:
                    persons[(state, slug(text))] = next(iter(found))
                else:
                    id = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                    if id in cs.minted:
                        cs.add("person", id=id)
                        name(id, text)
                    persons[(state, slug(text))] = id
            return persons[(state, slug(text))]

        sources: dict[str, UUID] = {}
        retired_persons: set[UUID] = set()
        held_options = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.option' and value like '2026-%'"
            ).fetchall()
        }
        sources: dict[str, UUID] = {}
        retired_persons: set[UUID] = set()
        persons: dict[tuple[str, str], UUID] = {}
        parties: dict[str, UUID] = {}

        def party_of(label: str | None) -> UUID | None:
            if label not in PARTIES:
                return None
            key = PARTIES[label][1]
            if key not in parties:
                parties[key] = cs.key("us.party", key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind="party", polity_id=cs.polity("US"))
                    name(parties[key], {"no-labels-party": "No Labels Party"}[key])
            return parties[key]

        def person_of(state: str, text: str) -> UUID:
            if (state, slug(text)) not in persons:
                found = pool.get((state, slug(text)), set())
                if len(found) == 1:
                    persons[(state, slug(text))] = next(iter(found))
                else:
                    id = cs.key(f"{SOURCE}.person", f"{state}:{slug(text)}")
                    if id in cs.minted:
                        cs.add("person", id=id)
                        name(id, text)
                    persons[(state, slug(text))] = id
            return persons[(state, slug(text))]

        sources: dict[tuple[str, int], UUID] = {}
        retired_persons: set[UUID] = set()
        elections: dict[tuple[str, int], UUID] = {}
        totals: dict[tuple[str, int], int] = {}
        for state, stage_no, (code, day, files, *first) in [
            *((state, 1, entry) for state, entry in FILES.items()),
            *((state, 2, entry) for state, entry in RUNOFFS.items()),
        ]:
            round_slot = (state, stage_no)
            round_word = "primary runoff" if stage_no == 2 else "primary"
            state_name = state_names[state]
            publisher, address, how = OFFICIAL.get(state, ("OpenElections", f"https://github.com/openelections/openelections-data-{code}", ""))
            source_title = f"{state_name} 2026 primary{' runoff' if stage_no == 2 else ''} results by county"
            sources[round_slot] = cs.source(publisher, source_title)
            if sources[round_slot] in cs.minted:
                cs.add(
                    "source",
                    id=sources[round_slot],
                    kind="official_body" if state in OFFICIAL else "community",
                    publisher=publisher,
                    title=source_title,
                    url=address,
                    license=None,
                )
            for file in files:
                cs.artifact(raw.cached(SOURCE, url(code, file)), sources[round_slot])
            election_key = f"{day}:{state}:{'runoff' if stage_no == 2 else 'primary'}"
            elections[round_slot] = cs.key("us.election", election_key)
            if elections[round_slot] in cs.minted:
                cs.add(
                    "election",
                    id=elections[round_slot],
                    polity_id=cs.polity(f"US-{state}"),
                    parent_id=None,
                    kind="runoff" if stage_no == 2 else "primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(elections[round_slot], f"2026 {state_name} primary runoff election" if stage_no == 2 else f"2026 {state_name} primary election")
                cs.add("slug", path=f"us/{state.lower()}/{day}-{round_word.replace(' ', '-')}", entity_id=elections[round_slot])
            grouped: dict[tuple[str, str, str], dict[tuple[str, str, str], dict[str, int]]] = defaultdict(
                lambda: defaultdict(lambda: defaultdict(int))
            )
            scope_labels: dict[str, str] = {}
            spellings: dict[tuple[str, str, str, str], str] = {}
            junk: dict[tuple[str, str, str], set[str]] = defaultdict(set)
            for file in files:
                for row in rows(code, raw.cached(SOURCE, url(code, file)).path, file):
                    chamber = OFFICES.get(row["office"])
                    if chamber is None or not row["votes"].isdigit() or row["party"] not in PARTIES:
                        continue
                    scope = PARTIES[row["party"]][0]
                    if scope == "" and state not in TOP_TWO:
                        continue
                    scope_labels.setdefault(scope, row["party"])
                    district = ("0" if state in AT_LARGE else str(int(row["district"]))) if chamber == "house" else ""
                    if JUNK.search(row["candidate"].strip()):
                        junk[(chamber, district, "" if state in TOP_TWO else scope)].add(title(row["candidate"].strip()))
                        continue
                    pooled = POOLED.search(row["candidate"]) is not None or row.get("write_in") == "Y"
                    label = row["party"] if not pooled else ""
                    candidate = "Write-ins" if pooled else title(row["candidate"].strip())
                    candidate = spellings.setdefault((chamber, district, scope, slug(candidate)), candidate)
                    county = row["county"] if row["county"].isdigit() else plain(row["county"])
                    county = ALIASES.get((state, county), county)
                    grouped[(chamber, district, "" if state in TOP_TWO else scope)][
                        (candidate, label, "pooled" if pooled else "")
                    ][county] += int(row["votes"])
            for (chamber, district, scope), labels in sorted(junk.items()):
                key_tail = chamber + (f":{district}" if district else "") + (f":{scope}" if scope else "")
                for label in sorted(labels):
                    if f"{day}:{state}:{key_tail}:{slug(label)}" not in held_options:
                        continue
                    option = cs.key("us.option", f"{day}:{state}:{key_tail}:{slug(label)}")
                    if option not in cs.minted:
                        reason = f"{label} is a report line, not a candidate"
                        cs.add(
                            "entity_retirement",
                            entity_id=cs.key("us.candidacy", f"{day}:{state}:{key_tail}:{slug(label)}"),
                            reason=reason,
                        )
                        cs.add("entity_retirement", entity_id=option, reason=reason)
                        gone = cs.key(f"{SOURCE}.person", f"{state}:{slug(label)}")
                        if gone not in retired_persons and gone not in cs.minted:
                            retired_persons.add(gone)
                            cs.add("entity_retirement", entity_id=gone, reason=reason)
            for (chamber, district, scope), candidates in sorted(grouped.items()):
                key_tail = chamber + (f":{district}" if district else "") + (f":{scope}" if scope else "")
                contest_key = f"{day}:{state}:{key_tail}"
                contest = cs.key("us.contest", contest_key)
                template_key = {
                    "house": f"2026-11-03:{state}:house:{district}",
                    "senate": f"2026-11-03:{state}:senate",
                    "governor": f"2026-11-03:{state}:governor",
                }[chamber]
                fallback = {
                    "house": f"2024-11-05:{state}:house:{district}",
                    "senate": f"2020-11-03:{state}:senate",
                    "governor": f"2022-11-08:{state}:governor",
                }[chamber]
                shapeless = template_key not in templates
                kind, office, body, area, seat, plan, magnitude, term, result_kind = templates[
                    fallback if shapeless else template_key
                ]
                if shapeless and chamber == "house":
                    area, plan = None, None
                scope_label = scope.replace("-", " ").title()
                if chamber == "house":
                    number = int(district)
                    suffix = "th" if 10 <= number % 100 <= 20 else ORDINALS.get(number % 10, "th")
                    at_large = number == 0
                    title_text = f"2026 U.S. House {scope_label + ' ' if scope else ''}{round_word} in {state_name}'s " + (
                        "at-large congressional district" if at_large else f"{number}{suffix} congressional district"
                    )
                elif chamber == "senate":
                    title_text = f"2026 U.S. Senate {scope_label + ' ' if scope else ''}{round_word} in {state_name}"
                else:
                    title_text = f"2026 {state_name} gubernatorial " + (
                        f"{scope_label} {round_word}" if scope else "top-two primary"
                    )
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[round_slot],
                    kind=kind,
                    office_id=office,
                    body_id=body,
                    district_area_id=area,
                    seat_id=seat,
                    boundary_set_id=plan,
                    magnitude=magnitude,
                    party_scope_id=party_of(scope_labels[scope])
                    if scope
                    else None,
                    term=term,
                    round_number=stage_no,
                    previous_round_id=cs.key("us.contest", f"{first[0]}:{state}:{key_tail}") if stage_no == 2 else None,
                    electoral_system_id=systems["top_two"] if state in TOP_TWO else systems["irv"] if state == "ME" else systems[RUNOFF_SYSTEMS.get(state, "fptp")],
                    default_result_kind=result_kind,
                )
                name(contest, title_text)
                cs.add(
                    "slug",
                    path=f"us/{state.lower()}/{day}-{round_word.replace(' ', '-')}/{chamber}"
                    + (f"-{district}" if district else "")
                    + (f"-{scope}" if scope else ""),
                    entity_id=contest,
                )
                cs.add("note", entity_id=contest, kind="methodology", body_md=OFFICIAL_METHOD.format(publisher=publisher, how=how) if state in OFFICIAL else METHOD.format(state=state_name))
                if shapeless and chamber == "house" and state in REDISTRICTED:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=CAVEAT)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[round_slot],
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[round_slot],
                    is_default=True,
                    completeness=1,
                )
                named: list[tuple[int, UUID]] = []
                rcv = RCV_WINNERS.get((state, chamber, district, scope))
                rcv_option = None
                for (candidate, label, pooled), by_county in sorted(candidates.items()):
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    party_id = party_of(label)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if pooled else "candidate",
                        party_id=None if pooled else party_id if (state in TOP_TWO or scope) else None,
                        ballot_label=candidate,
                    )
                    if not pooled:
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}"),
                            person_id=person_of(state, candidate),
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=party_id is None,
                        )
                    for county, votes in sorted(by_county.items()):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=counties[(FIPS[state], county)],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=votes,
                        )
                    if not pooled:
                        named.append((sum(by_county.values()), option))
                        if rcv is not None and slug(candidate) == rcv[0]:
                            rcv_option = option
                named.sort(key=lambda entry: -entry[0])
                everyone = sum(votes for votes, _ in named)
                if state in TOP_TWO or (stage_no == 1 and len(named) > 1 and state in THRESHOLDS and named[0][0] * 100 <= THRESHOLDS[state] * everyone):
                    leaders = named[:2]
                    if len(named) > 2 and named[1][0] == named[2][0]:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md="A tie for the last advancing place leaves the outcome unrecorded.")
                        continue
                    for _, option in leaders:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
                elif rcv_option is not None:
                    cs.add("outcome", result_set_id=result_set, option_id=rcv_option, kind="won", seats=None)
                    cs.add("note", entity_id=result_set, kind="context", body_md=f"County figures are first-choice votes. {rcv[1]}")
                elif named:
                    if len(named) > 1 and named[0][0] == named[1][0]:
                        continue
                    cs.add("outcome", result_set_id=result_set, option_id=named[0][1], kind="won", seats=None)
                totals[round_slot] = totals.get(round_slot, 0) + 1
        print(totals)
        return cs.write()
