"""The Bangladesh Election Commission's report on the 27 February 1991 general election to the 5th Jatiya Sangsad: a
Bengali scan with no text layer. Its appendix 47 prints each constituency's candidates, parties, and votes, with the
valid, cancelled, and tendered ballots beside them, and an earlier appendix prints each constituency's voters. Both
were read by eye from the page images into `report1991.json`, each figure checked against the printed sums: the
candidates' votes against the column's sum and the valid votes, and the ballots against the printed total. Where
the report's own legible figures disagree, they are kept as printed and the result set says so. The constituencies
whose figures the scan leaves unreadable are listed in the file with the reason and left out. This module reads the
file, names each party in English, and finds each vote on its page for the value's locator."""

import functools
import itertools
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

import numpy as np
from PIL import Image
from scipy import ndimage

from ea.store import DATA

TRANSCRIPT = Path(__file__).with_name("report1991.json")
# The PDF pages of appendix 47, "the candidates' names and the votes they received".
FIRST, LAST = 352, 464
DPI = 100
CACHE = DATA / "scan" / "bd.ecs_parliament" / "1991"

type Box = tuple[int, int, int, int]

# The report's table of parties (section 5 of chapter 19, PDF pages 50 to 52): each party as it prints the name,
# the English name the archive gives it, and the candidates, valid votes, and seats it prints for the party. A party
# that also ran in a later election takes the English name stored for it there.
PARTIES: tuple[tuple[str, str, int, int, int], ...] = (
    ("বাংলাদেশ জাতীয়তাবাদী দল (বি. এন. পি.)", "Bangladesh Nationalist Party (BNP)", 300, 10507549, 140),
    ("বাংলাদেশ আওয়ামী লীগ", "Bangladesh Awami League", 264, 10259866, 88),
    ("জাতীয় পার্টি", "Jatiya Party (Ershad)", 272, 4063537, 35),
    ("জামায়াতে ইসলামী বাংলাদেশ", "Jamaat-e-Islami Bangladesh", 222, 4136661, 18),
    ("বাংলাদেশ কৃষক-শ্রমিক আওয়ামী লীগ (বাকশাল)", "Bangladesh Krishak Sramik Awami League", 68, 616014, 5),
    ("জাকের পার্টি", "Jaker Party", 251, 417737, 0),
    ("বাংলাদেশ কমিউনিষ্ট পার্টি (সি. পি. বি)", "Bangladesher Communist Party", 49, 407515, 5),
    ("জাতীয় সমাজতান্ত্রিক দল জাসদ (রব)", "Jatiya Samajtantrik Dal (Rab)", 161, 269451, 0),
    ("ইসলামী ঐক্যজোট", "Islami Oikya Jote", 59, 269434, 1),
    ("বাংলাদেশ ন্যাশনাল আওয়ামী পার্টি ন্যাপ (মোজাফফর)", "National Awami Party (NAP)", 31, 259978, 1),
    ("জাতীয় সমাজতান্ত্রিক দল জাসদ (ইনু)", "Jatiya Samajtantrik Dal (Inu)", 68, 171011, 0),
    ("গণতন্ত্রী পার্টি", "Gonotantri Party", 16, 152592, 1),
    ("ন্যাশনাল ডেমোক্রেটিক পার্টি (এন, ডি, পি)", "National Democratic Party", 20, 121918, 1),
    ("বাংলাদেশ জনতা দল", "Bangladesh Janata Dal", 50, 120729, 0),
    ("বাংলাদেশের ইউনাইটেড কমিউনিষ্ট লীগ", "Bangladesher United Communist League", 26, 110517, 0),
    ("বাংলাদেশ খেলাফত আন্দোলন", "Bangladesh Khelafat Andolon", 43, 93049, 0),
    ("ফ্রীডম পার্টি", "Freedom Party", 65, 90781, 0),
    ("জাতীয় সমাজতান্ত্রিক দল জাসদ (শাজাহান সিরাজ)", "Jatiya Samajtantrik Dal (Shahjahan Siraj)", 31, 84276, 1),
    ("বাংলাদেশ মুসলিম লীগ (আয়েন উদ্দীন)", "Bangladesh Muslim League (Ayen Uddin)", 6, 66575, 0),
    ("বাংলাদেশ ওয়ার্কার্স পার্টি", "Bangladesh Workers Party", 35, 63434, 1),
    (
        "বাংলাদেশের সমাজতান্ত্রিক দল বাসদ (খালেকুজ্জামান)",
        "Bangladesh Samajtantrik Dal (BASAD-Khalekuzzaman)",
        13,
        34868,
        0,
    ),
    ("বাংলাদেশ মুসলিম লীগ (কাদের)", "Bangladesh Muslim League (Kader)", 62, 32693, 0),
    ("জনতা মুক্তি পার্টি", "Janata Mukti Party", 8, 30962, 0),
    ("জাতীয় গণতান্ত্রিক পার্টি (জাগপা)", "Jatiya Gonotantrik Party (JAGPA)", 16, 24761, 0),
    ("বাংলাদেশ ইসলামী ফ্রন্ট", "Bangladesh Islami Front", 13, 24310, 0),
    ("জাতীয় ঐক্য ফ্রন্ট", "Jatiya Oikya Front", 15, 21624, 0),
    (
        "জাতীয় জনতা পার্টি এবং গণতান্ত্রিক ঐক্যজোট",
        "Jatiya Janata Party and Gonotantrik Oikya Jote",
        11,
        20568,
        0,
    ),
    ("জামায়াতে ওলামায়ে ইসলামী ফ্রন্ট", "Jamiate Ulamae Islami Front", 3, 15073, 0),
    ("বাংলাদেশের সমাজতান্ত্রিক দল বাসদ (মাহবুব)", "Bangladesh Samajtantrik Dal (Mahbub)", 6, 13413, 0),
    ("বাংলাদেশ হিন্দু লীগ", "Bangladesh Hindu League", 6, 11941, 0),
    ("বাংলাদেশের সাম্যবাদী দল (এম, এল)", "Bangladesh Sammyabadi Dal (Marx-Lenin)", 4, 11275, 0),
    ("ঐক্য প্রক্রিয়া", "Oikya Prokria", 2, 11074, 0),
    ("বাংলাদেশ মুসলিম লীগ (মতিন)", "Bangladesh Muslim League (Matin)", 6, 11013, 0),
    ("বাংলাদেশ ন্যাশনাল আওয়ামী পার্টি-ন্যাপ (ভাসানী)", "National Awami Party (NAP-Bhasani)", 30, 9129, 0),
    ("প্রগতিশীল গণতান্ত্রিক শক্তি (প্রগশ)", "Pragatishil Gonotantrik Shakti (Pragash)", 2, 6677, 0),
    ("শ্রমিক কৃষক সমাজবাদী দল", "Sramik Krishak Samajbadi Dal", 3, 6396, 0),
    ("জাতীয় বিপ্লবী ফ্রন্ট", "Jatiya Biplobi Front", 8, 3691, 0),
    ("প্রগতিশীল জাতীয়তাবাদী দল", "Pragatishil Jatiyatabadi Dal", 7, 3598, 0),
    ("জাতীয় জনতা পার্টি (আশরাফ)", "Jatiya Janata Party (Ashraf)", 2, 3187, 0),
    ("বাংলাদেশ জাতীয় তাঁতী দল", "Bangladesh Jatiya Tanti Dal", 7, 3115, 0),
    ("বাংলাদেশ মুসলিম লীগ (ইউসুফ)", "Bangladesh Muslim League (Yusuf)", 8, 2757, 0),
    ("জাতীয় যুক্ত ফ্রন্ট", "Jatiya Jukta Front", 7, 2668, 0),
    ("জাতীয় জনতা পার্টি (আসাদ)", "Jatiya Janata Party (Sheik Asad)", 7, 1570, 0),
    ("বাংলাদেশ ন্যাশনাল কংগ্রেস", "Bangladesh National Congress", 10, 1421, 0),
    ("জাতীয়তাবাদী গণতান্ত্রিক চাষীদল (জাগচাদ)", "Jatiyatabadi Gonotantrik Chashi Dal (Jagachad)", 10, 1327, 0),
    ("বাংলাদেশ গণ-আজাদী লীগ (সামাদ)", "Bangladesh Gono Azadi League (Samad)", 1, 1314, 0),
    ("জনশক্তি পার্টি", "Janashakti Party", 4, 1263, 0),
    ("বাংলাদেশ নেজামে ইসলাম পার্টি", "Bangladesh Nezame Islam Party", 3, 1236, 0),
    ("ইসলামী সমাজতান্ত্রিক দল, বাংলাদেশ", "Islami Samajtantrik Dal Bangladesh", 2, 1039, 0),
    ("বাংলাদেশ ফ্রীডম লীগ", "Bangladesh Freedom League", 2, 1034, 0),
    ("পিপলস ডেমোক্রেটিক লীগ", "Peoples Democratic League", 1, 879, 0),
    ("বাংলাদেশ পিপলস লীগ (গরীবে নেওয়াজ)", "Bangladesh Peoples League (Garib-e-Newaz)", 5, 742, 0),
    ("জনতা মুক্তিদল (জামুদ)", "Janata Muktidal (Jamud)", 4, 723, 0),
    ("বাংলাদেশ জন পরিষদ", "Bangladesh Jana Parishad", 6, 686, 0),
    ("মুসলিম পিপলস পার্টি", "Muslim Peoples Party", 1, 515, 0),
    ("বাংলাদেশ কৃষক শ্রমিক মুক্তি আন্দোলন", "Bangladesh Krisak Sramik Mukti Andolon", 2, 503, 0),
    ("বাংলাদেশ ন্যাশন্যাল হিন্দু পার্টি", "Bangladesh National Hindu Party", 2, 502, 0),
    ("জাতীয়তাবাদী গণতান্ত্রিক দল (জাগদল)", "Jatiyatabadi Gonotantrik Dal (Jagodal)", 3, 496, 0),
    ("ডেমোক্রেটিক লীগ", "Democratic League", 1, 453, 0),
    (
        "ধুমপান ও মাদক দ্রব্য নিবারনকারী মানব সেবা সংস্থা (মিদসা)",
        "Dhumpan O Madok Drobbo Nibaronkari Manab Seba Sangstha (MIDSA)",
        2,
        453,
        0,
    ),
    ("জাতীয় তরুন সংঘ", "Jatiya Torun Sangha", 1, 417, 0),
    ("বাংলাদেশ লেবার পার্টি", "Bangladesh Labour Party", 1, 318, 0),
    ("বাংলাদেশ মানবতাবাদী দল (বামাদ)", "Bangladesh Manabatabadi Dal (Bamad)", 4, 294, 0),
    ("আইডিয়েল পার্টি", "Ideal Party", 1, 251, 0),
    (
        "বাংলাদেশ ন্যাশনাল আওয়ামী পার্টি (ভাসানী সাদেকুর রহমান)",
        "Bangladesh National Awami Party (Bhasani-Sadequr Rahman)",
        1,
        248,
        0,
    ),
    ("বাংলাদেশ খেলাফত পার্টি", "Bangladesh Khelafat Party", 1, 241, 0),
    ("বাংলাদেশ ইনকিলাব পার্টি", "Bangladesh Inqilab Party", 3, 214, 0),
    ("বাংলাদেশ ইসলামী বিপ্লবী পরিষদ", "Bangladesh Islami Biplobi Parishad", 1, 202, 0),
    ("বাংলাদেশ বেকার সমাজ", "Bangladesh Bekar Samaj", 2, 182, 0),
    ("বাংলাদেশ আদর্শ কৃষক দল", "Bangladesh Adarsha Krishak Dal", 1, 154, 0),
    ("বাংলাদেশ ইসলামী রিপাবলিকান পার্টি", "Bangladesh Islami Republican Party", 1, 138, 0),
    ("বাংলাদেশ বেকার পার্টি", "Bangladesh Bekar Party", 1, 39, 0),
    ("জাতীয় শ্রমজীবি পার্টি", "Jatiya Sramajibi Party", 1, 28, 0),
    (
        "ন্যাশনাল আওয়ামী পার্টি (ভাসানী নুর মোঃ কাজী)",
        "National Awami Party (Bhasani-Nur Mohammad Kazi)",
        1,
        27,
        0,
    ),
    ("বাংলাদেশ জাতীয় পিপলস পার্টি", "Bangladesh Jatiya Peoples Party", 1, 25, 0),
    ("স্বতন্ত্র/নির্দলীয়", "Independent", 424, 1497396, 3),
)
# The report's national totals under the table of parties.
CANDIDATES, VALID, SEATS = 2787, 34103777, 300


# Each party's name as the 1991 table of parties prints it, by its English name; the registry gives it to the
# parties the 2008 report does not name in Bengali.
BENGALI = {name: bengali for bengali, name, *_ in PARTIES if name != "Independent"}


@dataclass
class Candidate:
    name: str
    party: str  # the English name
    votes: int
    page: int
    box: Box | None = None  # the vote, in pixels at 100 dpi
    blank: bool = False  # the report leaves the vote blank, and it is the valid votes less the others'


@dataclass
class Table:
    number: int
    name: str  # Bengali, as printed
    page: int
    valid: int
    invalid: int
    tendered: int
    total: int | None  # the printed total of the ballots, tendered ones included, where the scan shows it
    candidates: list[Candidate]
    valid_page: int  # the page of the printed sum of the votes
    valid_box: Box | None = None

    def note(self) -> str | None:
        """What the result set says where the report's own figures for the constituency disagree."""
        notes = []
        if any(c.blank for c in self.candidates):
            notes.append(
                "The report leaves one candidate's votes blank; they are stored as the printed valid votes less the "
                "other candidates' votes."
            )
        counted = sum(c.votes for c in self.candidates)
        if counted != self.valid:
            notes.append(
                f"Here the candidates' votes as printed add to {counted:,}, while the report prints {self.valid:,} "
                "valid votes, both as the sum of the column and beside the table; the valid votes are stored as "
                "printed."
            )
        if self.total is not None and self.valid + self.invalid + self.tendered != self.total:
            notes.append(
                f"Here the report prints {self.valid:,} valid, {self.invalid:,} cancelled, and {self.tendered:,} "
                f"tendered ballots but a total of {self.total:,}; ballots cast are stored as the printed total less "
                "the tendered ballots."
            )
        return " ".join(notes) or None


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


# Misprints and variant spellings in the constituency tables' party names, and their spelling in the table of parties.
SPELLINGS = (
    ("বাংশলাদেশ", "বাংলাদেশ"),
    ("বাংলাদেনশ", "বাংলাদেশ"),
    ("বাংলাদেশন", "বাংলাদেশ"),
    ("বাংলদেশ", "বাংলাদেশ"),
    ("বাংলাদেশের", "বাংলাদেশ"),
    ("বাং ", "বাংলাদেশ "),
    ("জাতয়", "জাতীয়"),
    ("সামজ", "সমাজ"),
    ("মসুসলিম", "মুসলিম"),
    ("মোজাফ্ফর", "মোজাফফর"),
    ("মুজাফফর", "মোজাফফর"),
    ("ওয়ার্কাস", "ওয়ার্কার্স"),
    ("ইউনাইটে ", "ইউনাইটেড "),
    ("ইউছুফ", "ইউসুফ"),
    ("নেজামী", "নেজামে"),
    ("ক্র্যা", "ক্রে"),
    ("ক্রি", "ক্রে"),
    ("পিপলস্", "পিপলস"),
    ("আইডিয়াল", "আইডিয়েল"),
    ("জাতীয়তাবাদী (বিএনপি)", "জাতীয়তাবাদী দল (বিএনপি)"),
    ("শাহজাহান", "শাজাহান"),
    (" ও গণতান্ত্রিক", " এবং গণতান্ত্রিক"),
    ("মাদকদ্রব্য", "মাদক দ্রব্য"),
)


def squash(text: str) -> str:
    """A party name with its spacing, punctuation, and the variants of its letters closed up, for matching."""
    text = nfc(text)
    for a, b in SPELLINGS:
        text = text.replace(nfc(a), nfc(b))
    for a, b in (("য়", "য়"), ("ড়", "ড়"), ("ঢ়", "ঢ়"), ("ী", "ি"), ("ূ", "ু"), ("ণ", "ন"), ("ষ", "স"), ("শ", "স")):
        text = text.replace(nfc(a), nfc(b))
    text = text.replace("ৎ", "ত").replace("্", "").replace("\u200c", "").replace("\u200d", "")
    return re.sub(r"[\s().,\-/:;।'\"]", "", text)


def bare(text: str) -> str:
    """A party name without its bracketed abbreviation or faction."""
    return squash(re.sub(r"\([^)]*\)", "", nfc(text)))


# Names a constituency's table prints for a party that the spellings above do not bring to the table of parties.
ALIASES = {
    "স্বতন্ত্র": "Independent",
    "সতন্ত্র": "Independent",
    "নির্দলীয়": "Independent",
    "জামায়াতে ইসলাম": "Jamaat-e-Islami Bangladesh",
    "জামায়াতে ইসলাম- বাংলাদেশ": "Jamaat-e-Islami Bangladesh",
    "আওয়ামী লীগ": "Bangladesh Awami League",
    "বাংলাদেশ কৃষক শ্রমিক আওয়ামী লীগ": "Bangladesh Krishak Sramik Awami League",
    "বাংলাদেশ কৃষক শ্রমিক আওয়ামী (বাকশাল)": "Bangladesh Krishak Sramik Awami League",
    "বাংলাদেশ কমিউনিস্ট পার্টি": "Bangladesher Communist Party",
    "বাংলাদেশ আওয়ামী পার্টি(মোজাফফর)": "National Awami Party (NAP)",
    "জমিয়তে উলামায়ে ইসলামী পার্টি": "Jamiate Ulamae Islami Front",
    "জাতীয় মুক্তি দল (জামুদ)": "Janata Muktidal (Jamud)",
    "জাতীয়তাবাদী মুক্তি দল (জামুদ)": "Janata Muktidal (Jamud)",
    "ধূমপান ও মাদকদ্রব্য নিবারণকারী মানব সেবা সংস্থা (সিফসা)": ("Dhumpan O Madok Drobbo Nibaronkari Manab Seba Sangstha (MIDSA)"),
    "বাংলাদেশ সাম্যবাদী দল (মার্কসবাদ-লেনিনবাদী)": "Bangladesh Sammyabadi Dal (Marx-Lenin)",
    "বাংলাদেশের সাম্যবাদী দল (মার্কসবাদী-লেনিনবাদী)": "Bangladesh Sammyabadi Dal (Marx-Lenin)",
    "বাংলাদেশ ইসলামিক রিপাবলিকান পার্টি": "Bangladesh Islami Republican Party",
    "ন্যাশনাল আওয়ামী পার্টি (ভাসানী-ন্যাপ)(নুর মোহাম্মদ কাজী)": "National Awami Party (Bhasani-Nur Mohammad Kazi)",
    "বাংলাদেশ ন্যাশনাল আওয়ামী (ন্যাপ-ভাসানী)": "National Awami Party (NAP-Bhasani)",
    "পিপলস্ ডেমোক্রিটিক পার্টি": "Peoples Democratic League",
    "বাংলাদেশ ন্যাশনাল আওয়ামী পার্টি (মোজাফফর)": "National Awami Party (NAP)",
    "বাংলাদেশ ইসলামী ঐক্য ফ্রন্ট": "Bangladesh Islami Front",
    "ইসলামী ঐক্য ফ্রন্ট": "Islami Oikya Jote",
    "বাংলাদেশের সমাজতান্ত্রিক দল": "Bangladesh Samajtantrik Dal (BASAD-Khalekuzzaman)",
    "বাংলাদেশের সমাজতান্ত্রিক দল(বাসদ)": "Bangladesh Samajtantrik Dal (BASAD-Khalekuzzaman)",
    "বাংলাদেশ ডেমোক্র্যাটিক পার্টি": "National Democratic Party",
    "আইডিয়াল পার্টি (আই পি)": "Ideal Party",
    "জাতীয় জনতা পার্টি (শেখ আসাদ)": "Jatiya Janata Party (Sheik Asad)",
    "জাতীয় সমাজতান্ত্রিক দল (শাহজাহান সিরাজ)": "Jatiya Samajtantrik Dal (Shahjahan Siraj)",
    "জাতীয় গণতান্ত্রিক চাষী দল (জাগচাদ)": "Jatiyatabadi Gonotantrik Chashi Dal (Jagachad)",
    "বাংলাদেশ ন্যাশনাল হিন্দু পার্টি": "Bangladesh National Hindu Party",
    "জাতীয় জনতা পার্টি ও গণতান্ত্রিক": "Jatiya Janata Party and Gonotantrik Oikya Jote",
    "ইসলামী সমাজতান্ত্রিক দল": "Islami Samajtantrik Dal Bangladesh",
}


@functools.cache
def english() -> dict[str, str]:
    found: dict[str, str] = {}
    plain: dict[str, set[str]] = {}
    for bengali, name, *_ in PARTIES:
        found[squash(bengali)] = name
        plain.setdefault(bare(bengali), set()).add(name)
    # A name printed without its bracket matches the one party whose name it is.
    found |= {key: names.pop() for key, names in plain.items() if len(names) == 1 and key not in found}
    found |= {squash(k): v for k, v in ALIASES.items()}
    return found


def party(text: str) -> str:
    """The English name of the party a constituency's table prints."""
    found = english().get(squash(text))
    if found is None:
        raise ValueError(f"1991: unknown party {text!r}")
    return found


class Row(TypedDict):
    name: str
    party: str
    party_en: NotRequired[str]  # where the printed name is shared by two parties, or names none of them
    votes: int | None  # None where the report leaves the vote blank
    page: int


class Entry(TypedDict):
    number: int
    name: str
    page: int
    valid: int
    invalid: int
    tendered: int
    total: int | None
    sum_page: int
    candidates: list[Row]


class Left(TypedDict):
    number: int
    reason: str


class Voters(TypedDict):
    number: int
    name: str
    male: int
    female: int
    total: int
    page: int


class Transcript(TypedDict):
    constituencies: list[Entry]
    excluded: list[Left]
    voters: list[Voters]


@functools.cache
def transcript() -> Transcript:
    return json.loads(TRANSCRIPT.read_text())


def excluded() -> set[int]:
    """The constituencies the scan leaves unreadable, which the transcript lists with the reason."""
    return {entry["number"] for entry in transcript()["excluded"]}


def unbalanced() -> set[int]:
    """The constituencies whose printed figures disagree with each other."""
    return {table.number for table in tables() if table.note()}


def voters() -> dict[int, int]:
    """Each constituency's registered voters, from the report's table of voters."""
    return {row["number"]: row["total"] for row in transcript()["voters"]}


@functools.cache
def tables() -> list[Table]:
    from ea import raw

    found: list[Table] = []
    for entry in transcript()["constituencies"]:
        candidates = [
            Candidate(
                nfc(c["name"]),
                c.get("party_en") or party(c["party"]),
                c["votes"] or 0,
                c["page"],
                blank=c["votes"] is None,
            )
            for c in entry["candidates"]
        ]
        for candidate in candidates:
            if candidate.blank:
                candidate.votes = entry["valid"] - sum(c.votes for c in candidates)
        found.append(
            Table(
                entry["number"],
                nfc(entry["name"]),
                entry["page"],
                entry["valid"],
                entry["invalid"],
                entry["tendered"],
                entry["total"],
                candidates,
                entry["sum_page"],
            )
        )
    pdf = raw.cached("bd.ecs_parliament", URL).path
    for page in range(FIRST, LAST + 1):
        slots: list[tuple[Table, Candidate | None]] = []
        for table in found:
            slots += [(table, c) for c in table.candidates if c.page == page]
            if table.valid_page == page:
                slots.append((table, None))
        slots = [(t, c) for t, c in slots if c is None or not c.blank]
        values = [c.votes if c else t.valid for t, c in slots]
        for (table, candidate), box in zip(slots, align(values, numbers(render(pdf, page))), strict=True):
            if candidate is None:
                table.valid_box = box
            elif not candidate.blank:
                candidate.box = box
    return found


URL = "https://asset.ecs.gov.bd/media/uploaded_files/notices/2021/09/9FZXuUYX8PQKl3146b6Bh8nNwuFxam24YHB2vaGi.pdf"


# ----------------------------------------------------------------------------------------------------------------
# Locating each vote on its page. The votes are the rightmost column, each constituency's numbers right-aligned
# between two dashed rules; the page is skewed, and the blocks between rules sit at slightly different offsets, so
# each block finds its own right edge. The numbers found are then matched in order to the numbers transcribed for
# the page by their printed widths, and a number left unmatched has no locator.


def render(pdf: Path, page: int) -> np.ndarray:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"p{page}.png"
    if not out.exists():
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(pdf)]
            + [str(out.with_suffix(""))],
            check=True,
        )
    return np.asarray(Image.open(out).convert("L"))


def rules(image: np.ndarray) -> list[int]:
    """The heights of the dashed rules that cross at least a third of the page."""
    width = image.shape[1]
    closed = ndimage.binary_closing(image < 150, structure=np.ones((1, 15)))
    labels, _ = ndimage.label(closed)
    found = []
    for spot in ndimage.find_objects(labels):
        if spot is None:
            continue
        if spot[0].stop - spot[0].start <= 8 and spot[1].stop - spot[1].start > width * 0.3:
            found.append((spot[0].start + spot[0].stop) // 2)
    return sorted(found)


def numbers(image: np.ndarray) -> list[Box]:
    """The boxes of the numbers printed in the vote column, from the top of the page."""
    height, width = image.shape
    x0, x1 = int(width * 0.66), int(width * 0.99)
    dark = image[:, x0:x1] < 150
    closed = ndimage.binary_closing(dark, structure=np.ones((1, 9)))
    labels, count = ndimage.label(closed)
    keep = np.zeros(count + 1, bool)
    for index, spot in enumerate(ndimage.find_objects(labels), start=1):
        if spot is not None:
            keep[index] = not (spot[0].stop - spot[0].start <= 6 and spot[1].stop - spot[1].start > 30)
    dark &= keep[labels]
    labels, _ = ndimage.label(ndimage.binary_dilation(dark, structure=np.ones((5, 6))))
    boxes: list[Box] = []
    for spot in ndimage.find_objects(labels):
        if spot is None:
            continue
        ink = dark[spot]
        if ink.sum() < 25:
            continue
        ys, xs = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
        box = (
            x0 + spot[1].start + int(xs[0]),
            spot[0].start + int(ys[0]),
            x0 + spot[1].start + int(xs[-1]) + 1,
            spot[0].start + int(ys[-1]) + 1,
        )
        if 7 <= box[3] - box[1] <= 22 and 6 <= box[2] - box[0] <= 90:
            boxes.append(box)
    cuts = [0, *rules(image), height]
    found: list[Box] = []
    for top, bottom in itertools.pairwise(cuts):
        group = [b for b in boxes if top <= (b[1] + b[3]) / 2 < bottom and b[2] > width * 0.72]
        if not group:
            continue
        ends = [b[2] for b in group]
        edge = max(ends, key=lambda e: (sum(abs(f - e) <= 4 for f in ends), e))
        found += [b for b in group if abs(b[2] - edge) <= 6]
    return sorted(found, key=lambda b: b[1])


def printed(value: int) -> int:
    """How many characters the report prints for a number, with its Indian grouping commas."""
    digits = str(value)
    return len(digits) + (len(digits) - 2) // 2 if len(digits) > 3 else len(digits)


def align(values: list[int], boxes: list[Box]) -> list[Box | None]:
    """The box of each of the page's numbers, matched in order by printed width; None for a number not found."""
    if not values or not boxes:
        return [None] * len(values)
    widths = [printed(v) for v in values]
    scale = float(np.median([b[2] - b[0] for b in boxes]) / np.median(widths))
    out: list[Box | None] = [None] * len(values)
    for _ in range(2):
        n, m = len(values), len(boxes)
        cost = np.full((n + 1, m + 1), np.inf)
        cost[0, 0] = 0.0
        back: dict[tuple[int, int], tuple[int, int, bool]] = {}
        for i in range(n + 1):
            for j in range(m + 1):
                here = cost[i, j]
                if here == np.inf:
                    continue
                if i < n and here + 1.0 < cost[i + 1, j]:
                    cost[i + 1, j], back[i + 1, j] = here + 1.0, (i, j, False)
                if j < m and here + 0.8 < cost[i, j + 1]:
                    cost[i, j + 1], back[i, j + 1] = here + 0.8, (i, j, False)
                if i < n and j < m:
                    want = scale * widths[i]
                    miss = abs(boxes[j][2] - boxes[j][0] - want) / want
                    if miss < 0.3 and here + miss < cost[i + 1, j + 1]:
                        cost[i + 1, j + 1], back[i + 1, j + 1] = here + miss, (i, j, True)
        out = [None] * n
        i, j = n, m
        while (i, j) != (0, 0):
            pi, pj, matched = back[i, j]
            if matched:
                out[pi] = boxes[pj]
            i, j = pi, pj
        pairs = [(b[2] - b[0]) / w for b, w in zip(out, widths, strict=True) if b is not None]
        if pairs:
            scale = float(np.median(pairs))
    return out
