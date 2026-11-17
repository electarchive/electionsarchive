import csv
import functools
import io
import re
import subprocess
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import fastexcel

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.bmi"
BASE = "https://www.bmi.gv.at/412"


@dataclass(frozen=True)
class Edition:
    """One election the Federal Ministry of the Interior publishes, and the file that holds its results by municipality."""

    key: str
    family: str
    day: date
    page: str
    file: str
    member: str | None = None
    sheet: str | int = 0
    round: int = 1


NR = "nationalratswahlen"
EU = "europawahlen"
BP = "bundespraesidentenwahlen"
VB = "volksbefragungen"
EDITIONS = [
    Edition(
        "nr-1995",
        NR,
        date(1995, 12, 17),
        "nationalratswahl_1995_wiederholungswahl_1996",
        "nrw95_idf_whg1996_endergebnis.pdf",
    ),
    Edition("nr-1999", NR, date(1999, 10, 3), "nationalratswahl_1999", "nrwergebnis1999.zip", "NRW1999.xls"),
    Edition("nr-2002", NR, date(2002, 11, 24), "nationalratswahl_2002", "nrw02_multi.zip", "NRW02_E_Gesamt.csv"),
    Edition("nr-2006", NR, date(2006, 10, 1), "nationalratswahl_2006", "nrw_06_endergebnise.xls"),
    Edition("nr-2008", NR, date(2008, 9, 28), "nationalratswahl_2008", "ergebnis_end.zip", "Ergebnis_end.xls"),
    Edition("nr-2013", NR, date(2013, 9, 29), "nationalratswahl_2013", "nrw13_endgueltiges_gesamtergebnis.xlsx"),
    Edition("nr-2017", NR, date(2017, 10, 15), "nationalratswahl_2017", "nrw17_endgueltiges_gesamtergebnis.xlsx"),
    Edition(
        "nr-2019", NR, date(2019, 9, 29), "nationalratswahl_2019", "endgultiges_gesamtergebnis_nrw19_16102019.xlsx"
    ),
    Edition(
        "nr-2024",
        NR,
        date(2024, 9, 29),
        "nationalratswahl_2024",
        "endgueltiges_ergebnis_beschluss_bundeswahlbehoerde_16102024.xlsx",
    ),
    Edition("eu-1996", EU, date(1996, 10, 13), "europawahl_1996", "euw_1996_endergebnis.xls", sheet="Gemeindeliste"),
    Edition("eu-1999", EU, date(1999, 6, 13), "europawahl_1999", "euw_1999_endergebnis.xls"),
    Edition("eu-2004", EU, date(2004, 6, 13), "europawahl_2004", "euwahl04_erge.zip", "euwahl04_ergE.xls"),
    Edition("eu-2009", EU, date(2009, 6, 7), "europawahl_2009", "ergebnis_e.zip", "Ergebnis_E.xls"),
    Edition(
        "eu-2014",
        EU,
        date(2014, 5, 25),
        "europawahl_2014",
        "endgueltiges_ergebnis_mit_briefwahl.zip",
        "Endgueltiges_Ergebnis_mit_Briefwahl.xlsx",
    ),
    Edition(
        "eu-2019",
        EU,
        date(2019, 5, 26),
        "europawahl_2019",
        "endgueltiges_ergebnis/endgueltiges_ergebnis_inkl_wk_mit_gemeindeergebnissen_ew19.xls",
    ),
    Edition("eu-2024", EU, date(2024, 6, 9), "europawahl_2024", "endgueltiges_ergebnis_europawahl_26062024.xlsx"),
    Edition("bp-1998", BP, date(1998, 4, 19), "bundespraesidentenwahl_1998", "bpw1998_endergebnis.xlsx", sheet="Liste"),
    Edition("vb-2013", VB, date(2013, 1, 20), "volksbefragung_2013", "ergebnistabelle_vb2013.xls"),
    Edition("bp-2004", BP, date(2004, 4, 25), "bundespraesidentenwahl_2004", "bpwahl04_erge.pdf"),
    Edition("bp-2010", BP, date(2010, 4, 25), "bundespraesidentenwahl_2010", "ergebnis_e_bpw_2010.xls"),
    Edition(
        "bp-2016-1",
        BP,
        date(2016, 4, 24),
        "bundespraesidentenwahl_2016",
        "endgueltiges_gesamtergebnis_bpw16_1wg.xlsx",
    ),
    Edition(
        "bp-2016-2",
        BP,
        date(2016, 5, 22),
        "bundespraesidentenwahl_2016",
        "endgueltiges_gesamtergebnis_bpw16_2wg.xlsx",
        round=2,
    ),
    Edition(
        "bp-2016-3",
        BP,
        date(2016, 12, 4),
        "bundespraesidentenwahl_2016",
        "endgueltiges_gesamtergebnis_bpw16_2wg_wh.xlsx",
        round=3,
    ),
    Edition(
        "bp-2022",
        BP,
        date(2022, 10, 9),
        "bundespraesidentenwahl_2022",
        "endgueltiges_gesamtergebnis_inkl_briefwahl_bpw22_20221028.xlsx",
    ),
]


def url(edition: Edition) -> str:
    if edition.family == VB:
        return f"https://www.bmi.gv.at/416/files/{edition.file}"
    return f"{BASE}/{edition.family}/{edition.page}/files/{edition.file}"


def fetch() -> None:
    from ea.sources.at import bmi_history

    bmi_history.fetch()
    for edition in EDITIONS:
        raw.fetch(
            SOURCE,
            url(edition),
            suffix=f".{edition.file.rsplit('.', 1)[1]}",
        )


def workbook(edition: Edition) -> fastexcel.ExcelReader:
    path = raw.cached(SOURCE, url(edition)).path
    if edition.member is None:
        return fastexcel.read_excel(path)
    with zipfile.ZipFile(path) as archive:
        return fastexcel.read_excel(archive.read(edition.member))


def _number(text: str) -> str:
    """Turn a German-formatted cell, such as 1.792.499 or 36,51 or a dash, into a plain number."""
    if text.strip() == "-":
        return "0"
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?|-?\d+,\d+", text.strip()):
        return text.strip().replace(".", "").replace(",", ".")
    return text


def _csv(edition: Edition) -> list[list[str | None]]:
    path = raw.cached(SOURCE, url(edition)).path
    assert edition.member is not None
    with zipfile.ZipFile(path) as archive:
        text = archive.read(edition.member).decode("cp1252")
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=";") if any(c.strip() for c in row)]
    width = max(len(row) for row in rows)
    return [
        [None if not c.strip() else " ".join(_number(c).split()) for c in [*row, *[""] * (width - len(row))]]
        for row in rows
    ]


def cells(edition: Edition) -> list[list[str | None]]:
    if edition.member is not None and edition.member.endswith(".csv"):
        return _csv(edition)
    book = workbook(edition)
    frame = book.load_sheet(edition.sheet, header_row=None, dtypes="string").to_polars()
    return [[None if c is None or not c.strip() else " ".join(c.split()) for c in row] for row in frame.iter_rows()]


PLAIN = {"registered", "cast", "invalid", "valid"}


@dataclass
class Line:
    code: str | None
    name: str
    registered: int | None
    cast: int | None
    valid: int | None
    invalid: int | None
    votes: dict[str, int]


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return round(float(text))
    except ValueError:
        return None


def _field(label: str) -> str | None:
    low = label.lower().replace("- ", "").replace("-", "")
    if "teiligung" in low or "beteilig" in low:
        return None
    if re.search(r"rechtigte", low):
        return "registered"
    if "abgegeb" in low:
        return "cast"
    if re.search(r"\bungültig|\bungueltig", low):
        return "invalid"
    if re.search(r"\bgültig|\bgueltig", low):
        return "valid"
    return None


def _code(text: str | None) -> str | None:
    """Excel reads a code like 3E99 as the number 3e99, so rebuild the printed code (3E999) from it."""
    if text is not None and len(text) > 20 and text.isdigit():
        return f"{round(int(text) / 10**99)}E999"
    return text


class Header:
    """Column positions for one block of a sheet; a few sheets repeat the header, with other parties, for each state."""

    def __init__(self, top: list[str | None], second: list[str | None]) -> None:
        self.labels = [
            " ".join(x for x in (top[i], second[i] if second[i] not in ("Stimmen", "WK", "%") else None) if x)
            for i in range(len(top))
        ]
        self.fields: dict[str, int] = {}
        for column, label in enumerate(self.labels):
            field = _field(label)
            if field is not None and field not in self.fields:
                self.fields[field] = column
        self.code = next(
            (i for i, c in enumerate(top) if c and (c.upper() == "GKZ" or re.fullmatch(r"B\d{4}", c))), None
        )
        self.name = next(i for i, c in enumerate(top) if c and c.upper().startswith("GEBIET"))
        self.parties = {
            column: self.labels[column]
            for column in range(max(self.fields.values()) + 1, len(top))
            if (text := top[column]) and not text.endswith("%") and text.upper() != "LEERFELD"
        }


def _is_header(row: list[str | None]) -> bool:
    return any(c and (c.upper() == "GKZ" or c.upper().startswith("GEBIET")) for c in row[:3])


PDF_COLUMNS = {
    "nr-1995": (["SPÖ", "ÖVP", "FPÖ", "GRÜNE", "LIF", "NEIN", "KPÖ", "ÖNP", "DBP"], 5),
    "bp-2004": (["Dr. Ferrero-Waldner", "Dr. Fischer"], 2),
}
NUMBER = re.compile(r"-?\d{1,3}(\.\d{3})+(,\d+)?|-?\d+(,\d+)?|-")


def _count(token: str) -> int | None:
    return None if token == "-" else int(token.replace(".", "").split(",")[0])


def _pdf(edition: Edition) -> tuple[list[str], list[Line]]:
    """Read a results table that the authority publishes as a PDF with a text layer, one printed row per line."""
    labels, pairs = PDF_COLUMNS[edition.key]
    plain = len(labels) - pairs
    width = 3 + 2 * pairs + plain
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url(edition)).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    body: list[Line] = []
    land = ""

    def read(tokens: list[str]) -> Line | None:
        nonlocal land
        if len(tokens) < width + 1 or not all(NUMBER.fullmatch(t) for t in tokens[-width:]):
            return None
        core, head = tokens[-width:], tokens[:-width]
        registered = None
        if len(head) >= 2 and re.fullmatch(r"\d{1,3}(\.\d{3})+|\d+", head[-2]) and re.fullmatch(r"\d+,\d+", head[-1]):
            registered, head = _count(head[-2]), head[:-2]
        elif len(head) >= 2 and head[-2:] == ["-", "-"]:
            head = head[:-2]
        code = head[0] if head and re.fullmatch(r"\d+[A-H]?\d*", head[0]) else None
        name = " ".join(head[1:] if code else head)
        if code is None and name.upper().startswith(("ÖSTERREICH", "OESTERREICH")):
            code = "0"
        elif code is None and name.lower().startswith("davon wahlkarten"):
            code = f"{land}9999"
        votes = {}
        for index, label in enumerate(labels):
            at = 3 + 2 * index if index < pairs else 3 + 2 * pairs + (index - pairs)
            if (value := _count(core[at])) is not None:
                votes[label] = value
        return Line(code, name, registered, _count(core[0]), _count(core[1]), _count(core[2]), votes)

    for row in text.splitlines():
        tokens = row.split()
        line = read(tokens)
        if line is not None and sum(line.votes.values()) != line.valid:
            # trailing columns that printed nothing for parties with no votes leave the row short
            for pad in range(1, plain):
                again = read([*tokens, *["0"] * pad])
                if again is not None and sum(again.votes.values()) == again.valid:
                    line = again
                    break
        if line is None:
            continue
        if line.code is not None and re.fullmatch(r"\d", line.code):
            land = line.code
        body.append(line)
    return labels, body


@functools.cache
def lines(edition: Edition) -> tuple[list[str], list[Line]]:
    """Read the printed rows of a results sheet: the party labels, then each row's totals and votes."""
    if edition.file.endswith(".pdf"):
        return _pdf(edition)
    grid = cells(edition)
    head = next(i for i, row in enumerate(grid) if _is_header(row))
    header: Header | None = None
    labels: list[str] = []
    body: list[Line] = []
    index = head
    while index < len(grid):
        row = grid[index]
        if _is_header(row):
            first = grid[index + 1][0] if index + 1 < len(grid) else ""
            banner = index + 1 < len(grid) and (first is None or not re.match(r"^G?\d", first))
            header = Header(row, grid[index + 1] if banner else [None] * len(row))
            labels.extend(label for label in header.parties.values() if label not in labels)
            index += 2 if banner else 1
            continue
        index += 1
        assert header is not None
        name = row[header.name]
        figures = {f: _int(row[c]) for f, c in header.fields.items()}
        if name is None and all(v is None for v in figures.values()):
            continue
        votes = {label: v for column, label in header.parties.items() if (v := _int(row[column])) is not None}
        code = _code(row[header.code]) if header.code is not None else None
        body.append(
            Line(
                code,
                name or "",
                figures.get("registered"),
                figures.get("cast"),
                figures.get("valid"),
                figures.get("invalid"),
                votes,
            )
        )
    return labels, body


@dataclass
class Leaf:
    """One finest published unit: a municipality, or a block of postal ballots counted above it."""

    kind: str
    code: str
    land: str
    line: Line


WAHLKARTEN = re.compile(r"wahlkarte|briefwahl", re.IGNORECASE)
UNASSIGNED = re.compile(r"nicht\s?zuordenbar", re.IGNORECASE)
STRATEGIES = {
    "all": ("sb_wk", "rwk_wk", "land_wk"),
    "sb": ("sb_wk",),
    "rwk": ("rwk_wk",),
    "sb+land": ("sb_wk", "land_wk"),
    "rwk+land": ("rwk_wk", "land_wk"),
}


def kind_of(code: str, name: str) -> str:
    n = code.removeprefix("G")
    wk = bool(WAHLKARTEN.search(name))
    if n in ("0", "00000"):
        return "country_wk" if wk else "country"
    if re.fullmatch(r"\d(0000)?", n):
        return "land"
    if re.fullmatch(r"\d[A-H](000)?", n):
        return "rwk"
    if re.fullmatch(r"\d[A-H](999|099)", n):
        return "rwk_wk"
    if wk and re.fullmatch(r"\d(0099|9999)", n):
        return "land_wk"
    if re.fullmatch(r"\d{3}(00)?", n):
        return "sb"
    if wk and re.fullmatch(r"\d{3}99", n):
        return "sb_wk"
    if re.fullmatch(r"\d{5}", n):
        return "gem"
    return "other"


def _same(a: Line, b: tuple[int, int, int]) -> bool:
    return (a.cast, a.valid, a.invalid) == b


@functools.cache
def leaves(edition: Edition) -> tuple[str, Line, list[Leaf]]:
    """Pick the finest non-overlapping rows. The files list totals at several levels, and each edition nests its
    postal-ballot rows differently, so the rows that add up to every state's printed total are the leaves."""
    _, body = lines(edition)
    country = next(line for line in body if line.code and kind_of(line.code, line.name) == "country")
    land = ""
    states: dict[str, Line] = {}
    kinds: dict[str, list[Leaf]] = {}
    sb_rows: list[Leaf] = []
    for line in body:
        if line.code is None:
            if line.code is None and UNASSIGNED.search(line.name) and land:
                kinds.setdefault("unassigned", []).append(Leaf("unassigned", "", land, line))
            continue
        kind = kind_of(line.code, line.name)
        n = line.code.removeprefix("G")
        if kind in ("country", "country_wk", "other") or n[0] == "0":
            continue
        land = n[0]
        if kind == "land":
            states[land] = line
        elif kind == "sb":
            sb_rows.append(Leaf("gem", n[:3], land, line))
        else:
            kinds.setdefault(kind, []).append(Leaf(kind, n, land, line))
    has_children = {leaf.code[:3] for leaf in kinds.get("gem", [])}
    kinds.setdefault("gem", []).extend(leaf for leaf in sb_rows if leaf.code not in has_children)
    best: tuple[int, str] = (-1, "")
    for name, levels in STRATEGIES.items():
        chosen = [leaf for kind in ("gem", "unassigned", *levels) for leaf in kinds.get(kind, [])]
        matched = 0
        for digit, state in states.items():
            here = [leaf.line for leaf in chosen if leaf.land == digit]
            total = (
                sum(x.cast or 0 for x in here),
                sum(x.valid or 0 for x in here),
                sum(x.invalid or 0 for x in here),
            )
            matched += _same(state, total)
        if matched > best[0]:
            best = (matched, name)
    levels = STRATEGIES[best[1]]
    return best[1], country, [leaf for kind in ("gem", "unassigned", *levels) for leaf in kinds.get(kind, [])]


AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))
PERSISTENT = {"ÖVP", "SPÖ", "FPÖ", "GRÜNE", "NEOS", "KPÖ", "BZÖ", "LIF", "BIER", "CPÖ", "SLP"}
MEASURES = ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


SOURCE_ID = key("source", SOURCE)
NATIONAL_COUNCIL = key("body", "national-council")
PRESIDENCY = key("office", "federal-president")


def packages() -> list[str]:
    from ea.sources.at import bmi_history

    return [
        "at.bmi/geo",
        "at.bmi/registry",
        *(f"at.bmi/{edition.key}" for edition in EDITIONS),
        *bmi_history.packages(),
    ]


def parse() -> None:
    from ea.sources.at import bmi_history

    geo().write()
    registry().write()
    for edition in EDITIONS:
        election(edition).write()
    bmi_history.parse()


def emit() -> None:
    package.run("load", packages())


def name_of(text: str) -> str:
    text = " ".join(text.split())
    return text.title() if text.isupper() else text


def gemeinde_code(code: str) -> str:
    """A statutory city printed only as its 3-digit district is the municipality numbered 01 in that district."""
    return f"{code}01" if len(code) == 3 else code


@functools.cache
def places() -> dict[str, dict[str, list[str]]]:
    """Names of each state, district, and municipality, newest edition first."""
    found: dict[str, dict[str, list[str]]] = {"at.land": {}, "at.bezirk": {}, "at.gemeinde": {}}
    for edition in sorted(EDITIONS, key=lambda e: e.day, reverse=True):
        for line in lines(edition)[1]:
            if line.code is None:
                continue
            kind, n = kind_of(line.code, line.name), line.code.removeprefix("G")
            if kind == "land":
                scheme, code = "at.land", n[0]
            elif kind == "sb":
                scheme, code = "at.bezirk", n[:3]
            elif kind == "gem":
                scheme, code = "at.gemeinde", n
            else:
                continue
            names = found[scheme].setdefault(code, [])
            if (text := name_of(line.name)).lower() not in {x.lower() for x in names}:
                names.append(text)
    return found


def geo() -> Package:
    p = Package(
        "at.bmi/geo",
        SOURCE,
        "Austria's states, political districts, and municipalities as the Federal Ministry of the Interior codes them "
        "in its election results",
    )
    p.external |= {AUSTRIA, COUNTRY}
    for code, rank, noun in (
        ("at.land", 1, "state"),
        ("at.bezirk", 2, "political district"),
        ("at.gemeinde", 3, "municipality"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRIA, kind="administrative", rank=rank, noun=noun, code_system=None)
    found = places()
    for scheme, parent_scheme in (("at.land", None), ("at.bezirk", "at.land"), ("at.gemeinde", "at.bezirk")):
        for code, names in sorted(found[scheme].items()):
            id = area(scheme, code)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=code)
            if parent_scheme is None:
                parent = COUNTRY
            else:
                parent = area(parent_scheme, code[0] if parent_scheme == "at.land" else code[:3])
            p.add("area_parent", area_id=id, parent_id=parent)
            for index, text in enumerate(names):
                p.name(id, text, "de", "official", index == 0)
    return p


def registry() -> Package:
    p = Package(
        "at.bmi/registry",
        SOURCE,
        "The parties and candidates on Austrian ballots, the National Council, and the Federal Presidency, as the "
        "Federal Ministry of the Interior prints them",
        depends=["at.bmi/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Bundesministerium für Inneres",
        title="Wahlen: Ergebnisse der Nationalrats-, Europa- und Bundespräsidentenwahlen",
        url="https://www.bmi.gv.at/412/",
        attribution="Bundesministerium für Inneres",
    )
    for edition in EDITIONS:
        p.artifact(raw.cached(SOURCE, url(edition)), SOURCE_ID)
    p.add("body", id=NATIONAL_COUNCIL, polity_id=AUSTRIA, kind="lower_house")
    p.name(NATIONAL_COUNCIL, "National Council", "en", "official")
    p.name(NATIONAL_COUNCIL, "Nationalrat", "de", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=AUSTRIA, kind="head_of_state")
    p.name(PRESIDENCY, "Federal President of Austria", "en", "official")
    p.name(PRESIDENCY, "Bundespräsident", "de", "native", False)
    for edition in EDITIONS:
        if edition.family in (BP, VB):
            continue
        for label in lines(edition)[0]:
            id = party(edition, label)
            if any(row["id"] == id for row in p.rows.get("party", [])):
                continue
            p.add("party", id=id, kind="party", polity_id=AUSTRIA)
            p.add(
                "external_id",
                entity_id=id,
                scheme="at.bmi.party",
                value=label if label in PERSISTENT else f"{label}@{edition.key}",
            )
            p.name(id, label, "de", "abbreviation")
    return p


def party(edition: Edition, label: str) -> str:
    return key("party", label if label in PERSISTENT else f"{label}@{edition.key}")


EARLIER = {
    "bp-2016-2": ["at.bmi/bp-2016-1"],
    "bp-2016-3": ["at.bmi/bp-2016-1", "at.bmi/bp-2016-2"],
}
NR_2006_NOTE = (
    "The state total printed for Kärnten counts 86 more valid ballots than its municipalities and postal-ballot "
    "rows add up to (ÖVP 20, SPÖ 20, FPÖ 9, GRÜNE 18, BZÖ 17, MATIN 1, NFÖ 1), so the national total printed in the "
    "file is 86 above the sum of the units. The file prints those ballots only in the state and national totals."
)
BP_2022_NOTE = (
    "The file's municipality rows for Salzburg list 8 fewer registered voters than its state row, and one "
    "Kärnten municipality lists 2 more votes for the winner than it lists valid ballots. The national totals "
    "printed in the file follow the state rows, so they differ from the sum of the units by those amounts."
)
DISCREPANCIES: dict[str, tuple[tuple[str, ...], str]] = {
    "nr-2006": (("accounting.declared",), NR_2006_NOTE),
    "bp-2022": (("accounting.declared", "accounting.votes_valid"), BP_2022_NOTE),
}


def _over(p: Package, result_set: str, units: list[Leaf]) -> None:
    over = [leaf for leaf in units if leaf.kind == "gem" and (leaf.line.cast or 0) > (leaf.line.registered or 0)]
    if not over:
        return
    p.accept(
        "accounting.turnout",
        f"{len(over)} {'municipality' if len(over) == 1 else 'municipalities'} took more ballots than the rolls list, "
        "from voters with an absentee card (Wahlkarte), who may vote in a municipality other than the one that "
        "lists them; stored as printed",
    )
    p.add(
        "note",
        id=key("note", result_set, "absentee-cards"),
        entity_id=result_set,
        kind="caveat",
        body_md=f"In {len(over)} {'municipality' if len(over) == 1 else 'municipalities'}, more ballots were cast "
        "than voters were on the roll. Voters with an absentee card may vote in a municipality other than the one "
        "that lists them, so the figures are stored as printed.",
        lang="en",
    )


KINDS = {
    NR: ("National Council election", "Nationalratswahl", "national-council", "general"),
    EU: ("European Parliament election", "Europawahl", "european-parliament", "general"),
    BP: ("presidential election", "Bundespräsidentenwahl", "presidential", "general"),
    VB: ("referendum on conscription", "Volksbefragung", "referendum", "referendum"),
}
VB_ANSWERS = {
    "Lösungv. A": "Lösungsvariante a): Sind Sie für die Einführung eines Berufsheeres und eines bezahlten "
    "freiwilligen Sozialjahres?",
    "Lösungv. B": "Lösungsvariante b): Sind Sie für die Beibehaltung der allgemeinen Wehrpflicht und des "
    "Zivildienstes?",
}
METHOD = (
    "Results by municipality (Gemeinde) from the Federal Ministry of the Interior's final results file. Vienna is "
    "reported by municipal district (Gemeindebezirk). Postal and absentee-card ballots (Wahlkarten) are not counted "
    "in the municipality where the voter lives. The authority counts them above it, and the file prints them as their "
    "own rows, at the level of the political district, the regional electoral district, or the state. Each of those "
    "rows is stored as its own unit, so the units add up to the national result and no ballot is counted twice. "
    "Registered voters are listed only for municipalities."
)


def election(edition: Edition) -> Package:
    year = edition.day.year
    label, native, slug, kind = KINDS[edition.family]
    p = Package(
        f"at.bmi/{edition.key}",
        SOURCE,
        f"The {edition.day} Austrian {label} by municipality from the Federal Ministry of the Interior",
        depends=["at.bmi/geo", "at.bmi/registry", *EARLIER.get(edition.key, [])],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.uses(raw.cached(SOURCE, url(edition)))
    labels, _ = lines(edition)
    _, country, leaf_list = leaves(edition)
    round_number = edition.round
    runoff = edition.family == BP and round_number > 1
    id = key("election", edition.key)
    annulled = edition.key == "bp-2016-2"
    p.add(
        "election",
        id=id,
        polity_id=AUSTRIA,
        kind="runoff" if runoff else kind,
        date_from=edition.day,
        date_to=edition.day,
        status="annulled" if annulled else "held",
        rerun_of_id=key("election", "bp-2016-2") if edition.key == "bp-2016-3" else None,
    )
    suffix = (
        {1: "", 2: ", second round", 3: ", repeat of the second round"}[round_number] if edition.family == BP else ""
    )
    native_suffix = (
        {1: "", 2: ", zweiter Wahlgang", 3: ", Wiederholung des zweiten Wahlgangs"}[round_number]
        if edition.family == BP
        else ""
    )
    p.name(id, f"{year} Austrian {label}{suffix}", "en", "official")
    p.name(id, f"{native} {year}{native_suffix}", "de", "native", False)
    path = f"at/{edition.day}-{slug}" + ("-runoff" if runoff else "")
    p.add("slug", path=path, entity_id=id, is_current=True)
    contest = key("contest", edition.key)
    previous = key("contest", "bp-2016-1") if edition.key in ("bp-2016-2", "bp-2016-3") else None
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind={BP: "office", VB: "ballot_question"}.get(edition.family, "list_allocation"),
        office_id=PRESIDENCY if edition.family == BP else None,
        body_id=NATIONAL_COUNCIL if edition.family == NR else None,
        district_area_id=COUNTRY,
        magnitude=1 if edition.family == BP else None,
        round_number=2 if runoff else 1,
        previous_round_id=previous,
        default_result_kind="counted",
    )
    title = {
        NR: "National Council",
        EU: "Austrian seats in the European Parliament",
        BP: "Federal President",
        VB: "Conscription",
    }[edition.family]
    p.name(contest, f"{year} {title}{suffix}", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
    result_set = key("result_set", edition.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="annulled" if annulled else "active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    if annulled:
        p.add(
            "note",
            id=key("note", result_set, "annulled"),
            entity_id=result_set,
            kind="caveat",
            body_md="The Constitutional Court annulled this second round on 1 July 2016 (W I 6/2016) because of "
            "irregularities in counting postal ballots. It was repeated on 4 December 2016.",
            lang="en",
        )
    if edition.family == VB:
        p.add("ballot_question", contest_id=contest, type="plebiscite")
    options: dict[str, str] = {}
    for position, text in enumerate(labels, start=1):
        option = options[text] = key("option", edition.key, text)
        if edition.family == BP:
            person = key("person", f"{year}:{text}")
            if edition.key not in EARLIER:
                p.add("person", id=person)
                p.name(person, name_of(text), "de", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=text,
            )
            p.add(
                "candidacy",
                id=key("candidacy", edition.key, text),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=text,
            )
        elif edition.family == VB:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=VB_ANSWERS[text],
            )
        else:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(edition, text),
                ballot_position=position,
                ballot_label=text,
            )
    totals = {
        "registered": country.registered,
        "cast": country.cast,
        "valid": country.valid,
        "invalid": country.invalid,
    }
    for measure, field in MEASURES:
        if totals[field] is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=totals[field])
    for text, votes in country.votes.items():
        p.add("declared", result_set_id=result_set, option_id=options[text], measure=1, value=votes)
    _over(p, result_set, leaf_list)
    if edition.key in DISCREPANCIES:
        codes, reason = DISCREPANCIES[edition.key]
        for code in codes:
            p.accept(code, reason)
        p.add(
            "note",
            id=key("note", result_set, "printed"),
            entity_id=result_set,
            kind="caveat",
            body_md=reason,
            lang="en",
        )
    seen: defaultdict[str, int] = defaultdict(int)
    for leaf in leaf_list:
        line = leaf.line
        code = gemeinde_code(leaf.code) if leaf.kind == "gem" else leaf.code
        if not code:
            seen[leaf.land] += 1
            code = f"{leaf.land}-unassigned" + (f"-{seen[leaf.land]}" if seen[leaf.land] > 1 else "")
        unit = key("unit", edition.key, code)
        if leaf.kind == "gem":
            relation, place, unit_kind = "equals", area("at.gemeinde", code), "standard"
        elif leaf.kind == "sb_wk":
            relation, place, unit_kind = "within", area("at.bezirk", code[:3]), "postal_district"
        else:
            relation, place, unit_kind = "within", area("at.land", leaf.land), "aggregate"
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code="at.gemeinde",
            code=f"{edition.day}:{code}",
            area_relation=relation,
            area_id=place,
            kind=unit_kind,
        )
        p.name(unit, name_of(line.name) if line.name else f"Wahlkarten, {code}", "de", "official")
        for measure, field in MEASURES:
            value = getattr(line, field)
            if value is None or (field == "registered" and leaf.kind != "gem"):
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
        for text, votes in line.votes.items():
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[text],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
    return p
