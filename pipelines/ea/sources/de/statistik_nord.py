import io
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl
import xlrd

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "de.statistik_nord"
SITE = "https://www.statistik-nord.de"
BV = f"{SITE}/fileadmin/Dokumente/Wahlen/Hamburg/Bezirksversammlungswahlen"
LIVE = "https://www.wahlen-hamburg.de/bezirksversammlungswahlen_2024"
HAMBURG = "01a13bb5-c563-741b-83f7-4b6ecbb09948"
BEZIRKE = {
    "1": ("02001", "Hamburg-Mitte", "01a13bb5-c57a-72d2-9042-3be4099d1f9e"),
    "2": ("02002", "Altona", "01a13bb5-c57a-72d2-9042-3be579a55f9f"),
    "3": ("02003", "Eimsbüttel", "01a13bb5-c57a-72d2-9042-3be616835bba"),
    "4": ("02004", "Hamburg-Nord", "01a13bb5-c57a-72d2-9042-3be7ed04e38e"),
    "5": ("02005", "Wandsbek", "01a13bb5-c57a-72d2-9042-3be87fc671b1"),
    "6": ("02006", "Bergedorf", "01a13bb5-c57a-72d2-9042-3be99c649cdd"),
    "7": ("02007", "Harburg", "01a13bb5-c57a-72d2-9042-3bea358e3890"),
}
DAYS = {
    2001: date(2001, 9, 23),
    2004: date(2004, 9, 12),
    2011: date(2011, 2, 20),
    2014: date(2014, 5, 25),
    2019: date(2019, 5, 26),
    2024: date(2024, 6, 9),
}
SCHEME = "de.hh_wahlbezirk"
LICENSE = "Reproduction and distribution permitted with the source acknowledged; the content may not be altered"
SHEETS_2019 = ["Mitte", "Altona", "Eimsb%C3%BCttel", "Nord", "Wandsbek", "Bergedorf", "Harburg"]


def _files() -> dict[int, dict[str, list[str]]]:
    d14 = f"{BV}/2014/Wahlergebnisse_zum_Download_%28endg%C3%BCltig%29"
    pk = f"{BV}/2011/vorl%C3%A4ufig/Sitzverteilungen_und_gew%C3%A4hlte_Kandidaten"
    return {
        2024: {
            "units": [f"{LIVE}/ergebnis-download-bezirk.csv"],
            "fields": [f"{LIVE}/Feldbezeichner_Download_2024_Bezirksstimmen.xlsx"],
            "summary": [
                f"{BV}/2024/Endg%C3%BCltige_Ergebnisse/Ergebnisse_nach_Bezirken_und_Wahlkreisen/BVW_e_00{n}.xlsx"
                for n in range(1, 8)
            ],
        },
        2019: {
            "units": [f"{BV}/2019/end-down/Bez_e_INT-080-2019-Bezirksstimmen-in-allen-Wahlbezirken1.csv"],
            "summary": [
                f"{SITE}/fileadmin/Dokumente/Bez_e_001-00{n}-2019-Bezirksstimmen-Mandate-{name}.xlsx"
                for n, name in enumerate(SHEETS_2019, 1)
            ],
        },
        2014: {
            "units": [f"{d14}/INTBEZ_07_BZ_2014_Download_Bezirkslistenstimmen_endgueltig.csv"],
            "summary": [
                f"{BV}/2014/Sitzverteilungen_und_gew%C3%A4hlte_Kandidaten_%28endg%C3%BCltig%29/"
                f"INTBEZ_05_BZ_0{n}_BLZ_WKL_Sitze_2014_endgueltig.xlsx"
                for n in range(1, 8)
            ],
        },
        2011: {
            "units": [f"{BV}/2011/vorl%C3%A4ufig/Wahlergebnisse_zum_Download/DINTBEZ28.csv"],
            "summary": [
                f"{pk}/BEZWA_PK01_01.pdf",
                f"{pk}/BEZWA_PK02_01.pdf",
                f"{pk}/BEZWA_PK03_01.pdf",
                f"{pk}/BEZWA_PK04_01.pdf",
                f"{pk}/BEZWA_PK05_01.pdf",
                f"{pk}/BEZWA_PK06_kor_01.pdf",
                f"{pk}/BEZWA_PK07_neu.pdf",
            ],
        },
        2004: {"units": [f"{BV}/2004/4.3.4_bezwa_wahlbez_absolute_zahlen_korr.xls"]},
        2001: {
            "units": [f"{BV}/2001/Bez2001-Wahlbezirke.csv"],
            "seats": [f"{BV}/2001/bezirke_bez01_sitze.pdf"],
        },
    }


FILES = _files()
PAGES = {
    2024: f"{SITE}/wahlen/wahlen-in-hamburg/bezirksversammlungswahlen/2024",
    2019: f"{SITE}/wahlen/wahlen-in-hamburg/bezirksversammlungswahlen/bezirksversammlungswahlen-2019-in-hamburg",
    2014: f"{SITE}/wahlen/wahlen-in-hamburg/bezirksversammlungswahlen/bezirksversammlungswahlen-in-hamburg",
    2011: f"{SITE}/wahlen/wahlen-in-hamburg/bezirksversammlungswahlen/bezirksversammlungswahlen-2011-in-hamburg",
    2004: f"{SITE}/wahlen/wahlen-in-hamburg/bezirksversammlungswahlen/bezirksversammlungswahlen-2004-in-hamburg",
    2001: f"{SITE}/wahlen/wahlen-in-hamburg/bezirksversammlungswahlen/bezirksversammlungswahlen-2001-in-hamburg",
}
# Printed list names that name one party across years.
CANON = {
    "GRÜNE/GAL": "GRÜNE",
    "GRÜNE/ GAL": "GRÜNE",
    "F.D.P.": "FDP",
    "PDS HAMBURG": "PDS",
    "PDS": "PDS",
    "ST. PAULI": "ST.PAULI",
    "DIE KONSER-VATIVEN": "DIE KONSERVATIVEN",
    "REGEN-BOGEN": "REGENBOGEN",
    "FAMILIEN- POWER": "FAMILIEN-POWER",
    "STATT PARTEI": "STATT Partei",
    "HAMBURG OFFENSIV": "Hamburg Offensiv",
    "FPD": "FDP",
    "ÜBRIGE": "Übrige",
    "ANDERE": "Übrige",
}
FIELDS = {
    "registered": 2,
    "voters": 14,
    "ballots": 3,
    "invalid": 5,
    "valid": 4,
}
NOTE_SYSTEM = (
    "Each voter has several votes for the Bezirksversammlung of the district (since 2008 five votes for the district "
    "list, in 2001 and 2004 one). The figures are the votes for each district list (Bezirksliste) in every polling "
    "district, including the polling districts that count postal votes, as the Statistikamt Nord publishes them. "
    "From 2011 on a list's votes are its Gesamtstimmen, which add the votes marked for the list itself, the votes "
    "marked for its candidates, and since 2019 the votes the authority assigns by its healing rule (Heilungsregel) "
    "to ballots that marked more than the five votes. The seats of the assembly are shared among the lists by the "
    "district-list votes; each list's seats are the total of its district-list and constituency (Wahlkreis) seats. "
    "The votes for the constituency lists (Wahlkreisstimmen) and the votes for single candidates are not stored here "
    "yet. Registered voters (Wahlberechtigte) of a postal polling district are zero because voters holding a voting "
    "certificate are counted in the roll of the polling district they live in, so a postal district reports more "
    "voters than registered voters."
)


class Unit(NamedTuple):
    code: str
    bezirk: str
    figures: dict[str, int | None]
    votes: dict[str, int]
    spoiled: bool = False


class Total(NamedTuple):
    figures: dict[str, int | None]
    votes: dict[str, int]


def canon(label: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[¹²³\xad]", "", label)).strip()
    text = re.sub(r"(?<=[A-ZÄÖÜ])[1-3]$", "", text)
    return CANON.get(text.upper(), text)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def party(label: str) -> str:
    return key("party", canon(label))


def fetch() -> None:
    for files in FILES.values():
        for urls in files.values():
            for url in urls:
                raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def path(url: str) -> Path:
    return raw.cached(SOURCE, url).path


def whole(cell: object) -> int | None:
    if cell is None:
        return None
    if isinstance(cell, int | float):
        return int(cell)
    text = str(cell).replace("\xa0", " ").replace(" ", "").strip()
    if text in ("", "-", "–", "x", "·"):
        return None
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    return int(float(text.replace(",", ".")))


def csv(url: str, header: str) -> pl.DataFrame:
    text = path(url).read_bytes().decode("cp1252", errors="replace")
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header))
    frame = pl.read_csv(
        io.StringIO("\n".join(lines[start:])), separator=";", infer_schema_length=0, truncate_ragged_lines=True
    )
    return frame.select([c for c in frame.columns if c and not c.startswith("_duplicated")])


def sheet(url: str, name: int = 0) -> list[list[object]]:
    frame = pl.read_excel(path(url), sheet_id=name + 1, has_header=False, infer_schema_length=0)
    return [list(row) for row in frame.iter_rows()]


def code_of(cell: object) -> str:
    return str(cell).strip().split()[0]


def kind_of(code: str) -> str:
    if len(code) == 7:
        return "postal_district"
    return "aggregate" if code[1:] == "8888" else "standard"


def _figures(row: dict[str, str | None], names: dict[str, str]) -> dict[str, int | None]:
    return {field: whole(row.get(column)) for field, column in names.items()}


def units_2024() -> list[Unit]:
    frame = csv(FILES[2024]["units"][0], "Erfassungsgebietsnummer")
    labels: dict[str, dict[str, str]] = {}
    fields = sheet(FILES[2024]["fields"][0])
    header = fields[3]
    names = [str(row[0]) for row in fields if row[0] and str(row[0]).startswith("Wahlbezirke im Bezirk")]
    order = [n.removeprefix("Wahlbezirke im Bezirk ").strip() for n in names]
    mapping = {name: str(number) for number, (_, name, _) in BEZIRKE.items() for name in [name]}
    rows = [row for row in fields if row[0] == "…"]
    for name, row in zip(order, rows, strict=True):
        labels[mapping[name]] = {
            str(header[i]): str(cell).removeprefix("GESAMTSTIMMEN (").removesuffix(")")
            for i, cell in enumerate(row)
            if cell and str(cell).startswith("GESAMTSTIMMEN (") and header[i]
        }
    columns = {
        "registered": "Wahlberechtigte gesamt (A)",
        "voters": "Waehler gesamt (B)",
        "ballots": "Stimmzettel gesamt (B4)",
        "invalid": "Stimmzettel ungueltig (E1)",
        "valid": "Stimmzettel gueltig (E2)",
        "valid_votes": "Stimmen gueltige (F)",
    }
    out = []
    for row in frame.iter_rows(named=True):
        code = row["Erfassungsgebietsnummer"]
        bezirk = code[0]
        votes = {canon(label): whole(row[column]) or 0 for column, label in labels[bezirk].items()}
        out.append(Unit(code, bezirk, _figures(row, columns), votes))
    return out


def units_wide(year: int, header: str, columns: dict[str, str], bezirk_column: str | None) -> list[Unit]:
    frame = csv(FILES[year]["units"][0], header)
    parties = [c.removesuffix(" - Gesamtstimmen") for c in frame.columns if c.endswith(" - Gesamtstimmen")]
    out = []
    for row in frame.iter_rows(named=True):
        code = row["Wahlbezirk"]
        if code is None or not code.strip():
            continue
        code = code.strip()
        bezirk = code[0]
        votes = {canon(label): whole(row[f"{label} - Gesamtstimmen"]) or 0 for label in parties}
        out.append(Unit(code, bezirk, _figures(row, columns), votes))
    return out


def units_2019() -> list[Unit]:
    columns = {
        "registered": "Wahlberechtigte",
        "voters": "Wählende",
        "ballots": "Abgegebene Stimmzettel",
        "invalid": "Ungültige Stimmzettel",
        "valid": "Gültige Stimmzettel",
        "valid_votes": "Gültige Stimmen (Gesamt)",
    }
    return units_wide(2019, "Bezirk;Wahlkreis", columns, "Bezirk")


def units_2014() -> list[Unit]:
    columns = {
        "registered": "Wahlberechtigte",
        "voters": "Wähler insgesamt",
        "ballots": "Abgegebene Stimmzettel",
        "invalid": "Ungültige Stimmzettel",
        "valid": "Gültige Stimmzettel",
        "valid_votes": "Gültige Stimmen",
    }
    return units_wide(2014, "Bezirk;Wahlkreis", columns, "Bezirk")


def units_2011() -> list[Unit]:
    columns = {
        "registered": "Wahlberechtigte",
        "voters": "Wähler insgesamt",
        "ballots": "Abgegebene Stimmzettel",
        "invalid": "Ungültige Stimmzettel",
        "valid": "Gültige Stimmzettel",
        "valid_votes": "Gültige Stimmen",
    }
    return units_wide(2011, "Wahlkreis;Wahlbezirk", columns, None)


def units_2004() -> list[Unit]:
    book = xlrd.open_workbook(path(FILES[2004]["units"][0]))
    page = book.sheet_by_index(0)
    header = [str(c).replace("\n", " ").strip() for c in page.row_values(3)]
    out = []
    for r in range(4, page.nrows):
        row = page.row_values(r)
        if not str(row[0]).strip():
            continue
        code = code_of(row[0])
        votes = {
            canon(header[i]): int(row[i] or 0) for i in range(9, len(header)) if header[i] and header[i] != "Übrige"
        }
        figures = {
            "registered": whole(row[4]),
            "voters": whole(row[5]),
            "ballots": None,
            "invalid": whole(row[7]),
            "valid": whole(row[8]),
            "valid_votes": whole(row[8]),
        }
        out.append(Unit(code, code[0], figures, votes))
    return out


def units_2001() -> list[Unit]:
    frame = csv(FILES[2001]["units"][0], "Bezirk;Stadtteil")
    parties = frame.columns[10:]
    out = []
    for row in frame.iter_rows(named=True):
        code = row["Wahlbezirk"]
        if code is None or not code.strip():
            continue
        figures = {
            "registered": whole(row["Wahlberechtigte   insgesamt"]),
            "voters": whole(row["Waehler insgesamt"]),
            "ballots": None,
            "invalid": whole(row["ungueltige Stimmen"]),
            "valid": whole(row["gueltige Stimmen"]),
            "valid_votes": whole(row["gueltige Stimmen"]),
        }
        votes = {canon(label): whole(row[label]) or 0 for label in parties}
        out.append(Unit(code.strip(), code.strip()[0], figures, votes))
    return out


def units(year: int) -> list[Unit]:
    return {
        2024: units_2024,
        2019: units_2019,
        2014: units_2014,
        2011: units_2011,
        2004: units_2004,
        2001: units_2001,
    }[year]()


LABELS = (
    ("wahlberechtigte", "registered"),
    ("wähl", "voters"),
    ("abgegebene stimmzettel", "ballots"),
    ("ungültige stimmzettel", "invalid"),
    ("gültige stimmzettel", "valid"),
    ("gültige stimmen", "valid_votes"),
)


def _blank() -> dict[str, int | None]:
    return dict.fromkeys(("registered", "voters", "ballots", "invalid", "valid", "valid_votes"))


def _figure(label: str) -> str | None:
    text = re.sub(r"\s+", " ", label).strip().lower()
    if text.startswith(("darunter", "davon")):
        return None
    return next((field for start, field in LABELS if text.startswith(start)), None)


def summary_xlsx(url: str, seat_column: int | None) -> tuple[Total, dict[str, int]]:
    """Read a district's table: the votes in the first number column, and the seats either in a second table
    (`seat_column` None) or in `seat_column` of the same rows."""
    figures, votes, seats = _blank(), {}, {}
    stage = ""
    for row in sheet(url):
        label = "" if row[0] is None else re.sub(r"\s+", " ", str(row[0])).strip()
        low = label.lower()
        if low in ("gültige stimmen für", "davon für"):
            stage = "parties"
            continue
        if low == "partei":
            stage = "seats"
            continue
        if low.startswith(("mandatsverteilung", "endgültig", "©")) and stage == "parties":
            stage = ""
        if not label:
            continue
        if stage == "parties":
            votes[canon(label)] = whole(row[1]) or 0
            if seat_column is not None and whole(row[seat_column]) is not None:
                seats[canon(label)] = whole(row[seat_column]) or 0
        elif stage == "seats":
            if low == "insgesamt":
                stage = ""
            else:
                seats[canon(label)] = whole(row[1]) or 0
        else:
            field = _figure(label)
            if field is not None and figures[field] is None:
                figures[field] = whole(row[1])
                if field == "valid_votes" and seat_column is not None:
                    seats["total"] = whole(row[seat_column]) or 0
    return Total(figures, votes), seats


def pdf_lines(url: str) -> list[list[str]]:
    import pymupdf

    out: list[list[str]] = []
    for page in pymupdf.open(path(url)):
        rows: dict[int, list[tuple[float, str]]] = {}
        for x0, y0, _, _, word, *_ in page.get_text("words"):
            rows.setdefault(round(y0 / 4), []).append((x0, word))
        for k in sorted(rows):
            line = [word for _, word in sorted(rows[k])]
            if out and all(re.fullmatch(r"[\d,]+|x|\u2013", t) for t in line) and re.fullmatch(r"\d+,\d|x", line[0]):
                out[-1] += line
            else:
                out.append(line)
    return out


def numbers(tokens: list[str]) -> tuple[str, list[int | None]]:
    """Split a printed table row into its label and its numbers, joining thousands groups such as 189 442."""
    at = next((i for i, t in enumerate(tokens) if re.fullmatch(r"[\d,]+|x|\u2013", t) and i > 0), len(tokens))
    label = " ".join(tokens[:at])
    out: list[int | None] = []
    rest = tokens[at:]
    if any(not re.fullmatch(r"[\d,]+|x|\u2013", t) for t in rest):
        return " ".join(tokens), []
    i = 0
    while i < len(rest):
        token = rest[i]
        if token in ("x", "\u2013"):
            out.append(None)
        elif "," in token:
            out.append(-1)
        else:
            while i + 1 < len(rest) and re.fullmatch(r"\d{3}", rest[i + 1]) and len(token) <= 3:
                token += rest[i + 1]
                i += 1
            out.append(int(token))
        i += 1
    return label, out


def summary_2011(url: str) -> tuple[Total, dict[str, int]]:
    figures, votes, seats = _blank(), {}, {}
    stage = ""
    for tokens in pdf_lines(url):
        label, nums = numbers(tokens)
        low = label.lower()
        if low == "davon für":
            stage = "parties"
            continue
        if stage == "parties":
            if not nums or label[:1].isdigit():
                stage = ""
                continue
            if "\u00b2" in label:
                continue
            if nums[0] is not None:
                votes[canon(label)] = nums[0]
            if len(nums) >= 5 and nums[4] is not None and nums[4] != -1:
                seats[canon(label)] = nums[4]
            continue
        field = _figure(label)
        if field is not None and nums and figures[field] is None and nums[0] is not None:
            figures[field] = nums[0]
    return Total(figures, votes), seats


def totals(year: int) -> dict[str, tuple[Total, dict[str, int]]]:
    out: dict[str, tuple[Total, dict[str, int]]] = {}
    match year:
        case 2024 | 2019:
            for number, url in enumerate(FILES[year]["summary"], 1):
                out[str(number)] = summary_xlsx(url, None)
        case 2014:
            for number, url in enumerate(FILES[year]["summary"], 1):
                out[str(number)] = summary_xlsx(url, 5)
        case 2011:
            for number, url in enumerate(FILES[year]["summary"], 1):
                out[str(number)] = summary_2011(url)
        case 2001:
            out = totals_2001()
    return out


def totals_2001() -> dict[str, tuple[Total, dict[str, int]]]:
    frame = csv(FILES[2001]["units"][0], "Bezirk;Stadtteil")
    names = {name: number for number, (_, name, _) in BEZIRKE.items()}
    seats: dict[str, dict[str, int]] = {}
    labels: list[str] = []
    for tokens in pdf_lines(FILES[2001]["seats"][0]):
        joined = " ".join(tokens)
        if joined.startswith("Bezirk SPD"):
            labels = ["SPD", "CDU", "GRÜNE", "FDP", "Schill", "Übrige"]
        if labels and tokens[0] in names and len(tokens) == 7 and all(t.isdigit() for t in tokens[1:]):
            seats[names[tokens[0]]] = dict(zip(labels, map(int, tokens[1:]), strict=True))
            labels = labels if len(seats) < 7 else []
    out: dict[str, tuple[Total, dict[str, int]]] = {}
    parties = frame.columns[10:]
    for row in frame.iter_rows(named=True):
        if row["Wahlbezirk"] is not None or row["Stadtteil"] not in names or row["Stadtteil"] in out:
            continue
        number = names[row["Stadtteil"]]
        if row["Bezirk"] != number:
            continue
        figures = {
            "registered": whole(row["Wahlberechtigte   insgesamt"]),
            "voters": whole(row["Waehler insgesamt"]),
            "ballots": None,
            "invalid": whole(row["ungueltige Stimmen"]),
            "valid": whole(row["gueltige Stimmen"]),
            "valid_votes": whole(row["gueltige Stimmen"]),
        }
        votes = {canon(label): whole(row[label]) or 0 for label in parties}
        out[number] = (Total(figures, votes), seats[number])
    return out


COUNTRY = "01a13bb5-c550-7079-8650-a96e2b145352"
SRC = key("source", "statistik-nord")


def body(number: str) -> str:
    return key("body", number)


def seats_of(year: int) -> dict[str, dict[str, int]]:
    if year == 2004:
        return {}
    return {n: {k: v for k, v in seats.items() if k != "total"} for n, (_, seats) in totals(year).items()}


def check() -> None:
    """Fail unless every district's polling-district rows add up to the authority's district totals."""
    for year in DAYS:
        official = totals(year)
        rows = units(year)
        grand: Counter[str] = Counter()
        for number, (total, _) in sorted(official.items()):
            mine = [u for u in rows if u.bezirk == number]
            figures: Counter[str] = Counter()
            votes: Counter[str] = Counter()
            for u in mine:
                votes.update(u.votes)
                for field, value in u.figures.items():
                    figures[field] += value or 0
            for field, value in total.figures.items():
                if value is not None and figures[field] != value:
                    raise ValueError(f"{year} district {number} {field}: units {figures[field]}, published {value}")
            listed = {k: v for k, v in total.votes.items() if k in votes}
            for label, value in listed.items():
                if votes[label] != value:
                    raise ValueError(f"{year} district {number} {label}: units {votes[label]}, published {value}")
            grand.update(votes)
        print(f"{year}: {len(rows)} polling districts; district totals agree; votes {sum(grand.values())}", flush=True)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/bv-{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The polling-district scheme of the Hamburg district assembly elections",
    )
    p.external.add(HAMBURG)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=HAMBURG,
        kind="precinct",
        rank=6,
        noun="polling district",
        code_system=None,
    )
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The source, the lists, and the seven district assemblies of Hamburg's district assembly elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {HAMBURG, COUNTRY}
    p.add(
        "source",
        id=SRC,
        kind="official_body",
        publisher="Statistisches Amt für Hamburg und Schleswig-Holstein",
        title="Bezirksversammlungswahlen in Hamburg",
        url=f"{SITE}/wahlen/wahlen-in-hamburg/bezirksversammlungswahlen",
        license=LICENSE,
        license_url=f"{SITE}/impressum",
        attribution="© Statistisches Amt für Hamburg und Schleswig-Holstein",
    )
    for year, groups in FILES.items():
        for urls in groups.values():
            for url in urls:
                p.artifact(raw.cached(SOURCE, url), SRC)
    for number, (_, name, _) in BEZIRKE.items():
        id = body(number)
        p.add("body", id=id, polity_id=HAMBURG, kind="council")
        p.name(id, f"{name} District Assembly", "en", "official")
        p.name(id, f"Bezirksversammlung {name}", "de", "native", False)
    labels: set[str] = set()
    for year in DAYS:
        for u in units(year):
            labels |= set(u.votes)
        for _, seats in totals(year).values():
            labels |= {k for k in seats if k not in ("total", "Übrige")}
    for label in sorted(labels):
        id = party(label)
        p.add("party", id=id, kind="party", polity_id=COUNTRY)
        p.name(id, label, "de", "ballot")
    return p


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/bv-{year}",
        SOURCE,
        f"The {year} Hamburg district assembly elections by polling district",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {HAMBURG, *(area for _, _, area in BEZIRKE.values())}
    for urls in FILES[year].values():
        for url in urls:
            p.uses(raw.cached(SOURCE, url))
    official = totals(year)
    rows = units(year)
    seats = seats_of(year)
    id = key("election", year)
    p.add("election", id=id, polity_id=HAMBURG, kind="local", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Hamburg district assembly elections", "en", "official")
    p.name(id, f"Bezirksversammlungswahlen {year} in Hamburg", "de", "native", False)
    path = f"de/{day}-hamburg-district-assemblies"
    p.add("slug", path=path, entity_id=id, is_current=True)
    notes = [NOTE_SYSTEM]
    if year == 2004:
        notes.append(
            "The seats of the 2004 assemblies are not stored: the authority's seat table is a PDF whose font "
            "has no text layer."
        )
    if year in (2001, 2004):
        notes.append("In 2001 and 2004 each voter had one vote, so a list's votes are the ballots marked for it.")
    for number, (_, name, area) in BEZIRKE.items():
        mine = [u for u in rows if u.bezirk == number]
        total, _ = official.get(number, (None, {}))
        won = seats.get(number, {})
        running = sorted(
            {label for u in mine for label, v in u.votes.items() if v}
            | {label for label, n in won.items() if n and label != "Übrige"}
        )
        size = sum(won.values()) or None
        contest = key("contest", year, number)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="body_seats",
            body_id=body(number),
            district_area_id=area,
            magnitude=size,
            round_number=1,
            default_result_kind="counted",
        )
        title = f"{name} District Assembly election"
        p.name(contest, title, "en", "official")
        p.name(contest, f"Wahl zur Bezirksversammlung {name}", "de", "native", False)
        p.add("slug", path=f"{path}/{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SRC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=" ".join(notes),
            lang="en",
        )
        options = {}
        for label in running:
            option = options[label] = key("option", year, number, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(label),
                ballot_label=label,
            )
        for label, n in won.items():
            if n and label in options:
                p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=n)
        if total is not None:
            for field, measure in FIELDS.items():
                if total.figures[field] is not None:
                    p.add(
                        "declared",
                        result_set_id=result_set,
                        option_id=None,
                        measure=measure,
                        value=total.figures[field],
                    )
            for label, value in total.votes.items():
                if label in options:
                    p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=value)
        for u in mine:
            unit = key("unit", year, u.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=HAMBURG,
                scheme_code=SCHEME,
                code=f"{day}:{u.code}",
                area_relation="within",
                area_id=area,
                kind=kind_of(u.code),
            )
            for field, measure in FIELDS.items():
                if u.figures.get(field) is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=u.figures[field],
                    )
            for label, option in options.items():
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=u.votes.get(label, 0),
                )
    p.accept(
        "accounting.turnout",
        "Voters holding a voting certificate are counted on the roll of the polling district they live in but vote "
        "in a postal district or another district, and a postal district has no roll of its own, so a district can "
        "report more voters than registered voters.",
    )
    return p
