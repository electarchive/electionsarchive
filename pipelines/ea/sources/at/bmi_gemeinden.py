"""Results by municipality from the scanned result brochures of Austria's presidential elections.

Each brochure prints, after its summary tables, the results of every municipality ("Örtliche Wahlergebnisse nach
Gemeinden"), grouped by state and political district, with the state, district, and statutory city totals and the
votes cast abroad. The pages were read into `bmi_tables/{brochure}-gemeinden.txt`, one file per brochure, holding
every round the brochure prints. A `page` line gives the angle the page image was turned to straighten it and the
column rules; each row gives the bands of its lines on the straightened page, so every stored number points at its
page and box.

Parsing checks every printed figure: within each row, the votes cast less the invalid votes against the valid votes
and the candidates against the valid votes; the municipalities of each district against the district row; the
municipalities and the votes cast abroad of each state against the state row; and the state rows against the
figures the summary tables give for each state. A mismatch raises, so a misread number never reaches a package.

Municipalities carry the code the Federal Ministry of the Interior gave them in 1995. Where a district held fewer
municipalities at the election than in 1995 (some were split off since), its municipalities are linked to those
areas as an approximation.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path

from ea.package import Package

TABLES = Path(__file__).with_name("bmi_tables")
FIRST = ("cast", "invalid", "valid")
COLUMNS = ("lfd", "name", "registered", "cast", "invalid", "valid", "A", "Ap", "B", "Bp", "C", "D")
"""The columns between the rules of a page: candidates are lettered in the order the page prints them."""


@dataclass(frozen=True)
class Page:
    number: int
    angle: float
    """Degrees the page image was turned counterclockwise about its center to straighten its lines."""
    rules: tuple[float, ...]
    offsets: tuple[float, ...]
    """How far each column's figures sit below the row's band, as a fraction of the page height."""


@dataclass
class Row:
    kind: str
    """`state`, `abroad`, `district`, or `gem` (a municipality, a statutory city, or a Vienna district)."""
    code: str
    page: int
    bands: list[tuple[float, float]]
    registered: int | None
    rounds: list[dict[str, int]]
    name: str


@dataclass
class Brochure:
    candidates: dict[int, list[str]] = field(default_factory=dict)
    """Transcription codes of the candidates of each round, in the order the pages print them."""
    pages: dict[int, Page] = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)
    split: set[str] = field(default_factory=set)
    """Districts whose municipalities of the election are linked to the areas of 1995 only as an approximation."""


def brochure_key(edition_key: str) -> str:
    """`bp-1992` for the rounds `bp-1992-1` and `bp-1992-2`."""
    parts = edition_key.split("-")
    return "-".join(parts[:2])


def path(brochure: str) -> Path:
    return TABLES / f"{brochure}-gemeinden.txt"


def exists(edition_key: str) -> bool:
    return path(brochure_key(edition_key)).exists()


def _number(text: str) -> int | None:
    return None if text == "-" else int(text)


@cache
def brochure(key: str) -> Brochure:
    b = Brochure()
    for line in path(key).read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        body, _, name = line.partition("|")
        words = body.split()
        head = words[0]
        if head == "round":
            b.candidates[int(words[1])] = words[2:]
        elif head == "split":
            b.split.update(words[1:])
        elif head == "page":
            angle = float(words[2].removeprefix("angle="))
            if words[3] != "x":
                raise ValueError(f"{key}: bad page line {line!r}")
            at = words.index("dy")
            rules = tuple(float(x) for x in words[4:at])
            offsets = tuple(float(x) for x in words[at + 1 :])
            if len(rules) != len(COLUMNS) + 1 or len(offsets) != len(COLUMNS):
                raise ValueError(f"{key}: bad page line {line!r}")
            b.pages[int(words[1])] = Page(int(words[1]), angle, rules, offsets)
        elif head in ("state", "abroad", "district", "gem"):
            code, page = words[1], int(words[2])
            bands = []
            rest = words[3:]
            while rest and rest[0].startswith("@"):
                y0, y1 = rest.pop(0).removeprefix("@").split(":")
                bands.append((float(y0), float(y1)))
            values = [_number(x) for x in rest]
            rounds: list[dict[str, int]] = []
            at = 1
            for number in sorted(b.candidates):
                columns = [*FIRST, *b.candidates[number]]
                chunk = values[at : at + len(columns)]
                if len(chunk) != len(columns) or any(v is None for v in chunk):
                    raise ValueError(f"{key}: row {code} on page {page} lacks round {number}")
                rounds.append({c: v for c, v in zip(columns, chunk, strict=True) if v is not None})
                at += len(columns)
            if at != len(values) or len(bands) != len(rounds):
                raise ValueError(f"{key}: row {code} on page {page} has {len(values)} figures, {len(bands)} bands")
            b.rows.append(Row(head, code, page, bands, values[0], rounds, name.strip()))
        else:
            raise ValueError(f"{key}: unknown line {line!r}")
    _check(key, b)
    return b


def _add(into: dict[str, int], values: dict[str, int]) -> None:
    for column, value in values.items():
        into[column] = into.get(column, 0) + value


def _check(key: str, b: Brochure) -> None:
    failed: list[str] = []
    for row in b.rows:
        if row.page not in b.pages:
            raise ValueError(f"{key}: row {row.code} on page {row.page}, which has no page line")
        for number, values in enumerate(row.rounds, start=1):
            listed = sum(values[c] for c in b.candidates[number])
            if values["cast"] - values["invalid"] != values["valid"] or listed != values["valid"]:
                failed.append(f"{row.kind} {row.code} round {number}: {values}")
    gems = [row for row in b.rows if row.kind == "gem"]
    if len({row.code for row in gems}) != len(gems):
        failed.append("a municipality code repeats")
    for total in b.rows:
        if total.kind not in ("state", "district"):
            continue
        prefix = total.code
        members = [row for row in gems if row.code.startswith(prefix)]
        if total.kind == "state":
            members += [row for row in b.rows if row.kind == "abroad" and row.code == prefix]
        for number in range(len(total.rounds)):
            summed: dict[str, int] = {}
            for row in members:
                _add(summed, row.rounds[number])
            if summed != total.rounds[number]:
                failed.append(f"{total.kind} {total.code} round {number + 1}: rows add up to {summed}")
        registered = sum(row.registered or 0 for row in members)
        if registered != total.registered:
            failed.append(f"{total.kind} {total.code}: registered voters add up to {registered}")
    if failed:
        raise ValueError(f"{key}: " + "; ".join(failed))


def state_figures(edition_key: str, round_number: int) -> dict[str, dict[str, int | None]]:
    """Each state's figures for one round, keyed like the summary transcription's columns."""
    b = brochure(brochure_key(edition_key))
    found: dict[str, dict[str, int | None]] = {}
    for row in b.rows:
        if row.kind != "state":
            continue
        values = row.rounds[round_number - 1]
        found[row.code] = {
            "registered": row.registered,
            "cast": values["cast"],
            "invalid": values["invalid"],
            "valid": values["valid"],
            **{code: values[code] for code in b.candidates[round_number]},
        }
    return found


def box(page: Page, size: tuple[int, int], x: tuple[float, float], y: tuple[float, float]) -> tuple[float, ...]:
    """Map a box on the straightened page back onto the page as rendered, as fractions of its width and height."""
    width, height = size
    a = math.radians(page.angle)
    corners = []
    for u in x:
        for v in y:
            dx, dy = (u - 0.5) * width, (v - 0.5) * height
            corners.append(
                (
                    (dx * math.cos(a) - dy * math.sin(a)) / width + 0.5,
                    (dx * math.sin(a) + dy * math.cos(a)) / height + 0.5,
                )
            )
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return (
        round(max(min(xs), 0.0), 4),
        round(max(min(ys), 0.0), 4),
        round(min(max(xs), 1.0), 4),
        round(min(max(ys), 1.0), 4),
    )


def cell(page: Page, column: str, band: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
    """The edges and band of a column's cell on the straightened page."""
    index = COLUMNS.index(column)
    dy = page.offsets[index]
    return (page.rules[index], page.rules[index + 1]), (band[0] + dy, band[1] + dy)


@dataclass(frozen=True)
class Context:
    """What the election package hands over to add the result set by municipality."""

    key: str
    round_number: int
    day: date
    result_set: str
    supersedes: str
    election_id: str
    contest: str
    options: dict[str, str]
    national: dict[str, int | None]


def municipal_pages(edition_key: str) -> list[int]:
    return sorted(brochure(brochure_key(edition_key)).pages)


def add_result_set(
    p: Package,
    c: Context,
    page_ids: dict[int, str],
    sizes: dict[int, tuple[int, int]],
    key_fn: Callable[..., str],
    area_fn: Callable[[str, str], str],
    polity: str,
    measures: dict[str, int],
    votes: int,
) -> None:
    """Add the result set by municipality for one round."""
    b = brochure(brochure_key(c.key))
    candidates = b.candidates[c.round_number]
    letters = dict(zip(candidates, "ABCD", strict=False))
    approximate = b.split
    for column, value in c.national.items():
        if value is None:
            continue
        if column in measures:
            p.add("declared", result_set_id=c.result_set, option_id=None, measure=measures[column], value=value)
        else:
            p.add("declared", result_set_id=c.result_set, option_id=c.options[column], measure=votes, value=value)
    day = c.day
    for row in b.rows:
        if row.kind not in ("gem", "abroad"):
            continue
        if row.kind == "gem":
            unit = key_fn("unit", c.key, "gem", row.code)
            district = row.code[:3]
            if district in approximate:
                relation, place = "within", area_fn("at.bezirk", district)
            else:
                relation, place = "equals", area_fn("at.gemeinde", row.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=polity,
                scheme_code="at.gemeinde",
                code=f"{day}:{row.code}",
                area_relation=relation,
                area_id=place,
                kind="standard",
            )
            if district in approximate:
                p.add(
                    "reporting_unit_area",
                    unit_id=unit,
                    area_id=area_fn("at.gemeinde", row.code),
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                    approximate=True,
                    partial=False,
                    method="official_list",
                )
            p.name(unit, row.name, "de", "official")
        else:
            unit = key_fn("unit", c.key, "abroad", row.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=polity,
                scheme_code="at.gemeinde",
                code=f"{day}:{row.code}-abroad",
                area_relation="within",
                area_id=area_fn("at.land", row.code),
                kind="aggregate",
            )
            p.name(unit, "Im Ausland abgegeben", "de", "official")
        page = b.pages[row.page]
        band = row.bands[c.round_number - 1]
        values: dict[str, int | None] = dict(row.rounds[c.round_number - 1])
        if row.kind == "gem":
            values["registered"] = row.registered
        for column, value in values.items():
            if value is None:
                continue
            if column in measures:
                option_id, measure = None, measures[column]
                edges, y = cell(page, column, row.bands[0] if column == "registered" else band)
            else:
                option_id, measure = c.options[column], votes
                edges, y = cell(page, letters[column], band)
            p.add(
                "result_value",
                result_set_id=c.result_set,
                unit_id=unit,
                option_id=option_id,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
            x0, y0, x1, y1 = box(page, sizes[row.page], edges, y)
            p.add(
                "value_locator",
                result_set_id=c.result_set,
                unit_id=unit,
                option_id=option_id,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=page_ids[row.page],
                source_id=None,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
            )
