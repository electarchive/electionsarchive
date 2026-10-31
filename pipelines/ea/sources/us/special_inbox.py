import io
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime

import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "us.elstats_specials"
INBOX = DATA / "inbox" / "manual" / "MF-288"
WISCONSIN = "https://elections.wi.gov/elections/election-results/results-all#County%20by%20County%20Report_Representative%20in%20Congress%20District%207_Spring%20Primary_2_18_20.pdf"
OREGON = "https://sos.oregon.gov/elections/Pages/electionsstatistics.aspx#2011%20Special%20Primary%20Election%20Official%20Results.PDF"
ARKANSAS = (
    "https://www.sos.arkansas.gov/elections/research/election-results#2001_Special_Primary_US_Congress_District_3.pdf"
)
ARKANSAS_RUNOFFS = {
    "Democratic": "https://www.sos.arkansas.gov/elections/research/election-results#runoff24-dem.zip",
    "Republican": "https://www.sos.arkansas.gov/elections/research/election-results#runoff24-rep.zip",
}
SOUTH_CAROLINA = {
    "2013-03-19": ("https://www.enr-scvotes.org/SC/45550/#detail.xls", "MF-289/2013-03-19/detailxls (3).zip"),
    "2013-03-22": ("https://www.enr-scvotes.org/SC/46058/#detail.xls", "MF-289/2013-03-22/detailxls (4).zip"),
    "2013-04-02": ("https://www.enr-scvotes.org/SC/46107/116099/#detail.xls", "MF-448/detailxls.zip"),
    "2017-05-02": ("https://www.enr-scvotes.org/SC/68939/#detail.xls", "MF-289/2017-05-02/detailxls (2).zip"),
    "2017-05-16": ("https://www.enr-scvotes.org/SC/69738/#detail.xls", "MF-289/2017-05-16/detailxls.zip"),
}
OHIO = {
    "2005-06-14": {
        "Democratic": "2005_democratic_2nd_congressional_district_june_14.xlsx",
        "Republican": "2005_republican_2nd_congressional_district_june_14.xlsx",
    },
    "2007-11-06": {
        "Democratic": "2007_democratic_fifth_congressional_district_november_6_breakdown_by_county_table_2.xlsx",
        "Republican": "2007_republican_fifth_congressional_district_november_6_official_results_table_2.xlsx",
    },
}
OHIO_SPECIALS = {
    "2018-05-08": ("12", 0, {"Democratic": "summarydem.xlsx", "Republican": "summaryrep.xlsx"}),
    "2024-03-19": (
        "6",
        -1,
        {
            "Democratic": "summary-level-official-results-primary-2024---democratic.xlsx",
            "Republican": "summary-level-results-primary-2024---republican.xlsx",
        },
    ),
}
ARIZONA_URL = "https://azsos.gov/elections/results-data/election-results#MF-295/{}/{}"
ARIZONA_SPECIALS = {
    "2012-04-17": (
        "Canvass2012SpecialPrimary.pdf",
        ("Cochise", "Pima", "Pinal", "Santa Cruz"),
        {
            ("Democratic", "Ron Barber"): (4915, 37786, 1068, 416, 44185),
            ("Green", "Richard Grayson"): (1, 1, 0, 0, 2),
            ("Green", "Charlie Manolakis"): (13, 101, 0, 2, 116),
            ("Republican", "Frank Antenori"): (2483, 14415, 412, 187, 17497),
            ("Republican", "Jesse Kelly"): (4779, 21065, 1051, 206, 27101),
            ("Republican", "Martha McSally"): (3132, 15194, 943, 144, 19413),
            ("Republican", "Dave Sitton"): (843, 12054, 303, 99, 13299),
        },
        {"Ron Barber", "Charlie Manolakis", "Jesse Kelly"},
    ),
    "2018-02-27": (
        "2018_0307_official_canvass_special_primary_election.pdf",
        ("Maricopa",),
        {
            ("Democratic", "Kyle Martin"): (22, 22),
            ("Democratic", "Hiral Tipirneni"): (23175, 23175),
            ("Democratic", "Brianna Westbrook"): (15288, 15288),
            ("Green", "Richard Grayson"): (13, 13),
            ("Green", "Gary Swing"): (13, 13),
            ("Libertarian", "Kelly Noble"): (22, 22),
            ("Republican", "Chad Allen"): (824, 824),
            ("Republican", "Brenden Dilley"): (823, 823),
            ("Republican", "Stephen Dolgos"): (377, 377),
            ("Republican", "Debbie Lesko"): (27047, 27047),
            ("Republican", 'David "Dave" Lien'): (1341, 1341),
            ("Republican", "Phil Lovas"): (18562, 18562),
            ("Republican", "Richard Mack"): (1191, 1191),
            ("Republican", "Patrick Masoya"): (5, 5),
            ("Republican", "Steve Montenegro"): (18106, 18106),
            ("Republican", "Bob Stump"): (4032, 4032),
            ("Republican", "Christopher Sylvester"): (1490, 1490),
            ("Republican", "Blake Taylor"): (2, 2),
            ("Republican", "Clair Van Steenwyk"): (1787, 1787),
            ("Republican", "Von Wehunt"): (1, 1),
            ("Republican", "Mark Yates"): (871, 871),
        },
        {"Hiral Tipirneni", "Debbie Lesko"},
    ),
}
UTAH_URL = "https://vote.utah.gov/wp-content/uploads/2023/09/2017-Primary-Election-Canvass-1.pdf"
UTAH_2017 = {
    "Tanner Ainge": (192, 135, 331, 5519, 246, 10113, 1029, 17565),
    "John Curtis": (354, 247, 195, 7568, 227, 21733, 1157, 31481),
    "Christopher Herrod": (371, 259, 346, 6112, 755, 14868, 975, 23686),
}
UTAH_2017_COUNTIES = ("Carbon", "Emery", "Grand", "Salt Lake", "San Juan", "Utah", "Wasatch")
OHIO_2005 = {
    "Democratic": [
        "Paul Hackett",
        "Arthur Stanley Katz",
        "James John Parker",
        "Charles W. Sanders",
        "Jeff Sinnard",
        "Victoria Wells Wulsin",
    ],
    "Republican": [
        "Steve Austin",
        "Tom Bemmes",
        "Tom Brinkman Jr.",
        "Pat DeWine",
        "Peter A. Fossett",
        "Bob McEwen",
        "Eric Minamyer",
        "Douglas E. Mink",
        "Jeff Morgan",
        "Jean Schmidt",
        "David R. Smith",
    ],
}
OHIO_URL = "https://data.ohiosos.gov/portal/past-election-results#MF-290/{}/{}"
FILES = {
    WISCONSIN: INBOX
    / "2020-02-18"
    / "County%20by%20County%20Report_Representative%20in%20Congress%20District%207_Spring%20Primary_2_18_20.pdf",
    OREGON: INBOX / "2011-11-08" / "2011 Special Primary Election Official Results.PDF",
    ARKANSAS: INBOX / "2001" / "2001_Special_Primary_US_Congress_District_3.pdf",
    ARKANSAS_RUNOFFS["Democratic"]: INBOX / "2001" / "runoff24-dem.zip",
    ARKANSAS_RUNOFFS["Republican"]: INBOX / "2001" / "runoff24-rep.zip",
    **{url: INBOX.parent / name for url, name in SOUTH_CAROLINA.values()},
    **{
        OHIO_URL.format(day, name): INBOX.parent / "MF-290" / day / name
        for day, files in OHIO.items()
        for name in files.values()
    },
    **{
        ARIZONA_URL.format(day, name): INBOX.parent / "MF-295" / day / name
        for day, (name, *_) in ARIZONA_SPECIALS.items()
    },
    UTAH_URL: INBOX.parent / "MF-295" / "2017-08-15" / "2017-Primary-Election-Canvass-1.pdf",
    **{
        OHIO_URL.format(day, name).replace("MF-290", "MF-293"): INBOX.parent / "MF-293" / day / name
        for day, (_, _, files) in OHIO_SPECIALS.items()
        for name in files.values()
    },
}
WISCONSIN_CANDIDATES = {
    "DEMOCRATIC": ("Democratic", ["Tricia Zunker", "Lawrence Dale", "Scattering"]),
    "REPUBLICAN": ("Republican", ["Tom Tiffany", "Jason Church", "Michael Opela", "Scattering"]),
}
OREGON_CANDIDATES = {
    "Democrat": (
        "Democratic",
        [
            "Dominic Hammon",
            "Suzanne Bonamici",
            "Brad Witt",
            "Saba Ahmed",
            "Brad Avakian",
            "Dan Strite",
            "Robert E Lettin",
            "Todd Lee Ritter",
            "Scattering",
        ],
    ),
    "Republican": (
        "Republican",
        ["Lisa Michaels", "DR Delgado-Morgan", "Pavel Goberman", "Jim Greenfield", "Rob Cornilles", "Scattering"],
    ),
}
ARKANSAS_CANDIDATES = {
    "Boozman": "John Boozman",
    "Cates": "Brad Cates",
    "DeLay": "Gunner DeLay",
    "Hendren": "Jim Hendren",
    "Carson": "Jo Carson",
    "Hathorn": "Mike Hathorn",
    "Williams": 'Norman "Bill" Williams Jr.',
}


def fetch() -> None:
    for url, path in FILES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=path.suffix.lower(),
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/zip" if path.suffix == ".zip" else "application/pdf",
            )


def text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
    ).stdout


def rows() -> list[dict[str, object]]:
    found = []

    def add(state: str, day: str, district: str, party: str, candidate: str, county: str, votes: int) -> None:
        found.append(
            {
                "state": state,
                "election_date": day,
                "district_name": district,
                "candidate_name": candidate,
                "candidate_party_name": party,
                "division_name": county,
                "votes": str(votes),
                "outcome": "Defeated",
                "runoff": False,
                "primary": party,
            }
        )

    party = None
    totals: dict[str, int] = {}
    tally: dict[str, list[int]] = {}
    for line in text(WISCONSIN).splitlines():
        if hit := re.match(r"^REPRESENTATIVE IN CONGRESS DISTRICT 7 - (\w+)", line):
            party = hit[1]
            tally = {}
        elif party in WISCONSIN_CANDIDATES and (
            hit := re.match(r"^\s*(Office Totals:|[A-Z][A-Z. ]+?)\s+([\d,]+(?:\s+[\d,]+)+)\s*$", line)
        ):
            label, names = hit[1], WISCONSIN_CANDIDATES[party][1]
            values = [int(value.replace(",", "")) for value in hit[2].split()]
            if len(values) != len(names) + 1 or sum(values[1:]) != values[0]:
                raise ValueError(f"WI-7 2020 primary {party} {label}: {values}")
            if label == "Office Totals:":
                for index, name in enumerate(names):
                    if sum(row[index] for row in tally.values()) != values[index + 1]:
                        raise ValueError(f"WI-7 2020 primary {party} {name}: counties do not sum")
                party = None
            else:
                tally[label] = values[1:]
                for name, count in zip(names, values[1:], strict=True):
                    add("WI", "2020-02-18", "7", WISCONSIN_CANDIDATES[party][0], name, label.title(), count)

    party = None
    tally = {}
    for line in text(OREGON).splitlines():
        if line.strip() in OREGON_CANDIDATES:
            party, tally = line.strip(), {}
        elif party is not None and (hit := re.match(r"^\s*([A-Za-z]+|TOTAL)\s+((?:[\d,]+\s*)+)$", line)):
            names = OREGON_CANDIDATES[party][1]
            values = [int(value.replace(",", "")) for value in hit[2].split()]
            if len(values) != len(names):
                raise ValueError(f"OR-1 2011 primary {party} {hit[1]}: {values}")
            if hit[1] == "TOTAL":
                for index, name in enumerate(names):
                    if sum(row[index] for row in tally.values()) != values[index]:
                        raise ValueError(f"OR-1 2011 primary {party} {name}: counties do not sum")
                party = None
            else:
                tally[hit[1]] = values
                for name, count in zip(names, values, strict=True):
                    add("OR", "2011-11-08", "1", OREGON_CANDIDATES[party][0], name, hit[1], count)

    party = None
    columns: list[str] = []
    tally = {}
    seen: set[str] = set()
    for line in text(ARKANSAS).splitlines():
        if hit := re.match(r"^\s*(Republican|Democrat) Special Primary\s*$", line):
            party, columns, tally = hit[1], [], {}
        elif party is not None and (hit := re.match(r"^County\s+(.+?)\s*$", line)):
            columns = hit[1].split()
        elif party is not None and columns and (hit := re.match(r"^(?:(\w+) County|Total)\s+((?:\d+\s*)+)$", line)):
            values = [int(value) for value in hit[2].split()]
            if len(values) != len(columns):
                raise ValueError(f"AR-3 2001 special primary {party} {hit[1]}: {values}")
            if hit[1] is None:
                if party in seen:
                    continue
                for index, column in enumerate(columns):
                    if sum(row[index] for row in tally.values()) != values[index]:
                        raise ValueError(f"AR-3 2001 special primary {column}: counties do not sum")
                seen.add(party)
                party = None
            else:
                tally[hit[1]] = values
                for column, count in zip(columns, values, strict=True):
                    add(
                        "AR",
                        "2001-09-25",
                        "3",
                        "Democratic" if party == "Democrat" else party,
                        ARKANSAS_CANDIDATES[column],
                        hit[1],
                        count,
                    )
    for party, url in ARKANSAS_RUNOFFS.items():
        with zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, url).path.read_bytes())) as archive:
            lines = archive.read("runoff24.txt").decode("latin-1").replace("|Office|", "|\nOffice|").splitlines()
        counties: list[str] = []
        runoff: dict[str, list[int]] = {}
        for line in lines:
            cells = line.rstrip("|").split("|")
            if cells[0] == "Office":
                counties = [cell.removesuffix(" County") for cell in cells[3:]]
            elif len(cells) == len(counties) + 3:
                runoff[re.sub(r"^State (Senator|Representative) ", "", cells[1])] = [int(cell) for cell in cells[3:]]
        winner = max(runoff, key=lambda name: sum(runoff[name]))
        for name, values in runoff.items():
            for county, count in zip(counties, values, strict=True):
                add("AR", "2001-10-23", "3", party, name, county, count)
                found[-1]["runoff"] = True
                found[-1]["outcome"] = "Elected" if name == winner else "Defeated"
    sheet = "{urn:schemas-microsoft-com:office:spreadsheet}"
    for day, (url, _) in SOUTH_CAROLINA.items():
        with zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, url).path.read_bytes())) as archive:
            workbook = ET.fromstring(archive.read("detail.xls"))
        for worksheet in workbook.iter(f"{sheet}Worksheet"):
            table = [
                [cell.findtext(f"{sheet}Data") or "" for cell in row.iter(f"{sheet}Cell")]
                for row in worksheet.iter(f"{sheet}Row")
            ]
            hit = re.match(
                r"^U\.S\.\s+House of Representatives, District\s+(\d+) - (REP|DEM)",
                table[0][0] if table and table[0] else "",
            )
            if hit is None or (day == "2013-03-19" and hit[2] == "REP"):
                continue
            names = [cell for cell in table[1][2:] if cell]
            runoff = day in ("2013-04-02", "2017-05-16")
            block = []
            sums = [0] * len(names)
            for line in table[3:]:
                if line[0].startswith("Total"):
                    if [int(line[3 + 2 * index]) for index in range(len(names))] != sums:
                        raise ValueError(f"SC-{hit[1]} {day} {hit[2]}: counties do not sum")
                    continue
                for index, name in enumerate(names):
                    sums[index] += int(line[3 + 2 * index])
                    add(
                        "SC",
                        "2013-03-19" if day == "2013-03-22" else day,
                        hit[1],
                        "Republican" if hit[2] == "REP" else "Democratic",
                        name,
                        line[0],
                        int(line[3 + 2 * index]),
                    )
                    block.append(found[-1])
            qualifiers = {names[index] for index in sorted(range(len(names)), key=sums.__getitem__)[-2:]}
            for row in block:
                row["runoff"] = runoff
                if hit[2] == "REP" and not runoff and row["candidate_name"] in qualifiers:
                    row["outcome"] = "Runoff"
    for day, files in OHIO.items():
        for party, name in files.items():
            cells = pl.read_excel(
                raw.cached(SOURCE, OHIO_URL.format(day, name)).path, has_header=False, engine="calamine"
            ).rows()
            block = []
            if day == "2005-06-14":
                names = OHIO_2005[party]
                for line in cells[2:-1]:
                    for candidate, votes in zip(names, line[1:], strict=True):
                        add("OH", day, "2", party, candidate, line[0].title(), int(votes.replace(",", "")))
                        block.append(found[-1])
                totals = [int(value.replace(",", "")) for value in cells[-1][1:]]
                if [
                    sum(int(str(row["votes"])) for row in block if row["candidate_name"] == candidate)
                    for candidate in names
                ] != totals:
                    raise ValueError(f"OH-2 2005 {party}: counties do not sum")
            else:
                county = None
                for line in cells[1:]:
                    if line[0] and not line[0].startswith("*") and line[1] is None:
                        county = line[0].rstrip("*")
                    elif county and line[1] and "(" in line[1] and "total" not in line[1]:
                        candidate = re.sub(r"^\*|\s*\([A-Z]\)$", "", line[1])
                        last, first = candidate.split(", ")
                        add("OH", day, "5", party, f"{first} {last}", county, int(str(line[3]).replace(",", "")))
                        block.append(found[-1])
            top = max(
                {row["candidate_name"] for row in block},
                key=lambda candidate: sum(
                    int(str(row["votes"])) for row in block if row["candidate_name"] == candidate
                ),
            )
            for row in block:
                row["outcome"] = "Elected" if row["candidate_name"] == top else "Defeated"
    for day, (district, which, files) in OHIO_SPECIALS.items():
        for party, name in files.items():
            sheet = pl.read_excel(
                raw.cached(SOURCE, OHIO_URL.format(day, name).replace("MF-290", "MF-293")).path,
                sheet_name="U.S. Congress",
                has_header=False,
                engine="calamine",
            ).rows()
            starts = [
                index
                for index, cell in enumerate(sheet[0])
                if cell is not None and cell.strip().endswith(f"District {district.zfill(2)}")
            ]
            start = starts[which]
            end = next(
                (index for index in range(start + 1, len(sheet[0])) if sheet[0][index] is not None), len(sheet[0])
            )
            block = []
            for column in range(start, end):
                candidate = re.sub(r"\s*(\(WI\)\*)?\s*\([A-Z]\)$", "", str(sheet[1][column]))
                for line in sheet[4:]:
                    add("OH", day, district, party, candidate, line[0], int(str(line[column]).replace(",", "")))
                    block.append(found[-1])
                if sum(int(str(row["votes"])) for row in block if row["candidate_name"] == candidate) != int(
                    str(sheet[2][column]).replace(",", "")
                ):
                    raise ValueError(f"OH-{district} {day} {party} {candidate}: counties do not sum")
            top = max(
                {row["candidate_name"] for row in block},
                key=lambda candidate: sum(
                    int(str(row["votes"])) for row in block if row["candidate_name"] == candidate
                ),
            )
            for row in block:
                row["outcome"] = "Elected" if row["candidate_name"] == top else "Defeated"
    for day, (_, counties, tables, winners) in ARIZONA_SPECIALS.items():
        for (party, candidate), votes in tables.items():
            if sum(votes[:-1]) != votes[-1]:
                raise ValueError(f"AZ-8 {day} {candidate}: counties do not sum to {votes[-1]}")
            for county, count in zip(counties, votes, strict=False):
                add("AZ", day, "8", party, candidate, county, count)
                found[-1]["outcome"] = "Elected" if candidate in winners else "Defeated"
    for candidate, votes in UTAH_2017.items():
        if sum(votes[:-1]) != votes[-1]:
            raise ValueError(f"UT-3 2017 {candidate}: counties do not sum to {votes[-1]}")
        for county, count in zip(UTAH_2017_COUNTIES, votes, strict=False):
            add("UT", "2017-08-15", "3", "Republican", candidate, county, count)
            found[-1]["outcome"] = "Elected" if candidate == "John Curtis" else "Defeated"
    for row in found:
        if row["state"] == "AR" and not row["runoff"]:
            totals = {}
            for other in found:
                if other["state"] == "AR" and other["primary"] == row["primary"]:
                    totals[other["candidate_name"]] = totals.get(other["candidate_name"], 0) + int(str(other["votes"]))
            row["outcome"] = (
                "Runoff" if row["candidate_name"] in sorted(totals, key=totals.__getitem__)[-2:] else "Defeated"
            )
    return found
