"""Danish national referendums 1920-2000 by nomination district, from Statistics Denmark's and the Ministry of the
Interior's printed volumes."""

import functools
import html
import itertools
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import package, raw, scan
from ea.package import Package
from ea.store import DATA

SOURCE = "dk.dst_hist"
SE = "https://www.dst.dk/Site/Dst/Udgivelser/GetSEPdf.aspx?id="
IM = "https://cdn2.gopublic.dk/indenrigssundhedsminvalg/media/"
DENMARK = str(uuid5(package.NAMESPACE, "polity\x1fDK"))
SCHEME = "dk.opstillingskreds"
OCR = DATA / "parsed" / SOURCE / "ocr"
REGISTERED, VOTES, CAST, VALID, INVALID = 2, 1, 3, 4, 5

FILES = {
    "1920": SE + "8214",
    "1939": SE + "8994",
    "1953": SE + "9873",
    "1961": "https://www.dst.dk/extranet/bibscan/da/mono/folkeafstemning_1961.pdf",
    "1963": SE + "10645",
    "1969": "https://www.dst.dk/extranet/bibscan/da/mono/folkeafstemning_1969_a.pdf",
    "1971": "https://www.dst.dk/Site/Dst/Udgivelser/GetPubFile.aspx?id=20207&sid=valg1971",
    "1972": SE + "11338",
    "1978": SE + "12041",
    "1986": "https://dst.dk/seudg/25498",
    "1992": "https://dst.dk/seudg/25497",
    "1993": "https://dst.dk/seudg/25499",
    "1998": IM + "18834/amsterdamtraktaten.pdf",
    "2000": IM
    + "6921/statistik-over-folkeafstemningen-den-28-september-2000-om-danmarks-deltagelse-i-den-faelles-valuta.pdf",
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


@functools.cache
def text(volume: str) -> str:
    """The layout text of one printed volume (needs poppler's pdftotext)."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, FILES[volume]).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


Box = tuple[int, int, int, int]
COUNT = re.compile(r"^\d{1,3}(?:\.\d{3})+$|^\d+$")
GROUP = re.compile(
    r"^\s*(\D*?(?:Storkreds|Amtskreds|HELE LANDET|Hovedstaden|Øerne|Jylland|København-Frederiksberg)\S*)\s{2,}(\S.*)$"
)
ROW = re.compile(r"^\s*(\d{1,2})\.\s+([^\d].*?)\s{2,}(\S.*)$")


def integer(token: str) -> int | None:
    return int(token.replace(".", "")) if COUNT.match(token) else None


def block(volume: str, first: str, last: str) -> list[str]:
    lines = text(volume).splitlines()
    begin = next(i for i, line in enumerate(lines) if re.match(first, line))
    stop = next(i for i, line in enumerate(lines) if i > begin and re.match(last, line))
    return lines[begin:stop]


@dataclass
class Unit:
    group: str
    number: str
    name: str
    registered: int | None
    valid: int
    answers: list[tuple[int, int]]  # (yes, no) per question
    cast: int | None = None
    invalid: int | None = None
    where: dict[tuple[str | None, int], tuple[int, Box]] = field(default_factory=dict)  # (option, measure) -> page, box


@dataclass
class Question:
    code: str
    english: str
    danish: str


@dataclass
class Vote:
    volume: str
    day: date
    english: str
    danish: str
    questions: list[Question]
    units: list[Unit]
    totals: dict[str, int]  # the printed national figures: registered, valid, and yes/no per question
    pages: str = ""
    scanned: bool = False


def modern(volume: str, votes: tuple[str, str], ballots: tuple[str, str]) -> tuple[list[Unit], dict[str, int]]:
    """Tables of registered, turnout, valid, yes, no, then percentages; and of registered, cast, letter votes, invalid."""
    group = ""
    units: list[Unit] = []
    totals: dict[str, int] = {}
    for line in block(volume, *votes):
        found = GROUP.match(line)
        if found:
            group = found.group(1).strip()
            if group == "HELE LANDET":
                tokens = found.group(2).split()
                totals = {
                    "registered": integer(tokens[0]) or 0,
                    "valid": integer(tokens[2]) or 0,
                    "yes0": integer(tokens[3]) or 0,
                    "no0": integer(tokens[4]) or 0,
                }
            continue
        found = ROW.match(line)
        if found:
            tokens = found.group(3).split()
            values = [integer(tokens[i]) for i in (0, 2, 3, 4)]
            units.append(
                Unit(
                    group,
                    found.group(1),
                    found.group(2).strip(),
                    values[0],
                    values[1] or 0,
                    [(values[2] or 0, values[3] or 0)],
                )
            )
    by_name = {(u.group, u.number, u.name): u for u in units}
    group = ""
    for line in block(volume, *ballots):
        found = GROUP.match(line)
        if found:
            group = found.group(1).strip()
            if group == "HELE LANDET":
                tokens = found.group(2).split()
                totals["cast"] = integer(tokens[1]) or 0
                totals["invalid"] = integer(tokens[3]) or 0
            continue
        found = ROW.match(line)
        if found:
            tokens = found.group(3).split()
            unit = by_name[(group, found.group(1), found.group(2).strip())]
            if integer(tokens[0]) != unit.registered or integer(tokens[9]) != unit.valid:
                raise ValueError(f"{volume} {unit.name}: the two tables disagree on registered or valid voters")
            unit.cast = integer(tokens[1])
            unit.invalid = integer(tokens[3])
    return units, totals


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SYSTEM = key("electoral_system", "referendum")
SOURCE_DST = key("source", "dst")
SOURCE_IM = key("source", "im")
METHOD = (
    "Counted per nomination district (opstillingskreds) and printed in the official volume cited, which holds the "
    "result the election authority declared. Registered voters are the voters entitled to vote, valid votes are the "
    "valid ballots, and yes and no are the valid ballots for and against the question. Figures for a district are "
    "taken from the volume's table of nomination districts; the table's printed subtotals for each county or "
    "metropolitan constituency and for the whole country were used to check them."
)


SCAN_METHOD = (
    " The volume is a scan. Each page was read twice, from the scan's embedded text and with optical character "
    "recognition at 300 dpi, and the readings were reconciled cell by cell: yes and no add up to the valid votes, "
    "the printed percentages follow from the counts, the voters of each district's polling areas add up to the "
    "district, the districts add up to the county, and the counties add up to the national figures printed in "
    "tables 1 and 2. Each figure is stored with the page and the box where it is printed."
)


def volume_source(volume: str) -> str:
    return SOURCE_IM if volume in ("1998", "2000") else SOURCE_DST


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Sources and the referendum electoral system for Danish national referendums before 2009",
    )
    p.external.update({DENMARK, SCHEME})
    p.add(
        "source",
        id=SOURCE_DST,
        kind="official_body",
        publisher="Danmarks Statistik",
        title="Statistiske Efterretninger and Statistiske Meddelelser: referendum volumes",
        url="https://www.dst.dk/da/Statistik/emner/borgere/demokrati/folkeafstemninger",
        license="Statistics Denmark data may be reused with attribution to Statistics Denmark as the source",
        attribution="Danmarks Statistik",
    )
    p.add(
        "source",
        id=SOURCE_IM,
        kind="official_body",
        publisher="Indenrigsministeriet",
        title="Tidligere folkeafstemninger",
        url="https://www.valg.im.dk/noegletal/folkeafstemninger/tidligere-folkeafstemninger",
        license="Published by the Danish Ministry of the Interior; reused with attribution to the ministry as the source",
        attribution="Indenrigsministeriet",
    )
    p.add("electoral_system", id=SYSTEM, polity_id=DENMARK, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="A yes or no question put to all voters.",
        lang="en",
    )
    return p


def election(vote: Vote) -> Package:
    p = Package(
        f"{SOURCE}/{vote.day}",
        SOURCE,
        f"The {vote.day} Danish referendum by nomination district",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({DENMARK, SCHEME})
    source = volume_source(vote.volume)
    stored = raw.cached(SOURCE, FILES[vote.volume])
    artifact = p.artifact(stored, source)
    pages: dict[int, str] = {}
    id = key("election", vote.day)
    path = f"dk/{vote.day}-referendum"
    p.add("election", id=id, polity_id=DENMARK, kind="referendum", date_from=vote.day, date_to=vote.day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, vote.english, "en", "official")
    p.name(id, vote.danish, "da", "native", False)
    units: dict[int, str] = {}
    for index, unit in enumerate(vote.units):
        uid = key("unit", vote.day, unit.group, unit.number, unit.name)
        units[index] = uid
        p.add(
            "reporting_unit",
            id=uid,
            polity_id=DENMARK,
            scheme_code=SCHEME,
            code=f"{vote.day}:{unit.group}:{unit.number}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(uid, unit.name, "da", "official")
    for index, question in enumerate(vote.questions):
        contest = key("contest", vote.day, question.code)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="ballot_question",
            electoral_system_id=SYSTEM,
            round_number=1,
            default_result_kind="counted",
            magnitude=None,
        )
        p.name(contest, question.english, "en", "official")
        p.name(contest, question.danish, "da", "native", False)
        p.add("slug", path=f"{path}/{question.code}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type="referendum")
        result_set = key("result_set", vote.day, question.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + (SCAN_METHOD if vote.scanned else ""),
            lang="en",
        )
        options = {}
        for position, (code, label) in enumerate((("yes", "Ja"), ("no", "Nej")), 1):
            options[code] = key("option", vote.day, question.code, code)
            p.add(
                "contest_option",
                id=options[code],
                contest_id=contest,
                kind="answer",
                parent_option_id=None,
                party_id=None,
                ballot_position=None,
                ballot_label=label,
            )
        for position, unit in enumerate(vote.units):
            uid = units[position]
            yes, no = unit.answers[index]
            put(p, result_set, uid, None, VALID, yes + no)
            if unit.registered is not None:
                put(p, result_set, uid, None, REGISTERED, unit.registered)
            if unit.cast is not None and unit.invalid is not None:
                put(p, result_set, uid, None, CAST, unit.cast)
                put(p, result_set, uid, None, INVALID, unit.invalid)
            put(p, result_set, uid, options["yes"], VOTES, yes)
            put(p, result_set, uid, options["no"], VOTES, no)
            for (code, measure), (page, box) in unit.where.items():
                width, height = page_sizes(vote.volume)[page]
                if page not in pages:
                    pages[page] = key("page", vote.day, page)
                    p.add(
                        "source_page",
                        id=pages[page],
                        artifact_id=artifact,
                        page_number=page,
                        image_key=f"pages/{SOURCE}/{stored.sha256}/{page}.png",
                        width=width,
                        height=height,
                    )
                p.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=uid,
                    option_id=options[code] if code else None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    page_id=pages[page],
                    source_id=None,
                    x0=box[0] / width,
                    y0=box[1] / height,
                    x1=box[2] / width,
                    y1=box[3] / height,
                )
        for code, measure, value in (
            (None, VALID, vote.totals[f"yes{index}"] + vote.totals[f"no{index}"]),
            (None, REGISTERED, vote.totals.get("registered")),
            (None, CAST, vote.totals.get("cast")),
            (None, INVALID, vote.totals.get("invalid")),
            ("yes", VOTES, vote.totals[f"yes{index}"]),
            ("no", VOTES, vote.totals[f"no{index}"]),
        ):
            if value is not None:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=options[code] if code else None,
                    measure=measure,
                    value=value,
                )
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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


@functools.cache
def page_sizes(volume: str) -> dict[int, tuple[int, int]]:
    """Width and height in pixels of each page of a volume rendered at 300 dpi."""
    shown = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "32", str(raw.cached(SOURCE, FILES[volume]).path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {
        int(n): (round(float(w) * 300 / 72), round(float(h) * 300 / 72))
        for n, w, h in re.findall(r"Page\s+(\d+)\s+size:\s+([\d.]+) x ([\d.]+) pts", shown)
    }


MODERN = {
    "1998": ((r"\s*Tabel 8 \(Side 1", r"\s*Tabel 9\. "), (r"\s*Tabel 10 \(Side 1", r"\s*Tabel 11\. ")),
    "2000": ((r"\s*Tabel 8 \(Side 1", r"\s*Tabel 9"), (r"\s*Tabel 10 \(Side 1", r"\s*Tabel 11\. ")),
}
TITLES = {
    "1998": (
        date(1998, 5, 28),
        "1998 referendum on the Amsterdam Treaty",
        "Folkeafstemningen den 28. maj 1998 om Danmarks tiltrædelse af Amsterdamtraktaten",
    ),
    "2000": (
        date(2000, 9, 28),
        "2000 referendum on Danish participation in the euro",
        "Folkeafstemningen den 28. september 2000 om Danmarks deltagelse i den fælles valuta",
    ),
}


def votes() -> list[Vote]:
    found: list[Vote] = []
    for volume, (votes_table, ballots_table) in MODERN.items():
        units, totals = modern(volume, votes_table, ballots_table)
        day, english, danish = TITLES[volume]
        check(volume, units, totals)
        found.append(Vote(volume, day, english, danish, [Question("question", english, danish)], units, totals))
    for volume, day in SCANNED.items():
        found.append(scanned(volume, day))
    return found


def scanned(volume: str, day: date) -> Vote:
    """A scanned volume's districts and national figures, read from tables 7 and 9."""
    root = tree(volume)
    problems = disagreements(root) + against_summary(volume, root)
    if problems:
        raise ValueError(f"{volume}: " + "; ".join(problems))
    units: list[Unit] = []
    for county, number, name, node in districts(volume):
        registered, valid, yes, no = node.votes.values
        ballots = node.ballots.values
        where: dict[tuple[str | None, int], tuple[int, Box]] = {
            (None, REGISTERED): (node.votes.page, node.votes.boxes[0]),
            (None, VALID): (node.votes.page, node.votes.boxes[1]),
            ("yes", VOTES): (node.votes.page, node.votes.boxes[2]),
            ("no", VOTES): (node.votes.page, node.votes.boxes[3]),
            (None, CAST): (node.ballots.page, node.ballots.boxes[1]),
            (None, INVALID): (node.ballots.page, node.ballots.boxes[3]),
        }
        units.append(Unit(county, str(number), name, registered, valid, [(yes, no)], ballots[1], ballots[3], where))
    registered, valid, yes, no = root.votes.values
    totals = {
        "registered": registered,
        "valid": valid,
        "yes0": yes,
        "no0": no,
        "cast": root.ballots.values[1],
        "invalid": root.ballots.values[3],
    }
    english, danish = SCANNED_TITLES[volume]
    check(volume, units, totals)
    return Vote(volume, day, english, danish, [Question("question", english, danish)], units, totals, scanned=True)


def against_summary(volume: str, root: Node) -> list[str]:
    """Where tables 7 and 9 disagree with the national, regional, and county figures of tables 1 and 2."""
    rows = [node for node in walk(root) if node.kind in ("total", "region", "kreds")]
    short, long = read_table(volume, 7, True), read_table(volume, 9, True)
    if not len(rows) == len(short) == len(long):
        return [f"tables 1 and 2 have {len(short)} and {len(long)} lines, tables 7 and 9 have {len(rows)} subtotals"]
    found: list[str] = []
    for node, one, two in zip(rows, short, long, strict=True):
        if one.values != node.votes.values:
            found.append(f"{node.label}: table 1 prints {one.values}, table 7 {node.votes.values}")
        ballots = tuple(two.values[i] for i in (0, 1, 3, 4, 5, 6)) if two.values else ()
        if ballots != tuple(node.ballots.values[i] for i in (0, 1, 3, 4, 5, 6)):
            found.append(f"{node.label}: table 2 prints {two.values}, table 9 {node.ballots.values}")
    return found


def check(volume: str, units: list[Unit], totals: dict[str, int]) -> None:
    """Fail unless the districts add up to each other and to the printed national figures."""
    for unit in units:
        for yes, no in unit.answers:
            if yes + no != unit.valid:
                raise ValueError(f"{volume} {unit.name}: yes {yes} and no {no} do not make valid {unit.valid}")
    for index in range(len(units[0].answers)):
        for measure, value in (
            (f"yes{index}", sum(u.answers[index][0] for u in units)),
            (f"no{index}", sum(u.answers[index][1] for u in units)),
        ):
            if value != totals[measure]:
                raise ValueError(f"{volume} {measure}: districts add to {value}, the volume prints {totals[measure]}")
    for measure, value in (
        ("cast", sum(u.cast or 0 for u in units)),
        ("invalid", sum(u.invalid or 0 for u in units)),
    ):
        if measure in totals and value != totals[measure]:
            raise ValueError(f"{volume} {measure}: districts add to {value}, the volume prints {totals[measure]}")
    registered = sum(u.registered or 0 for u in units)
    if "registered" in totals and registered != totals["registered"]:
        raise ValueError(
            f"{volume} registered: districts add to {registered}, the volume prints {totals['registered']}"
        )


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{v.day}" for v in votes())]


def parse() -> None:
    registry().write()
    for vote in votes():
        election(vote).write()


def emit() -> None:
    package.run("load", packages())


# The 1992 and 1993 volumes are scans. Each page is read twice, with the volume's embedded text layer and with
# Tesseract's Danish model at 300 dpi, and the two readings are reconciled cell by cell against the arithmetic
# the tables print: yes plus no make the valid votes, the percentages follow from the counts, a district's
# polling areas add up to the district, and the districts add up to the county and to the country.

SCANNED = {"1992": date(1992, 6, 2), "1993": date(1993, 5, 18)}
VOTE_PAGES = range(8, 18)  # table 7: registered, valid, yes, no
BALLOT_PAGES = range(18, 28)  # table 9: registered, ballots cast, invalid, valid
SHAPES = {7: 8, 9: 10}  # numeric cells printed per row of each table
FLIP = {"0": "86", "1": "7", "2": "7", "3": "85", "5": "63", "6": "58", "7": "12", "8": "035", "9": "4"}
SCANNED_TITLES = {
    "1992": (
        "1992 referendum on the Treaty on European Union",
        "Folkeafstemningen den 2. juni 1992 om Danmarks tiltrædelse af traktaten om Den Europæiske Union",
    ),
    "1993": (
        "1993 referendum on the Edinburgh Agreement and the Maastricht Treaty",
        "Folkeafstemningen den 18. maj 1993 om Danmarks tiltrædelse af Edinburgh-Afgørelsen og Maastricht-Traktaten",
    ),
}
HEADER = re.compile(r"kreds|L\s*A\s*N\s*D\s*E\s*T|^København-Frederiksberg|^Øerne|^Jylland|^I ALT", re.IGNORECASE)


@dataclass
class Cell:
    text: str
    box: Box


@dataclass
class Row:
    """One printed line, as the two readings saw it."""

    page: int
    y: float
    labels: dict[str, str] = field(default_factory=dict)
    cells: dict[str, list[Cell]] = field(default_factory=dict)
    gap: bool = False


def clean(text: str) -> str:
    return html.unescape(text).strip().strip("|!").strip()


def group(words: list[tuple[str, Box]]) -> list[list[tuple[str, Box]]]:
    """Words of one page by line, top to bottom and left to right."""
    rows: list[list[tuple[str, Box]]] = []
    for word in sorted(words, key=lambda w: (w[1][1] + w[1][3]) / 2):
        middle = (word[1][1] + word[1][3]) / 2
        if rows and abs(middle - sum((w[1][1] + w[1][3]) / 2 for w in rows[-1]) / len(rows[-1])) < 14:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda w: w[1][0]) for row in rows]


def height(line: list[tuple[str, Box]]) -> float:
    return sum(w[1][1] + w[1][3] for w in line) / len(line) / 2


def read_page(volume: str, page: int, table: int, start: str = "", stop: str = r"Tabel\s+(8|10)\b") -> list[Row]:
    """The numeric lines of one page, the two readings paired by height."""
    pdf = raw.cached(SOURCE, FILES[volume])
    OCR.mkdir(parents=True, exist_ok=True)
    sources = {
        "ocr": [(w[0], w[1]) for w in scan.words(pdf.path, page, OCR / f"{pdf.sha256}-{page}.tsv", "dan")],
        "layer": scan.layer(pdf.path, page),
    }
    width = SHAPES[table]
    rows: list[Row] = []
    for name, words in sources.items():
        lines = group([(clean(t), b) for t, b in words if clean(t)])
        # The last page of a table can carry the next table, of municipalities, below it.
        starts = [height(line) for line in lines if start and re.match(start, " ".join(w[0] for w in line))]
        stops = [height(line) for line in lines if re.match(stop, " ".join(w[0] for w in line))]
        for line in lines:
            if (starts and height(line) < starts[0]) or (stops and height(line) > min(stops)):
                continue
            if len(line) < width or sum(bool(re.search(r"\d", w[0])) for w in line[-width:]) < width - 1:
                continue
            y = height(line)
            row = next((r for r in rows if name not in r.cells and abs(r.y - y) < 20), None)
            if row is None:
                row = Row(page, y)
                rows.append(row)
            row.labels[name] = " ".join(w[0] for w in line[:-width])
            row.cells[name] = [Cell(w[0], w[1]) for w in line[-width:]]
    rows.sort(key=lambda r: r.y)
    pitch = sorted(b.y - a.y for a, b in itertools.pairwise(rows))
    middle = pitch[len(pitch) // 2] if pitch else 40
    for index, row in enumerate(rows):
        row.gap = index == 0 or row.y - rows[index - 1].y > 1.5 * middle
    return rows


CONFUSED = str.maketrans({**{k: v for k, v in scan.CONFUSABLE.items()}, "/": "1", "J": "3", "^": "5"})


def counts(text: str) -> set[int]:
    """The whole number a printed count may be, with the thousands dots and look-alike letters dropped."""
    digits = re.sub(r"\D", "", text.translate(CONFUSED))
    return {int(digits)} if digits else set()


def percent(text: str) -> float | None:
    cleaned = text.translate(CONFUSED).replace(",", ".")
    if re.fullmatch(r"\d{1,3}\.\d", cleaned):
        return float(cleaned)
    digits = re.sub(r"\D", "", cleaned)
    return float(f"{digits[:-1]}.{digits[-1]}") if 2 <= len(digits) <= 4 else None


def edits(value: int, distance: int) -> set[int]:
    """The numbers that differ from this one in exactly `distance` digits, each swapped for a look-alike."""
    digits = str(value)
    found: set[int] = set()
    for places in itertools.combinations(range(len(digits)), distance):
        for swaps in itertools.product(*(FLIP.get(digits[i], "") for i in places)):
            changed = list(digits)
            for place, swap in zip(places, swaps, strict=True):
                changed[place] = swap
            if changed[0] != "0":
                found.add(int("".join(changed)))
    return found


def near(printed: list[float], num: int, den: int) -> bool:
    return den > 0 and any(abs(100 * num / den - p) <= 0.0501 for p in printed)


def vote_relation(v: tuple[int, ...], pcts: list[list[float]]) -> int | None:
    registered, valid, yes, no = v
    if yes + no != valid or valid > registered:
        return None
    return sum(near(pcts[i], n, d) for i, (n, d) in enumerate(((yes, valid), (no, valid), (no, registered))))


def ballot_relation(v: tuple[int, ...], pcts: list[list[float]]) -> int | None:
    registered, cast, letter, invalid, blank, other, valid = v
    if blank + other != invalid or valid + invalid != cast or cast > registered or letter > cast:
        return None
    return sum(near(pcts[i], n, d) for i, (n, d) in enumerate(((cast, registered), (letter, cast), (invalid, cast))))


def solve(
    bases: list[set[int]],
    pcts: list[list[float]],
    relation: Callable[[tuple[int, ...], list[list[float]]], int | None],
    floor: int = 2,
) -> list[tuple[int, ...]]:
    """The cheapest readings, in digit swaps, that satisfy the relation; several when the page cannot decide."""
    if any(not base for base in bases):
        return []
    pools: list[dict[int, set[int]]] = []
    for base in bases:
        pool = {0: set(base)}
        for distance in (1, 2):
            pool[distance] = set().union(*(edits(value, distance) for value in base)) - pool[0]
        pools.append(pool)
    for total in (0, 1, 2):
        best: dict[tuple[int, ...], int] = {}
        for spread in itertools.product(range(total + 1), repeat=len(bases)):
            if sum(spread) != total:
                continue
            for combo in itertools.product(*(pools[i][d] for i, d in enumerate(spread))):
                score = relation(combo, pcts)
                if score is not None and score >= min(floor, sum(bool(p) for p in pcts)):
                    best[combo] = score
        if best:
            top = max(best.values())
            return sorted(combo for combo, score in best.items() if score == top)
    return []


@dataclass
class Shape:
    counts: tuple[int, ...]  # positions of the counts that are kept
    pcts: tuple[int, ...]  # positions of the percentages that check them
    relation: Callable[[tuple[int, ...], list[list[float]]], int | None]


TABLES = {
    7: Shape((0, 2, 3, 4), (5, 6, 7), vote_relation),
    9: Shape((0, 1, 2, 3, 4, 5, 9), (6, 7, 8), ballot_relation),
}


@dataclass
class Printed:
    """A line of a table with its counts settled, or the readings it could not settle."""

    page: int
    y: float
    gap: bool
    label: str
    labels: list[str]
    values: tuple[int, ...] = ()
    boxes: tuple[Box, ...] = ()
    options: list[tuple[int, ...]] = field(default_factory=list)
    unseen: bool = False  # only one of the two readings found the line
    row: Row | None = None
    table: int = 7
    derived: bool = False  # settled by the sums of the rows around it, not by its own printed figures


def boxes(row: Row, table: int, values: tuple[int, ...]) -> tuple[Box, ...]:
    """Where each count is printed: a reading that has the value, else the first reading's cell."""
    names = sorted(row.cells, key=lambda n: n != "ocr")
    found: list[Box] = []
    for index, value in zip(TABLES[table].counts, values, strict=True):
        seen = [row.cells[n][index] for n in names if value in counts(row.cells[n][index].text)]
        found.append((seen or [row.cells[names[0]][index]])[0].box)
    return tuple(found)


def settle(row: Row, table: int, extra: dict[int, int] | None = None) -> Printed:
    """Settle a line's counts; `extra` adds what the other table prints, by position in the table's counts."""
    spec = TABLES[table]
    names = sorted(row.cells, key=lambda n: n != "ocr")
    bases = [set().union(*(counts(row.cells[n][i].text) for n in names)) for i in spec.counts]
    for index, value in (extra or {}).items():
        bases[index].add(value)
    pcts = [[q for n in names if (q := percent(row.cells[n][i].text)) is not None] for i in spec.pcts]
    found = solve(bases, pcts, spec.relation)
    if table == 9 and len({tuple(v[:2] + v[3:]) for v in found}) == 1:
        found = found[:1]  # the letter votes are not kept, and only the printed percentage tells their readings apart
    printed = Printed(row.page, row.y, row.gap, "", [row.labels[n] for n in names], unseen=len(names) < 2, row=row)
    if len(found) == 1:
        printed.values = found[0]
        printed.boxes = boxes(row, table, found[0])
    else:
        printed.options = found
    return printed


def read_table(volume: str, table: int, summary: bool = False) -> list[Printed]:
    """The lines of table 7 or 9, or with `summary` those of tables 1 and 2, which share page 2 and its layout."""
    if summary:
        rows = read_page(
            volume, 2, table, "" if table == 7 else r"Tabel\s+2\b", r"Tabel\s+2\b" if table == 7 else r"\Z"
        )
    else:
        rows = [row for page in (VOTE_PAGES if table == 7 else BALLOT_PAGES) for row in read_page(volume, page, table)]
    lines = [settle(row, table) for row in rows]
    for line in lines:
        line.table = table
    return lines


LEVELS = {"total": 0, "region": 1, "kreds": 2, "district": 3, "area": 4}


@dataclass
class Node:
    """A printed row of tables 7 and 9 together, with the rows nested under it."""

    kind: str
    label: str
    votes: Printed
    ballots: Printed
    children: list[Node] = field(default_factory=list)


def kind_of(row: Printed) -> str:
    label = row.labels[0]
    if re.search(r"L\s*A\s*N\s*D\s*E\s*T|^I ALT", label):
        return "total"
    if re.match(r"(København-Frederiksberg|Øerne|Jylland)$", label):
        return "region"
    if "kreds" in label:
        return "kreds"
    return "district" if row.gap else "area"


@functools.cache
def tree(volume: str) -> Node:
    votes, ballots = read_table(volume, 7), read_table(volume, 9)
    if len(votes) != len(ballots):
        raise ValueError(f"{volume}: table 7 has {len(votes)} lines and table 9 has {len(ballots)}")
    stack: list[Node] = []
    for a, b in zip(votes, ballots, strict=True):
        for one, other in ((a, b), (b, a)):
            if other.values and not one.values and one.row:  # the tables print the same registered and valid voters
                extra = (
                    {0: other.values[0], 1: other.values[6]}
                    if one.table == 7
                    else {0: other.values[0], 6: other.values[1]}
                )
                again = settle(one.row, one.table, extra)
                if again.values:
                    one.values, one.boxes = again.values, again.boxes
        node = Node(kind_of(a), a.labels[0], a, b)
        while stack and LEVELS[stack[-1].kind] >= LEVELS[node.kind]:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        stack.append(node)
    root = stack[0]
    for item in walk(root):
        if item.children:
            for printed in ("votes", "ballots"):
                complete(item, printed)
    return root


def walk(node: Node):
    yield node
    for child in node.children:
        yield from walk(child)


def complete(parent: Node, name: str) -> None:
    """Settle the one child, or the parent, that the figures on its row could not settle, from the sums."""
    rows = [parent, *parent.children]
    missing = [r for r in rows if not getattr(r, name).values]
    if len(missing) != 1:
        return
    gone = missing[0]
    others = [getattr(r, name).values for r in rows if r is not gone]
    width = len(others[0])
    if gone is parent:
        derived = tuple(sum(v[i] for v in others) for i in range(width))
    else:
        derived = tuple(others[0][i] - sum(v[i] for v in others[1:]) for i in range(width))
    skip = {2} if name == "ballots" else set()
    target = getattr(gone, name)
    fits = [o for o in target.options if all(o[i] == derived[i] for i in range(width) if i not in skip)]
    if fits or not target.options:
        target.values = fits[0] if fits else derived
        target.derived = not fits
        if target.row:
            target.boxes = boxes(target.row, target.table, target.values)


def disagreements(node: Node) -> list[str]:
    """Where a row's figures differ from the sum of the rows nested under it."""
    found: list[str] = []
    for item in walk(node):
        if not item.children:
            continue
        for name, values, parts in (
            ("votes", item.votes.values, [c.votes.values for c in item.children]),
            ("ballots", item.ballots.values, [c.ballots.values for c in item.children]),
        ):
            if not values or any(not p for p in parts):
                found.append(f"{item.label}: {name} unsettled")
                continue
            for index, (value, total) in enumerate(zip(values, map(sum, zip(*parts, strict=True)), strict=True)):
                if value != total and not (name == "ballots" and index == 2):
                    found.append(
                        f"{item.label} p{item.votes.page}: {name}[{index}] prints {value}, parts add to {total}"
                    )
    return found


def split_label(label: str) -> tuple[int, str] | None:
    """A district row's number and name, as one reading printed them: "12. Kbh. Bispebjerg"."""
    found = re.match(r"^[\s\"'|]*([\d/SIlO ]{1,5})[.,]\s*(.*)$", label)
    if not found:
        return None
    number = re.sub(r"\D", "", re.sub(r"\s", "", found.group(1)).translate(CONFUSED))
    name = re.sub(r"\s+[\d.,]*\d[\d.,]*$", "", found.group(2)).strip(" \"'|")
    return (int(number), name) if number and len(number) <= 2 else None


def run_of(name: str) -> str:
    """Copenhagen's districts are numbered once for the city and once for Frederiksberg."""
    return "Frb." if name.startswith("Frb.") else ""


def numbered(readings: list[list[int]], runs: list[str]) -> list[int]:
    """The numbers of one county's districts: each is one a reading printed, and each run counts up."""
    best: dict[tuple[int, str], tuple[int, list[int]]] = {(0, "start"): (0, [])}
    for index, options in enumerate(readings):
        step: dict[tuple[int, str], tuple[int, list[int]]] = {}
        for (last, run), (cost, chosen) in best.items():
            for number in set(options):
                if run == runs[index] and number <= last:
                    continue
                price = cost + sum(n != number for n in options)
                state = (number, runs[index])
                if state not in step or price < step[state][0]:
                    step[state] = (price, [*chosen, number])
        best = step
    return min(best.values(), key=lambda item: item[0])[1]


def districts(volume: str) -> list[tuple[str, int, str, Node]]:
    """Each nomination district with its county and number, in the order the volume prints them."""
    found: list[tuple[str, int, str, Node]] = []
    county = ""
    pending: list[Node] = []

    def flush() -> None:
        if not pending:
            return
        readings: list[list[int]] = []
        names: list[str] = []
        for node in pending:
            splits = [split_label(label) for label in (*node.votes.labels, *node.ballots.labels)]
            readings.append([s[0] for s in splits if s])
            names.append(Counter(s[1] for s in splits if s).most_common(1)[0][0] if any(splits) else "")
        runs = [run_of(name) for name in names]
        for node, number, name in zip(pending, numbered(readings, runs), names, strict=True):
            found.append((county, number, name, node))
        pending.clear()

    for node in walk(tree(volume)):
        if node.kind == "kreds":
            flush()
            county = node.label
        elif node.kind == "district":
            pending.append(node)
    flush()
    return found
