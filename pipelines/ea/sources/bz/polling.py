"""Belize: the 2009 city and town council elections by polling area.

The Department's combined 2009 workbook prints, for eight councils, each candidate's votes in every voters'-list box
(a letter range such as A-B), each polling area's total, and the council's final total. Orange Walk and San Ignacio
continue on a later page, where the same candidates' rows appear again without names, in the same order. The text layer
is read with `pdftotext -layout`; each row's numbers are split by the council's column layout, and every polling area's
boxes are checked against its printed total and every council's areas against its printed final total and against the
council results already published for the same day.
"""

import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date

from ea import raw
from ea.package import Package
from ea.sources.bz import municipal as m

DAY = date(2009, 3, 4)
SHEET = f"{m.BASE}/2023/12/2009-Municipal-Election-Results.pdf"
KEY = f"{m.SOURCE}/{DAY}-polling-areas"

# Each council's number columns, in print order: (polling area code, boxes before its total). A council's last printed
# number is its final total, unless it has a single polling area, whose total is the council's.
Layout = tuple[tuple[str, int], ...]
LAYOUTS: dict[str, Layout] = {
    "belmopan": (("86", 14),),
    "san-pedro": (("37", 9),),
    "corozal": (("39", 6), ("40", 4), ("41", 2)),
    "orange-walk": (("53", 5), ("54", 5), ("55", 1), ("60", 8)),
    "san-ignacio-santa-elena": (("72", 4), ("73", 8), ("76", 7)),
    "benque-viejo": (("83", 5), ("84", 2)),
    "dangriga": (("92", 2), ("94", 2), ("95", 4), ("96", 2)),
    "punta-gorda": (("109/111/112A#1", 4), ("109/111/112A#2", 2)),
}
TITLES = (
    ("BELMOPAN", "belmopan"),
    ("SAN PEDRO", "san-pedro"),
    ("COROZAL", "corozal"),
    ("ORANGE WALK", "orange-walk"),
    ("SAN IGNACIO", "san-ignacio-santa-elena"),
    ("BENQUE VIEJO", "benque-viejo"),
    ("DANGRIGA", "dangriga"),
    ("PUNTA GORDA", "punta-gorda"),
)
BLOCKS = {
    "U.D.P.": "UDP",
    "P.U.P.": "PUP",
    "V.I.P.": "VIP",
    "P.N.P.": "PNP",
    "BENQUE ASSOCIATION": "BENQUE ASSOCIATION",
    "CONGRESS OF THE P.G. PEOPLE": "CONGRESS OF THE PG PEOPLE",
    "INDEPENDENT": m.INDEPENDENT,
}
SIGNS = {"UDP": "UDP", "PUP": "PUP", "IND": m.INDEPENDENT}
ROW = re.compile(
    r"^(?P<name>\S.*?)(?:\s+|(?<=\"))(?P<office>MAYORAL|MAYOR|COUNCILLOR)(?:\s+(?P<sign>UDP|PUP|IND))?"
    r"(?P<nums>(?:\s+\d+)*)\s*$",
    re.IGNORECASE,
)
TOTAL = re.compile(r"^\s*TOTAL (?:NO\. OF VOTES|UDP|PUP)\s+(?P<nums>\d[\d\s]*)$", re.IGNORECASE)
NUMBERS = re.compile(r"^\s*(?P<nums>\d+(?:\s+\d+)*)\s*$")


@dataclass
class Row:
    name: str | None  # None for a printed total
    office: str | None
    party: str | None
    numbers: list[int] = field(default_factory=list)


@dataclass
class Area:
    code: str
    boxes: int
    votes: dict[int, int] = field(default_factory=dict)  # candidate index -> votes


@dataclass
class Town:
    key: str
    cands: list[tuple[str, str, str | None]] = field(default_factory=list)  # name, office, party
    areas: list[Area] = field(default_factory=list)
    final: dict[int, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def fetch() -> None:
    raw.fetch(m.SOURCE, SHEET, suffix=".pdf")


@functools.cache
def text() -> str:
    path = raw.cached(m.SOURCE, SHEET).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def rows() -> dict[str, list[Row]]:
    """Every council's candidate and total rows in print order, with the unnamed continuation rows attached by order."""
    found: dict[str, list[Row]] = {}
    carried: dict[str, list[list[int]]] = {}
    current: str | None = None
    party: str | None = None
    for line in text().splitlines():
        upper = line.strip().upper()
        if "COUNCIL ELECTION" in upper and "FOLLOWING" not in upper:
            current = next(key for word, key in TITLES if word in upper)
            found[current], carried[current], party = [], [], None
            continue
        if current is None or not line.strip():
            continue
        if upper in BLOCKS:
            party = BLOCKS[upper]
            continue
        total = TOTAL.match(line)
        if total:
            found[current].append(Row(None, None, None, [int(x) for x in total["nums"].split()]))
            continue
        hit = ROW.match(line)
        if hit:
            name = re.sub(r"\s+", " ", hit["name"].replace("“", '"').replace("”", '"')).strip()
            numbers = [int(x) for x in hit["nums"].split()]
            if not name or name.upper() in ("MAYOR", "COUNCILLOR") or not numbers:
                continue
            sign = SIGNS.get((hit["sign"] or "").upper())
            office = "mayor" if hit["office"].upper().startswith("MAYOR") else "council"
            found[current].append(Row(name, office, sign or party, numbers))
            continue
        plain = NUMBERS.match(line)
        if plain:
            carried[current].append([int(x) for x in plain["nums"].split()])
    for key, extra in carried.items():
        if extra and len(extra) != len(found[key]):
            raise ValueError(f"{key}: {len(extra)} continuation rows for {len(found[key])} rows")
        for row, more in zip(found[key], extra, strict=False):
            row.numbers = row.numbers + more
    return found


def squeeze(name: str) -> str:
    return re.sub(r"[^A-Z]", "", name.upper())


# The sheet's Punta Gorda P.N.P. rows for Petillo and Ortega carry each other's figures: the council results already
# published for the day, the sheet's own order (every other block is alphabetical), and the party total all agree.
SWAPS = {"punta-gorda": ("PETILLO, Maria", "ORTEGA, Bejerano Hector")}


def towns() -> dict[str, Town]:
    out: dict[str, Town] = {}
    for key, found in rows().items():
        layout = LAYOUTS[key]
        width = sum(boxes + 1 for _, boxes in layout) + (1 if len(layout) > 1 else 0)
        town = Town(key)
        town.areas = [Area(code, boxes) for code, boxes in layout]
        block = [0] * width
        for row in found:
            if len(row.numbers) != width:
                raise ValueError(f"{key}: {row.name or 'total'} has {len(row.numbers)} numbers, expected {width}")
            if row.name is None:
                if block != row.numbers:
                    town.notes.append(f"a printed party total {row.numbers} differs from its rows' sum {block}")
                block = [0] * width
                continue
            block = [x + y for x, y in zip(block, row.numbers, strict=True)]
            name = row.name
            if key in SWAPS and name in SWAPS[key]:
                one, two = SWAPS[key]
                name = two if name == one else one
            index = len(town.cands)
            town.cands.append((name, row.office or "", row.party))
            at = 0
            for area in town.areas:
                boxes, printed = row.numbers[at : at + area.boxes], row.numbers[at + area.boxes]
                if sum(boxes) != printed:
                    town.notes.append(f"{name}: boxes of area {area.code} add to {sum(boxes)}, printed {printed}")
                area.votes[index] = printed
                at += area.boxes + 1
            printed = row.numbers[at] if len(layout) > 1 else row.numbers[at - 1]
            if sum(a.votes[index] for a in town.areas) != printed:
                town.notes.append(f"{name}: areas add to {sum(a.votes[index] for a in town.areas)}, printed {printed}")
            town.final[index] = printed
        out[key] = town
    return out


def match(key: str, town: Town) -> dict[int, int]:
    """Each candidate on this sheet's index in the published council result's candidates, by office and votes, then party and name."""
    sheet = m.election(DAY)[key]
    pool = list(enumerate(sheet.cands))
    found: dict[int, int] = {}
    for index, (name, office, party) in enumerate(town.cands):
        hits = [i for i, c in pool if c.office == office and c.votes == town.final[index]]
        if len(hits) > 1:
            hits = [i for i in hits if sheet.cands[i].party == party] or hits
        if len(hits) > 1:
            hits = [i for i in hits if squeeze(sheet.cands[i].name)[:4] == squeeze(name)[:4]]
        if len(hits) != 1:
            raise ValueError(f"{key}: {name!r} ({office}, {party}, {town.final[index]}) matches {len(hits)} candidates")
        found[index] = hits[0]
        pool = [(i, c) for i, c in pool if i != hits[0]]
    if pool:
        raise ValueError(f"{key}: published candidates not on the sheet: {[c.name for _, c in pool]}")
    return found


def debug() -> None:
    for key, town in towns().items():
        print(key, len(town.cands), "candidates", [a.code for a in town.areas])
        for note in town.notes:
            print("   NOTE", note)
        print("   matched", len(match(key, town)))


def packages() -> list[str]:
    return [KEY]


METHOD = (
    "Counted for each council and published by the Elections and Boundaries Department in its combined 2009 results "
    "sheet, which prints every candidate's votes in each box of a polling area's voters' list (a letter range such as "
    "A-B), each polling area's total, and the council's final total. The votes stored here are each polling area's "
    "printed total; the letter-range boxes are not stored. The council's registered electors, ballots cast, and "
    "rejected ballots are the ones the Department's updated council sheet prints, stored at the council's own unit, "
    "and the council's candidate totals, declared below, are that sheet's. This set refines the council-level set for "
    "the same contest. Every voter chooses a mayor and up to {seats} councillors, so ballots cast are not the sum of "
    "the votes."
)
FIX_NOTE = (
    " The combined sheet prints the Punta Gorda P.N.P. rows for Petillo and Ortega with each other's figures, so "
    "each row's figures are stored for the candidate whose total the updated council sheet gives."
)
MAYOR_NOTE = (
    ' The combined sheet names the San Ignacio and Santa Elena independent mayoral candidate "Fernando Cruz"; his '
    'figures are stored for the candidate the updated council sheet prints as Alfonso "Poncho" Cruz, whose total they '
    "equal."
)


def unit_of(key: str, code: str) -> str:
    return m.key("unit", DAY, key, code)


def build() -> Package:
    p = Package(
        KEY,
        m.SOURCE,
        "The 2009 Belize city and town council elections of eight councils, by polling area",
        depends=[f"{m.SOURCE}/geo", f"{m.SOURCE}/registry", f"{m.SOURCE}/{DAY}"],
    )
    p.external.add(m.BELIZE)
    artifact = raw.cached(m.SOURCE, SHEET)
    p.artifact(artifact, m.EBC_SOURCE)
    eid = m.key("election", DAY)
    published = m.election(DAY)
    for key, town in towns().items():
        info = m.BY_KEY[key]
        sheet = published[key]
        council = m.key("unit", DAY, key)
        found = match(key, town)
        for area in town.areas:
            unit = unit_of(key, area.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=m.BELIZE,
                scheme_code=m.SCHEME,
                code=f"{DAY}:{key}:{area.code}",
                parent_id=council,
                area_relation="within",
                area_id=m.area(key),
                kind="standard",
            )
            part = area.code.partition("#")
            label = f"Polling area {part[0]}" + (f", table {part[2]}" if part[2] else "")
            p.name(unit, f"{info.name}, {label.lower()}", "en", "official")
        for office_kind in ("mayor", "council"):
            tag = f"{DAY}:{key}:{office_kind}"
            old = m.key("result_set", tag)
            rs = p.id("result_set", tag, "polling-area")
            p.add(
                "result_set",
                id=rs,
                election_id=eid,
                contest_id=m.key("contest", tag),
                stage="official",
                kind="counted",
                source_id=m.EBC_SOURCE,
                status="active",
                is_default=True,
                supersedes_id=old,
                completeness=1.0,
            )
            method = METHOD.format(seats=info.seats)
            if key in SWAPS:
                method += FIX_NOTE
            if key == "san-ignacio-santa-elena" and office_kind == "mayor":
                method += MAYOR_NOTE
            p.add("note", id=p.id("note", rs), entity_id=rs, kind="methodology", body_md=method, lang="en")
            ours = [i for i, c in enumerate(town.cands) if c[1] == office_kind]
            mine = {found[i]: i for i in ours}
            options = [
                (position, c) for position, c in enumerate([c for c in sheet.cands if c.office == office_kind], 1)
            ]
            for position, c in options:
                option = m.key("option", tag, position)
                index = mine[sheet.cands.index(c)]
                for area in town.areas:
                    m._put(p, rs, unit_of(key, area.code), option, m.VOTES, area.votes[index])
                p.add("declared", result_set_id=rs, option_id=option, measure=m.VOTES, value=c.votes)
                if c.elected:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
            for measure, stat in ((m.REGISTERED, "registered"), (m.CAST, "cast"), (m.INVALID, "rejected")):
                m._put(p, rs, council, None, measure, sheet.stats.get(stat))
    return p
