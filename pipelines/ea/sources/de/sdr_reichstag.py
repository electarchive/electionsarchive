import itertools
import json
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

BASE = "https://www.digitalereichsstatistik.de/drsa"
SOURCE = "de.sdr_reichstag"
# Each volume's table pages, by scan page: (first, last). A page's file is drsa_{volume}_{scan}_{printed}.html.
VOLUMES = {"140": (636, 695), "371": (600, 671), "531": (180, 215)}
# The tables of Wahlkreise: (volume, first scan page, last scan page, layout). Volume 14 holds the first ballots of
# 1871 and 1874 in its Table I and their second ballots in Table II; volumes 37/1 and 53/1 hold one survey per
# election, each ballot on its own row. The pages after each survey sum the majorities by state and are not read.
TABLES = {
    "1871-first": ("140", 636, 685, "first"),
    "1871-second": ("140", 686, 695, "second"),
    "1877": ("371", 600, 631, "survey"),
    "1878": ("371", 636, 667, "survey"),
    "1881": ("531", 180, 211, "survey"),
}


@dataclass
class Cell:
    text: str = ""
    span: int = 1
    rows: int = 1


@dataclass
class Row:
    cells: list[Cell] = field(default_factory=list)


class _Tables(HTMLParser):
    """Collect each table's rows and cells, with each cell's spans."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[Row]] = []
        self.cell: Cell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.tables[-1].append(Row())
        elif tag == "td" and self.tables and self.tables[-1]:
            self.cell = Cell(span=int(a.get("colspan") or 1), rows=int(a.get("rowspan") or 1))
            self.tables[-1][-1].cells.append(self.cell)
        elif tag == "br" and self.cell is not None:
            self.cell.text += " "

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag == "td":
            self.cell = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.text += data


# A grid cell: its text, and whether it is the top-left of its source cell.
Slot = tuple[str, bool]


def grid(rows: list[Row]) -> list[list[Slot]]:
    """Lay a table's cells out on a grid, repeating a cell under its rowspan (not its own top-left) as ("^", ...)."""
    out: list[list[Slot]] = []
    pending: dict[tuple[int, int], Slot] = {}
    for r, row in enumerate(rows):
        line: list[Slot] = []
        cells = iter(row.cells)
        c = 0
        while True:
            if (r, c) in pending:
                line.append(pending.pop((r, c)))
                c += 1
                continue
            cell = next(cells, None)
            if cell is None:
                break
            text = re.sub(r"\s+", " ", cell.text).strip()
            for dc in range(cell.span):
                line.append((text, True) if dc == 0 else ("", False))
                for dr in range(1, cell.rows):
                    pending[(r + dr, c + dc)] = ("^", False)
            c += cell.span
        while (r, c) in pending:
            line.append(pending.pop((r, c)))
            c += 1
        out.append(line)
    return out


def pages(volume: str, lo: int, hi: int) -> list[tuple[raw.Artifact, list[list[Slot]]]]:
    """The volume's table pages from scan page lo to hi, each as the grid of its first table."""
    listing = raw.cached(SOURCE, f"{BASE}/bd{volume}/htm/")
    names = sorted(set(re.findall(rf'href="(drsa_{volume}_(\d{{4}})_[^"]+\.html)"', listing.path.read_text())))
    out = []
    for name, scan in names:
        if lo <= int(scan) <= hi:
            artifact = raw.cached(SOURCE, f"{BASE}/bd{volume}/htm/{name}")
            parser = _Tables()
            parser.feed(artifact.path.read_text(encoding="utf-8"))
            out.append((artifact, grid(parser.tables[0])))
    return out


# The columns of scattered votes: votes for candidates with 25 or fewer in the whole Wahlkreis, and in 1871 and 1874
# also the votes for candidates whose party position the office did not determine.
SCATTERED = {"splinter", "mixed"}
# Party positions as the tables head their columns, matched on the header with hyphenation removed and lowercased.
POSITIONS: list[tuple[str, str]] = [
    ("unbestimmt;auchzersplittert", "mixed"),
    ("zersplittert", "splinter"),
    ("unbestimmt", "unknown"),
    ("liberalereichspartei", "liberal_reich"),
    ("liberalevereinigung", "liberal_union"),
    ("reichspartei", "reich"),
    ("ausser", "liberal_other"),
    ("ausschluss", "liberal_other"),
    ("konserv", "conservative"),
    ("nationalliberal", "national_liberal"),
    ("fortschritt", "progress"),
    ("volkspartei", "people"),
    ("sozial", "social_democrat"),
    ("centrum", "centre"),
    ("zentrum", "centre"),
    ("partikular", "particularist"),
    ("welfen", "guelph"),
    ("pole", "pole"),
    ("protest", "protest"),
    ("elsässer", "alsace"),
    ("dänen", "dane"),
]
# The columns each table prints, by printed column number, apart from its party columns.
FIELDS = {
    "first": {5: "year", 6: "registered", 7: "cast", 9: "valid", 10: "invalid"},
    "second": {2: "year", 3: "kind", 4: "cast", 6: "valid", 7: "invalid"},
    "survey": {5: "registered", 6: "kind", 7: "valid", 8: "invalid"},
}

# The cell each layout's rows are read from: the year of the ballot, or its kind (I., E., or N.).
ANCHOR = {
    "first": re.compile(r"187[14]"),
    "second": re.compile(r"187[14]|\"+(\s*\"+)?|„+"),
    "survey": re.compile(r"[IEN]\.?"),
}


@dataclass
class Line:
    """One printed row: a ballot in one Wahlkreis, or a subtotal, joined across the two halves of the page."""

    left: raw.Artifact
    right: raw.Artifact
    labels: list[str]
    fields: dict[str, str]
    votes: dict[str, int]
    remark: str


def _header(rows: list[list[Slot]], column: int) -> str:
    for row in reversed(rows):
        text = row[column][0] if column < len(row) else ""
        if text and text != "^":
            return re.sub(r"[\s\-]+", "", text).lower()
    return ""


def _position(header: str) -> str:
    for needle, key in POSITIONS:
        if needle in header:
            return key
    raise ValueError(f"no party position for the column headed {header!r}")


def number(text: str) -> int | None:
    """A printed count, without its footnote mark; None for a dash, a dot, or an empty cell."""
    text = re.sub(r"^(\(\w\s*|\*+\)\s*|\w{1,2}\)\s*)+", "", text.strip()).strip()
    text = text.replace(".", "").replace(" ", "")
    if not text or text in {"─", "-", "—", '"', "^"}:
        return None
    if not text.isdigit():
        raise ValueError(f"not a count: {text!r}")
    return int(text)


def _columns(rows: list[list[Slot]]) -> tuple[int, dict[int, int]]:
    """The index of the row of printed column numbers, and each printed number's grid column."""
    at = next(i for i, row in enumerate(rows) if re.fullmatch(r"\d+\.", row[0][0]))
    return at, {int(text[:-1]): c for c, (text, _) in enumerate(rows[at]) if re.fullmatch(r"\d+\.", text)}


def _count(page: raw.Artifact, text: str) -> int | None:
    """A party's printed count, or the value FIXES gives a cell the transcription garbled."""
    name = page.url.rsplit("/", 1)[1]
    if (name, text) in FIXES:
        return FIXES[(name, text)]
    try:
        return number(text)
    except ValueError:
        PROBLEMS.append((name, text))
        return None


# Cells the page transcription garbled, by page and text, with the count the printed table gives.
FIXES: dict[tuple[str, str], int | None] = {}
PROBLEMS: list[tuple[str, str]] = []

COUNT = re.compile(r"(\(\w\s*|\*+\)\s*|\w{1,2}\)\s*)*\d[\d.\s]*")

FIGURE = re.compile(rf"{COUNT.pattern}|\d+,\d+|─")


def _numeric(row: list[Slot], columns: list[int]) -> bool:
    return any(COUNT.fullmatch(row[c][0]) for c in columns if c < len(row))


def lines(name: str) -> list[Line]:
    """Join each left page with the right page after it, row by row over the rows that print counts."""
    out: list[Line] = []
    volume, lo, hi, table = TABLES[name]
    found = pages(volume, lo, hi)
    for (left, lrows), (right, rrows) in zip(found[0::2], found[1::2], strict=True):
        lat, lcols = _columns(lrows)
        rat, rcols = _columns(rrows)
        if 1 not in lcols or 1 in rcols:
            raise ValueError(f"{left.url} and {right.url} are not a left and a right page")
        fields = {name: lcols[n] for n, name in FIELDS[table].items()}
        parties = {
            c: _position(_header(lrows[:lat], c))
            for n, c in sorted(lcols.items())
            if table == "survey" and n > max(FIELDS[table])
        }
        remark = None
        for _, c in sorted(rcols.items()):
            header = _header(rrows[:rat], c)
            if "bemerkungen" in header:
                remark = c
                continue
            if SCATTERED & set(parties.values()):
                continue
            parties[-1 - c] = _position(header)
        if not SCATTERED & set(parties.values()):
            raise ValueError(f"{right.url} has no column of scattered votes")
        anchor = "kind" if table == "survey" else "year"
        lbody: list[tuple[list[Slot], int]] = []
        for row in lrows[lat + 1 :]:
            if sum(bool(FIGURE.fullmatch(text)) for text, _ in row) < 3:
                continue
            found = [i for i, (text, _) in enumerate(row) if ANCHOR[table].fullmatch(text)]
            at = min(found, key=lambda i: abs(i - fields[anchor])) if found else fields[anchor]
            lbody.append((row, at - fields[anchor] if abs(at - fields[anchor]) <= 2 else 0))
        rbody = [r for r in rrows[rat + 1 :] if sum(bool(FIGURE.fullmatch(text)) for text, _ in r) >= 3]
        if len(lbody) != len(rbody):
            raise ValueError(f"{left.url}: {len(lbody)} rows on the left, {len(rbody)} on the right")
        for (lrow, offset), rrow in zip(lbody, rbody, strict=False):
            votes: dict[str, int] = {}
            for c, key in parties.items():
                text, _ = lrow[c + offset] if c >= 0 else rrow[-1 - c]
                value = _count(left if c >= 0 else right, text)
                if value:
                    votes[key] = votes.get(key, 0) + value
            out.append(
                Line(
                    left=left,
                    right=right,
                    labels=[text for text, _ in lrow[: lcols[2] + offset]],
                    fields={name: lrow[c + offset][0] for name, c in fields.items()},
                    votes=votes,
                    remark=rrow[remark][0] if remark is not None and remark < len(rrow) else "",
                )
            )
    return out


ELECTIONS = {1871: date(1871, 3, 3), 1874: date(1874, 1, 10), 1877: date(1877, 1, 10), 1878: date(1878, 7, 30), 1881: date(1881, 10, 27)}  # fmt: skip
TOTAL = re.compile(r"Zusammen|Zus\.|Sa\.|Summa|Deutsches Reich|Ohne Elsass")
# The regions in the tables' order, with their number of Wahlkreise: the Prussian and Bavarian Regierungsbezirke (the
# Province of Hanover and the City of Berlin in Prussia's case) and every other state. Each Wahlkreis is numbered within
# its region. Alsace-Lorraine first voted in 1874.
REGIONS = [
    ("Königsberg", 10), ("Gumbinnen", 7), ("Danzig", 5), ("Marienwerder", 8), ("Berlin", 6), ("Potsdam", 10),
    ("Frankfurt", 10), ("Stettin", 7), ("Köslin", 5), ("Stralsund", 2), ("Posen", 10), ("Bromberg", 5),
    ("Breslau", 13), ("Liegnitz", 10), ("Oppeln", 12), ("Magdeburg", 8), ("Merseburg", 8), ("Erfurt", 4),
    ("Schleswig", 10), ("Hannover", 19), ("Münster", 4), ("Minden", 5), ("Arnsberg", 8), ("Kassel", 8),
    ("Wiesbaden", 6), ("Koblenz", 6), ("Düsseldorf", 12), ("Köln", 6), ("Trier", 6), ("Aachen", 5), ("Sigmaringen", 1),
    ("Oberbayern", 8), ("Niederbayern", 6), ("Pfalz", 6), ("Oberpfalz", 5), ("Oberfranken", 5), ("Mittelfranken", 6),
    ("Unterfranken", 6), ("Schwaben", 6), ("Sachsen", 23), ("Württemberg", 17), ("Baden", 14), ("Hessen", 9),
    ("Mecklenburg-Schwerin", 6), ("Sachsen-Weimar", 3), ("Mecklenburg-Strelitz", 1), ("Oldenburg", 3),
    ("Braunschweig", 3), ("Sachsen-Meiningen", 2), ("Sachsen-Altenburg", 1), ("Sachsen-Coburg-Gotha", 2), ("Anhalt", 2),
    ("Schwarzburg-Rudolstadt", 1), ("Schwarzburg-Sondershausen", 1), ("Waldeck", 1), ("Reuss älterer Linie", 1),
    ("Reuss jüngerer Linie", 1), ("Schaumburg-Lippe", 1), ("Lippe", 1), ("Lübeck", 1), ("Bremen", 1), ("Hamburg", 3),
    ("Elsass-Lothringen", 15),
]  # fmt: skip
ORDER = [name for name, _ in REGIONS]
# States the tables split into Regierungsbezirke, and names a Wahlkreis took under another region in 1871 and 1874.
STATES = {"Preussen", "Bayern"}
ALIASES = {("Lauenburg", 1): ("Schleswig", 10)}
DITTO = re.compile(r"\"+(\s*\"+)?|„+")


def region(text: str) -> str | None:
    """A Wahlkreis's state or Regierungsbezirk as the archive names it, from a label cell; None for no region."""
    text = re.sub(r"\(.*", "", text)
    text = re.sub(r"- (?=[A-ZÄÖÜ])", "-", text).replace("- ", "")
    text = re.sub(r"^(Noch:\s*)?([IVX]+\.\s*)?(Bayern\.\s*)?", "", text.strip())
    text = re.sub(r"^(Rgbz\.|Rbz\.|Rgbg\.|Reg\.-Bez\.|Prov\.|Provinz|Stadt)\s*", "", text)
    text = text.strip(" .†*").replace("Koburg", "Coburg").replace("Sehw", "Schw")
    text = {"Reuss ä. L": "Reuss älterer Linie", "Reuss j. L": "Reuss jüngerer Linie"}.get(text, text)
    if not text or text == "^" or text in STATES or re.fullmatch(r"[\d\s]+", text):
        return None
    return text


@dataclass
class Ballot:
    line: Line
    year: int
    round: int
    mended: str = ""


def ballots() -> dict[int, dict[tuple[str, int], list[Ballot]]]:
    """Each election's ballots by Wahlkreis (region, number): its first ballot, then its second if one was held."""
    out: dict[int, dict[tuple[str, int], list[Ballot]]] = {year: {} for year in ELECTIONS}
    sums: dict[tuple[int, str], dict[str, int]] = {}
    for name, (_, _, _, table) in TABLES.items():
        place: str | None = None
        seat: int | None = None
        year = int(name[:4])
        kind = ""
        closed: set[tuple[int, str, int]] = set()
        totals: dict[int, bool] = {}
        ended = False
        regional = False
        total_year = year
        for line in lines(name):
            labels = line.labels
            mark = re.sub(r"[\s*†]", "", labels[-1]) if labels else ""
            for c, text in enumerate(labels):
                if text != "^":
                    totals[c] = bool(TOTAL.match(text) or "Wahlen" in text)
            if (mark.isdigit() or ended) and not any(TOTAL.match(t) or "Wahlen" in t for t in labels):
                totals.clear()
            ended = any("ntscheidende" in text for text in labels)
            opened = any(TOTAL.match(text) for text in labels)
            if any(totals.get(c) for c in range(len(labels))) and not (
                # A total opens on its 1874 row; one that a misplaced label opens on a 1871 row is that year's
                # ballot in the Wahlkreis above.
                table == "first" and opened and line.fields["year"] == "1871"
            ):
                if opened:
                    named = [region(re.sub(r"^(Zusammen|Zus\.)\s*", "", t)) for t in labels if TOTAL.match(t)]
                    regional = place is not None and (place in named or any(t.startswith("Sa.") for t in labels))
                    total_year = year
                if table == "first" and regional:
                    total_year = 1874 if opened else 1871
                    sums.setdefault((total_year, place), line.votes)
                elif table == "survey" and regional and line.fields["kind"].startswith("I"):
                    sums.setdefault((year, place), line.votes)
                continue
            moved = False
            for text in labels[:-1]:
                if (found := region(text)) is not None and not TOTAL.match(text):
                    moved = found != place
                    place = found
            first = table != "survey" or line.fields["kind"].startswith("I")
            # A missing or misplaced region label: a number that starts again, or a first ballot without a number
            # after the region's last Wahlkreis, opens the next region.
            if (
                first
                and not moved
                and place is not None
                and seat is not None
                and (
                    (mark.isdigit() and int(mark) <= seat)
                    or (table == "survey" and mark in {"", "^"} and seat >= dict(REGIONS)[place])
                )
            ):
                place, moved = ORDER[ORDER.index(place) + 1], True
            if mark.isdigit():
                seat = int(mark)
            elif moved:
                seat = 1
            elif mark not in {"", "^"}:
                raise ValueError(f"{line.left.url}: no Wahlkreis number in {labels}")
            if place is None or seat is None:
                raise ValueError(f"{line.left.url}: a ballot before any Wahlkreis: {labels}")
            if table == "first" and opened and line.fields["year"] == "1871":
                labels = []
            if table in {"first", "second"}:
                printed = line.fields["year"]
                year = year if printed == "^" or DITTO.fullmatch(printed) else int(printed)
            if table == "second":
                printed = line.fields["kind"]
                kind = kind if printed == "^" or DITTO.fullmatch(printed) else printed
                step = 2 if kind.startswith("Engere") else 0
            elif table == "survey":
                step = {"I.": 1, "I": 1, "E.": 2, "E": 2}.get(line.fields["kind"], 0)
            else:
                step = 1
            key = ALIASES.get((place, seat), (place, seat))
            here = out[year].get(key, [])
            if step == 0:
                closed.add((year, *key))
                continue
            if (year, *key) in closed:
                continue
            if len(here) != step - 1:
                raise ValueError(f"{line.left.url}: ballot {step} of {place} {seat} in {year} out of order")
            out[year][key] = [*here, Ballot(line, year, step)]
    reconcile(out, sums)
    return out


def _settle(
    off: dict[str, int], wrong: dict[tuple[str, int], int], seats: dict[tuple[str, int], Ballot]
) -> list[tuple[tuple[str, int], str, int]]:
    """The one way to charge each party column's excess over the region total to the Wahlkreise whose votes miss their
    valid votes, so that each Wahlkreis's excess is met exactly; or, when the columns match the total, one count printed
    in a neighbouring Wahlkreis's row. Empty when there is no such way, or more than one."""
    if not off and len(wrong) == 2:
        (a, gap), (b, other) = wrong.items()
        moved = [p for p, v in seats[a].line.votes.items() if v == gap and p not in seats[b].line.votes]
        moved += [p for p, v in seats[b].line.votes.items() if v == other and p not in seats[a].line.votes]
        if gap == -other and len(moved) == 1:
            party = moved[0]
            return (
                [(a, party, gap), (b, party, other)]
                if party in seats[a].line.votes
                else [(b, party, other), (a, party, gap)]
            )
        return []
    if not wrong or len(off) > 6 or sum(off.values()) != sum(wrong.values()):
        return []
    if len(off) == 1:
        (party,) = off
        return [(key, party, gap) for key, gap in wrong.items()]
    keys = list(wrong)
    found: list[list[tuple[tuple[str, int], str, int]]] = []
    for choice in itertools.product(keys, repeat=len(off)):
        charged = dict.fromkeys(keys, 0)
        for key, gap in zip(choice, off.values(), strict=True):
            charged[key] += gap
        if charged == wrong:
            found.append([(key, party, gap) for key, (party, gap) in zip(choice, off.items(), strict=True)])
    return found[0] if len(found) == 1 else []


def reconcile(out: dict[int, dict[tuple[str, int], list[Ballot]]], sums: dict[tuple[int, str], dict[str, int]]) -> None:
    """Mend a first ballot whose party votes miss its valid votes by one transcription slip, which the region's printed
    total by party places: one party's column misses the region total by exactly the Wahlkreis's shortfall."""
    for year, found in out.items():
        for (_, place), total in ((k, v) for k, v in sums.items() if k[0] == year):
            seats = {key: b[0] for key, b in found.items() if key[0] == place}
            added: dict[str, int] = {}
            for ballot in seats.values():
                for party, votes in ballot.line.votes.items():
                    added[party] = added.get(party, 0) + votes
            off = {p: added.get(p, 0) - total.get(p, 0) for p in {*added, *total} if added.get(p, 0) != total.get(p, 0)}
            wrong = {
                key: sum(b.line.votes.values()) - (number(b.line.fields["valid"]) or 0) for key, b in seats.items()
            }
            wrong = {key: gap for key, gap in wrong.items() if gap}
            for key, party, gap in _settle(off, wrong, seats):
                ballot = seats[key]
                was = ballot.line.votes.get(party, 0)
                if was - gap:
                    ballot.line.votes[party] = was - gap
                else:
                    del ballot.line.votes[party]
                ballot.mended += f"{party} {was} in the transcription, {was - gap} by the region's total. "
                wrong[key] -= gap
            wrong = {key: gap for key, gap in wrong.items() if gap}
            if wrong:
                raise ValueError(f"{year} {place}: party votes miss the valid votes in {wrong}, by party {off}")


def winner(ballot: Ballot) -> str:
    """The party position of the member a deciding ballot returned: the one with the most votes. (The transcription
    drops some of the table's underlines and puts others on the wrong count.)"""
    votes = {party: n for party, n in ballot.line.votes.items() if party not in SCATTERED}
    top = sorted(votes.values())
    if len(top) > 1 and top[-1] == top[-2]:
        raise ValueError(f"a tie in {ballot.line.left.url}: {votes}")
    return max(votes, key=lambda party: votes[party])


GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
NATION = "01a13bb5-c55d-7311-a43d-bf016ca0f93c"
REICHSTAG = "1f7048c9-bb75-51d2-b484-67f5e22b5732"
# The elections and their national contests, which de.reichstag_empire holds with the national votes by party.
HELD = {
    1871: "f5dd03b5-738e-5425-a7d9-eb5df8598bbc",
    1874: "a0de44a3-6177-5f12-9e54-137cbf6fc0f8",
    1877: "9da7ac7c-28a4-510c-9e3f-3b40e63870ac",
    1878: "54a1477c-54cb-5653-bc33-d17c111e8d28",
    1881: "87df02c1-35d0-5679-b45f-5ebca389b885",
}
# The national parties de.reichstag_empire holds, for the party positions the tables name the same way. Its Zentrum
# option names a party the reference data does not hold, so Zentrum options carry their label alone.
PARTIES = {
    "conservative": "d204a9c4-05ff-5036-9ca4-89efd9e93dbe",
    "reich": "8e369882-c55f-52d8-96b7-d64522db8372",
    "national_liberal": "54a2fccd-8cc6-51af-8714-ff883f19a375",
    "progress": "798aa31c-a2f1-5fff-a923-c65055a78655",
    "people": "04a433cc-69ce-5204-b789-d2586910e6d6",
    "social_democrat": "01a13bb5-c6e1-7410-816e-3b22bd40874f",
}
LABELS = {
    "conservative": "Konservativ",
    "reich": "Deutsche Reichspartei (freikonservativ)",
    "liberal_reich": "Liberale Reichspartei",
    "liberal_other": "Liberal, außer nationalliberal und Fortschritt",
    "liberal_union": "Liberale Vereinigung",
    "national_liberal": "Nationalliberal",
    "progress": "Deutsche Fortschrittspartei",
    "people": "Volkspartei",
    "centre": "Zentrum",
    "pole": "Polen",
    "social_democrat": "Sozialdemokraten",
    "particularist": "Partikularisten",
    "guelph": "Welfen und Autonomisten",
    "protest": "Protestpartei",
    "alsace": "Elsässer",
    "dane": "Dänen",
    "unknown": "Unbestimmt",
    "splinter": "Zersplittert",
    "mixed": "Unbestimmt, auch zersplittert",
}
SCHEME = "de.kaiserreich_wahlkreis"
SINCE = {"Elsass-Lothringen": date(1874, 1, 10)}
FOUNDED = date(1871, 3, 3)
ENDED = date(1919, 1, 19)
PUBLISHER = "Kaiserliches Statistisches Amt"
TITLE = "Statistik des Deutschen Reichs: Statistik der Reichstagswahlen 1871 bis 1881 (Bände 14, 37 und 53)"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
RULES = {"formula": "two_round", "majority": 0.5, "advance": 2}
METHOD = (
    "Votes by Wahlkreis from the Imperial Statistical Office's Statistik des Deutschen Reichs (volume 14 for 1871 and "
    "1874, volume 37 part 1 for 1877 and 1878, volume 53 part 1 for 1881), read from the transcription of its tables "
    "by the Digitale Reichsstatistik project. The tables give each ballot's votes by the party position of the "
    "candidates, so two candidates of one position are one option here, and votes for candidates who drew 25 votes or "
    "fewer in the whole Wahlkreis are counted as scattered. The member returned is the position with the most votes "
    "in the deciding ballot. By-elections (Nachwahlen) are not included."
)


def code(place: str, seat: int) -> str:
    return f"{changeset.slug(place)}-{seat}"


def title(place: str, seat: int) -> str:
    return place if dict(REGIONS)[place] == 1 else f"{place} {seat}"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def artifacts() -> list[raw.Artifact]:
    found = [raw.cached(SOURCE, f"{BASE}/bd{volume}/htm/") for volume in VOLUMES]
    for volume, lo, hi, _ in TABLES.values():
        found += [artifact for artifact, _ in pages(volume, lo, hi)]
    return list({artifact.url: artifact for artifact in found}.values())


def geo() -> Package:
    p = Package(f"{SOURCE}/geo", SOURCE, "The Reichstag's Wahlkreise of the German Empire, 1871 to 1918")
    p.external |= {GERMANY, NATION}
    p.add("scheme", code=SCHEME, polity_id=GERMANY, kind="electoral_district", rank=1, noun="Wahlkreis")
    for place, count in REGIONS:
        for seat in range(1, count + 1):
            area = key("area", place, seat)
            since = SINCE.get(place, FOUNDED)
            p.add("area", id=area, scheme_code=SCHEME)
            p.add("area_code", area_id=area, code=code(place, seat), valid_from=since, valid_to=ENDED)
            p.add("area_parent", area_id=area, parent_id=NATION, valid_from=since, valid_to=ENDED)
            p.name(area, title(place, seat), "de", "official")
            p.add(
                "reporting_unit",
                id=key("unit", place, seat),
                polity_id=GERMANY,
                scheme_code=SCHEME,
                code=code(place, seat),
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=since,
                valid_from_precision="day",
                valid_to=ENDED,
                valid_to_precision="day",
            )
    return p


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The Statistik des Deutschen Reichs source and the Reichstag's system")
    p.external |= {GERMANY}
    source = key("source")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=f"{BASE}/",
        license="Public domain (published 1874 to 1882)",
        attribution="Statistik des Deutschen Reichs, transcribed by the Digitale Reichsstatistik project",
        notes=(
            "Volume 14 (Vierteljahrshefte 1875), Tables I and II; volume 37 part 1 (Monatshefte 1879) and volume 53 "
            "part 1 (Monatshefte 1882), Uebersicht Nr. 1."
        ),
    )
    for artifact in artifacts():
        p.artifact(artifact, source)
    system = key("electoral_system")
    p.add(
        "electoral_system",
        id=system,
        polity_id=GERMANY,
        valid_from=FOUNDED,
        valid_to=ENDED,
        rules=RULES,
        rules_version=1,
    )
    p.name(system, "Reichstag Wahlkreis with runoff, 1871-1918", "en", "official")
    return p


def election(year: int, found: dict[tuple[str, int], list[Ballot]]) -> Package:
    day = ELECTIONS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The Reichstag election of {day}, both ballots by Wahlkreis, from the Statistik des Deutschen Reichs",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    election = HELD[year]
    p.external |= {election, REICHSTAG, *PARTIES.values()}
    source, system = key("source"), key("electoral_system")
    used = {b.line.left.url for bs in found.values() for b in bs} | {
        b.line.right.url for bs in found.values() for b in bs
    }
    for artifact in artifacts():
        if artifact.url in used or artifact.url.endswith("/htm/"):
            p.uses(artifact)
    rows: list[tuple[str, str, str | None, int, int]] = []
    for place, count in REGIONS:
        for seat in range(1, count + 1):
            if (place, seat) not in found:
                continue
            ballots = found[(place, seat)]
            unit, area = key("unit", place, seat), key("area", place, seat)
            previous: str | None = None
            for ballot in ballots:
                n = ballot.round
                contest = key("contest", year, place, seat, n)
                name = title(place, seat) + ("" if n == 1 else ", Stichwahl")
                p.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=REICHSTAG,
                    district_area_id=area,
                    magnitude=1,
                    round_number=n,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                previous = contest
                p.name(contest, name, "de", "official")
                p.add(
                    "slug",
                    path=f"de/{day}-general/{code(place, seat)}" + ("" if n == 1 else "-stichwahl"),
                    entity_id=contest,
                    is_current=True,
                )
                result = key("result_set", year, place, seat, n)
                p.add(
                    "result_set",
                    id=result,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=source,
                    status="active",
                    is_default=True,
                    completeness=1.0,
                )
                body = METHOD + (f" Mended: {ballot.mended.strip()}" if ballot.mended else "")
                p.add("note", id=key("note", result), entity_id=result, kind="methodology", body_md=body, lang="en")
                p.add(
                    "citation",
                    id=key("citation", result),
                    entity_id=result,
                    source_id=source,
                    locator=json.dumps({"url": ballot.line.left.url, "right": ballot.line.right.url}),
                )
                options: dict[str, str] = {}
                for party in ballot.line.votes:
                    option = options[party] = key("option", year, place, seat, n, party)
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if party in SCATTERED else "list",
                        party_id=PARTIES.get(party),
                        ballot_label=LABELS[party],
                    )
                    rows.append((result, unit, option, VOTES, ballot.line.votes[party]))
                for column, measure in (
                    ("registered", REGISTERED),
                    ("cast", CAST),
                    ("valid", VALID),
                    ("invalid", INVALID),
                ):
                    printed = ballot.line.fields.get(column, "")
                    value = number(printed)
                    if column == "registered" and printed.startswith("*"):
                        # The 1871 roll of two Wahlkreise printed as one figure against the first of them.
                        value = None
                    if value is not None:
                        rows.append((result, unit, None, measure, value))
                    elif column == "invalid" and printed.strip() == "─":
                        rows.append((result, unit, None, measure, 0))
                if n == len(ballots):
                    p.add("outcome", result_set_id=result, option_id=options[winner(ballot)], kind="won", seats=1)
                else:
                    for party in ballots[n].line.votes:
                        if party not in SCATTERED and party in options:
                            p.add("outcome", result_set_id=result, option_id=options[party], kind="advanced")
    if year == 1877:
        p.accept(
            "accounting.votes_valid",
            "In the 1877 runoff in Sachsen-Coburg-Gotha 2 the table prints 11,312 and 8,934 votes for the two candidates, "
            "which add to 20,246, and 20,247 valid votes.",
        )
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in ELECTIONS)]


def fetch() -> None:
    for volume, (lo, hi) in VOLUMES.items():
        listing = raw.fetch(SOURCE, f"{BASE}/bd{volume}/htm/", suffix=".html")
        names = sorted(set(re.findall(rf'href="(drsa_{volume}_(\d{{4}})_[^"]+\.html)"', listing.path.read_text())))
        for name, scan in names:
            if lo <= int(scan) <= hi:
                raw.fetch(SOURCE, f"{BASE}/bd{volume}/htm/{name}", suffix=".html")


def parse() -> None:
    found = ballots()
    geo().write()
    registry().write()
    for year in ELECTIONS:
        election(year, found[year]).write()


def emit() -> None:
    raise SystemExit(f"{SOURCE} builds packages: run `ea parse {SOURCE}`, then `ea package load` its packages")
