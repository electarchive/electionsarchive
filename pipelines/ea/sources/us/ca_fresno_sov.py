import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import ca_precincts
from ea.store import DATA

SOURCE = "us.ca_fresno_sov"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Fresno County Clerk/Registrar of Voters"
TITLE = "Statement of Vote, delimited precinct export"
LISTING = "https://www.fresnocountyca.gov/Departments/County-ClerkRegistrar-of-Voters/Elections/Election-Results"
OLD = "https://www2.fresnocountyca.gov/2850/Results/"
NEW = "https://www.fresnocountyca.gov/files/sharedassets/county/v/1/county-clerk-registrar-of-voters/1_election-results-page-sov-ssov/"
FILES = {
    date(2000, 3, 7): OLD + "SOV%20Results/SOV_2000Mar.txt",
    date(2000, 11, 7): OLD + "SOV%20Results/SOV_2000Nov.txt",
    date(2002, 11, 5): OLD + "SOV%20Results/SOV_2002Nov.txt",
    date(2004, 11, 2): OLD + "SOV%20Results/SOV_2004Nov.txt",
    date(2006, 6, 6): OLD + "SOV%20Results/SOV_2006June.txt",
    date(2006, 11, 7): OLD + "SOV%20Results/SOV_2006Nov.txt",
    date(2008, 2, 5): OLD + "SOV%20Results/SOV_2008Feb.txt",
    date(2008, 6, 3): OLD + "SOV%20Results/SOV_2008June.txt",
    date(2008, 11, 4): OLD + "SOV%20Results/SOV_2008Nov.txt",
    date(2010, 6, 8): OLD + "SOV_06_08_2010.txt",
    date(2010, 11, 2): OLD + "SOV%20Results/SOV_2010Nov%20CSV.txt",
    date(2012, 6, 5): NEW + "old-election-results/2012/sov_csv_20120605.txt",
    date(2012, 11, 6): NEW + "old-election-results/2012/sov_csv_20121106.txt",
    date(2014, 6, 3): OLD + "SOV_CSV_20140603.txt",
    date(2016, 6, 7): OLD + "sov_delimited_20160607.txt",
    date(2016, 11, 8): OLD + "sov_delimited_20161108.txt",
    date(2017, 3, 7): OLD + "SOV_delimited_20170307.txt",
    date(2018, 6, 5): OLD + "SOV%20Results/SOV_2018June.txt",
    date(2018, 11, 6): NEW + "old-election-results/2018/31864-sov_nov2018_final.txt",
    date(2019, 3, 5): NEW + "2019/march-2019/34261-sov_03052019.txt",
    date(2020, 3, 3): NEW + "2020/march-2020/42560-sov-2020-03-03_csv.csv",
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".txt")


TOKEN = re.compile(r'"((?:[^"]|"")*)"|([^\s,"]+)')
WRAPPED = re.compile(r'^"\d+,\d+,""')
STATISTIC = re.compile(r"(?i)^(?:registered voters|times |number of )")
UNUSED = re.compile(r"(?i)write-?ins?|^(?:over|under)votes?$|^total")
METHOD = (
    "Precinct totals from Fresno County's Statement of Vote export (polling and vote-by-mail ballots, as the "
    "export's own total line), matched to the contest by candidate and checked against its official totals."
)


def parse() -> None:
    rows = []
    layouts: dict[date, int] = {}
    for day, url in FILES.items():
        taken = 0
        for line in raw.cached(SOURCE, url).path.read_text(encoding="latin-1").splitlines():
            if WRAPPED.match(line):
                line = line[1:].removesuffix('"').replace('""', '"')
            fields = [bare or quoted for quoted, bare in TOKEN.findall(line.replace('\\"', ""))]
            if len(fields) in (7, 8) and fields[-1].lstrip("-").isdigit():
                if fields[5] != "Total" or STATISTIC.match(fields[3]) or UNUSED.search(fields[3]):
                    continue
                contest, precinct, candidate, votes = fields[2], fields[0].zfill(4), fields[3], int(fields[6])
            elif (
                len(fields) >= 23
                and fields[3]
                and fields[4].isdigit()
                and int(fields[4]) < 999990
                and fields[19] == "999999"
                and fields[13].isdigit()
                and int(fields[13]) < 999990
                and not UNUSED.search(fields[14])
            ):
                contest, precinct, candidate, votes = f"{fields[4]}:{fields[5]}", fields[3], fields[14], int(fields[22])
            else:
                continue
            rows.append(
                {
                    "day": day,
                    "url": url,
                    "contest": contest.strip(),
                    "precinct": precinct.strip(),
                    "candidate": candidate.strip(),
                    "votes": votes,
                    "registered": None,
                    "ballots": None,
                }
            )
            taken += 1
        layouts[day] = taken
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema_overrides={"registered": pl.Int64, "ballots": pl.Int64}).write_parquet(
        PARSED / "rows.parquet"
    )
    print({str(day): count for day, count in layouts.items()})


def emit() -> Path:
    return ca_precincts.emit(
        pipeline=SOURCE,
        version=VERSION,
        summary="Fresno County's contests by precinct from the Statement of Vote exports",
        rows=pl.read_parquet(PARSED / "rows.parquet"),
        fips="06019",
        place="FRESNO",
        publisher=PUBLISHER,
        title=TITLE,
        listing=LISTING,
        method=METHOD,
    )
