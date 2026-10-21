import csv
import io
import zipfile

from ea import raw

SOURCE = "us.elstats_specials"
NORTH_CAROLINA = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{0}/results_pct_{1}.zip"
DAYS = ["2004_07_20", "2019_04_30", "2019_05_14", "2019_09_10"]
UNEXPIRED_PARTIES = {"G. K. Butterfield": "DEM", "Greg Dority": "REP", "Thomas I. Eisenmenger": "LIB"}
PRIMARIES = {"DEM": "Democratic", "REP": "Republican", "LIB": "Libertarian"}


def link(day: str) -> str:
    return NORTH_CAROLINA.format(day, day.replace("_", ""))


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, link(day), suffix=".zip")


def rows() -> list[dict[str, object]]:
    found = []
    for day in DAYS:
        with zipfile.ZipFile(raw.cached(SOURCE, link(day)).path) as archive:
            text = archive.read(archive.namelist()[0]).decode("latin-1")
        iso = f"{day[:4]}-{day[5:7]}-{day[8:]}"
        if day == "2004_07_20":
            for line in csv.reader(io.StringIO(text)):
                if line[4] != "US CONGRESS DISTRICT 1 UNEXPIRED TERM":
                    continue
                found.append(
                    {
                        "state": "NC",
                        "election_date": iso,
                        "district_name": "1",
                        "candidate_name": line[5].strip(),
                        "candidate_party_name": UNEXPIRED_PARTIES[line[5].strip()],
                        "division_name": line[0].title(),
                        "votes": line[7],
                    }
                )
            continue
        for line in csv.DictReader(io.StringIO(text), delimiter="\t"):
            contest = line["Contest Name"]
            if not contest.startswith("US HOUSE OF REPRESENTATIVES DISTRICT"):
                continue
            scope = contest.removesuffix(")").rpartition(" (")[2] if contest.endswith(")") else ""
            found.append(
                {
                    "state": "NC",
                    "election_date": iso,
                    "district_name": contest.split()[5],
                    "candidate_name": line["Choice"].strip(),
                    "candidate_party_name": line["Choice Party"] or None,
                    "division_name": line["County"].title(),
                    "votes": line["Total Votes"],
                    "primary": PRIMARIES[scope] if scope else None,
                }
            )
    return found
