"""Election Commission, Nepal: indirect elections from the Commission's press releases. National Assembly members are
chosen by each province's electoral college of Provincial Assembly members and local-level chairs, vice-chairs, mayors,
and deputy mayors, and the President and Vice-President by the members of the Federal Parliament and the Provincial
Assemblies, all with weighted votes. The releases are scanned images or PDFs set in a legacy font, so every number was
read from the page and is stored with the box it was read from."""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote
from uuid import uuid5

import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.np import eci

SOURCE = "np.press"
NEPAL = str(uuid5(package.NAMESPACE, "polity\x1fNP"))
COUNTRY = eci.COUNTRY
STORAGE = "https://election.gov.np/admin/public//storage/"
LISTING = "https://election.gov.np/admin/public/api/resources/press-release?lang=np&per_page=1000"
OUTLINES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/NPL/ADM1/"
    "geoBoundaries-NPL-ADM1_simplified.geojson"
)


def url(path: str) -> str:
    return STORAGE + quote(path, safe="/")


# Press releases by their id in the Commission's press-release listing.
RELEASES = {
    1049: url("Press Release/_Press_Release_2078.10.12.jpg"),
    2797: url("PPP_Image.jpg"),
    2864: url("Presidential Election/B486AB6A-34C0-40CC-8FC2-49EB6919E387.jpeg"),
    2875: url("Presidential Election/BE49E82B-6EB1-42A8-8AEA-49D82B9BD6B6.jpeg"),
    3122: url("Press Release/Press Relases_2080-10-11-2 (राष्ट्रिय सभा सदस्य निर्वाचनको मतपरिणाम सार्वजनिक).pdf"),
    3124: url("Press Release/प्रेस विज्ञप्ति २०८०।१०।१४.pdf"),
    4303: url("HOR 2082/Press Release/2082-10-11 Third Press Release.pdf"),
    4311: url("HOR 2082/Press Release/2082-10-14.pdf"),
}
# Pixel size of each page as the boxes below measure it: JPEGs at their own size, PDFs rendered at 200 dpi.
PAGES = {
    1049: [(2550, 3508)],
    2797: [(1700, 2338)],
    2864: [(2550, 3507)],
    2875: [(2550, 3507)],
    3122: [(1653, 2337)] * 3,
    4303: [(1653, 2339)] * 3,
}

PROVINCES = {1: "koshi", 2: "madhesh", 3: "bagmati", 4: "gandaki", 5: "lumbini", 6: "karnali", 7: "sudurpashchim"}
GROUPS = {
    "women": ("महिला", "women"),
    "dalit": ("दलित", "Dalit"),
    "minority": ("अपाङ्गता भएका व्यक्ति वा अल्पसंख्यक", "persons with disabilities or minorities"),
    "other": ("अन्य", "other"),
}
PARTIES = {
    "uml": ("नेपाल कम्युनिष्ट पार्टी (एकीकृत मार्क्सवादी लेनिनवादी)", "Communist Party of Nepal (Unified Marxist–Leninist)"),
    "nc": ("नेपाली काँग्रेस", "Nepali Congress"),
    "maoist": ("नेपाल कम्युनिष्ट पार्टी (माओवादी केन्द्र)", "Communist Party of Nepal (Maoist Centre)"),
    "us": ("नेपाल कम्युनिष्ट पार्टी (एकीकृत समाजवादी)", "Communist Party of Nepal (Unified Socialist)"),
    "jsp": ("जनता समाजवादी पार्टी, नेपाल", "People's Socialist Party, Nepal"),
    "ncp": ("नेपाली कम्युनिष्ट पार्टी", "Nepali Communist Party"),
    "lsp": ("लोकतान्त्रिक समाजवादी पार्टी, नेपाल", "Loktantrik Samajwadi Party, Nepal"),
    "nwpp": ("नेपाल मजदुर किसान पार्टी", "Nepal Workers and Peasants Party"),
    "rjm": ("राष्ट्रिय जनमोर्चा", "Rastriya Janamorcha"),
    "nup": ("नागरिक उन्मुक्ति पार्टी", "Nagarik Unmukti Party"),
}


@dataclass(frozen=True)
class Row:
    ne: str
    en: str
    party: str | None
    votes: int | None
    elected: bool
    page: int = 1
    box: tuple[float, float, float, float] | None = None
    split: tuple[int, int, int, int] | None = None  # assembly votes and weight, local votes and weight


@dataclass(frozen=True)
class Contest:
    province: int
    group: str
    rows: tuple[Row, ...]


def _cells(page: int, x: tuple[int, int], edges: list[int], rows: list[tuple[str, str, str, int | None, bool]]):
    if len(edges) != len(rows) + 1:
        raise ValueError(f"page {page}: {len(edges)} rules for {len(rows)} rows")
    return [
        Row(ne, en, party, votes, won, page, (x[0], edges[i], x[1], edges[i + 1]))
        for i, (ne, en, party, votes, won) in enumerate(rows)
    ]


# 2082 Magh 11 (2026-01-24), release 4303: a table of every candidate's weighted votes, read cell by cell.
_P1 = _cells(
    1,
    (1216, 1345),
    [913, 1003, 1048, 1137, 1182, 1271, 1360, 1448, 1537, 1582, 1670, 1759, 1804, 1848, 1977, 2022],
    [
        ("रोशनी मेचे", "Roshni Meche", "uml", 7173, True),
        ("नमिता न्यौपाने", "Namita Nyaupane", "ncp", 2268, False),
        ("सोमनाथ पोर्तेल", "Somnath Portel", "uml", 7423, True),
        ("खेमराज नेपाली", "Khemraj Nepali", "ncp", 2037, False),
        ("सुनिल बहादुर थापा", "Sunil Bahadur Thapa", "nc", None, True),
        ("रेखा कुमारी झा", "Rekha Kumari Jha", "uml", 7222, True),
        ("शमी कुमारी अग्रवाल", "Shami Kumari Agrawal", "ncp", 1944, False),
        ("चन्द्रकला देवी साह", "Chandrakala Devi Sah", "jsp", 322, False),
        ("धर्मेन्द्र पासवान", "Dharmendra Paswan", "nc", 7904, True),
        ("शम्भु पासवान हजारी", "Shambhu Paswan Hajari", "ncp", 1376, False),
        ("चमेली देवी दास", "Chameli Devi Das", "jsp", 379, False),
        ("रन्जित कर्ण", "Ranjit Karna", "nc", 8093, True),
        ("रामनन्दन प्रसाद", "Ramnandan Prasad", "ncp", 1361, False),
        ("शैलेश कुमार चौधरी", "Shailesh Kumar Chaudhari", "jsp", 277, False),
        ("लक्ष्मण पौडेल", "Laxman Poudel", "ncp", 1805, False),
    ],
)
_P2 = _cells(
    2,
    (1222, 1351),
    [406, 496, 541, 586, 631, 720, 810, 855, 944, 1033, 1078, 1124, 1169, 1214, 1303, 1391, 1436, 1481, 1656, 1744]
    + [1833, 1878, 1966, 2010],
    [
        ("महन्थ ठाकुर", "Mahantha Thakur", "lsp", 7725, True),
        ("गिता देवकोटा", "Gita Devkota", "nc", 6239, True),
        ("सरोज कुमारी झा", "Saroj Kumari Jha", "ncp", 2522, False),
        ("कविता महर्जन", "Kabita Maharjan", "nwpp", 197, False),
        ("प्रेम प्रसाद दंगाल", "Prem Prasad Dangal", "uml", 6205, True),
        ("प्रेम प्रसाद सापकोटा", "Prem Prasad Sapkota", "ncp", 2541, False),
        ("विक्रम ढुकुछु", "Bikram Dhukuchhu", "nwpp", 197, False),
        ("सम्झना देवकोटा", "Samjhana Devkota", "uml", 5014, True),
        ("गोमा लुइटेल घिमिरे", "Goma Luitel Ghimire", "ncp", 941, False),
        ("लक्ष्मी बास्कोटा", "Laxmi Baskota", "rjm", 76, False),
        ("जगत तिमिल्सिना", "Jagat Timilsina", "nc", 5033, True),
        ("कपिल लामा", "Kapil Lama", "ncp", 941, False),
        ("शंकर प्रसाद बराल", "Shankar Prasad Baral", "rjm", 76, False),
        ("राम कुमारी झाक्री", "Ram Kumari Jhakri", "uml", 6303, True),
        ("दुर्गा कुमारी भण्डारी", "Durga Kumari Bhandari", "ncp", 1362, False),
        ("पद्मा पण्डित", "Padma Pandit", "rjm", 186, False),
        ("वासुदेव घिमिरे", "Basudev Ghimire", "nc", 6322, True),
        ("सर्जन वि. क.", "Sarjan B.K.", "ncp", 1601, False),
        ("चन्द्र बहादुर के. सी.", "Chandra Bahadur K.C.", "nc", 5977, True),
        ("वृहस्पति अधिकारी", "Brihaspati Adhikari", "ncp", 1832, False),
        ("वोम बहादुर खत्री", "Bom Bahadur Khatri", "rjm", 167, False),
        ("मीनासिंह रखाल", "Meenasingh Rakhal", "uml", 3145, True),
        ("अन्जु महतारा", "Anju Mahatara", "ncp", 1734, False),
    ],
)
_P3 = _cells(
    3,
    (1215, 1345),
    [409, 498, 543, 632, 678, 766, 812],
    [
        ("ललित जङ्ग शाही", "Lalit Jung Shahi", "nc", 3278, True),
        ("वुध्दि प्रसाद शर्मा", "Buddhi Prasad Sharma", "ncp", 1639, False),
        ("लिला कुमारी भण्डारी", "Lila Kumari Bhandari", "uml", 3984, True),
        ("रोजी बस्नेत", "Roji Basnet", "ncp", 1676, False),
        ("खम्म बहादुर खाती", "Khamma Bahadur Khati", "nc", 3969, True),
        ("टिका राम जैशी", "Tika Ram Jaishi", "ncp", 1805, False),
    ],
)
_T = [*_P1, *_P2, *_P3]
NA_2026 = [
    Contest(1, "women", tuple(_T[0:2])),
    Contest(1, "dalit", tuple(_T[2:4])),
    Contest(1, "other", tuple(_T[4:5])),
    Contest(2, "women", tuple(_T[5:8])),
    Contest(2, "dalit", tuple(_T[8:11])),
    Contest(2, "minority", tuple(_T[11:14])),
    Contest(2, "other", tuple(_T[14:16])),
    Contest(3, "women", tuple(_T[16:19])),
    Contest(3, "other", tuple(_T[19:22])),
    Contest(4, "women", tuple(_T[22:25])),
    Contest(4, "other", tuple(_T[25:28])),
    Contest(5, "women", tuple(_T[28:31])),
    Contest(5, "minority", tuple(_T[31:33])),
    Contest(5, "other", tuple(_T[33:36])),
    Contest(6, "women", tuple(_T[36:38])),
    Contest(6, "other", tuple(_T[38:40])),
    Contest(7, "women", tuple(_T[40:42])),
    Contest(7, "other", tuple(_T[42:44])),
]


def _won(province: int, group: str, ne: str, en: str, party: str, split: tuple[int, int, int, int], page: int, y):
    a, wa, b, wb = split
    return Contest(province, group, (Row(ne, en, party, a * wa + b * wb, True, page, (280, y[0], 1500, y[1]), split),))


# 2080 Magh 11 (2024-01-25), release 3122: prose naming each winner with the votes and weighted votes from
# Provincial Assembly members (weight 53) and from local-level chairs and mayors (weight 19). Losing candidates' votes
# are not published.
NA_2024 = [
    _won(1, "women", "रुकमिणी कोइराला", "Rukmini Koirala", "uml", (49, 53, 131, 19), 1, (717, 907)),
    _won(1, "other", "कृष्णप्रसाद सिटौला", "Krishna Prasad Sitaula", "nc", (45, 53, 153, 19), 1, (887, 1107)),
    _won(2, "women", "पूजा चौधरी", "Puja Chaudhary", "jsp", (76, 53, 205, 19), 1, (1122, 1322)),
    _won(2, "other", "आनन्दप्रसाद ढुंगाना", "Ananda Prasad Dhungana", "nc", (67, 53, 201, 19), 1, (1322, 1522)),
    _won(3, "women", "बिष्णुदेवी पुडासैनी", "Bishnu Devi Pudasaini", "nc", (64, 53, 170, 19), 1, (1552, 1782)),
    _won(3, "dalit", "घनश्याम रिजाल", "Ghanashyam Rijal", "us", (63, 53, 167, 19), 1, (1752, 1942)),
    _won(3, "minority", "कृष्णप्रसाद अधिकारी", "Krishna Prasad Adhikari", "maoist", (62, 53, 163, 19), 2, (307, 457)),
    _won(3, "other", "जितजंग बस्नेत", "Jitjung Basnet", "nc", (63, 53, 168, 19), 2, (417, 667)),
    _won(4, "women", "मनरुपा शर्मा", "Manarupa Sharma", "maoist", (35, 53, 95, 19), 2, (677, 927)),
    _won(4, "minority", "पदमबहादुर परियार", "Padam Bahadur Pariyar", "nc", (35, 53, 101, 19), 2, (887, 1127)),
    _won(4, "other", "किरणबाबु श्रेष्ठ", "Kiran Babu Shrestha", "nc", (36, 53, 100, 19), 2, (1087, 1282)),
    _won(5, "women", "विष्णुकुमारी सापकोटा", "Bishnu Kumari Sapkota", "nc", (52, 53, 141, 19), 2, (1292, 1502)),
    _won(5, "other", "झक्कुप्रसाद सुवेदी", "Jhakku Prasad Subedi", "maoist", (52, 53, 141, 19), 2, (1502, 1722)),
    _won(6, "women", "सावित्री मल्ल", "Sabitri Malla", "us", (29, 53, 115, 19), 2, (1762, 2002)),
    _won(6, "dalit", "विष्णुबहादुर विश्वकर्मा", "Bishnu Bahadur Bishwakarma", "maoist", (29, 53, 120, 19), 3, (307, 487)),
    _won(6, "other", "कृष्णबहादुर रोकाया", "Krishna Bahadur Rokaya", "nc", (30, 53, 120, 19), 3, (477, 667)),
    _won(7, "women", "रेनु चन्द", "Renu Chand", "maoist", (41, 53, 120, 19), 3, (677, 887)),
    _won(7, "minority", "नारायणदत्त भट्ट", "Narayan Datta Bhatta", "nc", (40, 53, 121, 19), 3, (887, 1087)),
    _won(7, "other", "बलदेव बोहरा", "Baldev Bohara", "nc", (39, 53, 119, 19), 3, (1087, 1282)),
]
# The release's own totals for each winner, to check the transcription of the split against.
NA_2024_TOTALS = [5086, 5292, 7923, 7370, 6622, 6512, 6383, 6531, 3660, 3774, 3808, 5435, 5435, 3722, 3817, 3870]
NA_2024_TOTALS += [4453, 4419, 4328]

_W22 = [1134, 1246, 1393, 1583, 1649, 1714, 1825, 1889, 1955, 2021, 2087, 2199, 2264, 2329, 2395, 2542, 2654, 2767]
_W22 += [2832, 2898]
# 2078 Magh 12 (2022-01-26), release 1049: a table of the winners' weighted votes (Provincial Assembly members 48,
# local-level chairs and mayors 18). Losing candidates' votes are not published.
_R22 = _cells(
    1,
    (1250, 1440),
    _W22,
    [
        ("जयन्ती देवी राई", "Jayanti Devi Rai", "us", 4692, True),
        ("सोनाम गेल्जेन शेर्पा", "Sonam Gyaljen Sherpa", "uml", 4770, True),
        ("गोपाल कुमार बस्नेत", "Gopal Kumar Basnet", "nc", 4488, True),
        ("उर्मिला अर्याल", "Urmila Aryal", "maoist", 6156, True),
        ("मोहमम्द खालिद", "Mohammad Khalid", "jsp", 6678, True),
        ("गोमादेवी तिमिल्सीना", "Goma Devi Timilsina", "us", 4824, True),
        ("कृष्णप्रसाद पौडेल", "Krishna Prasad Poudel", "nc", 4794, True),
        ("कमला पन्त", "Kamala Pant", "nc", 3084, True),
        ("भुवन बहादुर सुनार", "Bhuwan Bahadur Sunar", "maoist", 3048, True),
        ("सुरेश कुमार आलेमगर", "Suresh Kumar Ale Magar", "maoist", 3096, True),
        ("राजेन्द्रलक्ष्मी गैरे", "Rajendra Laxmi Gaire", "us", 4374, True),
        ("तुल प्रसाद विश्वकर्मा", "Tul Prasad Bishwakarma", "rjm", 4356, True),
        ("युवराज शर्मा", "Yubaraj Sharma", "nc", 4344, True),
        ("दुर्गा गुरुङ", "Durga Gurung", "nc", 2970, True),
        ("नर बहादुर विष्ट", "Nar Bahadur Bista", "maoist", 3006, True),
        ("उदय बहादुर वोहरा", "Uday Bahadur Bohara", "us", 3006, True),
        ("मदन कुमारी शाह (गरिमा)", "Madan Kumari Shah (Garima)", "us", 3834, True),
        ("जगत बहादुर पार्की", "Jagat Bahadur Parki", "maoist", 3762, True),
        ("नारायण दत्त मिश्र", "Narayan Datta Mishra", "nc", 3744, True),
    ],
)
_G22 = [(1, "women"), (1, "minority"), (1, "other"), (2, "women"), (2, "other"), (3, "women"), (3, "other")]
_G22 += [(4, "women"), (4, "dalit"), (4, "other"), (5, "women"), (5, "dalit"), (5, "other"), (6, "women")]
_G22 += [(6, "minority"), (6, "other"), (7, "women"), (7, "dalit"), (7, "other")]
NA_2022 = [Contest(province, group, (row,)) for (province, group), row in zip(_G22, _R22, strict=True)]

# 2079 Magh 25 (2023-02-08), release 2797: the Lumbini by-election for one seat, in prose.
NA_2023 = [
    Contest(
        5,
        "seat",
        (
            Row("कुमार दसौदी", "Kumar Dasaudi", "uml", 4026, True, 1, (180, 730, 1550, 840)),
            Row("माधवप्रसाद आचार्य", "Madhav Prasad Acharya", "nc", 3720, False, 1, (180, 820, 1550, 940)),
            Row("राजेन्द्र वीर राय", "Rajendra Bir Rai", "nup", 144, False, 1, (180, 920, 1550, 990)),
        ),
    )
]

PRESIDENT = (
    Row("रामचन्द्र पौडेल", "Ram Chandra Poudel", None, 33802, True, 1, (300, 1110, 2300, 1190)),
    Row("सुवासचन्द्र नेम्वाङ", "Subas Chandra Nembang", None, 15518, False, 1, (300, 1260, 2300, 1340)),
)
VICE_PRESIDENT = (
    Row("रामसहाय प्रसाद यादव", "Ram Sahaya Prasad Yadav", None, 30328, True, 1, (300, 1130, 2300, 1210)),
    Row("अष्टलक्ष्मी शाक्य (वोहरा)", "Ashtalaxmi Shakya (Bohara)", None, 16328, False, 1, (300, 1200, 2300, 1270)),
    Row("ममता झा", "Mamata Jha", None, 2537, False, 1, (300, 1270, 2300, 1340)),
    Row("प्रमिला कुमारी", "Pramila Kumari", None, 48, False, 1, (300, 1270, 2300, 1340)),
)


@dataclass(frozen=True)
class Election:
    day: str
    kind: str
    bs: str
    release: int
    report: int | None
    what: str


ELECTIONS = [
    Election("2022-01-26", "indirect", "२०७८ माघ १२", 1049, None, "national_assembly"),
    Election("2023-02-08", "by_election", "२०७९ माघ २५", 2797, None, "national_assembly"),
    Election("2023-03-09", "indirect", "२०७९ फागुन २५", 2864, None, "president"),
    Election("2023-03-17", "indirect", "२०७९ चैत ३", 2875, None, "vice_president"),
    Election("2024-01-25", "indirect", "२०८० माघ ११", 3122, 3124, "national_assembly"),
    Election("2026-01-24", "indirect", "२०८२ माघ ११", 4303, 4311, "national_assembly"),
]
CONTESTS = {"2022-01-26": NA_2022, "2023-02-08": NA_2023, "2024-01-25": NA_2024, "2026-01-24": NA_2026}

COMMISSION = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fcommission"))
SHAPES = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fgeoboundaries"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NATIONAL_ASSEMBLY = key("body", "national_assembly")
PRESIDENCY = key("office", "president")
VICE_PRESIDENCY = key("office", "vice_president")
GROUP_SYSTEM = key("electoral_system", "national_assembly_group")
COLLEGE_SYSTEM = key("electoral_system", "president")


def province(id: int) -> str:
    return eci.area("np.province", str(id))


def fetch() -> None:
    raw.fetch(SOURCE, LISTING, suffix=".json")
    for link in RELEASES.values():
        raw.fetch(SOURCE, link, suffix="." + link.rsplit(".", 1)[-1].lower())
    raw.fetch(SOURCE, OUTLINES, suffix=".geojson")


def listing() -> dict[int, dict[str, Any]]:
    return {int(r["id"]): r for r in json.loads(raw.cached(SOURCE, LISTING).path.read_text())["data"]}


def check() -> None:
    """Hold the transcription to the sums the releases print, and each release to the listing's link."""
    entries = listing()
    for id, link in RELEASES.items():
        if url(str(entries[id]["value"]).removeprefix(STORAGE)) != link:
            raise ValueError(f"release {id} now links {entries[id]['value']}")
    for contest, total in zip(NA_2024, NA_2024_TOTALS, strict=True):
        if contest.rows[0].votes != total:
            raise ValueError(f"2024 {contest.province} {contest.group}: {contest.rows[0].votes} against {total}")
    for day, contests in CONTESTS.items():
        for c in contests:
            winners = [r for r in c.rows if r.elected]
            counted = [r.votes for r in c.rows if r.votes is not None]
            if len(winners) != 1 or (counted and winners[0].votes != max(counted)):
                raise ValueError(f"{day} {c.province} {c.group}: the winner is not the top count")
    seats = {day: len(contests) for day, contests in CONTESTS.items()}
    if seats != {"2022-01-26": 19, "2023-02-08": 1, "2024-01-25": 19, "2026-01-24": 18}:
        raise ValueError(f"seats {seats}")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS)]


def parse() -> None:
    check()
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Outlines of Nepal's seven provinces from geoBoundaries, for the provincial electoral colleges",
    )
    p.external |= {NEPAL, *(province(i) for i in PROVINCES)}
    shapes = raw.cached(SOURCE, OUTLINES)
    p.add(
        "source",
        id=SHAPES,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Nepal ADM1 (province outlines, simplified, 2020), from the Survey Department of Nepal",
        url="https://www.geoboundaries.org/",
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries (William & Mary geoLab); original data from the Survey Department of Nepal and OCHA",
    )
    p.artifact(shapes, SHAPES)
    outlines = {
        int(str(f["properties"]["shapeISO"]).removeprefix("NP-P")): shapely.geometry.shape(f["geometry"])
        for f in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]
    }
    if set(outlines) != set(PROVINCES):
        raise ValueError(f"outlines for {sorted(outlines)}")
    for id in PROVINCES:
        shape = shapely.make_valid(outlines[id])
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
        p.add(
            "area_geometry",
            area_id=province(id),
            valid_from=date(2015, 9, 20),
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=SHAPES,
        )
    return p


def party(code: str) -> str:
    return key("party", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Nepal's National Assembly, presidency, and vice-presidency, their electoral systems, and the parties in the "
        "indirect elections",
    )
    p.external |= {NEPAL}
    p.add(
        "source",
        id=COMMISSION,
        kind="official_body",
        publisher="निर्वाचन आयोग, नेपाल (Election Commission, Nepal)",
        title="Press releases of the Election Commission, Nepal",
        url="https://election.gov.np/np/press-release",
        license="No license stated; published by the electoral authority for public use",
        attribution="Election Commission, Nepal",
    )
    p.artifact(raw.cached(SOURCE, LISTING), COMMISSION)
    p.add("body", id=NATIONAL_ASSEMBLY, polity_id=NEPAL, kind="upper_house")
    p.name(NATIONAL_ASSEMBLY, "National Assembly", "en", "official")
    p.name(NATIONAL_ASSEMBLY, "राष्ट्रिय सभा", "ne", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=NEPAL, kind="head_of_state")
    p.name(PRESIDENCY, "President of Nepal", "en", "official")
    p.name(PRESIDENCY, "राष्ट्रपति", "ne", "native", False)
    p.add("office", id=VICE_PRESIDENCY, polity_id=NEPAL, kind="other")
    p.name(VICE_PRESIDENCY, "Vice-President of Nepal", "en", "official")
    p.name(VICE_PRESIDENCY, "उपराष्ट्रपति", "ne", "native", False)
    for id, rules, name, method in (
        (
            GROUP_SYSTEM,
            {"formula": "fptp"},
            "National Assembly election by provincial electoral college",
            (
                "Each province elects eight members of the National Assembly (Constitution, Art. 86(2)(a)): three women, "
                "one Dalit, one person with a disability or from a minority, and three others, on separate ballots for "
                "each group. Its electoral college is the Provincial Assembly members and the chairs and vice-chairs of "
                "rural municipalities and the mayors and deputy mayors of municipalities, and each kind of voter's vote "
                "carries a weight set before the election (48 and 18 in 2022 and 2023, 53 and 19 in 2024). A group with "
                "one seat to fill elects the candidate with the most weighted votes. A third of the members are elected "
                "every two years for six-year terms."
            ),
        ),
        (
            COLLEGE_SYSTEM,
            {"formula": "two_round", "majority": 0.5},
            "Election of the President or Vice-President by electoral college",
            (
                "The members of the Federal Parliament and of the Provincial Assemblies elect the President, and in a "
                "separate vote the Vice-President (Constitution, Arts. 62 and 67), with weighted votes (79 for a member "
                "of Parliament and 48 for a member of a Provincial Assembly in 2023). A candidate needs a majority of the "
                "college's total weighted vote; otherwise the top two go to a second vote."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=NEPAL,
            valid_from=date(2015, 9, 20),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.name(id, name, "en", "official")
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code, (ne, en) in PARTIES.items():
        p.add("party", id=party(code), kind="party", polity_id=NEPAL)
        p.name(party(code), ne, "ne", "official")
        p.name(party(code), en, "en", "english", False)
    return p


def _page(p: Package, e: Election, artifact: str, number: int) -> str:
    width, height = PAGES[e.release][number - 1]
    id = key("page", e.release, number)
    stored = raw.cached(SOURCE, RELEASES[e.release])
    p.add(
        "source_page",
        id=id,
        artifact_id=artifact,
        page_number=number,
        image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
        width=width,
        height=height,
    )
    return id


NA_METHOD = (
    "Read from the Election Commission's press release of the count, a scanned page. The figures are weighted votes: "
    "each Provincial Assembly member's vote and each local-level chair's or mayor's vote counts as many times as the "
    "weight set for the election."
)
WINNERS_ONLY = (
    " The release gives only the elected candidate's votes, so the other candidates on the ballot are not stored."
)


def election(e: Election) -> Package:
    p = Package(
        f"{SOURCE}/{e.day}",
        SOURCE,
        {
            "national_assembly": f"The {e.day} Nepal National Assembly "
            + ("by-election" if e.kind == "by_election" else "election")
            + " by province and group, from the Election Commission's press release",
            "president": f"The {e.day} election of the President of Nepal, from the Election Commission's press release",
            "vice_president": f"The {e.day} election of the Vice-President of Nepal, from the Election Commission's "
            "press release",
        }[e.what],
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NEPAL, "np.nation" if e.what in COLLEGE else "np.province"}
    artifact = p.artifact(raw.cached(SOURCE, RELEASES[e.release]), COMMISSION)
    if e.report is not None:
        p.artifact(raw.cached(SOURCE, RELEASES[e.report]), COMMISSION)
    pages = {n: _page(p, e, artifact, n) for n in range(1, len(PAGES[e.release]) + 1)}
    el = key("election", e.day)
    day = date.fromisoformat(e.day)
    p.add(
        "election",
        id=el,
        polity_id=NEPAL,
        kind=e.kind,
        date_from=day,
        date_to=day,
        original_text=e.bs,
        status="held",
    )
    if e.what == "national_assembly":
        title = f"{day.year} Nepal National Assembly " + ("by-election" if e.kind == "by_election" else "election")
        path = f"np/{e.day}-national-assembly"
        p.name(el, title, "en", "official")
        p.name(el, "राष्ट्रिय सभा सदस्य " + ("उपनिर्वाचन" if e.kind == "by_election" else "निर्वाचन"), "ne", "native", False)
        p.add("slug", path=path, entity_id=el, is_current=True)
        if e.day in SUMMARY:
            p.add("note", id=key("note", el), entity_id=el, kind="context", body_md=SUMMARY[e.day], lang="en")
        units: dict[int, str] = {}
        for c in CONTESTS[e.day]:
            if c.province not in units:
                units[c.province] = key("unit", e.day, c.province)
                p.external.add(province(c.province))
                p.add(
                    "reporting_unit",
                    id=units[c.province],
                    polity_id=NEPAL,
                    scheme_code="np.province",
                    code=f"na:{e.day}:{c.province}",
                    area_relation="equals",
                    area_id=province(c.province),
                    kind="aggregate",
                )
            _na_contest(p, e, el, path, c, units[c.province], pages)
    else:
        office, rows = (PRESIDENCY, PRESIDENT) if e.what == "president" else (VICE_PRESIDENCY, VICE_PRESIDENT)
        en = "President" if e.what == "president" else "Vice-President"
        ne = "राष्ट्रपति" if e.what == "president" else "उपराष्ट्रपति"
        path = f"np/{e.day}-{changeset.slug(en)}"
        adjective = "presidential" if e.what == "president" else "vice-presidential"
        p.name(el, f"{day.year} Nepalese {adjective} election", "en", "official")
        p.name(el, f"{ne}को निर्वाचन", "ne", "native", False)
        p.add("slug", path=path, entity_id=el, is_current=True)
        p.external.add(COUNTRY)
        unit = key("unit", e.day, "college")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEPAL,
            scheme_code="np.nation",
            code=f"{e.what}:{e.day}:NPL",
            area_relation="equals",
            area_id=COUNTRY,
            kind="aggregate",
        )
        c = key("contest", e.day, e.what)
        p.add(
            "contest",
            id=c,
            election_id=el,
            kind="office",
            office_id=office,
            district_area_id=COUNTRY,
            magnitude=1,
            round_number=1,
            electoral_system_id=COLLEGE_SYSTEM,
            default_result_kind="counted",
        )
        p.name(c, f"{day.year} election of the {en} of Nepal", "en", "official")
        p.name(c, f"{ne}को निर्वाचन", "ne", "native", False)
        p.add("slug", path=f"{path}/vote", entity_id=c, is_current=True)
        rs = key("result_set", e.day, e.what)
        p.add(
            "result_set",
            id=rs,
            election_id=el,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=COMMISSION,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=COLLEGE[e.what], lang="en")
        _options(p, e, c, rs, unit, rows, pages, f"{e.what}")
    return p


SUMMARY = {
    "2024-01-25": (
        "The Commission's report to the President (release of 2080 Magh 14) gives 2,046 electors in the seven "
        "provinces, of whom 1,999 voted, and 51 candidates of 8 parties for the 19 seats falling vacant on 2080 "
        "Falgun 20, of whom 19 from 5 parties were elected."
    ),
    "2026-01-24": (
        "The Commission's report to the President (release of 2082 Magh 14) gives 529 Provincial Assembly electors, "
        "of whom 498 voted, and 1,482 local-level electors, of whom 1,426 voted: 1,924 of 2,011 electors in all. Of "
        "the 1,924 votes, 1,831 were valid and 93 invalid. These are counts of electors, not weighted votes, for the "
        "whole election rather than any one group's ballot, so they are kept here rather than as figures for a "
        "contest."
    ),
    "2023-02-08": (
        "The release gives 305 electors in Lumbini Province, 218 local-level chairs and mayors and their deputies "
        "and 87 Provincial Assembly members, of whom 217 and 87 voted, with weights of 18 and 48."
    ),
}
COLLEGE = {
    "president": (
        "Read from the Election Commission's press release of 2079 Falgun 25, a scanned page. The figures are "
        "weighted votes: 79 for each member of the Federal Parliament and 48 for each member of a Provincial "
        "Assembly. The release gives 882 electors (332 members of Parliament and 550 of the Provincial Assemblies), "
        "of whom 313 and 518 voted; it gives no count of invalid votes."
    ),
    "vice_president": (
        "Read from the Election Commission's press release of 2079 Chaitra 3, a scanned page. The figures are "
        "weighted votes: 79 for each member of the Federal Parliament and 48 for each member of a Provincial "
        "Assembly. The release says 311 members of Parliament and 518 of the Provincial Assemblies voted; it gives "
        "no count of invalid votes."
    ),
}


def _na_contest(p: Package, e: Election, el: str, path: str, c: Contest, unit: str, pages: dict[int, str]) -> None:
    tag = f"{c.province}:{c.group}"
    name = PROVINCES[c.province]
    region = f"{name.title()} Province" if name != "sudurpashchim" else "Sudurpashchim Province"
    cid = key("contest", e.day, tag)
    p.add(
        "contest",
        id=cid,
        election_id=el,
        kind="body_seats",
        body_id=NATIONAL_ASSEMBLY,
        district_area_id=province(c.province),
        magnitude=1,
        round_number=1,
        electoral_system_id=GROUP_SYSTEM,
        default_result_kind="counted",
    )
    if c.group == "seat":
        p.name(cid, f"{date.fromisoformat(e.day).year} National Assembly by-election, {region}", "en", "official")
        p.add("slug", path=f"{path}/{name}", entity_id=cid, is_current=True)
    else:
        ne, en = GROUPS[c.group]
        p.name(cid, f"{date.fromisoformat(e.day).year} National Assembly election, {region}, {en}", "en", "official")
        p.name(cid, f"राष्ट्रिय सभा सदस्य निर्वाचन, {ne} समूह", "ne", "native", False)
        p.add("slug", path=f"{path}/{name}-{c.group}", entity_id=cid, is_current=True)
    tacit = all(r.votes is None for r in c.rows)
    rs = key("result_set", e.day, tag)
    p.add(
        "result_set",
        id=rs,
        election_id=el,
        contest_id=cid,
        stage="official",
        kind="tacit" if tacit else "counted",
        source_id=COMMISSION,
        status="active",
        is_default=True,
        completeness=0.0 if tacit else 1.0,
    )
    method = NA_METHOD
    if tacit:
        method = "The Election Commission's press release marks the only candidate as elected unopposed."
    elif e.day in ("2022-01-26", "2024-01-25"):
        method += WINNERS_ONLY
    split = c.rows[0].split
    if split is not None:
        a, wa, b, wb = split
        method += (
            f" The elected candidate's {a * wa + b * wb:,} weighted votes are {a} votes of Provincial Assembly "
            f"members ({a * wa:,} weighted) and {b} votes of local-level chairs and mayors ({b * wb:,} weighted)."
        )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=method, lang="en")
    if e.day == "2022-01-26" and c.province == 1 and c.group == "other":
        p.add(
            "note",
            id=key("caveat", rs),
            entity_id=rs,
            kind="caveat",
            body_md=(
                "The release prints 4,488 weighted votes and remarks that the candidate was given one more vote by "
                "drawing lots, for 4,489 in all. The printed 4,488 is stored."
            ),
            lang="en",
        )
    _options(p, e, cid, rs, unit, c.rows, pages, tag)


def _options(p: Package, e: Election, contest: str, rs: str, unit: str, rows, pages: dict[int, str], tag: str) -> None:
    for i, r in enumerate(rows):
        option = key("option", e.day, tag, r.ne)
        person = key("person", e.day, tag, r.ne)
        member = None if r.party is None else party(r.party)
        p.add("person", id=person)
        p.name(person, r.ne, "ne", "official")
        p.name(person, r.en, "en", "transliteration", False)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=member,
            ballot_position=i + 1,
            ballot_label=r.ne,
        )
        candidacy = key("candidacy", e.day, tag, r.ne)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=r.ne,
            member_of_party_id=member,
            is_independent=False if member else None,
        )
        if r.elected:
            p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
        if r.votes is None:
            continue
        fact = {"result_set_id": rs, "unit_id": unit, "option_id": option, "channel": 0, "measure": 1, "count_round": 0}
        p.add("result_value", **fact, flags=0, value=r.votes)
        if r.box is None:
            raise ValueError(f"{e.day} {tag} {r.en}: no box")
        x0, y0, x1, y1 = r.box
        p.add("value_locator", **fact, page_id=pages[r.page], source_id=COMMISSION, x0=x0, y0=y0, x1=x1, y1=y1)
