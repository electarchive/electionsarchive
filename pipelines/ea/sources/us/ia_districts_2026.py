import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import district_boards
from ea.store import DATA

SOURCE = "us.ia_districts_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "y26" / "ia" / "counties"
SWCD = "Soil and Water Conservation District"

COUNTIES = {
    "Adair": (
        "adair",
        "https://adaircounty.iowa.gov/files/candidate_list/2026_general_election_81412.pdf",
        "Adair County Auditor",
    ),
    "Benton": (
        "benton",
        "https://www.bentoncountyia.gov/files/auditor/soil_water_candidate_list2026_88555.pdf",
        "Benton County Auditor",
    ),
    "Black Hawk": (
        "black-hawk",
        "https://blackhawkcountyelections.iowa.gov/files/candidate_list/2026_general_election_39589.pdf",
        "Black Hawk County Elections",
    ),
    "Bremer": (
        "bremer",
        "https://bremercountyelections.iowa.gov/files/candidate_list/2026_general_election_81883.pdf",
        "Bremer County Elections",
    ),
    "Buena Vista": (
        "buena-vista",
        "https://buenavistacounty.iowa.gov/elections",
        "Buena Vista County Auditor",
    ),
    "Guthrie": (
        "guthrie",
        "https://guthriecounty.gov/files/candidate_list/2026_general_election_26810.pdf",
        "Guthrie County Auditor",
    ),
    "Jackson": (
        "jackson",
        "https://www.jacksoncountyiowa.gov/elections",
        "Jackson County Auditor",
    ),
    "Marion": (
        "marion",
        "https://www.marioncountyiowa.gov/elections",
        "Marion County Auditor",
    ),
    "Plymouth": (
        "plymouth",
        "https://plymouthcountyiowa.gov/elections",
        "Plymouth County Auditor",
    ),
    "Sioux": (
        "sioux",
        "https://siouxcountyia.gov/elections",
        "Sioux County Auditor",
    ),
    "Cerro Gordo": (
        "cerro-gordo",
        "https://cerrogordo.gov/files/candidate_list/2026_general_election_38511.pdf",
        "Cerro Gordo County Auditor",
    ),
    "Chickasaw": (
        "chickasaw",
        "https://chickasawcountyelections.iowa.gov/files/candidate_list/2026_general_election_73901.pdf",
        "Chickasaw County Elections",
    ),
    "Clayton": (
        "clayton",
        "https://elections.claytoncountyia.gov/files/candidate_list/2026_clayton_county_general_election_75165.pdf",
        "Clayton County Auditor",
    ),
    "Des Moines": (
        "des-moines",
        "https://dmcountyelections.iowa.gov/files/candidate_list/2026_general_election_36984.pdf",
        "Des Moines County Elections",
    ),
    "Keokuk": (
        "keokuk",
        "https://keokukcounty.iowa.gov/files/candidate_list/2026_general_election_93532.pdf",
        "Keokuk County Auditor",
    ),
    "Linn": (
        "linn",
        "https://gis.linncountyiowa.gov/web-data/elections/year/2026/20261103GE/candidates.pdf",
        "Linn County Election Services",
    ),
    "Poweshiek": (
        "poweshiek",
        "https://poweshiekcounty.iowa.gov/files/candidate_list/2026_general_election_november_3_2026_22207.pdf",
        "Poweshiek County Auditor",
    ),
    "Ringgold": (
        "ringgold",
        "https://ringgoldcountyelections.iowa.gov/files/candidate_list/november_3_2026_50330.pdf",
        "Ringgold County Elections",
    ),
    "Warren": (
        "warren",
        "https://www.warrencountyia.gov/wp-content/uploads/2026/09/Federal-State-County-Candidates-Public-Measures-General-110326.pdf",
        "Warren County Auditor",
    ),
}

BOARDS = (
    ("Adair", f"Adair County {SWCD}", "Commissioner", 2, False, ("Phil Kading", "Steve Baudler")),
    ("Adair", f"Adair County {SWCD}", "Commissioner", 1, True, ("Roger Jensen",)),
    (
        "Adair",
        "Adair County Public Hospital",
        "Trustee",
        4,
        False,
        ("Connie Hohertz", "Clark Dolch", "Jodi Bassett", "Mark Schildberg", "Bo Geidel"),
    ),
    ("Benton", f"Benton County {SWCD}", "Commissioner", 2, False, ("Cory Cronbaugh", "Steven A. Thompson")),
    ("Black Hawk", f"Black Hawk County {SWCD}", "Commissioner", 2, False, ("Kristi Heffelmeier", "Michael J Miller")),
    ("Bremer", f"Bremer County {SWCD}", "Commissioner", 2, False, ("Verle Tom Manson", "Sara Lockie")),
    (
        "Buena Vista",
        "Buena Vista County Public Hospital",
        "Trustee",
        4,
        False,
        ("Brenda Halverson", "Duane Miller", "Richard Lampe", "Tara Radke"),
    ),
    ("Buena Vista", f"Buena Vista County {SWCD}", "Commissioner", 2, False, ("Sean Waldstein", "Robert R. Donahoo")),
    (
        "Jackson",
        "Jackson County Hospital",
        "Trustee",
        4,
        False,
        ("Kevin L Burns", "Matthew C. Osterhaus", "William Schwenker"),
    ),
    ("Jackson", f"Jackson County {SWCD}", "Commissioner", 2, False, ("Terri McCulley", "William Bevan")),
    ("Jackson", f"Jackson County {SWCD}", "Commissioner", 1, True, ("Michelle Turner",)),
    ("Marion", f"Marion County {SWCD}", "Commissioner", 2, False, ("Kelsey Willardson", "Justin Petersen")),
    ("Plymouth", f"Plymouth County {SWCD}", "Commissioner", 2, False, ("Robert Puetz", "Jeremy Homan")),
    ("Plymouth", f"Plymouth County {SWCD}", "Commissioner", 1, True, ("Scott Jones",)),
    ("Sioux", f"Sioux County {SWCD}", "Commissioner", 2, False, ("Allan Reinders", "Kendall Roetman")),
    ("Cerro Gordo", f"Cerro Gordo County {SWCD}", "Commissioner", 2, False, ("Jill Bale", "Phillip Dougherty")),
    (
        "Guthrie",
        "Guthrie County Hospital",
        "Trustee",
        4,
        False,
        ("Sheyrl Clark", "Doris Terwilliger", "Michael Underwood", "Dana Tews"),
    ),
    ("Guthrie", f"Guthrie County {SWCD}", "Commissioner", 2, False, ("Jamie Patrick", "Alan Wedemeyer")),
    ("Guthrie", f"Guthrie County {SWCD}", "Commissioner", 1, True, ("Adam Ebert",)),
    ("Chickasaw", f"Chickasaw County {SWCD}", "Commissioner", None, False, ("Kim Leichtman", "Steven Mashek")),
    ("Clayton", f"Clayton County {SWCD}", "Commissioner", 2, False, ("Travis Anderson", "Scott Hilgerson")),
    ("Des Moines", f"Des Moines County {SWCD}", "Commissioner", None, False, ("June Morgan", "Brian Messer")),
    (
        "Keokuk",
        "Keokuk County Hospital",
        "Trustee",
        5,
        False,
        (
            "Ron Kurtz",
            "Allison Pence",
            "Judy Striegel",
            "Alan Sellers",
            "Dustin R. Bensmiller",
            "Jodi Greiner",
            "Terry Fogle",
            "Kathy Utterback",
            "Candy Breon",
            "John Dumont",
            "David J. Peterson",
        ),
    ),
    ("Keokuk", f"Keokuk County {SWCD}", "Commissioner", 2, False, ("Joel Millikin", "Dan Striegel")),
    ("Linn", f"Linn County {SWCD}", "Commissioner", 2, False, ("Joshua O'Connell", "Emmaly Renshaw", "Sean Stokes")),
    ("Poweshiek", f"Poweshiek County {SWCD}", "Commissioner", 2, False, ("Mike Phillips", "John Mann")),
    (
        "Ringgold",
        "Ringgold County Hospital",
        "Trustee",
        4,
        False,
        ("Dane Johnson", "Bruce Ricker", "Steve Roe", "Kathleen Schuster", "Darla Sobotka", "Norma Wolfe"),
    ),
    ("Ringgold", "Ringgold County Hospital", "Trustee", 1, True, ("Clinton L. Spurrier",)),
    ("Ringgold", f"Ringgold County {SWCD}", "Commissioner", 2, False, ("Joyce Frost", "Vickie Jeanes")),
    (
        "Warren",
        f"Warren County {SWCD}",
        "Commissioner",
        2,
        False,
        ("Emily McCauley", "Dan Bailey", "Michael Lee White"),
    ),
)


def fetch() -> None:
    for stem, url, _ in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            file = INBOX / f"{stem}.pdf"
            raw.store(
                SOURCE,
                url,
                file.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def squash(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def parse() -> None:
    texts: dict[str, str] = {}
    for county, (_, url, _) in COUNTIES.items():
        texts[county] = squash(
            subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
        )
    rows = []
    for county, district, role, vote, remainder, names in BOARDS:
        for name in names:
            assert not texts[county] or squash(name) in texts[county], (county, name)
            rows.append(
                {
                    "district": district,
                    "seat": "",
                    "role": role,
                    "name": name,
                    "county": county,
                    "vote": vote,
                    "remainder": remainder,
                    "url": COUNTIES[county][1],
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema_overrides={"vote": pl.Int64})
    frame.write_parquet(PARSED / "candidates.parquet")
    print(frame.height, frame["district"].n_unique())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet").with_columns(
        function=pl.when(pl.col("district").str.contains("Hospital"))
        .then(pl.lit("hospital"))
        .otherwise(pl.lit("soil-and-water"))
    )
    return district_boards.emit(
        source_id=SOURCE,
        version=VERSION,
        state="IA",
        summary="Iowa soil and water conservation district commissioner and county hospital trustee races on the November 3, 2026 ballot, with candidates from county auditors' candidate lists",
        sources={
            url: ("Iowa county auditors", f"{who}: November 3, 2026 General Election candidate list")
            for _, url, who in COUNTIES.values()
        },
        frame=frame,
    )
