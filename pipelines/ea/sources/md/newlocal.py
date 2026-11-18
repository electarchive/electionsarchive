"""Moldova's new local elections, held twice a year to fill a mayor's or council's vacancy, from the Central
Electoral Commission's reports on each round. A report gives each locality's turnout and the candidate or party
seats the commission declared, and no votes by candidate, so the packages hold turnout and declared winners."""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import pymupdf

from ea import raw
from ea.package import Package
from ea.sources.md import local

SOURCE = "md.cec"
MOLDOVA = local.MOLDOVA
COUNTRY = local.COUNTRY
GEO = "md.cec/new-local-geo"
REGISTRY = "md.cec/new-local-registry"
BASE = "https://a.cec.md/storage/ckfinder/files"
REPORTS = {
    "2021-11-21": f"{BASE}/ALN%2021%20nov%202021/C2_RAPORT_ALN_21_11_2021.pdf",
    "2022-05-29": f"{BASE}/ALN%2029.05.2022/C2_RAPORT_ALN-29_05_2022.pdf",
    "2022-10-16": f"{BASE}/ALN%2016%20oct%202022/C3_RAPORT_ALN%20din%2016_10_2022.pdf",
    "2024-05-19": f"{BASE}/ALN%202024%20-%202027/ALN%2019.05.2024/Raport_ALN_19%20mai%202024.pdf",
    "2024-11-17": f"{BASE}/ALN_17.11.2024/C2_Raport_ALN_noiembrie_2024%20(1).pdf",
    "2025-05-18": f"{BASE}/C2_Raport_ALN_18%20mai%202025.pdf",
    "2025-11-16": f"{BASE}/ALN_16.11.2025/C2_Raport_ALN_16%20noiembrie%202025.pdf",
    "2026-05-17": f"{BASE}/ALN%202024%20-%202027/ALN%2017.05.2026/C2_Raport_ALN_17%20mai%202026.pdf",
}
DAYS = tuple(REPORTS)
MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "ianuarie",
            "februarie",
            "martie",
            "aprilie",
            "mai",
            "iunie",
            "iulie",
            "august",
            "septembrie",
            "octombrie",
            "noiembrie",
            "decembrie",
        ],
        1,
    )
}
REGISTERED, VOTERS, VALID, INVALID, SEATS = 2, 14, 4, 5, 6
KINDS = {
    "satul": "sat",
    "s.": "sat",
    "comuna": "comuna",
    "com.": "comuna",
    "orașul": "oras",
    "or.": "oras",
    "municipiul": "municipiu",
    "mun.": "municipiu",
}
LABELS = {"oras": "town", "sat": "village", "comuna": "commune", "municipiu": "municipality"}
MARK = re.compile(r"^(satul|comuna|orașul|municipiul|s\.|com\.|or\.|mun\.)\s*(?=\S)", re.IGNORECASE)
PARTY = re.compile(
    r"(Partidul|Partid|Blocul|Mișcarea|Platforma|Uniunea|Alianța|Liga|Candidat independent|candidat independent|"
    r"Candidatul independent)",
)
FOLDED = str.maketrans("âÂ", "îÎ")


def clean(text: object) -> str:
    return local.clean(text)


def fold(text: object) -> str:
    return local.fold(str(text).translate(FOLDED))


def number(text: str | None) -> int | None:
    digits = re.sub(r"\s+", "", text or "")
    return int(digits) if digits.isdigit() else None


def cell_text(page: pymupdf.Page, box: tuple[float, float, float, float]) -> str:
    area = pymupdf.Rect(box)
    words = [w for w in page.get_text("words", clip=area + (-1, -1, 1, 1)) if area.intersects(pymupdf.Rect(w[:4]))]
    words.sort(key=lambda w: (round(w[1] / 3), w[0]))
    return clean(" ".join(w[4] for w in words))


def blocks(path: Path) -> list[tuple[int, str, object]]:
    """The reading order of a report: its text blocks and its tables, each table as rows of cell text."""
    found: list[tuple[int, str, object]] = []
    with pymupdf.open(path) as document:
        started = False
        for number_, page in enumerate(document, 1):
            text = page.get_text()
            if not started:
                started = "VOTAREA. NUMĂRAREA" in text
                if not started:
                    continue
            tables = page.find_tables().tables
            boxes = [pymupdf.Rect(t.bbox) for t in tables]
            items: list[tuple[float, str, object]] = []
            for block in page.get_text("blocks"):
                area = pymupdf.Rect(block[:4])
                if any((area & box).get_area() > 0.5 * area.get_area() for box in boxes if area.intersects(box)):
                    continue
                if clean(block[4]):
                    items.append((block[1], "text", clean(block[4])))
            for table in tables:
                rows = [[cell_text(page, c) if c else "" for c in row.cells] for row in table.rows]
                items.append((table.bbox[1], "table", rows))
            items.sort(key=lambda item: item[0])
            found.extend((number_, kind, value) for _, kind, value in items)
            if "CONTENCIOSUL ELECTORAL" in text:
                break
    return found


@dataclass
class Place:
    kind: str
    name: str
    district: str  # key of the district-level area

    @property
    def key(self) -> str:
        base = fold(self.name)
        return f"{base} {self.kind}" if (self.district, base) in local.AMBIGUOUS else base

    @property
    def label(self) -> str:
        text = local.title(self.name)
        return f"{text} ({LABELS[self.kind]})" if (self.district, fold(self.name)) in local.AMBIGUOUS else text


def place(text: str) -> Place:
    """A locality from the way a report prints it: `satul Opaci, raionul Căușeni` or `Municipiul Orhei`."""
    text = re.sub(r"\s*\(inclusiv[^)]*\)?", "", clean(text).replace(" ,", ","))
    mark = MARK.match(text)
    if mark is None:
        raise ValueError(f"no locality kind in {text!r}")
    kind = KINDS[mark.group(1).lower()]
    rest = text[mark.end() :]
    where = re.search(r",?\s*(?:raionul|r-l|r-nul)\s+(.+)$", rest, re.IGNORECASE)
    if where:
        district = fold(where.group(1))
        name = rest[: where.start()]
    elif re.search(r",?\s*UTA\s+G[ăa]g[ăa]uzia$", rest, re.IGNORECASE):
        district = "gagauzia"
        name = re.sub(r",?\s*UTA\s+G[ăa]g[ăa]uzia$", "", rest, flags=re.IGNORECASE)
    else:
        name = rest
        district = fold(name)
    name = name.strip(" ,").translate(FOLDED)
    if kind == "municipiu" and district in ("balti", "chisinau"):
        return Place(kind, name, district)
    if district not in local.districts():
        raise ValueError(f"unknown district {district!r} in {text!r}")
    return Place(kind, name, district)


@dataclass
class Turnout:
    kind: str  # council, mayor, or unknown where the report does not name the contest
    day: date
    stage: int
    repeat: bool
    place: Place
    registered: int
    voters: int
    valid: int
    invalid: int


@dataclass
class Seats:
    day: date
    place: Place
    party: str | None
    candidate: str | None
    seats: int
    votes: int | None = None


@dataclass
class Winner:
    place: Place
    name: str
    party: str | None
    stage: int
    repeat: bool
    independent: bool | None


@dataclass
class Report:
    day: date
    turnout: list[Turnout] = field(default_factory=list)
    seats: list[Seats] = field(default_factory=list)
    winners: list[Winner] = field(default_factory=list)
    totals: list[tuple[str, int, int, int, int, int, int]] = field(default_factory=list)
    sizes: dict[tuple[str, str], int] = field(default_factory=dict)


def stamp(text: str) -> date | None:
    found = re.search(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", text.lower())
    return date(int(found.group(3)), MONTHS[found.group(2)], int(found.group(1))) if found else None


def fetch() -> None:
    for url in REPORTS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


@dataclass
class State:
    """Where a report's reading has got to: which contest and round the tables under a heading count."""

    kind: str = "unknown"
    stage: int = 1
    repeat: bool = False
    day: date | None = None
    mode: str | None = None
    pending: list[str] = field(default_factory=list)
    winner: list[str] | None = None
    orphan: list[str] | None = None
    orphan_place: str = ""
    seat_place: Place | None = None
    seat_rows: list[Seats] = field(default_factory=list)
    council: tuple[Place, int] | None = None


def heading(text: str, state: State) -> None:
    """Update the reading state from a heading: which contest the following tables count, and which round."""
    low = clean(text).lower()
    if len(low) > 160:
        return
    if re.search(r"consilieri locali|consiliul local", low):
        state.kind, state.stage, state.repeat, state.day = "council", 1, False, None
    if re.search(r"\bprimari\b", low):
        state.kind, state.stage, state.repeat, state.day = "mayor", 1, False, None
    if "referendum" in low:
        state.mode = None
    if "votarea repetat" in low:
        state.kind, state.repeat, state.stage = "mayor", True, 1
    stage = re.search(r"turul\s+(ii|i)\b", low)
    if stage:
        state.stage = len(stage.group(1))
    when = stamp(low)
    if when and (stage or "repetat" in low):
        state.day = when


def read(day: str) -> Report:
    report = Report(date.fromisoformat(day))
    state = State()
    path = raw.cached(SOURCE, REPORTS[day]).path
    for _, kind, value in blocks(path):
        if kind == "text":
            text = str(value)
            heading(text, state)
            if re.search(r"CONTENCIOS|Contestații examinate", text):
                state.mode = None
            continue
        rows = value
        assert isinstance(rows, list)
        head = " ".join(" ".join(r) for r in rows[:7]).lower()
        if "referendum" in head:
            state.mode = None
        elif "denumirea localit" in head and "alegători" in head:
            state.mode = "turnout"
        elif "de mandate" in head and "de voturi" in head:
            state.mode, state.repeat = "votes", False
        elif "mandate" in head and "concurentul" in head:
            state.mode, state.repeat = "seats", False
        elif "câștigător" in head or "ales" in head and "hotărârea" in head:
            state.mode, state.repeat = "winners", False
        elif "denumirea localit" in head or "tipul datelor" in head:
            state.mode = None
        elif state.mode is None:
            continue
        take(report, state, rows)
    winner_row(report, state, None)
    for found in report.turnout:
        if not found.place.kind:
            raise ValueError(f"an unfinished locality {found.place.name!r}")
    return report


def take(report: Report, state: State, rows: list[list[str]]) -> None:
    mode = state.mode
    pending = state.pending
    for row in rows:
        cells = [c for c in row if c]
        if not cells:
            continue
        cells[0] = re.sub(r"^(?:\S\s+)+(?=\S{2})", "", cells[0])
        if len(cells) == 1:
            heading(cells[0], state)
            if mode == "votes":
                vote_row(report, state, cells)
                continue
            if mode == "winners" and state.orphan is not None:
                state.orphan_place = clean(f"{state.orphan_place} {cells[0]}")
                if re.search(r"(raionul|r-l)\s+\S|UTA", state.orphan_place):
                    winner_row(report, state, [state.orphan_place, *state.orphan])
                    state.orphan, state.orphan_place = None, ""
                continue
            if mode == "turnout" and pending and re.search(r"(,|raionul|r-l)$", pending[0], re.IGNORECASE):
                pending[0] = clean(f"{pending[0]} {cells[0]}")
                fix_last(report, pending[0])
            continue
        if mode == "turnout":
            figures = [number(c) for c in cells[1:]]
            if re.search(r"\btotal$", cells[0], re.IGNORECASE):
                nums = [f for f in figures if f is not None]
                if len(nums) > 5:
                    nums = [n for i, n in enumerate(nums) if i == 0 or n != nums[i - 1]]
                for at in range(len(nums) - 4):
                    a, b, c, d, e = nums[at : at + 5]
                    if b == c + d:
                        report.totals.append((state.kind, state.stage, a, b, c, d, e))
                        break
                else:
                    if len(nums) == 5:
                        report.totals.append((state.kind, state.stage, nums[0], nums[1], nums[2], nums[3], nums[4]))
                continue
            if sum(f is not None for f in figures) < 5 or not MARK.match(cells[0]):
                continue
            a, b, c, d, _ = [f for f in figures if f is not None][:5]
            pending[:] = [cells[0]]
            report.turnout.append(
                Turnout(
                    state.kind,
                    when(report, state),
                    state.stage,
                    state.repeat,
                    place(cells[0])
                    if not re.search(r"(,|raionul|r-l)$", cells[0], re.IGNORECASE)
                    else Place("", cells[0], ""),
                    a,
                    b,
                    c,
                    d,
                )
            )
        elif mode == "votes":
            vote_row(report, state, cells)
        elif mode == "seats":
            seat_row(report, state, cells)
        elif mode == "winners":
            winner_row(report, state, cells)


def when(report: Report, state: State) -> date:
    if state.day is not None:
        return state.day
    return report.day + timedelta(days=14 * (state.stage - 1))


def fix_last(report: Report, text: str) -> None:
    last = report.turnout[-1]
    last.place = place(text)


JUNK = re.compile(r"^(?:[a-z]{1,2}\s+)+")


def label(text: str) -> str:
    text = JUNK.sub("", clean(text))
    found = PARTY.search(text)
    return text[found.start() :] if found and found.start() < 8 else text


def seat_row(report: Report, state: State, cells: list[str]) -> None:
    """Take a row of the table of seats. The rows before a locality's own row can sit at the foot of the page before."""
    if re.search(r"\btotal$", cells[0], re.IGNORECASE):
        total = [number(re.sub(r"\s*mandat\w*", "", c)) for c in cells[1:]]
        size = [t for t in total if t is not None][-1]
        report.seats.extend(state.seat_rows)
        if state.seat_rows:
            there = state.seat_rows[0].place
            report.sizes[(there.district, there.key)] = size
        state.seat_place, state.seat_rows = None, []
        return
    seats = number(re.sub(r"\s*mandat\w*", "", cells[-1]))
    if seats is None:
        return
    first = len(cells) >= 3 and MARK.match(cells[0]) is not None
    if first:
        state.seat_place = place(cells[0])
        rest = [c for c in cells[1:-1] if not c.lower().startswith("hotărâ")]
        for old in state.seat_rows:
            old.place = state.seat_place
    else:
        rest = cells[:-1]
    text = label(" ".join(rest))
    independent = re.search(r"\s*[-–]?\s*candidat(?:ul)? independent$", text, re.IGNORECASE)
    party = None if independent else text
    name = text[: independent.start()].strip(" -–") if independent else None
    state.seat_rows.append(Seats(report.day, state.seat_place or Place("", "", ""), party, name, seats))


def person(text: str) -> str:
    words = clean(text).split()
    shouting = [w for w in words if w.isupper() and len(w) > 1]
    if shouting and len(shouting) < len(words):
        words = [*shouting, *(w for w in words if w not in shouting)]
    return " ".join(w.title() if w.isupper() else w for w in words)


def winner_row(report: Report, state: State, cells: list[str] | None) -> None:
    """Take a row of the table of winners. A row can run over a page, so each waits for the next to show it is whole."""
    held = state.winner
    if cells is not None and cells[0].lower().startswith("hotărâ"):
        state.orphan, state.orphan_place = cells, ""
        return
    if cells is not None and not MARK.match(cells[0]):
        if held is not None and re.match(r"(raionul|r-l|UTA)\b", cells[0]):
            width = max(len(held), len(cells))
            state.winner = [
                clean(f"{held[i] if i < len(held) else ''} {cells[i] if i < len(cells) else ''}") for i in range(width)
            ]
        return
    state.winner = cells
    if held is not None:
        winner_done(report, state, held)


def winner_done(report: Report, state: State, cells: list[str]) -> None:
    body = [c for c in cells[1:] if not c.lower().startswith("hotărâ")]
    stage = re.search(r"(?:^|turul\s+)(ii|i|1|2)\b", body[-1].strip(), re.IGNORECASE) if body else None
    again = bool(body) and "repetat" in body[-1].lower()
    if stage is not None:
        body = body[:-1]
    if len(body) >= 2:
        name, party = body[0], clean(" ".join(body[1:]))
    else:
        found = PARTY.search(body[0])
        name, party = (body[0][: found.start()], body[0][found.start() :]) if found else (body[0], "")
    name = name.strip(" ,-–")
    independent = (
        None if not party else re.fullmatch(r"candidat(ul)? independent", party.strip(), re.IGNORECASE) is not None
    )
    report.winners.append(
        Winner(
            place(cells[0]),
            person(name),
            party.strip(" ,") if independent is False else None,
            2 if stage is not None and stage.group(1).lower() in ("ii", "2") else 1,
            again,
            independent,
        )
    )


BODIES = {"comunal": "comuna", "sătesc": "sat", "orășenesc": "oras", "municipal": "municipiu"}


def vote_row(report: Report, state: State, cells: list[str]) -> None:
    """A council's seats and list votes, under a heading such as `Consiliul comunal Bobeica, raionul Hîncești – 13 mandate`."""
    heading_ = re.match(r"Consiliul\s+(\w+)\s+(.+?)\s*[–-]\s*(\d+)\s+mandate", cells[0])
    if heading_ and len(cells) == 1:
        word = {"comunal": "Comuna", "sătesc": "Satul", "orășenesc": "Orașul", "municipal": "Municipiul"}[
            heading_.group(1)
        ]
        there = place(f"{word} {heading_.group(2)}")
        state.council = (there, int(heading_.group(3)))
        report.sizes[(there.district, there.key)] = int(heading_.group(3))
        return
    at = state.council
    if at is None or len(cells) < 3:
        return
    votes = number(cells[2])
    seats = number(cells[1]) or 0
    text = label(cells[0])
    independent = re.search(r"\s*[-–]?\s*candidat(?:ul)? independent$", text, re.IGNORECASE)
    report.seats.append(
        Seats(
            report.day,
            at[0],
            None if independent else text,
            text[: independent.start()].strip(" -–") if independent else None,
            seats,
            votes,
        )
    )


# Cells the commission prints wrongly, each proven by the totals row printed beside them.
CORRECTIONS = {
    ("2021-12-05", "edinet", "cupcini", 2): {"voters": 2285},
}


# Totals rows whose voters cell disagrees with the rows above and with valid plus invalid ballots: the printed
# registered, valid, and invalid totals match the rows, so the voters total is the commission's slip.
PRINTED_ERRORS = {("2021-11-21", 2): "voters"}


def printed_voters(
    printed: set[tuple[int, tuple[int, int, int, int]]], stage: int, sums: tuple[int, int, int, int]
) -> tuple[int]:
    for found, figures in printed:
        if found == stage and figures[0] == sums[0] and figures[2:] == sums[2:]:
            return (figures[1],)
    return (sums[1],)


@cache
def report(day: str) -> Report:
    found = read(day)
    for row in found.turnout:
        fix = CORRECTIONS.get((row.day.isoformat(), row.place.district, row.place.key, row.stage))
        if fix:
            row.voters = fix.get("voters", row.voters)
    verify(found)
    return found


def verify(found: Report) -> None:
    """Check the rows against the totals each table prints and against each council's size."""
    groups: dict[tuple[int, bool], list[Turnout]] = {}
    for row in found.turnout:
        groups.setdefault((row.stage, row.repeat), []).append(row)
    printed = {(t[1], t[2:6]) for t in found.totals if t[0] != "seats"}
    for (stage, repeat), rows in groups.items():
        for kind in ("council", "mayor", "unknown"):
            of = [r for r in rows if r.kind == kind]
            if not of:
                continue
            sums = (
                sum(r.registered for r in of),
                sum(r.voters for r in of),
                sum(r.valid for r in of),
                sum(r.invalid for r in of),
            )
            if repeat or len(of) == 1:
                continue
            if PRINTED_ERRORS.get((found.day.isoformat(), stage)) == "voters":
                sums = (sums[0], *printed_voters(printed, stage, sums), sums[2], sums[3])
            if (stage, sums) not in printed:
                raise ValueError(f"{found.day} {kind} round {stage} sums {sums}, not a printed total {sorted(printed)}")
    for row in found.turnout:
        if row.voters != row.valid + row.invalid:
            raise ValueError(
                f"{found.day} {row.place.name}: {row.voters} voters, {row.valid} valid, {row.invalid} invalid"
            )
    seats: dict[tuple[str, str], int] = {}
    for item in found.seats:
        seats[(item.place.district, item.place.key)] = seats.get((item.place.district, item.place.key), 0) + item.seats
    for at, total in seats.items():
        if found.sizes.get(at) != total:
            raise ValueError(f"{found.day} council {at} holds {total} seats of {found.sizes.get(at)}")


def key(kind: str, *parts: object) -> str:
    return local.key(f"new-local:{kind}", *parts)


def locality_id(at: Place) -> str:
    if at.kind == "municipiu" and at.district in ("balti", "chisinau"):
        return local.district_area(at.district)
    return local.locality_area(at.district, at.key)


def mayor_office(at: Place) -> str:
    municipal = at.kind == "municipiu" and at.district in ("balti", "chisinau")
    return local.mayor_office(at.district, None if municipal else at.key)


@cache
def places() -> dict[tuple[str, str], Place]:
    """Every locality the reports name, by district and locality key."""
    found: dict[tuple[str, str], Place] = {}
    for day in DAYS:
        rep = report(day)
        for at in [
            *(r.place for r in rep.turnout),
            *(r.place for r in rep.seats),
            *(r.place for r in rep.winners),
        ]:
            found.setdefault((at.district, at.key), at)
    return found


def is_district(at: Place) -> bool:
    return at.kind == "municipiu" and at.district in ("balti", "chisinau")


def new_places() -> dict[tuple[str, str], Place]:
    known = local.localities()
    return {k: at for k, at in places().items() if not is_district(at) and k not in known}


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The localities named in the Central Electoral Commission's reports on the new local elections of 2021 to "
        "2026 that its lists of the elected in 2015 and 2019 do not name",
        depends=[local.GEO],
    )
    p.external |= {MOLDOVA, COUNTRY}
    for (district, locality), at in sorted(new_places().items()):
        id = local.locality_area(district, locality)
        p.add("area", id=id, scheme_code="md.locality")
        p.add("area_code", area_id=id, code=f"{district}:{locality}")
        p.add("area_parent", area_id=id, parent_id=local.district_area(district))
        p.name(id, at.label, "ro", "official")
        p.name(id, at.label, "en", "english", False)
    return p


def label_of(at: Place) -> str:
    known = local.localities().get((at.district, at.key))
    return known or at.label


def party_label(text: str) -> str:
    return re.sub(r"\s+", " ", local.fix(text.replace(",,", "„"))).strip()


def party_labels() -> set[str]:
    found: set[str] = set()
    for day in DAYS:
        rep = report(day)
        found |= {party_label(w.party) for w in rep.winners if w.party}
        found |= {party_label(s.party) for s in rep.seats if s.party}
    return found


def party_ids() -> dict[str, str]:
    """The id of every party the reports name, by label."""
    return {label: local.party_ref(label, local.existing_parties()) for label in sorted(party_labels())}


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The mayors' offices, councils, and parties named in the Central Electoral Commission's reports on the new "
        "local elections of 2021 to 2026 that its earlier lists do not name",
        depends=[*([GEO] if new_places() else []), local.GEO, local.REGISTRY, "md.cec/registry"],
    )
    p.external.add(MOLDOVA)
    from ea.sources.md import cec

    for url in REPORTS.values():
        p.artifact(raw.cached(SOURCE, url), cec.CEC_SOURCE)
    held_offices = {local.mayor_office(m.district_key, m.locality_key) for m in local.read_mayors_2019()}
    held_bodies = {
        local.council_body("local", w.district_key, w.locality_key) for w in local.read_councillors_2015()[0]
    }
    names = local.districts()
    made: set[str] = set()
    mayors = {(w.place.district, w.place.key): w.place for d in DAYS for w in report(d).winners}
    mayors |= {(r.place.district, r.place.key): r.place for d in DAYS for r in report(d).turnout if r.kind != "council"}
    for at in sorted(mayors.values(), key=lambda a: (a.district, a.key)):
        office = mayor_office(at)
        if office in held_offices or office in made:
            continue
        made.add(office)
        where = names[at.district][0] if is_district(at) else label_of(at)
        p.add("office", id=office, polity_id=MOLDOVA, kind="mayor")
        p.name(office, f"Mayor of {where}", "en", "official")
        p.name(office, f"Primarul localității {where}", "ro", "native", False)
    councils = {(s.place.district, s.place.key): s.place for d in DAYS for s in report(d).seats}
    councils |= {
        (r.place.district, r.place.key): r.place for d in DAYS for r in report(d).turnout if r.kind == "council"
    }
    for at in sorted(councils.values(), key=lambda a: (a.district, a.key)):
        body = local.council_body("local", at.district, at.key)
        if body in held_bodies or body in made:
            continue
        made.add(body)
        name = label_of(at)
        p.add("body", id=body, polity_id=MOLDOVA, kind="council")
        p.name(body, f"{name} Local Council", "en", "official")
        p.name(body, f"Consiliul local {name}", "ro", "native", False)
    known = set(local.parties())
    for label, id in party_ids().items():
        if id in local.existing_parties() or id in known or id in made:
            continue
        made.add(id)
        p.add("party", id=id, kind="bloc" if label.lower().startswith("blocul") else "party", polity_id=MOLDOVA)
        p.name(id, label, "ro", "official")
    return p


def build(first: str) -> Package:
    from ea.sources.md import cec

    rep = report(first)
    p = Package(
        f"md.cec/new-local-{first}",
        SOURCE,
        f"The new local elections of {first} and their runoffs: turnout and the declared winners, from the "
        "Central Electoral Commission's report on them",
        depends=[REGISTRY, *([GEO] if new_places() else []), local.GEO, local.REGISTRY, "md.cec/registry"],
    )
    p.external.add(MOLDOVA)
    p.uses(raw.cached(SOURCE, REPORTS[first]))
    parties = party_ids()
    names = local.districts()
    elections: dict[date, str] = {}

    def elect(held: date) -> str:
        if held not in elections:
            id = elections[held] = key("election", held.isoformat())
            p.add("election", id=id, polity_id=MOLDOVA, kind="local", date_from=held, date_to=held, status="held")
            p.add("slug", path=f"md/{held}-local-new", entity_id=id, is_current=True)
            p.name(id, f"Moldovan new local elections of {held.isoformat()}", "en", "official")
            p.name(id, f"Alegeri locale noi din {held.isoformat()}", "ro", "native", False)
        return elections[held]

    method = (
        "The Central Electoral Commission's report gives each locality's turnout and the candidate or party "
        "declared elected. It prints no votes by candidate, so this result set holds turnout and the declared "
        "winners."
    )
    slugs: set[str] = set()
    people: set[str] = set()
    contests: dict[tuple[str, str, str, date], str] = {}

    def contest(kind: str, at: Place, held: date, stage: int, repeat: bool) -> tuple[str, str, str]:
        name = f"{kind}:{at.district}:{at.key}:{held}"
        where = names[at.district][0] if is_district(at) else label_of(at)
        id = key("contest", name)
        area = locality_id(at)
        fields: dict[str, object] = {}
        if kind == "mayor":
            fields.update(office_id=mayor_office(at), kind="office")
        else:
            fields.update(body_id=local.council_body("local", at.district, at.key), kind="list_allocation")
        p.add(
            "contest",
            id=id,
            election_id=elect(held),
            district_area_id=area,
            magnitude=1 if kind == "mayor" else rep.sizes.get((at.district, at.key)),
            round_number=stage,
            default_result_kind="counted",
            **fields,
        )
        noun = "Mayor" if kind == "mayor" else "Local council"
        suffix = (
            f", round {stage}" + (", repeat vote" if repeat else "") if kind == "mayor" and stage > 1 or repeat else ""
        )
        p.name(id, f"{noun} of {where}{suffix}", "en", "official")
        p.name(id, f"{'Primarul' if kind == 'mayor' else 'Consiliul local'} {where}{suffix}", "ro", "native", False)
        slug = f"md/{held}-local-new/{at.district.replace(' ', '-')}/{local.slug(at.key)}-{kind}"
        while slug in slugs:
            slug += "-2"
        slugs.add(slug)
        p.add("slug", path=slug, entity_id=id, is_current=True)
        result_set = key("result_set", name)
        p.add(
            "result_set",
            id=result_set,
            election_id=elect(held),
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=cec.CEC_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        unit = key("unit", name)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MOLDOVA,
            scheme_code="md.locality",
            code=f"new:{held}:{kind}:{at.district}:{at.key}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(unit, f"{where} ({held})", "en", "official")
        if (at.district, at.key, held) in voided and kind == "mayor":
            p.add(
                "note",
                id=key("note", result_set, "void"),
                entity_id=result_set,
                kind="caveat",
                body_md="A later vote for the same office and round replaced this one: the report prints its "
                "turnout, but its result was declared void and it elected no one.",
                lang="en",
            )
        contests[(kind, at.district, at.key, held)] = id
        return id, result_set, unit

    def value(result_set: str, unit: str, option: str | None, measure: int, amount: int) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=amount,
        )

    def person(name: str, *parts: object) -> str:
        who = key("person", *parts)
        if who not in people:
            people.add(who)
            p.add("person", id=who)
            p.name(who, name, "ro", "official")
        return who

    wins: dict[tuple[str, str], list[Winner]] = {}
    for w in rep.winners:
        wins.setdefault((w.place.district, w.place.key), []).append(w)
    mayor_rows: dict[tuple[str, str], list[Turnout]] = {}
    for row in rep.turnout:
        at = (row.place.district, row.place.key)
        kind = row.kind
        if kind == "unknown":
            kind = "mayor" if at in wins else "council"
        row.kind = kind
        if kind == "mayor":
            mayor_rows.setdefault(at, []).append(row)
    made: dict[tuple[str, int, bool], str] = {}
    outcomes: dict[tuple[str, str], list[tuple[Turnout | None, Winner]]] = {}
    voided: set[tuple[str, str, date]] = set()
    for at, winners in wins.items():
        rows = sorted(mayor_rows.get(at, []), key=lambda r: r.day)
        for stage in (1, 2):
            theirs = [w for w in winners if w.stage == stage][-1:]
            of = [r for r in rows if r.stage == stage]
            voided |= {(at[0], at[1], r.day) for r in of[:-1]}
            for w, r in zip(theirs, of[-1:], strict=False):
                outcomes.setdefault(at, []).append((r, w))
            if not of and theirs:
                outcomes.setdefault(at, []).append((None, theirs[-1]))
    for at, rows in mayor_rows.items():
        for stage in (1, 2):
            of = sorted((r for r in rows if r.stage == stage), key=lambda r: r.day)
            voided |= {(at[0], at[1], r.day) for r in of[:-1]}
    # Mayors: a contest for each round held, with turnout where the report prints it.
    done: set[tuple[str, str, date]] = set()
    for at, rows in sorted(mayor_rows.items()):
        for row in sorted(rows, key=lambda r: r.day):
            id, result_set, unit = contest("mayor", row.place, row.day, row.stage, row.repeat)
            made[(f"{at}", row.stage, row.day == rep.day)] = id
            for measure, amount in (
                (REGISTERED, row.registered),
                (VOTERS, row.voters),
                (VALID, row.valid),
                (INVALID, row.invalid),
            ):
                value(result_set, unit, None, measure, amount)
            done.add((at[0], at[1], row.day))
    # Winners, in the round that elected them.
    for at, pairs in sorted(outcomes.items()):
        for row, w in pairs:
            held = row.day if row else rep.day + timedelta(days=14 * (w.stage - 1))
            if row is None:
                id, result_set, unit = contest("mayor", w.place, held, w.stage, False)
            else:
                id = contests[("mayor", at[0], at[1], held)]
                result_set = key("result_set", f"mayor:{at[0]}:{at[1]}:{held}")
            where = names[at[0]][0] if is_district(w.place) else label_of(w.place)
            party = parties[party_label(w.party)] if w.party else None
            option = key("option", id)
            p.add("contest_option", id=option, contest_id=id, kind="candidate", party_id=party, ballot_label=w.name)
            who = person(w.name, id)
            p.add(
                "candidacy",
                id=key("candidacy", id),
                person_id=who,
                option_id=option,
                role="head",
                ballot_name=w.name,
                member_of_party_id=party,
                is_independent=w.independent,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            unit = key("unit", f"mayor:{at[0]}:{at[1]}:{held}")
            value(result_set, unit, option, SEATS, 1)
            del where
    # Councils.
    by_council: dict[tuple[str, str], list[Seats]] = {}
    for s in rep.seats:
        by_council.setdefault((s.place.district, s.place.key), []).append(s)
    turnout = {(r.place.district, r.place.key): r for r in rep.turnout if r.kind == "council"}
    for at, lines in sorted(by_council.items()):
        row = turnout.get(at)
        held = row.day if row else rep.day
        id, result_set, unit = contest("council", lines[0].place, held, 1, False)
        if row:
            for measure, amount in (
                (REGISTERED, row.registered),
                (VOTERS, row.voters),
                (VALID, row.valid),
                (INVALID, row.invalid),
            ):
                value(result_set, unit, None, measure, amount)
        for number_, line in enumerate(lines, 1):
            party = parties[party_label(line.party)] if line.party else None
            option = key("option", id, number_)
            p.add(
                "contest_option",
                id=option,
                contest_id=id,
                kind="list" if party else "candidate",
                party_id=party,
                ballot_label=party_label(line.party) if line.party else line.candidate,
            )
            if line.candidate:
                who = person(line.candidate, id, number_)
                p.add(
                    "candidacy",
                    id=key("candidacy", id, number_),
                    person_id=who,
                    option_id=option,
                    role="head",
                    ballot_name=line.candidate,
                    is_independent=True,
                )
            if line.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=line.seats)
                value(result_set, unit, option, SEATS, line.seats)
            if line.votes is not None:
                value(result_set, unit, option, 1, line.votes)
    p.accept(
        "accounting.votes_valid",
        "the report prints each locality's valid votes but no votes by candidate, so the valid total has no "
        "candidate votes to equal",
    )
    return p


def packages() -> list[str]:
    return [*([GEO] if new_places() else []), REGISTRY, *(f"md.cec/new-local-{day}" for day in DAYS)]


def parse() -> None:
    if new_places():
        geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()
