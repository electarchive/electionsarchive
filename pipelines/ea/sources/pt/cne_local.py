"""The official results map of Portugal's 2005 local elections, from the National Elections Commission.

Decreto-lei style "Mapa Oficial n.º 1-A/2006" (Diário da República, I-B, 6 February 2006) prints, for every municipality,
the results of its municipal assembly, municipal chamber, and each parish assembly, with the number of seats the law
fixed for each body and the seats each list won. The Ministry's results site for the same election prints votes only.
Each page of the PDF holds up to three columns of blocks; `pdftotext -bbox` gives each word's position, from which the
blocks are cut by column.
"""

import functools
import re
import subprocess
from dataclasses import dataclass, field

from ea import raw

SOURCE = "pt.cne"
URL = "https://www.cne.pt/sites/default/files/dl/resultados_al_2005.pdf"
COLUMNS = (140.0, 310.0)  # x positions that separate where a block's lines start on a page of three columns
TITLES = {
    "ASSEMBLEIA MUNICIPAL": "AM",
    "CÂMARA MUNICIPAL": "CM",
    "ASSEMBLEIA DE FREGUESIA": "AF",
}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)</word>')
PAGE = re.compile(r"<page ")
PERCENT = re.compile(r"^\d+,\d+$")
SEATS = re.compile(r"NÚMERO DE MANDATOS: ?(\d+)")


@dataclass
class Row:
    label: str
    votes: int
    seats: int | None


@dataclass
class Body:
    organ: str
    district: str
    municipality: str
    parish: str | None
    ordinal: int = 0  # which municipality of the map, in order
    seats: int | None = None
    registered: int | None = None
    cast: int | None = None
    blank: int | None = None
    invalid: int | None = None
    rows: list[Row] = field(default_factory=list)


def fetch() -> raw.Artifact:
    return raw.fetch(SOURCE, URL, suffix=".pdf")


def _words(artifact: raw.Artifact) -> list[list[tuple[float, float, float, str]]]:
    """Each page's words as (x, y, x end, text)."""
    done = subprocess.run(
        ["pdftotext", "-bbox", str(artifact.path), "-"], capture_output=True, check=True, text=True
    ).stdout
    pages = []
    for chunk in PAGE.split(done)[1:]:
        pages.append(
            [
                (float(x), float(y), float(end), text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
                for x, y, end, text in WORD.findall(chunk)
            ]
        )
    return pages


NUMBER = re.compile(r"^[\d.]+(?:,\d+)?%?$")


def _lines(words: list[tuple[float, float, float, str]]) -> list[tuple[float, int, str]]:
    """Cut a page into (y, column, text) chunks: the part of a printed line that belongs to one block.

    Words whose tops are within 3 points share a line. A line breaks into blocks where the words are far apart or where
    a figure is followed by a word, since a long list name pushes its figures into the next block's space.
    """
    out = []
    mine = sorted((y, x, end, text) for x, y, end, text in words)
    row: list[tuple[float, float, str]] = []
    top = 0.0
    for y, x, end, text in [*mine, (1e9, 0.0, 0.0, "")]:
        if row and y - top > 3:
            chunk: list[tuple[float, float, str]] = []
            for item in sorted(row):
                last = chunk[-1] if chunk else None
                if last and (
                    (NUMBER.match(last[2]) and not NUMBER.match(item[2]))
                    or (not NUMBER.match(last[2]) and not NUMBER.match(item[2]) and item[0] - last[1] > 12)
                ):
                    out.append(_chunk(top, chunk))
                    chunk = []
                chunk.append(item)
            out.append(_chunk(top, chunk))
            row = []
        if y < 1e9:
            if not row:
                top = y
            row.append((x, end, text))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _chunk(y: float, chunk: list[tuple[float, float, str]]) -> tuple[float, int, str]:
    text = " ".join(word for _, _, word in chunk)
    if text.startswith("•") and len(chunk) > 5:
        # A footnote can run into the row under it; the row's figures tell which block it belongs to.
        figure = next((x for x, _, word in chunk if NUMBER.match(word)), chunk[0][0])
        return y, sum(figure >= edge for edge in (210.0, 375.0)), text
    return y, sum(chunk[0][0] >= edge for edge in COLUMNS), text


def _spread(lines: list[tuple[float, int, str]]) -> list[tuple[float, int, str]]:
    """Split a line whose padding ran several parish names together into one line per column."""
    out = []
    for y, column, text in lines:
        parts = [part.strip() for part in re.split(r"x{4,}", text) if part.strip()]
        if len(parts) > 1 and "x" * 4 in text and not text.startswith(("DISTRITO", "CONCELHO")):
            out += [(y, column + i, f"xxxx{part}xxxx") for i, part in enumerate(parts)]
        else:
            out.append((y, column, text))
    return out


def _clean(label: str) -> str:
    """The map pads every list name with X to eight characters."""
    return re.sub(r"X+$", "", label.strip()) if re.search(r"X{1,7}$", label) else label.strip()


def _row(line: str) -> Row | None:
    tokens = line.split()
    for i, token in enumerate(tokens):
        if i and PERCENT.match(token) and tokens[i - 1].isdigit():
            rest = tokens[i + 1 :]
            if len(rest) > 1 or (rest and not rest[0].isdigit()):
                return None
            label = " ".join(tokens[: i - 1])
            if label.startswith("•"):
                # A footnote about a rectified seat sequence runs into the row printed under it.
                tail = re.search(r"[A-Z][A-Z./\-]*$", label)
                if tail is None:
                    return None
                label = tail.group(0)
            return Row(label, int(tokens[i - 1]), int(rest[0]) if rest else None)
    return None


def _figure(line: str, word: str) -> int | None:
    found = re.match(rf"{word} (\d+)(?: [\d,]+)?$", line)
    return int(found.group(1)) if found else None


@functools.cache
def parse(artifact: raw.Artifact) -> list[Body]:
    """Every body of the map, in the order printed."""
    bodies: list[Body] = []
    district = municipality = ""
    ordinal = 0
    for page in _words(artifact):
        # Open blocks per column, and the page's items in reading order.
        current: dict[int, Body | None] = {0: None, 1: None, 2: None}
        pending_name: dict[int, Body | None] = {0: None, 1: None, 2: None}
        for _, column, text in _spread(_lines(page)):
            if column == 0 and text.startswith("DISTRITO:"):
                district = text.removeprefix("DISTRITO:").strip()
                current = {0: None, 1: None, 2: None}
                continue
            if column == 0 and text.startswith("CONCELHO:"):
                municipality = text.removeprefix("CONCELHO:").strip()
                ordinal += 1
                current = {0: None, 1: None, 2: None}
                continue
            if text in TITLES:
                body = Body(TITLES[text], district, municipality, None, ordinal)
                current[column] = body
                pending_name[column] = body if body.organ == "AF" else None
                bodies.append(body)
                continue
            body = current[column]
            if body is None:
                continue
            found = SEATS.fullmatch(text)
            if found:
                body.seats = int(found.group(1))
                pending_name[column] = None
                continue
            if pending_name[column] is body:
                # A parish name can wrap onto a second line; the map pads each line with x.
                part = re.sub(r"^x+|x+$", "", text.strip()).strip()
                part = re.sub(r"\s*%\s*Md$", "", part)
                body.parish = f"{body.parish} {part}" if body.parish else part
                continue
            for name, attribute in (
                ("INSCRITOS", "registered"),
                ("VOTANTES", "cast"),
                ("BRANCOS", "blank"),
                ("NULOS", "invalid"),
            ):
                value = _figure(text, name)
                if value is not None:
                    setattr(body, attribute, value)
                    break
            else:
                row = _row(text)
                if row is not None:
                    row.label = _clean(row.label)
                    body.rows.append(row)
    return [b for b in bodies if b.registered is not None and b.seats is not None]
