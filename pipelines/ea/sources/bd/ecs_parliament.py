"""Bangladesh Election Commission: Jatiya Sangsad general elections by constituency, from its statistical reports."""

import functools
import html
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.bd.bijoy import unicode

SOURCE = "bd.ecs_parliament"
BANGLADESH = str(uuid5(package.NAMESPACE, "polity\x1fBD"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBGD"))
HOME = "https://www.ecs.gov.bd/"
ASSET = "https://asset.ecs.gov.bd/media/uploaded_files/notices/2021/09/"
SCHEME = "bd.constituency"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

# The Commission's statistical reports of each general election, posted in its Election Report category.
REPORT_2001 = ASSET + "WqafvkDhpbk4kb1MV6PNWIHvIngSvbLWrVIhVxEK.pdf"
REPORT_2008 = ASSET + "36Sa01LjyXk71kuGw5U1oK5zGEvYq8LxRx5sm0MG.pdf"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECS = key("source", "ecs")
SANGSAD = key("body", "jatiya-sangsad")
FPTP = key("electoral_system", "fptp")


@dataclass
class Candidate:
    name: str
    party: str  # the canonical English name, or "Independent"
    votes: int
    none_of_above: bool = False


@dataclass
class Seat:
    number: int
    name: str  # English, e.g. "Kurigram-1"
    name_bn: str | None
    candidates: list[Candidate] = field(default_factory=list)
    registered: int = 0
    valid: int = 0
    invalid: int = 0
    cast: int = 0
    centers: int | None = None
    reported: int | None = None
    suspended: int | None = None
    day: date | None = None  # when the seat polled on another day than the general election


@dataclass(frozen=True)
class General:
    day: date
    number: int  # which Parliament
    url: str
    pages: tuple[int, int]
    seats: int  # how many constituencies the report prints


GENERAL = {
    2001: General(date(2001, 10, 1), 8, REPORT_2001, (208, 324), 300),
    2008: General(date(2008, 12, 29), 9, REPORT_2008, (159, 309), 300),
}
ORDINAL = {8: "8th", 9: "9th"}


def clean(text: str) -> str:
    return " ".join(text.split())


def squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def number(text: str) -> int:
    return int(text.replace(",", ""))


# ----------------------------------------------------------------------------------------------------------------
# 2001: the English report's "Detailed Official Results", read word by word with each word's box.

# The parties of 2001 as the report spells them in full. A party name whose last word runs into the printed symbol
# is matched by the longest of these that starts it.
PARTIES_2001 = (
    "Bangladesh Awami League",
    "Bangladesh Gono Azadi League (Samad)",
    "Bangladesh Hindu League",
    "Bangladesh Islami Front",
    "Bangladesh Janata Party",
    "Bangladesh Jatiya Party (N-F)",
    "Bangladesh Jatiya Tanti Dal",
    "Bangladesh Khelafat Andolon",
    "Bangladesh Krisak Sramik Janata Party",
    "Bangladesh Krisak Sramik Mukti Andolon",
    "Bangladesh Krishak Sramik Awami League",
    "Bangladesh Manabadhikar Dal (Bamad)",
    "Bangladesh Muslim League (Jamir Ali)",
    "Bangladesh Nationalist Party (BNP)",
    "Bangladesh Peoples Congress",
    "Bangladesh Peoples Party (BPP)",
    "Bangladesh Progressive Party (BPP)",
    "Bangladesh Samajtantrik Dal (BASAD-Khalekuzzaman)",
    "Bangladesh Samajtantrik Dal (Mahbub)",
    "Bangladesh Sammyabadi Dal (Marx-Lenin)",
    "Bangladesh Sarbahara Party",
    "Bangladesh Vasani Adarsha Bastabayan Parishad",
    "Bangladesh Workers Party",
    "Bangladesher Communist Party",
    "Bhasani Front",
    "Communist Kendra",
    "Democratic Republican Party",
    "Desh Prem Party",
    "Gono Forum",
    "Gonotantri Party",
    "Independent",
    "Islami Jatiya Oikya Front",
    "Islami Oikya Jote",
    "Islami Shasantantra Andolon",
    "Jaker Party",
    "Jamaat-e-Islami Bangladesh",
    "Jamiate Ulamae Islam Bangladesh",
    "Jatiya Janata Party (Hafizur)",
    "Jatiya Janata Party (Sheik Asad)",
    "Jatiya Party (Manju)",
    "Jatiya Samajtantrik Dal",
    "Jatiyo Janata Party (Adv. Nurul Islam Khan)",
    "Krisak Sramik Janata League",
    "Liberal Democrats Party",
    "Liberal Party Bangladesh",
    "National Awami Party (NAP)",
    "National Awami Party (NAP-Bhasani)",
    "National Awami Party (NAP-Vasani Mushtaq)",
    "National Patriotic Party",
    "Pragatishil Gonotantrik Shakti (Pragash)",
    "Quran Darshan Sangstha Bangladesh",
    "Quran and Sunnah Bastabayan Party",
    "Sama-Samaj Gonotantri Party",
    "Samridha Bangladesh Andolon",
    "Sramik Krishak Samajbadi Dal",
)
SQUASHED_2001 = sorted(((squash(p), p) for p in PARTIES_2001), key=lambda x: -len(x[0]))


def party_2001(text: str, symbol: str) -> str:
    """The party from the words in its column, where the first word of a symbol can run into the party's last."""
    for squashed in (squash(text), squash(f"{text} {symbol}")):
        for prefix, name in SQUASHED_2001:
            if squashed.startswith(prefix) and len(squashed) - len(prefix) <= 8:
                return name
    raise ValueError(f"2001: unknown party {text!r} with symbol {symbol!r}")


def run_together(word: str) -> tuple[str, str]:
    """Split a candidate's last word from the party name the report runs into it, as in "HossainJamaat-e-Islami"."""
    for i in range(1, len(word)):
        if word[i].isupper() and any(p.startswith(word[i:]) or word[i:].startswith(p.split()[0]) for p in PARTIES_2001):
            return word[:i], word[i:]
    raise ValueError(f"2001: cannot split {word!r} into a name and a party")


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


WORD = re.compile(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')


def pdf_path(url: str) -> str:
    return str(raw.cached(SOURCE, url).path)


def lines_2001() -> list[list[Word]]:
    first, last = GENERAL[2001].pages
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(first), "-l", str(last), pdf_path(REPORT_2001), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found: list[list[Word]] = []
    for page in out.split("<page")[1:]:
        words = [Word(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(page)]
        words.sort(key=lambda w: (w.y1, w.x0))
        rows: list[list[Word]] = []
        for w in words:
            if rows and abs(rows[-1][0].y1 - w.y1) < 4:
                rows[-1].append(w)
            else:
                rows.append([w])
        found.extend(sorted(row, key=lambda w: w.x0) for row in rows)
    return found


HEAD_2001 = re.compile(r"^(\d{1,3}) (.+?) Total Center: (\d+) Reported Centers: (\d+) Suspended Centers: (\d+)( \*)?$")
TOTAL_2001 = re.compile(
    r"Total Voters: ([\d,]+) Valid votes: ([\d,]+) Cancelled Votes: ([\d,]+) Turnout: ([\d,]+) % Turnout"
)
VOTES_2001 = re.compile(r"^[\d,]+$")
PERCENT = re.compile(r"^[\d.]+%$")


# The report's summary page notes that Cox's Bazar-3 polled on 1 November 2001.
LATE_2001 = {296: date(2001, 11, 1)}

# Constituency names the report prints cut short or in another form, by number.
NAMES_2001 = {132: "Barisal with Pirojpur", 160: "Mymensingh with Netrokona"}


@functools.cache
def seats_2001() -> list[Seat]:
    seats: list[Seat] = []
    columns: tuple[float, float] | None = None
    parts: list[list[list[str]]] = []  # per candidate: name words, party words, symbol words
    votes: list[int] = []

    def close() -> None:
        seat = seats[-1]
        for (name, party, symbol), count in zip(parts, votes, strict=True):
            label = party_2001(" ".join(party), " ".join(symbol))
            seat.candidates.append(Candidate(clean(" ".join(name)), label, count))
        parts.clear()
        votes.clear()

    for row in lines_2001():
        text = " ".join(w.text for w in row)
        if head := HEAD_2001.match(text):
            seat = Seat(int(head.group(1)), clean(head.group(2)), None)
            seat.name = NAMES_2001.get(seat.number, seat.name)
            seat.centers, seat.reported, seat.suspended = (int(head.group(i)) for i in (3, 4, 5))
            seat.day = LATE_2001.get(seat.number)
            seats.append(seat)
            continue
        if row[0].text == "Candidate":
            at = {w.text: w.x0 for w in row}
            columns = (at["Party"], at["Symbol"])
            continue
        if total := TOTAL_2001.search(text):
            close()
            seat = seats[-1]
            seat.registered, seat.valid, seat.invalid, seat.cast = (number(total.group(i)) for i in (1, 2, 3, 4))
            continue
        if not seats or columns is None or seats[-1].cast:
            continue
        if row[0].text in ("Detailed", "October") or re.fullmatch(r"\d+", text):
            continue
        party_x, symbol_x = columns
        starts = re.fullmatch(r"\d+\.", row[0].text) is not None and row[0].x0 < party_x
        if starts:
            if not (VOTES_2001.match(row[-2].text) and PERCENT.match(row[-1].text)):
                raise ValueError(f"2001 seat {seats[-1].number}: no votes on {text!r}")
            votes.append(number(row[-2].text))
            parts.append([[], [], []])
            row = row[1:-2]
        elif not parts:
            raise ValueError(f"2001 seat {seats[-1].number}: stray line {text!r}")
        for w in row:
            column = 0 if w.x0 < party_x - 6 else 1 if w.x0 < symbol_x - 6 else 2
            if column == 0 and w.x1 > party_x + 6:
                head, tail = run_together(w.text)
                parts[-1][0].append(head)
                parts[-1][1].append(tail)
                continue
            parts[-1][column].append(w.text)
    return seats


# ----------------------------------------------------------------------------------------------------------------
# 2008: the Bengali report's constituency-wise results, in the SutonnyMJ font, read from its laid-out text.

ROW_2008 = re.compile(r"^\s*(\d{1,3})\s+(\S+(?: \S+)?)\s{2,}(.*?)\s{2,}(.*?)\s{2,}(\S.*?)\s{2,}(\d+)\s+[\d.]+%\s*$")
NONE_2008 = re.compile(r"^\s*(\d{1,3})\s+(\S+(?: \S+)?)\s{2,}Dc‡ii Kvnv‡KI b‡n\s{2,}bv-[†‡]fvU\s+(\d+)\s+[\d.]+%\s*$")
# Words of the column headings that each page repeats.
HEADINGS_2008 = ("kZKiv", "Avm‡bi bvg", "†fv‡Ui", "Abycv‡Z", "AvmbwfwËK", "msm` wbe©vPb 2008", "cÖvß ˆea")
# The running text in each page's margin, which can fall between a row and its wrapped second line.
MARGIN_2008 = ("9g RvZxq msm` wbe©vPb", "cwimsL¨vb cÖwZ‡e`b")
TOTAL_2008 = re.compile(r"^\s*(\d{1,3})\s+(.*?)\s{2,}[†‡]gvU cÖvß [ˆ‰]ea\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+[\d.]+%\s*$")
LATE_2008 = "G Avm‡bi wbe©vPb 12 Rvbyqvix 2009 Zvwi‡L"

# The parties of 2008 by their Bengali names as the report prints them (hyphens closed up), with the English name
# of each. A party that also ran in 2001 takes the name the 2001 report printed, so the two elections share it.
PARTIES_2008 = {
    "বাংলাদেশ আওয়ামী লীগ": "Bangladesh Awami League",
    "বাংলাদেশ জাতীয়তাবাদী দল": "Bangladesh Nationalist Party (BNP)",
    "জাতীয় পার্টি": "Jatiya Party (Ershad)",
    "বাংলাদেশ জামায়াতে ইসলামী": "Jamaat-e-Islami Bangladesh",
    "ইসলামী আন্দোলন বাংলাদেশ": "Islami Andolan Bangladesh",
    "বিকল্প ধারা বাংলাদেশ": "Bikalpa Dhara Bangladesh",
    "বাংলাদেশ সমাজতান্ত্রিক দল": "Bangladesh Samajtantrik Dal",
    "কৃষক শ্রমিক জনতা লীগ": "Krisak Sramik Janata League",
    "গণ ফোরাম": "Gono Forum",
    "বাংলাদেশ কল্যাণ পার্টি": "Bangladesh Kalyan Party",
    "বাংলাদেশের কমিউনিস্ট পার্টি": "Bangladesher Communist Party",
    "জাকের পার্টি": "Jaker Party",
    "জাতীয় সমাজতান্ত্রিক দল-জেএসডি": "Jatiya Samajtantrik Dal-JSD",
    "জাতীয় সমাজতান্ত্রিক দল-জাসদ": "Jatiya Samajtantrik Dal-Jasad",
    "বাংলাদেশ খেলাফত আন্দোলন": "Bangladesh Khelafat Andolon",
    "বাংলাদেশ তরিকত ফেডারেশন": "Bangladesh Tarikat Federation",
    "ন্যাশনাল পিপলস পার্টি": "National Peoples Party",
    "প্রগতিশীল গণতান্ত্রিক দল": "Progressive Democratic Party",
    "বাংলাদেশ ইসলামী ফ্রন্ট": "Bangladesh Islami Front",
    "লিবারেল ডেমোক্রেটিক পার্টি-এলডিপি": "Liberal Democratic Party-LDP",
    "গণফ্রন্ট": "Gono Front",
    "বাংলাদেশ ন্যাশনাল আওয়ামী পার্টি": "Bangladesh National Awami Party",
    "বাংলাদেশ ন্যাপ": "Bangladesh NAP",
    "ঐক্যবদ্ধ নাগরিক আন্দোলন": "Oikyabaddha Nagorik Andolon",
    "বাংলাদেশ জাতীয় পার্টি": "Bangladesh Jatiya Party",
    "বাংলাদেশ জাতীয় পার্টি-বিজেপি": "Bangladesh Jatiya Party-BJP",
    "বাংলাদেশ খেলাফত মজলিস": "Bangladesh Khelafat Majlish",
    "জাতীয় পার্টি-জেপি": "Jatiya Party (Manju)",
    "জমিয়তে উলামায়ে ইসলাম বাংলাদেশ": "Jamiate Ulamae Islam Bangladesh",
    "বাংলাদেশের ওয়ার্কার্স পার্টি": "Bangladesh Workers Party",
    "গণতন্ত্রী পার্টি": "Gonotantri Party",
    "বাংলাদেশের বিপ্লবী ওয়ার্কার্স পার্টি": "Revolutionary Workers Party of Bangladesh",
    "বাংলাদেশ মুসলিম লীগ": "Bangladesh Muslim League",
    "ইসলামী ঐক্য জোট": "Islami Oikya Jote",
    "জাতীয় গণতান্ত্রিক পার্টি": "National Democratic Party",
    "ফ্রীডম পার্টি": "Freedom Party",
    "ইসলামিক ফ্রন্ট বাংলাদেশ": "Islamic Front Bangladesh",
    "বাংলাদেশের সাম্যবাদী দল (এম.এল)": "Bangladesh Sammyabadi Dal (Marx-Lenin)",
    "স্বতন্ত্র": "Independent",
}
PARTIES_2008 = {unicodedata.normalize("NFC", k): v for k, v in PARTIES_2008.items()}
BENGALI = {english: bengali for bengali, english in PARTIES_2008.items() if english != "Independent"}


def text_2008() -> list[str]:
    first, last = GENERAL[2008].pages
    out = subprocess.run(
        ["pdftotext", "-layout", "-f", str(first), "-l", str(last), pdf_path(REPORT_2008), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.splitlines()


def party_2008(text: str) -> str:
    bengali = re.sub(r"\s*-\s*", "-", unicode(text))
    if bengali not in PARTIES_2008:
        raise ValueError(f"2008: unknown party {bengali!r}")
    return PARTIES_2008[bengali]


@functools.cache
def seats_2008() -> list[Seat]:
    seats: dict[int, Seat] = {}
    offsets: tuple[int, int] = (0, 0)  # where the last row's candidate and party start
    last: Candidate | None = None
    for line in text_2008():
        if total := TOTAL_2008.match(line):
            seat = seats[int(total.group(1))]
            seat.valid, seat.invalid, seat.cast, seat.registered = (int(total.group(i)) for i in (3, 4, 5, 6))
            if LATE_2008 in total.group(2):
                seat.day = date(2009, 1, 12)
            last = None
            continue
        none = NONE_2008.match(line)
        row = None if none else ROW_2008.match(line)
        match = none or row
        if match:
            n = int(match.group(1))
            if n not in seats:
                place = unicode(clean(match.group(2)))
                seats[n] = Seat(n, "", place)
            if none:
                last = Candidate("", "", int(none.group(3)), none_of_above=True)
            else:
                assert row is not None
                last = Candidate(row.group(3), row.group(4), int(row.group(6)))
                offsets = (row.start(3), row.start(4))
            seats[n].candidates.append(last)
            continue
        stripped = line.strip()
        if "\f" in line or any(h in line for h in HEADINGS_2008):
            last = None
        if stripped in MARGIN_2008 or stripped.isdigit():
            continue
        if last is None or not stripped or last.none_of_above or stripped in ("†fvU", "‡fvU"):
            continue
        if re.match(r"^\d", stripped):
            raise ValueError(f"2008: unread row {line!r}")
        for segment in re.finditer(r"\S+(?: \S+)*", line):
            start = segment.start()
            if abs(start - offsets[1]) <= abs(start - offsets[0]):
                last.party = f"{last.party} {segment.group(0)}"
            else:
                last.name = f"{last.name} {segment.group(0)}"
    for seat in seats.values():
        for c in seat.candidates:
            if c.none_of_above:
                continue
            c.name = unicode(clean(c.name))
            if not c.party.strip():
                # a party name run into the candidate's with a single space
                for bengali in sorted(PARTIES_2008, key=len, reverse=True):
                    if f" {bengali} " in f" {c.name} ":
                        c.name = clean(f" {c.name} ".replace(f" {bengali} ", " "))
                        c.party = bengali
                        break
                c.party = party_2008(c.party or "?")
            else:
                c.party = party_2008(clean(c.party))
    return [seats[n] for n in sorted(seats)]


# ----------------------------------------------------------------------------------------------------------------


# The districts as the 2008 report names them in Bengali, with the spelling of the 2001 report, so that a
# constituency keeps one name across the two elections.
DISTRICTS = {
    "কক্সবাজার": "Coxs Bazar",
    "কিশোরগঞ্জ": "Kishoregonj",
    "কুমিল্লা": "Comilla",
    "কুষ্টিয়া": "Kushtia",
    "কুড়িগ্রাম": "Kurigram",
    "খুলনা": "Khulna",
    "গাইবান্ধা": "Gaibandha",
    "গাজীপুর": "Gazipur",
    "গোপালগঞ্জ": "Gopalgonj",
    "চট্টগ্রাম": "Chittagong",
    "চাঁদপুর": "Chandpur",
    "চাঁপাইনবাবগঞ্জ": "Nowabgonj",
    "চুয়াডাংগা": "Chuadanga",
    "জামালপুর": "Jamalpur",
    "জয়পুরহাট": "Joypurhat",
    "ঝালকাঠি": "Jhalokathi",
    "ঝিনাইদহ": "Jhenidha",
    "টাংগাইল": "Tangail",
    "ঠাকুরগাঁও": "Thakurgaon",
    "ঢাকা": "Dhaka",
    "দিনাজপুর": "Dinajpur",
    "নওগাঁ": "Noagoan",
    "নরসিংদী": "Narshingdi",
    "নাটোর": "Natore",
    "নারায়ণগঞ্জ": "Narayangonj",
    "নীলফামারী": "Nilphamari",
    "নেত্রকোনা": "Netrokona",
    "নোয়াখালী": "Noakhali",
    "নড়াইল": "Narail",
    "পঞ্চগড়": "Panchagarh",
    "পটুয়াখালী": "Patuakhali",
    "পাবনা": "Pabna",
    "পার্বত্য খাগড়াছড়ি": "Khagrachhari",
    "পার্বত্য বান্দরবান": "Bandarban",
    "পার্বত্য রাংগামাটি": "Rangamati",
    "পিরোজপুর": "Pirojpur",
    "ফরিদপুর": "Faridpur",
    "ফেনী": "Feni",
    "বগুড়া": "Bogra",
    "বরগুনা": "Barguna",
    "বরিশাল": "Barisal",
    "বাগেরহাট": "Bagerhat",
    "ব্রাহ্মণবাড়িয়া": "Brahmanbaria",
    "ভোলা": "Bhola",
    "মাগুরা": "Magura",
    "মাদারীপুর": "Madaripur",
    "মানিকগঞ্জ": "Manikgonj",
    "মুন্সীগঞ্জ": "Munshigonj",
    "মেহেরপুর": "Meherpur",
    "মৌলভীবাজার": "Moulavibazar",
    "ময়মনসিংহ": "Mymensingh",
    "যশোর": "Jessore",
    "রংপুর": "Rangpur",
    "রাজবাড়ী": "Rajbari",
    "রাজশাহী": "Rajshahi",
    "লক্ষ্মীপুর": "Lakshmipur",
    "লালমনিরহাট": "Lalmonirhat",
    "শরীয়তপুর": "Sariatpur",
    "শেরপুর": "Sherpur",
    "সাতক্ষীরা": "Satkhira",
    "সিরাজগঞ্জ": "Sirajgonj",
    "সিলেট": "Sylhet",
    "সুনামগঞ্জ": "Sunamgonj",
    "হবিগঞ্জ": "Hobigonj",
}
DISTRICTS = {unicodedata.normalize("NFC", k): v for k, v in DISTRICTS.items()}
DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def english(bengali: str) -> str:
    """The English name of a 2008 constituency, such as "Kurigram-1" for কুড়িগ্রাম-১."""
    district, _, n = bengali.translate(DIGITS).replace(" ", "").rpartition("-")
    if not district:
        district, n = n, ""
    for name, spelled in DISTRICTS.items():
        if name.replace(" ", "") == district:
            return f"{spelled}-{int(n)}" if n else spelled
    raise ValueError(f"2008: unknown district in {bengali!r}")


def seats(year: int) -> list[Seat]:
    found = seats_2001() if year == 2001 else seats_2008()
    for seat in found:
        if seat.name_bn and not seat.name:
            seat.name = english(seat.name_bn)
    spec = GENERAL[year]
    if [s.number for s in found] != list(range(1, spec.seats + 1)):
        raise ValueError(f"{year}: the report does not list constituencies 1 to {spec.seats} once each")
    for seat in found:
        total = sum(c.votes for c in seat.candidates)
        if total != seat.valid:
            raise ValueError(f"{year} seat {seat.number}: candidates sum to {total}, the report prints {seat.valid}")
        if seat.valid + seat.invalid != seat.cast:
            raise ValueError(f"{year} seat {seat.number}: valid and cancelled votes do not add to the turnout")
    return found


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{spec.day}" for spec in GENERAL.values())]


def fetch() -> None:
    for url in (REPORT_2001, REPORT_2008):
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    geo().write()
    registry().write()
    for year in GENERAL:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=ECS,
        kind="official_body",
        publisher="Bangladesh Election Commission",
        title="Statistical reports of the Jatiya Sangsad general elections",
        url=HOME,
        license="No license stated; published by the electoral authority for public use",
        attribution="Bangladesh Election Commission",
    )
    for spec in GENERAL.values():
        p.artifact(raw.cached(SOURCE, spec.url), ECS)


def area(name: str) -> str:
    return key("area", SCHEME, changeset.slug(name))


def constituencies() -> dict[str, tuple[str, str | None]]:
    """Each constituency by its slug: its English name and its Bengali name, where a report prints one."""
    found: dict[str, tuple[str, str | None]] = {}
    for year in GENERAL:
        for seat in seats(year):
            code = changeset.slug(seat.name)
            known = found.get(code, (seat.name, None))
            found[code] = (known[0], seat.name_bn or known[1])
    return dict(sorted(found.items()))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bangladesh's Jatiya Sangsad constituencies as the Election Commission's statistical reports of 2001 and "
        "2008 name them",
    )
    p.external |= {BANGLADESH, COUNTRY}
    _source(p)
    p.add("scheme", code=SCHEME, polity_id=BANGLADESH, kind="electoral_district", rank=1, noun="constituency")
    for code, (name, bengali) in constituencies().items():
        id = area(name)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
        if bengali:
            p.name(id, bengali, "bn", "official")
    return p


def party(name: str) -> str:
    return key("party", name.casefold())


def used_parties() -> list[str]:
    found = {c.party for year in GENERAL for seat in seats(year) for c in seat.candidates if not c.none_of_above}
    return sorted(found - {"Independent"})


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bangladesh's parties of the 2001 and 2008 general elections, the Jatiya Sangsad, and its electoral system",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BANGLADESH)
    p.add("body", id=SANGSAD, polity_id=BANGLADESH, kind="unicameral")
    p.name(SANGSAD, "Jatiya Sangsad", "en", "official")
    p.name(SANGSAD, "জাতীয় সংসদ", "bn", "official")
    p.add("electoral_system", id=FPTP, polity_id=BANGLADESH, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Each of the 300 territorial constituencies elects one member by plurality. The seats reserved for "
        "women are filled by the members elected and are not part of the constituency results stored here.",
        lang="en",
    )
    for name in used_parties():
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=BANGLADESH)
        p.name(id, name, "en", "official")
        if name in BENGALI:
            p.name(id, BENGALI[name], "bn", "official")
    return p


METHOD = {
    2001: (
        'Counted by the Bangladesh Election Commission for each constituency, as the "Detailed Official Results" '
        "of its statistical report on the 2001 election print them. Registered voters are the whole roll of the "
        "constituency; where polling was adjourned at some centres and the Commission declared the result decisive "
        "without them, the votes are those of the centres that reported. Ballots cast are the valid and cancelled "
        "votes together. The candidate with the most votes is stored as elected. Constituencies are matched "
        "across elections by name, and the Commission redrew them in 2008."
    ),
    2008: (
        "Counted by the Bangladesh Election Commission for each constituency, as the constituency-wise results of "
        "its statistical report on the 2008 election print them. Valid votes include the votes for none of the "
        'candidates ("না ভোট"), stored as their own option. Ballots cast are the valid and rejected votes '
        "together, and registered voters are the roll. The candidate with the most votes is stored as elected. "
        "Constituencies are matched across elections by name, and the Commission redrew them in 2008."
    ),
}
LATE_NOTE = "This constituency polled on {day:%-d %B %Y}, after the general election."


def election(year: int) -> Package:
    spec = GENERAL[year]
    day = spec.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} general election to Bangladesh's {ORDINAL[spec.number]} Jatiya Sangsad by "
        "constituency, from the Election Commission's statistical report",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BANGLADESH}
    p.uses(raw.cached(SOURCE, spec.url))
    id = key("election", day)
    path = f"bd/{day}"
    p.add("election", id=id, polity_id=BANGLADESH, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{year} Bangladeshi general election", "en", "official")
    lang = "en" if year == 2001 else "bn"
    for seat in seats(year):
        tag = f"{day}:{seat.number}"
        district = area(seat.name)
        unit = key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BANGLADESH,
            scheme_code=SCHEME,
            code=tag,
            area_relation="equals",
            area_id=district,
            kind="standard",
        )
        p.name(unit, f"{seat.number} {seat.name}", "en", "official")
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="body_seats",
            body_id=SANGSAD,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Jatiya Sangsad election, {seat.name}", "en", "official")
        p.add("slug", path=f"{path}/{seat.number}-{changeset.slug(seat.name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECS,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = METHOD[year]
        if seat.day:
            method = f"{method} {LATE_NOTE.format(day=seat.day)}"
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        real = [c for c in seat.candidates if not c.none_of_above]
        ranked = sorted((c.votes for c in real), reverse=True)
        for index, c in enumerate(seat.candidates, start=1):
            option = key("option", tag, index)
            if c.none_of_above:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="none_of_the_above",
                    ballot_position=index,
                    ballot_label="না ভোট",
                )
                put(p, result_set, unit, option, VOTES, c.votes)
                continue
            solo = c.party == "Independent"
            person = key("person", tag, index)
            p.add("person", id=person)
            p.name(person, c.name, lang, "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if solo else party(c.party),
                ballot_position=index,
                ballot_label=c.name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", tag, index),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=None if solo else party(c.party),
                is_independent=solo,
            )
            if c.votes == ranked[0] and (len(ranked) < 2 or ranked[0] > ranked[1]):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            put(p, result_set, unit, option, VOTES, c.votes)
        for measure, value in (
            (REGISTERED, seat.registered),
            (CAST, seat.cast),
            (INVALID, seat.invalid),
            (VALID, seat.valid),
        ):
            put(p, result_set, unit, None, measure, value)
    return p


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
