"""Election Commission of Nepal, the history of Nepal's elections (नेपालको निर्वाचन इतिहास, 2073 BS): the members the
1991, 1994, and 1999 House of Representatives elections and the 2008 Constituent Assembly election returned in each
constituency, and the party-list votes and seats of the 2008 and 2013 Constituent Assembly elections."""

import functools
import html
import json
import re
import subprocess
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.np.preeti import number, unicode
from ea.store import DATA

SOURCE = "np.history"
NEPAL = str(uuid5(package.NAMESPACE, "polity\x1fNP"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNPL"))
BOOK = "https://election.gov.np/source/Resources%20Other/%E0%A4%87%E0%A4%A4%E0%A4%BF%E0%A4%B9%E0%A4%BE%E0%A4%B8.pdf"
LOCAL = DATA / "inbox" / "manual" / SOURCE
INDEPENDENT = "स्वतन्त्र"
TOTAL = {"hDdf", "hDDff"}  # जम्मा, total


class Word(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


class Page(NamedTuple):
    number: int
    width: float
    height: float
    words: list[Word]


class Cell(NamedTuple):
    text: str
    page: int
    box: tuple[float, float, float, float]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    """Store the commission's book, which a browser saved from its site, in the raw cache."""
    index = json.loads((LOCAL / "index.json").read_text())
    for url, entry in index.items():
        if entry.get("source", url) != BOOK:
            continue
        raw.store(
            SOURCE,
            entry.get("source", url),
            (LOCAL / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


@functools.cache
def pages(first: int, last: int) -> list[Page]:
    """The words of PDF pages first to last, with their boxes in points from the top left."""
    path = raw.cached(SOURCE, BOOK).path
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(first), "-l", str(last), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = []
    for number_, chunk in enumerate(out.split("<page ")[1:], first):
        size = re.match(r'width="([\d.]+)" height="([\d.]+)"', chunk)
        if size is None:
            raise ValueError(f"page {number_} has no size")
        words = [
            Word(float(a), float(b), float(c), float(d), html.unescape(text))
            for a, b, c, d, text in re.findall(
                r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', chunk
            )
        ]
        found.append(Page(number_, float(size[1]), float(size[2]), words))
    return found


def _serial(text: str) -> int | None:
    clean = text.rstrip("=+").strip()
    if re.fullmatch(r"[0-9]+", clean):
        return int(clean)
    if re.fullmatch(r"[!@#$%^&*()]+", clean):
        return number(clean)
    return None


def _cell(words: list[Word], page: Page) -> Cell:
    words = sorted(words, key=lambda w: (round(w.y0), w.x0))
    text = ""
    last: Word | None = None
    for w in words:
        if last is not None:
            text += "" if abs(w.y0 - last.y0) < 2 and w.x0 - last.x1 < 0.8 else " "
        text += w.text
        last = w
    box = (
        min(w.x0 for w in words) / page.width,
        min(w.y0 for w in words) / page.height,
        max(w.x1 for w in words) / page.width,
        max(w.y1 for w in words) / page.height,
    )
    return Cell(text, page.number, box)


def table(
    first: int,
    last: int,
    columns: list[tuple[str, str, int]],
    count: int,
    top: bool = False,
    depth: float = 16,
    numeric: frozenset[str] = frozenset(),
) -> list[dict[str, Cell]]:
    """The rows of a table that runs from PDF page first to last and numbers its rows 1 to count.

    Each column is (name, header word, which occurrence of that word on the header line); the header's lines reach
    depth points below its first. Words made only of digits go to the next numeric column at or to their right. The first column holds the
    row numbers. A cell's words may wrap onto lines above or below its row number, so each word goes to the row whose
    number is nearest to it on the page, or with top, to the last row number at or above it. The table ends at its
    total line or at the first line whose first column is not a row number."""
    rows: list[dict[str, Cell]] = []
    for page in pages(first, last):
        heads = [w for w in page.words if w.text == columns[0][1]]
        if not heads:
            raise ValueError(f"page {page.number} has no {columns[0][1]} header")
        header = heads[0].y0
        line = sorted((w for w in page.words if abs(w.y0 - header) < 10), key=lambda w: w.x0)
        starts: list[tuple[float, str]] = []
        for name, head, nth in columns:
            matches = [w for w in line if w.text == head]
            if len(matches) <= nth:
                raise ValueError(f"page {page.number} has no header {head!r} #{nth}")
            starts.append((matches[nth].x0 - 3, name))
        starts.sort()
        cut = max(w.y1 for w in page.words if abs(w.y0 - header) < depth)
        body = [w for w in page.words if w.y0 > cut]
        serial_end = starts[1][0]
        anchors: list[tuple[int, Word]] = []
        totals = [w.y0 - 1 for w in body if w.text in TOTAL]
        end = min(totals, default=page.height)
        for w in sorted(body, key=lambda w: (w.y0, w.x0)):
            if w.x0 >= serial_end or w.y0 >= end:
                continue
            n = _serial(w.text)
            if n is None:
                end = w.y0 - 1
                break
            anchors.append((n, w))
        if not anchors:
            continue
        floor = anchors[-1][1].y1 + 16
        cells: dict[int, dict[str, list[Word]]] = {n: {} for n, _ in anchors}
        spill: list[Word] = []
        for w in body:
            if w.y0 >= end or w.y0 > floor or any(w is a for _, a in anchors) or w.text.isascii() and w.text.isdigit():
                continue  # the book's page numbers are the only figures set in Latin digits
            middle = (w.y0 + w.y1) / 2
            if top:
                above = [a for a in anchors if a[1].y0 <= w.y0 + 2]
                if not above:
                    spill.append(w)
                    continue
                n = above[-1][0]
            else:
                n = min(anchors, key=lambda a: abs((a[1].y0 + a[1].y1) / 2 - middle))[0]
            column = [name for start, name in starts if start <= w.x0][-1]
            if column not in numeric and re.fullmatch(r"[!@#$%^&*(),]+", w.text):
                # Figures are set flush right, so their first digits can start left of their column's header.
                column = next((name for start, name in starts if start > w.x0 and name in numeric), column)
            cells[n].setdefault(column, []).append(w)
        if spill:
            # The last row of the previous page runs over onto the top of this one.
            if not rows:
                raise ValueError(f"page {page.number}: {spill[0].text!r} lies above the first row")
            for w in spill:
                column = [name for start, name in starts if start <= w.x0][-1]
                prior = rows[-1][column]
                rows[-1][column] = Cell(f"{prior.text} {_cell([w], page).text}", prior.page, prior.box)
        for n, anchor in anchors:
            row = {name: _cell(words, page) for name, words in cells[n].items()}
            row[columns[0][0]] = _cell([anchor], page)
            if n != len(rows) + 1:
                raise ValueError(f"page {page.number}: row {n} follows row {len(rows)}")
            rows.append(row)
        if end < page.height or len(rows) == count:
            break
    if len(rows) != count:
        raise ValueError(f"pages {first}-{last} hold {len(rows)} rows, not {count}")
    return rows


# The 75 districts of 1962 to 2015, in the order the commission prints them, with the spellings the book also uses.
DISTRICTS = [
    ("ताप्लेजुङ", "Taplejung"), ("पाँचथर", "Panchthar"), ("इलाम", "Ilam"), ("झापा", "Jhapa"),
    ("संखुवासभा", "Sankhuwasabha"), ("तेह्रथुम", "Terhathum"), ("भोजपुर", "Bhojpur"), ("धनकुटा", "Dhankuta"),
    ("मोरङ", "Morang"), ("सुनसरी", "Sunsari"), ("सोलुखुम्बु", "Solukhumbu"), ("खोटाङ", "Khotang"),
    ("ओखलढुंगा", "Okhaldhunga"), ("उदयपुर", "Udayapur"), ("सप्तरी", "Saptari"), ("सिराहा", "Siraha"),
    ("दोलखा", "Dolakha"), ("रामेछाप", "Ramechhap"), ("सिन्धुली", "Sindhuli"), ("धनुषा", "Dhanusha"),
    ("महोत्तरी", "Mahottari"), ("सर्लाही", "Sarlahi"), ("रसुवा", "Rasuwa"), ("धादिङ", "Dhading"),
    ("नुवाकोट", "Nuwakot"), ("काठमाडौं", "Kathmandu"), ("भक्तपुर", "Bhaktapur"), ("ललितपुर", "Lalitpur"),
    ("काभ्रेपलाञ्चोक", "Kavrepalanchok"), ("सिन्धुपाल्चोक", "Sindhupalchok"), ("मकवानपुर", "Makwanpur"),
    ("रौतहट", "Rautahat"), ("बारा", "Bara"), ("पर्सा", "Parsa"), ("चितवन", "Chitwan"), ("गोरखा", "Gorkha"),
    ("मनाङ", "Manang"), ("लमजुङ", "Lamjung"), ("कास्की", "Kaski"), ("तनहुँ", "Tanahun"), ("स्याङजा", "Syangja"),
    ("गुल्मी", "Gulmi"), ("पाल्पा", "Palpa"), ("अर्घाखाँची", "Arghakhanchi"), ("नवलपरासी", "Nawalparasi"),
    ("रुपन्देही", "Rupandehi"), ("कपिलवस्तु", "Kapilvastu"), ("मुस्ताङ", "Mustang"), ("म्याग्दी", "Myagdi"),
    ("बाग्लुङ", "Baglung"), ("पर्वत", "Parbat"), ("रुकुम", "Rukum"), ("रोल्पा", "Rolpa"), ("प्युठान", "Pyuthan"),
    ("सल्यान", "Salyan"), ("दाङ", "Dang"), ("डोल्पा", "Dolpa"), ("मुगु", "Mugu"), ("जुम्ला", "Jumla"),
    ("कालिकोट", "Kalikot"), ("हुम्ला", "Humla"), ("जाजरकोट", "Jajarkot"), ("दैलेख", "Dailekh"),
    ("सुर्खेत", "Surkhet"), ("बाँके", "Banke"), ("बर्दिया", "Bardiya"), ("बाजुरा", "Bajura"), ("अछाम", "Achham"),
    ("बझाङ", "Bajhang"), ("डोटी", "Doti"), ("कैलाली", "Kailali"), ("दार्चुला", "Darchula"), ("बैतडी", "Baitadi"),
    ("डडेल्धुरा", "Dadeldhura"), ("कञ्चनपुर", "Kanchanpur"),
]  # fmt: skip
SPELLINGS = {
    "ईलाम": "इलाम", "काठमाण्डौ": "काठमाडौं", "काठमाडौँ": "काठमाडौं", "काभ्रेपलान्चोक": "काभ्रेपलाञ्चोक",
    "लम्जुङ": "लमजुङ", "बाके": "बाँके", "बांके": "बाँके", "कंचनपुर": "कञ्चनपुर", "कन्चनपुर": "कञ्चनपुर",
    "तनहु": "तनहुँ", "सिरहा": "सिराहा", "वारा": "बारा", "रूपन्देही": "रुपन्देही", "वाग्लुङ": "बाग्लुङ",
    "वैतडी": "बैतडी",
}  # fmt: skip
# Spellings of one party's name that differ only in typing; every other printed name is its own party.
PARTIES = {
    "ने.क.पा. (एमाले)": "नेपाल कम्युनिष्ट पार्टी (एमाले)",
    "ने.क.पा.(एमाले)": "नेपाल कम्युनिष्ट पार्टी (एमाले)",
    "नेपाल कम्यूनिष्ट पार्टी (एमाले)": "नेपाल कम्युनिष्ट पार्टी (एमाले)",
    "नेपाल कम्युनिष्ट पाटी (एमाले)": "नेपाल कम्युनिष्ट पार्टी (एमाले)",
    "नेपाल कम्युनिष्ट पार्टी (ए.मा.ले.)": "नेपाल कम्युनिष्ट पार्टी (एमाले)",
    "रा.प्र.पा.(चन्द)": "राष्ट्रिय प्रजातन्त्र पार्टी (चन्द)",
    "रा.प्र.पा. (थापा)": "राष्ट्रिय प्रजातन्त्र पार्टी (थापा)",
    "नेपाल मजदूर किसान पार्टी": "नेपाल मजदुर किसान पार्टी",
    "नेपाल कम्युनिष्ट पार्टी (माओवादीे)": "नेपाल कम्युनिष्ट पार्टी (माओवादी)",
    "नेपाल कम्युनिष्ट पार्टी (माओवादीे": "नेपाल कम्युनिष्ट पार्टी (माओवादी)",
    "नेपाली काँग्रेस": "नेपाली कांग्रेस",
    "मधेसी जन अधिकार फोरम, नेपाल": "मधेशी जनअधिकार फोरम, नेपाल",
    "सदभावना पार्टी": "सद्भावना पार्टी",
    "नेपाःराष्ट्रिय पार्टी": "नेपाः राष्ट्रिय पार्टी",
    "चुरे भावर राष्ट्रिय एकता पार्टी, नेपाल": "चुरेभावर राष्ट्रिय एकता पार्टी नेपाल",
    "तराई–मधेश लोकतान्त्रिक पार्टी": "तराई मधेश लोकतान्त्रिक पार्टी",
    "नेपाल कम्युनिष्ट पार्टी (मा.ले)": "नेपाल कम्युनिष्ट पार्टी (मा.ले.)",
    "राष्ट्रिय प्रजातन्त्र पार्टी, नेपाल": "राष्ट्रिय प्रजातन्त्र पार्टी नेपाल",
    "(समाजवादी) ॐ सेना नेपाल": "ॐ सेना नेपाल (समाजवादी)",
}
HOUSE_COLUMNS = [
    ("district", "lhNnf", 0),
    ("constituency", "If]", 0),
    ("name", "gfd", 0),
    ("age", "jif{", 0),
    ("sex", "lnË", 0),
    ("party", "/fhg}lts", 0),
]
CA_COLUMNS = [
    ("serial", "qm=;+=", 0),
    ("district", "lhNnf", 0),
    ("constituency", "lg=", 0),
    ("name", "pDd]jf/sf]", 0),
    ("sex", "lnË", 0),
    ("age", "pd]/", 0),
    ("party", "/fhgLlts", 0),
]
LIST_COLUMNS = [("serial", "qm=;+=", 0), ("party", "/fhgLlts", 0), ("votes", "k|fKt", 0), ("seats", "k|fKt", 1)]
SEX = {"d": "male", "h": "female"}
SEX_CA = {"k'": "male", "d": "female"}
# Rows the annexes mark as women whose members are men: with them, the annexes count 8 women in 1991 and 9 in 1994,
# where the book's summary of the elections (section 7.23.2) counts 7 in each.
SEX_ERRATA = {("1991-05-12", 195): "male", ("1994-11-15", 110): "male", ("1994-11-15", 111): "male"}
# Annex 10 numbers Kailali's second constituency 1, between Kailali 1 and Kailali 3.
NUMBER_ERRATA = {("1994-11-15", 196): 2}


class Election(NamedTuple):
    day: date
    end: date
    bs: str
    body: str
    scheme: str
    winners: tuple[int, int, str] | None  # first and last PDF page of the winners, and the serial header
    seats: dict[str, int]  # the constituency seats by party; the book's summaries agree except in 1999 (see CAVEATS)
    women: int  # the women elected in the constituencies, from the book's summary
    lists: tuple[int, int, int] | None  # first and last PDF page of the party-list table, and its parties
    annex: str  # the annex of the winners, or of the party-list table when the book prints no winners


HOUSE = key("body", "house_of_representatives_1990")
ASSEMBLY = key("body", "constituent_assembly")
FPTP = key("electoral_system", "house_fptp_1990")
CA_FPTP = key("electoral_system", "ca_fptp")
CA_PR = key("electoral_system", "ca_pr")
SOURCE_ID = key("source", SOURCE)
UML = "नेपाल कम्युनिष्ट पार्टी (एमाले)"
NC = "नेपाली कांग्रेस"

ELECTIONS = {
    "1991-05-12": Election(
        date(1991, 5, 12),
        date(1991, 5, 12),
        "२०४८ वैशाख २९",
        HOUSE,
        "np.hor_1991",
        (684, 690, "l;=g+"),
        {
            NC: 110, UML: 69, "संयुक्त जनमोर्चा नेपाल": 9, "नेपाल सद्भावना पार्टी": 6,
            "राष्ट्रिय प्रजातन्त्र पार्टी (चन्द)": 3, "नेपाल मजदुर किसान पार्टी": 2,
            "ने.क.पा. (प्रजातन्त्रवादी)": 2, "राष्ट्रिय प्रजातन्त्र पार्टी (थापा)": 1, INDEPENDENT: 3,
        },
        7,
        None,
        "annex 9",
    ),
    "1994-11-15": Election(
        date(1994, 11, 15),
        date(1994, 11, 15),
        "२०५१ कार्तिक २९",
        HOUSE,
        "np.hor_1994",
        (691, 696, "l;=g+="),
        {
            UML: 88, NC: 83, "राष्ट्रिय प्रजातन्त्र पार्टी": 20, "नेपाल मजदुर किसान पार्टी": 4,
            "नेपाल सद्भावना पार्टी": 3, INDEPENDENT: 7,
        },
        7,
        None,
        "annex 10",
    ),
    "1999-05-03": Election(
        date(1999, 5, 3),
        date(1999, 5, 17),
        "२०५६ वैशाख २० र जेठ ३",
        HOUSE,
        "np.hor_1994",
        (698, 704, "l;=g+="),
        {
            NC: 111, UML: 70, "राष्ट्रिय प्रजातन्त्र पार्टी": 12, "राष्ट्रिय जनमोर्चा": 5,
            "नेपाल सद्भावना पार्टी": 5, "नेपाल मजदुर किसान पार्टी": 1, "संयुक्त जनमोर्चा नेपाल": 1,
        },
        12,
        None,
        "annex 11",
    ),
    "2008-04-10": Election(
        date(2008, 4, 10),
        date(2008, 4, 10),
        "२०६४ चैत्र २८",
        ASSEMBLY,
        "np.ca_2008",
        (760, 770, "qm=;+="),
        {
            "नेपाल कम्युनिष्ट पार्टी (माओवादी)": 120, NC: 37, UML: 33, "मधेशी जनअधिकार फोरम, नेपाल": 30,
            "तराई मधेश लोकतान्त्रिक पार्टी": 9, "सद्भावना पार्टी": 4, "जनमोर्चा नेपाल": 2,
            "नेपाल मजदुर किसान पार्टी": 2, "राष्ट्रिय जनमोर्चा": 1, INDEPENDENT: 2,
        },
        30,
        (773, 773, 25),
        "annex 36",
    ),
    "2013-11-19": Election(
        date(2013, 11, 19),
        date(2013, 11, 19),
        "२०७० मंसिर ४",
        ASSEMBLY,
        "np.ca_2008",
        None,
        {},
        0,
        (883, 886, 122),
        "annex 68",
    ),
}  # fmt: skip
SCHEMES = {
    "np.hor_1991": "House of Representatives constituency of 1991",
    "np.hor_1994": "House of Representatives constituency of 1992 to 2002",
    "np.ca_2008": "Constituent Assembly constituency",
}
LIST_SEATS = 335


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in ELECTIONS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def party_name(text: str) -> str:
    printed = " ".join(unicode(text).split())
    return PARTIES.get(printed, printed)


def district(text: str) -> int:
    printed = SPELLINGS.get(unicode(text).replace(" ", ""), unicode(text).replace(" ", ""))
    names = [ne for ne, _ in DISTRICTS]
    if printed not in names:
        raise ValueError(f"unknown district {printed!r}")
    return names.index(printed) + 1


class Winner(NamedTuple):
    district: int
    number: int
    name: str
    gender: str
    party: str


@functools.cache
def winners(day: str) -> list[Winner]:
    """The member each constituency returned, checked against the seats by party and the women elected."""
    cfg = ELECTIONS[day]
    if cfg.winners is None:
        return []
    first, last, serial = cfg.winners
    house = cfg.body == HOUSE
    columns = [("serial", serial, 0), *HOUSE_COLUMNS] if house else CA_COLUMNS
    rows = table(first, last, columns, 205 if house else 240, top=not house)
    out: list[Winner] = []
    current = 0
    for i, row in enumerate(rows, 1):
        if "district" in row:
            d = district(row["district"].text)
            if d <= current:
                raise ValueError(f"{day} row {i}: district {d} out of order")
            current = d
        number_ = NUMBER_ERRATA.get((day, i), number(row["constituency"].text))
        if out and out[-1].district == current and number_ != out[-1].number + 1:
            raise ValueError(f"{day} row {i}: constituency {number_} follows {out[-1].number}")
        sex = SEX_ERRATA.get((day, i), (SEX if house else SEX_CA)[row["sex"].text])
        name = " ".join(unicode(row["name"].text).split())
        out.append(Winner(current, number_, name, sex, party_name(row["party"].text)))
    if current != len(DISTRICTS):
        raise ValueError(f"{day} ends at district {current}")
    seats = Counter(w.party for w in out)
    if seats != Counter(cfg.seats):
        raise ValueError(f"{day} seats by party {dict(seats)} differ from the book's {cfg.seats}")
    if sum(w.gender == "female" for w in out) != cfg.women:
        raise ValueError(f"{day} elects {sum(w.gender == 'female' for w in out)} women, not {cfg.women}")
    return out


class ListRow(NamedTuple):
    party: str
    label: str
    votes: int
    seats: int
    cell: Cell


@functools.cache
def lists(day: str) -> list[ListRow]:
    cfg = ELECTIONS[day]
    if cfg.lists is None:
        return []
    first, last, count = cfg.lists
    rows = table(first, last, LIST_COLUMNS, count, depth=4, numeric=frozenset({"votes", "seats"}))
    out = [
        ListRow(
            party_name(row["party"].text),
            " ".join(unicode(row["party"].text).split()),
            number(row["votes"].text),
            number(row["seats"].text),
            row["votes"],
        )
        for row in rows
    ]
    if sum(r.seats for r in out) != LIST_SEATS:
        raise ValueError(f"{day} list seats add up to {sum(r.seats for r in out)}")
    if len({r.party for r in out}) != len(out):
        raise ValueError(f"{day} names a party twice")
    return out


def area(scheme: str, district_: int, number_: int) -> str:
    return key("area", scheme, f"{district_}-{number_}")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Nepal's House of Representatives constituencies of 1991 and of 1992 to 2002, and its Constituent Assembly "
        "constituencies of 2008 and 2013, as the Election Commission's history of elections prints them",
    )
    p.external |= {NEPAL, COUNTRY}
    p.uses(raw.cached(SOURCE, BOOK))
    for rank, (code, noun) in enumerate(SCHEMES.items(), 1):
        p.add("scheme", code=code, polity_id=NEPAL, kind="electoral_district", rank=rank, noun=noun, code_system=None)
    seen: set[tuple[str, int, int]] = set()
    for day, cfg in ELECTIONS.items():
        for w in winners(day):
            if (cfg.scheme, w.district, w.number) in seen:
                continue
            seen.add((cfg.scheme, w.district, w.number))
            a = area(cfg.scheme, w.district, w.number)
            ne, en = DISTRICTS[w.district - 1]
            p.add("area", id=a, scheme_code=cfg.scheme)
            p.add("area_code", area_id=a, code=f"{w.district}-{w.number}")
            p.add("area_parent", area_id=a, parent_id=COUNTRY)
            p.name(a, f"{ne} - {w.number}", "ne", "official")
            p.name(a, f"{en} {w.number}", "en", "official", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Nepal's House of Representatives under the 1990 constitution, its Constituent Assembly, their electoral "
        "systems, and the parties of 1991 to 2013, from the Election Commission's history of elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEPAL}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="निर्वाचन आयोग, नेपाल (Election Commission, Nepal)",
        title="नेपालको निर्वाचन इतिहास (The History of Nepal's Elections), 2073 BS",
        url=BOOK,
        license="No license stated; published by the electoral authority for public use",
        attribution="Election Commission, Nepal",
    )
    p.artifact(raw.cached(SOURCE, BOOK), SOURCE_ID)
    p.add("body", id=HOUSE, polity_id=NEPAL, kind="lower_house")
    p.name(HOUSE, "House of Representatives (1990 constitution)", "en", "official")
    p.name(HOUSE, "प्रतिनिधि सभा", "ne", "native", False)
    p.add("body", id=ASSEMBLY, polity_id=NEPAL, kind="constituent_assembly")
    p.name(ASSEMBLY, "Constituent Assembly", "en", "official")
    p.name(ASSEMBLY, "संविधान सभा", "ne", "native", False)
    for id, rules, start, end, method in (
        (
            FPTP,
            {"formula": "fptp"},
            date(1990, 11, 9),
            date(2007, 1, 15),
            "One member for each of the 205 constituencies, elected by the most votes.",
        ),
        (
            CA_FPTP,
            {"formula": "fptp"},
            date(2007, 1, 15),
            date(2015, 9, 20),
            "One member of the Constituent Assembly for each of the 240 constituencies, elected by the most votes.",
        ),
        (
            CA_PR,
            {"formula": "sainte_lague"},
            date(2007, 1, 15),
            date(2015, 9, 20),
            (
                "335 members of the Constituent Assembly elected from closed party lists in one national "
                "constituency. The seats are divided among the parties by the Sainte-Laguë method, and each party "
                "fills them from its list under the inclusion quotas the law sets for women, Madheshi, Dalit, "
                "indigenous, and other groups."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=NEPAL,
            valid_from=start,
            valid_to=end,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names = {w.party for day in ELECTIONS for w in winners(day)} | {r.party for day in ELECTIONS for r in lists(day)}
    for name in sorted(names - {INDEPENDENT}):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=NEPAL)
        p.name(id, name, "ne", "official")
    return p


def party(name: str) -> str:
    return key("party", name)


WINNERS_NOTE = (
    "The Election Commission's history of Nepal's elections (2073 BS, {annex}) names the member each constituency "
    "returned, with the member's party, sex, and age. It prints no vote counts for the constituency races, so the "
    "result holds the declared winner only."
)
SUMMARY = "the seats by party in the book's summary of the House elections (section 7.23.2)"
CAVEATS = {
    "1991-05-12": (
        f"The winners' parties add up to {SUMMARY}. Annex 9 marks Ganga Bahadur Kunwar (Kailali 1) as a woman; the "
        "summary counts 7 women elected, so he is stored as a man."
    ),
    "1994-11-15": (
        f"The winners' parties add up to {SUMMARY}. Annex 10 marks Ramesh Rijal (Parsa 4) and Jagrit Prasad "
        "Bhetwal (Chitwan 1) as women; the summary counts 7 women elected, so they are stored as men. The annex "
        "numbers Kailali's second constituency 1, between Kailali 1 and 3; it is stored as Kailali 2."
    ),
    "1999-05-03": (
        "Polling took place in two phases, on 3 and 17 May 1999. Annex 11 gives the Communist Party of Nepal "
        "(Unified Marxist-Leninist) 70 constituencies and the Rastriya Prajatantra Party 12, where "
        f"{SUMMARY} gives them 71 and 11. The annex does not show which constituency differs, so it is stored as "
        "printed."
    ),
    "2008-04-10": "The winners' parties add up to the constituency seats by party in annex 35 of the book.",
    "2013-11-19": (
        "The book's list of the members the constituencies returned (annex 66) is set in a font whose text cannot "
        "be read back from the file, so the constituency races are not stored yet."
    ),
}
LIST_NOTE = (
    "The national party-list votes and seats of each party, as the Election Commission's history of Nepal's "
    "elections (2073 BS, {annex}) prints them. Each vote total keeps its page and box in the book."
)
LIST_NOTE_2008 = (
    "The national party-list votes and seats of the 25 parties that won list seats, as the Election Commission's "
    "history of Nepal's elections (2073 BS, annex 38) prints them. The book does not list the votes of the parties "
    "that won no list seat, so the votes here fall short of the valid party-list votes. Each vote total keeps its "
    "page and box in the book."
)


def election(day: str) -> Package:
    cfg = ELECTIONS[day]
    house = cfg.body == HOUSE
    year = cfg.day.year
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} Nepal {'House of Representatives' if house else 'Constituent Assembly'} election: "
        + ("the member each constituency returned" if cfg.winners else "")
        + (" and " if cfg.winners and cfg.lists else "")
        + ("the national party-list votes and seats" if cfg.lists else "")
        + ", from the Election Commission's history of elections",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEPAL, COUNTRY, "np.nation"}
    artifact = raw.cached(SOURCE, BOOK)
    p.uses(artifact)
    path = f"np/{day}-general"
    el = key("election", day)
    p.add(
        "election",
        id=el,
        polity_id=NEPAL,
        kind="general",
        date_from=cfg.day,
        date_to=cfg.end,
        original_text=cfg.bs,
        status="held",
    )
    body_en = "House of Representatives" if house else "Constituent Assembly"
    body_ne = "प्रतिनिधि सभा सदस्य निर्वाचन" if house else "संविधान सभा सदस्य निर्वाचन"
    p.name(el, f"{year} Nepal {'general election' if house else 'Constituent Assembly election'}", "en", "official")
    p.name(el, f"{body_ne}, {cfg.bs.split()[0]}", "ne", "native", False)
    p.add("slug", path=path, entity_id=el, is_current=True)
    p.add("note", id=key("note", el), entity_id=el, kind="caveat", body_md=CAVEATS[day], lang="en")
    section = "house" if house else "constituent-assembly"
    people: set[str] = set()
    for w in winners(day):
        ne, en = DISTRICTS[w.district - 1]
        tag = f"{w.district}-{w.number}"
        c = key("contest", day, tag)
        p.add(
            "contest",
            id=c,
            election_id=el,
            kind="body_seats",
            body_id=cfg.body,
            district_area_id=area(cfg.scheme, w.district, w.number),
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP if house else CA_FPTP,
            default_result_kind="counted",
        )
        p.name(c, f"{year} {body_en} election, {en} {w.number}", "en", "official")
        p.name(c, f"{body_ne}, {ne} - {w.number}", "ne", "native", False)
        p.add("slug", path=f"{path}/{section}/{changeset.slug(en)}-{w.number}", entity_id=c, is_current=True)
        rs = key("result_set", day, tag)
        p.add(
            "result_set",
            id=rs,
            election_id=el,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=None,
        )
        p.add(
            "note",
            id=key("note", rs),
            entity_id=rs,
            kind="methodology",
            body_md=WINNERS_NOTE.format(annex=cfg.annex),
            lang="en",
        )
        option = key("option", day, tag)
        # A candidate could stand in two constituencies; one name and party twice in an election is one person.
        person = key("person", day, w.name, w.party)
        solo = w.party == INDEPENDENT
        member = None if solo else party(w.party)
        if person not in people:
            people.add(person)
            p.add("person", id=person, gender=w.gender)
            p.name(person, w.name, "ne", "official")
        p.add("contest_option", id=option, contest_id=c, kind="candidate", party_id=member, ballot_label=w.name)
        p.add(
            "candidacy",
            id=key("candidacy", day, tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=w.name,
            member_of_party_id=member,
            is_independent=solo,
        )
        p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    if cfg.lists:
        _list_contest(p, day, el, path, artifact)
    return p


def _list_contest(p: Package, day: str, el: str, path: str, artifact: raw.Artifact) -> None:
    cfg = ELECTIONS[day]
    year = cfg.day.year
    c = key("contest", day, "list")
    p.add(
        "contest",
        id=c,
        election_id=el,
        kind="list_allocation",
        body_id=ASSEMBLY,
        district_area_id=COUNTRY,
        magnitude=LIST_SEATS,
        round_number=1,
        electoral_system_id=CA_PR,
        default_result_kind="counted",
    )
    p.name(c, f"{year} Constituent Assembly election, party-list vote", "en", "official")
    p.name(c, "संविधान सभा सदस्य निर्वाचन, समानुपातिक", "ne", "native", False)
    p.add("slug", path=f"{path}/constituent-assembly-party-list", entity_id=c, is_current=True)
    rows = lists(day)
    complete = day != "2008-04-10"
    rs = key("result_set", day, "list")
    p.add(
        "result_set",
        id=rs,
        election_id=el,
        contest_id=c,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0 if complete else None,
    )
    p.add(
        "note",
        id=key("note", rs),
        entity_id=rs,
        kind="methodology",
        body_md=LIST_NOTE.format(annex=cfg.annex) if complete else LIST_NOTE_2008,
        lang="en",
    )
    unit = key("unit", day, "list")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=NEPAL,
        scheme_code="np.nation",
        code=f"{day}:NPL",
        area_relation="equals",
        area_id=COUNTRY,
        kind="standard",
    )
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{SOURCE_ID}\x1f{artifact.storage_key}"))
    if cfg.lists is None:
        raise ValueError(f"{day} has no party-list table")
    sizes = {page.number: (page.width, page.height) for page in pages(cfg.lists[0], cfg.lists[1])}
    for number_ in sorted({r.cell.page for r in rows}):
        width, height = sizes[number_]
        p.add(
            "source_page",
            id=key("page", number_),
            artifact_id=artifact_id,
            page_number=number_,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{number_}.png",
            width=round(width),
            height=round(height),
        )
    for i, r in enumerate(rows, 1):
        option = key("option", day, "list", r.party)
        p.add(
            "contest_option",
            id=option,
            contest_id=c,
            kind="list",
            party_id=party(r.party),
            ballot_position=i,
            ballot_label=r.label,
        )
        p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=r.votes,
        )
        x0, y0, x1, y1 = r.cell.box
        p.add(
            "value_locator",
            result_set_id=rs,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=1,
            count_round=0,
            page_id=key("page", r.cell.page),
            source_id=SOURCE_ID,
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
        )
        if r.seats:
            p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=r.seats)
