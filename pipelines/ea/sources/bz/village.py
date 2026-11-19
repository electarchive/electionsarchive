"""Belize: village and community council elections, by village.

The Elections and Boundaries Department publishes one PDF for each village in the 2016, 2019, and 2022 elections,
combined district sheets for 2025, and one sheet for each by-election. The text layer of each PDF is read with
`pdftotext -bbox`: each candidate row is anchored on its position word, and each village's printed totals are
assigned to the chairperson or member count by their place on the sheet and checked against the candidates' votes.
"""

import functools
import io
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from html import unescape
from itertools import pairwise
from uuid import uuid5
from xml.etree import ElementTree

import shapely
import shapely.geometry
import shapely.ops

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "bz.village"
SITE = "https://elections.gov.bz"
YEAR_PAGES = {
    2016: f"{SITE}/2016-village-council-election-results/",
    2019: f"{SITE}/2019-village-council-election-results/",
    2022: f"{SITE}/2022-village-council-election-results/",
    2025: f"{SITE}/2025-village-council-elections/",
}
BY_PAGE = f"{SITE}/bi-election-results/"
U = f"{SITE}/wp-content/uploads"
BY_FILES = [
    f"{U}/2023/12/15-JANUARY-2023-BY-ELECTION-Isabella-Bank.pdf",
    f"{U}/2023/12/15-JANUARY-2023-BY-ELECTIONS-Boston.pdf",
    f"{U}/2023/12/15-JANUARY-2023-BY-ELECTIONS-Crooked-Tree.pdf",
    f"{U}/2023/12/15-JANUARY-2023-BY-ELECTIONS-Gardenia.pdf",
    f"{U}/2023/12/22ND-JANUARRY-2023-BY-ELECTION-FOREST-HOME.pdf",
    f"{U}/2023/12/22ND-JANUARRY-2023-BY-ELECTIONS-MEDINA-BANK.pdf",
    f"{U}/2024/01/CONCEPCION-07-January-2024-1-1.pdf",
    f"{U}/2026/06/31-May-2026-BY-ELECTIONS-San-Joaquin-1.pdf",
    f"{U}/2026/06/28-June-2026-Tower-HIll-Bi-Election-1.pdf",
    f"{U}/2026/08/Los-Tambos.pdf",
    f"{U}/2026/09/San-Lazaro-Official-Result-.pdf",
]
PLACES = "https://production-raw-data-api.s3.amazonaws.com/ISO3/BLZ/populated_places/hotosm_blz_populated_places_osm_geojson.zip"
LINK = re.compile(r'<a [^>]*href="([^"]*\.pdf)"', re.DOTALL)


def listing(page: str) -> list[str]:
    html = raw.cached(SOURCE, page).path.read_text(errors="replace")
    return list(dict.fromkeys(unescape(u) for u in LINK.findall(html)))


def fetch() -> None:
    pages = [*YEAR_PAGES.values(), BY_PAGE]
    for page in pages:
        raw.fetch(SOURCE, page, suffix=".html")
    urls = [u for page in YEAR_PAGES.values() for u in listing(page)]
    urls += BY_FILES
    raw.fetch_many(SOURCE, urls, suffix=".pdf")
    raw.fetch(SOURCE, PLACES, suffix=".zip")


DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
POSITIONS = {"Chairperson", "Member", "Chairpersons", "Members"}


@dataclass
class Word:
    x0: float
    x1: float
    y0: float
    y1: float
    text: str

    @property
    def x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Cand:
    name: str
    votes: int | None
    elected: bool
    tacit: bool
    y: float
    above: list[Word] = field(default_factory=list)
    below: list[Word] = field(default_factory=list)


@dataclass
class Group:
    cands: list[Cand] = field(default_factory=list)
    registered: int | None = None
    polled: int | None = None
    spoilt: int | None = None
    rejected: int | None = None


@dataclass
class Village:
    district: str
    name: str
    day: str
    chair: Group
    members: Group
    page: int
    sheet: str


@functools.cache
def pages(path: str) -> tuple[tuple[Word, ...], ...]:
    xml = subprocess.run(["pdftotext", "-bbox", path, "-"], capture_output=True, text=True, check=True).stdout
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", xml)
    xml = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", xml)
    root = ElementTree.fromstring(xml)
    out = []
    for page in root.iter("{http://www.w3.org/1999/xhtml}page"):
        out.append(
            tuple(
                Word(
                    float(w.get("xMin", 0)),
                    float(w.get("xMax", 0)),
                    float(w.get("yMin", 0)),
                    float(w.get("yMax", 0)),
                    (w.text or "").strip(),
                )
                for w in page.iter("{http://www.w3.org/1999/xhtml}word")
            )
        )
    return tuple(out)


def lines(words: list[Word], tol: float = 4.0) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.y, w.x0)):
        if rows and abs(rows[-1][0].y - w.y) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w.x0) for r in rows]


def is_number(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(,\d{3})*|\d+", text))


HEADER_WORDS = {
    "Election", "Nomination", "Date", "Village", "Candidates", "Candidate", "Name", "Position", "No.", "of", "Votes",
    "Received", "Remarks", "Registered", "Electors", "Voters", "Total", "Polled", "%", "Turnout", "Spoilt", "Rejected",
    "#",
}  # fmt: skip


def number(text: str) -> int:
    return int(text.replace(",", ""))


def blocks(words: list[Word], anchor: str = "Village") -> list[tuple[float, float, float, list[Word]]]:
    """Split a page into one block per header: (district line y, header bottom, body end, body words)."""
    if anchor == "Village":
        tops = sorted(
            w.y - 9
            for w in words
            if w.text == "Village" and any(o.text in ("Candidates", "Candidate") and abs(o.y - w.y) < 12 for o in words)
        )
    else:
        tops = sorted(w.y for w in words if w.x0 < 140 and w.text in ("Election", "Nomination"))
    out = []
    for n, top in enumerate(tops):
        if n + 1 < len(tops):
            nxt = tops[n + 1]
            above = [w.y0 for w in words if w.text in HEADER_WORDS and nxt - 22 <= w.y <= nxt + 21 and w.y > top + 30]
            end = min(above, default=nxt - 14) - 1
        else:
            end = 1e9
        head = [w for w in words if top - 3 <= w.y <= top + 40 and w.text in HEADER_WORDS]
        bottom = max(w.y1 for w in head if w.y <= top + 30)
        body = [w for w in words if bottom < w.y < end]
        # a village name can sit among the header words
        body += [
            w
            for w in words
            if top < w.y <= bottom and w.text not in HEADER_WORDS and w.x0 < 140 and not DATE.fullmatch(w.text)
        ]
        out.append((top, bottom, end, body))
    return out


def district_of(words: list[Word], top: float) -> str:
    """The district line nearest above a block's header, or the first one on the page."""
    found = [
        (row[0].y, " ".join(w.text for w in row))
        for row in lines(words)
        if any(w.text.lower() == "district" for w in row)
    ]
    above = [f for f in found if f[0] <= top + 5]
    pick = max(above) if above else min(found, default=(0.0, ""))
    return re.sub(r"\s*\(cont\.?\)", "", pick[1], flags=re.IGNORECASE).strip()


def centers(words: list[Word], top: float, bottom: float) -> dict[str, float]:
    head = [w for w in words if top - 16 <= w.y <= bottom]
    found: dict[str, float] = {}
    for w in head:
        match w.text:
            case "Received":
                found["votes"] = w.x
            case "Registered":
                found["registered"] = w.x
            case "Spoilt":
                found["spoilt"] = w.x
            case "Rejected":
                found["rejected"] = w.x
    total = [w for w in head if w.text == "Total"]
    if total:
        below = [w for w in head if w.text == "Polled" and w.y > total[0].y and abs(w.x - total[0].x) < 30]
        found["polled"] = below[0].x if below else total[0].x
    return found


def following(rows: list[list[Word]], row: list[Word]) -> str:
    """The position of the first labeled row below this one."""
    for other in rows[rows.index(row) + 1 :]:
        for w in other:
            if w.text in POSITIONS:
                return w.text
    return "Chairperson"


def with_votes(cols: dict[str, float], body: list[Word]) -> dict[str, float]:
    """The votes column's position, from the votes beside the position words when the header does not name it."""
    if "votes" in cols:
        return cols
    firsts = sorted(
        n.x
        for row in lines(body)
        for m in row
        if m.text in POSITIONS
        for n in row
        if is_number(n.text) and 0 < n.x0 - m.x1 < 90
    )
    if not firsts:
        firsts = [m.x + 90 for m in body if m.text in POSITIONS]
    return {**cols, "votes": firsts[len(firsts) // 2]}


def read_block(
    district: str, cols: dict[str, float], body: list[Word], page: int, sheet: str, flips: int = 0
) -> Village:
    rows = lines(body)
    data: list[tuple[list[Word], Word]] = []
    last: Word | None = None
    unlabeled = 0
    for row in rows:
        marks = [w for w in row if w.text in POSITIONS]
        if not marks:
            # a candidate row whose position cell is empty takes the position of the row above it
            votes = [w for w in row if is_number(w.text) and abs(w.x - cols["votes"]) < 30]
            if votes and any(w.x1 < votes[0].x0 - 40 for w in row):
                text = last.text if last is not None else following(rows, row)
                if flips >> unlabeled & 1:
                    text = "Member" if text.startswith("Chair") else "Chairperson"
                unlabeled += 1
                marks = [Word(votes[0].x0 - 30, votes[0].x0 - 10, row[0].y0, row[0].y1, text)]
            else:
                continue
        mark = marks[0]
        last = mark
        right = [w for w in row if w.x0 > mark.x1]
        if any(w.text == "Uncontested" for w in right) or any(
            is_number(w.text) and abs(w.x - cols["votes"]) < 30 for w in right
        ):
            data.append((row, mark))
    if not data:
        if any(w.text == "Nominations" for w in body):
            head = next(row for row in rows if any(w.text in POSITIONS for w in row))
            name = " ".join(w.text for w in head if w.x1 < min(w.x0 for w in body if w.text == "No") - 3)
            dates = [w.text for w in body if DATE.fullmatch(w.text)]
            return Village(district, name, dates[0] if dates else "", Group(), Group(), page, sheet)
        raise ValueError(f"{sheet} page {page}: no candidate rows")
    starts = [
        min((w.x0 for w in row if w.x1 <= mark.x0 and not DATE.fullmatch(w.text)), default=None) for row, mark in data
    ]
    counts: dict[float, int] = {}
    for s in starts:
        if s is not None:
            counts[round(s)] = counts.get(round(s), 0) + 1
    start = max(counts, key=lambda k: (counts[k], -k)) if len(data) > 1 else None
    village: list[Word] = []
    data_y = {round(row[0].y) for row, _ in data}
    members_y = [
        w.y for row in rows for w in row if w.text == "Members" and round(row[0].y) not in data_y and w.x0 < mark.x0
    ]
    cands: list[tuple[str, Cand]] = []
    for row, mark in data:
        left = [w for w in row if w.x1 <= mark.x0 and not DATE.fullmatch(w.text)]
        if start is None:
            gaps = [(b.x0 - a.x1, n) for n, (a, b) in enumerate(pairwise(left), 1)]
            big = [g for g in gaps if g[0] > 12]
            cut = big[0][1] if big else 0
        else:
            cut = next((n for n, w in enumerate(left) if w.x0 >= start - 3), len(left))
        village += left[:cut]
        name = " ".join(w.text for w in left[cut:])
        right = [w for w in row if w.x0 > mark.x1]
        votes = [w for w in right if is_number(w.text) and abs(w.x - cols["votes"]) < 30]
        cands.append(
            (
                mark.text,
                Cand(
                    name,
                    number(votes[0].text) if votes else None,
                    any(w.text == "Elected" for w in right),
                    any(w.text == "Uncontested" for w in right),
                    mark.y,
                ),
            )
        )
    # a name that wraps leaves its other lines in rows of their own, above or below the row that holds the votes
    used = {id(w) for r, _ in data for w in r}
    floor = start - 3 if start is not None else min(w.x0 for r, m in data for w in r if w.x1 <= m.x0)
    for w in sorted(body, key=lambda w: (w.y, w.x0)):
        if id(w) in used or DATE.fullmatch(w.text) or is_number(w.text) or w.text in POSITIONS or "%" in w.text:
            continue
        if w.x0 < floor or w.x1 > data[0][1].x0 + 2:
            continue
        near = min(range(len(data)), key=lambda n: abs(data[n][1].y - w.y))
        if abs(data[near][1].y - w.y) > 17:
            continue
        (cands[near][1].above if w.y < data[near][1].y else cands[near][1].below).append(w)
    for _, cand in cands:
        cand.name = re.sub(r",(?=\S)", ", ", cand.name)
        cand.name = " ".join([*(w.text for w in cand.above), cand.name, *(w.text for w in cand.below)])
    first = data[0][0][0].y
    for row in rows:
        if row[0].y >= first - 1:
            break
    chair = Group([c for p, c in cands if p.startswith("Chair")])
    members = Group([c for p, c in cands if p.startswith("Member")])
    # words left of the candidate names belong to the village (including lines without candidates)
    name_x = (
        start
        if start is not None
        else min(
            (
                w.x0
                for row, mark in data
                for w in row
                if w.x0 > 0 and w not in village and w.x1 <= mark.x0 and not DATE.fullmatch(w.text)
            ),
            default=1e9,
        )
    )
    village += [
        w
        for w in body
        if w.x1 <= name_x - 3
        and not DATE.fullmatch(w.text)
        and w not in village
        and w.text not in POSITIONS | {"Chairpersons", "Members"}
    ]
    dates = [w.text for w in body if DATE.fullmatch(w.text)]
    totals: dict[str, list[tuple[float, int]]] = {}
    for w in body:
        if not is_number(w.text) or w.x0 < cols["votes"] + 30:
            continue
        # totals are the integers in the right-hand columns that are not a candidate's votes
        label = min(((abs(w.x - x), key) for key, x in cols.items() if key != "votes"), default=None)
        if label is None or label[0] > 25:
            continue
        totals.setdefault(label[1], []).append((w.y, number(w.text)))
    for key, found in sorted(totals.items()):
        found.sort()
        if not chair.cands or not members.cands:
            places = [members if members.cands else chair] * len(found)
        elif len(found) == 2:
            places = [chair, members]
        elif members_y:
            places = [members if y > min(members_y) - 5 else chair for y, _ in found]
        else:
            near = lambda group, y: min(abs(y - c.y) for c in group.cands)
            places = [chair if near(chair, y) <= near(members, y) else members for y, _ in found]
        for group, (_, value) in zip(places, found, strict=True):
            if all(c.tacit for c in group.cands):
                other = members if group is chair else chair
                if value and not all(c.tacit for c in other.cands):
                    group = other
                else:
                    continue
            if getattr(group, key) is not None:
                raise ValueError(f"{sheet} page {page}: two {key} values for one group {found}")
            setattr(group, key, value)
    name = " ".join(w.text for w in sorted(village, key=lambda w: (round(w.y / 4), w.x0)))
    return Village(district, name, dates[0] if dates else "", chair, members, page, sheet)


def read(path: str) -> list[Village]:
    carried: list[tuple[str, dict[str, float], list[Word], int]] = []
    for number_, words in enumerate(pages(path), 1):
        page_blocks = [b for b in blocks(list(words)) if any(w.text in POSITIONS for w in b[3])]
        if not page_blocks:
            page_blocks = [b for b in blocks(list(words), "Election") if any(w.text in POSITIONS for w in b[3])]
        if not page_blocks and carried:
            # a page with no header continues the table above it
            district, cols, body, first = carried[-1]
            carried[-1] = (
                district,
                cols,
                body
                + [replace(w, y0=w.y0 + 1000 * (number_ - first), y1=w.y1 + 1000 * (number_ - first)) for w in words],
                first,
            )
            continue
        if not page_blocks and any(w.text in POSITIONS for w in words):
            # a sheet printed without its header: the columns keep their usual distances from the votes
            body = list(words)
            cols = with_votes({}, body)
            cols |= {
                key: cols["votes"] + gap
                for key, gap in (("registered", 107), ("polled", 163), ("spoilt", 305), ("rejected", 344))
            }
            district = district_of(body, 0)
            carried.append((district, cols, body, number_))
            continue
        for top, bottom, end, body in page_blocks:
            if not any(w.text in POSITIONS for w in body):
                continue
            cols = with_votes(centers(list(words), top, bottom), body)
            district = district_of(list(words), top)
            carried.append((district, cols, body, number_))
    pieces: list[tuple[str, dict[str, float], list[Word], int]] = []
    for district, cols, body, first in carried:
        for starts, part in segments(body, cols):
            if not starts and pieces:
                # a header-less run of rows continues the village above it
                before = pieces[-1]
                shift = 1000 * (first - before[3])
                pieces[-1] = (
                    before[0],
                    before[1],
                    before[2] + [replace(w, y0=w.y0 + shift, y1=w.y1 + shift) for w in part],
                    before[3],
                )
            else:
                pieces.append((district, cols, part, first))
    return [resolve(district, cols, body, first, path) for district, cols, body, first in pieces]


def segments(body: list[Word], cols: dict[str, float]) -> list[tuple[bool, list[Word]]]:
    """Split a block of rows into one segment per village: a village starts on a position row that holds no votes
    and has the village's name to its left."""
    starts = []
    for row in lines(body):
        marks = [w for w in row if w.text in POSITIONS]
        if not marks:
            continue
        mark = marks[0]
        right = [w for w in row if w.x0 > mark.x1]
        votes = "votes" in cols and any(is_number(w.text) and abs(w.x - cols["votes"]) < 30 for w in right)
        if votes or any(w.text in ("Uncontested", "Elected", "Nominations") for w in right):
            continue
        if mark.text.startswith("Chair") or any(w.x1 <= mark.x0 and not DATE.fullmatch(w.text) for w in row):
            starts.append(row[0].y)
    if not starts:
        return [(False, body)]
    cuts = [s - 8 for s in starts]
    out: list[list[Word]] = [[] for _ in range(len(starts) + 1)]
    for w in body:
        out[sum(w.y >= c for c in cuts)].append(w)
    if not any(w.text in POSITIONS for w in out[0]):
        out[1] = out[0] + out[1]
        return [(True, part) for part in out[1:]]
    return [(False, out[0])] + [(True, part) for part in out[1:]]


def resolve(district: str, cols: dict[str, float], body: list[Word], page: int, sheet: str) -> Village:
    best: Village | None = None
    for mask in range(1 << 3):
        village = read_block(district, cols, body, page, sheet, mask)
        if consistent(village):
            return village
        best = best or village
    assert best is not None
    return best


def consistent(village: Village) -> bool:
    for group in (village.chair, village.members):
        if group.cands and not all(c.tacit for c in group.cands):
            sums = sum(c.votes or 0 for c in group.cands)
            if group.polled not in (
                sums,
                sums + (group.rejected or 0),
                sums + (group.rejected or 0) + (group.spoilt or 0),
            ):
                return False
    return True


DISTRICTS = ("Corozal", "Orange Walk", "Belize", "Cayo", "Stann Creek", "Toledo")


@dataclass
class Record:
    cycle: str
    url: str
    village: Village
    district: str
    inferred: bool
    copies: list[str]
    key: str = ""


def district_name(text: str) -> str:
    found = re.sub(r"\s*district\s*$", "", text.strip(), flags=re.IGNORECASE).strip().title()
    return found if found in DISTRICTS else ""


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def read_cycle(cycle: str, urls: list[str]) -> list[Record]:
    """Every village in a cycle's files, with each file's identical copies folded in."""
    out: list[Record] = []
    seen: dict[str, Record] = {}
    for url in urls:
        artifact = raw.cached(SOURCE, url)
        if artifact.sha256 in seen and len(pages(str(artifact.path))) == 1:
            seen[artifact.sha256].copies.append(url)
            continue
        for village in read(str(artifact.path)):
            record = Record(cycle, url, village, district_name(village.district), False, [])
            out.append(record)
            seen.setdefault(artifact.sha256, record)
    return out


def infer_districts(cycles: dict[str, list[Record]], known: dict[str, set[str]]) -> None:
    """Fill in the district of sheets that do not print one. A village's name usually settles it, since it is held in
    one district across the cycles; where a name belongs to several, the district is the one the Department's listing
    reaches next, because the listing runs district by district on each day."""
    for records in cycles.values():
        used: dict[str, set[str]] = {}
        for r in records:
            if r.district:
                used.setdefault(r.key, set()).add(r.district)
        previous: Record | None = None
        for r in records:
            if not r.district:
                name = r.key
                options = known.get(name, set()) - used.get(name, set()) or known.get(name, set())
                follows = previous is not None and previous.village.day == r.village.day
                floor = DISTRICTS.index(previous.district) if follows and previous else 0
                ahead = sorted((d for d in options if DISTRICTS.index(d) >= floor), key=DISTRICTS.index)
                pool = ahead or sorted(options, key=DISTRICTS.index)
                if pool:
                    r.district = pool[0]
                elif follows and previous:
                    r.district = previous.district
                else:
                    raise ValueError(f"no district for {r.village.name} in {r.url}")
                r.inferred = True
                used.setdefault(name, set()).add(r.district)
            previous = r


def sources() -> dict[str, list[str]]:
    out = {str(year): listing(page) for year, page in YEAR_PAGES.items()}
    out["by"] = list(BY_FILES)
    return out


ALIASES = {
    "burrelboom": "burrellboom",
    "bullettree": "bullettreefalls",
    "succotz": "sanjosesuccotz",
    "cristoreycayo": "cristorey",
    "cristoreycorozal": "cristorey",
    "duckrun1": "duckruni",
    "duckrun11": "duckrunii",
    "duckrun2": "duckrunii",
    "duckrun111": "duckruniii",
    "duckrunthree": "duckruniii",
    "georgville": "georgeville",
    "frankeddy": "franksddy",
    "nuevosanjuanvillage": "nuevosanjuan",
    "sanantoniovillage": "sanantonio",
    "palmar": "sanjosepalmar",
    "hummingbird": "hummingbirdcommunity",
    "newmullinsriver": "mullinsriver",
    "bladden": "bladen",
    "bladencc": "bladen",
    "conejo": "conejocreek",
    "elridgeville": "eldridgeville",
    "berumdianlanding": "bermudianlanding",
}
NAMES = {
    "franksddy": "Frank's Eddy",
    "duckruni": "Duck Run I",
    "duckrunii": "Duck Run II",
    "duckruniii": "Duck Run III",
    "sanjosepalmar": "San Jose Palmar",
    "bladen": "Bladen",
    "hopkins": "Hopkins",
}
BLANK_NAMES = {"15-MAY-VILLAGE-COUNCIL-RESULTS-HOPKINS.pdf": "Hopkins"}


def settle_names(cycles: dict[str, list[Record]]) -> None:
    """Give a village the same spelling in every cycle: sheets spell it with and without apostrophes and in capitals,
    and name it in different ways."""
    spellings: dict[str, Counter[str]] = {}
    for records in cycles.values():
        for r in records:
            if not r.village.name:
                r.village.name = BLANK_NAMES[r.url.rsplit("/", 1)[-1]]
            k = ALIASES.get(norm(r.village.name), norm(r.village.name))
            r.key = k
            if r.village.name != r.village.name.upper() or len(r.village.name) < 4:
                spellings.setdefault(k, Counter())[r.village.name] += 1
    for records in cycles.values():
        for r in records:
            r.village.name = NAMES.get(r.key) or (
                spellings[r.key].most_common(1)[0][0] if r.key in spellings else r.village.name.title()
            )


@functools.cache
def corpus() -> dict[str, list[Record]]:
    cycles = {cycle: read_cycle(cycle, urls) for cycle, urls in sources().items()}
    settle_names(cycles)
    known: dict[str, set[str]] = {}
    for records in cycles.values():
        for r in records:
            if r.district:
                known.setdefault(r.key, set()).add(r.district)
    infer_districts(cycles, known)
    return cycles


BELIZE = str(uuid5(package.NAMESPACE, "polity\x1fBZ"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
SCHEME = "bz.village"
SEATS = 6
CYCLES = ("2016", "2019", "2022", "2025")
NAME_FIXES = {
    (
        "2022",
        "Sarawee",
        "Barahona, Chavez, Karla Angie Vanessa Abigail",
    ): "Barahona, Chavez, Angie Karla Abigail Vanessa",
}


def ident(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = ident("source", "ebd-village")
SYSTEM_CHAIR = ident("electoral_system", "chair")
SYSTEM_MEMBERS = ident("electoral_system", "members")


def body_name(village: str) -> str:
    return f"{village} Council" if village.endswith("Community") else f"{village} Village Council"


def day_of(text: str, cycle: str) -> tuple[date, str | None]:
    """The day a village voted, and the printed text when the sheet's year is a typing slip."""
    day, month, year = (int(part) for part in text.split("/"))
    year += 2000 if year < 100 else 0
    if cycle != "by" and year != int(cycle):
        return date(int(cycle), month, day), text
    return date(year, month, day), None


@dataclass
class Held:
    cycle: str
    record: Record
    day: date
    printed: str
    fixed: str | None

    @property
    def village(self) -> Village:
        return self.record.village

    @property
    def tag(self) -> str:
        return f"{self.day}:{self.record.district}:{self.record.key}"


@functools.cache
def held() -> tuple[Held, ...]:
    out = []
    for cycle, records in corpus().items():
        for record in records:
            v = record.village
            if not v.chair.cands and not v.members.cands:
                continue  # no nominations were received, so no election was held
            for group in (v.chair, v.members):
                for cand in group.cands:
                    cand.name = NAME_FIXES.get((cycle, v.name, cand.name), cand.name)
                    if cand.tacit:
                        cand.elected = True
            day, fixed = day_of(v.day, cycle)
            out.append(Held(cycle, record, day, v.day, fixed))
    return tuple(out)


def unheld() -> list[Held]:
    out = []
    for cycle, records in corpus().items():
        for record in records:
            v = record.village
            if not v.chair.cands and not v.members.cands:
                out.append(Held(cycle, record, day_of(v.day, cycle)[0], v.day, None))
    return out


def villages() -> dict[tuple[str, str], tuple[str, ...]]:
    """Each village that voted, by district and key, with the names its sheets give it."""
    found: dict[tuple[str, str], str] = {}
    for h in held():
        found.setdefault((h.record.district, h.record.key), h.village.name)
    return {k: (v,) for k, v in sorted(found.items())}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in names())]


def names() -> list[str]:
    return [*CYCLES, *sorted({f"by-{h.day.year}" for h in held() if h.cycle == "by"})]


def parse() -> None:
    geo().write()
    registry().write()
    for name in names():
        build(name).write()


def emit() -> None:
    package.run("load", packages())


def body(district: str, key: str) -> str:
    return ident("body", district, key)


def office(district: str, key: str) -> str:
    return ident("office", district, key)


OSM_SOURCE = ident("source", "osm")
SETTLEMENTS = {"village", "hamlet", "town", "city", "suburb", "neighbourhood"}
NEAR = 100 / 111_000  # degrees; a residential area this close to a village's point is taken as the village's


def squash(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(village|community|council|the)\b", " ", text).replace("saint", "st").replace("centre", "center")
    return re.sub(r"[^a-z0-9]", "", text)


def area(district: str, key: str) -> str:
    return ident("area", district, key)


@functools.cache
def outlines() -> dict[tuple[str, str], shapely.Geometry]:
    """The OpenStreetMap outline of each village that has one, by district and key.

    A village's outline is the polygon mapped for its place when there is one, and otherwise the mapped residential
    area that holds, or lies within 100 meters of, the village's place point. A village with neither has no outline.
    """
    with zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, PLACES).path.read_bytes())) as z:
        collection = json.loads(z.read("populated_places.geojson"))
    places: dict[tuple[str, str], list[shapely.Geometry]] = {}
    residential = []
    for f in collection["features"]:
        props, shape = f["properties"], shapely.geometry.shape(f["geometry"])
        if props["name"] and props["place"] in SETTLEMENTS:
            places.setdefault((props["adm1_name"], squash(props["name"])), []).append(shape)
        elif props["landuse"] == "residential" and not props["place"] and shape.geom_type != "Point":
            residential.append(shape)
    tree = shapely.STRtree(residential)
    found: dict[tuple[str, str], shapely.Geometry] = {}
    for (district, key), (name,) in villages().items():
        shapes = places.get((district, squash(name)), [])
        polygons = [s for s in shapes if s.geom_type in ("Polygon", "MultiPolygon")]
        if polygons:
            shape = shapely.ops.unary_union(polygons)
        elif shapes:
            point = shapes[0]
            hit = tree.query(point, predicate="within")
            if not len(hit):
                near = tree.nearest(point)
                hit = [near] if near is not None and point.distance(residential[int(near)]) <= NEAR else []
            if not len(hit):
                continue
            shape = residential[int(hit[0])]
        else:
            continue
        shape = shapely.force_2d(shapely.make_valid(shape))
        if shape.geom_type == "GeometryCollection":
            shape = shapely.ops.unary_union([g for g in shape.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
        found[(district, key)] = shape
    return found


def first_day() -> date:
    return min(h.day for h in held())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Belize's villages and communities as the units of their council elections, with outlines from OpenStreetMap",
    )
    p.external.add(BELIZE)
    artifact = raw.cached(SOURCE, PLACES)
    p.add(
        "source",
        id=OSM_SOURCE,
        kind="community",
        publisher="OpenStreetMap contributors",
        title="OpenStreetMap populated places of Belize, as exported by the Humanitarian OpenStreetMap Team",
        url="https://data.humdata.org/dataset/hotosm_blz_populated_places",
        license="Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="© OpenStreetMap contributors",
        notes=(
            "Belize publishes no village boundaries. Each outline is the extent OpenStreetMap contributors have "
            "mapped for the settlement: the polygon of its place when it has one, and otherwise the residential area "
            "that holds the village's place point. It is the built-up area, not the council's legal boundary, and it "
            "is as retrieved, not as drawn in the year of each election. Villages with no mapped extent have no outline."
        ),
    )
    p.artifact(artifact, OSM_SOURCE)
    p.add("scheme", code=SCHEME, polity_id=BELIZE, kind="administrative", rank=3, noun="village")
    vintage = artifact.retrieved_at.date()
    start = first_day()
    p.add(
        "boundary_set",
        id=ident("boundary_set", SCHEME, vintage),
        scheme_code=SCHEME,
        vintage=f"OpenStreetMap {vintage}",
        valid_from=start,
        as_of=vintage,
    )
    for (district, key), shape in outlines().items():
        id = area(district, key)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=f"{changeset.slug(district)}/{key}")
        p.name(id, villages()[(district, key)][0], "en", "official")
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=start,
            geom=shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=OSM_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The village and community councils of Belize, their chairperson offices, and their electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BELIZE)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Elections and Boundaries Commission of Belize",
        title="Village council election results published by the Elections and Boundaries Department",
        url=f"{SITE}/village-council-elections/",
        license="No license stated; published by the electoral authority for public use",
        attribution="Elections and Boundaries Commission of Belize",
    )
    cited = set()
    for url in [*(u for urls in sources().values() for u in urls), *YEAR_PAGES.values(), BY_PAGE]:
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in cited:  # the Department posts some sheets twice under different names
            cited.add(artifact.storage_key)
            p.artifact(artifact, SOURCE_ID)
    for id, rules, text_ in (
        (SYSTEM_CHAIR, {"formula": "fptp"}, "One chairperson, elected by the most votes."),
        (
            SYSTEM_MEMBERS,
            {"formula": "block"},
            (
                "A council's members are elected at large, each voter casting as many votes as there are seats, and "
                "the candidates with the most votes win the seats."
            ),
        ),
    ):
        p.add("electoral_system", id=id, polity_id=BELIZE, rules=rules, rules_version=1)
        p.add("note", id=ident("note", id), entity_id=id, kind="methodology", body_md=text_, lang="en")
    for (district, key), (village,) in villages().items():
        label = body_name(village)
        b = body(district, key)
        p.add("body", id=b, polity_id=BELIZE, kind="council")
        p.name(b, label, "en", "official")
        p.add(
            "external_id",
            entity_id=b,
            scheme="bz.village",
            value=f"{changeset.slug(district)}/{changeset.slug(village)}",
        )
        o = office(district, key)
        p.add("office", id=o, polity_id=BELIZE, body_id=b, kind="other")
        p.name(o, f"Chairperson of {label}", "en", "official")
    return p


METHOD = (
    "Counted for the village's council election and published by the Elections and Boundaries Department on one sheet "
    "for the village. Candidates' votes are stored as printed, and so are the rejected votes and, where the sheet "
    "prints them, the registered electors."
)


def build(name: str) -> Package:
    cycle = name if name in CYCLES else "by"
    group = [h for h in held() if h.cycle == cycle and (cycle != "by" or f"by-{h.day.year}" == name)]
    what = f"{name} village council elections" if cycle != "by" else f"{name[3:]} village council by-elections"
    p = Package(
        f"{SOURCE}/{name}", SOURCE, f"The Belize {what} by village", depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"]
    )
    p.external.add(BELIZE)
    for h in group:
        for url in [h.record.url, *h.record.copies]:
            p.uses(raw.cached(SOURCE, url))
    parent = None
    if cycle != "by":
        parent = ident("election", name)
        path = f"bz/{name}-village-council-elections"
        days = [h.day for h in group]
        p.add(
            "election",
            id=parent,
            polity_id=BELIZE,
            kind="local",
            date_from=min(days),
            date_to=max(days),
            date_precision="day",
            status="held",
        )
        p.add("slug", path=path, entity_id=parent, is_current=True)
        p.name(parent, f"{name} Belize village council elections", "en", "official")
        p.add(
            "note",
            id=ident("note", parent),
            entity_id=parent,
            kind="context",
            body_md=(
                "Village and community councils elect a chairperson and six members. The Elections and Boundaries "
                "Department holds the elections on several Sundays, district by district, and each village's "
                "election is its own election under this one."
            ),
            lang="en",
        )
    else:
        path = f"bz/{name[3:]}-village-council-by-elections"
    seen: Counter[tuple[str, str]] = Counter()
    for h in group:
        seen[(h.record.district, h.record.key)] += 1
        _election(p, h, parent, path, seen[(h.record.district, h.record.key)] > 1)
    singles = [
        f"{h.village.name} ({h.day})"
        for h in group
        if sum(x.elected for x in h.village.chair.cands) > 1 and not all(x.tacit for x in h.village.chair.cands)
    ]
    if singles:
        p.accept(
            "outcomes.single_winner",
            f"the sheet for {', '.join(singles)} marks two chairperson candidates as elected for the one seat; "
            "the outcomes are stored as declared",
        )
    return p


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def _election(p: Package, h: Held, parent: str | None, path: str, repeat: bool) -> None:
    v = h.village
    label = body_name(v.name)
    election = ident("election", h.tag)
    by = h.cycle == "by"
    p.add(
        "election",
        id=election,
        polity_id=BELIZE,
        parent_id=parent,
        kind="by_election" if by else "local",
        date_from=h.day,
        date_to=h.day,
        date_precision="day",
        original_text=h.printed,
        status="held",
    )
    slug = f"{path}/{changeset.slug(h.record.district)}/{changeset.slug(v.name)}" + (f"-{h.day}" if repeat else "")
    p.add("slug", path=slug, entity_id=election, is_current=True)
    p.name(election, f"{h.day.year} {label} {'by-election' if by else 'election'}", "en", "official")
    caveats = []
    if h.fixed:
        caveats.append(
            f"The sheet prints the election date as {h.fixed}; the other sheets of the day put it in {h.day.year}, "
            f"so the election is dated {h.day}."
        )
    if h.record.inferred:
        caveats.append(
            "The sheet prints no district. The district is the one the village belongs to in the Department's other "
            "sheets, and where a name belongs to several districts, the one the Department's listing reaches at that "
            "point."
        )
    if h.record.copies:
        caveats.append("The Department posts this sheet under more than one file name; the copies are identical.")
    for text_ in caveats:
        p.add("note", id=ident("note", election, text_), entity_id=election, kind="caveat", body_md=text_, lang="en")
    unit = ident("unit", h.tag)
    located = (h.record.district, h.record.key) in outlines()
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=BELIZE,
        scheme_code=SCHEME,
        code=f"{h.day}:{changeset.slug(h.record.district)}:{changeset.slug(v.name)}",
        area_relation="equals" if located else "unlocated",
        area_id=area(h.record.district, h.record.key) if located else None,
        kind="standard",
    )
    registered = v.chair.registered if v.chair.registered is not None else v.members.registered
    for which, group in (("chair", v.chair), ("members", v.members)):
        if group.cands:
            _contest(p, h, election, slug, which, group, unit, registered)


def _contest(
    p: Package, h: Held, election: str, path: str, which: str, group: Group, unit: str, registered: int | None
) -> None:
    v = h.village
    label = body_name(v.name)
    tacit = all(c.tacit for c in group.cands)
    tag = f"{h.tag}:{which}"
    contest = ident("contest", tag)
    chair = which == "chair"
    seats = 1 if chair else (SEATS if h.cycle != "by" else len(group.cands))
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office" if chair else "body_seats",
        office_id=office(h.record.district, h.record.key) if chair else None,
        body_id=None if chair else body(h.record.district, h.record.key),
        magnitude=seats,
        round_number=1,
        electoral_system_id=SYSTEM_CHAIR if chair else SYSTEM_MEMBERS,
        default_result_kind="tacit" if tacit else "counted",
    )
    p.name(contest, f"{h.day.year} {label}, {'chairperson' if chair else 'members'}", "en", "official")
    p.add("slug", path=f"{path}/{which}", entity_id=contest, is_current=True)
    result_set = ident("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if tacit else "counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=None if tacit else 1.0,
    )
    method = METHOD
    totals: list[tuple[int, int | None]] = []
    if tacit:
        method = (
            "Only as many candidates were nominated as there were seats to fill, so the Elections and Boundaries "
            "Department declared them elected without a vote."
        )
    else:
        total = sum(c.votes or 0 for c in group.cands)
        rejected = group.rejected or 0
        spoilt = group.spoilt or 0
        printed = group.polled
        if printed == total:
            totals.append((VALID, printed))
        elif printed in (total + rejected, total + rejected + spoilt) and chair:
            totals.append((CAST, printed))
            method += f" The sheet's {printed} total votes polled include the {rejected} rejected votes."
        elif printed in (total + rejected, total + rejected + spoilt):
            method += (
                f" The sheet's {printed} total votes polled are the candidates' votes plus the {rejected} rejected "
                "votes, so they are not stored separately."
            )
        elif printed is not None:
            method += (
                f" The sheet prints {printed} total votes polled, but the candidates' votes add up to {total}"
                f"{f' and it prints {rejected} rejected votes' if rejected else ''}; the printed total is not stored, "
                "and the candidates' votes are stored as printed."
            )
        if group.rejected is not None:
            totals.append((INVALID, group.rejected))
        if group.spoilt:
            method += f" The sheet prints {group.spoilt} spoilt votes."
    p.add("note", id=ident("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    for position, c in enumerate(group.cands):
        option = ident("option", tag, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position + 1,
            ballot_label=c.name,
        )
        person = ident("person", tag, position)
        p.add("person", id=person)
        p.name(person, c.name, "en", "official")
        p.add(
            "candidacy",
            id=ident("candidacy", tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            is_independent=True,
        )
        if c.elected:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        if not tacit:
            _put(p, result_set, unit, option, VOTES, c.votes)
    if not tacit:
        _put(p, result_set, unit, None, REGISTERED, registered)
        for measure, value in totals:
            _put(p, result_set, unit, None, measure, value)
