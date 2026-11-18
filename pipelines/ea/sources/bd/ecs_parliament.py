"""Bangladesh Election Commission: Jatiya Sangsad general elections by constituency, from its statistical reports."""

import functools
import html
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.bd import report1996
from ea.sources.bd.bijoy import unicode

SOURCE = "bd.ecs_parliament"
BANGLADESH = str(uuid5(package.NAMESPACE, "polity\x1fBD"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBGD"))
HOME = "https://www.ecs.gov.bd/"
ASSET = "https://asset.ecs.gov.bd/media/uploaded_files/notices/2021/09/"
SCHEME = "bd.constituency"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

# The Commission's statistical reports of each general election, posted in its Election Report category.
REPORT_1996 = ASSET + "VCgSSqUcjDR8tI1q8QbNACSe96YNm2EYTISFFKtl.pdf"
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
    1996: General(date(1996, 6, 12), 7, REPORT_1996, (report1996.FIRST, report1996.LAST), 300),
    2001: General(date(2001, 10, 1), 8, REPORT_2001, (208, 324), 300),
    2008: General(date(2008, 12, 29), 9, REPORT_2008, (159, 309), 300),
}
ORDINAL = {7: "7th", 8: "8th", 9: "9th"}


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
# 1996: the English scan's chapter 6, read by OCR in report1996 and reconciled against its printed shares and totals.

# The parties of 1996 as the report's list of participating parties (3.01) spells them, each with its symbol, and
# the name the 2001 or 2008 report gives the same party, where it ran again, so the elections share one party.
PARTIES_1996: tuple[tuple[str, str, str | None], ...] = (
    ("Bangladesh Awami League", "Nowka (Boat)", None),
    ("Bangladesh Bastuhara Parishad", "Truck", None),
    ("Bangladesh Bekar Samaj", "Thelagari", None),
    ("Bangladesh Gono Azadi League", "Dhenki", "Bangladesh Gono Azadi League (Samad)"),
    ("Bangladesh Hindu League", "Shankha (Conchshel)", None),
    ("Bangladesh Islami Biplobi Parishad", "Tarabari (Sword)", None),
    ("Bangladesh Islami Front", "Mombati (Candle)", None),
    ("Bangladesh Islami Party", "Kolsi (Pitcher)", None),
    ("Bangladesh Janata Party", "Bus", None),
    ("Bangladesh Jatiya Agragati Party", "Tiffin Carrier", None),
    ("Bangladesh Jatiya League (Sobhan)", "Latim (Top)", None),
    ("Bangladesh Jatiyabadi Awami League (Mostofa Allama)", "Nolkup (Tubewell)", None),
    ("Bangladesh Khelafat Andolon", "Bot Gachh (Banyon Tree)", None),
    ("Bangladesh Krisak Sramik Janata Party", "Boi (Book)", None),
    ("Bangladesh Krisak Sramik Mukti Andolon", "Morog (Cock)", None),
    ("Bangladesh Krishak Raj Islami Party (F.Hq. Sardar)", "Glass", None),
    ("Bangladesh Manabodhikar Dal", "Anaros (Pineapple)", "Bangladesh Manabadhikar Dal (Bamad)"),
    ("Bangladesh Mehanati Front", "Helicopter", None),
    ("Bangladesh Muslim League (Jamir Ali)", "Hariken (Lantern)", None),
    ("Bangladesh National Awami Parti (Nap Vasani)", "Chari (Walking Stick)", "National Awami Party (NAP-Bhasani)"),
    ("Bangladesh National Awami Party (NAP)", "Kureh Ghar (Hut)", "National Awami Party (NAP)"),
    ("Bangladesh National Congress", "Ghora (Horse)", None),
    ("Bangladesh Nationalist Party", "Dhaner Shish (Sheaf Of Paddy)", "Bangladesh Nationalist Party (BNP)"),
    ("Bangladesh People's League", "Hati (Elephant)", None),
    ("Bangladesh Peoples Party", "Rocket", "Bangladesh Peoples Party (BPP)"),
    ("Bangladesh Samajtantrik Dal (Mahbub)", "Rickshaw", None),
    ("Bangladesh Samajtantrik Samsad (Darshan Shava)", "Aam (Mango)", None),
    ("Bangladesh Samyabadi Dal (Marx-Lenin)", "Chair", "Bangladesh Sammyabadi Dal (Marx-Lenin)"),
    ("Bangladesh Sarbahara Party", "Chaka (Wheel)", None),
    ("Bangladesh Tafsil Jati Federation (S.K. Mandal)", "Kanthal (Jack Fruit)", None),
    ("Bangladesh Tafsili Federation (Sudir)", "Palki", None),
    ("Bangladesh Tanjimul Muslimin", "Phuler Mala (Garland of Flowers)", None),
    ("Bangladesh Vasani Adarsha Bastabayan Parishad", "Hansh (Duck)", None),
    ("Bangladesh Workers Party", "Haturi (Hammer)", None),
    ("Bangladesher Communist Party", "Kaste", None),
    (
        "Bangladesher Samajtantrik Dal (Khalekuzzaman)",
        "Tala (Lock)",
        "Bangladesh Samajtantrik Dal (BASAD-Khalekuzzaman)",
    ),
    ("Bhasani Front", "Pradip (Lamp)", None),
    ("Communist Kendra", "Chabi (Key)", None),
    ("Democratic Republican Party", "Moiyur (Peacock)", None),
    ("Desh Prem Party", "Ektara", None),
    ("Freedom Party", "Tara (Star)", None),
    ("Gonatantry Party", "Paira (Pegion)", "Gonotantri Party"),
    ("Gono Forum", "Udiyaman Surjo (Rising Sun)", None),
    ("Gono Oikkya Front (Guff)", "Baloon", None),
    ("Gontantrik Sarbahara Party", "Baitha", None),
    ("Hak Kathar Mancha", "Chingri Machh (Lobster)", None),
    ("Islami Al Zihad Dal", "Kolar Chhari (Row of Bananas)", None),
    ("Islami Oikya Jote", "Minar", None),
    ("Islami Shasantantra Andolon", "Minar1", None),
    ("Islamic Dal Bangladesh (Saifur)", "Tractor", None),
    ("Jaker Party", "Golap Phul (Rose)", None),
    ("Jamat-E-Islami Bangladesh", "Daripalla (Scale)", "Jamaat-e-Islami Bangladesh"),
    ("Jamiate Ulumaye Islam Bangladesh", "Khejur Gachh (Date Tree)", "Jamiate Ulamae Islam Bangladesh"),
    ("Jana Dal", "Balti (Bucket)", None),
    ("Jatiya Biplobi Front", "Ghuri (Kite)", None),
    ("Jatiya Daridra Party", "Thala (Plate)", None),
    ("Jatiya Janata Party (Nurul Islam)", "Moi (Ladder)", None),
    ("Jatiya Janata Party (Sheikh Asad)", "Horin (Deer)", "Jatiya Janata Party (Sheik Asad)"),
    ("Jatiya Party", "Langol (Plough)", "Jatiya Party (Ershad)"),
    ("Jatiya Samajtantrik Dal (Inu)", "Moshal (Flaming Torch)", None),
    ("Jatiya Samajtantrik Dal (Mahiuddin)", "Battery", None),
    ("Jatiya Samajtantrik Dal (Rab)", "Charka (Spining Wheel)", None),
    ("Jatiya Seba Dal", "Dab (Coconut)", None),
    ("National Awami Party (NAP Bhashani) (Mustaq)", "Oot (Camel)", "National Awami Party (NAP-Vasani Mushtaq)"),
    ("National Democratic Party", "Bagh (Tiger)", None),
    ("National Patriotic Party", "Urajahaj (Aeroplane)", None),
    ("Oikya Prokria", "Shighho (Lion)", None),
    ("People's Muslim League", "Suitcase", None),
    ("Progatishil Gonotantrik Shakti", "Kodal (Spade)", "Pragatishil Gonotantrik Shakti (Pragash)"),
    ("Progotisil Jatiatabadi Dal (Nurul A Moula)", "Chand (Moon)", None),
    ("Quran Sunna Bastabayan Party", "Apple", "Quran and Sunnah Bastabayan Party"),
    ("Quran Dorshion Sangshta Bangladesh", "Anchor", "Quran Darshan Sangstha Bangladesh"),
    ("Saat Dalya Jote (Mirpur)", "Mathal", None),
    ("Sammilita Sangram Parishad", "Table Ghori (Table Clock)", None),
    ("Samridhya Bangladesh Andolon", "Machh (Fish)", "Samridha Bangladesh Andolon"),
    ("Samridhya Bangladesh Babosai Samproday", "Chamoch (Spoon)", None),
    ("Social Democratic Party", "Magh (Mug)", None),
    ("Sramajibi Oikya Foram", "Telephone", None),
    ("Sramik Krishak Samajbadi Dal", "Chhata (Umbrella)", None),
    ("Taherikay Olama-E-Bangladesh", "Table", None),
    ("United People's Party", "Garur Gari (Bullock Cart)", None),
)


def partial(text: str, name: str) -> float:
    """How well text matches a name, allowing the stray marks OCR reads before or after it."""
    best = report1996.similar(text, name)
    for start in range(max(len(text) - len(name), 0) + 1):
        best = max(best, report1996.similar(text[start : start + len(name)], name))
    for cut in (1, 2, 3):
        best = max(best, report1996.similar(text[cut:], name))
    # The column cuts long names short, so a short reading matches the start of the name.
    if 15 <= len(text) < len(name) and len(name) >= 25:
        best = max(best, report1996.similar(text, name[: len(text)]))
    return best


def party_1996(text: str, symbol: str) -> str | None:
    """The party of a row, from its printed party and symbol as OCR reads them, or None when the reading is too
    damaged to tell. Each party's symbol is its own, so the symbol settles a party name the scan has damaged; a
    candidate with no party's symbol is independent."""
    party, sign = squash(text), squash(symbol)
    both = squash(f"{text} {symbol}")
    if "ndepend" in party or "depend" in party or report1996.similar(party, "independent") >= 0.7:
        return "Independent"
    # A legible party name settles the row; two parties' symbols both print as "Minar".
    if len(party) >= 6:
        ranked = sorted(((partial(party, squash(name)), name) for name, _, _ in PARTIES_1996), reverse=True)
        named = ranked[0] if ranked[0][0] - ranked[1][0] >= 0.05 else (0.0, "")
        signed = max((report1996.similar(sign, squash(mark)), name, mark) for name, mark, _ in PARTIES_1996)
        clear_sign = signed[0] >= 0.85 and squash(signed[2]) != "minar"
        if named[0] >= 0.8 and not (clear_sign and signed[1] != named[1]):
            return named[1]
        # The report prints a few rows whose party and symbol disagree; a name read near-exactly is what it prints,
        # and a damaged or truncated name yields to a clear symbol.
        if clear_sign and signed[1] != named[1]:
            return named[1] if named[0] >= 0.92 else signed[1]
    best, found = 0.0, None
    for name, printed_symbol, _ in PARTIES_1996:
        name_s, symbol_s = squash(name), squash(printed_symbol)
        by_name = partial(party, name_s) if len(party) >= 4 else None
        by_sign = report1996.similar(sign, symbol_s) if len(sign) >= 3 else None
        if by_name is not None and by_sign is not None:
            score = max((by_name + by_sign) / 2, report1996.similar(both, name_s + symbol_s))
        else:
            score = max(by_name or 0.0, by_sign or 0.0, report1996.similar(both, name_s + symbol_s))
        if score > best:
            best, found = score, name
    if found is None or best < 0.68:
        return None
    return found


CANONICAL_1996 = {name: later or name for name, _, later in PARTIES_1996} | {"Independent": "Independent"}
# Words of a name that the scan's rules turn into stray marks, and the prefix it misreads most often.
JUNK = re.compile(r"^[^A-Za-z]*$")
TITLES = {"Ma.": "Md.", "Ma,": "Md.", "Md,": "Md.", "Mad.": "Md.", "Mo.": "Md.", "Ma": "Md.", "Id.": "Md."}


def name_1996(text: str) -> str:
    """A candidate's name from the OCR of its cell, without the marks that the cell's rules leave around it."""
    words = []
    for word in text.split():
        word = word.strip("|[]{}()‘’'\"“”_—-~;:,°©=*!")
        if not word or JUNK.match(word):
            continue
        if len(word) > 2 and word[0] in "Ijli|\\" and word[1].isupper():
            word = word[1:]
        words.append(word)
    while words and len(words[-1]) <= 2 and words[-1].islower():
        words.pop()
    while words and len(words[0]) == 1 and words[0].islower():
        words.pop(0)
    if words:
        words[0] = TITLES.get(words[0], words[0])
    # Restore the bracket around a nickname, as in "Golam Mohammad (Kader)", where it survives on one side.
    out = " ".join(words)
    out = re.sub(r"\(([^)]*)$", r"(\1)", out)
    return clean(out)


def overrides_1996() -> dict[int, dict[str, int]]:
    """The cells read by eye, by constituency: "row1" and so on for the candidates in printed order, and the totals."""
    if not report1996.OVERRIDES.exists():
        return {}
    found: dict[int, dict[str, int]] = {}
    for number, entry in json.loads(report1996.OVERRIDES.read_text()).items():
        cells = {f"row{i}": int(v) for i, v in enumerate(entry.get("votes", []), start=1)}
        cells |= {k: int(entry[k]) for k in ("registered", "valid", "invalid", "cast") if k in entry}
        found[int(number)] = cells
    return found


def candidates_1996() -> dict[int, list[tuple[str, str, int]]]:
    """The tables read by eye in full, where the page's rows could not be told apart by machine."""
    if not report1996.OVERRIDES.exists():
        return {}
    entries = json.loads(report1996.OVERRIDES.read_text())
    return {int(n): [(a, b, int(c)) for a, b, c in e["candidates"]] for n, e in entries.items() if "candidates" in e}


def names_1996() -> dict[int, dict[int, str]]:
    """The candidates' names read by eye, by constituency and row, where OCR reads a name cell as blank."""
    if not report1996.OVERRIDES.exists():
        return {}
    entries = json.loads(report1996.OVERRIDES.read_text())
    return {int(n): {int(i): v for i, v in e.get("names", {}).items()} for n, e in entries.items()}


def parties_1996() -> dict[int, dict[int, str]]:
    """The parties read by eye, by constituency and row, where the scan leaves a row's party and symbol illegible."""
    if not report1996.OVERRIDES.exists():
        return {}
    entries = json.loads(report1996.OVERRIDES.read_text())
    return {int(n): {int(i): p for i, p in e.get("parties", {}).items()} for n, e in entries.items()}


@functools.cache
def tables_1996() -> dict[int, tuple[report1996.Table, report1996.Settled]]:
    pdf = raw.cached(SOURCE, REPORT_1996).path
    fixes = overrides_1996()
    found: dict[int, tuple[report1996.Table, report1996.Settled]] = {}
    listed = candidates_1996()
    for table in report1996.tables(pdf):
        if table.number in found:
            raise ValueError(f"1996: constituency {table.number} appears twice")
        cells = fixes.get(table.number, {})
        if table.number in listed:
            votes = [v for _, _, v in listed[table.number]]
            totals = [cells[k] for k in ("registered", "valid", "invalid", "cast")]
            problems = (
                [] if sum(votes) == totals[1] and totals[1] + totals[2] == totals[3] else ["listed votes do not add"]
            )
            found[table.number] = (table, report1996.Settled(votes, *totals, problems))
        else:
            found[table.number] = (table, report1996.settle(table, cells))
    return found


def party_of(row: report1996.Row) -> str | None:
    """A row's party, reading its party and symbol cells again on their own when the page's OCR missed them."""
    found = party_1996(row.party, row.symbol)
    if found is None and len(row.spans) == 3:
        pdf = raw.cached(SOURCE, REPORT_1996).path
        party, symbol = (report1996.text(pdf, row.page, box) for box in row.spans[1:])
        row.party, row.symbol = party, symbol
        found = party_1996(party, symbol)
    return found


@functools.cache
def seats_1996() -> list[Seat]:
    names = {seat.number: seat.name for seat in seats_2001()}
    seats: list[Seat] = []
    problems: list[str] = []
    for number, (table, settled) in sorted(tables_1996().items()):
        problems.extend(f"{number} {table.name}: {problem}" for problem in settled.problems)
        seat = Seat(number, names[number], None)
        seat.registered, seat.valid, seat.invalid, seat.cast = (
            settled.registered,
            settled.valid,
            settled.invalid,
            settled.cast,
        )
        if number in candidates_1996():
            for name, party_name, votes in candidates_1996()[number]:
                seat.candidates.append(Candidate(name, CANONICAL_1996[party_name], votes))
            seats.append(seat)
            continue
        named = parties_1996().get(number, {})
        for index, (row, votes) in enumerate(zip(table.rows, settled.votes, strict=True), start=1):
            found = named.get(index) or party_of(row)
            if found is None:
                problems.append(f"{number} {table.name}: no party for {row.party!r} with symbol {row.symbol!r}")
                continue
            name = names_1996().get(number, {}).get(index) or name_1996(row.name)
            if len(re.sub(r"[^A-Za-z]", "", name)) < 5 and row.spans:
                pdf = raw.cached(SOURCE, REPORT_1996).path
                name = name_1996(report1996.text(pdf, row.page, row.spans[0])) or name
            if not name:
                problems.append(f"{number} {table.name}: no name in row {index}")
            seat.candidates.append(Candidate(name, CANONICAL_1996[found], votes))
        seats.append(seat)
    if problems:
        raise ValueError("1996: tables that do not reconcile:\n" + "\n".join(problems))
    return seats


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
    found = {1996: seats_1996, 2001: seats_2001, 2008: seats_2008}[year]()
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
    for url in (REPORT_1996, REPORT_2001, REPORT_2008):
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
        "Bangladesh's parties of the 1996, 2001, and 2008 general elections, the Jatiya Sangsad, and its electoral system",
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
    1996: (
        "Counted by the Bangladesh Election Commission for each constituency, as chapter 6 of its statistical "
        'report on the 1996 election ("Detailed Results as Described in the Result Sheet furnished by the Returning '
        'Officer") prints them. The report is a scan, so every number was read by OCR, cell by cell, and kept only '
        "where the candidates' votes add to the printed valid votes, each candidate's votes give the printed share "
        "of them, the valid and cancelled votes add to the printed turnout, and the turnout gives the printed "
        "percentage of the registered voters. Candidates' names were read by OCR from the same scan and may carry "
        "its misreadings; each party is identified by its printed symbol, which the Commission allots to one party "
        "only. The candidate with the most votes is stored as elected. Constituencies are matched to the 2001 "
        "report's by number, under the same delimitation."
    ),
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
FOOTNOTES_1996 = {
    "decisive": "Polling was suspended at some centres here, and the Commission declared the result decisive without "
    "them; the votes are those of the centres that reported.",
    "repoll": "Polling was suspended at some centres here and held again there on 19 June 1996; the votes include "
    "the repoll.",
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
    # Two general elections were held in 1996, on 15 February and 12 June, so the June one carries its month.
    p.name(id, f"{'June ' if year == 1996 else ''}{year} Bangladeshi general election", "en", "official")
    lang = "bn" if year == 2008 else "en"
    scanned = year == 1996
    if scanned:
        stored = raw.cached(SOURCE, spec.url)
        artifact = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{ECS}\x1f{stored.storage_key}"))
        tables = tables_1996()
        pages: dict[int, str] = {}
        for number in sorted(
            {t.page for t, _ in tables.values()} | {r.page for t, _ in tables.values() for r in t.rows}
        ):
            pages[number] = key("page", day, number)
            p.add(
                "source_page",
                id=pages[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
                width=PAGE_WIDTH,
                height=PAGE_HEIGHT,
            )
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
        p.name(
            contest, f"{'June ' if year == 1996 else ''}{year} Jatiya Sangsad election, {seat.name}", "en", "official"
        )
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
        if scanned and (footnote := tables[seat.number][0].footnote):
            method = f"{method} {FOOTNOTES_1996[footnote]}"
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
            put(p, result_set, unit, option, VOTES, c.votes, scanned)
            rows = tables[seat.number][0].rows if scanned else []
            if scanned and len(rows) == len(seat.candidates):
                row = rows[index - 1]
                locate(p, result_set, unit, option, VOTES, pages[row.page], row.box)
        for measure, value in (
            (REGISTERED, seat.registered),
            (CAST, seat.cast),
            (INVALID, seat.invalid),
            (VALID, seat.valid),
        ):
            put(p, result_set, unit, None, measure, value, scanned)
            if scanned:
                spot = tables[seat.number][0].boxes.get(MEASURES[measure])
                if spot is not None:
                    locate(p, result_set, unit, None, measure, pages[spot[0]], spot[1])
    return p


# The size of a page of the 1996 scan at 100 dpi, the scale its page images are kept at, and the 300 dpi the cells
# were read at.
PAGE_WIDTH, PAGE_HEIGHT = 827, 1170
SCAN_WIDTH, SCAN_HEIGHT = 2480, 3509
MEASURES = {REGISTERED: "registered", CAST: "cast", INVALID: "invalid", VALID: "valid"}
DIGITIZED = 8


def locate(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, page: str, box: report1996.Box
) -> None:
    x0, y0, x1, y1 = box
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=page,
        source_id=None,
        x0=round(max(x0, 0) / SCAN_WIDTH, 4),
        y0=round(max(y0, 0) / SCAN_HEIGHT, 4),
        x1=round(min(x1, SCAN_WIDTH) / SCAN_WIDTH, 4),
        y1=round(min(y1, SCAN_HEIGHT) / SCAN_HEIGHT, 4),
    )


def put(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int, scanned: bool = False
) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=DIGITIZED if scanned else 0,
        value=value,
    )
