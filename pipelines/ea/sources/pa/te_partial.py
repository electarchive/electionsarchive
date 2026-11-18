"""Panama's Tribunal Electoral: the partial elections of 1999 (representatives) and 2014 (deputies, mayors,
representatives), from the Tribunal's statistical tables."""

import functools
import html
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pa import te
from ea.store import DATA

SOURCE = "pa.te_partial"
SITE = te.SITE
INBOX = DATA / "inbox" / SOURCE
UPLOADS = SITE + "/wp-content/uploads/2017/07/"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = te.VOTES, te.REGISTERED, te.CAST, te.VALID, te.INVALID, te.BLANK

# The party columns of the 1999 tables, in print order, and of the 2014 tables of December.
COLUMNS_1999 = ["PRD", "PDC", "MOLIRENA", "ARN", "MPE", "REN CIV", "SOL", "MORENA", "PNP", "LIB", "LIB NAL", "CAM DEM",
                "CHOCOLATE", "AZUL OSCURO", "GRIS OSCURO"]  # fmt: skip
COLUMNS_2014 = ["PRD", "PP", "MOLIRENA", "PAN", "CAM DEM", "FAD", "CELESTE", "VERDE", "CHOCOLATE"]
MESA_COLUMNS = ["PRD", "POPULAR", "MOLIRENA", "PANAMEÑISTA", "CD"]
# Labels te knows a party by, for the column labels it spells differently.
PARTY_LABEL = {"PP": "POPULAR", "PANAMEÑISTA": "PAN"}

DOWNLOAD = te.DOWNLOAD
FILES_1999 = {date(1999, 8, 15): ("6970", "6971"), date(1999, 8, 22): ("6972", "6973")}
FILES_2014 = {
    "mesas": "Cuadro-18-Elecciones-Parciales-Circuito-7-1-del-16-de-Noviembre.pdf",
    "6.1": "Cuadro-29-Elecciones-Parciales-Circuito-6-1-del-21-de-Diciembre.pdf",
    "9.1": "Cuadro-30-Elecciones-Parciales-Circuito-9-1-del-21-de-Diciembre.pdf",
    "9.2": "Cuadro-31-Elecciones-Parciales-Circuito-9-2-del-21-de-Diciembre.pdf",
    "santiago": "Cuadro-32-Elecciones-Parciales-Santiago-del-21-de-Diciembre.pdf",
    "la chorrera": "Cuadro-33-Elecciones-Parciales-La-Chorrera-del-21-de-Diciembre.pdf",
    "remedios:el puerto": "Cuadro-35-Elecciones-Parciales-El-Puerto-del-21-de-Diciembre.pdf",
    "tole:quebrada de piedra": "Cuadro-36-Elecciones-Parciales-Quebrada-de-Piedra-del-21-de-Diciembre.pdf",
    "la chorrera:playa leona": "Cuadro-37-Elecciones-Parciales-Playa-Leona-del-21-de-Diciembre.pdf",
}


def url_1999(number: str) -> str:
    return DOWNLOAD + number


def url_2014(name: str) -> str:
    return UPLOADS + name


def all_urls() -> list[str]:
    return [
        *(url_1999(n) for numbers in FILES_1999.values() for n in numbers),
        *(url_2014(n) for n in FILES_2014.values()),
    ]


def fetch() -> None:
    """Load the files that were saved from the Tribunal's site (data/inbox/pa.te_partial/) into the raw cache."""
    index = json.loads((INBOX / "index.json").read_text())
    for key, entry in index.items():
        raw.store(
            SOURCE,
            entry.get("source", key),
            (INBOX / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


# PDF words -------------------------------------------------------------------------------------------------------

WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)</word>')
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
DECIMAL = re.compile(r"\d+\.\d+")


@dataclass(frozen=True)
class Word:
    x0: float
    x1: float
    text: str

    @property
    def count(self) -> int | None:
        return int(self.text.replace(",", "")) if NUMBER.fullmatch(self.text) else None

    @property
    def numeric(self) -> bool:
        return NUMBER.fullmatch(self.text) is not None or DECIMAL.fullmatch(self.text) is not None


@dataclass
class Line:
    page: int
    y: float
    words: list[Word]

    @property
    def label(self) -> str:
        found = []
        for w in self.words:
            if w.numeric:
                break
            found.append(w.text)
        return " ".join(found)

    @property
    def numbers(self) -> list[Word]:
        return [w for w in self.words if w.numeric]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@functools.cache
def pdf_lines(url: str) -> list[Line]:
    """The words of a PDF with their positions, grouped into lines page by page."""
    path = raw.cached(SOURCE, url).path
    xml = subprocess.run(["pdftotext", "-bbox-layout", str(path), "-"], capture_output=True, check=True).stdout
    out: list[Line] = []
    for page, body in enumerate(xml.decode("utf-8").split("<page ")[1:]):
        words = sorted(((float(y), float(x0), float(x1), html.unescape(t)) for x0, y, x1, t in WORD.findall(body)))
        for y, x0, x1, t in words:
            if out and out[-1].page == page and abs(out[-1].y - y) <= 3.0:
                out[-1].words.append(Word(x0, x1, t))
            else:
                out.append(Line(page, y, [Word(x0, x1, t)]))
    for line in out:
        line.words.sort(key=lambda w: w.x0)
    return out


def nearest(anchors: list[float], x: float, tolerance: float = 9.0) -> int:
    best = min(range(len(anchors)), key=lambda i: abs(anchors[i] - x))
    if abs(anchors[best] - x) > tolerance:
        raise ValueError(f"a figure at {x} lies in no column")
    return best


def assign(anchors: list[float], labels: list[str], words: list[Word], where: str) -> dict[str, int]:
    """Put each figure under the column whose right edge it shares."""
    out: dict[str, int] = {}
    for w in words:
        label = labels[nearest(anchors, w.x1)]
        if label in out or w.count is None:
            raise ValueError(f"{where}: two figures or a non-count under {label}: {w.text}")
        out[label] = w.count
    return out


# Tables ----------------------------------------------------------------------------------------------------------


@dataclass
class Row:
    reg: int | None = None
    cast: int | None = None
    valid: int | None = None
    blank: int | None = None
    null: int | None = None
    votes: dict[str, int] = field(default_factory=dict)


@dataclass
class Candidate:
    name: str
    total: int
    votes: dict[str, int]


@dataclass
class Contest:
    office: str  # representative, mayor, or deputy
    scheme: str  # pa.corregimiento, pa.distrito, or pa.circuito
    code: str  # the area's code in the Tribunal's general-election geography
    label: str
    row: Row
    candidates: list[Candidate] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)  # (note kind, text) pairs


@dataclass
class Mesa:
    number: str
    row: Row
    kind: str = "standard"
    label: str = ""


@dataclass
class Election:
    day: date
    files: list[str]
    contests: list[Contest] = field(default_factory=list)
    mesas: dict[str, list[Mesa]] = field(default_factory=dict)  # circuit code -> polling-table units
    printed: Row | None = None  # the national Total row the 1999 tables print
    context: str = ""


def counts(line: Line, where: str) -> list[int]:
    out = [w.count for w in line.numbers if not DECIMAL.fullmatch(w.text)]
    if any(c is None for c in out):
        raise ValueError(f"{where}: {line.text}")
    return [int(c) for c in out if c is not None]


def title(name: str) -> str:
    """A printed candidate name in capitals as a name: the first letter of every word, a nickname in quotes included."""
    words = []
    for i, word in enumerate(name.split()):
        low = word.lower()
        if i and low in te.SMALL:
            words.append(low)
            continue
        letters = [j for j, c in enumerate(low) if c.isalpha()]
        words.append(low[: letters[0]] + low[letters[0]].upper() + low[letters[0] + 1 :] if letters else word)
    return " ".join(words)


def row_of(tokens: list[Word], labels: list[str], anchors: list[float], where: str, reg: bool = True) -> Row:
    """A row of the 1999 corregimiento tables: eight figures of centers, tables, and turnout, then the party columns
    (blank where a party had no candidate), then blank votes with their share and null votes with theirs."""
    plain = [w for w in tokens if not DECIMAL.fullmatch(w.text)]
    if len(tokens) < 12 or len(plain) != len(tokens) - 4:
        raise ValueError(f"{where}: {[w.text for w in tokens]}")
    row = Row(
        reg=plain[3].count, cast=plain[4].count, valid=plain[5].count, blank=plain[-2].count, null=plain[-1].count
    )
    row.votes = assign(anchors, labels, plain[6:-2], where)
    return row


def table_1999(url: str) -> tuple[Row, dict[tuple[str, str], tuple[str, Row]]]:
    """The party table of a 1999 partial election: the national Total row and each corregimiento's row, keyed by
    province and corregimiento with the district."""
    lines = pdf_lines(url)
    total = next(line for line in lines if line.label == "TOTAL" and line.numbers)
    anchors = [w.x1 for w in total.numbers if not DECIMAL.fullmatch(w.text)][6:-2]
    if len(anchors) != len(COLUMNS_1999):
        raise ValueError(f"{url}: {len(anchors)} party columns in the Total row")
    grand = row_of(total.numbers, COLUMNS_1999, anchors, "total")
    rows: dict[tuple[str, str], tuple[str, Row]] = {}
    province = district = ""
    for line in lines[lines.index(total) + 1 :]:
        if re.match(r"\d/:|FUENTE", line.text):
            break
        if not line.numbers:
            if line.label.isupper():
                province, district = line.label, ""
            else:
                district = line.label
            continue
        name = re.sub(r"\s+\d/$", "", line.label)
        rows[(te.place_key(province), te.place_key(name))] = (
            district,
            row_of(line.numbers, COLUMNS_1999, anchors, name),
        )
    return grand, rows


# The right edge of a figure lies this far right of the centre of its column header on the Tribunal's tables.
FIGURE_OFFSET = 18.3
HEADER_1999 = ["PRD", "PDC", "ARN", "MPE", "SOL", "MORENA", "PNP", "LIB"]


def candidates_1999(url: str) -> tuple[dict[tuple[str, str], list[Candidate]], set[tuple[str, str]]]:
    """The candidates of a 1999 partial election by corregimiento, each with the votes under every party column, and
    the corregimientos the table marks as tied."""
    lines = pdf_lines(url)
    out: dict[tuple[str, str], list[Candidate]] = {}
    tied: set[tuple[str, str]] = set()
    province = place = ""
    # The header repeats on every page; each page's candidates use that page's columns.
    page_anchors: dict[int, list[float]] = {}
    for i, line in enumerate(lines):
        if line.words[0].text == "CORREGIMIENTO" and "TOTAL" in line.text:
            page_anchors[line.page] = header_anchors(lines, i)
    for line in lines:
        if line.page not in page_anchors or line.y < 140 or re.match(r"FUENTE|TOTAL-", line.text):
            continue
        if not line.numbers:
            if line.label == "EMPATE":
                tied.add((province, place))
            elif line.label.isupper():
                province, place = te.place_key(line.label), ""
            else:
                place = te.place_key(line.label)
            continue
        name = html.unescape(line.label)
        total, *rest = line.numbers
        votes = assign(page_anchors[line.page], COLUMNS_1999, rest, f"{province}/{place}/{name}")
        out.setdefault((province, place), []).append(Candidate(title(name), int(total.count or 0), votes))
    return out, tied


def header_anchors(lines: list[Line], index: int) -> list[float]:
    """Right edges of the party columns under a page header of the candidate table: each header cell's centre plus the
    offset the page's figures keep from it."""
    near = [line for line in lines[index - 1 : index + 6] if line.page == lines[index].page]
    centres: dict[str, float] = {}
    prd = next(line for line in near if any(w.text == "PRD" for w in line.words))
    for w in prd.words:
        if w.text in HEADER_1999:
            centres[w.text] = (w.x0 + w.x1) / 2
    for line in near:
        for w in line.words:
            if line is prd:
                continue
            key = {"MOLI-": "MOLIRENA", "REN": "REN CIV", "LIB": "LIB NAL", "CAM": "CAM DEM", "CHOCO-": "CHOCOLATE",
                   "AZUL": "AZUL OSCURO", "GRIS": "GRIS OSCURO"}.get(w.text)  # fmt: skip
            if key and key not in centres:
                centres[key] = (w.x0 + w.x1) / 2
    if set(centres) != set(COLUMNS_1999):
        raise ValueError(f"header columns found: {sorted(centres)}")
    return [centres[label] + FIGURE_OFFSET for label in COLUMNS_1999]


def load_1999(day: date) -> Election:
    first, second = FILES_1999[day]
    table, people = url_1999(first), url_1999(second)
    e = Election(day, [table, people])
    grand, rows = table_1999(table)
    e.printed = grand
    found, tied = candidates_1999(people)
    for (province, place), (district, row) in rows.items():
        code = f"{te.place_key(district)}:{place}"
        label = f"{te.pretty(place_name(table, place))}, {te.pretty(district)}"
        c = Contest("representative", "pa.corregimiento", code, label, row, found.pop((province, place)))
        if (province, place) in tied:
            c.notes.append(("caveat", "The Tribunal's table records the vote in this corregimiento as a tie."))
        e.contests.append(c)
    if found:
        raise ValueError(f"candidates without a party row: {sorted(found)}")
    for c in e.contests:
        if c.code == "los pozos:capuri":
            c.notes.append(("caveat", CAPURI))
    return e


CAPURI = "The Tribunal's table adds that the figures include the votes recognized from polling tables 1989 and 1991."


@functools.cache
def names_1999(url: str) -> dict[str, str]:
    """The printed spelling of every corregimiento in a 1999 table, by key."""
    out: dict[str, str] = {}
    for line in pdf_lines(url):
        if line.numbers and line.y > 130 and line.label != "TOTAL":
            out[te.place_key(re.sub(r"\s+\d/$", "", line.label))] = re.sub(r"\s+\d/$", "", line.label)
    return out


def place_name(url: str, key: str) -> str:
    return names_1999(url)[key]


# 2014 ------------------------------------------------------------------------------------------------------------


def table_2014(url: str) -> tuple[Line, Row, list[Candidate], list[str]]:
    """A December 2014 table: the total line, the row of the unit, its candidates, and the notes it prints."""
    lines = pdf_lines(url)
    head = next(i for i, line in enumerate(lines) if "CANDIDATO" in line.text)
    body = lines[head + 1 :]
    total = next(line for line in body if len(line.numbers) >= 14)
    figures = [w for w in total.numbers if not DECIMAL.fullmatch(w.text)]
    if len(figures) != 5 - 1 + len(COLUMNS_2014) + 2 - 0:
        raise ValueError(f"{url}: {len(figures)} figures in the total line")
    anchors = [w.x1 for w in figures[4 : 4 + len(COLUMNS_2014)]]
    row = Row(reg=figures[0].count, cast=figures[1].count, valid=figures[4 - 1].count, blank=figures[-2].count,
              null=figures[-1].count)  # fmt: skip
    row.votes = assign(anchors, COLUMNS_2014, figures[4 : 4 + len(COLUMNS_2014)], "total")
    people: list[Candidate] = []
    notes: list[str] = []
    for line in body[body.index(total) + 1 :]:
        if line.text.startswith("NOTA"):
            notes.append(line.text)
            continue
        if line.text.startswith(("FUENTE", "FECHA")):
            continue
        if not line.numbers:
            continue
        name = html.unescape(line.label)
        first, *rest = line.numbers
        votes = assign(anchors, COLUMNS_2014, rest, name)
        people.append(Candidate(title(name), int(first.count or 0), votes))
    return total, row, people, notes


# What each note of a December table says, by the Spanish text it must contain.
NOTES_2014 = {
    "EL PRD YA CONTABA CON LA CURUL DE RUBÉN DE LEÓN": "The PRD already held the seat of Rubén de León.",
    "RENUNCIÓ LA CANDIDATA ESMERALDA LÓPEZ POR EL FAD": "Candidate Esmeralda López withdrew from the FAD ticket.",
    "POSTULADOS LOS CANDIDATOS MÁS VOTADO POR PARTIDO POLÍTICO SEGÚN ELECCIONES DEL 4 DE MAYO DE 2014": (
        "The parties nominated their most-voted candidates in the election of 4 May 2014."
    ),
    "RENUNCIÓ EL CANDIDATO JONNY HERNÁNDEZ POR LA LIBRE POSTULACIÓN CELESTE": (
        "Candidate Jonny Hernández withdrew from the Celeste free-nomination line."
    ),
    "RENUNCIÓ EL CANDIDATO EDGAR BÓSQUEZ POR LOS PARTIDOS POPULAR Y PANAMEÑISTA": (
        "Candidate Edgar Bósquez withdrew from the Popular and Panameñista parties' ticket."
    ),
    "EL CANDIDATO AMETH OLMOS RENUNCIÓ A LA LIBRE POSTULACIÓN VERDE Y EDWIN CÁRDENAS POR LOS PARTIDOS POPULAR Y "
    "PANAMEÑISTA": (
        "Candidate Ameth Olmos withdrew from the Verde free-nomination line, and Edwin Cárdenas from the Popular and "
        "Panameñista parties' ticket."
    ),
    "RENUNCIÓ LA CANDIDATA CARMÉN MELÉNDEZ POR PRD": "Candidate Carmén Meléndez withdrew from the PRD ticket.",
    "RENUNCIARON LOS CANDIDATOS OSVALDO GALLARDO POR EL MOLIRENA Y MABEL BATISTA POR EL PARTIDO PANAMEÑISTA": (
        "Candidates Osvaldo Gallardo of MOLIRENA and Mabel Batista of the Panameñista party withdrew."
    ),
}


def english(notes: list[str]) -> list[str]:
    out = []
    for note in notes:
        text = " ".join(note.removeprefix("NOTA:").replace(".", " ").split()).rstrip(".")
        hits = [said for key, said in NOTES_2014.items() if key in text]
        if len(hits) != 1:
            raise ValueError(f"no translation for the note {note!r}")
        out.append(hits[0])
    return out


CONTESTS_2014 = {
    "6.1": ("deputy", "pa.circuito", "6.1", "circuit 6.1"),
    "9.1": ("deputy", "pa.circuito", "9.1", "circuit 9.1"),
    "9.2": ("deputy", "pa.circuito", "9.2", "circuit 9.2"),
    "santiago": ("mayor", "pa.distrito", "santiago", "Santiago"),
    "la chorrera": ("mayor", "pa.distrito", "la chorrera", "La Chorrera"),
    "remedios:el puerto": ("representative", "pa.corregimiento", "remedios:el puerto", "El Puerto, Remedios"),
    "tole:quebrada de piedra": ("representative", "pa.corregimiento", "tole:quebrada de piedra",
                                "Quebrada de Piedra, Tolé"),
    "la chorrera:playa leona": ("representative", "pa.corregimiento", "la chorrera:playa leona",
                                "Playa Leona, La Chorrera"),
}  # fmt: skip


def load_december_2014() -> Election:
    e = Election(date(2014, 12, 21), [])
    for name, (office, scheme, code, label) in CONTESTS_2014.items():
        url = url_2014(FILES_2014[name])
        e.files.append(url)
        _, row, people, notes = table_2014(url)
        c = Contest(office, scheme, code, label, row, people, [("context", n) for n in english(notes)])
        e.contests.append(c)
    e.context = (
        "The Tribunal holds these partial elections after the general election of 4 May 2014: three deputy seats by "
        "circuit, two mayoralties, and three representatives of corregimientos."
    )
    return e


def load_november_2014() -> Election:
    url = url_2014(FILES_2014["mesas"])
    e = Election(date(2014, 11, 16), [url])
    lines = pdf_lines(url)
    names = ["PRD", "POPULAR", "MOLIRENA", "PANAMEÑISTA", "CD"]

    def row_from(figures: list[int]) -> Row:
        reg, cast, _, valid, *parties, blank, null = figures
        return Row(
            reg=reg, cast=cast, valid=valid, blank=blank, null=null, votes=dict(zip(names, parties, strict=True))
        )

    total: Row | None = None
    earlier: Row | None = None
    mesas: list[Mesa] = []
    for line in lines:
        figures = [w for w in line.numbers if not DECIMAL.fullmatch(w.text)]
        if "mesas escrutadas el 4 de Mayo" in line.text:
            earlier = row_from([int(w.count or 0) for w in figures[-11:]])
        elif len(figures) == 13 and line.words[0].text == figures[0].text == "113" and total is None:
            total = row_from([int(w.count or 0) for w in figures[2:]])
        elif len(figures) == 13 and line.y > 140:
            mesas.append(Mesa(figures[1].text, row_from([int(w.count or 0) for w in figures[2:]])))
    if total is None or earlier is None or not mesas:
        raise ValueError("the table of polling tables did not parse")
    for column in (*names, "reg", "cast", "valid", "blank", "null"):
        parts = [(m.row.votes if column in names else vars(m.row))[column] for m in mesas]
        whole = (total.votes if column in names else vars(total))[column]
        before = (earlier.votes if column in names else vars(earlier))[column]
        if sum(parts) + before != whole:
            raise ValueError(f"the polling tables do not add up to the circuit in {column}")
    e.contests.append(Contest("deputy", "pa.circuito", "7.1", "circuit 7.1", total))
    e.mesas["7.1"] = [Mesa("4 May", earlier, "aggregate", "the 98 polling tables counted on 4 May 2014"), *mesas]
    e.context = (
        "The Tribunal's table of circuit 7.1 includes the partial results of the 15 contested polling tables of the "
        "deputy election, which voted again on 16 November 2014. It also prints the results of the 98 tables "
        "counted on 4 May 2014, and the circuit's 113 tables together."
    )
    return e


def elections() -> list[Election]:
    return [load_1999(date(1999, 8, 15)), load_1999(date(1999, 8, 22)), load_november_2014(), load_december_2014()]


# Checks ----------------------------------------------------------------------------------------------------------


def verify(e: Election) -> list[str]:
    """Where a table disagrees with itself: candidates against party columns, columns against valid votes."""
    issues: list[str] = []
    for c in e.contests:
        if sum(c.row.votes.values()) != c.row.valid:
            issues.append(f"{e.day} {c.code}: party columns add to {sum(c.row.votes.values())}, valid {c.row.valid}")
        if (c.row.valid or 0) + (c.row.blank or 0) + (c.row.null or 0) != c.row.cast:
            issues.append(f"{e.day} {c.code}: valid, blank, and null votes do not add up to the votes cast")
        if not c.candidates:
            continue
        summed: dict[str, int] = {}
        for who in c.candidates:
            if sum(who.votes.values()) != who.total:
                issues.append(
                    f"{e.day} {c.code}: {who.name} has {who.total} votes, columns add to {sum(who.votes.values())}"
                )
            for label, value in who.votes.items():
                summed[label] = summed.get(label, 0) + value
        printed = {label: value for label, value in c.row.votes.items() if label in summed or value}
        if summed != printed:
            issues.append(f"{e.day} {c.code}: candidates' columns {summed} differ from the party row {printed}")
    return issues


def compare(e: Election) -> list[str]:
    """The sum of a table's rows against the national Total row the Tribunal prints, where it prints one."""
    if e.printed is None:
        return []
    out = []
    for name in ("reg", "cast", "valid", "blank", "null"):
        got = sum(getattr(c.row, name) or 0 for c in e.contests)
        out.append(f"{e.day} {name}: rows {got:,}, printed {getattr(e.printed, name):,}")
    for label, value in e.printed.votes.items():
        got = sum(c.row.votes.get(label, 0) for c in e.contests)
        out.append(f"{e.day} {label}: rows {got:,}, printed {value:,}")
    return out


# Packages --------------------------------------------------------------------------------------------------------

PANAMA = te.PANAMA
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"


def _key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = _key("source", SOURCE)
MESA = "pa.mesa"
METHOD = (
    "Counted by the Tribunal Electoral and printed in its statistical tables of the partial elections. Registered "
    "voters are the electoral roll, ballots cast are the votes issued, valid votes are the votes for parties and "
    "free-nomination lines, and blank and null votes are printed separately. Candidates run under one party or "
    "several, and the tables give each candidate's votes under every party column."
)
MESA_METHOD = (
    "The Tribunal's table of circuit 7.1 lists each of the 15 polling tables that voted again on 16 November 2014, "
    "and the total of the 98 tables counted on 4 May 2014, which together make the circuit's 113 tables."
)
OFFICES = {
    "representative": ("Representative of {}", "Representante de {}", "representatives"),
    "mayor": ("Mayor of {}", "Alcalde de {}", "mayors"),
    "deputy": ("Deputy, {}", "Diputado, {}", "deputies"),
}


def geo() -> Package:
    p = Package(
        GEO, SOURCE, "Panama's polling tables, the finest unit of the Tribunal Electoral's partial-election tables"
    )
    p.external.add(PANAMA)
    p.add("scheme", code=MESA, polity_id=PANAMA, kind="precinct", rank=4, noun="polling table", code_system=None)
    return p


def registry() -> Package:
    p = Package(
        REGISTRY, SOURCE, "The Tribunal Electoral's tables of Panama's partial elections as a source", depends=[GEO]
    )
    p.external.add(PANAMA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Tribunal Electoral de Panamá",
        title="Resultados de las elecciones parciales, cuadros estadísticos",
        url=SITE,
        license="Publicly posted official results; no license stated",
        license_url=SITE,
        attribution="Tribunal Electoral de Panamá",
    )
    for url in all_urls():
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    return p


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/{e.day}" for e in elections())]


def option_label(label: str) -> str:
    return PARTY_LABEL.get(label, label)


def election(e: Election) -> Package:
    p = Package(
        f"{SOURCE}/{e.day}",
        SOURCE,
        f"The {e.day:%-d %B %Y} partial elections of Panama from the Tribunal Electoral's tables",
        depends=[GEO, REGISTRY, te.SOURCE + "/geo", te.SOURCE + "/registry"],
    )
    p.external.add(PANAMA)
    for url in e.files:
        p.uses(raw.cached(SOURCE, url))
    eid = _key("election", e.day)
    kinds = sorted({c.office for c in e.contests})
    what = {
        "representative": "representatives",
        "mayor": "mayors",
        "deputy": "deputies",
    }
    named = " and ".join(what[k] for k in kinds)
    p.add("election", id=eid, polity_id=PANAMA, kind="by_election", date_from=e.day, date_to=e.day, status="held")
    p.name(eid, f"Panamanian partial elections of {e.day:%-d %B %Y}", "en", "official")
    p.name(
        eid, f"Elecciones parciales del {e.day.day} de {SPANISH[e.day.month]} de {e.day.year}", "es", "native", False
    )
    path = f"pa/{e.day}-by-election"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    context = e.context or (
        f"The Tribunal's tables of the partial elections of {named} held on {e.day:%-d %B %Y}, by corregimiento."
    )
    p.add("note", id=_key("note", eid), entity_id=eid, kind="context", body_md=context, lang="en")
    units: dict[tuple[str, str], str] = {}

    def unit(scheme: str, code: str) -> str:
        if (scheme, code) not in units:
            uid = units[(scheme, code)] = _key("unit", e.day, scheme, code)
            p.add(
                "reporting_unit",
                id=uid,
                polity_id=PANAMA,
                scheme_code=scheme,
                code=f"{e.day}:{code}",
                area_relation="equals",
                area_id=te.area(scheme, code),
                kind="standard",
            )
        return units[(scheme, code)]

    def values(rid: str, uid: str, row: Row, options: dict[str, str]) -> None:
        for measure, value in (
            (REGISTERED, row.reg),
            (CAST, row.cast),
            (VALID, row.valid),
            (INVALID, row.null),
            (BLANK, row.blank),
        ):
            if value is not None:
                p.add("result_value", result_set_id=rid, unit_id=uid, option_id=None, channel=0, measure=measure,
                      count_round=0, flags=0, value=value)  # fmt: skip
        for label, value in row.votes.items():
            p.add("result_value", result_set_id=rid, unit_id=uid, option_id=options[label], channel=0, measure=VOTES,
                  count_round=0, flags=0, value=value)  # fmt: skip

    def result_set(cid: str, tag: str, default: bool, method: str) -> str:
        rid = _key("result_set", e.day, tag)
        p.add("result_set", id=rid, election_id=eid, contest_id=cid, stage="official", kind="counted",
              source_id=SOURCE_ID, status="active", is_default=default, completeness=1.0)  # fmt: skip
        p.add("note", id=_key("note", rid), entity_id=rid, kind="methodology", body_md=method, lang="en")
        return rid

    for c in e.contests:
        parts = (c.office, c.scheme, c.code)
        en, es, words = OFFICES[c.office]
        cid = _key("contest", e.day, *parts)
        fields: dict[str, object]
        if c.office == "deputy":
            fields = {"kind": "body_seats", "body_id": te.BODY_ASSEMBLY}
        else:
            fields = {
                "kind": "office",
                "office_id": te.REPRESENTATIVE if c.office == "representative" else te.MAYORALTY,
                "magnitude": 1,
                "electoral_system_id": te.SYSTEM_PLURALITY,
            }
        p.add("contest", id=cid, election_id=eid, district_area_id=te.area(c.scheme, c.code), round_number=1,
              default_result_kind="counted", **fields)  # fmt: skip
        p.name(cid, en.format(c.label), "en", "official")
        p.name(cid, es.format(c.label), "es", "native", False)
        p.add("slug", path=f"{path}/{words}/{changeset.slug(c.label)}", entity_id=cid, is_current=True)
        rid = result_set(cid, "/".join(parts), True, METHOD)
        options: dict[str, str] = {}
        for position, label in enumerate(c.row.votes, 1):
            oid = options[label] = _key("option", cid, te.norm(label))
            if te.independent(label):
                p.add("contest_option", id=oid, contest_id=cid, kind="independents", ballot_position=position,
                      ballot_label=te.pretty(label))  # fmt: skip
            else:
                p.add("contest_option", id=oid, contest_id=cid, kind="list", party_id=te.party_id(option_label(label)),
                      ballot_position=position, ballot_label=label)  # fmt: skip
        uid = unit(c.scheme, c.code)
        values(rid, uid, c.row, options)
        for kind, text in c.notes:
            target = rid if kind == "caveat" else cid
            p.add("note", id=_key("note", target, kind, text), entity_id=target, kind=kind, body_md=text, lang="en")
        for who in c.candidates:
            main = max(who.votes, key=lambda label: who.votes[label])
            tag = (e.day, *parts, te.norm(who.name))
            person, candidacy = _key("person", *tag), _key("candidacy", *tag)
            p.add("person", id=person)
            p.name(person, who.name, "es", "official")
            p.add("candidacy", id=candidacy, person_id=person, option_id=options[main], role="head",
                  ballot_name=who.name, is_independent=te.independent(main))  # fmt: skip
            figures = ", ".join(f"{label} {value:,}" for label, value in who.votes.items())
            body = (
                f"Received {who.total:,} votes ({figures})." if len(who.votes) > 1 else f"Received {who.total:,} votes."
            )
            p.add("note", id=_key("note", candidacy), entity_id=candidacy, kind="context", body_md=body, lang="en")
        if c.code in e.mesas:
            fine = result_set(cid, "/".join(parts) + "/mesa", False, MESA_METHOD)
            for mesa in e.mesas[c.code]:
                code = mesa.number if mesa.kind == "standard" else "counted-2014-05-04"
                mid = _key("unit", e.day, MESA, code)
                p.add("reporting_unit", id=mid, polity_id=PANAMA, scheme_code=MESA, code=f"{e.day}:{code}",
                      parent_id=uid, area_relation="within", area_id=te.area(c.scheme, c.code), kind=mesa.kind)  # fmt: skip
                values(fine, mid, mesa.row, options)
                if mesa.label:
                    p.add(
                        "note",
                        id=_key("note", mid),
                        entity_id=mid,
                        kind="context",
                        body_md=mesa.label[0].upper() + mesa.label[1:] + ".",
                        lang="en",
                    )
    return p


SPANISH = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"]  # fmt: skip


def parse() -> None:
    found = elections()
    issues = [issue for e in found for issue in verify(e)]
    if issues:
        raise ValueError("\n".join(issues))
    for e in found:
        for line in compare(e):
            print(line, flush=True)
    geo().write()
    registry().write()
    for e in found:
        election(e).write()


def emit() -> None:
    package.run("load", packages())
