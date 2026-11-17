"""Dominica's Electoral Office: the village councils, Roseau City Council, Portsmouth Town Council, Canefield Urban
Council, and the Kalinago Chief and Council, from the results notices it posts on its website, 2012 to 2026."""

import functools
import html
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from uuid import uuid5

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "dm.local"
SITE = "https://electoraloffice.gov.dm"
SECTIONS = (("village-council-elections", 15), ("news", 31))
LIST_PAGE = f"{SITE}/village-council-elections/list-of-village-councils"
DOMINICA = str(uuid5(package.NAMESPACE, "polity\x1fDM"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
AREA_SCHEME = "dm.local_area"
UNIT_SCHEME = "dm.local_count"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DEO = key("source", "electoral-office")
BLOCK = key("electoral_system", "block")
FPTP = key("electoral_system", "fptp")
CHIEF_OFFICE = key("office", "kalinago-chief")

METHOD = (
    "Published by the Electoral Office of the Commonwealth of Dominica in a notice from the Chief Elections Officer. "
    "The notice gives each candidate's votes and the turnout. Ballots cast are the voters counted as having voted, "
    "and rejected ballots are stored as invalid ballots. The notice does not give the number of electors on the "
    "roll, so registered voters are not stored. Each voter could vote for as many candidates as there were seats, so "
    "the votes for candidates are not a count of ballots."
)
METHOD_SINGLE = (
    "Published by the Electoral Office of the Commonwealth of Dominica in a notice from the Chief Elections Officer. "
    "The notice gives each candidate's votes and the turnout. Ballots cast are the voters counted as having voted, "
    "and rejected ballots are stored as invalid ballots. The notice does not give the number of electors on the "
    "roll, so registered voters are not stored."
)

MONTHS = {
    name: number
    for number, name in enumerate(
        [
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
        ],
        1,
    )
}
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5}
HONORIFIC = re.compile(r"^(?:Mr|Ms|Mrs|Miss|Dr|Hon)\.?\s+", re.IGNORECASE)
DATE = re.compile(r"(%s)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})" % "|".join(MONTHS), re.IGNORECASE)

# Notices that announce nominations but also name the candidates returned without a poll:
# page -> (council, date of that council's count, ward).
UNOPPOSED = {
    "/village-council-elections/portsmouth-nomination-notice": ("portsmouth", date(2014, 9, 16), 1),
    "/news/notice-residents-of-portsmouth": ("portsmouth", date(2023, 12, 21), 4),
    "/news/nominated-to-contest-the-roseau-city-council": ("roseau", date(2023, 2, 9), 1),
    "/village-council-elections/withdrawal-of-candidate-roseau-city-council": ("roseau", date(2013, 11, 19), 3),
}

# The seats a council's general election fills, below which a notice without a label is a by-election.
SIZE = {"roseau": 2, "portsmouth": 2, "kalinago": 6}

# How the notices spell a village council that the Office's list of councils names differently.
ALIASES = {
    "tetemorne": "Tete Morne",
    "fondcaine": "Fond Canie",
    "coulibistire": "Coulibistrie",
}


# Fetching --------------------------------------------------------------------------------------------------------


def index_urls() -> list[str]:
    return [
        f"{SITE}/{section}" + (f"?start={10 * i}" if i else "") for section, pages in SECTIONS for i in range(pages)
    ]


def clean(text: object) -> str:
    return " ".join(html.unescape(str(text or "")).replace("\xa0", " ").split())


def fetch_text(link: str) -> str:
    return raw.fetch(SOURCE, link, suffix=".html").path.read_text(encoding="utf-8", errors="replace")


def notice_links() -> list[str]:
    links: dict[str, None] = {}
    for link in index_urls():
        text = fetch_text(link)
        for section, _ in SECTIONS:
            for path in re.findall(r'href="(/%s/[^"?#]+)"' % section, text):
                links[SITE + path] = None
    return sorted(links)


def fetch() -> None:
    raw.fetch(SOURCE, LIST_PAGE, suffix=".html")
    raw.fetch_many(SOURCE, notice_links(), suffix=".html")


# Parsing ---------------------------------------------------------------------------------------------------------


def article(text: str) -> str:
    start = text.find('itemprop="articleBody"')
    end = text.find('id="sp-bottom"', start)
    return text[start : end if end > 0 else len(text)]


def lines(text: str) -> list[str]:
    body = article(text)
    body = re.sub(r"</t[dh]>\s*", " ", body)
    body = re.sub(r"</tr>|<br\s*/?>|</p>|</h\d>|</div>|</li>", "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", "", body))
    found = [re.sub(r"[ \t\xa0]+", " ", row).strip() for row in body.split("\n")]
    found = [row for row in found if row and not row.startswith(("itemprop=", "<section"))]
    merged: list[str] = []
    for row in found:
        if re.fullmatch(r"(?:Name|Candidate)(?:\s+Votes)?|Votes", row, re.IGNORECASE):
            continue
        if merged and re.fullmatch(r"\d[\d,]*\s*votes?\.?", row, re.IGNORECASE):
            merged[-1] += " " + row
        elif merged and re.fullmatch(r"\d[\d,]*", row) and not re.search(r"\d|:$", merged[-1][-1:]):
            merged[-1] += f" {row} votes"
        else:
            merged.append(row)
    return merged


def person(text: str) -> str:
    return HONORIFIC.sub("", clean(text).strip(" ,.;:-")).strip()


def count(text: str) -> int:
    found = re.search(r"\(\s*(\d[\d,]*)\s*\)", text) or re.search(r"(\d[\d,]*)", text)
    return int((found.group(1) if found else "0").replace(",", ""))


@dataclass
class Candidate:
    name: str
    votes: int | None
    elected: bool = False
    tied: bool = False
    rank: int | None = None  # the number printed before the name


@dataclass
class Contest:
    ward: int | None = None
    candidates: list[Candidate] = field(default_factory=list)
    seats: int | None = None
    cast: int | None = None
    percent: str | None = None
    male: int | None = None
    female: int | None = None
    shares: tuple[str, str, str] | None = None  # the printed percentages: turnout, male, female
    rejected: int | None = None
    tacit: bool = False
    stated: bool = False  # the notice says how many seats the contest filled
    gaps: int = 0  # candidates the printed numbering says are missing
    tail: list[str] = field(default_factory=list)
    total: tuple[int, str] | None = None  # the notice's own total over all wards


@dataclass
class Notice:
    url: str
    council: str  # a village's printed name, or "roseau", "portsmouth", "canefield", "kalinago", "chief"
    day: date
    kind: str
    contests: list[Contest]
    inferred: bool = False  # the notice does not say whether it was a general election or a by-election
    urls: list[str] = field(default_factory=list)


def parse_date(text: str) -> date | None:
    found = DATE.search(text)
    if not found:
        return None
    return date(int(found.group(3)), MONTHS[found.group(1).lower()], int(found.group(2)))


VOTE_LINE = re.compile(
    r"^(?:(?P<rank>\d+)\s*[.)]\s*)?(?P<name>[A-Za-z][^\d]*?)[\s\-–:]*(?P<votes>\d[\d,]*)(?:\s*votes?)?\s*[.,]?$",
    re.IGNORECASE,
)
WARD_LINE = re.compile(r"^(?:\d+\s*[.)]\s*)?ward\s*\(?\s*(iv|iii|ii|i|\d)\s*\)?\s*:?$", re.IGNORECASE)
OTHERS = re.compile(r"^(?:the )?other candidates?\b", re.IGNORECASE)
TIED = re.compile(
    r"^(?P<names>.+?)\s+(?:were\s+)?tied\s+in\s+(?:\w+\s+)?(?:place|position)(?:\s*\(\w+\))?(?:\s+to be on the council)?\s+with\s+(?P<votes>\d+)\s+votes",
    re.IGNORECASE,
)


def ward_number(label: str) -> int:
    return ROMAN.get(label.lower()) or int(label)


def council_of(text: str, url: str) -> str:
    low = text.lower()
    if "kalinago chief" in low or "carib chief" in low:
        return "chief"
    if "kalinago" in low or "carib council" in low or "carib territory" in low:
        return "kalinago"
    if "roseau city council" in low or "roseau" in url:
        return "roseau"
    if "portsmouth" in low or "portsmouth" in url:
        return "portsmouth"
    if "canefield urban council" in low:
        return "canefield"
    found = re.search(
        r"\b(?:to|for) (?:serve as members? of )?the ((?:[A-Z][\w'’.\-]*\s*|/\s*)+?)\s*Village Council", text
    )
    found = found or re.search(r"\b(?:of|for) the ((?:[A-Z][\w'’.\-]*\s*|/\s*)+?)\s*Village Council", text)
    if found:
        return clean(found.group(1))
    found = re.search(r"residents of ((?:[A-Z][\w'’.\-]*\s*|/\s*|,\s*|and\s+)+?) are informed", text, re.IGNORECASE)
    if found:
        return clean(found.group(1))
    raise ValueError(f"{url}: no council name")


def kind_of(url: str, text: str, filled: int, size: int = 5) -> tuple[str, bool]:
    """The election kind, and whether it was inferred from the seats the notice fills rather than named."""
    where = (url + " " + text[:600]).lower()
    if re.search(r"by[\s-]*election|by[\s-]*results", where):
        return "by_election", False
    if re.search(r"general[\s-]*election", where):
        return "general", False
    return ("general" if filled >= size else "by_election"), True


def elected_names(sentence: str, body: list[str]) -> list[str]:
    inline = re.split(r",\s*(?:and\s+)?|\s+and\s+", re.sub(r"\s+", " ", sentence))
    names = [person(name) for name in inline if person(name)]
    return names or [person(row) for row in body]


def unopposed(url: str, text: str) -> Notice:
    council, day, ward = UNOPPOSED[url.removeprefix(SITE)]
    rows = lines(text)
    at = next(i for i, row in enumerate(rows) if re.search(r"duly elected", row, re.IGNORECASE))
    names: list[str] = []
    flat = " ".join(rows)
    declared = re.search(r"withdrawal,\s+(.+?)\s+are declared duly elected", flat)
    if declared:
        names = elected_names(declared.group(1), [])
    else:
        for row in rows[at + 1 :]:
            if re.match(r"(?:Chief|CHIEF)", row):
                break
            found = re.match(r"^\d+\s*[.)]\s*(.+?)(?:\s+-\s+.*)?$", row)
            if found:
                names.append(person(found.group(1)))
    names = [name for name in names if name]
    if not names:
        raise ValueError(f"{url}: no unopposed candidates")
    contest = Contest(ward, [Candidate(name, None, True) for name in names], len(names), tacit=True)
    return Notice(url, council, day, "general", [contest], False, [url])


def tacit(url: str, text: str) -> Notice | None:
    rows = lines(text)
    flat = " ".join(rows)
    found = re.search(
        r"on\s+(?:\w+day,?\s+)?(?P<day>(?:%s)\s+\d{1,2},?\s+\d{4}),?\s+(?P<who>.+?)\s+(?:was|were)\s+(?:declared\s+)?(?:duly\s+)?elected"
        % "|".join(MONTHS),
        flat,
        re.IGNORECASE,
    )
    if not found:
        return None
    who = found.group("who")
    body: list[str] = []
    if re.match(r"the\s+under-?mentioned candidates$", who, re.IGNORECASE):
        start = next(i for i, row in enumerate(rows) if re.search(r"duly elected", row, re.IGNORECASE))
        body = [row for row in rows[start + 1 :] if not row.lower().startswith("chief elec")]
        names = [person(row) for row in body]
    else:
        names = elected_names(who, [])
    day = parse_date(found.group("day"))
    ward = re.search(r"\(WARD (IV|III|II|I)\)", flat, re.IGNORECASE)
    council = council_of(flat, url)
    contest = Contest(ward_number(ward.group(1)) if ward else None, [Candidate(n, None, True) for n in names])
    contest.seats = len(names)
    contest.tacit = True
    if day is None or not names:
        raise ValueError(f"{url}: unreadable declaration")
    kind, inferred = ("general", False) if council == "chief" else kind_of(url, flat, len(names), SIZE.get(council, 5))
    return Notice(url, council, day, kind, [contest], inferred, [url])


def whole(text: str) -> int:
    return int(text.replace(",", ""))


def gaps(contest: Contest, joined: bool) -> int:
    """Candidates a printed list numbers but does not show: a list should run 1, 2, 3 without a break."""
    missing = 0
    lists: list[list[int]] = [[]]
    for c in contest.candidates:
        if c.rank is None:
            continue
        if lists[-1] and c.rank == 1 and not joined:
            lists.append([])
        lists[-1].append(c.rank)
    for ranks in lists:
        if ranks:
            missing += max(ranks) - len(ranks)
    return missing


def tally(contest: Contest, text: str) -> None:
    """Read the turnout, the split by sex, and the rejected ballots from the lines that follow a contest's candidates."""
    labelled = re.search(r"voter turnout\s*[-–:]?\s*\(?(\d[\d,]*)\)?\s*or\s*([\d.]+)\s*%", text, re.IGNORECASE)
    after = r"\b{0}\s*[-–:]?\s*\(?(\d[\d,]*)\)?\s*or\s*[\d.]+\s*%"
    before = r"\(?(\d[\d,]*)\)?\s*or\s*[\d.]+\s*%+\s*(?:were\s+)?{0}\b"
    pattern = after if labelled else before
    male = re.search(pattern.format("males?"), text, re.IGNORECASE)
    female = re.search(pattern.format("females?"), text, re.IGNORECASE)
    contest.male = whole(male.group(1)) if male else None
    contest.female = whole(female.group(1)) if female else None
    shares = [
        re.search(pattern.replace(r"[\d.]+", r"([\d.]+)").format(sex), text, re.IGNORECASE)
        for sex in ("males?", "females?")
    ]
    contest.shares = (
        ("", shares[0].group(2), shares[1].group(2)) if shares[0] and shares[1] and male and female else None
    )
    stated = re.findall(r"\(?(\d[\d,]*)\)?\s*or\s*([\d.]+)\s*%+\s*of\s+(?:the\s+)?electorate", text, re.IGNORECASE)
    if labelled:
        contest.cast, contest.percent = whole(labelled.group(1)), labelled.group(2)
        if stated:
            contest.total = (whole(stated[0][0]), stated[0][1])
    elif stated:
        contest.cast, contest.percent = whole(stated[0][0]), stated[0][1]
    if contest.shares:
        contest.shares = (contest.percent or "", contest.shares[1], contest.shares[2])
    rejected = re.search(r"there (?:was|were)\s+(?:[\w\s-]*?\(\s*(\d+)\s*\)|(\d+))\s*rejected", text, re.IGNORECASE)
    if rejected:
        contest.rejected = int(rejected.group(1) or rejected.group(2))


def chief_table(rows: list[str]) -> Contest:
    """The 2014 Carib Chief notice: a table of names and votes whose last row also holds the rejected and total."""
    contest = Contest()
    start = next(i for i, row in enumerate(rows) if row.startswith("Name of Candidate"))
    for row in rows[start + 1 :]:
        found = re.match(r"^(?P<name>[^\d]+?)\s+(?P<votes>\d+)(?:\s+(?P<rejected>\d+)\s+(?P<total>\d+))?$", row)
        if not found:
            break
        contest.candidates.append(Candidate(person(found.group("name")), int(found.group("votes"))))
        if found.group("rejected"):
            contest.rejected = int(found.group("rejected"))
    elected = re.search(r"elected as Chief is (.+)", " ".join(rows))
    if not elected:
        raise ValueError("Chief table without a winner")
    name = person(elected.group(1))
    for c in contest.candidates:
        c.elected = c.name == name
    contest.seats = 1
    contest.tail = rows[start + 1 :]
    return contest


def counted(url: str, text: str) -> Notice | None:
    rows = lines(text)
    flat = " ".join(rows)
    if not re.search(r"\bvotes?\b", flat, re.IGNORECASE) or not re.search(
        r"electorate|voter turnout", flat, re.IGNORECASE
    ):
        return None
    if re.search(r"Name of Candidate No\. of votes polled", flat):
        contests = [chief_table(rows)]
        day = parse_date(flat)
        if day is None:
            raise ValueError(f"{url}: no date")
        for c in contests:
            tally(c, " ".join(c.tail))
        return Notice(url, "chief", day, "general", contests, False, [url])
    contests: list[Contest] = []
    current = Contest()
    preface: list[str] = []
    group = "elected"
    for row in rows:
        ward = WARD_LINE.match(row)
        vote = VOTE_LINE.match(row)
        tie = TIED.match(row)
        if ward:
            current = Contest(ward_number(ward.group(1)))
            contests.append(current)
            group = "elected"
        elif vote and not re.search(
            r"\b(?:received|polled|were|was|who|election|council|notice|results?)\b", vote.group("name"), re.IGNORECASE
        ):
            if not contests:
                contests.append(current)
            rank = int(vote.group("rank")) if vote.group("rank") else None
            current.candidates.append(
                Candidate(
                    person(vote.group("name")),
                    whole(vote.group("votes")),
                    group == "elected",
                    rank=rank,
                )
            )
        elif OTHERS.match(row):
            group = "others"
            loser = re.match(r"^the other candidates?,?\s+(?P<n>.+?),?\s+received (?P<v>\d+) votes", row, re.IGNORECASE)
            if loser:
                if not contests:
                    contests.append(current)
                current.candidates.append(Candidate(person(loser.group("n")), int(loser.group("v"))))
            else:
                current.tail.append(row)
        elif tie:
            for name in re.split(r"\s+and\s+|,\s*", tie.group("names")):
                current.candidates.append(Candidate(person(name), int(tie.group("votes")), False, True))
            current.tail.append(row)
        elif not contests and not current.candidates:
            preface.append(row)
        else:
            current.tail.append(row)
    if not contests:
        contests.append(current)
    lone = re.search(
        r"(?P<name>(?:Mrs?\.|Ms\.?|Miss)?\s*[A-Z][\w.'\- ]+?)\s+(?:who received|of [\w ]+ in the Kalinago Territory was elected Kalinago Chief.*?polled)"
        r"[^()]*?\(?(?P<votes>\d[\d,]*)\)?\s+votes",
        " ".join(preface),
    )
    given = re.search(
        r"(?P<name>[A-Z][\w.' \-]+?)\s+was elected to serve (?:as a member of|on) .*?received (?P<votes>\d+) votes",
        " ".join(preface),
    )
    if lone:
        contests[0].candidates.insert(0, Candidate(person(lone.group("name")), count(lone.group("votes")), True))
    elif given:
        contests[0].candidates.insert(0, Candidate(person(given.group("name")), int(given.group("votes")), True))
    day = parse_date(" ".join(preface) or flat) or parse_date(flat)
    if day is None:
        raise ValueError(f"{url}: no date")
    council = council_of(flat, url)
    for c in contests:
        if not re.search(r"elected", flat, re.IGNORECASE) and not any(OTHERS.match(row) for row in rows):
            top = max(x.votes or 0 for x in c.candidates)
            for x in c.candidates:
                x.elected, x.tied = False, x.votes == top
            c.seats = 0
        words = re.search(r"\b(?P<n>%s)\s+elected\s+members" % "|".join(WORDS), " ".join(c.tail), re.IGNORECASE)
        c.seats = WORDS[words.group("n").lower()] if words else sum(1 for x in c.candidates if x.elected)
        c.stated = bool(words)
        if words:
            ranked = [x for x in c.candidates if not x.tied]
            for i, x in enumerate(ranked):
                x.elected = i < c.seats
        tally(c, " ".join((preface if len(contests) == 1 else []) + c.tail))
        c.gaps = gaps(c, bool(words))
        if not c.candidates:
            raise ValueError(f"{url}: ward {c.ward} has no candidates")
    if council in ("roseau", "portsmouth") and any(c.ward is None for c in contests):
        raise ValueError(f"{url}: a council with wards but a contest without one")
    filled = sum((c.seats or 0) + sum(x.tied for x in c.candidates) + c.gaps for c in contests)
    kind, inferred = ("general", False) if council == "chief" else kind_of(url, flat, filled, SIZE.get(council, 5))
    return Notice(url, council, day, kind, contests, inferred, [url])


def read(link: str) -> Notice | None:
    text = fetch_text(link)
    path = link.removeprefix(SITE)
    if path in UNOPPOSED:
        return unopposed(link, text)
    flat = clean(re.sub(r"<[^>]+>", " ", article(text)))
    if re.search(
        r"returning officer|presiding officer|grant of (?:a )?poll|nominated to contest|nomination day|appointed election"
        r"|election officers|house of assembly|constituency|\bwrit\b",
        flat,
        re.IGNORECASE,
    ):
        return None
    if re.search(r"\bvotes?\b", flat, re.IGNORECASE) and re.search(r"electorate|voter turnout", flat, re.IGNORECASE):
        return counted(link, text)
    if re.search(r"duly elected|was elected|were elected", flat, re.IGNORECASE):
        return tacit(link, text)
    return None


# Councils -----------------------------------------------------------------------------------------------------------

STOP = {"and", "the", "village", "council"}
SPELLINGS = {"coulibistire": "coulibistrie", "tetemorne": "tete morne", "fondcaine": "fond canie", "caribe": "carib"}
WARDED = {"roseau": "Roseau City Council", "portsmouth": "Portsmouth Town Council"}
OTHER_COUNCILS = {
    **WARDED,
    "canefield": "Canefield Urban Council",
    "kalinago": "Kalinago Council",
}


def words(name: str) -> set[str]:
    text = name.lower().replace("'", "").replace("\u2019", "")
    for wrong, right in SPELLINGS.items():
        text = text.replace(wrong, right)
    return {w for w in re.findall(r"[a-z]+", text) if w not in STOP}


def same(a: set[str], b: set[str]) -> bool:
    return all(any(SequenceMatcher(None, x, y).ratio() >= 0.85 for y in b) for x in a)


@dataclass(frozen=True)
class Council:
    key: str  # a slug that is stable across spellings
    name: str  # the body's name
    area: str  # the area its candidates stand in, at large
    kind: str  # village, city, town, urban, kalinago, or chief


def village_names() -> dict[str, str]:
    """The Office's list of village councils: each council's name as the list prints it."""
    text = fetch_text(LIST_PAGE)
    names: dict[str, str] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", article(text), flags=re.DOTALL):
        cells = [clean(re.sub(r"<[^>]+>", " ", c)) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)]
        if (
            len(cells) >= 3
            and cells[0]
            and not re.search(r"Town Council|City Council|Urban Council|^Carib Council", cells[0])
        ):
            name = re.sub(r"\s*/\s*", "/", cells[0])
            names[slug(name)] = name
    if len(names) < 37:
        raise ValueError(f"the list of village councils holds {len(names)} names")
    return names


def council(label: str, listed: dict[str, str]) -> Council:
    if label in OTHER_COUNCILS:
        name = OTHER_COUNCILS[label]
        return Council(label, name, {"canefield": "Canefield", "kalinago": "Kalinago Territory"}.get(label, ""), label)
    if label == "chief":
        return Council("chief", "Kalinago Chief", "Kalinago Territory", "chief")
    page = words(label)
    best = [name for name in listed.values() if same(page, words(name)) or same(words(name), page)]
    best.sort(key=lambda n: len(words(n) ^ page))
    name = best[0] if best else re.sub(r"\s*/\s*", "/", label)
    return Council(slug(name), f"{name} Village Council", name, "village")


# Elections ----------------------------------------------------------------------------------------------------------


@dataclass
class Election:
    council: Council
    day: date
    kind: str
    contests: dict[int | None, Contest]
    urls: list[str]
    inferred: bool = False

    @property
    def slug(self) -> str:
        return f"{self.day.isoformat()}-{self.council.key}-{'general' if self.kind == 'general' else 'by-election'}"


def notice_urls() -> list[str]:
    return sorted(
        url
        for url in raw.keys(SOURCE)
        if re.fullmatch(rf"{re.escape(SITE)}/(?:news|village-council-elections)/[^?#/]+", url) and url != LIST_PAGE
    )


def nominations(listed: dict[str, str]) -> dict[tuple[str, date], str]:
    """The kind of election each officer notice announces, by council and nomination day, which settles the kind of a
    notice that only says who was declared elected on that day."""
    found: dict[tuple[str, date], str] = {}
    for link in notice_urls():
        flat = clean(re.sub(r"<[^>]+>", " ", article(fetch_text(link))))
        day = re.search(r"nomination day is the (\d+)(?:st|nd|rd|th) day of (\w+),? (\d{4})", flat, re.IGNORECASE)
        label = re.search(r"\b(general election|by-election)\b", flat, re.IGNORECASE)
        if not day or not label or day.group(2).lower() not in MONTHS:
            continue
        try:
            who = council(council_of(flat, link), listed).key
        except ValueError:
            continue
        found[who, date(int(day.group(3)), MONTHS[day.group(2).lower()], int(day.group(1)))] = (
            "by_election" if label.group(1).lower() == "by-election" else "general"
        )
    return found


def same_contest(a: Contest, b: Contest) -> bool:
    return [(x.name, x.votes, x.elected, x.tied) for x in a.candidates] == [
        (x.name, x.votes, x.elected, x.tied) for x in b.candidates
    ]


@functools.cache
def elections() -> dict[str, Election]:
    listed = village_names()
    nominated = nominations(listed)
    grouped: dict[tuple[str, date], list[Notice]] = {}
    for link in notice_urls():
        notice = read(link)
        if notice is not None:
            grouped.setdefault((council(notice.council, listed).key, notice.day), []).append(notice)
    out: dict[str, Election] = {}
    for (who, day), notices in sorted(grouped.items()):
        body = council(notices[0].council, listed)
        kinds = {n.kind for n in notices if not n.inferred}
        if len(kinds) > 1:
            raise ValueError(f"{who} {day}: notices disagree on the kind of election")
        inferred = not kinds
        kind = kinds.pop() if kinds else nominated.get((who, day)) or notices[0].kind
        inferred = inferred and (who, day) not in nominated
        contests: dict[int | None, Contest] = {}
        urls: list[str] = []
        for notice in notices:
            urls += notice.urls
            for c in notice.contests:
                if c.ward in contests and not same_contest(contests[c.ward], c):
                    raise ValueError(f"{who} {day}: two notices give different results for ward {c.ward}")
                contests.setdefault(c.ward, c)
        election = Election(
            body, day, kind, dict(sorted(contests.items(), key=lambda i: i[0] or 0)), sorted(urls), inferred
        )
        out[election.slug] = election
    return out


# Packages --------------------------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{slug}" for slug in elections())]


def parse() -> None:
    registry().write()
    for election in elections().values():
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def area_key(who: Council, ward: int | None) -> str:
    if who.kind in ("kalinago", "chief"):
        return key("area", "kalinago")
    return key("area", who.key, f"ward-{ward}" if ward else "council")


def body_key(who: Council) -> str:
    return key("body", "kalinago" if who.kind in ("kalinago", "chief") else who.key)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The village councils, Roseau City Council, Portsmouth Town Council, Canefield Urban Council, and the "
        "Kalinago Chief and Council, with the areas they cover, from the Electoral Office's published notices",
    )
    p.external.add(DOMINICA)
    p.add(
        "source",
        id=DEO,
        kind="official_body",
        publisher="Electoral Office of the Commonwealth of Dominica",
        title="Village council elections and news: notices of results",
        url=f"{SITE}/village-council-elections",
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Office of the Commonwealth of Dominica",
    )
    urls = {LIST_PAGE, *(url for e in elections().values() for url in e.urls)}
    for url in sorted(urls):
        p.artifact(raw.cached(SOURCE, url), DEO)
    for code, kind, rank, noun in (
        (AREA_SCHEME, "electoral_district", 1, "council area"),
        (UNIT_SCHEME, "synthetic", 2, "contest count"),
    ):
        p.add("scheme", code=code, polity_id=DOMINICA, kind=kind, rank=rank, noun=noun)
    for id, formula, note in (
        (
            FPTP,
            "fptp",
            "One seat is filled, and the candidate with the most votes is elected.",
        ),
        (
            BLOCK,
            "block",
            "Each voter may vote for as many candidates as there are seats, and the candidates with the most votes fill them.",
        ),
    ):
        p.add("electoral_system", id=id, polity_id=DOMINICA, rules={"formula": formula}, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    places: dict[str, tuple[str, str]] = {}
    bodies: dict[str, Council] = {}
    for e in elections().values():
        bodies[body_key(e.council)] = e.council
        for ward in e.contests:
            who = e.council
            if who.kind in WARDED:
                places[area_key(who, ward)] = (
                    f"{who.area or who.name.removesuffix(' City Council').removesuffix(' Town Council')} Ward {ward}",
                    "",
                )
            else:
                places[area_key(who, ward)] = (who.area, "")
    for id, (name, _) in sorted(places.items(), key=lambda i: i[1]):
        p.add("area", id=id, scheme_code=AREA_SCHEME)
        p.add("area_code", area_id=id, code=slug(name))
        p.name(id, name, "en", "official")
    for id, who in sorted(bodies.items(), key=lambda i: i[1].name):
        if who.kind == "chief":
            continue
        p.add("body", id=id, polity_id=DOMINICA, kind="council")
        p.name(id, who.name, "en", "official")
        if who.kind == "kalinago":
            p.name(id, "Carib Council", "en", "historical", False)
    if any(e.council.kind == "chief" for e in elections().values()):
        p.add("office", id=CHIEF_OFFICE, polity_id=DOMINICA, kind="other")
        p.name(CHIEF_OFFICE, "Kalinago Chief", "en", "official")
        p.name(CHIEF_OFFICE, "Carib Chief", "en", "historical", False)
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
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


def note(p: Package, entity: str, kind: str, label: str, text: str) -> None:
    p.add("note", id=key("note", entity, label), entity_id=entity, kind=kind, body_md=text, lang="en")


def caveats(c: Contest) -> list[str]:
    found: list[str] = []
    tied = [x for x in c.candidates if x.tied]
    if tied:
        names = " and ".join(x.name for x in tied)
        found.append(
            f"The notice reports {names} tied with {tied[0].votes} votes each"
            + (" and declares no candidate elected." if not c.seats else " for a seat it does not declare filled.")
            + " It does not say how the tie was settled."
        )
    if c.gaps:
        found.append(
            "The notice's numbered list of candidates skips "
            + ("a number" if c.gaps == 1 else f"{c.gaps} numbers")
            + ", so a line is missing from the page. Only the candidates it prints are stored."
        )
    if c.male is not None and c.female is not None and c.cast is not None and c.male + c.female != c.cast:
        found.append(
            f"The notice's split by sex adds up to {c.male + c.female:,}, not the {c.cast:,} voters it reports."
        )
    if c.cast is not None and not c.tacit and c.candidates:
        votes = sum(x.votes or 0 for x in c.candidates)
        rejected = c.rejected or 0
        seats = c.seats or 1
        if seats == 1 and not tied and votes + rejected != c.cast:
            found.append(
                f"The votes for candidates ({votes:,})"
                + (f" and the rejected ballots ({rejected:,})" if c.rejected is not None else "")
                + f" do not add up to the {c.cast:,} voters the notice reports."
            )
        elif votes > max(seats, 1) * (c.cast - rejected):
            found.append(
                f"The votes for candidates ({votes:,}) are more than the {seats} seats allow for {c.cast - rejected:,} "
                "valid ballots."
            )
    return found


def context(c: Contest) -> str | None:
    if c.cast is None:
        return None
    text = f"The notice reports {c.cast:,} voters"
    if c.shares and c.shares[0]:
        text += f", {c.shares[0]}% of the electorate"
    if c.male is not None and c.female is not None:
        text += f", of whom {c.male:,}"
        text += f" ({c.shares[1]}%)" if c.shares else ""
        text += f" were male and {c.female:,}"
        text += f" ({c.shares[2]}%)" if c.shares else ""
        text += " were female"
    text += "."
    if c.total is not None:
        text += f" For all the wards together it reports {c.total[0]:,} voters, {c.total[1]}% of the electorate."
    return text


def build(e: Election) -> Package:
    who = e.council
    label = "general election" if e.kind == "general" else "by-election"
    p = Package(
        f"{SOURCE}/{e.slug}",
        SOURCE,
        f"The {who.name} {label} of {e.day.isoformat()}, with each candidate's votes, the turnout, and the rejected "
        "ballots where the Electoral Office's notice gives them",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(DOMINICA)
    for url in e.urls:
        p.uses(raw.cached(SOURCE, url))
    election = key("election", e.slug)
    p.add(
        "election",
        id=election,
        polity_id=DOMINICA,
        kind=e.kind,
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        status="held",
    )
    title = (
        f"{e.day.year} {who.name} general election"
        if e.kind == "general"
        else f"{who.name} by-election, {e.day.day} {e.day:%B %Y}"
    )
    p.name(election, title, "en", "official")
    p.add("slug", path=f"dm/{e.slug}", entity_id=election, is_current=True)
    if e.inferred:
        note(
            p,
            election,
            "caveat",
            "kind",
            f"The Electoral Office's notice does not say whether this was a general election or a by-election. It is "
            f"stored as a {label} because of the number of seats it fills.",
        )
    for ward, c in e.contests.items():
        code = "chief" if who.kind == "chief" else f"ward-{ward}" if ward else "council"
        name = who.name if not ward else f"{who.name}, Ward {ward}"
        contest = key("contest", e.slug, code)
        ties = any(x.tied for x in c.candidates)
        magnitude = (
            len(c.candidates) if c.tacit else (c.seats if c.seats and not ties and (c.stated or not c.gaps) else None)
        )
        single = who.kind == "chief" or magnitude == 1
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office" if who.kind == "chief" else "body_seats",
            office_id=CHIEF_OFFICE if who.kind == "chief" else None,
            body_id=None if who.kind == "chief" else body_key(who),
            district_area_id=area_key(who, ward),
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=FPTP if single else BLOCK,
            default_result_kind="tacit" if c.tacit else "counted",
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"dm/{e.slug}/{code}", entity_id=contest, is_current=True)
        options: list[str] = []
        for position, x in enumerate(c.candidates):
            option = key("option", e.slug, code, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position + 1,
                ballot_label=x.name,
            )
            who_id = key("person", e.slug, code, position)
            p.add("person", id=who_id)
            p.name(who_id, x.name, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", e.slug, code, position),
                person_id=who_id,
                option_id=option,
                role="head",
                ballot_name=x.name,
            )
        rs = key("result_set", e.slug, code)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="tacit" if c.tacit else "counted",
            source_id=DEO,
            status="active",
            is_default=True,
            completeness=None if c.tacit else 1.0,
        )
        if c.tacit:
            note(
                p,
                rs,
                "methodology",
                "method",
                "The candidates named were declared elected without a poll, as the Electoral Office's notice says. "
                "It lists no votes.",
            )
        else:
            note(p, rs, "methodology", "method", METHOD if not single else METHOD_SINGLE)
            if context(c):
                note(p, rs, "context", "turnout", context(c) or "")
            for n, text in enumerate(caveats(c)):
                note(p, rs, "caveat", f"caveat-{n}", text)
            unit = key("unit", e.slug, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=DOMINICA,
                scheme_code=UNIT_SCHEME,
                code=f"{e.slug}:{code}",
                area_relation="equals",
                area_id=area_key(who, ward),
                kind="standard",
            )
            p.name(unit, name, "en", "official")
            for position, x in enumerate(c.candidates):
                put(p, rs, unit, options[position], VOTES, x.votes)
            put(p, rs, unit, None, CAST, c.cast)
            put(p, rs, unit, None, INVALID, c.rejected)
        for position, x in enumerate(c.candidates):
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=options[position],
                kind="elected" if x.elected or c.tacit else "tied" if x.tied else "lost",
                seats=1 if x.elected or c.tacit else None,
            )
    return p
