import csv
import io
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl
import pymupdf
from psycopg import Connection

from ea import package, raw
from ea.changeset import slug
from ea.package import Draft
from ea.store import DATA, connect

SOURCE = "es.euskadi"
KEY = f"{SOURCE}/all"
VERSION = "1"
PUBLISHER = "Gobierno Vasco"
TITLE = "Resultados de las elecciones al Parlamento Vasco (euskadi.eus)"
PORTAL = "https://www.euskadi.eus/informacion/descargas-de-ficheros-de-resultados-de-elecciones/web01-a2haukon/es/"
BASE = "https://www.euskadi.eus/contenidos/informacion/w_em_descargas/es_def/adjuntos/csv/"
STATUTE = "Ley Orgánica 3/1979, de 18 de diciembre, de Estatuto de Autonomía para el País Vasco"
LAW = "Ley 5/1990, de 15 de junio, de Elecciones al Parlamento Vasco"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
TERRITORIES = {"01": "Álava", "20": "Gipuzkoa", "48": "Bizkaia"}
NATIVE = {"01": "Araba/Álava", "20": "Gipuzkoa", "48": "Bizkaia"}
ELECTIONS = {
    1980: ("P80_c.zip", date(1980, 3, 9)),
    1984: ("P84_c.zip", date(1984, 2, 26)),
    1986: ("P86_c.zip", date(1986, 11, 30)),
    1990: ("P90_c.zip", date(1990, 10, 28)),
    1994: ("P94_c.zip", date(1994, 10, 23)),
    1998: ("P98_c.zip", date(1998, 10, 25)),
    2001: ("P01_c.zip", date(2001, 5, 13)),
    2005: ("P05_c.zip", date(2005, 4, 17)),
    2009: ("P09_c.zip", date(2009, 3, 1)),
    2012: ("P12_c.zip", date(2012, 10, 21)),
    2016: ("P16_c.zip", date(2016, 9, 25)),
    2020: ("P20_c.zip", date(2020, 7, 12)),
    2024: ("Cas-csv.zip", date(2024, 4, 21)),
}
FIXED = {
    "th": "th",
    "ambito": "name",
    "municipio": "name",
    "codmunicipio": "mun",
    "codcomarca": "comarca",
    "distrito": "district",
    "seccion": "section",
    "mesa": "mesa",
    "nota": "note",
    "censo": "electors",
    "votantes": "voters",
    "votos": "voters",
    "nulos": "invalid",
    "validos": "valid",
    "blancos": "blank",
    "votoscand": "cand",
    "votoscandidatura": "cand",
    "votoscandidaturas": "cand",
    "abstenciones": "abstentions",
    "abstencion": "abstentions",
}
FIELDS = ("electors", "voters", "invalid", "blank", "cand")
Row = dict[str, str]
BOE = "Boletín Oficial del Estado"
BOE_LICENSE = "https://www.boe.es/informacion/aviso_legal/index.php"
BOPV = "Boletín Oficial del País Vasco"
GAZETTE = "https://www.boe.es/boe/dias/"
BULLETIN = "https://www.euskadi.eus/bopv2/datos/"
# The rules that set each election's threshold, as the BOE prints them (it republished the Basque laws in 2011 and
# 2012), with the passages the build finds in each file.
LAWS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "rdl-20-1977": (
        "Real Decreto-ley 20/1977, de 18 de marzo, sobre Normas Electorales (BOE 70, 23 March 1977)",
        GAZETTE + "1977/03/23/pdfs/A06584-06600.pdf",
        ("el tres por ciento de los votos válidos emitidos en el distrito",),
    ),
    "decreto-1980": (
        (
            "Decreto del Consejo General del País Vasco de 12 de enero de 1980 por el que se convocan elecciones al "
            "Parlamento Vasco (Boletín Oficial del Consejo General del País Vasco 32, 12 January 1980; BOE 17, 19 "
            "January 1980)"
        ),
        GAZETTE + "1980/01/19/pdfs/A01449-01449.pdf",
        ("para elegir 20 Diputados por circunscripción", "Real Decreto-ley 20/1977"),
    ),
    "ley-28-1983": (
        (
            "Ley 28/1983, de 25 de noviembre, de Elecciones al Parlamento Vasco (BOPV 182, 10 December 1983; BOE 92, 17 "
            "April 2012)"
        ),
        GAZETTE + "2012/04/17/pdfs/BOE-A-2012-5194.pdf",
        (
            "Se elegirán veinticinco parlamentarios por circunscripción electoral",
            "un porcentaje mínimo de votos válidos del 5% del total",
            "no se computarán como votos válidos los votos nulos y los votos en blanco",
        ),
    ),
    "ley-5-1990": (
        (
            "Ley 5/1990, de 15 de junio, de Elecciones al Parlamento Vasco (BOPV 134, 6 July 1990; BOE 51, 29 February "
            "2012)"
        ),
        GAZETTE + "2012/02/29/pdfs/BOE-A-2012-2859.pdf",
        (
            "el 5 por 100 del total de votos válidos emitidos en la respectiva circunscripción",
            "Se considerarán como votos válidos emitidos los votos obtenidos por las candidaturas y los votos en blanco",
        ),
    ),
    "ley-6-2000": (
        (
            "Ley 6/2000, de 4 de octubre, de modificación de la Ley 5/1990, de 15 de junio, de Elecciones al Parlamento "
            "Vasco (BOPV 213, 7 November 2000; BOE 299, 13 December 2011)"
        ),
        GAZETTE + "2011/12/13/pdfs/BOE-A-2011-19448.pdf",
        (
            "el 3 por ciento del total de votos válidos emitidos en la respectiva circunscripción",
            "entrará en vigor al día siguiente al de su publicación",
        ),
    ),
}
# The laws behind each threshold's elections.
SYSTEM_LAWS = {3: ("rdl-20-1977", "decreto-1980", "ley-5-1990", "ley-6-2000"), 5: ("ley-28-1983", "ley-5-1990")}
# The Junta Electoral de la Comunidad Autónoma's publication of each election's results and seats in the BOPV. The
# BOPV's first issue appeared on 30 April 1980, after the first election, and for 1986 the Interior Department's order
# of 3 December 1986 (BOPV 241, 5 December 1986) called for one that the issues of December 1986 to February 1987 do
# not carry.
PROCLAMATIONS: dict[int, tuple[str, str]] = {
    1984: (
        (
            "Certificación de la Junta Electoral de la Comunidad Autónoma, de 9 de marzo de 1984, de los resultados finales "
            "globales obtenidos por cada candidatura en las Elecciones al Parlamento Vasco de 1984 y de los escaños "
            "adjudicados a cada una de ellas (BOPV 47, 12 March 1984)"
        ),
        BULLETIN + "1984/03/8400533a.pdf",
    ),
    1990: (
        (
            "Acuerdo de 22 de noviembre de 1990, de la Junta Electoral de la Comunidad Autónoma, por el que se dispone la "
            "publicación de los resultados correspondientes al escrutinio general de las elecciones al Parlamento Vasco "
            "celebradas el día 28 de octubre de 1990 (BOPV 241, 3 December 1990)"
        ),
        BULLETIN + "1990/12/9003612a.pdf",
    ),
    1994: (
        (
            "Acuerdo de 10 de noviembre de 1994, de la Junta Electoral de la Comunidad Autónoma, por la que se dispone la "
            "publicación de los resultados correspondientes al escrutinio general de las elecciones al Parlamento Vasco "
            "celebradas el día 23 de octubre (BOPV 217, 15 November 1994)"
        ),
        BULLETIN + "1994/11/9404192a.pdf",
    ),
    1998: (
        (
            "Acuerdo de 9 de noviembre de 1998, de la Junta Electoral de la Comunidad Autónoma, por el que se dispone la "
            "publicación de los resultados correspondientes al escrutinio general de las Elecciones al Parlamento Vasco del "
            "25 de octubre de 1998 (BOPV 220, 18 November 1998)"
        ),
        BULLETIN + "1998/11/9805186a.pdf",
    ),
    2001: (
        (
            "Acuerdo de la Junta Electoral de la Comunidad Autónoma del País Vasco, por la que se acuerda la publicación de "
            "los resultados correspondientes al escrutinio general de las elecciones al Parlamento Vasco (BOPV 105, 4 June "
            "2001)"
        ),
        BULLETIN + "2001/06/0103081a.pdf",
    ),
    2005: (
        (
            "Acuerdo de la Junta Electoral de la Comunidad Autónoma del País Vasco, por la que se acuerda la publicación de "
            "los resultados correspondientes al escrutinio general de las elecciones al Parlamento Vasco celebradas el día "
            "17 de abril de 2005 (BOPV 89, 13 May 2005)"
        ),
        BULLETIN + "2005/05/0502415a.pdf",
    ),
    2009: (
        (
            "Acuerdo de 18 de marzo de 2009, de la Junta Electoral de la Comunidad Autónoma del País Vasco, por el que se "
            "dispone la publicación de los resultados correspondientes al escrutinio general de las elecciones al "
            "Parlamento Vasco celebradas el día 1 de marzo de 2009 (BOPV 65, 3 April 2009)"
        ),
        BULLETIN + "2009/04/0901888a.pdf",
    ),
    2012: (
        (
            "Acuerdo de 9 de noviembre de 2012, de la Junta Electoral de la Comunidad Autónoma del País Vasco, por el que se "
            "dispone la publicación de los resultados correspondientes al escrutinio general de las elecciones al "
            "Parlamento Vasco celebradas el día 21 de octubre de 2012 (BOPV 223, 19 November 2012)"
        ),
        BULLETIN + "2012/11/1205083a.pdf",
    ),
    2016: (
        (
            "Acuerdo de 10 de octubre de 2016, de la Junta Electoral de la Comunidad Autónoma del País Vasco, por el que se "
            "dispone la publicación de los resultados correspondientes al escrutinio general de las elecciones al "
            "Parlamento Vasco celebradas el día 25 de septiembre de 2016 (BOPV 194, 13 October 2016)"
        ),
        BULLETIN + "2016/10/1604297a.pdf",
    ),
    2020: (
        (
            "Acuerdo de 22 de julio de 2020, de la Junta Electoral de la Comunidad Autónoma del País Vasco, por el que se "
            "dispone la publicación de los resultados correspondientes al escrutinio general de las elecciones al "
            "Parlamento Vasco celebradas el día 12 de julio de 2020 (BOPV 148, 29 July 2020)"
        ),
        BULLETIN + "2020/07/2003044a.pdf",
    ),
    2024: (
        (
            "Acuerdo de 2 de mayo de 2024, de la Junta Electoral de la Comunidad Autónoma del País Vasco, por el que se "
            "dispone la publicación de los resultados correspondientes al escrutinio general de las elecciones al "
            "Parlamento Vasco celebradas el día 21 de abril de 2024 (BOPV 90, 9 May 2024)"
        ),
        BULLETIN + "2024/05/2402188a.pdf",
    ),
}
# The 1984 and 1990 publications are scans without a text layer; these are the seats they print for each list, read
# from the page images, under the labels they print.
SCANNED_SEATS: dict[int, dict[str, dict[str, int]]] = {
    1984: {
        "01": {"PNV-EAJ": 9, "PSE-PSOE": 7, "AP-PDP-UL": 4, "HB": 3, "EE": 2},
        "20": {"PNV-EAJ": 11, "PSE-PSOE": 6, "HB": 5, "EE": 2, "AP-PDP-UL": 1},
        "48": {"PNV-EAJ": 12, "PSE-PSOE": 6, "HB": 3, "EE": 2, "AP-PDP-UL": 2},
    },
    1990: {
        "01": {"EAJ-PNV": 6, "PSE-PSOE": 6, "HB": 3, "UA": 3, "PP": 3, "EA": 2, "EE": 2},
        "20": {"HB": 6, "EAJ-PNV": 6, "PSE-PSOE": 5, "EA": 5, "EE": 2, "PP": 1},
        "48": {"EAJ-PNV": 10, "PSE-PSOE": 5, "HB": 4, "PP": 2, "EA": 2, "EE": 2},
    },
}
# The BOPV's label for a list, folded, where it differs from the label in the Basque Government's files.
ALIASES: dict[str, tuple[str, ...]] = {
    "pnveaj": ("EAJ-PNV",),
    "ua": ("U.AL.",),
    "unidadalavesa": ("U.AL.",),
    "iueb": ("EB",),
    "ebiu": ("EB",),
    "pseee": ("PSE-EE/PSOE",),
    "coalicioneajpnvea": ("EAJ-PNV / EA",),
    "coalicioneajpnveaeanv": ("EAJ-PNV / EA",),
    # The 2005 publication prints PCTV-EAHK for Gipuzkoa and PCTV-EHAK elsewhere.
    "pctveahk": ("PCTV-EHAK",),
    "upyd": ("UPD", "UPYD"),
    "elkarrekinpodemospodemosahalduguezkeranitzaizquierdaunidaequo": ("PODEMOS/AHAL DUGU-IU",),
    "elkarrekinpodemosiu": ("PODEMOS-AHALDUGU/EZKER AN",),
}
SEAT_WORDS = {
    "un": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
}
HEADER = re.compile(r"^\s*TERRITORIO HIST[ÓO]RICO DE (\w+)", re.MULTILINE)
SEAT_LINE = re.compile(r"^(?P<label>.+?)\s*(?::|\.{2,}|…+)\s*(?P<count>\w+) escaños?\.?$")
SEATS_END = re.compile(r"^(TERRITORIO|COMUNIDAD|Vitoria|EUSKAL|ARABAKO|BIZKAIKO|GIPUZKOAKO)")
PAGE_HEAD = re.compile(
    r"^(N\.º \d+|\w+ \d+ de \w+ de \d{4}|BOLETÍN OFICIAL DEL PAÍS VASCO|\d{4}/\d+ \(\d+/\d+\)|EHAA\b.*|BOPV\b.*|"
    r"B\.O\.P\.V\..*|\d+)$"
)
NAMED = {"alava": "01", "araba": "01", "gipuzkoa": "20", "bizkaia": "48"}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(char for char in plain.lower() if char.isalnum())


def territory(text: str) -> str:
    key = fold(text)
    if key.startswith(("araba", "alava")):
        return "01"
    if key.startswith("gipuzkoa"):
        return "20"
    if key.startswith("bizkaia"):
        return "48"
    raise ValueError(f"unknown territory {text!r}")


def url(year: int) -> str:
    return BASE + quote(ELECTIONS[year][0])


def fetch() -> None:
    for year in ELECTIONS:
        raw.fetch(SOURCE, url(year), suffix=".zip")
    for _, link, _ in LAWS.values():
        raw.fetch(SOURCE, link, suffix=".pdf")
    for _, link in PROCLAMATIONS.values():
        raw.fetch(SOURCE, link, suffix=".pdf")


def pages(path: Path) -> list[str]:
    with pymupdf.open(path) as document:
        return [page.get_text() for page in document]


def folded(text: str) -> str:
    """Text without case, accents, punctuation, spacing, or the hyphens that break a word at a line's end, so a
    passage reads the same in a gazette's text layer."""
    return fold(re.sub(r"[­-]\s*\n\s*", "", text))


def printed(path: Path, passage: str) -> int:
    """The page of a gazette file that prints the passage."""
    for number, page in enumerate(pages(path), 1):
        if folded(passage) in folded(page):
            return number
    raise ValueError(f"{path} does not print {passage!r}")


def proclaimed(path: Path) -> dict[str, dict[str, int]]:
    """The seats of each list in each territory, as the Junta Electoral's publication prints them in Spanish under
    "Distribución de escaños"; the Basque columns count seats as eserleku and are passed over."""
    text = "\n".join(pages(path))
    seats: dict[str, dict[str, int]] = {}
    for match in HEADER.finditer(text):
        found: dict[str, int] = {}
        pending = ""
        start = text.index("Distribución de escaños", match.end())
        for line in text[start:].split("\n")[1:]:
            line = line.strip()
            if not line or PAGE_HEAD.match(line):
                continue
            if SEATS_END.match(line):
                break
            pending = f"{pending} {line}".strip()
            if not re.search(r"escaños?\.?$", pending):
                if len(pending) > 200:
                    break
                continue
            seat = SEAT_LINE.match(pending)
            if seat is None:
                break
            found[seat["label"]] = SEAT_WORDS[fold(seat["count"])]
            pending = ""
        seats[NAMED[fold(match.group(1))]] = found
    return seats


def matched(printed_seats: dict[str, int], declared: Counter[str], where: str) -> None:
    """Check that the seats a publication prints for each list equal the declared seats of the list it names."""
    seen: dict[str, int] = {}
    for label, count in printed_seats.items():
        key = fold(label)
        names = [name for name in declared if fold(name) == key] or [
            name for name in ALIASES.get(key, ()) if name in declared
        ]
        if len(names) != 1:
            raise ValueError(f"{where}: the BOPV's list {label!r} matches {names} among {sorted(declared)}")
        seen[names[0]] = count
    if seen != dict(declared):
        raise ValueError(f"{where}: the BOPV prints the seats {seen}, the files declare {dict(declared)}")


def sheets(year: int, source: str = SOURCE, link: str | None = None) -> dict[str, list[list[str]]]:
    found: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(raw.cached(source, link or url(year)).path) as archive:
        for entry in archive.namelist():
            stem = entry.rsplit("/", 1)[-1].lower()
            if not stem.endswith(".csv"):
                continue
            kind = next(kind for kind in ("cir", "elec", "ele", "mes", "mun") if stem.startswith(kind))
            text = archive.read(entry).decode("latin-1")
            delimiter = ";" if text.count(";") > text.count(",") else ","
            found["elec" if kind == "ele" else kind] = [
                [cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)
            ]
    return found


def results(rows: list[list[str]]) -> list[Row]:
    start = next(index for index, row in enumerate(rows) if "censo" in map(fold, row))
    header = [fold(cell) for cell in rows[start]]
    above = rows[start - 1] if start else []
    last = max(index for index, key in enumerate(header) if key in FIXED and FIXED[key] == "abstentions")
    groups: list[tuple[str, int, int]] = []
    column = last + 1
    width = len(header)
    while column < width:
        named = column < len(above) and above[column].strip()
        label = above[column].strip() if named else rows[start][column]
        if not label:
            column += 1
            continue
        size = 1
        if named:
            while column + size < len(above) and above[column + size].strip() == label:
                size += 1
        if size > 1:
            picked = [at for at in range(column, column + size) if not rows[start][at].strip().startswith("%")]
            if picked:
                groups.append((label, picked[0], 1))
        else:
            groups.append((label, column, size))
        column += size
    out: list[Row] = []
    for raw_row in rows[start + 1 :]:
        if len(raw_row) < last or not any(raw_row):
            continue
        row: Row = {}
        for index, key in enumerate(header[: last + 1]):
            if key in FIXED:
                row[FIXED[key]] = raw_row[index].strip()
        if not row.get("th") and (row.get("name", "").upper().startswith("TOTAL") or not row.get("name")):
            continue
        votes: dict[str, str] = {}
        for label, column, size in groups:
            if column < len(raw_row) and raw_row[column].strip():
                votes[label] = raw_row[column].strip()
        row["votes"] = ";".join(f"{label}\t{value}" for label, value in votes.items())
        out.append(row)
    return out


def number(text: str) -> int | None:
    return int(text.replace(".", "")) if text.strip() else None


def tidy(rows: list[Row], year: int, keys: tuple[str, ...]) -> tuple[list[dict], list[dict]]:
    units: list[dict] = []
    votes: list[dict] = []
    for row in rows:
        if "th" in row and not row["th"]:
            continue
        unit = {"year": year}
        if "th" in row:
            unit["th"] = territory(row["th"])
        else:
            unit["th"] = territory(row["name"])
        for key in keys:
            if key == "mun":
                unit[key] = row[key].zfill(3)
            elif key == "district":
                unit[key] = row[key].zfill(2)
            elif key == "section":
                unit[key] = row[key].zfill(3)
            else:
                unit[key] = row[key]
        for field in FIELDS:
            unit[field] = number(row[field]) if field in row else None
        units.append(unit)
        for pair in row["votes"].split(";"):
            label, value = pair.split("\t")
            votes.append({**{key: unit[key] for key in ("year", "th", *keys)}, "party": label, "votes": int(value)})
    return units, votes


def elected(rows: list[list[str]], year: int) -> list[dict]:
    start = next(index for index, row in enumerate(rows) if "partido" in map(fold, row))
    header = [fold(cell) for cell in rows[start]]
    th, party = header.index("th"), header.index("partido")
    out = []
    for row in rows[start + 1 :]:
        if len(row) <= max(th, party) + 1 or not row[th]:
            continue
        name = ", ".join(part.strip() for part in row[-1].strip("'\" ").split(","))
        out.append({"year": year, "th": territory(row[th]), "party": row[party], "name": name})
    return out


def threshold(year: int) -> float:
    """The share of a territory's valid votes a list needs to win a seat: 3% under the rules of 1977 that the first
    election applied, 5% under Ley 28/1983 and Ley 5/1990, and 3% since Ley 6/2000 took effect on 8 November 2000."""
    return 0.05 if 1984 <= year <= 1998 else 0.03


def blank_valid(year: int) -> bool:
    """Whether blank ballots count among the valid votes the threshold is a share of. Ley 28/1983 (1984 and 1986)
    counted neither null nor blank ballots as valid, and Ley 5/1990 counts blank ballots as valid. The rules of 1977
    do not say; both readings give the declared seats of 1980."""
    return year not in (1984, 1986)


def dhondt(votes: dict[str, int], valid: int, seats: int, share: float) -> Counter[str]:
    eligible = {code: count for code, count in votes.items() if count >= share * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = defaultdict(list)
    for year in ELECTIONS:
        files = sheets(year)
        cir = results(files["cir"])
        mun = results(files["mun"])
        mes = results(files["mes"])
        for name, rows, keys in (
            ("terr", cir, ()),
            ("mun", mun, ("mun", "name")),
            ("mesa", mes, ("mun", "district", "section", "mesa", "name")),
        ):
            units, votes = tidy(rows, year, keys)
            tables[name].extend(units)
            tables[f"{name}_votes"].extend(votes)
        members = elected(files["elec"], year)
        ran = {row["party"] for row in tables["terr_votes"] if row["year"] == year}
        for member in members:
            if member["party"] not in ran:
                (member["party"],) = [p for p in ran if p.startswith(member["party"]) or member["party"].startswith(p)]
        tables["elected"].extend(members)
        counts = Counter((row["th"], row["party"]) for row in members)
        terr_votes = [row for row in tables["terr_votes"] if row["year"] == year]
        for th in TERRITORIES:
            totals = next(row for row in tables["terr"] if row["year"] == year and row["th"] == th)
            part = {row["party"]: row["votes"] for row in terr_votes if row["th"] == th}
            seats = sum(count for (code, _), count in counts.items() if code == th)
            valid = totals["cand"] + (totals["blank"] if blank_valid(year) else 0)
            won = dhondt(part, valid, seats, threshold(year))
            wrong = {p for p in set(part) | {p for (c, p) in counts if c == th} if won[p] != counts[th, p]}
            unknown = {p for (c, p) in counts if c == th} - set(part)
            print(
                year,
                th,
                f"seats={seats}",
                f"lists={sum(part.values()) == totals['cand']}",
                f"dhondt_differs={sorted(wrong)}",
                f"unknown={sorted(unknown)}",
            )
            if wrong or unknown:
                raise ValueError(f"{year} {th}: D'Hondt under the law's threshold does not give the declared seats")
        for name in ("mun", "mesa"):
            for th in TERRITORIES:
                got = sum(r["votes"] for r in tables[f"{name}_votes"] if r["year"] == year and r["th"] == th)
                official = sum(r["votes"] for r in terr_votes if r["th"] == th)
                print(year, name, th, got, official, got - official)
    for name, rows in tables.items():
        frame = pl.DataFrame(rows, infer_schema_length=None)
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")
    build()


def emit() -> None:
    package.run("load", [KEY])


def retrieved(conn: Connection) -> Callable[[raw.Artifact], raw.Artifact]:
    """A file the cache holds with the same bytes and address as a file the archive already cites keeps the
    retrieval time the archive recorded for it, so a fresh copy of the cache rebuilds the same citation."""
    stored: dict[tuple[str, str], datetime] = {
        (str(url), str(digest)): at
        for url, digest, at in conn.execute(
            """select a.url, a.sha256, a.retrieved_at from source_artifact a
               join changeset c on c.id = a.changeset_id where c.pipeline = %s""",
            [SOURCE],
        )
    }

    def pin(artifact: raw.Artifact) -> raw.Artifact:
        at = stored.get((artifact.url, artifact.sha256))
        return artifact if at is None else replace(artifact, retrieved_at=at)

    return pin


FIRST_ELECTION_NOTE = (
    "The first Parliament of the autonomous community had 60 seats, 20 for each historic territory, as the Consejo "
    "General del País Vasco's decree of 12 January 1980 calling the election set (article 2) under the first "
    "transitional provision of the " + STATUTE + ". From 1984 each territory elects 25, 75 in all (Ley 28/1983, "
    "article 9; " + LAW + ", article 10)."
)
SYSTEMS = {
    3: (
        "Closed party lists in three constituencies, the historic territories of Álava, Gipuzkoa, and Bizkaia, which "
        "share the seats equally. Seats go by the D'Hondt method to the lists with at least 3% of the valid votes in "
        "the territory. The first election, of 9 March 1980, followed the rules of the 1977 general election, as the "
        "Consejo General del País Vasco's decree of 12 January 1980 calling it set (article 3): Real Decreto-ley "
        "20/1977 leaves out the lists below 3% of the valid votes cast in the district (article 20.4.b) and does not "
        "say whether blank ballots count among them, and either reading gives the declared seats. Ley 6/2000, in force "
        "since 8 November 2000, set the same 3% in article 11.1 of " + LAW + ", which counts the votes to candidacies "
        "and blank ballots as valid (article 11.2); it has governed every election since 2001. In 2001 a 5% threshold "
        "would have given the same seats; from 2005 on, only 3% gives the declared seats. The Basque Government "
        "publishes the elected members of each list, and those are the declared seats stored here. From 2001 on they "
        "equal the seats of each list that the Junta Electoral de la Comunidad Autónoma published in the Boletín "
        "Oficial del País Vasco after each election; the 1980 election came before the bulletin's first issue."
    ),
    5: (
        "Closed party lists in three constituencies, the historic territories of Álava, Gipuzkoa, and Bizkaia, which "
        "elect 25 members each. Seats go by the D'Hondt method to the lists with at least 5% of the valid votes in the "
        "territory. Ley 28/1983 set the threshold for the elections of 1984 and 1986 (article 10) and counted neither "
        "null nor blank ballots as valid; " + LAW + " kept 5% for those of 1990, 1994, and 1998 (article 11.1) and "
        "counts blank ballots as valid (article 11.2), until Ley 6/2000 lowered the threshold to 3% from 8 November "
        "2000. Counting blank ballots or not changes no seat in these elections, and in 1986 and 1998 only the 5% "
        "threshold gives the declared seats. The Basque Government publishes the elected members of each list, and "
        "those are the declared seats stored here. They equal the seats of each list that the Junta Electoral de la "
        "Comunidad Autónoma published in the Boletín Oficial del País Vasco for 1984, 1990, 1994, and 1998; for 1986 "
        "the bulletin's issues of December 1986 to February 1987 carry no such publication."
    ),
}
PROCLAIMED = (
    " The Junta Electoral de la Comunidad Autónoma published the seats of each list in this territory in the Boletín "
    "Oficial del País Vasco ({gazette}), and they equal the seats stored here."
)
UNPROCLAIMED = {
    1980: (
        " The election came before the first issue of the Boletín Oficial del País Vasco (30 April 1980), so the seats "
        "rest on the Basque Government's files."
    ),
    1986: (
        " The Interior Department's order of 3 December 1986 (BOPV 241, 5 December 1986) called on the Junta Electoral "
        "de la Comunidad Autónoma to publish the results and seats in the Boletín Oficial del País Vasco; the issues "
        "of December 1986 to February 1987 carry no such publication, so the seats rest on the Basque Government's "
        "files."
    ),
}
TERRITORY_METHOD = (
    "List votes, seats, registered voters, ballots cast, blank ballots, and null ballots by historic territory from the "
    "Basque Government's results files. Valid ballots here are the votes to candidacies, with blank ballots stored "
    "apart. The seats are the number of members the Elec file names for each list."
)
MUNICIPALITY_METHOD = (
    "List votes and ballot counts by municipality from the Basque Government's results files, with votes cast abroad "
    "on a separate row of the territory where the file prints one. The municipal figures add up to {votes:,} list "
    "votes, against {official:,} in the territory results."
)
MESA_METHOD = (
    "List votes and ballot counts by polling station (mesa) from the Basque Government's results files, each mesa on "
    "its census section where the 2025 INE section map has the same code, and otherwise on its municipality. The mesa "
    "figures add up to {votes:,} list votes, against {official:,} in the territory results, and the files give no "
    "unit for the difference."
)
LISTS_DIFFER = (
    " In the source, the list votes of this territory add up to {lists:,}, against {candidacies:,} votes to "
    "candidacies in its own totals."
)
ABROAD = 4


def clean(text: str) -> str:
    return " ".join(text.split())


def build() -> Path:
    """Write the package es.euskadi/all from the parsed tables. It reads the database, through Draft, so the rows it
    rebuilds keep the ids the changeset path gave them."""
    terr = pl.read_parquet(PARSED / "terr.parquet")
    terr_votes = pl.read_parquet(PARSED / "terr_votes.parquet")
    muns = pl.read_parquet(PARSED / "mun.parquet")
    mun_votes = pl.read_parquet(PARSED / "mun_votes.parquet")
    mesas = pl.read_parquet(PARSED / "mesa.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    members = pl.read_parquet(PARSED / "elected.parquet")
    with connect() as conn:
        pin = retrieved(conn)
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parlamento Vasco elections of 1980 to 2024 by historic territory, municipality, and polling station, "
                "with the elected members, from the Basque Government's results files, with the laws that set each "
                "election's threshold and the Junta Electoral's publications of the seats in the Boletín Oficial del "
                "País Vasco"
            ),
            polity="ES",
        )
        cs.accept(
            "accounting.votes_valid",
            "the Basque Government's 1986 files print list votes that differ from the votes to candidacies in Álava's "
            "territory total, two municipalities, and ten mesas; they are stored as printed, and the territory set's "
            "note gives both figures",
        )
        cs.accept(
            "accounting.ballots_sum",
            "the Basque Government's files print voters that differ from the sum of valid, null, and blank ballots in "
            "17 municipalities and 144 mesas of 1980, 9 mesas of 1984, 11 of 1986, and one territory, 3 municipalities, "
            "and 10 mesas of 1990; they are stored as printed",
        )
        cs.accept(
            "accounting.turnout",
            "the Basque Government's 2001 file prints more voters than electors for one Gipuzkoa mesa; it is stored as "
            "printed",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, basque = cs.polity("ES"), cs.polity("ES-PV")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Parlamento Vasco")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license=None,
            license_url=None,
            attribution="Fuente: Gobierno Vasco, euskadi.eus.",
        )
        for year in ELECTIONS:
            cs.artifact(pin(raw.cached(SOURCE, url(year))), source)

        def gazette(publisher: str, title: str, link: str) -> tuple[UUID, raw.Artifact]:
            """A gazette item as a source, citing its file."""
            item = cs.source(publisher, title)
            boe = publisher == BOE
            cs.add(
                "source",
                id=item,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=link,
                license="Public record: official gazette, free to reuse" if boe else None,
                license_url=BOE_LICENSE if boe else None,
                attribution="Fuente: Boletín Oficial del Estado." if boe else "Fuente: Boletín Oficial del País Vasco.",
            )
            artifact = pin(raw.cached(SOURCE, link))
            cs.artifact(artifact, item)
            return item, artifact

        laws: dict[str, tuple[UUID, int]] = {}
        for key, (title, link, passages) in LAWS.items():
            item, artifact = gazette(BOE, title, link)
            laws[key] = (item, min(printed(artifact.path, passage) for passage in passages))
        proclamations: dict[int, tuple[UUID, str, dict[str, dict[str, int]]]] = {}
        for year, (title, link) in PROCLAMATIONS.items():
            item, artifact = gazette(BOPV, title, link)
            seats = SCANNED_SEATS.get(year) or proclaimed(artifact.path)
            if set(seats) != set(TERRITORIES):
                raise ValueError(f"{year}: the BOPV's seats name the territories {sorted(seats)}")
            for th, found in seats.items():
                declared = members.filter((pl.col("year") == year) & (pl.col("th") == th))
                matched(found, Counter(declared["party"].to_list()), f"{year} {th}")
            proclamations[year] = (item, title.rsplit("(", 1)[1].rstrip(")"), seats)

        parties: dict[str, UUID] = {}
        for label in sorted(terr_votes["party"].unique()):
            parties[label] = cs.key(f"{SOURCE}.party", label)
            # A party merged into another pipeline's resolves to that party, which keeps its own record.
            if parties[label] in cs.minted:
                cs.add("party", id=parties[label], kind="party", polity_id=basque)
                name(parties[label], label, "es")

        body = cs.key("es.body", "basque-parliament")
        cs.add("body", id=body, polity_id=basque, kind="unicameral")
        name(body, "Basque Parliament")
        name(body, "Parlamento Vasco", "es", "native")
        name(body, "Eusko Legebiltzarra", "eu", "native")
        systems: dict[float, UUID] = {}
        for share in sorted({threshold(year) for year in ELECTIONS}):
            percent = int(share * 100)
            system = systems[share] = cs.key("es.electoral_system", f"basque-parliament-{percent}")
            cs.add(
                "electoral_system",
                id=system,
                polity_id=basque,
                rules={
                    "formula": "dhondt",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "scope": "contest", "percent": percent},
                },
                rules_version=1,
            )
            name(system, f"Basque Parliament closed-list D'Hondt system with a {percent}% threshold")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEMS[percent])
            for key in SYSTEM_LAWS[percent]:
                item, page = laws[key]
                cs.add("citation", entity_id=system, source_id=item, locator={"page": page})

        sizes = members.group_by("year").agg(seats=pl.len()).sort("year")
        previous: tuple[date, int] | None = None
        for year, seats in sizes.iter_rows():
            day = ELECTIONS[year][1]
            if previous is not None and previous[1] != seats:
                cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=day)
                previous = None
            if previous is None:
                previous = (day, seats)
        if previous is not None:
            cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=None)

        # Units sit on the 2025 map; es.ine also holds earlier sections under the same codes, dated to end when the
        # map redrew them.
        municipality_areas = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'es.municipio' and c.valid @> date '2025-01-01'"""
            ).fetchall()
        )
        section_areas = dict(
            conn.execute(
                """select c.code, a.id from area a join area_code c on c.area_id = a.id
                   where a.scheme_code = 'es.seccion' and c.valid @> date '2025-01-01'"""
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province_units: dict[str, UUID] = {}
        for code in TERRITORIES:
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, None, "equals", cs.area("es.provincia", code), "standard")
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(province: str, mun: str) -> UUID:
            code = province + mun
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if mun == "999":
                    area, relation, kind = cs.area("es.provincia", province), "within", "aggregate"
                else:
                    area = municipality_areas.get(code)
                    relation, kind = ("equals" if area is not None else "unlocated"), "standard"
                unit_rows[unit] = (unit, "es.municipio", code, province_units[province], relation, area, kind)
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def accounting(result_set: UUID, unit: UUID, row: dict[str, int | None], channel: int = 0) -> None:
            for measure, field in (
                (REGISTERED, "electors"),
                (BALLOTS, "voters"),
                (VALID, "cand"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                if (value := row[field]) is not None:
                    values.append((str(result_set), str(unit), None, measure, value, channel))

        for year, (_, day) in ELECTIONS.items():
            election = cs.key("es.election", f"basque:{day}")
            cs.add("election", id=election, polity_id=basque, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} Basque regional election")
            name(election, f"Elecciones al Parlamento Vasco de {year}", "es", "native")
            path = f"es/pv/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            if year == 1980:
                cs.add("note", entity_id=election, kind="context", body_md=FIRST_ELECTION_NOTE)
            stamp = f"{year}{day.month:02d}"
            year_filter = pl.col("year") == year
            for province, province_name in TERRITORIES.items():
                where = year_filter & (pl.col("th") == province)
                total = terr.filter(where).row(0, named=True)
                rows = terr_votes.filter(where).sort("votes", descending=True)
                declared = members.filter(where)
                seats_by_party = Counter(declared["party"].to_list())
                key = f"{day}:{province}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=cs.area("es.provincia", province),
                    magnitude=declared.height,
                    electoral_system_id=systems[threshold(year)],
                    default_result_kind="counted",
                )
                name(contest, province_name)
                if NATIVE[province] != province_name:
                    name(contest, NATIVE[province], "es", "native")
                cs.add("slug", path=f"{path}/{slug(province_name)}", entity_id=contest)
                options: dict[str, UUID] = {}
                for entry in rows.iter_rows(named=True):
                    option = options[entry["party"]] = cs.key(f"{SOURCE}.option", f"{key}:{entry['party']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[entry["party"]],
                        ballot_label=entry["party"],
                    )
                official = int(rows["votes"].sum())

                def make_set(label: str, default: bool, completeness: float, note: str) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=default,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    return result_set

                note = TERRITORY_METHOD
                if official != total["cand"]:
                    note += LISTS_DIFFER.format(lists=official, candidacies=total["cand"])
                if year in proclamations:
                    note += PROCLAIMED.format(gazette=proclamations[year][1])
                else:
                    note += UNPROCLAIMED[year]
                territory_set = make_set("territory", True, 1, note)
                if year in proclamations:
                    cs.add("citation", entity_id=territory_set, source_id=proclamations[year][0], locator={"page": 1})
                unit = province_units[province]
                accounting(territory_set, unit, total)
                for entry in rows.iter_rows(named=True):
                    values.append(
                        (str(territory_set), str(unit), str(options[entry["party"]]), VOTES, entry["votes"], 0)
                    )
                    if seats := seats_by_party.get(entry["party"], 0):
                        cs.add(
                            "outcome",
                            result_set_id=territory_set,
                            option_id=options[entry["party"]],
                            kind="won",
                            seats=seats,
                        )
                seen: Counter[str] = Counter()
                for member in declared.iter_rows(named=True):
                    person_key = f"{key}:{member['party']}:{fold(member['name'])}"
                    seen[person_key] += 1
                    if seen[person_key] > 1:
                        person_key += f":{seen[person_key]}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, member["name"], "es")
                    party = parties[member["party"]]
                    option = cs.key(f"{SOURCE}.option", person_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["party"]],
                        party_id=party,
                        ballot_label=member["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", person_key),
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=member["name"],
                        member_of_party_id=party,
                        is_independent=False,
                    )
                    cs.add("outcome", result_set_id=territory_set, option_id=option, kind="elected")

                places = muns.filter(where)
                place_votes = mun_votes.filter(where)
                got = int(place_votes["votes"].sum())
                municipality_set = make_set(
                    "municipality",
                    False,
                    min(got / official, 1),
                    MUNICIPALITY_METHOD.format(votes=got, official=official),
                )
                for row in places.iter_rows(named=True):
                    accounting(
                        municipality_set,
                        municipality_unit(province, row["mun"]),
                        row,
                        ABROAD if row["mun"] == "999" else 0,
                    )
                for row in place_votes.iter_rows(named=True):
                    values.append(
                        (
                            str(municipality_set),
                            str(municipality_unit(province, row["mun"])),
                            str(options[row["party"]]),
                            VOTES,
                            row["votes"],
                            ABROAD if row["mun"] == "999" else 0,
                        )
                    )

                units = mesas.filter(where)
                unit_votes = mesa_votes.filter(where)
                got = int(unit_votes["votes"].sum())
                mesa_set = make_set(
                    "mesa", False, min(got / official, 1), MESA_METHOD.format(votes=got, official=official)
                )
                mesa_units: dict[tuple[str, str, str, str], tuple[UUID, int]] = {}
                for row in units.iter_rows(named=True):
                    section = f"{province}{row['mun']}{row['district']}{row['section']}"
                    code = f"{stamp}:{section}{row['mesa']}"
                    unit = cs.unit("es.mesa", code)
                    channel = 0
                    if row["mun"] == "999":
                        parent, area, relation, kind, channel = (
                            province_units[province],
                            cs.area("es.provincia", province),
                            "within",
                            "aggregate",
                            ABROAD,
                        )
                    else:
                        parent = municipality_unit(province, row["mun"])
                        area = (section_areas.get(section) if year >= 2020 else None) or municipality_areas.get(
                            province + row["mun"]
                        )
                        relation, kind = ("within" if area is not None else "unlocated"), "standard"
                    unit_rows[unit] = (unit, "es.mesa", code, parent, relation, area, kind)
                    mesa_units[row["mun"], row["district"], row["section"], row["mesa"]] = (unit, channel)
                    accounting(mesa_set, unit, row, channel)
                for row in unit_votes.iter_rows(named=True):
                    unit, channel = mesa_units[row["mun"], row["district"], row["section"], row["mesa"]]
                    values.append((str(mesa_set), str(unit), str(options[row["party"]]), VOTES, row["votes"], channel))

        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": str(unit),
                        "polity_id": str(spain),
                        "scheme_code": scheme,
                        "code": code,
                        "parent_id": None if parent is None else str(parent),
                        "area_relation": relation,
                        "area_id": None if area is None else str(area),
                        "kind": kind,
                    }
                    for unit, scheme, code, parent, relation, area, kind in unit_rows.values()
                    if unit in cs.minted
                ],
                schema_overrides={"parent_id": pl.String, "area_id": pl.String},
            ),
        )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                    "channel": pl.Int16,
                },
                orient="row",
            ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
