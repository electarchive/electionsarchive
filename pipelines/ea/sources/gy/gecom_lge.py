"""Guyana's local government elections of 2016, 2018, and 2023, from the Elections Commission's Form 31 declarations,
one document per local authority area: list votes, rejected ballots, seats, and candidate votes for each constituency.

The 2016 declarations are scans whose figures are typed or handwritten, and many 2018 declarations have no usable
text layer, so both years are read from the page images. For 2016 two independent readings were reconciled figure by
figure; for 2018 each reading was checked against the numbers the text layer prints, where it has one. The 2023
declarations are parsed from their text layer where they have one; the 2023 declarations that are scans only, many of
them handwritten, are read from the page images, with each typed figure placed by OCR on the page and each handwritten
one boxed by eye and tightened to its ink. The readings, each figure with its page and box, are kept in
`gecom_lge_forms/{year}.json`, and the parse checks each declaration's arithmetic again."""

import difflib
import json
import re
import subprocess
from collections.abc import Iterator
from datetime import date
from functools import cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "gy.gecom_lge"
SITE = "https://gecom.org.gy/public"
PAGES = {
    "all": "https://www.gecom.org.gy/public/home/results",
    "2018": f"{SITE}/home/results/lge2018",
    "2023": f"{SITE}/home/resource_hub/lge2023",
}
FORM31 = f"{SITE}/assets/docs/lge2023/form31s/"
FORMS = Path(__file__).with_name("gecom_lge_forms")
DPI = 150  # the resolution at which the transcribed boxes were read


def tree(page: str) -> list[tuple[str, str]]:
    """The folder path and URL of every file the results page lists in its file tree."""
    html = raw.cached(SOURCE, PAGES[page]).path.read_text(errors="replace")
    found = re.search(r"var passedArray = (\[.*?\]);\s*\n", html, re.DOTALL)
    if not found:
        raise ValueError(f"{page} lists no files")
    files: list[tuple[str, str]] = []

    def walk(nodes: list[dict[str, object]], path: str) -> None:
        for node in nodes:
            children = node.get("nodes")
            if isinstance(children, list):
                walk(children, f"{path}/{node['text']}")
            elif isinstance(node.get("href"), str):
                files.append((f"{path}/{node['text']}".strip("/"), str(node["href"])))

    walk(json.loads(found.group(1)), "")
    return files


@cache
def files(day: str) -> list[tuple[str, str]]:
    """The folder-and-name and URL of each declaration of one election day."""
    if day == "2016":
        # The tree also keeps superseded copies (named _OLD or "incorrect"); only the current declaration counts.
        prefix = "2016 - Local Government Elections/"
        return [
            (path.removeprefix(prefix), url)
            for path, url in tree("all")
            if path.startswith(prefix) and path.endswith(".pdf") and "incorrect" not in path
        ]
    if day == "2018":
        return tree("2018")
    page = raw.cached(SOURCE, PAGES["2023"]).path.read_text(errors="replace")
    names = sorted(set(re.findall(r"form31s/(\d+\.\d+\.pdf)", page)), key=lambda n: [int(x) for x in n[:-4].split(".")])
    return [(unquote(n), f"{FORM31}{n}") for n in names]


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    count = 0
    for day in DAYS:
        for _, url in files(day):
            raw.fetch(SOURCE, url, suffix=".pdf")
            count += 1
    print(f"{count} files")


NUMBER = re.compile(r"(\d[\d,]*)\s*$")
UNOPPOSED = re.compile(r"unopposed|no contest|uncontested", re.IGNORECASE)
HEADER = re.compile(r"^\s*constituen\w*\s*(?:no\.?|number|#)?\s*(\d+)\b[\s:.\-–]*(.*)$", re.IGNORECASE)
NOISE = re.compile(r"^(page \d+ of\s*\d+|created in photo|returning officer|signature|dated?\b)", re.IGNORECASE)


class Candidate(NamedTuple):
    name: str
    party: str | None
    votes: int | None  # None for a candidate returned unopposed


class Form(NamedTuple):
    lists: list[tuple[str, int]]
    rejected: int | None
    seats: dict[str, int]
    constituencies: dict[int, list[Candidate]]
    elected: dict[int, str]
    valid: int | None
    labels: dict[int, str]


COLUMNED = re.compile(r"^(.*?\S)\s{2,}([A-Za-z][A-Za-z./]{1,9})\s{2,}(\S.*)$")


BROKEN_HEADER = re.compile(
    r"^\s*C\s?O\s?N?\s?S\s?T\s?I\s?T\s?U\s?E[\s,]*N?\s?C\s?Y\s*#?\s*([0-9IlO|]+)(?:\s([0-9IlO|]))?(?=\s|$)(.*)$"
)


def numeral(text: str) -> str:
    """Digits as the text layer misreads them in a constituency number: I, l, and | for 1, and O for 0."""
    return text.translate(str.maketrans("IlO|", "1101"))


def clean(line: str) -> str:
    header = BROKEN_HEADER.match(line)
    if header:
        first, second, rest = header.group(1), header.group(2), header.group(3)
        if second and len(first) == 1:
            first += second  # "#I 0" is constituency 10
        elif second:
            rest = f" {second}{rest}"
        line = f"CONSTITUENCY #{numeral(first)} {rest.strip()}"
    columned = COLUMNED.match(line)
    if columned and not re.search(r"\d", columned.group(2)) and not UNOPPOSED.search(line[: columned.start(3)]):
        line = f"{columned.group(1)} ({columned.group(2)}) {columned.group(3)}"  # name, party, and votes in columns
    line = line.replace("…", " ").replace("\u2011", "-")
    line = re.sub(r"\.{2,}", " ", line)
    line = re.sub(r"[ \t]+", " ", line)
    return line.strip()


def lines_of(text: str) -> list[str]:
    return [line for line in (clean(raw_line) for raw_line in text.splitlines()) if line and not NOISE.match(line)]


def tail(line: str) -> tuple[str, int | None]:
    """A line's text and the number that closes it, if there is one."""
    line = re.sub(r"(\d)\s*\(\s*[A-Za-z]{3,8}\s*\)\s*$", r"\1", line)
    line = re.sub(r"[\s:\-–]+$", "", line) if re.search(r"\d[\s:\-–]*$", line) else line
    found = NUMBER.search(line)
    if not found:
        return line.strip(" :-–"), None
    return line[: found.start()].strip(" :-–"), int(found.group(1).replace(",", ""))


WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
PARTY_WORDS = re.compile(
    r"party|alliance|partnership|group|movement|association|council|united|congress|apnu|ppp", re.IGNORECASE
)


# Labels that the declarations print in several forms, or that the text layer misreads, for one party.
ALIASES = {
    "UNITEDREPUBLICANPARTY": "URP",
    "UNITEDREPUBLICANPARTY-URP": "URP",
    "UNITEDREPUBLICANPARTY/URP": "URP",
    "GNSPARTY": "AGNSP",
    "CP": "ICP",
    "LCP": "ICP",
    "RCP": "ICP",
    "APU": "APNU",
    "TOEVLUGT/PATENTIAACTIONGROUP": "TPAG",
}


def party_key(label: str) -> str:
    """A short key for a party label, from its abbreviation or its words, so that spelling variants meet."""
    upper = label.upper()
    bracketed = re.search(r"\(([^()]+)\)\s*$", upper)
    if bracketed and bracketed.start() > 0:
        return party_key(bracketed.group(1))  # the printed abbreviation decides, not words in the name
    if re.search(r"AFC|ALLIANCE FOR CHANGE", upper) and re.search(r"APNU|PARTNERSHIP", upper):
        return "APNU+AFC"
    if re.search(r"APNU|A\.P\.N\.U|PARTNERSHIP|PARTNERS|PARTHNER|PARETNER|PARTERS", upper):
        return "APNU"
    if re.search(r"PPP|P\.P\.P|PROGRESSIVE", upper):
        return "PPP/C"
    if re.search(r"\bAFC\b|ALLIANCE FOR CHANGE", upper):
        return "AFC"
    found = re.findall(r"\(([^)]+)\)", upper)
    key = re.sub(r"[^A-Z/+]", "", found[-1] if found else upper)
    return ALIASES.get(key, key)


def tokens(name: str) -> list[str]:
    return sorted(re.findall(r"[A-Z]{2,}", re.sub(r"\bMC\s+", "MC", re.sub(r"\([^)]*\)", " ", name.upper()))))


LEADING_PARTY = re.compile(
    r"^(?:A\.?\s?P\.?\s?N\.?\s?U\.?|P\.?\s?P\.?\s?P\.?\s?/?\s?C?\.?|A\.?\s?F\.?\s?C\.?)\s+", re.IGNORECASE
)


def same_person(a: str, b: str) -> bool:
    """Whether two printed names are one person's, ignoring order, punctuation, titles, and a leading party."""
    a, b = (LEADING_PARTY.sub("", split_candidate(LEADING_PARTY.sub("", n))[0]) for n in (a, b))
    x, y = tokens(a), tokens(b)
    if not x or not y:
        return False
    if set(x) <= set(y) or set(y) <= set(x):
        return True
    if sorted("".join(x)) == sorted("".join(y)):
        return True  # the same letters, where a space was lost or added between two names
    if len(x) > 1 and len(y) > 1:
        # A letter or two misread, with the names in the same order.
        first, second = (re.sub(r"[^A-Z]", "", re.sub(r"\([^)]*\)", " ", n.upper())) for n in (a, b))
        if difflib.SequenceMatcher(None, first, second).ratio() >= 0.9:
            return True
    return difflib.SequenceMatcher(None, " ".join(x), " ".join(y)).ratio() >= 0.85


def split_candidate(text: str) -> tuple[str, str | None]:
    """A candidate's name and party label from a line that gives one or both."""
    text = text.strip(" :-–")
    leading = re.match(r"\(?(APNU|PPP/C|AFC)\)?(?:\s*[-–]\s*|\s+)(\S.*)$", text)
    if leading:
        return leading.group(2).strip(), leading.group(1)  # the party is printed before the name
    trailing = re.match(r"(.*\S)\s*[-–]\s*(APNU|PPP/C|AFC)$", text)
    if trailing:
        return trailing.group(1).strip(), trailing.group(2)
    found = re.search(r"\(([^)]*)\)\s*$", text)
    if found and not re.fullmatch(r"\d+", found.group(1)):
        label = found.group(1).strip()
        return text[: found.start()].strip(" :-–"), "INDEPENDENT" if re.fullmatch(
            r"independent|individual", label, re.IGNORECASE
        ) else label
    tied = re.fullmatch(
        r"(.+?)\s*[-–]\s*([^-–]*(?:party|group|council|alliance|association)[^-–]*)", text, re.IGNORECASE
    )
    if tied and not PARTY_WORDS.search(tied.group(1)):
        return tied.group(1).strip(), tied.group(2).strip()
    for dash in (" – ", " - ", "–"):
        if dash in text:
            left, right = (part.strip() for part in text.split(dash, 1))
            if re.search(r"independent|individual", right, re.IGNORECASE):
                return left, "INDEPENDENT"
            if re.search(r"independent|individual", left, re.IGNORECASE):
                return right, "INDEPENDENT"
            if PARTY_WORDS.search(left) and not PARTY_WORDS.search(right):
                return right, left
    return text, None


def parse_form(text: str) -> Form:
    lines = lines_of(text)

    def find(pattern: str, start: int = 0) -> int | None:
        return next((i for i in range(start, len(lines)) if re.search(pattern, lines[i], re.IGNORECASE)), None)

    first_header = next((i for i, line in enumerate(lines) if HEADER.match(line)), len(lines))
    listing = find(r"votes\s+cast\s+for\s+each\s+list")
    rejected_at = find(r"rejected\s+ballot", listing or 0)
    seats_at = find(r"seats\s+allocated", listing or 0)
    after = find(r"names?\s+of\s+(the\s+)?persons?|following is a notification|constituen", (seats_at or 0) + 1)
    pairs: list[tuple[str, int | None]] = []
    valid: int | None = None
    if listing is not None and rejected_at is not None:
        body = lines[listing + 1 : rejected_at]
        index = 0
        while index < len(body):
            name, number = tail(body[index])
            if number is None and UNOPPOSED.search(name):
                name, number = UNOPPOSED.sub("", name).strip(" :-–"), 0  # a single list that took its seats unopposed
            if number is None and index + 1 < len(body) and re.fullmatch(r"\d[\d,]*", body[index + 1]):
                number = int(body[index + 1].replace(",", ""))
                index += 1
            index += 1
            if re.match(r"total", name, re.IGNORECASE):
                valid = number
            elif name and not re.search(r"candidates\s*:?$", name, re.IGNORECASE):
                if number is None and pairs and pairs[-1][1] is not None and not pairs[-1][0].endswith(")"):
                    pairs[-1] = (f"{pairs[-1][0]} {name}", pairs[-1][1])  # a label wrapped onto a second line
                else:
                    pairs.append((name, number))
    lists = [(name, number) for name, number in pairs if number is not None]
    rejected = None
    if rejected_at is not None:
        for line in (*lines[rejected_at : rejected_at + 2], lines[rejected_at - 1]):
            number = tail(line)[1]
            if number is not None:
                rejected = number
                break
    seats: dict[str, int] = {}
    if seats_at is not None:
        end = after if after is not None else len(lines)
        seat_rows: list[tuple[str, int]] = []
        for line in lines[seats_at + 1 : end]:
            name, number = tail(re.sub(r"\s+seats?$", "", line, flags=re.IGNORECASE))
            if number is None:
                last = name.split()[-1].lower() if name.split() else ""
                if last in WORDS:
                    name, number = " ".join(name.split()[:-1]), WORDS[last]
            if number is not None and name:
                seat_rows.append((name, number))
            elif name and seat_rows and not re.search(r"names?\s+of|nil\b", name, re.IGNORECASE):
                seat_rows[-1] = (f"{seat_rows[-1][0]} {name}", seat_rows[-1][1])  # a label wrapped onto a second line
        seats = {party_key(name): number for name, number in seat_rows}
    if seats_at is not None and len(seats) < len(lists):
        # Some declarations name the councillors under each list instead of printing a seat count.
        found = {party_key(name) for name, _ in lists}
        end = find(r"following is a notification|constituen\w*\s*(no\.?\s*)?\d", seats_at + 1) or len(lines)
        heading: str | None = None
        counted: dict[str, int] = {}
        for line in lines[seats_at + 1 : end]:
            label, number = tail(line)
            key = party_key(label) if label else ""
            if key in found:
                heading = key
                counted.setdefault(key, 0)
                label = ""
            elif re.match(r"names?\s+of|nil\b|-$", label, re.IGNORECASE) or heading is None:
                continue
            if label and re.search(r"[A-Za-z]{2}", label) and not re.match(r"names?\s+of", label, re.IGNORECASE):
                counted[heading or ""] += 1
        for key, count in counted.items():
            seats.setdefault(key, count)
    # Constituency blocks run from the first header to the restart of the numbering, where the elected councillors begin.
    marker = None
    for i in range(first_header, len(lines)):
        if re.search(r"names?\s+of\s+(the\s+)?persons?", lines[i], re.IGNORECASE) and i > first_header:
            marker = i
            break
    blocks: dict[int, list[Candidate]] = {}
    elected: dict[int, str] = {}
    current: int | None = None
    previous = 0
    mode = "votes"
    raw_blocks: dict[int, list[str]] = {}
    unnamed: list[int] = []
    standalone: list[str] = []
    labels: dict[int, str] = {}
    for i in range(first_header, len(lines)):
        line = lines[i]
        if marker is not None and i == marker:
            mode, current = "elected", None
            continue
        found = HEADER.match(line)
        if found:
            number = int(found.group(1))
            if mode == "votes" and marker is None and number <= previous:
                mode = "elected"
            if mode == "votes":
                previous = number
                current = number
                raw_blocks.setdefault(number, [])
                rest = found.group(2).strip()
                if rest and tail(rest)[1] is not None:
                    raw_blocks[number].append(rest)
                elif rest:
                    labels[number] = rest
            else:
                rest = found.group(2).strip(" :-–")
                if rest:
                    elected[number] = rest
                else:
                    unnamed.append(number)
            continue
        if mode == "elected":
            # A name on a line of its own sits above or below its header, so the names pair with the headers in order.
            if re.search(r"[A-Za-z]{2}", line) and not re.match(
                r"names?\s+of|(the )?constituency elections", line, re.IGNORECASE
            ):
                if standalone and standalone[-1].count("(") > standalone[-1].count(")"):
                    standalone[-1] += " " + line  # a name wrapped onto a second line
                else:
                    standalone.append(line)
            continue
        if mode == "votes" and current is not None:
            raw_blocks[current].append(line)
    if len(standalone) >= len(unnamed):  # any further line is the Returning Officer's signature
        elected.update(zip(unnamed, standalone, strict=False))
    for number, entries in raw_blocks.items():
        candidates: list[Candidate] = []
        index = 0
        while index < len(entries):
            name, votes = tail(entries[index])
            if votes is None and index + 1 < len(entries) and re.fullmatch(r"\d[\d,]*", entries[index + 1]):
                votes = int(entries[index + 1].replace(",", ""))
                index += 1
            index += 1
            unreadable = votes is not None and re.search(r"\)\s*\S*\d", name) is not None
            if unreadable:
                name, votes = name[: name.rindex(")") + 1], None  # a figure with a letter among its digits
            opposed = UNOPPOSED.search(name) is not None
            name = re.sub(r"\b(unopposed|no contest|uncontested)\b( list)?", "", name, flags=re.IGNORECASE).strip(
                " :-–"
            )
            if not re.search(r"[A-Za-z]{2}", name) or re.match(
                r"(no\.? of|names? of|the following)", name, re.IGNORECASE
            ):
                continue
            printed = re.match(r"\(([^()]+)\)\s+(\S.*)$", name)
            if printed and candidates and candidates[-1].party is None:
                # The columns shift: a candidate's party is printed ahead of the next candidate's name.
                candidates[-1] = candidates[-1]._replace(party=printed.group(1).strip())
                name = printed.group(2)
            whole = re.fullmatch(r"(?:\S+\s+){2,}\S*\([^()]+\)", name) is not None
            if (
                votes is None
                and not opposed
                and not unreadable
                and not (whole and candidates and candidates[-1].party)
                and candidates
                and candidates[-1].votes is not None
            ):
                last = candidates[-1]  # a party or group name wrapped onto a second line
                who, label = split_candidate(f"{last.name} {name}" if last.party is None else name)
                candidates[-1] = Candidate(who, label or last.party, last.votes)
                continue
            who, label = split_candidate(name)
            if re.fullmatch(r"nil|none|-|APNU|PPP/C|AFC", who, re.IGNORECASE):
                continue  # the party fielded no candidate here
            candidates.append(Candidate(who, label, None if opposed else votes))
        blocks[number] = candidates
    return Form(lists, rejected, seats, blocks, elected, valid, labels)


def hare(votes: dict[str, int], seats: int) -> dict[str, int]:
    """Seats by the largest remainder method with a Hare quota."""
    total = sum(votes.values())
    if not total or not seats:
        return dict.fromkeys(votes, 0)
    quota = total / seats
    won = {key: int(v // quota) for key, v in votes.items()}
    spare = seats - sum(won.values())
    for key in sorted(votes, key=lambda k: (-(votes[k] - won[k] * quota), k))[:spare]:
        won[key] += 1
    return won


def uncontested(form: Form) -> bool:
    """Whether one list stood and took every list seat without a poll."""
    return len(form.lists) == 1 and form.lists[0][1] == 0


def problems(form: Form) -> tuple[list[str], list[int]]:
    """What stops the list contest from loading, and which constituencies cannot be loaded."""
    bad: list[str] = []
    keys = [party_key(name) for name, _ in form.lists]
    if not form.lists:
        bad.append("no list votes")
    elif len(set(keys)) != len(keys):
        bad.append("two lists share a key")
    elif form.rejected is None and not uncontested(form):
        bad.append("no rejected ballots")
    elif not form.seats or not set(form.seats) <= set(keys):
        bad.append(f"seats {sorted(form.seats)} do not match lists {keys}")
    else:
        votes = {party_key(name): v for name, v in form.lists}
        total = sum(form.seats.values())
        got = hare(votes, total)
        if uncontested(form):
            if form.seats != {keys[0]: total}:
                bad.append(f"declared seats {form.seats} for the one unopposed list {keys[0]}")
        elif {k: form.seats.get(k, 0) for k in votes} != got:
            bad.append(f"declared seats {form.seats} differ from the Hare allocation {got}")
        if form.valid is not None and form.valid != sum(votes.values()):
            bad.append(f"valid votes {form.valid} differ from the list sum {sum(votes.values())}")
    out: list[int] = []
    for number, candidates in form.constituencies.items():
        elected = form.elected.get(number)
        if elected is None or not candidates:
            out.append(number)
        elif len(candidates) == 1:
            if not same_person(candidates[0].name, elected):
                out.append(number)
        elif any(c.votes is None for c in candidates):
            out.append(number)
        else:
            top = max(c.votes or 0 for c in candidates)
            leaders = [c for c in candidates if c.votes == top]
            if not any(same_person(c.name, elected) for c in leaders) or (len(leaders) > 1 and elected is None):
                out.append(number)
    if sorted(form.constituencies) != list(range(1, len(form.constituencies) + 1)):
        out = sorted(form.constituencies)
    if set(form.constituencies) != set(form.elected):
        out = sorted(form.constituencies)
    if not bad and not out and form.constituencies and sum(form.seats.values()) != len(form.constituencies):
        # The Act gives a council as many list seats as constituencies, so the two counts check each other.
        bad.append(f"{sum(form.seats.values())} list seats for {len(form.constituencies)} constituencies")
    return bad, out


COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fGY"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DAYS = {"2016": date(2016, 3, 18), "2018": date(2018, 11, 12), "2023": date(2023, 6, 12)}
TRANSCRIBED = {"2016", "2018", "2023"}
PARTLY = {"2023"}  # days whose declarations with a text layer are parsed from it, and only the scans transcribed
MUNICIPALITIES = {
    "mabaruma",
    "anna-regina",
    "georgetown",
    "new-amsterdam",
    "rose-hall",
    "corriverton",
    "bartica",
    "mahdia",
    "lethem",
    "linden",
}
# The 2023 declarations are numbered by region and area; each number names the same area as the 2018 file of that name.
AREAS_2023 = {
    "1.01": "Port Kaituma",
    "1.02": "Mabaruma",
    "2.01": "Moruka Providence",
    "2.02": "Nile Cozier",
    "2.03": "Charity",
    "2.04": "Evergreen Paradise",
    "2.05": "Anna Regina",
    "2.08": "Good Hope Pomona",
    "3.01": "Wakenaam",
    "3.02": "Leguan",
    "3.04": "Hydronie Good Hope",
    "3.05": "Greenwich Park Vergenoegen",
    "3.06": "Tuschen Uitvlugt",
    "3.07": "Stewartville Cornelia Ida",
    "3.08": "Canals Polder",
    "3.09": "La Grange Nismes",
    "3.10": "Toevlugt Patentia",
    "3.11": "Hague Blakenburg",
    "4.01": "Haurarunui Yarowkabra",
    "4.03": "Soesdyke Huist Coverden",
    "4.06": "Mocha Arcadia",
    "4.07": "Golden Grove Diamond Place",
    "4.08": "Eccles Ramsburg",
    "4.09": "Georgetown",
    "4.11": "Better Hope LBI",
    "4.12": "Beterverwagting Triumph",
    "4.15": "Enmore Hope",
    "4.17": "Unity Vereeniging",
    "4.18": "Cane Grove",
    "5.04": "Profit Rising Sun",
    "5.05": "Seafield Tempe",
    "5.07": "Bath Woodley Park",
    "5.08": "Woodlands Bel Air",
    "5.10": "Blairmont Gelderland",
    "6.03": "Enfield New Doe Park",
    "6.05": "Kintyre Borlam",
    "6.09": "Kilcoy Hampshire",
    "6.10": "Rose Hall",
    "6.12": "Bloomfield Whim",
    "6.14": "Adventure Bushlot",
    "6.15": "Maida Tarlogie",
    "6.17": "Black Bush Polder",
    "6.19": "No 52 No 63",
    "6.21": "Crabwood Creek Moleson Creek",
    "10.02": "Kwakwani",
}
# The file names spell some areas differently from year to year; each variant maps to one slug.
SAME = {
    "adventure-no-28-or-bush-lot": "adventure-bush-lot",
    "adventure-bushlot": "adventure-bush-lot",
    "annadale-riverstown": "annandale-riverstown",
    "better-hope-lbi": "better-hope-la-bonne-intention",
    "blairmont-no-3-gelderland": "blairmont-gelderland",
    "buxton-fouls": "buxton-foulis",
    "cane-field-enterprise": "canefield-enterprise",
    "charity": "charity-urasara",
    "crabwood-creek-moleson": "crabwood-creek-moleson-creek",
    "ramsburg-eccles": "eccles-ramsburg",
    "gilbraltar-fyrish": "gibraltar-fyrish",
    "diamond-place-golden-grove": "golden-grove-diamond-place",
    "caledonia-good-success": "good-success-caledonia",
    "hague-blakenburg": "hague-blankenburg",
    "haurarunui-yarowkabra": "hauraruni-yarowkabra",
    "kintyre-no-37-borlam": "kintyre-borlam",
    "la-jalousie-novelle-flanders": "la-jalousie-nouvelle-flanders",
    "mon-repos-la-reconnisance": "mon-repos-la-reconnaissance",
    "ordnance-fortlands-no-28": "ordnance-fortlands",
    "ordnance-fort-lands-no-38": "ordnance-fortlands",
    "port-kaituma-matthews-ridge-arakaka": "port-kaituma",
    "port-mourant-john": "port-mourant-johns",
    "seafield-tempe": "seafield-tempie",
    "the-best-klien-pouderoyen": "the-best-klein-pouderoyen",
}
# The name shown for an area whose file names misspell or shorten it.
NAMES = {
    "adventure-bush-lot": "Adventure Bush Lot",
    "annandale-riverstown": "Annandale Riverstown",
    "better-hope-la-bonne-intention": "Better Hope La Bonne Intention",
    "blairmont-gelderland": "Blairmont Gelderland",
    "buxton-foulis": "Buxton Foulis",
    "canefield-enterprise": "Canefield Enterprise",
    "charity-urasara": "Charity Urasara",
    "crabwood-creek-moleson-creek": "Crabwood Creek Moleson Creek",
    "eccles-ramsburg": "Eccles Ramsburg",
    "gibraltar-fyrish": "Gibraltar Fyrish",
    "golden-grove-diamond-place": "Golden Grove Diamond Place",
    "good-success-caledonia": "Good Success Caledonia",
    "hague-blankenburg": "Hague Blankenburg",
    "hauraruni-yarowkabra": "Hauraruni Yarowkabra",
    "kintyre-borlam": "Kintyre Borlam",
    "la-jalousie-nouvelle-flanders": "La Jalousie Nouvelle Flanders",
    "mon-repos-la-reconnaissance": "Mon Repos La Reconnaissance",
    "ordnance-fortlands": "Ordnance Fortlands",
    "port-kaituma": "Port Kaituma Matthews Ridge Arakaka",
    "port-mourant-johns": "Port Mourant Johns",
    "seafield-tempie": "Seafield Tempie",
    "the-best-klein-pouderoyen": "The Best Klein Pouderoyen",
    "no-52-no-74": "No. 52 No. 74",
    "no-52-no-63": "No. 52 No. 63",
    "no-64-no-74": "No. 64 No. 74",
    "good-hope-no-51": "Good Hope No. 51",
}
BLOCS = {"APNU", "APNU+AFC"}
FULL = {
    "APNU": "A Partnership for National Unity",
    "PPP/C": "People's Progressive Party/Civic",
    "AFC": "Alliance for Change",
    "APNU+AFC": "A Partnership for National Unity + Alliance for Change",
}
METHOD = (
    "Declared by the Returning Officer on Form 31 and published by the Guyana Elections Commission. Local authority "
    "areas elect half their councillors from party and group lists over the whole area, allocated by the largest "
    "remainder method, and half in single-member constituencies."
)
READ = " The figures were read from the scanned declaration; each one links to its place on the page."


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def bare(stem: str) -> str:
    stem = re.sub(r"\s+", " ", stem.replace("_", " ")).strip()
    return re.sub(r"^(the\s+)?(municipality|town) of\s+", "", stem, flags=re.IGNORECASE)


def slug_of(stem: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", bare(stem).lower()).strip("-")
    return SAME.get(slug, slug)


def display(stem: str) -> str:
    slug = slug_of(stem)
    if slug in NAMES:
        return NAMES[slug]
    name = bare(stem)
    return name.title() if name.isupper() else name


Box = tuple[int, tuple[float, float, float, float]]  # a page number and a box in fractions of the page


class Area(NamedTuple):
    day: str
    slug: str
    name: str
    url: str
    form: Form
    list_ok: bool
    ok: list[int]  # constituencies that load
    skipped: list[int]
    reasons: list[str]
    winners: dict[int, int]  # the elected candidate's index in each constituency that loads
    boxes: dict[tuple[object, ...], Box]  # where each transcribed figure is printed
    caveats: list[str]  # published with the list contest
    uncontested: bool = False  # a single list took the list seats without a poll
    read: bool = False  # the figures were read from the page images


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def loosely(a: str, b: str) -> bool:
    """Whether two spellings of a name share a word, as the vote and elected sections of one form do."""
    words = [{w for w in re.findall(r"[a-z]{3,}", n.lower()) if w not in ("mr", "mrs", "miss")} for n in (a, b)]
    return bool(words[0] & words[1])


def winner(candidates: list[Candidate], elected: str | None, side: str | None = None) -> int | None:
    """The index of the elected candidate: the one the declaration names, else the one with the most votes. Where the
    elected section spells the name differently, or the votes are printed by party only, the candidate with the most
    votes counts as elected if the name shares a word with it or the party agrees."""

    def agrees(c: Candidate) -> bool:
        if elected is None:
            return True
        if c.name:
            return same_person(c.name, elected) or loosely(c.name, elected)
        return side is None or (c.party is not None and party_key(c.party) == party_key(side))

    if len(candidates) == 1:
        return 0 if agrees(candidates[0]) else None
    if any(c.votes is None for c in candidates):
        return None
    named = [i for i, c in enumerate(candidates) if elected is not None and c.name and same_person(c.name, elected)]
    if len(named) == 1:
        return named[0]
    top = max(c.votes or 0 for c in candidates)
    leaders = [i for i, c in enumerate(candidates) if c.votes == top]
    if len(leaders) == 1 and agrees(candidates[leaders[0]]):
        return leaders[0]
    return None


def parsed(day: str) -> list[Area]:
    """The 2023 declarations whose text reads cleanly, with the contests that pass their own checks."""
    out: list[Area] = []
    for name, url in files(day):
        text = text_of(url)
        if len(text) < 600 or name[:-4] not in AREAS_2023 or name in scanned(day):
            continue  # a scan with no text layer, or a file whose area is not known
        stem = AREAS_2023[name[:-4]]
        form = parse_form(text)
        if uncontested(form):
            form = form._replace(rejected=None)  # no poll was held
        bad, skipped = problems(form)
        caveats: list[str] = []
        for reason in [r for r in bad if r.startswith("declared seats") and "Hare allocation" in r]:
            bad.remove(reason)  # the seats are stored as declared, with the allocation the votes would give
            got = reason.split("Hare allocation ", 1)[1]
            caveats.append(
                "The seats are stored as the declaration gives them; a largest remainder allocation of the declared "
                f"votes would give {got.strip('{}').replace(chr(39), '').replace(': ', ' ')}."
            )
        ok = [n for n in sorted(form.constituencies) if n not in skipped]
        if bad and not ok:
            continue
        winners = {n: w for n in ok if (w := winner(form.constituencies[n], None)) is not None}
        ok = [n for n in ok if n in winners]
        out.append(
            Area(
                day,
                slug_of(stem),
                display(stem),
                url,
                form,
                not bad,
                ok,
                skipped,
                bad,
                winners,
                {},
                caveats,
                uncontested(form),
            )
        )
    return out


def number(field: object) -> tuple[int | None, Box | None]:
    """A transcribed figure and where it is printed."""
    if not isinstance(field, dict):
        return None, None
    value, page, box = field.get("value"), field.get("page"), field.get("box")
    located = (int(page), (box[0], box[1], box[2], box[3])) if isinstance(page, int) and isinstance(box, list) else None
    return (value if isinstance(value, int) else None), located


def place(entry: dict[str, object]) -> Box | None:
    page, box = entry.get("page"), entry.get("box")
    if isinstance(page, int) and isinstance(box, list) and len(box) == 4:
        return page, (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    return None


@cache
def scanned(day: str) -> dict[str, dict[str, object]]:
    """The transcription of each declaration of a day that was read from its page images."""
    if day not in TRANSCRIBED:
        return {}
    return json.loads((FORMS / f"{day}.json").read_text())


def transcribed(day: str) -> list[Area]:
    """Every declaration of a day whose page images were transcribed, checked again here."""
    urls = dict(files(day))
    forms = scanned(day)
    if set(forms) - set(urls) or (day not in PARTLY and set(forms) != set(urls)):
        raise ValueError(f"{day}: transcriptions {sorted(set(forms) ^ set(urls))} do not match the published files")
    out: list[Area] = []
    for name, t in forms.items():
        # A file named by number names its area in the transcription.
        stem = str(t["stem"]) if t.get("stem") else name.rsplit("/", 1)[1][:-4]
        boxes: dict[tuple[object, ...], Box] = {}
        lists: list[tuple[str, int]] = []
        listed = t.get("lists")
        entries = [e for e in listed if isinstance(e, dict)] if isinstance(listed, list) else []
        complete = bool(entries)
        # A single list printed as "NO CONTEST" takes every list seat without a poll.
        uncontested = len(entries) == 1 and not isinstance(entries[0].get("votes"), int)
        for e in entries:
            label, abbr, votes = str(e["label"]), str(e["abbr"]), e.get("votes")
            if f"({abbr})" not in label.replace(" ", "") and party_key(label) != party_key(abbr):
                label = f"{label} ({abbr})"
            if uncontested:
                lists.append((label, 0))
                continue
            if not isinstance(votes, int):
                complete = False
                continue
            lists.append((label, votes))
            located = place(e)
            if located:
                boxes[("list", party_key(label))] = located
        valid, at = number(t.get("valid")) if not uncontested else (None, None)
        if at and valid == sum(v for _, v in lists):
            boxes[("valid",)] = at
        rejected, at = number(t.get("rejected")) if not uncontested else (None, None)
        if at:
            boxes[("rejected",)] = at
        seats: dict[str, int] = {}
        allotted = t.get("seats")
        for e in allotted if isinstance(allotted, list) else []:
            if isinstance(e, dict) and isinstance(e.get("seats"), int):
                seats[party_key(str(e["abbr"]))] = int(e["seats"])
        constituencies: dict[int, list[Candidate]] = {}
        labels: dict[int, str] = {}
        status: dict[int, str] = {}
        found = t.get("constituencies")
        for c in found if isinstance(found, list) else []:
            if not isinstance(c, dict):
                continue
            n = int(c["number"])
            status[n] = str(c.get("status"))
            if c.get("label"):
                labels[n] = str(c["label"])
            people: list[Candidate] = []
            listed_people = c.get("candidates")
            for position, e in enumerate(listed_people if isinstance(listed_people, list) else [], 1):
                abbr = str(e.get("abbr") or "")
                party = "INDEPENDENT" if abbr == "IND" else abbr or None
                votes = e.get("votes")
                who = str(e.get("name") or "").strip()
                people.append(Candidate(who, party, votes if isinstance(votes, int) else None))
                located = place(e)
                if located:
                    boxes[("candidate", n, position)] = located
            constituencies[n] = people
        elected: dict[int, str] = {}
        sides: dict[int, str] = {}  # the party of each elected councillor
        named = t.get("elected")
        for e in named if isinstance(named, list) else []:
            if isinstance(e, dict) and isinstance(e.get("number"), int):
                elected[int(e["number"])] = str(e["name"])
                if e.get("abbr"):
                    sides[int(e["number"])] = str(e["abbr"])
        form = Form(lists, rejected, seats, constituencies, elected, valid, labels)
        reasons: list[str] = []
        keys = [party_key(label) for label, _ in lists]
        if not complete:
            reasons.append("a list's votes are not legible")
        if len(set(keys)) != len(keys):
            raise ValueError(f"{day} {name}: two lists share a key")
        for k in [k for k, v in seats.items() if k not in keys and not v]:
            del seats[k]  # a nil seat line for a party with no list
        if set(seats) < set(keys) and sum(seats.values()) == len(constituencies):
            # Some declarations print seats only for the lists that won any.
            seats.update({k: 0 for k in keys if k not in seats})
        if set(seats) != set(keys):
            reasons.append(f"seats {sorted(seats)} do not match lists {keys}")
        caveats: list[str] = [str(t["note"])] if t.get("note") else []
        if not reasons and not uncontested:
            got = hare(dict(zip(keys, (v for _, v in lists), strict=True)), sum(seats.values()))
            if got != seats:
                caveats.append(
                    "The seats are stored as the declaration gives them; a largest remainder allocation of the "
                    "declared votes would give " + ", ".join(f"{k} {got[k]}" for k in keys) + "."
                )
        if valid is not None and valid != sum(v for _, v in lists):
            caveats.append(f"The declaration prints {valid:,} valid votes, which is not the sum of the list votes.")
        winners: dict[int, int] = {}
        skipped: list[int] = []
        for n, people in sorted(constituencies.items()):
            tacit = status[n] == "unopposed" and len(people) == 1
            counted = status[n] == "counted" and people and all(c.votes is not None for c in people)
            w = winner(people, elected.get(n), sides.get(n)) if tacit or counted else None
            if w is None:
                skipped.append(n)
                continue
            if not people[w].name and n in elected:
                # The votes are printed by party; the elected section names the winner.
                people[w] = people[w]._replace(name=elected[n])
            if tacit:
                constituencies[n] = [people[0]._replace(votes=None)]
            winners[n] = w
        ok = sorted(winners)
        out.append(
            Area(
                day,
                slug_of(stem),
                display(stem),
                urls[name],
                form,
                not reasons,
                ok,
                skipped,
                reasons,
                winners,
                boxes,
                caveats,
                uncontested,
                True,
            )
        )
    return out


@cache
def areas(day: str) -> list[Area]:
    out = (parsed(day) if day in PARTLY else []) + (transcribed(day) if day in TRANSCRIBED else [])
    slugs = [a.slug for a in out]
    if len(set(slugs)) != len(slugs):
        raise ValueError(f"{day}: two declarations for one area")
    return out


def kinds() -> Iterator[Area]:
    for day in DAYS:
        yield from areas(day)


def council(slug: str) -> str:
    return key("body", "council", slug)


SYSTEM_LIST = key("electoral_system", "list")
SYSTEM_FPTP = key("electoral_system", "fptp")
SOURCE_ID = key("source", "gecom")


def party_id(label: str) -> str:
    return key("party", party_key(label))


# Parties that packages already loaded refer to, under keys that today's readings of the same declarations no longer
# produce; they stay in the registry so that those packages' contest options keep their party.
LEGACY_PARTIES = {"AGNSP": "GNS Party", "UNITEDREPUBLICANPARTYURP": "United Republican Party - Urp"}


def legacy_party_key(label: str) -> str:
    """The key the first loaded packages gave a label, before a printed abbreviation decided it."""
    upper = label.upper()
    if re.search(r"AFC|ALLIANCE FOR CHANGE", upper) and re.search(r"APNU|PARTNERSHIP", upper):
        return "APNU+AFC"
    if re.search(r"APNU|A\.P\.N\.U|PARTNERSHIP|PARTNERS|PARTHNER|PARETNER|PARTERS", upper):
        return "APNU"
    if re.search(r"PPP|P\.P\.P|PROGRESSIVE", upper):
        return "PPP/C"
    if re.search(r"\bAFC\b|ALLIANCE FOR CHANGE", upper):
        return "AFC"
    found = re.findall(r"\(([^)]+)\)", upper)
    key_ = re.sub(r"[^A-Z/+]", "", found[-1] if found else upper)
    return ALIASES.get(key_, key_)


def party_names() -> dict[str, str]:
    """The key and the name printed for each party that any declaration lists or any candidate stands for.

    Every parsed form counts, including the lists and constituencies that are left out of a contest, and a key
    that an earlier load gave a label stays, so that no party a loaded package refers to leaves the registry."""
    found: dict[str, str] = dict(LEGACY_PARTIES)
    for area in kinds():
        labels = [name for name, _ in area.form.lists]
        for cands in area.form.constituencies.values():
            labels += [c.party for c in cands if c.party and c.party != "INDEPENDENT"]
        for label in labels:
            for k in {party_key(label), legacy_party_key(label)}:
                if k in FULL:
                    found[k] = FULL[k]
                elif k not in found or (len(found[k]) <= len(k) and re.search(r"[A-Za-z]{4}", label)):
                    clean = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()
                    found[k] = (clean.title() if label.isupper() else clean) if re.search(r"[A-Za-z]{4}", label) else k
    return found


# Council bodies that packages already loaded refer to, under slugs that the areas no longer produce because the same
# area is now named alike across the election days; they stay in the registry so that those contests keep their body.
LEGACY_COUNCILS = {"adventure-bushlot": "Adventure Bushlot"}


def council_name(slug: str, name: str) -> str:
    return f"Municipality of {name}" if slug in MUNICIPALITIES else f"{name} Neighbourhood Democratic Council"


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Local authority area councils of Guyana, their parties and groups, and their electoral systems",
    )
    p.external.add(COUNTRY)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Guyana Elections Commission",
        title="Form 31 declarations of the local government elections published by the Guyana Elections Commission",
        url=PAGES["all"],
        license="Official publication; reuse terms are not stated",
        attribution="Guyana Elections Commission",
    )
    p.add("scheme", code="gy.lae", polity_id=COUNTRY, kind="electoral_district", rank=3, noun="local authority area")
    p.add("scheme", code="gy.constituency", polity_id=COUNTRY, kind="electoral_district", rank=4, noun="constituency")
    seen: dict[str, str] = {}
    for area in kinds():
        seen.setdefault(area.slug, area.name)
    for slug, name in LEGACY_COUNCILS.items():
        seen.setdefault(slug, name)
    for slug, name in sorted(seen.items()):
        p.add("body", id=council(slug), polity_id=COUNTRY, kind="council")
        p.name(council(slug), council_name(slug, name), "en", "official")
    p.add("electoral_system", id=SYSTEM_LIST, polity_id=COUNTRY, rules={"formula": "hare"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM_LIST),
        entity_id=SYSTEM_LIST,
        kind="methodology",
        body_md="Voters choose a party or group list. Half of a council's seats go to the lists by the largest remainder method with a Hare quota.",
        lang="en",
    )
    p.add("electoral_system", id=SYSTEM_FPTP, polity_id=COUNTRY, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM_FPTP),
        entity_id=SYSTEM_FPTP,
        kind="methodology",
        body_md="The other half of a council's seats are single-member constituencies, each won by the candidate with the most votes.",
        lang="en",
    )
    for k, name in sorted(party_names().items()):
        party = key("party", k)
        group = (
            re.search(r"group|association|developers|council|action|movement|citizens", name, re.IGNORECASE)
            and k not in FULL
        )
        p.add(
            "party",
            id=party,
            kind="bloc" if k in BLOCS else "independent_group" if group else "party",
            polity_id=COUNTRY,
        )
        p.name(party, k, "en", "abbreviation")
        if name != k:
            p.name(party, name, "en", "official", False)
    return p


@cache
def sizes(url: str) -> dict[int, tuple[int, int]]:
    """Each page's size in pixels at the resolution the boxes were read at."""
    out = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "9999", str(raw.cached(SOURCE, url).path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {
        int(n): (round(float(w) * DPI / 72), round(float(h) * DPI / 72))
        for n, w, h in re.findall(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+) pts", out)
    }


class Writer:
    """Adds the values of one package, with the page and box of each transcribed figure."""

    def __init__(self, p: Package) -> None:
        self.p = p
        self.pages: set[tuple[str, int]] = set()

    def put(
        self, area: Area, rs: str, unit: str, option: str | None, measure: int, value: int | None, at: Box | None
    ) -> None:
        if value is None:
            return
        self.p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )
        if at is None:
            return
        page, (x0, y0, x1, y1) = at
        width, height = sizes(area.url)[page]
        self.pages.add((area.url, page))
        self.p.add(
            "value_locator",
            result_set_id=rs,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=key("page", area.url, page),
            source_id=None,
            x0=round(x0 * width, 1),
            y0=round(y0 * height, 1),
            x1=round(x1 * width, 1),
            y1=round(y1 * height, 1),
        )


def build(day: str) -> Package:
    first = DAYS[day]
    chosen = areas(day)
    p = Package(
        f"{SOURCE}/{first.isoformat()}",
        SOURCE,
        f"The Guyanese local government elections of {first}: party list votes and seats for each local authority "
        "area and candidate votes for each constituency, from the Form 31 declarations",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(COUNTRY)
    artifacts = {area.url: p.artifact(raw.cached(SOURCE, area.url), SOURCE_ID) for area in chosen}
    out = Writer(p)
    election = key("election", day)
    p.add("election", id=election, polity_id=COUNTRY, kind="local", date_from=first, date_to=first, status="held")
    p.add("slug", path=f"gy/{day}-local", entity_id=election, is_current=True)
    p.name(election, f"{first.year} Guyanese local government elections", "en", "official")
    for area in chosen:
        method = METHOD + (READ if area.read else "")
        f = area.form
        title = council_name(area.slug, area.name)
        locator = {"url": area.url}
        if area.list_ok:
            unit = key("unit", day, area.slug)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=COUNTRY,
                scheme_code="gy.lae",
                code=f"{day}:{area.slug}",
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
            )
            p.name(unit, area.name, "en", "official")
            seats = f.seats
            contest = key("contest", day, area.slug, "list")
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=council(area.slug),
                magnitude=sum(seats.values()),
                round_number=1,
                electoral_system_id=SYSTEM_LIST,
                default_result_kind="tacit" if area.uncontested else "counted",
            )
            p.name(contest, f"{first.year} {title} election, list seats", "en", "official")
            p.add("slug", path=f"gy/{day}-local/{area.slug}", entity_id=contest, is_current=True)
            rs = key("result_set", day, area.slug, "list")
            p.add(
                "result_set",
                id=rs,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if area.uncontested else "counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=True,
                completeness=0.0 if area.uncontested else 1.0,
            )
            body = (
                (METHOD + " Only one list stood, and it took every list seat without a poll.")
                if area.uncontested
                else method
            )
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=body, lang="en")
            if area.caveats:
                p.add(
                    "note",
                    id=key("note", rs, "caveat"),
                    entity_id=rs,
                    kind="caveat",
                    body_md=" ".join(area.caveats),
                    lang="en",
                )
            p.add("citation", id=key("citation", rs), entity_id=rs, source_id=SOURCE_ID, locator=locator)
            out.put(area, rs, unit, None, INVALID, f.rejected, area.boxes.get(("rejected",)))
            if f.valid is not None:
                out.put(area, rs, unit, None, VALID, sum(v for _, v in f.lists), area.boxes.get(("valid",)))
                p.add("declared", result_set_id=rs, option_id=None, measure=VALID, value=f.valid)
            for position, (label, votes) in enumerate(f.lists, 1):
                k = party_key(label)
                option = key("option", day, area.slug, "list", k)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=key("party", k),
                    ballot_position=position,
                    ballot_label=FULL.get(k, label),
                )
                if not area.uncontested:
                    out.put(area, rs, unit, option, VOTES, votes, area.boxes.get(("list", k)))
                p.add(
                    "outcome",
                    result_set_id=rs,
                    option_id=option,
                    kind="won" if seats[k] else "lost",
                    seats=seats[k],
                )
        for n in area.ok:
            candidates = f.constituencies[n]
            label = f.labels.get(n)
            unit = key("unit", day, area.slug, n)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=COUNTRY,
                scheme_code="gy.constituency",
                code=f"{day}:{area.slug}:{n}",
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
            )
            p.name(unit, f"{area.name}, constituency {n}" + (f" ({label.title()})" if label else ""), "en", "official")
            tacit = len(candidates) == 1 and candidates[0].votes is None
            contest = key("contest", day, area.slug, "constituency", n)
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=council(area.slug),
                magnitude=1,
                round_number=1,
                electoral_system_id=SYSTEM_FPTP,
                default_result_kind="tacit" if tacit else "counted",
            )
            p.name(contest, f"{first.year} {title} election, constituency {n}", "en", "official")
            p.add("slug", path=f"gy/{day}-local/{area.slug}/constituency-{n}", entity_id=contest, is_current=True)
            rs = key("result_set", day, area.slug, "constituency", n)
            p.add(
                "result_set",
                id=rs,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=True,
                completeness=0.0 if tacit else 1.0,
            )
            body = (METHOD + " The only candidate was returned unopposed.") if tacit else method
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=body, lang="en")
            p.add("citation", id=key("citation", rs), entity_id=rs, source_id=SOURCE_ID, locator=locator)
            for position, c in enumerate(candidates, 1):
                option = key("option", day, area.slug, "constituency", n, position)
                person = key("person", day, area.slug, n, position)
                solo = c.party == "INDEPENDENT"
                party = None if solo or not c.party else party_id(c.party)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=position,
                    # A candidate the declaration gives by party only is labelled with the party.
                    ballot_label=c.name or f"{FULL.get(party_key(c.party or ''), c.party or 'Independent')} candidate",
                )
                if area.winners[n] == position - 1:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="elected", seats=1)
                if not tacit:
                    out.put(area, rs, unit, option, VOTES, c.votes, area.boxes.get(("candidate", n, position)))
                if not c.name:
                    continue
                p.add("person", id=person)
                p.name(person, c.name.title() if c.name.isupper() else c.name, "en", "official")
                candidacy = key("candidacy", day, area.slug, n, position)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=c.name,
                    member_of_party_id=party,
                    is_independent=solo,
                )
    wrong = sorted(
        a.name
        for a in chosen
        if a.list_ok and a.form.valid is not None and a.form.valid != sum(v for _, v in a.form.lists)
    )
    if wrong:
        p.accept(
            "accounting.declared",
            f"The declarations for {', '.join(wrong)} print a total of valid votes that is not the sum of their own "
            "list votes; the total is kept as printed and the values are the list votes.",
        )
    for url, page in sorted(out.pages):
        width, height = sizes(url)[page]
        sha = raw.cached(SOURCE, url).sha256
        p.add(
            "source_page",
            id=key("page", url, page),
            artifact_id=artifacts[url],
            page_number=page,
            image_key=f"pages/{SOURCE}/{sha}/{page}.png",
            width=width,
            height=height,
        )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{DAYS[day].isoformat()}" for day in DAYS)]


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())
