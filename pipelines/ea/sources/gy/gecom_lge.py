"""Guyana's local government elections of 2018 and 2023, from the Elections Commission's Form 31 declarations, one
document per local authority area: list votes, rejected ballots, seats, and candidate votes for each constituency."""

import difflib
import json
import re
from functools import cache
from typing import NamedTuple
from urllib.parse import unquote

from ea import raw

SOURCE = "gy.gecom_lge"
SITE = "https://gecom.org.gy/public"
PAGES = {
    "2018": f"{SITE}/home/results/lge2018",
    "2023": f"{SITE}/home/resource_hub/lge2023",
}
FORM31 = f"{SITE}/assets/docs/lge2023/form31s/"


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
    if day == "2018":
        return tree("2018")
    page = raw.cached(SOURCE, PAGES["2023"]).path.read_text(errors="replace")
    names = sorted(set(re.findall(r"form31s/(\d+\.\d+\.pdf)", page)), key=lambda n: [int(x) for x in n[:-4].split(".")])
    return [(unquote(n), f"{FORM31}{n}") for n in names]


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    count = 0
    for day in PAGES:
        for _, url in files(day):
            raw.fetch(SOURCE, url, suffix=".pdf")
            count += 1
    print(f"{count} files")


NUMBER = re.compile(r"(\d[\d,]*)\s*$")
UNOPPOSED = re.compile(r"unopposed|no contest|uncontested", re.IGNORECASE)
HEADER = re.compile(r"^\s*constituen\w*\s*(?:no\.?|number|#)?\s*(\d+)\b[\s:.\-–]*(.*)$", re.IGNORECASE)
NOISE = re.compile(r"^(page \d+ of \d+|created in photo|returning officer|signature|dated?\b)", re.IGNORECASE)


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


def clean(line: str) -> str:
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
    return sorted(re.findall(r"[A-Z]{2,}", re.sub(r"\([^)]*\)", " ", name.upper())))


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
    return difflib.SequenceMatcher(None, " ".join(x), " ".join(y)).ratio() >= 0.85


def split_candidate(text: str) -> tuple[str, str | None]:
    """A candidate's name and party label from a line that gives one or both."""
    text = text.strip(" :-–")
    found = re.search(r"\(([^)]*)\)\s*$", text)
    if found and not re.fullmatch(r"\d+", found.group(1)):
        label = found.group(1).strip()
        return text[: found.start()].strip(" :-–"), "INDEPENDENT" if re.fullmatch(
            r"independent|individual", label, re.IGNORECASE
        ) else label
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
            if number is None and index + 1 < len(body) and re.fullmatch(r"\d[\d,]*", body[index + 1]):
                number = int(body[index + 1].replace(",", ""))
                index += 1
            index += 1
            if re.match(r"total", name, re.IGNORECASE):
                valid = number
            elif name and not re.search(r"candidates\s*:?$", name, re.IGNORECASE):
                pairs.append((name, number))
    lists = [(name, number) for name, number in pairs if number is not None]
    rejected = None
    if rejected_at is not None:
        for line in lines[rejected_at : rejected_at + 2]:
            number = tail(line)[1]
            if number is not None:
                rejected = number
                break
    seats: dict[str, int] = {}
    if seats_at is not None:
        end = after if after is not None else len(lines)
        for line in lines[seats_at + 1 : end]:
            name, number = tail(line)
            if number is None:
                last = name.split()[-1].lower() if name.split() else ""
                if last in WORDS:
                    name, number = " ".join(name.split()[:-1]), WORDS[last]
            if number is not None and name:
                seats[party_key(name)] = number
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
                if not rest:
                    rest = next((lines[j] for j in range(i + 1, len(lines)) if not HEADER.match(lines[j])), "")
                elected[number] = rest
            continue
        if mode == "votes" and current is not None:
            raw_blocks[current].append(line)
    for number, entries in raw_blocks.items():
        candidates: list[Candidate] = []
        index = 0
        while index < len(entries):
            name, votes = tail(entries[index])
            if votes is None and index + 1 < len(entries) and re.fullmatch(r"\d[\d,]*", entries[index + 1]):
                votes = int(entries[index + 1].replace(",", ""))
                index += 1
            index += 1
            opposed = UNOPPOSED.search(name) is not None
            name = re.sub(r"\b(unopposed|no contest|uncontested)\b( list)?", "", name, flags=re.IGNORECASE).strip(
                " :-–"
            )
            if not re.search(r"[A-Za-z]{2}", name) or re.match(
                r"(no\.? of|names? of|the following)", name, re.IGNORECASE
            ):
                continue
            who, label = split_candidate(name)
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


def problems(form: Form) -> tuple[list[str], list[int]]:
    """What stops the list contest from loading, and which constituencies cannot be loaded."""
    bad: list[str] = []
    keys = [party_key(name) for name, _ in form.lists]
    if not form.lists:
        bad.append("no list votes")
    elif len(set(keys)) != len(keys):
        bad.append("two lists share a key")
    elif form.rejected is None:
        bad.append("no rejected ballots")
    elif not form.seats or not set(form.seats) <= set(keys):
        bad.append(f"seats {sorted(form.seats)} do not match lists {keys}")
    else:
        votes = {party_key(name): v for name, v in form.lists}
        total = sum(form.seats.values())
        got = hare(votes, total)
        if {k: form.seats.get(k, 0) for k in votes} != got:
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
            if len(leaders) != 1 or not same_person(leaders[0].name, elected):
                out.append(number)
    if sorted(form.constituencies) != list(range(1, len(form.constituencies) + 1)):
        out = sorted(form.constituencies)
    if set(form.constituencies) != set(form.elected):
        out = sorted(form.constituencies)
    if not bad and not out and form.constituencies and sum(form.seats.values()) != len(form.constituencies):
        # The Act gives a council as many list seats as constituencies, so the two counts check each other.
        bad.append(f"{sum(form.seats.values())} list seats for {len(form.constituencies)} constituencies")
    return bad, out


import subprocess
from collections.abc import Iterator
from datetime import date
from uuid import uuid5

from ea import package
from ea.package import Package

COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fGY"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DAYS = {"2018": date(2018, 11, 12), "2023": date(2023, 6, 12)}
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
# The file names misspell some areas' names.
DISPLAY = {
    "Haurarunui Yarowkabra": "Hauraruni Yarowkabra",
    "Hague Blakenburg": "Hague Blankenburg",
    "Annadale Riverstown": "Annandale Riverstown",
    "Buxton Fouls": "Buxton Foulis",
    "Mon Repos La Reconnisance": "Mon Repos La Reconnaissance",
    "Seafield Tempe": "Seafield Tempie",
    "La Jalousie Novelle Flanders": "La Jalousie Nouvelle Flanders",
    "Gilbraltar Fyrish": "Gibraltar Fyrish",
    "Better Hope LBI": "Better Hope La Bonne Intention",
    "Adventure Bushlot": "Adventure Bushlot",
    "Kintyre Borlam": "Kintyre Borlam",
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


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def slug_of(stem: str) -> str:
    stem = re.sub(r"^(the\s+)?(municipality|town) of\s+", "", stem.strip(), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def display(stem: str) -> str:
    stem = re.sub(r"^(the\s+)?(municipality|town) of\s+", "", re.sub(r"\s+", " ", stem.strip()), flags=re.IGNORECASE)
    return DISPLAY.get(stem, stem)


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


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


@cache
def areas(day: str) -> list[Area]:
    """Each declaration of the day whose text reads cleanly, with the contests that pass their own checks."""
    out: list[Area] = []
    for name, url in files(day):
        text = text_of(url)
        if len(text) < 600:
            continue  # a scan with no text layer
        if day == "2018":
            stem = name.rsplit("/", 1)[1][:-4]
        elif name[:-4] in AREAS_2023:
            stem = AREAS_2023[name[:-4]]
        else:
            continue
        form = parse_form(text)
        bad, skipped = problems(form)
        ok = [n for n in sorted(form.constituencies) if n not in skipped]
        if bad and not ok:
            continue
        out.append(Area(day, slug_of(stem), display(stem), url, form, not bad, ok, skipped, bad))
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


def party_names() -> dict[str, str]:
    """The key and the name printed for each party that a declaration lists or a candidate stands for."""
    found: dict[str, str] = {}
    for area in kinds():
        labels = [name for name, _ in area.form.lists] if area.list_ok else []
        for candidates in area.form.constituencies.values():
            labels += [c.party for c in candidates if c.party and c.party != "INDEPENDENT"]
        for label in labels:
            k = party_key(label)
            if k in FULL:
                found[k] = FULL[k]
            elif k not in found or (len(found[k]) <= len(k) and re.search(r"[A-Za-z]{4}", label)):
                found[k] = (
                    (
                        re.sub(r"\s*\([^)]*\)\s*$", "", label).strip().title()
                        if label.isupper()
                        else re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()
                    )
                    if re.search(r"[A-Za-z]{4}", label)
                    else k
                )
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Local authority area councils of Guyana, their parties, and their electoral systems",
    )
    p.external.add(COUNTRY)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Guyana Elections Commission",
        title="Form 31 declarations of the local government elections published by the Guyana Elections Commission",
        url=f"{SITE}/home/results",
        license="Official publication; reuse terms are not stated",
        attribution="Guyana Elections Commission",
    )
    p.add("scheme", code="gy.lae", polity_id=COUNTRY, kind="electoral_district", rank=3, noun="local authority area")
    p.add("scheme", code="gy.constituency", polity_id=COUNTRY, kind="electoral_district", rank=4, noun="constituency")
    seen: dict[str, str] = {}
    for area in kinds():
        seen.setdefault(area.slug, area.name)
    for slug, name in sorted(seen.items()):
        p.add("body", id=council(slug), polity_id=COUNTRY, kind="council")
        town = slug in MUNICIPALITIES
        p.name(
            council(slug),
            f"Municipality of {name}" if town else f"{name} Neighbourhood Democratic Council",
            "en",
            "official",
        )
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
        p.name(party, name, "en", "official", False)
    return p


def _put(p: Package, rs: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is not None:
        p.add(
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


def build(day: str) -> Package:
    first = DAYS[day]
    chosen = areas(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Guyanese local government elections of {first}: party list votes and seats for each local authority "
        "area and candidate votes for each constituency, from the Form 31 declarations",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(COUNTRY)
    for area in chosen:
        p.artifact(raw.cached(SOURCE, area.url), SOURCE_ID)
    election = key("election", day)
    p.add("election", id=election, polity_id=COUNTRY, kind="local", date_from=first, date_to=first, status="held")
    p.add("slug", path=f"gy/{day}-local", entity_id=election, is_current=True)
    p.name(election, f"{first.year} Guyanese local government elections", "en", "official")
    for area in chosen:
        f = area.form
        council_name = (
            f"Municipality of {area.name}"
            if area.slug in MUNICIPALITIES
            else f"{area.name} Neighbourhood Democratic Council"
        )
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
                default_result_kind="counted",
            )
            p.name(contest, f"{first.year} {council_name} election, list seats", "en", "official")
            p.add("slug", path=f"gy/{day}-local/{area.slug}", entity_id=contest, is_current=True)
            rs = key("result_set", day, area.slug, "list")
            p.add(
                "result_set",
                id=rs,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
            p.add("citation", id=key("citation", rs), entity_id=rs, source_id=SOURCE_ID, locator=locator)
            _put(p, rs, unit, None, INVALID, f.rejected)
            total = sum(v for _, v in f.lists)
            if f.valid is not None:
                _put(p, rs, unit, None, VALID, total)
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
                _put(p, rs, unit, option, VOTES, votes)
                if k in seats:
                    p.add(
                        "outcome",
                        result_set_id=rs,
                        option_id=option,
                        kind="won" if seats[k] else "lost",
                        seats=seats[k],
                    )
        for number in area.ok:
            candidates = f.constituencies[number]
            label = f.labels.get(number)
            unit = key("unit", day, area.slug, number)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=COUNTRY,
                scheme_code="gy.constituency",
                code=f"{day}:{area.slug}:{number}",
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
            )
            p.name(
                unit, f"{area.name}, constituency {number}" + (f" ({label.title()})" if label else ""), "en", "official"
            )
            tacit = len(candidates) == 1 and candidates[0].votes is None
            contest = key("contest", day, area.slug, "constituency", number)
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
            p.name(contest, f"{first.year} {council_name} election, constituency {number}", "en", "official")
            p.add("slug", path=f"gy/{day}-local/{area.slug}/constituency-{number}", entity_id=contest, is_current=True)
            rs = key("result_set", day, area.slug, "constituency", number)
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
            body = METHOD + (" The only candidate was returned unopposed." if tacit else "")
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=body, lang="en")
            p.add("citation", id=key("citation", rs), entity_id=rs, source_id=SOURCE_ID, locator=locator)
            top = max((c.votes or 0 for c in candidates), default=0)
            for position, c in enumerate(candidates, 1):
                option = key("option", day, area.slug, "constituency", number, position)
                person = key("person", day, area.slug, number, position)
                solo = c.party == "INDEPENDENT"
                party = None if solo or not c.party else party_id(c.party)
                p.add("person", id=person)
                p.name(person, c.name.title() if c.name.isupper() else c.name, "en", "official")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=c.name,
                )
                candidacy = key("candidacy", day, area.slug, number, position)
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
                if tacit or c.votes == top:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="elected", seats=1)
                if not tacit:
                    _put(p, rs, unit, option, VOTES, c.votes)
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())
