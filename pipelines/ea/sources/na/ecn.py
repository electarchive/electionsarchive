"""Namibia's national election authority: the Electoral Commission of Namibia's published results.

The Commission publishes results as Government Gazette notices and printed tables on its website. Constituency
results come from regional council elections (2010, 2015, 2020, and the 2025 by-elections) and from the 2014 National
Assembly table; local authority results come from the 2020 gazette; the 2024 presidential and National Assembly
results are gazetted nationally. Every file has a text layer, read here with ``pdftotext -layout``. The 1998 and 2004
regional council and local authority tables are spreadsheets, read here with ``xlrd``.
"""

import difflib
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from uuid import uuid5

import xlrd

from ea import package, raw
from ea.package import Package

SOURCE = "na.ecn"
SITE = "https://www.ecn.na"
UPLOADS = f"{SITE}/wp-content/uploads"
PAGES = f"{SITE}/previous-election-results/"
BY_ELECTIONS_PAGE = f"{SITE}/by-elections/"
NAMIBIA = str(uuid5(package.NAMESPACE, "polity\x1fNA"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

URLS = {
    "rc2010": f"{UPLOADS}/2021/06/Regional-Councils-2010.pdf",
    "na2014": f"{UPLOADS}/2021/06/FINAL-RESULTS-Elections-2014.pdf",
    "rc2015": f"{UPLOADS}/2021/06/Regional-Counils-Election-Results_Duly-elected-candidates-2015.pdf",
    "rc2020": f"{UPLOADS}/2023/02/7654-Gov-N219-Results-Regional-Council-2020.pdf",
    "la2020": f"{UPLOADS}/2021/06/7497-Gov-N65-Results-LA-2020.pdf",
    "pna2024": f"{UPLOADS}/2025/03/Results-publication-2024PNA.pdf",
    "rcby2025": f"{UPLOADS}/2025/10/Govt-Gazette-five-by-elections-result.pdf",
    "rc1998": f"{UPLOADS}/2021/06/REgional-Councils-Elections-1998.xls",
    "la1998": f"{UPLOADS}/2021/06/Local-Authourities-Elections-1998.xls",
    "rc2004": f"{UPLOADS}/2021/06/Regional-Councils-Election-2004.xls",
    "la2004": f"{UPLOADS}/2021/06/local-Authourity-Election-2004.xls",
}
DAYS = {
    "1998-02-16": date(1998, 2, 16),
    "1998-11-30": date(1998, 11, 30),
    "2004-11-15": date(2004, 11, 15),
    "2010-11-27": date(2010, 11, 27),
    "2014-11-28": date(2014, 11, 28),
    "2015-11-27": date(2015, 11, 27),
    "2020-11-25": date(2020, 11, 25),
    "2024-11-27": date(2024, 11, 27),
    "2025-06-17": date(2025, 6, 17),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECN_SOURCE = key("source", "ecn")
ASSEMBLY = key("body", "national-assembly")
REGIONAL = key("body", "regional-councils")
LOCAL = key("body", "local-authority-councils")
PRESIDENCY = key("office", "president")
SYSTEM_PRESIDENT = key("electoral_system", "president")
SYSTEM_ASSEMBLY = key("electoral_system", "national-assembly")
SYSTEM_REGIONAL = key("electoral_system", "regional-council")
SYSTEM_LOCAL = key("electoral_system", "local-authority")

# Every party printed in the Commission's tables, by the key its own abbreviation gives, with its full name.
PARTIES = {
    "ADM": "Action Democratic Movement",
    "AR": "Affirmative Repositioning",
    "APP": "All People's Party",
    "BCP": "Body of Christ Party",
    "CDV": "Christian Democratic Voice Party",
    "COD": "Congress of Democrats",
    "DCN": "Democratic Coalition of Namibia",
    "FCN": "Federal Convention of Namibia",
    "NDMC": "Namibia Democratic Movement for Change",
    "DPN": "Democratic Party of Namibia",
    "DTA": "Democratic Turnhalle Alliance of Namibia",
    "IPC": "Independent Patriots for Change",
    "LPM": "Landless People's Movement",
    "MAG": "Monitor Action Group",
    "NDP": "National Democratic Party of Namibia",
    "NEFC": "National Empowerment Fighting Corruption",
    "NEFF": "Namibia Economic Freedom Fighters",
    "NUDO": "National Unity Democratic Organisation",
    "RDP": "Rally for Democracy and Progress",
    "RP": "Republican Party of Namibia",
    "SWANU": "SWANU of Namibia",
    "SWAPO": "SWAPO Party of Namibia",
    "UDF": "United Democratic Front of Namibia",
    "UNP": "United Namibians Party",
    "UPM": "United People's Movement",
    "WRP": "Workers Revolutionary Party",
}
# The Democratic Turnhalle Alliance took the name Popular Democratic Movement; one party carries both names.
ALIASES = {
    "swapo party of namibia": "SWAPO",
    "swapo": "SWAPO",
    "dta of namibia": "DTA",
    "dta": "DTA",
    "pdm": "DTA",
    "popular democratic movement": "DTA",
    "rdp of namibia": "RDP",
    "rdp": "RDP",
    "rally for democracy and progress": "RDP",
    "udf of namibia": "UDF",
    "udf": "UDF",
    "united democratic front": "UDF",
    "united democratic front of namibia": "UDF",
    "nudo of namibia": "NUDO",
    "nudo": "NUDO",
    "national unity democratic organisation": "NUDO",
    "dpn of namibia": "DPN",
    "dpn": "DPN",
    "democratic party of namibia": "DPN",
    "united people’s movement": "UPM",
    "united people's movement": "UPM",
    "upm of namibia": "UPM",
    "upm": "UPM",
    "congress of democrats": "COD",
    "cod": "COD",
    "all people's party": "APP",
    "all people’s party": "APP",
    "app": "APP",
    "swanu of namibia": "SWANU",
    "swanu": "SWANU",
    "republican party of namibia": "RP",
    "rp": "RP",
    "national democratic party of namibia": "NDP",
    "ndp": "NDP",
    "monitor action group": "MAG",
    "mag": "MAG",
    "workers revolutionary party": "WRP",
    "wrp": "WRP",
    "christian democratic voice": "CDV",
    "cdv": "CDV",
    "namibia economic freedom fighters": "NEFF",
    "neff": "NEFF",
    "landless people's movement": "LPM",
    "landless people’s movement": "LPM",
    "lpm": "LPM",
    "independent patriots for change": "IPC",
    "independent patriots of change": "IPC",
    "ipc": "IPC",
    "affirmative repositioning": "AR",
    "ar": "AR",
    "action democratic movement": "ADM",
    "adm": "ADM",
    "body of christ party": "BCP",
    "bcp": "BCP",
    "united namibians party": "UNP",
    "unp": "UNP",
    "republican party": "RP",
    "rally of namibia": "RDP",
    "rally for democracy": "RDP",
    "national unity democratic organisation of namibia": "NUDO",
    "national unity democratic organization": "NUDO",
    "united people’s movement of namibia": "UPM",
    "national empowerment fighting corruption": "NEFC",
    "cod of namibia": "COD",
    "swapo party": "SWAPO",
    "app of namibia": "APP",
    "swapo-party": "SWAPO",
    "dcn": "DCN",
    "democratic coalition of namibia": "DCN",
    "fcn": "FCN",
    "federal convention of namibia": "FCN",
    "ndmc": "NDMC",
    "namibia dmc": "NDMC",
    "namibia-dmc": "NDMC",
    "namibia democratic movement for change": "NDMC",
}
HEADER_ROWS = {"IN RESPECT OF A", "REGIONAL COUNCIL", "CONSTITUENCY FOR", "CONSTITUENCY", "AND", "COUNCIL"}
INDEPENDENT = "independent candidate"

METHOD = (
    "Counted per constituency or local authority area and published by the Electoral Commission of Namibia. "
    "Votes recorded for each candidate or party are as printed; the totals are the Commission's own."
)


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


LOCAL_PARTIES: dict[str, str] = {}  # local associations on local authority ballots, by abbreviation
ABBREVIATIONS = {"PDM": "DTA", "COD": "COD"}


def party_of(label: str) -> str | None:
    """The key of the party a printed label names; None for an independent candidate."""
    label = clean(label)
    if "(" not in label:
        label = re.sub(r"\s(\w+)\)$", r" (\1)", label)
    found = re.search(r"\(([^()]*)\)?$", label)
    name = clean(label[: found.start()] if found else label)
    abbreviation = clean(found.group(1)).upper() if found else ""
    if name.casefold() == INDEPENDENT:
        return None
    if name.casefold() in ALIASES:
        return ALIASES[name.casefold()]
    if abbreviation in ABBREVIATIONS:
        return ABBREVIATIONS[abbreviation]
    if abbreviation in PARTIES:
        return abbreviation
    if abbreviation:
        code = re.sub(r"\W+", "_", abbreviation)
        LOCAL_PARTIES.setdefault(code, name)
        return code
    raise ValueError(f"party {label!r} is not in the list of the Commission's parties")


def party_id(code: str) -> str:
    return key("party", code)


def text(name: str) -> list[str]:
    """The pages of a cached file as layout text, one string per page."""
    path = raw.cached(SOURCE, URLS[name]).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return done.stdout.split("\f")


def number(value: str) -> int:
    return int(re.sub(r"[ , ]", "", value))


def segments(line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]


@dataclass
class Candidate:
    name: str
    party: str | None
    votes: int | None
    elected: bool = False
    label: str = ""  # the printed party text, before it is resolved


@dataclass
class Contest:
    region: str
    name: str
    registered: int | None = None
    counted: int | None = None  # votes counted, which the printed tables sum from the candidates' votes
    cast: int | None = None  # ballots cast, valid and rejected together, where the table prints them
    rejected: int | None = None
    candidates: list[Candidate] = field(default_factory=list)
    declared: str = ""  # the printed name of the candidate declared elected
    tacit: bool = False


def region_name(label: str) -> str:
    return clean(re.sub(r"\s*REGION$", "", label.strip().lstrip("/!"))).title()


# --- 2010 regional councils ------------------------------------------------------------------------------------------


def read_2010() -> list[Contest]:
    contests: list[Contest] = []
    region = ""
    current: Contest | None = None
    pending: Contest | None = None  # a row printed without its constituency, which the line after it names
    skip = ("Column", "Total number", "Constituency", "(Registered", "votes (rejected", "ballot papers", "SCHEDULE")
    for page in text("rc2010"):
        for line in page.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(skip) or stripped.startswith(("(registered", "REGIONAL")):
                continue
            if re.fullmatch(r"[A-Z!/ ]{4,}", stripped):
                region = stripped
                continue
            if stripped.startswith(("Votes", "recorded for", "Candidate")) and not re.search(r"\d", stripped):
                continue
            lead = re.match(r"^\(([\d,]+)\)\s{2,}(\S.*)$", stripped)
            if lead and current is not None and current.registered is None:
                current.registered = number(lead.group(1))
                stripped = lead.group(2)
            found = re.match(r"^(.*?)\s{2,}(\d+) \((\d+)\)\s*(.*)$", line.rstrip())
            if found:
                name = clean(found.group(1))
                current = Contest(region_name(region), name, counted=int(found.group(2)), rejected=int(found.group(3)))
                contests.append(current)
                registered = re.search(r"\(([\d,]+)\)$", name)
                if registered:
                    current.registered = number(registered.group(1))
                    current.name = clean(name[: registered.start()])
                if not name:
                    pending = current
                stripped = found.group(4).strip()
                if not stripped:
                    continue
            if current is None:
                continue
            only = re.fullmatch(r"\(([\d,]+)\)", stripped)
            if only and current.registered is None:
                current.registered = number(only.group(1))
                continue
            named = re.match(r"^(.*?)\s*\(([\d,]+)\)\s{2,}(.*)$", stripped)
            if named and pending is not None and not current.name:
                current.name = clean(named.group(1))
                current.registered = number(named.group(2))
                pending = None
                stripped = named.group(3)
            if "Duly Elected" in stripped:
                head, _, rest = stripped.partition("Duly Elected")
                registered = re.search(r"\(([\d,]+)\)", head)
                current = Contest(
                    region_name(region),
                    clean(re.sub(r"\([\d,]+\)", "", head)),
                    registered=number(registered.group(1)) if registered else None,
                    tacit=True,
                )
                contests.append(current)
                parts = re.split(r"\s{2,}", rest.strip())
                current.candidates.append(Candidate(parts[0], party_of(parts[1]), None, True))
                continue
            parts = re.split(r"\s{2,}", stripped)
            if len(parts) >= 3 and re.fullmatch(r"\d+", parts[-1]):
                current.candidates.append(Candidate(parts[-3], party_of(parts[-2]), int(parts[-1])))
            else:
                raise ValueError(f"unreadable 2010 line {line!r}")
    return contests


# --- regional councils of 2015, 2020, and the 2025 by-elections ------------------------------------------------------


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _mode(values: list[int]) -> int:
    return max(sorted(set(values)), key=values.count)


def read_regional(name: str, columns: tuple[int, int, int] | None = None) -> list[Contest]:
    """Constituency tables whose columns are the constituency, its votes, each candidate's votes, name, and party."""
    contests: list[Contest] = []
    region = ""
    current: Contest | None = None
    elected_at = 0
    for page in text(name):
        rows = [segments(line) for line in page.splitlines() if line.strip()]
        rows = [row for row in rows if not (len(row) == 1 and row[0][1].startswith("Government Gazette"))]
        both = [
            [p for p, t in row if t.isdigit()]
            for row in rows
            if sum(1 for p, t in row if t.isdigit() and 10 <= p < 60) >= 2
        ]
        if not both:
            continue
        boundary = (_median([b[0] for b in both]) + _median([b[1] for b in both])) // 2
        lead = min(b[0] for b in both) - 2
        if columns:
            votes_at = _median([b[1] for b in both])
            cand_at, party_at, elected_at = (votes_at + offset for offset in columns)
        else:
            after = [[(p, t) for p, t in row if p > boundary + 8 and not t.isdigit()] for row in rows]
            after = [
                a
                for a in after
                if a and any(t.isdigit() and lead <= p <= boundary + 8 for p, t in rows[after.index(a)])
            ]
            cand_at = _mode([a[0][0] for a in after])
            party_at = _mode([a[1][0] for a in after if len(a) > 1])
            heads = [p for row in rows[:12] for p, t in row if t.startswith(("CANDIDATE DECLARED", "DULY ELECTED"))]
            thirds = [a[2][0] for a in after if len(a) > 2]
            elected_at = min([*heads, *([_mode(thirds)] if len(thirds) > 2 else [])] or [elected_at])
        started = False
        pending = ""
        for row in rows:
            row = [(p, t) for p, t in row if p < elected_at + 30]
            if not row:
                continue
            nums = [(p, t) for p, t in row if t.isdigit() and lead <= p < cand_at - 1]
            if len(row) == 1 and row[0][0] < 10 and not nums and re.fullmatch(r"[A-Z!/ ]+", row[0][1]):
                heading = row[0][1].strip()
                if heading.endswith(" REGION"):
                    region, pending, started = heading, "", True
                elif heading == "REGION" and pending:
                    region, pending, started = pending, "", True
                elif heading not in HEADER_ROWS and len(heading) > 3:
                    pending = heading
                continue
            pending = "" if nums else pending
            if not started and not nums:
                continue
            started = True
            label = clean(" ".join(t for p, t in row if p < lead))
            if any(t.casefold().startswith("duly elected") and lead <= p < cand_at - 1 for p, t in row):
                current = Contest(region_name(region), label, tacit=True)
                contests.append(current)
                current.candidates.append(Candidate("", None, None))
                nums = []
                label = ""
            totals = [t for p, t in nums if p < boundary]
            amounts = [t for p, t in nums if p >= boundary]
            if totals:
                current = Contest(region_name(region), label, counted=int(totals[0]))
                contests.append(current)
            elif label and current is not None:
                current.name = clean(f"{current.name} {label}")
            if current is None:
                continue
            if amounts:
                current.candidates.append(Candidate("", None, int(amounts[0])))
            if not current.candidates:
                continue
            last = current.candidates[-1]
            cand = clean(" ".join(t for p, t in row if cand_at - 1 <= p < party_at - 1 and not t.isdigit()))
            right = [(p, t) for p, t in row if p >= party_at - 1]
            party = clean(right[0][1]) if right and right[0][0] < elected_at - 3 else ""
            elected = clean(" ".join(t for p, t in right[1 if party else 0 :]))
            last.name = clean(f"{last.name} {cand}")
            last.label = clean(f"{last.label} {party}")
            current.declared = clean(f"{current.declared} {elected}")
    for contest in contests:
        for candidate in contest.candidates:
            label = candidate.label
            if not label:  # a long name that runs into the party column with a single space
                lowered = candidate.name.casefold()
                match = next(
                    (a for a in sorted([*ALIASES, INDEPENDENT], key=len, reverse=True) if lowered.endswith(f" {a}")), ""
                )
                if match:
                    label = candidate.name[len(candidate.name) - len(match) :]
                    candidate.name = candidate.name[: len(candidate.name) - len(match)].strip()
            try:
                candidate.party = party_of(label)
            except ValueError:
                print(name, contest.region, contest.name, candidate)
                raise
        top = max(contest.candidates, key=lambda c: c.votes or 0)
        if contest.tacit:
            contest.candidates[0].elected = True
            contest.declared = ""
            continue
        for candidate in contest.candidates:
            if contest.declared and _same(candidate.name, contest.declared):
                candidate.elected = True
        if contest.declared and not any(c.elected for c in contest.candidates):
            raise ValueError(f"{contest.name}: declared {contest.declared!r} matches no candidate")
        if contest.declared and not top.elected:
            print(f"note: {name} {contest.name}: declared winner {contest.declared!r} is not the top vote getter")
    return contests


def _same(a: str, b: str) -> bool:
    x, y = re.sub(r"\W", "", a.casefold()), re.sub(r"\W", "", b.casefold())
    return x == y or x.startswith(y) or y.startswith(x)


# --- 2014 National Assembly by constituency ---------------------------------------------------------------------------

PARTIES_2014 = [
    "APP",
    "CDV",
    "COD",
    "DPN",
    "DTA",
    "MAG",
    "NEFF",
    "NDP",
    "NUDO",
    "RDP",
    "RP",
    "SWANU",
    "SWAPO",
    "UDF",
    "UPM",
    "WRP",
]


@dataclass
class Row:
    region: str
    name: str
    registered: int | None
    votes: list[int]
    total: int | None = None


def read_2014() -> tuple[list[Row], Row, int]:
    """The constituency rows, the printed national row, and the printed national total of votes."""
    left: list[Row] = []
    totals: list[list[int]] = []
    region = ""
    right: list[list[int]] = []
    counted: list[int] = []
    national = 0
    for line in "\n".join(text("na2014")).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("Registered Voters", "NDP ")):
            continue
        found = re.match(r"^([A-Za-z!/#].*?)\s{2,}([\d,]+)\s+((?:\d+\s+){6}\d+)$", stripped)
        intl = re.match(r"^(International votes)\s+((?:\d+\s+){6}\d+)$", stripped)
        if re.fullmatch(r"[A-Z!/ ]{4,}", stripped):
            region = stripped
        elif found and found.group(1) != "TOTAL":
            left.append(
                Row(
                    region_name(region),
                    found.group(1),
                    number(found.group(2)),
                    [int(v) for v in found.group(3).split()],
                )
            )
        elif found:
            totals.append([number(found.group(2)), *(int(v) for v in found.group(3).split())])
        elif intl:
            left.append(Row("International", "International votes", None, [int(v) for v in intl.group(2).split()]))
        elif re.fullmatch(r"(\d+\s+){8}\d+", stripped):
            right.append([int(v) for v in stripped.split()])
        elif re.fullmatch(r"\d+", stripped):
            counted.append(int(stripped))
        elif re.fullmatch(r"\d+ \d{3}", stripped) and stripped.startswith(("893", "89")):
            national = number(stripped)
        elif re.match(r"^TOTAL:?\s*[\d ]+$", stripped):
            continue
    if len(right) != len(left) + 1 or len(counted) != len(left):
        raise ValueError(f"2014 table halves disagree: {len(left)} left, {len(right)} right, {len(counted)} totals")
    for row, more, total in zip(left, right[:-1], counted, strict=True):
        row.votes += more
        row.total = total
    last = Row("", "Total", totals[-1][0], [*totals[-1][1:], *right[-1]], national)
    return left, last, national


# --- 2020 local authority councils ------------------------------------------------------------------------------------


@dataclass
class Entry:
    label: str
    votes: int
    seats: int
    names: list[str] = field(default_factory=list)


@dataclass
class Area:
    region: str
    name: str
    cast: int | None = None
    quota: str = ""
    lists: list[Entry] = field(default_factory=list)
    registered: int | None = None
    valid: int | None = None
    rejected: int | None = None


def read_local() -> list[Area]:
    areas: list[Area] = []
    region = ""
    current: Area | None = None
    for page in text("la2020"):
        header = True
        for line in page.splitlines():
            row = [(p, t) for p, t in segments(line) if not t.startswith("Government Ga")]
            if not row or row[0][1].startswith(("Government Gazette", "Column", "Local authority")):
                continue
            if len(row) == 1 and row[0][0] < 3 and re.fullmatch(r"[/!A-Z -]+", row[0][1]):
                region = row[0][1].replace("-", " ")
                header = False
                continue
            party = next(
                (
                    i
                    for i, (p, t) in enumerate(row)
                    if p >= 80 and i + 1 < len(row) and t.isdigit() and row[i + 1][1].isdigit() and row[i + 1][0] >= 95
                ),
                None,
            )
            if party is None:
                if header or current is None or not current.lists:
                    continue
                for p, t in row:
                    if p < 10 and re.search(r"[a-z]", t):
                        current.name = clean(f"{current.name} {t}")
                    elif 38 <= p <= 60 and not t.isdigit():
                        current.lists[-1].label = clean(f"{current.lists[-1].label} {t}")
                    elif p >= 100 and re.search(r"[A-Za-z]", t):
                        current.lists[-1].names.append(t)
                continue
            header = False
            first = next(i for i, (p, t) in enumerate(row) if p >= 38 and not re.fullmatch(r"[\d.,]+", t))
            before, label, after = row[:first], row[first:party], row[party:]
            if len(before) >= 3:
                current = Area(region_name(region), before[0][1], number(before[1][1]), before[2][1])
                areas.append(current)
            elif current is None:
                raise ValueError(f"local authority row without an area: {line!r}")
            current.lists.append(
                Entry(
                    clean(" ".join(t for _, t in label)),
                    number(after[0][1]),
                    number(after[1][1]),
                    [after[2][1]] if len(after) > 2 else [],
                )
            )
    return areas


# --- 2024 presidential and National Assembly elections ----------------------------------------------------------------

THOUSANDS = r"\d{1,3}(?: \d{3})*"


@dataclass
class National:
    president_counted: int
    president: list[tuple[str, str, int]]  # name, party label, votes
    winner: str
    cast: int
    counted: int
    quota: int
    lists: list[tuple[str, int, int]]  # party label, votes, seats
    members: list[list[str]]  # per list, the names declared elected in column 8, in printed order


def _wrap(named: list[str], entry: str) -> None:
    """Add a column 8 entry: a new name when it has a comma, else the rest of a name that wrapped."""
    if "," in entry:
        named.append(entry)
    elif named:
        named[-1] = f"{named[-1]} {entry}"


def read_2024() -> National:
    pages = text("pna2024")
    president: list[tuple[str, str, int]] = []
    president_counted = 0
    winner = ""
    for line in pages[1].splitlines():
        row = segments(line)
        if not row or row[0][1].startswith("Government Gazette"):
            continue
        if row[0][0] == 0 and re.fullmatch(THOUSANDS, row[0][1]):
            president_counted = number(row[0][1])
            row = row[1:]
        if len(row) >= 3 and re.fullmatch(THOUSANDS, row[0][1]) and row[0][0] == 15:
            president.append((row[1][1], row[2][1], number(row[0][1])))
            if row[-1][0] >= 120 and not winner:
                winner = row[-1][1]
    lists: list[tuple[str, int, int]] = []
    members: list[list[str]] = []
    cast = counted = quota = 0
    for page in pages[3:]:
        raw_lines = [
            line for line in page.splitlines() if "Government Gazette" not in line and not line.strip().isdigit()
        ]
        # Column 8 holds "Surname, Given names" entries, all starting at one offset on a page.
        offsets = [p for line in raw_lines for p, t in segments(line) if "," in t and not re.fullmatch(r"[\d,]+", t)]
        names_at = max(set(offsets), key=offsets.count) if offsets else len(max(raw_lines, key=len, default=""))
        for line in raw_lines:
            tail = "" if len(line[names_at - 1 : names_at + 1].strip()) == 2 else line[names_at:].strip()
            row = segments(line[:names_at] if tail else line)
            if not row:
                if tail and members:
                    _wrap(members[-1], tail)
                continue
            at = next(
                (
                    i
                    for i, (_, t) in enumerate(row)
                    if i + 1 < len(row) and re.fullmatch(THOUSANDS, t) and re.fullmatch(r"\d{1,2}", row[i + 1][1])
                ),
                None,
            )
            if at is None:
                if tail and members:
                    _wrap(members[-1], tail)
                if lists and not lists[-1][0].endswith(")") and row[0][1].endswith(")"):
                    lists[-1] = (clean(f"{lists[-1][0]} {row[0][1]}"), lists[-1][1], lists[-1][2])
                continue
            head = row[:at]
            if head and re.fullmatch(r"[\d,]+", head[0][1]):
                cast, counted, quota = number(head[0][1]), number(head[1][1]), number(head[2][1])
                head = head[3:]
            lists.append((clean(" ".join(t for _, t in head)), number(row[at][1]), int(row[at + 1][1])))
            members.append([])
            if tail:
                _wrap(members[-1], tail)
    for (label, _, won), named in zip(lists, members, strict=True):
        if len(named) != won:
            raise ValueError(f"{label}: {won} seats but {len(named)} elected names")
    return National(
        president_counted,
        president,
        winner,
        cast,
        counted,
        quota,
        lists,
        members,
    )


# --- 1998 and 2004 regional councils and local authorities (spreadsheets) ---------------------------------------------

# Local associations on the 1998 and 2004 ballots. The tables truncate the names, so each is found by its opening words.
ASSOCIATION_NAMES = {
    "CONCERNED_PEOPLES_ORGANISATION_KEETMANSHOOP": "Concerned Peoples Organisation Keetmanshoop",
    "GOBABIS_RESIDENTS_ASSOCIATION": "Gobabis Residents Association",
    "LOCAL_COMMUNITY_ASSOCIATION": "Local Community Association",
    "OKAHANDJA_RATE_PAYERS_ASSOCIATION": "Okahandja Rate Payers Association",
    "OMARURU_RESIDENTS_ASSOCIATION": "Omaruru Residents Association",
    "OTAVI_RESIDENTS_ASSOCIATION": "Otavi Residents Association",
    "REHOBOTH_RATE_PAYERS_ASSOCIATION": "Rehoboth Rate Payers Association",
    "SWAKOPMUND_RESIDENTS_ASSOCIATION": "Swakopmund Residents Association",
    "CIVIC_ASSOCIATION_OF_HENTIES_BAY": "Civic Association of Henties Bay",
    "KATIMA_ALLIANCE_DEVELOPMENT_ASSOCIATION": "Katima Alliance Development Association",
    "OTJIWARONGO_RESIDENTS_ASSOCIATION": "Otjiwarongo Residents Association",
    "ADA_GUI_SENIOR_CITIZENS_AND_DESTITUTE_CHILDRENS_ASSOCIATION": (
        "Ada /Gui Senior Citizens and Destitute Children's Association"
    ),
}
ASSOCIATIONS = {
    r"concerned peop": "CONCERNED_PEOPLES_ORGANISATION_KEETMANSHOOP",
    r"gobabis res": "GOBABIS_RESIDENTS_ASSOCIATION",
    r"local community": "LOCAL_COMMUNITY_ASSOCIATION",
    r"lca$": "LOCAL_COMMUNITY_ASSOCIATION",
    r"okahandja rate": "OKAHANDJA_RATE_PAYERS_ASSOCIATION",
    r"omaruru res": "OMARURU_RESIDENTS_ASSOCIATION",
    r"otavi res": "OTAVI_RESIDENTS_ASSOCIATION",
    r"rehoboth rate": "REHOBOTH_RATE_PAYERS_ASSOCIATION",
    r"swakopmund res": "SWAKOPMUND_RESIDENTS_ASSOCIATION",
    r"civic assoc": "CIVIC_ASSOCIATION_OF_HENTIES_BAY",
    r"katima alliance": "KATIMA_ALLIANCE_DEVELOPMENT_ASSOCIATION",
    r"ot(?:ij|i)warongo res": "OTJIWARONGO_RESIDENTS_ASSOCIATION",
    r"ada ?/ ?gui": "ADA_GUI_SENIOR_CITIZENS_AND_DESTITUTE_CHILDRENS_ASSOCIATION",
}


def old_party(label: str) -> str | None:
    """The key of the party or association a printed label of the 1998 and 2004 tables names."""
    label = clean(label)
    lowered = label.casefold()
    if lowered.startswith("independent"):
        return None
    for pattern, code in ASSOCIATIONS.items():
        if re.match(pattern, lowered):
            LOCAL_PARTIES.setdefault(code, ASSOCIATION_NAMES[code])
            return code
    if lowered in ALIASES:
        return ALIASES[lowered]
    raise ValueError(f"party {label!r} is not in the list of the Commission's parties")


def party_label(code: str | None) -> str:
    return INDEPENDENT.capitalize() if code is None else PARTIES.get(code) or LOCAL_PARTIES[code]


def sheet(name: str, tab: str) -> list[list[object]]:
    book = xlrd.open_workbook(str(raw.cached(SOURCE, URLS[name]).path))
    page = book.sheet_by_name(tab)
    return [[cell.value for cell in page.row(r)] for r in range(page.nrows)]


def cell(row: list[object], column: int) -> object:
    return row[column] if column < len(row) else ""


def amount(value: object) -> int | None:
    """A printed count: a number, or text with spaces for thousands; None for an empty cell or a dash."""
    if isinstance(value, float):
        return round(value)
    digits = re.sub(r"[\s,]", "", str(value))
    return int(digits) if digits.isdigit() else None


def words(value: object) -> str:
    return clean(str(value))


def _fold(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", plain.casefold())


def _closest(name: str, options: list[str]) -> str | None:
    found = difflib.get_close_matches(_fold(name), [_fold(o) for o in options], n=1, cutoff=0.8)
    return next((o for o in options if _fold(o) == found[0]), None) if found else None


def read_rc1998() -> list[Contest]:
    rows = sheet("rc1998", "rc")
    stop = next(i for i, row in enumerate(rows) if "Uncontested Constituencies" in words(cell(row, 2)))
    contests: list[Contest] = []
    region = ""
    current: Contest | None = None
    for row in rows[39:stop]:
        first, label, votes = words(cell(row, 0)), words(cell(row, 5)), cell(row, 7)
        if first and not label and not cell(row, 1):
            region = first
            continue
        if not label:
            continue
        if first:
            current = Contest(region_name(region), first, amount(cell(row, 1)), amount(cell(row, 2)))
            current.rejected, current.cast = amount(cell(row, 3)), amount(cell(row, 4))
            contests.append(current)
        if current is None:
            raise ValueError(f"1998 regional council row without a constituency: {row!r}")
        if str(votes).strip().casefold() == "uncontested":
            current.tacit = True
        current.candidates.append(Candidate(words(cell(row, 6)), old_party(label), amount(votes)))
    for contest in contests:
        if contest.tacit:
            contest.candidates[0].elected = True
    # The constituencies the table leaves to its list of uncontested seats
    names = {(c.region, c.name): c for c in contests}
    region = ""
    for row in rows[stop + 4 :]:
        if words(cell(row, 0)) == "Independent candidate":
            break
        region = region_name(words(cell(row, 0))) if words(cell(row, 0)) else region
        name, who, label = words(cell(row, 1)), words(cell(row, 2)), words(cell(row, 4))
        if not name or not label:
            continue
        known = _closest(name, [n for r, n in names if r == region])
        if known is None:
            contest = Contest(region, name, tacit=True)
            contest.candidates.append(Candidate(who, old_party(label), None, True))
            contests.append(contest)
            names[(region, name)] = contest
    declared = read_elected1998()
    for contest in contests:
        if contest.tacit:
            continue
        known = _closest(contest.name, [n for r, n in declared if r == contest.region])
        if known is None:
            print(f"note: rc1998 {contest.region} {contest.name}: no declared winner is printed")
            continue
        who, party = declared[(contest.region, known)]
        surname = _fold(who.split(",")[0])
        names = [_fold(c.name.split(",")[0]) for c in contest.candidates]
        close = difflib.get_close_matches(surname, names, n=1, cutoff=0.7)
        match = [c for c, n in zip(contest.candidates, names, strict=True) if close and n == close[0]]
        if len(match) != 1:
            match = [c for c in contest.candidates if party and c.party == party]
        if len(match) != 1:
            print(f"note: rc1998 {contest.region} {contest.name}: declared {who!r} ({party}) matches {len(match)}")
            continue
        match[0].elected = True
        contest.declared = who
    for contest in contests:
        if contest.tacit and not contest.candidates[0].name:
            for r, n in declared:
                if r == contest.region and _fold(n) == _fold(contest.name):
                    contest.candidates[0].name = declared[(r, n)][0]
    return contests


def read_elected1998() -> dict[tuple[str, str], tuple[str, str | None]]:
    elected: dict[tuple[str, str], tuple[str, str | None]] = {}
    region = ""
    for row in sheet("rc1998", "Ele")[5:]:
        region = region_name(words(cell(row, 0))) if words(cell(row, 0)) else region
        name, who, label = words(cell(row, 1)), words(cell(row, 2)), words(cell(row, 4))
        if name:
            elected[(region, name)] = (who, old_party(label) if label else None)
    return elected


def read_rc2004() -> list[Contest]:
    rows = sheet("rc2004", "Sheet1")
    heads = [words(cell(rows[5], c)) for c in range(2, 10)] + ["Independent candidates"]
    contests: list[Contest] = []
    region = ""
    for row in rows[7:]:
        name = words(cell(row, 0))
        if not name:
            continue
        if not cell(row, 1):
            region = name
            continue
        if name == "Regional Total":
            continue
        contest = Contest(region_name(region), name, amount(cell(row, 1)), amount(cell(row, 11)))
        contest.rejected, contest.cast = amount(cell(row, 12)), amount(cell(row, 13))
        for offset, head in enumerate(heads):
            votes = amount(cell(row, 2 + offset))
            if votes is not None:
                contest.candidates.append(Candidate("", old_party(head), votes))
        if contest.rejected is None and len(contest.candidates) == 1:
            # The table prints the registered voters as the votes of a candidate nobody stood against.
            contest.tacit = True
            contest.counted = contest.cast = None
            contest.candidates[0].votes = None
            contest.candidates[0].elected = True
        contests.append(contest)
    return contests


def read_la1998() -> list[Area]:
    areas: list[Area] = []
    region = ""
    current: Area | None = None
    for row in sheet("la1998", "la")[38:]:
        first, label = words(cell(row, 0)), words(cell(row, 5))
        if first.startswith("1998 - Women"):
            break
        if first and not label and not cell(row, 1):
            region = first
            continue
        if not label:
            continue
        if first:
            current = Area(region_name(region), first, amount(cell(row, 4)))
            current.registered, current.rejected = amount(cell(row, 1)), amount(cell(row, 3))
            areas.append(current)
        if current is None:
            raise ValueError(f"1998 local authority row without an area: {row!r}")
        current.lists.append(Entry(label, amount(cell(row, 6)) or 0, amount(cell(row, 7)) or 0))
    return areas


def read_la2004() -> list[Area]:
    areas: list[Area] = []
    current: Area | None = None
    pending = ""
    for row in sheet("la2004", "lc")[50:]:
        first, label = words(cell(row, 0)), words(cell(row, 6))
        if first == "TOTALS":
            break
        if label and amount(cell(row, 7)) is None:
            pending = label  # a long name printed over two lines
            continue
        if not label:
            continue
        label, pending = clean(f"{pending} {label}"), ""
        if first:
            current = Area("", first.title(), amount(cell(row, 4)))
            current.valid, current.rejected = amount(cell(row, 2)), amount(cell(row, 3))
            current.registered = amount(cell(row, 1))
            areas.append(current)
        if current is None:
            raise ValueError(f"2004 local authority row without an area: {row!r}")
        current.lists.append(Entry(label, amount(cell(row, 7)) or 0, amount(cell(row, 9)) or 0))
    return areas


# --- packages ---------------------------------------------------------------------------------------------------------


def slug(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=url[url.rfind(".") :])


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


@cache
def regional(name: str) -> tuple[Contest, ...]:
    if name == "rc2010":
        return tuple(read_2010())
    if name == "rc1998":
        return tuple(read_rc1998())
    if name == "rc2004":
        return tuple(read_rc2004())
    return tuple(read_regional(name, (8, 28, 48) if name == "rcby2025" else None))


@cache
def local() -> tuple[Area, ...]:
    return tuple(read_local())


@cache
def old_local(name: str) -> tuple[Area, ...]:
    return tuple(read_la1998() if name == "la1998" else read_la2004())


@cache
def national() -> National:
    return read_2024()


@cache
def assembly_2014() -> tuple[list[Row], Row, int]:
    return read_2014()


def everything() -> None:
    """Read every file once, so the registry sees every party any package uses."""
    for name in ("rc2010", "rc2015", "rc2020", "rcby2025", "rc1998", "rc2004"):
        regional(name)
    local()
    for name in ("la1998", "la2004"):
        old_local(name)
    assembly_2014()
    for _, label, _ in national().president:
        party_of(label)
    for label, *_ in national().lists:
        party_of(label)
    for area in local():
        for entry in area.lists:
            party_of(entry.label)


def registry() -> Package:
    everything()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Namibian parties, the National Assembly, regional and local authority councils, the presidency, and their "
        "electoral systems, from the Electoral Commission's published results",
    )
    p.external.add(NAMIBIA)
    p.add(
        "source",
        id=ECN_SOURCE,
        kind="official_body",
        publisher="Electoral Commission of Namibia",
        title="Election results published by the Electoral Commission of Namibia",
        url=PAGES,
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Commission of Namibia",
    )
    for url in URLS.values():
        p.artifact(raw.cached(SOURCE, url), ECN_SOURCE)
    for code, noun, kind, rank in (
        ("na.constituency", "constituency", "electoral_district", 1),
        ("na.local_authority", "local authority area", "electoral_district", 1),
        ("na.national", "country", "electoral_district", 0),
    ):
        p.add("scheme", code=code, polity_id=NAMIBIA, kind=kind, rank=rank, noun=noun)
    for id, kind, english in (
        (ASSEMBLY, "lower_house", "National Assembly of Namibia"),
        (REGIONAL, "council", "Regional councils of Namibia"),
        (LOCAL, "council", "Local authority councils of Namibia"),
    ):
        p.add("body", id=id, polity_id=NAMIBIA, kind=kind)
        p.name(id, english, "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=NAMIBIA, body_id=None, kind="head_of_state")
    p.name(PRESIDENCY, "President of Namibia", "en", "official")
    systems = (
        (
            SYSTEM_ASSEMBLY,
            {"formula": "hare"},
            (
                "Closed party lists in one national constituency. Seats go to each party by largest remainder, "
                "using the quota of votes the Commission prints for the election."
            ),
        ),
        (
            SYSTEM_PRESIDENT,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate with more than half of the votes counted is elected; otherwise the two leading "
                "candidates go to a second round."
            ),
        ),
        (SYSTEM_REGIONAL, {"formula": "fptp"}, "Each constituency elects the candidate with the most votes."),
        (
            SYSTEM_LOCAL,
            {"formula": "hare"},
            (
                "Party lists in each local authority area. Seats go to each list by largest remainder, using the "
                "quota of votes the Commission prints for the area."
            ),
        ),
    )
    for id, rules, text_ in systems:
        p.add("electoral_system", id=id, polity_id=NAMIBIA, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text_, lang="en")
    for code, name in sorted({**PARTIES, **LOCAL_PARTIES}.items()):
        id = party_id(code)
        p.add("party", id=id, kind="party", polity_id=NAMIBIA)
        p.name(id, name, "en", "official")
        if code not in ASSOCIATIONS.values():
            p.name(id, "PDM" if code == "DTA" else code.replace("_", " "), "en", "abbreviation", False)
        if code == "DTA":
            p.name(id, "Popular Democratic Movement", "en", "official", False)
            p.name(id, "DTA", "en", "abbreviation", False)
    return p


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
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
        flags=0,
        value=value,
    )


def _election(p: Package, tag: str, kind: str, day: date, end: date, english: str, path: str) -> str:
    election = key("election", tag)
    p.add("election", id=election, polity_id=NAMIBIA, kind=kind, date_from=day, date_to=end, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    return election


def _contest(
    p: Package, tag: str, election: str, kind: str, english: str, path: str, tacit: bool = False, **fields: object
) -> str:
    contest = key("contest", tag)
    row = {"round_number": 1, "default_result_kind": "tacit" if tacit else "counted", **fields}
    p.add("contest", id=contest, election_id=election, kind=kind, **row)
    p.name(contest, english, "en", "official")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    return contest


def _result_set(p: Package, tag: str, election: str, contest: str, tacit: bool = False, empty: bool = False) -> str:
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if tacit else "counted",
        source_id=ECN_SOURCE,
        status="active",
        is_default=True,
        completeness=0.0 if empty else 1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    return result_set


def _unit(p: Package, tag: str, scheme: str, code: str, english: str) -> str:
    unit = key("unit", tag, code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=NAMIBIA,
        scheme_code=scheme,
        code=f"{tag}:{code}",
        area_relation="unlocated",
        area_id=None,
        kind="standard",
    )
    p.name(unit, english, "en", "official")
    return unit


def _candidate(p: Package, tag: str, contest: str, position: int, name: str, party: str | None) -> str:
    option = key("option", tag, position)
    if not name:  # the table prints the party or the independent label without a name
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party_id(party) if party else None,
            ballot_position=position,
            ballot_label=party_label(party),
        )
        return option
    person = key("person", tag, position, name)
    p.add("person", id=person)
    p.name(person, name, "en", "official")
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        party_id=party_id(party) if party else None,
        ballot_position=position,
        ballot_label=name,
    )
    p.add(
        "candidacy",
        id=key("candidacy", tag, position),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=name,
        member_of_party_id=party_id(party) if party else None,
        is_independent=party is None,
    )
    return option


def _caveat(p: Package, entity: str, name: str, body: str) -> None:
    p.add("note", id=key("note", entity, name), entity_id=entity, kind="caveat", body_md=body, lang="en")


def _constituencies(
    p: Package,
    day: str,
    tag: str,
    election_kind: str,
    english: str,
    path: str,
    first: date,
    last: date,
    contests: tuple[Contest, ...],
    total: int,
    valid_printed: bool = False,
) -> None:
    """One election of single-member constituency contests, each counted as one unit."""
    election = _election(p, tag, election_kind, first, last, english, path)
    unequal: list[str] = []
    for contest in contests:
        code = f"{slug(contest.region)}-{slug(contest.name)}"
        title = f"{english}, {contest.name} constituency ({contest.region} region)"
        ctag = f"{tag}:{code}"
        node = _contest(
            p,
            ctag,
            election,
            "body_seats",
            title,
            f"{path}/{slug(contest.region)}/{slug(contest.name)}",
            tacit=contest.tacit,
            body_id=REGIONAL,
            magnitude=1,
            electoral_system_id=SYSTEM_REGIONAL,
        )
        unit = _unit(p, tag, "na.constituency", code, f"{contest.name} constituency, {contest.region} region")
        values = contest.registered is not None or not contest.tacit
        result_set = _result_set(p, ctag, election, node, tacit=contest.tacit, empty=not values)
        if contest.tacit:
            _put(p, result_set, unit, None, REGISTERED, contest.registered)
        else:
            _put(p, result_set, unit, None, REGISTERED, contest.registered)
            _put(p, result_set, unit, None, VALID if valid_printed else CAST, contest.counted)
            _put(p, result_set, unit, None, INVALID, contest.rejected)
            _put(p, result_set, unit, None, CAST, contest.cast) if valid_printed else None
        for position, candidate in enumerate(contest.candidates, 1):
            option = _candidate(p, ctag, node, position, candidate.name, candidate.party)
            _put(p, result_set, unit, option, VOTES, candidate.votes)
            if contest.declared or contest.tacit or candidate.elected:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    candidacy_id=None,
                    kind="elected" if candidate.elected else "lost",
                    seats=None,
                )
        summed = sum(c.votes or 0 for c in contest.candidates)
        if not contest.tacit and valid_printed and contest.counted != summed:
            unequal.append(f"{contest.name} (printed {contest.counted:,}, candidates {summed:,})")
        if (
            contest.declared
            and not contest.tacit
            and not any(
                c.elected and c.votes == max(x.votes or 0 for x in contest.candidates) for c in contest.candidates
            )
        ):
            _caveat(
                p,
                result_set,
                "winner",
                f"The Commission declares {contest.declared} elected, although another candidate has more votes in "
                "its printed table. The votes and the declared winner are stored as printed.",
            )
    if unequal:
        p.accept(
            "accounting.votes_valid",
            "The printed total of votes differs from the sum of the candidates' votes for "
            + "; ".join(unequal)
            + ". Each is stored as printed.",
        )
    if total != len(contests):
        raise ValueError(f"{tag}: {len(contests)} contests, expected {total}")


def build(day: str) -> Package:
    first = DAYS[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Namibian elections of {first} by constituency or local authority area, from the Electoral "
        "Commission's published results",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(NAMIBIA)
    everything()
    if day == "2010-11-27":
        p.uses(raw.cached(SOURCE, URLS["rc2010"]))
        _constituencies(
            p,
            day,
            "rc2010",
            "local",
            "2010 Namibian regional council elections",
            f"na/{day}-regional-councils",
            first,
            first,
            regional("rc2010"),
            107,
            valid_printed=True,
        )
    elif day == "2014-11-28":
        _assembly_2014(p, day, first)
    elif day == "2015-11-27":
        p.uses(raw.cached(SOURCE, URLS["rc2015"]))
        _constituencies(
            p,
            day,
            "rc2015",
            "local",
            "2015 Namibian regional council elections",
            f"na/{day}-regional-councils",
            first,
            first,
            regional("rc2015"),
            95,
            valid_printed=True,
        )
    elif day == "2020-11-25":
        p.uses(raw.cached(SOURCE, URLS["rc2020"]))
        p.uses(raw.cached(SOURCE, URLS["la2020"]))
        _constituencies(
            p,
            day,
            "rc2020",
            "local",
            "2020 Namibian regional council elections",
            f"na/{day}-regional-councils",
            first,
            date(2021, 2, 26),
            regional("rc2020"),
            121,
        )
        _local_2020(p, day, first)
    elif day == "1998-02-16":
        p.uses(raw.cached(SOURCE, URLS["la1998"]))
        _local_old(p, "la1998", day, first, first, "1998 Namibian local authority council elections", 0)
    elif day == "1998-11-30":
        p.uses(raw.cached(SOURCE, URLS["rc1998"]))
        _constituencies(
            p,
            day,
            "rc1998",
            "local",
            "1998 Namibian regional council elections",
            f"na/{day}-regional-councils",
            first,
            date(1998, 12, 1),
            regional("rc1998"),
            len(regional("rc1998")),
            valid_printed=True,
        )
        p.add(
            "note",
            id=key("note", "rc1998", "summary"),
            entity_id=key("election", "rc1998"),
            kind="caveat",
            body_md=(
                "The Commission's summary page of this election prints totals that differ from its constituency "
                "table: 213,789 votes cast and 4,757 rejected against 213,924 and 5,116 summed over the "
                "constituencies, and 82 seats for SWAPO and 16 for the DTA against 83 and 15 by the party printed "
                "beside each constituency winner (the Opuwo and Outjo tables and the list of elected "
                "representatives name different parties for the same winners). It lists 26 uncontested "
                "constituencies; the tables show 28. Every figure is stored as printed in its own table."
            ),
            lang="en",
        )
    elif day == "2004-11-15":
        p.uses(raw.cached(SOURCE, URLS["rc2004"]))
        p.uses(raw.cached(SOURCE, URLS["la2004"]))
        end = date(2004, 11, 16)
        _constituencies(
            p,
            day,
            "rc2004",
            "local",
            "2004 Namibian regional council elections",
            f"na/{day}-regional-councils",
            first,
            end,
            regional("rc2004"),
            len(regional("rc2004")),
            valid_printed=True,
        )
        _local_old(p, "la2004", day, first, end, "2004 Namibian local authority council elections", 0)
        p.add(
            "note",
            id=key("note", "la2004", "date"),
            entity_id=key("election", "la2004"),
            kind="caveat",
            body_md="The local authority table prints no date. It is dated with the regional council election of "
            "the same year, whose table prints 15 and 16 November 2004.",
            lang="en",
        )
    elif day == "2024-11-27":
        _national_2024(p, day, first)
    elif day == "2025-06-17":
        p.uses(raw.cached(SOURCE, URLS["rcby2025"]))
        _constituencies(
            p,
            day,
            "rcby2025",
            "by_election",
            "2025 Namibian regional council by-elections",
            f"na/{day}-by-elections",
            first,
            first,
            regional("rcby2025"),
            5,
        )
    return p


def _assembly_2014(p: Package, day: str, first: date) -> None:
    p.uses(raw.cached(SOURCE, URLS["na2014"]))
    rows, last, printed = assembly_2014()
    tag, path = "na2014", f"na/{day}-general"
    election = _election(p, tag, "general", first, first, "2014 Namibian National Assembly election", path)
    contest = _contest(
        p,
        tag,
        election,
        "list_allocation",
        "2014 Namibian National Assembly election",
        f"{path}/national-assembly",
        body_id=ASSEMBLY,
        electoral_system_id=SYSTEM_ASSEMBLY,
    )
    result_set = _result_set(p, tag, election, contest)
    options = []
    for position, code in enumerate(PARTIES_2014, 1):
        option = key("option", tag, position)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party_id(code),
            ballot_position=position,
            ballot_label=PARTIES[code],
        )
    for row in rows:
        code = f"{slug(row.region)}-{slug(row.name)}"
        english = row.name if row.region == "International" else f"{row.name} constituency, {row.region} region"
        unit = _unit(p, tag, "na.constituency", code, english)
        _put(p, result_set, unit, None, REGISTERED, row.registered)
        _put(p, result_set, unit, None, VALID, row.total)
        for option, votes in zip(options, row.votes, strict=True):
            _put(p, result_set, unit, option, VOTES, votes)
    p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=last.registered)
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=printed)
    for option, votes in zip(options, last.votes, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
    p.accept(
        "accounting.declared",
        "The printed national row gives 3,404 votes for the Congress of Democrats and 893,645 votes in all, while "
        "the constituency rows add up to 3,402 and 893,643; every other party's total and the registered voters "
        "agree with the rows. Each row is stored as printed.",
    )
    _caveat(
        p,
        result_set,
        "totals",
        "The Commission's printed national row exceeds the sum of its constituency rows by two votes for the "
        "Congress of Democrats. The table declares no seats or winners, so none are stored.",
    )


def _local_old(p: Package, tag: str, day: str, first: date, last: date, english: str, total: int) -> None:
    """Local authority councils with the votes and seats of each list, and the area's ballot counts where printed."""
    path = f"na/{day}-local-authorities"
    election = _election(p, tag, "local", first, last, english, path)
    unequal: list[str] = []
    cast: list[str] = []
    for area in old_local(tag):
        code = f"{slug(area.region)}-{slug(area.name)}".strip("-")
        where = f"{area.name} ({area.region} region)" if area.region else area.name
        ctag = f"{tag}:{code}"
        contest = _contest(
            p,
            ctag,
            election,
            "list_allocation",
            f"{english}, {where}",
            f"{path}/{slug(area.region)}/{slug(area.name)}" if area.region else f"{path}/{slug(area.name)}",
            body_id=LOCAL,
            magnitude=sum(entry.seats for entry in area.lists),
            electoral_system_id=SYSTEM_LOCAL,
        )
        unit = _unit(
            p,
            tag,
            "na.local_authority",
            code,
            f"{area.name} local authority area" + (f", {area.region} region" if area.region else ""),
        )
        result_set = _result_set(p, ctag, election, contest)
        _put(p, result_set, unit, None, REGISTERED, area.registered)
        _put(p, result_set, unit, None, CAST, area.cast)
        _put(p, result_set, unit, None, VALID, area.valid)
        _put(p, result_set, unit, None, INVALID, area.rejected)
        for position, entry in enumerate(area.lists, 1):
            party = old_party(entry.label)
            option = key("option", ctag, position)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_id(party) if party else None,
                ballot_position=position,
                ballot_label=party_label(party),
            )
            _put(p, result_set, unit, option, VOTES, entry.votes)
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                candidacy_id=None,
                kind="won" if entry.seats else "lost",
                seats=entry.seats,
            )
        if area.valid is not None and area.valid + (area.rejected or 0) != area.cast:
            cast.append(
                f"{area.name} (valid {area.valid:,} and rejected {area.rejected or 0:,}, cast {area.cast or 0:,})"
            )
        summed = sum(entry.votes for entry in area.lists)
        printed = area.valid if area.valid is not None else (area.cast or 0) - (area.rejected or 0)
        if summed != printed:
            unequal.append(f"{area.name} (printed {printed:,}, lists {summed:,})")
    if cast:
        p.accept(
            "accounting.ballots_sum",
            "The Commission's printed votes cast differ from its printed valid and rejected votes for "
            + "; ".join(cast)
            + ". Each is stored as printed.",
        )
    if unequal:
        p.add(
            "note",
            id=key("note", tag, "valid"),
            entity_id=election,
            kind="caveat",
            body_md="In "
            + str(len(unequal))
            + " areas the Commission's printed total of valid votes differs from the sum of its lists' votes: "
            + "; ".join(unequal)
            + ". Both are stored as printed.",
            lang="en",
        )


def _local_2020(p: Package, day: str, first: date) -> None:
    tag, path = "la2020", f"na/{day}-local-authorities"
    election = _election(
        p, tag, "local", first, date(2021, 2, 26), "2020 Namibian local authority council elections", path
    )
    unequal: list[str] = []
    for area in local():
        code = f"{slug(area.region)}-{slug(area.name)}"
        seats = sum(entry.seats for entry in area.lists)
        ctag = f"{tag}:{code}"
        contest = _contest(
            p,
            ctag,
            election,
            "list_allocation",
            f"2020 Namibian local authority council election, {area.name} ({area.region} region)",
            f"{path}/{slug(area.region)}/{slug(area.name)}",
            body_id=LOCAL,
            magnitude=seats,
            electoral_system_id=SYSTEM_LOCAL,
        )
        unit = _unit(p, tag, "na.local_authority", code, f"{area.name} local authority area, {area.region} region")
        result_set = _result_set(p, ctag, election, contest)
        p.add(
            "note",
            id=key("note", result_set, "quota"),
            entity_id=result_set,
            kind="methodology",
            body_md=f"The Commission prints a quota of {area.quota} votes for the area.",
            lang="en",
        )
        _put(p, result_set, unit, None, CAST, area.cast)
        for position, entry in enumerate(area.lists, 1):
            label, votes, won, names = entry.label, entry.votes, entry.seats, entry.names
            party = party_of(str(label))
            option = key("option", ctag, position)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_id(party) if party else None,
                ballot_position=position,
                ballot_label=clean(re.sub(r"\s*\([^()]*\)?$", "", str(label))),
            )
            _put(p, result_set, unit, option, VOTES, votes)
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                candidacy_id=None,
                kind="won" if won else "lost",
                seats=won,
            )
            for place, name in enumerate(names, 1):
                person = key("person", ctag, position, place)
                candidacy = key("candidacy", ctag, position, place)
                p.add("person", id=person)
                p.name(person, str(name), "en", "official")
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=place,
                    ballot_name=str(name),
                    member_of_party_id=party_id(party) if party else None,
                    is_independent=False,
                )
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    candidacy_id=candidacy,
                    kind="elected",
                    seats=None,
                )
        summed = sum(entry.votes for entry in area.lists)
        if summed != area.cast:
            unequal.append(f"{area.name} (printed {area.cast:,}, lists {summed:,})")
    p.add(
        "note",
        id=key("note", tag, "cast"),
        entity_id=election,
        kind="caveat",
        body_md="The Commission's printed total of votes cast in a local authority area equals the sum of its "
        "lists' votes in most areas. In "
        + str(len(unequal))
        + " areas the two differ by a few votes: "
        + "; ".join(unequal)
        + ". Both are stored as printed.",
        lang="en",
    )


def _national_2024(p: Package, day: str, first: date) -> None:
    p.uses(raw.cached(SOURCE, URLS["pna2024"]))
    data = national()
    last = date(2024, 11, 30)
    tag, path = "pna2024", f"na/{day}-general"
    election = _election(
        p, tag, "general", first, last, "2024 Namibian presidential and National Assembly elections", path
    )
    unit = _unit(p, tag, "na.national", "national", "Namibia")
    # presidency
    contest = _contest(
        p,
        f"{tag}:president",
        election,
        "office",
        "2024 Namibian presidential election",
        f"{path}/president",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    result_set = _result_set(p, f"{tag}:president", election, contest)
    _put(p, result_set, unit, None, VALID, data.president_counted)
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=data.president_counted)
    winner = max(data.president, key=lambda c: c[2])
    if not data.winner or not winner[0].startswith(data.winner.split(",")[0]):
        raise ValueError(f"declared winner {data.winner!r} is not the leading candidate {winner[0]!r}")
    for position, (name, label, votes) in enumerate(data.president, 1):
        option = _candidate(p, f"{tag}:president", contest, position, name, party_of(label))
        _put(p, result_set, unit, option, VOTES, votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            candidacy_id=None,
            kind="elected" if (name, label, votes) == winner else "lost",
            seats=None,
        )
    # assembly
    seats = sum(entry[2] for entry in data.lists)
    contest = _contest(
        p,
        f"{tag}:assembly",
        election,
        "list_allocation",
        "2024 Namibian National Assembly election",
        f"{path}/national-assembly",
        body_id=ASSEMBLY,
        magnitude=seats,
        electoral_system_id=SYSTEM_ASSEMBLY,
    )
    result_set = _result_set(p, f"{tag}:assembly", election, contest)
    p.add(
        "note",
        id=key("note", result_set, "quota"),
        entity_id=result_set,
        kind="methodology",
        body_md=f"The Commission prints a quota of {data.quota:,} votes for the election.",
        lang="en",
    )
    for measure, value in ((CAST, data.cast), (VALID, data.counted)):
        _put(p, result_set, unit, None, measure, value)
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for position, ((label, votes, won), named) in enumerate(zip(data.lists, data.members, strict=True), 1):
        party = party_of(label)
        option = key("option", f"{tag}:assembly", position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party_id(party) if party else None,
            ballot_position=position,
            ballot_label=clean(re.sub(r"\s*\([^()]*\)?$", "", label)),
        )
        _put(p, result_set, unit, option, VOTES, votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            candidacy_id=None,
            kind="won" if won else "lost",
            seats=won,
        )
        for place, ballot_name in enumerate(named, 1):
            surname, _, given = ballot_name.partition(", ")
            tag_member = f"{tag}:assembly:{position}:{place}"
            person = key("person", tag_member)
            p.add("person", id=person)
            p.name(person, clean(f"{given} {surname}"), "en", "official")
            candidacy = key("candidacy", tag_member)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=place,
                ballot_name=ballot_name,
                member_of_party_id=party_id(party) if party else None,
                is_independent=False,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                candidacy_id=candidacy,
                kind="elected",
                seats=None,
            )
