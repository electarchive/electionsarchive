"""The Central Electoral Commission's summary pages: declared totals, seats, and elected members.

Each election's home page links summary pages under `/rinkimai/<id>/<round>/<n>/rezultatai/lt/`. Those pages print
what the Commission declared for each constituency, mayor, and council contest (the roll, the ballots cast, the valid
and invalid ballots, the votes of each candidate or list, and the council seats of each list) and who was elected to
the Seimas, the European Parliament, and the mayoralties. The polling-district files carry the counts; these pages
carry the declared figures the counts must add up to.
"""

import html as markup
import re
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import NamedTuple

from ea import raw

SOURCE = "lt.vrk"
SITE = "https://www.vrk.lt"
BASE = f"{SITE}/statiniai/puslapiai"

REGISTERED, CAST, VALID, INVALID = 2, 3, 4, 5


class Cell(NamedTuple):
    text: str
    href: str | None  # the page path the cell's first link opens, as the site writes it (`?srcUrl=<path>`)


Row = list[Cell]


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.done: list[list[Row]] = []
        self.stack: list[dict[str, list]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.stack.append({"rows": [], "row": [], "cell": []})
        elif not self.stack:
            return
        elif tag == "tr":
            top = self.stack[-1]
            top["row"] = []
            top["rows"].append(top["row"])
        elif tag in ("td", "th"):
            top = self.stack[-1]
            cell: list = [[], None]
            top["cell"] = cell
            top["row"].append(cell)
        elif tag == "a" and self.stack[-1]["cell"] and self.stack[-1]["cell"][1] is None:
            link = dict(attrs).get("href") or ""
            found = re.search(r"srcUrl=([^&]+)", link)
            self.stack[-1]["cell"][1] = found.group(1) if found else ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.stack:
            top = self.stack.pop()
            self.done.append(
                [[Cell(clean("".join(cell[0])), cell[1] or None) for cell in row] for row in top["rows"] if row]
            )
        elif tag in ("td", "th") and self.stack:
            self.stack[-1]["cell"] = []

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1]["cell"]:
            self.stack[-1]["cell"][0].append(data)


def clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").replace("﻿", " ").split())


def tables(html: str) -> list[list[Row]]:
    parser = _Tables()
    parser.feed(html)
    parser.close()
    return parser.done


def flat(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL)
    return clean(markup.unescape(re.sub(r"<[^>]+>", " ", html)))


def table(html: str, *headers: str) -> list[Row]:
    """The first table whose header rows hold every one of the words."""
    for found in tables(html):
        head = " ".join(cell.text for row in found[:3] for cell in row)
        if all(word in head for word in headers):
            return found
    return []


def links(html: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"srcUrl=([^'\"&]+)", html)))


def number(text: str) -> int | None:
    digits = text.replace(" ", "")
    return int(digits) if digits.isdigit() else None


def url(path: str) -> str:
    return f"{SITE}/statiniai/puslapiai{path}"


def cached(path: str) -> str | None:
    """The text of a page from the raw cache, or none when it was not downloaded."""
    address = url(path)
    if address not in raw.keys(SOURCE):
        return None
    return raw.cached(SOURCE, address).path.read_text(encoding="utf-8")


def round_of(path: str) -> int | None:
    found = re.match(r"/rinkimai/\d+/(\d)/", path)
    return int(found.group(1)) if found else None


def code_of(text: str) -> str | None:
    """The number a constituency or municipality has in its listed name, `12. Verkių` giving `12`."""
    found = re.match(r"(\d+)\.", text)
    return found.group(1) if found else None


class Contest:
    """What the Commission declared for one constituency, mayor, or council contest."""

    def __init__(self) -> None:
        self.totals: dict[int, int] = {}
        self.votes: dict[str, int] = {}  # by candidate name, or by the number of a list
        self.seats: dict[str, int] = {}  # by the number of a list
        self.elected: list[str] = []  # candidate names
        self.annulled = False  # a court annulled the declared result


class Member(NamedTuple):
    name: str
    area: str  # the constituency's listed name, or `Daugiamandatė` for the list seats
    area_path: str | None
    party: str


def constituencies(html: str) -> dict[str, Contest]:
    """The declared ballots of each constituency, from the summary table of one round."""
    found: dict[str, Contest] = {}
    for row in table(html, "Apygarda", "Dalyvavo"):
        code = code_of(row[0].text)
        values = [number(cell.text) for cell in row]
        if code is None or len(row) < 10 or values[3] is None:
            continue
        contest = found.setdefault(code, Contest())
        for measure, at in ((REGISTERED, 3), (CAST, 4), (INVALID, 6), (VALID, 8)):
            value = values[at]
            if value is not None:
                contest.totals[measure] = value
    return found


def members(html: str, columns: tuple[int, int, int | None] = (0, 1, 2)) -> list[Member]:
    """The rows of a page of elected members: candidate, area, and the party or list, by column."""
    name, area, party = columns
    out = []
    for row in table(html, "Kandidatas"):
        if row[0].text in ("Kandidatas", "Savivaldybė") or len(row) <= max(name, area, party or 0):
            continue
        out.append(Member(row[name].text, row[area].text, row[area].href, row[party].text if party is not None else ""))
    return out


MARKERS = (
    (REGISTERED, r"rinkėjų sąraše įrašyta rinkėjų"),
    (CAST, r"dalyvavo rinkėjų"),
    (INVALID, r"negaliojančių biuletenių"),
    (VALID, r"(?<!ne)galiojančių biuletenių"),
)
DASH = r"\s*[–-]\s*"


def declared(html: str) -> tuple[dict[int, int], dict[int, int]]:
    """The ballots a municipality page declares, as (mayor, council) figures by measure."""
    text = flat(html)
    start = min(
        (found.start() for _, expression in MARKERS if (found := re.search(expression, text))), default=len(text)
    )
    text = text[start:]
    text = re.split(r"Tarybos narių rinkimai|Mero rinkimai", text)[0]
    where = sorted(
        (found.start(), measure) for measure, expression in MARKERS if (found := re.search(expression, text))
    )
    mayor: dict[int, int] = {}
    council: dict[int, int] = {}
    for at, (begin, measure) in enumerate(where):
        end = where[at + 1][0] if at + 1 < len(where) else len(text)
        part = text[begin:end]
        first = re.search(rf"merų{DASH}(\d+)", part) or re.search(rf"{DASH}(\d+)\s*\([^)]*\)\s*merų", part)
        if first is None and "tarybos narių" not in part:
            first = re.search(r"^[^–:-]*[–:-]\s*(\d+)", part)  # a municipality with a mayor's election only
        second = re.search(rf"tarybos narių rinkimuose{DASH}(\d+)", part) or re.search(
            r"(\d+)\s*\([^)]*\)\s*(?:kitų )?tarybos narių", part
        )
        if first:
            mayor[measure] = int(first.group(1))
        if second:
            council[measure] = int(second.group(1))
    return mayor, council


def municipality(html: str) -> tuple[Contest | None, Contest | None]:
    """The mayor and council contests a municipality page declares."""
    mayor_totals, council_totals = declared(html)
    mayor = council = None
    if mayor_totals:
        mayor = Contest()
        mayor.totals = mayor_totals
        found = re.search(
            r"(?:meru išrinktas|mere išrinkta)\s+(.+?)(\s+Vyriausiosios rinkimų komisijos sprendimas.*?panaikintas "
            r"teismo sprendimu)?\s+Kandidatas",
            flat(html),
            re.IGNORECASE,
        )
        if found and found.group(2):
            mayor.annulled = True  # a court annulled the Commission's confirmation of the result
        elif found:
            mayor.elected.append(found.group(1))
        for row in table(html, "Kandidatas", "Iškėlė"):
            if row[0].text in ("", "Kandidatas", "Iš viso") or len(row) < 5:
                continue
            box, post, total = (number(row[at].text) for at in (2, 3, 4))
            if box is None or post is None or total is None:
                continue
            if box + post != total:
                raise ValueError(f"{row[0].text}: {box} + {post} votes are not {total}")
            mayor.votes[row[0].text] = total
    if council_totals:
        council = Contest()
        council.totals = council_totals
        for row in table(html, "Kandidatų sąrašo pavadinimas"):
            box, post, total = (number(row[at].text) for at in (3, 4, 5)) if len(row) > 8 else (None, None, None)
            if number(row[0].text) is None or box is None or post is None or total is None:
                continue
            if box + post != total:
                raise ValueError(f"list {row[0].text}: {box} + {post} votes are not {total}")
            council.votes[row[0].text] = total
            seats = number(row[-1].text)
            if seats:
                council.seats[row[0].text] = seats
    return mayor, council


def named(text: str) -> str:
    return clean(text).casefold()


def hits(paths: Iterable[str], word: str) -> list[str]:
    return [path for path in paths if path.endswith(word) or f"/{word}" in path]
