"""Belize: House of Representatives elections, by-elections, and national referendums by electoral division.

The Elections and Boundaries Department (now Commission) publishes one PDF per election with the result for each
electoral division. The text layer of each PDF is read with `pdftotext -layout`, and every division is checked
against its own printed totals: candidate votes plus rejected ballots must equal the votes cast.

The returns for 1993 and 1998 are image-only scans, and the 2003 Cayo South by-election return holds its candidates in an image. Their figures were read from the page images into
`ebc_scans.json`, each with its page and bounding box (pixels of the page rendered at 100 dpi), and `scanned` checks
them against the totals each return prints.
"""

import functools
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "bz.ebc"
PAGE = "https://elections.gov.bz/general-election-results/"
BASE = "https://elections.gov.bz/wp-content/uploads"
BELIZE = str(uuid5(package.NAMESPACE, "polity\x1fBZ"))
VOTES, REGISTERED, CAST, INVALID = 1, 2, 3, 5
VALID, VOTE_SHARE = 4, 13
SHARES, DIGITIZED = 4, 8
SCHEME = "bz.division"

URLS = {
    "1979": f"{BASE}/2023/12/GENERAL-ELECTIONS-1979.pdf",
    "1984-1989": f"{BASE}/2025/03/Belize-General-Elections-Results-1984-and-1989.docx.pdf",
    "1993": f"{BASE}/2023/12/1993-General-Election-Results.pdf",
    "1998": f"{BASE}/2023/12/1998-General-Elections-Results.pdf",
    "2003": f"{BASE}/2023/12/2003-General-Elections-Results.pdf",
    "2008": f"{BASE}/2023/12/2008-General-Elections-Results.pdf",
    "2012": f"{BASE}/2023/12/2012-General-Elections-Results.pdf",
    "2015": f"{BASE}/2023/12/2015-General-Election-Results.pdf",
    "2020": f"{BASE}/2023/12/2020-General-Election-Results-1.pdf",
    "2025": f"{BASE}/2025/03/GENERAL-ELECTION-12TH-MARCH-2025.pdf",
    "2003-cayo-south": f"{BASE}/2023/12/2003-Cayo-South-By-Results.pdf",
    "2015-dangriga": f"{BASE}/2023/12/2015-Dangriga-Official-By-Results.pdf",
    "2021-corozal-bay": f"{BASE}/2023/12/2021-Corozal-Official-By-Election-Results-1.pdf",
    "2024-toledo-east": f"{BASE}/2024/07/FINAL-Toledo-East-By-Election-Results-2024-1-2.pdf",
    "2008-referendum": f"{BASE}/2023/12/2008-Referendum-Results.pdf",
    "2019-referendum": f"{BASE}/2023/12/2019-Referendum-Results.pdf",
}


@dataclass(frozen=True)
class Held:
    key: str
    day: date
    kind: str
    layout: str
    files: tuple[str, ...]
    english: str
    printed: str | None = None
    note: str | None = None
    date_to: date | None = None
    precision: str = "day"
    package: str | None = None
    slug: str | None = None


GENERAL_NOTE = (
    "The published results give the election's date only in some years. Where the document prints no election date, "
    "or prints a different one, the date is the day the election was held."
)
HELD = (
    Held(
        "1979",
        date(1979, 11, 21),
        "general",
        "modern",
        ("1979",),
        "1979 Belize general election",
        "24th November 1979",
        "The results page is headed 24th November 1979; the general election was held on 21 November 1979.",
    ),
    Held(
        "1993",
        date(1993, 6, 30),
        "general",
        "scan-1993",
        ("1993",),
        "1993 Belize general election",
        None,
        'The scanned return is headed only "Election results 1993" and prints no election date; the general '
        "election was held on 30 June 1993.",
    ),
    Held(
        "1993-freetown",
        date(1993, 1, 1),
        "by_election",
        "scan-1993-freetown",
        ("1993",),
        "1993 Freetown by-election",
        None,
        "The scanned return prints no election date, only its source, the Gazette of 30 January 1993, so the day is "
        "not stated; the by-election was held in January 1993.",
        date(1993, 1, 31),
        "month",
        "1993-01",
        "bz/1993-01-by-election-freetown",
    ),
    Held(
        "1998",
        date(1998, 8, 27),
        "general",
        "scan-1998",
        ("1998",),
        "1998 Belize general election",
        "Thursday, 27 August, 1998",
    ),
    Held("1984", date(1984, 12, 14), "general", "ballots-1984", ("1984-1989",), "1984 Belize general election"),
    Held("1989", date(1989, 9, 4), "general", "ballots-1989", ("1984-1989",), "1989 Belize general election"),
    Held(
        "2003",
        date(2003, 3, 5),
        "general",
        "inline",
        ("2003",),
        "2003 Belize general election",
        None,
        "The results page is dated 13 March 2003, after the election of 5 March 2003.",
    ),
    Held(
        "2003-cayo-south",
        date(2003, 10, 29),
        "by_election",
        "scan-2003-cayo-south",
        ("2003-cayo-south",),
        "2003 Cayo South by-election",
        "October 29, 2003",
        "The candidates' table is an image in the PDF; only the summary has a text layer.",
    ),
    Held("2008", date(2008, 2, 7), "general", "block", ("2008",), "2008 Belize general election"),
    Held("2012", date(2012, 3, 7), "general", "inline", ("2012",), "2012 Belize general election", "March 7, 2012"),
    Held(
        "2015", date(2015, 11, 4), "general", "modern", ("2015",), "2015 Belize general election", "4th November, 2015"
    ),
    Held(
        "2020", date(2020, 11, 11), "general", "modern", ("2020",), "2020 Belize general election", "11th November 2020"
    ),
    Held("2025", date(2025, 3, 12), "general", "modern", ("2025",), "2025 Belize general election", "12th March 2025"),
    Held(
        "2015-dangriga",
        date(2015, 7, 8),
        "by_election",
        "modern",
        ("2015-dangriga",),
        "2015 Dangriga by-election",
        "8th July 2015",
    ),
    Held(
        "2021-corozal-bay",
        date(2021, 3, 3),
        "by_election",
        "modern",
        ("2021-corozal-bay",),
        "2021 Corozal Bay by-election",
        "3rd March 2021",
    ),
    Held(
        "2024-toledo-east",
        date(2024, 7, 17),
        "by_election",
        "modern",
        ("2024-toledo-east",),
        "2024 Toledo East by-election",
        "17th July 2024",
    ),
    Held(
        "2008-referendum",
        date(2008, 2, 7),
        "referendum",
        "referendum-2008",
        ("2008-referendum",),
        "2008 Belize referendum",
        None,
        "The results page prints no date; the referendum was held with the general election on 7 February 2008.",
    ),
    Held(
        "2019-referendum",
        date(2019, 5, 8),
        "referendum",
        "referendum-2019",
        ("2019-referendum",),
        "2019 Belize referendum",
        "8th May 2019",
    ),
)

DIVISIONS = (
    "Albert",
    "Belize Rural Central",
    "Belize Rural North",
    "Belize Rural South",
    "Belmopan",
    "Caribbean Shores",
    "Cayo Central",
    "Cayo North East",
    "Cayo North",
    "Cayo South",
    "Cayo West",
    "Collet",
    "Corozal Bay",
    "Corozal North",
    "Corozal South East",
    "Corozal South West",
    "Corozal South",
    "Dangriga",
    "Fort George",
    "Freetown",
    "Lake Independence",
    "Mesopotamia",
    "Orange Walk Central",
    "Orange Walk East",
    "Orange Walk North",
    "Orange Walk South",
    "Pickstock",
    "Port Loyola",
    "Queen's Square",
    "Stann Creek South",
    "Stann Creek West",
    "Toledo East",
    "Toledo North",
    "Toledo South",
    "Toledo West",
)
ALIASES = {
    "dangringa": "Dangriga",
    "mesopotomia": "Mesopotamia",
    "mesopotomia ": "Mesopotamia",
    "queens square": "Queen's Square",
    "lake i": "Lake Independence",
    "queen square": "Queen's Square",
    "corozal south west": "Corozal South West",
}
PARTIAL = {"Lake", "Orange Walk", "Belize Rural", "Cayo North", "Corozal South", "Caribbean"}
SUFFIX = {"Independence", "Central", "North", "South", "East", "West", "Shores"}
PARTY_ALIASES = {"UPD": "UDP", "INDP": "IND", "NRTCP": "NRTCP", "P.U.P.": "PUP", "U.D.P.": "UDP"}
INDEPENDENT = {"IND"}
PARTY_NAMES = {
    "PUP": "People's United Party",
    "UDP": "United Democratic Party",
    "PDP": "People's Democratic Party",
    "NABR": "National Alliance for Belize Rights",
}
NUMBER = r"\d[\d,]*"


@dataclass
class Race:
    division: str
    registered: int | None = None
    cast: int | None = None
    rejected: int | None = None
    # (name, party label, votes, elected, uncontested)
    cands: list[tuple[str, str, int | None, bool, bool]] = field(default_factory=list)
    answers: dict[str, int] = field(default_factory=dict)
    # scanned returns: valid votes (when printed as such), each candidate's printed share, and the page and box of each
    # stored figure, keyed "registered", "cast", "rejected", "valid", or ("votes", position) and ("share", position)
    valid: int | None = None
    shares: list[str | None] = field(default_factory=list)
    boxes: dict[object, tuple[int, list[int]]] = field(default_factory=dict)
    derived_winner: bool = False


def n(text: str) -> int:
    return int(text.replace(",", ""))


def canonical(name: str) -> str:
    text = re.sub(r"\s+", " ", name.replace("’", "'")).strip()
    found = ALIASES.get(text.lower())
    if found:
        return found
    for known in DIVISIONS:
        if known.lower() == text.lower():
            return known
    raise LookupError(f"unknown electoral division {name!r}")


def party_of(label: str) -> str:
    return PARTY_ALIASES.get(
        re.sub(r"\s*\(.*", "", label).strip().upper(), re.sub(r"\s*\(.*", "", label).strip().upper()
    )


def url(name: str) -> str:
    return URLS[name]


def fetch() -> None:
    for link in URLS.values():
        raw.fetch(SOURCE, link, suffix=".pdf")


@functools.cache
def text(name: str) -> str:
    path = raw.cached(SOURCE, url(name)).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


# ---------------------------------------------------------------- layouts

HEAD = re.compile(
    rf"^\s*(?P<name>[A-Za-z][A-Za-z' .&-]*?)\s{{2,}}(?P<reg>{NUMBER})\s+(?P<cast>{NUMBER})\s+(?P<pct>[\d.]+)%?(?:\s+\d+)?\s*$"
)
NAMEONLY = re.compile(r"^\s{0,12}(?P<name>[A-Z][A-Za-z' .&-]+?)\s*$")
PARTY = r"[A-Z]{2,6}(?:\s?\(\w+\))?"
COLOURS = {"WHITE", "RED", "BLUE", "GREEN", "YELLOW", "ORANGE", "BLACK", "PURPLE", "PINK", "GREY", "GRAY"}
CAND = re.compile(
    rf"^\s+(?:(?P<name>\S.*?)\s{{2,}})?(?P<party>{PARTY})\s*"
    rf"(?:(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%?|(?P<unc>Uncontested)|(?P<none>))\s*(?P<rem>Elected)?\s*$"
)
REJ = re.compile(rf"^\s*(?:[A-Z]+\s+)?rejected\s+(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%?\s*$", re.IGNORECASE)
VOTES_ONLY = re.compile(rf"^\s+(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%\s*(?P<rem>Elected)?\s*$")
FRAGMENT = re.compile(r"^\s+(?P<frag>[A-Za-z(][^\d]*?)(?:\s{2,}[A-Z]+)?\s*$")
SKIP = re.compile(
    r"Electoral|Candidates|Political|Voters|Source:|Elections and Boundaries|GENERAL ELECTION|OFFICIAL RESULTS|"
    r"Mahogany|Market Square|Old Land|City of Belmopan|Turnout|Party|Votes|^\s*\d+(th|st|nd|rd)\b|Belize City|"
    r"Charles B|Hyde|Official Result|RESULT|Chief Elections|JOSEPHINE|BY-ELECTION|Summary|SUMMARY|General Election|#|%|"
    r"ELECTORAL DIVISION|By Election|^\s*Total\s*$|^\s*Cast\s*$|^\s*Voter\s*$"
)


def modern(body: str) -> list[Race]:
    """Layouts with the division on its own line, then one line per candidate (1979, 2015, 2020, 2025, by-elections)."""
    races: list[Race] = []
    cur: Race | None = None
    waiting: int | None = None
    for line in body.splitlines():
        if not line.strip():
            continue
        if re.match(r"\s*(SUMMARY|Summary)", line):
            break
        head = HEAD.match(line)
        if head:
            cur = Race(canonical(head["name"]), n(head["reg"]), n(head["cast"]))
            races.append(cur)
            waiting = None
            continue
        rejected = REJ.match(line)
        if rejected and cur:
            cur.rejected = n(rejected["votes"])
            continue
        candidate = CAND.match(line)
        if candidate and candidate["party"] in COLOURS and not candidate["votes"]:
            candidate = None
        if candidate and cur is not None and (candidate["votes"] or candidate["unc"] or not SKIP.search(line)):
            votes = None if candidate["unc"] or candidate["votes"] is None else n(candidate["votes"])
            cur.cands.append(
                (
                    (candidate["name"] or "").strip(),
                    candidate["party"],
                    votes,
                    bool(candidate["rem"]),
                    bool(candidate["unc"]),
                )
            )
            waiting = len(cur.cands) - 1 if candidate["votes"] is None and not candidate["unc"] else None
            continue
        only = VOTES_ONLY.match(line)
        if only and cur is not None and waiting is not None:
            name, party, _, _, unc = cur.cands[waiting]
            cur.cands[waiting] = (name, party, n(only["votes"]), bool(only["rem"]), unc)
            waiting = None
            continue
        if SKIP.search(line):
            continue
        name_only = NAMEONLY.match(line)
        if name_only:
            try:
                cur = Race(canonical(name_only["name"]))
            except LookupError:
                pass
            else:
                if races and races[-1].division == cur.division:
                    cur = races[-1]
                else:
                    races.append(cur)
                continue
        frag = FRAGMENT.match(line)
        if frag and cur is not None and cur.cands:
            name, party, votes, rem, unc = cur.cands[-1]
            cur.cands[-1] = (f"{name} {frag['frag'].strip()}".strip(), party, votes, rem, unc)
            continue
        name_only = NAMEONLY.match(line)
        if name_only:
            try:
                cur = Race(canonical(name_only["name"]))
            except LookupError:
                continue
            races.append(cur)
            continue
        raise ValueError(f"unparsed line {line!r}")
    return [r for r in races if r.cands]


DIVISION_START = re.compile(r"^(?P<name>" + "|".join(sorted(map(re.escape, DIVISIONS), key=len, reverse=True)) + r")\b")
PARTIAL_START = re.compile(r"^(?P<name>Lake|Orange Walk|Belize Rural)\b")
INLINE = re.compile(
    rf"^(?P<name>\S.*?)\s+(?P<party>{PARTY})\s+(?P<reg>{NUMBER})\s+(?P<cast>{NUMBER})\s+(?P<turn>[\d.]+)%?\s+"
    rf"(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%\s*$"
)
INLINE_ROW = re.compile(rf"^\s+(?P<name>\S.*?)\s{{2,}}(?P<party>{PARTY})\s+(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%\s*$")
INLINE_REJ = re.compile(
    rf"^\s*(?:(?P<party>[A-Z]{{2,4}})\s+)?rejected(?:\s+(?P<votes>{NUMBER}))?(?:\s+(?P<pct>[\d.]+)%)?\s*$"
)
INLINE_NUMS = re.compile(rf"^\s*(?P<frag>[A-Za-z][^\d]*?)\s+(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%\s*$")


def inline(body: str, fixes: dict[tuple[str, int], str]) -> list[Race]:
    """Layouts with the division's name on the same line as its first candidate and the registered voters (2003, 2012)."""
    lines = [line for line in body.splitlines() if line.strip()]
    races: list[Race] = []
    cur: Race | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if re.match(r"\s*(SUMMARY|Summary)", line) or re.match(r"\s{20,}\d{3},?\d{3}\s", line):
            break
        start = None if line.startswith(" ") else (DIVISION_START.match(line) or PARTIAL_START.match(line))
        if start:
            rest = line[start.end() :].strip()
            name = start["name"]
            if name in PARTIAL or name not in DIVISIONS:
                suffix = None
                for look in lines[index : index + 2]:
                    found = re.match(r"^(Independence|Central|North|South|East|West|Shores)\b", look)
                    if found and not look.startswith(" "):
                        suffix = found
                        break
                if suffix is not None:
                    name = f"{name} {suffix.group(1)}"
                    lines[index + lines[index : index + 2].index(look)] = look[suffix.end() :]
            if name == "Corozal South" and lines[index].startswith("West"):
                name, lines[index] = "Corozal South West", lines[index][4:]
            if name == "Cayo North" and lines[index].startswith("East"):
                name, lines[index] = "Cayo North East", lines[index][4:]
            cur = Race(canonical(name))
            races.append(cur)
            line = " " * 21 + rest if rest else ""
            if not line:
                continue
            first = INLINE.match(line.strip())
            if first:
                cur.registered, cur.cast = n(first["reg"]), n(first["cast"])
                cur.cands.append((first["name"].strip(), first["party"], n(first["votes"]), False, False))
                continue
            registered = re.match(
                rf"^\s*(?P<name>\S.*?)\s{{2,}}(?P<party>{PARTY})?\s+(?P<reg>{NUMBER})\s+(?P<cast>{NUMBER})\s+[\d.]+%?\s*(?P<votes>{NUMBER})?\s*(?P<pct>[\d.]+%)?\s*$",
                line,
            )
            if registered:
                cur.registered, cur.cast = n(registered["reg"]), n(registered["cast"])
                cur.cands.append(
                    (
                        registered["name"].strip(),
                        registered["party"] or "",
                        n(registered["votes"]) if registered["votes"] else None,
                        False,
                        False,
                    )
                )
                continue
            raise ValueError(f"unparsed division line {line!r}")
        if cur is None:
            continue
        stripped = line.strip()
        if re.match(r"^(Independence|Central|North|South|East|West|Shores)\b", stripped) and not line.startswith("  "):
            line = " " * 21 + re.sub(r"^\w+\s*", "", stripped)
            if not line.strip():
                continue
        row = INLINE_ROW.match(line)
        if row:
            cur.cands.append((row["name"].strip(), row["party"], n(row["votes"]), False, False))
            continue
        rej = INLINE_REJ.match(line)
        if rej:
            if rej["party"] and cur.cands and not cur.cands[0][1]:
                name, _, votes, rem, unc = cur.cands[0]
                cur.cands[0] = (name, rej["party"], votes, rem, unc)
            if rej["votes"]:
                cur.rejected = n(rej["votes"])
            elif cur.rejected is None:
                cur.rejected = 0
            continue
        nums = INLINE_NUMS.match(line)
        if nums and cur.cands:
            # the rejected ballots' figures printed on the line below the last name fragment
            name, party, votes, rem, unc = cur.cands[-1]
            cur.cands[-1] = (f"{name} {nums['frag'].strip()}", party, votes, rem, unc)
            cur.rejected = n(nums["votes"])
            continue
        frag = line.strip()
        if cur.cands and not re.search(r"\d|Source", frag):
            name, party, votes, rem, unc = cur.cands[-1]
            cur.cands[-1] = (f"{name} {frag}", party, votes, rem, unc)
            continue
        if re.search(r"Source|Elections and", frag):
            continue
        raise ValueError(f"unparsed line {line!r}")
    for race in races:
        for position, (name, party, votes, rem, unc) in enumerate(race.cands):
            fixed = fixes.get((race.division, position))
            if fixed and not party:
                race.cands[position] = (name, fixed, votes, rem, unc)
    return races


BLOCK_ROW = re.compile(
    rf"^\s*(?P<party>[A-Z]{{2,6}})\s+(?P<name>\S.*?)\s{{2,}}(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%\s*(?P<rem>Elected)?\s*$"
)
BLOCK_NAMELESS = re.compile(
    rf"^\s*(?P<party>[A-Z]{{2,6}})\s{{2,}}(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%\s*(?P<rem>Elected)?\s*$"
)
BLOCK_REJ = re.compile(rf"^\s*Rejected Votes\s+(?P<votes>{NUMBER})\s+(?P<pct>[\d.]+)%\s*$")
BLOCK_HEAD = re.compile(
    rf"^(?P<name>[A-Za-z][A-Za-z' .&-]*?)\s{{2,}}(?P<reg>{NUMBER})\s+(?P<cast>{NUMBER})\s+(?P<pct>[\d.]+)\s*$"
)


def block(body: str) -> list[Race]:
    """The 2008 layout: a division line, then rows of party, candidate, votes, and percent."""
    races: list[Race] = []
    cur: Race | None = None
    before = ""
    lines = [line for line in body.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if re.match(r"\s*Summary", line):
            break
        head = BLOCK_HEAD.match(line)
        if head:
            cur = Race(canonical(head["name"]), n(head["reg"]), n(head["cast"]))
            races.append(cur)
            before = ""
            continue
        if cur is None or re.search(r"Total:|Party\s+Candidate|# of", line):
            continue
        rej = BLOCK_REJ.match(line)
        if rej:
            cur.rejected = n(rej["votes"])
            continue
        row = BLOCK_ROW.match(line)
        if row:
            cur.cands.append((row["name"].strip(), row["party"], n(row["votes"]), bool(row["rem"]), False))
            continue
        nameless = BLOCK_NAMELESS.match(line)
        if nameless:
            cur.cands.append((before, nameless["party"], n(nameless["votes"]), bool(nameless["rem"]), False))
            before = ""
            continue
        frag = line.strip()
        if re.search(r"\bParty\b|Candidate|\bVotes\b", frag):
            continue
        if re.search(r"\d", frag):
            raise ValueError(f"unparsed line {line!r}")
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if BLOCK_NAMELESS.match(following) or not cur.cands:
            before = f"{before} {frag}".strip()
        else:
            name, label, votes, elected, solo = cur.cands[-1]
            cur.cands[-1] = (f"{name} {frag}".strip(), label, votes, elected, solo)
    return races


ROW84 = re.compile(r"^(?P<name>[A-Za-z][A-Za-z' .]*?)\s+(?P<cells>(?:(?:\d[\d,]*|---)\s*)+)$")


def ballots(body: str, year: int) -> list[Race]:
    """The 1984 and 1989 tables: per constituency, ballots for each party's candidate."""
    body = re.sub(r"Orange Walk\n\s+([\d,\s-]+?)\n\s*Central", lambda m: "Orange Walk Central " + m[1].strip(), body)
    start = body.index(f"General Election of {year}")
    end = body.index("TOTAL", start)
    races: list[Race] = []
    for line in body[start:end].splitlines()[1:]:
        row = ROW84.match(line.strip())
        if not row:
            continue
        cells = row["cells"].split()
        cells = [None if c == "---" else n(c) for c in cells]
        race = Race(canonical(row["name"].strip()))
        if year == 1984:
            parties = ("PUP", "UDP", "IND", "CDP")
            if len(cells) != 5:
                raise ValueError(line)
            race.cast = cells[4]
            votes = cells[:4]
        else:
            if len(cells) != 7:
                raise ValueError(line)
            race.registered, race.cast, race.rejected = cells[0], cells[1], cells[2]
            parties = ("PUP", "UDP", "IND")
            votes = cells[3:6]
        for party, count in zip(parties, votes, strict=False):
            if count is not None:
                race.cands.append(("", party, count, False, False))
        races.append(race)
    return races


REF_ROW = re.compile(r"^(?P<left>.{0,19}?)\s*\b(?P<answer>YES|NO|REJECTED|Yes|No|Rejected)\b(?P<rest>[^A-Za-z]*)$")
REF_BLOCK = re.compile(rf"^\s*(?P<reg>{NUMBER})\s+(?P<cast>{NUMBER})\s+(?P<pct>[\d.]+)%?\s*$")
REF_HEAD = re.compile(
    r"Electoral|Division|Voters|Votes|Cast|Certificate|Elections and|Referendum Results|Yes/No|YES/NO|Source|# of|%"
)


def referendum(body: str) -> list[Race]:
    """The referendum layouts: registered voters and votes cast, then a Yes, No, and Rejected row per division."""
    races: list[Race] = []
    labels: list[list[str]] = []
    cur: Race | None = None
    before: list[str] = []
    started = False
    for line in body.splitlines():
        if not line.strip():
            continue
        if re.match(r"\s*(Summary|SUMMARY)", line) or "Source:" in line:
            break
        if re.match(rf"^\s*{NUMBER}\s+[\d.]+%\s*$", line):
            continue
        opening = REF_BLOCK.match(line)
        row = REF_ROW.match(line)
        if not started and not (opening or row):
            continue
        started = True
        stray = opening and cur is not None and "Yes" in cur.answers and "No" not in cur.answers
        starts = opening or (row and row["answer"].lower() == "yes" and (cur is None or "Yes" in cur.answers))
        if not stray and starts and (cur is None or "Rejected" in cur.answers or "Yes" in cur.answers or opening):
            cur = Race("")
            races.append(cur)
            labels.append(before)
            before = []
        if stray:
            continue
        if opening and cur is not None:
            cur.registered, cur.cast = n(opening["reg"]), n(opening["cast"])
            continue
        if row and cur is not None:
            answer = row["answer"].capitalize()
            tokens = re.findall(r"[\d,.]+%?", row["rest"])
            if answer == "Yes" and len(tokens) >= 5:
                cur.registered, cur.cast = n(tokens[0]), n(tokens[1])
                votes = n(tokens[3])
            else:
                votes = n(tokens[0]) if tokens else 0
            cur.answers[answer] = votes
            if row["left"].strip():
                labels[-1].append(row["left"].strip())
            continue
        if REF_HEAD.search(line) and not cur:
            continue
        (labels[-1] if cur is not None and "Rejected" not in cur.answers else before).append(line.strip())
    for race, parts in zip(races, labels, strict=True):
        race.division = canonical(" ".join(parts))
    return races


def summary(body: str) -> dict[str, int]:
    """The national totals the document prints under its summary heading."""
    found: dict[str, int] = {}
    tail = re.split(r"\n\s*(?:SUMMARY|Summary)\s*:?", body)[-1]
    for line in tail.splitlines():
        match = re.match(
            rf"^\s*(Total Registered Electors|Total Votes Cast(?: for \S+(?: \(\w+\))?)?|Total Votes for [\w() ]+?|"
            rf"Total Votes Counted for \w+|Total Ballots [Rr]ejected|Rejected|Total ballots cast|Total ballots counted|"
            rf"Total votes cast for \w+|Total Ballots Rejected)\s+(?P<v>{NUMBER})",
            line,
        )
        if match:
            found[re.sub(r"\s+", " ", match.group(1)).strip()] = n(match["v"])
    return found


# ---------------------------------------------------------------- per-election repairs

PARTY_FIXES = {("2003", "Orange Walk Central", 0): "PUP", ("2003", "Orange Walk Central", 1): "UDP"}
NAME_FIXES = {
    ("2012", "Orange Walk North", 0): 'Jorge Alberto "George" Briceño',
    ("2012", "Orange Walk North", 1): 'Gaspar "Gapi" Vega',
    ("2012", "Orange Walk East", 0): 'Orlando Alexander "Landy" Burns',
    ("2012", "Orange Walk East", 1): "Dr. Marco Tulio Mendez",
    ("2012", "Dangriga", 1): 'Ivan Micahel "Maca" Ramos',
    ("2012", "Dangriga", 2): 'William "Turo" Arthur Roches',
    ("2012", "Collet", 1): 'Carolyn "Ms. C" Trench-Sandiford',
}
# The Department prints the 2015 by-election's "Total Votes Counted", which excludes rejected ballots.
COUNTED = {"2015-dangriga"}


CAST_KEYS = {"1998", "2003-cayo-south"}
SCANS = json.loads(Path(__file__).with_name("ebc_scans.json").read_text())


def scanned(key: str) -> list[Race]:
    """The digitized returns of 1993 and 1998, checked division by division and against the national totals printed."""
    data = SCANS[key]
    found: list[Race] = []
    for entry in data["divisions"]:
        race = Race(canonical(entry["division"]))
        for name in ("registered", "cast", "rejected", "valid"):
            if name in entry:
                setattr(race, name, entry[name][0])
                race.boxes[name] = (entry["page"], entry[name][1])
        for position, cand in enumerate(entry["candidates"]):
            kind = "votes" if "votes" in cand else "share"
            race.cands.append((cand["name"], cand["party"], cand.get("votes"), bool(cand.get("elected")), False))
            race.shares.append(cand.get("share"))
            race.boxes[(kind, position)] = (entry["page"], cand["box"])
        found.append(race)
        _check_division(key, race)
    _check_national(key, data["printed"], found)
    return found


def _check_division(key: str, race: Race) -> None:
    votes = [c[2] for c in race.cands]
    if key == "1993":
        # The return prints only percentages, so the winner is the candidate with the highest printed share.
        shown = [Decimal(s) for s in race.shares if s is not None]
        if len(shown) == len(race.shares) and abs(sum(shown) - 100) > Decimal("0.25"):
            raise ValueError(f"{key} {race.division}: printed shares add up to {sum(shown)}")
        best = max(range(len(shown)), key=lambda i: shown[i])
        race.cands[best] = (*race.cands[best][:3], True, False)
        race.derived_winner = True
        return
    total = sum(v for v in votes if v is not None)
    if key in CAST_KEYS and total + (race.rejected or 0) != race.cast:
        raise ValueError(f"{key} {race.division}: candidates {total} plus rejected {race.rejected} is not {race.cast}")
    if key == "1993-freetown" and total != race.valid:
        raise ValueError(f"{key} {race.division}: candidates {total} is not the printed total {race.valid}")
    elected = [c for c in race.cands if c[3]]
    if len(elected) != 1 or elected[0][2] != max(v for v in votes if v is not None):
        raise ValueError(f"{key} {race.division}: the printed winner does not hold the most votes")
    if key == "1993-freetown":
        race.derived_winner = True


def _check_national(key: str, printed: dict, found: list[Race]) -> None:
    """Compare the divisions' sums with the totals the return prints; 1993's candidates carry shares only."""
    sums: defaultdict[str, float] = defaultdict(float)
    for race in found:
        sums["registered"] += race.registered or 0
        sums["cast"] += race.cast or 0
        sums["rejected"] += race.rejected or 0
        sums["valid"] += race.valid or 0
        shown = sum(Decimal(s) for s in race.shares if s is not None)
        for (_, label, votes, _, _), share in zip(race.cands, race.shares, strict=True):
            party = party_of(label)
            if votes is not None:
                sums[party] += votes
            elif race.valid is not None:
                # an illegible share is taken as the rest of the division, for this comparison only
                rest = Decimal(share) if share is not None else 100 - shown
                sums[party] += float(rest * race.valid / 100)
    if key == "1998":
        sums["valid"] = sums["cast"] - sums["rejected"]
    for name in ("registered", "cast", "rejected", "valid"):
        if name in printed and not (key == "1993" and name == "rejected") and sums[name] != printed[name]:
            raise ValueError(f"{key}: divisions add up to {name} {sums[name]}, the return prints {printed[name]}")
    # 1993's shares are rounded to a tenth of a percent, so each party's implied votes may differ by a few dozen
    slack = 60 if key == "1993" else 0
    for party in printed["parties"].keys() | {p for p in sums if p.isupper() and p not in printed["parties"]}:
        if party in ("registered", "cast", "rejected", "valid"):
            continue
        if abs(sums[party] - printed["parties"].get(party, 0)) > slack:
            raise ValueError(
                f"{key}: {party} votes add up to {sums[party]}, the return prints {printed['parties'].get(party)}"
            )


def races(held: Held) -> list[Race]:
    if held.layout.startswith("scan-"):
        found = scanned(held.layout.removeprefix("scan-"))
    elif held.layout == "modern":
        found = modern(text(held.files[0]))
    elif held.layout == "inline":
        found = inline(text(held.files[0]), {})
    elif held.layout == "block":
        found = block(text(held.files[0]))
    elif held.layout.startswith("ballots-"):
        found = ballots(text(held.files[0]), int(held.layout[-4:]))
    else:
        found = referendum(text(held.files[0]))
    for race in found:
        for position, (name, party, votes, rem, unc) in enumerate(race.cands):
            party = PARTY_FIXES.get((held.key, race.division, position), party)
            name = NAME_FIXES.get((held.key, race.division, position), name)
            if held.key == "2024-toledo-east" and name.split()[0].isupper():
                first, *rest = name.split()
                name = " ".join([*rest, first.capitalize()])
            race.cands[position] = (re.sub(r"\s+", " ", name).replace("- ", "-").strip(), party, votes, rem, unc)
    return found


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


EBC_SOURCE = key("source", "ebc")
HOUSE = key("body", "house")
SYSTEM_FPTP = key("electoral_system", "fptp")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")


def area(division: str) -> str:
    return key("area", SCHEME, division)


def party(label: str) -> str:
    return key("party", party_of(label))


def days() -> dict[str, list[Held]]:
    """The elections grouped into packages, named by their day (or by their month, where the day is not known)."""
    found: dict[str, list[Held]] = {}
    for held in HELD:
        found.setdefault(held.package or str(held.day), []).append(held)
    return found


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=EBC_SOURCE,
        kind="official_body",
        publisher="Elections and Boundaries Commission of Belize",
        title="Election results published by the Elections and Boundaries Department",
        url=PAGE,
        license="No license stated; published by the electoral authority for public use",
        attribution="Elections and Boundaries Commission of Belize",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Belize's electoral divisions, as the Elections and Boundaries Department names them in its results",
    )
    p.external.add(BELIZE)
    p.add("scheme", code=SCHEME, polity_id=BELIZE, kind="electoral_district", rank=1, noun="electoral division")
    used = sorted({race.division for held in HELD for race in races(held)})
    for division in used:
        id = area(division)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=changeset.slug(division))
        p.name(id, division, "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Belize's parties, House of Representatives, and electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BELIZE)
    _source(p)
    for name in dict.fromkeys(name for held in HELD for name in held.files):
        p.artifact(raw.cached(SOURCE, url(name)), EBC_SOURCE)
    p.add("body", id=HOUSE, polity_id=BELIZE, kind="lower_house")
    p.name(HOUSE, "House of Representatives", "en", "official")
    for id, rules, text_ in (
        (SYSTEM_FPTP, {"formula": "fptp"}, "One member for each electoral division, elected by the most votes."),
        (SYSTEM_REFERENDUM, {"formula": "referendum"}, "A yes or no question put to all voters."),
    ):
        p.add("electoral_system", id=id, polity_id=BELIZE, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text_, lang="en")
    labels: Counter[str] = Counter()
    for held in HELD:
        for race in races(held):
            for _, label, _, _, _ in race.cands:
                if label:
                    labels[party_of(label)] += 1
    for code in sorted(labels):
        if code in INDEPENDENT:
            continue
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=BELIZE)
        p.add("external_id", entity_id=id, scheme="bz.ebc.party", value=code)
        p.name(id, code, "en", "abbreviation")
        if code in PARTY_NAMES:
            p.name(id, PARTY_NAMES[code], "en", "official", False)
    return p


METHOD = (
    "Counted for each electoral division and published by the Elections and Boundaries Department. Registered voters "
    "are the division's electors on the list, and votes cast are the total the Department prints for the division. "
    "Candidates' votes are stored as printed, and so are rejected ballots where the Department prints them."
)


def build(day: str) -> Package:
    held = days()[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Belize {' and '.join(h.kind.replace('_', '-') for h in held)} of {day} by electoral division",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BELIZE)
    for h in held:
        for name in h.files:
            p.uses(raw.cached(SOURCE, url(name)))
        _election(p, h)
    if day == "1993-06-30":
        p.accept(
            "accounting.votes_valid",
            "the scanned return prints each candidate's percentage of the votes but no candidate counts, so a "
            "division's valid votes (its printed total votes) have no candidate votes to add up to; the shares are "
            "stored as printed instead",
        )
    if day == "2008-02-07":
        p.accept(
            "accounting.declared",
            "the referendum results page prints national totals that differ from the sum of its own divisional rows: "
            "registered electors 156,993 against 157,520, votes cast 73,213 against 72,425, No votes 26,793 against "
            "26,061, and rejected ballots 1,363 against 1,307; the Yes total of 45,057 agrees. Each division's own "
            "Yes, No, and rejected ballots add up to its printed votes cast, so the divisional rows are stored as printed",
        )
    return p


def _election(p: Package, held: Held) -> None:
    election = key("election", held.key)
    label = {"general": "general", "by_election": "by-election", "referendum": "referendum"}[held.kind]
    path = held.slug or f"bz/{held.day}-{label}" + (
        f"-{held.key.split('-', 1)[1]}" if held.kind == "by_election" else ""
    )
    p.add(
        "election",
        id=election,
        polity_id=BELIZE,
        kind=held.kind,
        date_from=held.day,
        date_to=held.date_to or held.day,
        date_precision=held.precision,
        original_text=held.printed,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, held.english, "en", "official")
    if held.note:
        p.add("note", id=key("note", election), entity_id=election, kind="caveat", body_md=held.note, lang="en")
    found = races(held)
    if held.layout.startswith("scan-"):
        _pages(p, held)
    units: dict[str, str] = {}
    for race in found:
        unit = units[race.division] = key("unit", held.key, race.division)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BELIZE,
            scheme_code=SCHEME,
            code=f"{held.key}:{changeset.slug(race.division)}",
            area_relation="equals",
            area_id=area(race.division),
            kind="standard",
        )
    if held.kind == "referendum":
        _referendum(p, held, election, path, found, units)
    else:
        for race in found:
            _contest(p, held, election, path, race, units[race.division])


def _put(
    p: Package,
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    value: int | Decimal | None,
    flags: int = 0,
    where: tuple[str, list[int]] | None = None,
) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=flags,
        value=value,
    )
    if where is not None:
        page, (x0, y0, x1, y1) = where
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
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
        )


def _page(p: Package, held: Held, number: int) -> str:
    return p.id("page", raw.cached(SOURCE, url(held.files[0])).sha256, number)


def _pages(p: Package, held: Held) -> None:
    """Register the pages of a scanned return; boxes are in pixels of each page rendered at 100 dpi."""
    stored = raw.cached(SOURCE, url(held.files[0]))
    artifact = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{EBC_SOURCE}\x1f{stored.storage_key}"))
    for number, (width, height) in SCANS[held.layout.removeprefix("scan-")]["pages"].items():
        p.add(
            "source_page",
            id=_page(p, held, int(number)),
            artifact_id=artifact,
            page_number=int(number),
            image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
            width=width,
            height=height,
        )


def _result_set(p: Package, tag: str, election: str, contest: str, tacit: bool, method: str) -> str:
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if tacit else "counted",
        source_id=EBC_SOURCE,
        status="active",
        is_default=True,
        completeness=None if tacit else 1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return result_set


DIGITIZED_METHOD = (
    "Digitized from the Department's scanned return: every figure was read from the page image, checked against the "
    "totals the return prints, and keeps its page and bounding box."
)
SCAN_METHOD = {
    "1998": (
        f"{METHOD} {DIGITIZED_METHOD} Each division's candidates' votes plus its spoilt and rejected ballots equal "
        "its votes cast. Percentages are not stored."
    ),
    "1993": (
        "Counted for each electoral division and published by the Elections and Boundaries Department. "
        f"{DIGITIZED_METHOD} The return prints each candidate's percentage of the votes but no candidate counts, so "
        "the shares are stored as printed, to a tenth of a percent, and no counts are derived from them. A "
        "division's total votes are stored as valid votes: the 29 divisional totals add up to the 70,431 votes the "
        "return prints as the total voted, and the 499 rejected ballots it prints are given for the country only."
    ),
    "2003-cayo-south": (
        f"{METHOD} {DIGITIZED_METHOD} The candidates' votes plus the rejected ballots equal the votes cast. The "
        "return also prints 6,422 ballots used and 20 spoilt, which are not stored. Percentages are not stored."
    ),
    "1993-freetown": (
        f"Counted for the electoral division and published by the Elections and Boundaries Department. "
        f"{DIGITIZED_METHOD} The return prints the votes polled, which do not include its 7 spoilt and rejected "
        "ballots, so they are stored as valid votes. Percentages are not stored."
    ),
}
SCAN_WINNER = {
    "1993": (
        " The return does not mark the winners; the candidate with the highest printed share is stored as elected."
    ),
    "1993-freetown": " The return does not mark the winner; the candidate with the most votes is stored as elected.",
}


def _contest(p: Package, held: Held, election: str, path: str, race: Race, unit: str) -> None:
    tag = f"{held.key}:{race.division}"
    contest = key("contest", tag)
    uncontested = all(c[4] for c in race.cands)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=HOUSE,
        district_area_id=area(race.division),
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM_FPTP,
        default_result_kind="tacit" if uncontested else "counted",
    )
    p.name(contest, f"{held.english}, {race.division}", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug(race.division)}", entity_id=contest, is_current=True)
    method = METHOD
    if held.key == "2025":
        method += (
            " The Department prints UDP's candidates with a party-colour mark, UDP (S) or UDP (T); all are stored under "
            "the United Democratic Party."
        )
    if held.key in COUNTED:
        method = (
            "Counted for the electoral division and published by the Elections and Boundaries Department. The "
            "Department prints the votes counted, which exclude rejected ballots, so they are stored as valid votes."
        )
    scan = held.layout.startswith("scan-")
    shared = any(share is not None for share in race.shares)
    if scan:
        method = SCAN_METHOD[held.key]
        if race.derived_winner:
            method += SCAN_WINNER[held.key]
        if shared and None in race.shares:
            method += (
                " The return prints ERR in place of one candidate's percentage in this division, so no share is "
                "stored for that candidate."
            )
    flags = DIGITIZED if scan else 0

    def at(name: object) -> tuple[str, list[int]] | None:
        found = race.boxes.get(name) if scan else None
        return None if found is None else (_page(p, held, found[0]), found[1])

    result_set = _result_set(p, tag, election, contest, uncontested, method)
    for position, (name, label, votes, elected, _) in enumerate(race.cands):
        option = key("option", tag, position)
        solo = party_of(label) in INDEPENDENT or not label
        owner = None if solo else party(label)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=owner,
            ballot_position=position + 1,
            ballot_label=name or party_of(label),
        )
        if name:
            person = key("person", tag, position)
            p.add("person", id=person)
            p.name(person, name, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", tag, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=owner,
                is_independent=solo,
            )
        if uncontested or elected:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        if uncontested:
            continue
        if shared:
            share = race.shares[position]
            if share is not None:
                _put(
                    p, result_set, unit, option, VOTE_SHARE, Decimal(share), DIGITIZED | SHARES, at(("share", position))
                )
        else:
            _put(p, result_set, unit, option, VOTES, votes, flags, at(("votes", position)))
    if uncontested:
        return
    if held.key in COUNTED:
        _put(p, result_set, unit, None, 4, race.cast)
    else:
        _put(p, result_set, unit, None, CAST, race.cast, flags, at("cast"))
    _put(p, result_set, unit, None, VALID, race.valid, flags, at("valid"))
    _put(p, result_set, unit, None, REGISTERED, race.registered, flags, at("registered"))
    _put(p, result_set, unit, None, INVALID, race.rejected, flags, at("rejected"))


REFERENDUM_METHOD = (
    "Counted for each electoral division and published by the Elections and Boundaries Department. Votes cast are the "
    "total the Department prints for the division, and its Yes, No, and rejected ballots add up to it."
)


def _referendum(p: Package, held: Held, election: str, path: str, found: list[Race], units: dict[str, str]) -> None:
    contest = key("contest", held.key)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        round_number=1,
        electoral_system_id=SYSTEM_REFERENDUM,
        default_result_kind="counted",
    )
    p.name(contest, held.english, "en", "official")
    p.add("slug", path=f"{path}/question", entity_id=contest, is_current=True)
    p.add("ballot_question", contest_id=contest, type="referendum")
    result_set = _result_set(p, held.key, election, contest, False, REFERENDUM_METHOD)
    options = {}
    for position, answer in enumerate(("Yes", "No"), 1):
        option = options[answer] = key("option", held.key, answer)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=answer,
        )
    for race in found:
        unit = units[race.division]
        for answer, option in options.items():
            _put(p, result_set, unit, option, VOTES, race.answers[answer])
        _put(p, result_set, unit, None, CAST, race.cast)
        _put(p, result_set, unit, None, REGISTERED, race.registered)
        _put(p, result_set, unit, None, INVALID, race.answers["Rejected"])
    totals = summary(text(held.files[0]))
    for measure, label in (
        (REGISTERED, "Total Registered Electors"),
        (CAST, "Total Votes Cast"),
        (INVALID, "Total Ballots rejected"),
    ):
        if label in totals:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=totals[label])
    for answer, option in options.items():
        value = totals.get(f"Total Votes Cast for {answer.upper()}")
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
