"""The 2009 indigenous autonomy referendums of Bolivia's twelve municipalities, by locality.

Volume II of the Atlas Electoral de Bolivia prints, after the municipal table of these referendums (page 119), one table
per department with the votes of each locality of the twelve municipalities (pages 120, 122, 123, 125, and 126). A
locality's name wraps over two or three lines that sit above and below its numbers, so each word of the name goes to
the row of numbers nearest to it. The tables give no municipality for a row but the label that spans several rows, so a
row belongs to the municipality whose printed row on page 119 the rows add up to exactly.

The package adds a finer, non-default result set to each municipality's question in the package of the 2009 autonomy
referendums, and a reporting unit for each locality inside the municipality's area.
"""

import functools
import re
from datetime import date
from typing import NamedTuple

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.bo import atlas
from ea.sources.bo.oep_referendos import (
    AIOC,
    ATLAS2,
    ATTRIBUTION,
    BALLOTS,
    BLANK,
    BOLIVIA,
    INVALID,
    LICENSE,
    REGISTERED,
    SOURCE,
    VALID,
    VOTES,
    area,
    section,
    unit,
)

DAY = date(2009, 12, 6)
KEY = f"{SOURCE}/2009-localities"
DEPENDS = [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]
# The page of each department's table of localities, and its department code.
DEPARTMENTS = {120: "1", 122: "2", 123: "4", 125: "5", 126: "7"}
PAGES = tuple(DEPARTMENTS)
SCHEME = "bo.localidad"
# The code of each municipality's area, as the package of the municipalities names it.
CODES = {
    "Mojocoya": "1:mojocoya",
    "Tarabuco": "1:tarabuco",
    "Huacaya": "1:huacaya",
    "Jesús de Machaca": "2:jesus-de-machaca",
    "Charazani": "2:charazani",
    "Curahuara de Carangas": "4:curahuara-de-carangas",
    "Pampa Aullagas": "4:pampa-aullagas",
    "Salinas de Garci Mendoza": "4:salinas-de-garci-mendoza",
    "Chipaya": "4:chipaya",
    "San Pedro de Totora": "4:san-pedro-de-totora",
    "Chayanta": "5:chayanta",
    "Charagua": "7:charagua",
}
METHOD = (
    "Counts of each locality of the municipality from the Atlas Electoral de Bolivia's table of the indigenous "
    "autonomy referendum by locality. The localities add up exactly to the municipality's row of the municipal table, "
    "which stays the default result set and holds the outcome."
)
COUNTS = ("yes", "no", "valid", "blank", "null", "cast", "registered")


class Locality(NamedTuple):
    municipality: str
    name: str
    counts: dict[str, int]
    page: int


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def table(number: int, words: list[atlas.Word]) -> list[tuple[str, dict[str, int]]]:
    """The names and counts of the rows of the locality table of a page, in order."""
    rows = atlas.table(number, words)
    if not rows or any(row.level != "locality" for row in rows):
        raise ValueError(f"page {number}: not a table of localities")
    page_lines = atlas.lines(words)
    header = next(
        line
        for line in page_lines
        if any(w.text == "Localidad" for w in line) and any(w.text == "Municipio" for w in line)
    )
    left = next(w.x0 for w in header if w.text == "Localidad") - 15
    right = min(w.x0 for w in header if atlas.HEADERS.get(w.text.lower()) in {"yes"}) - 35 + 15
    bottom = max(row.y for row in rows) + 14
    owned: list[list[atlas.Word]] = [[] for _ in rows]
    for word in words:
        if word.y0 <= header[0].y1 or word.y0 > bottom or word.x0 < left or word.x1 > right:
            continue
        nearest = min(range(len(rows)), key=lambda i: abs(rows[i].y - word.y))
        if abs(rows[nearest].y - word.y) > 12:
            raise ValueError(f"page {number}: {word.text!r} at {word.y:.0f} is far from every row")
        owned[nearest].append(word)
    found = []
    for row, mine in zip(rows, owned, strict=True):
        text = clean(" ".join(w.text for line in atlas.lines(mine) for w in line))
        if not text:
            raise ValueError(f"page {number}: a row at {row.y:.0f} has no name")
        found.append((text, row.counts))
    return found


def add(total: dict[str, int], more: dict[str, int]) -> dict[str, int]:
    return {name: total.get(name, 0) + more[name] for name in more}


def split(rows: list[tuple[str, dict[str, int]]], page: int, wanted: dict[str, dict[str, int]]) -> list[Locality]:
    """Hands each run of rows to the municipality whose printed counts the run adds up to."""
    out: list[Locality] = []
    left = dict(wanted)
    start = 0
    while start < len(rows):
        total: dict[str, int] = {}
        match = None
        for end in range(start, len(rows)):
            total = add(total, rows[end][1])
            hits = [name for name, counts in left.items() if counts == total]
            if hits:
                match = (end, hits)
                break
        if match is None:
            raise ValueError(f"page {page}: rows from {rows[start][0]!r} add up to no municipality's printed row")
        end, hits = match
        if len(hits) != 1:
            raise ValueError(f"page {page}: rows from {rows[start][0]!r} add up to {hits}")
        out += [Locality(hits[0], name, counts, page) for name, counts in rows[start : end + 1]]
        del left[hits[0]]
        start = end + 1
    if left:
        raise ValueError(f"page {page}: no rows for {sorted(left)}")
    return out


@functools.cache
def localities() -> list[Locality]:
    municipal = {part.name: part for part in section("2009-aioc").parts}
    if set(municipal) != set(AIOC):
        raise ValueError(f"page 119 prints {sorted(municipal)}")
    pages = atlas.pages(raw.cached(SOURCE, ATLAS2).path)
    out: list[Locality] = []
    for number in PAGES:
        rows = table(number, pages[number - 1])
        here = {name: part.counts for name, part in municipal.items() if AIOC[name] == DEPARTMENTS[number]}
        out += split(rows, number, here)
    return out


def check() -> list[str]:
    """Every municipality's localities add up to its row of the municipal table; returns a line for each."""
    municipal = {part.name: part for part in section("2009-aioc").parts}
    found = localities()
    lines = []
    for name, part in municipal.items():
        mine = [row for row in found if row.municipality == name]
        total: dict[str, int] = {}
        names = [row.name for row in mine]
        if len(set(names)) != len(names):
            raise ValueError(f"{name}: a locality is printed twice")
        for row in mine:
            c = row.counts
            if c["yes"] + c["no"] != c["valid"]:
                raise ValueError(f"{name}: {row.name} on page {row.page} does not add up: {c}")
            total = add(total, c)
        if total != part.counts:
            raise ValueError(f"{name}: localities add up to {total}, not the printed {part.counts}")
        lines.append(f"{name}: {len(mine)} localities add up exactly to page {part.page}: {total}")
    return lines


def build() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The 2009 indigenous autonomy referendums by locality, from the Atlas Electoral de Bolivia",
        depends=DEPENDS,
    )
    p.external |= {BOLIVIA}
    source = p.id("source", ATLAS2, "localities")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Supremo Electoral de Bolivia",
        title="Atlas Electoral de Bolivia, tomo II, capítulos III a VI (referéndum de autonomía indígena originario "
        "campesina por localidad, 2009)",
        url=ATLAS2,
        license=LICENSE,
        attribution=ATTRIBUTION,
    )
    p.artifact(raw.cached(SOURCE, ATLAS2), source)
    p.add("scheme", code=SCHEME, polity_id=BOLIVIA, kind="precinct", rank=3, noun="locality")
    election = p.id("election", DAY)
    municipal = {part.name: part for part in section("2009-aioc").parts}
    found = localities()
    values: list[tuple[str, str, str | None, int, int]] = []
    for name, code in CODES.items():
        mine = [row for row in found if row.municipality == name]
        cid = p.id("contest", election, f"indigenous-{name}")
        options = [p.id("option", cid, position) for position in (1, 2)]
        result_set = p.id("result_set", cid, "locality")
        printed = municipal[name].counts
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=cid,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=False,
            completeness=round(sum(row.counts["valid"] for row in mine) / printed["valid"], 6),
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        p.add(
            "citation",
            id=p.id("citation", result_set),
            entity_id=result_set,
            source_id=source,
            locator={"pages": sorted({row.page for row in mine}), "table": "Resultados por localidad"},
        )
        for option, count in zip(options, ("yes", "no"), strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=printed[count])
        for measure, count in MEASURES:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=printed[count])
        for row in mine:
            id = p.id("unit", SCHEME, f"{code}/{row.name}")
            p.add(
                "reporting_unit",
                id=id,
                polity_id=BOLIVIA,
                scheme_code=SCHEME,
                code=f"{code}/{row.name}",
                parent_id=unit(code),
                area_relation="within",
                area_id=area("bo.municipio", code),
                kind="standard",
            )
            p.name(id, row.name, "es", "official")
            c = row.counts
            values += [(result_set, id, options[0], VOTES, c["yes"]), (result_set, id, options[1], VOTES, c["no"])]
            values += [(result_set, id, None, measure, c[count]) for measure, count in MEASURES]
    p.frame(
        "result_value",
        pl.DataFrame(
            values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    over = [
        f"{row.name} of {row.municipality}: {row.counts['cast']:,} ballots cast, {row.counts['registered']:,} registered"
        for row in found
        if row.counts["cast"] > row.counts["registered"]
    ]
    short = [
        f"{row.name} of {row.municipality}: valid {row.counts['valid']:,} + blank {row.counts['blank']:,} + null "
        f"{row.counts['null']:,} is {row.counts['valid'] + row.counts['blank'] + row.counts['null']:,}, not the "
        f"{row.counts['cast']:,} printed"
        for row in found
        if row.counts["valid"] + row.counts["blank"] + row.counts["null"] != row.counts["cast"]
    ]
    if short:
        p.accept(
            "accounting.ballots_sum",
            "The Atlas prints ballots cast that differ from the valid, blank, and null ballots it prints for "
            + "; ".join(short)
            + ". The figures are as the Atlas prints them.",
        )
    if over:
        p.accept(
            "accounting.turnout",
            "The Atlas prints more ballots cast than registered voters for " + "; ".join(over) + ". The figures are "
            "as the Atlas prints them.",
        )
    return p


MEASURES = (
    (REGISTERED, "registered"),
    (BALLOTS, "cast"),
    (VALID, "valid"),
    (INVALID, "null"),
    (BLANK, "blank"),
)


def parse() -> None:
    for line in check():
        print(line)
    build().write()


def emit() -> None:
    package.run("load", [KEY])
