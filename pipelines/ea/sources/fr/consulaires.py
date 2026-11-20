import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from uuid import uuid5

import fastexcel
import pymupdf
import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "fr.consulaires"
DATAGOUV = "https://static.data.gouv.fr/resources"
FILES = {
    "2014-results": f"{DATAGOUV}/resultats-des-elections-des-conseillers-consulaires-de-2014/20191011-163633/"
    "copie-de-resultas-cc-dc-complets.xlsx",
    "2014-turnout": f"{DATAGOUV}/resultats-des-elections-des-conseillers-consulaires-de-2014/20191011-163553/"
    "cc-votes-et-participation-par-circo.xlsx",
    "2021-results": f"{DATAGOUV}/resultats-des-elections-consulaires-de-mai-2021/20210602-175134/"
    "resulatst-elections-consulaires-mai-2021-vote-a-lurne-et-vote-par-internet-par-circonscription-electorale-"
    "consulaire.xlsx",
    "2021-elected": f"{DATAGOUV}/resultats-des-elections-des-conseillers-consulaires-de-mai-2021/20210602-132111/"
    "2021-liste-cfde-elus-pour-fd.xlsx",
    "2026-results": "https://www.diplomatie.gouv.fr/files/files/pdf/resultats-et-elus-danae-01-06-2026.pdf",
    "2026-elected": "https://www.diplomatie.gouv.fr/files/files/pdf/tableau-elus-avec-ordre-tirage-au-sort.pdf",
    "2026-turnout": "https://www.diplomatie.gouv.fr/files/files/pdf/"
    "tableau-general-recensement-participation-elections_cfde_et_dc.pdf",
}
PAGES = {
    2014: "https://www.data.gouv.fr/datasets/resultats-des-elections-des-conseillers-consulaires-de-2014/",
    2021: "https://www.data.gouv.fr/datasets/resultats-des-elections-consulaires-de-mai-2021/",
    2026: "https://www.diplomatie.gouv.fr/fr/services-aux-francaises-et-aux-francais/vie-administrative-et-elections/"
    "elections-a-l-etranger/consulter-les-resultats-des-elections-vote-des-francais-de-l-etranger/"
    "elections-des-conseillers-des-francais-de-l-etranger-2026-resultats",
}
DAYS = {
    2014: (date(2014, 5, 24), date(2014, 5, 25)),
    2021: (date(2021, 5, 29), date(2021, 5, 30)),
    2026: (date(2026, 5, 30), date(2026, 5, 31)),
}
YEARS = tuple(DAYS)
NATURAL_EARTH = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip"
FRANCE = "01a13b8a-b1bc-7045-aa86-d470ebdf31b2"
SCHEME = "fr.circ_consulaire"


@dataclass
class Entry:
    number: int
    label: str
    urne: int | None
    internet: int | None
    total: int
    seats: int
    delegates: int
    elected: list[str] = field(default_factory=list)


@dataclass
class Constituency:
    year: int
    name: str
    registered: int | None
    entries: list[Entry]
    voters: int | None = None
    blank: int | None = None
    invalid: int | None = None
    valid: int | None = None


def fetch() -> None:
    for name, url in FILES.items():
        raw.fetch(SOURCE, url, suffix=".pdf" if name.startswith("2026") else ".xlsx")
    raw.fetch(SOURCE, NATURAL_EARTH, suffix=".zip")


def _rows(name: str) -> list[tuple[object, ...]]:
    path = raw.cached(SOURCE, FILES[name]).path
    return fastexcel.read_excel(str(path)).load_sheet_by_idx(0, header_row=None).to_polars().rows()


def _int(value: object) -> int | None:
    if value is None:
        return None
    text = re.sub(r"\s", "", str(value))
    if text in ("", "-"):
        return None
    return int(float(text))


def _seats(value: object) -> int:
    return _int(value) or 0


def clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text)).replace("’", "'").strip()


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFD", text.replace("’", "'"))
    return "".join(c for c in plain if not unicodedata.combining(c)).upper()


def key_of(name: object) -> str:
    """A spelling of a constituency name that the three years' files share."""
    text = fold(str(name)).replace("ETATS-UNIS D'AMERIQUE", "ETATS-UNIS")
    text = re.sub(r"\(.*?\)|\bAVEC\b.*", "", text)
    found = re.search(r"(\d)\s*(?:ERE|EME|RE|E)?\s*CIRC", text) or re.search(r"[-_\s](\d)$", text)
    text = re.sub(r"\d\s*(?:ERE|EME|RE|E)?\s*CIRC\w*", "", text)
    text = re.sub(r"[^A-Z]", "", re.sub(r"CIRCONSCRIPTION|\bCIRC\b", "", text))
    return text + "|" + (found.group(1) if found else "")


def canonical_names() -> dict[str, str]:
    """The constituencies of 2021 by key. The 2021 workbook names them in full, and lists the three that postponed."""
    rows = _rows("2021-results")[6:]
    return {key_of(clean(r[0])): clean(r[0]) for r in rows if r[0] and clean(r[0]) != "TOTAL" and r[1] is not None}


# Names the 2026 tables break across lines in a way that reads wrongly.
NAME_FIXES = {
    "Canada -4e Canada 4e circonscription": "Canada - 4e circonscription",
    "Iran, Pakistan, Afghanistan, Azerbaïdjan,": "Iran, Pakistan, Afghanistan, Azerbaïdjan, Turkménistan, Kazakhstan, "
    "Tadjikistan, Ouzbékistan, Kirghizstan",
}


def canonical(name: str) -> str:
    known = canonical_names()
    name = NAME_FIXES.get(name, name)
    return known.get(key_of(name), name)


# 2014 ---------------------------------------------------------------------------------------------------------------


def results_2014() -> list[Constituency]:
    out: list[Constituency] = []
    current: Constituency | None = None
    for row in _rows("2014-results")[2:]:
        head = row[0]
        if head is None:
            continue
        text = clean(head)
        if text.lower().startswith("circonscription électorale"):
            current = Constituency(2014, clean(text[len("circonscription électorale") :]), None, [])
            out.append(current)
        elif current is None:
            continue
        elif text.startswith("Nombre d'inscrits"):
            current.registered = _int(row[3])
        elif text in ("TOTAL", "GRAND TOTAL"):
            continue
        else:
            current.entries.append(
                Entry(
                    number=len(current.entries) + 1,
                    label=text,
                    urne=_int(row[1]),
                    internet=_int(row[2]),
                    total=_int(row[3]) or 0,
                    seats=_seats(row[5]),
                    delegates=_seats(row[6]),
                )
            )
    return out


def _plain(name: object) -> str:
    text = fold(str(name)).replace("ETATS-UNIS D'AMERIQUE", "ETATS-UNIS")
    text = re.sub(r"\(.*?\)", "", text)
    found = re.search(r"(\d)\s*(?:ERE|EME|E)?\s*CIRC", text) or re.search(r"\s(\d)$", text)
    text = re.sub(r"\d\s*(ERE|EME)?\s*CIRC", "", text)
    text = re.sub(r"\bCIRC\b", "", re.sub(r"[^A-Z ]", " ", text))
    return re.sub(r"\s+", " ", text).strip() + "|" + (found.group(1) if found else "")


# The turnout workbook spells constituencies its own way and lists the Uzbekistan and Kyrgyzstan post on its own row.
TURNOUT_ALIAS_2014 = {
    "REPUBLIQUE DEMOCRATIQUE DU CONGO": ["RDC"],
    "IRAN, PAKISTAN, AFGHANISTAN, AZERBAIDJAN, TURKMENISTAN, KAZAKHSTAN, TADJIKISTAN, OUZBEKISTAN, KIRGHIZSTAN": [
        "IRAN PAKISTAN……",
        "OUZBEKISTAN KIRGHISTAN",
    ],
}


def turnout_2014(places: list[Constituency]) -> dict[str, list[dict[str, int | None]]]:
    """The turnout rows of the 2014 workbook, assigned to the constituency they belong to."""
    rows = [r for r in _rows("2014-turnout")[3:] if r[2] is not None]
    taken: dict[int, str] = {}
    for name, aliases in TURNOUT_ALIAS_2014.items():
        for alias in aliases:
            (index,) = [i for i, r in enumerate(rows) if clean(r[2]) == alias]
            taken[index] = name
    scored = sorted(
        (
            (SequenceMatcher(None, _plain(r[2]), _plain(p.name)).ratio(), i, p.name)
            for i, r in enumerate(rows)
            for p in places
            if i not in taken and p.name not in TURNOUT_ALIAS_2014
        ),
        reverse=True,
    )
    used = {name for name in TURNOUT_ALIAS_2014}
    for _, index, name in scored:
        if index in taken or name in used:
            continue
        taken[index] = name
        used.add(name)
    if len(taken) != len(rows) or used != {p.name for p in places}:
        raise LookupError("the 2014 turnout rows do not match the constituencies one to one")
    out: dict[str, list[dict[str, int | None]]] = {}
    for index, name in taken.items():
        r = rows[index]
        out.setdefault(name, []).append(
            {
                "registered": _int(r[3]),
                "voters": _int(r[6]),
                "blank": (_int(r[10]) or 0) + (_int(r[11]) or 0),
                "invalid": (_int(r[12]) or 0) + (_int(r[13]) or 0),
                "valid": _int(r[17]),
            }
        )
    return out


def constituencies_2014() -> list[Constituency]:
    places = [p for p in results_2014() if p.entries]
    turnout = turnout_2014(places)
    for place in places:
        rows = turnout[place.name]
        place.registered = sum(r["registered"] or 0 for r in rows) if len(rows) > 1 else place.registered
        place.voters = sum(r["voters"] or 0 for r in rows)
        place.blank = sum(r["blank"] or 0 for r in rows)
        place.invalid = sum(r["invalid"] or 0 for r in rows)
        place.valid = sum(r["valid"] or 0 for r in rows)
        place.name = canonical(place.name)
    return places


# 2021 ---------------------------------------------------------------------------------------------------------------


def constituencies_2021() -> list[Constituency]:
    out: list[Constituency] = []
    current: Constituency | None = None
    for row in _rows("2021-results")[6:]:
        if row[0] is not None and clean(row[0]) != "TOTAL":
            if not re.fullmatch(r"[\d\s]+", str(row[1])):
                current = None
                continue
            current = Constituency(2021, clean(row[0]), _int(row[1]), [])
            out.append(current)
        elif row[0] is not None or current is None or row[2] is None:
            continue
        if row[2] is None or current is None:
            continue
        match = re.match(r"(\d+)\s*-\s*(.*)", clean(row[2]), re.DOTALL)
        if match is None:
            continue
        current.entries.append(
            Entry(
                number=int(match.group(1)),
                label=clean(match.group(2)),
                urne=_int(row[3]),
                internet=_int(row[4]),
                total=_int(row[5]) or 0,
                seats=_seats(row[6]),
                delegates=_seats(row[7]),
            )
        )
    known = list(canonical_names())
    people: dict[str, list[tuple[str, str]]] = {}
    for name, label, person in elected_2021():
        key = key_of(name)
        if key not in known:
            key = max(known, key=lambda k: SequenceMatcher(None, k, key).ratio())
        people.setdefault(key, []).append((label, person))
    for place in out:
        place.valid = sum(e.total for e in place.entries)
        chosen = people.pop(key_of(place.name), [])
        for entry in place.entries:
            for _ in range(entry.seats):
                label, person = chosen.pop(0)
                if SequenceMatcher(None, fold(label), fold(entry.label)).ratio() < 0.4 and fold(label)[:12] not in fold(
                    entry.label
                ):
                    raise LookupError(f"2021: {person} is not on {entry.label!r} in {place.name}")
                entry.elected.append(person)
        if chosen:
            raise LookupError(f"2021: councillors left over in {place.name}")
    if any(people.values()):
        raise LookupError(f"2021: councillors in unknown constituencies {list(people)}")
    return out


def elected_2021() -> list[tuple[str, str, str]]:
    """(constituency, list label, person) in the order the source gives them, which is the order of the draw."""
    out: list[tuple[str, str, str]] = []
    for row in _rows("2021-elected")[5:]:
        if row[0] is None or row[4] is None:
            continue
        person = clean(f"{row[3] or ''} {row[4]}")
        out.append((clean(row[0]), clean(row[1]), person))
    return out


# 2026 ---------------------------------------------------------------------------------------------------------------


def _words(page: pymupdf.Page) -> list[tuple[float, float, float, float, str]]:
    return [(w[0], w[1], w[2], w[3], str(w[4])) for w in page.get_text("words")]


def _text(words: list[tuple[float, float, float, float, str]]) -> str:
    """Words in reading order: lines top to bottom, each left to right."""
    return clean(" ".join(w[4] for w in sorted(words, key=lambda w: (round((w[1] + w[3]) / 4), w[0]))))


def _in(words: list[tuple[float, float, float, float, str]], x0: float, x1: float, y0: float, y1: float):
    return [w for w in words if x0 <= (w[0] + w[2]) / 2 < x1 and y0 <= (w[1] + w[3]) / 2 < y1]


def _tables(name: str) -> list[tuple[int, pymupdf.Page, list[pymupdf.table.TableRow], list[float]]]:
    out = []
    document = pymupdf.open(str(raw.cached(SOURCE, FILES[name]).path))
    for number in range(len(document)):
        page = document[number]
        for table in page.find_tables().tables:
            full = next((r for r in table.rows if all(r.cells)), None)
            if full is not None:
                out.append((number, page, list(table.rows), [full.cells[0][0], *(c[2] for c in full.cells)]))
    return out


def _title(parts: list[str]) -> str:
    text = clean(" ".join(parts)).removeprefix("Circonscription électorale").strip()
    return re.sub(r"-\s+(?=\w)", "-", re.sub(r"\s+([,)])", r"\1", text))


Word = tuple[float, float, float, float, str]


def _lines(words: list[Word]) -> list[tuple[float, list[Word]]]:
    """Words grouped into lines by the height of their middle, top to bottom."""
    out: list[tuple[float, list[Word]]] = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        middle = (w[1] + w[3]) / 2
        if out and middle - out[-1][0] < 2.5:
            out[-1][1].append(w)
        else:
            out.append((middle, [w]))
    return out


def _flow(lines: list[tuple[float, list[Word]]], last: int) -> list[tuple[list[str], float, float]]:
    """A column of titles cut into one title for each list: a title opens at a line that starts with a list number
    above the last one, or with 1, and runs until the next such line. Gives each title with the height of its first
    and last line."""
    titles: list[tuple[list[str], float, float]] = []
    for middle, words in lines:
        text = _text(words)
        match = re.match(r"(\d+)\s*-\s*\S", text)
        if match is not None and (int(match.group(1)) == 1 or int(match.group(1)) > last):
            last = int(match.group(1))
            titles.append(([text], middle, middle))
        elif titles:
            titles[-1][0].append(text)
            titles[-1] = (titles[-1][0], titles[-1][1], middle)
    return titles


def results_2026() -> list[Constituency]:
    """Lists of the 2026 results table. The ruling of the table has gaps, so rows are found from the figures: each
    list has one line of figures, and its title, which can run over several lines, is the next title in the
    title column."""
    out: list[Constituency] = []
    names: list[list[str]] = []
    edges: list[float] = []
    document = pymupdf.open(str(raw.cached(SOURCE, FILES["2026-results"]).path))
    for number in range(len(document)):
        page = document[number]
        if number == 0:
            first = page.find_tables().tables[0]
            row = next(r for r in first.rows if all(r.cells))
            edges = [row.cells[0][0], *(c[2] for c in row.cells)]
            head = first.rows[0].bbox[3]
        else:
            head = 0.0
        words = [w for w in _words(page) if w[1] >= max(head, 60) and w[3] <= 790]
        column = [_in(words, edges[i], edges[i + 1], 0, 900) for i in range(len(edges) - 1)]
        titles = _flow(_lines(column[1]), out[-1].entries[-1].number if out else 0)
        figures = []
        for middle, total in _lines([w for w in column[4] if w[4].isdigit()]):
            near = [[w for w in column[i] if abs((w[1] + w[3]) / 2 - middle) < 3.5] for i in (2, 3, 5, 6)]
            figures.append((middle, [_text(n) for n in near], _text(total)))
        if len(titles) != len(figures):
            raise LookupError(f"2026 results: {len(titles)} titles but {len(figures)} rows on page {number}")
        spans: dict[int, list[float]] = {}
        for (title, top, bottom), (_, cells, total) in zip(titles, figures, strict=True):
            match = re.match(r"(\d+)\s*-\s*(.*)", " ".join(title), re.DOTALL)
            if match is None:
                raise LookupError(f"2026 results: no number in {title}")
            if int(match.group(1)) == 1 or not out:
                out.append(Constituency(2026, "", None, []))
                names.append([])
            high = spans.setdefault(len(out) - 1, [top, bottom])
            high[0], high[1] = min(high[0], top), max(high[1], bottom)
            out[-1].entries.append(
                Entry(
                    number=int(match.group(1)),
                    label=clean(match.group(2)),
                    internet=_int(cells[0]),
                    urne=_int(cells[1]),
                    total=_int(total) or 0,
                    seats=_seats(cells[2]),
                    delegates=_seats(cells[3]),
                )
            )
        # A name is centered on its block and can run past it, so each line goes to the block whose middle is nearest.
        for middle, line in _lines(column[0]):
            index = min(spans, key=lambda i: abs(middle - sum(spans[i]) / 2))
            names[index].append(_text(line))
    for place, parts in zip(out, names, strict=True):
        place.name = _title(parts)
    return out


def turnout_2026() -> list[tuple[str, dict[str, int | None]]]:
    out: list[tuple[str, dict[str, int | None]]] = []
    names: list[str] = []
    for _, page, rows, edges in _tables("2026-turnout"):
        words = _words(page)
        start = rows[0].bbox[1]
        for row in rows:
            cells = [
                _text(_in(words, edges[i], edges[i + 1], row.bbox[1], row.bbox[3])) for i in range(1, len(edges) - 1)
            ]
            if fold(cells[1]).replace(" ", "").startswith("TOTALCIRCONSCRIPTIONELECTORALE"):
                names.append(_text(_in(words, edges[0], edges[1], start, row.bbox[3])))
                start = row.bbox[3]
                out.append(
                    (
                        _title(names),
                        {
                            "registered": _int(cells[2]),
                            "voters": _int(cells[3]),
                            "blank": _int(cells[5]),
                            "invalid": _int(cells[6]),
                            "valid": _int(cells[7]),
                        },
                    )
                )
                names = []
        names.append(_text(_in(words, edges[0], edges[1], start, rows[-1].bbox[3])))
    return out


def elected_2026() -> list[tuple[str, str, str]]:
    """(draw number, list label, person) of every councillor, in the order of the table. Each person has a line with
    the draw number, and a name or list title that wraps sits within a few points above and below it."""
    out: list[tuple[str, str, str]] = []
    edges: list[float] = []
    document = pymupdf.open(str(raw.cached(SOURCE, FILES["2026-elected"]).path))
    for number in range(len(document)):
        page = document[number]
        if number == 0:
            first = page.find_tables().tables[0]
            row = next(r for r in first.rows if all(r.cells))
            edges = [row.cells[0][0], *(c[2] for c in row.cells)]
            head = first.rows[0].bbox[3]
        else:
            head = 0.0
        words = [w for w in _words(page) if w[1] >= max(head, 40)]
        column = [_in(words, edges[i], edges[i + 1], 0, 900) for i in range(len(edges) - 1)]
        for middle, line in _lines([w for w in column[3] if w[4].isdigit()]):
            near = [[w for w in column[i] if abs((w[1] + w[3]) / 2 - middle) < 6] for i in (1, 2, 4)]
            kind, label, person = (_text(n) for n in near)
            if kind.startswith("CFdE"):
                out.append((_text(line), label, person))
    return out


def constituencies_2026() -> list[Constituency]:
    places = results_2026()
    turnout = turnout_2026()
    if len(turnout) != len(places):
        raise LookupError(f"2026: {len(places)} constituencies in the results but {len(turnout)} in the turnout")
    # The two tables sort accented names differently, so each turnout row goes to the constituency that spells it most
    # nearly alike, and the figures confirm it: the valid votes of the table are the votes of the lists.
    scored = sorted(
        (
            (SequenceMatcher(None, key_of(NAME_FIXES.get(p.name, p.name)), key_of(name)).ratio(), i, j)
            for i, p in enumerate(places)
            for j, (name, _) in enumerate(turnout)
        ),
        reverse=True,
    )
    done: dict[int, int] = {}
    for _, i, j in scored:
        if i not in done and j not in done.values():
            done[i] = j
    for i, place in enumerate(places):
        figures = turnout[done[i]][1]
        if figures["valid"] != sum(e.total for e in place.entries):
            raise LookupError(f"2026: the turnout of {place.name} does not match its lists")
        place.registered = figures["registered"]
        place.voters = figures["voters"]
        place.blank = figures["blank"]
        place.invalid = figures["invalid"]
        place.valid = figures["valid"]
        place.name = canonical(place.name)
    people = elected_2026()
    at = 0
    for place in places:
        for entry in place.entries:
            for _ in range(entry.seats):
                number, _, person = people[at]
                at += 1
                if int(number) != entry.number:
                    raise LookupError(f"2026: {person} is not on list {entry.number} of {place.name}")
                entry.elected.append(person)
    if at != len(people):
        raise LookupError("2026: councillors left over")
    return places


# Packages -----------------------------------------------------------------------------------------------------------

LOADERS = {2014: constituencies_2014, 2021: constituencies_2021, 2026: constituencies_2026}
FILES_OF = {
    2014: ("2014-results", "2014-turnout"),
    2021: ("2021-results", "2021-elected"),
    2026: ("2026-results", "2026-elected", "2026-turnout"),
}
BODY = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fbody\x1fcfde"))
SOURCES = {
    2014: str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1f2014")),
    2021: str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1f2021")),
    2026: str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1f2026")),
}
EARTH_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fnatural_earth"))
MINISTRY = "Ministère de l'Europe et des Affaires étrangères"
LISTS = {
    "formula": "dhondt",
    "lists": {"order": "closed", "vacancy": "next_on_list"},
}
SINGLE = {"formula": "fptp"}
SYSTEMS = {
    "list": (
        LISTS,
        (
            "Where a consular constituency elects two or more councillors, voters choose a closed list, without "
            "preference votes or changes to the order, in one round. The seats go to the lists by the highest average "
            "(D'Hondt). The same ballot elects the consular delegates, who are not stored here."
        ),
    ),
    "single": (
        SINGLE,
        (
            "Where a consular constituency elects one councillor, voters choose a candidate, who runs with a substitute, "
            "in one round, and the candidate with the most votes is elected. The same ballot elects the consular delegate "
            "where there is one, who is not stored here."
        ),
    ),
}
METHOD = (
    "Counted per consular constituency (circonscription électorale consulaire) by the Ministère de l'Europe et des "
    "Affaires étrangères. Votes are stored for the two channels the source reports: votes cast at a polling station "
    "(urne) are on the election-day channel, and votes cast online (vote par internet, called vote électronique in "
    "2014) are on the special-rules channel, because the archive's channel list has no online channel. Registered "
    "voters are those on the consular electoral roll at the close of the polling stations. {extra}"
)
EXTRA = {
    2014: "The source gives the figures of each constituency's votes and seats, and the turnout of each consular post "
    "separately; the posts are added to their constituency. The source's valid-vote count and the sum of the list "
    "votes differ in a few constituencies. The elected councillors are not named.",
    2021: "The source gives each list's votes by channel and the names of the elected councillors, but not the "
    "number of voters or of blank and invalid ballots. The elections in the two Indian constituencies and in "
    "Madagascar were postponed and are not in this result set.",
    2026: "The source gives each list's votes by channel and the names of the elected councillors, and the turnout "
    "of each constituency, with its blank and annulled ballots.",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(name: str) -> str:
    return key("area", changeset.slug(name))


def single_seat(place: Constituency) -> bool:
    return sum(e.seats for e in place.entries) == 1


# Countries of the constituencies that cover whole countries, as Natural Earth's ADM0_A3 codes.
COUNTRIES = {
    "Afghanistan": "AFG", "Afrique du Sud": "ZAF", "Albanie": "ALB", "Andorre": "AND", "Angola": "AGO",
    "Argentine": "ARG", "Arménie": "ARM", "Australie": "AUS", "Autriche": "AUT", "Azerbaïdjan": "AZE",
    "Bahreïn": "BHR", "Belgique": "BEL", "Birmanie": "MMR", "Biélorussie": "BLR", "Bolivie": "BOL",
    "Bosnie-Herzégovine": "BIH", "Botswana": "BWA", "Brunei": "BRN", "Bulgarie": "BGR", "Burkina Faso": "BFA",
    "Burundi": "BDI", "Bénin": "BEN", "Cambodge": "KHM", "Cameroun": "CMR", "Cap-Vert": "CPV", "Chili": "CHL",
    "Chypre": "CYP", "Colombie": "COL", "Comores": "COM", "Congo": "COG", "Corée du Sud": "KOR",
    "Costa Rica": "CRI", "Croatie": "HRV", "Cuba": "CUB", "Côte d'Ivoire": "CIV", "Danemark": "DNK",
    "Djibouti": "DJI", "Egypte": "EGY", "Emirats arabes unis": "ARE", "Equateur": "ECU", "Estonie": "EST",
    "Ethiopie": "ETH", "Fidji": "FJI", "Finlande": "FIN", "Gabon": "GAB", "Ghana": "GHA", "Grèce": "GRC",
    "Guatemala": "GTM", "Guinée": "GIN", "Guinée équatoriale": "GNQ", "Guinée-Bissau": "GNB", "Géorgie": "GEO",
    "Haïti": "HTI", "Honduras": "HND", "Hongrie": "HUN", "Indonésie": "IDN", "Irak": "IRQ", "Iran": "IRN",
    "Irlande": "IRL", "Islande": "ISL", "Jamaïque": "JAM", "Japon": "JPN", "Jordanie": "JOR", "Kazakhstan": "KAZ",
    "Kenya": "KEN", "Kirghizstan": "KGZ", "Kosovo": "KOS", "Laos": "LAO", "Lettonie": "LVA", "Liban": "LBN",
    "Libye": "LBY", "Lituanie": "LTU", "Luxembourg": "LUX", "Macédoine": "MKD", "Madagascar": "MDG",
    "Malaisie": "MYS", "Mali": "MLI", "Maurice": "MUS", "Mauritanie": "MRT", "Mexique": "MEX", "Moldavie": "MDA",
    "Monaco": "MCO", "Monténégro": "MNE", "Mozambique": "MOZ", "Namibie": "NAM", "Nicaragua": "NIC",
    "Niger": "NER", "Nigeria": "NGA", "Norvège": "NOR", "Nouvelle-Zélande": "NZL", "Oman": "OMN",
    "Ouganda": "UGA", "Ouzbékistan": "UZB", "Pakistan": "PAK", "Panamá": "PAN", "Papouasie-Nouvelle-Guinée": "PNG",
    "Paraguay": "PRY", "Pays-Bas": "NLD", "Philippines": "PHL", "Pologne": "POL", "Portugal": "PRT",
    "Pérou": "PER", "Qatar": "QAT", "Roumanie": "ROU", "Russie": "RUS", "Rwanda": "RWA",
    "République centrafricaine": "CAF", "République dominicaine": "DOM", "République démocratique du Congo": "COD",
    "République tchèque": "CZE", "Sainte-Lucie": "LCA", "Salvador": "SLV", "Serbie": "SRB", "Seychelles": "SYC",
    "Singapour": "SGP", "Slovaquie": "SVK", "Slovénie": "SVN", "Soudan": "SDN", "Soudan du Sud": "SDS",
    "Suède": "SWE", "Syrie": "SYR", "Sénégal": "SEN", "Tadjikistan": "TJK", "Tanzanie": "TZA", "Taïwan": "TWN",
    "Tchad": "TCD", "Thaïlande": "THA", "Togo": "TGO", "Trinité-et-Tobago": "TTO", "Tunisie": "TUN",
    "Turkménistan": "TKM", "Turquie": "TUR", "Ukraine": "UKR", "Uruguay": "URY", "Vanuatu": "VUT",
    "Venezuela": "VEN", "Vietnam": "VNM", "Zambie": "ZMB", "Zimbabwe": "ZWE",
}  # fmt: skip
# Natural Earth draws these countries in more than one piece.
EXTRA_PIECES = {"CYP": ["CYN", "CNM"]}


def countries_of(name: str) -> list[str] | None:
    """The countries a constituency covers, or None where it covers part of one country or more than the name says."""
    if "circonscription" in name:
        return None
    parts = re.split(r",\s*", re.sub(r"\s+et\s+", ", ", name))
    return [COUNTRIES[part] for part in parts]


def earth() -> dict[str, shapely.Geometry]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, NATURAL_EARTH).path)
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read("ne_10m_admin_0_countries.shp")),
        shx=io.BytesIO(archive.read("ne_10m_admin_0_countries.shx")),
        dbf=io.BytesIO(archive.read("ne_10m_admin_0_countries.dbf")),
        encoding="utf-8",
    )
    out: dict[str, list[shapely.Geometry]] = {}
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            continue
        code = str(record.as_dict()["ADM0_A3"])
        out.setdefault(code, []).append(shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__)))
    return {code: shapely.union_all(shapes) for code, shapes in out.items()}


def all_names() -> list[str]:
    return sorted(canonical_names().values())


def geo() -> Package:
    p = Package(
        "fr.consulaires/geo",
        SOURCE,
        "The 130 consular constituencies of the French residents abroad, with outlines where a constituency covers "
        "whole countries",
    )
    p.external.add(FRANCE)
    p.add(
        "source",
        id=EARTH_SOURCE,
        kind="community",
        publisher="Natural Earth",
        title="Natural Earth Admin 0 - Countries, 1:10m",
        url="https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-countries/",
        license="Public domain",
        license_url="https://www.naturalearthdata.com/about/terms-of-use/",
        attribution="Made with Natural Earth",
    )
    p.artifact(raw.cached(SOURCE, NATURAL_EARTH), EARTH_SOURCE)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=FRANCE,
        kind="electoral_district",
        rank=1,
        noun="consular constituency",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", 2014),
        scheme_code=SCHEME,
        vintage="2014",
        valid_from=date(2014, 1, 1),
        as_of=raw.cached(SOURCE, NATURAL_EARTH).retrieved_at.date(),
    )
    shapes = earth()
    for name in all_names():
        id = area(name)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=changeset.slug(name))
        p.name(id, name, "fr", "official")
        codes = countries_of(name)
        if codes is None:
            continue
        pieces = [shapes[extra] for code in codes for extra in [code, *EXTRA_PIECES.get(code, [])] if extra in shapes]
        if len(pieces) < len(codes):
            raise LookupError(f"no outline for {name}: {codes}")
        shape = shapely.simplify(shapely.union_all(pieces), 0.02)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=EARTH_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        "fr.consulaires/registry",
        SOURCE,
        "The body of consular councillors of the French residents abroad and the electoral systems it was elected by",
        depends=["fr.consulaires/geo"],
    )
    p.external.add(FRANCE)
    for year in YEARS:
        page = year == 2026
        p.add(
            "source",
            id=SOURCES[year],
            kind="official_body",
            publisher=MINISTRY,
            title=f"Élections des conseillers des Français de l'étranger {year}: résultats",
            url=PAGES[year],
            license="Réutilisation libre des informations publiques (CRPA, articles L321-1 et suivants)"
            if page
            else ("Licence Ouverte v1.0" if year == 2014 else "Licence Ouverte v2.0"),
            license_url="https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000031366350/LEGISCTA000031367344/"
            if page
            else "https://www.etalab.gouv.fr/licence-ouverte-open-licence",
            attribution=MINISTRY,
        )
        for name in FILES_OF[year]:
            p.artifact(raw.cached(SOURCE, FILES[name]), SOURCES[year])
    p.add("body", id=BODY, polity_id=FRANCE, kind="council")
    p.name(BODY, "Conseillers des Français de l'étranger", "fr", "official")
    p.name(BODY, "Consular councillors of French residents abroad", "en", "english", False)
    for name, (rules, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=FRANCE,
            valid_from=date(2014, 1, 1),
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return p


def cfde(year: int) -> Package:
    first, last = DAYS[year]
    p = Package(
        f"fr.consulaires/cfde-{year}",
        SOURCE,
        f"The {year} elections of consular councillors by consular constituency from the Ministère de l'Europe et "
        "des Affaires étrangères",
        depends=["fr.consulaires/geo", "fr.consulaires/registry"],
    )
    p.external.add(FRANCE)
    for name in FILES_OF[year]:
        p.uses(raw.cached(SOURCE, FILES[name]))
    source = SOURCES[year]
    election = key("election", year)
    p.add("election", id=election, polity_id=FRANCE, kind="general", date_from=first, date_to=last, status="held")
    p.name(election, f"Élections des conseillers des Français de l'étranger {year}", "fr", "official")
    p.name(election, f"{year} consular councillor elections (French residents abroad)", "en", "english", False)
    path = f"fr/{first}-consular-councillors"
    p.add("slug", path=path, entity_id=election, is_current=True)
    for place in LOADERS[year]():
        single = single_seat(place)
        seats = sum(e.seats for e in place.entries)
        slug = changeset.slug(place.name)
        contest = key("contest", year, slug)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats" if single else "list_allocation",
            body_id=BODY,
            district_area_id=area(place.name),
            magnitude=seats,
            round_number=1,
            electoral_system_id=key("electoral_system", "single" if single else "list"),
            default_result_kind="counted",
            boundary_set_id=key("boundary_set", 2014),
        )
        p.name(contest, place.name, "fr", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        unit = key("unit", year, slug)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=FRANCE,
            scheme_code=SCHEME,
            code=f"{first}:{slug}",
            area_relation="equals",
            area_id=area(place.name),
            kind="standard",
        )
        result_set = key("result_set", year, slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(extra=EXTRA[year]),
            lang="en",
        )
        accounting = {
            2: place.registered,
            3: place.voters,
            4: place.valid,
            5: place.invalid,
            6: place.blank,
        }
        for measure, value in accounting.items():
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        for entry in place.entries:
            tag = f"{year}:{slug}:{entry.number}"
            option = key("option", tag)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate" if single else "list",
                ballot_position=entry.number,
                ballot_label=entry.label,
            )
            names = [entry.label] if single else entry.elected
            for index, person_name in enumerate(names):
                person = key("person", tag, index, person_name)
                candidacy = key("candidacy", tag, index, person_name)
                p.add("person", id=person, birth_precision=None)
                p.name(person, person_name, "fr", "official")
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head" if single else "list_member",
                    ballot_name=person_name,
                )
                if not single:
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
            if entry.seats > 0:
                if single:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=entry.seats)
                else:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry.seats)
            for channel, value in ((1, entry.urne), (9, entry.internet)):
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=channel,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
    if year == 2014:
        p.accept(
            "accounting.votes_valid",
            "In a few constituencies the authority's turnout workbook counts a different number of valid votes than the "
            "sum of the list votes in its results workbook; both are stored as published.",
        )
    return p


def packages() -> list[str]:
    return ["fr.consulaires/geo", "fr.consulaires/registry", *(f"fr.consulaires/cfde-{year}" for year in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        cfde(year).write()


def emit() -> None:
    package.run("load", packages())
