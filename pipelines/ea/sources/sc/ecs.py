import re
import subprocess
from collections.abc import Sequence
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "sc.ecs"
SEYCHELLES = str(uuid5(package.NAMESPACE, "polity\x1fSC"))
SITE = "https://ecs.sc"
UP = f"{SITE}/wp-content/uploads"
PAGE = f"{SITE}/other-past-elections/"
REGISTERED, CAST, VALID, INVALID, VOTES = 2, 3, 4, 5, 1

URLS = {
    "pres2015-1": f"{UP}/2024/12/Presidential-Results-Election-2015-1st-Round.pdf",
    "pres2015-2": f"{UP}/2024/12/Presidential-Results-Election-2015-2nd-Round.pdf",
    "na2016": f"{UP}/2024/12/RESULTS-NAT-ASSB2016-2.pdf",
    "pres2020": f"{UP}/2024/12/Presidential_2020-Result-Detail-2.pdf",
    "pres2020-summary": f"{UP}/2024/12/RESULTS-2020-PRESIDENTIAL-1.pdf",
    "na2020": f"{UP}/2024/12/ELECTION-2020_-National_Assembly-detail-.pdf",
    "na2020-seats": f"{UP}/2024/12/National_Assembly_2020_Results.pdf",
    "report2025-1": f"{UP}/2025/12/2025-First-Ballot-Election-Report.pdf",
    "report2025-2": f"{UP}/2026/02/2025-Second-Ballot-Election-Report.pdf",
    "pres2025-1": f"{UP}/2025/09/PRESIDENTIAL-NATIONAL-ASSEMBLY-ELECTION-RESULTS-BY-POLITICAL-PARTY-AND-ELECTORAL-AREA-1-1-scaled.jpg",
    "na2025": f"{UP}/2025/09/PRESIDENTIAL-NATIONAL-ASSEMBLY-ELECTION-RESULTS-BY-POLITICAL-PARTY-AND-ELECTORAL-AREA-1-2-scaled.jpg",
}
for _n in range(5, 31):
    URLS[f"na2025-slide{_n}"] = f"{UP}/2025/09/Slide{_n}.jpg"
for _n in range(4, 30):
    URLS[f"pres2025-2-slide{_n}"] = f"{UP}/2025/10/Slide{_n}.jpg"
URLS["pres2025-2-total"] = f"{UP}/2025/10/Slide31.jpg"

# package key -> (date_from, date_to)
DAYS = {
    "2015-12-03": (date(2015, 12, 3), date(2015, 12, 5)),
    "2015-12-16": (date(2015, 12, 16), date(2015, 12, 18)),
    "2016-09-08": (date(2016, 9, 8), date(2016, 9, 10)),
    "2020-10-22": (date(2020, 10, 22), date(2020, 10, 24)),
    "2025-09-25": (date(2025, 9, 25), date(2025, 9, 27)),
    "2025-10-09": (date(2025, 10, 9), date(2025, 10, 11)),
}
EARLIER = {"2015-12-16": ["sc.ecs/2015-12-03"], "2025-10-09": ["sc.ecs/2025-09-25"]}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECS_SOURCE = key("source", "ecs")
ASSEMBLY = key("body", "national_assembly")
PRESIDENCY = key("office", "president")
SYSTEM_FPTP = key("electoral_system", "assembly", "fptp")
SYSTEM_PRESIDENT = key("electoral_system", "president", "two_round")

METHOD = (
    "Counted by the Electoral Commission of Seychelles and published per electoral area. The commission publishes no "
    "figures below the electoral area. Eligible voters are the voters on the certified register, ballots cast are the "
    "ballots in the boxes, rejected votes are the ballots declared invalid, and valid votes are the votes counted for "
    "candidates."
)

# The electoral areas, in the commission's serial order, with the codes its 2025 tables print.
AREAS = [
    ("AA", "Anse Aux Pins"),
    ("AB", "Anse Boileau"),
    ("AE", "Anse Etoile"),
    ("AR", "Anse Royale"),
    ("AC", "Au Cap"),
    ("BL", "Baie Lazare"),
    ("BS", "Baie Ste Anne"),
    ("BV", "Beau Vallon"),
    ("BA", "Bel Air"),
    ("BO", "Belombre"),
    ("CA", "Cascade"),
    ("ER", "English River"),
    ("GL", "Glacis"),
    ("GM", "Grand Anse Mahe"),
    ("GP", "Grand Anse Praslin"),
    ("IP", "Ile Perseverance"),
    ("II", "Inner Islands"),
    ("LM", "Les Mamelles"),
    ("MB", "Mont Buxton"),
    ("MF", "Mont Fleuri"),
    ("PA", "Plaisance"),
    ("PL", "Pointe Larue"),
    ("PG", "Port Glaud"),
    ("RC", "Roche Caiman"),
    ("SL", "Saint Louis"),
    ("TA", "Takamaka"),
]
# La Digue was an electoral area of its own until it joined Inner Islands.
EXTRA = [("LD", "La Digue")]
NAMES = dict(AREAS + EXTRA)
ALIASES = {
    "anseaupins": "AA",
    "anseauxpins": "AA",
    "anseboileau": "AB",
    "anseetoile": "AE",
    "anseroyale": "AR",
    "anseroyal": "AR",
    "aucap": "AC",
    "baielazare": "BL",
    "baiesteanne": "BS",
    "baiestanne": "BS",
    "baiestannepraslin": "BS",
    "baiesteannepraslin": "BS",
    "beauvallon": "BV",
    "belair": "BA",
    "belombre": "BO",
    "cascade": "CA",
    "englishriver": "ER",
    "glacis": "GL",
    "grandansemahe": "GM",
    "grandansepraslin": "GP",
    "ileperseverance": "IP",
    "innerislands": "II",
    "ladigue": "LD",
    "lesmamelles": "LM",
    "montbuxton": "MB",
    "montfleuri": "MF",
    "plaisance": "PA",
    "pointelarue": "PL",
    "portglaud": "PG",
    "rochecaiman": "RC",
    "saintlouis": "SL",
    "stlouis": "SL",
    "takamaka": "TA",
}


def area_code(text: str) -> str:
    found = ALIASES.get(re.sub(r"[^a-z]", "", text.lower()))
    if found is None:
        raise ValueError(f"unknown electoral area {text!r}")
    return found


PARTIES = {
    "pl": "Parti Lepep",
    "us": "United Seychelles",
    "lds": "Linyon Demokratik Seselwa",
    "spm": "Seychelles Patriotic Movement",
    "os": "One Seychelles",
    "ls": "Lalyans Seselwa",
    "lns": "Lalyans Nouvo Sesel",
    "spnm": "Seychelles People's National Movement",
    "sum": "Seychelles United Movement",
    "mls": "Mouvman Lavwa Seselwa",
    "ibt": "Laliberté",
    "pdm": "PDM",
    "spsjd": "SPSJD",
}
PARTY_TEXT = {
    "PARTI LEPEP": "pl",
    "SEYCHELLES PATRIOTIC MOVEMENT": "spm",
    "LINYON DEMOKRATIK SESELWA": "lds",
    "UNITED SEYCHELLES": "us",
    "ONE SEYCHELLES": "os",
    "LALYANS SESELWA": "ls",
}


def party(code: str) -> str:
    return key("party", code)


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def number(text: str) -> int:
    return int(text.replace(",", ""))


def text(name: str, *flags: str) -> str:
    path = raw.cached(SOURCE, URLS[name]).path
    return subprocess.run(
        ["pdftotext", "-layout", *flags, str(path), "-"], capture_output=True, text=True, check=True
    ).stdout


class Row:
    """One electoral area's printed figures: accounting (None where unprinted) and votes by candidate label."""

    def __init__(
        self, area: str, accounting: tuple[int | None, int | None, int | None, int | None], votes: Sequence[int | None]
    ):
        self.area = area
        self.accounting = accounting  # eligible, cast, valid, rejected
        self.votes = votes


class Contest:
    def __init__(
        self,
        rows: list[Row],
        candidates: Sequence[tuple[str, str | None]],
        total: tuple[int | None, int | None, int | None, int | None] | None,
        total_votes: list[int],
    ) -> None:
        self.rows = rows
        self.candidates = candidates  # printed name and party code (None for independent candidates)
        self.total = total
        self.total_votes = total_votes


# --- 2015 and 2020 presidential tables -------------------------------------------------------------------------


def read_2015(which: str) -> Contest:
    count = 6 if which.endswith("1") else 2
    pattern = re.compile(r"^\s*(\d+)\s+(.+?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)$")
    footer = re.compile(r"^\s{10,}(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)$")
    rows: list[Row] = []
    total: tuple[int | None, int | None, int | None, int | None] | None = None
    total_votes: list[int] = []
    for line in text(which).splitlines():
        tail = footer.match(line)
        if tail and len(rows) == 25:
            figures = tuple(number(tail.group(i)) for i in range(1, 5))
            total = (figures[0], figures[1], figures[2], figures[3])
            total_votes = [number(x) for x in re.findall(r"\b\d+\b(?!\.)", re.sub(r"[\d.]+%", " ", tail.group(5)))][
                :count
            ]
            continue
        found = pattern.match(line)
        if found:
            # the table prints eligible voters, ballots cast, valid votes, and rejected votes
            votes = [number(x) for x in re.findall(r"\b\d+\b(?!\.)", re.sub(r"[\d.]+%", " ", found.group(7)))][:count]
            rows.append(
                Row(
                    area_code(found.group(2)),
                    (number(found.group(3)), number(found.group(4)), number(found.group(5)), number(found.group(6))),
                    votes,
                )
            )
    names = (
        [
            ("James Alix Michel", "pl"),
            ("David Pierre", "pdm"),
            ("Patrick Pillay", "ls"),
            ("Wavel John Charles Ramkalawan", "lds"),
            ("Alexia Amesbury", "spsjd"),
            ("Phillip Boule", None),
        ]
        if count == 6
        else [("Wavel John Charles Ramkalawan", "lds"), ("James Alix Michel", "pl")]
    )
    return Contest(rows, names, total, total_votes)


def read_2020_president() -> Contest:
    pattern = re.compile(
        r"^\s*(\d+)\s+(.+?)\s{2,}([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%\s+([\d,]+)\s+[\d.]+%\s+([\d,]+)\s+[\d.]+%"
    )
    rows: list[Row] = []
    for line in text("pres2020").splitlines():
        found = pattern.match(line)
        if found:
            g = found.groups()
            rows.append(
                Row(
                    area_code(g[1]),
                    (number(g[2]), number(g[3]), number(g[5]), number(g[4])),
                    [number(g[6]), number(g[7]), number(g[8])],
                )
            )
    names = [
        ("Wavel John Charles Ramkalawan", "lds"),
        ("Danny Antoine Rollen Faure", "us"),
        ("Marie Alain Basil Raoul St Ange", "os"),
    ]
    return Contest(rows, names, (74634, 66017, 64761, 1256), [35562, 28178, 1021])


# --- 2016 National Assembly ------------------------------------------------------------------------------------

MISMATCH_2016: dict[str, tuple[int, int]] = {}
PARTY_LINE = re.compile(
    r"^(PARTI LEPEP|SEYCHELLES PATRIOTIC MOVEMENT|LINYON DEMOKRATIK SESELWA|INDEPENDENT CANDIDATE)\s*(\d[\d,]*)?$"
)


def read_2016() -> dict[str, tuple[list[tuple[str, str | None]], list[int], int]]:
    found: dict[str, tuple[list[tuple[str, str | None]], list[int], int]] = {}
    for page in text("na2016").split("\f"):
        if "ELECTION 2016" not in page or "Seats" in page:
            continue
        lines = [clean(line) for line in page.splitlines() if clean(line)]
        start = next(i for i, line in enumerate(lines) if line.startswith("ELECTION 2016"))
        name = clean(lines[start].removeprefix("ELECTION 2016"))
        body = lines[start + 1 :]
        if not name:
            name, body = body[0], body[1:]
        names: list[str] = []
        parties: list[str | None] = []
        votes: list[int] = []
        total = 0
        for line in body:
            if re.match(r"^(candidates|party/candidates)\b", line, re.IGNORECASE):
                continue
            if re.fullmatch(r"\d[\d,]*", line):
                votes.append(number(line))
                continue
            totalled = re.fullmatch(r"Total\s+(?:100%\s+)?(\d[\d,]*)", line)
            if totalled:
                total = number(totalled.group(1))
                continue
            listed = PARTY_LINE.match(line)
            if listed:
                parties.append(PARTY_TEXT.get(listed.group(1)))
                if listed.group(2):
                    votes.append(number(listed.group(2)))
                continue
            names.append(line)
        if not (len(names) == len(parties) == len(votes)):
            raise ValueError(f"2016 {name}: {names} {parties} {votes} {total}")
        if sum(votes) != total:
            MISMATCH_2016[area_code(name)] = (sum(votes), total)
        found[area_code(name)] = (list(zip((person_name(n) for n in names), parties, strict=True)), votes, total)
    return found


def person_name(text_: str) -> str:
    """The printed 'Given names SURNAME' form, with stray commas dropped."""
    return clean(text_.replace(",", " "))


# --- 2020 National Assembly ------------------------------------------------------------------------------------

NA2020_COLUMNS = ["lds", "os", "ls", "us", None]
FIXES_2020 = {("BV", "Keith Hubert Garvin"): "Keith Hubert Garvin ANDRE"}


def read_2020_assembly() -> tuple[
    dict[str, tuple[tuple[int, int, int, int], list[tuple[str, str | None, int]]]], tuple[int, int, int, int]
]:
    lines = text("na2020").splitlines()
    header = next(line for line in lines if "ELECTORAL AREA" in line and "LDS CANDIDATE" in line)
    starts = [header.index(label) for label in ("LDS CANDIDATE", "ONE SEYCHELLES", "LALYANS SESELWA", "US CANDIDATE")]
    starts.append(header.index("CANDIDATE", starts[3] + 14 + 20))
    head = re.compile(r"^\s*(\d+)\s+([A-Za-z() ]+?)\s{2,}([\d,]+)\s+([\d,]+)\s+(?:([\d,]+)\s+)?([\d,]+)\s")
    segment = re.compile(r"(\S.*?\S)\s{2,}([\d,]+)\s+(\d+(?:\.\d+)?%)")
    rows: dict[str, tuple[tuple[int, int, int, int], list[tuple[str, str | None, int]]]] = {}
    total = (0, 0, 0, 0)
    for line in lines:
        found = head.match(line)
        if found and found.group(2).strip() != "TOTAL" and len(line) > 400:
            code = area_code(found.group(2))
            accounting = (
                number(found.group(3)),
                number(found.group(4)),
                number(found.group(6)),
                number(found.group(5) or "0"),
            )
            end = line.index(found.group(2).strip(), found.end())
            body = line[found.end() : end]
            candidates: list[tuple[str, str | None, int]] = []
            for part in segment.finditer(body):
                offset = found.end() + part.start()
                column = max(i for i, start in enumerate(starts) if start - 4 <= offset)
                name = person_name(part.group(1))
                name = FIXES_2020.get((code, name), name)
                candidates.append((name, NA2020_COLUMNS[column], number(part.group(2))))
            rows[code] = (accounting, candidates)
        elif line.strip().startswith("TOTAL") and "74,634" in line:
            nums = [number(x) for x in re.findall(r"\d[\d,]*", line)[:4]]
            total = (nums[0], nums[1], nums[3], nums[2])
    return rows, total


# --- National Assembly seats by party -------------------------------------------------------------------------

SEAT_ROW = re.compile(r"([A-Z][A-Z ]+?)\s+(\d+)\s+(\d+)(?:\s+(\d+))?(?=\s+[A-Z]|\s*$)")


def read_seats(name: str) -> dict[str, tuple[int, int]]:
    """Party code -> (directly elected seats, proportional seats) from the commission's closing results slide."""
    page = next(p for p in text(name).split("\f") if "Nominated" in p)
    body = clean(page.split("Nominated", 1)[1])
    found: dict[str, tuple[int, int]] = {}
    for match in SEAT_ROW.finditer(body):
        code = PARTY_TEXT[match.group(1).strip()]
        direct, nominated = int(match.group(2)), int(match.group(3))
        if match.group(4) is not None and int(match.group(4)) != direct + nominated:
            raise ValueError(f"{name}: {match.group(0)}")
        found[code] = (direct, nominated)
    return found


# Table 43 of the 2025 first ballot report, read from the page image: party -> (directly elected, proportional seats).
SEATS_2025 = {"lds": (11, 4), "spnm": (0, 0), "us": (15, 4), "mls": (0, 0), "sum": (0, 0), "ibt": (0, 0), "lns": (0, 0)}
# The table prints total seats of 15 and 19 for the two parties that won, and 26, 8, and 34 in its total row.
SEATS_2025_TOTAL = (26, 8, 34)

# --- 2025 tables transcribed from the commission's published images --------------------------------------------

# September 2025 presidential election, first ballot: votes by area for LNS, US, IC2, IC1, SPNM, IC3, LDS, SUM.
PRES_2025_1_COLUMNS = ["Lalyans Nouvo Sesel", "United Seychelles", "IC2", "IC1", "SPNM", "IC3", "LDS", "SUM"]
PRES_2025_1 = {
    "AA": [20, 1391, 3, 6, 21, 12, 1246, 56],
    "AB": [13, 1368, 3, 9, 36, 7, 1354, 67],
    "AE": [35, 1351, 4, 9, 36, 17, 1827, 102],
    "AR": [16, 1419, 6, 7, 22, 15, 1254, 61],
    "AC": [21, 1395, 1, 12, 35, 10, 1520, 64],
    "BL": [17, 1125, 3, 6, 18, 15, 1184, 73],
    "BS": [10, 1672, 2, 10, 28, 2, 1167, 49],
    "BV": [32, 1134, 6, 21, 29, 11, 1481, 45],
    "BA": [37, 856, 0, 6, 22, 12, 861, 39],
    "BO": [39, 1141, 3, 7, 27, 26, 1350, 53],
    "CA": [12, 1228, 2, 6, 19, 6, 1035, 51],
    "ER": [13, 1156, 0, 6, 31, 11, 1133, 51],
    "GL": [35, 1200, 4, 12, 24, 8, 1325, 46],
    "GM": [19, 1097, 0, 9, 25, 7, 1008, 69],
    "GP": [17, 1285, 0, 7, 41, 6, 1048, 27],
    "IP": [18, 1684, 5, 7, 31, 12, 1095, 63],
    "II": [23, 1074, 1, 3, 12, 1, 642, 31],
    "LM": [11, 881, 3, 14, 15, 17, 887, 61],
    "MB": [7, 1101, 2, 7, 11, 6, 1174, 37],
    "MF": [20, 916, 4, 8, 27, 6, 1113, 42],
    "PA": [28, 1304, 4, 12, 22, 13, 1204, 42],
    "PL": [16, 1180, 0, 5, 16, 8, 836, 56],
    "PG": [13, 974, 5, 1, 13, 5, 717, 34],
    "RC": [13, 970, 2, 14, 8, 3, 717, 33],
    "SL": [16, 864, 3, 11, 10, 12, 1100, 42],
    "TA": [12, 970, 2, 2, 14, 5, 952, 35],
}
PRES_2025_1_TOTAL = [513, 30736, 68, 217, 593, 253, 29230, 1329]
PRES_2025_1_VALID = {
    "AA": 2755, "AB": 2857, "AE": 3381, "AR": 2800, "AC": 3058, "BL": 2441, "BS": 2940, "BV": 2759, "BA": 1833,
    "BO": 2646, "CA": 2359, "ER": 2401, "GL": 2654, "GM": 2234, "GP": 2431, "IP": 2915, "II": 1787, "LM": 1889,
    "MB": 2345, "MF": 2136, "PA": 2629, "PL": 2117, "PG": 1762, "RC": 1760, "SL": 2058, "TA": 1992,
}  # fmt: skip
# Printed totals for the first ballot, from the commission's report: eligible, cast, valid, rejected.
PRES_2025_1_PRINTED = (77045, 64809, 62939, 1870)

# September 2025 National Assembly election: votes by area for LDS, IC, SPNM, US, MLS, SUM, IBT, LNS (None: no candidate).
NA_2025_COLUMNS = ["lds", None, "spnm", "us", "mls", "sum", "ibt", "lns"]
NA_2025 = {
    "AA": [1189, None, None, 1390, None, 101, None, 74],
    "AB": [1346, None, 45, 1431, None, 24, None, 13],
    "AE": [1727, None, None, 1363, 75, 120, 22, 65],
    "AR": [1084, None, None, 1662, 24, 41, None, 16],
    "AC": [1384, 152, 39, 1388, None, 56, None, 42],
    "BL": [1207, None, None, 1139, None, 50, None, 35],
    "BS": [1097, None, 36, 1562, 197, 33, None, None],
    "BV": [1370, None, None, 1099, 171, 45, 21, 48],
    "BA": [785, None, None, 954, None, 41, None, 42],
    "BO": [1343, None, 51, 1152, None, 53, None, 43],
    "CA": [1131, None, None, 1089, None, 108, None, 7],
    "ER": [1139, None, None, 1118, 55, 44, None, 32],
    "GL": [1317, 143, 27, 1130, None, 16, None, 27],
    "GM": [974, None, None, 1160, 24, 46, None, 25],
    "GP": [944, 81, 63, 1341, None, 10, None, None],
    "IP": [915, None, 44, 1505, None, 57, 119, 249],
    "II": [550, None, 21, 1102, None, 100, None, None],
    "LM": [889, None, None, 839, None, 125, 11, 19],
    "MB": [1054, None, None, 1093, 154, 42, None, 7],
    "MF": [1247, None, None, 873, None, 27, None, None],
    "PA": [1151, 38, None, 1367, None, 32, None, 23],
    "PL": [837, None, None, 1220, None, 45, None, 24],
    "PG": [699, None, None, 1022, None, 18, None, 23],
    "RC": [646, None, None, 1063, None, 26, None, 13],
    "SL": [1088, None, None, 914, None, 22, 17, 17],
    "TA": [1046, None, None, 904, None, 34, None, 8],
}  # fmt: skip
NA_2025_TOTAL = [28159, 414, 326, 30880, 700, 1316, 190, 852]
INDEPENDENTS_2025 = {"AC": "Flory Larue", "GL": "Kenneth Pierre", "GP": "Yvon Esther", "PA": "Cerlden Padayachy"}
# Eligible voters, ballots cast, valid votes, and rejected votes by area, from each area's results slide.
NA_2025_ACCOUNTING = {
    "AA": (3368, 2827, 2754, 73), "AB": (3456, 2929, 2859, 70), "AE": (4191, 3505, 3372, 133),
    "AR": (3497, 2895, 2827, 68), "AC": (3732, 3131, 3061, 70), "BL": (2923, 2522, 2431, 91),
    "BS": (3585, 2990, 2925, 65), "BV": (3514, 2858, 2754, 104), "BA": (2305, 1887, 1822, 65),
    "BO": (3353, 2746, 2642, 104), "CA": (2871, 2442, 2335, 107), "ER": (2945, 2483, 2388, 95),
    "GL": (3320, 2726, 2660, 66), "GM": (2677, 2290, 2229, 61), "GP": (2931, 2484, 2439, 45),
    "IP": (3432, 3009, 2889, 120), "II": (2140, 1826, 1773, 53), "LM": (2286, 1945, 1883, 62),
    "MB": (2905, 2394, 2350, 44), "MF": (2655, 2203, 2147, 56), "PA": (3256, 2722, 2611, 111),
    "PL": (2546, 2197, 2126, 71), "PG": (2101, 1818, 1762, 56), "RC": (2096, 1808, 1748, 60),
    "SL": (2572, 2104, 2058, 46), "TA": (2388, 2046, 1992, 54),
}  # fmt: skip
NA_2025_PRINTED = (77045, 64787, 62837, 1950)

# October 2025 second ballot: votes for Herminie (US) and Ramkalawan (LDS), then eligible, cast, valid, rejected.
PRES_2025_2 = {
    "AA": (1567, 1277, 3368, 2908, 2844, 64), "AB": (1525, 1392, 3456, 2985, 2917, 68),
    "AE": (1576, 1936, 4191, 3602, 3512, 90), "AR": (1594, 1333, 3497, 3006, 2927, 79),
    "AC": (1561, 1616, 3732, 3250, 3177, 73), "BL": (1267, 1252, 2923, 2591, 2519, 72),
    "BS": (1813, 1285, 3585, 3144, 3098, 46), "BV": (1315, 1579, 3514, 2973, 2894, 79),
    "BA": (989, 892, 2305, 1936, 1881, 55), "BO": (1306, 1458, 3353, 2847, 2764, 83),
    "CA": (1359, 1094, 2871, 2522, 2453, 69), "ER": (1288, 1188, 2945, 2542, 2476, 66),
    "GL": (1375, 1380, 3320, 2818, 2755, 63), "GM": (1212, 1059, 2677, 2340, 2271, 69),
    "GP": (1408, 1143, 2931, 2602, 2551, 51), "IP": (1865, 1131, 3432, 3076, 2996, 80),
    "II": (1163, 670, 2140, 1875, 1833, 42), "LM": (993, 969, 2286, 2006, 1962, 44),
    "MB": (1219, 1227, 2905, 2509, 2446, 63), "MF": (1051, 1151, 2655, 2261, 2202, 59),
    "PA": (1446, 1254, 3256, 2785, 2700, 85), "PL": (1323, 912, 2546, 2276, 2235, 41),
    "PG": (1058, 764, 2101, 1858, 1822, 36), "RC": (1054, 740, 2096, 1834, 1794, 40),
    "SL": (986, 1132, 2572, 2161, 2118, 43), "TA": (1076, 989, 2388, 2112, 2065, 47),
}  # fmt: skip
PRES_2025_2_TOTAL = (34389, 30823)
PRES_2025_2_PRINTED = (77045, 66819, 65212, 1607)
# The first-ballot candidates, with the party each stood for.
CANDIDATES_2025_1 = [
    ("Marie Alain Raoul St. Ange", "lns"),
    ("Mathew Antonio Patrick Herminie", "us"),
    ("Independent candidate IC2", None),
    ("Independent candidate IC1", None),
    ("Robert Antoine Moumou", "spnm"),
    ("Independent candidate IC3", None),
    ("Wavel John Charles Ramkalawan", "lds"),
    ("Maarco Francis", "sum"),
]
CANDIDATES_2025_2 = [("Mathew Antonio Patrick Herminie", "us"), ("Wavel John Charles Ramkalawan", "lds")]


def packages() -> list[str]:
    return ["sc.ecs/geo", "sc.ecs/registry", *(f"sc.ecs/{day}" for day in DAYS)]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=".pdf" if url.endswith(".pdf") else ".jpg")


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        "sc.ecs/geo",
        SOURCE,
        "Seychelles' electoral areas, from the Electoral Commission's results tables",
    )
    p.external.add(SEYCHELLES)
    p.add("scheme", code="sc.area", polity_id=SEYCHELLES, kind="electoral_district", rank=1, noun="electoral area")
    p.add("scheme", code="sc.count", polity_id=SEYCHELLES, kind="precinct", rank=2, noun="counting area")
    for code, name in AREAS + EXTRA:
        id = key("area", code)
        p.add("area", id=id, scheme_code="sc.area")
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "en", "official")
    return p


def registry() -> Package:
    p = Package(
        "sc.ecs/registry",
        SOURCE,
        "Seychelles' parties, National Assembly, presidency, and electoral systems, from the Electoral Commission's "
        "results tables",
    )
    p.external.add(SEYCHELLES)
    p.add(
        "source",
        id=ECS_SOURCE,
        kind="official_body",
        publisher="Electoral Commission Seychelles",
        title="Election results published by the Electoral Commission of Seychelles",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Commission Seychelles",
    )
    for url in URLS.values():
        p.artifact(raw.cached(SOURCE, url), ECS_SOURCE)
    p.add("body", id=ASSEMBLY, polity_id=SEYCHELLES, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly of Seychelles", "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=SEYCHELLES, kind="head_of_state")
    p.name(PRESIDENCY, "President of Seychelles", "en", "official")
    systems = (
        (
            SYSTEM_FPTP,
            {"formula": "fptp"},
            (
                "Each electoral area elects one member by plurality. The Assembly's proportional seats, which go to "
                "parties for each 10% of the votes they polled, are stored as a national contest of their own."
            ),
        ),
        (
            SYSTEM_PRESIDENT,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate with more than half of the valid votes is elected; otherwise the two leading candidates "
                "go to a second ballot."
            ),
        ),
    )
    for id, rules, body in systems:
        p.add("electoral_system", id=id, polity_id=SEYCHELLES, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    for code, name in PARTIES.items():
        p.add("party", id=party(code), kind="party", polity_id=SEYCHELLES)
        p.name(party(code), name, "en", "official")
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


class Build:
    def __init__(self, p: Package, day: str) -> None:
        self.p = p
        self.day = day
        self.units: dict[str, str] = {}

    def unit(self, code: str) -> str:
        if code not in self.units:
            unit = self.units[code] = key("unit", f"{self.day}:{code}")
            self.p.add(
                "reporting_unit",
                id=unit,
                polity_id=SEYCHELLES,
                scheme_code="sc.count",
                code=f"{self.day}:{code}",
                area_relation="equals",
                area_id=key("area", code),
                kind="standard",
            )
            self.p.name(unit, NAMES[code], "en", "official")
        return self.units[code]

    def election(self, name: str, days: tuple[date, date], kind: str, english: str, path: str) -> str:
        election = key("election", name)
        self.p.add(
            "election", id=election, polity_id=SEYCHELLES, kind=kind, date_from=days[0], date_to=days[1], status="held"
        )
        self.p.add("slug", path=path, entity_id=election, is_current=True)
        self.p.name(election, english, "en", "official")
        return election

    def contest(
        self, name: str, election: str, english: str, path: str, completeness: float = 1.0, **fields: object
    ) -> tuple[str, str]:
        contest = key("contest", name)
        self.p.add(
            "contest",
            id=contest,
            election_id=election,
            **{"magnitude": 1, "round_number": 1, "default_result_kind": "counted", **fields},
        )
        self.p.name(contest, english, "en", "official")
        self.p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = key("result_set", name)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECS_SOURCE,
            status="active",
            is_default=True,
            completeness=completeness,
        )
        self.p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en"
        )
        return contest, result_set

    def option(
        self, name: str, contest: str, position: int, label: str, party_code: str | None, person: bool = True
    ) -> str:
        p = self.p
        option = key("option", name, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(party_code) if party_code else None,
            ballot_position=position,
            ballot_label=label,
        )
        if person:
            who = key("person", name, position)
            p.add("person", id=who)
            p.name(who, label, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", name, position),
                person_id=who,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=party(party_code) if party_code else None,
                is_independent=party_code is None,
            )
        return option

    def accounting(
        self, result_set: str, unit: str, figures: tuple[int | None, int | None, int | None, int | None]
    ) -> None:
        eligible, cast, valid, rejected = figures
        for measure, value in ((REGISTERED, eligible), (CAST, cast), (VALID, valid), (INVALID, rejected)):
            _put(self.p, result_set, unit, None, measure, value)

    def declare(
        self,
        result_set: str,
        options: list[str],
        total_votes: Sequence[int | None],
        figures: tuple[int | None, int | None, int | None, int | None] | None,
    ) -> None:
        if figures is not None:
            for measure, value in zip((REGISTERED, CAST, VALID, INVALID), figures, strict=True):
                if value is not None:
                    self.p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for option, value in zip(options, total_votes, strict=True):
            if value is not None:
                self.p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)

    def outcomes(self, result_set: str, options: list[str], votes: list[int], round_one: bool, advance: bool) -> None:
        order = sorted(range(len(options)), key=lambda i: -votes[i])
        for rank, index in enumerate(order):
            if advance:
                kind = "advanced" if rank < 2 else "eliminated"
            else:
                kind = "elected" if rank == 0 else "lost"
            self.p.add("outcome", result_set_id=result_set, option_id=options[index], kind=kind)


def build(day: str) -> Package:
    p = Package(
        f"sc.ecs/{day}",
        SOURCE,
        f"The Seychelles elections of {DAYS[day][0]} by electoral area, from the Electoral Commission's results",
        depends=["sc.ecs/geo", "sc.ecs/registry", *EARLIER.get(day, [])],
    )
    p.external.add(SEYCHELLES)
    b = Build(p, day)
    if day == "2015-12-03":
        president_2015(b, 1)
    elif day == "2015-12-16":
        president_2015(b, 2)
    elif day == "2016-09-08":
        assembly_2016(b)
    elif day == "2020-10-22":
        president_2020(b)
        assembly_2020(b)
    elif day == "2025-09-25":
        president_2025_first(b)
        assembly_2025(b)
    else:
        president_2025_second(b)
    return p


def _uses(p: Package, *names: str) -> None:
    for name in names:
        p.uses(raw.cached(SOURCE, URLS[name]))


def _check(
    label: str, rows: list[Row], total: tuple[int | None, int | None, int | None, int | None] | None, votes: list[int]
) -> None:
    for index in range(4):
        if total is not None and total[index] is not None:
            got = sum(row.accounting[index] or 0 for row in rows)
            if got != total[index]:
                print(f"NOTE {label}: accounting column {index} sums to {got}, printed {total[index]}")
    for index, expected in enumerate(votes):
        got = sum(row.votes[index] or 0 for row in rows)
        if got != expected:
            print(f"NOTE {label}: candidate {index} sums to {got}, printed {expected}")


def _national(
    b: Build,
    name: str,
    election: str,
    english: str,
    path: str,
    contest_data: Contest,
    round_number: int,
    previous: str | None,
    advance: bool,
) -> None:
    contest, result_set = b.contest(
        name,
        election,
        english,
        path,
        kind="office",
        office_id=PRESIDENCY,
        round_number=round_number,
        previous_round_id=previous,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    options = [
        b.option(name, contest, position, label, party_code)
        for position, (label, party_code) in enumerate(contest_data.candidates, 1)
    ]
    for row in contest_data.rows:
        unit = b.unit(row.area)
        b.accounting(result_set, unit, row.accounting)
        for option, value in zip(options, row.votes, strict=True):
            _put(b.p, result_set, unit, option, VOTES, value)
    b.declare(result_set, options, list(contest_data.total_votes), contest_data.total)
    b.outcomes(result_set, options, contest_data.total_votes, round_number == 1, advance)


def president_2015(b: Build, round_number: int) -> None:
    which = f"pres2015-{round_number}"
    _uses(b.p, which)
    data = read_2015(which)
    first = DAYS[b.day]
    runoff = round_number == 2
    election = b.election(
        which,
        first,
        "runoff" if runoff else "general",
        "2015 Seychelles presidential election" + (", second round" if runoff else ""),
        f"sc/{b.day}-presidential",
    )
    _check(which, data.rows, data.total, data.total_votes)
    _national(
        b,
        which,
        election,
        f"2015 Seychelles presidential election, {'second' if runoff else 'first'} round",
        f"sc/{b.day}-presidential/president",
        data,
        round_number,
        key("contest", "pres2015-1") if runoff else None,
        not runoff,
    )
    if not runoff:
        b.p.add(
            "note",
            id=key("note", "pres2015-1", "labels"),
            entity_id=key("result_set", "pres2015-1"),
            kind="caveat",
            body_md="The table's column heads give the party abbreviations PL, PDM, LS, SNP, SPSJD, and IND above "
            "the candidates' names. Candidates are stored by name; Wavel Ramkalawan is linked to the Linyon "
            "Demokratik Seselwa, the party the second-round table prints for him.",
            lang="en",
        )


def president_2020(b: Build) -> None:
    _uses(b.p, "pres2020", "pres2020-summary")
    data = read_2020_president()
    election = b.election(
        "pres2020", DAYS[b.day], "general", "2020 Seychelles presidential election", f"sc/{b.day}-presidential"
    )
    _check("pres2020", data.rows, data.total, data.total_votes)
    _national(
        b,
        "pres2020",
        election,
        "2020 Seychelles presidential election",
        f"sc/{b.day}-presidential/president",
        data,
        1,
        None,
        False,
    )


def _district(b: Build, name: str, election: str, year: int, code: str, path: str) -> tuple[str, str]:
    return b.contest(
        f"{name}-{code}",
        election,
        f"{year} Seychelles National Assembly election, {NAMES[code]}",
        f"{path}/{code.lower()}-{re.sub(r'[^a-z]+', '-', NAMES[code].lower()).strip('-')}",
        kind="body_seats",
        body_id=ASSEMBLY,
        district_area_id=key("area", code),
        electoral_system_id=SYSTEM_FPTP,
    )


def proportional(
    b: Build, name: str, election: str, year: int, seats: dict[str, tuple[int, int]], wins: dict[str, int]
) -> None:
    """The national contest for the proportionally elected members, with the commission's declared seats by party."""
    for code, (direct, _) in seats.items():
        if wins.get(code, 0) != direct:
            raise ValueError(f"{year}: {code} printed {direct} directly elected seats, areas give {wins.get(code, 0)}")
    if sum(wins.values()) != sum(direct for direct, _ in seats.values()):
        raise ValueError(f"{year}: directly elected seats {wins} against {seats}")
    magnitude = sum(proportional for _, proportional in seats.values())
    contest, result_set = b.contest(
        f"{name}-pr",
        election,
        f"{year} Seychelles National Assembly election, proportional seats",
        f"sc/{b.day}-national-assembly/proportional",
        kind="list_allocation",
        body_id=ASSEMBLY,
        magnitude=magnitude,
        completeness=0.0,
    )
    for position, (code, (direct, nominated)) in enumerate(seats.items(), 1):
        option = key("option", f"{name}-pr", position)
        b.p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(code),
            ballot_position=position,
            ballot_label=PARTIES[code],
        )
        b.p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="won" if nominated else "lost",
            seats=nominated,
        )
    b.p.add(
        "note",
        id=key("note", f"{name}-pr", "seats"),
        entity_id=result_set,
        kind="methodology",
        body_md=f"The commission declares {magnitude} proportional seats in {year}, which parties nominate for each "
        "10% of the votes they polled. This contest holds those seats by party. Each party's directly elected seats "
        "are in the electoral areas' contests; the commission prints "
        + ", ".join(
            f"{PARTIES[code]} {direct} directly elected and {nominated} proportional, {direct + nominated} in all"
            for code, (direct, nominated) in seats.items()
            if direct + nominated
        )
        + ". The commission publishes no votes with these seats.",
        lang="en",
    )


def assembly_2016(b: Build) -> None:
    _uses(b.p, "na2016")
    wins: dict[str, int] = {}
    election = b.election(
        "na2016", DAYS[b.day], "general", "2016 Seychelles National Assembly election", f"sc/{b.day}-national-assembly"
    )
    found = read_2016()
    if len(found) != 25:
        raise ValueError(f"2016 has {len(found)} areas")
    valid = 0
    for code in [c for c, _ in AREAS + EXTRA if c in found]:
        candidates, votes, total = found[code]
        valid += total
        contest, result_set = _district(b, "na2016", election, 2016, code, f"sc/{b.day}-national-assembly")
        options = [b.option(f"na2016-{code}", contest, i, label, who) for i, (label, who) in enumerate(candidates, 1)]
        unit = b.unit(code)
        for option, value in zip(options, votes, strict=True):
            _put(b.p, result_set, unit, option, VOTES, value)
        _put(b.p, result_set, unit, None, VALID, total)
        b.declare(result_set, options, list(votes), (None, None, total, None))
        b.outcomes(result_set, options, votes, True, False)
        top = candidates[max(range(len(votes)), key=votes.__getitem__)][1]
        wins[top or "ic"] = wins.get(top or "ic", 0) + 1
    proportional(b, "na2016", election, 2016, read_seats("na2016"), wins)
    for code, (counted, printed) in MISMATCH_2016.items():
        b.p.accept(
            "accounting.votes_valid",
            f"the commission's {NAMES[code]} sheet prints a total of {printed:,} valid votes while its candidates' "
            f"votes add up to {counted:,}; both are stored as printed",
        )
        b.p.add(
            "note",
            id=key("note", "na2016", code, "total"),
            entity_id=key("result_set", f"na2016-{code}"),
            kind="caveat",
            body_md=f"The commission's sheet for {NAMES[code]} prints a total of {printed:,} valid votes, but the "
            f"candidates' votes it prints add up to {counted:,}. Both are stored as printed.",
            lang="en",
        )
    b.p.add(
        "note",
        id=key("note", "na2016", "national"),
        entity_id=election,
        kind="caveat",
        body_md=f"The commission's summary page prints 61,314 valid votes nationally. The area sheets' printed totals "
        f"add up to {valid:,}, and their candidates' votes add up to {valid - sum(t - c for c, t in MISMATCH_2016.values()):,}. "
        "Each area is stored as its own sheet prints it.",
        lang="en",
    )


def assembly_2020(b: Build) -> None:
    _uses(b.p, "na2020", "na2020-seats")
    wins: dict[str, int] = {}
    election = b.election(
        "na2020", DAYS[b.day], "general", "2020 Seychelles National Assembly election", f"sc/{b.day}-national-assembly"
    )
    rows, total = read_2020_assembly()
    sums = [0, 0, 0, 0]
    for code, _ in AREAS:
        accounting, candidates = rows[code]
        for i in range(4):
            sums[i] += accounting[i]
        contest, result_set = _district(b, "na2020", election, 2020, code, f"sc/{b.day}-national-assembly")
        options = [
            b.option(f"na2020-{code}", contest, i, label, who) for i, (label, who, _) in enumerate(candidates, 1)
        ]
        votes = [v for _, _, v in candidates]
        unit = b.unit(code)
        b.accounting(result_set, unit, accounting)
        for option, value in zip(options, votes, strict=True):
            _put(b.p, result_set, unit, option, VOTES, value)
        b.declare(result_set, options, votes, accounting)
        b.outcomes(result_set, options, votes, True, False)
        top = candidates[max(range(len(votes)), key=votes.__getitem__)][1]
        wins[top or "ic"] = wins.get(top or "ic", 0) + 1
    proportional(b, "na2020", election, 2020, read_seats("na2020-seats"), wins)
    print(f"NOTE na2020 sums {sums} printed {total}")


def president_2025_first(b: Build) -> None:
    _uses(b.p, "pres2025-1", "report2025-1")
    first = DAYS[b.day]
    election = b.election(
        "pres2025-1", first, "general", "2025 Seychelles presidential election", f"sc/{b.day}-presidential"
    )
    eligible = read_eligible_2025()
    contest, result_set = b.contest(
        "pres2025-1",
        election,
        "2025 Seychelles presidential election, first round",
        f"sc/{b.day}-presidential/president",
        kind="office",
        office_id=PRESIDENCY,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    options = [
        b.option("pres2025-1", contest, i, label, who, person=who is not None)
        for i, (label, who) in enumerate(CANDIDATES_2025_1, 1)
    ]
    sums = [0] * 8
    for code, _ in AREAS:
        votes = PRES_2025_1[code]
        if sum(votes) != PRES_2025_1_VALID[code]:
            raise ValueError(f"2025 first ballot {code}: {sum(votes)} != {PRES_2025_1_VALID[code]}")
        unit = b.unit(code)
        b.accounting(result_set, unit, (eligible[code], None, PRES_2025_1_VALID[code], None))
        for i, (option, value) in enumerate(zip(options, votes, strict=True)):
            _put(b.p, result_set, unit, option, VOTES, value)
            sums[i] += value
    if sums != PRES_2025_1_TOTAL:
        raise ValueError(f"2025 first ballot column sums {sums} != {PRES_2025_1_TOTAL}")
    b.declare(result_set, options, list(PRES_2025_1_TOTAL), PRES_2025_1_PRINTED)
    b.p.accept(
        "accounting.declared",
        "the commission publishes ballots cast and rejected votes for this round only as national totals, with no "
        "figure by electoral area, so the declared national figures have no area values to compare with",
    )
    b.outcomes(result_set, options, list(PRES_2025_1_TOTAL), True, True)
    b.p.add(
        "note",
        id=key("note", result_set, "areas"),
        entity_id=result_set,
        kind="caveat",
        body_md="The commission's table for this round gives each area's valid votes and each candidate's votes. It "
        "does not give ballots cast or rejected votes by area, so only the national figures for those are declared. "
        "The table heads the three independent candidates IC1, IC2, and IC3 without naming them, and they are stored "
        "under those labels. The table was read from the commission's published image of it.",
        lang="en",
    )


def read_eligible_2025() -> dict[str, int]:
    found: dict[str, int] = {}
    block = text("report2025-1").split("Number of Eligible Voters by Electoral Area and Gender, 2025")[1]
    for line in block.splitlines():
        match = re.match(r"^\s*([A-Z][A-Z ]+?)\s{2,}[\d,]+\s+[\d,]+\s+([\d,]+)\s*$", line)
        if match and match.group(1).strip() not in ("GRAND TOTAL", "DISTRICT / ELECTORAL AREA"):
            found[area_code(match.group(1))] = number(match.group(2))
    if len(found) != 26:
        raise ValueError(f"2025 eligible voters for {len(found)} areas")
    return found


def assembly_2025(b: Build) -> None:
    _uses(b.p, "na2025", "report2025-1", *(f"na2025-slide{n}" for n in range(5, 31)))
    election = b.election(
        "na2025", DAYS[b.day], "general", "2025 Seychelles National Assembly election", f"sc/{b.day}-national-assembly"
    )
    sums = [0] * 8
    totals = [0, 0, 0, 0]
    wins: dict[str, int] = {}
    for code, _ in AREAS:
        accounting = NA_2025_ACCOUNTING[code]
        cells = NA_2025[code]
        if sum(v or 0 for v in cells) != accounting[2] or accounting[1] != accounting[2] + accounting[3]:
            raise ValueError(f"2025 assembly {code}")
        contest, result_set = _district(b, "na2025", election, 2025, code, f"sc/{b.day}-national-assembly")
        options: list[str] = []
        votes: list[int] = []
        position = 0
        for index, value in enumerate(cells):
            if value is None:
                continue
            position += 1
            who = NA_2025_COLUMNS[index]
            if who is None:
                label, party_code = INDEPENDENTS_2025[code], None
            else:
                label, party_code = PARTIES[who], who
            options.append(b.option(f"na2025-{code}", contest, position, label, party_code, person=who is None))
            votes.append(value)
            sums[index] += value
        unit = b.unit(code)
        b.accounting(result_set, unit, accounting)
        for option, value in zip(options, votes, strict=True):
            _put(b.p, result_set, unit, option, VOTES, value)
        b.declare(result_set, options, votes, accounting)
        b.outcomes(result_set, options, votes, True, False)
        index = max((i for i, v in enumerate(cells) if v is not None), key=lambda i: cells[i] or 0)
        top = NA_2025_COLUMNS[index] or "ic"
        wins[top] = wins.get(top, 0) + 1
        for i in range(4):
            totals[i] += accounting[i]
    if sums != NA_2025_TOTAL:
        raise ValueError(f"2025 assembly column sums {sums} != {NA_2025_TOTAL}")
    if tuple(map(sum, zip(*SEATS_2025.values(), strict=True))) != SEATS_2025_TOTAL[:2]:
        raise ValueError("2025 seats do not add up to the printed total row")
    proportional(b, "na2025", election, 2025, SEATS_2025, wins)
    print(f"NOTE na2025 accounting sums {totals} printed {NA_2025_PRINTED}")


def president_2025_second(b: Build) -> None:
    _uses(b.p, "report2025-2", "pres2025-2-total", *(f"pres2025-2-slide{n}" for n in range(4, 30)))
    election = b.election(
        "pres2025-2",
        DAYS[b.day],
        "runoff",
        "2025 Seychelles presidential election, second round",
        f"sc/{b.day}-presidential",
    )
    contest, result_set = b.contest(
        "pres2025-2",
        election,
        "2025 Seychelles presidential election, second round",
        f"sc/{b.day}-presidential/president",
        kind="office",
        office_id=PRESIDENCY,
        round_number=2,
        previous_round_id=key("contest", "pres2025-1"),
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    options = [b.option("pres2025-2", contest, i, label, who) for i, (label, who) in enumerate(CANDIDATES_2025_2, 1)]
    sums = [0, 0]
    totals = [0, 0, 0, 0]
    for code, _ in AREAS:
        a, c, eligible, cast, valid, rejected = PRES_2025_2[code]
        if a + c != valid or valid + rejected != cast:
            raise ValueError(f"2025 second ballot {code}")
        unit = b.unit(code)
        b.accounting(result_set, unit, (eligible, cast, valid, rejected))
        _put(b.p, result_set, unit, options[0], VOTES, a)
        _put(b.p, result_set, unit, options[1], VOTES, c)
        sums[0] += a
        sums[1] += c
        for i, value in enumerate((eligible, cast, valid, rejected)):
            totals[i] += value
    if tuple(sums) != PRES_2025_2_TOTAL or tuple(totals) != PRES_2025_2_PRINTED:
        raise ValueError(f"2025 second ballot sums {sums} {totals}")
    b.declare(result_set, options, list(PRES_2025_2_TOTAL), PRES_2025_2_PRINTED)
    b.outcomes(result_set, options, list(PRES_2025_2_TOTAL), False, False)
