"""Pakistan's Election Commission: the 2002 and 2008 general elections, by constituency.

The 2002 election covers the National Assembly and, in one package each, the four provincial assemblies. The 2008 election covers the National Assembly and the four
provincial assemblies, read from the word positions of the Commission's second report volume."""

import html
import re
import subprocess
from datetime import date
from difflib import SequenceMatcher
from typing import NamedTuple
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "pk.ecp"
PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
PAGE = "https://ecp.gov.pk/general-elections-2002"
DAY = date(2002, 10, 10)
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
CONSTITUENCIES = "https://web.archive.org/web/20240701044518id_/https://ecp.gov.pk/storage/files/1/ge2002-n1.pdf"
PARTIES = "https://web.archive.org/web/20240701044536id_/https://ecp.gov.pk/storage/files/1/ge2002-n6.pdf"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECP_SOURCE = key("source", "ecp")
ASSEMBLY = key("body", "national-assembly")
SYSTEM = key("electoral_system", "national-assembly", "fptp")


def province(iso: str) -> str:
    return str(uuid5(package.NAMESPACE, f"polity\x1f{iso}"))


class Volume(NamedTuple):
    """One assembly of the 2002 elections: its constituency prefix, the volume that prints it, and its seats."""

    prefix: str
    url: str
    iso: str  # the polity that holds the assembly: PK for the National Assembly, else the province
    seats: int
    name: str  # the assembly's name in 2002
    slug: str  # the path under pk/ of a provincial election; empty for the National Assembly


VOLUMES = [
    Volume("NA", CONSTITUENCIES, "PK", 272, "National Assembly of Pakistan", ""),
    Volume(
        "PF",
        "https://web.archive.org/web/20240701044521id_/https://ecp.gov.pk/storage/files/1/ge2002-n2.pdf",
        "PK-KP",
        99,
        "North-West Frontier Province",
        "khyber-pakhtunkhwa",
    ),
    Volume(
        "PP",
        "https://web.archive.org/web/20251202153110id_/https://ecp.gov.pk/storage/files/1/ge2002-n3.pdf",
        "PK-PB",
        297,
        "Punjab",
        "punjab",
    ),
    Volume("PS", "https://ecp.gov.pk/storage/files/1/ge2002-n4.pdf", "PK-SD", 130, "Sindh", "sindh"),
    Volume(
        "PB",
        "https://web.archive.org/web/20240701044533id_/https://ecp.gov.pk/storage/files/1/ge2002-n5.pdf",
        "PK-BA",
        51,
        "Balochistan",
        "balochistan",
    ),
]


def holder(volume: Volume) -> str:
    return PAKISTAN if volume.iso == "PK" else province(volume.iso)


def ident(volume: Volume, *parts: object) -> tuple[object, ...]:
    """The key parts of a 2002 row; the National Assembly's keep the shape they had before the provincial
    assemblies were added."""
    return parts if volume.prefix == "NA" else (volume.prefix.lower(), *parts)


METHOD = (
    "Counted and published by constituency by the Election Commission of Pakistan in its detailed results of the "
    "2002 general elections. Valid votes are the votes polled by the candidates, total votes are valid and rejected "
    "votes together, and registered voters are the voters on the electoral roll of the constituency. The Commission "
    "does not publish the count of each polling station in this volume."
)

# Spellings the printed volume uses for one party, by the squashed lowercase text.
PARTY_NAMES = {
    "awamiqaidatparty": "Awami Qiadat Party",
    "balohistannationalmovement": "Balochistan National Movement",
    "independent": "Independent",
    "independentt": "Independent",
    "independentretired": "Independent",
    "hazaraquamimahaz": "Hazara Quami Mahaz",
    "hazaraqaumimahaz": "Hazara Quami Mahaz",
    "laboyrpartypakistan": "Labour Party Pakistan",
    "labourpartypakistankrandi": "Labour Party Pakistan (Krandi)",
    "muttahiddamajliseamal": "Muttahidda Majlis-e-Amal Pakistan",
    "pakmuslimalliance": "Pak Muslim Alliance",
    "pakistamuslimleaguej": "Pakistan Muslim League (J)",
    "pakistanpeoplespartyparliamentarian": "Pakistan Peoples Party Parliamentarians",
    "pakistanmazdoorkissanparty": "Pakistan Mazdoor Kissan Party",
    "pakistanawamiparty": "Pakistan Awami Party",
    "indepndent": "Independent",
    "indeindependentdent": "Independent",
    "indeindependentdente": "Independent",
    "muthidaqaumimovement": "Muttahida Qaumi Movement",
    "muttahidaqaumimoement": "Muttahida Qaumi Movement",
    "muttahidaqaumimoment": "Muttahida Qaumi Movement",
    "muttahidaqaumimovement": "Muttahida Qaumi Movement",
    "muttahiddamajliseamalpakistan": "Muttahidda Majlis-e-Amal Pakistan",
    "pakistandemocraticparty": "Pakistan Democratic Party",
    "pakistanmulimleagueqa": "Pakistan Muslim League (QA)",
    "pakistanmuslimleagueqa": "Pakistan Muslim League (QA)",
    "pakistanmuslimleaguen": "Pakistan Muslim League (N)",
    "pakistanmuslimleaguef": "Pakistan Muslim League (F)",
    "pakistanmuslimleaguej": "Pakistan Muslim League (J)",
    "pakistanmuslimleaguez": "Pakistan Muslim League (Z)",
    "pakistanpeoplespartyparliamentarians": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoplespartyparlimentarians": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoplespartysheedbhutto": "Pakistan Peoples Party (Shaheed Bhutto)",
    "pakistanpeoplespartyshaheedbhutto": "Pakistan Peoples Party (Shaheed Bhutto)",
    "pakistanpeoplespartysherpao": "Pakistan Peoples Party (Sherpao)",
    "pakistantahreekeinsaf": "Pakistan Tehreek-e-Insaf",
    "pakistantehreekeinsaf": "Pakistan Tehreek-e-Insaf",
    "pakistansocialdemocraticparty": "Pakistan Social Democratic Party",
    "nazimemistafa": "Nizam-e-Mustafa Party",
    "nizamemustafaparty": "Nizam-e-Mustafa Party",
    "pakistanawamitehrikeinqilab": "Pakistan Awami Tehrik-e-Inqilab",
}


def clean(text: str) -> str:
    return " ".join(text.split())


def party_name(text: str) -> str:
    squashed = re.sub(r"[^a-z]", "", text.lower())
    return PARTY_NAMES.get(squashed, clean(text))


def party_id(name: str) -> str:
    return key("party", name.casefold())


class Candidate:
    def __init__(self, position: int, name: str, party: str, votes: int) -> None:
        self.position = position
        self.name = name
        self.party = party
        self.votes = votes


class Constituency:
    def __init__(self, number: int, name: str) -> None:
        self.number = number
        self.name = name
        self.candidates: list[Candidate] = []
        self.totals: dict[str, int] = {}


def text(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


HEADER = re.compile(r"^\s*(NA|P[FPSB])\s*-\s*(\d+)[\s-]+(.+?)\s*$")
CANDIDATE = re.compile(r"^\s*(\d+)?\s*(\S.*?)\s{2,}(.+?)\s{2,}(\d+)\s*$")
TOTAL = re.compile(r"^\s*(Valid Votes|Rejected Votes|Total Votes|Registered Voters)(?: Votes)?\s+(\d+)\s*$")


# Candidates whose printed line wraps in the volume, so that their votes sit on a line of their own, by
# constituency number and ballot position.
WRAPPED = {
    (51, 6): ("Syed Mozam Ali Rizvi", "Pak Wattan Party", 533),
    (89, 11): ("Lt. Comdr ® Sohaib Farooq", "Pakistan Muslim League(QA)", 1415),
}


# A line whose party is run into the candidate's name, by constituency number and votes.
RUN_TOGETHER = {(157, 12572): ("Mr.Sahibzada Muhammad Aziz-ur-Rehman Hamdi", "Muttahidda Majlis-e-Amal Pakistan")}


# Provincial lines whose name wraps onto a second line or whose party is run into the name, by assembly prefix,
# constituency number and printed ballot number.
PROVINCIAL_LINES = {
    ("PF", 14, 4): ("Moulana Mohammad Mujahid Khan Al-Hussaini", "Muttahidda Majlis-e-Amal Pakistan"),
    ("PP", 62, 5): ("Mr.Muhammad Saddique ul Hasnat Dogar Advocate", "Sunni Tehreek"),
    ("PP", 240, 2): ("Sardar Hafiz Aurangzeb Alamgir Khan Khetran Advocate", "Independent"),
    ("PP", 243, 5): ("Mr.Sher Muhammad Khan Khalol Khosa Advocate.", "Pakistan Peoples Party Parliamentarians"),
    ("PP", 277, 3): ("Mr.Muhammad Akhtar Khadim Alias Khadim Hussain.", "Pakistan Muslim League (QA)"),
    ("PS", 31, 4): ("Syed Shahabuddin Shah @ Syed Dindar Shah Rashdi", "Independent"),
}


def constituencies(assembly: Volume = VOLUMES[0]) -> list[Constituency]:
    found: list[Constituency] = []
    for line in text(assembly.url).splitlines():
        if header := HEADER.match(line):
            if header.group(1) != assembly.prefix:
                raise ValueError(f"{header.group(1)}-{header.group(2)} is printed in the {assembly.prefix} volume")
            found.append(Constituency(int(header.group(2)), clean(header.group(3))))
        elif not found:
            continue
        elif total := TOTAL.match(line):
            found[-1].totals[total.group(1)] = int(total.group(2))
        elif candidate := CANDIDATE.match(line):
            last = found[-1].candidates[-1].position if found[-1].candidates else 0
            position = int(candidate.group(1)) if candidate.group(1) else last + 1
            found[-1].candidates.append(
                Candidate(position, clean(candidate.group(2)), party_name(candidate.group(3)), int(candidate.group(4)))
            )
    wrapped = WRAPPED if assembly.prefix == "NA" else {}
    run_together = RUN_TOGETHER if assembly.prefix == "NA" else {}
    for seat in found:
        for candidate in seat.candidates:
            if (assembly.prefix, seat.number, candidate.position) in PROVINCIAL_LINES:
                candidate.name, party = PROVINCIAL_LINES[assembly.prefix, seat.number, candidate.position]
                candidate.party = party_name(party)
            if (seat.number, candidate.votes) in run_together:
                candidate.name, candidate.party = run_together[seat.number, candidate.votes]
                candidate.party = party_name(candidate.party)
        for (number, position), (name, party, votes) in wrapped.items():
            if seat.number == number:
                seat.candidates.append(Candidate(position, name, party_name(party), votes))
        seat.candidates.sort(key=lambda c: c.position)
        numbers = [c.position for c in seat.candidates]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"{assembly.prefix}-{seat.number}: candidate numbering is {numbers}")
        for index, candidate in enumerate(seat.candidates, 1):
            candidate.position = index  # the volume skips a printed number in a few constituencies
        if len(seat.totals) != 4:
            raise ValueError(f"{assembly.prefix}-{seat.number}: totals are {seat.totals}")
        if sum(c.votes for c in seat.candidates) != seat.totals["Valid Votes"]:
            raise ValueError(
                f"{assembly.prefix}-{seat.number}: candidates sum to {sum(c.votes for c in seat.candidates)}"
            )
    if [seat.number for seat in found] != list(range(1, assembly.seats + 1)):
        raise ValueError(
            f"the volume does not list {assembly.prefix}-1 to {assembly.prefix}-{assembly.seats} once each"
        )
    return found


def printed_totals() -> dict[str, int]:
    """The printed national row of the party table: total seats and total votes."""
    for line in text(PARTIES).splitlines():
        if match := re.match(r"^\s*TOTAL\s+(\d+)\s+(\d+)\s*$", line):
            return {"seats": int(match.group(1)), "votes": int(match.group(2))}
    raise ValueError("no TOTAL row in the party table")


# The 2008 general election. The National Assembly and the four provincial assemblies were elected on one day.

DAY_2008 = date(2008, 2, 18)
PAGE_2008 = "https://web.archive.org/web/20251122094303id_/https://ecp.gov.pk/general-elections-2008"
VOLUME = "https://web.archive.org/web/20250406190549id_/https://ecp.gov.pk/storage/files/1/ge2008-2.pdf"
PARTY_VOTES = "https://web.archive.org/web/20230712135121if_/https://ecp.gov.pk/storage/files/1/ge2008-nrp2.pdf"
TURNOUT_NA = "https://web.archive.org/web/20230710144517if_/https://ecp.gov.pk/storage/files/1/ge2008-t1.pdf"
TURNOUT_PA = "https://web.archive.org/web/20240513151720if_/https://ecp.gov.pk/storage/files/1/ge2008-t2.pdf"
POSITION_NA = "https://web.archive.org/web/20240227123057if_/https://ecp.gov.pk/storage/files/1/ge2008-p1.pdf"
POSITION_PA = "https://web.archive.org/web/20240227122941if_/https://ecp.gov.pk/storage/files/1/ge2008-p2.pdf"
# The notifications of the candidates returned on 1 March 2008, by assembly. The Sindh notification is not archived.
RETURNED = {
    "NA": "https://web.archive.org/web/20240627001823id_/https://ecp.gov.pk/storage/files/1/ge2008-g1.pdf",
    "PF": "https://web.archive.org/web/20240627001825id_/https://ecp.gov.pk/storage/files/1/ge2008-g2.pdf",
    "PP": "https://web.archive.org/web/20240627001828id_/https://ecp.gov.pk/storage/files/1/ge2008-g3.pdf",
    "PB": "https://web.archive.org/web/20240627001831id_/https://ecp.gov.pk/storage/files/1/ge2008-g4.pdf",
}
FILES_2008 = [PAGE_2008, VOLUME, PARTY_VOTES, TURNOUT_NA, TURNOUT_PA, POSITION_NA, POSITION_PA, *RETURNED.values()]
ECP_2008 = key("source", "ecp", "2008")
ECP_BYE_2008 = key("source", "ecp", "2008-by")
BYE_DAY = date(2008, 6, 26)
SITE = "https://ecp.gov.pk/"
POSTAL = SITE + "exercise-of-right-of-vote-through-postal-ballot-papers-for-fresh-electionsbye-elections"
BYE_FILES = [
    SITE + "election-commission-issues-schedule-for-the-conduct-of-bye-elections-2008",
    SITE + "election-commission-issues-revised-schedule-for-the-conduct-of-bye-elections-2008",
    SITE + "bye-elections-programme-of-provincial-assembly",
    SITE + "election-schedule-of-national-assembly",
    POSTAL,
    SITE
    + "candidates-returned-to-the-provincial-assemblies-of-punjab-pp-59-pp-124-pp-219-pp-229-pp-243-pp-258-pp-277-as-a-result-of-bye-elections",
]
# The constituencies the Commission's release of 17 May 2008 lists for the fresh elections and by-elections it says
# were held on 26 June 2008. The volume prints no result for NA-123, NA-42, PF-59, PF-81 or PF-92.
POLLED_BYE = [
    "NA-11",
    "NA-52",
    "NA-55",
    "NA-119",
    "NA-123",
    "NA-131",
    "NA-147",
    "NA-207",
    "PP-10",
    "PP-48",
    "PP-59",
    "PP-70",
    "PP-99",
    "PP-107",
    "PP-118",
    "PP-124",
    "PP-141",
    "PP-154",
    "PP-171",
    "PP-219",
    "PP-229",
    "PP-243",
    "PP-258",
    "PP-277",
    "PP-295",
    "PS-30",
    "PS-44",
    "PS-62",
    "PF-20",
    "PF-45",
    "PF-59",
    "PF-75",
    "PF-81",
    "PF-91",
    "PF-92",
    "PB-9",
    "PB-32",
    "PB-44",
]
# The candidates the Commission's notification of 1 July 2008 returned to the Punjab Assembly, with their fathers.
RETURNED_BYE = {
    "PP-59": ("Qasim Zia", "Mian Zia-ud-Din"),
    "PP-124": ("Rana Shamim Ahmed Khan", "Rana Muhammad Charagh"),
    "PP-219": ("Karam Dad Wahla", "Abdul Rehman Wahla"),
    "PP-229": ("Sardar Wajid Ali", "Sardar Allah Ditta"),
    "PP-243": ("Sardar Zulfiqar Ali Khan Khosa", "Sardar Dost Muhammad Khan Khosa"),
    "PP-258": ("Mukhdoom Zada Syed Haroon Ahmed Sultan Bukhari", "Mukhdoom Syed Abdullah Shah Bukhari"),
    "PP-277": ("Mian Fida Hussain", "Mian Nazar Muhammad"),
}


class Assembly(NamedTuple):
    scheme: str
    body: str
    name: str
    seats: int
    label: str


ASSEMBLIES = {
    "NA": Assembly("pk.na", ASSEMBLY, "National Assembly of Pakistan", 272, "National Assembly"),
    "PP": Assembly("pk.pp", key("body", "pa", "punjab"), "Provincial Assembly of the Punjab", 297, "Punjab"),
    "PS": Assembly("pk.ps", key("body", "pa", "sindh"), "Provincial Assembly of Sindh", 130, "Sindh"),
    "PF": Assembly(
        "pk.pf", key("body", "pa", "nwfp"), "Provincial Assembly of the North-West Frontier Province", 99, "NWFP"
    ),
    "PB": Assembly("pk.pb", key("body", "pa", "balochistan"), "Provincial Assembly of Balochistan", 51, "Balochistan"),
}

# Constituencies whose poll of 18 February did not stand, by the notifications of 1 March 2008: polls postponed for
# law and order, and proceedings terminated on a candidate's death. The volume prints the later poll of some of them
# among the general results, so they are left out of this election. PB-9 polled, but the volume prints only its
# by-election, so it is left out too.
NOT_POLLED = {
    "NA-37": "postponed",
    "NA-42": "postponed",
    "NA-119": "terminated",
    "PP-70": "terminated",
    "PP-99": "terminated",
    "PP-141": "terminated",
    "PP-154": "terminated",
    "PP-171": "terminated",
    "PF-59": "terminated",
    "PF-81": "terminated",
    "PF-92": "terminated",
    "PB-9": "missing",
}

# The party names the 2008 volume prints, each mapped to one spelling: the Commission's own party-wise table where it
# lists the party.
PARTIES_2008 = {
    "Awami Himayat Tehreek Pakistan": None,
    "Awami National Party": None,
    "Azad Pakistan Party": None,
    "Balochistan National Party (Awami)": None,
    "Balochistan National Party(Awami)": "Balochistan National Party (Awami)",
    "Bedar Pakistan": None,
    "Hazara Democratic Party": None,
    "Independent": None,
    "Jamait Ahle-Hadith Pakistan (Elahi Zaheer)": None,
    "Jamiat-Ahle-Hadith Pakistan (Elahi Zaheer": "Jamait Ahle-Hadith Pakistan (Elahi Zaheer)",
    "Jamiat-Ahle-Hadith Pakistan (Elahi Zaheer)": "Jamait Ahle-Hadith Pakistan (Elahi Zaheer)",
    "Jamhoori Wattan Party": None,
    "Jamiat Ulama-e-Islam (S)": None,
    "Jamiat Ulama-e-Pakistan (Niazi)": None,
    "Labour Party Pakistan": None,
    "Markazi Jamiat Ulema-e-Pakistan (FK)": None,
    "Muthida Majilis-e-Amal Pakistan": "Mutthida Majlis-e-Amal Pakistan (MMA)",
    "Muttahida Qaumi Movement Pakistan": None,
    "Mutthida Majlis-e-Amal Pakistan (MMA)": None,
    "National Party": None,
    "National People Party": "National Peoples Party",
    "National Peoples Party": None,
    "Pak Muslim Alliance": None,
    "Pakistan Aman Party": None,
    "Pakistan Awami Party": None,
    "Pakistan Bachao Party": None,
    "Pakistan Citizen Movement": None,
    "Pakistan Democratic Party": None,
    "Pakistan Freedom Party": None,
    "Pakistan Gharib Party": None,
    "Pakistan Ittehad Tehreek": None,
    "Pakistan Muhafiz Party": None,
    "Pakistan Muslim League": None,
    "Pakistan Muslim League (F)": None,
    "Pakistan Muslim League (N)": None,
    "Pakistan Muslim League(N)": "Pakistan Muslim League (N)",
    # NA-205 cuts the name short; the party-wise table lists no party of this name.
    "Pakistan Peoples Party": "Pakistan Peoples Party Parliamentarians",
    "Pakistan Peoples Party (Shaheed Bhutto)": None,
    "Pakistan Peoples Party(SB)": "Pakistan Peoples Party (Shaheed Bhutto)",
    "Pakistan Peoples Party (Sherpao)": None,
    "Pakistan Peoples Party Parliamentarians": None,
    "Pakistan Qaumi League": None,
    "Pakistan Qaumi Party": None,
    "Pakistan Tehrek-e-Inqalab": None,
    "Pasban": None,
    "Punjab National Party": None,
    "Seraiki Sooba Movement Pakistan": None,
    "Sindh United Party": None,
    "Sunni Tehreek": None,
    "Tehrik-e-Istaqlal (Rehmat Khan Wardag)": None,
}
RETIRED = " (Retired)"

METHOD_2008 = (
    "Counted and published by constituency by the Election Commission of Pakistan in the second volume of its report "
    "on the 2008 general elections. Valid votes are the votes polled by the candidates, total votes are valid and "
    "rejected votes together, and registered voters are the voters on the electoral roll of the constituency. The "
    "volume does not publish the count of each polling station. The elected candidate is the one the Commission "
    "notified as returned on 1 March 2008."
)


class Word(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def y(self) -> float:
        return (self.y0 + self.y1) / 2


class Line(NamedTuple):
    y: float
    words: list[Word]


class Seat2008:
    def __init__(self, code: str, page: int) -> None:
        self.code = code
        self.page = page
        self.header: list[Line] = []
        self.lines: list[Line] = []
        self.totals: dict[str, int] = {}
        self.columns = (0.0, 0.0, 0.0)
        self.name = ""
        self.candidates: list[Candidate2008] = []
        self.tacit = False

    @property
    def prefix(self) -> str:
        return self.code.split("-")[0]

    @property
    def number(self) -> int:
        return int(self.code.split("-")[1])


class Candidate2008:
    def __init__(self, serial: int, name: str, party: str, votes: int | None, retired: bool) -> None:
        self.serial = serial
        self.name = name
        self.party = party
        self.votes = votes
        self.retired = retired


CODE = re.compile(r"^(NA|PP|PS|PF|PB)-(\d+)$")
WORD = re.compile(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
LABELS = {
    "Valid Votes": "valid",
    "Rejected Votes": "rejected",
    "Total Votes": "total",
    "Regd. Voters": "registered",
    "Regd. Vots": "registered",
    # PB-18 prints its rejected votes with a stray backslash in place of the label.
    "\\": "rejected",
}
TOTAL_2008 = re.compile(r"^(Valid Votes|Rejected Votes|Total Votes|Regd\. Voters|Regd\. Vots|\\)\s+(\d+)$")


def pages(url: str) -> list[list[Word]]:
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return [
        [Word(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(chunk)]
        for chunk in out.split("<page ")[1:]
    ]


def lines(words: list[Word]) -> list[Line]:
    found: list[Line] = []
    for word in sorted(words, key=lambda w: (w.y, w.x0)):
        if found and abs(found[-1].y - word.y) <= 2.5:
            found[-1].words.append(word)
        else:
            found.append(Line(word.y, [word]))
    return [Line(line.y, sorted(line.words)) for line in found]


def joined(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def general_seats(bye: bool = False) -> list[Seat2008]:
    """Every constituency of the volume's detailed results before its by-election pages, or, with `bye`, of the
    by-election pages after them."""
    seats: list[Seat2008] = []
    seen: dict[str, Seat2008] = {}
    current: Seat2008 | None = None
    started = not bye
    for number, words in enumerate(pages(VOLUME), 1):
        if "BYE ELECTIONS" in joined(words):
            if not bye:
                break
            started = True
            continue
        if not started:
            continue
        # The by-election pages print a code with a trailing comma, and run a code into the name beside it.
        words = [w._replace(text=w.text.rstrip(",")) if re.match(r"^[A-Z]{2}-\d+,$", w.text) else w for w in words]
        if any(w.text == "PF-91" for w in words) and any(w.text == "Charsadda-IV" for w in words):
            # PF-91's header is printed over the stale one of PF-20 Charsadda-IV.
            words = [w for w in words if w.text not in ("PF-20", "Charsadda-IV")]
        header = next((w for w in words if w.text == "Contesting"), None)
        if header is None:
            current = None
            continue
        zone = [w for w in words if abs(w.y0 - header.y0) < 14]
        columns = {w.text: w for w in zone}
        name_x, party_x, votes_x = columns["Contesting"].x0, columns["Party"].x0, columns["Votes"].x0
        top = max(
            w.y1
            for w in zone
            if w.text in ("Contesting", "Candidates", "Party", "Affilliation", "Votes", "Polled", "No.")
        )
        body = [w for w in words if top - 2 < w.y0 < 740]
        if bye and (left := [w.x0 for w in body if w.text == "Independent"]):
            party_x = min(party_x, min(left))  # some by-election pages print the party column left of its heading
        codes = sorted((w for w in body if CODE.match(w.text)), key=lambda w: w.y)
        if codes and codes[0].text in seen:
            # Each page repeats a constituency from earlier as a running header, sometimes over the real header.
            running = seen[codes[0].text]
            drop = [w.text for w in running.header[0].words]
            for w in sorted(body, key=lambda w: abs(w.y - codes[0].y)):
                if abs(w.y - codes[0].y) < 12 and w.text in drop:
                    drop.remove(w.text)
                    body.remove(w)
        for line in lines(body):
            code = next((w for w in line.words if CODE.match(w.text)), None)
            if code is not None:
                if code.text in seen:
                    raise ValueError(f"page {number}: {code.text} appears twice")
                current = Seat2008(code.text, number)
                current.columns = (name_x, party_x, votes_x)
                current.header.append(line)
                seen[code.text] = current
                seats.append(current)
                continue
            if current is None:
                raise ValueError(f"page {number}: a line before any constituency: {joined(line.words)}")
            text = joined(line.words)
            if total := TOTAL_2008.match(text):
                current.totals[LABELS[total.group(1)]] = int(total.group(2))
            elif text.lower().startswith("% of votes"):
                continue
            elif current.totals:
                raise ValueError(f"{current.code}: a line after the totals: {text}")
            else:
                current.lines.append(line)
    for seat in seats:
        rows(seat)
    return seats


def canonical(party: str) -> tuple[str, bool]:
    retired = party.endswith(RETIRED)
    party = party.removesuffix(RETIRED)
    if party not in PARTIES_2008:
        raise KeyError(party)
    return PARTIES_2008[party] or party, retired


def is_party_start(text: str) -> bool:
    return any(name.startswith(text) for name in PARTIES_2008)


def rows(seat: Seat2008) -> None:
    """Split a constituency's lines into candidates. A cell that wraps is either top-aligned, continuing below its
    row, or centered on its row, with the row's own line empty in that column; so a wrapped line between two rows
    belongs to the row whose own line is empty in that column, or else to the row above."""
    name_x, party_x, votes_x = seat.columns
    anchors: list[int] = []
    cells: list[dict[str, list[Word]]] = []
    for index, line in enumerate(seat.lines):
        cell: dict[str, list[Word]] = {"serial": [], "name": [], "party": [], "votes": []}
        for w in line.words:
            if w.x1 < name_x - 2 and w.text.isdigit():
                cell["serial"].append(w)
            elif w.x1 > votes_x - 10 and (w.text.isdigit() or "Contested" in w.text or w.text == "Un-"):
                cell["votes"].append(w)
            elif w.x0 >= party_x - 3:
                cell["party"].append(w)
            else:
                cell["name"].append(w)
        cells.append(cell)
        if cell["serial"]:
            anchors.append(index)
    # A line above the first row that is no party's start continues the constituency's header.
    while anchors and seat.lines and anchors[0] > 0:
        first = cells[0]
        text = joined(first["party"])
        if first["name"] or first["votes"] or is_party_start(text):
            break
        seat.header.append(seat.lines.pop(0))
        cells.pop(0)
        anchors = [a - 1 for a in anchors]
    words = [w for line in seat.header for w in line.words if not CODE.match(w.text)]
    seat.name = clean(joined(sorted(words, key=lambda w: (round(w.y), w.x0))))
    if not anchors:
        raise ValueError(f"{seat.code}: no candidates")
    owned: list[dict[str, list[Word]]] = [{"name": [], "party": [], "votes": []} for _ in anchors]
    for index, cell in enumerate(cells):
        for column in ("name", "party", "votes"):
            if not cell[column]:
                continue
            if index in anchors:
                owner = anchors.index(index)
            elif index < anchors[0]:
                owner = 0
            elif index > anchors[-1]:
                owner = len(anchors) - 1
            else:
                below = next(k for k, a in enumerate(anchors) if a > index)
                above = below - 1
                full_above = bool(cells[anchors[above]][column])
                full_below = bool(cells[anchors[below]][column])
                if full_above and not full_below:
                    owner = below
                elif full_below:
                    owner = above
                else:
                    gap_above = seat.lines[index].y - seat.lines[anchors[above]].y
                    gap_below = seat.lines[anchors[below]].y - seat.lines[index].y
                    if abs(gap_above - gap_below) < 1:
                        raise ValueError(f"{seat.code}: cannot place {joined(cell[column])}")
                    owner = above if gap_above < gap_below else below
            owned[owner][column].extend(cell[column])
    serials = [int(joined(cells[a]["serial"])) for a in anchors]
    if sorted(serials) != list(range(1, len(serials) + 1)):
        raise ValueError(f"{seat.code}: serial numbers {serials}")
    for serial, cell in zip(serials, owned, strict=True):
        name = clean(joined(sorted(cell["name"], key=lambda w: (round(w.y), w.x0))))
        party_words = sorted(cell["party"], key=lambda w: (round(w.y), w.x0))
        party = clean(joined(party_words))
        # A long name runs into the party column; move its words back until the rest is a party.
        while party_words and party.removesuffix(RETIRED) not in PARTIES_2008:
            name = f"{name} {party_words.pop(0).text}"
            party = clean(joined(party_words))
        if not party_words:
            raise ValueError(f"{seat.code}: no party for {name}: {joined(cell['party'])}")
        votes = joined(cell["votes"])
        if "Contested" in votes:
            seat.tacit = True
            count = None
        elif votes.isdigit():
            count = int(votes)
        else:
            raise ValueError(f"{seat.code}: votes {votes!r} for {name}")
        party, retired = canonical(party)
        seat.candidates.append(Candidate2008(serial, name, party, count, retired))
    if seat.tacit:
        if len(seat.candidates) != 1 or seat.totals:
            raise ValueError(f"{seat.code}: an uncontested seat with {len(seat.candidates)} candidates")
    elif set(seat.totals) != {"valid", "rejected", "total", "registered"}:
        raise ValueError(f"{seat.code}: totals are {seat.totals}")


class Notified(NamedTuple):
    name: str
    father: str


def returned(prefix: str) -> dict[str, Notified | None]:
    """The returned candidate of each constituency in a notification, or None where the notification is stayed or
    the election did not stand. A name is read from its column, which on each page runs from where most entries'
    names begin to where most of their fathers' names begin, through the lines it wraps onto."""
    found: dict[str, Notified | None] = {}
    entry = re.compile(r"^\s*(NA|PP|PS|PF|PB)-\s?(\d+)(.*)$")
    for page in text(RETURNED[prefix]).split("\f"):
        lines = page.splitlines()
        entries: list[tuple[str, int, str]] = []
        starts: list[int] = []
        ends: list[int] = []
        for index, line in enumerate(lines):
            match = entry.match(line)
            if not match or match.group(1) != prefix:
                continue
            code = f"{prefix}-{match.group(2)}"
            rest = match.group(3)
            fields = list(re.finditer(r"\S+(?: \S+)*", rest))
            # A constituency name that wraps leaves the code alone before the returned candidate's name.
            position = 0 if rest.startswith("  ") else 1
            father = fields[position + 1].group() if len(fields) > position + 1 else ""
            entries.append((code, index, father))
            if not re.search(r"stayed|Postponed|Terminated|expired|Returned cand", rest, re.IGNORECASE):
                starts.append(match.start(3) + fields[position].start())
                ends.append(match.start(3) + fields[position + 1].start())
        if not entries:
            continue
        start = max(set(starts), key=starts.count)
        end = max(set(ends), key=ends.count)
        for code, index, father in entries:
            block = [lines[index]]
            for line in lines[index + 1 :]:
                if not line.strip() or entry.match(line) or re.search(r"subject to|Order dated", line):
                    break
                block.append(line)
            whole = clean(" ".join(entry.sub(r"\3", line) for line in block))
            if appointed := re.search(r"The Returned candi\w+,? (.+?) appointed\b", whole):
                # Returned, then appointed governor before the notification.
                found[code] = Notified(appointed.group(1), "")
            elif re.search(r"stayed|Postponed|Terminated|expired", whole, re.IGNORECASE):
                found[code] = None
            else:
                found[code] = Notified(clean(" ".join(line[start - 2 : end - 1] for line in block)), father)
    if len(found) != ASSEMBLIES[prefix].seats:
        raise ValueError(f"{prefix} notification lists {len(found)} constituencies")
    return found


TITLES = {"mr", "mrs", "ms", "miss", "dr", "syed", "haji", "mian", "ch", "chaudhary", "sardar", "malik", "rana", "mst"}


def tokens(name: str) -> list[str]:
    words = re.findall(r"[a-z]+", name.lower())
    return [w for w in words if w not in TITLES and len(w) > 1]


def likeness(notified: str, candidate: str) -> float:
    """How well a notified name matches a candidate's name: over the words of each name, the mean of each word's best
    spelling match among the other name's words, weighted toward the notified words, since a notified name that
    wraps is cut short."""
    wanted, have = tokens(notified), tokens(candidate)
    if not wanted or not have:
        return 0.0

    def one_way(a: list[str], b: list[str]) -> float:
        return sum(max(SequenceMatcher(None, x, y).ratio() for y in b) for x in a) / len(a)

    return 0.7 * one_way(wanted, have) + 0.3 * one_way(have, wanted)


def squashed(name: str) -> str:
    return "".join(tokens(name))


def winner(seat: Seat2008, notified: Notified) -> Candidate2008:
    """The candidate a notification names, by the spelling of their names. Where the volume prints two candidates of
    one name, it adds each one's father, which the notification also gives."""
    name = notified.name
    exact = [c for c in seat.candidates if squashed(c.name) == squashed(name)]
    if len(exact) == 1:
        return exact[0]
    namesakes = [
        c for c in seat.candidates if " S/o " in c.name and squashed(c.name.split(" S/o ")[0]) == squashed(name)
    ]
    if len(namesakes) > 1:
        return max(namesakes, key=lambda c: likeness(notified.father, c.name.split(" S/o ")[1]))
    scored = sorted(((likeness(name, c.name), c) for c in seat.candidates), key=lambda t: t[0], reverse=True)
    best = scored[0]
    if best[0] < 0.6 or (len(scored) > 1 and scored[1][0] > best[0] - 0.05):
        raise ValueError(
            f"{seat.code}: the notification's {name!r} matches no single candidate: "
            f"{[(round(x, 2), c.name) for x, c in scored[:3]]}"
        )
    return best[1]


def seats_2008() -> list[Seat2008]:
    seats = [s for s in general_seats() if s.code not in NOT_POLLED]
    for prefix, assembly in ASSEMBLIES.items():
        have = {s.number for s in seats if s.prefix == prefix}
        expected = {n for n in range(1, assembly.seats + 1) if f"{prefix}-{n}" not in NOT_POLLED}
        if have != expected:
            raise ValueError(f"{prefix}: missing {sorted(expected - have)}, extra {sorted(have - expected)}")
    return sorted(seats, key=lambda s: (list(ASSEMBLIES).index(s.prefix), s.number))


def elected_2008(seats: list[Seat2008]) -> dict[str, Candidate2008]:
    """The candidate each available notification returned, by constituency."""
    found: dict[str, Candidate2008] = {}
    by_code = {s.code: s for s in seats}
    for prefix in RETURNED:
        for code, name in returned(prefix).items():
            if name is not None and code in by_code:
                found[code] = winner(by_code[code], name)
    return found


def election_key(volume: Volume) -> str:
    return "2002-10-10" if volume.prefix == "NA" else f"2002-10-10-{volume.prefix.lower()}"


def packages() -> list[str]:
    return ["pk.ecp/registry", *(f"pk.ecp/{election_key(v)}" for v in VOLUMES), "pk.ecp/2008-02-18", "pk.ecp/2008-06-26"]


def fetch() -> None:
    for url in (*(v.url for v in VOLUMES), PARTIES, *FILES_2008):
        raw.fetch(SOURCE, url, suffix=".html" if url == PAGE_2008 else ".pdf")
    for url in BYE_FILES:
        raw.fetch(SOURCE, url, suffix=".html")


def parse() -> None:
    registry().write()
    for volume in VOLUMES:
        build(volume).write()
    build_2008().write()
    build_bye_2008().write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        "pk.ecp/registry",
        SOURCE,
        "Pakistani parties, the National Assembly, the provincial assemblies, and their electoral system, from the "
        "Election Commission's detailed results of 2002 and 2008",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=ECP_SOURCE,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="General Elections 2002: constituency-wise detailed results",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in (*(v.url for v in VOLUMES), PARTIES):
        p.artifact(raw.cached(SOURCE, url), ECP_SOURCE)
    p.add(
        "source",
        id=ECP_2008,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="General Elections 2008: report, constituency-wise detailed results, and notifications",
        url="https://ecp.gov.pk/general-elections-2008",
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in FILES_2008:
        p.artifact(raw.cached(SOURCE, url), ECP_2008)
    p.add("scheme", code="pk.na", polity_id=PAKISTAN, kind="electoral_district", rank=1, noun="constituency")
    p.add("body", id=ASSEMBLY, polity_id=PAKISTAN, kind="lower_house")
    p.name(ASSEMBLY, "National Assembly of Pakistan", "en", "official")
    for prefix, assembly in ASSEMBLIES.items():
        if prefix == "NA":
            continue
        p.add(
            "scheme", code=assembly.scheme, polity_id=PAKISTAN, kind="electoral_district", rank=1, noun="constituency"
        )
        p.add("body", id=assembly.body, polity_id=PAKISTAN, kind="unicameral")
        p.name(assembly.body, assembly.name, "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each general constituency of the National Assembly and of the provincial assemblies elects one "
        "member by plurality. Seats reserved for women and non-Muslims are filled from party lists in proportion to "
        "the general seats won, and are not part of the constituency results stored here.",
        lang="en",
    )
    names = {party_name(c.party): None for v in VOLUMES for seat in constituencies(v) for c in seat.candidates}
    names |= {c.party: None for seat in seats_2008() for c in seat.candidates}
    for name in sorted(names):
        if name == "Independent":
            continue
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=PAKISTAN)
        p.name(id, name, "en", "official")
    return p


def build(assembly: Volume = VOLUMES[0]) -> Package:
    national = assembly.prefix == "NA"
    polity = holder(assembly)
    prefix = assembly.prefix
    scheme = f"pk.{prefix.lower()}"
    if national:
        summary = (
            "The Pakistani National Assembly general election of 2002 by constituency, from the Election "
            "Commission's detailed results"
        )
        title = "2002 Pakistani general election"
        path = "pk/2002-10-10-general"
        what = "Pakistani National Assembly election"
    else:
        summary = (
            f"The {assembly.name} provincial assembly general election of 2002 by constituency, from the Election "
            "Commission's detailed results"
        )
        title = f"2002 {assembly.name} provincial election"
        path = f"pk/{assembly.slug}/2002-10-10-general"
        what = f"{assembly.name} Provincial Assembly election"
    p = Package(f"pk.ecp/{election_key(assembly)}", SOURCE, summary, depends=["pk.ecp/registry"])
    p.external.update({PAKISTAN, polity})
    for url in (assembly.url, PARTIES) if national else (assembly.url,):
        p.uses(raw.cached(SOURCE, url))
    election = key("election", *(("2002",) if national else ("2002", prefix.lower())))
    p.add("election", id=election, polity_id=polity, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, title, "en", "official")
    for seat in constituencies(assembly):
        contest = key("contest", *ident(assembly, seat.number))
        label = f"{prefix}-{seat.number} {seat.name}"
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=(ASSEMBLY if national else ASSEMBLIES[prefix].body),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"2002 {what}, {label}", "en", "official")
        p.add("slug", path=f"{path}/{prefix.lower()}-{seat.number}", entity_id=contest, is_current=True)
        unit = key("unit", *ident(assembly, seat.number))
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PAKISTAN,
            scheme_code=scheme,
            code=f"2002-10-10:{prefix}-{seat.number}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, label, "en", "official")
        result_set = key("result_set", *ident(assembly, seat.number))
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECP_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        accounting = (
            (REGISTERED, seat.totals["Registered Voters"]),
            (CAST, seat.totals["Total Votes"]),
            (INVALID, seat.totals["Rejected Votes"]),
            (VALID, seat.totals["Valid Votes"]),
        )
        for measure, value in accounting:
            put(p, result_set, unit, None, measure, value)
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for c in seat.candidates:
            option = key("option", *ident(assembly, seat.number, c.position))
            independent = c.party == "Independent"
            person = key("person", *ident(assembly, seat.number, c.position))
            p.add("person", id=person)
            p.name(person, c.name, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=c.position,
                ballot_label=c.name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", *ident(assembly, seat.number, c.position)),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=None if independent else party_id(c.party),
                is_independent=independent,
            )
            put(p, result_set, unit, option, VOTES, c.votes)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.votes)
    return p


def build_2008() -> Package:
    p = Package(
        "pk.ecp/2008-02-18",
        SOURCE,
        "The Pakistani general election of 2008 for the National Assembly and the four provincial assemblies by "
        "constituency, from the Election Commission's detailed results and notifications of returned candidates",
        depends=["pk.ecp/registry"],
    )
    p.external.add(PAKISTAN)
    for url in FILES_2008:
        p.uses(raw.cached(SOURCE, url))
    seats = seats_2008()
    elected = elected_2008(seats)
    notified = {code: name for prefix in RETURNED for code, name in returned(prefix).items()}
    election = key("election", "2008")
    p.add(
        "election", id=election, polity_id=PAKISTAN, kind="general", date_from=DAY_2008, date_to=DAY_2008, status="held"
    )
    p.add("slug", path="pk/2008-02-18-general", entity_id=election, is_current=True)
    p.name(election, "2008 Pakistani general election", "en", "official")
    left_out = ", ".join(code for code, why in NOT_POLLED.items() if why != "missing")
    p.add(
        "note",
        id=key("note", election, "left-out"),
        entity_id=election,
        kind="context",
        body_md="The National Assembly and the provincial assemblies of the Punjab, Sindh, the North-West Frontier "
        "Province, and Balochistan were elected on the same day. The polls in "
        f"{left_out} did not stand: they were postponed for law and order, or their proceedings were terminated on "
        "the death of a contesting candidate, and they were polled later, so they are not part of this election. "
        "PB-9 polled, but its returned candidate died on 21 February 2008, and the Commission's report prints only "
        "its later by-election, so it is not stored here either.",
        lang="en",
    )
    for seat in seats:
        notes = caveats(seat, seat.code in elected, notified)
        add_seat(
            p,
            seat,
            election,
            "2008",
            "2008-02-18",
            "pk/2008-02-18-general",
            "election",
            ECP_2008,
            METHOD_2008,
            elected,
            notes,
        )
    for code, reason in ACCEPTED_2008.items():
        p.accept(code, reason)
    return p


ACCEPTED_2008 = {
    "accounting.votes_valid": "In 17 constituencies the candidates' votes printed in the Commission's volume do not "
    "sum to the valid votes it prints for them (NA-25, NA-33, NA-203, NA-216, PP-9, PP-194, PS-57, PS-69, PS-111, "
    "PS-128, PF-30, PF-33, PF-50, PF-61, PF-63, PF-68, PF-75); both are stored as printed, with a caveat on each.",
    "accounting.ballots_sum": "PF-50's printed valid and rejected votes (58,609 and 1,313) sum to 81 fewer than its "
    "printed total votes (60,003); all three are stored as printed, with a caveat.",
}


def add_seat(
    p: Package,
    seat: Seat2008,
    election: str,
    tag: str,
    tag_day: str,
    slug: str,
    noun: str,
    source: str,
    method: str,
    elected: dict[str, Candidate2008],
    notes: list[str],
    year: int = 2008,
) -> None:
    assembly = ASSEMBLIES[seat.prefix]
    contest = key("contest", tag, seat.code)
    label = f"{seat.code} {seat.name}"
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=assembly.body,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="tacit" if seat.tacit else "counted",
    )
    title = (
        f"{year} Pakistani National Assembly {noun}"
        if seat.prefix == "NA"
        else f"{year} {assembly.label} Provincial Assembly {noun}"
    )
    p.name(contest, f"{title}, {label}", "en", "official")
    p.add("slug", path=f"{slug}/{seat.code.lower()}", entity_id=contest, is_current=True)
    result_set = key("result_set", tag, seat.code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if seat.tacit else "counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=None if seat.tacit else 1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    for index, caveat in enumerate(notes):
        p.add(
            "note",
            id=key("note", result_set, "caveat", index),
            entity_id=result_set,
            kind="caveat",
            body_md=caveat,
            lang="en",
        )
    unit = key("unit", tag, seat.code)
    if not seat.tacit:
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PAKISTAN,
            scheme_code=assembly.scheme,
            code=f"{tag_day}:{seat.code}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, label, "en", "official")
        accounting = (
            (REGISTERED, seat.totals["registered"]),
            (CAST, seat.totals["total"]),
            (INVALID, seat.totals["rejected"]),
            (VALID, seat.totals["valid"]),
        )
        for measure, value in accounting:
            if measure == REGISTERED and value == 0:
                continue  # printed as 0 where the figure is missing
            put(p, result_set, unit, None, measure, value)
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for position, c in enumerate(seat.candidates, 1):
        option = key("option", tag, seat.code, position)
        independent = c.party == "Independent"
        person = key("person", tag, seat.code, position)
        p.add("person", id=person)
        p.name(person, c.name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=c.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, seat.code, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=None if independent else party_id(c.party),
            is_independent=independent,
        )
        if seat.code in elected:
            kind = "elected" if elected[seat.code] is c else "withdrew" if c.retired else "lost"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        elif c.retired:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="withdrew")
        if c.votes is not None:
            put(p, result_set, unit, option, VOTES, c.votes)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.votes)


def caveats(seat: Seat2008, has_winner: bool, notified: dict[str, Notified | None], bye: bool = False) -> list[str]:
    found: list[str] = []
    if not seat.tacit:
        votes = sum(c.votes or 0 for c in seat.candidates)
        if votes != seat.totals["valid"]:
            found.append(
                f"The candidates' votes printed in the Commission's volume sum to {votes:,}, while it prints "
                f"{seat.totals['valid']:,} valid votes for the constituency. Both are stored as printed."
            )
        ballots = seat.totals["valid"] + seat.totals["rejected"]
        if ballots != seat.totals["total"]:
            found.append(
                f"The printed valid and rejected votes sum to {ballots:,}, while the volume prints "
                f"{seat.totals['total']:,} total votes. All three are stored as printed."
            )
        if seat.totals["registered"] == 0:
            found.append(
                "The volume prints 0 registered voters and no turnout for this constituency, so the number of "
                "registered voters is not stored."
            )
    else:
        found.append("The only candidate was returned unopposed, so no poll was held.")
    if not has_winner and not seat.tacit and bye:
        found.append(
            "The Commission's notification of the candidate returned from this poll is not among the files used "
            "here, so no winner is stored for this constituency."
        )
    elif not has_winner:
        if seat.prefix == "PS":
            found.append(
                "The Commission's notification of the candidates returned to the Provincial Assembly of Sindh is "
                "not archived, so no winner is stored for this constituency."
            )
        elif notified.get(seat.code) is None:
            found.append(
                "The Commission's notification of 1 March 2008 stayed the return of a candidate here, by its own "
                "order or a court's, so no winner is stored for this constituency."
            )
    if any(c.retired for c in seat.candidates):
        found.append("The volume marks a candidate as retired: they withdrew, but stayed on the ballot.")
    return found


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def bye_seats() -> list[Seat2008]:
    """The seats of the 26 June 2008 polls: those the by-election pages print, and the later polls the general pages
    print, among the constituencies the Commission's release lists."""
    found = {seat.code: seat for seat in general_seats(True) if seat.code in POLLED_BYE}
    for seat in general_seats():
        if seat.code in NOT_POLLED and seat.code in POLLED_BYE and seat.code not in found:
            found[seat.code] = seat
    order = list(ASSEMBLIES)
    return sorted(found.values(), key=lambda s: (order.index(s.prefix), s.number))


def build_bye_2008() -> Package:
    p = Package(
        "pk.ecp/2008-06-26",
        SOURCE,
        "The Pakistani by-elections of 26 June 2008 to the National Assembly and the provincial assemblies, by "
        "constituency, from the Election Commission's second report volume on the 2008 general elections",
        depends=["pk.ecp/registry"],
    )
    p.external.add(PAKISTAN)
    for url in (VOLUME, *BYE_FILES):
        p.uses(raw.cached(SOURCE, url))
    p.add(
        "source",
        id=ECP_BYE_2008,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Bye-elections of 2008: schedules, press releases, and notifications",
        url=SITE + "election-commission-issues-schedule-for-the-conduct-of-bye-elections-2008",
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in BYE_FILES:
        p.artifact(raw.cached(SOURCE, url), ECP_BYE_2008)
    seats = bye_seats()
    by_code = {s.code: s for s in seats}
    elected: dict[str, Candidate2008] = {s.code: s.candidates[0] for s in seats if s.tacit}
    for code, (name, father) in RETURNED_BYE.items():
        elected[code] = winner(by_code[code], Notified(name, father))
    election = key("election", "2008-06-26")
    p.add(
        "election",
        id=election,
        polity_id=PAKISTAN,
        kind="by_election",
        date_from=BYE_DAY,
        date_to=BYE_DAY,
        status="held",
    )
    p.add("slug", path="pk/2008-06-26-by-election", entity_id=election, is_current=True)
    p.name(election, "2008 Pakistani by-elections", "en", "official")
    p.add(
        "note",
        id=key("note", election, "context"),
        entity_id=election,
        kind="context",
        body_md="The Commission scheduled these by-elections, and the fresh elections that replaced the polls "
        "terminated on a candidate's death in the general election of 18 February 2008, for 18 June 2008, then "
        "revised the schedule. Its press release of 17 May 2008 gives 26 June 2008 as the day of the polls. The "
        "volume prints a result for the constituencies stored here; for the others in the release, and for NA-37, "
        "NA-42, NA-210, and PP-250, it prints none or a poll on another day.",
        lang="en",
    )
    for seat in seats:
        notes = caveats(seat, seat.code in elected, {}, bye=True)
        add_seat(
            p,
            seat,
            election,
            "2008-06-26",
            "2008-06-26",
            "pk/2008-06-26-by-election",
            "by-election",
            ECP_2008,
            METHOD_BYE,
            elected,
            notes,
        )
    for code, reason in ACCEPTED_BYE.items():
        p.accept(code, reason)
    return p


METHOD_BYE = (
    "Counted and published by constituency by the Election Commission of Pakistan in the second volume of its report "
    "on the 2008 general elections. Valid votes are the votes polled by the candidates, total votes are valid and "
    "rejected votes together, and registered voters are the voters on the electoral roll of the constituency. The "
    "volume does not publish the count of each polling station. The polling day is the one given in the "
    "Commission's press release of 17 May 2008."
)
ACCEPTED_BYE = {
    "accounting.votes_valid": "In PP-59, PP-124, PP-154, PP-258, and PF-91 the candidates' votes printed in the "
    "Commission's volume do not sum to the valid votes it prints for them; both are stored as printed, with a "
    "caveat on each.",
    "accounting.ballots_sum": "In PP-107, PP-124, and PP-258 the printed valid and rejected votes do not sum to the "
    "printed total votes (PP-124 prints a total equal to its valid votes); all three are stored as printed, with a "
    "caveat on each.",
}
