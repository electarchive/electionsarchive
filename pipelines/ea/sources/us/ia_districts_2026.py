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
IMAGE_ONLY = {
    "Henry",
    "Jasper",
    "Pottawattamie",
    "Audubon",
    "Emmet",
    "Fremont",
    "Lucas",
    "Lyon",
    "Palo Alto",
    "Taylor",
    "Union",
}
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
    "Henry": (
        "henry",
        "https://cap.gmdsolutions.com/viewfile.aspx?id=58CC9BF357494FD2B2B91C512294B2D4&dir=Objects",
        "Henry County Auditor",
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
    "Polk": (
        "polk",
        "https://www.polkcountyiowa.gov/county-auditor/election/",
        "Polk County Auditor",
    ),
    "Story": (
        "story",
        "https://storycountyiowa.gov/1754/General-Election-11032026",
        "Story County Auditor",
    ),
    "Dallas": (
        "dallas",
        "https://www.dallascountyiowa.gov/DocumentCenter/View/7620",
        "Dallas County Auditor",
    ),
    "Jasper": (
        "jasper",
        "https://www.jaspercountyiowa.gov/elections",
        "Jasper County Auditor",
    ),
    "Johnson": (
        "johnson",
        "https://www.johnsoncountyiowa.gov/auditor/elections",
        "Johnson County Auditor",
    ),
    "Scott": (
        "scott",
        "https://elections.scottcountyiowa.gov/",
        "Scott County Auditor",
    ),
    "Pottawattamie": (
        "pottawattamie",
        "https://www.pottcounty-ia.gov/elections",
        "Pottawattamie County Auditor",
    ),
    "Woodbury": (
        "woodbury",
        "https://www.woodburycountyiowa.gov/elections",
        "Woodbury County Auditor",
    ),
    "Clinton": (
        "clinton",
        "https://www.clintoncountyiowa.com/elections",
        "Clinton County Auditor",
    ),
    "Muscatine": (
        "muscatine",
        "https://www.muscatinecountyiowa.gov/elections",
        "Muscatine County Auditor",
    ),
    "Wapello": (
        "wapello",
        "https://www.wapellocounty.org/elections",
        "Wapello County Auditor",
    ),
    "Boone": (
        "boone",
        "https://www.boonecounty.iowa.gov/elections",
        "Boone County Auditor",
    ),
    "Mahaska": (
        "mahaska",
        "https://www.mahaskacounty.org/elections",
        "Mahaska County Auditor",
    ),
    "Lee": (
        "lee",
        "https://www.leecountyiowa.org/elections",
        "Lee County Auditor",
    ),
    "Marshall": (
        "marshall",
        "https://www.marshallcountyiowa.gov/elections",
        "Marshall County Auditor",
    ),
    "Allamakee": (
        "allamakee",
        "https://allamakeecountyelections.iowa.gov/files/candidate_list/2026_general_election_76436.pdf",
        "Allamakee County Elections",
    ),
    "Audubon": (
        "audubon",
        "https://auduboncountyia.gov/webres/File/Auditor/Election/Sample%20Ballot.pdf",
        "Audubon County Auditor",
    ),
    "Cass": (
        "cass",
        "https://www.casscountyia.gov/wp-content/uploads/2019/10/Sample-Ballots-November-3-2026-General-Election.pdf",
        "Cass County Auditor",
    ),
    "Cedar": (
        "cedar",
        "https://cedarcounty.iowa.gov/files/auditor/2026_candidate_list_89671.pdf",
        "Cedar County Auditor",
    ),
    "Clarke": (
        "clarke",
        "https://clarkecounty.iowa.gov/elections/election-info/",
        "Clarke County Auditor",
    ),
    "Crawford": (
        "crawford",
        "https://www.crawfordcounty.iowa.gov/elections/info/2026_general_election_2026_11_03/",
        "Crawford County Auditor",
    ),
    "Dickinson": (
        "dickinson",
        "https://dickinsoncountyiowa.gov/wp-content/uploads/2026/09/2026-General-Election-Candidate-List.pdf",
        "Dickinson County Auditor",
    ),
    "Emmet": (
        "emmet",
        "https://emmetcountyia.gov/wp-content/uploads/2026/09/Emmet-Estherville-sample-ballot.pdf",
        "Emmet County Auditor",
    ),
    "Fremont": (
        "fremont",
        "https://www.fremontcountyia.gov/vimages/shared/vnews/stories/5cad3edf734e1/2026%20General%20Publication%20with%20signature.pdf",
        "Fremont County Auditor",
    ),
    "Hancock": (
        "hancock",
        "https://elections.hancockcountyia.gov/files/candidate_list/2026_general_election_10233.pdf",
        "Hancock County Auditor",
    ),
    "Ida": (
        "ida",
        "https://idacounty.iowa.gov/wp-content/uploads/2026/10/2026-General-Notice-of-Election.pdf",
        "Ida County Auditor",
    ),
    "Jones": (
        "jones",
        "https://www.jonescountyiowaelections.gov/files/sample_ballots/20261103_anamosa_city_14043.pdf",
        "Jones County Auditor",
    ),
    "Louisa": (
        "louisa",
        "https://elections.louisacountyia.gov/files/public_notice/2026_louisa_county_general_election_19316.pdf",
        "Louisa County Auditor",
    ),
    "Lucas": (
        "lucas",
        "https://lucascountyelections.iowa.gov/files/sample_ballots/1197_chariton_city_hall_precinct_32914.pdf",
        "Lucas County Auditor",
    ),
    "Lyon": (
        "lyon",
        "https://lyoncounty.iowa.gov/files/sample_ballots/1095_george_p1_23087.pdf",
        "Lyon County Auditor",
    ),
    "Mills": (
        "mills",
        "https://www.millscountyiowa.gov/385/General-Election",
        "Mills County Auditor",
    ),
    "O'Brien": (
        "obrien",
        "https://obriencounty.iowa.gov/wp-content/uploads/2026/09/General-Ballot-Candidates-and-Measures-1.pdf",
        "O'Brien County Auditor",
    ),
    "Osceola": (
        "osceola",
        "https://osceolacountyia.gov/wp-content/uploads/2026/10/2026-General-Election-Notice.pdf",
        "Osceola County Auditor",
    ),
    "Palo Alto": (
        "palo-alto",
        "https://paloaltocounty.iowa.gov/wp-content/uploads/2026/09/2026-GENERAL-ELECTION-SAMPLE-BALLOTS.pdf",
        "Palo Alto County Auditor",
    ),
    "Sac": (
        "sac",
        "https://www.saccountyiowa.gov/files/news/notice_of_election_38682.pdf",
        "Sac County Auditor",
    ),
    "Taylor": (
        "taylor",
        "https://taylorcounty.iowa.gov/wp-content/uploads/2026/10/Precinct-1-Sample-Ballots-2026-General-Election.pdf",
        "Taylor County Auditor",
    ),
    "Hardin": (
        "hardin",
        "https://www.hardincountyia.gov/2026-General-election-0",
        "Hardin County Auditor",
    ),
    "Kossuth": (
        "kossuth",
        "https://www.kossuthcounty.iowa.gov/general-election-notice-2026/",
        "Kossuth County Auditor",
    ),
    "Montgomery": (
        "montgomery",
        "https://montgomerycountyia.gov/docview.aspx?docid=51747",
        "Montgomery County Auditor",
    ),
    "Pocahontas": (
        "pocahontas",
        "https://pocahontascounty.iowa.gov/wp-content/uploads/2026-General-Election-Sample-Ballots.pdf",
        "Pocahontas County Auditor",
    ),
    "Van Buren": (
        "van-buren",
        "https://www.vanburencounty.iowa.gov/Candidate%20Names%20%26%20Addresses.pdf",
        "Van Buren County Auditor",
    ),
    "Union": (
        "union",
        "https://unioncountyiowa.gov/wp-content/uploads/2026/10/SAMPLE-BALLOTS-NOVEMBER-3-2026.pdf",
        "Union County Auditor",
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
    ("Polk", "Polk County Hospital", "Trustee", 2, False, ("Emily Webb", "Abu Bakarr Sillah", "Emily Westergaard")),
    (
        "Polk",
        f"Polk County {SWCD}",
        "Commissioner",
        2,
        False,
        ("Douglas R. Zehr", "Autumn L. Ogden", "Kirk Norris", "Deborah Neustadt"),
    ),
    (
        "Story",
        "Story County Hospital",
        "Trustee",
        4,
        False,
        ("Elizabeth Hertz", "Austin Vier", "Pamela Elliott Cain", "Patrick Sheets"),
    ),
    ("Story", f"Story County {SWCD}", "Commissioner", 2, False, ("Selden Spencer", "Stephen Riggins")),
    (
        "Dallas",
        "Dallas County Hospital",
        "Trustee",
        4,
        False,
        ("Kari Boyens", "Joelle Miner", "Pat Steele", "Dennis Cochran"),
    ),
    (
        "Dallas",
        f"Dallas County {SWCD}",
        "Commissioner",
        2,
        False,
        ("Craig Fleishman", "Veronica M. Lack", "James P Beni"),
    ),
    ("Dallas", f"Dallas County {SWCD}", "Commissioner", 2, True, ("Ken Herring", "Jerry Vos")),
    ("Jasper", f"Jasper County {SWCD}", "Commissioner", 2, False, ("Dave Van Waus", "Joe Otto", "Deb Wearmouth")),
    (
        "Johnson",
        f"Johnson County {SWCD}",
        "Commissioner",
        2,
        False,
        ("Corey Johnson", "Alexander Schmidt", "Travis Spevacek"),
    ),
    (
        "Scott",
        f"Scott County {SWCD}",
        "Commissioner",
        2,
        False,
        ("Christine Bolte", "Marty Larson", "Chad Dexter", "Alexandra Dermody"),
    ),
    ("Pottawattamie", f"West Pottawattamie {SWCD}", "Commissioner", 2, False, ("Dolores Bristol", "Don Dilts")),
    ("Woodbury", f"Woodbury County {SWCD}", "Commissioner", 2, False, ("Gary Walters",)),
    ("Clinton", f"Clinton County {SWCD}", "Commissioner", 2, False, ("Cindy Heilmann", "John Tuthill")),
    ("Muscatine", f"Muscatine County {SWCD}", "Commissioner", 2, False, ("Randall Eichelberger", "Robert L. Beatty")),
    ("Wapello", f"Wapello County {SWCD}", "Commissioner", 2, False, ("Keith Lewis", "Connie Beinhart")),
    (
        "Boone",
        "Boone County Hospital",
        "Trustee",
        4,
        False,
        ("Brian Reimers", "Lisa Kobernusz", "Meredith Nerem", "Robert Getschman"),
    ),
    ("Boone", f"Boone County {SWCD}", "Commissioner", 2, False, ("Steve Carlson", "Melissa Sturgeon-Bice")),
    (
        "Mahaska",
        "Mahaska County Hospital",
        "Trustee",
        4,
        False,
        ("Joette Stout", "David Langkamp", "Marsha Riordan", "Donna Arendt"),
    ),
    ("Mahaska", f"Mahaska County {SWCD}", "Commissioner", 2, False, ("Steven Plate",)),
    ("Lee", f"Lee County {SWCD}", "Commissioner", 2, False, ("Stanley Sanders", "Ralph Krogmeier")),
    ("Marshall", f"Marshall County {SWCD}", "Commissioner", 2, False, ("Roger Lutes", "Tyler Kelley")),
    (
        "Allamakee",
        f"Allamakee County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "Donald Elsbernd",
            "Scott Dougherty",
        ),
    ),
    (
        "Audubon",
        f"Audubon County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "Jonnie Meislahn",
            "Lora L. Anthofer",
        ),
    ),
    (
        "Cass",
        f"Cass County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "John J. Hansen",
            "Gregory D Zellmer",
        ),
    ),
    ("Cedar", f"Cedar County {SWCD}", "Commissioner", 1, True, ("James Pelzer",)),
    (
        "Clarke",
        f"Clarke County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "Gabriel Crawford",
            "Chris George",
            "Ethan Abbott",
        ),
    ),
    ("Crawford", f"Crawford County {SWCD}", "Commissioner", 2, False, ("David Gress",)),
    ("Dickinson", f"Dickinson County {SWCD}", "Commissioner", 2, False, ("S. Vance Hjelm",)),
    ("Emmet", f"Emmet County {SWCD}", "Commissioner", 2, False, ("Carson Glasnapp",)),
    ("Fremont", f"Fremont County {SWCD}", "Commissioner", 2, False, ("David Carter",)),
    ("Hancock", f"Hancock County {SWCD}", "Commissioner", 1, True, ("Chase Ulven",)),
    ("Ida", f"Ida County {SWCD}", "Commissioner", 2, False, ("Roger Bumann",)),
    (
        "Jones",
        f"Jones County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "Julie Orris",
            "Jeff Von Behren",
        ),
    ),
    (
        "Louisa",
        f"Louisa County {SWCD}",
        "Commissioner",
        None,
        False,
        (
            "Kathy Dice",
            "Tim Knutson",
        ),
    ),
    ("Lucas", f"Lucas County {SWCD}", "Commissioner", 2, False, ("Richard Arnold",)),
    (
        "Lyon",
        f"Lyon County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "Randy Warner",
            "Dale Winkowitsch",
            "Richard Reitsma",
        ),
    ),
    (
        "Mills",
        f"Mills County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "Carol Vinton",
            "Stephen McGrew",
        ),
    ),
    ("O'Brien", f"O'Brien County {SWCD}", "Commissioner", 2, False, ("Carmen Billick",)),
    ("Osceola", f"Osceola County {SWCD}", "Commissioner", 2, False, ("Dennis Monier", "Chris Slagter")),
    ("Osceola", f"Osceola County {SWCD}", "Commissioner", 1, True, ("Tanya Dykstra",)),
    ("Palo Alto", f"Palo Alto County {SWCD}", "Commissioner", 2, False, ("Jerry Joyce",)),
    (
        "Sac",
        f"Sac County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "James Frederick",
            "Steven Pickhinke",
        ),
    ),
    ("Taylor", f"Taylor County {SWCD}", "Commissioner", 2, False, ("Steve Wright",)),
    ("Taylor", f"Taylor County {SWCD}", "Commissioner", 1, True, ("Ballie Walters",)),
    (
        "Union",
        f"Union County {SWCD}",
        "Commissioner",
        2,
        False,
        (
            "Cody Purdum",
            "Roger Baker",
        ),
    ),
    (
        "Van Buren",
        "Van Buren County Hospital",
        "Trustee",
        4,
        False,
        ("Ann McBride", "Kayla Scheuers", "Michael William Thomas", "Quenton Dodson"),
    ),
    ("Van Buren", f"Van Buren County {SWCD}", "Commissioner", 2, False, ("Dennis Gratz", "Rodney Peck")),
    (
        "Henry",
        f"Henry County {SWCD}",
        "Commissioner",
        2,
        False,
        ("Ricky M. Mallams", "Harlan Miller", "Andrew John Woline"),
    ),
    (
        "Henry",
        "Henry County Health Center",
        "Trustee",
        4,
        False,
        ("Rebecca Bender", "Mary Liechty", "Amy J. McLaughlin", "Joel D. Prottsman"),
    ),
    (
        "Henry",
        "Henry County Agricultural Extension Council",
        "Member",
        5,
        False,
        ("Marc Benson", "Amanda McArtor-Morin", "Brooke Kelley-McArtor", "Theresa Rose"),
    ),
    ("Henry", "Henry County Agricultural Extension Council", "Member", 1, True, ("Jason Liechty",)),
    ("Hardin", f"Hardin County {SWCD}", "Commissioner", 2, False, ("Jo Duncan", "John Everly")),
    ("Kossuth", f"Kossuth County {SWCD}", "Commissioner", 2, False, ("Luke Snyder", "Nancy Bohl Bormann")),
    (
        "Kossuth",
        "Kossuth County Hospital",
        "Trustee",
        3,
        False,
        ("Brenda Bormann", "Greg Penning", "Conley Nelson"),
    ),
    (
        "Montgomery",
        "Montgomery County Public Hospital",
        "Trustee",
        4,
        False,
        ("Christopher Keith Gilbert", "Jill Carter Bergstrom", "Jeffrey Focht", "Jason Poston"),
    ),
    (
        "Montgomery",
        f"Montgomery County {SWCD}",
        "Commissioner",
        2,
        False,
        ("Maggie McQuown", "Kirk Baird", "Roger Cerven"),
    ),
    ("Pocahontas", f"Pocahontas County {SWCD}", "Commissioner", 2, False, ("Ronald Lampe", "Brian Hoffman")),
)


def fetch() -> None:
    for stem, url, _ in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            file = next(INBOX.glob(f"{stem}.*"))
            raw.store(
                SOURCE,
                url,
                file.read_bytes(),
                suffix=file.suffix,
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="text/html" if file.suffix == ".html" else "application/pdf",
            )


def squash(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def parse() -> None:
    texts: dict[str, str] = {}
    for county, (_, url, _) in COUNTIES.items():
        path = raw.cached(SOURCE, url).path
        if path.suffix == ".html":
            texts[county] = squash(re.sub(r"<[^>]+>", " ", path.read_text(errors="ignore")))
            continue
        texts[county] = squash(
            subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
        )
    rows = []
    for county, district, role, vote, remainder, names in BOARDS:
        for name in names:
            assert county in IMAGE_ONLY or not texts[county] or squash(name) in texts[county], (county, name)
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
        function=pl.when(pl.col("district").str.contains("Hospital|Health Center"))
        .then(pl.lit("hospital"))
        .when(pl.col("district").str.contains("Extension"))
        .then(pl.lit("extension"))
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
