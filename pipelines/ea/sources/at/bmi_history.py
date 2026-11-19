"""Austria's National Council elections of 1945 to 1994 and presidential elections of 1951 to 1992. The elections of
1945 to 1970 are stored by constituency (Wahlkreis), the one of 1994 by regional constituency (Regionalwahlkreis), the
others by state.

The Federal Ministry of the Interior (BMI) links the official result brochures of these elections from its historical
pages for National Council and presidential elections: one scanned volume per election, prepared by the Austrian
Central Statistical Office, with summary tables up front and the results of every municipality after them. The scans
have no text layer.

The summary tables by constituency and state (Bundesland) were read from the page images and transcribed into
`bmi_tables/{key}.txt`, one file per election or round. Each row keeps its band on the page and each table the edges
of its columns, so every stored number points at its page and box. A row coded `1` to `9` is a state, a row coded
`W1` to `W25` a constituency of the plan of 1945, and a row coded `R1A` to `R9G` a regional constituency of 1994.
Parsing checks every printed figure: the states of each column against the printed national total, the
constituencies of each state and of the country against the printed state and national figures, the lists or
candidates against the valid votes, and the votes cast less the invalid votes against the valid votes. A mismatch
raises, unless the transcription records that the printed figures themselves disagree, so a misread number never
reaches a package.

Where `bmi_tables/{brochure}-gemeinden.txt` holds a presidential brochure's results by municipality
(`bmi_gemeinden`), each round also gets a result set by municipality that supersedes the one by state.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.at import bmi_gemeinden
from ea.sources.at.bmi import AUSTRIA, COUNTRY, NATIONAL_COUNCIL, PRESIDENCY, area, key
from ea.sources.at.parlament import NAMES_1923

SOURCE = "at.bmi"
TABLES = Path(__file__).with_name("bmi_tables")
BASE = "https://www.bmi.gv.at/412"
NR_HISTORY = f"{BASE}/nationalratswahlen/historischer_rueckblick.html"
BP_HISTORY = f"{BASE}/bundespraesidentenwahlen/historischer_rueckblick.html"
HISTORY_SOURCE = key("source", "historischer-rueckblick")
REGISTRY = f"{SOURCE}/registry-history"

VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
MEASURES = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "invalid": INVALID}
LANDS = {
    "1": "Burgenland",
    "2": "Kärnten",
    "3": "Niederösterreich",
    "4": "Oberösterreich",
    "5": "Salzburg",
    "6": "Steiermark",
    "7": "Tirol",
    "8": "Vorarlberg",
    "9": "Wien",
}
WAHLKREIS = "at.nr_wahlkreis"
PLAN_1945 = date(1945, 11, 25)
"""The 25 constituencies of the National Council elections of 1945 to 1970, dated to the first election they served."""
STATE_OF = {
    **dict.fromkeys(range(1, 8), "9"),
    **dict.fromkeys(range(8, 12), "3"),
    **dict.fromkeys(range(12, 17), "4"),
    17: "5",
    18: "7",
    19: "8",
    **dict.fromkeys(range(20, 24), "6"),
    24: "2",
    25: "1",
}
"""The state each constituency lies in."""
REGIONALWAHLKREIS = "at.nr_regionalwahlkreis"
PLAN_1994 = date(1994, 10, 9)
"""The 43 regional constituencies of the National Council elections from 1994, dated to the first election they served."""
NAMES_1994 = {
    "1A": "Burgenland Nord",
    "1B": "Burgenland Süd",
    "2A": "Klagenfurt",
    "2B": "Villach",
    "2C": "Kärnten West",
    "2D": "Kärnten Ost",
    "3A": "Weinviertel",
    "3B": "Waldviertel",
    "3C": "Mostviertel",
    "3D": "Niederösterreich Mitte",
    "3E": "Niederösterreich Süd",
    "3F": "Wien-Umgebung",
    "3G": "Niederösterreich Süd-Ost",
    "4A": "Linz und Umgebung",
    "4B": "Innviertel",
    "4C": "Hausruckviertel",
    "4D": "Traunviertel",
    "4E": "Mühlviertel",
    "5A": "Salzburg Stadt",
    "5B": "Flachgau/Tennengau",
    "5C": "Lungau/Pinzgau/Pongau",
    "6A": "Graz",
    "6B": "Steiermark Mitte",
    "6C": "Steiermark Süd",
    "6D": "Steiermark Süd-Ost",
    "6E": "Steiermark Ost",
    "6F": "Steiermark Nord",
    "6G": "Steiermark Nord-West",
    "6H": "Steiermark West",
    "7A": "Innsbruck",
    "7B": "Innsbruck-Land",
    "7C": "Unterland",
    "7D": "Oberland",
    "7E": "Osttirol",
    "8A": "Vorarlberg Nord",
    "8B": "Vorarlberg Süd",
    "9A": "Wien Innen-Süd",
    "9B": "Wien Innen-West",
    "9C": "Wien Innen-Ost",
    "9D": "Wien Süd",
    "9E": "Wien Süd-West",
    "9F": "Wien Nord-West",
    "9G": "Wien Nord",
}
"""The regional constituencies of 1994 and their names, as the brochure's section 1.2.1 prints them."""


def state_of(unit: str) -> str:
    """The state a constituency (a number of the plan of 1945, or a code such as `3A` of the plan of 1994) lies in."""
    return unit[0] if unit in NAMES_1994 else STATE_OF[int(unit)]


def unit_name(unit: str) -> str:
    return NAMES_1994[unit] if unit in NAMES_1994 else NAMES_1923[int(unit) - 1]


def unit_scheme(unit: str) -> str:
    return REGIONALWAHLKREIS if unit in NAMES_1994 else WAHLKREIS


def unit_area(unit: str) -> str:
    return regionalwahlkreis(unit) if unit in NAMES_1994 else wahlkreis(int(unit))


def unit_order(unit: str) -> tuple[int, str]:
    return len(unit), unit


def wahlkreis(number: int) -> str:
    """The area of a constituency of the plan of 1945 to 1970."""
    return area(WAHLKREIS, f"1945:{number}")


def regionalwahlkreis(code: str) -> str:
    """The area of a regional constituency of the plan of 1994."""
    return area(REGIONALWAHLKREIS, f"1994:{code}")


PARTY_LABELS = {
    "OEVP": "ÖVP",
    "SPOE": "SPÖ",
    "FPOE": "FPÖ",
    "KPOE": "KPÖ",
    "KPOE_LS": "KPÖ",
    "GRUENE": "GRÜNE",
    "LIF": "LIF",
}
"""Transcription codes of the parties the archive already holds from the BMI's later results, by their label there."""


@dataclass(frozen=True)
class Edition:
    key: str
    family: str
    """`nr` for the National Council, `bp` for the Federal President."""
    day: date
    page: str
    """Folder of the BMI page that links the file, under the family's folder; empty for the historical page itself."""
    file: str
    seats: int = 0
    round: int = 1
    decided: bool = True
    """For a presidential round: whether a candidate won it outright (otherwise the top two advanced)."""
    notes: tuple[str, ...] = ()


NR, BP = "nr", "bp"
EDITIONS = [
    Edition("nr-1945", NR, date(1945, 11, 25), "nationalratswahl_1945", "nationalratswahl-25111945.pdf", 165),
    Edition("nr-1949", NR, date(1949, 10, 9), "nationalratswahl_1949", "nationalratswahl-9101949.pdf", 165),
    Edition("nr-1953", NR, date(1953, 2, 22), "nationalratswahl_1953", "nationalratswahl-2221953.pdf", 165),
    Edition("nr-1956", NR, date(1956, 5, 13), "nationalratswahl_1956", "nationalratswahl-1351956.pdf", 165),
    Edition("nr-1959", NR, date(1959, 5, 10), "nationalratswahl_1959", "nationalratswahl-1051959.pdf", 165),
    Edition("nr-1962", NR, date(1962, 11, 18), "nationalratswahl_1962", "nationalratswahl-18111962.pdf", 165),
    Edition("nr-1966", NR, date(1966, 3, 6), "nationalratswahl_1966", "nationalratswahl-631966.pdf", 165),
    Edition(
        "nr-1970",
        NR,
        date(1970, 3, 1),
        "nationalratswahl_1970",
        "nationalratswahl-131970.pdf",
        165,
        notes=(
            (
                "These are the figures of 1 March 1970. The Constitutional Court annulled the vote in three Vienna "
                "constituencies (nine municipal districts), which voted again on 4 October 1970. The BMI's supplement "
                "(nationalratswahl_131970_ergaenzungsteil.pdf) prints the final result: in Vienna 1,068,376 votes cast, "
                "6,538 invalid, and 1,061,838 valid (ÖVP 371,027, SPÖ 622,923, FPÖ 43,182, KPÖ 15,689, DFP 9,017); in "
                "Austria 4,630,851 cast, 41,890 invalid, and 4,588,961 valid (SPÖ 2,221,981, ÖVP 2,051,012, FPÖ 253,425, "
                "KPÖ 44,750, DFP 14,925, NDP 2,631, MRF 237). The other states were unchanged. The seats moved from "
                "SPÖ 81, ÖVP 79, FPÖ 5 to SPÖ 81, ÖVP 78, FPÖ 6."
            ),
        ),
    ),
    Edition("nr-1971", NR, date(1971, 10, 10), "nationalratswahl_1971", "nationalratswahl-10101971.pdf", 183),
    Edition("nr-1975", NR, date(1975, 10, 5), "nationalratswahl_1975", "nationalratswahl-5101975.pdf", 183),
    Edition("nr-1979", NR, date(1979, 5, 6), "nationalratswahl_1979", "nationalratswahl-651979.pdf", 183),
    Edition("nr-1983", NR, date(1983, 4, 24), "nationalratswahl_1983", "nationalratswahl-2441983.pdf", 183),
    Edition("nr-1986", NR, date(1986, 11, 23), "nationalratswahl_1986", "nationalratswahl-23111986.pdf", 183),
    Edition("nr-1990", NR, date(1990, 10, 7), "nationalratswahl_1990", "nationalratswahl-7101990.pdf", 183),
    Edition("nr-1994", NR, date(1994, 10, 9), "nationalratswahl_1994", "nationalratswahl-9101994.pdf", 183),
    Edition("bp-1951-1", BP, date(1951, 5, 6), "", "bpw_1951.pdf", decided=False),
    Edition("bp-1951-2", BP, date(1951, 5, 27), "", "bpw_1951.pdf", round=2),
    Edition("bp-1957", BP, date(1957, 5, 5), "", "bpw_1957.pdf"),
    Edition("bp-1963", BP, date(1963, 4, 28), "", "bpw_1963.pdf"),
    Edition("bp-1965", BP, date(1965, 5, 23), "", "bpw_1965.pdf"),
    Edition("bp-1971", BP, date(1971, 4, 25), "", "bpw_1971.pdf"),
    Edition("bp-1974", BP, date(1974, 6, 23), "", "bpw_1974.pdf"),
    Edition("bp-1980", BP, date(1980, 5, 18), "", "bpw_1980.pdf"),
    Edition("bp-1986-1", BP, date(1986, 5, 4), "", "bpw_1986.pdf", decided=False),
    Edition("bp-1986-2", BP, date(1986, 6, 8), "", "bpw_1986.pdf", round=2),
    Edition("bp-1992-1", BP, date(1992, 4, 26), "", "bpw_1992.pdf", decided=False),
    Edition("bp-1992-2", BP, date(1992, 5, 24), "", "bpw_1992.pdf", round=2),
]
BY_KEY = {e.key: e for e in EDITIONS}


def url(e: Edition) -> str:
    if e.family == NR:
        return f"{BASE}/nationalratswahlen/{e.page}/files/{e.file}"
    return f"{BASE}/bundespraesidentenwahlen/files/{e.file}"


def page_url(e: Edition) -> str:
    if e.family == NR:
        return f"{BASE}/nationalratswahlen/{e.page}/start.html"
    return BP_HISTORY


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{e.key}" for e in EDITIONS)]


def fetch() -> None:
    raw.fetch(SOURCE, NR_HISTORY, suffix=".html")
    raw.fetch(SOURCE, BP_HISTORY, suffix=".html")
    for e in EDITIONS:
        if e.family == NR:
            raw.fetch(SOURCE, page_url(e), suffix=".html")
        raw.fetch(SOURCE, url(e), suffix=".pdf")


def parse() -> None:
    registry().write()
    for e in EDITIONS:
        election(e).write()


# ---------------------------------------------------------------- transcriptions


@dataclass
class Row:
    code: str
    band: tuple[float, float]
    values: dict[str, int | None]


@dataclass
class Table:
    page: int
    rotate: int
    columns: list[str] = field(default_factory=list)
    edges: list[tuple[float, float]] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    constituencies: list[Row] = field(default_factory=list)
    """The rows coded `W1` to `W25` (plan of 1945) or `R1A` to `R9G` (plan of 1994)."""
    total: Row | None = None


@dataclass
class Transcription:
    options: dict[str, str]
    """Code of each list or candidate and its printed name, in the order the file gives them."""
    tables: list[Table]
    seats: dict[str, int]
    source_errors: list[str]


def _number(text: str) -> int | None:
    return None if text == "-" else int(text)


@cache
def transcription(edition: str) -> Transcription:
    t = Transcription({}, [], {}, [])
    for line in (TABLES / f"{edition}.txt").read_text().splitlines():
        if line.startswith("# SOURCE-ERROR:"):
            t.source_errors.append(line.removeprefix("# SOURCE-ERROR:").strip())
        words = line.split("#", 1)[0].split()
        if not words:
            continue
        head, rest = words[0], words[1:]
        if head == "option":
            t.options[rest[0]] = " ".join(rest[1:])
        elif head == "table":
            attrs = dict(word.split("=", 1) for word in rest)
            t.tables.append(Table(int(attrs["page"]), int(attrs.get("rotate", "0"))))
        elif head == "columns":
            t.tables[-1].columns = rest
        elif head == "x":
            edges = [float(x) for x in rest]
            t.tables[-1].edges = list(zip(edges[::2], edges[1::2], strict=True))
        elif head in ("row", "total"):
            table = t.tables[-1]
            y0, y1 = rest[1].removeprefix("@").split(":")
            values = dict(zip(table.columns, (_number(x) for x in rest[2:]), strict=True))
            row = Row(rest[0], (float(y0), float(y1)), values)
            if head == "row" and row.code[0] in "WR":
                table.constituencies.append(row)
            elif head == "row":
                table.rows.append(row)
            else:
                table.total = row
        elif head == "seats":
            t.seats.update({code: int(n) for code, n in zip(rest[1::2], rest[2::2], strict=True)})
        else:
            raise ValueError(f"{edition}.txt: unknown line {line!r}")
    return t


def _merge(edition: str, into: dict[str, int | None], values: dict[str, int | None], where: str) -> None:
    """Add a row's figures; a figure that two tables both print must agree."""
    for column, value in values.items():
        if into.get(column) is not None and value is not None and into[column] != value:
            raise ValueError(f"{edition} {where}, {column}: the tables print {into[column]} and {value}")
        if into.get(column) is None:
            into[column] = value


@cache
def figures(edition: str) -> tuple[dict[str, dict[str, int | None]], dict[str, int | None], list[str]]:
    """Each state's figures, the national figures, and the checks the printed figures fail, if any."""
    t = transcription(edition)
    units: dict[str, dict[str, int | None]] = {}
    national: dict[str, int | None] = {}
    failed: list[str] = []
    for table in t.tables:
        if table.total is None and table.rows:
            raise ValueError(f"{edition} page {table.page}: no printed total")
        if len(table.edges) != len(table.columns):
            raise ValueError(f"{edition} page {table.page}: {len(table.edges)} column edges")
        for column in table.columns:
            if column not in MEASURES and column not in t.options:
                raise ValueError(f"{edition} page {table.page}: unknown column {column}")
            if table.total is None:
                continue
            summed = sum(row.values[column] or 0 for row in table.rows)
            if summed != (table.total.values[column] or 0):
                failed.append(
                    f"page {table.page}, {column}: states add up to {summed}, printed {table.total.values[column]}"
                )
        for row in table.rows:
            if row.code not in LANDS:
                raise ValueError(f"{edition} page {table.page}: unknown state {row.code}")
            _merge(edition, units.setdefault(row.code, {}), row.values, f"state {row.code}")
        if table.total is not None:
            _merge(edition, national, table.total.values, "Austria")
    if set(units) != set(LANDS):
        raise ValueError(f"{edition}: states {sorted(units)}")
    for where, values in [*sorted(units.items()), ("AT", national)]:
        valid = values.get("valid")
        if valid is not None and any(code in values for code in t.options):
            listed = sum(values.get(code) or 0 for code in t.options)
            if listed != valid:
                failed.append(f"{where}: the options add up to {listed}, the valid votes are {valid}")
        cast, invalid = values.get("cast"), values.get("invalid")
        if valid is not None and cast is not None and invalid is not None and cast - invalid != valid:
            failed.append(f"{where}: {cast} cast less {invalid} invalid is not {valid} valid")
    wks = constituencies(edition)
    if wks:
        failed += _check_constituencies(wks, units, national, t)
    if failed and not t.source_errors:
        raise ValueError(f"{edition}: " + "; ".join(failed))
    return units, national, failed


@cache
def constituencies(edition: str) -> dict[str, dict[str, int | None]]:
    """Each constituency's figures, merged from the tables that print it; empty when the file has none."""
    found: dict[str, dict[str, int | None]] = {}
    for table in transcription(edition).tables:
        for row in table.constituencies:
            unit = row.code[1:]
            if (row.code[0] == "R") != (unit in NAMES_1994) or (row.code[0] == "W" and not 1 <= int(unit) <= 25):
                raise ValueError(f"{edition} page {table.page}: unknown constituency {row.code}")
            _merge(edition, found.setdefault(unit, {}), row.values, f"constituency {unit}")
    expected = set(NAMES_1994) if any(unit in NAMES_1994 for unit in found) else {str(n) for n in STATE_OF}
    if found and set(found) != expected:
        raise ValueError(f"{edition}: constituencies {sorted(found, key=unit_order)}")
    return found


def _check_constituencies(
    wks: dict[str, dict[str, int | None]],
    units: dict[str, dict[str, int | None]],
    national: dict[str, int | None],
    t: Transcription,
) -> list[str]:
    """The constituencies must add up to the printed state and national figures and to their own printed totals."""
    failed: list[str] = []
    columns = {column for values in wks.values() for column in values}
    for column in sorted(columns):
        if not any(values.get(column) is not None for values in wks.values()):
            continue
        for state, values in sorted(units.items()):
            summed = sum(wks[n].get(column) or 0 for n in wks if state_of(n) == state)
            if summed != (values.get(column) or 0):
                failed.append(
                    f"{column}, constituencies of {LANDS[state]} add up to {summed}, state printed {values.get(column)}"
                )
        if column in national:
            summed = sum(values.get(column) or 0 for values in wks.values())
            if summed != (national[column] or 0):
                failed.append(f"{column}, constituencies add up to {summed}, national printed {national[column]}")
    for n, values in sorted(wks.items(), key=lambda item: unit_order(item[0])):
        valid = values.get("valid")
        if valid is not None and any(code in values for code in t.options):
            listed = sum(values.get(code) or 0 for code in t.options)
            if listed != valid:
                failed.append(f"constituency {n}: the options add up to {listed}, the valid votes are {valid}")
        cast, invalid = values.get("cast"), values.get("invalid")
        if valid is not None and cast is not None and invalid is not None and cast - invalid != valid:
            failed.append(f"constituency {n}: {cast} cast less {invalid} invalid is not {valid} valid")
    return failed


# ---------------------------------------------------------------- registry


def _party_codes() -> dict[str, str]:
    found: dict[str, str] = {}
    for e in EDITIONS:
        if e.family != NR:
            continue
        for code, name in transcription(e.key).options.items():
            if code not in PARTY_LABELS:
                found.setdefault(code, name)
    return found


def party(code: str) -> str:
    if code in PARTY_LABELS:
        return key("party", PARTY_LABELS[code])
    return key("party", f"history:{code}")


CANDIDATES: dict[str, str] = {}
"""Transcription code of a candidate mapped to the code that names the person, where two files spell one differently."""


def person(code: str) -> str:
    return key("person", f"history:{CANDIDATES.get(code, code)}")


def _people() -> dict[str, str]:
    found: dict[str, str] = {}
    for e in EDITIONS:
        if e.family == BP:
            for code, name in transcription(e.key).options.items():
                found.setdefault(CANDIDATES.get(code, code), name)
    return found


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The result brochures of Austria's National Council elections of 1945 to 1994 and presidential elections of "
        "1951 to 1992, with the parties and candidates they print",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(AUSTRIA)
    p.add(
        "source",
        id=HISTORY_SOURCE,
        kind="official_body",
        publisher="Bundesministerium für Inneres",
        title="Nationalratswahlen und Bundespräsidentenwahlen: Historischer Rückblick",
        url=NR_HISTORY,
        attribution="Bundesministerium für Inneres; Österreichisches Statistisches Zentralamt",
    )
    p.artifact(raw.cached(SOURCE, NR_HISTORY), HISTORY_SOURCE)
    p.artifact(raw.cached(SOURCE, BP_HISTORY), HISTORY_SOURCE)
    seen: set[str] = set()
    for e in EDITIONS:
        for link in (page_url(e), url(e)):
            if link not in seen and link != BP_HISTORY:
                seen.add(link)
                p.artifact(raw.cached(SOURCE, link), HISTORY_SOURCE)
    for code, name in sorted(_party_codes().items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=AUSTRIA)
        p.add("external_id", entity_id=id, scheme="at.bmi.party", value=f"history:{code}")
        p.name(id, name, "de", "official")
    for code, name in sorted(_people().items()):
        id = person(code)
        p.add("person", id=id)
        p.name(id, name, "de", "official")
    return p


# ---------------------------------------------------------------- elections


def artifact_id(storage_key: str) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{HISTORY_SOURCE}\x1f{storage_key}"))


@cache
def _pages(path: Path) -> dict[int, tuple[int, int]]:
    """Each page's width and height in points as rendered, with the page's own rotation applied."""
    out = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "9999", str(path)], capture_output=True, text=True, check=True
    ).stdout
    sizes: dict[int, tuple[int, int]] = {}
    for found in re.finditer(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)[^\n]*\nPage\s+\d+ rot:\s+(\d+)", out):
        width, height = round(float(found.group(2))), round(float(found.group(3)))
        sizes[int(found.group(1))] = (height, width) if int(found.group(4)) % 180 else (width, height)
    return sizes


def _box(rotate: int, x: tuple[float, float], y: tuple[float, float]) -> tuple[float, float, float, float]:
    """Map a box measured on the page turned `rotate` degrees counterclockwise back onto the page as rendered."""
    (u0, u1), (v0, v1) = x, y
    if rotate == 0:
        return u0, v0, u1, v1
    if rotate == 90:
        return 1 - v1, u0, 1 - v0, u1
    if rotate == 270:
        return v0, 1 - u1, v1, 1 - u0
    raise ValueError(f"rotation {rotate}")


def _previous(e: Edition) -> Edition | None:
    if e.family != BP or e.round == 1:
        return None
    return BY_KEY[f"{e.key[:-2]}-1"]


def _method(e: Edition, pages: list[int], by_constituency: bool, by_municipality: bool = False) -> str:
    return (
        f"Transcribed from the scanned summary tables of the official result brochure (pages {', '.join(map(str, pages))} "
        f"of {e.file}), which the Austrian Central Statistical Office prepared for the Federal Ministry of the "
        "Interior. Every number was checked against the totals the tables print: "
        + (
            "each constituency column against the printed state and national totals, the "
            if by_constituency
            else "each state column against the national total, the "
        )
        + ("lists" if e.family == NR else "candidates")
        + " against the valid votes, and the votes cast less the invalid votes against the valid votes. "
        + (
            "The results of every municipality, which the brochure also prints, form the set that supersedes this one."
            if by_municipality
            else "The brochure also prints the results of every municipality, which are not yet stored."
        )
    )


def election(e: Edition) -> Package:
    t = transcription(e.key)
    units, national, failed = figures(e.key)
    wks = constituencies(e.key)
    previous = _previous(e)
    year = e.day.year
    if e.family == NR:
        label, native, slug, title = (
            "National Council election",
            "Nationalratswahl",
            "national-council",
            "National Council",
        )
    else:
        label, native, slug, title = (
            "presidential election",
            "Bundespräsidentenwahl",
            "presidential",
            "Federal President",
        )
    runoff = previous is not None
    p = Package(
        f"{SOURCE}/{e.key}",
        SOURCE,
        f"The {e.day} Austrian {label} by {'constituency' if wks else 'state'} from the Federal Ministry of the "
        "Interior's historical results",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            REGISTRY,
            *(["at.parlament/geo"] if any(unit not in NAMES_1994 for unit in wks) else []),
            *([f"{SOURCE}/{previous.key}"] if previous else []),
        ],
    )
    p.external |= {AUSTRIA, COUNTRY}
    link = url(e)
    stored = raw.cached(SOURCE, link)
    p.uses(stored)
    election_id = key("election", e.key)
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRIA,
        kind="runoff" if runoff else "general",
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        status="held",
    )
    suffix, native_suffix = (", second round", ", zweiter Wahlgang") if runoff else ("", "")
    p.name(election_id, f"{year} Austrian {label}{suffix}", "en", "official")
    p.name(election_id, f"{native} {year}{native_suffix}", "de", "native", False)
    path = f"at/{e.day}-{slug}" + ("-runoff" if runoff else "")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", e.key)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office" if e.family == BP else "list_allocation",
        office_id=PRESIDENCY if e.family == BP else None,
        body_id=NATIONAL_COUNCIL if e.family == NR else None,
        district_area_id=COUNTRY,
        magnitude=1 if e.family == BP else e.seats,
        round_number=e.round,
        previous_round_id=key("contest", previous.key) if previous else None,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} {title}{suffix}", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
    result_set = key("result_set", e.key)
    by_municipality = e.family == BP and bmi_gemeinden.exists(e.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=HISTORY_SOURCE,
        status="active",
        is_default=not by_municipality,
        completeness=1.0,
    )
    pages = sorted({table.page for table in t.tables if table.constituencies or not wks})
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=_method(e, pages, bool(wks), by_municipality),
        lang="en",
    )
    for index, text in enumerate([*e.notes, *t.source_errors]):
        p.add("note", id=key("note", result_set, index), entity_id=result_set, kind="caveat", body_md=text, lang="en")
    if wks:
        over = [
            unit_name(n)
            for n, v in sorted(wks.items(), key=lambda item: unit_order(item[0]))
            if (v.get("cast") or 0) > (v.get("registered") or 0) > 0
        ]
    else:
        over = [
            LANDS[code] for code, v in sorted(units.items()) if (v.get("cast") or 0) > (v.get("registered") or 0) > 0
        ]
    if over:
        text = (
            f"More votes were cast than voters were registered in {', '.join(over)}. The brochure counts the votes of "
            "voters with a voting card (Wahlkarte) in the constituency where they voted, not the one that lists them, "
            "so the figures are stored as printed."
        )
        p.accept("accounting.turnout", text)
        p.add(
            "note", id=key("note", result_set, "turnout"), entity_id=result_set, kind="caveat", body_md=text, lang="en"
        )
    if wks and national.get("registered") is not None and not any("registered" in v for v in wks.values()):
        text = (
            f"The brochure prints the registered voters of {year} only by state and for the whole country, not by "
            "constituency, so the constituencies carry no registered voters and the national figure is stored as printed."
        )
        p.accept("accounting.declared", text)
        p.add(
            "note",
            id=key("note", result_set, "registered"),
            entity_id=result_set,
            kind="caveat",
            body_md=text,
            lang="en",
        )
    if failed:
        reason = "The brochure's own printed figures disagree: " + "; ".join(t.source_errors)
        codes = {"accounting.declared", "accounting.votes_valid", "accounting.ballots"}
        for code in sorted(codes):
            p.accept(code, reason)

    options: dict[str, str] = {}
    order = sorted(t.options, key=lambda code: -(national.get(code) or 0))
    for position, code in enumerate(order, start=1):
        option = options[code] = key("option", e.key, code)
        name = t.options[code]
        if e.family == BP:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", e.key, code),
                person_id=person(code),
                option_id=option,
                role="head",
                ballot_name=name,
            )
        else:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(code),
                ballot_position=position,
                ballot_label=name,
            )
    for column, value in national.items():
        if value is None:
            continue
        if column in MEASURES:
            p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURES[column], value=value)
        else:
            p.add("declared", result_set_id=result_set, option_id=options[column], measure=VOTES, value=value)
    if e.family == NR:
        if sum(t.seats.values()) != e.seats:
            raise ValueError(f"{e.key}: the seats add up to {sum(t.seats.values())}, not {e.seats}")
        for code, n in t.seats.items():
            if n == 0:
                continue
            p.add("outcome", result_set_id=result_set, option_id=options[code], kind="won", seats=n)
    else:
        ranked = sorted(t.options, key=lambda code: -(national[code] or 0))
        for index, code in enumerate(ranked):
            if e.decided:
                kind = "elected" if index == 0 else "lost"
            else:
                kind = "advanced" if index < 2 else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=options[code], kind=kind)

    sizes = _pages(stored.path)
    page_ids: dict[int, str] = {}
    shared = {table.page for table in transcription(previous.key).tables} if previous else set()
    # Every page the transcription covers keeps its row, including the state tables an edition by constituency only
    # checks against: earlier versions of the package, and value locators elsewhere, cite them.
    for number in sorted({table.page for table in t.tables}):
        width, height = sizes[number]
        page_ids[number] = key("page", e.file, number)
        if number in shared:
            continue
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id(stored.storage_key),
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}/{number}.png",
            width=width,
            height=height,
        )
    unit_ids: dict[str, str] = {}
    if wks:
        for number in sorted(wks, key=unit_order):
            code = f"R{number}" if number in NAMES_1994 else f"W{number}"
            unit = unit_ids[code] = key("unit", e.key, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRIA,
                scheme_code=unit_scheme(number),
                code=f"{e.day}:{number}",
                area_relation="equals",
                area_id=unit_area(number),
                kind="standard",
            )
            p.name(unit, unit_name(number), "de", "official")
    else:
        for code in sorted(units):
            unit = unit_ids[code] = key("unit", e.key, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRIA,
                scheme_code="at.land",
                code=f"{e.day}:{code}",
                area_relation="equals",
                area_id=area("at.land", code),
                kind="standard",
            )
            p.name(unit, LANDS[code], "de", "official")
    stored_cells: set[tuple[str, str]] = set()
    for table in t.tables:
        for row in table.constituencies if wks else table.rows:
            for column, edges in zip(table.columns, table.edges, strict=True):
                value = row.values[column]
                if value is None or (row.code, column) in stored_cells:
                    continue
                stored_cells.add((row.code, column))
                if column in MEASURES:
                    option_id, measure = None, MEASURES[column]
                else:
                    option_id, measure = options[column], VOTES
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_ids[row.code],
                    option_id=option_id,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
                x0, y0, x1, y1 = _box(table.rotate, edges, row.band)
                p.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit_ids[row.code],
                    option_id=option_id,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    page_id=page_ids[table.page],
                    source_id=None,
                    x0=round(x0, 4),
                    y0=round(y0, 4),
                    x1=round(x1, 4),
                    y1=round(y1, 4),
                )
    if by_municipality:
        _municipalities(p, e, election_id, contest, result_set, options, units, national, stored, sizes, page_ids)
    return p


def _municipalities(
    p: Package,
    e: Edition,
    election_id: str,
    contest: str,
    by_state: str,
    options: dict[str, str],
    units: dict[str, dict[str, int | None]],
    national: dict[str, int | None],
    stored: raw.Artifact,
    sizes: dict[int, tuple[int, int]],
    page_ids: dict[int, str],
) -> None:
    """Add the result set by municipality, which supersedes the one by state and takes the default."""
    for code, values in bmi_gemeinden.state_figures(e.key, e.round).items():
        for column, value in values.items():
            if units[code].get(column) is not None and units[code][column] != value:
                raise ValueError(
                    f"{e.key} {LANDS[code]}, {column}: the summary prints {units[code][column]}, the municipal "
                    f"pages {value}"
                )
    result_set = key("result_set", e.key, "gemeinden")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=HISTORY_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
        supersedes_id=by_state,
    )
    for outcome in [row for row in p.rows.get("outcome", []) if row["result_set_id"] == by_state]:
        p.add("outcome", **{**outcome, "result_set_id": result_set})
    pages = bmi_gemeinden.municipal_pages(e.key)
    b = bmi_gemeinden.brochure(bmi_gemeinden.brochure_key(e.key))
    method = (
        f"Transcribed from the results by municipality in the official result brochure (pages {pages[0]} to "
        f"{pages[-1]} of {e.file}, 'Örtliche Wahlergebnisse nach Gemeinden'), which the Austrian Central Statistical "
        "Office prepared for the Federal Ministry of the Interior. The page images were straightened and read "
        "figure by figure; every number was checked against the totals the pages print: within each row the votes "
        "cast less the invalid votes against the valid votes and the candidates against the valid votes, the "
        "municipalities of each political district against the district row, the municipalities and the votes cast "
        "abroad of each state against the state row, and the state rows against the summary tables. Vienna is "
        "reported by municipal district. The set supersedes the one by state."
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    caveats = [
        (
            'The votes cast abroad ("darunter im Ausland abgegeben") are printed for each state and belong to no '
            "municipality, so they are stored as one unit per state without registered voters."
        ),
    ]
    if b.split:
        caveats.append(
            "Municipalities carry the codes of 1995. In the political districts "
            + ", ".join(sorted(b.split))
            + " some municipalities of 1995 were not yet separate at this election, so the municipalities of those "
            "districts are linked to the areas of 1995 only approximately."
        )
    for index, text in enumerate(caveats):
        p.add(
            "note",
            id=key("note", result_set, "caveat", index),
            entity_id=result_set,
            kind="caveat",
            body_md=text,
            lang="en",
        )
    over = sum(
        1
        for row in b.rows
        if row.kind == "gem" and row.registered is not None and row.rounds[e.round - 1]["cast"] > row.registered
    )
    if over:
        text = (
            f"In {over} municipalities more votes were cast than voters were registered. The brochure counts the "
            "votes of voters with a voting card (Wahlkarte) in the municipality where they voted, not the one that "
            "lists them, so the figures are stored as printed."
        )
        p.accept("accounting.turnout", text)
        p.add(
            "note",
            id=key("note", result_set, "turnout"),
            entity_id=result_set,
            kind="caveat",
            body_md=text,
            lang="en",
        )
    if e.round == 1:
        for number in pages:
            width, height = sizes[number]
            page_ids[number] = key("page", e.file, number)
            p.add(
                "source_page",
                id=page_ids[number],
                artifact_id=artifact_id(stored.storage_key),
                page_number=number,
                image_key=f"pages/{SOURCE}/{stored.sha256}/{number}.png",
                width=width,
                height=height,
            )
    else:
        for number in pages:
            page_ids[number] = key("page", e.file, number)
    context = bmi_gemeinden.Context(
        key=e.key,
        round_number=e.round,
        day=e.day,
        result_set=result_set,
        supersedes=by_state,
        election_id=election_id,
        contest=contest,
        options=options,
        national=national,
    )
    bmi_gemeinden.add_result_set(p, context, page_ids, sizes, key, area, AUSTRIA, MEASURES, VOTES)
