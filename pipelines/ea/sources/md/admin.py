"""Moldova's first-level administrative-territorial units (municipalities, towns, communes, and villages), from the
annexes to the Central Electoral Commission's decision of 4 August 2023 on the number of councillors, checked against
the Commission's table of electoral circumscriptions. The annexes list the 34 second-level units (districts and the
municipalities of Chișinău and Bălți) and, under each of those and under Gagauzia, the units of the first level."""

import html
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache

import pymupdf

from ea import raw
from ea.package import Package
from ea.sources.md import cec, local

SOURCE = "md.cec"
GEO = "md.cec/admin-geo"
PAGE = "https://a.cec.md/index.php/ro/numarul-circumscriptiilor-electorale-si-sectiilor-de-votare-15130.html"
ANNEXES = "https://a.cec.md/storage/ckfinder/files/1157_Anexe%201-2_stabilire%20numar%20mandate%20consilier(1).pdf"
KINDS = {"orașul": "oras", "comuna": "comuna", "satul": "sat", "mun.": "municipiu"}
LABELS = {"municipiu": "municipality", "oras": "town", "comuna": "commune", "sat": "village"}
SECOND_LEVEL = 34
FIRST_LEVEL = 896
ROW = re.compile(r"^(\d+)\.?\s+(orașul|comuna|satul|mun\.)\s+(.+?)\s+(\d+)\s+(\d+)$")
HEADING = re.compile(r"^(Municipiul|Raionul|UTA)\s+(\D+)$")
SECOND = re.compile(r"^(\d+)\.?\s+(municipiul|raionul)\s+(.+?)\s+(\d+)\s+(\d+)$")


@dataclass(frozen=True)
class Unit:
    """A first-level unit as the annex prints it."""

    district: str
    kind: str
    name: str
    population: int
    mandates: int

    @property
    def key(self) -> str:
        return f"{self.district}:{self.kind}:{local.fold(self.name)}"


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    raw.fetch(SOURCE, ANNEXES, suffix=".pdf")


def lines() -> list[str]:
    """The annexes' text, one string per printed row, with the cells of a row joined in reading order."""
    found: list[str] = []
    with pymupdf.open(raw.cached(SOURCE, ANNEXES).path) as document:
        for page in document:
            words = sorted(page.get_text("words"), key=lambda w: (round(w[1]), w[0]))
            row: list[tuple[float, str]] = []
            top = -100.0
            for word in words:
                if abs(word[1] - top) > 3 and row:
                    found.append(" ".join(text for _, text in sorted(row)))
                    row = []
                if not row:
                    top = word[1]
                row.append((word[0], word[4]))
            if row:
                found.append(" ".join(text for _, text in sorted(row)))
    return [" ".join(line.split()) for line in found]


@cache
def annexes() -> tuple[dict[str, tuple[str, int, int]], list[Unit]]:
    """The second-level units (name, population, mandates) by district key, and the first-level units."""
    second: dict[str, tuple[str, int, int]] = {}
    units: list[Unit] = []
    district: str | None = None
    for line in lines():
        if "Anexa nr." in line:
            district = None
            continue
        match = SECOND.match(line)
        if match:
            name = local.fix(match.group(3))
            second[local.district_key(name)] = (name, int(match.group(4)), int(match.group(5)))
            continue
        heading = HEADING.match(local.fix(line))
        if heading:
            district = local.district_key(heading.group(2))
            continue
        match = ROW.match(local.fix(line))
        if match and district is not None:
            kind = KINDS[match.group(2)]
            units.append(Unit(district, kind, match.group(3), int(match.group(4)), int(match.group(5))))
    return second, units


def circumscriptions() -> dict[str, int]:
    """The number of first-level circumscriptions the Commission's table gives each second-level one, by district key."""
    text = raw.cached(SOURCE, PAGE).path.read_text(encoding="utf-8", errors="replace")
    table = text[text.index('<table class="Table"') :]
    found: dict[str, int] = {}
    for row in re.findall(r"<tr.*?</tr>", table[: table.index("</table>")], flags=re.S):
        cells = [
            " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())
            for cell in re.findall(r"<t[dh].*?</t[dh]>", row, flags=re.S)
        ]
        match = re.match(r"^CE[MR]?\s+(.+?)\s+nr\.\s*\d+$", cells[1]) if len(cells) > 2 else None
        if match:
            found[local.district_key(re.sub(r"^UTA\s+", "", match.group(1)))] = int(cells[2])
    return found


def check() -> None:
    """Stop unless the annexes and the circumscription table give the same units."""
    second, units = annexes()
    counts = Counter(unit.district for unit in units)
    table = circumscriptions()
    if len(second) != SECOND_LEVEL:
        raise ValueError(f"{len(second)} second-level units in the annex, not {SECOND_LEVEL}")
    if len(units) != FIRST_LEVEL or sum(table.values()) != FIRST_LEVEL:
        raise ValueError(f"{len(units)} first-level units in the annex and {sum(table.values())} in the table")
    if dict(counts) != table:
        raise ValueError(f"annex and table disagree: {sorted(set(counts.items()) ^ set(table.items()))}")
    if missing := set(second) - set(local.districts()):
        raise ValueError(f"second-level units with no area in {local.GEO}: {sorted(missing)}")
    if repeated := [k for k, n in Counter(unit.key for unit in units).items() if n > 1]:
        raise ValueError(f"repeated first-level units: {repeated}")


def geo() -> Package:
    check()
    _, units = annexes()
    p = Package(
        GEO,
        SOURCE,
        "Moldova's 896 first-level administrative-territorial units (municipalities, towns, communes, and villages), "
        "each under its district, municipality, or Gagauzia, from the Central Electoral Commission's annexes of "
        "August 2023",
        depends=[local.GEO, "md.cec/registry"],
    )
    p.external.add(local.MOLDOVA)
    p.artifact(raw.cached(SOURCE, ANNEXES), cec.CEC_SOURCE)
    p.artifact(raw.cached(SOURCE, PAGE), cec.CEC_SOURCE)
    p.add(
        "scheme",
        code="md.localitate",
        polity_id=local.MOLDOVA,
        kind="administrative",
        rank=2,
        noun="locality",
        kinds=LABELS,
    )
    for unit in sorted(units, key=lambda u: u.key):
        id = p.id("area", "md.localitate", unit.key)
        p.add("area", id=id, scheme_code="md.localitate")
        p.add("area_code", area_id=id, code=unit.key)
        p.add("area_kind", area_id=id, kind=unit.kind, source_id=cec.CEC_SOURCE)
        p.add("area_parent", area_id=id, parent_id=local.district_area(unit.district))
        name = local.fix(unit.name)
        p.name(id, name, "ro", "official")
        p.name(id, name, "en", "english", False)
    return p


def packages() -> list[str]:
    return [GEO]


def parse() -> None:
    geo().write()
