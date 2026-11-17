"""Iraq's Council of Representatives election of 11 November 2025, from the Independent High Electoral Commission.

The Commission counts each governorate as one constituency and publishes one total for it. The governorates and the
Council itself are those of the 2021 election's pipeline (`iq.ihec`), which this pipeline's packages depend on.
"""

import itertools
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from urllib.parse import quote
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.bt.pdfwords import LINE_GAP, Word, pages
from ea.sources.iq import ihec

SOURCE = "iq.ihec_cor_2025"
IRAQ = str(uuid5(package.NAMESPACE, "polity\x1fIQ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIRQ"))
DAY = date(2025, 11, 11)
UPLOADS = "https://ihec.iq/wp-content/uploads/"
FINAL_NAMES = UPLOADS + "2025/12/Final-names-2025.pdf"
CANDIDATES = UPLOADS + "2025/11/" + quote("أصوات-المرشحين-الكلي") + ".pdf"
PARTIES = UPLOADS + "2025/11/" + quote("أصوات-الأحزاب-الكلية") + ".pdf"
QUOTA = UPLOADS + "2025/11/" + quote("أصوات-الكوتا") + ".pdf"
# The Commission's first publication of the component candidates' votes, whose text comes out right.
COMPONENT_NAMES = UPLOADS + "2025/11/" + quote("المكونات-للنشر") + ".pdf"
# The register of candidates published before the election, which says where each component candidate ran.
REGISTER = UPLOADS + "2025/10/" + quote("اسماء-المرشحين") + ".pdf"
# The Commission's first publication of the candidates' votes. Its text is the only one whose Arabic letters come out
# right, so the names are read from it; the votes are those of the later, final files.
NAMES = UPLOADS + "2025/11/" + quote("أصوات-المرشحين-للنشر-الكلي") + ".pdf"
# The governorate summaries by the Commission's governorate number, as the file stem the Commission gave each.
GOVERNORATE_FILES = {
    "1": "بغداد",
    "12": "نينوى-1",
    "35": "البصرة-1",
    "33": "ذي-قار-1",
    "22": "الانبار",
    "5": "أربيل-1",
    "6": "السليمانية-1",
    "4": "دهوك-1",
    "14": "كركوك-1",
    "21": "ديالى-1",
    "27": "صلاح-الدين-1",
    "24": "بابل-1",
    "25": "كربلاء-1",
    "28": "النجف-1",
    "31": "القادسية-1",
    "32": "المثنى-1",
    "34": "ميسان-1",
    "26": "واسط-1",
}


def summary_url(stem: str) -> str:
    return UPLOADS + "2025/11/" + quote(stem) + ".pdf"


def fetch() -> None:
    for url in (FINAL_NAMES, CANDIDATES, PARTIES, QUOTA, COMPONENT_NAMES, REGISTER, NAMES):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for stem in GOVERNORATE_FILES.values():
        raw.fetch(SOURCE, summary_url(stem), suffix=".pdf")


def arabic(text: str) -> str:
    """A word of the Commission's PDFs in reading order: they store Arabic in visual order, with presentation forms."""
    return text if text.isascii() else unicodedata.normalize("NFKC", text[::-1])


Row = dict[str, list[Word]]


def band(
    words: list[Word],
    columns: Sequence[tuple[str, float, float]],
    anchor: str,
    header: float,
    foot: float = 10_000.0,
    reach: float = 10_000.0,
) -> list[Row]:
    """The rows of a table whose cells wrap over several lines. A row's anchor cell holds one word on the row's middle
    line; every other word belongs to the row whose anchor is nearest to it vertically."""

    def cell(word: Word) -> str | None:
        center = (word.x + word.x1) / 2
        return next((name for name, low, high in columns if low <= center < high), None)

    body = [word for word in words if header < word.y < foot]
    anchors = sorted(word.y for word in body if cell(word) == anchor)
    if not anchors:
        return []
    found: list[Row] = [defaultdict(list) for _ in anchors]
    for word in body:
        name = cell(word)
        if name is not None:
            nearest = min(range(len(anchors)), key=lambda k: abs(anchors[k] - word.y))
            if abs(anchors[nearest] - word.y) <= reach:
                found[nearest][name].append(word)
    return found


def rows(path: Path, columns: Sequence[tuple[str, float, float]], anchor: str, header: float) -> list[Row]:
    found: list[Row] = []
    for _, words in pages(path):
        foot = min((word.y for word in words if word.text == "Page"), default=10_000.0)
        found += band(words, columns, anchor, header, foot - 1)
    return found


def text(words: list[Word]) -> str:
    """A cell's words in reading order: its lines from top to bottom, each from right to left."""
    lines: list[list[Word]] = []
    top = -100.0
    for word in sorted(words, key=lambda word: word.y):
        if lines and word.y - top <= LINE_GAP:
            lines[-1].append(word)
        else:
            lines.append([word])
            top = word.y
    return " ".join(arabic(word.text) for line in lines for word in sorted(line, key=lambda word: -word.x))


def number(words: list[Word]) -> int:
    return int("".join(word.text for word in words).replace(",", ""))


# The Commission's governorate numbers: the English name, the Arabic name, and the ISO 3166-2 code that geoBoundaries
# gives the governorate's shape.
GOVERNORATES = {
    "1": ("Baghdad", "بغداد", "IQ-BG"),
    "4": ("Dohuk", "دهوك", "IQ-DA"),
    "5": ("Erbil", "أربيل", "IQ-AR"),
    "6": ("Sulaymaniyah", "السليمانية", "IQ-SU"),
    "12": ("Nineveh", "نينوى", "IQ-NI"),
    "14": ("Kirkuk", "كركوك", "IQ-KI"),
    "21": ("Diyala", "ديالى", "IQ-DI"),
    "22": ("Anbar", "الأنبار", "IQ-AN"),
    "24": ("Babil", "بابل", "IQ-BB"),
    "25": ("Karbala", "كربلاء", "IQ-KA"),
    "26": ("Wasit", "واسط", "IQ-WA"),
    "27": ("Salah al-Din", "صلاح الدين", "IQ-SD"),
    "28": ("Najaf", "النجف", "IQ-NA"),
    "31": ("Qadisiyyah", "القادسية", "IQ-QA"),
    "32": ("Muthanna", "المثنى", "IQ-MU"),
    "33": ("Dhi Qar", "ذي قار", "IQ-DQ"),
    "34": ("Maysan", "ميسان", "IQ-MA"),
    "35": ("Basra", "البصرة", "IQ-BA"),
}
GENDER = {"ذكر": "male", "انثى": "female"}


def fold(name: str) -> str:
    """A name with its tatweel, spaces, and letter variants folded and its letters sorted, which matches the
    Commission's spellings of a governorate however a table swaps the letters around a ligature."""
    value = re.sub("[ـ\\s]", "", name).translate(str.maketrans("أإآىیةي", "اااييهي"))
    return "".join(sorted(value.replace("ا", "")))


def governorate(name: str) -> str:
    """The Commission's number for a governorate the way a table prints its name."""
    found = [code for code, (_, arabic, _) in GOVERNORATES.items() if fold(arabic) == fold(name)]
    if len(found) != 1:
        raise ValueError(f"unknown governorate {name!r}")
    return found[0]


@dataclass(frozen=True)
class Entry:
    """A candidate as the Commission's first publication prints them."""

    governorate: str
    entity: str
    seat: int
    name: str
    gender: str
    entity_name: str


def names() -> dict[tuple[str, str, int], Entry]:
    columns = [
        ("votes", 0, 100),
        ("gender", 100, 145),
        ("name", 145, 362),
        ("seat", 362, 420),
        ("entity", 420, 610),
        ("lottery", 610, 665),
        ("gov", 665, 800),
    ]
    found: dict[tuple[str, str, int], Entry] = {}
    for row in rows(raw.cached(SOURCE, NAMES).path, columns, "lottery", 150):
        code = governorate(text(row["gov"]))
        entry = Entry(
            code,
            text(row["lottery"]),
            number(row["seat"]),
            text(row["name"]),
            GENDER[text(row["gender"])],
            text(row["entity"]),
        )
        key = (entry.governorate, entry.entity, entry.seat)
        if key in found:
            raise ValueError(f"{key} appears twice in the first publication")
        found[key] = entry
    return found


# The final files' names come out of the PDF with each lam-alef ligature swapped to alef-lam, which reads the same as
# the definite article. A word is repaired by trying each swap and keeping the spelling that the first publication
# prints for some candidate, the commonest if several do.
AMBIGUOUS = re.compile("(?=ال|أل|إل|آل)")


def spellings(word: str) -> list[str]:
    word = word.replace("هللا", "\0")
    places: list[int] = []
    for found in AMBIGUOUS.finditer(word):
        if not places or found.start() >= places[-1] + 2:
            places.append(found.start())
    options = []
    for mask in itertools.product((False, True), repeat=len(places)):
        letters = list(word)
        for place, swap in zip(places, mask, strict=True):
            if swap:
                letters[place], letters[place + 1] = letters[place + 1], letters[place]
        options.append("".join(letters))
    return [option.replace("\0", "الله") for option in options]


# Words that are written joined to the definite article, as in Abd al-Rahman, so that an alef-lam after them is the
# article and not a ligature.
JOINED = ("عبد", "نور", "جمال", "صلاح", "زين", "شمس", "سيف", "بدر", "فخر", "ضياء", "علاء", "كمال", "ابو", "ابي", "ذو")


def guessed(word: str) -> str:
    """A word that no candidate prints in the first publication: swap every alef-lam except one that starts the word or
    follows a word written joined to the article."""
    letters = list(word.replace("هللا", "\0"))
    plain = "".join(letters)
    for found in AMBIGUOUS.finditer(plain):
        place = found.start()
        if place and not any(plain[:place].endswith(joined) for joined in JOINED) and plain[place - 1] != "\0":
            letters[place], letters[place + 1] = letters[place + 1], letters[place]
    return "".join(letters).replace("\0", "الله")


def repaired(name: str, vocabulary: Counter[str]) -> str:
    words = []
    for word in name.split():
        seen = [option for option in spellings(word) if vocabulary[option]]
        words.append(max(seen, key=lambda option: vocabulary[option]) if seen else guessed(word))
    return " ".join(words)


@dataclass(frozen=True)
class Winner:
    serial: int
    seat: str
    votes: int
    governorate: str
    entity: str
    candidate: int
    gender: str


def winners() -> list[Winner]:
    columns = [
        ("seat", 0, 125),
        ("status", 125, 175),
        ("votes", 175, 250),
        ("gender", 250, 290),
        ("name", 290, 412),
        ("candidate", 412, 440),
        ("entity", 440, 600),
        ("lottery", 600, 650),
        ("gov", 650, 710),
        ("serial", 710, 900),
    ]
    found = []
    for row in rows(raw.cached(SOURCE, FINAL_NAMES).path, columns, "serial", 95):
        found.append(
            Winner(
                number(row["serial"]),
                text(row["seat"]),
                number(row["votes"]),
                governorate(text(row["gov"])),
                text(row["lottery"]),
                number(row["candidate"]),
                GENDER[text(row["gender"])],
            )
        )
    return found


@dataclass(frozen=True)
class Summary:
    """A governorate's summary: the turnout figures it prints and the lists that won seats."""

    centers: int
    stations: int
    registered: int
    voters: int
    male: int
    female: int
    valid: int
    invalid: int
    seats: int
    general: int
    quota: int
    women: int
    won: dict[str, tuple[int, int]]  # list number: seats, votes


def summary(code: str) -> Summary:
    path = raw.cached(SOURCE, summary_url(GOVERNORATE_FILES[code])).path
    figures: list[int] = []
    won: dict[str, tuple[int, int]] = {}
    columns = [("seats", 0, 110), ("votes", 110, 200), ("name", 200, 455), ("entity", 455, 500), ("serial", 500, 560)]
    for index, words in pages(path):
        cut = min((word.y for word in words if arabic(word.text) == "أسماء"), default=10_000.0)
        words = [word for word in words if word.y < cut - 1]
        if index == 1:
            header = min(word.y for word in words if arabic(word.text) == "الكيان")
            figures = [
                int(word.text.replace(",", ""))
                for word in sorted(words, key=lambda word: word.y)
                if word.y < header and word.x < 200 and re.fullmatch(r"[\d,]+", word.text)
            ]
            for row in band(words, columns, "serial", header + 12, reach=9):
                won[text(row["entity"])] = (number(row["seats"]), number(row["votes"]))
    figures = figures[1:]  # the title's year
    if len(figures) != 12 or sum(seats for seats, _ in won.values()) != figures[8]:
        raise ValueError(f"{code}: the summary's figures are {figures} and its lists hold {won}")
    return Summary(*figures, won)


def lists() -> dict[tuple[str, str], int]:
    """The final votes of every list, coalition, and individual candidate by governorate number and ballot number."""
    columns = [("votes", 0, 200), ("name", 200, 400), ("entity", 400, 440), ("gov", 440, 500), ("govno", 500, 600)]
    found: dict[tuple[str, str], int] = {}
    for row in rows(raw.cached(SOURCE, PARTIES).path, columns, "govno", 63):
        key = (text(row["govno"]), text(row["entity"]))
        if key in found:
            raise ValueError(f"{key} appears twice in the list votes")
        found[key] = number(row["votes"])
    return found


@dataclass(frozen=True)
class Vote:
    """A candidate's line in the Commission's final file."""

    governorate: str
    entity: str
    seat: int
    votes: int
    gender: str
    elected: bool
    name: str


def votes() -> list[Vote]:
    columns = [
        ("status", 0, 170),
        ("votes", 170, 245),
        ("gender", 245, 285),
        ("name", 285, 440),
        ("seat", 440, 500),
        ("entity", 500, 650),
        ("lottery", 650, 710),
        ("gov", 710, 775),
        ("govno", 775, 900),
    ]
    found = []
    for row in rows(raw.cached(SOURCE, CANDIDATES).path, columns, "govno", 88):
        found.append(
            Vote(
                text(row["govno"]),
                text(row["lottery"]),
                number(row["seat"]),
                number(row["votes"]),
                GENDER[text(row["gender"])],
                text(row["status"]) == "فائز",
                text(row["name"]),
            )
        )
    return found


@dataclass(frozen=True)
class Component:
    """A candidate for a seat reserved for a religious or ethnic component, with the final votes."""

    entity: str
    component: str
    votes: int
    name: str


def components(url: str) -> list[Component]:
    columns = [("votes", 0, 160), ("component", 160, 310), ("lottery", 310, 400), ("name", 400, 600)]
    return [
        Component(text(row["lottery"]), text(row["component"]), number(row["votes"]), text(row["name"]))
        for row in rows(raw.cached(SOURCE, url).path, columns, "lottery", 180)
    ]


def register() -> dict[str, tuple[str, str]]:
    """The governorate and gender of each individual candidate in the register published before the election, by ballot
    number."""
    found: dict[str, tuple[str, str]] = {}
    for _, words in pages(raw.cached(SOURCE, REGISTER).path):
        for anchor in words:
            if anchor.x > 80 or not anchor.text.isdigit():
                continue
            line = [word for word in words if abs(word.y - anchor.y) <= 4]
            if not any(arabic(word.text) == "فرد" for word in line):
                continue
            place = " ".join(arabic(word.text) for word in sorted(line, key=lambda word: -word.x) if 330 < word.x < 365)
            gender = " ".join(arabic(word.text) for word in line if 365 <= word.x < 400)
            found[anchor.text] = (governorate(place), "female" if "انث" in gender else "male")
    return found


# ---------------------------------------------------------------- assembly

COMPONENTS = {
    "المسيحيين": "Christian",
    "االيزيدي": "Yazidi",
    "الايزيدي": "Yazidi",
    "الشبك": "Shabak",
    "الصابئي": "Sabean-Mandaean",
    "الكورد الفيليين": "Faili Kurd",
}


def component(label: str) -> str:
    found = [english for arabic_name, english in COMPONENTS.items() if label.endswith(arabic_name)]
    if len(found) != 1:
        raise ValueError(f"unknown component {label!r}")
    return found[0]


@dataclass
class Candidate:
    seat: int
    name: str
    gender: str
    votes: int
    elected: bool = False
    announced: bool = False
    rebuilt: bool = False


@dataclass
class Option:
    """A ballot line of a governorate: a list or coalition with its candidates, or one individual candidate."""

    entity: str
    name: str
    votes: int
    seats: int = 0
    individual: bool = False
    component: str | None = None
    candidates: list[Candidate] | None = None


@dataclass
class Contest:
    code: str
    summary: Summary
    options: list[Option]


@cache
def contests() -> list[Contest]:
    first = names()
    vocabulary = Counter(word for entry in first.values() for word in entry.name.split())
    placed = register()
    totals = lists()
    final = {(w.governorate, w.entity, w.candidate): w for w in winners()}
    entity_names: dict[str, str] = {}
    for entry in first.values():
        if entity_names.setdefault(entry.entity, entry.entity_name) != entry.entity_name:
            raise ValueError(f"ballot number {entry.entity} has two names")
    by_list: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for vote in votes():
        entry = first.get((vote.governorate, vote.entity, vote.seat))
        name = entry.name if entry else repaired(vote.name, vocabulary)
        by_list[(vote.governorate, vote.entity)].append(
            Candidate(
                vote.seat,
                name,
                vote.gender,
                vote.votes,
                (vote.governorate, vote.entity, vote.seat) in final
                and final[(vote.governorate, vote.entity, vote.seat)].seat == "عام",
                vote.elected,
                entry is None,
            )
        )
    quota_names = {c.entity: c.name for c in components(COMPONENT_NAMES)}
    quota = {c.entity: c for c in components(QUOTA)}
    out = []
    for code in GOVERNORATES:
        options = []
        for (place, entity), total in sorted(totals.items(), key=lambda item: int(item[0][1])):
            if place != code:
                continue
            candidates = sorted(by_list[(place, entity)], key=lambda candidate: candidate.seat)
            individual = entity in placed
            name = candidates[0].name if individual else entity_names.get(entity)
            if name is None:
                raise ValueError(f"no name for list {entity}")
            options.append(
                Option(
                    entity,
                    name,
                    total,
                    sum(candidate.elected for candidate in candidates),
                    individual,
                    None,
                    candidates,
                )
            )
        for entity, (place, gender) in sorted(placed.items()):
            if place != code or entity not in quota:
                continue
            given = quota[entity]
            rebuilt = entity not in quota_names
            name = repaired(given.name, vocabulary) if rebuilt else quota_names[entity]
            won = (code, entity, 1) in final
            options.append(
                Option(
                    entity,
                    name,
                    given.votes,
                    int(won),
                    True,
                    component(given.component),
                    [Candidate(1, name, gender, given.votes, won, won, rebuilt)],
                )
            )
        out.append(Contest(code, summary(code), options))
    return out


# ---------------------------------------------------------------- packages


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


IHEC = key("source", "ihec")
SCHEME = ihec.GOVERNORATE_SCHEME
BODY = ihec.COUNCIL
SYSTEM = key("electoral_system", "council-of-representatives")
ELECTION = key("election", "cor", 2025)
PATH = "iq/2025-11-11-council-of-representatives"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, CANDIDATE_VOTES = 1, 2, 3, 4, 5, 10


def area(code: str) -> str:
    """The governorate's area, which the 2021 election's pipeline keys by its ISO 3166-2 code."""
    return ihec.governorate_area(GOVERNORATES[code][2])


def party(entity: str) -> str:
    return key("party", entity)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/cor-2025"]


def parse() -> None:
    for build in (registry(), election()):
        build.write()


def emit() -> None:
    package.run("load", packages())


SYSTEM_NOTE = (
    "The 329 seats of the Council of Representatives are filled in 18 multi-member constituencies, one for each "
    "governorate, from open lists. A list's seats follow the modified Sainte-Laguë method with a first divisor of "
    "1.7, and the candidates of a list take its seats in the order of their personal votes. Voters choose one list "
    "or individual candidate and may choose one candidate on it. At least a quarter of the seats are reserved for "
    "women, and nine seats are reserved for components: five for Christians (in Baghdad, Nineveh, Kirkuk, Erbil, and "
    "Dohuk), and one each for Yazidis and Shabaks (Nineveh), Sabean-Mandaeans (Baghdad), and Faili Kurds (Wasit). "
    "A component seat goes to the candidate of that component with the most votes in the governorate, and those "
    "candidates stand as individuals."
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system of Iraq's Council of Representatives from 2023, and the lists and coalitions that "
        "stood on 11 November 2025",
    )
    p.external.add(IRAQ)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=IRAQ,
        valid_from=date(2023, 1, 1),
        rules={"formula": "modified_sainte_lague", "firstDivisor": 1.7},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    lists = {option.entity: option.name for c in contests() for option in c.options if not option.individual}
    for entity, name in sorted(lists.items(), key=lambda item: int(item[0])):
        id = party(entity)
        p.add("party", id=id, kind="bloc" if name.startswith(("ائتلاف", "تحالف")) else "party", polity_id=IRAQ)
        p.name(id, name, "ar", "official")
    return p


METHOD = (
    "The Independent High Electoral Commission counted the ballots of each governorate and publishes one total for "
    "it, so the governorate is the finest unit. The registered voters, the voters, and the valid and invalid ballots "
    "are those of the Commission's governorate summary. A list's votes, and an individual candidate's, are those of "
    "its files of 17 November 2025, and each candidate's votes are those of the same day's list of all candidates. "
    "The seats and the winners are those of the Commission's final list of winners of 15 December 2025. Voters of "
    "the components with reserved seats vote for individual candidates, who stand in the governorate where they "
    "registered."
)
GENDER_LABEL = {"male": "male", "female": "female"}


def accounting(p: Package, contest: Contest, result_set: str, unit: str) -> None:
    s = contest.summary
    for measure, amount in ((REGISTERED, s.registered), (BALLOTS, s.voters), (VALID, s.valid), (INVALID, s.invalid)):
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=amount,
        )
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=amount)


def caveats(contest: Contest) -> list[str]:
    found = []
    total = sum(option.votes for option in contest.options)
    valid = contest.summary.valid
    if total != valid:
        found.append(
            f"The votes of all lists and individual candidates add up to {total:,}, and the Commission's summary "
            f"prints {valid:,} valid ballots, {abs(valid - total):,} {'more' if valid > total else 'fewer'}. The "
            "Commission does not explain the difference, and every figure is stored as printed."
        )
    moved = [c for option in contest.options for c in option.candidates or [] if c.elected != c.announced]
    if moved:
        found.append(
            f"The Commission's announcement of 17 November 2025 named {sum(c.announced for c in moved)} winners "
            f"here who are not on its final list of winners of 15 December 2025, which names {sum(c.elected for c in moved)} "
            "others. The final list is stored; the candidates' votes are those of 17 November."
        )
    rebuilt = sum(c.rebuilt for option in contest.options for c in option.candidates or [])
    if rebuilt:
        found.append(
            f"The names of {rebuilt} candidates are missing from the Commission's first publication, so they are read "
            "from its final files, where the Arabic letter pair lam and alef comes out swapped. They were repaired "
            "from the spellings of the other candidates, and a few may still be misspelled."
        )
    return found


def election() -> Package:
    p = Package(
        f"{SOURCE}/cor-2025",
        SOURCE,
        "The 11 November 2025 Council of Representatives election of Iraq by governorate, with list and candidate "
        "votes, from the Independent High Electoral Commission",
        depends=["iq.ihec/geo", "iq.ihec/registry", f"{SOURCE}/registry"],
    )
    p.external |= {IRAQ, COUNTRY}
    p.add(
        "source",
        id=IHEC,
        kind="official_body",
        publisher="Independent High Electoral Commission of Iraq",
        title="Results of the 2025 Council of Representatives election",
        url="https://ihec.iq/",
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent High Electoral Commission of Iraq",
    )
    for url in (FINAL_NAMES, CANDIDATES, PARTIES, QUOTA, COMPONENT_NAMES, REGISTER, NAMES):
        p.artifact(raw.cached(SOURCE, url), IHEC)
    for stem in GOVERNORATE_FILES.values():
        p.artifact(raw.cached(SOURCE, summary_url(stem)), IHEC)
    p.accept(
        "accounting.votes_valid",
        "the Commission's list and individual candidate votes of each governorate do not add up to the valid "
        "ballots in its governorate summary, in either direction; both are stored as printed",
    )
    p.add("election", id=ELECTION, polity_id=IRAQ, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=PATH, entity_id=ELECTION, is_current=True)
    p.name(ELECTION, "2025 Iraqi parliamentary election", "en", "official")
    p.name(ELECTION, "انتخابات مجلس النواب العراقي 2025", "ar", "native", False)
    for contest in contests():
        english, arabic_name, _ = GOVERNORATES[contest.code]
        id = key("contest", contest.code)
        p.add(
            "contest",
            id=id,
            election_id=ELECTION,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=area(contest.code),
            magnitude=contest.summary.seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(id, english, "en", "official")
        p.name(id, arabic_name, "ar", "native", False)
        p.add("slug", path=f"{PATH}/{changeset.slug(english)}", entity_id=id, is_current=True)
        result_set = key("result_set", contest.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=IHEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        unit = key("unit", contest.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IRAQ,
            scheme_code=SCHEME,
            code=f"{DAY}:{contest.code}",
            area_relation="equals",
            area_id=area(contest.code),
            kind="standard",
        )
        accounting(p, contest, result_set, unit)
        for body in caveats(contest):
            p.add(
                "note",
                id=key("note", result_set, body[:40]),
                entity_id=result_set,
                kind="caveat",
                body_md=body,
                lang="en",
            )

        def value(option: str, measure: int, amount: int, declared: bool) -> None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=amount,
            )
            if declared:
                p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=amount)

        for position, entry in enumerate(contest.options, 1):
            option = key("option", contest.code, entry.entity)
            label = entry.name if entry.individual else f"{entry.entity} – {entry.name}"
            if entry.component:
                label = f"{entry.name} ({entry.component} seat)"
            p.add(
                "contest_option",
                id=option,
                contest_id=id,
                kind="independents" if entry.individual else "list",
                party_id=None if entry.individual else party(entry.entity),
                ballot_position=int(entry.entity),
                ballot_label=label,
            )
            value(option, VOTES, entry.votes, True)
            if entry.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry.seats)
            for candidate in entry.candidates or []:
                tag = (contest.code, entry.entity, candidate.seat)
                person = key("person", *tag)
                p.add("person", id=person, gender=candidate.gender)
                p.name(person, candidate.name, "ar", "official")
                child = key("option", *tag)
                p.add(
                    "contest_option",
                    id=child,
                    contest_id=id,
                    kind="candidate",
                    parent_option_id=option,
                    party_id=None if entry.individual else party(entry.entity),
                    ballot_position=candidate.seat,
                    ballot_label=candidate.name,
                )
                value(child, CANDIDATE_VOTES, candidate.votes, False)
                candidacy = key("candidacy", *tag)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=child,
                    role="list_member",
                    list_position=None if entry.individual else candidate.seat,
                    ballot_name=candidate.name,
                    member_of_party_id=None if entry.individual else party(entry.entity),
                    is_independent=entry.individual,
                )
                if candidate.elected:
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
    return p
