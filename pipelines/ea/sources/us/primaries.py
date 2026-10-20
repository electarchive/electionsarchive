import csv
import html
import io
import itertools
import json
import re
import struct
import subprocess
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID
from xml.etree import ElementTree

import fastexcel
import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.sources.us import elstats, ncsbe, openelections, wa_local
from ea.sources.us.county_changes import ct_regions
from ea.store import DATA, connect

SOURCE = "us.primaries"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
CALIFORNIA = "https://elections.cdn.sos.ca.gov/sov/2024-primary/sov/79-us-rep-congress.xlsx"
CA_PRESIDENT = "https://elections.cdn.sos.ca.gov/sov/2024-primary/sov/25-presidential-formatted.xlsx"
NORTH_CAROLINA = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2024_03_05/results_pct_20240305.zip"
MINNESOTA = "https://electionresultsfiles.sos.mn.gov/20240813/{}.txt"
MN_PRESIDENT = "https://electionresultsfiles.sos.mn.gov/20240305/{}.txt"
MARYLAND = "https://elections.maryland.gov/elections/archive/2024/election_data/PP24_{:02d}Precincts{}.csv"
WASHINGTON = "https://results.vote.wa.gov/results/{0}/export/{0}_allcounties.csv"
INDIANA = "https://enr.indianavoters.in.gov/archive/2024Primary/data/OffCatC_{}_B.json"
IN_CATEGORIES = (1012, 1006, 1005, 1009)
ALABAMA = (
    "https://www.sos.alabama.gov/sites/default/files/election-data/2024-04/2024%20Primary%20Precinct%20Results.ZIP",
    "https://www.sos.alabama.gov/sites/default/files/election-data/2024-07/2024%20Primary%20Runoff%20Precinct%20Results.zip",
)
MASSACHUSETTS = [
    f"{elstats.MASSACHUSETTS}search/year_from:2024/year_to:2024/office_id:{office}{stage}"
    for office, stage in (("1", ""), ("6", ""), ("5", "/stage:Democratic"), ("5", "/stage:Republican"))
]
IOWA = "https://electionresults.iowa.gov/IA/121508/344769/reports/detailxml.zip"
SOUTH_CAROLINA = {
    f"https://www.enr-scvotes.org/SC/{election}/reports/detailxml.zip": day
    for election, day in (
        ("119283/327921", date(2024, 2, 3)),
        ("119816/330796", date(2024, 2, 24)),
        ("122026/344536", date(2024, 6, 25)),
    )
}
KANSAS = "https://sos.ks.gov/elections/24elec/2024-Primary-Election-United-States-House-of-Representatives-Precinct%20Results.xlsx"
KS_PRESIDENT = "https://sos.ks.gov/elections/24elec/2024-Presidential-Preference-Primary-Official-Vote-Totals{}.pdf"
TED = "https://data.capitol.texas.gov/api/3/action/package_show?id=2024_{}"
TEXAS = ("democratic_primary", "republican_primary", "democratic_runoff", "republican_runoff")
OHIO = (
    "https://web.archive.org/web/20240420171448id_/https://www.ohiosos.gov/globalassets/elections/2024/pri/official/precinct-level-official-results-2024-primary---republican.xlsx",
    "https://web.archive.org/web/20240420171451id_/https://www.ohiosos.gov/globalassets/elections/2024/pri/official/precinct-level-official-results-primary-2024---democratic.xlsx",
)
FLORIDA = "https://flelectionfiles.floridados.gov/enightfilespublic/{}_ElecResultsFL.txt"
OREGON = "https://records.sos.state.or.us/ORSOSCMSearch/Search/DocumentStream.ashx?uri=13735456"
ARKANSAS = "https://enr-results-api.totalresults.com/Contest/{}?cId=arkansas&electionID=1845{}"
PENNSYLVANIA = "https://www.electionreturns.pa.gov/api/ElectionReturn/GetCountyBreak?officeId={}&districtId={}&methodName=GetCountyBreak&electionid=105&electiontype=P&isactive=0"
PA_OFFICES = ((1, 1), (2, 1), *((11, district) for district in range(2, 19)))
ILLINOIS = "https://elections.il.gov/NewDocDisplay.aspx?khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1Ze1iXEwZay4hPK3eAEa%2f2wcdY8MIeoGR39sSBMsL2x%2bcO%2bo4b1aWap6eXuodBvDiO9C3KC9P%2fqVu"
IL_TOTALS = "https://elections.il.gov/NewDocDisplay.aspx?khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1Ze1iXEwZay4hPK3eAEa%2f2we615l6oeLwg6WnqfsqBpfS0q%2bmbe1kY9qqK4nL4jdXPvz3PxN7kL%2fldHLhodL0QRQ%3d"
IL_WRITE_INS = ("33561", "33562", "33567")
COLUMBIA = "https://electionresults.dcboe.org/{}"
LOUISIANA = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=20240323/{}.htm"
LA_RACES = {"65031": "Democratic", "65033": "Republican"}
MS_RUNOFF = "https://www.sos.ms.gov/elections/electionResults/Republican%20Primary%20Runoff%20Certified%20Results24.pdf"
NC_RUNOFF = "https://er.ncsbe.gov/enr/20240514/data/{}.txt"
NEW_YORK = tuple(
    f"https://web.archive.org/web/{stamp}id_/https://elections.ny.gov/system/files/documents/2024/{name}.xlsx"
    for stamp, name in (
        ("20240503020451", "05/2024-democratic-presidential-primary"),
        ("20240520213959", "05/2024-republican-presidential-primary"),
        ("20240917001213", "08/2024-june-primary-results-for-certification_0"),
    )
)
ARIZONA = tuple(
    f"https://web.archive.org/web/{stamp}id_/https://azsos.gov/sites/default/files/docs/{name}.pdf"
    for stamp, name in (
        ("20240407175415", "2024_PPE_Official_Canvass_20240404"),
        ("20240817161358", "2024_Primary_Election_Official_Canvass_0815b"),
    )
)
ALASKA = "https://www.elections.alaska.gov/results/24PRIM/ENRbyPrecinct.csv"
VERMONT = "https://outside.vermont.gov/dept/sos/Elections_Division/election_info_resources/elections_results_data/2024_presidential_primary_official_report_canvassing_committee.pdf"
CLARK = "https://clark.legistar1.com/clark/attachments/f2a5b366-4205-4df9-9335-68731b056c1f.pdf"
OKLAHOMA = "https://web.archive.org/web/20240314213731id_/https://oklahoma.gov/content/dam/ok/en/elections/candidate-filing-archives/2024-candidate-filing-archives/2024-ppp-cd-results.pdf"
MONTANA = "https://web.archive.org/web/20240905130237id_/https://sosmt.gov/wp-content/uploads/wpfd/preview_files/2024-Primary-State-Canvass.pdf"
WISCONSIN = tuple(
    f"https://web.archive.org/web/{stamp}id_/https://elections.wi.gov/sites/default/files/documents/County%20by%20County%20{name}.pdf"
    for stamp, name in (
        ("20240510102059", "Report_all%20contests"),
        ("20240915104042", "Report%20-%20US%20Senate"),
        ("20240915104039", "Report_US%20Congress"),
    )
)
KENTUCKY = "https://elect.ky.gov/results/2020-2029/Documents/2024%20Primary%20Results.pdf"
DELAWARE = "https://elections.delaware.gov/reports/PR2024.{}"
UTAH = "https://electionresults.utah.gov/cdn/results/utah/export-primary06252024.json"
SUMMIT = "https://vote.utah.gov/wp-content/uploads/2024/08/P24_Summit.pdf"
HAWAII = "https://files.hawaii.gov/elections/files/results/2024/Primary/{}.txt"
MISSOURI = "https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/ActualResults-August62024.pdf"
MAINE = "https://lldc.mainelegislature.org/Open/Elections/Returns/2024/{}.xlsx"
NEW_JERSEY = "https://www.nj.gov/state/elections/assets/pdf/election-results/2024/2024-official-primary-results-{}.pdf"
NEBRASKA = "https://sos.nebraska.gov/sites/default/files/doc/elections/2024/2024%20Primary%20Canvass%20Book.pdf"
NJ_FILES = {"president": "President", "us-senate": "U.S. Senate", "us-house": "U.S. House"}
ORDINALS = (
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Eleventh",
    "Twelfth",
)
ME_FILES = {
    "20240305/elec_20240305a": ("President", None, "Democratic"),
    "20240305/elec_20240305b": ("President", None, "Republican"),
    "20240611/elec_20240611a": ("U.S. Senate", None, "Democratic"),
    "20240611/elec_20240611c": ("U.S. Senate", None, "Republican"),
    "20240611/elec_20240611e": ("U.S. House", "1", "Democratic"),
    "20240611/elec_20240611i": ("U.S. House", "1", "Republican"),
    "20240611/elec_20240611k": ("U.S. House", "2", "Democratic"),
    "20240611/elec_20240611m": ("U.S. House", "2", "Republican"),
}
OPENELECTIONS = {
    "AZ": "bf14331521f62f74cb17848ab11ca3dfd27f4f82",
    "CT": "f948d0a1ce36871de4a1d696612ab2a268fd0758",
    "GA": "36b7453793848c02d8f41601d9304a66ad828dd0",
    "KY": "025799258505414d62830ea75fd34b58cb2ee59b",
    "MI": "2103114f669c2bb25f19792bf5ca290300381396",
    "MS": "84d07603a7eacefa07cb58e90fcb12ce76fcdcce",
    "ND": "c25145fbab08f849e6eeec319f516d7916533ea1",
    "PA": "e79aeb525b4bb4e834c21bab0699fdbf9b6b2e9a",
    "RI": "977565f9d7cf7a32d3a2a454282b5fd291cef291",
    "SC": "5e05b67e15f3614a20d31144df83248235cba6f2",
    "SD": "9e01b9aea628e9ae17ada6656b649daf319fd212",
    "TN": "4cb8cc5d0bb74b45dc05ad03c80c41b246301c1c",
    "UT": "a023ab4d87c4a93f0913f505c6fcbad1a585db57",
    "WV": "49d32c279d0e02d740c8af4fcb5b5eee380fbed4",
    "WY": "d4d430672e8823beeb7dbc9274362992739155c3",
}
PARTIAL = ("AZ", "PA", "NV")
ELECTIONSTATS = ("CO", "CT", "ID", "NH", "NM", "NY", "VA", "VT")
TYPOS = {
    "MO": {"kansascity": "2938000"},
    "MS": {"jeffdavis": "28065"},
    "TN": {"williamsonx": "47187"},
    "VT": {"essexjunction": "50007"},
}
DAYS = {("RI", date(2024, 8, 13)): date(2024, 9, 10)}
CHECKED = {
    "2024-05-14:MD:president:republican",
    "2024-05-14:MD:house:3:democratic",
    "2024-05-07:IN:governor:republican",
    "2024-03-05:AL:house:3:republican",
    "2024-03-05:AL:house:4:republican",
    "2024-06-04:NJ:president:republican",
    "2024-06-25:NY:house:1:democratic",
    "2024-06-25:NY:house:10:democratic",
    "2024-06-25:NY:house:14:democratic",
    "2024-06-04:MT:governor:democratic",
    "2024-03-05:VT:president:democratic",
    "2024-08-20:AK:house:0",
}
PARTIES = {
    "d": "democratic",
    "dem": "democratic",
    "democrat": "democratic",
    "democratic": "democratic",
    "democratic party": "democratic",
    "r": "republican",
    "rep": "republican",
    "republican": "republican",
    "republican party": "republican",
    "lib": "libertarian",
    "lbn": "libertarian",
    "libertarian": "libertarian",
    "dfl": "minnesota-democratic-farmer-labor",
    "grn": "green",
    "green": "green",
    "legal marijuana now": "legal-marijuana-now",
    "working families": "working-families",
}
SKIPPED = r"(?i)^(registered voters|ballots cast|total ballots|times (blank|over|under) voted|total ?votes|under ?votes?|over ?votes?|no candidate qualified|blank|void)\b"
COLUMNS = ("county", "precinct", "office", "district", "party", "candidate", "votes")


def search(state: str) -> str:
    return elstats.SEARCH.format(tenant=elstats.TENANTS[state][0]) + urlencode(
        {
            "search": json.dumps(
                {
                    "global": {"years": {"from": 2024, "to": 2024}},
                    "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
                    "contests": {
                        "candidates": [],
                        "divisions": [],
                        "offices": [
                            {"free": office}
                            for office in (
                                "U.S. House",
                                "U.S. Senate",
                                "United States",
                                "Congress",
                                "President",
                                "Governor",
                            )
                        ],
                    },
                    "specialElectionsOnly": False,
                    "voterStats": False,
                    "stages": [],
                },
                separators=(",", ":"),
            )
        }
    )


def picks(fips: dict[str, dict[str, str]]) -> list[tuple[str, str, str]]:
    chosen: list[tuple[str, str, str]] = []
    for state, commit in OPENELECTIONS.items():
        tree = json.loads(
            raw.cached(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit)).path.read_text()
        )
        found: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for entry in tree["tree"]:
            name = entry["path"].rsplit("/", 1)[-1]
            if not (
                entry["type"] == "blob"
                and entry["path"].startswith("2024/")
                and re.fullmatch(rf"2024\d{{4}}__{state.lower()}__[\w-]*primary[\w-]*\.csv", name)
            ):
                continue
            middle = re.search(r"primary__([a-z_]+)__precinct\.csv$", name)
            grain = (
                "county"
                if re.search(r"__(county|county-level|town)\.csv$", name)
                else "part"
                if "/counties/" in entry["path"] or (middle and re.sub(r"[^a-z]", "", middle[1]) in fips[state])
                else "precinct"
            )
            found[name[:8]][grain].append(entry["path"])
        for _, files in sorted(found.items()):
            precincts = files["precinct"] or (
                files["part"] if len(files["part"]) == len(fips[state]) or state in PARTIAL else []
            )
            chosen.extend((state, "county", path) for path in files["county"])
            chosen.extend((state, "precinct", path) for path in precincts)
    return chosen


def fetch() -> None:
    raw.fetch(SOURCE, CALIFORNIA, suffix=".xlsx")
    raw.fetch(SOURCE, CA_PRESIDENT, suffix=".xlsx")
    raw.fetch(SOURCE, NORTH_CAROLINA, suffix=".zip")
    for county in range(1, 25):
        for party in ("Democratic", "Republican"):
            raw.fetch(SOURCE, MARYLAND.format(county, party), suffix=".csv")
    for name in ("ushousepct", "ussenatepct", "PrctTbl"):
        raw.fetch(SOURCE, MINNESOTA.format(name), suffix=".txt")
    for name in ("PNPPrecinct", "PrctTbl"):
        raw.fetch(SOURCE, MN_PRESIDENT.format(name), suffix=".txt")
    for day in ("20240312", "20240806"):
        raw.fetch(SOURCE, WASHINGTON.format(day), suffix=".csv")
    for category in IN_CATEGORIES:
        raw.fetch(SOURCE, INDIANA.format(category), suffix=".json")
    for url in ALABAMA:
        raw.fetch(SOURCE, url, suffix=".zip")
    for path in ME_FILES:
        raw.fetch(SOURCE, MAINE.format(path), suffix=".xlsx")
    raw.fetch(SOURCE, IOWA, suffix=".zip")
    for url in SOUTH_CAROLINA:
        raw.fetch(SOURCE, url, suffix=".zip")
    for name in NJ_FILES:
        raw.fetch(SOURCE, NEW_JERSEY.format(name), suffix=".pdf")
    raw.fetch(SOURCE, NEBRASKA, suffix=".pdf")
    for url in OHIO:
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for day in ("20240319", "20240820"):
        raw.fetch(SOURCE, FLORIDA.format(day), suffix=".txt")
    raw.fetch(SOURCE, OREGON, suffix=".pdf")
    raw.fetch(SOURCE, MISSOURI, suffix=".pdf")
    for name in ("media", "summary"):
        raw.fetch(SOURCE, HAWAII.format(name), suffix=".txt")
    raw.fetch(SOURCE, UTAH, suffix=".json")
    raw.fetch(SOURCE, KENTUCKY, suffix=".pdf")
    raw.fetch(SOURCE, ARKANSAS.format("GetContestResults", "&contestType=FED"), suffix=".json")
    raw.fetch(
        SOURCE, COLUMBIA.format("Downloads/Reports/June_4_2024_Primary_Election_Certified_Results.csv"), suffix=".csv"
    )
    raw.fetch(SOURCE, COLUMBIA.format("cityWide/getCityWide/2024-Primary-Election"), suffix=".json")
    raw.fetch(SOURCE, LOUISIANA.format("RacesCandidates_Multiparish"), suffix=".json")
    for race in LA_RACES:
        raw.fetch(SOURCE, LOUISIANA.format(f"VotesRaceByParish/Votes_{race}"), suffix=".json")
        for parish in range(1, 65):
            raw.fetch(SOURCE, LOUISIANA.format(f"VotesRaceByPrecinct/Votes_{race}_{parish:02d}"), suffix=".json")
    raw.fetch(SOURCE, MS_RUNOFF, suffix=".pdf")
    for url in WISCONSIN:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in NEW_YORK:
        raw.fetch(SOURCE, url, suffix=".xlsx")
    raw.fetch(SOURCE, MONTANA, suffix=".pdf")
    raw.fetch(SOURCE, OKLAHOMA, suffix=".pdf")
    raw.fetch(SOURCE, CLARK, suffix=".pdf")
    raw.fetch(SOURCE, VERMONT, suffix=".pdf")
    raw.fetch(SOURCE, ALASKA, suffix=".csv")
    for url in ARIZONA:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for place in json.loads(raw.fetch(SOURCE, NC_RUNOFF.format("county"), suffix=".json").path.read_text()):
        raw.fetch(SOURCE, NC_RUNOFF.format(f"results_{place['cid']}"), suffix=".json")
    for url in (ILLINOIS, IL_TOTALS):
        raw.fetch(SOURCE, url, suffix=".txt")
    for office, district in PA_OFFICES:
        raw.fetch(SOURCE, PENNSYLVANIA.format(office, district), suffix=".json")
    raw.fetch(SOURCE, ARKANSAS.format("GetContestSearchList", ""), suffix=".json")
    for suffix in ("html", "csv"):
        raw.fetch(SOURCE, DELAWARE.format(suffix), suffix=f".{suffix}")
    raw.fetch(SOURCE, SUMMIT, suffix=".pdf")
    for package in TEXAS:
        for resource in json.loads(raw.fetch(SOURCE, TED.format(package), suffix=".json").path.read_text())["result"][
            "resources"
        ]:
            if resource["name"].startswith(("U.S.", "President")):
                raw.fetch(SOURCE, resource["url"], suffix=".csv")
    raw.fetch(SOURCE, KANSAS, suffix=".xlsx")
    for suffix in ("", "-by-County"):
        raw.fetch(SOURCE, KS_PRESIDENT.format(suffix), suffix=".pdf")
    for listing in MASSACHUSETTS:
        for election, *_ in elstats.MA_ROW.findall(raw.fetch(SOURCE, listing, suffix=".html").path.read_text()):
            raw.fetch(SOURCE, f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:1/", suffix=".csv")
    for state in ELECTIONSTATS:
        raw.fetch(SOURCE, search(state), suffix=".csv")
    for state, commit in OPENELECTIONS.items():
        raw.fetch(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit), suffix=".json")
    with connect() as conn:
        fips = openelections.counties(conn)
    for state, _, path in picks(fips):
        raw.fetch(
            SOURCE,
            openelections.REPOSITORY.format(state=state.lower(), commit=OPENELECTIONS[state], path=path),
            suffix=".csv",
        )


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
        towns = {
            (state, re.sub(r"[^a-z]", "", town.lower())): county
            for state, county, town in conn.execute(
                """select distinct split_part(code, ':', 2), split_part(code, ':', 3), split_part(code, ':', 4)
                   from reporting_unit where scheme_code = 'us.precinct' and code ~ '^2024:(CT|RI):[0-9]{5}:'"""
            ).fetchall()
        }
        hawaii = dict(
            conn.execute(
                """select distinct split_part(split_part(code, ':', 5), '-', 1), split_part(code, ':', 3)
                   from reporting_unit where scheme_code = 'us.precinct' and code like '2024:HI:%'"""
            ).fetchall()
        )
        delaware = dict(
            conn.execute(
                """select split_part(code, ':', 5), split_part(code, ':', 3)
                   from reporting_unit where scheme_code = 'us.precinct' and code like '2024:DE:%'"""
            ).fetchall()
        )
    rows: list[tuple[str, date, str, int | None, str | None, str, str, str | None, str, bool, int, str]] = []
    sheet = (
        fastexcel.read_excel(raw.cached(SOURCE, CALIFORNIA).path)
        .load_sheet(0, header_row=None, dtypes="string")
        .to_polars()
    )
    lines = list(sheet.iter_rows())
    district = 0
    names: list[tuple[str, str, bool]] = []
    counted: list[int] = []
    for index, (label, *cells) in enumerate(lines):
        if label is not None and (found := re.fullmatch(r"(\d+)\w\w Congressional District", label.strip())):
            district = int(found[1])
        elif label is None and any(cells) and index + 1 < len(lines) and lines[index + 1][0] is None:
            names = [
                (" ".join(name.split()), party.removesuffix(" (W/I)"), party.endswith("(W/I)"))
                for name, party in zip(cells, lines[index + 1][1:], strict=True)
                if name is not None and party is not None
            ]
            counted = [0] * len(names)
        elif label == "District Totals":
            printed = [int(cell) for cell in cells[: len(names)]]
            if printed != counted:
                raise ValueError(f"CA {district}: counties sum to {counted}, the sheet prints {printed}")
        elif label is not None and label.strip() != "Percent" and names:
            county = fips["CA"][re.sub(r"[^a-z]", "", label.lower())]
            for position, ((name, _, write_in), cell) in enumerate(zip(names, cells, strict=False)):
                counted[position] += int(cell or 0)
                rows.append(
                    (
                        "CA",
                        date(2024, 3, 5),
                        "house",
                        district,
                        None,
                        "county",
                        county,
                        None,
                        name,
                        write_in,
                        int(cell or 0),
                        CALIFORNIA,
                    )
                )
    lines = list(
        fastexcel.read_excel(raw.cached(SOURCE, CA_PRESIDENT).path)
        .load_sheet(0, header_row=None, dtypes="string")
        .to_polars()
        .iter_rows()
    )
    nominees = [
        (
            " ".join(str(name).split()).removesuffix("*"),
            {"DEM": "democratic", "REP": "republican"}.get(party.split()[0]),
            "(W/I)" in party,
        )
        for name, party in zip(lines[0][1:], lines[1][1:], strict=True)
    ]
    counted = [0] * len(nominees)
    for label, *cells in lines[2:]:
        if label is None or label.strip() == "Percent":
            continue
        if label == "State Totals":
            if [int(cell) for cell in cells] != counted:
                raise ValueError(f"CA president: the counties sum to {counted}, the sheet prints {cells}")
            continue
        county = fips["CA"][re.sub(r"[^a-z]", "", label.lower())]
        for position, ((name, party, write_in), cell) in enumerate(zip(nominees, cells, strict=True)):
            counted[position] += int(cell)
            if party is not None:
                rows.append(
                    (
                        "CA",
                        date(2024, 3, 5),
                        "president",
                        None,
                        party,
                        "county",
                        county,
                        None,
                        name,
                        write_in,
                        int(cell),
                        CA_PRESIDENT,
                    )
                )
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in([*ELECTIONSTATS, "MA"]))
    places: dict[str, dict[str, str]] = defaultdict(dict)
    for state, geoid, name in (
        gazetteer.sort(pl.col("NAME").str.contains(r" (gore|grant|location|purchase|UT)$"))
        .select("USPS", "GEOID", "NAME")
        .iter_rows()
    ):
        places[state].setdefault(elstats.plain(name), geoid[:5])
        places[state].setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    for (state, name), code in elstats.FORMER.items():
        places[state].setdefault(name, code)
    texas = {
        resource["url"]: resource["name"]
        for package in TEXAS
        for resource in json.loads(raw.cached(SOURCE, TED.format(package)).path.read_text())["result"]["resources"]
        if resource["name"].startswith(("U.S.", "President"))
    }
    frames = [
        pl.DataFrame(
            rows,
            schema={
                "state": pl.String,
                "day": pl.Date,
                "office": pl.String,
                "district": pl.Int64,
                "party": pl.String,
                "grain": pl.String,
                "county": pl.String,
                "precinct": pl.String,
                "candidate": pl.String,
                "write_in": pl.Boolean,
                "votes": pl.Int64,
                "url": pl.String,
            },
            orient="row",
        )
    ]
    for state, grain, url in [
        ("NC", "precinct", NORTH_CAROLINA),
        ("MN", "precinct", MINNESOTA.format("ushousepct")),
        ("MN", "precinct", MINNESOTA.format("ussenatepct")),
        ("MN", "precinct", MN_PRESIDENT.format("PNPPrecinct")),
        ("WA", "county", WASHINGTON.format("20240312")),
        ("WA", "county", WASHINGTON.format("20240806")),
        *(("IN", "county", INDIANA.format(category)) for category in IN_CATEGORIES),
        *(("AL", "precinct", url) for url in ALABAMA),
        *(("ME", "precinct", MAINE.format(path)) for path in ME_FILES),
        ("IA", "county", IOWA),
        *(("SC", "county", url) for url in SOUTH_CAROLINA),
        *(("NJ", "county", NEW_JERSEY.format(name)) for name in NJ_FILES),
        ("NE", "county", NEBRASKA),
        *(("TX", "precinct", url) for url in texas),
        *(("OH", "precinct", url) for url in OHIO),
        *(("FL", "county", FLORIDA.format(day)) for day in ("20240319", "20240820")),
        ("OR", "county", OREGON),
        ("MO", "county", MISSOURI),
        ("HI", "precinct", HAWAII.format("media")),
        ("UT", "county", UTAH),
        ("DE", "precinct", DELAWARE.format("html")),
        ("KY", "county", KENTUCKY),
        ("AR", "county", ARKANSAS.format("GetContestResults", "&contestType=FED")),
        *(("PA", "county", PENNSYLVANIA.format(office, district)) for office, district in PA_OFFICES),
        ("IL", "county", ILLINOIS),
        *(
            ("NC", "county", NC_RUNOFF.format(f"results_{place['cid']}"))
            for place in json.loads(raw.cached(SOURCE, NC_RUNOFF.format("county")).path.read_text())
            if place["cid"] != "0"
        ),
        ("MS", "county", MS_RUNOFF),
        *(("WI", "county", url) for url in WISCONSIN),
        *(("NY", "county", url) for url in NEW_YORK),
        ("MT", "county", MONTANA),
        ("OK", "county", OKLAHOMA),
        ("NV", "county", CLARK),
        ("VT", "county", VERMONT),
        ("AK", "precinct", ALASKA),
        *(("AZ", "county", url) for url in ARIZONA),
        *(
            ("LA", "precinct", LOUISIANA.format(f"VotesRaceByPrecinct/Votes_{race}_{parish:02d}"))
            for race in LA_RACES
            for parish in range(1, 65)
        ),
        ("DC", "precinct", COLUMBIA.format("Downloads/Reports/June_4_2024_Primary_Election_Certified_Results.csv")),
        ("UT", "county", SUMMIT),
        ("KS", "precinct", KANSAS),
        ("KS", "county", KS_PRESIDENT.format("-by-County")),
        *(
            ("MA", "precinct", f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:1/")
            for election in dict.fromkeys(
                election
                for listing in MASSACHUSETTS
                for election, _, _, _ in elstats.MA_ROW.findall(raw.cached(SOURCE, listing).path.read_text())
            )
        ),
        *(
            ("MD", "precinct", MARYLAND.format(county, party))
            for county in range(1, 25)
            for party in ("Democratic", "Republican")
        ),
        *(
            (state, grain, openelections.REPOSITORY.format(state=state.lower(), commit=OPENELECTIONS[state], path=path))
            for state, grain, path in picks(fips)
        ),
    ]:
        if url == NORTH_CAROLINA:
            with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                text = archive.read(archive.namelist()[0])
            contest = pl.col("Contest Name").str.strip_chars()
            frame = pl.read_csv(
                io.BytesIO(text), separator="\t", infer_schema=False, truncate_ragged_lines=True
            ).select(
                county="County",
                precinct="Precinct",
                office=contest.str.replace(r"^NC ", "").str.replace(r"\s*\(\w+\)$", ""),
                district=contest.str.extract(r"DISTRICT (\d+)"),
                party=contest.str.extract(r"\((\w+)\)$"),
                candidate="Choice",
                votes="Total Votes",
            )
        elif state == "WA":
            race = pl.col("Race").str.strip_chars()
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False).unique(
                    subset=["County", "Race", "Candidate"], keep="first", maintain_order=True
                )
            ).select(
                county="County",
                office=race.str.replace(r"^Washington State ", "").str.replace(r" - \w+ Party$", ""),
                district=race.str.extract(r"(?i)congressional district (\d+)"),
                party=race.str.extract(r" - (\w+) Party$"),
                candidate="Candidate",
                votes="Votes",
            )
        elif state == "AL":
            sheets: list[pl.DataFrame] = []
            with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                for name in archive.namelist():
                    sheet = fastexcel.read_excel(archive.read(name)).load_sheet(0, header_row=None).to_polars()
                    header = [str(cell).strip() for cell in sheet.row(0)]
                    columns = [
                        f"{cell} ({header[:index].count(cell) + 1})" if cell in header[:index] else cell
                        for index, cell in enumerate(header)
                    ]
                    sheets.append(
                        sheet.slice(1)
                        .rename(dict(zip(sheet.columns, columns, strict=True)))
                        .unpivot(columns[3:], index=columns[:3], variable_name="precinct", value_name="votes")
                        .select(
                            county=pl.lit(re.sub(r"^2024-Primary-(Runoff-)?|\.xls$", "", name)),
                            precinct="precinct",
                            office=pl.col("Contest Title").str.strip_chars(),
                            district=pl.col("Contest Title").str.strip_chars(),
                            party="Party",
                            candidate="Candidate",
                            votes="votes",
                        )
                    )
            frame = pl.concat(sheets)
        elif url == KS_PRESIDENT.format("-by-County"):
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            cells = []
            place = ""
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    if re.sub(r"[^a-z]", "", text.lower()) in fips["KS"]:
                        place = text
                    elif found := re.fullmatch(r"([DR])-(.+?)(?: ([\d,]+))? [\d.]+%", text):
                        box = (
                            words[-2][1]
                            if found[3]
                            else (words[0][1][0], words[0][1][1], words[-1][1][2], words[-1][1][3])
                        )
                        cells.append(
                            {
                                "county": place,
                                "office": "President",
                                "party": found[1],
                                "candidate": found[2],
                                "votes": found[3] or "0",
                                "page": page,
                                "x0": box[0],
                                "y0": box[1],
                                "x1": box[2],
                                "y1": box[3],
                            }
                        )
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
            summed = frame.group_by("party", "candidate").agg(
                pl.col("votes").str.replace_all(",", "").cast(pl.Int64).sum()
            )
            printed = {
                (party, name): int(votes.replace(",", ""))
                for party, name, votes in re.findall(
                    r"(?m)^\s*([DR])-(.+?)\s+([\d,]+)\s+[\d.]+%$",
                    subprocess.run(
                        ["pdftotext", "-layout", str(raw.cached(SOURCE, KS_PRESIDENT.format("")).path), "-"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout,
                )
            }
            if {(party, name): votes for party, name, votes in summed.iter_rows()} != printed:
                raise ValueError(f"KS president: the counties sum to {summed.rows()}, the state totals are {printed}")
        elif state == "KS":
            workbook = fastexcel.read_excel(raw.cached(SOURCE, url).path)
            sheets = [
                workbook.load_sheet(workbook.sheet_names[0], dtypes="string")
                .to_polars()
                .select(
                    county="County",
                    precinct="Precinct",
                    office="Race",
                    district="Race",
                    party="Party",
                    candidate=pl.col("Candidate").str.replace(r"^([^,]+), (.+)$", "$2 $1"),
                    votes="Votes",
                )
            ]
            for name in workbook.sheet_names[1:]:
                table = workbook.load_sheet(name, header_row=None, dtypes="string").to_polars()
                columns = table.columns[2:]
                label = pl.col(table.columns[1])
                total = table.filter(label == "COUNTY TOTALS")
                table = (
                    table.slice(3)
                    .filter(label.is_not_null(), ~label.str.contains("TOTAL"))
                    .with_columns(pl.col(columns).str.replace(r"(?i)^redacted.*", "").replace("", None))
                )
                if table.select(pl.any_horizontal(pl.col(columns).is_null())).to_series().any():
                    table = pl.concat(
                        [
                            table,
                            pl.DataFrame(
                                {
                                    table.columns[1]: ["Redacted precincts (county total less the reported ones)"],
                                    **{
                                        column: [str(int(total[column][0]) - table[column].cast(pl.Int64).sum())]
                                        for column in columns
                                    },
                                }
                            ),
                        ],
                        how="diagonal",
                    )
                for column in columns:
                    if table[column].cast(pl.Int64).sum() != int(total[column][0]):
                        raise ValueError(f"Kansas' {name} precincts do not add up to the county total")
                heading = workbook.load_sheet(name, header_row=None, dtypes="string", n_rows=3).to_polars()
                sheets.append(
                    table.unpivot(columns, index=table.columns[1], variable_name="column", value_name="votes").select(
                        county=pl.lit(name.title()),
                        precinct=label,
                        office=pl.col("column").replace_strict({column: heading[column][0] for column in columns}),
                        district=pl.col("column").replace_strict({column: heading[column][0] for column in columns}),
                        party=pl.col("column").replace_strict({column: heading[column][1] for column in columns}),
                        candidate=pl.col("column").replace_strict({column: heading[column][2] for column in columns}),
                        votes="votes",
                    )
                )
            frame = pl.concat(sheets)
        elif url.endswith("detailxml.zip"):
            with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            for choice in root.iter("Choice"):
                if sum(int(str(place.get("votes"))) for place in choice.iter("County")) != int(
                    str(choice.get("totalVotes"))
                ):
                    raise ValueError(f"{url}: the counties do not add up to {choice.get('text')}'s total")
            frame = pl.DataFrame(
                [
                    {
                        "county": place.get("name"),
                        "office": re.sub(r"^(DEM|REP) ", "", str(contest.get("text"))),
                        "district": contest.get("text"),
                        "party": re.sub(r"^(DEM|REP) .*$|^.* - (\w+)\.?$", r"\1\2", str(contest.get("text"))),
                        "candidate": str(choice.get("text")).removesuffix(" - WITHDREW"),
                        "votes": place.get("votes"),
                    }
                    for contest in root.iter("Contest")
                    for choice in contest.iter("Choice")
                    for kind in choice.iter("VoteType")
                    for place in kind.iter("County")
                ],
                schema=dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
            )
        elif state == "NJ":
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            cells: list[dict[str, object]] = []
            seat: int | None = None
            candidate: str | None = None
            opened = party = ""
            naming = False
            tallied = 0
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                lines: list[list[tuple[str, scan.Box]]] = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][1], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][1] - box[1]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                heading = next(
                    (words for words in lines if {"Name", "Address", "Party", "Tally"} <= {word for word, _ in words}),
                    None,
                )
                if heading is None:
                    continue
                columns = {word: box[0] for word, box in heading}
                for words in lines[lines.index(heading) + 1 :]:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    named = next(
                        (
                            word
                            for word, box in words
                            if word in ("Democratic", "Republican") and abs(box[0] - columns["Party"]) < 40
                        ),
                        None,
                    )
                    first = re.sub(r"[^a-z]", "", " ".join(word for word, _ in words[:2]).lower())
                    place = (
                        fips["NJ"].get(first) or fips["NJ"].get(re.sub(r"[^a-z]", "", words[0][0].lower()))
                        if abs(words[0][1][0] - columns["County"]) < 40
                        else None
                    )
                    if found := re.match(r"(\w+) Congressional District:", text):
                        seat = ORDINALS.index(found[1]) + 1
                    elif named is not None:
                        heading_name = " ".join(word for word, box in words if box[2] < columns["Address"])
                        if candidate is not None and heading_name == opened:
                            continue
                        if candidate is not None:
                            raise ValueError(f"NJ {url}: {candidate} has no total")
                        candidate = opened = heading_name
                        party = named
                        naming = True
                        tallied = 0
                    elif candidate is not None and (found := re.fullmatch(r"Total ([\d,]+)", text)):
                        if int(found[1].replace(",", "")) != tallied:
                            raise ValueError(f"NJ {url}: {candidate}'s counties sum to {tallied}, not {found[1]}")
                        candidate = None
                    elif candidate is not None and place is not None and re.fullmatch(r"[\d,]+", words[-1][0]):
                        naming = False
                        tallied += int(words[-1][0].replace(",", ""))
                        cells.append(
                            {
                                "county": place,
                                "office": NJ_FILES[url.split("results-")[1].removesuffix(".pdf")],
                                "district": None if seat is None else str(seat),
                                "party": party,
                                "candidate": candidate,
                                "votes": words[-1][0],
                                "page": page,
                                "x0": words[-1][1][0],
                                "y0": words[-1][1][1],
                                "x1": words[-1][1][2],
                                "y1": words[-1][1][3],
                            }
                        )
                    elif candidate is not None and naming and words[0][1][2] < columns["Address"]:
                        candidate += " " + " ".join(word for word, box in words if box[2] < columns["Address"])
            if candidate is not None:
                raise ValueError(f"NJ {url}: {candidate} has no total")
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            ).with_columns(candidate=pl.col("candidate").str.replace_all(r"\(w\)|\*", "").str.strip_chars())
        elif state == "AR":
            listed = json.loads(raw.cached(SOURCE, ARKANSAS.format("GetContestSearchList", "")).path.read_text())
            cells = []
            for number, contest in json.loads(raw.cached(SOURCE, url).path.read_text())["response"]["contests"].items():
                heading = listed["response"]["contests"][number]["contestName"]
                labels = listed["response"]["contests"][number]["choices"]
                summed: dict[str, int] = defaultdict(int)
                for place in contest["locations"].values():
                    for choice in place["choices"]:
                        summed[choice["choiceID"]] += choice["totalVotes"]
                        cells.append(
                            {
                                "county": place["locationId"],
                                "office": heading.rsplit(" - ", 1)[0],
                                "district": heading.rsplit(" - ", 1)[0],
                                "party": heading.rsplit(" - ", 1)[1],
                                "candidate": re.sub(r"^(Congressman|Senator) ", "", labels[choice["choiceID"]]["name"]),
                                "votes": str(choice["totalVotes"]),
                            }
                        )
                if any(summed[choice["choiceID"]] != choice["totalVotes"] for choice in contest["choices"]):
                    raise ValueError(f"AR: the counties do not add up to {heading}'s totals")
            frame = pl.DataFrame(cells, schema=dict.fromkeys(COLUMNS, pl.String))
        elif url.startswith(PENNSYLVANIA.split("?")[0]):
            cells = []
            for heading, blocks in json.loads(json.loads(raw.cached(SOURCE, url).path.read_text()))["Election"].items():
                for block in blocks:
                    for county, bands in block.items():
                        for band in bands:
                            for side, entries in band.items():
                                for entry in entries:
                                    if int(entry["Votes"]) != sum(
                                        int(entry[f"{kind}Votes"]) for kind in ("ElectionDay", "MailIn", "Provisional")
                                    ):
                                        raise ValueError(
                                            f"PA {heading}: {entry['CandidateName']}'s votes do not add up"
                                        )
                                    cells.append(
                                        {
                                            "county": county,
                                            "office": "President"
                                            if "officeId=1&" in url
                                            else "U.S. Senate"
                                            if "officeId=2&" in url
                                            else "U.S. House",
                                            "district": heading,
                                            "party": side,
                                            "candidate": re.sub(r"\(\w+\)$", "", entry["CandidateName"])
                                            .strip()
                                            .title(),
                                            "votes": entry["Votes"],
                                        }
                                    )
            frame = pl.DataFrame(cells, schema=dict.fromkeys(COLUMNS, pl.String))
        elif url.startswith(NC_RUNOFF.removesuffix("{}.txt")):
            number = re.findall(r"results_(\d+)\.txt", url)[0]
            name = next(
                place["cnm"]
                for place in json.loads(raw.cached(SOURCE, NC_RUNOFF.format("county")).path.read_text())
                if place["cid"] == number
            )
            listed = [entry for entry in json.loads(raw.cached(SOURCE, url).path.read_text()) if entry["ogl"] == "FED"]
            for entry in listed:
                if entry["prt"] != entry["ptl"] or int(entry["vct"]) != sum(
                    int(entry[kind]) for kind in ("evc", "ovc", "avc", "pvc")
                ):
                    raise ValueError(f"NC runoff {name}: {entry['bnm']}'s returns are incomplete or do not add up")
            frame = pl.DataFrame(
                [
                    {
                        "county": name,
                        "office": entry["cnm"],
                        "district": entry["cnm"],
                        "party": entry["pty"],
                        "candidate": entry["bnm"],
                        "votes": entry["vct"],
                    }
                    for entry in listed
                ],
                schema=dict.fromkeys(COLUMNS, pl.String),
            )
        elif state == "NY":
            book = fastexcel.read_excel(raw.cached(SOURCE, url).path)
            cells = []
            for name in book.sheet_names:
                if name not in ("County DEM 2024", "Rep Pres by County") and not re.fullmatch(
                    r"\d+\w\w CD \w+ Primary", name
                ):
                    continue
                grid = [list(row) for row in book.load_sheet(name, header_row=None).to_polars().iter_rows()]
                if url == NEW_YORK[2]:
                    titled = re.match(
                        r"Representative in Congress (\d+)\w\w Congressional District - (.+?) - ", grid[0][0]
                    )
                    if titled is None:
                        raise ValueError(f"NY {name}: {grid[0][0]}")
                    heads = [
                        re.sub(r"^Part of | County Vote Results$", "", " ".join(str(cell).split()))
                        for cell in grid[1][1:-2]
                    ]
                    added = [0] * len(heads)
                    for row in grid[2:]:
                        counts = [int(cell or 0) for cell in row[1:-2]]
                        if row[0] == "Total Votes by County":
                            if counts != added:
                                raise ValueError(f"NY {name}: counties sum to {added}, not {counts}")
                            break
                        if sum(counts) != int(row[-2]):
                            raise ValueError(f"NY {name}: {row[0]} sums to {sum(counts)}, not {row[-2]}")
                        added = [total + count for total, count in zip(added, counts, strict=True)]
                        cells += [
                            {
                                "county": head,
                                "office": "Representative in Congress",
                                "district": titled[1],
                                "party": titled[2],
                                "candidate": re.sub(r" \(\w+\)$", "", row[0]),
                                "write_in": "1" if row[0] == "Scattering" else "0",
                                "votes": str(count),
                            }
                            for head, count in zip(heads, counts, strict=True)
                        ]
                    continue
                top = next(index for index, row in enumerate(grid) if row[0] == "County")
                added = [0] * (len(grid[top]) - 2)
                for row in grid[top + 1 :]:
                    if row[0] is None or row[0] in ("Total", "Total NYC"):
                        continue
                    counts = [int(cell or 0) for cell in row[1:-1]]
                    if row[0] in ("Total Votes by Candidate", "Grand Total"):
                        if counts != added:
                            raise ValueError(f"NY {name}: counties sum to {added}, not {counts}")
                        break
                    if sum(counts) != int(row[-1]):
                        raise ValueError(f"NY {name}: {row[0]} sums to {sum(counts)}, not {row[-1]}")
                    added = [total + count for total, count in zip(added, counts, strict=True)]
                    cells += [
                        {
                            "county": row[0],
                            "office": "President",
                            "party": "Democratic" if "DEM" in name else "Republican",
                            "candidate": re.sub(r" \(\w+\)$", "", head),
                            "votes": str(count),
                        }
                        for head, count in zip(grid[top][1:-1], counts, strict=True)
                    ]
            frame = pl.DataFrame(cells, schema=dict.fromkeys((*COLUMNS, "write_in"), pl.String))
        elif url in ARIZONA:
            artifact = raw.cached(SOURCE, url)
            cells = []
            for page in (5,) if url == ARIZONA[0] else (1, 2, 3):
                cache = PARSED / "ocr" / f"{artifact.sha256}-{page}.tsv"
                upright = cache.with_suffix(".png")
                if not cache.exists():
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory() as folder:
                        Image.open(scan.rendered(artifact.path, page, Path(folder))).convert("L").transpose(
                            Image.Transpose.ROTATE_270
                        ).save(upright)
                    cache.write_text(
                        subprocess.run(
                            ["tesseract", str(upright), "stdout", "--psm", "6", "-l", "eng", "tsv"],
                            capture_output=True,
                            text=True,
                            check=True,
                        ).stdout
                    )
                read = list(csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE))
                width = int(read[0]["width"])
                grouped: dict[tuple[str, str, str], list[tuple[str, scan.Box]]] = {}
                for row in read:
                    if row["level"] == "5" and row["text"].strip():
                        left, top = int(row["left"]), int(row["top"])
                        grouped.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append(
                            (row["text"], (left, top, left + int(row["width"]), top + int(row["height"])))
                        )
                ordered = sorted(grouped.values(), key=lambda words: min(box[1] for _, box in words))
                header = next(words for words in ordered if any(word == "Maricopa" for word, _ in words))
                heads = {
                    county: next(box[2] for word, box in header if re.fullmatch(pattern, word))
                    for county, pattern in (
                        ("Apache", "Apache"),
                        ("Cochise", "Cochise"),
                        ("Coconino", "Coconino"),
                        ("Gila", "Gi.a"),
                        ("Graham", "Graham"),
                        ("Greenlee", "Greenlee"),
                        ("La Paz", "(La)?Paz"),
                        ("Maricopa", "Maricopa"),
                        ("Mohave", "Mohave"),
                        ("Navajo", "Navajo"),
                        ("Pima", "Pima"),
                        ("Pinal", "Pinal"),
                        ("Santa Cruz", "Cruz"),
                        ("Yavapai", "Yavapai"),
                        ("Yuma", "Yuma"),
                        ("TOTAL", r"TOTAL\.?"),
                    )
                }
                office = affiliation = seat = None
                for words in ordered[ordered.index(header) + 1 :]:
                    text = " ".join(word for word, _ in words)
                    if "U.S. Senator" in text:
                        office, seat = "U.S. Senate", None
                    elif found := re.search(r"Representative in Congress\W+District No\. (\d+)", text):
                        office, seat = "U.S. House", found[1]
                    elif "President of the United States" in text:
                        office, seat = "President", None
                    elif text in ("Republican", "Democratic"):
                        affiliation = text
                    elif re.match(r"State |Corporation|Superior|Justice|County|Precinct", text):
                        office = None
                    elif office is not None and any(abs(box[2] - heads["TOTAL"]) < 30 for _, box in words):
                        named = " ".join(
                            word
                            for word, box in words
                            if box[2] < heads["Apache"] - 120 and re.search(r"[A-Za-z]", word)
                        )
                        marked = re.match(r"[({](REP|DEM|GRN|LBT)[)}] ", named)
                        options: list[list[int | None]] = []
                        located: list[scan.Box] = []
                        reading: list[str] = []
                        for county, right in heads.items():
                            inside = [(word, box) for word, box in words if abs(box[2] - right) < 30]
                            reading.append("".join(word for word, _ in inside))
                            located.append(
                                (
                                    min((box[0] for _, box in inside), default=right - 110),
                                    min(box[1] for word, box in words if re.search(r"[A-Za-z0-9]", word)),
                                    right + 5,
                                    max(box[3] for word, box in words if re.search(r"[A-Za-z0-9]", word)),
                                )
                            )
                            if re.fullmatch(r"\d", reading[-1]) and inside[0][1][2] - inside[0][1][0] < 13:
                                options.append([1])
                            elif re.fullmatch(r"\d{1,3}(,\d{3})*", reading[-1]):
                                options.append([int(reading[-1].replace(",", ""))])
                            elif (
                                office == "U.S. House" and county != "TOTAL" and re.fullmatch(r"[—_~=–-]*", reading[-1])
                            ):
                                options.append([None])
                            else:
                                options.append([])
                        support = [Counter(choices) for choices in options]
                        if (
                            any(not choices for choices in options)
                            or sum(choices[0] or 0 for choices in options[:-1]) != options[-1][0]
                        ):
                            reread = [position for position, choices in enumerate(options) if choices != [None]]
                            for position, texts in zip(
                                reread,
                                scan.read(
                                    upright,
                                    [
                                        (
                                            (
                                                list(heads.values())[position] - 140,
                                                located[position][1] - 12,
                                                list(heads.values())[position] + 12,
                                                located[position][3] + 12,
                                            ),
                                            True,
                                        )
                                        for position in reread
                                    ],
                                    "eng",
                                ),
                                strict=True,
                            ):
                                for text in texts:
                                    found = scan.numbers(text)
                                    if found:
                                        support[position][max(found, key=found.__getitem__)] += 1
                                options[position] = list(
                                    dict.fromkeys(
                                        [
                                            *options[position],
                                            *(number for text in texts for number in scan.numbers(text)),
                                        ]
                                    )
                                ) or ([None] if office == "U.S. House" else [])
                        fits = [
                            combination
                            for combination in itertools.product(*options)
                            if combination[-1] is not None
                            and sum(value or 0 for value in combination[:-1]) == combination[-1]
                        ]
                        if not fits:
                            raise ValueError(f"AZ {url} page {page}: {named} reads {reading}, which never sums")
                        best = max(
                            sum(votes[value] for value, votes in zip(combination, support, strict=True))
                            for combination in fits
                        )
                        chosen = {
                            combination
                            for combination in fits
                            if sum(votes[value] for value, votes in zip(combination, support, strict=True)) == best
                        }
                        if len(chosen) != 1:
                            raise ValueError(f"AZ {url} page {page}: {named} reads {reading} {len(chosen)} ways")
                        cells += [
                            {
                                "county": county,
                                "office": office,
                                "district": seat,
                                "party": {
                                    "REP": "Republican",
                                    "DEM": "Democratic",
                                    "GRN": "Green",
                                    "LBT": "Libertarian",
                                }[marked[1]]
                                if marked
                                else affiliation,
                                "candidate": re.sub(
                                    r"\s*(\*|\((?i:Write-In)\)|\(WITHDRAWN\))\s*",
                                    " ",
                                    re.sub(r"\b([A-Z]), ", r"\1. ", named[marked.end() if marked else 0 :]),
                                ).strip(),
                                "write_in": "1" if re.search(r"(?i)\(write-in\)", named) else "0",
                                "votes": str(value),
                                "page": page,
                                "x0": box[1],
                                "y0": width - box[2],
                                "x1": box[3],
                                "y1": width - box[0],
                            }
                            for county, value, box in zip(heads, next(iter(chosen)), located, strict=True)
                            if county != "TOTAL" and value is not None
                        ]
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys((*COLUMNS, "write_in"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif url == CLARK:
            artifact = raw.cached(SOURCE, url)
            cells = []
            for page in (3, 4):
                cache = PARSED / "ocr" / f"{artifact.sha256}-{page}.tsv"
                cache.parent.mkdir(parents=True, exist_ok=True)
                grouped: dict[tuple[int, int, int], list[tuple[str, scan.Box]]] = {}
                for word, box, line, _ in scan.words(artifact.path, page, cache):
                    grouped.setdefault(line, []).append((word, box))
                seat: str | None = None
                party = ""
                entries: list[tuple[str, int, scan.Box]] = []
                for words in sorted(grouped.values(), key=lambda words: min(box[1] for _, box in words)):
                    text = re.sub(r"^[^A-Za-z]+", "", " ".join(word for word, _ in words))
                    if found := re.match(r"(.+?) - (REP|DEM) \(Vote for", text):
                        titled = re.fullmatch(r"Representative in Congress District (\d)", found[1])
                        seat = titled[1] if titled else None
                        party = {"REP": "Republican", "DEM": "Democratic"}[found[2]]
                        entries = []
                    elif seat is None:
                        continue
                    elif found := re.fullmatch(r"Total Votes ([\d,]+)\W*", text):
                        if sum(votes for _, votes, _ in entries) != int(found[1].replace(",", "")):
                            raise ValueError(
                                f"NV Clark page {page}: district {seat} {party} does not sum to {found[1]}"
                            )
                        cells += [
                            {
                                "county": "32003",
                                "office": "U.S. House",
                                "district": seat,
                                "party": party,
                                "candidate": re.sub(r"^(.+?), (?:(Jr\.|Sr\.), )?(.+)$", r"\3 \1 \2", name).strip(),
                                "votes": str(votes),
                                "page": page,
                                "x0": box[0],
                                "y0": box[1],
                                "x1": box[2],
                                "y1": box[3],
                            }
                            for name, votes, box in entries
                        ]
                        seat = None
                    elif found := re.fullmatch(r"(.+?) \W*(?:REP|DEM) \W*([\d,]+) \W*[\d.]+%\W*", text):
                        box = next(box for word, box in words if re.fullmatch(r"\d{1,3}(,\d{3})*", word))
                        entries.append((re.sub(r"[|‘']+$", "", found[1]).strip(), int(found[2].replace(",", "")), box))
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(COLUMNS, pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif url == VERMONT:
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            candidates: dict[str, list[str]] = {}
            stated: dict[str, list[int]] = {}
            cells = []
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                lines = []
                for word, box in sorted(scan.layer(pdf, page), key=lambda item: (item[1][1], item[1][0])):
                    if lines and abs(lines[-1][0][1][1] - box[1]) < 12:
                        lines[-1].append((html.unescape(word), box))
                    else:
                        lines.append([(html.unescape(word), box)])
                texts = [" ".join(word for word, _ in sorted(line, key=lambda item: item[1][0])) for line in lines]
                if page == 1:
                    party = ""
                    for text in texts:
                        if found := re.fullmatch(r"\d (DEMOCRATIC|REPUBLICAN) Party", text):
                            party = found[1].title()
                            candidates[party] = []
                        elif party and (found := re.fullmatch(r"(.+?)\*? ([\d,]+)(?: [\d.]+%)?", text)):
                            if found[1] == "Total votes counted":
                                stated[party] = [
                                    int(re.fullmatch(r"(.+?)\*? ([\d,]+)(?: [\d.]+%)?", line)[2].replace(",", ""))
                                    for line in texts[
                                        texts.index(text) - len(candidates[party]) : texts.index(text) + 1
                                    ]
                                ]
                                party = ""
                            else:
                                candidates[party].append(found[1])
                    continue
                heading = next((text for text in texts if "STATEWIDE TOTALS" in text), None)
                if heading is None:
                    continue
                party = heading.split()[-1].title()
                names = candidates[party]
                rows = []
                for line in lines:
                    words = sorted(line, key=lambda item: item[1][0])
                    numbers = [(word, box) for word, box in words if re.fullmatch(r"[\d,]+", word)]
                    label = " ".join(word for word, _ in words if not re.fullmatch(r"[\d,]+", word))
                    if len(numbers) == len(names) + 1 and label:
                        counts = [int(word.replace(",", "")) for word, _ in numbers]
                        if sum(counts[:-1]) != counts[-1]:
                            raise ValueError(f"VT page {page}: {label} does not add up")
                        rows.append((label, counts, [box for _, box in numbers]))
                *counties, (label, total, _) = rows
                if label != "Statewide Totals" or len(counties) != 14:
                    raise ValueError(f"VT page {page}: expected 14 counties and the statewide totals")
                if [sum(row[1][column] for row in counties) for column in range(len(total))] != total:
                    raise ValueError(f"VT page {page}: {party} counties do not sum to the statewide totals")
                if total != stated[party]:
                    raise ValueError(f"VT page {page}: {party} totals differ from the summary on page 1")
                cells += [
                    {
                        "county": county,
                        "office": "President",
                        "party": party,
                        "candidate": name,
                        "votes": str(count),
                        "page": page,
                        "x0": box[0],
                        "y0": box[1],
                        "x1": box[2],
                        "y1": box[3],
                    }
                    for county, counts, boxes in counties
                    for name, count, box in zip(names, counts, boxes, strict=False)
                    if name not in ("Overvotes", "Blank votes")
                ]
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(COLUMNS, pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif url == OKLAHOMA:
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            cells = []
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                lines: list[list[tuple[str, scan.Box]]] = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][1], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][1] - box[1]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                district = ""
                party = ""
                entries: list[tuple[str, list[int], scan.Box]] = []
                for line in lines:
                    text = " ".join(word for word, _ in sorted(line, key=lambda item: item[1][0]))
                    if found := re.fullmatch(r"District: UNITED STATES CONGRESSIONAL DISTRICT (\d+)", text):
                        district = f"40{int(found[1]):02d}"
                    elif text in ("Libertarian", "Republican", "Democrat"):
                        party = {"Democrat": "Democratic"}.get(text, text)
                        entries = []
                    elif found := re.fullmatch(r"(\D*?) ?([\d,]+) ([\d,]+) ([\d,]+) ([\d,]+)(?: [\d.]+%)?", text):
                        counts = [int(found[position].replace(",", "")) for position in range(2, 6)]
                        if sum(counts[:3]) != counts[3]:
                            raise ValueError(f"OK page {page}: {text} does not add up")
                        if found[1]:
                            entries.append(
                                (
                                    found[1],
                                    counts,
                                    max(line, key=lambda item: item[1][0] if "%" not in item[0] else 0)[1],
                                )
                            )
                            continue
                        if [sum(entry[1][column] for entry in entries) for column in range(4)] != counts:
                            raise ValueError(f"OK page {page}: {party} in {district} does not sum to {text}")
                        cells += [
                            {
                                "county": district,
                                "office": "President",
                                "party": party,
                                "candidate": name.title(),
                                "votes": str(counts[3]),
                                "page": page,
                                "x0": box[0],
                                "y0": box[1],
                                "x1": box[2],
                                "y1": box[3],
                            }
                            for name, counts, box in entries
                        ]
                        entries = []
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(COLUMNS, pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif state == "MT":
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            rows: list[tuple[int, list[tuple[str, scan.Box]]]] = []
            for page in range(2, int(info.split("Pages:")[1].split()[0]) + 1):
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page) if 200 < box[1] < 3195),
                    key=lambda item: (item[1][1], item[1][0]),
                ):
                    if rows and rows[-1][0] == page and abs(rows[-1][1][0][1][1] - box[1]) < 12:
                        rows[-1][1].append((word, box))
                    else:
                        rows.append((page, [(word, box)]))
            cells = []
            header: list[tuple[str, scan.Box]] = []
            titled: re.Match[str] | None = None
            starts: list[int] = []
            candidates: list[str] = []
            entries: list[tuple[list[str], list[tuple[str, scan.Box]], int]] = []
            for index, (page, words) in enumerate(rows):
                numbers = [(word, box) for word, box in words if re.fullmatch(r"[\d,]+", word) and box[0] > 280]
                named = [word for word, box in words if box[0] < 280]
                if not (numbers and named):
                    if entries and not numbers and named:
                        entries[-1][0].extend(named)
                    else:
                        header += words
                    continue
                if header:
                    header.sort(key=lambda item: (item[1][1], item[1][0]))
                    cut = next(
                        position
                        for position, (word, _) in enumerate(header)
                        if word in ("Democratic", "Republican", "Green", "Libertarian", "Partisan")
                    )
                    closing = next(
                        later
                        for later in range(index, len(rows))
                        if [word for word, box in rows[later][1] if box[0] < 280] == ["Total"]
                    )
                    starts = sorted(
                        {
                            box[0]
                            for _, later in rows[index : closing + 1]
                            for word, box in later
                            if re.fullmatch(r"[\d,]+", word) and box[0] > 280
                        }
                    )
                    titled = re.fullmatch(
                        r"(PRESIDENT|UNITEDSTATESSENATOR|USREPRESENTATIVEDIST(\d)|GOVERNOR&LT\.GOVERNOR)"
                        r"(Democratic|Republican|Green|Libertarian)",
                        "".join(word for word, _ in header[: cut + 1]),
                    )
                    candidates = [
                        re.sub(
                            r"- ",
                            "-",
                            " ".join(
                                word
                                for word, box in header[cut + 1 :]
                                if max(start for start in starts if start <= box[0] + 10) == column
                            ),
                        ).title()
                        for column in starts
                    ]
                    header = []
                if [box[0] for _, box in numbers] != starts:
                    raise ValueError(
                        f"MT page {page}: {' '.join(named)} has numbers at {[box[0] for _, box in numbers]}"
                    )
                if named != ["Total"]:
                    entries.append((named, numbers, page))
                    continue
                for column, (word, _) in enumerate(numbers):
                    if sum(int(entry[1][column][0].replace(",", "")) for entry in entries) != int(
                        word.replace(",", "")
                    ):
                        raise ValueError(f"MT page {page}: {candidates[column]}'s counties do not sum to {word}")
                if titled is not None:
                    cells += [
                        {
                            "county": " ".join(county),
                            "office": {"PRESIDENT": "President", "UNITEDSTATESSENATOR": "U.S. Senate"}.get(
                                titled[1], "Governor" if titled[1].startswith("GOVERNOR") else "U.S. House"
                            ),
                            "district": titled[2],
                            "party": titled[3],
                            "candidate": candidate,
                            "votes": value,
                            "page": located,
                            "x0": box[0],
                            "y0": box[1],
                            "x1": box[2],
                            "y1": box[3],
                        }
                        for county, counted, located in entries
                        for candidate, (value, box) in zip(candidates, counted, strict=True)
                    ]
                entries = []
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(COLUMNS, pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif state == "WI":
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            cells = []
            heading = ""
            summed: dict[tuple[str, str], int] = defaultdict(int)
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                rows: list[list[tuple[str, scan.Box]]] = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][1], item[1][0]),
                ):
                    if rows and abs(rows[-1][0][1][1] - box[1]) < 12:
                        rows[-1].append((word, box))
                    else:
                        rows.append([(word, box)])
                for words in rows:
                    words.sort(key=lambda item: item[1][0])
                texts = [" ".join(word for word, _ in words) for words in rows]
                first = next(
                    (
                        index
                        for index, words in enumerate(rows)
                        if index > 3
                        and words[0][0] != "Report"
                        and sum(bool(re.fullmatch(r"[\d,]+", word)) for word, _ in words) > 1
                    ),
                    None,
                )
                if first is None:
                    continue
                marked = next(
                    index
                    for index, words in enumerate(rows[3:first], start=3)
                    if "Total" in texts[index].split() or all(re.fullmatch(r"[A-Z]{3}", word) for word, _ in words)
                )
                if marked > 3:
                    heading = " ".join(texts[3:marked])
                found = re.match(
                    r"(PRESIDENT OF THE UNITED STATES|UNITED STATES SENATOR|REPRESENTATIVE IN CONGRESS DISTRICT (\d+))"
                    r".* - (Democratic|Republican)$",
                    heading,
                )
                header = [
                    (word, box)
                    for words in rows[marked:first]
                    for word, box in words
                    if word not in ("Total", "Votes", "Cast", "County", "SCATTERING")
                ]
                centers = [(box[0] + box[2]) / 2 for word, box in header if re.fullmatch(r"[A-Z]{3}", word)]
                if found is None or not centers:
                    continue
                named: list[list[tuple[int, int, str]]] = [[] for _ in centers]
                for word, box in header:
                    if not re.fullmatch(r"[A-Z]{3}", word):
                        nearest = min(range(len(centers)), key=lambda slot: abs(centers[slot] - (box[0] + box[2]) / 2))
                        named[nearest].append((-box[0], box[1], word))
                candidates = [*(" ".join(word for _, _, word in sorted(slot)) for slot in named), "Scattering"]
                for words in rows[first:]:
                    if words[0][0] == "Report":
                        break
                    numbers = [(word, box) for word, box in words if re.fullmatch(r"[\d,]+", word)]
                    if len(numbers) != len(candidates) + 1:
                        raise ValueError(f"WI {url} page {page}: {texts[rows.index(words)]} has {len(numbers)} numbers")
                    counts = [int(word.replace(",", "")) for word, _ in numbers]
                    if counts[0] != sum(counts[1:]):
                        raise ValueError(f"WI {url} page {page}: {texts[rows.index(words)]} does not add up")
                    if words[0][0] == "Office":
                        for candidate, count in zip(candidates, counts[1:], strict=True):
                            if summed[(heading, candidate)] != count:
                                raise ValueError(
                                    f"WI {heading}: {candidate}'s counties sum to {summed[(heading, candidate)]}"
                                )
                        break
                    for candidate, (word, box) in zip(candidates, numbers[1:], strict=True):
                        summed[(heading, candidate)] += int(word.replace(",", ""))
                        cells.append(
                            {
                                "county": " ".join(word for word, _ in words[: len(words) - len(numbers)]),
                                "office": found[1],
                                "district": found[2],
                                "party": found[3],
                                "candidate": candidate,
                                "write_in": "1" if candidate == "Scattering" else "0",
                                "votes": word,
                                "page": page,
                                "x0": box[0],
                                "y0": box[1],
                                "x1": box[2],
                                "y1": box[3],
                            }
                        )
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys((*COLUMNS, "write_in"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif url == MS_RUNOFF:
            pdf = raw.cached(SOURCE, url).path
            cells = []
            counted: dict[str, int] = defaultdict(int)
            for page in range(3, 6):
                text = subprocess.run(
                    ["pdftotext", "-raw", "-f", str(page), "-l", str(page), str(pdf), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                recap = text.split("day of April 2024\n")[1].split("\nStatewide")[0].split("\n")
                for found in re.finditer(r"^(Ron Eller|Andrew Scott Smith) Republican ", text, flags=re.MULTILINE):
                    marker = next(box for word, box in scan.layer(pdf, page) if word == found[1].split()[0])
                    numbers = sorted(
                        (box[0], word, box)
                        for word, box in scan.layer(pdf, page)
                        if re.fullmatch(r"\d+", word) and abs(box[1] - marker[1]) < 12
                    )
                    if len(numbers) != len(recap):
                        raise ValueError(f"MS runoff page {page}: {len(numbers)} numbers for {len(recap)} counties")
                    for place, (_, word, box) in zip(recap, numbers, strict=True):
                        if place == "TOTAL":
                            if int(word) != counted[found[1]]:
                                raise ValueError(
                                    f"MS runoff: {found[1]}'s counties sum to {counted[found[1]]}, not {word}"
                                )
                            continue
                        counted[found[1]] += int(word)
                        cells.append(
                            {
                                "county": place,
                                "office": "U.S. House",
                                "district": "2",
                                "party": "Republican",
                                "candidate": found[1],
                                "votes": word,
                                "page": page,
                                "x0": box[0],
                                "y0": box[1],
                                "x1": box[2],
                                "y1": box[3],
                            }
                        )
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(COLUMNS, pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif state == "LA":
            race, parish = re.findall(r"Votes_(\d+)_(\d+)", url)[0]
            labels = {
                choice["ID"]: re.sub(r" \(\w+\)$", "", choice["Desc"])
                for contest in json.loads(
                    raw.cached(SOURCE, LOUISIANA.format("RacesCandidates_Multiparish")).path.read_text()
                )["Races"]["Race"]
                if contest["ID"] == race
                for choice in contest["Choice"]
            }
            frame = pl.DataFrame(
                [
                    {
                        "county": f"22{int(parish) * 2 - 1:03d}",
                        "precinct": entry["Precinct"].replace("/", " "),
                        "office": "President",
                        "district": None,
                        "party": LA_RACES[race],
                        "candidate": labels[choice["ID"]],
                        "votes": choice["VoteTotal"],
                    }
                    for entry in json.loads(raw.cached(SOURCE, url).path.read_text())["Precincts"]["Precinct"]
                    for choice in entry["Choice"]
                ],
                schema=dict.fromkeys(COLUMNS, pl.String),
            )
            reported = {
                labels[choice["ID"]]: int(choice["VoteTotal"])
                for place in json.loads(
                    raw.cached(SOURCE, LOUISIANA.format(f"VotesRaceByParish/Votes_{race}")).path.read_text()
                )["Parishes"]["Parish"]
                if place["ParishValue"] == parish
                for choice in place["Choice"]
            }
            if any(
                reported[candidate] != votes
                for candidate, votes in frame.group_by("candidate")
                .agg(pl.col("votes").cast(pl.Int64).sum())
                .iter_rows()
            ):
                raise ValueError(f"LA {race}: parish {parish}'s precincts do not add up to its total")
        elif state == "DC":
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False)
                .filter(pl.col("ContestName").str.contains("PRESIDENTIAL"), pl.col("Party") == "DEM")
                .select(
                    county=pl.lit("11001"),
                    precinct="PrecinctNumber",
                    office=pl.lit("President"),
                    district=pl.lit(None, pl.String),
                    party="Party",
                    candidate="Candidate",
                    votes="Votes",
                )
            )
            citywide = {
                entry["Contestant"]: entry["Votes"]
                for contest in json.loads(
                    raw.cached(SOURCE, COLUMBIA.format("cityWide/getCityWide/2024-Primary-Election")).path.read_text()
                )
                if contest["OfficeName"] == "DEM DEMOCRATIC PRESIDENTIAL PREFERENCE CITYWIDE"
                for entry in contest["ElectionData"]
            }
            if any(
                citywide[candidate] != votes
                for candidate, votes in frame.group_by("candidate")
                .agg(pl.col("votes").cast(pl.Int64).sum())
                .iter_rows()
            ):
                raise ValueError("DC: the precincts do not add up to the citywide totals")
        elif url == ILLINOIS:
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, separator="\t", infer_schema=False, encoding="utf8-lossy")
                .filter(pl.col("OfficeName").str.contains("CONGRESS|PRESIDENT"))
                .select(
                    county="County",
                    office="OfficeName",
                    district="OfficeName",
                    party="PartyName",
                    candidate=pl.concat_str("CanFirstName", "CanLastName", separator=" "),
                    votes="Votes",
                    id="CandidateID",
                )
            )
            if not (
                frame.group_by("office", "id", "party")
                .agg(pl.col("votes").cast(pl.Int64).sum())
                .join(
                    pl.read_csv(
                        raw.cached(SOURCE, IL_TOTALS).path, separator="\t", infer_schema=False, encoding="utf8-lossy"
                    ).select(
                        office="OfficeName", id="CandidateID", party="PartyName", total=pl.col("Votes").cast(pl.Int64)
                    ),
                    on=["office", "id", "party"],
                    how="left",
                )
                .select((pl.col("votes") == pl.col("total")).all())
                .item()
            ):
                raise ValueError("IL: the counties do not add up to the statewide totals")
            frame = frame.with_columns(write_in=pl.col("id").is_in(IL_WRITE_INS).cast(pl.Int8).cast(pl.String)).drop(
                "id"
            )
        elif url == KENTUCKY:
            pdf = raw.cached(SOURCE, url).path
            cells = []
            seat = affiliation = None
            title = "President"
            people: list[list[str]] = []
            tally: list[int] = []
            for page in (*range(2, 6), *range(12, 18)):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][-1][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                pending: list[list[tuple[str, scan.Box]]] = []
                slots: list[tuple[int, int]] = []
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    numbers = [(word, box) for word, box in words if re.fullmatch(r"\d{1,3}(,\d{3})*", word)]
                    if found := re.search(r"(\d+)(st|nd|rd|th) Congressional District", text):
                        seat = found[1]
                        title = "U.S. House"
                    elif text == "President of the United States":
                        seat = None
                        title = "President"
                    elif found := re.fullmatch(r"(Democratic|Republican) Party", text):
                        affiliation = found[1]
                        pending = []
                    elif "Commonwealth" in text or "Secretary" in text or not numbers:
                        if not re.search(r"Representative|Commonwealth|Secretary|Results|For the office", text):
                            pending.append(words)
                    elif text.startswith("Total Votes"):
                        if [int(word.replace(",", "")) for word, _ in numbers] != tally:
                            raise ValueError(
                                f"KY page {page}: the {seat} {affiliation} counties sum to {tally}, not {text}"
                            )
                        slots = []
                        tally = []
                        people = []
                    else:
                        if not slots:
                            slots = [(box[0], box[2]) for _, box in numbers]
                            heard: list[list[str]] = [[] for _ in slots]
                            for line in pending:
                                clusters: list[list[tuple[str, scan.Box]]] = []
                                for word, box in line:
                                    if clusters and box[0] - clusters[-1][-1][1][2] < 45:
                                        clusters[-1].append((word, box))
                                    else:
                                        clusters.append([(word, box)])
                                for cluster in clusters:
                                    sides = [
                                        min(
                                            range(len(slots)),
                                            key=lambda index, middle=(box[0] + box[2]) / 2: abs(
                                                (slots[index][0] + slots[index][1]) / 2 - middle
                                            ),
                                        )
                                        for _, box in cluster
                                    ]
                                    if len(set(sides)) == 1:
                                        middle = (cluster[0][1][0] + cluster[-1][1][2]) / 2
                                        sides = [
                                            min(
                                                range(len(slots)),
                                                key=lambda index: abs((slots[index][0] + slots[index][1]) / 2 - middle),
                                            )
                                        ] * len(cluster)
                                    for (word, _), side in zip(cluster, sides, strict=True):
                                        heard[side].append(word)
                            pending = []
                            if people and heard != people:
                                raise ValueError(
                                    f"KY page {page}: the continued table's names {heard} differ from {people}"
                                )
                            people = heard
                            tally = tally or [0] * len(slots)
                        if len(numbers) != len(slots):
                            raise ValueError(f"KY page {page}: {text} has {len(numbers)} numbers for {len(slots)}")
                        for index, (word, box) in enumerate(numbers):
                            tally[index] += int(word.replace(",", ""))
                            cells.append(
                                {
                                    "county": " ".join(word for word, _ in words[: words.index(numbers[0])]),
                                    "office": title,
                                    "district": seat,
                                    "party": affiliation,
                                    "candidate": " ".join(people[index])
                                    .replace('UNCOMMITTE D"', 'UNCOMMITTED"')
                                    .title(),
                                    "votes": word,
                                    "page": page,
                                    "x0": box[0],
                                    "y0": box[1],
                                    "x1": box[2],
                                    "y1": box[3],
                                }
                            )
            if tally:
                raise ValueError(f"KY: the {seat} {affiliation} table has no total")
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(COLUMNS, pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif url == DELAWARE.format("html"):
            page = raw.cached(SOURCE, url).path.read_text()
            page = page[page.index('id="byelectiondist"') : page.index('id="bycounty"')]
            cells = []
            heading = district = affiliation = None
            for match in re.finditer(
                r'<h3 class="contest-title[^"]*">([^<]+)</h3>|<h4 class="electiondistrict-title[^"]*">Election District'
                r' ([^<]+)</h4>|<h4 class="party-Name[^"]*">([^<]+)</h4>|<tr>\s*<td[^>]*>([^<]+)</td>'
                r"(?:\s*<td[^>]*>[^<]*</td>){3}\s*<td[^>]*>([^<]+)</td>",
                page,
                flags=re.DOTALL,
            ):
                if match[1]:
                    heading = match[1].strip()
                elif match[2]:
                    district = match[2].strip()
                elif match[3]:
                    affiliation = match[3].strip()
                elif heading in ("U.S. Representative in Congress", "Governor") and district and affiliation:
                    cells.append(
                        {
                            "county": delaware[district],
                            "precinct": district,
                            "office": heading,
                            "district": None,
                            "party": affiliation.removesuffix(" Party"),
                            "candidate": html.unescape(match[4]).strip().title(),
                            "votes": match[5].replace(",", ""),
                        }
                    )
            frame = pl.DataFrame(cells, schema=dict.fromkeys(COLUMNS, pl.String))
            printed = pl.read_csv(raw.cached(SOURCE, DELAWARE.format("csv")).path, infer_schema=False).select(
                office="office",
                party=pl.col("partyname").str.replace(" Party$", ""),
                candidate=pl.col("candidatename").str.to_titlecase(),
                printed=pl.col("totalvotessum").cast(pl.Int64),
            )
            summed = (
                frame.group_by("office", "party", "candidate")
                .agg(pl.col("votes").cast(pl.Int64).sum())
                .join(printed, on=["office", "party", "candidate"], how="left")
            )
            if summed.filter(pl.col("votes") != pl.col("printed").fill_null(-1)).height:
                raise ValueError(f"DE: the election districts do not add up to the statewide report: {summed}")
        elif url == UTAH:
            export = json.loads(raw.cached(SOURCE, url).path.read_text())
            statewide = {
                (item["name"].strip(), option["name"]): option["voteCount"]
                for item in export["results"]["ballotItems"]
                for option in item["ballotOptions"]
            }
            cells = []
            for locality in export["localResults"]:
                for item in locality["ballotItems"]:
                    if not re.search(r" For (US |Governor)", item["name"]):
                        continue
                    for option in item["ballotOptions"]:
                        statewide[(item["name"].strip(), option["name"])] -= option["voteCount"]
                        cells.append(
                            {
                                "county": locality["name"],
                                "office": item["name"].split(" For ", 1)[1].strip(),
                                "district": item["name"],
                                "party": item["name"].split(" For ", 1)[0],
                                "candidate": option["name"].split("/")[0],
                                "votes": str(option["voteCount"]),
                            }
                        )
            if any(votes for (name, _), votes in statewide.items() if re.search(r" For (US |Governor)", name)):
                raise ValueError("UT: the counties do not add up to the statewide results")
            frame = pl.DataFrame(cells, schema=dict.fromkeys(COLUMNS, pl.String))
        elif url == SUMMIT:
            pdf = raw.cached(SOURCE, url).path
            cells = []
            race: tuple[str, str] | None = None
            seen = 0
            for page in range(1, 6):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][-1][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    heading = re.fullmatch(r"(Republican|Democratic) Candidate for (.+)", text)
                    if heading:
                        race = heading[1], heading[2]
                        seen = 0
                    elif race and text.startswith("Total Votes Cast"):
                        if seen != int(words[3][0].replace(",", "")):
                            raise ValueError(f"UT: Summit's {race} lines sum to {seen}, the total is {words[3][0]}")
                        race = None
                    elif race and re.fullmatch(r"[\d,]+", words[-2][0]) and words[-1][0].endswith("%"):
                        seen += int(words[-2][0].replace(",", ""))
                        cells.append(
                            {
                                "county": "Summit",
                                "office": race[1].removesuffix(" and Lieutenant Governor").replace("U.S.", "US"),
                                "district": race[1],
                                "party": race[0],
                                "candidate": " ".join(word for word, _ in words[:-2]).split("/")[0].title(),
                                "votes": words[-2][0],
                                "page": page,
                                "x0": words[-2][1][0],
                                "y0": words[-2][1][1],
                                "x1": words[-2][1][2],
                                "y1": words[-2][1][3],
                            }
                        )
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(COLUMNS, pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif state == "HI":
            records = [
                record
                for record in csv.reader(raw.cached(SOURCE, url).path.read_text(encoding="utf-16").splitlines()[2:])
                if record[7].startswith("U.S.") and re.fullmatch(r"\d{2}-\d{2}", record[0])
            ]
            tallies: dict[tuple[str, str, str], int] = defaultdict(int)
            for record in records:
                tallies[(record[7], record[8], record[10])] += int(record[13]) + int(record[14])
            for record in csv.reader(
                raw.cached(SOURCE, HAWAII.format("summary")).path.read_text(encoding="utf-16").splitlines()[2:]
            ):
                if record[1].startswith("U.S.") and tallies.pop((record[1], record[4], record[15])) != int(record[20]):
                    raise ValueError(f"HI: the precincts do not add up to the summary for {record[15]}")
            if tallies:
                raise ValueError(f"HI: {sorted(tallies)} are not in the summary")
            frame = pl.DataFrame(
                [
                    {
                        "county": hawaii[record[0].split("-")[0]],
                        "precinct": f"{record[0]} {record[1]}".strip(),
                        "office": record[7],
                        "district": {"Dist I": "1", "Dist II": "2"}.get(record[7].split(", ")[-1]),
                        "party": {"D": "democratic", "R": "republican", "G": "green", "L": "libertarian"}.get(
                            record[8]
                        ),
                        "candidate": " ".join(reversed(record[10].split(", ", 1))),
                        "votes": str(int(record[13]) + int(record[14])),
                    }
                    for record in records
                ],
                schema=dict.fromkeys(COLUMNS, pl.String),
            )
        elif state == "MO":
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            cells = []
            running: dict[tuple[str, str, str], int] = defaultdict(int)
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                start = next(index for index, words in enumerate(lines) if words[0][0] == "Primary") + 1
                header: list[list[tuple[str, scan.Box]]] = []
                edges: list[int] = []
                for words in lines[start:]:
                    if re.fullmatch(r"\d+", words[0][0]) or not re.fullmatch(r"\d+", words[-1][0]):
                        if edges:
                            raise ValueError(f"MO page {page}: {words[0][0]} interrupts a table")
                        header.append(words)
                        continue
                    if not edges:
                        margin = words[0][1][0]
                        edges = [box[2] for word, box in words if re.fullmatch(r"\d+", word)]
                        label: list[str] = []
                        banners: list[str | None] = [None] * len(edges)
                        heads: list[list[str]] = [[] for _ in edges]
                        for line in header:
                            chain = 0
                            if line[0][1][0] < margin + 120:
                                chain = 1
                                while chain < len(line) and line[chain][1][0] - line[chain - 1][1][2] < 60:
                                    chain += 1
                            label.extend(word for word, _ in line[:chain])
                            for word, box in line[chain:]:
                                column = next((i for i, edge in enumerate(edges) if box[2] <= edge + 30), None)
                                if column is None:
                                    raise ValueError(f"MO page {page}: {word} sits past the last column")
                                if word in ("Republican", "Democratic", "Libertarian", "Green", "Constitution"):
                                    banners[column] = word
                                else:
                                    heads[column].append(word)
                        title = " ".join(label)
                        numbered = re.search(r"District (\d+)", title)
                        kept = (
                            "U.S. Senate"
                            if title == "United States Senator"
                            else "Governor"
                            if title == "Governor"
                            else "U.S. House"
                            if title.startswith("U.S. Representative District")
                            else None
                        )
                    numbers = [(word, box) for word, box in words if re.fullmatch(r"\d+", word)]
                    place = " ".join(word for word, _ in words if not re.fullmatch(r"\d+", word))
                    if len(numbers) != len(edges):
                        raise ValueError(f"MO page {page}: {place} has {len(numbers)} numbers for {len(edges)} columns")
                    for column, (word, box) in enumerate(numbers):
                        affiliation = banners[column]
                        if affiliation is None or kept is None:
                            continue
                        key = (title, affiliation, " ".join(heads[column]))
                        if place == "Total":
                            summed = running.pop(key, 0)
                            if summed != int(word):
                                raise ValueError(f"MO page {page}: {key} sums to {summed}, the total is {word}")
                            continue
                        running[key] += int(word)
                        cells.append(
                            {
                                "county": place,
                                "office": kept,
                                "district": numbered[1] if numbered else None,
                                "party": affiliation,
                                "candidate": key[2],
                                "votes": word,
                                "page": page,
                                "x0": box[0],
                                "y0": box[1],
                                "x1": box[2],
                                "y1": box[3],
                            }
                        )
                    if place == "Total":
                        header = []
                        edges = []
            if running:
                raise ValueError(f"MO: {sorted(running)} have no total row")
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif state == "OR":
            pdf = raw.cached(SOURCE, url).path
            cells = []
            office_text = None
            seat = None
            party = ""
            stacked: list[list[tuple[str, scan.Box]]] = []
            labels = []
            sums: list[int] = []
            for page in range(1, 10):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    numbers = [(word, box) for word, box in words if re.fullmatch(r"\d{1,3}(,\d{3})*", word)]
                    place = " ".join(word for word, _ in words if not re.fullmatch(r"\d{1,3}(,\d{3})*", word))
                    if "Abstract of Votes" in text:
                        party, labels = "", []
                    elif text in ("US President", "US Representative"):
                        office_text, seat = ("President" if text == "US President" else "U.S. House"), None
                    elif found := re.fullmatch(r"(\d+)\w\w District", text):
                        seat = int(found[1])
                    elif text in ("Democrat", "Republican"):
                        party, stacked, labels = text, [], []
                    elif numbers and len(numbers) == len(words) - len(place.split()) and place:
                        if not labels:
                            edges = [box[2] for _, box in numbers]
                            columns: list[list[list[str]]] = [[] for _ in edges]
                            for header in stacked:
                                for column in columns:
                                    column.append([])
                                for word, box in header:
                                    if word != "County":
                                        columns[next(i for i, edge in enumerate(edges) if box[2] <= edge + 30)][
                                            -1
                                        ].append(word)
                            headers = [[" ".join(line) for line in column if line] for column in columns]
                            if not all(headers):
                                raise ValueError(f"OR page {page}: {place}'s columns have the headers {headers}")
                            labels = [
                                "Write-ins" if name == ["Misc."] else " ".join([*name[1:], name[0].lstrip("*")])
                                for name in headers
                            ]
                            sums = [0] * len(labels)
                        if len(numbers) != len(labels):
                            raise ValueError(f"OR page {page}: {place} has {len(numbers)} numbers for {labels}")
                        votes = [int(word.replace(",", "")) for word, _ in numbers]
                        if place == "Total":
                            if votes != sums:
                                raise ValueError(
                                    f"OR page {page}: the counties sum to {sums}, the abstract prints {votes}"
                                )
                            continue
                        if not any(votes):
                            continue
                        for position, (label, (word, box)) in enumerate(zip(labels, numbers, strict=True)):
                            sums[position] += votes[position]
                            cells.append(
                                {
                                    "county": place,
                                    "office": office_text,
                                    "district": None if seat is None else str(seat),
                                    "party": party,
                                    "candidate": label,
                                    "votes": word,
                                    "page": page,
                                    "x0": box[0],
                                    "y0": box[1],
                                    "x1": box[2],
                                    "y1": box[3],
                                }
                            )
                    elif party and not labels:
                        stacked.append(words)
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif state == "FL":
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, separator="\t", infer_schema=False, quote_char=None)
                .filter(pl.col("RaceCode").is_in(["PRE", "USS", "USR"]))
                .select(
                    county="CountyName",
                    office="RaceName",
                    district="RaceName",
                    party="PartyName",
                    candidate=pl.concat_str(
                        "CanNameFirst", "CanNameMiddle", "CanNameLast", separator=" ", ignore_nulls=True
                    ),
                    votes="CanVotes",
                )
            )
        elif state == "OH":
            reader = fastexcel.read_excel(raw.cached(SOURCE, url).path)
            sheets = []
            for sheet_name in ("Presidential Delegates", "U.S. Congress"):
                table = reader.load_sheet(sheet_name, header_row=None, dtypes="string").to_polars()
                body = table.slice(4).filter(pl.col(table.columns[0]).is_not_null())
                heading = ""
                for index, (label, name, total) in enumerate(
                    zip(table.row(0), table.row(1), table.row(2), strict=True)
                ):
                    heading = (label or "").strip() or heading
                    if index < 8 or "(district)" in heading or "Unexpired" in heading:
                        continue
                    column = table.columns[index]
                    if body[column].cast(pl.Int64).sum() != int(total):
                        raise ValueError(
                            f"OH {heading} {name}: the precincts sum to {body[column].cast(pl.Int64).sum()}, not {total}"
                        )
                    sheets.append(
                        body.select(
                            county=table.columns[0],
                            precinct=table.columns[1],
                            office=pl.lit(heading),
                            district=pl.lit(heading),
                            party=pl.lit(re.sub(r"^.*\((\w+)\)$", r"\1", name)),
                            candidate=pl.lit(re.sub(r"\s*\(\w+\)$", "", name)),
                            votes=column,
                        )
                    )
            frame = pl.concat(sheets)
        elif state == "TX":
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False)
                .unpivot(index=["CNTYVTD", "VTDKEY"], variable_name="candidate", value_name="votes")
                .select(
                    county=pl.lit("48") + pl.col("CNTYVTD").str.slice(0, 3),
                    precinct=(
                        ((pl.col("CNTYVTD").str.slice(0, 3).cast(pl.Int64) + 1) // 2).cast(pl.String).str.zfill(3)
                        + pl.col("CNTYVTD").str.slice(3)
                    ).str.strip_chars_start("0"),
                    office=pl.lit(texas[url]),
                    district=pl.lit(texas[url]),
                    party=pl.col("candidate").str.extract(r"([DR])_24[PR]_"),
                    candidate=pl.col("candidate").str.replace(r"[DR]_24[PR]_.*$", ""),
                    votes="votes",
                )
            )
        elif state == "NE":
            pdf = raw.cached(SOURCE, url).path
            cells = []
            printed: dict[tuple[str | None, str | None, str, str], int] = {}
            office_text: str | None = None
            seat: int | None = None
            parties: list[tuple[int, str]] = []
            above: list[list[tuple[str, scan.Box]]] = []
            sections: list[tuple[int, int, str, list[str]]] = []
            for page in range(13, 24):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    if text.startswith("Primary Election"):
                        break
                    heading = (
                        ("President", None)
                        if "President of the United States" in text
                        else ("U.S. Senate", None)
                        if "Six Year Term" in text
                        else (None, None)
                        if "Two Year Term" in text and "Senate" in text
                        else ("U.S. House", int(found[1]))
                        if (found := re.search(r"Congressional District (\d+)", text))
                        else False
                    )
                    if heading:
                        office_text, seat = heading
                    if found := re.findall(
                        r"(Republican|Democratic|Libertarian|Legal Marijuana NOW) Party Nomination", text
                    ):
                        parties = [
                            (box[0], party) for party in found for word, box in words if word == party.split()[0]
                        ]
                        above, sections = [], []
                        continue
                    if heading:
                        above, sections = [], []
                        continue
                    if words[0][0] == "County":
                        starts = [box[0] for word, box in words if word == "County"] + [10**6]
                        sections = []
                        for start, end in itertools.pairwise(starts):
                            party = min(parties, key=lambda item: abs(item[0] - start) if len(parties) > 1 else 0)[1]
                            sections.append((start, end, party, []))
                        above.append(words)
                        continue
                    if not sections:
                        above.append(words)
                        continue
                    for start, end, party, labels in sections:
                        part = [(word, box) for word, box in words if start - 20 <= box[0] < end - 20]
                        numbers = [(word, box) for word, box in part if re.fullmatch(r"[\d,]+", word)]
                        if not numbers:
                            continue
                        if not labels:
                            edges = [box[2] for _, box in numbers]
                            stacks: list[list[str]] = [[] for _ in edges]
                            for header in above:
                                for word, box in header:
                                    if word != "County" and start - 20 <= box[0] < end - 20:
                                        stacks[next(i for i, edge in enumerate(edges) if box[2] <= edge + 20)].append(
                                            word
                                        )
                            labels.extend(" ".join(column) for column in stacks)
                        place = re.sub(
                            r"\s*\(.*\)$", "", " ".join(word for word, _ in part if not re.fullmatch(r"[\d,]+", word))
                        )
                        if len(numbers) != len(labels):
                            raise ValueError(f"NE page {page}: {place} has {len(numbers)} numbers for {labels}")
                        for name, (word, box) in zip(labels, numbers, strict=True):
                            if office_text is None:
                                continue
                            if place == "Total":
                                printed[(office_text, None if seat is None else str(seat), party, name)] = int(
                                    word.replace(",", "")
                                )
                                continue
                            cells.append(
                                {
                                    "county": fips["NE"][re.sub(r"[^a-z]", "", place.lower())],
                                    "office": office_text,
                                    "district": None if seat is None else str(seat),
                                    "party": party,
                                    "candidate": name,
                                    "votes": word,
                                    "page": page,
                                    "x0": box[0],
                                    "y0": box[1],
                                    "x1": box[2],
                                    "y1": box[3],
                                }
                            )
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
            summed = frame.group_by("office", "district", "party", "candidate").agg(
                pl.col("votes").str.replace_all(",", "").cast(pl.Int64).sum()
            )
            if {(*key, votes) for *key, votes in summed.iter_rows()} != {
                (*key, votes) for key, votes in printed.items()
            }:
                raise ValueError(
                    f"NE: the counties sum to {summed.sort('office', 'district').rows()}, the book prints {printed}"
                )
        elif state == "ME":
            office_text, district, stage = ME_FILES[url.removeprefix(MAINE[:-7]).removesuffix(".xlsx")]
            table = (
                fastexcel.read_excel(raw.cached(SOURCE, url).path)
                .load_sheet(0, header_row=None, dtypes="string")
                .to_polars()
            )
            header = [str(cell or "").strip() for cell in table.row(0)]
            where = header.index("TOWN") if "TOWN" in header else header.index("MUNICIPALITY")
            region = header.index("CTY") if "CTY" in header else header.index("COUNTY")
            candidates = [
                column
                for index, (column, cell) in enumerate(zip(table.columns, header, strict=True))
                if index > where and cell not in ("BLANK", "TBC", "TOTAL BALLOTS CAST")
            ]
            town = pl.col(table.columns[where]).str.strip_chars()
            region_code = pl.col(table.columns[region]).str.strip_chars()
            grand = table.filter(town.str.contains("TOTAL") | region_code.str.contains("TOTAL")).tail(1)
            table = table.slice(1).filter(
                (region_code.str.contains(r"^[A-Z]{3}$") & ~town.str.contains("TOTAL")) | town.str.contains("UOCAVA")
            )
            for column in candidates:
                if table[column].cast(pl.Int64).sum() != int(grand[column][0]):
                    raise ValueError(f"Maine's towns do not add up to the state total in {url}")
            abbreviations = {name[:3].upper(): code for name, code in fips["ME"].items()}
            frame = table.unpivot(
                candidates,
                index=[table.columns[region], table.columns[where]],
                variable_name="column",
                value_name="votes",
            ).select(
                county=pl.when(region_code.str.contains(r"^[A-Z]{3}$"))
                .then(region_code.replace_strict(abbreviations, default=None))
                .otherwise(pl.lit("23")),
                precinct=town,
                office=pl.lit(office_text),
                district=pl.lit(district, pl.String),
                party=pl.lit(stage),
                candidate=pl.col("column")
                .replace_strict(
                    {
                        column: "Write-ins" if cell == "OTHERS" else cell
                        for column, cell in zip(table.columns, header, strict=True)
                    }
                )
                .str.replace(r"^([^,]+), ([^,]+), (.+)$", "$2 $1 $3")
                .str.replace(r"^([^,]+), ([^,]+)$", "$2 $1"),
                votes="votes",
            )
        elif state == "IN":
            data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig"))["Root"]
            lines = [
                {
                    "county": race["Jurisdiction"].get("FIPS", region.get("MAP_FIPS")),
                    "office": race["SubSortOrder"],
                    "district": race["SubSortOrder"],
                    "party": candidate["PARTY_ABBREV"],
                    "candidate": re.sub(r" \(W/I\)$", " (write-in)", candidate["NAME_ON_BALLOT"]),
                    "votes": str(candidate["TOTAL_VOTES"]),
                    "race": race["OFFICEID"],
                }
                for region in data["OfficeCategory"]["Regions"]["Region"]
                for race in (
                    region["Races"]["Race"] if isinstance(region["Races"]["Race"], list) else [region["Races"]["Race"]]
                )
                for candidate in (
                    race["Candidates"]["Candidate"]
                    if isinstance(race["Candidates"]["Candidate"], list)
                    else [race["Candidates"]["Candidate"]]
                )
            ]
            counted: dict[tuple[str, str], int] = defaultdict(int)
            for line in lines:
                counted[line["race"], line["candidate"]] += int(line["votes"])
            summary = data["StatewideSummary"]["Race"]
            if counted != {
                (race["OFFICEID"], re.sub(r" \(W/I\)$", " (write-in)", candidate["NAME_ON_BALLOT"])): int(
                    candidate["TOTAL"]
                )
                for race in (summary if isinstance(summary, list) else [summary])
                for candidate in (
                    race["Candidates"]["Candidate"]
                    if isinstance(race["Candidates"]["Candidate"], list)
                    else [race["Candidates"]["Candidate"]]
                )
            }:
                raise ValueError(f"Indiana's counties do not add up to the statewide totals in {url}")
            frame = pl.DataFrame(lines, schema=dict.fromkeys(lines[0], pl.String)).drop("race")
        elif state == "MA":
            listed: dict[str, tuple[str, str, str]] = {
                election: (office, district, stage)
                for listing in MASSACHUSETTS
                for election, _, office, district, stage in re.findall(
                    r'id="election-id-(\d+)".*?<td[^>]*>(\d{4})</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>',
                    raw.cached(SOURCE, listing).path.read_text(),
                    re.DOTALL,
                )
            }
            office_text, district, stage = listed[url.split("/")[-3]]
            if "Primary" not in stage:
                continue
            table = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, skip_rows_after_header=1)
            table = table.rename({table.columns[0]: "City/Town"})
            frame = (
                table.filter(pl.col("City/Town").is_not_null(), pl.col("City/Town") != "TOTALS")
                .unpivot(
                    [column for column in table.columns[3:] if column not in ("Blanks", "Total Votes Cast")],
                    index=["City/Town", "Ward", "Pct"],
                    variable_name="candidate",
                    value_name="votes",
                )
                .select(
                    county=pl.col("City/Town"),
                    precinct=pl.concat_str(
                        "City/Town",
                        pl.when(pl.col("Ward") != "-").then(pl.col("Ward")),
                        "Pct",
                        separator=" ",
                        ignore_nulls=True,
                    ),
                    office=pl.lit(office_text),
                    district=pl.lit(district),
                    party=pl.lit(stage.removesuffix(" Primary")),
                    candidate=pl.when(pl.col("candidate") == "All Others")
                    .then(pl.lit("Write-ins"))
                    .otherwise("candidate"),
                    votes=pl.col("votes").str.replace_all(",", ""),
                )
            )
        elif state == "MD":
            frame = pl.read_csv(
                io.BytesIO(re.sub(rb'" *\r?\n', b'"\n', raw.cached(SOURCE, url).path.read_bytes())),
                infer_schema=False,
            )
            frame = frame.filter(
                (pl.col("Office Name") != "President - Vice Pres")
                | (pl.col("Office District").fill_null("").str.strip_chars() == "")
            ).select(
                county="County Name",
                precinct="Election District - Precinct",
                office="Office Name",
                district="Office District",
                party="Party",
                candidate="Candidate Name",
                votes=pl.sum_horizontal(
                    pl.col(column).str.strip_chars().cast(pl.Int64)
                    for column in (
                        "Early Votes",
                        "Election Night Votes",
                        "Mail-In Ballot 1 Votes",
                        "Provisional Votes",
                        "Mail-In Ballot 2 Votes",
                    )
                ).cast(pl.String),
            )
        elif url == ALASKA:
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, truncate_ragged_lines=True)
                .filter(pl.col("Contest_title") == "U.S. Representative")
                .select(
                    county=pl.lit("02"),
                    precinct="Precinct_name",
                    office="Contest_title",
                    party="Party_Code",
                    candidate=pl.col("candidate_name").str.replace(r"^([^,]+), (.+)$", "$2 $1"),
                    votes="total_votes",
                )
            )
        elif state == "MN":
            minnesota = {
                "column_1": "county",
                "column_2": "precinct",
                "column_4": "office",
                "column_5": "district",
                "column_7": "candidate",
                "column_10": "party",
                "column_13": "votes",
            }
            precincts = pl.read_csv(
                raw.cached(SOURCE, url.rsplit("/", 1)[0] + "/PrctTbl.txt").path,
                separator=";",
                has_header=False,
                infer_schema=False,
                encoding="utf8-lossy",
            ).select(county="column_0", precinct="column_1", name="column_2")
            frame = (
                pl.read_csv(
                    raw.cached(SOURCE, url).path,
                    separator=";",
                    has_header=False,
                    infer_schema=False,
                    encoding="utf8-lossy",
                    quote_char=None,
                )
                .rename(minnesota)
                .select(*minnesota.values())
                .join(precincts, on=["county", "precinct"], how="left", validate="m:1")
                .with_columns(
                    county=pl.lit("27") + (pl.col("county").cast(pl.Int64) * 2 - 1).cast(pl.String).str.zfill(3),
                    precinct=pl.coalesce("name", "precinct"),
                )
                .drop("name")
            )
        else:
            text = raw.cached(SOURCE, url).path.read_bytes()
            frame = pl.read_csv(
                io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
                infer_schema_length=0,
                truncate_ragged_lines=True,
            ).rename(str.lower)
        if "votes" not in frame.columns:
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                    for column in frame.columns
                    if column.endswith("_votes")
                ).cast(pl.String)
            )
        if state in ("MN", "IN", "ME", "NJ", "NE", "TX", "HI", "DE", "AR", "DC", "LA") or url in (
            OKLAHOMA,
            CLARK,
            ALASKA,
        ):
            pass
        elif state == "MA":
            frame = frame.with_columns(
                county=pl.col("county")
                .str.replace(r"^N\. ", "North ")
                .str.replace(r"^S\. ", "South ")
                .str.replace(r"^E\. ", "East ")
                .str.replace(r"^W\. ", "West ")
                .str.replace(r" City$", "")
                .map_elements(elstats.plain, return_dtype=pl.String)
                .replace_strict(places["MA"], default=None)
            )
        elif "county" not in frame.columns or state in ("CT", "RI"):
            frame = frame.with_columns(
                county=pl.col("town" if "town" in frame.columns else "county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .str.replace(r"limited$", "")
                .replace_strict(
                    {
                        **(
                            ct_regions()
                            if state == "CT"
                            else {town: county for (known, town), county in towns.items() if known == state}
                        ),
                        **dict.fromkeys(("federalprecinct", "federalprecincts", "congressionaldistrict"), "44"),
                    },
                    default=None,
                )
            )
        else:
            frame = frame.with_columns(
                county=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all(r"\b(county|parish)\b", "")
                .str.replace_all(r"[^a-z]", "")
                .replace_strict({**fips[state], **TYPOS.get(state, {})}, default=None)
            )
        office = pl.col("office").str.to_lowercase().str.strip_chars()
        day = (
            date(2024, 3, 19)
            if url == ARIZONA[0]
            else date(2024, 7, 30)
            if url == ARIZONA[1]
            else date(2024, 6, 25)
            if url == NEW_YORK[2]
            else date(2024, 4, 2)
            if state == "NY"
            else date(2024, 5, 14)
            if state in ("MD", "NE")
            else date(2024, 3, 19)
            if state == "OH"
            else date(2024, 5, 21)
            if state == "OR"
            else date(2024, 8, 6)
            if state == "MO"
            else date(2024, 8, 10)
            if state == "HI"
            else date(2024, 6, 25)
            if url in (UTAH, SUMMIT)
            else date(2024, 9, 10)
            if url == DELAWARE.format("html")
            else date(2024, 5, 21)
            if url == KENTUCKY
            else SOUTH_CAROLINA[url]
            if url in SOUTH_CAROLINA
            else date(2024, 3, 5)
            if state == "AR" or url in (OKLAHOMA, VERMONT)
            else date(2024, 8, 20)
            if url == ALASKA
            else date(2024, 6, 11)
            if url == CLARK
            else date(2024, 4, 23)
            if url.startswith(PENNSYLVANIA.split("?")[0])
            else date(2024, 3, 19)
            if url == ILLINOIS
            else date(2024, 6, 4)
            if state == "DC"
            else date(2024, 3, 23)
            if state == "LA"
            else date(2024, 4, 2)
            if url in (MS_RUNOFF, WISCONSIN[0])
            else date(2024, 8, 13)
            if state == "WI"
            else (date(2024, 5, 28) if re.search(r"/50[89]/", url) else date(2024, 3, 5))
            if state == "TX"
            else (date(2024, 3, 19) if url.endswith("by-County.pdf") else date(2024, 8, 6))
            if state == "KS"
            else date(2024, 6, 4)
            if state in ("IA", "NJ", "MT")
            else date(2024, 5, 7)
            if state == "IN"
            else (date(2024, 4, 16) if "Runoff" in url else date(2024, 3, 5))
            if state == "AL"
            else (date(2024, 3, 5) if frame["office"][0] == "President" else date(2024, 9, 3))
            if state == "MA"
            else datetime.strptime(re.findall(r"2024\d{4}", url)[-1], "%Y%m%d").date()
        )
        frames.append(
            frame.with_columns(
                [pl.lit(None, pl.String).alias(column) for column in COLUMNS if column not in frame.columns]
            )
            .with_columns(
                kind=pl.when(
                    office.str.contains(r"^(united states |u\.?s\.? )?president|^presidential")
                    & ~office.str.contains("council|board|commission|delegate")
                )
                .then(pl.lit("president"))
                .when(office.str.contains(openelections.SENATE.pattern))
                .then(pl.lit("senate"))
                .when(
                    office.str.contains(openelections.HOUSE.pattern) & ~office.str.contains("measure|initiat|delegate")
                )
                .then(pl.lit("house"))
                .when(office.str.contains(r"^governor\b"))
                .then(pl.lit("governor"))
            )
            .filter(
                pl.col("kind").is_not_null(),
                ~pl.col("candidate").fill_null("").str.contains(SKIPPED),
                pl.col("candidate").fill_null("").str.strip_chars() != "",
            )
            .select(
                *(
                    pl.col(column).cast(pl.Int64)
                    for column in ("page", "x0", "y0", "x1", "y1")
                    if column in frame.columns
                ),
                state=pl.lit(state),
                day=pl.lit(DAYS.get((state, day), day)),
                office="kind",
                district=pl.when(pl.col("kind") == "house").then(
                    pl.lit(0, pl.Int64)
                    if state in ("AK", "DE", "ND", "SD", "VT", "WY")
                    else pl.col("district").fill_null("").str.extract(r"(\d+)").cast(pl.Int64).fill_null(0)
                ),
                party=pl.lit(None, pl.String)
                if state == "WA" and day.month == 8 or url == ALASKA
                else pl.col("party")
                .str.to_lowercase()
                .str.strip_chars()
                .replace_strict(
                    {**PARTIES, "dem": "north-dakota-democratic-nonpartisan-league"}
                    if state == "ND"
                    else {**PARTIES, "dfl": "democratic"}
                    if url == MN_PRESIDENT.format("PNPPrecinct")
                    else PARTIES,
                    default=None,
                ),
                grain=pl.lit(grain),
                county="county",
                precinct=pl.when(pl.lit(grain) == "precinct").then(
                    pl.col("precinct").str.strip_chars().str.replace_all(r"\s+", " ")
                ),
                candidate=pl.col("candidate").str.strip_chars().str.replace_all(r"\s+", " "),
                write_in=pl.col("candidate").str.contains(r"(?i)write[- ]?ins?\b")
                | (pl.col("write_in") == "1" if "write_in" in frame.columns else pl.lit(False)),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
                url=pl.lit(url),
            )
            .filter(pl.col("votes").is_not_null())
        )
    for state in ELECTIONSTATS:
        places[state].update(TYPOS.get(state, {}))
        if state == "CT":
            places[state] = ct_regions()
        url = search(state)
        frame = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, truncate_ragged_lines=True)
        office = pl.col("office_name").str.to_lowercase().str.strip_chars()
        division = pl.col("division_name").str.replace(r"\s*\(.*\)$", "").str.strip_chars()
        frames.append(
            frame.filter(
                pl.col("election_type").str.contains("(?i)primary"),
                pl.col("division_type").is_in(["County", "Locality", "City/Town"]),
                ~pl.col("candidate_name").fill_null("").str.contains(SKIPPED),
            )
            .with_columns(
                kind=pl.when(
                    office.str.contains(r"^(u\.?s\.? )?president|^presidential") & ~office.str.contains("delegate")
                )
                .then(pl.lit("president"))
                .when(office.str.contains(openelections.SENATE.pattern))
                .then(pl.lit("senate"))
                .when(office.str.contains(openelections.HOUSE.pattern))
                .then(pl.lit("house"))
                .when(office.str.contains(r"^governor\b"))
                .then(pl.lit("governor"))
            )
            .filter(pl.col("kind").is_not_null(), (pl.lit(state) != "VT") | (pl.col("kind") != "president"))
            .select(
                state=pl.lit(state),
                day=pl.col("election_date").str.slice(0, 10).str.to_date(),
                office="kind",
                district=pl.when(pl.col("kind") == "house").then(
                    pl.col("district_name").str.extract(r"(\d+)").cast(pl.Int64).fill_null(0)
                ),
                party=pl.col("primary_party")
                .str.to_lowercase()
                .str.strip_chars()
                .replace_strict(PARTIES, default=None),
                grain=pl.lit("county"),
                county=pl.when(pl.col("division_type") == "City/Town")
                .then(
                    pl.coalesce(
                        division.map_elements(elstats.plain, return_dtype=pl.String).replace_strict(
                            places[state], default=None
                        ),
                        division.str.to_lowercase()
                        .str.replace_all(elstats.SUFFIXES, "")
                        .map_elements(elstats.plain, return_dtype=pl.String)
                        .replace_strict(places[state], default=None),
                    )
                )
                .otherwise(
                    division.str.to_lowercase()
                    .str.replace_all(r"saint ", "st ")
                    .str.replace_all(r"\b(county|parish)\b", "")
                    .str.replace_all(r"[^a-z]", "")
                    .replace_strict({**fips[state], **TYPOS.get(state, {})}, default=None)
                ),
                precinct=pl.lit(None, pl.String),
                candidate=pl.col("candidate_name").str.strip_chars().str.replace_all(r"\s+", " "),
                write_in=pl.col("candidate_name").str.contains(r"(?i)write[- ]?ins?\b|scatter|^all other"),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                url=pl.lit(url),
            )
            .filter(pl.col("votes").is_not_null())
        )
    frame = pl.concat(frames, how="diagonal")
    frame = pl.concat(
        [
            frame.filter(~pl.col("url").is_in(NEW_YORK)),
            frame.filter(pl.col("url").is_in(NEW_YORK)).join(
                frame.filter(pl.col("url") == search("NY")).select("day", "office", "district", "party").unique(),
                on=["day", "office", "district", "party"],
                how="anti",
                nulls_equal=True,
            ),
        ]
    )
    print(frame.filter(pl.col("county").is_null()).group_by("state", "url").agg(pl.col("votes").sum()))
    frame = frame.filter(pl.col("county").is_not_null())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    print(frame.group_by("state", "grain").agg(pl.col("day").n_unique(), pl.col("votes").sum()).sort("state", "grain"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "returns.parquet").with_columns(
        key=pl.concat_str(
            pl.col("day").cast(pl.String),
            "state",
            "office",
            pl.col("district").cast(pl.String),
            "party",
            separator=":",
            ignore_nulls=True,
        )
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Official and OpenElections 2024 primary returns by county or precinct, taking the default from "
            "Wikipedia's election boxes",
            polity="US",
        )
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value = any(%s)",
                [frame["key"].unique().to_list()],
            ).fetchall()
        )
        done: dict[str, tuple[str, UUID, dict[str, int]]] = {}
        for value, result_set, county, votes in conn.execute(
            """select x.value, r.id,
                      case u.scheme_code when 'us.county_part' then split_part(u.code, ':', 2)
                      when 'us.precinct' then split_part(u.code, ':', 3) else u.code end,
                      sum(v.value)::bigint
               from external_id x join result_set r on r.id = x.entity_id and r.is_default
               join result_value v on v.result_set_k = r.k and v.measure = 1
               join reporting_unit u on u.k = v.unit_k
               where x.scheme = %s group by 1, 2, 3 having sum(v.value) > 0""",
            [f"{SOURCE}.result_set"],
        ).fetchall():
            done.setdefault(re.sub(r":r\d+$", "", value), (value, result_set, {}))[2][county] = votes
        counties = {
            code: (unit, area, name)
            for code, unit, area, name in conn.execute(
                """select u.code, u.id, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short' where u.scheme_code = 'us.county'
                   union all select code, id, area_id, 'statewide' from reporting_unit where scheme_code = 'us.state'
                   union all select code, id, area_id, 'Kansas City' from reporting_unit
                   where scheme_code = 'us.place' and code = '2938000'
                   union all select code, id, area_id, 'district' from reporting_unit
                   where scheme_code = 'us.cd' and valid_to is null"""
            ).fetchall()
        }
        columns = (
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
        )
        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        pages: dict[tuple[str, int], tuple[UUID, int, int]] = {}
        for state, used in frame.select("state", "url").unique(maintain_order=True).iter_rows():
            publisher, title, url, kind, license = (
                (
                    "California Secretary of State",
                    "Statement of Vote, March 5, 2024, Presidential Primary Election",
                    CALIFORNIA,
                    "official_body",
                    "Public record",
                )
                if state == "CA"
                else (
                    ncsbe.PUBLISHER,
                    "Election results, May 14, 2024 second primary, official",
                    "https://er.ncsbe.gov/?election_dt=05/14/2024&county_id=0&office=FED&contest=0",
                    "official_body",
                    "Public record",
                )
                if used.startswith(NC_RUNOFF.removesuffix("{}.txt"))
                else (
                    ncsbe.PUBLISHER,
                    ncsbe.TITLE,
                    "https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
                    "official_body",
                    "Public record",
                )
                if state == "NC"
                else (
                    "Minnesota Secretary of State",
                    "Election results media files",
                    "https://electionresults.sos.mn.gov",
                    "official_body",
                    "Public record",
                )
                if state == "MN"
                else (
                    "Maryland State Board of Elections",
                    "Election data",
                    "https://elections.maryland.gov/elections/archive/2024/election_data/index.html",
                    "official_body",
                    "Public record",
                )
                if state == "MD"
                else (
                    wa_local.PUBLISHER,
                    wa_local.TITLE,
                    "https://results.vote.wa.gov/results/",
                    "official_body",
                    "Public record",
                )
                if state == "WA"
                else (
                    "Indiana Election Division",
                    "2024 Primary Election results",
                    "https://enr.indianavoters.in.gov/archive/2024Primary/",
                    "official_body",
                    "Public record",
                )
                if state == "IN"
                else (
                    "Alabama Secretary of State",
                    "Election data downloads",
                    "https://www.sos.alabama.gov/alabama-votes/voter/election-data",
                    "official_body",
                    "Public record",
                )
                if state == "AL"
                else (
                    "Maine Department of the Secretary of State",
                    "2024 primary election tabulations",
                    "https://lldc.mainelegislature.org/Open/Elections/Returns/2024/",
                    "official_body",
                    "Public record",
                )
                if state == "ME"
                else (
                    "Iowa Secretary of State",
                    "Election night results, 2024 Primary Election",
                    "https://electionresults.iowa.gov/IA/121508/",
                    "official_body",
                    "Public record",
                )
                if state == "IA"
                else (
                    "Kansas Secretary of State",
                    "Official vote totals by county, 2024 Presidential Preference Primary Election",
                    "https://sos.ks.gov/elections/election-results.html",
                    "official_body",
                    "Public record",
                )
                if used.endswith("by-County.pdf")
                else (
                    "Texas Legislative Council",
                    "Texas Election Data, 2024 primary and runoff elections by VTD",
                    "https://data.capitol.texas.gov/",
                    "official_body",
                    "Creative Commons Attribution",
                )
                if state == "TX"
                else (
                    "Ohio Secretary of State",
                    "March 19, 2024 Presidential Primary Election official canvass by precinct",
                    "https://www.ohiosos.gov/elections/election-results-and-data/2024-official-election-results/",
                    "official_body",
                    "Public record",
                )
                if state == "OH"
                else (
                    "Florida Division of Elections",
                    "Election results file, 2024 presidential preference primary and primary election",
                    "https://results.elections.myflorida.com/",
                    "official_body",
                    "Public record",
                )
                if state == "FL"
                else (
                    "Oregon Secretary of State",
                    "May 21, 2024, Primary Election Abstract of Votes",
                    "https://records.sos.state.or.us/ORSOSCMSearch/Search/SearchDetail.aspx?uri=13735456",
                    "official_body",
                    "Public record",
                )
                if state == "OR"
                else (
                    "Missouri Secretary of State",
                    "Official results, Primary Election - August 6, 2024",
                    "https://www.sos.mo.gov/elections/results",
                    "official_body",
                    "Public record",
                )
                if state == "MO"
                else (
                    "Hawaii Office of Elections",
                    "2024 Primary Election, results by precinct",
                    "https://elections.hawaii.gov/election-results/",
                    "official_body",
                    "Public record",
                )
                if state == "HI"
                else (
                    "Utah Office of the Lieutenant Governor",
                    "2024 Primary Election, official results by county",
                    "https://vote.utah.gov/county-canvass-certifications-and-results-june-2024-primary-election/",
                    "official_body",
                    "Public record",
                )
                if used in (UTAH, SUMMIT)
                else (
                    "Delaware Department of Elections",
                    "2024 Primary Election Report, official results",
                    "https://elections.delaware.gov/reports/PR2024.html",
                    "official_body",
                    "Public record",
                )
                if used == DELAWARE.format("html")
                else (
                    "Kentucky State Board of Elections",
                    "Official 2024 Primary Election Results",
                    "https://elect.ky.gov/results/2020-2029/Pages/2024.aspx",
                    "official_body",
                    "Public record",
                )
                if used == KENTUCKY
                else (
                    "South Carolina Election Commission",
                    "Election results",
                    "https://scvotes.gov/election-results/",
                    "official_body",
                    "Public record",
                )
                if used in SOUTH_CAROLINA
                else (
                    "Arkansas Secretary of State",
                    "2024 Primary, official results",
                    "https://arkansas.tally-enr.com/#election=1845",
                    "official_body",
                    "Public record",
                )
                if state == "AR"
                else (
                    "Pennsylvania Department of State",
                    "2024 Presidential Primary, official returns by county",
                    "https://www.electionreturns.pa.gov/",
                    "official_body",
                    "Public record",
                )
                if used.startswith(PENNSYLVANIA.split("?")[0])
                else (
                    "Illinois State Board of Elections",
                    "Downloadable vote totals, 2024 general primary, candidate totals by county",
                    "https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx",
                    "official_body",
                    "Public record",
                )
                if used == ILLINOIS
                else (
                    "District of Columbia Board of Elections",
                    "Primary Election 2024, certified results by precinct",
                    "https://electionresults.dcboe.org/election_results/2024-Primary-Election",
                    "official_body",
                    "Public record",
                )
                if state == "DC"
                else (
                    "Louisiana Secretary of State",
                    "Election results, March 23, 2024 presidential preference primary, by precinct",
                    "https://voterportal.sos.la.gov/graphical",
                    "official_body",
                    "Public record",
                )
                if state == "LA"
                else (
                    "Mississippi Secretary of State",
                    "Official recapitulation, April 2, 2024 Republican primary runoff, certified results",
                    "https://www.sos.ms.gov/elections/electionResults/2024RepublicanPrimaryRunoff.asp",
                    "official_body",
                    "Public record",
                )
                if used == MS_RUNOFF
                else (
                    "Wisconsin Elections Commission",
                    "County by County Report, 2024 Spring Election and Presidential Preference Vote, canvass"
                    if used == WISCONSIN[0]
                    else "County by County Report, 2024 Partisan Primary, canvass",
                    "https://elections.wi.gov/elections/election-results",
                    "official_body",
                    "Public record",
                )
                if state == "WI"
                else (
                    "New York State Board of Elections",
                    "Certified results, June 25, 2024 primary election"
                    if used == NEW_YORK[2]
                    else "Certified results, April 2, 2024 presidential primary, by county",
                    "https://elections.ny.gov/election-results",
                    "official_body",
                    "Public record",
                )
                if state == "NY"
                else (
                    "Montana Secretary of State",
                    "2024 Statewide Primary Election Canvass",
                    "https://sosmt.gov/elections/results/",
                    "official_body",
                    "Public record",
                )
                if state == "MT"
                else (
                    "Arizona Secretary of State",
                    "State of Arizona Official Canvass, 2024 Presidential Preference Election"
                    if used == ARIZONA[0]
                    else "State of Arizona Official Canvass, 2024 Primary Election",
                    "https://azsos.gov/elections/results-data",
                    "official_body",
                    "Public record",
                )
                if used in ARIZONA
                else (
                    "Oklahoma State Election Board",
                    "Official Results, Presidential Preferential Primary, March 5, 2024, by congressional district",
                    "https://oklahoma.gov/elections/elections-results/election-results/2024-election-results/march-ppp-special-elections.html",
                    "official_body",
                    "Public record",
                )
                if used == OKLAHOMA
                else (
                    "Vermont Secretary of State",
                    "Official Report of the Canvassing Committee, Presidential Primary, March 5, 2024",
                    "https://sos.vermont.gov/elections/election-info-resources/elections-results-data/",
                    "official_body",
                    "Public record",
                )
                if used == VERMONT
                else (
                    "Alaska Division of Elections",
                    "2024 Primary Election, official results by precinct",
                    "https://www.elections.alaska.gov/election-results/e/?id=24prim",
                    "official_body",
                    "Public record",
                )
                if used == ALASKA
                else (
                    "Clark County Election Department",
                    "Results of Canvass, 2024 Primary Election, June 11, 2024",
                    "https://clark.legistar.com/Calendar.aspx",
                    "official_body",
                    "Public record",
                )
                if used == CLARK
                else (
                    "Kansas Secretary of State",
                    "Election results, 2024 primary election",
                    "https://sos.ks.gov/elections/election-results.html",
                    "official_body",
                    "Public record",
                )
                if state == "KS"
                else (
                    "New Jersey Division of Elections",
                    "2024 official primary election results",
                    "https://www.nj.gov/state/elections/election-information-2024.shtml",
                    "official_body",
                    "Public record",
                )
                if state == "NJ"
                else (
                    "Nebraska Secretary of State",
                    "Official report of the Board of State Canvassers, 2024 primary election",
                    NEBRASKA,
                    "official_body",
                    "Public record",
                )
                if state == "NE"
                else (
                    elstats.MA_PUBLISHER,
                    elstats.TITLE,
                    "https://electionstats.state.ma.us",
                    "official_body",
                    "Public record",
                )
                if state == "MA"
                else (
                    elstats.TENANTS[state][1],
                    elstats.TITLE,
                    elstats.TENANTS[state][2],
                    "official_body",
                    "Public record",
                )
                if "civera.com" in used
                else (
                    "OpenElections",
                    f"{state} 2024 primary results",
                    f"https://github.com/openelections/openelections-data-{state.lower()}",
                    "community",
                    "No license stated: facts published with attribution, raw files not redistributed",
                )
            )
            sources[used] = cs.source(publisher, title)
            if sources[used] in cs.minted and sources[used] not in list(sources.values())[:-1]:
                cs.add(
                    "source", id=sources[used], kind=kind, publisher=publisher, title=title, url=url, license=license
                )
            artifacts[used] = cs.artifact(raw.cached(SOURCE, used), sources[used])
        spans: dict[tuple[str, int], set[str]] = defaultdict(set)
        for value, county in conn.execute(
            """select distinct e.value, case u.scheme_code when 'us.county' then u.code
                      when 'us.county_part' then split_part(u.code, ':', 2) else split_part(u.code, ':', 3) end
               from external_id e join result_set rs on rs.contest_id = e.entity_id
               join result_value v on v.result_set_k = rs.k join reporting_unit u on u.k = v.unit_k
               where e.scheme = 'us.contest' and e.value ~ %s
               and u.scheme_code in ('us.county', 'us.county_part', 'us.precinct')""",
            [rf"^2024-11-05:({'|'.join(PARTIAL)}):house:[0-9]+$"],
        ).fetchall():
            spans[(value.split(":")[1], int(value.split(":")[3]))].add(county)
        values: list[pl.DataFrame] = []
        precincts: dict[str, tuple[str, date, str]] = {}
        parts: dict[str, tuple[str, str, date, str, UUID | None]] = {}
        ballots: dict[tuple[str, date, str, int | None], dict[str | None, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for state, day, office, district, party, candidate in (
            frame.select("state", "day", "office", "district", "party", "candidate").unique().iter_rows()
        ):
            ballots[(state, day, office, district)][party].add(slug(candidate))
        for (key, state, day, office, district), rows in frame.group_by(
            "key", "state", "day", "office", "district", maintain_order=True
        ):
            contest = contests.get(key)
            if contest is None:
                if rows["votes"].sum() > 0:
                    print(f"{key}: no contest, {rows['votes'].sum()} votes skipped")
                continue
            fine = rows.filter(pl.col("grain") == "precinct").with_columns(
                pl.col("precinct").fill_null("County total (no precinct breakdown)")
            )
            coarse = rows.filter(pl.col("grain") == "county")
            if fine.height and coarse.height:
                sums = [
                    part.group_by("candidate").agg(pl.col("votes").sum()).sort("candidate") for part in (fine, coarse)
                ]
                if not sums[0].equals(sums[1]):
                    print(f"{key}: precinct and county files disagree, the county file used")
                    fine = fine.clear()
            lines = fine if fine.height else coarse
            revised = done.get(key)
            if revised is not None and any(
                revised[2]
                == dict(part.group_by("county").agg(pl.col("votes").sum()).filter(pl.col("votes") > 0).iter_rows())
                for part in (rows.filter(pl.col("grain") == "precinct"), coarse)
            ):
                continue
            if state in PARTIAL:
                prefix = lines["county"][0][:2]
                needed: set[str] = (
                    spans[(state, district)]
                    if office == "house"
                    else {str(code) for code in counties if len(code) == 5 and code.startswith(prefix)}
                )
                missing = needed - {str(code) for code in lines["county"]}
                if not needed or missing:
                    print(f"{key}: files for {sorted(missing)} missing, skipped")
                    continue
            election, district_area = conn.execute(
                "select election_id, district_area_id from contest where id = %s", [contest]
            ).fetchone() or (None, None)
            options = conn.execute(
                """select o.id, o.kind, o.ballot_label,
                          (select n.text from candidacy cy join name n on n.entity_id = cy.person_id and n.is_preferred
                           where cy.option_id = o.id and cy.role = 'head' limit 1),
                          (select sum(v.value)::bigint from result_value v
                           where v.result_set_k = any(%s) and v.option_k = o.k and v.measure = 1)
                   from contest_option o where o.contest_id = %s and o.parent_option_id is null""",
                [
                    [
                        k
                        for (k,) in conn.execute(
                            "select k from result_set where contest_id = %s and is_default", [contest]
                        ).fetchall()
                    ],
                    contest,
                ],
            ).fetchall()
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            picked: dict[str, UUID | str] = {}
            dropped: list[str] = []
            for candidate, write_in, votes in (
                lines.group_by("candidate", "write_in", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                words = [
                    word
                    for word in slug(re.sub(r"\(.*?\)|\".*?\"", "", candidate)).split("-")
                    if word not in {"jr", "sr", "ii", "iii", "iv"}
                ] or ["-"]
                same = [
                    option
                    for option, kind, label, person, _ in options
                    if kind != "write_in"
                    and any(words[-1] in slug(text).split("-") for text in (label, person) if text)
                ]
                for pattern, kind in (
                    (r"(?i)uncommitted|uninstructed|no preference|noncommitted", "uncommitted"),
                    (r"(?i)^none of", "none_of_the_above"),
                ):
                    if re.search(pattern, candidate):
                        same = [option for option, known, _, _, _ in options if known == kind]
                if len(same) > 1:
                    same = [
                        option
                        for option, _, label, person, _ in options
                        if option in same
                        and any(slug(text).split("-")[0] == words[0] for text in (label, person) if text)
                    ]
                if not same:
                    same = [
                        option
                        for option, kind, label, _, _ in options
                        if kind != "write_in" and label and slug(label).startswith(slug(candidate))
                    ]
                if not same and votes:
                    same = [
                        option
                        for option, kind, _, _, total in options
                        if kind != "write_in" and total == votes and option not in picked.values()
                    ]
                if len(same) == 1:
                    picked[candidate] = same[0]
                elif write_in or any(
                    slug(candidate) in names or words[-1] in (name.split("-")[-1] for name in names)
                    for party, names in ballots[(state, day, office, district)].items()
                    if party != rows["party"][0]
                ):
                    pooled = [option for option, kind, _, _, _ in options if kind == "write_in"]
                    picked[candidate] = pooled[0] if len(pooled) == 1 else f"{key}:write-in:{slug(candidate)}"
                elif not votes:
                    dropped.append(candidate)
                else:
                    break
            else:
                candidate = None
            if candidate is not None:
                print(f"{key}: {candidate} matches no single option, skipped")
                continue
            lines = lines.filter(~pl.col("candidate").is_in(dropped))
            totals = (
                lines.with_columns(option=pl.col("candidate").replace_strict({k: str(v) for k, v in picked.items()}))
                .group_by("option")
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
            )
            kept = {str(option) for option, kind, _ in outcomes if kind in ("won", "advanced", "elected")}
            leaders = set(totals["option"].head(len(kept)))
            if leaders != kept and key not in CHECKED:
                print(f"{key}: the returns lead with {leaders}, the outcomes name {kept}, skipped")
                continue
            known = {str(option): total for option, _, _, _, total in options}
            drift = [
                (option, votes, known.get(option))
                for option, votes in totals.iter_rows()
                if known.get(option) is not None
                and abs(votes - (known[option] or 0)) > 10 + (known[option] or 0) // 100
            ]
            if drift and key not in CHECKED:
                print(f"{key}: drift from the earlier default {drift}, skipped")
                continue
            for candidate, option in list(picked.items()):
                if isinstance(option, str):
                    picked[candidate] = cs.key("us.option", option)
                    if picked[candidate] in cs.minted:
                        cs.add(
                            "contest_option",
                            id=picked[candidate],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label=candidate,
                        )
            result_set = cs.key(
                f"{SOURCE}.result_set",
                key
                if revised is None
                else f"{key}:r{int(found[1]) + 1 if (found := re.search(r':r(\d+)$', revised[0])) else 2}",
            )
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add(
                    "result_set",
                    **{
                        **dict(zip(columns, row, strict=True)),
                        **({"status": "superseded"} if revised is not None and row[0] == revised[1] else {}),
                    },
                    is_default=False,
                )
            if revised is not None:
                for option, kind, seats in outcomes:
                    cs.add("outcome", result_set_id=revised[1], option_id=option, kind=kind, seats=seats)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[lines["url"][0]],
                is_default=True,
                supersedes_id=None if revised is None else revised[1],
            )
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            members: dict[str, str] = {}
            if fine.height:
                for county, precinct in lines.select("county", "precinct").unique().iter_rows():
                    code = f"2024:{state}:{county}:{counties[county][2].upper()}:{precinct}"
                    members[f"{county}\x1f{precinct}"] = str(cs.unit("us.precinct", code, on=day))
                    precincts[members[f"{county}\x1f{precinct}"]] = (code, day, county)
            for county in lines["county"].unique().to_list():
                if fine.height:
                    continue
                if office != "house" or not district:
                    members[county] = str(counties[county][0])
                    continue
                part = f"{day.year}:{county}:{district}"
                members[county] = str(cs.unit("us.county_part", part, on=day))
                parts[members[county]] = (state, part, day, county, district_area)
            values.append(
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=(
                        pl.concat_str("county", "precinct", separator="\x1f") if fine.height else pl.col("county")
                    ).replace_strict(members),
                    option_id=pl.col("candidate").replace_strict({k: str(v) for k, v in picked.items()}),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.when(pl.col("url").is_in([KENTUCKY, *ARIZONA, CLARK])).then(8).otherwise(0).cast(pl.Int16),
                    value="votes",
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(pl.col("value").sum())
            )
            for url, number, county, candidate, x0, y0, x1, y1 in (
                lines.filter(pl.col("page").is_not_null())
                .select("url", "page", "county", "candidate", "x0", "y0", "x1", "y1")
                .iter_rows()
            ):
                if (url, number) not in pages:
                    pdf = raw.cached(SOURCE, url)
                    image = PAGES / f"{pdf.sha256}-{number}.png"
                    if not image.exists():
                        PAGES.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                            + [str(pdf.path), str(image.with_suffix(""))],
                            check=True,
                        )
                    width, height = struct.unpack(">II", image.read_bytes()[16:24])
                    page = cs.page(artifacts[url], number)
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifacts[url],
                        page_number=number,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                    pages[(url, number)] = (page, width * 3, height * 3)
                page, width, height = pages[(url, number)]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=UUID(members[county]),
                    option_id=UUID(str(picked[candidate])),
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=page,
                    source_id=None,
                    x0=x0 / width,
                    y0=y0 / height,
                    x1=x1 / width,
                    y1=y1 / height,
                )
        for unit, (code, day, county) in precincts.items():
            if UUID(unit) in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=UUID(unit),
                    polity_id=cs.polity(f"US-{code.split(':')[1]}"),
                    scheme_code="us.precinct",
                    code=code,
                    parent_id=counties[county][0],
                    area_relation="within",
                    area_id=counties[county][1],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
        for unit, (state, part, day, county, district_area) in parts.items():
            if UUID(unit) in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=UUID(unit),
                    polity_id=cs.polity(f"US-{state}"),
                    scheme_code="us.county_part",
                    code=part,
                    parent_id=counties[county][0],
                    area_relation="within",
                    area_id=counties[county][1],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                cs.add(
                    "reporting_unit_area",
                    unit_id=UUID(unit),
                    area_id=district_area,
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
        for unit, district_area, day in conn.execute(
            """with house as (
                 select r.k, c.district_area_id, e.date_from
                 from result_set r join contest c on c.id = r.contest_id join election e on e.id = r.election_id
                 join changeset h on h.id = r.changeset_id and h.pipeline = %s
                 join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value ~ '^[0-9-]+:[A-Z]{2}:house:'
                 where r.is_default and c.district_area_id is not null
               ),
               cast_votes as (
                 select distinct house.district_area_id, house.date_from, u.id
                 from house join result_value v on v.result_set_k = house.k and v.value > 0
                 join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
               ),
               split as (select id from cast_votes group by id having count(distinct district_area_id) > 1)
               select c.id, c.district_area_id, c.date_from from cast_votes c
               where not exists (
                 select from cast_votes s where s.district_area_id = c.district_area_id
                   and s.date_from = c.date_from and s.id in (select id from split)
               )
               and not exists (
                 select from reporting_unit_area a where a.unit_id = c.id and a.area_id = c.district_area_id
               )
               order by 3, 2, 1""",
            [SOURCE],
        ).fetchall():
            cs.add(
                "reporting_unit_area",
                unit_id=unit,
                area_id=district_area,
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
