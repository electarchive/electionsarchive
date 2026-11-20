"""Palestine's local elections of 11 December 2021 and 26 March 2022 from the Central Elections Commission.

The Commission ran the 2021 local elections in two phases and publishes each as PDF tables by local authority.
Where more than one list stood (154 authorities in the first phase, 50 in the second) it prints each list's
candidates, votes, and seats with the authority's registered voters and ballots cast, and names the winners. Where
one list stood (162 and 23 authorities) the Commission declared it elected by acclamation (tazkiya) and prints the
list and its winners with no count. Every council is elected from closed lists by the Sainte-Laguë method with an
8 percent threshold. The tables embed fonts whose text map drops the letters of lam-alef and Allah ligatures; the
`ligature_pdf` module restores them before the names are read.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ps import cec_local, ligature_pdf
from ea.sources.ps.cec_local_tables import Box, Cell, number
from ea.sources.ps.ligature_pdf import Line

SOURCE = "ps.cec_local21"
SITE = "https://www.elections.ps"
PALESTINE = cec_local.PALESTINE
COUNTRY = cec_local.COUNTRY
FIRST = date(2021, 12, 11)
SECOND = date(2022, 3, 26)
VOTES, REGISTERED, CAST = 1, 2, 3
FILES = {
    "page1": f"{SITE}/tabid/1204/language/ar-PS/Default.aspx",
    "page2": f"{SITE}/tabid/1215/language/ar-PS/Default.aspx",
    "seats1": f"{SITE}/Portals/0/26_12-ResultFinalSeatsDistribution.pdf",
    "winners1": f"{SITE}/Portals/0/26_12-ResultFinalWinnersNames.pdf",
    "acclaimed-lists1": f"{SITE}/Portals/0/WinnerByAcclamation2021.pdf",
    "acclaimed-names1": f"{SITE}/Portals/0/ResultWinnersByAcclamation2021.pdf",
    "seats2": f"{SITE}/Portals/0/LE2021-2stg-SeatsDistribution2022WithPol_1.pdf",
    "winners2": f"{SITE}/Portals/0/LE2021-2stg-WinnersNamesByVoting2022_1.pdf",
    "acclaimed-lists2": f"{SITE}/Portals/0/ResultWinnerByAcclamation5_4_2022.pdf",
    "acclaimed-names2": f"{SITE}/Portals/0/LE2021-2stg-ResultWinnersNamesByAcclamation2022.pdf",
    "report": f"{SITE}/Portals/0/2021localElections_finalReport_1.pdf",
}
DAYS = {1: FIRST, 2: SECOND}
PHASES = {
    1: {"polled": 154, "acclaimed": 162, "contested_seats": 1503, "registered": 405687, "cast": 268318},
    2: {"polled": 50, "acclaimed": 23, "contested_seats": 632, "registered": 715413, "cast": 384098},
}
INDEPENDENT = "مستقلة"
FATAH_TYPE = "حركة فتح"
THRESHOLD = 8
SYSTEM_NOTE = (
    "Local Elections Law no. 10 of 2005 as amended sets proportional representation from closed lists for every "
    "local council: the voter chooses one list, a list's votes are divided by the odd numbers (the Sainte-Laguë "
    "method), lists below 8 percent of the valid votes win no seats, and a list's seats go to its candidates in "
    "list order, with the representation of women and the Muslim-Christian quota taken into account (a seat can "
    "pass to the first woman not yet elected on the list). Seats are stored as declared."
)
LIST_METHOD = (
    "Counted by the Central Elections Commission per local authority. The Commission's table prints each list's "
    "candidates, votes, and seats, and the authority's registered voters and ballots cast; it prints no valid, "
    "invalid, or blank totals for the authority, so none is stored. A list's votes are the ballots for it. Seats "
    "are stored as declared, and the winners are the candidates the Commission names for each list."
)
TACIT_METHOD = (
    "No vote was held for this council: one list stood, and the Commission declared its candidates elected by "
    "acclamation (tazkiya). The Commission prints the list and its winners and the council's seats; it counts no "
    "ballots, so the set holds no votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
SYSTEM = key("electoral_system", "list")
FATAH = key("party", "fatah")


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".aspx" if url.endswith(".aspx") else ".pdf")
    cec_local.fetch()  # the 2026 tables say which reporting units the 2026 package already writes


def packages() -> list[str]:
    return [f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{FIRST}", f"{SOURCE}/{SECOND}"]


def parse() -> None:
    cec_local.geo().write()
    registry().write()
    for phase in (1, 2):
        election(phase).write()


def emit() -> None:
    package.run("load", packages())


def path(name: str) -> Path:
    return raw.cached(SOURCE, FILES[name]).path


# --- reading the tables ---------------------------------------------------------------------------------------------


def norm(text: str) -> str:
    return re.sub(r"[\s()]", "", text)


def match(text: str) -> str:
    """A name as the tables spell it, folded so that the same name in two tables compares equal."""
    text = re.sub(r"[^\w]|[\d_ـ]", "", text)
    for source, target in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        text = text.replace(source, target)
    return text


class Row(NamedTuple):
    cells: dict[str, Cell]


@dataclass
class Council:
    district: str
    authority: str
    seats: int
    page: int
    registered: Cell | None = None
    cast: Cell | None = None
    list_name: str | None = None
    lists: list[dict[str, Cell]] | None = None
    winners: list[tuple[int, str, str | None]] | None = None  # position, name, list name

    @property
    def ident(self) -> str:
        return f"{self.district}:{norm(self.authority)}"

    @property
    def matching(self) -> tuple[str, str]:
        return match(self.district), match(self.authority)


def _box(line: Line, page: int | None = None) -> Box:
    return Box(page or line.page, line.x0, line.y0, line.x1, line.y1)


def _pages(lines: list[Line]) -> dict[int, list[Line]]:
    out: dict[int, list[Line]] = defaultdict(list)
    for line in lines:
        out[line.page].append(line)
    return out


def _is_footer(text: str) -> bool:
    return bool(re.fullmatch(r"\d{4}/\d+/\d+|Page \d+ of \d+|OM\w*|NO\w*", text))


def _beside(lines: list[Line], label: Line) -> Line:
    """The value printed to the left of a label on its row."""
    band = [
        line
        for line in lines
        if line is not label
        and abs((line.y0 + line.y1) / 2 - (label.y0 + label.y1) / 2) < 12
        and line.x1 <= label.x0 + 2
        and not line.text.endswith(":")
    ]
    if not band:
        raise ValueError(f"page {label.page}: no value beside {label.text!r}")
    return min(band, key=lambda line: label.x0 - line.x1)


def _label(lines: list[Line], *starts: str) -> Line:
    for line in lines:
        if line.text.startswith(starts):
            return line
    raise ValueError(f"page {lines[0].page}: no label {starts}")


def _rows(lines: list[Line], anchors: list[Line]) -> list[list[Line]]:
    """Groups lines under the anchor line nearest in height."""
    groups: list[list[Line]] = [[] for _ in anchors]
    for line in lines:
        middle = (line.y0 + line.y1) / 2
        nearest = min(range(len(anchors)), key=lambda i: abs((anchors[i].y0 + anchors[i].y1) / 2 - middle))
        groups[nearest].append(line)
    return groups


def _text(parts: list[Line]) -> str:
    ordered = sorted(parts, key=lambda line: (round((line.y0 + line.y1) / 2 / 6), -line.x1))
    return ligature_pdf.clean(" ".join(line.text for line in ordered))


def seat_councils(file: Path) -> list[Council]:
    """The polled authorities: the header, and a row for each list with its type, candidates, votes, and seats."""
    out: list[Council] = []
    all_lines = ligature_pdf.lines(file)
    all_words = ligature_pdf.words(file)
    for page, lines in sorted(_pages(all_lines).items()):
        district = _label(lines, "المنطقة الانتخابية:").text.removeprefix("المنطقة الانتخابية:").strip()
        authority = _label(lines, "الهيئة المحلية:").text.removeprefix("الهيئة المحلية:").strip()
        band = next(line for line in lines if "عدد الناخبين" in line.text)
        figures = sorted(
            (
                w
                for w in all_words[page - 1]
                if band.y0 - 1 <= w[1] and w[3] <= band.y1 + 1 and re.fullmatch(r"%?[\d,.]+%?", w[4])
            ),
            key=lambda w: -w[0],
        )
        if len(figures) != 4:
            raise ValueError(f"page {page}: the header figures are {[w[4] for w in figures]}")
        cells = [Cell(w[4], Box(page, w[0], w[1], w[2], w[3])) for w in figures]
        registered, cast, turnout, seats = cells
        top = max(line.y1 for line in lines if line.text in {"المرشحين", "الاصوات", "صفة الترشح", "القائمة الانتخابية"})
        # the table's words, not its text runs: a run can join a wrapped type with the candidates beside it
        body = [Line(page, w[0], w[1], w[2], w[3], w[4]) for w in all_words[page - 1] if w[1] > top - 4]
        body = [line for line in body if not _is_footer(line.text)]

        def column(line: Line) -> str:
            middle = (line.x0 + line.x1) / 2
            if line.x1 > 417:
                return "name"  # the type column ends at 413; a longer name spills left of its own column
            if re.fullmatch(r"[\d,]+", line.text):
                return (
                    "candidates"
                    if middle >= 150 and middle < 230
                    else "votes"
                    if middle >= 80 and middle >= 80 and middle < 150
                    else "seats"
                    if middle < 80
                    else "type"
                )
            return "type"

        anchors = [line for line in body if column(line) == "candidates" and re.fullmatch(r"\d+", line.text)]
        rows: list[dict[str, Cell]] = []
        for anchor, group in zip(anchors, _rows(body, anchors), strict=True):
            row: dict[str, Cell] = {}
            for name in ("name", "type", "candidates", "votes", "seats"):
                parts = [line for line in group if column(line) == name]
                if not parts:
                    raise ValueError(f"page {page}: a list row without {name}: {[line.text for line in group]}")
                row[name] = Cell(_text(parts), Box(page, *_union(parts)))
            rows.append(row)
        current = Council(
            district, authority, number(seats.text), page, _figure(registered), _figure(cast), None, rows, None
        )
        if int(turnout.text.strip("%").split(".")[0]) > 100:
            raise ValueError(f"page {page}: turnout {turnout.text}")
        previous = out[-1] if out else None
        if previous and (previous.district, previous.authority) == (district, authority):
            assert previous.lists is not None
            previous.lists.extend(rows)
        else:
            out.append(current)
    return out


def _figure(cell: Cell) -> Cell:
    return Cell(str(number(cell.text)), cell.box)


def _union(parts: list[Line]) -> tuple[float, float, float, float]:
    return min(p.x0 for p in parts), min(p.y0 for p in parts), max(p.x1 for p in parts), max(p.y1 for p in parts)


def winner_councils(file: Path, acclaimed: bool) -> list[Council]:
    """The winners of each authority, by list for the polled ones, and the acclaimed list for the others."""
    out: list[Council] = []
    for page, lines in sorted(_pages(ligature_pdf.lines(file)).items()):
        district = _beside(lines, _label(lines, ":المنطقة")).text
        authority = _beside(lines, _label(lines, "الهيـئــة")).text
        seats = number(_beside(lines, _label(lines, "عــدد المقــاعــد", "عدد المقاعد")).text)
        list_name = _beside(lines, _label(lines, "اســــــم القــائمــة")).text if acclaimed else None
        header = _label(lines, "اسم الفائز")
        body = [line for line in lines if line.y0 > header.y1 - 4 and not _is_footer(line.text)]
        body = [line for line in body if line.text not in {"اسم القائمة", "اسم الفائز"}]
        # one anchor per row: the name (or the position beside it), the right-most text of the row
        rows: dict[int, list[Line]] = defaultdict(list)
        for line in sorted(body, key=lambda line: (line.y0 + line.y1) / 2):
            middle = (line.y0 + line.y1) / 2
            for key_, parts in rows.items():
                if abs(key_ - middle) < 14:
                    parts.append(line)
                    break
            else:
                rows[round(middle)].append(line)
        winners: list[tuple[int, str, str | None]] = []
        for parts in rows.values():
            index = [p for p in parts if re.fullmatch(r"\d+", p.text) and p.x0 >= 500]
            names = [p for p in parts if p.x1 >= 300 and p not in index]
            lists = [p for p in parts if p.x1 < 300]
            name = _text(names)
            if index:
                position = int(index[0].text)
            else:
                glued = re.fullmatch(r"(.*\D)(\d+)", name)
                if glued is None:
                    raise ValueError(f"page {page}: a winner without a position: {name!r}")
                name, position = glued.group(1).strip(), int(glued.group(2))
            winners.append((position, name, _text(lists) if lists else None))
        winners.sort()
        council = Council(district, authority, seats, page, None, None, list_name, None, winners)
        previous = out[-1] if out else None
        if previous and (previous.district, previous.authority) == (district, authority):
            assert previous.winners is not None
            previous.winners.extend(winners)
        else:
            out.append(council)
    return out


class Accepted(NamedTuple):
    district: str
    authority: str
    list_name: str
    seats: int
    type: str | None
    page: int


def acclaimed_lists(file: Path, typed: bool) -> list[Accepted]:
    out: list[Accepted] = []
    for page, lines in sorted(_pages(ligature_pdf.lines(file)).items()):
        district = _beside(lines, _label(lines, ":المنطقة")).text
        header = max(line.y1 for line in lines if line.text in {"المقاعد", "عدد"})
        body = [line for line in lines if line.y0 > header - 2 and not _is_footer(line.text)]
        body = [line for line in body if line.text not in {"عدد", "المقاعد", "صفة الترشح"}]
        anchors = [line for line in body if re.fullmatch(r"\d+", line.text) and line.x0 >= 560]
        for anchor, group in zip(anchors, _rows(body, anchors), strict=True):
            parts = [line for line in group if line is not anchor]
            seats = [p for p in parts if re.fullmatch(r"\d+", p.text) and p.x1 < 60]
            rest = [p for p in parts if p not in seats]
            authority = [p for p in rest if p.x1 >= 520]
            kind = [p for p in rest if typed and p.x1 <= 200]
            names = [p for p in rest if p not in authority and p not in kind]
            if len(seats) != 1 or not authority or not names:
                raise ValueError(f"page {page}: a row of {[p.text for p in parts]}")
            out.append(
                Accepted(
                    district,
                    _text(authority),
                    _text(names),
                    int(seats[0].text),
                    _text(kind) if kind else None,
                    page,
                )
            )
    return out


@dataclass(frozen=True)
class Authority:
    phase: int
    district: str
    name: str
    seats: int
    polled: bool
    registered: Cell | None
    cast: Cell | None
    lists: list[dict[str, Cell]]  # a polled council's lists
    list_name: str | None
    list_type: str | None
    winners: list[tuple[int, str, str | None]]

    @property
    def ident(self) -> str:
        return f"{self.district}:{norm(self.name)}"


@cache
def authorities(phase: int) -> list[Authority]:
    out: list[Authority] = []
    everyone = seat_councils(path(f"seats{phase}"))
    seats = {c.matching: c for c in everyone if not _unheld(c)}
    winners = {c.matching: c for c in winner_councils(path(f"winners{phase}"), False)}
    if set(seats) != set(winners):
        raise ValueError(
            f"phase {phase}: the list and winner tables name different councils: {set(seats) ^ set(winners)}"
        )
    for k, council in sorted(seats.items()):
        won = winners[k]
        if won.seats != council.seats:
            raise ValueError(
                f"{council.ident}: {won.seats} seats in the winners table and {council.seats} in the lists"
            )
        assert council.lists is not None and won.winners is not None
        out.append(
            Authority(
                phase,
                council.district,
                council.authority,
                council.seats,
                True,
                council.registered,
                council.cast,
                council.lists,
                None,
                None,
                won.winners,
            )
        )
    names = {c.matching: c for c in winner_councils(path(f"acclaimed-names{phase}"), True)}
    tables = {
        (match(a.district), match(a.authority)): a for a in acclaimed_lists(path(f"acclaimed-lists{phase}"), phase == 2)
    }
    if set(names) != set(tables):
        raise ValueError(f"phase {phase}: the acclaimed tables name different councils: {set(names) ^ set(tables)}")
    for k, council in sorted(names.items()):
        row = tables[k]
        if row.seats != council.seats:
            raise ValueError(f"{council.ident}: the acclaimed tables disagree: {row} against {council.list_name}")
        assert council.winners is not None
        out.append(
            Authority(
                phase,
                council.district,
                council.authority,
                council.seats,
                False,
                None,
                None,
                [],
                council.list_name,
                row.type,
                council.winners,
            )
        )
    idents = [a.ident for a in out]
    if len(set(idents)) != len(idents):
        raise ValueError(f"two authorities share a name: {[i for i, n in Counter(idents).items() if n > 1]}")
    unknown = {a.district for a in out} - set(cec_local.DISTRICTS)
    if unknown:
        raise ValueError(f"districts without a governorate: {unknown}")
    return out


def _unheld(council: Council) -> bool:
    """A council the table lists with no ballots, votes, or seats: its ballot was not held in this phase."""
    assert council.lists is not None and council.cast is not None
    return council.cast.text == "0" and all(row["votes"].text == row["seats"].text == "0" for row in council.lists)


@cache
def unheld(phase: int) -> list[Council]:
    return [c for c in seat_councils(path(f"seats{phase}")) if _unheld(c)]


def types() -> set[str]:
    kinds: set[str] = set()
    for phase in (1, 2):
        for a in authorities(phase):
            kinds.update(row["type"].text for row in a.lists)
            if a.list_type:
                kinds.add(a.list_type)
    return kinds


# --- packages -------------------------------------------------------------------------------------------------------

# The parties a list's nomination type names alone, by the Commission's Arabic name and an English name. A type that
# names several parties, or a coalition, belongs to no single party.
PARTIES = {
    "حركة فتح": ("fatah", "Fatah"),
    "الجبهة الديمقراطية لتحرير فلسطين": ("dflp", "Democratic Front for the Liberation of Palestine"),
    "الجبهة الشعبية لتحرير فلسطين": ("pflp", "Popular Front for the Liberation of Palestine"),
    "المبادرة الوطنية الفلسطينية": ("pni", "Palestinian National Initiative"),
    "جبهة النضال الشعبي الفلسطيني": ("ppsf", "Palestinian Popular Struggle Front"),
    "حزب الشعب الفلسطيني": ("ppp", "Palestinian People's Party"),
}


def party_of(kind: str | None) -> str | None:
    return key("party", PARTIES[kind][0]) if kind in PARTIES else None


def fold(text: str) -> str:
    """A list's name without its spacing, whose digits the tables print at either end."""
    text = text.replace("ـ", "")
    for source, target in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        text = text.replace(source, target)
    return re.sub(r"[\W\d_]", "", text) + "#" + "".join(re.findall(r"\d+", text))


def words(text: str) -> list[str]:
    return sorted(re.findall(r"[^\W_]+", text.replace("ـ", "")))


def resolve(name: str, names: dict[str, str]) -> str | None:
    """The list a winners table names, among the lists `names` maps from their folded names to the printed ones:
    the one it spells the same, with the same words in another order, or nearly the same (a long name is cut
    short in one table)."""
    wanted = fold(name)
    if wanted in names:
        return wanted
    same = [label for label, printed in names.items() if words(printed) == words(name)]
    if len(same) == 1:
        return same[0]
    ranked = sorted(((SequenceMatcher(None, wanted, label).ratio(), label) for label in names), reverse=True)
    if ranked and ranked[0][0] >= 0.8 and (len(ranked) == 1 or ranked[1][0] < ranked[0][0] - 0.1):
        return ranked[0][1]
    return None


def body(authority: Authority) -> str:
    """An authority's council, shared with the 2026 pipeline: the same code takes the same id there and here."""
    return cec_local.key("body", authority.ident)


@cache
def _written_bodies() -> frozenset[str]:
    """The codes of the authorities whose council the 2026 registry already writes."""
    return frozenset(a.ident for a in cec_local.authorities())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Palestine's local councils, the electoral system of the 2021-2022 local elections, and the parties the "
        "lists name",
        depends=[f"{cec_local.SOURCE}/geo", f"{cec_local.SOURCE}/registry"],
    )
    p.external |= {PALESTINE, COUNTRY}
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="لجنة الانتخابات المركزية - فلسطين (Central Elections Commission, Palestine)",
        title="Results of the local elections of 11 December 2021 and 26 March 2022 published by the Central "
        "Elections Commission",
        url=FILES["page1"],
        license="Official publication; reuse terms are not stated",
        attribution="Central Elections Commission, Palestine",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), CEC_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=PALESTINE,
        rules={"formula": "sainte_lague", "threshold": {"percent": THRESHOLD, "level": "list", "scope": "contest"}},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for arabic, (tag, english) in PARTIES.items():
        id = key("party", tag)
        p.add("party", id=id, kind="party", polity_id=PALESTINE)
        p.name(id, arabic, "ar", "official")
        p.name(id, english, "en", "english", False)
        p.add("external_id", entity_id=id, scheme="ps.cec_local21.party", value=tag)
    seen: set[str] = set()
    for phase in (1, 2):
        for authority in authorities(phase):
            id = body(authority)
            if authority.ident in _written_bodies():
                p.external.add(id)
                continue
            if id not in seen:
                seen.add(id)
                p.add("body", id=id, polity_id=PALESTINE, kind="council")
                p.name(id, f"الهيئة المحلية {authority.name}", "ar", "official")
    return p


def _source_pages(p: Package, name: str) -> dict[int, str]:
    artifact = raw.cached(SOURCE, FILES[name])
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CEC_SOURCE}\x1f{artifact.storage_key}"))
    pages: dict[int, str] = {}
    document = pymupdf.open(artifact.path)
    for number_ in range(1, document.page_count + 1):
        page = document[number_ - 1]
        pages[number_] = key("page", name, number_)
        p.add(
            "source_page",
            id=pages[number_],
            artifact_id=artifact_id,
            page_number=number_,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{number_}.png",
            width=round(page.rect.width),
            height=round(page.rect.height),
        )
    return pages


def _put(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, cell: Cell, pages: dict[int, str]
) -> int:
    value = number(cell.text)
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
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=pages[cell.box.page],
        source_id=CEC_SOURCE,
        x0=cell.box.x0,
        y0=cell.box.y0,
        x1=cell.box.x1,
        y1=cell.box.y1,
    )
    return value


def _candidates(
    p: Package,
    authority: Authority,
    contest: str,
    result_set: str,
    options: dict[str, str],
    kinds: dict[str, str | None],
    names: dict[str, str],
) -> None:
    for position, name, list_name in authority.winners:
        label = resolve(list_name, names) if list_name else next(iter(options))
        if label is None:
            raise ValueError(f"{authority.ident}: winner {name} is on an unknown list {list_name!r}")
        parent = options[label]
        tag = f"{authority.phase}:{authority.ident}:{position}"
        person = key("person", tag)
        p.add("person", id=person)
        p.name(person, name, "ar", "official")
        option = key("option", tag)
        party = party_of(kinds[label])
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=parent,
            party_id=party,
            ballot_position=position,
            ballot_label=name,
        )
        candidacy = key("candidacy", tag)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=parent,
            role="list_member",
            list_position=position,
            ballot_name=name,
            member_of_party_id=party,
            is_independent=True if kinds[label] == INDEPENDENT else None,
        )
        p.add("outcome", result_set_id=result_set, option_id=parent, candidacy_id=candidacy, kind="elected")


def _list_option(p: Package, authority: Authority, contest: str, position: int, label: str, kind: str | None) -> str:
    option = key("option", authority.phase, authority.ident, position)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="list",
        party_id=party_of(kind),
        ballot_position=position,
        ballot_label=label,
    )
    if kind and kind != INDEPENDENT and kind not in PARTIES:
        p.add(
            "note",
            id=key("note", option),
            entity_id=option,
            kind="context",
            body_md=f"The Commission records the list's nomination as: {kind}.",
            lang="en",
        )
    return option


@cache
def _written() -> frozenset[str]:
    """The codes of the authorities whose reporting unit the 2026 pipeline already writes."""
    return frozenset(a.ident for a in cec_local.authorities() if a.polled)


def _unit(p: Package, authority: Authority) -> str:
    """An authority's reporting unit, shared with the 2026 pipeline: the same code takes the same id there and here.

    A unit the 2026 package already writes is referenced, not written again; any other is written under the key the
    2026 pipeline would give it, so a later election of the same council reuses it.
    """
    unit = cec_local.key("unit", authority.ident)
    if authority.ident in _written():
        p.external.add(unit)
        return unit
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=PALESTINE,
        scheme_code="ps.local_authority",
        code=authority.ident,
        area_relation="within",
        area_id=cec_local.governorate(authority.district),
        kind="standard",
    )
    p.name(unit, authority.name, "ar", "official")
    return unit


def election(phase: int) -> Package:
    day = DAYS[phase]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Palestinian local elections of {day:%-d %B %Y} ({'first' if phase == 1 else 'second'} phase): the "
        "councils of the West Bank's local authorities, by authority, from the Central Elections Commission's tables",
        depends=[
            f"{cec_local.SOURCE}/geo",
            f"{cec_local.SOURCE}/{cec_local.DAY}",
            f"{cec_local.SOURCE}/registry",
            f"{SOURCE}/registry",
        ],
    )
    p.external |= {PALESTINE, COUNTRY}
    for url in FILES.values():
        p.uses(raw.cached(SOURCE, url))
    pages = _source_pages(p, f"seats{phase}")
    election_id = key("election", day)
    p.add("election", id=election_id, polity_id=PALESTINE, kind="local", date_from=day, date_to=day, status="held")
    p.name(
        election_id,
        f"2021 Palestinian local elections, {'first' if phase == 1 else 'second'} phase",
        "en",
        "official",
    )
    p.name(
        election_id, f"الانتخابات المحلية 2021 - المرحلة {'الأولى' if phase == 1 else 'الثانية'}", "ar", "native", False
    )
    prefix = f"ps/{day}-local"
    p.add("slug", path=prefix, entity_id=election_id, is_current=True)
    ordinal: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    for authority in sorted(authorities(phase), key=lambda a: (a.district, a.name)):
        ordinal[authority.district] += 1
        slug = (
            f"{prefix}/{changeset.slug(cec_local.DISTRICTS[authority.district][0])}-{ordinal[authority.district]:03d}"
        )
        contest = key("contest", authority.phase, authority.ident)
        result_set = key("result_set", authority.phase, authority.ident)
        kind = "counted" if authority.polled else "tacit"
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(authority),
            magnitude=authority.seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind=kind,
        )
        p.name(contest, f"الهيئة المحلية {authority.name}، {day.year}", "ar", "official")
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind=kind,
            source_id=CEC_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0 if authority.polled else None,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=LIST_METHOD if authority.polled else TACIT_METHOD,
            lang="en",
        )
        options: dict[str, str] = {}
        kinds: dict[str, str | None] = {}
        names: dict[str, str] = {}
        if authority.polled:
            unit = _unit(p, authority)
            assert authority.registered is not None and authority.cast is not None
            counts["registered"] += _put(p, result_set, unit, None, REGISTERED, authority.registered, pages)
            counts["cast"] += _put(p, result_set, unit, None, CAST, authority.cast, pages)
            for position, row in enumerate(authority.lists, 1):
                label = fold(row["name"].text)
                if label in options:
                    raise ValueError(f"{authority.ident}: two lists named {row['name'].text!r}")
                kinds[label] = row["type"].text
                names[label] = row["name"].text
                option = options[label] = _list_option(
                    p, authority, contest, position, row["name"].text, row["type"].text
                )
                seats = number(row["seats"].text)
                p.add(
                    "outcome", result_set_id=result_set, option_id=option, kind="won" if seats else "lost", seats=seats
                )
                counts["votes"] += _put(p, result_set, unit, option, VOTES, row["votes"], pages)
                counts["seats"] += seats
            won = Counter(resolve(name or "", names) for _, _, name in authority.winners)
            for row in authority.lists:
                if won[fold(row["name"].text)] != number(row["seats"].text):
                    raise ValueError(
                        f"{authority.ident}: list {row['name'].text!r} has {won[fold(row['name'].text)]} winners"
                    )
        else:
            assert authority.list_name is not None
            label = fold(authority.list_name)
            kinds[label] = authority.list_type
            names[label] = authority.list_name
            options[label] = _list_option(p, authority, contest, 1, authority.list_name, authority.list_type)
            p.add(
                "outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=len(authority.winners)
            )
            counts["seats"] += len(authority.winners)
        _candidates(p, authority, contest, result_set, options, kinds, names)
        counts["winners"] += len(authority.winners)
        counts["polled" if authority.polled else "acclaimed"] += 1
    _verify(phase, counts)
    short = [a for a in authorities(phase) if len(a.winners) != a.seats]
    if short:
        p.accept(
            "outcomes.seats_magnitude",
            "The Commission declares fewer winners than seats for "
            + ", ".join(f"{a.name} ({a.district}) {len(a.winners)} of {a.seats}" for a in short)
            + ", because too few candidates were nominated; the declared seats are the winners it names and the "
            "council's size is as printed.",
        )
    note = _summary(phase, counts)
    p.add("note", id=key("note", election_id, "totals"), entity_id=election_id, kind="caveat", body_md=note, lang="en")
    return p


def _expected(phase: int) -> dict[str, int]:
    """The Commission's national figures: the authorities that voted and were acclaimed, the contested seats, and (from its
    report's table by district) the registered voters and ballots cast. In the first phase the report counts the 6,815
    registered voters of Jaba' (Jenin), whose table here prints no ballots, and 2 more ballots than the council tables."""
    figures = dict(PHASES[phase])
    if phase == 1:
        figures["polled"] -= len(unheld(1))
        figures["registered"] -= sum(int(c.registered.text) for c in unheld(1) if c.registered)
        figures["cast"] -= 2
    return figures


def _verify(phase: int, counts: Counter[str]) -> None:
    expected = _expected(phase)
    contested = sum(a.seats for a in authorities(phase) if a.polled)
    found = {
        "polled": counts["polled"],
        "acclaimed": counts["acclaimed"],
        "contested_seats": contested,
        "registered": counts["registered"],
        "cast": counts["cast"],
    }
    if found != expected:
        raise ValueError(
            f"phase {phase}: the councils differ from the Commission's figures: {found} against {expected}"
        )
    if counts["votes"] > counts["cast"]:
        raise ValueError(f"phase {phase}: more votes than ballots cast")


def _summary(phase: int, counts: Counter[str]) -> str:
    figures = PHASES[phase]
    text = (
        f"The Commission's report gives {figures['polled']} authorities that voted, {figures['acclaimed']} declared "
        f"by acclamation, and {figures['contested_seats']:,} contested seats. The tables here hold "
        f"{counts['polled']} authorities that voted, {counts['acclaimed']} by acclamation, and "
        f"{counts['registered']:,} registered voters and {counts['cast']:,} ballots cast in the authorities that voted."
    )
    if phase == 1:
        held = unheld(1)
        text += (
            " The first phase's table of lists also prints Jaba' in Jenin governorate "
            f"({', '.join(f'{c.registered.text} registered voters' for c in held if c.registered)}) with no ballots, "
            "votes, or seats, because its ballot was not held that day; it voted in the second phase, and this "
            "election holds no contest for it. The report's table by district counts those registered voters and "
            "268,318 ballots cast, 2 more than the council tables add up to."
        )
    return text
