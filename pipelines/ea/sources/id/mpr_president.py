import re
import struct
import subprocess
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple, TypedDict
from uuid import UUID

from ea import package, raw
from ea.package import Draft
from ea.store import DATA, connect

SOURCE = "id.mpr_president"
KEY = f"{SOURCE}/all"
VERSION = "1"
URL = (
    "https://www.mpr.go.id/dokumen/risalah/file/24%20Risalah%20Perubahan%20Undang-Undang%20Dasar%20Negara%20Republik"
    "%20Indonesia%20Tahun%201945%20Tahun%20Sidang%201999.pdf"
)
TITLE = "Risalah Perubahan Undang-Undang Dasar Negara Republik Indonesia Tahun 1945, Tahun Sidang 1999"
PUBLISHER = "Sekretariat Jenderal Majelis Permusyawaratan Rakyat Republik Indonesia"
SUMMARY = (
    "Indonesia's elections of the President by the People's Consultative Assembly (MPR) and its provisional "
    "predecessor from 1968 to 1999 and of the Vice President from 1973 to 2001"
)
MEASURES = {"votes": 1, "ballots": 3, "valid": 4, "invalid": 5, "blank": 6, "present": 20}
WAYBACK = "https://web.archive.org/web/{}id_/{}"
BOOK_2001 = (
    "https://www.mpr.go.id/dokumen/risalah/file/34%202.%20Risalah%20Perubahan%20Undang-Undang%20Dasar%20Negara"
    "%20Republik%20Indonesia%20Tahun%201945%20Tahun%20Sidang%202001%20-%20Buku%20Dua.pdf"
)
BOOK_2001_TITLE = (
    "Risalah Perubahan Undang-Undang Dasar Negara Republik Indonesia Tahun 1945, Tahun Sidang 2001, Buku Dua"
)
BOOK_2001_CAPTURE = "20251124113248"
ARTICLE = "Pemilihan Wakil Presiden Indonesia 2001"
ARTICLE_URL = "https://id.wikipedia.org/wiki/Pemilihan_Wakil_Presiden_Indonesia_2001"
ARTICLE_RAW = "https://id.wikipedia.org/w/index.php?title=Pemilihan_Wakil_Presiden_Indonesia_2001&action=raw"
# The reports filed on the days of the vote, each read from its Wayback capture, with the phrases the build checks
# each counted figure against.
REPORTS = [
    (
        "Liputan6.com",
        "Tiga Kandidat Wapres Bersaing di Voting Kedua",
        "https://www.liputan6.com/news/read/17148/tiga-kandidat-wapres-bersaing-di-voting-kedua",
        "20220626135357",
        [
            "Hamzah yang dicalonkan Fraksi Persatuan Pembangunan memperoleh 238 suara",
            "Akbar Tandjung dari Fraksi Partai Golkar sebanyak 177 suara",
            "mengantungi 122 suara",
            "memperoleh 41 suara",
            "meraih 31 suara",
            "Dari 613 anggota MPR yang memberikan suara, empat dinyatakan abstain",
        ],
    ),
    (
        "Tempo",
        "Pemilihan Wapres Tertunda 40 Menit",
        "https://nasional.tempo.co/read/35358/pemilihan-wapres-tertunda-40-menit",
        "20220705102101",
        [
            (
                "Agum Gumelar 41 suara, Susilo Bambang Yudhoyono 122 suara, Akbar Tandjung 177 suara, Hamzah Haz 238 "
                "suara dan Siswono Yudhohusodo 31 suara"
            ),
            "Sedangkan suara yang abstain sebanyak 4 suara. Total suara yang masuk sebanyak 613",
            "Akbar di tempat kedua dengan 203 suara dan Susilo Bambang Yudhoyono di tempat ketiga dengan 147 suara",
            "Jumlah suara yang masuk adalah 604 ditambah tiga suara abstain dan dua suara yang dianggap tidak sah",
            "Kamis (26/7)",
            "Rabu (25/7) malam",
        ],
    ),
    (
        "Liputan6.com",
        "Hamzah Haz Terpilih sebagai Wapres ke Sembilan",
        "https://www.liputan6.com/news/read/17180/hamzah-haz-terpilih-sebagai-wapres-ke-sembilan",
        "20220702165951",
        [
            "setelah meraih 340 suara pada pemilihan babak ketiga dalam Sidang Paripurna MPR, Kamis (26/7) pagi",
            "Akbar Tandjung memperoleh 237 suara",
            "tercatat 29 suara abstain",
        ],
    ),
]
CANDIDATES_2001 = {
    "Q76344": "Hamzah Haz",
    "Q2581168": "Akbar Tanjung",
    "Q57405": "Susilo Bambang Yudhoyono",
    "Q4694586": "Agum Gumelar",
    "Q13097173": "Siswono Yudo Husodo",
}
# Each ballot of the 2001 vote: its day, the votes for each candidate with the outcome, and the totals the sources
# agree on.
BALLOTS_2001: list[tuple[date, dict[str, tuple[int, str]], dict[str, int]]] = [
    (
        date(2001, 7, 25),
        {
            "Q76344": (238, "advanced"),
            "Q2581168": (177, "advanced"),
            "Q57405": (122, "advanced"),
            "Q4694586": (41, "eliminated"),
            "Q13097173": (31, "eliminated"),
        },
        {"ballots": 613, "valid": 609, "blank": 4},
    ),
    (
        date(2001, 7, 25),
        {"Q76344": (254, "advanced"), "Q2581168": (203, "advanced"), "Q57405": (147, "eliminated")},
        {"valid": 604, "invalid": 2, "blank": 3},
    ),
    (date(2001, 7, 26), {"Q76344": (340, "elected"), "Q2581168": (237, "lost")}, {"valid": 577, "blank": 29}),
]
FIRST_2001 = date(2001, 7, 25)
PATH_2001 = "id/2001-07-25-vice-presidential"
TERM_END = date(2004, 10, 20)
ORDINALS = {1: "pertama", 2: "kedua", 3: "ketiga"}
METHODOLOGY_2001 = (
    "Under Article 6(2) of the 1945 Constitution and MPR decree VI/MPR/1999, the People's Consultative Assembly "
    "(MPR) elected a Vice President by secret ballot when the office fell vacant. A candidate needed more than half "
    "of the votes; failing that, the members voted again between the three leading candidates and then between the "
    "two leading candidates (Article 19 of the decree). Each ballot is its own contest, chained to the one before."
)
NOTE_2001 = (
    "The minutes of the MPR's 2001 special session are not among the Risalah the MPR Secretariat General publishes: "
    "its 2001 session-year volumes skip from 17 July to 29 August 2001. Counts are transcribed from Indonesian "
    "Wikipedia's article on the election (revision pinned) and checked against the reports Liputan6.com and Tempo "
    "filed on 25 to 27 July 2001. For the first ballot, all three give the five counts, 613 ballots, and 4 "
    "abstentions. For the second, Tempo prints Hamzah Haz's count as 304, which its own total of 604 votes for the "
    "three candidates rules out; the article's 254 fits that total. For the third, Liputan6.com gives 5 invalid "
    "ballots of 611 and the article 4 of 610, so invalid ballots and the total are left out. Abstentions are blank "
    "ballots."
)
SUCCESSION_NOTE = (
    "Megawati Soekarnoputri, Vice President, became President by MPR decree III/MPR/2001 on 23 July 2001, when the "
    "MPR's special session removed Abdurrahman Wahid. There was no vote. Her term, and that of Hamzah Haz, whom the "
    "MPR elected Vice President on 26 July 2001, ran to the end of the 1999 to 2004 term on 20 October 2004."
)
METHODOLOGY = (
    "Under Article 6(2) of the 1945 Constitution as it stood in 1999, the People's Consultative Assembly (MPR) "
    "elected the President and the Vice President by the most votes. MPR decree VI/MPR/1999 set the procedure: "
    "fractions nominated candidates in writing, the MPR leadership checked their papers, and with more than one "
    "candidate the members voted by secret ballot, in stages while more than two remained."
)
NOTE = (
    "Counts as the chair announced them, from the minutes of the MPR's 13th plenary (20 October 1999) for the "
    "President and its 15th plenary (21 October 1999) for the Vice President, reprinted in the MPR Secretariat "
    "General's Risalah for the 1999 session year. Members present are the attendance the minutes record for each "
    "plenary. The minutes list abstentions and no invalid ballots; the President's table gives no total, and the "
    "Vice President's gives 685. Candidates who withdrew after their papers were accepted received no votes."
)


JDIH = "https://jdih.mpr.go.id/common/dokumen/{}.pdf"
MPR = "Majelis Permusyawaratan Rakyat Republik Indonesia"
MPRS = "Majelis Permusyawaratan Rakyat Sementara Republik Indonesia"
DECREE_LICENSE = "Public record: decree of the MPR, no license stated"
DECREE_LICENSE_URL = "https://jdih.mpr.go.id/"


class Decree(NamedTuple):
    """One decree of the MPR(S) that appointed a President or a Vice President: its number, the file the MPR's legal
    documentation network serves and the Wayback capture the cache holds, the office, the day it was adopted, the
    person and the name it prints, and the PDF pages that print that name and the date."""

    number: str
    file: str
    capture: str
    office: str
    day: date
    person: str
    printed: str
    pages: tuple[int, int]


# Every appointment from 1968 to 1998. The MPR's site refuses scripted requests, so each decree is read from the
# capture the Wayback Machine made of the file the MPR's legal documentation network links.
DECREES = [
    Decree("XLIV/MPRS/1968", "1968tapmprs044", "20220610170751", "presiden", date(1968, 3, 27), "soeharto",
           "Jenderal Soeharto", (2, 3)),
    Decree("IX/MPR/1973", "1973tapmpr009", "20220703031705", "presiden", date(1973, 3, 23), "soeharto",
           "Soeharto Jenderal TNI", (2, 3)),
    Decree("XI/MPR/1973", "1973tapmpr011", "20220703031817", "wakil-presiden", date(1973, 3, 23),
           "hamengkubuwono-ix", "Sri Sultan Hamengkubuwono IX", (2, 3)),
    Decree("X/MPR/1978", "1978tapmpr010", "20220703031459", "presiden", date(1978, 3, 22), "soeharto",
           "SOEHARTO", (2, 3)),
    Decree("XI/MPR/1978", "1978tapmpr011", "20220703031549", "wakil-presiden", date(1978, 3, 23), "adam-malik",
           "ADAM MALIK", (2, 3)),
    Decree("VI/MPR/1983", "1983tapmpr006", "20220703031226", "presiden", date(1983, 3, 10), "soeharto",
           "Jenderal TNI (Purnawirawan) SOEHARTO", (2, 3)),
    Decree("VIII/MPR/1983", "1983tapmpr008", "20220703031200", "wakil-presiden", date(1983, 3, 11),
           "umar-wirahadikusumah", "Jenderal TNI (Purnawirawan) UMAR WIRAHADIKUSUMAH", (2, 3)),
    Decree("V/MPR/1988", "1988tapmpr005", "20220703031054", "presiden", date(1988, 3, 10), "soeharto",
           "Soeharto", (2, 3)),
    Decree("VII/MPR/1988", "1988tapmpr007", "20220703031031", "wakil-presiden", date(1988, 3, 11), "sudharmono",
           "Sudharmono, S.H Letnan Jenderal TNI Purnawirawan", (2, 3)),
    Decree("IV/MPR/1993", "1993tapmpr004", "20220703030959", "presiden", date(1993, 3, 10), "soeharto",
           "Soeharto", (2, 3)),
    Decree("V/MPR/1993", "1993tapmpr005", "20220703030934", "wakil-presiden", date(1993, 3, 11), "try-sutrisno",
           "Try Sutrisno", (2, 3)),
    Decree("IV/MPR/1998", "1998tapmpr004", "20220703030402", "presiden", date(1998, 3, 10), "soeharto",
           "Soeharto", (2, 3)),
    Decree("VI/MPR/1998", "1998tapmpr006", "20220703030801", "wakil-presiden", date(1998, 3, 11), "habibie",
           "Prof. Dr.-Ing. Bacharuddin Jusuf Habibie", (2, 3)),
]  # fmt: skip
APPOINTEES = {
    "soeharto": "Soeharto",
    "hamengkubuwono-ix": "Hamengkubuwono IX",
    "adam-malik": "Adam Malik",
    "umar-wirahadikusumah": "Umar Wirahadikusumah",
    "sudharmono": "Sudharmono",
    "try-sutrisno": "Try Sutrisno",
    "habibie": "Bacharuddin Jusuf Habibie",
}
SUBJECTS = {
    "presiden": "Pengangkatan Presiden Republik Indonesia",
    "wakil-presiden": "Pengangkatan Wakil Presiden Republik Indonesia",
}
SUBJECT_1968 = "Pengangkatan Pengemban Ketetapan MPRS No. IX/MPRS/1966 sebagai Presiden Republik Indonesia"
PROCEDURE = Decree("II/MPR/1973", "1973tapmpr002", "20220703031836", "", date(1973, 3, 19), "", "", (5, 7))
PROCEDURE_SUBJECT = "Tata-Cara Pemilihan Presiden dan Wakil Presiden Republik Indonesia"
ACCOUNT = Decree("III/MPR/1999", "1999tapmpr003", "20220703030546", "", date(1999, 10, 19), "habibie", "", (1, 1))
ACCOUNT_SUBJECT = "Pertanggungjawaban Presiden Republik Indonesia Prof. Dr. Ing. Bacharuddin Jusuf Habibie"
HABIBIE_SWORN = date(1998, 5, 21)
OFFICE_NAMES = {
    "presiden": ("presidential", "president", "Presiden", "President"),
    "wakil-presiden": ("vice-presidential", "vice-president", "Wakil Presiden", "Vice President"),
}
METHODOLOGY_1973 = (
    "From 1973 to 1998 the People's Consultative Assembly (MPR) elected the President and then the Vice President "
    "under its decree II/MPR/1973. Fractions nominated candidates in writing. With more than one candidate the "
    "members voted by secret ballot, and a candidate needed more than half of the members present, with a further "
    "ballot between the two leading candidates. When every fraction nominated the same single candidate, the "
    "plenary ratified that candidate without a ballot (Articles 13(2) and 25(2)). Each election from 1973 to 1998 "
    "had one candidate for each office, so each is stored as that candidate elected, with no counts."
)
NOTE_1968 = (
    "The Provisional People's Consultative Assembly (MPRS) appointed General Soeharto, who held the mandate of its "
    "decree IX/MPRS/1966 and had served as Acting President, as President of the Republic until a President was "
    "elected by the MPR formed from a general election (decree XLIV/MPRS/1968, Article 1). The decree records the "
    "MPRS's fifth general session and no vote, so the contest holds him elected, with no counts."
)
HABIBIE_NOTE = (
    "Bacharuddin Jusuf Habibie, Vice President, became President when he took the oath of office on 21 May 1998, "
    "ending Soeharto's term; there was no vote. MPR decree III/MPR/1999 records that he held the office from that "
    "oath. He served until the MPR elected Abdurrahman Wahid on 20 October 1999."
)


def decree_note(decree: Decree) -> str:
    office = OFFICE_NAMES[decree.office][3]
    return (
        f"MPR decree {decree.number}, adopted on {decree.day.day} {decree.day:%B %Y}, appoints {decree.printed} "
        f"{office} of the Republic of Indonesia (Article 1). It cites the plenary's decision and records no vote."
    )


def decree_title(decree: Decree, subject: str) -> str:
    assembly = MPRS if "MPRS" in decree.number else MPR
    return f"Ketetapan {assembly} Nomor {decree.number} tentang {subject}"


class Vote(TypedDict):
    office: str
    day: date
    path: str
    slug: str
    english: str
    native: str
    attendance: tuple[int, int]
    page: int
    rows: list[tuple[str, str, str, str | None, int | None, str]]
    totals: list[tuple[str, str, int]]
    tenure: tuple[date, date, str]


VOTES: list[Vote] = [
    {
        "office": "presiden",
        "day": date(1999, 10, 20),
        "path": "id/1999-10-20-presidential",
        "slug": "president",
        "english": "1999 Indonesian presidential election",
        "native": "Pemilihan Presiden Republik Indonesia 1999",
        "attendance": (845, 694),
        "page": 850,
        "rows": [
            ("Q76167", "Abdurrahman Wahid", "KH. Abdurahman Wahid", "Abdurahman", 373, "elected"),
            ("Q76179", "Megawati Soekarnoputri", "Megawati Soekarnoputri", "Megawati", 313, "lost"),
            ("Q8061915", "Yusril Ihza Mahendra", "Prof. Dr. Yusril Ihza Mahendra", None, None, "withdrew"),
        ],
        "totals": [("blank", "Abstain", 5)],
        "tenure": (date(1999, 10, 20), date(2001, 7, 23), "removed"),
    },
    {
        "office": "wakil-presiden",
        "day": date(1999, 10, 21),
        "path": "id/1999-10-21-vice-presidential",
        "slug": "vice-president",
        "english": "1999 Indonesian vice presidential election",
        "native": "Pemilihan Wakil Presiden Republik Indonesia 1999",
        "attendance": (857, 685),
        "page": 859,
        "rows": [
            ("Q76179", "Megawati Soekarnoputri", "Megawati Soekarno Putri", "Megawati", 396, "elected"),
            ("Q76344", "Hamzah Haz", "Dr. H. Hamzah Haz", "Haz", 284, "lost"),
            ("Q2581168", "Akbar Tanjung", "Ir. Akbar Tanjung", None, None, "withdrew"),
            ("Q731740", "Wiranto", "H. Wiranto, S.I.P., S.H.", None, None, "withdrew"),
        ],
        "totals": [("blank", "Abstain", 5), ("ballots", "Jumlah", 685)],
        "tenure": (date(1999, 10, 21), date(2001, 7, 23), "elevated"),
    },
]
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
SIZE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)"')


def fetch() -> None:
    raw.fetch(SOURCE, ARTICLE_RAW, suffix=".wiki")
    for _, _, url, capture, _ in REPORTS:
        raw.fetch(SOURCE, WAYBACK.format(capture, url), suffix=".html")
    # mpr.go.id refuses scripted requests from some hosts; the Wayback capture holds the same volume.
    raw.fetch(SOURCE, WAYBACK.format(BOOK_2001_CAPTURE, BOOK_2001), suffix=".pdf")
    raw.fetch(SOURCE, URL, suffix=".pdf")
    for decree in [*DECREES, PROCEDURE, ACCOUNT]:
        raw.fetch(SOURCE, WAYBACK.format(decree.capture, JDIH.format(decree.file)), suffix=".pdf")


def article_counts(text: str) -> dict[str, list[int]]:
    """The counts in the article's results table: for each candidate's row, the vote counts of the ballots it
    stood in, and for the abstention, valid, invalid, and ballots rows, one count per ballot."""
    table = text[text.index("== Hasil Pemilihan ==") :]
    rows: dict[str, list[int]] = {}
    for block in table.split("\n|-")[1:]:
        cells = [cell.strip() for cell in block.split("\n|") if cell.strip()]
        if not cells:
            continue
        label = re.sub(r"^colspan=\"?\d\"?\s*\|", "", cells[0]).strip()
        label = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", label)
        numbers = [
            int(re.sub(r"^colspan=\"?\d\"?\s*\|", "", cell).strip())
            for cell in cells[1:]
            if re.fullmatch(r"(colspan=\"?\d\"?\s*\|)?\s*\d+", cell)
        ]
        if numbers:
            rows[label] = numbers
    return rows


def check_2001() -> None:
    """Raise unless the article's table and the reports print every count the 2001 ballots store."""
    rows = article_counts(raw.cached(SOURCE, ARTICLE_RAW).path.read_text())
    for item, name in CANDIDATES_2001.items():
        expected = [votes[item][0] for _, votes, _ in BALLOTS_2001 if item in votes]
        if rows.get(name) != expected:
            raise ValueError(f"{ARTICLE}: {name} has {rows.get(name)}, not {expected}")
    for label, measure in (("Abstain", "blank"), ("Suara sah", "valid")):
        expected = [totals[measure] for _, _, totals in BALLOTS_2001]
        if rows.get(label) != expected:
            raise ValueError(f"{ARTICLE}: {label} is {rows.get(label)}, not {expected}")
    if rows.get("Suara tidak sah", [None, None])[1] != BALLOTS_2001[1][2]["invalid"]:
        raise ValueError(f"{ARTICLE}: the second ballot's invalid count differs")
    if rows.get("Suara terdaftar", [None])[0] != BALLOTS_2001[0][2]["ballots"]:
        raise ValueError(f"{ARTICLE}: the first ballot's total differs")
    for publisher, title, url, capture, phrases in REPORTS:
        page = raw.cached(SOURCE, WAYBACK.format(capture, url)).path.read_text(errors="replace")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
        missing = [phrase for phrase in phrases if phrase not in text]
        if missing:
            raise ValueError(f"{publisher}, {title}: missing {missing}")


def parse() -> None:
    print(build())


def emit() -> None:
    package.run("load", [KEY])


def locate(path: Path, page: int, anchor: str, value: int) -> tuple[float, float, float, float]:
    xml = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    match = SIZE.search(xml)
    if match is None:
        raise ValueError(f"page {page}: no size")
    width, height = (float(group) for group in match.groups())
    words = [(float(a), float(b), float(c), float(d), text) for a, b, c, d, text in WORD.findall(xml)]
    found = [
        (x0, y0, x1, y1)
        for x0, y0, x1, y1, text in words
        if text == str(value)
        and any(
            other == anchor and right < x0 and abs((top + bottom) / 2 - (y0 + y1) / 2) < 5
            for _, top, right, bottom, other in words
        )
    ]
    if len(found) != 1:
        raise ValueError(f"page {page}: {len(found)} matches for {anchor} {value}")
    x0, y0, x1, y1 = found[0]
    return x0 / width, y0 / height, x1 / width, y1 / height


class Book:
    """One Risalah volume: its cached PDF, or, where the cache lacks it, the archive's record of the file, its page
    renders, and the boxes `locate` found in it. The 1999 volume (169 MB) is cached only where it was first fetched,
    and mpr.go.id refuses scripted requests from elsewhere, so a rebuild there reads what the first build recorded."""

    def __init__(self, cs: Draft, url: str, source: UUID) -> None:
        self.cs = cs
        if url in raw.keys(SOURCE):
            self.cached: raw.Artifact | None = raw.cached(SOURCE, url)
            artifact = self.cached
        else:
            self.cached = None
            row = cs.conn.execute(
                """select a.sha256, a.storage_key, a.retrieved_at, a.media_type, a.bytes
                   from source_artifact a join changeset c on c.id = a.changeset_id
                   where c.pipeline = %s and a.url = %s""",
                [SOURCE, url],
            ).fetchone()
            if row is None:
                raise LookupError(f"{url} is neither in the {SOURCE} raw cache nor recorded; run `ea fetch {SOURCE}`")
            sha256, storage_key, retrieved_at, media_type, size = row
            if not isinstance(retrieved_at, datetime):
                raise TypeError(f"{url}: retrieved_at is {retrieved_at!r}")
            artifact = raw.Artifact(
                source=SOURCE,
                url=url,
                sha256=str(sha256),
                path=DATA / str(storage_key),
                retrieved_at=retrieved_at,
                media_type=None if media_type is None else str(media_type),
                bytes=int(size),
            )
        self.sha256 = artifact.sha256
        self.id = cs.artifact(artifact, source)
        self.pages: dict[int, UUID] = {}
        self.recorded: dict[int, tuple[int, int]] = {}
        self.boxes: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
        if self.cached is None:
            for number, width, height in cs.conn.execute(
                "select page_number, width, height from source_page where artifact_id = %s", [self.id]
            ).fetchall():
                self.recorded[int(number)] = (int(width), int(height))
            for number, value, x0, y0, x1, y1 in cs.conn.execute(
                """select p.page_number, v.value, l.x0, l.y0, l.x1, l.y1 from value_locator l
                   join source_page p on p.id = l.page_id
                   join result_set r on r.k = l.result_set_k
                   join result_value v on v.result_set_k = l.result_set_k and v.unit_k = l.unit_k
                    and v.option_k = l.option_k and v.channel = l.channel and v.measure = l.measure
                    and v.count_round = l.count_round
                   where p.artifact_id = %s""",
                [self.id],
            ).fetchall():
                self.boxes.setdefault((int(number), int(value)), []).append(
                    (float(x0), float(y0), float(x1), float(y1))
                )

    def page(self, number: int) -> UUID:
        if number not in self.pages:
            image = DATA / "pages" / SOURCE / f"{self.sha256}-{number}.png"
            if image.exists():
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
            elif self.cached is not None:
                image.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(self.cached.path), str(image.with_suffix(""))],
                    check=True,
                )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
            else:
                width, height = self.recorded[number]
            self.pages[number] = self.cs.page(self.id, number)
            self.cs.add(
                "source_page",
                id=self.pages[number],
                artifact_id=self.id,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        return self.pages[number]

    def locate(self, number: int, anchor: str, value: int) -> tuple[float, float, float, float]:
        if self.cached is not None:
            return locate(self.cached.path, number, anchor, value)
        found = self.boxes.get((number, value), [])
        if len(found) != 1:
            raise ValueError(f"page {number}: {len(found)} recorded boxes for {anchor} {value}")
        return found[0]


def build() -> Path:
    """Write the package id.mpr_president/all. It reads the database, through Draft, so the rows it rebuilds keep
    the ids the changeset path gave them."""
    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="ID")

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        nation = cs.area("id.nation", "ID")
        assembly = cs.key("id.body", "mpr")
        offices = {key: cs.key("id.office", key) for key in ("presiden", "wakil-presiden")}
        if assembly in cs.minted or any(office in cs.minted for office in offices.values()):
            raise ValueError("publish id.kpu before id.mpr_president")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record: official minutes of the MPR, no license stated",
            license_url="https://www.mpr.go.id/risalah",
        )
        book = Book(cs, URL, source)

        cs.add("scheme", code="id.assembly", polity_id=indonesia, kind="electoral_district", rank=0, noun="assembly")
        unit = cs.unit("id.assembly", "mpr")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=indonesia,
            scheme_code="id.assembly",
            code="mpr",
            area_relation="unlocated",
            kind="aggregate",
        )
        name(unit, "Majelis Permusyawaratan Rakyat dalam Rapat Paripurna")
        name(unit, "People's Consultative Assembly in plenary", "en", "english")
        system = cs.key("id.electoral_system", "mpr:presiden")
        cs.add("electoral_system", id=system, polity_id=indonesia, rules={"formula": "fptp"}, rules_version=1)
        name(system, "Election of the President and Vice President by the MPR", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)

        persons: dict[str, UUID] = {}
        for vote in VOTES:
            for item, common, *_ in vote["rows"]:
                if item not in persons:
                    persons[item] = cs.key("wikidata", item)
                    if persons[item] in cs.minted:
                        cs.add("person", id=persons[item])
                        name(persons[item], common)

        for vote in VOTES:
            day = vote["day"]
            election = cs.key("id.election", str(day))
            cs.add(
                "election",
                id=election,
                polity_id=indonesia,
                kind="indirect",
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            name(election, vote["english"], "en", "english")
            name(election, vote["native"])
            cs.add("slug", path=vote["path"], entity_id=election)
            contest_key = f"{day}:{vote['office']}"
            contest = cs.key("id.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=offices[vote["office"]],
                body_id=assembly,
                district_area_id=nation,
                term="full",
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, vote["english"], "en", "english")
            name(contest, vote["native"])
            cs.add("slug", path=f"{vote['path']}/{vote['slug']}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)

            def value(option: UUID | None, measure: str, number: int, at: int, anchor: str) -> None:
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": MEASURES[measure],
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=number)
                x0, y0, x1, y1 = book.locate(at, anchor, number)
                cs.add("value_locator", **fact, page_id=book.page(at), source_id=None, x0=x0, y0=y0, x1=x1, y1=y1)

            value(None, "present", vote["attendance"][1], vote["attendance"][0], "Hadir")
            for measure, anchor, number in vote["totals"]:
                value(None, measure, number, vote["page"], anchor)
            for item, _, ballot, anchor, votes, outcome in vote["rows"]:
                option = cs.key("id.option", f"{contest_key}:{item}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_position=None,
                    ballot_label=ballot,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("id.candidacy", f"{contest_key}:{item}"),
                    person_id=persons[item],
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=False,
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome, seats=None)
                if votes is not None and anchor is not None:
                    value(option, "votes", votes, vote["page"], anchor)
                if outcome == "elected":
                    start, end, via = vote["tenure"]
                    cs.add(
                        "tenure",
                        id=cs.key(f"{SOURCE}.tenure", contest_key),
                        person_id=persons[item],
                        office_id=offices[vote["office"]],
                        from_date=start,
                        to_date=end,
                        acquired_via="elected",
                        ended_via=via,
                        contest_id=contest,
                    )

        check_2001()
        article = cs.source("Wikipedia", ARTICLE)
        cs.add(
            "source",
            id=article,
            kind="community",
            publisher="Wikipedia",
            title=ARTICLE,
            url=ARTICLE_URL,
            license="Creative Commons Attribution-ShareAlike 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        )
        cs.artifact(raw.cached(SOURCE, ARTICLE_RAW), article)
        reports: list[UUID] = []
        for publisher, title, url, capture, _ in REPORTS:
            report = cs.source(publisher, title)
            cs.add("source", id=report, kind="news_agency", publisher=publisher, title=title, url=url)
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(capture, url)), report)
            reports.append(report)
        book_2001 = cs.source(PUBLISHER, BOOK_2001_TITLE)
        cs.add(
            "source",
            id=book_2001,
            kind="official_body",
            publisher=PUBLISHER,
            title=BOOK_2001_TITLE,
            url=BOOK_2001,
            license="Public record: official minutes of the MPR, no license stated",
            license_url="https://www.mpr.go.id/risalah",
        )
        cs.artifact(raw.cached(SOURCE, WAYBACK.format(BOOK_2001_CAPTURE, BOOK_2001)), book_2001)

        repeated = cs.key("id.electoral_system", "mpr:wakil-presiden-ulang")
        cs.add(
            "electoral_system",
            id=repeated,
            polity_id=indonesia,
            rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
            rules_version=1,
        )
        name(repeated, "Election of the Vice President by the MPR in repeated ballots", "en", "english")
        cs.add("note", entity_id=repeated, kind="methodology", body_md=METHODOLOGY_2001)
        for item, common in CANDIDATES_2001.items():
            if item not in persons:
                persons[item] = cs.key("wikidata", item)
                if persons[item] in cs.minted:
                    cs.add("person", id=persons[item])
                    name(persons[item], common)

        parent = cs.key("id.election", f"{FIRST_2001}:wakil-presiden")
        cs.add(
            "election",
            id=parent,
            polity_id=indonesia,
            kind="indirect",
            date_from=FIRST_2001,
            date_to=BALLOTS_2001[-1][0],
            date_precision="day",
            status="held",
        )
        name(parent, "2001 Indonesian vice presidential election", "en", "english")
        name(parent, "Pemilihan Wakil Presiden Republik Indonesia 2001")
        cs.add("slug", path=PATH_2001, entity_id=parent)
        cs.add("note", entity_id=parent, kind="caveat", body_md=NOTE_2001)
        days: dict[date, UUID] = {}
        previous: UUID | None = None
        for number, (day, votes, totals) in enumerate(BALLOTS_2001, 1):
            if sum(count for count, _ in votes.values()) != totals["valid"]:
                raise ValueError(f"ballot {number}: the votes do not add up to {totals['valid']}")
            if "ballots" in totals and totals["ballots"] != sum(
                totals.get(m, 0) for m in ("valid", "invalid", "blank")
            ):
                raise ValueError(f"ballot {number}: the parts do not add up to {totals['ballots']}")
            if day not in days:
                days[day] = cs.key("id.election", f"{day}:wakil-presiden:day")
                cs.add(
                    "election",
                    id=days[day],
                    polity_id=indonesia,
                    parent_id=parent,
                    kind="indirect",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    days[day],
                    f"2001 Indonesian vice presidential election, {day:%B} {day.day} ballots",
                    "en",
                    "english",
                )
                name(days[day], f"Pemilihan Wakil Presiden Republik Indonesia 2001, pemungutan suara {day.day} Juli")
                cs.add("slug", path=f"{PATH_2001}/{day}", entity_id=days[day])
            contest_key = f"{FIRST_2001}:wakil-presiden:{number}"
            contest = cs.key("id.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=days[day],
                kind="office",
                office_id=offices["wakil-presiden"],
                body_id=assembly,
                district_area_id=nation,
                term="remainder",
                magnitude=1,
                round_number=number,
                previous_round_id=previous,
                electoral_system_id=repeated,
                default_result_kind="counted",
            )
            name(contest, f"2001 Indonesian vice presidential election, ballot {number}", "en", "english")
            name(contest, f"Pemilihan Wakil Presiden Republik Indonesia 2001, putaran {ORDINALS[number]}")
            cs.add("slug", path=f"{PATH_2001}/{day}/ballot-{number}", entity_id=contest)
            previous = contest
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=days[day],
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=article,
                is_default=True,
                completeness=1,
            )
            for report in reports:
                cs.add("citation", entity_id=result_set, source_id=report, locator={"ballot": number})
            facts: list[tuple[UUID | None, str, int]] = [(None, measure, value) for measure, value in totals.items()]
            for item, (count, outcome) in votes.items():
                option = cs.key("id.option", f"{contest_key}:{item}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_position=None,
                    ballot_label=CANDIDATES_2001[item],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("id.candidacy", f"{contest_key}:{item}"),
                    person_id=persons[item],
                    option_id=option,
                    role="head",
                    ballot_name=CANDIDATES_2001[item],
                    is_independent=False,
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome, seats=None)
                facts.append((option, "votes", count))
            for option, measure, count in facts:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=MEASURES[measure],
                    count_round=0,
                    flags=0,
                    value=count,
                )

        president = cs.key(f"{SOURCE}.tenure", f"{VOTES[0]['day']}:presiden")
        vice_president = cs.key(f"{SOURCE}.tenure", f"{VOTES[1]['day']}:wakil-presiden")
        succession = cs.key(f"{SOURCE}.tenure", "2001-07-23:presiden")
        cs.add(
            "tenure",
            id=succession,
            person_id=persons["Q76179"],
            office_id=offices["presiden"],
            from_date=date(2001, 7, 23),
            to_date=TERM_END,
            acquired_via="succession",
            ended_via="term_end",
            contest_id=None,
            succeeds_tenure_id=president,
        )
        cs.add("note", entity_id=succession, kind="context", body_md=SUCCESSION_NOTE)
        cs.add("citation", entity_id=succession, source_id=book_2001, locator={"pdf_pages": [20, 21, 22]})
        elected = cs.key(f"{SOURCE}.tenure", f"{FIRST_2001}:wakil-presiden")
        cs.add(
            "tenure",
            id=elected,
            person_id=persons["Q76344"],
            office_id=offices["wakil-presiden"],
            from_date=BALLOTS_2001[-1][0],
            to_date=TERM_END,
            acquired_via="elected",
            ended_via="term_end",
            contest_id=previous,
            succeeds_tenure_id=vice_president,
        )
        cs.add("citation", entity_id=elected, source_id=book_2001, locator={"pdf_pages": [20, 21]})
        cs.add("citation", entity_id=elected, source_id=article, locator={"section": "lead"})
        appointments(cs, name, indonesia, nation, assembly, offices)
        return cs.write()


def decree_source(cs: Draft, decree: Decree, subject: str) -> UUID:
    """The decree as a source, with the cached capture of its file as the artifact."""
    publisher = MPRS if "MPRS" in decree.number else MPR
    title = decree_title(decree, subject)
    source = cs.source(publisher, title)
    cs.add(
        "source",
        id=source,
        kind="official_body",
        publisher=publisher,
        title=title,
        url=JDIH.format(decree.file),
        license=DECREE_LICENSE,
        license_url=DECREE_LICENSE_URL,
    )
    cs.artifact(raw.cached(SOURCE, WAYBACK.format(decree.capture, JDIH.format(decree.file))), source)
    return source


def appointments(
    cs: Draft,
    name: Callable[..., None],
    indonesia: UUID,
    nation: UUID,
    assembly: UUID,
    offices: dict[str, UUID],
) -> None:
    """The MPR(S)'s appointments of the President from 1968 and of the Vice President from 1973 to 1998, each a
    contest with one candidate declared elected and no counts, the terms they began, and Habibie's succession to the
    presidency in May 1998, which was not an election."""
    procedure = decree_source(cs, PROCEDURE, PROCEDURE_SUBJECT)
    system = cs.key("id.electoral_system", "mpr:1973")
    cs.add(
        "electoral_system",
        id=system,
        polity_id=indonesia,
        rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
        rules_version=1,
    )
    name(system, "Election of the President and Vice President by the MPR under decree II/MPR/1973", "en", "english")
    cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY_1973)
    cs.add("citation", entity_id=system, source_id=procedure, locator={"pdf_pages": list(PROCEDURE.pages)})

    provisional = cs.key("id.body", "mprs")
    if provisional in cs.minted:
        cs.add("body", id=provisional, polity_id=indonesia, kind="council")
        name(provisional, "Majelis Permusyawaratan Rakyat Sementara")
        name(provisional, "MPRS", kind="abbreviation")
        name(provisional, "Provisional People's Consultative Assembly", "en", "english")

    persons: dict[str, UUID] = {}
    for key, common in APPOINTEES.items():
        persons[key] = cs.key(f"{SOURCE}.person", key)
        if persons[key] in cs.minted:
            cs.add("person", id=persons[key])
            name(persons[key], common)

    held: dict[str, UUID] = {}
    tenures: list[tuple[str, dict[str, object]]] = []
    for decree in DECREES:
        day, office = decree.day, decree.office
        english, slug, native, _ = OFFICE_NAMES[office]
        mprs = "MPRS" in decree.number
        source = decree_source(cs, decree, SUBJECT_1968 if mprs else SUBJECTS[office])
        election = cs.key("id.election", f"{day}:{office}")
        path = f"id/{day}-{english}"
        title = f"{day.year} Indonesian {english.replace('-', ' ')} election"
        cs.add(
            "election",
            id=election,
            polity_id=indonesia,
            kind="indirect",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        name(election, title, "en", "english")
        name(election, f"Pemilihan {native} Republik Indonesia {day.year}")
        cs.add("slug", path=path, entity_id=election)
        contest_key = f"{day}:{office}"
        contest = cs.key("id.contest", contest_key)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=offices[office],
            body_id=provisional if mprs else assembly,
            district_area_id=nation,
            term="full",
            magnitude=1,
            round_number=1,
            previous_round_id=None,
            electoral_system_id=None if mprs else system,
            default_result_kind="tacit",
        )
        name(contest, title, "en", "english")
        name(contest, f"Pemilihan {native} Republik Indonesia {day.year}")
        cs.add("slug", path=f"{path}/{slug}", entity_id=contest)
        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="tacit",
            status="active",
            source_id=source,
            is_default=True,
            completeness=None,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE_1968 if mprs else decree_note(decree))
        cs.add("citation", entity_id=result_set, source_id=source, locator={"pdf_pages": list(decree.pages)})
        option = cs.key("id.option", f"{contest_key}:{decree.person}")
        cs.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None,
            ballot_position=None,
            ballot_label=decree.printed,
        )
        cs.add(
            "candidacy",
            id=cs.key("id.candidacy", f"{contest_key}:{decree.person}"),
            person_id=persons[decree.person],
            option_id=option,
            role="head",
            ballot_name=decree.printed,
            is_independent=False,
        )
        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
        tenure = cs.key(f"{SOURCE}.tenure", contest_key)
        tenures.append(
            (
                office,
                {
                    "id": tenure,
                    "person_id": persons[decree.person],
                    "office_id": offices[office],
                    "from_date": day,
                    "acquired_via": "elected",
                    "ended_via": "term_end",
                    "contest_id": contest,
                    "succeeds_tenure_id": held.get(office),
                },
            )
        )
        held[office] = tenure

    # Each term runs to the day the next appointment to the same office was adopted. Soeharto's last term ended
    # when he resigned and Habibie, his Vice President, took the oath as President.
    following: dict[str, object] = {office: HABIBIE_SWORN for office in held}
    for office, row in reversed(tenures):
        row["to_date"] = following[office]
        following[office] = row["from_date"]
    account = decree_source(cs, ACCOUNT, ACCOUNT_SUBJECT)
    succession = cs.key(f"{SOURCE}.tenure", f"{HABIBIE_SWORN}:presiden")
    for office, row in tenures:
        if row["id"] == held[office]:
            row["ended_via"] = "resigned" if office == "presiden" else "elevated"
        cs.add("tenure", **row)
    cs.add(
        "tenure",
        id=succession,
        person_id=persons["habibie"],
        office_id=offices["presiden"],
        from_date=HABIBIE_SWORN,
        to_date=VOTES[0]["day"],
        acquired_via="succession",
        ended_via="term_end",
        contest_id=None,
        succeeds_tenure_id=held["presiden"],
    )
    cs.add("note", entity_id=succession, kind="context", body_md=HABIBIE_NOTE)
    cs.add("citation", entity_id=succession, source_id=account, locator={"pdf_pages": list(ACCOUNT.pages)})
