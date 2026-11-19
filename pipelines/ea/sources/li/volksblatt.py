"""Liechtenstein's national popular votes of 1919-2000 by municipality, digitized from the country's newspapers.

The Liechtensteiner Volksblatt was the principality's official publication organ, and from the late 1940s it printed
the government's own notice of each vote's result (Kundmachung über das Ergebnis der Volksabstimmung) with the
registered voters, the votes cast, the valid, invalid, and blank ballots, and the yes and no votes of each municipality.
Before that the paper, and where it prints less the other papers of the country, published the count by municipality
in its report of the vote. The national library's digital collection holds every issue with its OCR text in ALTO, so
each number keeps the page and box it was read from.

The figures were transcribed from the page images into `volksabstimmungen_1919_2000.json`; this module checks every
column against the printed district and national totals and locates each value on the page through the ALTO words.
The government's results site, which `li.abstimmungen` reads, begins in 2002.
"""

import itertools
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.li import regierung

SOURCE = "li.volksblatt"
API = "https://www.eliechtensteinensia.li/viewer/api/v1/records"
VIEWER = "https://www.eliechtensteinensia.li/viewer/image"
LIECHTENSTEIN = regierung.LIECHTENSTEIN
DATA = Path(__file__).with_name("volksabstimmungen_1919_2000.json")
REGISTRY = f"{SOURCE}/registry"
LI_GEO = f"{regierung.SOURCE}/geo"
OBERLAND = ("Vaduz", "Balzers", "Planken", "Schaan", "Triesen", "Triesenberg")
UNTERLAND = ("Eschen", "Gamprin", "Mauren", "Ruggell", "Schellenberg")
TOWNS = OBERLAND + UNTERLAND
DISTRICTS = {"Oberland": OBERLAND, "Unterland": UNTERLAND, "Liechtenstein": TOWNS}
CODES = {name: code for name, code, _ in regierung.PLACES.values()}
VOTES = 1
# Accounting columns and the measure each is stored under.
ACCOUNTING = {"reg": 2, "cards": 14, "ballots": 3, "valid": 4, "invalid": 5, "blank": 6}
PAPERS = {
    "000476564": "Liechtensteiner Volksblatt",
    "000469834": "Liechtensteiner Vaterland",
    "000476558": "Oberrheinische Nachrichten",
    "000476556": "Liechtensteiner Nachrichten",
}
QUESTION_TYPES = {"referendum", "optional_referendum", "initiative", "counter_proposal", "plebiscite"}
SYSTEM_NOTE = (
    "A popular vote under Liechtenstein's constitution and its law on popular rights (before October 1921, under an "
    "ordinance for the vote): each question is decided by a simple majority of the valid votes cast, with no quorum. "
    "Until 1984 only men voted. When an initiative and a counter-proposal of the Landtag were on one ballot, each was "
    "a question of its own; until 1987 a voter could say yes to only one of them."
)
METHOD = {
    "notice": (
        "Counted by the municipalities and declared by the government; digitized from the government's notice of the "
        "result (Kundmachung) printed in the {paper} of {issue}, which gives the result by municipality."
    ),
    "report": (
        "Counted by the municipalities; digitized from the {paper} of {issue}, which prints the result by municipality "
        "in its report of the vote."
    ),
}
COLUMNS_NOTE = (
    "Registered voters are the roll (Stimmberechtigte), voters are the cards or votes cast (abgegebene Stimmen or "
    "Stimmkarten), ballots cast are the ballots put in the box (eingelegte Stimmzettel), and each is valid, invalid, "
    "or blank. Each value keeps the page and the box of the printed figure."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


VB_SOURCE = key("source", SOURCE)
SYSTEM = key("electoral_system", "popular_vote", 1919)


@dataclass
class Question:
    de: str
    en: str
    type: str
    options: list[str] | None


@dataclass
class Table:
    issue: str
    page: str
    box: tuple[float, float, float, float]
    kind: str
    columns: list[str]
    rows: dict[str, list[int | None]]


@dataclass
class Vote:
    first: date
    last: date
    text: str
    questions: list[Question]
    tables: list[Table]
    misprints: list[dict[str, str]]
    accepted: dict[str, str]
    notes: list[str]

    @property
    def key(self) -> str:
        return f"{SOURCE}/{self.last}"


def votes() -> list[Vote]:
    loaded = json.loads(DATA.read_text(encoding="utf-8"))
    return [
        Vote(
            first=date.fromisoformat(v["first"]),
            last=date.fromisoformat(v["last"]),
            text=v["text"],
            questions=[Question(q["de"], q["en"], q["type"], q.get("options")) for q in v["questions"]],
            tables=[
                Table(
                    t["issue"],
                    t["page"],
                    (t["box"][0], t["box"][1], t["box"][2], t["box"][3]),
                    t["kind"],
                    t["columns"],
                    t["rows"],
                )
                for t in v["tables"]
            ],
            misprints=v.get("misprints", []),
            accepted=v.get("accepted", {}),
            notes=v.get("notes", []),
        )
        for v in loaded
    ]


def alto_url(issue: str, page: str) -> str:
    return f"{API}/{issue}/files/alto/{page}.xml"


def manifest_url(issue: str) -> str:
    return f"{API}/{issue}/manifest/"


def not_html(path: Path) -> None:
    if path.read_bytes()[:64].lstrip(b"\xef\xbb\xbf").lower().startswith(b"<!doctype html"):
        raise ValueError(f"{path}: the library answered with an HTML page")


def fetch() -> None:
    for v in votes():
        for t in v.tables:
            raw.fetch(SOURCE, manifest_url(t.issue), suffix=".json", check=not_html)
            raw.fetch(SOURCE, alto_url(t.issue, t.page), suffix=".xml", check=not_html)


# Reading a column: (question index or None for every question, measure name or option index).
Cell = tuple[int | None, str]


def column(c: str) -> Cell | None:
    """The question and the measure a printed column holds; None for a column that is not stored."""
    base, _, suffix = c.partition(".")
    if base == "ignore":
        return None
    if base == "opt":
        return (None, c)
    if base in ("yes", "no"):
        return (int(suffix) if suffix else 0, base)
    if base in ACCOUNTING:
        return (int(suffix) if suffix else None, base)
    raise ValueError(f"unknown column {c}")


def figures(v: Vote) -> dict[Cell, dict[str, int]]:
    """Every stored value as {(question, measure): {row: value}}."""
    found: dict[Cell, dict[str, int]] = defaultdict(dict)
    for t in v.tables:
        for row, values in t.rows.items():
            if row not in TOWNS and row not in DISTRICTS:
                raise ValueError(f"{v.last}: unknown row {row}")
            if len(values) != len(t.columns):
                raise ValueError(f"{v.last} {row}: {len(values)} values for {len(t.columns)} columns")
            for c, value in zip(t.columns, values, strict=True):
                cell = column(c)
                if cell is None or value is None:
                    continue
                if row in found[cell] and found[cell][row] != value:
                    raise ValueError(f"{v.last} {row} {c}: printed twice as {found[cell][row]} and {value}")
                found[cell][row] = value
    return found


def misprinted(v: Vote, row: str, cell: Cell) -> bool:
    names = {cell[1], f"{cell[1]}.{cell[0]}" if cell[0] is not None else cell[1]}
    return any(m["row"] == row and m["column"] in names for m in v.misprints)


def check(v: Vote) -> None:
    """Every column must add up to each printed district and national total, unless the paper misprints it."""
    for q in v.questions:
        if q.type not in QUESTION_TYPES:
            raise ValueError(f"{v.last}: unknown question type {q.type}")
    for cell, rows in figures(v).items():
        for total, towns in DISTRICTS.items():
            if total not in rows or not all(t in rows for t in towns):
                continue
            summed = sum(rows[t] for t in towns)
            if summed != rows[total] and not misprinted(v, total, cell):
                raise ValueError(f"{v.last} {cell}: {total} printed {rows[total]}, municipalities add up to {summed}")
    for i, q in enumerate(v.questions):
        cells = [(i, "yes"), (i, "no")] if not q.options else [(None, f"opt.{j}") for j in range(len(q.options))]
        found = figures(v)
        for cell in cells:
            if cell not in found:
                raise ValueError(f"{v.last} question {i}: no {cell[1]} column")


def label(v: Vote) -> str:
    return f"{v.last.day} {v.last:%B %Y}"


def packages() -> list[str]:
    return [REGISTRY, *(v.key for v in votes())]


def parse() -> None:
    registry().write()
    for v in votes():
        built = election(v)
        why = reasons(v)
        for code, _, _ in package.check(built.write()):
            if code not in why:
                raise ValueError(f"{v.key}: finding {code} has no reason in the printed figures")
            built.accept(code, why[code])
        built.write()


def number(value: int) -> str:
    return f"{value:,}"


def reasons(v: Vote) -> dict[str, str]:
    """The accounting findings the printed figures cause, each with the rows that cause it."""
    found = figures(v)
    paper = PAPERS[v.tables[0].issue[:9]]
    why: dict[str, str] = dict(v.accepted)
    sums, turnout = [], []
    for index, _ in enumerate(v.questions):

        def get(measure: str, row: str, index: int = index) -> int | None:
            for cell in ((index, measure), (None, measure)):
                if row in found.get(cell, {}):
                    return found[cell][row]
            return None

        for row in TOWNS:
            ballots, valid = get("ballots", row), get("valid", row)
            invalid, blank = get("invalid", row), get("blank", row)
            if ballots is not None and valid is not None:
                parts = valid + (invalid or 0) + (blank or 0)
                if (invalid is None and blank is None and valid > ballots) or (
                    (invalid is not None or blank is not None) and parts != ballots
                ):
                    text = f"{row} with {number(ballots)} ballots against {number(valid)} valid"
                    text += f", {number(invalid or 0)} invalid and {number(blank or 0)} blank"
                    if text not in sums:
                        sums.append(text)
            registered = get("reg", row)
            cast = get("ballots", row) if get("ballots", row) is not None else get("cards", row)
            if registered is not None and cast is not None and cast > registered:
                text = f"{row} with {number(cast)} votes cast against {number(registered)} registered voters"
                if text not in turnout:
                    turnout.append(text)
    if sums:
        why.setdefault(
            "accounting.ballots_sum",
            f"The {paper} prints {'; '.join(sums)}; the categories do not add up to the ballots in the source, and "
            "every figure is stored as printed.",
        )
    if turnout:
        why.setdefault(
            "accounting.turnout",
            f"The {paper} prints {'; '.join(turnout)}; stored as printed.",
        )
    declared = [f"{m['column']}: {m['note'].rstrip('.')}" for m in v.misprints if m["row"] == "Liechtenstein"]
    if declared:
        why.setdefault(
            "accounting.declared",
            "The national totals the paper prints differ from the sums of its municipalities ("
            + "; ".join(declared)
            + "). Both are stored as printed.",
        )
    return why


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The newspaper archive as the source of Liechtenstein's national votes of 1919-2000, and the electoral system "
        "of a popular vote in those years",
    )
    p.external.add(LIECHTENSTEIN)
    p.add(
        "source",
        id=VB_SOURCE,
        kind="official_body",
        publisher="Regierung des Fürstentums Liechtenstein and the country's newspapers (eLiechtensteinensia, "
        "Liechtensteinische Landesbibliothek)",
        title="Liechtensteiner Volksblatt and the other papers of the country: results of the national votes, "
        "1919-2000",
        url="https://www.eliechtensteinensia.li/",
        license="Public record; scans by the Liechtensteinische Landesbibliothek",
        license_url="https://www.landesbibliothek.li/",
        attribution="eLiechtensteinensia, Liechtensteinische Landesbibliothek",
    )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=LIECHTENSTEIN,
        valid_from=date(1919, 1, 1),
        valid_to=date(2001, 12, 31),
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def digits(self) -> str:
        return re.sub(r"\D", "", self.text)

    @property
    def box(self) -> Box:
        return (self.x0, self.y0, self.x1, self.y1)


Box = tuple[float, float, float, float]
# Some pages' ALTO gives words no height; such a word takes the height of a line of type.
LINE = 36.0


def words(issue: str, page: str) -> list[Word]:
    from xml.etree import ElementTree

    text = raw.cached(SOURCE, alto_url(issue, page)).path.read_text(encoding="utf-8-sig")
    text = re.sub(r'\s[\w:]*xmlns(:\w+)?="[^"]+"', "", text)
    # Some issues' ALTO prefixes every element and attribute with a namespace.
    text = re.sub(r"\s\w+:(\w+=)", r" \1", re.sub(r"<(/?)\w+:", r"<\1", text))
    root = ElementTree.fromstring(text.encode())
    found = []
    for s in root.iter("String"):
        x, y = float(s.get("HPOS") or 0), float(s.get("VPOS") or 0)
        width, height = float(s.get("WIDTH") or 0), float(s.get("HEIGHT") or 0)
        found.append(Word(s.get("CONTENT") or "", x, y, x + width, y + (height or LINE)))
    return found


def canvas(issue: str, page: str) -> tuple[int, int]:
    loaded = json.loads(raw.cached(SOURCE, manifest_url(issue)).path.read_text(encoding="utf-8"))
    for c in loaded["sequences"][0]["canvases"]:
        if any(r["@id"].endswith(f"/alto/{page}.xml") for r in c["rendering"]):
            return int(c["width"]), int(c["height"])
    raise LookupError(f"page {page} not in {issue}")


def locate(t: Table) -> dict[tuple[str, int], Box]:
    """The box of each printed value as {(row, column index): box} in canvas pixels.

    The words inside the table's box are grouped into lines. The table's rows take lines in printed order, each the
    line below the last that holds most of its values; within a line, values are matched left to right. A value the
    OCR missed or misread takes its column's span and its row's line.
    """
    x0, y0, x1, y1 = t.box
    margin = 15
    inside = [
        w for w in words(t.issue, t.page) if x0 - margin <= w.x0 <= x1 + margin and y0 - margin <= w.y0 <= y1 + margin
    ]
    # Thousands printed with a space ("12 124") are two ALTO words; add their union as one more word.
    ordered = sorted(inside, key=lambda w: (round(w.y0 / 10), w.x0))
    joined = []
    for a, b in itertools.pairwise(ordered):
        if (
            abs(a.y0 - b.y0) < 15
            and 0 <= b.x0 - a.x1 < 45
            and re.fullmatch(r"\d{1,3}", a.text)
            and re.fullmatch(r"\d{3}\D?", b.text)
        ):
            joined.append(Word(f"{a.text}{b.text}", a.x0, min(a.y0, b.y0), b.x1, max(a.y1, b.y1)))
    inside += joined
    heights = [w.y1 - w.y0 for w in inside if w.digits] or [LINE]
    step = median(heights) * 0.7
    lines: list[list[Word]] = []
    for w in sorted(inside, key=lambda w: (w.y0 + w.y1) / 2):
        middle = (w.y0 + w.y1) / 2
        if lines and abs(median((u.y0 + u.y1) / 2 for u in lines[-1]) - middle) < step:
            lines[-1].append(w)
        else:
            lines.append([w])
    found: dict[tuple[str, int], Box] = {}
    row_line: dict[str, int] = {}
    start = 0
    for row, values in t.rows.items():
        wanted = {str(v) for v in values if v is not None}
        # A one-digit value matches stray figures anywhere; weigh the longer ones when the row has any.
        telling = {v for v in wanted if len(v) > 1} or wanted
        best, best_score = None, 0.0
        for n in range(start, len(lines)):
            digits = {w.digits for w in lines[n]}
            score = len(telling & digits) / max(len(telling), 1)
            if any(re.sub(r"[^a-z]", "", w.text.lower())[:5] == row.lower()[:5].replace("s", "f") for w in lines[n]):
                score += 0.3
            if any(w.text.lower().startswith(row.lower()[:6]) for w in lines[n]):
                score += 0.3
            if score > best_score + 1e-9:
                best, best_score = n, score
        if best is None or best_score < 0.3:
            continue
        row_line[row] = best
        start = best + 1
        left = -1e9
        used: set[int] = set()
        for i, value in enumerate(values):
            if value is None:
                continue
            hits = [
                w
                for w in sorted(lines[best], key=lambda w: w.x0)
                if id(w) not in used and w.digits == str(value) and w.x0 >= left - 5
            ]
            if hits:
                hit = hits[0]
                used.add(id(hit))
                found[(row, i)] = hit.box
                left = hit.x1
    # Values the OCR missed or misread take their column's span and their row's line.
    spans: dict[int, list[Box]] = defaultdict(list)
    for (_, i), box in found.items():
        spans[i].append(box)
    rows = list(t.rows)
    tops: dict[str, tuple[float, float]] = {}
    for row in rows:
        if row in row_line:
            line = [w for w in lines[row_line[row]] if w.digits] or lines[row_line[row]]
            tops[row] = (median(w.y0 for w in line), median(w.y1 for w in line))
    for k, row in enumerate(rows):
        if row in tops:
            continue
        before = [r for r in rows[:k] if r in tops]
        after = [r for r in rows[k + 1 :] if r in tops]
        if before and after:
            a, b = rows.index(before[-1]), rows.index(after[0])
            share = (k - a) / (b - a)
            top = tops[before[-1]][0] + (tops[after[0]][0] - tops[before[-1]][0]) * share
            tops[row] = (top, top + (tops[before[-1]][1] - tops[before[-1]][0]))
        else:
            tops[row] = (y0, y1)
    for row, values in t.rows.items():
        top, bottom = tops[row]
        for i, value in enumerate(values):
            if value is None or (row, i) in found:
                continue
            if spans[i]:
                found[(row, i)] = (min(b[0] for b in spans[i]), top, max(b[2] for b in spans[i]), bottom)
                continue
            before = [j for j in range(i) if spans[j]]
            after = [j for j in range(i + 1, len(values)) if spans[j]]
            left = max(b[2] for b in spans[before[-1]]) + 5 if before else x0
            right = min(b[0] for b in spans[after[0]]) - 5 if after else x1
            found[(row, i)] = (left, top, max(right, left + 10), bottom)
    return found


def election(v: Vote) -> Package:
    check(v)
    p = Package(
        v.key,
        SOURCE,
        f"The Liechtenstein popular vote of {label(v)} by municipality, from the country's newspapers",
        depends=[LI_GEO, REGISTRY],
    )
    p.external.add(LIECHTENSTEIN)
    election_id = key("election", v.last)
    p.add(
        "election",
        id=election_id,
        polity_id=LIECHTENSTEIN,
        kind="referendum",
        date_from=v.first,
        date_to=v.last,
        date_precision="day",
        original_text=v.text,
        status="held",
    )
    p.name(election_id, f"{label(v)} Liechtenstein popular vote", "en", "english")
    p.name(election_id, f"Volksabstimmung vom {v.text}", "de", "native", False)
    path = f"li/{v.last}-referendum"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    first = v.tables[0]
    p.add(
        "citation",
        id=key("citation", election_id),
        entity_id=election_id,
        source_id=VB_SOURCE,
        locator=json.dumps({"url": f"{VIEWER}/{first.issue}/{int(first.page)}/", "paper": PAPERS[first.issue[:9]]}),
    )
    pages: dict[tuple[str, str], str] = {}
    for t in v.tables:
        if (t.issue, t.page) in pages:
            continue
        p.uses(raw.cached(SOURCE, manifest_url(t.issue)))
        artifact = p.artifact(raw.cached(SOURCE, alto_url(t.issue, t.page)), VB_SOURCE)
        width, height = canvas(t.issue, t.page)
        pages[(t.issue, t.page)] = key("page", t.issue, t.page)
        # The ALTO file gives each word's box in the pixels of the page scan, which the IIIF service serves at the
        # canvas size recorded here.
        p.add(
            "source_page",
            id=pages[(t.issue, t.page)],
            artifact_id=artifact,
            page_number=int(t.page),
            image_key=f"pages/{SOURCE}/{t.issue}/{t.page}.jpg",
            width=width,
            height=height,
        )
    units: dict[str, str] = {}
    for name in TOWNS:
        code = CODES[name]
        area = regierung.area("li.gemeinde", code)
        units[name] = key("unit", v.last, code)
        p.add(
            "reporting_unit",
            id=units[name],
            polity_id=LIECHTENSTEIN,
            scheme_code="li.gemeinde",
            code=f"{v.last}:{code}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
    boxes = {id(t): locate(t) for t in v.tables}
    found = figures(v)
    for index, q in enumerate(v.questions):
        contest = key("contest", v.last, index)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question",
            electoral_system_id=SYSTEM,
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type=q.type)
        p.name(contest, q.de, "de", "official", False)
        p.name(contest, q.en, "en", "english")
        p.add("slug", path=f"{path}/{changeset.slug(q.en)}", entity_id=contest, is_current=True)
        # option per printed answer: (cell, ballot label, English name)
        answers: list[tuple[Cell, str, str]]
        if q.options:
            answers = [((None, f"opt.{j}"), o, o) for j, o in enumerate(q.options)]
        else:
            answers = [((index, "yes"), "Ja", "Yes"), ((index, "no"), "Nein", "No")]
        options: dict[Cell, str] = {}
        for position, (cell, de, en) in enumerate(answers, start=1):
            options[cell] = key("option", v.last, index, cell[1])
            p.add(
                "contest_option",
                id=options[cell],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=de,
            )
            if en != de:
                p.name(options[cell], en, "en", "english")
            p.name(options[cell], de, "de", "official", en != de)
        result_set = key("result_set", v.last, index)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VB_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        tables = [t for t in v.tables if any(stored(c, index, q) for c in t.columns)]
        kind = "notice" if any(t.kind == "notice" for t in tables) else "report"
        t0 = next(t for t in tables if t.kind == kind)
        issue_day = date.fromisoformat(t0.issue.split("_")[1])
        method = METHOD[kind].format(paper=PAPERS[t0.issue[:9]], issue=f"{issue_day:%-d %B %Y}")
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=" ".join([method, COLUMNS_NOTE, *v.notes]),
            lang="en",
        )
        written: set[tuple[str, int, str | None]] = set()
        for t in tables:
            for row, values in t.rows.items():
                # A question's own column comes before the column every question shares.
                pairs = sorted(enumerate(zip(t.columns, values, strict=True)), key=lambda p: "." not in p[1][0])
                for i, (c, value) in pairs:
                    if value is None or not stored(c, index, q):
                        continue
                    cell = column(c)
                    assert cell is not None
                    option = options.get(cell)
                    measure = VOTES if option else ACCOUNTING[cell[1]]
                    if row not in TOWNS:
                        continue
                    if (row, measure, option) in written:
                        continue
                    written.add((row, measure, option))
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[row],
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
                    box = boxes[id(t)].get((row, i))
                    if box is None:
                        raise ValueError(f"{v.last} {row} {c}: no box on page {t.page}")
                    p.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=units[row],
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        page_id=pages[(t.issue, t.page)],
                        source_id=VB_SOURCE,
                        x0=box[0],
                        y0=box[1],
                        x1=box[2],
                        y1=box[3],
                    )
        # The national totals the paper prints are declared.
        totals: dict[tuple[str | None, int], int] = {}
        for cell, rows in found.items():
            if "Liechtenstein" not in rows or not (cell[0] in (None, index)):
                continue
            if cell[1].startswith("opt.") and cell not in options:
                continue
            if cell[1] in ("yes", "no") and cell not in options:
                continue
            option = options.get(cell)
            measure = VOTES if option else ACCOUNTING[cell[1]]
            if (option, measure) in totals and cell[0] is None:
                continue
            totals[(option, measure)] = rows["Liechtenstein"]
        for (option, measure), value in totals.items():
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
        national = {cell: national_total(found[cell]) for cell in options if cell in found}
        if not q.options and None not in national.values():
            yes, no = national[(index, "yes")], national[(index, "no")]
            if yes != no:
                winner, loser = (
                    ((index, "yes"), (index, "no")) if (yes or 0) > (no or 0) else ((index, "no"), (index, "yes"))
                )
                p.add("outcome", result_set_id=result_set, option_id=options[winner], kind="won")
                p.add("outcome", result_set_id=result_set, option_id=options[loser], kind="lost")
    for code, reason in v.accepted.items():
        p.accept(code, reason)
    return p


def national_total(rows: dict[str, int]) -> int | None:
    if "Liechtenstein" in rows:
        return rows["Liechtenstein"]
    if all(t in rows for t in TOWNS):
        return sum(rows[t] for t in TOWNS)
    return None


def stored(c: str, index: int, q: Question) -> bool:
    """Whether a printed column holds a value of question `index`."""
    cell = column(c)
    if cell is None:
        return False
    if cell[1].startswith("opt."):
        return bool(q.options)
    if cell[1] in ("yes", "no"):
        return not q.options and cell[0] == index
    return cell[0] in (None, index)
