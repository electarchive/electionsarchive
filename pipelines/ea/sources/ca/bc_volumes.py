"""British Columbia's provincial elections from 1871 to 1990, read from the text layer of the Electoral History of
British Columbia 1871-1986 (Elections British Columbia and the Legislative Library, 1988) and its Supplement
1987-2001 (Legislative Library, 2002), both published by Elections BC. The ca.bc_history pipeline builds them as
packages: a geo package for the districts of each map, a registry package for the parties and electoral systems,
one package per general election, and one per parliament for its by-elections.

The volumes print, for each district, every candidate's votes and share, the total, and from 1928 the rejected
ballots; the elected candidates' family names are in capitals. The reading checks each district: the candidates add
up to the printed total, each share matches its votes, and the capitals name as many members as the district
elects. Where the text layer misreads a figure, the printed share and total settle it, and the repair is logged."""

import itertools
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import polars as pl
import pymupdf

from ea import raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.licenses import PUBLIC_RECORD
from ea.store import DATA

SOURCE = "ca.bc_history"
VOLUME = "https://elections.bc.ca/docs/rpt/1871-1986_ElectoralHistoryofBC.pdf"
SUPPLEMENT = "https://elections.bc.ca/docs/rpt/1987-2001-ElectionHistory.pdf"
ERRATA = "https://elections.bc.ca/docs/rpt/ElectoralHistoryofBritishColumbia1871-1986Errata.pdf"
PUBLISHER = "Elections BC"
TITLES = {
    VOLUME: "Electoral History of British Columbia, 1871-1986",
    SUPPLEMENT: "Electoral History of British Columbia, Supplement, 1987-2001",
    ERRATA: "Electoral History of British Columbia, 1871-1986: Errata",
}
NAMESPACE = uuid5(NAMESPACE_URL, "https://electionsarchive.com/")
# Rows production holds, which the packages reference: British Columbia, its province-wide area, its assembly, and
# the parties that ran before 1991 under the names they carry today.
BC = "01a13fb6-eab7-7501-a0f9-4908fbc129f2"
PROVINCE = "01a13fb6-eab7-7501-a0f9-48fb66b1f67b"
ASSEMBLY = "fc04f6de-b056-5266-86b1-6ddf28de75d8"
HELD = {
    "liberal": "b28bdee3-6b69-585d-a0e4-d688177bf023",
    "conservative": "c79c662f-4fc4-5d3b-98af-3e0d56be3f48",
    "social_credit": "35ecc6b9-793b-5783-a10b-604eb77bcc70",
    "ndp": "d8b8e9e0-62b4-5a4b-8280-b55dfec74117",
    "communist": "b8defee3-ed95-5ecd-816c-22ed32a6d06d",
    "green": "82162765-133e-5270-8253-34cf0bf6f9cc",
    "libertarian": "385526ee-4f61-5834-b712-2c3f9848705e",
    "wcc": "282efebf-132c-5344-9953-c1a1d875fa38",
    "peoples_front": "d190116e-fb6e-58da-936f-c8b3a35c2c26",
}
# The 75-district map of 1991 is not in the archive yet, so the reading stops at the by-elections of the 34th
# parliament; the map the 1986 general election used stays in force until the 1991 general election.
END = date(1991, 10, 17)
DIGITIZED = 8
DPI = 110

# Polling day of each general election, as each election's summary page gives it; 1871 and 1875 polled over weeks.
GENERALS: dict[int, tuple[date, date]] = {
    1871: (date(1871, 10, 16), date(1871, 12, 15)),
    1875: (date(1875, 9, 11), date(1875, 10, 25)),
    **{
        d.year: (d, d)
        for d in (
            date(1878, 5, 22),
            date(1882, 7, 24),
            date(1886, 7, 7),
            date(1890, 6, 13),
            date(1894, 7, 7),
            date(1898, 7, 9),
            date(1900, 6, 9),
            date(1903, 10, 3),
            date(1907, 2, 2),
            date(1909, 11, 25),
            date(1912, 3, 28),
            date(1916, 9, 14),
            date(1920, 12, 1),
            date(1924, 6, 20),
            date(1928, 7, 18),
            date(1933, 11, 2),
            date(1937, 6, 1),
            date(1941, 10, 21),
            date(1945, 10, 25),
            date(1949, 6, 15),
            date(1952, 6, 12),
            date(1953, 6, 9),
            date(1956, 9, 19),
            date(1960, 9, 12),
            date(1963, 9, 30),
            date(1966, 9, 12),
            date(1969, 8, 27),
            date(1972, 8, 30),
            date(1975, 12, 11),
            date(1979, 5, 10),
            date(1983, 5, 5),
            date(1986, 10, 22),
        )
    },
}
ALTERNATIVE = {1952, 1953}
# Members in the legislature at each general election, from each election's summary page.
MEMBERS = {
    **dict.fromkeys((1871, 1875, 1878, 1882), 25),
    1886: 27,
    **dict.fromkeys((1890, 1894), 33),
    **dict.fromkeys((1898, 1900), 38),
    **dict.fromkeys((1903, 1907, 1909, 1912), 42),
    **dict.fromkeys((1916, 1920), 47),
    **dict.fromkeys((1924, 1928), 48),
    1933: 47,
    **dict.fromkeys((1937, 1941, 1945, 1949, 1952, 1953), 48),
    **dict.fromkeys((1956, 1960, 1963), 52),
    **dict.fromkeys((1966, 1969, 1972, 1975), 55),
    **dict.fromkeys((1979, 1983), 57),
    1986: 69,
}

# Party abbreviations as the volumes print them (with the text layer's misreadings of a few), and the party each
# stands for. Labels held in the archive point at the party's row there; the rest are the volumes' own parties.
# Before 1903 candidates ran for or against the government of the day, and the volume's labels for those groupings
# are kept as nuances. A label the volume prints in brackets or with a question mark is uncertain in the volume; the
# reading keeps the label and says so in a note on the election.
PARTIES: dict[str, tuple[str, str]] = {
    "GOV.": ("Government", "nuance"),
    "OPP.": ("Opposition", "nuance"),
    "REF.": ("Reform", "nuance"),
    "PRO.": ("Provincial Party (1898-1900)", "nuance"),
    "IND./GOV.": ("Independent Government", "nuance"),
    "GOV./IND.": ("Independent Government", "nuance"),
    "GOV.(IND.)": ("Independent Government", "nuance"),
    "IND.GOV.": ("Independent Government", "nuance"),
    "IND./OPP.": ("Independent Opposition", "nuance"),
    "IND.OPP.": ("Independent Opposition", "nuance"),
    "IND.REF.": ("Independent Reform", "nuance"),
    "IND./PRO.": ("Independent Provincial Party", "nuance"),
    "OPP./PRO.": ("Opposition and Provincial Party", "nuance"),
    "GOV./PRO.": ("Government and Provincial Party", "nuance"),
    "CONS./OPP.": ("Conservative Opposition", "nuance"),
    "LIB-CONS.": ("Liberal-Conservative", "nuance"),
    "LBR.": ("Labour (Party)", "party"),
    "IND./LBR.": ("Independent Labour", "party"),
    "LIB.": ("liberal", "held"),
    "CONS.": ("conservative", "held"),
    "PC": ("conservative", "held"),
    "SC": ("social_credit", "held"),
    "BCSCL": ("social_credit", "held"),
    "NDP": ("ndp", "held"),
    "NDP-CCF": ("ndp", "held"),
    "CPC": ("communist", "held"),
    "GP": ("green", "held"),
    "BCLP": ("libertarian", "held"),
    "LBN": ("libertarian", "held"),
    "WCC": ("wcc", "held"),
    "PF": ("peoples_front", "held"),
    "BCC": ("British Columbia Constructive Party", "party"),
    "BCCP": ("British Columbia Constructive Party", "party"),
    "CCF": ("Co-operative Commonwealth Federation", "party"),
    "CDP": ("Christian Democratic Party", "party"),
    "CDPC": ("Christian Democratic Party of Canada", "party"),
    "CGW": ("Comrades of the Great War", "party"),
    "CHAM.": ("Chameleon Party", "party"),
    "CIT.": ("Citizens' Party", "party"),
    "CLP": ("Canadian Labour Party (B.C. section)", "party"),
    "CLPBC": ("Canadian Labour Party of British Columbia", "party"),
    "COAL.": ("Coalition", "bloc"),
    "CPC(ML)": ("Communist Party of Canada (Marxist-Leninist)", "party"),
    "DPC": ("Democratic Party of Canada", "party"),
    "EP": ("Emancipation Party", "party"),
    "FAR.": ("Farmer", "party"),
    "FAR.LBR.": ("Farmer-Labour", "party"),
    "FJP": ("Financial Justice Party", "party"),
    "FLP": ("Federated Labour Party", "party"),
    "FTP": ("Free Trade Party", "party"),
    "GATE": ("Gay Alliance Toward Equality", "party"),
    "GAUV": ("Grand Army of the United Veterans", "party"),
    "GWVA": ("Great War Veterans' Association", "party"),
    "ILP": ("Independent Labour Party", "party"),
    "IND.CCF": ("Independent Co-operative Commonwealth Federation", "party"),
    "IND.CONS.": ("Independent Conservative", "party"),
    "IND.FAR.": ("Independent Farmer", "party"),
    "IND.FL": ("Independent Farm Labour", "party"),
    "IND.LBR.": ("Independent Labour", "party"),
    "IND.LIB.": ("Independent Liberal", "party"),
    "IND.PRO.": ("Independent Progressive Association", "party"),
    "IND.PC": ("Independent Progressive Conservative", "party"),
    "IND.SC": ("Independent Social Credit", "party"),
    "IND.SOC.": ("Independent Socialist", "party"),
    "IND.SOC./LBR.": ("Independent Socialist", "party"),
    "IND.SOL.": ("Independent Soldier", "party"),
    "INHP": ("Independent New Hope Party", "party"),
    "LBC": ("Libertas of British Columbia", "party"),
    "LIB.IND.": ("Liberal Industrialist", "party"),
    "LLBC": ("Liberty League of British Columbia", "party"),
    "LPP": ("Labour Progressive Party", "party"),
    "LRC": ("Labour Representation Committee", "party"),
    "LSA": ("League for Socialist Action", "party"),
    "NALP": ("North American Labour Party", "party"),
    "N(I)LP": ("Nanaimo (Independent) Labour Party", "party"),
    "NPIG": ("Non Partisan Independent Group", "party"),
    "NRP": ("New Republic Party for British Columbia", "party"),
    "O.CONS.": ("Official Conservative", "party"),
    "OGM": ("Oxford Group Movement", "party"),
    "PCCF": ("People's Co-operative Commonwealth Federation", "party"),
    "PEOPLE": ("People (Common Herd Party)", "party"),
    "PP": ("People's Party", "party"),
    "PPBC": ("People's Party of British Columbia", "party"),
    "PRO.LIB.": ("Progressive Liberal", "party"),
    "PROV.": ("Provincial Party of British Columbia", "party"),
    "RMG": ("Revolutionary Marxist Group", "party"),
    "RP": ("Republican Party (1980s)", "party"),
    "RPB": ("Religious Political Brotherhood", "party"),
    "S.CONS.": ("Social Conservative", "party"),
    "SCA": ("Social Credit (alliance)", "party"),
    "SCLBC": ("Social Credit League of British Columbia", "party"),
    "SCP": ("Social Credit Party (1949)", "party"),
    "SDPC": ("Social Democratic Party of Canada", "party"),
    "SLP": ("Socialist Labour Party", "party"),
    "SOL.": ("Soldier", "party"),
    "SOL.FAR.": ("Soldier-Farmer", "party"),
    "SOL.LBR.": ("Soldier-Labour", "party"),
    "SPBC": ("Socialist Party of British Columbia", "party"),
    "SPC": ("Socialist Party of Canada", "party"),
    "SPC-WPC": ("Socialist Party of Canada-Workers' Party of Canada", "party"),
    "UE": ("Union of Electors", "party"),
    "UF": ("United Front (Workers and Farmers)", "party"),
    "UFBC": ("United Farmers of British Columbia", "party"),
    "ULV": ("United Labour Veterans", "party"),
    "UNION": ("Unionist League/Association", "party"),
    "UPBC": ("Unionist Party of British Columbia", "party"),
    "V(I)LP": ("Vancouver (Independent) Labour Party", "party"),
    "VRA": ("Vancouver Ratepayers Association", "party"),
    "VWD": ("Victory Without Debt Party", "party"),
    "WFL": ("Women's Freedom League", "party"),
    "WIP": ("Western Independence Party", "party"),
    "WNP": ("Western National Party", "party"),
    "WPC": ("Workers' Party of Canada", "party"),
    "OFF.CONS.": ("Official Conservative", "party"),
    "RPC": ("RPC", "party"),
    # The supplement's labels.
    "LIB": ("liberal", "held"),
    "IND": ("", "independent"),
    "NPP": ("New Populist Party", "party"),
    "HRP": ("Human Race Party", "party"),
    "BCP": ("B.C. Party", "party"),
    "IND.": ("", "independent"),
    "-": ("", "none"),
}
# The text layer's readings of some labels.
LABELS = {
    "NOP": "NDP",
    "WEE": "WCC",
    "OPR.": "OPP.",
    "SOC.": "IND.SOC.",
    "CLP BC": "CLPBC",
    "GREEN": "GP",
    "IND.ICONS.": "IND.CONS.",
    "PR0.": "PRO.",
    "SPC": "SPC",
    "LIB": "LIB",
    "IND.(CONS)": "IND.",
    "IND.(CONS.)": "IND.",
    "IND.(OGM)": "IND.",
    "IND.[SC]": "IND.",
    "IND.[SCJ": "IND.",
    "[IND.JCONS.": "IND.CONS.",
    "[IND.]PC": "IND.PC",
    "GOV.IND.": "GOV./IND.",
    "IND.JCONS.": "IND.CONS.",
    "NILP": "N(I)LP",
    "VILP": "V(I)LP",
    "L CONS": "CONS.",
    "UR": "LIB.",
    "VF": "UF",
}

# District names the text layer splits or garbles, and the name each one prints.
DISTRICTS = {
    "Atlins": "Atlin",
    "Bumaby-Willingdon": "Burnaby-Willingdon",
    "Carib co": "Cariboo",
    "Cari boo": "Cariboo",
    "Chilli whack": "Chilliwhack",
    "Com ox": "Comox",
    "Cran brook": "Cranbrook",
    "c;ranbrook": "Cranbrook",
    "Dewdneys": "Dewdney",
    "EsquimaltB": "Esquimalt",
    "EsquimaltS": "Esquimalt",
    "EsquimaltlO": "Esquimalt",
    "Femie": "Fernie",
    "GreenwoodlO": "Greenwood",
    "Kasloll": "Kaslo",
    "Nelsn-Creston": "Nelson-Creston",
    "Revels toke": "Revelstoke",
    "Sirnilkameen": "Similkameen",
    "Surrey~Guildford-Whalley": "Surrey-Guildford-Whalley",
    "Vancouver Cityl": "Vancouver City",
    "Vancouver East ls": "Vancouver East",
    "Victoria Cityis": "Victoria City",
    "Westminsters": "Westminster",
}
# Candidate names the text layer garbles, as (year, district, printed start) -> the printed name; and the errata the
# Legislative Library published for the 1988 volume, which correct names.
NAMES: dict[tuple[int, str, str], str] = {
    (1916, "Kamloops", "ANDERSO"): "ANDERSON, Frederick William",
}
ERRATA_NAMES = [
    ("UPHILL, Thomas Aubert", "UPHILL, Thomas Hubert"),
    ("NEILL, Allan Webster", "NEILL, Alan Webster"),
    ("TATLOW, Robert Garnet", "TATLOW, Robert Garnett"),
    ("DRURY, Robert Low", "DRURY, Richard Low"),
    ("Drury, Robert Low", "Drury, Richard Low"),
    ("PEARSON, George Sherratt", "PEARSON, George Sharratt"),
    ("MacINTOSH, Macgregor Fullarton", "MacINTOSH, MacGregor Fullarton"),
    ("LeFEAUX, Wallis Walter", "LEFEAUX, Wallis Walter"),
    ("McDONELL, Alan James", "McDONELL, Allan James"),
    ("HILLS, George Ewin", "HILLS, George Edwin"),
    ("Winch, Ernest Edward", "WINCH, Ernest Edward"),
    ("DAILLY, Eileen Elizabet", "DAILLY, Eileen Elizabeth"),
    ("FRASER, Alexander Vaughn", "FRASER, Alexander Vaughan"),
    ("JACOBSEN, Johann Alvin Norman", "JACOBSEN, Johan Alvin Norman"),
]
# Figures the text layer cannot give: the 1933 Atlin row prints its own correction ("Omit 9, should be 419", as the
# errata also say), and the row below it carries a stray dash.
VOTES: dict[tuple[int, str, str], int] = {
    (1933, "Atlin", "ASSELSTINE"): 419,
    (1933, "Atlin", "Falconer"): 136,
}
INT = re.compile(r"^\[?\d{1,3}(?:,\d{3})*\]?[.'_]?$")
PCT = re.compile(r"^\.?\[?\d{0,3}\.\d{2}\]?$")
FOOT = re.compile(r"^(?:[0-9Il]{1,2}|t|\*)$")
MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class Row:
    y: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Figure:
    """A number as printed, with the page and box it sits in."""

    value: int | None
    page: int
    box: tuple[float, float, float, float]
    raw: str = ""


@dataclass
class Entry:
    name: str
    party: str | None
    figures: list[Figure | None]
    shares: list[str | None]
    acclaimed: bool
    page: int
    uncertain: bool = False

    @property
    def votes(self) -> int | None:
        first = self.figures[0] if self.figures else None
        return None if first is None else first.value


@dataclass
class Race:
    general: bool
    year: int
    day: date
    district: str
    magnitude: int
    page: int
    source: str
    ballot: str | None = None
    entries: list[Entry] = field(default_factory=list)
    total: Figure | None = None
    final: Figure | None = None
    rejected: Figure | None = None
    counts: int | None = None
    total_is_ballots: bool = False
    uncertain: bool = False


def rows_of(page: pymupdf.Page) -> list[Row]:
    words = [Word(*w[:5]) for w in page.get_text("words")]
    words.sort(key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
    rows: list[Row] = []
    for w in words:
        centre = (w.y0 + w.y1) / 2
        if rows and abs(rows[-1].y - centre) < 3.5:
            rows[-1].words.append(w)
        else:
            rows.append(Row(centre, [w]))
    for row in rows:
        row.words.sort(key=lambda w: w.x0)
    return rows


def split_leader(words: list[Word]) -> tuple[list[Word], list[Word]] | None:
    """The words before a row's dot leader and the words after it, or None for a row without one."""
    for i, w in enumerate(words):
        m = re.search(r"\.{3,}|·{3,}|(?:\. ){2,}\.", w.text)
        if m or (w.text.count(".") >= 2 and set(w.text) <= set(".·, ")):
            before = words[:i]
            pre = w.text[: m.start()] if m else ""
            if pre.strip():
                before = [*before, Word(w.x0, w.y0, w.x1, w.y1, pre)]
            j = i + 1
            while j < len(words) and set(words[j].text) <= set(".·,_:"):
                j += 1
            return before, words[j:]
    return None


def numfix(text: str) -> str:
    """A number token as printed, past the text layer's slips: a tilde or semicolon for a comma, I or l for 1, a stray
    leading mark, and a footnote mark run into the last group."""
    u = text.replace("~", ",").replace(";", ",").strip("·")
    if re.fullmatch(r"[\dIlO,]+", u) and re.search(r"\d", u):
        u = u.replace("I", "1").replace("l", "1").replace("O", "0")
    u = u.lstrip("'(,").replace("(", "[").replace(")", "]")
    if m := re.fullmatch(r"(\d{1,3}(?:,\d{3})+)\d{1,2}", u):
        u = m.group(1)
    return u


def digits(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def share(text: str) -> float:
    return float(re.sub(r"^\.(?=\d*\.)", "", text.strip("[]")))


def find_date(text: str) -> date | None:
    t = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text.replace("~", "e"))
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)
    t = re.sub(r"\bS(?= [A-Z][a-z]+ \d{4})", "5", t)
    for m in re.finditer(r"(\d{1,2})\s+([A-Z][a-z]+)\s+(1[89]\d\d)", t):
        month = MONTHS.get(m.group(2).lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                continue
    return None


def elected(name: str) -> bool:
    """The volumes print an elected candidate's family name in capitals."""
    name = name.replace(";", ",")
    family = name.split(",")[0].strip() if "," in name else name.split()[0]
    words = family.split()
    letters = re.sub(r"[^A-Za-z]", "", words[-1] if words else "")
    return len(letters) >= 2 and letters[-2:].isupper()


def display(name: str) -> str:
    """'McCREIGHT, John Foster' as 'John Foster McCreight'."""
    name = name.replace(";", ",")
    family, _, given = name.partition(",")
    if not _:
        family, _, given = name.partition(" ")

    def fold(part: str) -> str:
        if m := re.fullmatch(r"((?:[A-Z]?[a-z]+)?)([A-Z][A-Z'\-]+)", part):
            head, tail = m.groups()
            return head + "-".join("'".join(s.capitalize() for s in t.split("'")) for t in tail.split("-"))
        return part

    family = " ".join(fold(part) for part in family.split())
    return re.sub(r"\s+", " ", f"{given.strip()} {family}").strip()


def clean_name(text: str) -> str:
    """A candidate's name past the text layer's slips: footnote marks run into the words, letter-spaced family
    names ("Petti piece"), "1~" or "~" for "r,", and the brackets round given names the volume supplied."""
    bracketed = "]" in text
    text = re.sub(r"1?~(?=\s|$)", "r,", text.replace(";", ",")) if "," not in text else text.replace(";", ",")
    text = re.sub(r"\b1(?=[a-z])", "J", text).replace("!", "l")
    text = re.sub(r"\bMc[li](?=[nv])", "McI", text)
    text = re.sub(r"[\[\]:]", " ", text)
    family, comma, given = text.partition(",")
    pieces = family.split()
    joined = [pieces[0]] if pieces else []
    for piece in pieces[1:]:
        lower = piece[0].islower()
        short_caps = piece.isupper() and len(piece) <= 2 and joined[-1].isupper() and len(joined[-1]) > 2
        if lower or short_caps:
            joined[-1] += piece
        else:
            joined.append(piece)
    words = []
    for word in given.split():
        word = re.sub(r"(?<=[A-Za-z.)\"])\d+[A-Z]?$", "", word)
        if word == "0":
            word = "O"
        if re.fullmatch(r"\d+|[B-Z]", word) and words and re.fullmatch(r"\d+", word):
            continue
        if re.fullmatch(r"\d+", word):
            continue
        words.append(word)
    if words and re.fullmatch(r"[BS]", words[-1]) and bracketed:
        words.pop()
    return f"{' '.join(joined)}{comma} {' '.join(words)}".strip() if comma else " ".join([*joined, *words])


def district_name(text: str) -> tuple[str, int]:
    text = re.sub(r"[-\s]*Continued", "", text.replace("·", ""))
    m = re.search(r"\(\s*(\d)\s*\)", text)
    magnitude = int(m.group(1)) if m else 1
    text = re.sub(r"\(\s*\d\s*\)", "", text)
    text = re.sub(r"(?<=[A-Za-z])\d+\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return DISTRICTS.get(text, text), magnitude


def folded(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.casefold())


def label(tokens: list[str]) -> tuple[str | None, bool]:
    """A party label from the tokens before the votes, and whether the volume marks it uncertain."""
    text = " ".join(t for t in tokens if re.search(r"[A-Za-z]", t))
    uncertain = "?" in text or "[" in text
    text = re.sub(r"[\[\]?]", "", text).strip(" .:,'·-")
    text = re.sub(r"\s+\.+$", "", text)
    text = re.sub(r"(?<=[A-Za-z.)])\d+$", "", text)
    if not text:
        return None, uncertain
    text = text.split()[0] if text.split()[0].upper() in PARTIES or text.split()[0] in LABELS else text
    up = text.upper()
    for candidate in (text, up, up.rstrip(".") + "."):
        if candidate in LABELS:
            return LABELS[candidate], uncertain
        if candidate in PARTIES:
            return candidate, uncertain
    # A footnote mark run into the label: digits, or the letters the text layer reads them as.
    for cut in range(1, 4):
        stem = up[:-cut]
        for candidate in (stem, stem + "."):
            if candidate in LABELS:
                return LABELS[candidate], uncertain
            if candidate in PARTIES:
                return candidate, uncertain
    UNKNOWN.add(text)
    return None, uncertain


UNKNOWN: set[str] = set()


@cache
def document(url: str) -> pymupdf.Document:
    return pymupdf.open(raw.cached(SOURCE, url).path)


def classify(rows: list[Row]) -> tuple[str | None, int | None]:
    head = " ".join(r.text for r in rows[:6])
    year = re.search(r"ELECTION\S*\s+(1[89]\d\d)", head)
    if re.search(r"RESULTS\s+BY\s+ELECTORAL", head, re.IGNORECASE) and year:
        if re.search(r"\bA[nl1I]{1,2}\s+Cou", head, re.IGNORECASE):
            return "all_counts", int(year.group(1))
        return "general", int(year.group(1))
    if re.search(r"BY.?ELECTIONS?\s", head, re.IGNORECASE):
        return "by", None
    return None, None


@cache
def pages() -> dict[tuple[str, int | None], list[int]]:
    found: dict[tuple[str, int | None], list[int]] = defaultdict(list)
    doc = document(VOLUME)
    for number in range(18, 366):
        kind, year = classify(rows_of(doc[number]))
        if kind:
            found[(kind, year)].append(number)
    return dict(found)


def page_columns(body: list[Row]) -> tuple[float, float] | None:
    """Where candidate names start on a page, and where the party or votes column starts."""
    starts: list[int] = []
    rights: list[float] = []
    for r in body:
        s = split_leader(r.words)
        if not s or not s[0]:
            continue
        before = s[0]
        segment = [before[-1]]
        for a, b in zip(before[::-1][1:], before[::-1], strict=False):
            if b.x0 - a.x1 > 12:
                break
            segment.append(a)
        starts.append(round(segment[-1].x0))
        if s[1]:
            rights.append(s[1][0].x0)
    if not starts or not rights:
        return None
    rights.sort()
    return Counter(starts).most_common(1)[0][0], rights[min(1, len(rights) - 1)] if len(rights) > 3 else rights[0]


def candidate_row(words: list[Word], nx: float, rx: float) -> tuple[str, list[Word]] | None:
    s = split_leader(words)
    if s:
        name = [w for w in s[0] if w.x1 >= nx - 2]
        right = s[1]
    else:
        name = [w for w in words if w.x1 >= nx - 2 and w.x0 < rx - 6]
        right = [w for w in words if w.x0 >= rx - 6]
    if not name or not right:
        return None
    text = " ".join(w.text for w in name).strip(" .·")
    tokens = [numfix(w.text) for w in right]
    # A row with no figure is a candidate only when the volume gives a party and no votes (Cassiar in 1886).
    figured = any(INT.match(t) or t.startswith("Accl") or PCT.match(t) for t in tokens)
    if not figured and not (s and "," in text and all(re.search(r"[A-Z]{2,}", t) for t in tokens)):
        return None
    if not re.search(r"[A-Za-z]{2,}", text):
        return None
    return text, [Word(w.x0, w.y0, w.x1, w.y1, numfix(w.text)) for w in right]


def entry(name: str, right: list[Word], page: int, year: int) -> Entry:
    party: list[str] = []
    figures: list[Figure | None] = []
    shares: list[str | None] = []
    acclaimed = False
    for w in right:
        t = w.text
        box = (w.x0, w.y0, w.x1, w.y1)
        if INT.match(t):
            figures.append(Figure(digits(t), page, box, t))
        elif re.fullmatch(r"\d{4,7}", t):
            figures.append(Figure(None, page, box, t))
        elif PCT.match(t):
            shares.append(t)
        elif t.startswith("Accl"):
            acclaimed = True
        elif re.fullmatch(r"[-—–_']+", t):
            if figures or year in ALTERNATIVE:
                figures.append(None)
                shares.append(None)
        elif not figures:
            party.append(t)
    name = clean_name(name)
    for old, new in ERRATA_NAMES:
        if name == old:
            name = new
    abbreviation, uncertain = label(party)
    return Entry(name, abbreviation, figures, shares, acclaimed, page, uncertain)


def general_races(year: int) -> list[Race]:
    """The district results of one general election, page by page."""
    doc = document(VOLUME)
    first, _ = GENERALS[year]
    races: list[Race] = []
    current: Race | None = None
    left_text: dict[int, list[tuple[str, list[Word]]]] = defaultdict(list)
    totals: dict[int, list[Word]] = {}
    for number in pages()[("general", year)]:
        rows = rows_of(doc[number])
        head = max(
            (
                i
                for i, r in enumerate(rows[:9])
                if re.search(r"VOTES|COUNT|%|PAR|PAI|varns", r.text) and not split_leader(r.words)
            ),
            default=None,
        )
        if head is None:
            continue
        body = rows[head + 1 :]
        columns = page_columns(body)
        if columns is None:
            continue
        nx, rx = columns
        started = False
        for r in body:
            if FOOT.match(r.words[0].text) and len(r.words) > 2 and not split_leader(r.words):
                break
            left = [w for w in r.words if w.x1 < nx - 2]
            rest = [w for w in r.words if w.x1 >= nx - 2]
            lt = " ".join(w.text for w in left)
            if left and not re.search(r"[A-Za-z]{3,}", lt) and not re.match(r"(Tot|Rej|Counts?)\b", lt):
                left, lt = [], ""
            t = " ".join(w.text for w in rest)
            if m := re.match(r"Ballot\s+([A-Z])\b", t):
                name = lt if lt and "Continued" not in lt else current.district if current else ""
                district, magnitude = district_name(name)
                if current is not None and current.district.startswith(district):
                    district, magnitude = current.district, current.magnitude
                current = Race(True, year, first, district, magnitude, number, VOLUME, ballot=m.group(1))
                races.append(current)
                continue
            if re.match(r"TO[TfI]AL\b", t) and current is not None:
                totals[id(current)] = rest[1:]
                continue
            found = candidate_row(rest, nx, rx) if rest else None
            if rest and not found and not left:
                continue
            if re.fullmatch(r"[-\s]*Continued", lt):
                left, lt = [], ""
            if (
                left
                and current is not None
                and current.page != number
                and not started
                and district_name(lt)[0] == current.district
            ):
                left, lt = [], ""
            if (
                left
                and not re.match(r"(Tot|Rej|Counts?)\b", lt)
                and not INT.match(left[0].text)
                and "Continued" not in lt
            ):
                if current is not None and not current.entries and not found:
                    district, magnitude = district_name(f"{current.district} ({current.magnitude}) {lt}")
                    current.district, current.magnitude = district, magnitude
                else:
                    district, magnitude = district_name(lt)
                    current = Race(True, year, first, district, magnitude, number, VOLUME)
                    races.append(current)
                left = []
            if current is None:
                continue
            if left:
                left_text[id(current)].append((lt, left))
            if found:
                started = True
                name, right = found
                for (y, d, start), fixed in NAMES.items():
                    if y == year and d == current.district and name.startswith(start):
                        name = fixed
                current.entries.append(entry(name, right, number, year))
    for race in races:
        for text, words in left_text.get(id(race), []):
            text = re.sub(r",\s+(?=\d{3})", ",", text.replace("·", ""))
            figures = [w for w in words if re.search(r"\d", w.text)]
            box = (
                (
                    min(w.x0 for w in figures),
                    min(w.y0 for w in figures),
                    max(w.x1 for w in figures),
                    max(w.y1 for w in figures),
                )
                if figures
                else (0.0, 0.0, 0.0, 0.0)
            )
            for key, pattern in (("tot", r"Tot"), ("rej", r"Rej"), ("counts", r"Counts?")):
                if m := re.search(pattern + r"\.?\s*\[?([\d,]+)", text):
                    figure = Figure(digits(m.group(1)), race.page, box, m.group(1))
                    if key == "tot":
                        race.total = figure
                    elif key == "rej":
                        race.rejected = figure
                    else:
                        race.counts = figure.value
        if id(race) in totals:
            numbers = [w for w in totals[id(race)] if INT.match(numfix(w.text))]
            if numbers:
                w = numbers[0]
                race.total = Figure(digits(numfix(w.text)), race.page, (w.x0, w.y0, w.x1, w.y1), w.text)
            if len(numbers) > 1:
                w = numbers[1]
                race.final = Figure(digits(numfix(w.text)), race.page, (w.x0, w.y0, w.x1, w.y1), w.text)
        for e in race.entries:
            for (y, d, start), value in VOTES.items():
                if y == year and d == race.district and e.name.startswith(start):
                    old = e.figures[0] if e.figures else None
                    page, box = (old.page, old.box) if old else (e.page, (0.0, 0.0, 0.0, 0.0))
                    e.figures = [Figure(value, page, box, str(value))]
    return [race for race in races if race.entries]


KEYWORDS = re.compile(
    r"ISSUE|WRIT|ELECTION|BY-ELECTION|PART\b|PAR|VOTE|PAKI|PAITT|REASON|TOTAL|REJECTED|CONTINUED|RETURNING",
    re.IGNORECASE,
)


def heading(r: Row) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", r.text)
    return (
        len(letters) >= 3
        and letters.isupper()
        and r.words[0].x0 > 120
        and not KEYWORDS.search(r.text)
        and not split_leader(r.words)
    )


@dataclass
class Block:
    """One by-election as its page lays it out, before it is read into a race."""

    heading: str
    page: int
    entries: list[tuple[str, list[Word], int]] = field(default_factory=list)
    dates: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    columns: dict[str, float] = field(default_factory=dict)
    total: Figure | None = None
    rejected: Figure | None = None
    party_x: float | None = None


def first_figure(words: list[Word], page: int) -> Figure | None:
    for w in words:
        if INT.match(numfix(w.text)):
            return Figure(digits(numfix(w.text)), page, (w.x0, w.y0, w.x1, w.y1), w.text)
    return None


def by_races(url: str, numbers: list[int]) -> list[Race]:
    """The by-elections on the given pages: for each, the district heading, the dates under the writ's columns, and
    the results table."""
    doc = document(url)
    blocks: list[Block] = []
    current: Block | None = None
    state = None
    for number in numbers:
        for r in rows_of(doc[number]):
            t = r.text
            if r.y < 118 and re.search(r"ELECTION RESULTS|BY-ELECTIONS|PART", t, re.IGNORECASE):
                continue
            if heading(r) and state in (None, "after", "results"):
                current = Block(t, number)
                blocks.append(current)
                state = "head"
                continue
            if current is None:
                continue
            if state in ("head", "dates") and re.search(r"Issue|ISSUE|Polling|Writ|WRIT|DAY", t) and not find_date(t):
                for w in r.words:
                    for k in ("NOMINATION", "POLLING", "RETURN", "DATE"):
                        if w.text.upper().startswith(k[:5]):
                            current.columns.setdefault(k, w.x0)
                state = "dates"
                continue
            if state == "dates":
                if re.search(r"ELECTION\s+RESULTS", t):
                    state = "results"
                    continue
                columns = current.columns
                for w in r.words:
                    if "POLLING" in columns and w.x0 < columns["POLLING"] - 28:
                        k = "nom" if w.x0 >= columns.get("NOMINATION", 130) - 30 else "issue"
                    elif "RETURN" in columns and w.x0 < columns["RETURN"] - 12:
                        k = "poll"
                    else:
                        k = "rest"
                    current.dates[k].append(w.text)
                continue
            if state != "results":
                continue
            if re.match(r"PAR|PAI|PAK|Party", t) or (re.search(r"VOTE|Votes", t) and not split_leader(r.words)):
                marks = [w for w in r.words if re.match(r"PA|Pa", w.text)]
                current.party_x = (marks[0].x0 - 14) if marks else None
                continue
            if re.match(r"TO[TfI]AL", t):
                current.total = first_figure(r.words[1:], number)
                continue
            if re.match(r"REJ", t):
                current.rejected = first_figure(r.words[1:], number)
                continue
            if re.match(r"REASON", t):
                state = "after"
                continue
            found = candidate_row(r.words, 25, current.party_x or 260)
            if found:
                current.entries.append((found[0], found[1], number))
                continue
            if FOOT.match(r.words[0].text):
                state = "after"
    races: list[Race] = []
    for block in blocks:
        day = find_date(" ".join(block.dates["poll"])) or find_date(" ".join(block.dates["nom"]))
        if day is None:
            raise ValueError(f"by-election {block.heading} on page {block.page}: no date")
        district, magnitude = district_name(block.heading.title())
        race = Race(False, day.year, day, district, magnitude, block.page, url)
        race.entries = [entry(name, right, page, day.year) for name, right, page in block.entries]
        race.total, race.rejected = block.total, block.rejected
        if not race.entries:
            raise ValueError(f"by-election {district} {day}: no candidates")
        races.append(race)
    return races


def supplement_pages() -> list[int]:
    doc = document(SUPPLEMENT)
    found = []
    for number in range(doc.page_count):
        head = " ".join(r.text for r in rows_of(doc[number])[:4])
        if re.search(r"BY-ELECTIONS\s+34TH\s+PARLIAMENT", head):
            found.append(number)
    return found


def settle(race: Race) -> list[str]:
    """Check one race against its printed total and shares, settle the text layer's misreadings, and return what
    was repaired."""
    log: list[str] = []
    where = f"{race.day} {race.district}{' ballot ' + race.ballot if race.ballot else ''}"
    entries = race.entries
    total = race.total.value if race.total else None
    for e in entries:
        for i, f in enumerate(e.figures):
            if f is not None and f.value is None:
                options = [int(f.raw), int(f.raw[:-1]), int(f.raw[:-2])]
                printed_share = e.shares[i] if i < len(e.shares) else None
                p = share(printed_share) if printed_share else None
                others = sum(x.votes or 0 for x in entries if x is not e) if i == 0 else None
                fits = [o for o in options if total is not None and others is not None and others + o == total]
                if fits:
                    f.value = fits[0]
                elif p is not None and total:
                    f.value = min(options, key=lambda o: abs(o * 100 / total - p))
                else:
                    f.value = options[1]
                if f.value != int(f.raw):
                    log.append(f"{where}: {e.name} read {f.raw} as {f.value}")
    if all(e.acclaimed for e in entries) or any(not e.figures for e in entries):
        return log
    first = [e.votes for e in entries]
    if None in first or total is None:
        return log
    counted = sum(v for v in first if v is not None)
    if counted == total:
        return log
    if race.rejected and race.rejected.value is not None and counted + race.rejected.value == total:
        race.total_is_ballots = True
        return log
    wrong: list[tuple[Entry, int, str]] = []
    for e in entries:
        printed_share = e.shares[0] if e.shares else None
        if printed_share and e.votes is not None:
            guess = round(share(printed_share) * total / 100)
            if abs(guess - e.votes) > max(2, total * 0.0006):
                wrong.append((e, guess, printed_share))
    if len(wrong) == 1:
        e, guess, printed_share = wrong[0]
        need = total - (counted - (e.votes or 0))
        head = e.figures[0]
        if need > 0 and head is not None and abs(need * 100 / total - share(printed_share)) <= 0.006:
            printed: list[Figure | None] = [f for f in e.figures[1:] if f is not None and f.value == need]
            if printed:
                log.append(f"{where}: {e.name} has a footnote mark before its votes, {need}")
                e.figures = printed[:1]
            else:
                log.append(f"{where}: {e.name} read {e.votes} as {need} (share {printed_share}, total {total})")
                head.value = need
            return log
    if wrong and counted + sum(g - (e.votes or 0) for e, g, _ in wrong) == total:
        for e, g, printed_share in wrong:
            head = e.figures[0]
            if head is not None:
                log.append(f"{where}: {e.name} read {e.votes} as {g} (share {printed_share}, total {total})")
                head.value = g
        return log
    # Every share agrees with the candidates' sum and not with the printed total: the total is misread.
    shares = [e.shares[0] if e.shares else None for e in entries]
    if race.total is not None and all(
        printed_share is not None and abs((e.votes or 0) * 100 / counted - share(printed_share)) <= 0.011
        for e, printed_share in zip(entries, shares, strict=True)
    ):
        log.append(f"{where}: total read {total} as {counted} (every share fits {counted})")
        race.total.value = counted
    return log


def check(race: Race) -> list[str]:
    where = f"{race.day} {race.district}{' ballot ' + race.ballot if race.ballot else ''} (page {race.page + 1})"
    problems = []
    winners = sum(elected(e.name) for e in race.entries)
    if race.ballot or race.year in ALTERNATIVE:
        if winners != 1:
            problems.append(f"{where}: {winners} elected")
    elif race.general and winners != race.magnitude:
        problems.append(f"{where}: {winners} elected of {race.magnitude}")
    elif not race.general and winners < 1:
        problems.append(f"{where}: no one elected")
    if all(e.acclaimed for e in race.entries) or all(not e.figures for e in race.entries):
        return problems
    first = [e.votes for e in race.entries]
    if None in first:
        problems.append(f"{where}: a candidate has no votes")
        return problems
    counted = sum(v for v in first if v is not None)
    total = race.total.value if race.total else None
    if total is None:
        if len(race.entries) > 1:
            problems.append(f"{where}: no total")
    elif counted != total and not race.total_is_ballots:
        problems.append(f"{where}: candidates add up to {counted}, total {total}")
    if race.year in ALTERNATIVE and race.final and race.counts and race.counts > 1:
        final = [e.figures[1].value for e in race.entries if len(e.figures) > 1 and e.figures[1] is not None]
        if sum(v or 0 for v in final) != race.final.value:
            problems.append(f"{where}: final count adds up to {sum(v or 0 for v in final)}, total {race.final.value}")
    return problems


@cache
def read() -> tuple[list[Race], list[str]]:
    """Every race of the two volumes from 1871 to 1990, checked; raises on any race that does not add up."""
    races: list[Race] = []
    for year in sorted(GENERALS):
        races += general_races(year)
    by_pages = pages()[("by", None)]
    races += by_races(VOLUME, by_pages)
    races += by_races(SUPPLEMENT, supplement_pages())
    log: list[str] = []
    problems: list[str] = []
    printed: dict[str, str] = {}
    for race in races:
        if race.general:
            printed.setdefault(folded(race.district), race.district)
        else:
            race.district = printed.get(folded(race.district), race.district)
    for race in races:
        log += settle(race)
        problems += check(race)
    for year, members in MEMBERS.items():
        won = sum(elected(e.name) for r in races if r.general and r.year == year for e in r.entries)
        if won != members:
            problems.append(f"{year}: {won} elected, the summary gives {members} members")
    if problems:
        raise ValueError("races that do not check:\n" + "\n".join(problems))
    return races, log


# Packages.


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, "volumes", kind, *(str(part) for part in parts)])))


SOURCES = {url: key("source", url) for url in TITLES}
SYSTEMS = {
    "fptp": ({"formula": "fptp"}, "Single-member plurality"),
    "block": ({"formula": "block"}, "Block vote in multi-member districts"),
    "irv": ({"formula": "irv"}, "Alternative vote (1952 and 1953)"),
}


@dataclass
class Map:
    vintage: int
    start: date
    end: date
    districts: dict[str, str]  # folded name -> printed name
    seats: int


@cache
def maps() -> list[Map]:
    """The district maps: a new map starts at each general election whose districts or their seats differ from the
    one before."""
    races, _ = read()
    signature: dict[int, dict[str, tuple[str, int]]] = defaultdict(dict)
    ballots: dict[tuple[int, str], int] = Counter()
    for race in races:
        if race.general:
            k = folded(race.district)
            ballots[(race.year, k)] += 1 if race.ballot else 0
            _, seen = signature[race.year].get(k, (race.district, 0))
            signature[race.year][k] = (race.district, max(seen, race.magnitude, ballots[(race.year, k)]))
    found: list[Map] = []
    previous: dict[str, tuple[str, int]] | None = None
    for year in sorted(signature):
        seats = {k: m for k, (_, m) in signature[year].items()}
        if previous is None or seats != {k: m for k, (_, m) in previous.items()}:
            found.append(Map(year, GENERALS[year][0], END, {}, sum(seats.values())))
        found[-1].districts.update({k: name for k, (name, _) in signature[year].items()})
        previous = signature[year]
    for a, b in itertools.pairwise(found):
        a.end = b.start
    return found


def map_on(day: date) -> Map:
    return [m for m in maps() if m.start <= day][-1]


@cache
def areas() -> dict[tuple[int, str], tuple[str, str, date, date]]:
    """Each district of each map: (vintage, folded name) -> (area id, printed name, from, to). A by-election's
    district that the map's general elections did not contest joins that map from the by-election's day."""
    races, _ = read()
    found: dict[tuple[int, str], tuple[str, str, date, date]] = {}
    for m in maps():
        for folded_name, name in m.districts.items():
            found[(m.vintage, folded_name)] = (key("area", m.vintage, folded_name), name, m.start, m.end)
    for race in sorted(races, key=lambda r: r.day):
        m = map_on(race.day)
        k = (m.vintage, folded(race.district))
        if k not in found:
            found[k] = (key("area", m.vintage, k[1]), race.district, race.day, m.end)
    return found


def area_of(race: Race) -> str:
    return areas()[(map_on(race.day).vintage, folded(race.district))][0]


def party_id(abbreviation: str | None) -> str | None:
    if abbreviation is None:
        return None
    name, kind = PARTIES[abbreviation]
    if kind == "held":
        return HELD[name]
    if kind in ("independent", "none"):
        return None
    return key("party", name)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo-1871-1986",
        SOURCE,
        "British Columbia's electoral districts from 1871 to 1986, one area for each district of each map, from the "
        "Electoral History of British Columbia",
    )
    p.external |= {BC, PROVINCE, "ca.bc"}
    for m in maps():
        p.add(
            "boundary_set",
            id=key("boundary_set", m.vintage),
            scheme_code="ca.bc",
            vintage=str(m.vintage),
            valid_from=m.start,
            valid_to=m.end,
            as_of=m.start,
        )
    for (vintage, folded_name), (id, name, start, end) in sorted(areas().items(), key=lambda kv: (kv[0][0], kv[1][1])):
        p.add("area", id=id, scheme_code="ca.bc")
        p.add("area_code", area_id=id, code=f"{vintage}:{slug(name)}", valid_from=start, valid_to=end)
        p.add("area_parent", area_id=id, parent_id=PROVINCE, valid_from=start, valid_to=end)
        p.name(id, name, "en", "official")
    return p


def registry() -> Package:
    races, _ = read()
    p = Package(
        f"{SOURCE}/registry-1871-1990",
        SOURCE,
        "The parties, groupings, and electoral systems of British Columbia's elections from 1871 to 1990, the "
        "assembly's size on each map, and the two Electoral History volumes",
    )
    p.external |= {BC, ASSEMBLY}
    for url, title in TITLES.items():
        p.add(
            "source",
            id=SOURCES[url],
            kind="official_body",
            publisher=PUBLISHER,
            title=title,
            url=url,
            license=PUBLIC_RECORD,
            notes=(
                "Compiled by Elections British Columbia and the Legislative Library from official and unofficial "
                "sources; the volume says it is not an official record."
                if url == VOLUME
                else None
            ),
        )
        p.artifact(raw.cached(SOURCE, url), SOURCES[url])
    used = {e.party for r in races for e in r.entries if e.party}
    made: set[str] = set()
    for abbreviation in sorted(used):
        name, kind = PARTIES[abbreviation]
        if kind not in ("party", "nuance", "bloc") or name in made:
            continue
        made.add(name)
        id = key("party", name)
        p.add("party", id=id, kind=kind, polity_id=BC)
        p.name(id, name, "en", "official")
        labels = sorted(a for a, (n, _) in PARTIES.items() if n == name and a in used)
        for label_text in labels:
            p.add("external_id", entity_id=id, scheme=f"{SOURCE}.party", value=label_text)
    for code, (rules, name) in SYSTEMS.items():
        id = key("electoral_system", code)
        p.add("electoral_system", id=id, polity_id=BC, rules=rules, rules_version=1)
        p.name(id, name, "en", "official")
    for m in maps():
        p.add("body_size", body_id=ASSEMBLY, seats=m.seats, valid_from=m.start, valid_to=m.end)
    return p


def parliaments() -> dict[str, list[Race]]:
    """The races of each package: each general election, and the by-elections of each parliament after it."""
    races, _ = read()
    years = sorted(GENERALS)
    found: dict[str, list[Race]] = defaultdict(list)
    for race in races:
        if race.general:
            found[f"{race.year}-general"].append(race)
        else:
            year = max(y for y in years if GENERALS[y][0] <= race.day)
            found[f"{year}-by-elections"].append(race)
    return dict(sorted(found.items()))


def listed(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return " and ".join(items)
    return f"{', '.join(items[:-1])}, and {items[-1]}"


@cache
def page_size(url: str, number: int) -> tuple[float, float, int, int]:
    rect = document(url)[number].rect
    return rect.width, rect.height, round(rect.width * DPI / 72), round(rect.height * DPI / 72)


def image_key(url: str, number: int) -> str:
    return f"pages/{SOURCE}/{raw.cached(SOURCE, url).sha256}/{number + 1}.png"


def render(url: str, number: int) -> Path:
    out = DATA / image_key(url, number)
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        pixmap = document(url)[number].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
        pixmap.save(out)
    return out


METHOD = (
    "From the Electoral History of British Columbia, 1871-1986 (Elections British Columbia and the Legislative "
    "Library, 1988), read from the text layer of Elections BC's scan and checked district by district: the "
    "candidates add up to the printed total, each printed share matches its votes, and the candidates printed in "
    "capitals number as many as the seats. In districts that elected more than one member each voter had as many "
    "votes as seats (the block vote), so a district's votes exceed its ballots."
)
UNOFFICIAL = (
    " Before 1916 no official returns survive, and the volume takes its figures from newspapers, the Canadian "
    "Parliamentary Companion and Guide, and similar unofficial sources."
)
METHOD_SUPPLEMENT = (
    "From the Electoral History of British Columbia, Supplement, 1987-2001 (Legislative Library, 2002), read from "
    "its text layer and checked against the printed totals and shares."
)
METHOD_ALTERNATIVE = (
    " In 1952 and 1953 the province used the alternative vote: voters ranked the candidates, and a multi-member "
    "district gave each seat its own ballot (A, B, C) with its own candidates. The volume prints each ballot's first "
    "and final counts, stored here as count 1 and the last count; the counts between them are not stored."
)
DEFERRED = (
    " A few remote districts polled after the main polling day; the volume dates the general election by its main "
    "polling day."
)


def election(name: str, group: list[Race]) -> Package:
    general = group[0].general
    _, log = read()
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        (
            f"The {group[0].year} British Columbia general election by electoral district"
            if general
            else f"British Columbia's by-elections after the {name[:4]} general election, by electoral district"
        )
        + ", from the Electoral History of British Columbia",
        depends=[f"{SOURCE}/geo-1871-1986", f"{SOURCE}/registry-1871-1990"],
    )
    p.external |= {BC, ASSEMBLY, "ca.bc"}
    urls = sorted({r.source for r in group})
    for url in urls:
        p.uses(raw.cached(SOURCE, url))
    if any(r.source == VOLUME for r in group):
        p.uses(raw.cached(SOURCE, ERRATA))
    artifacts = {
        url: str(uuid5(NAMESPACE, f"source_artifact\x1f{SOURCES[url]}\x1f{raw.cached(SOURCE, url).storage_key}"))
        for url in urls
    }
    for race in group:
        for e in race.entries:
            if (held := party_id(e.party)) is not None and e.party and PARTIES[e.party][1] == "held":
                p.external.add(held)
    page_ids: dict[tuple[str, int], str] = {}

    def locate(figure: Figure | None, url: str, **fact: object) -> int:
        if figure is None or figure.box == (0.0, 0.0, 0.0, 0.0):
            return 0
        k = (url, figure.page)
        if k not in page_ids:
            page_ids[k] = key("page", name, url, figure.page)
            width, height, pixels_x, pixels_y = page_size(url, figure.page)
            p.add(
                "source_page",
                id=page_ids[k],
                artifact_id=artifacts[url],
                page_number=figure.page + 1,
                image_key=image_key(url, figure.page),
                width=pixels_x,
                height=pixels_y,
            )
        width, height, _, _ = page_size(url, figure.page)
        x0, y0, x1, y1 = figure.box
        p.add(
            "value_locator",
            **fact,
            channel=0,
            page_id=page_ids[k],
            source_id=SOURCES[url],
            x0=max(0.0, x0 / width),
            y0=max(0.0, y0 / height),
            x1=min(1.0, x1 / width),
            y1=min(1.0, y1 / height),
        )
        return DIGITIZED

    days = sorted({r.day for r in group})
    elections: dict[date, str] = {}
    for day in days:
        here = [r for r in group if r.day == day]
        id = elections[day] = key("election", "general" if general else "by-election", day)
        last = GENERALS[day.year][1] if general else day
        p.add(
            "election",
            id=id,
            polity_id=BC,
            kind="general" if general else "by_election",
            date_from=day,
            date_to=last,
            status="held",
        )
        spoken = f"{day:%B} {day.day}, {day.year}"
        districts = list(dict.fromkeys(r.district for r in here))
        title = (
            f"{day.year} British Columbia general election"
            if general
            else f"{listed(districts)} provincial by-election{'s' if len(districts) > 1 else ''}, {spoken}"
        )
        p.name(id, title, "en", "official")
        p.add("slug", path=f"ca/bc/{day}-{'general' if general else 'by-election'}", entity_id=id, is_current=True)
        method = METHOD if here[0].source == VOLUME else METHOD_SUPPLEMENT
        if day.year < 1916:
            method += UNOFFICIAL
        if general and day.year in ALTERNATIVE:
            method += METHOD_ALTERNATIVE
        if general and day.year <= 1924:
            method += DEFERRED
        uncertain = [f"{display(e.name)} ({r.district})" for r in here for e in r.entries if e.uncertain]
        if uncertain:
            method += (
                " The volume prints the affiliation of "
                + listed(uncertain)
                + " in brackets or with a question mark, as uncertain; the label it prints is kept."
            )
        repaired = [line.split(": ", 1)[1] for line in log if line.startswith(f"{day} ")]
        if repaired:
            method += (
                " Where the text layer misreads a figure, the printed share and total give it: "
                + "; ".join(repaired)
                + "."
            )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    values: list[dict[str, object]] = []
    units: dict[tuple[date, str], str] = {}
    for race in group:
        day = race.day
        url = race.source
        kind = "general" if general else "by-election"
        area = area_of(race)
        vintage = map_on(day).vintage
        tag = f"{day}:{folded(race.district)}{':' + race.ballot if race.ballot else ''}"
        alternative = day.year in ALTERNATIVE and general
        magnitude = (
            1
            if (race.ballot or alternative)
            else (race.magnitude if general else max(1, sum(elected(e.name) for e in race.entries)))
        )
        system = "irv" if alternative else "block" if magnitude > 1 else "fptp"
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=elections[day],
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=area,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=key("electoral_system", system),
            default_result_kind="tacit" if all(e.acclaimed for e in race.entries) else "counted",
        )
        ballot = f", ballot {race.ballot}" if race.ballot else ""
        p.name(
            contest,
            f"{day.year} {'election' if general else 'by-election'} in {race.district}{ballot}",
            "en",
            "official",
        )
        p.add(
            "slug",
            path=f"ca/bc/{day}-{kind}/{slug(race.district)}{'-ballot-' + race.ballot.lower() if race.ballot else ''}",
            entity_id=contest,
            is_current=True,
        )
        unit_key = (day, folded(race.district))
        if unit_key not in units:
            unit = units[unit_key] = key("unit", day, vintage, folded(race.district))
            last = GENERALS[day.year][1] if general else day
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BC,
                scheme_code="ca.bc",
                code=f"{day}:{vintage}:{slug(race.district)}",
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=day,
                valid_to=last + timedelta(days=1),
            )
        unit = units[unit_key]
        acclaimed = all(e.acclaimed for e in race.entries)
        unknown = all(not e.figures for e in race.entries) and not acclaimed
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[day],
            contest_id=contest,
            stage="official",
            kind="tacit" if acclaimed else "counted",
            source_id=SOURCES[url],
            status="active",
            is_default=True,
            completeness=None if acclaimed or unknown else 1.0,
        )
        if unknown:
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="caveat",
                body_md="The volume gives the candidates and the member elected but no votes for this district.",
                lang="en",
            )
        for position, e in enumerate(race.entries, 1):
            person_tag = f"{tag}:{e.name}"
            person = key("person", person_tag)
            shown = display(e.name)
            p.add("person", id=person)
            p.name(person, shown, "en", "official")
            party = party_id(e.party)
            independent = e.party is not None and PARTIES[e.party][1] == "independent"
            option = key("option", person_tag)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=shown,
            )
            candidacy = key("candidacy", person_tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=shown,
                member_of_party_id=party,
                is_independent=independent,
            )
            if elected(e.name):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            if acclaimed or unknown:
                continue
            rounds: list[tuple[int, Figure | None]]
            if alternative:
                rounds = [(1, e.figures[0] if e.figures else None)]
                if race.counts and race.counts > 1 and len(e.figures) > 1:
                    rounds.append((race.counts, e.figures[1]))
            else:
                rounds = [(0, e.figures[0] if e.figures else None)]
            for count_round, figure in rounds:
                if figure is None or figure.value is None:
                    continue
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "measure": 1,
                    "count_round": count_round,
                }
                flags = locate(figure, url, **fact)
                values.append({**fact, "channel": 0, "flags": flags, "value": figure.value})
        if acclaimed or unknown:
            continue
        accounting_round = 1 if alternative else 0
        counted = sum(e.votes or 0 for e in race.entries)
        if magnitude == 1:
            valid = race.total if race.total and not race.total_is_ballots else None
            if valid is not None:
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": None,
                    "measure": 4,
                    "count_round": accounting_round,
                }
                flags = locate(valid, url, **fact)
                values.append({**fact, "channel": 0, "flags": flags, "value": counted})
            if race.total_is_ballots and race.total:
                fact = {"result_set_id": result_set, "unit_id": unit, "option_id": None, "measure": 3, "count_round": 0}
                flags = locate(race.total, url, **fact)
                values.append({**fact, "channel": 0, "flags": flags, "value": race.total.value})
        if race.rejected is not None and race.rejected.value is not None:
            fact = {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": None,
                "measure": 5,
                "count_round": accounting_round,
            }
            flags = locate(race.rejected, url, **fact)
            values.append({**fact, "channel": 0, "flags": flags, "value": race.rejected.value})
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "channel": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ),
    )
    for url, number in sorted(page_ids):
        render(url, number)
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo-1871-1986", f"{SOURCE}/registry-1871-1990", *(f"{SOURCE}/{n}" for n in parliaments())]


def fetch() -> None:
    for url in TITLES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    _, log = read()
    for line in log:
        print(line)
    geo().write()
    registry().write()
    for name, group in parliaments().items():
        election(name, group).write()
