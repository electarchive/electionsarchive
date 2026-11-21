"""Reads the National Assembly contests of the National Electoral Council's results book for the 2023 early elections.

The book prints each contest on a run of pages: a summary page with the lists and their votes, a page that names the
members elected, and the contest's rows of votes by canton or country, and, for the larger circumscriptions, by
parish. The tables are read from the text layer with the same row reader as the 2017 book (``book2017``); the layout
differs in the headings of the summary box and in the member table, which prints each list's votes between the list
and its member.
"""

import functools
import re
from collections import Counter

from ea.sources.ec import book2017
from ea.sources.ec.book2017 import Group, Row, Winner

BOOK = f"{book2017.cne.BASE}/2024/06/ResultadosElectoralesAnticipadasyConsultas2023.pdf"
FIRST_PAGE = 135
BOX = (
    ("ELECTORES", "registered"),
    ("SUFRAGANTES", "cast"),
    ("VOTOS EN BLANCO", "blank"),
    ("VOTOS NULOS", "null"),
)
# words of a list's name that the text layer drops or splits, as the book prints them
REPAIRS = {
    "PATRIÓTICA DE ENERO": "PATRIÓTICA 21 DE ENERO",
    "PRO- GRESO": "PROGRESO",
    "RC5- MAR70": "RC5-MAR70",
    "PSP LISTA3-PSC LISTA": "PSP LISTA 3-PSC LISTA 6",
}
SEATS = re.compile(r"([A-Z][A-Z/\"”“.\-0-9 ]*?): (\d+)")
VOTE = re.compile(r"^\d{1,3}(?:\.\d{3})+$")


def page_count() -> int:
    return len(book2017.pages(BOOK))


@functools.cache
def page_text(page: int) -> str:
    return "\n".join(" ".join(w.text for w in ws) for _, ws in book2017.lines(page, BOOK))


def box(page: int) -> dict[str, int]:
    """The general-data box of a summary page: electors, voters, blank ballots, and null ballots."""
    found: dict[str, int] = {}
    for line in page_text(page).split("\n"):
        for label, name in BOX:
            match = re.match(rf"^{label} ([\d.]+)\b", line)
            if match and name not in found:
                found[name] = book2017.number(match[1])
    missing = {name for _, name in BOX} - set(found)
    if missing:
        raise ValueError(f"page {page}: the data box has no {sorted(missing)}")
    return found


def entries(page: int) -> list[book2017.Entry]:
    found = book2017.summary(page, BOOK)
    result = []
    for entry in found:
        # the list's abbreviation wraps over lines and its words keep their spaces; the name's list number is a word
        name, sigla = entry.name, entry.sigla
        for broken, whole in REPAIRS.items():
            name = name.replace(broken, whole)
        if name.endswith(" POR") and sigla.startswith("UN PAIS"):
            name, sigla = name.removesuffix(" POR"), "POR " + sigla
        result.append(book2017.Entry(entry.number, sigla, name, entry.votes))
    return result


def seats(page: int) -> Counter[str]:
    """The seats each list won, as the donut's labels print them, such as ``RC: 2``."""
    ls = book2017.lines(page, BOOK)
    counts: Counter[str] = Counter()
    for _, words in ls:
        line = " ".join(w.text for w in words)
        for label, value in SEATS.findall(line):
            counts[label.strip()] += int(value)
    return counts


def members(page: int) -> list[Winner]:
    """The members a seats page names, each with the list they won for, the book's list votes taken out of the name."""
    found = book2017.winners(page, BOOK)
    result = []
    for w in found:
        words = [t for t in w.name.split() if not VOTE.match(t)]
        gender = "FEMENINO" if w.gender == "FEMENIO" else w.gender  # the book's misspelling in one table
        result.append(Winner(w.number, w.sigla, " ".join(words), gender, w.votes))
    return result


def with_list(member: Winner, found: list[book2017.Entry]) -> Winner:
    """A member whose list number the text layer lost gets the number of the one list that has their abbreviation."""
    if any(member.number == e.number for e in found):
        return member
    same = [e for e in found if e.sigla == member.sigla]
    if len(same) != 1:
        raise ValueError(f"no list for {member.name!r} ({member.number!r}, {member.sigla!r})")
    return Winner(same[0].number, member.sigla, member.name, member.gender, member.votes)


def starts() -> list[int]:
    """The summary page of every contest: the pages with a general-data box and a total of valid votes."""
    return [
        p
        for p in range(FIRST_PAGE, page_count() + 1)
        if "TOTAL VOTOS VÁLIDOS" in page_text(p) and "DATOS GENERALES" in page_text(p)
    ]


def heading(page: int) -> str:
    """The contest's heading, such as PROVINCIA: AZUAY or CIRCUNSCRIPCIÓN DEL EXTERIOR: CANADÁ Y ESTADOS UNIDOS."""
    for _, ws in book2017.lines(page, BOOK):
        line = " ".join(w.text for w in ws)
        if line.startswith(("PROVINCIA", "CIRCUNSCRIPCIÓN DEL EXTERIOR")):
            return " ".join(line.replace("PROVINCIA:", "PROVINCIA").split())
    raise ValueError(f"page {page} has no heading")


def groups() -> list[Group]:
    """Every provincial, circumscription, and overseas contest, each with its rows."""
    begins = [p for p in starts() if p not in (139, 142, 149)]
    ends = [*begins[1:], page_count() + 1]
    result = []
    for start, end in zip(begins, ends, strict=True):
        found = entries(start)
        count = len(found)
        seat_pages = [p for p in range(start + 1, end) if "NÚMERO DE ESCAÑOS" in page_text(p)]
        elected = [with_list(m, found) for p in seat_pages for m in members(p)]
        rows: list[Row] = []
        parishes: list[Row] = []
        split = False
        pages_ = [p for p in range(start + 2, end) if p not in seat_pages]
        for p in pages_:
            text = page_text(p)
            if "RESULTADOS POR" not in text and "CANTÓN/PARROQUIA" not in text:
                continue
            cut = next((y for y, ws in book2017.lines(p, BOOK) if any(w.text == "CANTÓN/PARROQUIA" for w in ws)), 1e9)
            if split:
                cut = 0.0
            here = [r for r in book2017.rows(p, count + 5, 0, cut, BOOK) if len(r.numbers) == count + 5]
            rows.extend(here)
            parishes.extend(book2017.rows(p, count + 1, cut, 1e9, BOOK))
            split = split or cut < 1e9
        total = next((r for r in rows if r.name.startswith("TOTAL")), None)
        units = [r for r in rows if r is not total]
        head = heading(start)
        result.append(Group(start, head, found, box(start), elected, units, total, parishes, pages_))
    return result


NATIONAL_SUMMARY, NATIONAL_SEATS, NATIONAL_TABLE = 139, 140, 141


def national() -> tuple[Group, Counter[str]]:
    """The national contest: its lists, the nation's figures, each province's and overseas circumscription's votes.

    The book prints no names for the national members, only the seats each list won, so the second value maps each
    list number to its seats.
    """
    found = entries(NATIONAL_SUMMARY)
    count = len(found)
    cut = next(y for y, ws in book2017.lines(NATIONAL_TABLE, BOOK) if any(w.text == "VÁLIDOS" for w in ws))
    rows = book2017.rows(NATIONAL_TABLE, count + 1, cut, 1e9, BOOK)
    total = next(r for r in rows if r.name.startswith("TOTAL"))
    units = [r for r in rows if r is not total]
    by_sigla = {e.sigla: e.number for e in found}
    won: Counter[str] = Counter()
    for sigla, value in re.findall(r"([A-Z/]+) (\d+) \d+,\d+%", page_text(NATIONAL_SEATS)):
        if sigla in by_sigla:
            won[by_sigla[sigla]] += int(value)
    if sum(won.values()) != 15:
        raise ValueError(f"the national seats add up to {sum(won.values())}, not 15")
    group = Group(NATIONAL_SUMMARY, "NACIONAL", found, box(NATIONAL_SUMMARY), [], units, total, [], [NATIONAL_TABLE])
    return group, won
