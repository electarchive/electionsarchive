"""Namibia's national election authority: the Electoral Commission of Namibia's published results.

The Commission publishes results as Government Gazette notices and printed tables on its website. Constituency
results come from regional council elections (2010, 2015, 2020, and the 2025 by-elections) and from the 2014 National
Assembly table; local authority results come from the 2020 gazette; the 2014 and 2024 presidential and National Assembly
results are gazetted nationally, and the 2024 election report adds each constituency's registered voters, rejected
votes, and votes cast. Regional council by-elections from 2018 on come from media releases, Government Gazette
notices of results, and the Commission's post-election and annual reports. Every file but two scanned media releases
has a text layer, read here with ``pdftotext -layout``. The 1998 and 2004
regional council and local authority tables are spreadsheets, read here with ``xlrd``.
"""

import difflib
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from functools import cache
from uuid import uuid5

import xlrd

from ea import package, raw
from ea.package import Package
from ea.sources.na import early

SOURCE = "na.ecn"
SITE = "https://www.ecn.na"
UPLOADS = f"{SITE}/wp-content/uploads"
PAGES = f"{SITE}/previous-election-results/"
BY_ELECTIONS_PAGE = f"{SITE}/by-elections/"
NAMIBIA = str(uuid5(package.NAMESPACE, "polity\x1fNA"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8

URLS = {
    "rc2010": f"{UPLOADS}/2021/06/Regional-Councils-2010.pdf",
    "na2014": f"{UPLOADS}/2021/06/FINAL-RESULTS-Elections-2014.pdf",
    "rc2015": f"{UPLOADS}/2021/06/Regional-Counils-Election-Results_Duly-elected-candidates-2015.pdf",
    "rc2020": f"{UPLOADS}/2023/02/7654-Gov-N219-Results-Regional-Council-2020.pdf",
    "la2020": f"{UPLOADS}/2021/06/7497-Gov-N65-Results-LA-2020.pdf",
    "gazette2014": "https://www.lac.org.na/laws/2015/5641.pdf",
    "pna2024": f"{UPLOADS}/2025/03/Results-publication-2024PNA.pdf",
    "report2024": f"{UPLOADS}/2025/07/ECN_2024-NPA_Report_Latest_Lower-Version.pdf",
    "rcby2025": f"{UPLOADS}/2025/10/Govt-Gazette-five-by-elections-result.pdf",
    "rc1998": f"{UPLOADS}/2021/06/REgional-Councils-Elections-1998.xls",
    "la1998": f"{UPLOADS}/2021/06/Local-Authourities-Elections-1998.xls",
    "rc2004": f"{UPLOADS}/2021/06/Regional-Councils-Election-2004.xls",
    "la2004": f"{UPLOADS}/2021/06/local-Authourity-Election-2004.xls",
    "gazette2025": "https://www.lac.org.na/laws/2025/8818.pdf",
    "rc1992": "https://www.lac.org.na/laws/1992/545.pdf",
    "la1992": "https://www.lac.org.na/laws/1992/547.pdf",
    "gazette2010": "https://www.lac.org.na/laws/2010/4627.pdf",
    "nom1992": "https://www.lac.org.na/laws/1992/521.pdf",
    "rccandidates2025": f"{UPLOADS}/2025/11/8790-Gov-N310.-RC-Candidates.pdf",
    "lacandidates2025": f"{UPLOADS}/2025/11/8791-Gov-N311-Candidates-LA.pdf",
    "byopuwo2021": f"{UPLOADS}/2021/07/Opuwo-Rural-Final-Results-Media-Release-03-July-2021.pdf",
    "byswakopmund2022": f"{UPLOADS}/2022/08/OFFICIAL-RESULTS-FOR-THE-REGIONAL-COUNCIL-ELECTION-FOR-THE-SWAKOPMUND-CONSTITUENCY.pdf",
    "annual2018": f"{UPLOADS}/2021/06/ECN_Annual-Report_20172018.pdf",
    "byondangwa2019": f"{UPLOADS}/2021/07/Ondangwa_Assessment-Report_2019.pdf",
    "byoshakati2019": f"{UPLOADS}/2021/06/Oshakati-East-Assessment-Report_2019.pdf",
    "bygobabis2020": f"{UPLOADS}/2021/07/ECN_Gobabis_Assessment-Report_2020.pdf",
    "byotjiwarongo2020": f"{UPLOADS}/2021/07/Otjiwarong-Assessment-2020.pdf",
    "gazette7650": "https://www.lac.org.na/laws/2021/7650.pdf",
    "byncamagoro2021": f"{UPLOADS}/2022/07/Ncamagoro_Post-Election-Report-2022_Final.pdf",
    "bygaroeb2023": f"{UPLOADS}/2023/08/Preliminary-Report-Moses-Garoeb-20.06.2023_LR.pdf",
    "byokaku2023": f"{UPLOADS}/2023/10/Preliminary-Report-Okaku-by-election-Report_24.10.23.pdf",
    "gazette8117": "https://www.lac.org.na/laws/2023/8117.pdf",
    "annual2024": f"{UPLOADS}/2026/02/10032025-ECN-Annual-Report-23-24_print-read.pdf",
    "gazette8576": "https://www.lac.org.na/laws/2025/8576.pdf",
    "annual2025": f"{UPLOADS}/2026/05/ECN-2024-25-Annual-Report.pdf",
    "gazette8764": "https://www.lac.org.na/laws/2025/8764.pdf",
    "byoutapi2025": f"{UPLOADS}/2026/05/270326-Outapi-Constituency-By-election-Report_Final.pdf",
}
DAYS = {
    "1992-11-30": date(1992, 11, 30),
    "1998-02-16": date(1998, 2, 16),
    "1998-11-30": date(1998, 11, 30),
    "2004-11-15": date(2004, 11, 15),
    "2010-11-26": date(2010, 11, 26),
    "2010-11-27": date(2010, 11, 27),
    "2014-11-28": date(2014, 11, 28),
    "2015-11-27": date(2015, 11, 27),
    "2018-03-28": date(2018, 3, 28),
    "2019-06-15": date(2019, 6, 15),
    "2019-08-24": date(2019, 8, 24),
    "2020-01-15": date(2020, 1, 15),
    "2020-03-09": date(2020, 3, 9),
    "2020-11-25": date(2020, 11, 25),
    "2021-07-02": date(2021, 7, 2),
    "2021-08-25": date(2021, 8, 25),
    "2021-12-17": date(2021, 12, 17),
    "2022-08-12": date(2022, 8, 12),
    "2023-01-06": date(2023, 1, 6),
    "2023-03-17": date(2023, 3, 17),
    "2023-05-15": date(2023, 5, 15),
    "2024-11-27": date(2024, 11, 27),
    "2025-01-17": date(2025, 1, 17),
    "2025-06-17": date(2025, 6, 17),
    "2025-09-29": date(2025, 9, 29),
    "2025-11-26": date(2025, 11, 26),
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
    "NPF": "National Patriotic Front of Namibia",
    "NEFF": "Namibia Economic Freedom Fighters",
    "NPP": "Namibia Progressive Party",
    "NUDO": "National Unity Democratic Organisation",
    "PUC": "People Unite for Change",
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
    "namibia progressive party": "NPP",
    "npp": "NPP",
    "national democratic party": "NDP",
    "people unite for change": "PUC",
    "puc": "PUC",
    "united namibian’s party": "UNP",
    "united namibian's party": "UNP",
    "app of namibia": "APP",
    "swapo-party": "SWAPO",
    "united democratic party of namibia": "UDF",
    "npf": "NPF",
    "npf of namibia": "NPF",
    "national patriotic front of namibia": "NPF",
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


GAZETTE_2025 = ("rc2025", "la2025")  # the two notices of Government Gazette 8818, one file


def text(name: str) -> list[str]:
    """The pages of a cached file as layout text, one string per page."""
    if name in GAZETTE_2025:
        pages = text("gazette2025")
        start = next(i for i, page in enumerate(pages) if "FOR REGIONAL COUNCILS: ELECTORAL ACT" in page)
        return pages[start:] if name == "rc2025" else pages[:start]
    if name == "la2010":
        pages = text("gazette2010")
        start = next(
            i for i, page in enumerate(pages) if "NOTIFICATION OF RESULT OF GENERAL ELECTION FOR LOCAL" in page
        )
        return pages[start:]
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
        if name in GAZETTE_2025:  # the gazette number printed in each page's margin
            rows = [[(p, t) for p, t in row if not (t == "8818" and p > 100)] for row in rows]
            rows = [row for row in rows if row]
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
                unopposed = (
                    name in GAZETTE_2025
                    and not amounts
                    and any(cand_at - 1 <= p < party_at - 1 and not t.isdigit() for p, t in row)
                    and any(p >= party_at - 1 for p, t in row)
                )
                if unopposed:  # a constituency with one candidate prints no votes
                    current = Contest(region_name(region), label, tacit=True)
                    contests.append(current)
                    current.candidates.append(Candidate("", None, None))
                else:
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
    if name == "rc2025":
        contests = _split_2025(contests)
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
            if candidate.name and candidate.name in label:  # the party's name and the winner's run together
                label = clean(label.replace(candidate.name, "", 1))
                contest.declared = clean(f"{contest.declared} {candidate.name}")
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


def _split_2025(contests: list[Contest]) -> list[Contest]:
    """Tell apart the constituencies whose names the 2025 gazette prints in a block that runs on from the one above.

    The Ondobe and Ongenga blocks each print one name beside the other's candidates, and Ondangwa Urban and
    Ongwediva share one total cell; the votes add up to the printed totals only with the candidates split here.
    """
    out: list[Contest] = []
    for contest in contests:
        if (contest.region, contest.name) == ("Ohangwena", "Ondobe Ongenga"):
            contest.name = "Ondobe"
        elif (contest.region, contest.name) == ("Ohangwena", ""):
            contest.name = "Ongenga"
        elif (contest.region, contest.name) == ("Oshana", "Ondangwa Urban Ongwediva"):
            urban, rest = contest.candidates[:4], contest.candidates[4:]
            contest.name, contest.candidates = "Ondangwa Urban", urban
            second = Contest(contest.region, "Ongwediva", candidates=rest)  # the gazette prints no total for it
            printed = re.sub(r"\W", "", contest.declared.casefold())
            for part in (contest, second):
                top = max(part.candidates, key=lambda c: c.votes or 0)
                part.declared = top.name if re.sub(r"\W", "", top.name.casefold()) in printed else ""
            out.extend([contest, second])
            continue
        out.append(contest)
    return out


def _same(a: str, b: str) -> bool:
    x, y = re.sub(r"\W", "", a.casefold()), re.sub(r"\W", "", b.casefold())
    return x == y or x.startswith(y) or y.startswith(x) or SequenceMatcher(None, x, y).ratio() >= 0.9  # typos


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
    tacit: bool = False


def read_local(name: str = "la2020") -> list[Area]:
    if name == "la2025":
        return read_local_2025()
    areas: list[Area] = []
    region = ""
    current: Area | None = None
    for page in text(name):
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


def read_local_2025() -> list[Area]:
    """Local authority tables of the 2025 gazette, whose columns shift a few characters from page to page."""
    areas: list[Area] = []
    region = ""
    current: Area | None = None
    for page in text("la2025"):
        for line in page.splitlines():
            row = [(p, t) for p, t in segments(line) if not t.startswith("Government Gazette")]
            row = [
                (p, t)
                for p, t in row
                if not (t.startswith("____") or t in ("8818", "Column 1") or (len(row) == 1 and t.isdigit()))
            ]
            if not row or row[0][1].startswith(("Column", "LOCAL", "AUTHORITY", "AREA", "CAST", "votes cast")):
                continue
            if len(row) == 1 and re.fullmatch(r"[/!A-Z -]+ REGION", row[0][1]):
                region = row[0][1]
                continue
            party = next(
                (
                    i
                    for i, (_, t) in enumerate(row)
                    if i >= 1
                    and t.isdigit()
                    and i + 1 < len(row)
                    and row[i + 1][1].isdigit()
                    and row[i - 1][0] >= 20
                    and not row[i - 1][1].isdigit()
                ),
                None,
            )
            if party is None:
                if current is None or not current.lists:
                    continue
                for p, t in row:
                    if p >= 60 and re.search(r"[A-Za-z]", t):
                        if "," not in t and current.lists[-1].names:  # a name that wraps onto the next line
                            current.lists[-1].names[-1] = clean(f"{current.lists[-1].names[-1]} {t}")
                        else:
                            current.lists[-1].names.append(t)
                    elif p >= 20 and not t.isdigit():
                        current.lists[-1].label = clean(f"{current.lists[-1].label} {t}")
                    elif re.search(r"[a-z]", t):
                        current.name = clean(f"{current.name} {t}")
                continue
            before, label, after = row[: party - 1], row[party - 1], row[party:]
            # the segments before the label are the area, its votes cast, and its quota
            if len(before) >= 3:
                current = Area(region_name(region), before[0][1], number(before[1][1]), before[2][1])
                areas.append(current)
            elif len(before) == 2 and region_name(region) == "Oshikoto":
                # the gazette leaves this area's name cell empty; the candidates gazette puts these lists in Oniipa
                current = Area(region_name(region), "Oniipa", number(before[0][1]), before[1][1])
                areas.append(current)
            elif current is None:
                raise ValueError(f"local authority row without an area: {line!r}")
            current.lists.append(
                Entry(label[1], number(after[0][1]), number(after[1][1]), [after[2][1]] if len(after) > 2 else [])
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


# --- 2014 presidential election and the 2024 constituency tables --------------------------------------------------------


def read_2014_president() -> tuple[int, list[tuple[str, str, int]], str]:
    """The votes counted, each candidate's printed name, party label, and votes, and the candidate declared elected."""
    page = next(p for p in text("gazette2014") if "SCHEDULE" in p and "Column 4" in p and "President" in p)
    counted = 0
    winner = ""
    candidates: list[tuple[str, str, int]] = []
    for line in page.splitlines():
        row = segments(line)
        first = len(row) >= 4 and re.fullmatch(THOUSANDS, row[0][1]) is not None  # the row that also prints the total
        at = int(first)
        if len(row) < at + 3 or not re.fullmatch(THOUSANDS, row[at][1]) or "," not in row[at + 1][1]:
            continue
        if first:
            counted, winner = number(row[0][1]), row[-1][1]
        candidates.append((clean(row[at + 1][1].replace(" ,", ",")), row[at + 2][1], number(row[at][1])))
    if sum(votes for _, _, votes in candidates) != counted:
        raise ValueError(f"2014 presidential votes add up to {sum(v for _, _, v in candidates)}, not {counted}")
    return counted, candidates, clean(winner.replace(" ,", ","))


@dataclass
class Turnout:
    region: str
    name: str
    registered: int | None
    ballots: int  # ballot papers issued to the constituency's polling stations
    valid: int
    invalid: int
    cast: int


TURNOUT_NUMBERS = re.compile(
    r"(?:(?P<registered>\d{1,3}(?:,\d{3})*)\s+)?(?P<ballots>\d[\d,]*)\s+(?P<valid>\d[\d,]*)\s+(?P<invalid>\d[\d,]*)\s+(?P<cast>\d[\d,]*)"
    r"(?:\s+(?P<turnout>\d+(?:\.\d+)?))?\s*$"
)
TURNOUT_HEADS = {
    "Total",
    "Registered",
    "Ballots",
    "Valid",
    "Reject",
    "Rejected",
    "Votes",
    "Counted",
    "Cast",
    "Casted",
    "Voters",
    "%Turnout",
    "Region",
    "Constituency",
}


def _plain(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name.replace("ǁ", "/").replace("‡", "#"))
    return re.sub(r"[^a-z]+", "", "".join(c for c in folded if not unicodedata.combining(c)).casefold())


def read_turnout(title: str, stop: str) -> tuple[list[Turnout], Turnout, list[tuple[int, str]]]:
    """The constituency rows of a results table in the 2024 report, and its printed subtotal row.

    The third value lists the region labels printed beside the rows, each with the number of rows above its line.
    The table starts at the page that prints ``title`` and ends at the page with the SUB-TOTAL row. A constituency
    name that wraps sits on the lines just above and below its numbers, so each fragment goes to the nearest numbers.
    """
    pages = text("report2024")
    first = next(i for i, page in enumerate(pages) if title in page)
    last = next(i for i in range(first, len(pages)) if "SUB-TOTAL" in pages[i])
    rows: list[Turnout] = []
    labels: list[tuple[int, str]] = []
    subtotal: Turnout | None = None
    for index in range(first, last + 1):
        lines = pages[index].splitlines()
        if index == first:
            lines = lines[next(i for i, line in enumerate(lines) if title in line) + 1 :]
        head = next(i for i, line in enumerate(lines) if "Constituency" in line)
        column = lines[head].index("Constituency") - 3
        numbers: list[tuple[int, re.Match[str], list[str]]] = []
        fragments: list[tuple[int, str]] = []
        for i, line in enumerate(lines[head:], head):
            if stop in line or "ECN 2024 NPA" in line or not line.strip():
                continue
            found = TURNOUT_NUMBERS.search(line)
            row = segments(line[: found.start()] if found else line)
            if not found and all(word in TURNOUT_HEADS for _, t in row for word in t.split()):
                continue
            left = [t for start, t in row if start >= column or t == "SUB-TOTAL"]
            labels.extend((len(rows) + len(numbers), t) for start, t in row if start < column and t != "SUB-TOTAL")
            if found:
                numbers.append((i, found, left))
            else:
                fragments.extend((i, t) for t in left)
        pieces: list[list[tuple[int, str]]] = [[(i, t) for t in left] for i, _, left in numbers]
        for i, t in fragments:
            distance = sorted((abs(i - at), k) for k, (at, _, _) in enumerate(numbers))
            near = [k for d, k in distance if d == distance[0][0]]
            if len(near) > 1:  # a fragment between two rows belongs to the one whose numbers have no name beside them
                near = [k for k in near if not numbers[k][2]]
            if len(near) != 1:
                raise ValueError(f"2024 report page {index + 1}: cannot tell which row {t!r} belongs to")
            pieces[near[0]].append((i, t))
        for (_, found, _), parts in zip(numbers, pieces, strict=True):
            name = clean(" ".join(t for _, t in sorted(parts)))
            registered = found.group("registered")
            row = Turnout(
                "",
                name,
                number(registered) if registered else None,
                number(found.group("ballots")),
                number(found.group("valid")),
                number(found.group("invalid")),
                number(found.group("cast")),
            )
            if row.valid + row.invalid != row.cast:
                raise ValueError(f"2024 report: {name}: {row.valid} + {row.invalid} is not {row.cast}")
            if (
                row.registered
                and "." in (found.group("turnout") or "")
                and abs(row.cast / row.registered * 100 - float(found.group("turnout"))) > 0.006
            ):
                raise ValueError(
                    f"2024 report: {name}: printed turnout {found.group('turnout')} is not cast / registered"
                )
            if name == "SUB-TOTAL":
                subtotal = row
            else:
                rows.append(row)
    if subtotal is None:
        raise ValueError(f"2024 report: no subtotal under {title!r}")
    return rows, subtotal, labels


FOREIGN = "foreign-missions"
# The regions in the order the 2024 report's tables run, each with its number of constituencies; the report prints
# each region's name beside the middle of its rows, which is checked against these counts.
REGIONS_2024 = (
    ("Kharas", 7),
    ("Erongo", 7),
    ("Hardap", 8),
    ("Kavango East", 6),
    ("Kavango West", 8),
    ("Khomas", 10),
    ("Kunene", 7),
    ("Ohangwena", 12),
    ("Omaheke", 7),
    ("Omusati", 12),
    ("Oshana", 11),
    ("Oshikoto", 11),
    ("Otjozondjupa", 7),
    ("Zambezi", 8),
    ("Foreign Missions", 1),
)
# The text layer draws the clicks of !Nami#nus and Moses //Garoeb as other characters, and a capital L as a lowercase one.
SPELLINGS_2024 = {"Nehale lyaMpingana": "Nehale LyaMpingana"}


def _spelled(name: str) -> str:
    name = SPELLINGS_2024.get(name, name).replace("‡", "#").replace("ǁ", "//")
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))


def _regions_2024(rows: int, labels: list[tuple[int, str]]) -> list[str]:
    """The region of each row, after checking that the report prints each region's name beside its own rows."""
    regions = [region for region, count in REGIONS_2024 for _ in range(count)]
    if len(regions) != rows:
        raise ValueError(f"2024 report: {rows} rows, but the regions' constituency counts add up to {len(regions)}")
    at, text_ = 0, ""
    for region, count in REGIONS_2024:
        end = at + count
        spots = []
        while _plain(text_) != _plain(region):
            if not labels or not _plain(region).startswith(_plain(text_ + labels[0][1])):
                raise ValueError(f"2024 report: the region label for {region} is not where {count} rows put it")
            spots.append(labels[0][0])
            text_ += labels.pop(0)[1]
        if not all(at <= spot <= end for spot in spots):
            raise ValueError(f"2024 report: the label {region} is not beside rows {at + 1} to {end}")
        at, text_ = end, ""
    if labels:
        raise ValueError(f"2024 report: region labels left over: {labels}")
    return regions


@cache
def turnout_2024() -> tuple[list[tuple[str, str, Turnout, Turnout]], tuple[Turnout, Turnout]]:
    """Each constituency's code, English name, and its row in the presidential and National Assembly tables, plus both subtotals.

    The tables print each region's name once, beside its rows. The foreign missions are one more row, with no
    registered voters.
    """
    president, president_total, labels = read_turnout("Table 18: Results Count", "9. ELECTION")
    assembly, assembly_total, assembly_labels = read_turnout("Table 20: Results Count", "9. ELECTION")
    if [r.name for r in president] != [r.name for r in assembly]:
        raise ValueError("the presidential and National Assembly tables list different constituencies")
    regions = _regions_2024(len(president), labels)
    if _regions_2024(len(assembly), assembly_labels) != regions:
        raise ValueError("2024 report: the two tables group the constituencies differently")
    out: list[tuple[str, str, Turnout, Turnout]] = []
    for region, a, b in zip(regions, president, assembly, strict=True):
        if a.registered != b.registered or a.ballots != b.ballots:
            raise ValueError(f"{a.name}: the two tables print different registered voters or ballot papers")
        if region == "Foreign Missions":
            out.append((FOREIGN, "Foreign missions", a, b))
            continue
        name = _spelled(a.name)
        out.append((f"{slug(region)}-{slug(name)}", f"{name} constituency, {region} region", a, b))
    if len({code for code, *_ in out}) != len(out):
        raise ValueError("2024 report: two rows share a constituency")
    for table, total in ((president, president_total), (assembly, assembly_total)):
        for column in ("registered", "ballots", "valid", "invalid", "cast"):
            if sum(getattr(r, column) or 0 for r in table) != getattr(total, column):
                raise ValueError(f"2024 report: the {column} column does not add up to its printed subtotal")
    return out, (president_total, assembly_total)


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
    "RUNDU_CONCERNED_CITIZEN_ASSOCIATION": "Rundu Concerned Citizen Association",
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
    r"swako\w+ res": "SWAKOPMUND_RESIDENTS_ASSOCIATION",
    r"rundu concerned": "RUNDU_CONCERNED_CITIZEN_ASSOCIATION",
    r"association of otjiwarongo": "OTJIWARONGO_RESIDENTS_ASSOCIATION",
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


# --- 1992 regional councils and local authorities, 2010 local authorities (Government Gazette) -------------------------


def early_party(label: str) -> str | None:
    """The key of the party a label of the 1992 and 2010 notices names, whose OCR text splits and misspells some."""
    label = clean(label).replace("’", "'")
    label = re.sub(r"(?i)sw\s?a[p?]o", "SWAPO", label)
    label = re.sub(r"(?i)\b[do]\s?t\s?a\b", "DTA", label)
    label = re.sub(r"(?i)workers'? revolutionary p\w+", "Workers Revolutionary Party", label)
    label = re.sub(r"(?i)\bpeoples\b", "people's", label)
    label = label.replace("Paity", "Party").replace(".", "")
    return old_party(label)


def noisy(name: str) -> bool:
    return bool(re.search(r"[^A-Za-zÀ-ÿ'\- ,.]|[A-Za-z][A-Z]", name))


# Candidates whose names neither the results notice nor the notice of nominations gives in clean OCR text, read from
# the scan of the results notice.
MANUAL_NAMES: dict[tuple[str, str], str] = {
    ("Okaku", "SWAPO"): "Ya Kasita, Henock Sheya",
    ("Mashari", "SWAPO"): "Shikongo, Paulus Kaeuru",
    ("Kongola", "DTA"): "Walubita, Mukelabayi Conrad",
    ("Walvis Bay", "SWAPO"): "Emvula, Wilfried Inotira",
    ("Grootfontein", "SWAPO"): "Sheyapo, Fidelis Antonius",
    ("Otavi", "SWAPO"): "Shangetha, Bartholomeus",
    ("Otjiwarongo", "SWAPO"): "Ua-Ndjarakana, Mbeuta",
    ("Soweto", "SWAPO"): "Pandeni, John Alphons",
    ("Rehoboth West Urban", "DTA"): "Cloete, Herman Heinie",
    ("Opuwo", "DTA"): "Mumbuu, Edward Uapundua",
    ("Oshikango", "SWAPO"): "Hishikushitja, Michael",
    ("Windhoek East", "DTA"): "Howes, Kenneth James",
    ("Anamulenge", "SWAPO"): "Andowa, Generosa",
    ("Okatjali", "SWAPO"): "Kapia, Paulus Iilonga",
    ("Engela", "SWAPO"): "Nghiwewelekwa, Heita",
    ("Ondobe", "SWAPO"): "Mwaringange, Billy William",
    ("Ohangwena", "SWAPO"): "Kandjambanga, Hamutire Josephine",
    ("Kapako", "SWAPO"): "Kongowa, Gabriel Kalimbwe",
    ("Omatako", "DTA"): "Tjihuro, Arnold Ngaurije",
    ("Katutura Central", "SWAPO"): "!!Gowases, Tsukhoe",
    ("Keetmanshoop Urban", "SWAPO"): "!Ha-Eiros, Ida Jimmy",
    ("Swakopmund", "DTA"): "Göthje, Hans-Dieter Kurt",
    ("Windhoek West", "DTA"): "Mudge, Henry Ferdinand",
    ("Windhoek Rural", "DTA"): "Tsuob, Albert",
    ("Omaruru", "DTA"): "Scheepers, Ian André",
}


def read_rc1992() -> list[Contest]:
    nominated = early.read_nominations_1992(text("nom1992"))
    contests: list[Contest] = []
    for seat in early.read_rc1992(text("rc1992")):
        close = difflib.get_close_matches(seat.name, list(nominated), 1, 0.7)
        pool: dict[str | None, list[str]] = {}
        for name, label in nominated.get(close[0], []) if close else []:
            code = early_party("WRP" if label.lower() == "workers" else label) if label else "?"
            pool.setdefault(code, []).append(name)
        printed = seat.names if len(seat.names) == len(seat.parties) else [""] * len(seat.parties)
        used: dict[str | None, int] = {}
        candidates: list[Candidate] = []
        for name, label, votes in zip(printed, seat.parties, seat.votes, strict=True):
            code = early_party(label)
            name = re.sub(r"^([^\s,.]+)\. ", r"\1, ", clean(name))
            taken = used[code] = used.get(code, -1) + 1
            if code and (seat.name, code) in MANUAL_NAMES:
                name = MANUAL_NAMES[seat.name, code]
            elif not name or noisy(name):
                options = pool.get(code, [])
                if len(options) > taken:
                    name = options[taken]
                elif pool.get("?"):
                    name = pool["?"].pop(0)  # a nomination whose party the OCR text lost
                else:
                    print("MISSING", seat.name, label, repr(name), seat.names, pool)
                    name = "?"
            candidates.append(Candidate(name, code, votes, label=label))
        top = max(candidates, key=lambda c: c.votes or 0)
        if sum(1 for c in candidates if c.votes == top.votes) != 1:
            raise ValueError(f"1992 regional: {seat.name} has no single leader")
        top.elected = True
        contests.append(
            Contest(
                region_name(seat.region),
                seat.name,
                counted=seat.total,
                rejected=seat.rejected,
                candidates=candidates,
                declared=top.name,
            )
        )
    return contests


def _entries(rows: list[early.Row]) -> list[Entry]:
    return [Entry(row.label, row.votes, row.seats, list(row.names)) for row in rows]


def read_la1992() -> list[Area]:
    areas = [
        Area("", place.name, place.total, place.quota, _entries(place.rows), rejected=place.rejected)
        for place in early.read_la1992(text("la1992"))
    ]
    areas += [Area("", name, 0, "", [Entry(label, 0, seats)], tacit=True) for name, label, seats in early.TACIT_1992]
    return sorted(areas, key=lambda area: area.name)


def read_la2010() -> list[Area]:
    areas: list[Area] = []
    for place in early.read_2010(text("la2010")):
        region = difflib.get_close_matches(place.region.replace("OTOZ", "OTJOZ"), early.REGIONS, 1, 0)[0]
        entries = _entries(place.rows)
        for entry in entries:
            for name in list(entry.names):
                elsewhere = any(name in other.names for other in entries if other is not entry)
                if elsewhere and len(entry.names) > entry.seats:
                    entry.names.remove(name)
            if len(entry.names) != entry.seats:
                raise ValueError(
                    f"2010 local: {place.name} {entry.label} has {len(entry.names)} names, {entry.seats} seats"
                )
        areas.append(
            Area(
                region.title(),
                place.name,
                place.total,
                place.quota,
                entries,
                rejected=place.rejected,
                tacit=place.total == 0,
            )
        )
    return areas


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
    if name == "rc1992":
        return tuple(read_rc1992())
    if name == "rc2010":
        return tuple(read_2010())
    if name == "rc1998":
        return tuple(read_rc1998())
    if name == "rc2004":
        return tuple(read_rc2004())
    return tuple(read_regional(name, (8, 28, 48) if name == "rcby2025" else None))


# Two media releases are scanned images with no text layer. Their figures were read from the page images and checked
# against each release's own printed totals: the candidates' votes sum to the votes counted in both.
MEDIA_RELEASES = {
    "2021-07-02": (
        "byopuwo2021",
        "2021 Opuwo Rural constituency by-election",
        Contest(
            "Kunene",
            "Opuwo Rural",
            registered=8194,
            counted=3451,
            declared="Muharukua Western Uakaisiua Melchizedek",
            candidates=[
                Candidate("Maundu Heinz Hariki", "NUDO", 414),
                Candidate("Muharukua Western Uakaisiua Melchizedek", "DTA", 2330, True),
                Candidate("Rikambura Kaiho", "RDP", 141),
                Candidate("Tjambiru Kazepanda", "SWAPO", 486),
                Candidate("Tjituri Howards Kaihepere", "IPC", 80),
            ],
        ),
    ),
    "2022-08-12": (
        "byswakopmund2022",
        "2022 Swakopmund constituency by-election",
        Contest(
            "Erongo",
            "Swakopmund",
            registered=37406,
            counted=9918,
            rejected=56,
            declared="Kativa Louisa Ndaninga",
            candidates=[
                Candidate("Dantu Zerolda Venansia", "UDF", 465),
                Candidate("Dausab Rodger", "NEFF", 307),
                Candidate("Kativa Louisa Ndaninga", "IPC", 3625, True),
                Candidate("Musheko Tangeni Hilaria", "LPM", 1362),
                Candidate("Narib Williward", None, 56),
                Candidate("Pascheka Lelanie", "DTA", 672),
                Candidate("Salomon Nehemiah Ndara", "SWAPO", 3122),
                Candidate("Von Seydlitz-Kurzbach Bernhard Karl Siegfried", "UPM", 309),
            ],
        ),
    ),
}


# Where each figure of the scanned media releases is printed, measured from word boxes read with Tesseract on the
# pages rendered at 300 dpi: the page and the box in pixels, keyed by the measure or the candidate's position.
SCAN_DPI = 300
BOXES: dict[str, dict[str | int, tuple[int, int, int, int, int]]] = {
    "byopuwo2021": {
        "registered": (2, 2010, 539, 2095, 575),
        "counted": (1, 704, 1977, 801, 2014),
        1: (1, 1805, 2386, 1879, 2422),
        2: (1, 1805, 2502, 1903, 2539),
        3: (1, 1810, 2619, 1872, 2654),
        4: (1, 1804, 2734, 1877, 2771),
        5: (1, 1806, 2832, 1852, 2869),
    },
    "byswakopmund2022": {
        "registered": (2, 284, 1471, 420, 1507),
        "counted": (1, 730, 1859, 839, 1895),
        "rejected": (1, 740, 1978, 788, 2014),
        1: (1, 1954, 2480, 2025, 2515),
        2: (1, 1954, 2655, 2027, 2691),
        3: (1, 1939, 2831, 2038, 2866),
        4: (2, 1963, 319, 2055, 355),
        5: (2, 1984, 494, 2030, 530),
        6: (2, 1971, 583, 2042, 619),
        7: (2, 1957, 758, 2053, 793),
        8: (2, 1969, 933, 2039, 969),
    },
}


def _locate(p: Package, name: str, contest: Contest) -> None:
    """Page records for a scanned release and a locator for each of its figures, boxes normalized to the page."""
    stored = raw.cached(SOURCE, URLS[name])
    artifact = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{ECN_SOURCE}\x1f{stored.storage_key}"))  # in registry
    sizes: dict[int, tuple[float, float]] = {}
    for line in subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "99", str(stored.path)], capture_output=True, text=True, check=True
    ).stdout.splitlines():
        found = re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line)
        if found:
            sizes[int(found.group(1))] = (float(found.group(2)), float(found.group(3)))
    pages: dict[int, str] = {}
    for number in sorted({box[0] for box in BOXES[name].values()}):
        width, height = sizes[number]
        pages[number] = key("page", name, number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    code = f"{slug(contest.region)}-{slug(contest.name)}"
    measures = {"registered": REGISTERED, "counted": VALID, "rejected": INVALID}
    for spot, (number, x0, y0, x1, y1) in BOXES[name].items():
        width, height = (side * SCAN_DPI / 72 for side in sizes[number])
        p.add(
            "value_locator",
            result_set_id=key("result_set", f"{name}:{code}"),
            unit_id=key("unit", name, code),
            option_id=key("option", f"{name}:{code}", spot) if isinstance(spot, int) else None,
            channel=0,
            measure=VOTES if isinstance(spot, int) else measures[spot],
            count_round=0,
            page_id=pages[number],
            source_id=None,
            x0=round(x0 / width, 4),
            y0=round(y0 / height, 4),
            x1=round(x1 / width, 4),
            y1=round(y1 / height, 4),
        )


# Regional council by-elections read from files with a text layer: the Government Gazette notices of results, the
# Commission's performance assessment and post-election reports, and its annual reports. Each entry names the files
# its figures come from, the first of them printing the candidates' votes; every figure, and every candidate's surname,
# is checked against those files' text when the package is built. The candidates' votes sum to the votes counted in
# every one of them.
@dataclass(frozen=True)
class ByElection:
    files: tuple[str, ...]
    english: str
    contests: tuple[Contest, ...]
    note: str = ""


TEXT_BY_ELECTIONS = {
    "2018-03-28": ByElection(
        ("annual2018",),
        "2018 Ncuncuni constituency by-election",
        (
            Contest(
                "Kavango West",
                "Ncuncuni",
                registered=5045,
                counted=1452,
                declared="Sindjanga Ritha",
                candidates=[
                    Candidate("Mangundu Paulus Kakoma", "APP", 355),
                    Candidate("Sindjanga Ritha", "SWAPO", 1097, True),
                ],
            ),
        ),
        "The Commission's annual report for 2017/18 gives these figures in its account of the by-election: the "
        "final voters' register and the votes for each of the two candidates, which add up to the 1,452 votes it "
        "says were cast. Votes were cast on electronic voting machines, and no rejected ballots are reported.",
    ),
    "2019-06-15": ByElection(
        ("byondangwa2019",),
        "2019 Ondangwa Urban constituency by-election",
        (
            Contest(
                "Oshana",
                "Ondangwa Urban",
                counted=3792,
                declared="Negonga Leonard S",
                candidates=[
                    Candidate("Asser Vincent", "APP", 97),
                    Candidate("Immanuel Angeline", None, 1402),
                    Candidate("Mandume Andreas", "COD", 31),
                    Candidate("Martin Johannes", "DTA", 326),
                    Candidate("Negonga Leonard S", "SWAPO", 1936, True),
                ],
            ),
        ),
        "The report rounds the register to sixteen thousand voters, so no registered voters are stored. Votes were "
        "cast on electronic voting machines.",
    ),
    "2019-08-24": ByElection(
        ("byoshakati2019",),
        "2019 Oshakati East constituency by-election",
        (
            Contest(
                "Oshana",
                "Oshakati East",
                counted=3413,
                declared="Shikongo Abner S",
                candidates=[
                    Candidate("Iiyambo Elias", "COD", 17),
                    Candidate("Kamati Theofelus", "DTA", 185),
                    Candidate("Kambayi Rebbeka", "SWANU", 10),
                    Candidate("Kuutondoka Fiina", None, 234),
                    Candidate("Shaduva Festus D", "NEFF", 32),
                    Candidate("Shikongo Abner S", "SWAPO", 2935, True),
                ],
            ),
        ),
        "The report gives the register only as about 18,385 voters, so no registered voters are stored. Votes were "
        "cast on electronic voting machines.",
    ),
    "2020-01-15": ByElection(
        ("bygobabis2020",),
        "2020 regional council by-elections",
        (
            Contest(
                "Omaheke",
                "Gobabis",
                registered=13457,
                counted=2339,
                declared="Tebele Augustinus",
                candidates=[
                    Candidate("Binga Sylvestor Daniel", "LPM", 571),
                    Candidate("Kauesa Iuonga Elvis", None, 82),
                    Candidate("Modise Ellenterius Braynie", "DTA", 277),
                    Candidate("Tebele Augustinus", "SWAPO", 1409, True),
                ],
            ),
            Contest(
                "Kharas",
                "Keetmanshoop Urban",
                registered=11534,
                counted=3556,
                declared="Minnaar Maxie Meliza",
                candidates=[
                    Candidate("/Goagoseb Abraham", "DTA", 292),
                    Candidate("Minnaar Maxie Meliza", "LPM", 1958, True),
                    Candidate("Shilimela Festus", "SWAPO", 1306),
                ],
            ),
            Contest(
                "Khomas",
                "Khomasdal",
                registered=25550,
                counted=2104,
                declared="Angolo Samuel",
                candidates=[
                    Candidate("Angolo Samuel", "SWAPO", 1227, True),
                    Candidate("Baumgartner Mara Bonita", "LPM", 349),
                    Candidate("Diergaardt Raymond Reginald", "DTA", 326),
                    Candidate("Kauahuma Bartholomeus Tjiunomuinjo", "SWANU", 202),
                ],
            ),
            Contest(
                "Omusati",
                "Oshikuku",
                declared="Matheus Gabriel",
                tacit=True,
                candidates=[Candidate("Matheus Gabriel", "SWAPO", None, True)],
            ),
            Contest(
                "Erongo",
                "Walvis Bay Urban",
                registered=23169,
                counted=3593,
                declared="Knowledge Ndunge Ipinge",
                candidates=[
                    Candidate("Hoaeb Richard", "DTA", 208),
                    Candidate("Jason Kenneth Iilonga", None, 436),
                    Candidate("Knowledge Ndunge Ipinge", None, 1636, True),
                    Candidate("Sirie Topulathana", "SWAPO", 1313),
                ],
            ),
        ),
        "Only one candidate was nominated in Oshikuku, and the returning officer declared him elected on 6 December "
        "2019 without a poll. Votes were cast on electronic voting machines.",
    ),
    "2020-03-09": ByElection(
        ("byotjiwarongo2020",),
        "2020 Otjiwarongo and Opuwo Rural constituency by-elections",
        (
            Contest(
                "Otjozondjupa",
                "Otjiwarongo",
                counted=4682,
                cast=4707,
                rejected=25,
                declared="Mbakera Marlayn Sharlay",
                candidates=[
                    Candidate("Guiteb Sebeteus", "LPM", 752),
                    Candidate("Erastus Mbumba", None, 1020),
                    Candidate("Mbakera Marlayn Sharlay", "SWAPO", 2910, True),
                ],
            ),
            Contest(
                "Kunene",
                "Opuwo Rural",
                counted=3141,
                cast=3161,
                rejected=20,
                declared="Kakuva Uarikua Cornelius Venajo",
                candidates=[
                    Candidate("Kuvare Kapukatua", "NUDO", 640),
                    Candidate("Kakuva Uarikua Cornelius Venajo", "DTA", 2066, True),
                    Candidate("Katupa Steyn Keesire Uauirako Mupyone", "SWAPO", 435),
                ],
            ),
        ),
        "The report prints each constituency's turnout as a percentage but not its register, so no registered "
        "voters are stored.",
    ),
    "2021-08-25": ByElection(
        ("gazette7650",),
        "2021 Katima Mulilo Urban constituency by-election",
        (
            Contest(
                "Zambezi",
                "Katima Mulilo Urban",
                counted=3002,
                declared="Simasiku Kennedy S.",
                candidates=[
                    Candidate("Kubwima Poniso Miller", None, 156),
                    Candidate("Malipa Lynnette M.", "IPC", 296),
                    Candidate("Matengu Mweti Marklee", "DTA", 294),
                    Candidate("Nkando Gibson Kabuna", None, 417),
                    Candidate("Simasiku Chripher M.", "NDP", 282),
                    Candidate("Simasiku Kennedy S.", "SWAPO", 1557, True),
                ],
            ),
        ),
    ),
    "2021-12-17": ByElection(
        ("byncamagoro2021",),
        "2021 Ncamagoro constituency by-election",
        (
            Contest(
                "Kavango West",
                "Ncamagoro",
                registered=3818,
                counted=1268,
                rejected=15,
                declared="Rengi Thomas Kavau",
                candidates=[
                    Candidate("Chimbango Domingo Frans", "DTA", 105),
                    Candidate("Nyundu Moses", None, 153),
                    Candidate("Rengi Thomas Kavau", "SWAPO", 912, True),
                    Candidate("Shindimba Ladislaus Poroto", None, 79),
                    Candidate("Tjiwana Andreas Kambinda", "IPC", 19),
                ],
            ),
        ),
    ),
    "2023-01-06": ByElection(
        ("bygaroeb2023",),
        "2023 Moses //Garoeb constituency by-election",
        (
            Contest(
                "Khomas",
                "Moses //Garoeb",
                registered=42135,
                counted=4822,
                cast=4845,
                rejected=23,
                declared="Ndengu Stefanus",
                candidates=[
                    Candidate("Hashoongo Kleopas Ndalifilwa", None, 72),
                    Candidate("Hengobe Moses", None, 243),
                    Candidate("Mwandingi Moses", "IPC", 1270),
                    Candidate("Nakale Paulus Shinime", "NEFF", 267),
                    Candidate("Ndengu Stefanus", "SWAPO", 2970, True),
                ],
            ),
        ),
    ),
    "2023-03-17": ByElection(
        ("byokaku2023",),
        "2023 Okaku constituency by-election",
        (
            Contest(
                "Oshana",
                "Okaku",
                registered=10920,
                counted=4463,
                cast=4485,
                rejected=22,
                declared="Fillipus David Taukondjele",
                candidates=[
                    Candidate("Fillipus David Taukondjele", "SWAPO", 3195, True),
                    Candidate("Haufiku Jason Nghinomenwa", "DTA", 122),
                    Candidate("Jacob Sakaria", "IPC", 852),
                    Candidate("Nambambi Tomas Natangwe", "NEFF", 294),
                ],
            ),
        ),
    ),
    "2023-05-15": ByElection(
        ("gazette8117", "annual2024"),
        "2023 Keetmanshoop Rural constituency by-election",
        (
            Contest(
                "Kharas",
                "Keetmanshoop Rural",
                registered=6675,
                counted=2912,
                declared="Labuschagne Willem Petrus",
                candidates=[
                    Candidate("Eiman Johannes Frederick Jacobus", "DTA", 275),
                    Candidate("Kharuxab Elias", "SWAPO", 872),
                    Candidate("Labuschagne Willem Petrus", "LPM", 1270, True),
                    Candidate("Van Staden Magdalena", "IPC", 32),
                    Candidate("Witbooi Gerrit Albertus", None, 463),
                ],
            ),
        ),
        "The votes are those of Government Notice 177 in Government Gazette 8117 of 23 June 2023; the final "
        "voters' register is the one the Commission's annual report for 2023/24 gives.",
    ),
    "2025-01-17": ByElection(
        ("gazette8576", "annual2025"),
        "2025 Guinas constituency by-election",
        (
            Contest(
                "Oshikoto",
                "Guinas",
                registered=4337,
                counted=2019,
                declared="Khumub Moses",
                candidates=[
                    Candidate("Khumub Moses", "SWAPO", 1407, True),
                    Candidate("Khumub Fransikus", "LPM", 612),
                ],
            ),
        ),
        "The votes are those of Government Notice 25 in Government Gazette 8576 of 13 February 2025; the final "
        "voters' register is the one the Commission's annual report for 2024/25 gives.",
    ),
    "2025-09-29": ByElection(
        ("gazette8764", "byoutapi2025"),
        "2025 Outapi constituency by-election",
        (
            Contest(
                "Omusati",
                "Outapi",
                registered=25814,
                counted=10344,
                declared="Asino Selma Nelago Nakambale",
                candidates=[
                    Candidate("Asino Selma Nelago Nakambale", "SWAPO", 9675, True),
                    Candidate("Shikewambi Fillemon", "IPC", 669),
                ],
            ),
        ),
        "The votes are those of Government Notice 273 in Government Gazette 8764 of 16 October 2025, which spells "
        "the losing candidate Shikewambi; the Commission's post-election report spells him Shikwambi. The final "
        "voters' register is the one the report gives.",
    ),
}

# The Okaku report also prints the votes in each of its eighteen polling teams, fixed (F) and mobile (M): each
# candidate's votes in ballot order, then the ballot papers counted and rejected.
OKAKU_TEAMS = {
    "F101": (176, 4, 83, 16, 279, 2),
    "F102": (243, 2, 70, 17, 332, 2),
    "F103": (166, 4, 58, 6, 234, 0),
    "F104": (195, 12, 82, 3, 292, 0),
    "F105": (273, 2, 79, 1, 355, 1),
    "F106": (160, 3, 9, 1, 173, 0),
    "F107": (186, 14, 45, 2, 247, 0),
    "F108": (147, 8, 10, 0, 165, 0),
    "F109": (160, 0, 56, 136, 352, 4),
    "F110": (52, 17, 27, 2, 98, 2),
    "F111": (79, 1, 108, 2, 190, 1),
    "F112": (80, 0, 24, 11, 115, 0),
    "F113": (125, 2, 8, 1, 136, 0),
    "F114": (100, 1, 37, 82, 220, 1),
    "F115": (290, 5, 18, 1, 314, 0),
    "M301": (291, 34, 11, 1, 337, 5),
    "M302": (233, 11, 98, 10, 352, 4),
    "M303": (239, 2, 29, 2, 272, 0),
}


def _printed(files: tuple[str, ...]) -> str:
    return "\n".join(page for name in files for page in text(name))


def _check_printed(day: str, entry: ByElection) -> None:
    """Every figure and every surname of a by-election is printed in its files, and the votes add up."""
    printed = _printed(entry.files)
    folded = _fold(printed)
    for contest in entry.contests:
        figures = [contest.registered, contest.counted, contest.cast, contest.rejected]
        figures += [c.votes for c in contest.candidates]
        for figure in figures:
            if figure is None:
                continue
            spaced = f"{figure:,}".replace(",", "[ , ]?")
            if not re.search(rf"(?<![\d,]){spaced}(?![\d])", printed):
                raise ValueError(f"{day}: {contest.name} figure {figure} is not printed in {entry.files}")
        for candidate in contest.candidates:
            surname = _fold(candidate.name.split()[0].lstrip("/"))
            if surname not in folded:
                raise ValueError(f"{day}: {candidate.name} is not printed in {entry.files}")
        if not contest.tacit and contest.counted != sum(c.votes or 0 for c in contest.candidates):
            raise ValueError(f"{day}: {contest.name} votes do not add up to {contest.counted}")
        if contest.cast is not None and contest.cast != (contest.counted or 0) + (contest.rejected or 0):
            raise ValueError(f"{day}: {contest.name} ballots cast do not add up")
    if day == "2023-03-17":
        contest = entry.contests[0]
        columns = list(zip(*OKAKU_TEAMS.values(), strict=True))
        expected = [*(c.votes for c in contest.candidates), contest.counted, contest.rejected]
        if [sum(column) for column in columns] != expected:
            raise ValueError("2023-03-17: the Okaku teams do not add up to the constituency")
        for team, row in OKAKU_TEAMS.items():
            if sum(row[:4]) != row[4] or not re.search(rf"{team}\s+" + r"\s+".join(map(str, row)), printed):
                raise ValueError(f"2023-03-17: team {team} is not printed as {row}")


def _okaku_teams(p: Package, tag: str) -> None:
    """The Okaku polling teams as units below the constituency, with their votes and ballot papers."""
    code = "oshana-okaku"
    ctag = f"{tag}:{code}"
    parent = key("unit", tag, code)
    result_set = key("result_set", ctag)
    for team, row in OKAKU_TEAMS.items():
        unit = key("unit", tag, code, team)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NAMIBIA,
            scheme_code="na.polling_station",
            code=f"{tag}:{code}:{team}",
            parent_id=parent,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, f"Polling team {team}, Okaku constituency, Oshana region", "en", "official")
        for position, votes in enumerate(row[:4], 1):
            _put(p, result_set, unit, key("option", ctag, position), VOTES, votes)
        _put(p, result_set, unit, None, VALID, row[4])
        _put(p, result_set, unit, None, INVALID, row[5])
        _put(p, result_set, unit, None, CAST, row[4] + row[5])


@cache
def local(name: str = "la2020") -> tuple[Area, ...]:
    return tuple(read_local(name))


@cache
def early_local(name: str) -> tuple[Area, ...]:
    return tuple(read_la1992() if name == "la1992" else read_la2010())


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
    for name in ("rc2010", "rc2015", "rc2020", "rcby2025", "rc2025", "rc1998", "rc2004", "rc1992"):
        regional(name)
    for name in ("la1992", "la2010"):
        for area in early_local(name):
            for entry in area.lists:
                early_party(entry.label)
    local()
    local("la2025")
    for name in ("la1998", "la2004"):
        old_local(name)
    assembly_2014()
    for _, label, _ in read_2014_president()[1]:
        party_of(label)
    for _, label, _ in national().president:
        party_of(label)
    for label, *_ in national().lists:
        party_of(label)
    for area in (*local(), *local("la2025")):
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
        ("na.polling_station", "polling station", "precinct", 2),
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
    # Every association the 1998 and 2004 tables can name is listed outright, so the registry never depends on
    # which files a run happened to read before writing it.
    for code, name in sorted({**PARTIES, **ASSOCIATION_NAMES, **LOCAL_PARTIES}.items()):
        id = party_id(code)
        p.add("party", id=id, kind="party", polity_id=NAMIBIA)
        p.name(id, name, "en", "official")
        if code not in ASSOCIATIONS.values():
            p.name(id, "PDM" if code == "DTA" else code.replace("_", " "), "en", "abbreviation", False)
        if code == "DTA":
            p.name(id, "Popular Democratic Movement", "en", "official", False)
            p.name(id, "DTA", "en", "abbreviation", False)
    return p


def _put(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None, flags: int = 0
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


def _candidate(
    p: Package, tag: str, contest: str, position: int, name: str, party: str | None, person_name: str | None = None
) -> str:
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
    p.name(person, person_name or name, "en", "official")
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
    flags: int = 0,
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
            _put(p, result_set, unit, None, REGISTERED, contest.registered, flags)
        else:
            _put(p, result_set, unit, None, REGISTERED, contest.registered, flags)
            _put(p, result_set, unit, None, VALID if valid_printed else CAST, contest.counted, flags)
            _put(p, result_set, unit, None, INVALID, contest.rejected, flags)
            _put(p, result_set, unit, None, CAST, contest.cast, flags) if valid_printed else None
        for position, candidate in enumerate(contest.candidates, 1):
            option = _candidate(p, ctag, node, position, candidate.name, candidate.party)
            _put(p, result_set, unit, option, VOTES, candidate.votes, flags)
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
        if tag == "rc2025" and contest.counted and summed < contest.counted * 0.9:
            _caveat(
                p,
                result_set,
                "total",
                "The printed total of votes counted is more than a tenth above the sum of the candidates' printed votes"
                + (
                    ", and the gazette prints only some of the candidates and declares no winner. The candidates"
                    " nominated for this constituency who are missing from the printed table are not stored."
                    if not contest.declared
                    else ". Both are stored as printed."
                ),
            )
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
    if day == "1992-11-30":
        for name in ("rc1992", "nom1992", "la1992"):
            p.uses(raw.cached(SOURCE, URLS[name]))
        _constituencies(
            p,
            day,
            "rc1992",
            "local",
            "1992 Namibian regional council elections",
            f"na/{day}-regional-councils",
            first,
            date(1992, 12, 3),
            regional("rc1992"),
            len(regional("rc1992")),
        )
        _local_early(
            p,
            day,
            "la1992",
            first,
            date(1992, 12, 3),
            "1992 Namibian local authority council elections",
            early_local("la1992"),
            "cast",
        )
        _notes_1992(p)
    elif day == "2010-11-26":
        p.uses(raw.cached(SOURCE, URLS["gazette2010"]))
        _local_early(
            p,
            day,
            "la2010",
            first,
            date(2010, 11, 27),
            "2010 Namibian local authority council elections",
            early_local("la2010"),
            "valid",
        )
    elif day == "2010-11-27":
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
        _local(p, day, first, date(2021, 2, 26), 2020, local())
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
    elif day in MEDIA_RELEASES:
        name, english, contest = MEDIA_RELEASES[day]
        p.uses(raw.cached(SOURCE, URLS[name]))
        _constituencies(
            p,
            day,
            name,
            "by_election",
            english,
            f"na/{day}-by-election",
            first,
            first,
            (contest,),
            1,
            valid_printed=True,
            flags=DIGITIZED,
        )
        _locate(p, name, contest)
    elif day in TEXT_BY_ELECTIONS:
        entry = TEXT_BY_ELECTIONS[day]
        _check_printed(day, entry)
        for name in entry.files:
            p.uses(raw.cached(SOURCE, URLS[name]))
        tag = f"by{day}"
        plural = len(entry.contests) > 1
        _constituencies(
            p,
            day,
            tag,
            "by_election",
            entry.english,
            f"na/{day}-by-election{'s' if plural else ''}",
            first,
            first,
            entry.contests,
            len(entry.contests),
            valid_printed=True,
        )
        if entry.note:
            _caveat(p, key("election", tag), "sources", entry.note)
        if day == "2023-03-17":
            _okaku_teams(p, tag)
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
    elif day == "2025-11-26":
        for name in ("gazette2025", "rccandidates2025", "lacandidates2025"):
            p.uses(raw.cached(SOURCE, URLS[name]))
        _constituencies(
            p,
            day,
            "rc2025",
            "local",
            "2025 Namibian regional council elections",
            f"na/{day}-regional-councils",
            first,
            first,
            regional("rc2025"),
            120,
        )
        _local(p, day, first, first, 2025, local("la2025"))
    return p


def _assembly_2014(p: Package, day: str, first: date) -> None:
    p.uses(raw.cached(SOURCE, URLS["na2014"]))
    p.uses(raw.cached(SOURCE, URLS["gazette2014"]))
    rows, last, printed = assembly_2014()
    tag, path = "na2014", f"na/{day}-general"
    election = _election(
        p, tag, "general", first, first, "2014 Namibian presidential and National Assembly elections", path
    )
    _president_2014(p, election, path)
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


def _president_2014(p: Package, election: str, path: str) -> None:
    """The presidential contest, from Government Notice 259 of the 30 December 2014 Gazette: national totals only."""
    counted, candidates, declared = read_2014_president()
    tag = "na2014:president"
    contest = _contest(
        p,
        tag,
        election,
        "office",
        "2014 Namibian presidential election",
        f"{path}/president",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    result_set = _result_set(p, tag, election, contest)
    unit = _unit(p, tag, "na.national", "national", "Namibia")
    _put(p, result_set, unit, None, VALID, counted)
    p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=counted)
    leader = max(candidates, key=lambda c: c[2])
    if declared != leader[0]:
        raise ValueError(f"declared winner {declared!r} is not the leading candidate {leader[0]!r}")
    for position, (printed, label, votes) in enumerate(candidates, 1):
        surname, _, given = printed.partition(", ")
        party = party_of(label)
        option = _candidate(p, tag, contest, position, printed, party, clean(f"{given} {surname}"))
        _put(p, result_set, unit, option, VOTES, votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            candidacy_id=None,
            kind="elected" if printed == declared else "lost",
            seats=None,
        )
    _caveat(
        p,
        result_set,
        "scope",
        "The notice prints national totals only: the votes counted and each candidate's votes. Results by "
        "constituency are not part of it.",
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


def _largest_remainder(votes: list[int], seats: int) -> list[int]:
    quota = sum(votes) / seats
    won = [int(v // quota) for v in votes]
    order = sorted(range(len(votes)), key=lambda i: -(votes[i] / quota - won[i]))
    for i in order[: seats - sum(won)]:
        won[i] += 1
    return won


def _local_early(
    p: Package, day: str, tag: str, first: date, last: date, english: str, areas: tuple[Area, ...], basis: str
) -> None:
    """Local authority councils of a Government Gazette notice, where an area's printed total counts ``basis`` ballots."""
    path = f"na/{day}-local-authorities"
    election = _election(p, tag, "local", first, last, english, path)
    measure = VALID if basis == "valid" else CAST
    unequal: list[str] = []
    allocated: list[str] = []
    for area in areas:
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
            tacit=area.tacit,
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
        result_set = _result_set(p, ctag, election, contest, tacit=area.tacit, empty=area.tacit)
        if area.quota:
            p.add(
                "note",
                id=key("note", result_set, "quota"),
                entity_id=result_set,
                kind="methodology",
                body_md=f"The Commission prints a quota of {area.quota} votes for the area.",
                lang="en",
            )
        if not area.tacit:
            _put(p, result_set, unit, None, measure, area.cast)
            _put(p, result_set, unit, None, INVALID, area.rejected)
        for position, entry in enumerate(area.lists, 1):
            party = early_party(entry.label)
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
            if not area.tacit:
                _put(p, result_set, unit, option, VOTES, entry.votes)
            if entry.label.startswith("United Democratic Party"):
                _caveat(
                    p,
                    result_set,
                    "party",
                    f"The notice names a party {entry.label} here, which is read as the United Democratic Front of Namibia.",
                )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                candidacy_id=None,
                kind="won" if entry.seats else "lost",
                seats=entry.seats,
            )
            for place, name in enumerate(entry.names, 1):
                person = key("person", ctag, position, place)
                candidacy = key("candidacy", ctag, position, place)
                p.add("person", id=person)
                p.name(person, name, "en", "official")
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=place,
                    ballot_name=name,
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
        if area.tacit:
            _caveat(
                p,
                result_set,
                "tacit",
                "One party submitted a list, so no poll was held and the notice prints no votes. The party is "
                "declared to hold every seat.",
            )
            continue
        votes = [entry.votes for entry in area.lists]
        seats = [entry.seats for entry in area.lists]
        printed = area.cast or 0
        summed = sum(votes) + (area.rejected or 0 if basis == "cast" else 0)
        if summed != printed:
            unequal.append(f"{area.name} (printed {printed:,}, lists {summed:,})")
            _caveat(
                p,
                result_set,
                "total",
                f"The notice prints {printed:,} as the total number of votes counted. The lists' votes "
                + ("and the rejected ballot papers " if basis == "cast" else "")
                + f"add up to {summed:,}. Every figure is stored as printed.",
            )
        if all(votes) and _largest_remainder(votes, sum(seats)) != seats:
            allocated.append(area.name)
            _caveat(
                p,
                result_set,
                "seats",
                "The seats the notice declares differ from a largest remainder allocation of the printed votes, "
                "which gives "
                + ", ".join(str(n) for n in _largest_remainder(votes, sum(seats)))
                + ". The declared seats are stored as printed.",
            )
    if unequal:
        p.accept(
            "accounting.votes_valid",
            "The printed total of votes counted differs from the sum of the lists' votes"
            + (" and rejected ballot papers" if basis == "cast" else "")
            + " for "
            + "; ".join(unequal)
            + ". Each is stored as printed.",
        )


def _notes_1992(p: Package) -> None:
    regional_election = key("election", "rc1992")
    _caveat(
        p,
        regional_election,
        "notice",
        "The Government Gazette notice prints each result as one table of constituencies, with the total number of "
        "votes counted and, in brackets, the rejected ballot papers. It marks the member declared elected by "
        "underlining the name, which the notice's scanned text layer does not keep. The member of each constituency "
        "is stored as the candidate with the most votes. The text layer misreads many names, so the names are read "
        "against the page images and the notice of nominations (Government Notice 155 of 1992). The notice lists no "
        "result for the Keetmanshoop Rural and Olukonda constituencies, where the polls were held again in February "
        "1993.",
    )
    for contest in regional("rc1992"):
        votes = sum(c.votes or 0 for c in contest.candidates)
        printed = contest.counted or 0
        if printed == votes + (contest.rejected or 0):
            continue
        code = f"{slug(contest.region)}-{slug(contest.name)}"
        _caveat(
            p,
            key("result_set", f"rc1992:{code}"),
            "total",
            f"The notice prints {printed:,} as the total number of votes counted, with {contest.rejected:,} rejected "
            f"ballot papers in brackets. The candidates' votes add up to {votes:,}"
            + (
                ", the printed total, so the total leaves out the rejected ballot papers."
                if votes == printed
                else f", and with the rejected ballot papers to {votes + (contest.rejected or 0):,}."
            )
            + " Every figure is stored as printed.",
        )
    _caveat(
        p,
        key("election", "la1992"),
        "notice",
        "The notice prints, for each local authority area with a poll, the total number of votes counted with the "
        "rejected ballot papers in brackets, the quota, each party's votes and seats, and the names of the members "
        "declared elected. The names are not stored. Three areas where one party stood are declared elected without "
        "a poll. The election for the Aroab council was held in February 1993.",
    )


def _local(p: Package, day: str, first: date, last: date, year: int, areas: tuple[Area, ...]) -> None:
    tag, path = f"la{year}", f"na/{day}-local-authorities"
    election = _election(p, tag, "local", first, last, f"{year} Namibian local authority council elections", path)
    unequal: list[str] = []
    for area in areas:
        code = f"{slug(area.region)}-{slug(area.name)}"
        seats = sum(entry.seats for entry in area.lists)
        ctag = f"{tag}:{code}"
        contest = _contest(
            p,
            ctag,
            election,
            "list_allocation",
            f"{year} Namibian local authority council election, {area.name} ({area.region} region)",
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
        if year == 2025 and area.name == "Oniipa":
            _caveat(
                p,
                result_set,
                "name",
                "The gazette leaves the name cell of this area empty. The candidates gazette lists these parties and "
                "candidates under Oniipa.",
            )
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
        + " areas the two differ: "
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
    president_contest = contest
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
    _turnout_2024(p, tag, election, path, president_contest, contest, data)


def _turnout_2024(
    p: Package, tag: str, election: str, path: str, president: str, assembly: str, data: National
) -> None:
    """Registered voters, rejected votes, and votes cast in each constituency, in sets beside the national ones.

    The report's tables print no votes for candidates or parties by constituency, so these sets hold only the
    constituency totals. The national sets stay the default and keep the outcomes.
    """
    p.uses(raw.cached(SOURCE, URLS["report2024"]))
    rows, totals = turnout_2024()
    units = {code: _unit(p, tag, "na.constituency", code, english) for code, english, *_ in rows}
    over: dict[str, list[str]] = {}
    for which, contest, official, subtotal in (
        ("president", president, data.president_counted, totals[0]),
        ("assembly", assembly, data.counted, totals[1]),
    ):
        result_set = key("result_set", f"{tag}:{which}:constituencies")
        summed = sum((row[2] if which == "president" else row[3]).valid for row in rows)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECN_SOURCE,
            status="active",
            is_default=False,
            completeness=min(1.0, summed / official),
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=(
                "Registered voters, rejected votes, and votes cast in each constituency and for the "
                "foreign missions, from the Commission's 2024 election report. Valid votes are the votes cast less "
                "the rejected votes, and are not stored by constituency because the report prints no votes for "
                "candidates or parties by constituency, and it also prints the ballot papers issued to each "
                "constituency, which are not stored here."
            ),
            lang="en",
        )
        for code, _, president_row, assembly_row in rows:
            row = president_row if which == "president" else assembly_row
            unit = units[code]
            _put(p, result_set, unit, None, REGISTERED, row.registered)
            _put(p, result_set, unit, None, CAST, row.cast)
            _put(p, result_set, unit, None, INVALID, row.invalid)
        over[which] = [
            english.split(" constituency")[0]
            for _, english, a, b in rows
            if (row := a if which == "president" else b).registered is not None and row.cast > row.registered
        ]
        if summed != subtotal.valid or summed != official:
            _caveat(
                p,
                result_set,
                "total",
                f"The report's rows add up to {summed:,} valid votes, as its own subtotal prints, while the "
                f"Commission's gazetted national total is {official:,}. Each is stored as printed.",
            )
    p.accept(
        "accounting.turnout",
        "The report prints more votes cast than registered voters in "
        f"{len(over['president'])} constituencies for the presidential election and {len(over['assembly'])} for the "
        "National Assembly election, among them Windhoek East, Windhoek Rural, and Okatyali, whose printed turnout "
        "exceeds 100 percent. Each figure is stored as printed.",
    )
