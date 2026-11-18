"""The 2010 general election by constituency, from the National Election Board's results articles of 2010.

After the poll of 23 May 2010 (15 Ginbot 2002 E.C.), the Board's site of the time, electionethiopia.org, published one
article per zone for the House of Peoples' Representatives and one per zone for the regional councils. Each article
prints, constituency by constituency, every candidate's name, age, sex, and party, with the votes cast at the ordinary
polling stations, at the stations of higher education institutions, and at the stations of defense training centers,
their total, and the candidate's rank. The site no longer answers, so the articles are read from web.archive.org
captures: one capture per article where it was saved alone, and otherwise the article's capture inside one of the two
files that join every captured article of a body.
"""

import functools
import html
import itertools
import json
import re
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from ea import raw
from ea.store import DATA

SOURCE = "et.nebe"
INBOX = DATA / "inbox" / SOURCE
BUNDLES = {
    "house": "browser://20261118-210435-b20507/electionethiopia-2010-house-of-peoples-representatives-results.html",
    "council": "browser://20261118-210435-b20507/electionethiopia-2010-regional-council-results.html",
}
LISTINGS = {
    "house": "http://www.electionethiopia.org/am/2010-06-29-07-34-37.html",
    "council": "http://www.electionethiopia.org/am/ክልል-ምክር-ቤት.html",
}
# The region of an article, from the number of its region's menu in the article's path.
FOLDERS = {
    "house": {
        80: "TG",
        81: "AF",
        82: "AM",
        83: "OR",
        84: "SO",
        85: "BG",
        86: "SN",
        87: "GA",
        88: "HA",
        89: "AA",
        90: "DD",
    },
    "council": {71: "TG", 72: "AF", 73: "AM", 74: "OR", 75: "SO", 76: "BG", 77: "SN", 78: "GA", 79: "HA"},
}
# A table whose heading row lost its constituency, named from where it stands: in both of the zone's articles the
# constituencies run in the same order, and the council article names this one ሊሙ ኮሳ 1.
UNNAMED = {
    ("house", "OR", "ጅማ", 9): (
        "ሊሙ ኮሶ 1",
        (
            "The page prints no constituency name above this table. It stands between ጅማ ከተማ and ሊሙ ኮሶ 2, and the "
            "zone's council article, which lists the constituencies in the same order, has ሊሙ ኮሳ 1 there, so it is "
            "named ሊሙ ኮሶ 1."
        ),
    )
}


@functools.cache
def articles() -> dict[str, list[tuple[str, str | None]]]:
    """For each body, the article URLs in the order of the Board's listing, each with the URL of its own capture
    when one was saved alone."""
    data = json.loads((Path(__file__).parent / "nebe_2010.json").read_text(encoding="utf-8"))
    return {body: [(url, own) for url, _, own in rows] for body, rows in data.items()}


def fetch() -> None:
    """Load the captures saved for this source (data/inbox/et.nebe/) into the raw cache."""
    index = json.loads((INBOX / "index.json").read_text(encoding="utf-8"))
    wanted = set(BUNDLES.values()) | {own for rows in articles().values() for _, own in rows if own}
    for key, entry in index.items():
        url = entry.get("source", key)
        if url not in wanted:
            continue
        raw.store(
            SOURCE,
            url,
            (INBOX / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


@functools.cache
def bundle(body: str) -> dict[str, str]:
    """The articles inside a joined file, by their URL."""
    text = raw.cached(SOURCE, BUNDLES[body]).path.read_text(encoding="utf-8")
    parts = re.split(r"<!-- SOURCE: (\S+) \(Wayback capture \d+\) -->", text)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def cited(body: str) -> list[raw.Artifact]:
    """Every raw file the body's results are read from."""
    out = [raw.cached(SOURCE, BUNDLES[body])]
    out += [raw.cached(SOURCE, own) for _, own in articles()[body] if own]
    return out


def article(body: str, url: str, own: str | None) -> tuple[str, raw.Artifact]:
    if own:
        artifact = raw.cached(SOURCE, own)
        return artifact.path.read_text(encoding="utf-8"), artifact
    return bundle(body)[url], raw.cached(SOURCE, BUNDLES[body])


def rows(text: str) -> list[list[str]]:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL)
    out: list[list[str]] = []
    for row in re.findall(r"<tr.*?</tr>", text, flags=re.DOTALL):
        if "<table" in row:
            continue
        cells = [
            " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).replace("\u200b", "").split())
            for body in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)
        ]
        if any(cells):
            out.append(cells)
    return out


class Candidate(NamedTuple):
    row: int
    name: str
    age: int | None
    sex: str
    party: str
    votes: tuple[int, int, int, int] | None  # ordinary, higher education, defense, total
    rank: int


class Constituency(NamedTuple):
    body: str
    region: str
    zone: str
    name: str
    seq: int  # 1, or the count of earlier tables of the zone under the same name, plus one
    url: str
    artifact: raw.Artifact
    candidates: list[Candidate]
    notes: list[str]

    @property
    def code(self) -> str:
        code = f"{self.region}:{self.zone}:{self.name}" if self.zone else f"{self.region}:{self.name}"
        return code if self.seq == 1 else f"{code}#{self.seq}"


SEXES = {"ወ": "male", "ወንድ": "male", "ሴ": "female", "ሴት": "female"}


def candidate(cells: list[str]) -> Candidate:
    """A candidate's row. Rows differ in small ways: some put a `)` cell after the number, some split the rank as
    `1` and `ኛ`, some leave out the age cell; and four rows print only two vote figures before the rank."""
    cells = [c for c in cells if c not in {")", ""}] if cells[1] in {")", ""} else [c for c in cells]
    while cells and not cells[-1]:
        cells.pop()
    if cells[-1] == "ኛ":
        cells = [*cells[:-2], cells[-2] + "ኛ"]
    sex = next(i for i, c in enumerate(cells) if c in SEXES)
    age = cells[2] if sex == 3 and cells[2] else None
    numbers = cells[sex + 2 : -1]
    rank = re.fullmatch(r"(\d+)ኛ", cells[-1])
    # One row prints its total with the rank's suffix and no rank (`348 9 1 358ኛ`); its rank is its row.
    if (
        rank
        and len(numbers) == 3
        and all(re.fullmatch(r"\d+", n) for n in numbers)
        and sum(int(n) for n in numbers) == int(rank.group(1))
    ):
        numbers.append(rank.group(1))
        rank = re.fullmatch(r"(\d+)", cells[0])
    if not rank or not all(re.fullmatch(r"\d+", n) for n in numbers) or len(numbers) not in {2, 4}:
        raise ValueError(f"unreadable candidate row {cells}")
    votes = None
    if len(numbers) == 4:
        a, b, c, total = (int(n) for n in numbers)
        if a + b + c != total:
            raise ValueError(f"{cells}: {a} + {b} + {c} is not {total}")
        votes = (a, b, c, total)
    return Candidate(
        int(cells[0]), cells[1], int(age) if age else None, SEXES[cells[sex]], cells[sex + 1], votes, int(rank.group(1))
    )


def region_of(body: str, url: str) -> str:
    match = re.search(r"/(\d+)-[^/]+/\d+-[^/]+$", url)
    if not match:
        raise ValueError(url)
    return FOLDERS[body][int(match.group(1))]


@functools.cache
def constituencies(body: str) -> list[Constituency]:
    out: list[Constituency] = []
    for url, own in articles()[body]:
        text, artifact = article(body, url, own)
        code = region_of(body, url)
        table = rows(text)
        current: Constituency | None = None
        at = 0
        for i, row in enumerate(table):
            filled = [c for c in row if c]
            if filled[:3] == ["ክልል", "ዞን", "ምርጫ ክልል"]:
                head = [c for c in table[i + 1] if c]
                notes: list[str] = []
                if len(head) == 3:
                    zone, name = head[1], head[2]
                elif len(head) == 2:
                    zone, name = "", head[1]
                else:
                    zone = out[-1].zone
                    name, why = UNNAMED[(body, code, zone, at)]
                    notes.append(why)
                seq = 1 + sum(1 for c in out if (c.region, c.zone, c.name) == (code, zone, name))
                if seq > 1:
                    notes.append(
                        f"The zone's article prints {seq} tables headed {name}. They hold different candidates, so "
                        f"each is a constituency of its own; this is table {seq}, under the name the page prints."
                    )
                current = Constituency(body, code, zone, name, seq, url, artifact, [], notes)
                out.append(current)
                at += 1
            elif current is not None and re.fullmatch(r"\d+", row[0]) and len(filled) >= 8:
                current.candidates.append(candidate(row))
    for c in out:
        if not c.candidates:
            raise ValueError(f"{c.code}: no candidates")
        ranks = [x.rank for x in c.candidates]
        if ranks != list(range(ranks[0], ranks[0] + len(ranks))):
            raise ValueError(f"{c.code}: ranks {ranks}")
        counted = [x for x in c.candidates if x.votes]
        for above, below in itertools.pairwise(counted):
            if below.votes and above.votes and below.votes[3] > above.votes[3]:
                c.notes.append(
                    f"The page ranks {below.name} ({below.party}) at {below.rank} with {below.votes[3]:,} votes, "
                    f"below {above.name} ({above.party}) with {above.votes[3]:,}; the votes are stored as printed."
                )
        top = c.candidates[0]
        if body == "council" and top.rank > 1:
            c.notes.append(
                f"The page's table starts at the candidate it ranks {top.rank}; the row of the candidate ranked "
                "first is missing from it, so that candidate and their votes are not stored."
            )
            continue
        if (
            top.rank != 1
            or not top.votes
            or body == "house"
            and any(x.votes and x.votes[3] >= top.votes[3] for x in c.candidates[1:])
        ):
            raise ValueError(f"{c.code}: the first-ranked candidate does not lead")
    codes = [c.code for c in out]
    if len(codes) != len(set(codes)):
        raise ValueError(f"{body}: repeated constituencies {sorted(x for x in codes if codes.count(x) > 1)}")
    return out


# Ethiopic syllables in Latin letters, for slugs and the transliterated names: each row of eight holds one consonant
# in its seven vowel orders and one labialized form.
CONSONANTS = {
    0x1200: "h",
    0x1208: "l",
    0x1210: "h",
    0x1218: "m",
    0x1220: "s",
    0x1228: "r",
    0x1230: "s",
    0x1238: "sh",
    0x1240: "q",
    0x1250: "q",
    0x1260: "b",
    0x1268: "v",
    0x1270: "t",
    0x1278: "ch",
    0x1280: "h",
    0x1290: "n",
    0x1298: "ny",
    0x12A0: "",
    0x12A8: "k",
    0x12B8: "h",
    0x12C8: "w",
    0x12D0: "",
    0x12D8: "z",
    0x12E0: "zh",
    0x12E8: "y",
    0x12F0: "d",
    0x12F8: "dd",
    0x1300: "j",
    0x1308: "g",
    0x1318: "ng",
    0x1320: "t",
    0x1328: "ch",
    0x1330: "p",
    0x1338: "ts",
    0x1340: "ts",
    0x1348: "f",
    0x1350: "p",
}
VOWELS = ("e", "u", "i", "a", "e", "", "o", "wa")
LABIALIZED = {0x1248: "q", 0x1288: "h", 0x12B0: "k", 0x1310: "g"}


def romanize(text: str) -> str:
    out: list[str] = []
    start = True
    for ch in text:
        code = ord(ch)
        row = code & ~7
        if row in CONSONANTS:
            # The glottal letters' first order is said as a: አዲስ is Addis.
            vowel = "a" if code - row == 0 and not CONSONANTS[row] else VOWELS[code - row]
            # A sixth-order letter that opens a word is said with a short vowel: ጅማ is Jima, ምስራቅ Misraq.
            out.append(CONSONANTS[row] + ("i" if vowel == "" and start and CONSONANTS[row] else vowel))
            start = False
        elif row in LABIALIZED:
            out.append(LABIALIZED[row] + {0: "we", 2: "wi", 3: "wa", 4: "we", 5: "w"}.get(code - row, ""))
            start = False
        elif 0x1360 <= code <= 0x1368:
            out.append(" ")
            start = True
        else:
            out.append(ch)
            start = not ch.isalnum()
    return " ".join("".join(out).split())
