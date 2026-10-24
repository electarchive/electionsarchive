import csv
import gzip
import html
import json
import re
import subprocess
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mayor_canvass"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
PHILADELPHIA = "https://vote.phila.gov/files/"
FILES = {
    date(2007, 11, 6): ("election-results/2007_General_WD.csv", "election-results/2007_Primary_WD.csv"),
    date(2011, 11, 8): ("election-results/2011_General_WD.csv", "election-results/2011_Primary_WD.csv"),
    date(2015, 11, 3): ("election-results/2015_GENERAL/2015_GENERAL_-_WARD_RESULTS_-_PUBLIC.xlsx", None),
    date(2019, 11, 5): ("raw-data/2019_general.csv", None),
}
NYC_FILES = "https://www.vote.nyc/sites/default/files/"
NYC = {
    date(2001, 11, 6): "downloads/pdf/results/2001/generalelection/general2001.pdf",
    date(2005, 11, 8): "downloads/pdf/results/2005/general/Manhattan/Croossover%20Mayor%20Re-Cap%20NYC.pdf",
    date(2009, 11, 3): "downloads/pdf/results/2009/General/1.11CitywideMayorRecap.pdf",
    date(2013, 11, 5): (
        "downloads/pdf/results/2013/2013GeneralElection/00001100000Citywide%20Mayor%20Citywide%20Recap.pdf"
    ),
    date(2017, 11, 7): (
        "pdf/election_results/2017/20171107General%20Election/00001100000Citywide%20Mayor%20Citywide%20Recap.csv"
    ),
}
BOROUGHS = {"NEW YORK": "36061", "BRONX": "36005", "KINGS": "36047", "QUEENS": "36081", "RICHMOND": "36085"}
LINES_2001 = (
    ("Michael R. Bloomberg", "REPUBLICAN"),
    ("Mark Green", "DEMOCRATIC"),
    ("Michael R. Bloomberg", "INDEPENDENCE"),
    ("Terrance M. Gray", "CONSERVATIVE"),
    ("Alan G. Hevesi", "LIBERAL"),
    ("Julia Willebrand", "GREEN"),
    ("Mark Green", "WORKING FAMILIES"),
    ("Alan G. Hevesi", "BETTER SCHOOLS"),
    ("Kenneth B. Golding", "AMERICAN DREAM"),
    ("Kenny Kramer", "LIBERTARIAN"),
    ("Bernard H. Goetz", "FUSION"),
    ("Thomas K. Leighton", "MARIJUANA REFORM"),
    ("Write In", "WRITE-IN"),
)
NYC_PUBLISHER = "Board of Elections in the City of New York"
NYC_TITLE = "Statement and Return Report for Certification"
NYC_NOTE = (
    "Borough totals from the Board of Elections' certified Statement and Return report, one line per ballot line as "
    "New York's fusion ballot prints them, with write-ins as one line. Ballots cast are the report's total ballots."
)
DC = {
    date(1994, 11, 8): "https://www.dcboe.org/elections/election-results-archives/november-8-general-election-1994",
    date(1998, 11, 3): "https://web.archive.org/web/19990418030534if_/http://www.dcboee.org/htmldocs/113noche.htm",
    date(2002, 11, 5): (
        "https://www.dcboe.org/elections/election-results-archives/november-5-general-election/"
        "certification-summary-candidate"
    ),
    date(2006, 11, 7): "https://www.dcboe.org/getmedia/77fd9c1b-bb65-473b-8c1c-4530af2e2f2f/Summary_General_2006.pdf",
    date(2010, 11, 2): "https://electionresults.dcboe.org/cityWide/getCityWide/2010-General-Election",
}
DC_PUBLISHER = "District of Columbia Board of Elections"
DC_TITLE = "Election results archive"
DC_NOTE = (
    "Citywide totals from the Board of Elections' final results: the 1994 and 1998 final and complete results pages "
    "(1998 from the Internet Archive's copy of the Board's old site), the 2002 certification summary, the 2006 "
    "certified summary report, and the 2010 certified citywide results. Party labels are as printed; independents "
    "carry none. Ballots cast are the reported total turnout, where given."
)
MARYLAND = "https://elections.maryland.gov/elections/archive/"
BALTIMORE = {
    date(1991, 11, 5): "baltimore/1991.html",
    date(1995, 11, 7): "baltimore/1995.html",
    date(1999, 11, 2): "baltimore/election_data/Baltimore_City_General_1999.csv",
    date(2004, 11, 2): "baltimore/election_data/Baltimore_City_General_2003.csv",
    date(2007, 11, 6): "baltimore/election_data/Baltimore_City_General_2007.csv",
    date(2011, 11, 8): "baltimore/election_data/Baltimore_City_County_2011_General.csv",
    date(2016, 11, 8): "2016/election_data/Baltimore_City_County_2016_General.csv",
    date(2020, 11, 3): "2020/election_data/Baltimore_City_County_2020_General.csv",
}
BALTIMORE_PUBLISHER = "Maryland State Board of Elections"
BALTIMORE_TITLE = "Baltimore City election results"
BALTIMORE_NOTE = (
    "Citywide totals from the State Board of Elections' Baltimore City results: its 1991 and 1995 results pages and "
    "its county-level data files from 1999 on (the 2003 cycle's general was held on November 2, 2004). Party labels "
    "are as printed; the 1991 and 1995 pages print none for the general, so the Democratic and Republican nominees' "
    "parties come from the same page's primary winners. Unaffiliated candidates carry none, and named write-in "
    "candidates are kept with the party the file prints."
)
ALLEGHENY = "https://www.alleghenycounty.us/files/assets/county/v/1/government/elections/documents/elections-archive/"
CLARITY = "https://results.enr.clarityelections.com/PA/Allegheny/"
OLD_SITE = "https://web.archive.org/web/{}if_/http://www.county.allegheny.pa.us:80/elect/{}11gen/muni188.asp"
PITTSBURGH = {
    date(2001, 11, 6): OLD_SITE.format(20021203230933, 2001),
    date(2005, 11, 8): OLD_SITE.format(20061003035211, 2005).replace(":80", ""),
    date(2007, 11, 6): ALLEGHENY + "2007-general-summary.pdf",
    date(2009, 11, 3): ALLEGHENY + "2009-general-summary-reg-1-2-4-7.pdf",
    date(2013, 11, 5): ALLEGHENY + "2013-general-summary.pdf",
    date(2017, 11, 7): CLARITY + "71801/193474/json/en/summary.json",
    date(2021, 11, 2): CLARITY + "111176/285575/json/en/summary.json",
}
PITTSBURGH_PUBLISHER = "Allegheny County Elections Division"
PITTSBURGH_TITLE = "Official election results"
PITTSBURGH_NOTE = (
    "Citywide totals from the Allegheny County Elections Division's official results: its results pages for 2001 and "
    "2005 (from the Internet Archive's copies of the county's old site), its official summary reports for 2007, 2009, "
    "and 2013, and its county-certified results site for 2017 and 2021. Party labels are as printed; independents and "
    "labels the county does not spell out carry none."
)
ERIE = "https://elections.erie.gov/Files/Election%20Results/"
BUFFALO = {
    date(2001, 11, 6): ("2001/2001%20GENERAL%20ELECTION%20results.xls", "Mayor"),
    date(2005, 11, 8): ("2005/2005%20GENERAL%20ELECTION.xls", "BuffMayor"),
    date(2009, 11, 3): ("2009/11032009/2009%20GENERAL%20ELECTION.xlsx", "BuffMayor"),
    date(2013, 11, 5): ("2013/11052013/2013%20General%20Election%20Resultsnew.xlsx", "BuffMayor"),
    date(2017, 11, 7): ("2017/2017-General-Election-Web.xlsx", "BuffMayor"),
    date(2021, 11, 2): ("2021/2021%20General%20Canvass%20Bookup.xlsx", "Buffalo Mayor"),
}
BUFFALO_PUBLISHER = "Erie County Board of Elections"
BUFFALO_TITLE = "Election results archive"
BUFFALO_NOTE = (
    "Citywide totals from the Erie County Board of Elections' official results workbooks, one line per ballot line as "
    "New York's fusion ballot prints them. Named write-in candidates are kept as candidates without a party; the "
    "workbooks' blank, void, and scattering column is left out."
)
MECKBOE = "https://web.archive.org/web/{}if_/http://meckboe.org/pages/Election/e_info/general{}/off1.html"
NCSBE = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/"
CHARLOTTE = {
    date(1999, 11, 2): MECKBOE.format(20101219103451, "99"),
    date(2001, 11, 6): MECKBOE.format(20081204161819, "01"),
    date(2003, 11, 4): MECKBOE.format(20101219114733, "03"),
    date(2005, 11, 8): MECKBOE.format(20101125230849, "05"),
    date(2011, 11, 8): NCSBE + "2011_11_08/results_pct_20111108.zip",
    date(2013, 11, 5): NCSBE + "2013_11_05/results_sort_20131105.zip",
}
CHARLOTTE_NOTE = (
    "Citywide totals from official results: the Mecklenburg County Board of Elections' official results pages for "
    "1999 to 2005 (from the Internet Archive's copies of its old site), and the State Board of Elections' precinct "
    "results files for 2011 and 2013, summed, with 2013's named write-ins counted in the write-ins line. Party labels "
    "are as printed; the 1999 and 2001 pages print none."
)
RHODE_ISLAND = "https://rigov.s3.amazonaws.com/election/results/{}/general_election/providence.json"
PROVIDENCE = {
    date(2002, 11, 5): "https://web.archive.org/web/20090416063146id_/http://www.elections.ri.gov/downloads/LOCAL.zip",
    date(2006, 11, 7): (
        "https://web.archive.org/web/20090416062619id_/"
        "http://www.elections.ri.gov/elections/results/2006/generalelection/sumprov.php"
    ),
    date(2010, 11, 2): RHODE_ISLAND.format(2010),
    date(2014, 11, 4): RHODE_ISLAND.format(2014),
    date(2018, 11, 6): RHODE_ISLAND.format(2018),
}
PROVIDENCE_NOTE = (
    "Citywide totals from the Rhode Island Board of Elections' results for the city of Providence: its 2002 local "
    "precinct workbook (summed) and 2006 Providence summary page, both from the Internet Archive's copies of its old "
    "site, and its results files from 2010 on. Party labels are "
    "as printed; independents carry none. The 2014 file reports 79 of 80 precincts."
)
LOUISIANA = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob={:%Y%m%d}/{}"
NEW_ORLEANS = [date(2017, 11, 18), date(2021, 11, 13)]
NEW_ORLEANS_NOTE = (
    "Citywide totals from the Louisiana Secretary of State's official results for Orleans Parish, with each "
    "candidate's party as printed on the ballot; independents and candidates with no party carry none. Ballots cast "
    "are the race's reported voters."
)
SURNAMES = {"clarke": "clark"}
PARTIES = {
    "D": ("democratic-party", "Democratic"),
    "DEM": ("democratic-party", "Democratic"),
    "DEMOCRATIC": ("democratic-party", "Democratic"),
    "R": ("republican-party", "Republican"),
    "REP": ("republican-party", "Republican"),
    "REPUBLICAN": ("republican-party", "Republican"),
    "SOCIALIST WORKERS PARTY": ("socialist-workers-party", "Socialist Workers"),
    "SOCIALIST WORKERS": ("socialist-workers-party", "Socialist Workers"),
    "SOCIALIST WORKER": ("socialist-workers-party", "Socialist Workers"),
    "CONSERVATIVE": ("NY:conservative-party", "Conservative"),
    "WORKING FAMILIES": ("NY:working-families-party", "Working Families"),
    "INDEPENDENCE": ("NY:independence-party", "Independence"),
    "LIBERAL": ("NY:liberal", "Liberal"),
    "GREEN": ("green-party", "Green"),
    "LIBERTARIAN": ("libertarian-party", "Libertarian"),
    "MARIJUANA REFORM": ("NY:marijuana-reform-party", "Marijuana Reform"),
    "REFORM": ("NY:reform-party", "Reform"),
    "FUSION": ("NY:fusion", "Fusion"),
    "RENT IS TOO HIGH": ("NY:rent-is-2-damn-high", "Rent Is Too Damn High"),
    "RENT IS TOO DAMN HIGH": ("NY:rent-is-2-damn-high", "Rent Is Too Damn High"),
    "RENT IS 2 DAMN HIGH": ("NY:rent-is-2-damn-high", "Rent Is Too Damn High"),
    "SOCIALISM & LIB": ("party-for-socialism-and-liberation", "Socialism and Liberation"),
    "DC STATEHOOD": ("DC:d-c-statehood", "D.C. Statehood"),
    "D.C. STATEHOOD": ("DC:d-c-statehood", "D.C. Statehood"),
    "STG": ("DC:d-c-statehood-green-party", "D.C. Statehood Green"),
    "SG": ("DC:d-c-statehood-green-party", "D.C. Statehood Green"),
    "SOC": ("socialist-workers-party", "Socialist Workers"),
    "GRN": ("green-party", "Green"),
    "GRE": ("green-party", "Green"),
    "DEMOCRAT": ("democratic-party", "Democratic"),
    "WOMEN'S EQUALITY": ("NY:women-s-equality-party", "Women's Equality"),
    "REPUBLICAN/STRONG LEADERSHIP": ("republican-party", "Republican and Strong Leadership"),
    "PROGRESSIVE": (None, "Progressive"),
    "LIB": ("libertarian-party", "Libertarian"),
    "DEM/REP": ("democratic-party", "Democratic and Republican"),
    "WCP": (None, "Working Class"),
    "REPUBLICAN\\LIBERAL": ("republican-party", "Republican and Liberal"),
    "INDEPENDENCE/JOBS & EDUCATION": ("NY:independence-party", "Independence and Jobs & Education"),
}
PUBLISHER = "Philadelphia City Commissioners"
TITLE = "Election results by ward and division"
NOTE = (
    "Citywide totals summed from the City Commissioners' division results. Party labels are as printed in the "
    "results file; for 2007 and 2011, whose general election files print no party, the Democratic and Republican "
    "nominees' parties come from the same year's primary results file, and other candidates carry none."
)


def fetch() -> None:
    for files in FILES.values():
        for name in files:
            if name is not None:
                raw.fetch(SOURCE, PHILADELPHIA + name, suffix=Path(name).suffix)
    for name in NYC.values():
        raw.fetch(SOURCE, NYC_FILES + name, suffix=Path(name).suffix)
    for day, link in DC.items():
        raw.fetch(SOURCE, link, suffix=".pdf" if link.endswith(".pdf") else ".json" if day.year == 2010 else ".html")
    for name in BALTIMORE.values():
        raw.fetch(SOURCE, MARYLAND + name, suffix=Path(name).suffix)
    for link in PITTSBURGH.values():
        raw.fetch(SOURCE, link, suffix=Path(link).suffix if link.endswith((".pdf", ".json")) else ".html")
    for name, _ in BUFFALO.values():
        raw.fetch(SOURCE, ERIE + name, suffix=Path(name).suffix)
    for link in CHARLOTTE.values():
        raw.fetch(SOURCE, link, suffix=".zip" if link.endswith(".zip") else ".html")
    for link in PROVIDENCE.values():
        raw.fetch(SOURCE, link, suffix=Path(link).suffix if link.endswith((".zip", ".json")) else ".html")
    for day in NEW_ORLEANS:
        for name in ("RacesCandidates/ByParish_36.htm", "VotesParish/Votes_36.htm"):
            raw.fetch(SOURCE, LOUISIANA.format(day, name), suffix=".json")


def parse() -> None:
    rows = []
    for day, (general, primary) in FILES.items():
        path = raw.cached(SOURCE, PHILADELPHIA + general).path
        if general.endswith(".xlsx"):
            frame = (
                fastexcel.read_excel(path)
                .load_sheet("Precint-level Data")
                .to_polars()
                .filter(pl.col("RACE") == "MAYOR")
                .select(candidate="CANDIDATE", party="PARTY", votes=pl.col("VOTES").cast(pl.Int64))
            )
        elif primary is None:
            frame = (
                pl.read_csv(path, infer_schema_length=0, encoding="utf8-lossy")
                .filter(pl.col("CATEGORY") == "MAYOR")
                .select(candidate="SELECTION", party="PARTY", votes=pl.col("VOTE COUNT").cast(pl.Int64))
            )
        else:
            nominees = {
                " ".join(found[1].split()): found[2]
                for column in pl.read_csv(
                    raw.cached(SOURCE, PHILADELPHIA + primary).path, n_rows=0, infer_schema_length=0
                ).columns
                if (found := re.fullmatch(r"(.+) - MAYOR-(D|R|DEM|REP)", column)) is not None
            }
            wide = pl.read_csv(path, infer_schema_length=0)
            frame = pl.DataFrame(
                [
                    (name, nominees.get(name), wide[column].cast(pl.Int64).sum())
                    for column in wide.columns
                    if column.endswith(" - MAYOR")
                    for name in [" ".join(column.removesuffix(" - MAYOR").split())]
                ],
                schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
                orient="row",
            )
        rows.append(
            frame.group_by("candidate", "party")
            .agg(pl.col("votes").sum())
            .with_columns(day=pl.lit(day), place=pl.lit("4260000"), county=pl.lit(None, pl.String))
        )
    ballots = []
    for day, name in NYC.items():
        path = raw.cached(SOURCE, NYC_FILES + name).path
        lines = []
        if day.year == 2001:
            text = subprocess.run(
                ["pdftotext", "-layout", "-f", "1", "-l", "2", str(path), "-"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            seen: dict[str, int] = {}
            for line in text.splitlines():
                borough = next((code for label, code in BOROUGHS.items() if line.startswith(f"{label} ")), None)
                if borough is None:
                    continue
                numbers = [
                    int(token)
                    for token in line.removeprefix(next(k for k, v in BOROUGHS.items() if v == borough)).split()
                    if token.isdigit()
                ]
                table = seen[borough] = seen.get(borough, 0) + 1
                if table == 1:
                    ballots.append((day, borough, sum(numbers[:5])))
                    lines += [(borough, *LINES_2001[i], votes) for i, votes in enumerate(numbers[6:11])]
                else:
                    lines += [(borough, *LINES_2001[5 + i], votes) for i, votes in enumerate(numbers[:8])]
        elif name.endswith(".csv"):
            seen_rows = set()
            with path.open(newline="") as handle:
                for row in csv.reader(handle):
                    borough = BOROUGHS.get(row[3].removesuffix(" County").upper())
                    if borough is None:
                        continue
                    if (borough, row[4]) in seen_rows:
                        continue
                    seen_rows.add((borough, row[4]))
                    if (found := re.fullmatch(r"(.+) \((.+)\)", row[4])) is not None:
                        lines.append((borough, found[1], found[2].upper(), int(row[5].replace(",", ""))))
                    if (borough, "ballots") not in seen_rows:
                        seen_rows.add((borough, "ballots"))
                        ballots.append((day, borough, int(row[7].replace(",", ""))))
        else:
            text = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
            ).stdout
            borough = None
            seen_lines = set()
            for line in text.splitlines():
                if (found := re.fullmatch(r" (New York|Bronx|Kings|Queens|Richmond) County", line)) is not None:
                    borough = BOROUGHS[found[1].upper()]
                elif re.match(r" ?\S", line):
                    borough = (
                        None if not line.startswith(("BOARD", "IN THE CITY", "PRINTED", "1", "Statement")) else borough
                    )
                elif borough is None:
                    continue
                elif (found := re.fullmatch(r"\s+TOTAL BALLOTS\s+([\d,]+)", line, flags=re.IGNORECASE)) is not None:
                    ballots.append((day, borough, int(found[1].replace(",", ""))))
                elif (found := re.fullmatch(r"\s+(.+?) \((.+)\)\s+([\d,]+)", line)) is not None:
                    if (borough, found[1], found[2]) in seen_lines:
                        raise ValueError(f"{day} {borough}: {found[1]} ({found[2]}) twice")
                    seen_lines.add((borough, found[1], found[2]))
                    lines.append((borough, found[1], found[2], int(found[3].replace(",", ""))))
        frame = pl.DataFrame(
            lines,
            schema={"county": pl.String, "candidate": pl.String, "party": pl.String, "votes": pl.Int64},
            orient="row",
        ).with_columns(
            candidate=pl.when(pl.col("party").is_in(["WRITE-IN", "WRITE IN"]))
            .then(pl.lit("Write In"))
            .otherwise("candidate"),
            party=pl.when(pl.col("party").is_in(["WRITE-IN", "WRITE IN"])).then(None).otherwise("party"),
        )
        rows.append(
            frame.group_by("county", "candidate", "party")
            .agg(pl.col("votes").sum())
            .with_columns(day=pl.lit(day), place=pl.lit("3651000"))
        )
    ballots = [(day, "3651000", county, cast) for day, county, cast in ballots]
    for day, link in DC.items():
        path = raw.cached(SOURCE, link).path
        lines = []
        if day.year == 2010:
            for contest in json.loads(path.read_text()):
                if contest["OfficeName"] == "MAYOR":
                    lines += [(row["Contestant"], row["PartyCode"], row["Votes"]) for row in contest["ElectionData"]]
        elif day.year in (1994, 1998):
            text = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(path.read_text(errors="replace"))).split())
            block = re.search(r" MAYOR (.+?) CHAIRMAN OF THE COUNCIL", text)[1]
            lines = re.findall(
                r"(\S.*?) (?:(DEMOCRATIC|REPUBLICAN|D\.?C\.? STATEHOOD|SOCIALIST WORKERS|INDEPENDENT) )?([\d,]+) \d+(?= |$)",
                block,
            )
            ballots.append((day, "1150000", None, int(re.search(r"TOTAL TURNOUT ([\d,]+)", text)[1].replace(",", ""))))
        else:
            if day.year == 2002:
                text = re.sub(r"<[^>]+>", "", html.unescape(path.read_text()))
                cast = re.search(r"BALLOTS CAST - TOTAL[ .]+([\d,]+)", text)[1]
                pattern = r" (.+?)(?: \((\w+)\))?[ .]+?([\d,]+) +[\d.]+\b"
            else:
                text = subprocess.run(
                    ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
                ).stdout
                cast = re.search(r"Total \.\.\. +([\d,]+) +[\d.]+%", text)[1]
                pattern = r"(?:(\w+) - )?(.+?) +([\d,]+) +[\d.]+%"
            ballots.append((day, "1150000", None, int(cast.replace(",", ""))))
            block = text.split("MAYOR OF THE DISTRICT OF COLUMBIA", 1)[1].splitlines()[1:]
            for line in block:
                if re.match(r"\s+Total \.", line):
                    break
                if (found := re.match(pattern, line[:66])) is not None and "Votes" not in line[:66]:
                    lines.append(found.groups() if day.year == 2002 else (found[2], found[1], found[3]))
        frame = pl.DataFrame(
            [(name.strip(), party, int(str(votes).replace(",", ""))) for name, party, votes in lines],
            schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
            orient="row",
        ).with_columns(
            candidate=pl.when(pl.col("candidate").str.to_uppercase().str.contains(r"^WRITE[ -]?IN"))
            .then(pl.lit("Write In"))
            .otherwise("candidate"),
            party=pl.when(pl.col("party").is_in(["IND", "INDEPENDENT", "NON", ""])).then(None).otherwise("party"),
            day=pl.lit(day),
            place=pl.lit("1150000"),
            county=pl.lit(None, pl.String),
        )
        print(day, frame.select("candidate", "party", "votes").rows())
        rows.append(frame)
    for day, name in BALTIMORE.items():
        path = raw.cached(SOURCE, MARYLAND + name).path
        if name.endswith(".html"):
            text = [
                line.strip()
                for line in re.sub(r"<[^>]+>", "\n", html.unescape(path.read_text())).splitlines()
                if line.strip()
            ]
            start = next(i for i, line in enumerate(text) if line.endswith("General Election"))
            nominees = {
                text[i - 1]: party
                for heading, party in (("Democratic Candidates", "DEM"), ("Republican Candidates", "REP"))
                for i in range(text.index(heading), start)
                if text[i].endswith(" - winner")
                and next(line for line in reversed(text[:i]) if line.endswith("Candidates")) == heading
            }
            end = text.index("Contact", start)
            lines = [
                (text[i], nominees.get(text[i]), text[i + 1].removesuffix(" - winner"))
                for i in range(start + 3, end, 2)
            ]
        else:
            lines = [
                (row["Candidate Name"], row["Party"], row["Total Votes"])
                for row in csv.DictReader(path.open(newline="", encoding="utf-8-sig"))
                if row["Office Name"].strip() == "Mayor"
            ]
        frame = pl.DataFrame(
            [(candidate.strip(), party, int(votes.replace(",", ""))) for candidate, party, votes in lines],
            schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
            orient="row",
        ).with_columns(
            candidate=pl.when(pl.col("party") == "BOT").then(pl.lit("Write In")).otherwise("candidate"),
            party=pl.when(pl.col("party").is_in(["BOT", "UNF", "IND"])).then(None).otherwise("party"),
            day=pl.lit(day),
            place=pl.lit("2404000"),
            county=pl.lit("24510"),
        )
        print(day, frame.select("candidate", "party", "votes").rows())
        rows.append(frame)
    for day, link in PITTSBURGH.items():
        path = raw.cached(SOURCE, link).path
        if link.endswith(".json"):
            body = path.read_bytes()
            contest = next(
                contest
                for contest in json.loads(gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body)
                if contest["C"] in ("Mayor CITYWIDE", "Mayor Pittsburgh")
            )
            lines = [
                (re.sub(r"^[A-Z]{3} ", "", choice), party, votes)
                for choice, party, votes in zip(contest["CH"], contest["P"], contest["V"], strict=True)
            ]
        elif link.endswith(".pdf"):
            text = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True
            ).stdout.splitlines()
            start = next(i for i, line in enumerate(text) if line.upper() == "MAYOR CITYWIDE")
            lines = []
            for line in text[start + 2 :]:
                if (found := re.match(r"\s+(.+?) \(([\w/]+)\)[ .]+?([\d,]+)\s+[\d.]+$", line)) is not None:
                    lines.append(found.groups())
                elif (found := re.match(r"\s+WRITE-IN[ .]+?([\d,]+)\s+[\d.]+$", line)) is not None:
                    lines.append(("Write In", None, found[1]))
                else:
                    break
        else:
            text = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(path.read_text(errors="replace"))).split())
            block = re.search(r" Mayor Mayor (.+?) Totals ", text)[1]
            lines = re.findall(r"(\S.*?) \((\w+)\) ([\d,]+) [\d.]+%", block)
        frame = pl.DataFrame(
            [
                (
                    "Write In" if candidate.upper().startswith("WRITE") else candidate.strip(),
                    None if party is None or party.upper() not in PARTIES else party.upper(),
                    int(str(votes).replace(",", "")),
                )
                for candidate, party, votes in lines
            ],
            schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
            orient="row",
        ).with_columns(day=pl.lit(day), place=pl.lit("4261000"), county=pl.lit(None, pl.String))
        print(day, frame.select("candidate", "party", "votes").rows())
        rows.append(frame)
    for day, (name, sheet) in BUFFALO.items():
        table = (
            fastexcel.read_excel(raw.cached(SOURCE, ERIE + name).path).load_sheet(sheet, header_row=None).to_polars()
        )
        total = [
            row
            for row in table.iter_rows()
            if " ".join(str(row[0] or "").split()).upper() in ("TOTAL", "CITY OF BUFFALO TOTAL")
        ][-1]
        lines = []
        for column in range(1, table.width):
            parts = [part.strip() for part in re.split(r"\n|\s{2,}", str(table[0, column] or "")) if part.strip()]
            if not parts or parts[0].upper() == "TOTAL" or parts[0].upper().startswith("BLANK"):
                continue
            party = parts[-1].upper() if len(parts) > 1 else None
            lines.append((parts[0], None if party is None or party.startswith("WRITE") else party, total[column]))
        frame = pl.DataFrame(
            [(candidate, party, int(float(votes))) for candidate, party, votes in lines],
            schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
            orient="row",
        ).with_columns(day=pl.lit(day), place=pl.lit("3611000"), county=pl.lit(None, pl.String))
        print(day, frame.select("candidate", "party", "votes").rows())
        rows.append(frame)
    for day, link in CHARLOTTE.items():
        path = raw.cached(SOURCE, link).path
        if link.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                table = pl.read_csv(
                    archive.read(next(name for name in archive.namelist() if name.endswith(".txt"))),
                    infer_schema=False,
                )
            lines = (
                table.filter(pl.col("contest") == "CITY OF CHARLOTTE MAYOR")
                .group_by("choice", "party", maintain_order=True)
                .agg(pl.col("total votes").cast(pl.Int64).sum())
                .rows()
            )
        else:
            text = [
                line.strip()
                for line in re.sub(r"<[^>]+>", "\n", html.unescape(path.read_text(errors="replace"))).splitlines()
                if line.strip()
            ]
            start = next(i for i, line in enumerate(text) if line.startswith("Official Results as of"))
            total = text.index("Total", start)
            votes = []
            for line in text[total + 1 :]:
                if not line.isdigit():
                    break
                votes.append(int(line))
            names = text[total - len(votes) : total]
            parties = [line.upper() for line in text[start + 1 : total - len(votes)] if line.upper() in PARTIES]
            lines = [(names[i], parties[i] if i < len(parties) else None, votes[i]) for i in range(len(votes))]
        frame = pl.DataFrame(
            [
                (
                    "Write In" if "WRITE" in candidate.upper() else candidate,
                    party if party in PARTIES else None,
                    votes,
                )
                for candidate, party, votes in lines
            ],
            schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
            orient="row",
        ).with_columns(day=pl.lit(day), place=pl.lit("3712000"), county=pl.lit(None, pl.String))
        print(day, frame.select("candidate", "party", "votes").rows())
        rows.append(frame)
    for day, link in PROVIDENCE.items():
        path = raw.cached(SOURCE, link).path
        if day.year == 2002:
            with zipfile.ZipFile(path) as archive:
                sheet = fastexcel.read_excel(archive.read("LOCAL.xls")).load_sheet("LOCAL CAND", header_row=4)
            lines = (
                sheet.to_polars()
                .filter(pl.col("MUNICIPALITY") == "PROVIDENCE", pl.col("CONTEST").str.strip_chars() == "MAYOR")
                .group_by("CANDIDATE", "PARTY", maintain_order=True)
                .agg(pl.col("TOTAL").cast(pl.Float64).sum().cast(pl.Int64))
                .rows()
            )
        elif day.year == 2006:
            text = [
                line.strip()
                for line in re.sub(r"<[^>]+>", "\n", html.unescape(path.read_text(errors="replace"))).splitlines()
                if line.strip()
            ]
            start = text.index("MAYOR CITY OF PROVIDENCE") + 6
            lines = []
            while not text[start].isupper():
                lines.append((text[start], text[start + 1], text[start + 4].split(" - ")[0]))
                start += 5
        else:
            lines = [
                (row["name"], row["party_code"], row["votes"])
                for row in next(
                    contest
                    for contest in json.loads(path.read_text())["contests"]
                    if contest["name"].upper() == "MAYOR CITY OF PROVIDENCE"
                )["candidates"]
            ]
        frame = pl.DataFrame(
            [
                (
                    "Write In"
                    if name.upper().startswith("WRITE")
                    else " ".join(word.title() if word.isupper() else word for word in name.split()),
                    party.upper() if party is not None and party.upper() in PARTIES else None,
                    int(str(votes).replace(",", "")),
                )
                for name, party, votes in lines
            ],
            schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
            orient="row",
        ).with_columns(day=pl.lit(day), place=pl.lit("4459000"), county=pl.lit(None, pl.String))
        print(day, frame.select("candidate", "party", "votes").rows())
        rows.append(frame)
    for day in NEW_ORLEANS:
        listed = json.loads(
            raw.cached(SOURCE, LOUISIANA.format(day, "RacesCandidates/ByParish_36.htm")).path.read_text()
        )
        race = next(race for race in listed["Races"]["Race"] if race["SpecificTitle"] == "Mayor -- City of New Orleans")
        names = {choice["ID"]: choice["Desc"] for choice in race["Choice"]}
        counted = next(
            item
            for item in json.loads(
                raw.cached(SOURCE, LOUISIANA.format(day, "VotesParish/Votes_36.htm")).path.read_text()
            )["Races"]["Race"]
            if item["ID"] == race["ID"]
        )
        ballots.append((day, "2255000", None, int(counted["VoterCountVoted"])))
        lines = [
            re.fullmatch(r"(.+) \((\w+)\)", names[choice["ID"]]).groups() + (int(choice["VoteTotal"]),)
            for choice in counted["Choice"]
        ]
        frame = pl.DataFrame(
            [(name, party if party in PARTIES else None, votes) for name, party, votes in lines],
            schema={"candidate": pl.String, "party": pl.String, "votes": pl.Int64},
            orient="row",
        ).with_columns(day=pl.lit(day), place=pl.lit("2255000"), county=pl.lit(None, pl.String))
        print(day, frame.select("candidate", "party", "votes").rows())
        rows.append(frame)
    candidates = (
        pl.concat(rows, how="diagonal")
        .group_by("day", "place", "county", "candidate", "party", maintain_order=True)
        .agg(pl.col("votes").sum())
        .with_columns(write_in=pl.col("candidate") == "Write In")
    )
    for (day,), group in candidates.filter(pl.col("place") == "3651000").group_by("day"):
        print(
            day, group.group_by("candidate", "party").agg(pl.col("votes").sum()).sort("votes", descending=True).rows()
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    candidates.sort("day", "votes", descending=[False, True]).write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(
        ballots, schema={"day": pl.Date, "place": pl.String, "county": pl.String, "ballots": pl.Int64}, orient="row"
    ).write_parquet(PARSED / "ballots.parquet")


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    ballots = pl.read_parquet(PARSED / "ballots.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Mayoral generals from official canvasses with each ballot line's party, superseding ALGED's partyless "
                "sets: Philadelphia's 2007 to 2019 from the City Commissioners, and New York City's 2001 to 2017 by "
                "borough from the Board of Elections' certified Statement and Return reports, and Washington's 1994 to 2010 from "
                "the District of Columbia Board of Elections"
            ),
            polity="US",
        )
        sources = {}
        for place, publisher, title, link, links in (
            (
                "4260000",
                PUBLISHER,
                TITLE,
                "https://vote.phila.gov/resources-data/past-election-results/archived-data-sets/",
                [PHILADELPHIA + name for pair in FILES.values() for name in pair if name is not None],
            ),
            (
                "3651000",
                NYC_PUBLISHER,
                NYC_TITLE,
                "https://www.vote.nyc/page/election-results-summary",
                [NYC_FILES + name for name in NYC.values()],
            ),
            (
                "1150000",
                DC_PUBLISHER,
                DC_TITLE,
                "https://www.dcboe.org/elections/election-results-archives",
                list(DC.values()),
            ),
            (
                "2404000",
                BALTIMORE_PUBLISHER,
                BALTIMORE_TITLE,
                MARYLAND + "index.html",
                [MARYLAND + name for name in BALTIMORE.values()],
            ),
            (
                "4261000",
                PITTSBURGH_PUBLISHER,
                PITTSBURGH_TITLE,
                "https://www.alleghenycounty.us/Government/Elections/Election-Results",
                list(PITTSBURGH.values()),
            ),
            (
                "3611000",
                BUFFALO_PUBLISHER,
                BUFFALO_TITLE,
                "https://elections.erie.gov/ElectionArch",
                [ERIE + name for name, _ in BUFFALO.values()],
            ),
            (
                "3712000",
                "Mecklenburg County Board of Elections",
                "Official election results",
                "https://vote.mecknc.gov/",
                [link for link in CHARLOTTE.values() if not link.endswith(".zip")],
            ),
            (
                "3712000:ncsbe",
                "North Carolina State Board of Elections",
                "Election results data",
                "https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
                [link for link in CHARLOTTE.values() if link.endswith(".zip")],
            ),
            (
                "4459000",
                "Rhode Island Board of Elections",
                "Election results by city and town",
                "https://www.ri.gov/election/results/",
                list(PROVIDENCE.values()),
            ),
            (
                "2255000",
                "Louisiana Secretary of State",
                "Official election results by parish",
                "https://voterportal.sos.la.gov/graphical",
                [
                    LOUISIANA.format(day, name)
                    for day in NEW_ORLEANS
                    for name in ("RacesCandidates/ByParish_36.htm", "VotesParish/Votes_36.htm")
                ],
            ),
        ):
            sources[place] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[place],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=link,
                license="Public record",
            )
            for name in links:
                cs.artifact(raw.cached(SOURCE, name), sources[place])

        def surname(text: str) -> str:
            folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
            folded = re.sub(r",?\s+(JR|SR|I|II|III|IV)\.?$", "", folded.strip(), flags=re.IGNORECASE)
            name = re.sub(r"\W", "", folded.split()[-1]).lower()
            return SURNAMES.get(name, name)

        values = []
        staged: set[UUID] = set()
        for (place, day), rows in candidates.group_by("place", "day", maintain_order=True):
            city = {
                "4260000": "philadelphia_PA",
                "3651000": "new york_NY",
                "1150000": "washington_DC",
                "2404000": "baltimore_MD",
                "4261000": "pittsburgh_PA",
                "3611000": "buffalo_NY",
                "3712000": "charlotte_NC",
                "4459000": "providence_RI",
                "2255000": "new orleans_LA",
            }[place]
            contest_key = f"alged:{place}_{day.year}_11_{city}_Mayor_mayor_1"
            contest = cs.key("us.contest", contest_key)
            if contest in cs.minted:
                raise ValueError(f"{contest_key} must already exist")
            columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "is_default", "completeness")
            prior = conn.execute(
                f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                    join external_id x on x.entity_id = r.id and x.scheme = 'us.alged.result_set'
                    where x.value = %s""",
                [contest_key],
            ).fetchone()
            if prior is None:
                raise ValueError(f"{contest_key}: no ALGED result set")
            prior = dict(zip(columns, prior, strict=True))
            cs.add("result_set", **(prior | {"is_default": False}), status="superseded")
            roots = {
                surname(label): option
                for option, label in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=prior["election_id"],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources.get(f"{place}:ncsbe" if day.year >= 2011 else place, sources[place]),
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md={
                    "4260000": NOTE,
                    "3651000": NYC_NOTE,
                    "2404000": BALTIMORE_NOTE,
                    "4261000": PITTSBURGH_NOTE,
                    "3611000": BUFFALO_NOTE,
                    "3712000": CHARLOTTE_NOTE,
                    "4459000": PROVIDENCE_NOTE,
                    "2255000": NEW_ORLEANS_NOTE,
                }.get(place, DC_NOTE),
            )
            totals = (
                rows.filter(~pl.col("write_in"))
                .group_by(pl.col("candidate").map_elements(surname, pl.String).alias("surname"))
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
            )
            for name, _ in totals.iter_rows():
                if name not in roots:
                    label = rows.filter(pl.col("candidate").map_elements(surname, pl.String) == name)["candidate"][0]
                    option_key = f"{contest_key}:{slug(label)}"
                    roots[name] = cs.key("us.option", option_key)
                    person = cs.key(f"{SOURCE}.person", f"{place}:{slug(label)}")
                    cs.add("contest_option", id=roots[name], contest_id=contest, kind="candidate", ballot_label=label)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{option_key}:head"),
                        person_id=person,
                        option_id=roots[name],
                        role="head",
                        ballot_name=label,
                    )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=roots[name],
                    kind="won" if name == totals["surname"][0] else "lost",
                )
            for county, candidate, party, votes, write_in in rows.select(
                "county", "candidate", "party", "votes", "write_in"
            ).iter_rows():
                unit = cs.unit("us.place", place) if county is None else cs.unit("us.county", county)
                if write_in:
                    option = cs.key("us.option", f"{contest_key}:write_in")
                    if option not in staged:
                        staged.add(option)
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                elif party is None or (place == "4260000" and party not in PARTIES):
                    option = roots[surname(candidate)]
                else:
                    key, label = PARTIES.get(party, (None, party.title()))
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}:{key or slug(party)}")
                    if option in staged:
                        values.append((str(result_set), str(unit), str(option), 1, votes))
                        continue
                    staged.add(option)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        parent_option_id=roots[surname(candidate)],
                        kind="ticket",
                        party_id=None if key is None else cs.key("us.party", key),
                        ballot_label=f"{candidate.title() if candidate.isupper() else candidate} ({label})",
                    )
                values.append((str(result_set), str(unit), str(option), 1, votes))
            for county, cast in (
                ballots.filter(pl.col("day") == day, pl.col("place") == place).select("county", "ballots").iter_rows()
            ):
                unit = cs.unit("us.place", place) if county is None else cs.unit("us.county", county)
                values.append((str(result_set), str(unit), None, 3, cast))
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
