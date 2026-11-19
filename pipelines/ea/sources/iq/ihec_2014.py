"""Iraq's Council of Representatives election of 30 April 2014, from the Independent High Electoral Commission.

`ea.sources.iq.ihec` builds this package alongside the 2018 and 2021 elections', on the same governorates and body.

The Commission published the 2014 results as PDFs on its file server, which it has since taken down. Two kinds
survive in archive copies: one table of every candidate's votes in each governorate (9,051 rows, with the component
seats' candidates at its end), and one summary per governorate of the valid votes, the seats, the lists that won
seats with their votes, and the winners. The tables come from Excel; the text layer stores a ligature's letters in
reverse, which `letters` puts back from the zero-width characters that mark them.
"""

import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

import pymupdf

from ea import raw
from ea.package import Package
from ea.sources.iq import ihec_2018
from ea.sources.iq.ihec import COUNCIL, COUNTRY, GOVERNORATE_SCHEME, IRAQ, SOURCE, governorate_area, key
from ea.sources.iq.text import Word

DAY = date(2014, 4, 30)
BASE = "browser://20261119-175332-3261ec/2014/"
CANDIDATES = f"{BASE}Complete.pdf"
CHRISTIANS = f"{BASE}maseeh.pdf"
PAGE = "https://ihec.iq/2014-results/"
PATH = f"iq/{DAY}-council-of-representatives"
KEY = f"{SOURCE}/cor-2014"
VOTES, VALID, CANDIDATE_VOTES = 1, 4, 10

# The governorate summaries, by the file the Commission published.
SUMMARIES = (
    "anbar (1)",
    "arbeel (1)",
    "babeel",
    "baghdad (1)",
    "basra (1)",
    "deyala (1)",
    "dhok (1)",
    "karbala (1)",
    "karkook",
    "maysan (1)",
    "mthana",
    "naenawa (1)",
    "najaf",
    "qadseya (1)",
    "salah-aldeen",
    "sulaemaneya",
    "waset (1)",
    "zeqar (1)",
)
# The Commission's governorate numbers, as its component-seat table prints them.
NUMBERS = {
    "1": "IQ-BG",
    "4": "IQ-DA",
    "5": "IQ-AR",
    "12": "IQ-NI",
    "14": "IQ-KI",
}
# The components' lists in the governorate summaries, by the Commission's list number.
COMPONENTS = {
    "291": ("Yazidi", "الإيزيديين", "IQ-NI"),
    "293": ("Shabak", "الشبك", "IQ-NI"),
    "295": ("Sabaean-Mandaean", "الصابئة المندائيين", "IQ-BG"),
}
SEX = {"ذكر": "male", "انثى": "female"}
GENERAL_SYSTEM = key("electoral_system", "open-list-modified-sainte-lague-2014")
COMPONENT_SYSTEM = key("electoral_system", "component-seat-2014")
IHEC = key("source", "ihec-2014")


def fold(name: str) -> str:
    """A governorate's name without the letters the two kinds of file spell differently."""
    name = unicodedata.normalize("NFKC", name).translate(str.maketrans("یکھۀ", "يكهه"))
    name = re.sub(r"[ً-ٰٟـ\s]", "", name).translate(str.maketrans("أإآىة", "ااايه"))
    return name


def _governorates() -> dict[str, str]:
    return {fold(arabic): iso for iso, (arabic, _, _) in ihec_2018.GOVERNORATES.items()}


def governorate(name: str) -> str:
    found = _governorates()
    folded = fold(name)
    if folded not in found:
        raise ValueError(f"unknown governorate {name!r}")
    return found[folded]


def letters(chars: list[tuple[str, float, float]]) -> str:
    """A line's characters in logical order. A ligature's letters come out reversed: all but the last are zero-width."""
    out: list[str] = []
    run: list[str] = []
    for char, x0, x1 in chars:
        if x1 - x0 < 0.05 and char not in " ":
            run.append(char)
            continue
        out.extend(reversed([*run, char]) if run else [char])
        run = []
    out.extend(run)
    return "".join(out)


def pages(path: Path) -> list[list[Word]]:
    """Each page's words, split where the characters are a space or a gap apart."""
    found: list[list[Word]] = []
    with pymupdf.open(path) as pdf:
        for number, page in enumerate(pdf, 1):
            words: list[Word] = []
            for block in page.get_text("rawdict")["blocks"]:
                for line in block.get("lines", []):
                    chars = [(ch["c"], ch["bbox"][0], ch["bbox"][2]) for span in line["spans"] for ch in span["chars"]]
                    y = line["bbox"][1]
                    start = 0
                    for i in range(1, len(chars) + 1):
                        gap = i == len(chars) or chars[i][0] == " " or chars[i - 1][0] == " "
                        if not gap:
                            before, after = chars[i - 1], chars[i]
                            gap = min(abs(before[1] - after[2]), abs(after[1] - before[2])) > 1.2
                        if gap:
                            piece = chars[start:i]
                            start = i
                            text = letters(piece).strip()
                            if text:
                                words.append(Word(min(c[1] for c in piece), max(c[2] for c in piece), y, text, number))
            found.append(words)
    return found


@dataclass(frozen=True)
class Candidate:
    governorate: str
    list: int
    number: int
    name: str
    gender: str
    votes: int
    list_name: str
    component: str


def _tokens(words: list[Word]) -> list[str]:
    return [w.text for w in sorted(words, key=lambda w: -w.x1)]


def _lines(words: list[Word]) -> list[Word]:
    """Words in reading order: lines top to bottom, each right to left."""
    ordered = sorted(words, key=lambda w: w.y)
    line, last, keyed = 0, None, []
    for w in ordered:
        if last is not None and w.y - last > 4:
            line += 1
        last = w.y
        keyed.append((line, -w.x1, w))
    return [w for _, _, w in sorted(keyed, key=lambda k: k[:2])]


def _row(words: list[Word]) -> tuple[int, str, int, str, str, int]:
    """One candidate's row, right to left: list number, list name, candidate number, name, sex, and votes."""
    toks = sorted(words, key=lambda w: (-round(w.x1), w.y))
    sex = next(i for i, w in enumerate(toks) if w.text in SEX)
    number = next(i for i in range(1, sex) if toks[i].text.isdigit())
    votes = int(toks[-1].text.replace(",", ""))
    list_name = " ".join(w.text for w in _lines(toks[1:number]))
    name = " ".join(w.text for w in toks[number + 1 : sex])
    return int(toks[0].text), list_name, int(toks[number].text), name, toks[sex].text, votes


@cache
def candidates() -> list[Candidate]:
    found: list[Candidate] = []
    current = ""
    for number, words in enumerate(pages(raw.cached(SOURCE, CANDIDATES).path), 1):
        words = [w for w in words if w.y >= 135]
        for column, (low, high) in enumerate(((530, 2000), (0, 530))):
            cells = [w for w in words if low <= w.middle < high]
            bands = [(w.y - 12, w.y + 8) for w in cells if w.text == "الاصوات"]
            cells = [w for w in cells if not any(a <= w.y <= b for a, b in bands)]
            anchors = sorted(w.y for w in cells if w.text in SEX)
            heading = next((w for w in cells if w.text.endswith("محافظة")), None)
            new = None
            if heading:
                line = [w for w in cells if abs(w.y - heading.y) < 3 and w.x1 <= heading.x0 + 1]
                new = governorate(" ".join(_tokens(line)))
            rows: dict[float, list[Word]] = {y: [] for y in anchors}
            for w in cells:
                if not anchors or w is heading:
                    continue
                y = min(anchors, key=lambda a: abs(a - w.y))
                if abs(y - w.y) <= 9:
                    rows[y].append(w)
            for y in anchors:
                toks = [w.text for w in sorted(rows[y], key=lambda w: (-round(w.x1), w.y))]
                sex = next(i for i, t in enumerate(toks) if t in SEX)
                if sex + 2 < len(toks):
                    point = next(i for i in range(3, sex) if toks[i].isdigit())
                    found.append(
                        Candidate(
                            NUMBERS[toks[0]],
                            int(toks[2]),
                            int(toks[point]),
                            " ".join(toks[point + 1 : sex]),
                            SEX[toks[sex]],
                            int(toks[-1].replace(",", "")),
                            " ".join(toks[3:point]),
                            toks[sex + 1],
                        )
                    )
                    continue
                g = new if (new and heading and y > heading.y) else current
                number_, list_name, cno, name, sex_, votes = _row(rows[y])
                found.append(Candidate(g, number_, cno, name, SEX[sex_], votes, list_name, ""))
            if new:
                current = new
    return found


def list_names() -> dict[int, str]:
    """Each list's name as the table prints it most often, the longest of the spellings that extend it."""
    seen: dict[int, Counter[str]] = defaultdict(Counter)
    for c in candidates():
        seen[c.list][c.list_name] += 1
    names = {}
    for number, counts in seen.items():
        common = counts.most_common(1)[0][0]
        names[number] = max((n for n in counts if n.startswith(common)), key=len)
    return names


def _cells(line: str) -> list[str]:
    text = unicodedata.normalize("NFKC", line).translate(str.maketrans("یکھۀ", "يكهه"))
    text = re.sub(r"[\u202a-\u202e\u200e\u200f]", "", text)
    return [c.strip() for c in re.split(r"\s{2,}", text.strip()) if c.strip()]


@dataclass
class ListResult:
    number: int
    votes: int
    seats: int
    winners: list[int]


@dataclass
class Summary:
    governorate: str
    valid: int
    seats: int
    women: int
    lists: list[ListResult]


@cache
def summary(stem: str) -> Summary:
    path = raw.cached(SOURCE, f"{BASE}{stem}.pdf").path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    out = Summary("", 0, 0, 0, [])
    pending = 0
    current: ListResult | None = None
    for line in text.splitlines():
        cells = _cells(line)
        if not cells:
            continue
        joined = " | ".join(cells)
        if cells[-1] == "المحافظة" and len(cells) == 2:
            out.governorate = governorate(cells[0])
        elif "مجموع الكلي للاصوات الصحيحة" in joined:
            out.valid = int(cells[0])
        elif "عدد المقاعد الكلي" in joined:
            out.seats = int(cells[0])
        elif "عدد مقاعد النساء" in joined:
            out.women = int(cells[0])
        elif cells[0] in SEX and len(cells) >= 4:
            if current:
                current.winners.append(int(cells[-2]))
        else:
            for i, cell in enumerate(cells):
                if i and cells[i - 1].isdigit():
                    if cell == "عدد الأصوات":
                        pending = int(cells[i - 1])
                    elif cell == "رقم الكيان":
                        current = ListResult(int(cells[i - 1]), pending, 0, [])
                        out.lists.append(current)
                    elif cell == "عدد المقاعد" and current:
                        current.seats = int(cells[i - 1])
    return out


@cache
def christians() -> list[tuple[int, int, int, str, int]]:
    """The Christian component's winners: list, governorate, candidate number, and votes, with the list's seats."""
    path = raw.cached(SOURCE, CHRISTIANS).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    found: list[tuple[int, int, int, str, int]] = []
    current = 0
    for line in text.splitlines():
        cells = _cells(line)
        for i, cell in enumerate(cells):
            if cell == "رقم الكيان" and i and cells[i - 1].isdigit():
                current = int(cells[i - 1])
        if len(cells) == 5 and cells[1].isdigit() and cells[3].isdigit():
            found.append((current, int(cells[3]), int(cells[1]), governorate(cells[0]), int(cells[4])))
    return found


METHOD = (
    "The Commission's table of every candidate's votes in each governorate, and its summary for each governorate of "
    "the valid votes, the seats, and the lists that won seats with their votes. The summaries print votes only for "
    "the lists that won seats, so the other lists have candidate votes and no list total. A list's votes include "
    "votes cast for the list alone, so they exceed its candidates' votes added together. The component seats "
    "(Christian, Yazidi, Shabak, and Sabaean-Mandaean) are separate contests; the votes of their candidates are "
    "those of the table, at the governorate where each candidate stood."
)
COMPONENT_METHOD = (
    "The component's candidates and their votes are those of the Commission's table, at the governorate where each "
    "candidate stood. The winners are those of the Commission's summaries. The summaries print list votes for the "
    "lists that won seats, and no total of valid votes for the component, so none is stored."
)
GENERAL_NOTE = (
    "Seats in each governorate are shared among lists by the modified Sainte-Laguë method with a first divisor of "
    "1.6, and each list's seats go to its candidates in order of their votes (Council of Representatives Elections "
    "Law no. 45 of 2013). Women hold at least a quarter of the seats. Eight further seats are reserved for "
    "components."
)
COMPONENT_NOTE = (
    "Eight seats are reserved for components: five for Christians, and one each for Yazidis, Shabaks, and "
    "Sabaean-Mandaeans. The candidate of a single-seat component with the most votes wins."
)
# The component seats' contests: the label the table prints, the English and Arabic names, and the seats.
CONTESTS = {
    "مسيحي": ("Christian", "المسيحيين", 5),
    "ايزيدي": ("Yazidi", "الإيزيديين", 1),
    "شبكي": ("Shabak", "الشبك", 1),
    "صابئي": ("Sabaean-Mandaean", "الصابئة المندائيين", 1),
}
SLUG = ihec_2018.slug


def party(number: int) -> str:
    return key("party", 2014, number)


def unit(iso: str) -> str:
    return key("unit", 2014, iso)


def election() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "Iraq's Council of Representatives election of 30 April 2014 by governorate, with every candidate's votes, "
        "the winning lists' votes, and the 328 declared winners",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(IRAQ)
    p.external.add(COUNTRY)
    p.accept(
        "accounting.votes_valid",
        "the summaries print votes only for the lists that won seats, so the lists' votes add up to fewer than the "
        "valid votes of each governorate; both are stored as printed",
    )
    p.add(
        "source",
        id=IHEC,
        kind="official_body",
        publisher="Independent High Electoral Commission of Iraq",
        title="Results of the 2014 Council of Representatives election",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Independent High Electoral Commission of Iraq",
    )
    for url in (CANDIDATES, CHRISTIANS, *(f"{BASE}{stem}.pdf" for stem in SUMMARIES)):
        p.artifact(raw.cached(SOURCE, url), IHEC)
    for id, rules, body in (
        (
            GENERAL_SYSTEM,
            {
                "formula": "modified_sainte_lague",
                "firstDivisor": 1.6,
                "lists": {"order": "open", "vacancy": "next_by_votes"},
            },
            GENERAL_NOTE,
        ),
        (COMPONENT_SYSTEM, {"formula": "fptp"}, COMPONENT_NOTE),
    ):
        p.add("electoral_system", id=id, polity_id=IRAQ, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    entity = key("election", 2014)
    p.add("election", id=entity, polity_id=IRAQ, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.name(entity, "2014 Iraqi Council of Representatives election", "en", "official")
    p.name(entity, "انتخابات مجلس النواب العراقي 2014", "ar", "official")
    p.add("slug", path=PATH, entity_id=entity, is_current=True)
    names = list_names()
    for number in sorted(names):
        p.add("party", id=party(number), kind="list_vehicle", polity_id=IRAQ)
        p.name(party(number), names[number], "ar", "official")
    for iso in ihec_2018.GOVERNORATES:
        p.add(
            "reporting_unit",
            id=unit(iso),
            polity_id=IRAQ,
            scheme_code=GOVERNORATE_SCHEME,
            code=f"{DAY}:{iso}",
            area_relation="equals",
            area_id=governorate_area(iso),
            kind="standard",
        )
    everyone = candidates()
    for stem in SUMMARIES:
        _governorate(p, entity, summary(stem), [c for c in everyone if not c.component])
    _components(p, entity, everyone)
    return p


def _person(p: Package, contest: str, c: Candidate, tag: tuple[object, ...], parent: str | None, position: bool):
    person, option = key("person", 2014, *tag), key("option", 2014, *tag)
    p.add("person", id=person, gender=c.gender)
    p.name(person, c.name, "ar", "official")
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        parent_option_id=parent,
        party_id=party(c.list),
        ballot_position=c.number,
        ballot_label=c.name,
    )
    candidacy = key("candidacy", 2014, *tag)
    p.add(
        "candidacy",
        id=candidacy,
        person_id=person,
        option_id=option,
        role="list_member",
        list_position=c.number if position else None,
        ballot_name=c.name,
        member_of_party_id=party(c.list),
    )
    return option, candidacy


def _value(p: Package, result_set: str, unit_id: str, option: str | None, measure: int, amount: int, declared: bool):
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit_id,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=amount,
    )
    if declared:
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=amount)


def _result_set(p: Package, entity: str, contest: str, code: str, body: str) -> str:
    result_set = key("result_set", 2014, code)
    p.add(
        "result_set",
        id=result_set,
        election_id=entity,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=IHEC,
        status="active",
        is_default=True,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
    return result_set


def _governorate(p: Package, entity: str, s: Summary, rows: list[Candidate]) -> None:
    iso = s.governorate
    arabic, english, _ = ihec_2018.GOVERNORATES[iso]
    contest = key("contest", 2014, iso)
    general = [lst for lst in s.lists if str(lst.number) not in COMPONENTS]
    magnitude = sum(lst.seats for lst in general)
    if magnitude != s.seats:
        raise ValueError(f"{iso}: the lists hold {magnitude} seats and the summary {s.seats}")
    p.add(
        "contest",
        id=contest,
        election_id=entity,
        kind="list_allocation",
        body_id=COUNCIL,
        district_area_id=governorate_area(iso),
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=GENERAL_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"2014 {english}", "en", "official")
    p.name(contest, arabic, "ar", "official")
    p.add("slug", path=f"{PATH}/{SLUG(english)}", entity_id=contest, is_current=True)
    result_set = _result_set(p, entity, contest, iso, METHOD)
    place = unit(iso)
    _value(p, result_set, place, None, VALID, s.valid, True)
    results = {lst.number: lst for lst in general}
    won = {(lst.number, number) for lst in general for number in lst.winners}
    names = list_names()
    by_list: defaultdict[int, list[Candidate]] = defaultdict(list)
    for c in rows:
        if c.governorate == iso:
            by_list[c.list].append(c)
    for number in sorted(by_list):
        option = key("option", 2014, iso, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(number),
            ballot_position=number,
            ballot_label=f"{number} – {names[number]}",
        )
        if number in results:
            _value(p, result_set, place, option, VOTES, results[number].votes, True)
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=results[number].seats)
        for c in sorted(by_list[number], key=lambda c: c.number):
            child, candidacy = _person(p, contest, c, (iso, number, c.number), option, True)
            _value(p, result_set, place, child, CANDIDATE_VOTES, c.votes, False)
            if (number, c.number) in won:
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
    missing = set(results) - set(by_list)
    if missing:
        raise ValueError(f"{iso}: lists {sorted(missing)} have no candidates in the table")


def _components(p: Package, entity: str, rows: list[Candidate]) -> None:
    winners: set[tuple[str, int, int]] = {(g, lst, n) for lst, n, _, g, _ in christians()}
    for stem in SUMMARIES:
        s = summary(stem)
        for lst in s.lists:
            if str(lst.number) in COMPONENTS:
                winners |= {(s.governorate, lst.number, n) for n in lst.winners}
    for label, (english, arabic, seats) in CONTESTS.items():
        members = [c for c in rows if c.component == label]
        contest = key("contest", 2014, english)
        p.add(
            "contest",
            id=contest,
            election_id=entity,
            kind="body_seats",
            body_id=COUNCIL,
            district_area_id=COUNTRY,
            magnitude=seats,
            round_number=1,
            electoral_system_id=COMPONENT_SYSTEM if seats == 1 else None,
            default_result_kind="counted",
        )
        p.name(contest, f"2014 {english} seats", "en", "official")
        p.name(contest, f"مقاعد {arabic}", "ar", "official")
        p.add("slug", path=f"{PATH}/{SLUG(english)}", entity_id=contest, is_current=True)
        result_set = _result_set(p, entity, contest, english, COMPONENT_METHOD)
        for c in sorted(members, key=lambda c: (c.governorate, c.list, c.number)):
            option, _ = _person(p, contest, c, (english, c.governorate, c.list, c.number), None, False)
            _value(p, result_set, unit(c.governorate), option, VOTES, c.votes, False)
            if (c.governorate, c.list, c.number) in winners:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
