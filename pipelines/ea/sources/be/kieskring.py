"""The municipalities of each Flemish provincial council district, from the decrees that set the districts.

The 2006 districts come from the table annexed to the draft of the Provinciedecreet of 9 December 2005 that the
Flemish Parliament debated (document 815, 2005-2006); the Codex no longer shows the annex of the enacted decree,
which the Constitutional Court annulled in 2007. The 2012 and 2018 districts come from the annex to the Lokaal en
Provinciaal Kiesdecreet of 8 July 2011 in the version the Flemish Codex shows for the election (2012) or for the
municipal plan that held it (2018, after the municipal mergers of 1 January 2019).
"""

import functools
import html
import re
import subprocess
from html.parser import HTMLParser

from ea import raw

CODEX = "https://codex.vlaanderen.be/PrintDocument.ashx?id=1020561&datum="
PARLIAMENT = "https://docs.vlaamsparlement.be/files/pfile?id=1027560"
# election day -> (decree document, file suffix)
DECREES: dict[str, tuple[str, str]] = {
    "2006-10-08": (PARLIAMENT, ".pdf"),
    "2012-10-14": (CODEX + "2012-10-14", ".html"),
    "2018-10-14": (CODEX + "2019-01-01", ".html"),
}
# misspellings in the 2012 annex, by the folded spelling
ALIASES = {"begijnenbijk": "Begijnendijk", "kappelle op den bos": "Kapelle-op-den-Bos", "tielst": "Tielt"}
MARK = re.compile(r"\s*\(\d\)")


def fetch(source: str) -> None:
    for url, suffix in DECREES.values():
        raw.fetch(source, url, suffix=suffix)


def _clean(text: str) -> str:
    return MARK.sub("", html.unescape(text).replace("\xa0", " ")).strip()


class _Tables(HTMLParser):
    """Every table's rows as lists of (paragraphs, rowspan) cells, for the tables after the last annex heading."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[tuple[list[str], int]]]] = []
        self.row: list[tuple[list[str], int]] | None = None
        self.cell: list[str] | None = None
        self.span = 1
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self.row = []
        elif tag == "td":
            self.cell = []
            self.parts = []
            self.span = int(dict(attrs).get("rowspan") or 1)
        elif tag in ("p", "br") and self.cell is not None:
            self._flush()

    def _flush(self) -> None:
        if self.cell is not None:
            text = _clean("".join(self.parts))
            if text:
                self.cell.append(text)
            self.parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.cell is not None and self.row is not None:
            self._flush()
            self.row.append((self.cell, self.span))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.tables:
            self.tables[-1].append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.parts.append(data)


def _grid(rows: list[list[tuple[list[str], int]]]) -> list[list[list[str]]]:
    """The rows with the cells that a row span carries down filled in."""
    carried: dict[int, tuple[list[str], int]] = {}
    out: list[list[list[str]]] = []
    for row in rows:
        cells: list[list[str]] = []
        source = iter(row)
        column = 0
        while True:
            if column in carried:
                text, left = carried[column]
                cells.append(text)
                if left <= 1:
                    del carried[column]
                else:
                    carried[column] = (text, left - 1)
            else:
                taken = next(source, None)
                if taken is None:
                    break
                cells.append(taken[0])
                if taken[1] > 1:
                    carried[column] = (taken[0], taken[1] - 1)
            column += 1
        out.append(cells)
    return out


def _codex(day: str) -> dict[str, list[str]]:
    url, _ = DECREES[day]
    page = raw.cached("be.vlaanderen", url).path.read_text(encoding="utf-8", errors="replace")
    annex = page[page.rindex("Bijlage (") :]
    parser = _Tables()
    parser.feed(annex)
    found: dict[str, list[str]] = {}
    district = ""
    for rows in parser.tables:
        for cells in _grid(rows):
            if len(cells) == 2:  # 2018: district, municipality
                name, towns = (cells[0][0] if cells[0] else ""), cells[1]
                if name in ("provinciedistrict", ""):
                    if not name and towns:
                        found[district].extend(towns)
                    continue
                district = name
                found[district] = list(towns)
            elif len(cells) == 3:  # 2012: district, canton, municipalities
                if (cells[0] and cells[0][0] == "Provinciedistrict") or not cells[2]:
                    continue
                if cells[0]:  # a canton row without a district cell belongs to the district above
                    district = cells[0][0]
                found.setdefault(district, []).extend(cells[2])
    return found


def _parliament(day: str) -> dict[str, list[str]]:
    url, _ = DECREES[day]
    path = raw.cached("be.vlaanderen", url).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    lines = text[text.rindex("BIJLAGE") :].split("\n")
    found: dict[str, list[str]] = {}
    district = ""
    pending = ""
    skip = ("Samenstelling", "Tabel", "bij het", "Administratief", "PROVINCIE", "Districten", "BIJLAGE", "Stuk 815")
    for line in lines:
        text = line.strip()
        if not text or text.startswith(skip) or "Stuk 815" in text or re.fullmatch(r"\d+", text):
            continue
        cells = re.split(r"\s{2,}", text)
        if len(cells) == 4:  # district, capital, canton, municipality
            district = cells[0]
            found[district] = []
            name = cells[3]
        elif len(cells) in (1, 2):  # [canton,] municipality
            name = cells[-1]
        else:
            raise ValueError(f"unexpected annex row {cells}")
        name = pending + name
        pending = ""
        if name.endswith("-"):  # a hyphenated name that the page wraps
            pending = name
            continue
        found[district].append(name)
    return found


@functools.cache
def decree(day: str) -> dict[str, list[str]]:
    """The municipalities, as the decree names them, of each district, by the district's name."""
    return _parliament(day) if DECREES[day][1] == ".pdf" else _codex(day)


def fold(name: str) -> str:
    return re.sub(r"[^a-z]+", " ", name.casefold()).strip()


def members(day: str, towns: dict[str, str]) -> dict[str, list[str]]:
    """The municipality codes of each district of the election, by district name. `towns` maps each code of the
    election's municipal plan to its Dutch name."""
    code = {fold(name): nis for nis, name in towns.items()}
    out: dict[str, list[str]] = {}
    seen: set[str] = set()
    for district, names in decree(day).items():
        codes = []
        for name in names:
            nis = code.get(fold(ALIASES.get(fold(name), name)))
            if nis is None:
                raise ValueError(f"{day}: {name!r} in district {district} is not a municipality of the plan")
            if nis in seen:
                raise ValueError(f"{day}: {name!r} is in two districts")
            seen.add(nis)
            codes.append(nis)
        out[district] = codes
    missing = set(towns) - seen
    if missing:
        raise ValueError(f"{day}: municipalities outside every district: {sorted(missing)}")
    return out
