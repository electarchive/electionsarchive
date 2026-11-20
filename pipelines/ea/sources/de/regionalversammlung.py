"""Germany's regional assemblies: the Regionalversammlung of the Verband Region Stuttgart, the Verbandsversammlung of
the Regionalverband Ruhr, and the Bezirkstag of the Bezirksverband Pfalz.

Region Stuttgart is built from the Statistisches Landesamt Baden-Württemberg's final reports on the municipal and
regional elections (table 5, by Wahlkreis: the city of Stuttgart and the five counties), for 2004 to 2024. The
Regionalverband Ruhr is built from the Landeswahlleiterin's result pages for the Verbandsversammlung (one result for
the whole Verbandsgebiet) for 2020 and 2025. The Bezirksverband Pfalz is built from the Landeswahlleiter's volumes
for 2009, 2014, and 2019 (tables by city and county), which also hold the 2004 results as the comparison.
"""

import functools
import html
import io
import re
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "de.regionalversammlung"
GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
BW = "01a13bb5-c563-741b-83f7-4b7aeca76288"
NW = "01a13bb5-c563-741b-83f7-4b74611ca2d9"
RP = "01a13bb5-c563-741b-83f7-4b782d3a3364"
AREAS = Path(__file__).with_name("regionalversammlung_areas.tsv")
VG250 = "https://daten.gdz.bkg.bund.de/produkte/vg/vg250_ebenen_0101/2025/vg250_01-01.utm32s.shape.ebenen.zip"
KREISE = "vg250_01-01.utm32s.shape.ebenen/vg250_ebenen_0101/VG250_KRS"
STALA = "https://www.statistischebibliothek.de/mir/servlets/MCRFileNodeServlet"
RLP = "https://www.wahlen.rlp.de/fileadmin/wahlen.rlp.de/KW"
NRW = "https://www.wahlergebnisse.nrw/kommunalwahlen"

SCHEME_REGION = "de.kommunalverband"
SCHEME_UNIT = "de.regionalwahl"

MEASURE = {"votes": 1, "registered": 2, "voters": 3, "valid": 4, "invalid": 5}


class Region(NamedTuple):
    key: str
    land: str
    code: str
    members: tuple[str, ...]
    polity_de: str
    polity_en: str
    body_de: str
    body_en: str
    slug: str
    publisher: str
    title: str
    page: str
    license: str
    license_url: str
    attribution: str
    unit_noun: str


REGIONS = {
    "st": Region(
        "st",
        BW,
        "region-stuttgart",
        ("08111", "08115", "08116", "08117", "08118", "08119"),
        "Verband Region Stuttgart",
        "Stuttgart Region",
        "Regionalversammlung",
        "Regional Assembly of the Stuttgart Region",
        "stuttgart-regional-assembly",
        "Statistisches Landesamt Baden-Württemberg",
        "Endgültige Ergebnisse der Wahlen der Gemeinderäte und Kreisräte in Baden-Württemberg sowie der "
        "Regionalversammlung des Verbands Region Stuttgart (Statistische Berichte B VII 3)",
        "https://www.statistik-bw.de/Wahlen/Kommunalwahlen/",
        "Reproduction and distribution permitted with attribution (Statistisches Landesamt Baden-Württemberg)",
        "",
        "© Statistisches Landesamt Baden-Württemberg",
        "Stuttgart region electoral district (Wahlkreis)",
    ),
    "rvr": Region(
        "rvr",
        NW,
        "regionalverband-ruhr",
        (
            "05112",
            "05113",
            "05117",
            "05119",
            "05512",
            "05513",
            "05911",
            "05913",
            "05914",
            "05915",
            "05916",
            "05954",
            "05562",
            "05978",
            "05170",
        ),
        "Regionalverband Ruhr",
        "Ruhr Regional Association",
        "Verbandsversammlung",
        "Assembly of the Ruhr Regional Association",
        "ruhr-regional-assembly",
        "Landeswahlleiterin des Landes Nordrhein-Westfalen",
        "Wahlergebnisse in NRW: Kommunalwahlen",
        "https://www.wahlergebnisse.nrw/kommunalwahlen/",
        "Datenlizenz Deutschland, Zero, Version 2.0",
        "https://www.govdata.de/dl-de/zero-2-0",
        "Landeswahlleiterin NRW, wahlergebnisse.nrw",
        "Ruhr Regional Association electoral area",
    ),
    "pf": Region(
        "pf",
        RP,
        "bezirksverband-pfalz",
        (
            "07311",
            "07312",
            "07313",
            "07314",
            "07316",
            "07317",
            "07318",
            "07320",
            "07332",
            "07333",
            "07334",
            "07335",
            "07336",
            "07337",
            "07338",
            "07340",
        ),
        "Bezirksverband Pfalz",
        "Palatinate District Association",
        "Bezirkstag Pfalz",
        "Palatinate District Assembly",
        "palatinate-district-assembly",
        "Landeswahlleiter Rheinland-Pfalz",
        "Kommunalwahlen in Rheinland-Pfalz: Wahlbände",
        "https://www.wahlen.rlp.de/kommunalwahlen/ergebnisse-1",
        "Datenlizenz Deutschland, Namensnennung, Version 2.0",
        "https://www.govdata.de/dl-de/by-2-0",
        "© Landeswahlleiter Rheinland-Pfalz",
        "Palatinate city or county",
    ),
}

# Each regional election: the region, its date, and the files it is built from.
ST_REPORTS = {
    2024: ("https://www.statistik-bw.de/fileadmin/user_upload/Service/Veroeff/Statistische_Berichte/425224001_.pdf", 0),
    2019: ("https://www.statistik-bw.de/fileadmin/user_upload/Service/Veroeff/Statistische_Berichte/425224001_.pdf", 1),
    2014: (f"{STALA}/BWHeft_derivate_00003995/4252_14002.pdf", 0),
    2009: (f"{STALA}/BWHeft_derivate_00001777/4252_09002.pdf", 0),
    2004: (f"{STALA}/BWHeft_derivate_00001777/4252_09002.pdf", 1),
}
ST_CHECK = {2009: (f"{STALA}/BWHeft_derivate_00003995/4252_14002.pdf", 1)}
ST_DAYS = {
    2004: date(2004, 6, 13),
    2009: date(2009, 6, 7),
    2014: date(2014, 5, 25),
    2019: date(2019, 5, 26),
    2024: date(2024, 6, 9),
}
ST_UNITS = ("08111", "08115", "08116", "08117", "08118", "08119")
PF_REPORTS = {
    2019: (f"{RLP}/Wahlband_Kommunalwahlen_2019_Kreise_Bezirkstag.pdf", 0),
    2014: (f"{RLP}/Wahlband_Kommunalwahlen_2014.pdf", 0),
    2009: (f"{RLP}/Wahlband_Kommunalwahl_2009.pdf", 0),
    2004: (f"{RLP}/Wahlband_Kommunalwahl_2009.pdf", 1),
}
PF_CHECK = {2014: (f"{RLP}/Wahlband_Kommunalwahlen_2019_Kreise_Bezirkstag.pdf", 1)}
PF_DAYS = {
    2004: date(2004, 6, 13),
    2009: date(2009, 6, 7),
    2014: date(2014, 5, 25),
    2019: date(2019, 5, 26),
}
PF_CITIES = ("07311", "07312", "07313", "07314", "07316", "07317", "07318", "07320")
PF_COUNTIES = ("07332", "07333", "07334", "07335", "07336", "07337", "07338", "07340")
PF_NAMES = {
    "07311": "frankenthal",
    "07312": "kaiserslautern",
    "07313": "landau",
    "07314": "ludwigshafen",
    "07316": "neustadt",
    "07317": "pirmasens",
    "07318": "speyer",
    "07320": "zweibr",
    "07332": "bad d",
    "07333": "donnersberg",
    "07334": "germersheim",
    "07335": "kaiserslautern",
    "07336": "kusel",
    "07337": "weinstra",
    "07338": "rhein-pfalz",
    "07340": "westpfalz",
}
RVR_PAGES = {
    2020: f"{NRW}/2020/aktuell/a200000kw2000.shtml",
    2025: f"{NRW}/2025/aktuell/a200000kw2500.shtml",
}
RVR_FILES = {
    2020: (f"{NRW}/2020/KW20_Wählergruppen_KrfrStädte_Kreise.pdf", f"{NRW}/2020/KW20_Parteien.pdf"),
    2025: (f"{NRW}/2025/KW25_Heft_3.pdf",),
}
RVR_DAYS = {2020: date(2020, 9, 13), 2025: date(2025, 9, 14)}
RVR_GROUPS = {
    2020: {
        "Wählergruppe 1": "FW FREIE WÄHLER NRW",
        "Wählergruppe 2": "DAL",
        "Wählergruppe 3": "Aktiv",
        "Wählergruppe 4": "iGemRuhr",
        "Wählergruppe 5": "NATIONALES BÜNDNIS RUHRGEBIET",
        "Wählergruppe 6": "UWG: Freie Bürger",
    },
    2025: {"Wählergruppe 1": "AUF-Ruhr", "Wählergruppe 2": "GUT", "Wählergruppe 3": "WIN"},
}

GROUPS = {"FREIE WÄHLER", "Vielfalt", "SÖS", "Freie Regionale Rems-Murr", "FWG", "Gemeinsamer Wahlvorschlag FDP und FW"}
CANON = {"ödp": "ÖDP", "freie wähler": "FREIE WÄHLER", "fdp und fw": "Gemeinsamer Wahlvorschlag FDP und FW"}
PARTY_NAMES = {
    "CDU": "Christlich Demokratische Union Deutschlands",
    "SPD": "Sozialdemokratische Partei Deutschlands",
    "GRÜNE": "BÜNDNIS 90/DIE GRÜNEN",
    "FDP": "Freie Demokratische Partei",
    "AfD": "Alternative für Deutschland",
    "DIE LINKE": "DIE LINKE",
    "ÖDP": "Ökologisch-Demokratische Partei",
    "PIRATEN": "Piratenpartei Deutschland",
    "REP": "Die Republikaner",
    "NPD": "Nationaldemokratische Partei Deutschlands",
    "FREIE WÄHLER": "FREIE WÄHLER",
    "FWG": "Freie Wählergruppen",
}


class Unit(NamedTuple):
    registered: int | None
    voters: int | None
    invalid: int | None
    valid: int | None
    votes: dict[str, int]


class Result(NamedTuple):
    year: int
    units: dict[str, Unit]
    total: Unit | None
    seats: dict[str, int]
    seat_total: int
    sections: dict[str, str]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def slug(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})
    return re.sub(r"[^a-z0-9]+", "-", text.translate(table).lower()).strip("-")


def areas() -> dict[str, str]:
    lines = AREAS.read_text().splitlines()[1:]
    return dict(line.split("\t") for line in lines)


def region_area(region: str) -> str:
    return key("area", region)


def region_polity(region: str) -> str:
    return key("polity", region)


def body(region: str) -> str:
    return key("body", region)


def source_id(region: str) -> str:
    return key("source", region)


def party(region: str, label: str) -> str:
    return key("party", region, label)


def fetch() -> None:
    raw.fetch(SOURCE, VG250, suffix=".zip")
    urls = {url for url, _ in ST_REPORTS.values()} | {url for url, _ in PF_REPORTS.values()}
    for url in sorted(urls):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in [*RVR_PAGES.values()]:
        raw.fetch(SOURCE, url, suffix=".html")
    for urls_ in RVR_FILES.values():
        for url in urls_:
            raw.fetch(SOURCE, url, suffix=".pdf")


@functools.cache
def _pdf(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
    return done.stdout.decode("utf-8", errors="replace")


DASHES = {"–", "-", "—"}
COUNT = re.compile(r"^(?:\d{1,3}(?:[ .]\d{3})+|\d+)$")
PERCENT = re.compile(r"^\d+,\d$")


def _cells(line: str) -> list[str]:
    return re.split(r"\s{2,}", line.strip())


def _number(cell: str) -> int | None:
    if cell in DASHES:
        return None
    if not COUNT.match(cell):
        raise ValueError(f"{cell!r} is not a count")
    return int(cell.replace(" ", "").replace(".", ""))


def _isdata(cell: str) -> bool:
    return cell in DASHES or bool(COUNT.match(cell)) or bool(PERCENT.match(cell))


def canon(label: str) -> str:
    label = re.sub(r"\s+", " ", label).strip()
    return CANON.get(label.casefold(), label)


# ---------------------------------------------------------------------------------------------------------------
# Region Stuttgart


def stuttgart_table(url: str, years: tuple[int, int]) -> dict[int, Result]:
    text = _pdf(url)
    head = text.rindex("5. Ergebnis der Wahl der Regionalversammlung des Verbands Region Stuttgart")
    lines = text[head:].splitlines()
    split = next(i for i, line in enumerate(lines) if "Esslingen" in line and "Göppingen" in line)
    labels: dict[int, str] = {}
    rows: dict[int, list[str]] = {}
    phase: dict[int, str] = {}
    section: dict[int, str] = {}
    current = "votes"
    group = "party"
    pending: tuple[int, str] | None = None
    prefix: list[str] = []
    for line in lines[1:split]:
        cells = _cells(line)
        if not line.strip() or re.fullmatch(r"\d\)", cells[0]):
            continue
        if len(cells) == 1 and cells[0].casefold().startswith("wählervereinigungen"):
            group = "group"
            continue
        if len(cells) == 1 and cells[0].casefold().startswith(("parteien", "die parteien")):
            group = "party"
            continue
        if COUNT.match(cells[0]) and len(cells) >= 2 and int(cells[0]) < 100 and not _isdata(cells[1]):
            lfd, label = int(cells[0]), cells[1]
            data = cells[2:]
            if not data:
                pending = (lfd, label)
                continue
        elif pending is not None and not _isdata(cells[0]):
            lfd, label = pending
            label = f"{label} {cells[0]}"
            data = cells[1:]
            pending = None
        elif COUNT.match(cells[0]) and len(cells) == 1:
            continue
        else:
            if not any(_isdata(cell) for cell in cells):
                prefix.append(cells[0])
            continue
        if prefix and prefix[0].startswith("Gemeinsamer"):
            label = "Gemeinsamer Wahlvorschlag " + label
        prefix = []
        if label.startswith("Gewählte Mitglieder"):
            current = "seats"
        elif label.startswith("Ausgleichssitze"):
            current = "comp"
        labels[lfd] = label
        rows[lfd] = data
        phase[lfd] = current
        section[lfd] = group
    narrow = {
        lfd for lfd, name in labels.items() if lfd == 1 or phase[lfd] != "votes" or name.startswith("Wahlberechtigte")
    }
    wide_rows: dict[int, list[str]] = {}
    for line in lines[split + 1 :]:
        cells = _cells(line)
        if len(cells) < 3 or not COUNT.match(cells[-1]):
            continue
        lfd = int(cells[-1])
        if lfd not in labels or lfd in wide_rows:
            continue
        wide_rows[lfd] = cells[:-1]
    names = ["total", *ST_UNITS[:2]]
    names2 = list(ST_UNITS[2:])
    out: dict[int, Result] = {}
    for index, year in enumerate(years):
        units: dict[str, dict[str, int | None]] = {}
        votes: dict[str, dict[str, int]] = {}
        seats: dict[str, dict[str, int]] = {}
        sections: dict[str, str] = {}
        for lfd, label in labels.items():
            if phase[lfd] == "comp":
                continue
            for block, order in ((rows[lfd], names), (wide_rows.get(lfd), names2)):
                if block is None:
                    raise ValueError(f"{url}: row {lfd} {label} has no second block")
                width = 2 if lfd in narrow else 4
                if len(block) != width * len(order):
                    raise ValueError(f"{url}: row {lfd} {label}: {len(block)} cells for {len(order)} units")
                for position, name in enumerate(order):
                    cell = block[position * width + (index if width == 2 else 2 * index)]
                    count = _number(cell)
                    if phase[lfd] == "seats":
                        seats.setdefault(canon(label), {})[name] = count or 0
                    elif lfd == 1:
                        units.setdefault(name, {})["registered"] = count
                    elif lfd == 2:
                        units.setdefault(name, {})["voters"] = count
                    elif lfd == 3:
                        units.setdefault(name, {})["invalid"] = count
                    elif lfd == 4:
                        units.setdefault(name, {})["valid"] = count
                    elif phase[lfd] == "votes" and lfd > 4 and count is not None:
                        votes.setdefault(name, {})[canon(label)] = count
            if phase[lfd] == "votes" and lfd > 4:
                sections[canon(label)] = section[lfd]
        built = {
            name: Unit(
                units[name]["registered"],
                units[name]["voters"],
                units[name]["invalid"],
                units[name]["valid"],
                votes.get(name, {}),
            )
            for name in ["total", *ST_UNITS]
        }
        member = seats.pop("Gewählte Mitglieder")
        out[year] = Result(
            year,
            {name: built[name] for name in ST_UNITS},
            built["total"],
            {label: unit["total"] for label, unit in seats.items() if unit.get("total")},
            member["total"],
            sections,
        )
    return out


# ---------------------------------------------------------------------------------------------------------------
# Bezirksverband Pfalz


def _decode(text: str) -> str:
    return "".join(chr(ord(c) + 29) if ord(c) < 100 and c != " " else c for c in text)


def pfalz_table(url: str, years: tuple[int, int]) -> dict[int, Result]:
    text = _pdf(url)
    lines = text.splitlines()
    active = False
    table = ""
    order: list[str] = []
    unit: str | None = None
    totals: dict[str, dict[int, dict[str, int | None]]] = {}
    votes: dict[str, dict[int, dict[str, int]]] = {}
    seats: dict[int, dict[str, int]] = {0: {}, 1: {}}
    seat_total: dict[int, int] = {}
    in_seats = False
    index = {0: 1, 1: 3}
    heading = re.compile(r"^\s*T\s?(\d+)\s+(.*)")
    for number, line in enumerate(lines):
        found = heading.match(line)
        if found:
            title = found.group(2)
            active = "Bezirkstagswahlergebnisse" in title
            in_seats = False
            if active:
                if "reisfreie" in title:
                    kind = "city"
                elif "Landkreisen" in title or "Landkreise" in title:
                    kind = "county"
                else:
                    kind = "total"
                if kind != table:
                    table = kind
                    order = {"city": list(PF_CITIES), "county": list(PF_COUNTIES), "total": ["total"]}[kind]
                unit = None
            continue
        if not active or not line.strip():
            continue
        cells = _cells(line)
        if cells[0].startswith("Sitzverteilung"):
            in_seats = True
            continue
        following = next((later.strip() for later in lines[number + 1 : number + 4] if later.strip()), "")
        if len(cells) == 1 and following.startswith("Wahlberechtigte"):
            name = cells[0]
            if table == "total":
                if name.startswith("Bezirksverband"):
                    unit = "total"
                continue
            shifted = _decode(name)
            for code in order:
                wanted = PF_NAMES[code]
                if wanted in name.casefold() or wanted in shifted.casefold():
                    unit = code
                    order.remove(code)
                    totals.setdefault(unit, {})
                    votes.setdefault(unit, {0: {}, 1: {}})
                    break
            continue
        if unit is None:
            continue
        if table == "total" and unit == "total":
            totals.setdefault("total", {})
            votes.setdefault("total", {0: {}, 1: {}})
        label = cells[0]
        if in_seats and unit == "total":
            data = cells[1:]
            if label.startswith("Sitze insgesamt"):
                for number in (0, 1):
                    seat_total[number] = int(data[number])
            elif (
                label != "Merkmal" and len(data) >= 2 and all(cell in DASHES or COUNT.match(cell) for cell in data[:2])
            ):
                for number in (0, 1):
                    count = _number(data[number])
                    if count:
                        seats[number][canon(label)] = count
            continue
        if in_seats:
            continue
        if label in ("Wahlberechtigte",):
            for number in (0, 1):
                totals[unit].setdefault(number, {})["registered"] = _number(cells[1 + number])
        elif label in ("Wähler", "Ungültige Stimmen", "Gültige Stimmen") and len(cells) >= 5:
            name = {"Wähler": "voters", "Ungültige Stimmen": "invalid", "Gültige Stimmen": "valid"}[label]
            for number in (0, 1):
                totals[unit].setdefault(number, {})[name] = _number(cells[index[number]])
        elif (
            len(cells) >= 5
            and label.isupper()
            or (len(cells) >= 5 and label in ("GRÜNE", "DIE LINKE", "AfD"))
            or len(cells) >= 5
            and label not in ("Merkmal",)
            and unit is not None
            and _isdata(cells[1])
        ):
            for number in (0, 1):
                count = _number(cells[index[number]])
                if count is not None:
                    votes[unit][number][canon(label)] = count
    out: dict[int, Result] = {}
    for number, year in enumerate(years):
        built = {
            name: Unit(
                totals[name][number]["registered"],
                totals[name][number]["voters"],
                totals[name][number]["invalid"],
                totals[name][number]["valid"],
                votes[name][number],
            )
            for name in totals
        }
        wanted = [*PF_CITIES, *PF_COUNTIES]
        if sorted(name for name in built if name != "total") != sorted(wanted):
            raise ValueError(f"{url}: units {sorted(built)}")
        out[year] = Result(
            year,
            {name: built[name] for name in wanted},
            built["total"],
            seats[number],
            seat_total[number],
            {label: "group" if label in GROUPS else "party" for label in votes["total"][number]},
        )
    return out


# ---------------------------------------------------------------------------------------------------------------
# Regionalverband Ruhr


def rvr_page(year: int) -> Result:
    text = raw.cached(SOURCE, RVR_PAGES[year]).path.read_text(errors="replace")
    table: list[list[str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        if cells and any(cells):
            table.append(cells)
    start = next(i for i, row in enumerate(table) if row[0].startswith("Wahlberechtigte ohne"))
    seat_column = 6 if year == 2025 else 3
    figures: dict[str, int | None] = {}
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    seat_total = 0
    sections: dict[str, str] = {}
    listing = False
    for row in table[start:]:
        label = row[0]
        if label.startswith("Download"):
            break
        count = _number(row[1].replace("—", "-"))
        if label == "Wahlberechtigte insgesamt":
            figures["registered"] = count
        elif label.startswith("Wähler/-innen"):
            figures["voters"] = count
        elif label.startswith("Ungültige"):
            figures["invalid"] = count
        elif label.startswith("Gültige Stimmen"):
            figures["valid"] = count
            seat_total = int(row[seat_column])
            listing = True
        elif listing and count is not None and label not in ("Einzelbewerber/-innen", "Sonstige"):
            name = re.sub(r"(?<=\b\w) (?=\w\b)", "", label)
            name = "TIERSCHUTZ" if name.replace(" ", "") == "TIERSCHUTZ" else name
            votes[name] = count
            sections[name] = "group" if name.startswith("Wählergruppe") else "party"
            given = _number(row[seat_column].replace("—", "-"))
            if given:
                seats[name] = given
    unit = Unit(figures["registered"], figures["voters"], figures["invalid"], figures["valid"], votes)
    return Result(year, {"rvr": unit}, None, seats, seat_total, sections)


# ---------------------------------------------------------------------------------------------------------------
# Checks


def check_results(region: str, results: dict[int, Result]) -> list[str]:
    notes: list[str] = []
    for year, result in sorted(results.items()):
        for name, unit in result.units.items():
            added = sum(unit.votes.values())
            if unit.valid is not None and added != unit.valid:
                notes.append(f"{region} {year} {name}: list votes {added} against valid {unit.valid}")
            valid, invalid, voters = unit.valid, unit.invalid, unit.voters
            if valid is not None and invalid is not None and voters is not None and valid + invalid != voters:
                notes.append(f"{region} {year} {name}: valid + invalid against voters {voters}")
        if sum(result.seats.values()) != result.seat_total:
            notes.append(f"{region} {year}: seats {sum(result.seats.values())} against {result.seat_total}")
        total = result.total
        if total is not None:
            for field in ("registered", "voters", "invalid", "valid"):
                added = sum(getattr(unit, field) or 0 for unit in result.units.values())
                if added != getattr(total, field):
                    notes.append(f"{region} {year}: {field} units sum {added} against total {getattr(total, field)}")
            labels = {label for unit in result.units.values() for label in unit.votes}
            for label in sorted(labels | set(total.votes)):
                added = sum(unit.votes.get(label, 0) for unit in result.units.values())
                if added != total.votes.get(label, 0):
                    notes.append(
                        f"{region} {year}: {label} units sum {added} against total {total.votes.get(label, 0)}"
                    )
    return notes


def hare(votes: dict[str, int], seats: int) -> dict[str, int]:
    total = sum(votes.values())
    base = {label: v * seats // total for label, v in votes.items()}
    rest = sorted(votes, key=lambda label: -(votes[label] * seats % total))
    for label in rest[: seats - sum(base.values())]:
        base[label] += 1
    return {label: n for label, n in base.items() if n}


def sainte_lague(votes: dict[str, int], seats: int) -> dict[str, int]:
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(votes, key=lambda label: votes[label] / (2 * won[label] + 1))
        won[best] += 1
    return {label: n for label, n in won.items() if n}


def dhondt(votes: dict[str, int], seats: int) -> dict[str, int]:
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(votes, key=lambda label: votes[label] / (won[label] + 1))
        won[best] += 1
    return {label: n for label, n in won.items() if n}


# ---------------------------------------------------------------------------------------------------------------
# Data


@functools.cache
def stuttgart() -> dict[int, Result]:
    found: dict[int, Result] = {}
    cache: dict[tuple[str, int], dict[int, Result]] = {}
    for year, (url, index) in ST_REPORTS.items():
        years = (year, year - 5) if index == 0 else (year + 5, year)
        if (url, index) not in cache:
            cache[(url, 0)] = stuttgart_table(url, years)
        found[year] = cache[(url, 0)][year]
    return found


@functools.cache
def stuttgart_notes() -> list[str]:
    results = stuttgart()
    notes = check_results("st", results)
    url, _ = ST_CHECK[2009]
    other = stuttgart_table(url, (2014, 2009))[2009]
    mine = results[2009]
    if other.units != mine.units or other.total != mine.total or other.seats != mine.seats:
        notes.append("st 2009: the 2014 report's comparison column differs from the 2009 report")
    return notes


@functools.cache
def pfalz() -> dict[int, Result]:
    found: dict[int, Result] = {}
    cache: dict[str, dict[int, Result]] = {}
    for year, (url, index) in PF_REPORTS.items():
        if url not in cache:
            first = year if index == 0 else year + 5
            cache[url] = pfalz_table(url, (first, first - 5))
        found[year] = cache[url][year]
    return found


@functools.cache
def pfalz_notes() -> list[str]:
    results = pfalz()
    notes = check_results("pf", results)
    url, _ = PF_CHECK[2014]
    other = pfalz_table(url, (2019, 2014))[2014]
    mine = results[2014]
    for name, unit in mine.units.items():
        theirs = other.units[name]
        shared = {label for label in unit.votes if label in theirs.votes}
        if unit[:4] != theirs[:4] or any(unit.votes[label] != theirs.votes[label] for label in shared):
            notes.append(f"pf 2014 {name}: the 2019 volume's comparison column differs from the 2014 volume")
    return notes


@functools.cache
def rvr() -> dict[int, Result]:
    return {year: rvr_page(year) for year in RVR_PAGES}


def rvr_notes() -> list[str]:
    return check_results("rvr", rvr())


def pfalz_rule(year: int, result: Result) -> dict[str, int]:
    votes = result.total.votes if result.total else {}
    method = hare if year < 2019 else sainte_lague
    return method(votes, result.seat_total)


# ---------------------------------------------------------------------------------------------------------------
# Packages

SYSTEMS = {
    "st": (
        ("dhondt-2009", date(1994, 1, 1), date(2014, 1, 1), {"formula": "dhondt"}),
        ("sainte-lague-schepers", date(2014, 1, 1), None, {"formula": "sainte_lague_schepers"}),
    ),
    "rvr": (("sainte-lague-schepers", date(2020, 1, 1), None, {"formula": "sainte_lague_schepers"}),),
    "pf": (
        ("hare", date(1994, 1, 1), date(2019, 1, 1), {"formula": "hare"}),
        ("sainte-lague-schepers", date(2019, 1, 1), None, {"formula": "sainte_lague_schepers"}),
    ),
}
METHOD = {
    "st": (
        "The Regionalversammlung of the Verband Region Stuttgart is elected every five years by the voters of the "
        "city of Stuttgart and the five counties of Böblingen, Esslingen, Göppingen, Ludwigsburg, and Rems-Murr, who "
        "cast one vote for a party or a voters' association (Wählervereinigung). Each of the six electoral districts "
        "(Wahlkreise) has a number of seats set by its population, and a party that wins more seats in the whole "
        "region than its districts give it is balanced out by compensation seats, so the seats of the assembly follow "
        "the votes across the region. The Statistisches Landesamt Baden-Württemberg publishes the final result for "
        "each Wahlkreis. Seats are shown for the whole region, with compensation seats included."
    ),
    "rvr": (
        "The Verbandsversammlung of the Regionalverband Ruhr is elected by the voters of the 11 cities and four "
        "counties of the association, who cast one vote for a party or an association of voters, in one list election "
        "for the whole association area, at the same time as the municipal elections of North Rhine-Westphalia. The "
        "Landeswahlleiterin publishes the result for the whole association area, and the seats are shared by the "
        "Sainte-Laguë/Schepers method."
    ),
    "pf": (
        "The Bezirkstag of the Bezirksverband Pfalz has 29 members, elected by the voters of the eight cities and "
        "eight counties of the Palatinate at the same time as the municipal elections. Each voter has one vote for "
        "a closed list of a party or an association of voters, and the Landeswahlleiter publishes the result for the "
        "association and for each city and county."
    ),
}


def _shapes() -> dict[str, shapely.Geometry]:
    with zipfile.ZipFile(raw.cached(SOURCE, VG250).path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{KREISE}.shp")),
            shx=io.BytesIO(archive.read(f"{KREISE}.shx")),
            dbf=io.BytesIO(archive.read(f"{KREISE}.dbf")),
            encoding="utf-8",
        )
        found: dict[str, list[shapely.Geometry]] = {}
        for record, shape in zip(reader.records(), reader.shapes(), strict=True):
            if record is not None and shape is not None and record["GF"] == 4:
                found.setdefault(str(record["AGS"]), []).append(shapely.geometry.shape(shape.__geo_interface__))
    return {code: shapely.union_all(parts) for code, parts in found.items()}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Outlines of the Verband Region Stuttgart, the Regionalverband Ruhr, and the Bezirksverband Pfalz, each the "
        "union of its member cities and counties in the Federal Agency for Cartography and Geodesy's administrative "
        "areas at 1 January 2025",
    )
    p.external |= {GERMANY, BW, NW, RP, *areas().values()}
    bkg = key("source", "bkg")
    p.add(
        "source",
        id=bkg,
        kind="official_body",
        publisher="Bundesamt für Kartographie und Geodäsie",
        title="Verwaltungsgebiete 1:250 000, Ebenen, Stand 01.01.2025 (VG250 01.01.)",
        url="https://gdz.bkg.bund.de/index.php/default/verwaltungsgebiete-1-250-000-ebenen-stand-01-01-vg250-ebenen-01-01.html",
        license="Datenlizenz Deutschland – Namensnennung – Version 2.0",
        license_url="https://www.govdata.de/dl-de/by-2-0",
        attribution="© GeoBasis-DE / BKG (2025)",
    )
    p.artifact(raw.cached(SOURCE, VG250), bkg)
    p.add(
        "scheme",
        code=SCHEME_REGION,
        polity_id=GERMANY,
        kind="administrative",
        rank=5,
        noun="regional association",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME_REGION, 2025),
        scheme_code=SCHEME_REGION,
        vintage="2025",
        as_of=date(2025, 1, 1),
    )
    shapes = _shapes()
    project = pyproj.Transformer.from_crs(25832, 4326, always_xy=True)
    for region in REGIONS.values():
        id = region_area(region.key)
        p.add("area", id=id, scheme_code=SCHEME_REGION)
        p.add("area_code", area_id=id, code=region.code)
        p.name(id, region.polity_de, "de", "official")
        p.name(id, region.polity_en, "en", "english", False)
        union = shapely.union_all([shapes[code] for code in region.members]).simplify(20)
        moved = shapely.transform(union, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
        parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
        outline = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=bkg,
        )
    return p


def _method(region: str) -> str:
    return METHOD[region]


def registry(found: dict[str, dict[int, Result]]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Verband Region Stuttgart, the Regionalverband Ruhr, and the Bezirksverband Pfalz as polities, their "
        "assemblies, the systems that fill them, and the parties and voters' associations on their ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {GERMANY, BW, NW, RP, *areas().values()}
    p.add(
        "scheme",
        code=SCHEME_UNIT,
        polity_id=GERMANY,
        kind="electoral_district",
        rank=6,
        noun="regional election reporting unit",
    )
    for region in REGIONS.values():
        src = source_id(region.key)
        p.add(
            "source",
            id=src,
            kind="official_body",
            publisher=region.publisher,
            title=region.title,
            url=region.page,
            license=region.license,
            license_url=region.license_url or None,
            attribution=region.attribution,
        )
        polity = region_polity(region.key)
        p.add("polity", id=polity, kind="region", iso_code=None)
        p.add("polity_parent", polity_id=polity, parent_id=region.land)
        p.add("polity_territory", polity_id=polity, area_id=region_area(region.key))
        p.name(polity, region.polity_de, "de", "official")
        p.name(polity, region.polity_en, "en", "english", False)
        post = body(region.key)
        p.add("body", id=post, polity_id=polity, kind="council")
        p.name(post, region.body_de, "de", "official")
        p.name(post, region.body_en, "en", "english", False)
        for name, since, until, rules in SYSTEMS[region.key]:
            system = key("electoral_system", region.key, name)
            p.add(
                "electoral_system",
                id=system,
                polity_id=polity,
                valid_from=since,
                valid_to=until,
                rules=rules,
                rules_version=1,
            )
        labels: dict[str, str] = {}
        for result in found[region.key].values():
            labels |= result.sections
        for label, kind in sorted(labels.items()):
            id = party(region.key, label)
            p.add("party", id=id, kind="independent_group" if kind == "group" else "party", polity_id=GERMANY)
            p.add("external_id", entity_id=id, scheme=f"de.regionalversammlung.{region.key}.party", value=label)
            if label in PARTY_NAMES and PARTY_NAMES[label] != label:
                p.name(id, label, "de", "abbreviation")
                p.name(id, PARTY_NAMES[label], "de", "official", False)
            else:
                p.name(id, label, "de", "official")
    return p


def system_for(region: str, day: date) -> str:
    for name, since, until, _ in SYSTEMS[region]:
        if since <= day and (until is None or day < until):
            return key("electoral_system", region, name)
    raise ValueError(f"{region} has no system on {day}")


def election(region_key: str, result: Result, day: date, artifacts: list[str]) -> Package:
    region = REGIONS[region_key]
    year = result.year
    p = Package(
        f"{SOURCE}/{region_key}-{year}",
        SOURCE,
        f"The {year} election of the {region.body_de} of the {region.polity_de}, by "
        + ("city and county" if region_key != "rvr" else "association area"),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {GERMANY, region.land, *areas().values()}
    src = source_id(region_key)
    for url in artifacts:
        p.uses(raw.cached(SOURCE, url))
    polity = region_polity(region_key)
    election_id = key("election", region_key, year)
    path = f"de/{day}-{region.slug}"
    p.add(
        "election",
        id=election_id,
        polity_id=polity,
        kind="local",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{day:%B} {day.day}, {year} {region.body_en} election", "en", "official")
    p.name(election_id, f"Wahl der {region.body_de} am {day:%d.%m.%Y}", "de", "native", False)
    contest = key("contest", region_key, year)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=body(region_key),
        district_area_id=region_area(region_key),
        magnitude=result.seat_total,
        round_number=1,
        electoral_system_id=system_for(region_key, day),
        default_result_kind="counted",
    )
    p.name(contest, f"{year} {region.body_en} election", "en", "official")
    p.name(contest, f"Wahl der {region.body_de} {year}", "de", "native", False)
    p.add("slug", path=f"{path}/{slug(region.body_en)}", entity_id=contest, is_current=True)
    result_set = key("result_set", region_key, year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=src,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = _method(region_key)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    labels: list[str] = []
    for unit in result.units.values():
        for label in unit.votes:
            if label not in labels:
                labels.append(label)
    options: dict[str, str] = {}
    for position, label in enumerate(labels, start=1):
        option = options[label] = key("option", region_key, year, label)
        printed = label
        if region_key == "rvr" and label in RVR_GROUPS[year]:
            printed = f"{label} ({RVR_GROUPS[year][label]})"
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(region_key, label),
            ballot_position=position,
            ballot_label=printed,
        )
    for label, seats in result.seats.items():
        p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=seats)
    for code, unit in result.units.items():
        unit_id = key("unit", region_key, year, code)
        if region_key == "rvr":
            area_id = region_area("rvr")
            unit_code = f"{day}:RVR"
        else:
            area_id = areas()[code]
            unit_code = f"{day}:{code}"
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=GERMANY,
            scheme_code=SCHEME_UNIT,
            code=unit_code,
            area_relation="equals",
            area_id=area_id,
            kind="standard",
        )
        for measure, value in (
            ("registered", unit.registered),
            ("voters", unit.voters),
            ("invalid", unit.invalid),
            ("valid", unit.valid),
        ):
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit_id,
                    option_id=None,
                    channel=0,
                    measure=MEASURE[measure],
                    count_round=0,
                    flags=0,
                    value=value,
                )
        for label, votes in unit.votes.items():
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit_id,
                option_id=options[label],
                channel=0,
                measure=MEASURE["votes"],
                count_round=0,
                flags=0,
                value=votes,
            )
    total = result.total
    if total is not None:
        for measure, value in (
            ("registered", total.registered),
            ("voters", total.voters),
            ("invalid", total.invalid),
            ("valid", total.valid),
        ):
            if value is not None:
                p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURE[measure], value=value)
        for label, votes in total.votes.items():
            p.add("declared", result_set_id=result_set, option_id=options[label], measure=MEASURE["votes"], value=votes)
    if region_key == "st" and year == 2014:
        p.accept(
            "accounting.votes_valid",
            "The Statistisches Landesamt's table for 2014 prints 197,541 valid votes for the Ludwigsburg Wahlkreis, "
            "while the votes it prints for the lists there add up to 197,595, 54 more, "
            "the same 54 by which the REP's Wahlkreis votes exceed the printed regional total.",
        )
        p.accept(
            "accounting.declared",
            "The REP's votes in the six Wahlkreise of the 2014 table add up to 15,686, while the table's region "
            "total is 15,632; the 54 votes are the difference in Ludwigsburg between its list votes and its valid votes.",
        )
    return p


def _artifacts(region: str, year: int) -> list[str]:
    if region == "st":
        return [ST_REPORTS[year][0]]
    if region == "pf":
        return [PF_REPORTS[year][0]]
    return [RVR_PAGES[year], *RVR_FILES[year]]


def all_results() -> dict[str, dict[int, Result]]:
    return {"st": stuttgart(), "rvr": rvr(), "pf": pfalz()}


def parse() -> None:
    found = all_results()
    notes = [*stuttgart_notes(), *pfalz_notes(), *rvr_notes()]
    for note in notes:
        print(note, flush=True)
    for year, result in pfalz().items():
        expected = pfalz_rule(year, result)
        if expected != result.seats:
            print(f"pf {year}: the allocation rule gives {expected}, the source {result.seats}", flush=True)
    geo().write()
    registry(found).write()
    days = {"st": ST_DAYS, "rvr": RVR_DAYS, "pf": PF_DAYS}
    for region, results in found.items():
        for year, result in results.items():
            election(region, result, days[region][year], _artifacts(region, year)).write()


def packages() -> list[str]:
    keys = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    keys += [f"{SOURCE}/st-{year}" for year in ST_REPORTS]
    keys += [f"{SOURCE}/rvr-{year}" for year in RVR_PAGES]
    keys += [f"{SOURCE}/pf-{year}" for year in PF_REPORTS]
    return keys


def emit() -> None:
    package.run("load", packages())
