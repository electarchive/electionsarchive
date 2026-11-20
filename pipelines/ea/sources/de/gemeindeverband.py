"""Council elections of the municipal associations of Germany: the Verbandsgemeinden of Rhineland-Palatinate,
Saxony-Anhalt, and Brandenburg.

Rhineland-Palatinate is read from the Landeswahlleiter's printed volumes on the municipal elections (2009, 2014,
2019), Saxony-Anhalt from the Statistisches Landesamt's result files for the Verbandsgemeinderatswahlen (2009, 2014,
2019, 2024), and Brandenburg's one Verbandsgemeinde from the Landeswahlleiter's result pages. The Verbandsgemeinden
come with the outlines of the federal mapping agency's administrative areas (VG250).
"""

import functools
import io
import re
import subprocess
import zipfile
from datetime import date
from html import unescape
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

SOURCE = "de.gemeindeverband"
ST_BASE = "https://wahlergebnisse.sachsen-anhalt.de/wahlen"
ST_FILES = {
    2009: f"{ST_BASE}/vw09/and/vw09dat1.csv",
    2014: f"{ST_BASE}/vw14/and/vw14dat1.csv",
    2019: f"{ST_BASE}/vw19/erg/csv/vw19dat1.csv",
    2024: f"{ST_BASE}/vw24/erg/csv/vw24erf_20250910.csv",
}
ST_NAMED_2014 = f"{ST_BASE}/vw14/erg/csv/vw14dat1.csv"
ST_LAYOUT_2009 = f"{ST_BASE}/vw09/and/DSB_VBG_2009.pdf"
RP_BASE = "https://www.wahlen.rlp.de/fileadmin/wahlen.rlp.de/KW"
RP_FILES = {
    2009: f"{RP_BASE}/Wahlband_Kommunalwahl_2009.pdf",
    2014: f"{RP_BASE}/Wahlband_Kommunalwahlen_2014.pdf",
    2019: f"{RP_BASE}/Wahlband_Kommunalwahlen_2019_Verbandsgemeindeebene.pdf",
}
RP_DAYS = {2009: date(2009, 6, 7), 2014: date(2014, 5, 25), 2019: date(2019, 5, 26)}
RP_PRIOR_DAYS = {2004: date(2004, 6, 13)}
BB_BASE = "https://wahlergebnisse.brandenburg.de/625031/400/20240609/verbandsgemeindevertretungswahl_vg"
BB_DAY = date(2024, 6, 9)
VG250 = "https://daten.gdz.bkg.bund.de/produkte/vg/vg250_ebenen_0101/2025/vg250_01-01.utm32s.shape.ebenen.zip"
VWG = "vg250_01-01.utm32s.shape.ebenen/vg250_ebenen_0101/VG250_VWG"


class Result(NamedTuple):
    """One council election in one Verbandsgemeinde."""

    land: str
    year: int
    day: date
    name: str
    kreis: str
    registered: int | None
    voters: int | None
    invalid: int | None
    valid: int | None
    votes: int | None
    seats: int | None
    lists: list[tuple[str, int, int]]


def num(text: str) -> int | None:
    text = text.replace("\xa0", "").replace(" ", "").replace(".", "").strip()
    return int(text) if text.isdigit() else None


# Saxony-Anhalt

ST_CODES_2009 = {"D01": "CDU", "D02": "DIE LINKE", "D03": "SPD", "D04": "FDP", "D05": "GRÜNE", "D12": "NPD"}
TOTALS = {
    "WG (Summe)": "Wählergruppen",
    "EB (Summe)": "Einzelbewerber",
    "D18": "Wählergruppen",
    "D19": "Einzelbewerber",
}


def decode(url: str) -> list[list[str]]:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
    return [[cell.strip() for cell in line.rstrip("\r").split(";")] for line in text.splitlines() if line.strip()]


def st_results(year: int) -> list[Result]:
    rows = decode(ST_FILES[year])
    header, rows = rows[0], rows[1:]
    if year == 2014:
        header = decode(ST_NAMED_2014)[0]
    head = [cell.split(" - ")[0].strip() for cell in header]
    first = {code: i for i, code in reversed(list(enumerate(head))) if code}
    low = [cell.lower() for cell in head]

    def find(*names: str) -> int:
        return next(i for i, cell in enumerate(low) if cell in names)

    day_at, key_at, name_at = find("datum"), find("schlüsselnummer", "ags", "nr"), find("name")
    d_at, s_at = first["D"], first["S"]
    votes_at = list(range(d_at + 1, s_at))
    seats_at = [i for i in range(s_at + 1, len(header)) if header[i]]
    if len(votes_at) != len(seats_at):
        raise ValueError(f"{year}: {len(votes_at)} vote columns and {len(seats_at)} seat columns")
    labels: list[str] = []
    for i in votes_at:
        text = header[i]
        code = head[i]
        label = (ST_CODES_2009.get(code) if year == 2009 else None) or TOTALS.get(code) or TOTALS.get(text)
        labels.append(label or re.sub(r"^D\d+ - ", "", text))
    out: list[Result] = []
    for row in rows:
        if "VBG" not in row[:7]:
            continue
        d, m, y = row[day_at].split(".")
        lists = [
            (label, num(row[v]) or 0, num(row[s]) if s < len(row) else 0)
            for label, v, s in zip(labels, votes_at, seats_at, strict=True)
        ]
        lists = [(label, v, s or 0) for label, v, s in lists if v or s]
        out.append(
            Result(
                "st",
                year,
                date(int(y), int(m), int(d)),
                re.sub(r"^Verbandsgemeinde\s+", "", row[name_at]).strip(),
                "15090" if row[key_at].startswith("15091 ") and "Arneburg" in row[name_at] else row[key_at][:5],
                num(row[first["A"]]),
                num(row[first["B"]]),
                num(row[first["C1"]]),
                num(row[first["C2"]]),
                num(row[d_at]),
                num(row[s_at]),
                lists,
            )
        )
    return out


# Rhineland-Palatinate

TOKEN = re.compile(r"-?\d{1,3}(?: \d{3})+(?:,\d+)?|-?\d[\d.]*(?:,\d+)?|-")
METRICS = {
    "Wahlberechtigte": "registered",
    "Wähler": "voters",
    "Ungültige Stimmzettel": "invalid",
    "Gültige Stimmzettel": "valid",
    "Gültige Stimmen": "votes",
}
RP_ALIASES = {
    "Mayen Koblenz": "Mayen-Koblenz",
    "Altenkirchen": "Altenkirchen (Westerwald)",
    "Kaiserlautern": "Kaiserslautern",
}
# The volume of 2019 heads one association "Wahl verschoben" (election postponed) and prints its result without a day.
UNDATED: set[tuple[int, str]] = set()
RP_TABLES = {2009: "5", 2014: "5", 2019: "2"}
RP_KREISE = {
    "Ahrweiler",
    "Altenkirchen (Westerwald)",
    "Alzey-Worms",
    "Bad Dürkheim",
    "Bad Kreuznach",
    "Bernkastel-Wittlich",
    "Birkenfeld",
    "Cochem-Zell",
    "Donnersbergkreis",
    "Eifelkreis Bitburg-Prüm",
    "Germersheim",
    "Kaiserslautern",
    "Kusel",
    "Mainz-Bingen",
    "Mayen-Koblenz",
    "Mayen Koblenz",
    "Altenkirchen",
    "Kaiserlautern",
    "Neuwied",
    "Rhein-Hunsrück-Kreis",
    "Rhein-Lahn-Kreis",
    "Rhein-Pfalz-Kreis",
    "Südliche Weinstraße",
    "Südwestpfalz",
    "Trier-Saarburg",
    "Vulkaneifel",
    "Westerwaldkreis",
}
SUMMARY = {"summe wählergruppen": "Wählergruppen", "sonstige": "Sonstige"}


def pdf_text(url: str) -> list[list[str]]:
    path = raw.cached(SOURCE, url).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return [page.split("\n") for page in text.split("\f")]


def cells(line: str) -> tuple[str, list[tuple[str, int]]]:
    """A table row's label and its figures, each with the column where it ends."""
    found = re.match(r"\s*(\D+?)(?:\s{2,}|\s(?=-?\d))(.*)$", line)
    if not found:
        return "", []
    start = found.start(2)
    return found.group(1).strip(), [(m.group(0), start + m.end()) for m in TOKEN.finditer(found.group(2))]


def rp_results(year: int, earlier: bool = False) -> list[Result]:
    """The results printed for `year`, or with `earlier` the comparison columns for the election five years before,
    which a volume prints for each association whose territory did not change."""
    pages = pdf_text(RP_FILES[year])
    section = False
    kreis = ""
    out: list[Result] = []
    current: dict[str, int | None] = {}
    lists: list[tuple[str, int, int]] = []
    name = ""
    usable = True
    result_year = year - 5 if earlier else year

    def close() -> None:
        nonlocal current, lists
        if name and usable:
            out.append(
                Result(
                    "rp",
                    result_year,
                    RP_PRIOR_DAYS[result_year]
                    if earlier
                    else (date(year, 1, 1) if (year, name) in UNDATED else RP_DAYS[year]),
                    name,
                    kreis,
                    current.get("registered"),
                    current.get("voters"),
                    current.get("invalid"),
                    current.get("valid"),
                    current.get("votes"),
                    current.get("seats"),
                    lists,
                )
            )
        current, lists = {}, []

    e_votes: int | None = None
    e_seats: int | None = None
    want_votes: int | None = None
    want_seats: int | None = None
    for page in pages:
        for at, line in enumerate(page):
            head = line.strip()
            table = re.match(r"T\s?(\d+)\s+Kommunalwahlergebnisse", head)
            if table:
                if section and table.group(1) != RP_TABLES[year]:
                    close()
                    name = ""
                section = table.group(1) == RP_TABLES[year]
                continue
            if not section:
                continue
            found_kreis = re.match(r"(?:Landkreis )?(.+?)(?:, Landkreis)?\**$", head)
            if found_kreis and found_kreis.group(1) in RP_KREISE:
                close()
                name = ""
                kreis = RP_ALIASES.get(found_kreis.group(1), found_kreis.group(1))
                continue
            if "Wahl im Herbst" in head:
                close()
                name = ""
                continue
            postponed = "Wahl verschoben" in head
            found_vg = re.match(r"Verbandsgemeinde (\D+?)\**$", re.sub(r" - \(Anm\..*$", "", head))
            following = next((text.strip() for text in page[at + 1 :] if text.strip()), "")
            if (
                not found_vg
                and section
                and head
                and following.startswith("Wahlberechtigte")
                and not re.search(r"\d", head)
            ):
                # A few headings omit the word Verbandsgemeinde.
                found_vg = re.match(r"(\D+?)\**$", head)
            if found_vg or postponed:
                close()
                name = re.sub(r"^Verbandsgemeinde | - Wahl verschoben.*$| - \(Anm\..*$|\**$", "", head).strip()
                if postponed:
                    UNDATED.add((year, name))
                # A footnote on the heading says the earlier figures are those of another body.
                usable = not (earlier and (head.endswith("*") or "(Anm." in head or postponed))
                e_votes = e_seats = want_votes = want_seats = None
                continue
            if not name:
                continue
            label, figures = cells(line)
            if (
                not label
                or label.startswith(("darunter", "dar.", "Kommunalwahlen", "©", "Anzahl", "Diff", "*"))
                or label == "Merkmal"
            ):
                continue
            if label in METRICS:
                field = METRICS[label]
                if not figures:
                    raise ValueError(f"{year} {name}: {label} has no figures")
                if field == "registered":
                    e_votes = figures[0][1]
                    want_votes = e_votes if not earlier else (figures[1][1] if len(figures) > 1 else None)
                if e_votes is None or abs(figures[0][1] - e_votes) > 3:
                    raise ValueError(f"{year} {name}: {label} has no figure for the election")
                if want_votes is None:
                    usable = False
                    continue
                picked = [t for t, end in figures if abs(end - want_votes) <= 3]
                if len(picked) != 1:
                    usable = usable and not earlier
                    if not earlier:
                        raise ValueError(f"{year} {name}: {label} {figures}")
                    continue
                current[field] = num(picked[0])
                if field == "votes":
                    seat = [(t, end) for t, end in figures[1:] if end - e_votes > 45]
                    if not seat:
                        raise ValueError(f"{year} {name}: no seat total in {figures}")
                    e_seats = seat[0][1]
                    if earlier:
                        prior = [(t, end) for t, end in seat if 4 <= end - e_seats <= 11]
                        want_seats = prior[0][1] if prior else None
                        current["seats"] = num(prior[0][0]) if prior else None
                        usable = usable and bool(prior)
                    else:
                        want_seats = e_seats
                        current["seats"] = num(seat[0][0])
                continue
            label = SUMMARY.get(label.lower(), label)
            if not usable:
                continue
            if want_seats is None or want_votes is None:
                raise ValueError(f"{year} {name}: {label!r} comes before the totals")
            votes = [t for t, end in figures if abs(end - want_votes) <= 3]
            seats = [t for t, end in figures if abs(end - want_seats) <= 2]
            if len(votes) > 1 or len(seats) > 1:
                raise ValueError(f"{year} {name}: {label!r} {figures}")
            v, s = num(votes[0]) if votes else 0, num(seats[0]) if seats else 0
            if v or s:
                lists.append((label, v or 0, s or 0))
    close()
    return out


# Brandenburg

BB_COMMUNES = ("120625031024", "120625031128", "120625031341", "120625031500")


class Commune(NamedTuple):
    ags: str
    name: str
    registered: int
    voters: int
    invalid: int
    votes: int
    lists: list[tuple[str, int]]


def bb_page(url: str) -> tuple[str, list[str]]:
    html = raw.cached(SOURCE, url).path.read_text(encoding="utf-8")
    html = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL)
    text = unescape(re.sub(r"<[^>]*>", "\n", html))
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return text, [line for line in lines if line]


def bb_table(lines: list[str]) -> dict[str, int]:
    """The accounting rows and each list's votes, in the order the table prints them."""
    start = lines.index("Merkmal")
    end = next(i for i, line in enumerate(lines) if i > start and line.startswith("* Die Anzahl"))
    rows = lines[start + 4 : end]
    out: dict[str, int] = {}
    i = 0
    while i < len(rows):
        out[rows[i]] = num(rows[i + 1]) or 0
        i += 3 if rows[i + 2].endswith("%") or rows[i + 2] == "-" else 2
    return out


def bb_result() -> tuple[Result, list[Commune]]:
    _, lines = bb_page(f"{BB_BASE}/ergebnisse.html")
    table = bb_table(lines)
    elected: dict[str, int] = {}
    at = lines.index("Gewählte/-r")
    stop = next(i for i, line in enumerate(lines) if i > at and line.startswith("Kandidierendenstimmen"))
    rows = lines[at + 3 : stop]
    for i in range(0, len(rows) - 2, 3):
        elected[rows[i + 1]] = elected.get(rows[i + 1], 0) + 1
    fixed = {"Wahlberechtigte", "Wählende", "Ungültige Stimmzettel", "Gültige Stimmen*"}
    parties = [(label, votes, elected.get(label, 0)) for label, votes in table.items() if label not in fixed]
    if set(elected) - {label for label, _, _ in parties}:
        raise ValueError("a list with elected members has no votes")
    result = Result(
        "bb",
        2024,
        BB_DAY,
        "Liebenwerda",
        "12062",
        table["Wahlberechtigte"],
        table["Wählende"],
        table["Ungültige Stimmzettel"],
        table["Wählende"] - table["Ungültige Stimmzettel"],
        table["Gültige Stimmen*"],
        sum(elected.values()),
        parties,
    )
    communes: list[Commune] = []
    for ars in BB_COMMUNES:
        _, page = bb_page(f"{BB_BASE}/ergebnisse_gemeinde_{ars}.html")
        t = bb_table(page)
        heading = next(line for line in page if line.startswith("Wahl zur Verbandsgemeindeversammlung, "))
        name = heading.split(" - ", 1)[1]
        communes.append(
            Commune(
                ars[:5] + ars[9:],
                name,
                t["Wahlberechtigte"],
                t["Wählende"],
                t["Ungültige Stimmzettel"],
                t["Gültige Stimmen*"],
                [(label, votes) for label, votes in t.items() if label not in fixed],
            )
        )
    return result, communes


# Areas

DE = "01a13bb5-c550-7079-8650-a96e2b145352"
SCHEME = "de.verbandsgemeinde"
LANDS = {
    "rp": ("07", "01a13bb5-c563-741b-83f7-4b782d3a3364", "Rhineland-Palatinate", "Rheinland-Pfalz", "DE-RP"),
    "st": ("15", "01a13bb5-c563-741b-83f7-4b88a681db04", "Saxony-Anhalt", "Sachsen-Anhalt", "DE-ST"),
    "bb": ("12", "01a13bb5-c563-741b-83f7-4b829f1533bb", "Brandenburg", "Brandenburg", "DE-BB"),
}
RP_KREIS_CODES = {
    "Ahrweiler": "07131",
    "Altenkirchen (Westerwald)": "07132",
    "Bad Kreuznach": "07133",
    "Birkenfeld": "07134",
    "Cochem-Zell": "07135",
    "Mayen-Koblenz": "07137",
    "Neuwied": "07138",
    "Rhein-Hunsrück-Kreis": "07140",
    "Rhein-Lahn-Kreis": "07141",
    "Westerwaldkreis": "07143",
    "Bernkastel-Wittlich": "07231",
    "Eifelkreis Bitburg-Prüm": "07232",
    "Vulkaneifel": "07233",
    "Trier-Saarburg": "07235",
    "Alzey-Worms": "07331",
    "Bad Dürkheim": "07332",
    "Donnersbergkreis": "07333",
    "Germersheim": "07334",
    "Kaiserslautern": "07335",
    "Kusel": "07336",
    "Südliche Weinstraße": "07337",
    "Rhein-Pfalz-Kreis": "07338",
    "Mainz-Bingen": "07339",
    "Südwestpfalz": "07340",
}
RENAMED = {"Herdorf-Daaden": "Daaden-Herdorf", "Bitburg-Land": "Bitburger Land"}
KREISE = Path(__file__).with_name("gemeindeverband_kreise.tsv")
VG_FIELDS = ("OBJID", "ARS", "GEN", "BEZ", "GF")


class Vg(NamedTuple):
    """A municipal association, current or dissolved, that held a council election."""

    ident: str
    land: str
    kreis: str
    name: str
    ars: str | None


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def norm(text: str) -> str:
    return re.sub(r"[^a-zäöüß]", "", RENAMED.get(text, text).lower())


def kreise() -> dict[str, tuple[str, str]]:
    rows = (line.split("\t") for line in KREISE.read_text().splitlines()[1:])
    return {code: (area, name) for code, area, name in rows}


def outlines() -> dict[str, tuple[str, shapely.Geometry]]:
    """The 2025 outline and name of each Verbandsgemeinde by its regional key (ARS)."""
    with zipfile.ZipFile(raw.cached(SOURCE, VG250).path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{VWG}.shp")),
            shx=io.BytesIO(archive.read(f"{VWG}.shx")),
            dbf=io.BytesIO(archive.read(f"{VWG}.dbf")),
            encoding="utf-8",
        )
        found: dict[str, tuple[str, shapely.Geometry]] = {}
        for shape, record in zip(reader.shapes(), reader.records(), strict=True):
            if shape is None or record is None or record["GF"] != 4 or record["BEZ"] != "Verbandsgemeinde":
                continue
            found[str(record["ARS"])] = (str(record["GEN"]), shapely.geometry.shape(shape.__geo_interface__))
    return found


@functools.cache
def results() -> dict[tuple[str, int], list[Result]]:
    out: dict[tuple[str, int], list[Result]] = {}
    for year in ST_FILES:
        out["st", year] = st_results(year)
    for year in RP_FILES:
        out["rp", year] = rp_results(year)
    out["rp", 2004] = rp_results(2009, earlier=True)
    out["bb", 2024] = [bb_result()[0]]
    return out


@functools.cache
def vgs() -> dict[tuple[str, str, str], Vg]:
    """Every association in the results, by land, county code, and normalized name."""
    current = {ars: name for ars, (name, _) in outlines().items()}
    by_name = {(ars[:5], norm(name)): ars for ars, name in current.items()}
    out: dict[tuple[str, str, str], Vg] = {}
    for (land, _), pool in results().items():
        for result in pool:
            kreis = RP_KREIS_CODES[result.kreis] if land == "rp" else result.kreis
            ars = by_name.get((kreis, norm(result.name)))
            if ars is None and land == "st":
                raise ValueError(f"{result.name} has no outline")
            name = current[ars] if ars else result.name
            ident = ars or f"{kreis}:{norm(result.name)}"
            out[land, kreis, norm(result.name)] = Vg(ident, land, kreis, name, ars)
    return out


def vg_of(result: Result) -> Vg:
    kreis = RP_KREIS_CODES[result.kreis] if result.land == "rp" else result.kreis
    return vgs()[result.land, kreis, norm(result.name)]


def area_of(vg: Vg) -> str:
    return key("area", vg.ident)


# Packages

REGISTRY = f"{SOURCE}/registry"
GEO = f"{SOURCE}/geo"
SINCE = date(2025, 1, 1)
AGGREGATES = {"Wählergruppen", "Einzelbewerber", "Sonstige"}
GROUPS = {"WG LUN", "UWG", "LAW/Freie Wähler", "BVG", "WOS", "UVK", "WG Elbe-DEMOKRATEN"}
SOURCES = {
    "rp": (
        "Landeswahlleiter Rheinland-Pfalz, Statistisches Landesamt Rheinland-Pfalz",
        "Die Kommunalwahlen in Rheinland-Pfalz: Ergebnisse nach Verwaltungskreisen, Verbandsgemeinden und Gemeinden",
        "https://www.wahlen.rlp.de/kommunalwahlen/ergebnisse-1",
        "Official publication; reuse terms are not stated beyond the copyright notice of the Landeswahlleiter",
        "https://www.wahlen.rlp.de/",
        "© Landeswahlleiter Rheinland-Pfalz",
    ),
    "st": (
        "Statistisches Landesamt Sachsen-Anhalt",
        "Verbandsgemeinderatswahlen in Sachsen-Anhalt",
        "https://wahlergebnisse.sachsen-anhalt.de/wahlen/vmvm/",
        "Official publication; reuse terms are not stated",
        "https://wahlergebnisse.sachsen-anhalt.de/wahlen/and/impressum.html",
        "Statistisches Landesamt Sachsen-Anhalt",
    ),
    "bb": (
        "Landeswahlleiter des Landes Brandenburg",
        "Wahl zur Verbandsgemeindeversammlung Liebenwerda, 9. Juni 2024",
        f"{BB_BASE}/",
        "Reproduction and distribution, in part too, permitted with attribution of the source",
        "https://wahlergebnisse.brandenburg.de/impressum.html",
        "© Landeswahlleiter des Landes Brandenburg",
    ),
}
METHOD = (
    "Counted by the {body} and published for each Verbandsgemeinde. Registered voters are those entitled to vote, "
    "voters are those who took part, and valid ballots are the ballots (Stimmzettel) that were valid. Every voter has "
    "as many votes as the council has seats {extra}and may give them to individual candidates of any list, up to three "
    "to one candidate, so a list's votes are all the votes its candidates received and the votes of all lists add up to "
    "the valid votes, not to the valid ballots. Seats are those the lists won; {tail}"
)
BODIES = {
    "rp": "Landeswahlleiter of Rhineland-Palatinate (the Statistisches Landesamt, in its volumes on the municipal elections)",
    "st": "Statistisches Landesamt of Saxony-Anhalt, as the final result of each Verbandsgemeinderatswahl",
    "bb": "Landeswahlleiter of Brandenburg, as the official final result",
}
EXTRA = {
    "rp": "(which depends on the population) ",
    "st": "(which depends on the population) ",
    "bb": "(three) ",
}
TAILS = {
    "rp": (
        "the source prints each party's votes and seats, the votes of the local voters' groups (Wählergruppen) together "
        "as one total, and a residual line of other lists where it has one."
    ),
    "st": (
        "the source prints each party's votes and seats, and the voters' groups (Wählergruppen) and independent "
        "candidates (Einzelbewerber) each as one total."
    ),
    "bb": "the source prints each list and independent candidate separately.",
}
SYSTEMS = {
    ("rp", 2004): ({"formula": "hare"}, "the Hare-Niemeyer largest-remainder method"),
    ("rp", 2009): ({"formula": "hare"}, "the Hare-Niemeyer largest-remainder method"),
    ("rp", 2014): ({"formula": "sainte_lague_schepers"}, "the Sainte-Laguë/Schepers divisor method"),
    ("rp", 2019): ({"formula": "sainte_lague_schepers"}, "the Sainte-Laguë/Schepers divisor method"),
}


def slug(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})
    return re.sub(r"[^a-z0-9]+", "-", text.translate(table).lower()).strip("-")


def multipolygon(shape: shapely.Geometry) -> shapely.MultiPolygon:
    simple = shapely.simplify(shapely.make_valid(shape), 10)
    wgs = shapely.transform(simple, lambda c: numpy.column_stack(TRANSFORM.transform(c[:, 0], c[:, 1])))
    gridded = shapely.set_precision(shapely.make_valid(wgs), 1e-6)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    if not polygons:
        raise ValueError("an outline holds no polygon")
    return shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)


TRANSFORM = pyproj.Transformer.from_crs(25832, 4326, always_xy=True)


def source_id(land: str) -> str:
    return key("source", land)


def add_source(p: Package, land: str) -> None:
    publisher, title, url, license_, license_url, attribution = SOURCES[land]
    p.add(
        "source",
        id=source_id(land),
        kind="official_body",
        publisher=publisher,
        title=title,
        url=url,
        license=license_,
        license_url=license_url,
        attribution=attribution,
    )


def land_urls(land: str, year: int) -> list[str]:
    if land == "st":
        return [ST_FILES[year]] + ([ST_NAMED_2014] if year == 2014 else []) + ([ST_LAYOUT_2009] if year == 2009 else [])
    if land == "rp":
        return [RP_FILES[2009 if year == 2004 else year]]
    return [f"{BB_BASE}/ergebnisse.html"]


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Outlines of the Verbandsgemeinden of Rhineland-Palatinate, Saxony-Anhalt, and Brandenburg that held council "
        "elections, from the federal mapping agency's administrative areas as of January 1, 2025",
    )
    p.external |= {DE, *(area for area, _ in kreise().values())}
    source = key("source", "vg250")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Bundesamt für Kartographie und Geodäsie",
        title="Verwaltungsgebiete 1:250 000, Ebenen, Stand 01.01.2025 (VG250 01.01.)",
        url="https://gdz.bkg.bund.de/index.php/default/verwaltungsgebiete-1-250-000-ebenen-stand-01-01-vg250-ebenen-01-01.html",
        license="Datenlizenz Deutschland – Namensnennung – Version 2.0",
        license_url="https://www.govdata.de/dl-de/by-2-0",
        attribution="© GeoBasis-DE / BKG (2025)",
        notes=(
            "The outlines are those of January 1, 2025. An association that existed under the same name and key "
            "keeps it for earlier elections; associations that were merged or dissolved since have no outline here."
        ),
    )
    p.artifact(raw.cached(SOURCE, VG250), source)
    p.add(
        "scheme", code=SCHEME, polity_id=DE, kind="administrative", rank=3, noun="Verbandsgemeinde", code_system="ars"
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, "2025"),
        scheme_code=SCHEME,
        vintage="2025",
        valid_from=None,
        valid_to=None,
        as_of=SINCE,
    )
    shapes = outlines()
    area_of_kreis = kreise()
    for vg in sorted(vgs().values(), key=lambda item: item.ident):
        id = area_of(vg)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_parent", area_id=id, parent_id=area_of_kreis[vg.kreis][0], valid_from=None, valid_to=None)
        p.name(id, f"Verbandsgemeinde {vg.name}", "de", "official")
        if vg.ars:
            p.add("area_code", area_id=id, code=vg.ars, valid_from=None, valid_to=None)
            outline = multipolygon(shapes[vg.ars][1])
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=None,
                valid_to=None,
                geom=shapely.to_wkb(outline, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=source,
            )
    return p


def party_key(land: str, label: str) -> str:
    return key("party", land, label)


def system_key(land: str, year: int) -> str | None:
    if (land, year) not in SYSTEMS:
        return None
    return key("electoral_system", land, 2009 if year <= 2009 else 2014)


def labels(land: str) -> list[str]:
    seen: dict[str, None] = {}
    for (here, _), pool in results().items():
        if here == land:
            for result in pool:
                for label, _, _ in result.lists:
                    seen[label] = None
    return sorted(seen)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Verbandsgemeinden of Rhineland-Palatinate, Saxony-Anhalt, and Brandenburg as municipalities with their "
        "councils, the seat-allocation rules, and the parties and voters' groups of their council elections",
        depends=[GEO],
    )
    p.external |= {DE, *(polity for _, polity, *_ in LANDS.values())}
    for land in LANDS:
        add_source(p, land)
        urls = {url for year in sorted(y for (here, y) in results() if here == land) for url in land_urls(land, year)}
        for url in sorted(urls):
            p.artifact(raw.cached(SOURCE, url), source_id(land))
    for (land, year), (rules, text) in SYSTEMS.items():
        if year in (2004, 2019):
            continue
        next_ = date(2014, 1, 1) if year == 2009 else None
        id = system_key(land, year)
        assert id is not None
        p.add(
            "electoral_system",
            id=id,
            polity_id=LANDS[land][1],
            valid_from=date(2004, 1, 1) if year == 2009 else date(2014, 1, 1),
            valid_to=next_,
            rules=rules,
            rules_version=1,
        )
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="methodology",
            body_md=(
                f"The seats of a Verbandsgemeinde council go to the lists by {text}, in proportion to the votes of "
                "each list; the votes a candidate receives count for the list."
            ),
            lang="en",
        )
    for land in LANDS:
        for label in labels(land):
            if label in AGGREGATES or label.startswith("EB "):
                continue
            id = party_key(land, label)
            p.add("party", id=id, kind="independent_group" if label in GROUPS else "party", polity_id=LANDS[land][1])
            p.add("external_id", entity_id=id, scheme="de.gemeindeverband.party", value=f"{land}:{label}")
            p.name(id, label, "de", "official")
    for vg in sorted(vgs().values(), key=lambda item: item.ident):
        polity = key("polity", vg.ident)
        p.add("polity", id=polity, kind="municipality", iso_code=None)
        p.add("polity_parent", polity_id=polity, parent_id=LANDS[vg.land][1], valid_from=None, valid_to=None)
        p.add("polity_territory", polity_id=polity, area_id=area_of(vg), valid_from=None, valid_to=None)
        p.name(polity, f"Verbandsgemeinde {vg.name}", "de", "official")
        if vg.ars:
            p.add("external_id", entity_id=polity, scheme="de.ars", value=vg.ars)
        body = key("body", vg.ident)
        p.add("body", id=body, polity_id=polity, kind="council")
        p.name(body, f"Verbandsgemeinderat {vg.name}", "de", "official")
        p.name(body, f"Council of the Verbandsgemeinde {vg.name}", "en", "english", False)
    return p


def next_day(vg: Vg, day: date) -> date | None:
    later = sorted(
        result.day
        for (here, _), pool in results().items()
        for result in pool
        if here == vg.land and vg_of(result) == vg and result.day > day
    )
    return later[0] if later else None


def election(land: str, year: int) -> Package:
    _, polity, english, native, _ = LANDS[land]
    pool = results()[land, year]
    p = Package(
        f"{SOURCE}/{land}-{year}",
        SOURCE,
        f"The {year} council elections of the Verbandsgemeinden of {english}, by Verbandsgemeinde",
        depends=[REGISTRY, GEO],
    )
    p.external.add(polity)
    p.accept(
        "accounting.votes_valid",
        "Every voter has as many votes as the council has seats, so the votes for the lists add up to the valid "
        "votes the source prints, not to the valid ballots.",
    )
    for url in land_urls(land, year):
        p.uses(raw.cached(SOURCE, url))
    elections: dict[date, str] = {}
    taken: set[tuple[str, str]] = set()
    rules = system_key(land, year)
    for result in sorted(pool, key=lambda item: (item.day, item.name)):
        vg = vg_of(result)
        day = result.day
        undated = (year, result.name) in UNDATED
        path = f"de/{year}-{land}-verbandsgemeinderat-postponed" if undated else f"de/{day}-{land}-verbandsgemeinderat"
        if day not in elections:
            id = elections[day] = key("election", land, "undated" if undated else day)
            p.add(
                "election",
                id=id,
                polity_id=polity,
                kind="local",
                date_from=day,
                date_to=date(year, 12, 31) if undated else day,
                date_precision="year" if undated else "day",
                original_text="Wahl verschoben" if undated else None,
                status="held",
            )
            p.add("slug", path=path, entity_id=id, is_current=True)
            if undated:
                p.name(
                    id,
                    f"{year} {english} Verbandsgemeinde council election held after the regular day",
                    "en",
                    "official",
                )
                p.name(id, f"Verbandsgemeinderatswahl in {native} {year}, verschoben", "de", "native", False)
            else:
                p.name(
                    id, f"{day:%B} {day.day}, {day.year} {english} Verbandsgemeinde council elections", "en", "official"
                )
                p.name(id, f"Verbandsgemeinderatswahlen in {native} am {day:%d.%m.%Y}", "de", "native", False)
        election_id = elections[day]
        area = area_of(vg)
        contest = key("contest", land, day, vg.ident)
        unit = key("unit", land, day, vg.ident)
        result_set = key("result_set", land, day, vg.ident)
        seats = result.seats
        if seats is None:
            raise ValueError(f"{vg.name}: no seat total")
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=key("body", vg.ident),
            district_area_id=area,
            magnitude=seats,
            round_number=1,
            electoral_system_id=rules,
            default_result_kind="counted",
        )
        p.name(contest, f"{day.year} Verbandsgemeinde {vg.name} council election", "en", "official")
        p.name(contest, f"Verbandsgemeinderatswahl {vg.name} {day.year}", "de", "native", False)
        tail = slug(vg.name)
        if (path, tail) in taken:
            tail = f"{tail}-{vg.kreis}"
        taken.add((path, tail))
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        p.add("body_size", body_id=key("body", vg.ident), seats=seats, valid_from=day, valid_to=next_day(vg, day))
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=polity,
            scheme_code=SCHEME,
            code=f"{year if undated else day}:{vg.ident}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id(land),
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = METHOD.format(body=BODIES[land], extra=EXTRA[land], tail=TAILS[land])
        if land == "bb":
            method += (
                " The valid ballots are the voters less the invalid ballots, which the page does not print, and are "
                "marked as derived."
            )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        for measure, value, flags in (
            (2, result.registered, 0),
            (3, result.voters, 0),
            (5, result.invalid, 0),
            (4, result.valid, 1 if land == "bb" else 0),
        ):
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=flags,
                    value=value,
                )
        if result.voters != (result.valid or 0) + (result.invalid or 0):
            raise ValueError(f"{vg.name} {year}: valid and invalid ballots differ from the voters")
        if sum(v for _, v, _ in result.lists) != result.votes or sum(s for *_, s in result.lists) != seats:
            raise ValueError(f"{vg.name} {year}: the lists differ from the printed votes or seats")
        for position, (label, votes, won) in enumerate(result.lists, start=1):
            option = key("option", land, day, vg.ident, label)
            aggregate = label in AGGREGATES
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if aggregate else "list",
                party_id=None if aggregate or label.startswith("EB ") else party_key(land, label),
                ballot_position=position,
                ballot_label=label,
            )
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
    return p


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/{land}-{year}" for land, year in sorted(results()))]


def fetch() -> None:
    for url in ST_FILES.values():
        raw.fetch(SOURCE, url, suffix=".csv")
    raw.fetch(SOURCE, ST_NAMED_2014, suffix=".csv")
    raw.fetch(SOURCE, ST_LAYOUT_2009, suffix=".pdf")
    for url in RP_FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, f"{BB_BASE}/ergebnisse.html", suffix=".html")
    for ars in BB_COMMUNES:
        raw.fetch(SOURCE, f"{BB_BASE}/ergebnisse_gemeinde_{ars}.html", suffix=".html")
    raw.fetch(SOURCE, VG250, suffix=".zip")


def parse() -> None:
    geo().write()
    registry().write()
    for land, year in sorted(results()):
        election(land, year).write()


def emit() -> None:
    package.run("load", packages())
