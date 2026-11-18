"""Chile's Chamber and Senate elections of 1925-1973 by electoral grouping, from the returns of the Dirección del
Registro Electoral that Servel's historical archive publishes as scanned volumes.

Servel's page "Resultados Electorales 1925-1973" links one scanned PDF per chamber and election year. The volumes are
typewritten or printed tables with no text layer. Each grouping's figures (the agrupación departamental that elected
deputies, or the agrupación provincial that elected senators) were read from the page images by eye, with the page
and a box around each number, and kept in `registro_returns/{year}-{chamber}.json`. The parse checks that the figures
add up the way the volume says they do, and records every disagreement the volume itself prints in a note.

The volumes of 1925-1945 print each list's votes with each candidate's preference votes and the votes cast for the
list without a preference; from 1949 on they print votes by party only, by commune, with the seats each party won.

The Senate volumes of 1949-1973 also print each grouping's votes by commune (by civil circumscription in 1949, by
department in 1957), and from 1957 on each candidate's votes there too. Those tables were read the same way into
`registro_communes/{year}-senators.json`, one row per printed unit with every printed subtotal; they publish a finer
result set that supersedes the grouping's total, and the grouping totals already read become its declared figures.
"""

import json
import os
import re
import subprocess
import unicodedata
from collections import Counter
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import NAMESPACE, Package

SOURCE = "cl.registro"
CHILE = str(uuid5(NAMESPACE, "polity\x1fCL"))
RETURNS = Path(__file__).with_name("registro_returns")
COMMUNES = Path(__file__).with_name("registro_communes")
PAGE = "https://www.servel.cl/archivo-historicoybiblioteca/"
SHARE = "https://servelcl-my.sharepoint.com/:b:/g/personal/archivo-historicoii_servel_cl/"
DPI = 150
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK, PREFERENCE, LEGEND, OBJECTED = 1, 2, 3, 4, 5, 6, 7, 9, 15
DIGITIZED = 8

# The link Servel's archive page gives for each volume, as the archive's browser session saved it.
VOLUMES: dict[str, str] = {
    "1925-deputies": "EQO4ALb3CKNOrwJTyq_24YcBL4AQNdJEJVxKjVFRpil0FQ?e=qnjw9p",
    "1925-senators": "EWn7oUZuHtJAsRBPQi1uflABZsI0atUFrZwcnMclpYOcyQ?e=wy6Vxx",
    "1930-deputies": "Ea6BDYsegudCvqIr0Y3koaMBpRdjgqxbliJBnlebAHmM7w?e=YOb43v",
    "1930-senators": "EWIXypew8C9MoZjpIBP5R6YBIMsALshU5d1cE5vmD8Xo2g?e=eOryJS",
    "1932-deputies": "EXlJeh5bkpNNgNku76DtD_sBVixw1PNL3vVQQAUrxQ7VNw?e=eZfmVP",
    "1932-senators": "ERfMmPymSpdOtWtbQbF4VbYBo3VQsIAbPPjYk6ADnY5YKg?e=jFIklt",
    "1937-deputies": "ERdwsvsHrvNKqjdHMBsmNI8BOEE0MSWeBK4HBtTqJJcLUQ?e=jEEMud",
    "1937-senators": "Ea6cBesCzVJBpUpm50dyGmUBsmar_gljlxOed_KjvaXGPA?e=tkTeZL",
    "1941-deputies": "EcbtOuQMKUBCnTfArk14zAwBYXAwVrGZ9jguC3CfA101Bw?e=L6WciD",
    "1941-senators": "EfxLl6taDmtGk26uEPE_CscBNf3GQslJTia3V8ijsnm5Pw?e=s9lJbA",
    "1945-deputies": "EQshT2NpBlhLpQl5gaYD_sYBQLnw1hPj0_Qlw08mBxs4mQ?e=EXuhAb",
    "1945-senators": "EXDmoLWpcWhOnfzNonCEN_gBD6Np81aSjVocgQUAlmfePA?e=r2zaAh",
    "1949-deputies": "EUEovk7q5gNDhp0ZfpaZan8BeCL9Dtt0-rwIc5UnPCzzYw?e=2EMNPC",
    "1949-senators": "ESZ3b8bYDnJMr0aMqy-LECoBxTdP0fUZVxVXFRU610yD8A?e=19v6F5",
    "1953-deputies": "EZxsqtGmn7ZDmM1QjZs8aDwBZKRSqGhHrQKbyHiBH35eqQ?e=NcnC35",
    "1953-senators": "EcDK3JZxsB5BgAAnYlGf_t8Bvq3_UTGPHUt42yBpmJHHfA?e=DlP6Hu",
    "1957-deputies": "IQDzNrcOUXcuT4-2GPFWeXoNAZXFQ9Ux_XDnLgWaIdV83uI?e=Ws9NVr",
    "1957-senators": "EYiZeb_wS_pMgcMsk1QzQAQB9bkM4U8rw3bp0Mnaxab-GQ?e=rtFoYT",
    "1961-deputies": "EUfJJ52vSNlMkUybjjflD5kBrD1BK5IO8EXqBR2hdbcS6g?e=sHYeV8",
    "1961-senators": "EaxzYRVTFCZIm_OxgHR6DqcB36-gPAZAol7-Dk7rkuIyvA?e=fmJvrk",
    "1965-deputies": "EU7B4NnZ94NNhxTWazmFR9wBgOXRlrAb4jCbLd0bt3tdlw?e=dWF0jT",
    "1965-senators": "EeejLVN_OfZOu3Zt2hMFfE8B8pMAkjltwyRyUA2VnOlqug?e=jsteTL",
    "1969-deputies": "Edjzg8j24elBnzYn8w8nlKYBjRTJBL_nU8OTgzmSvAqi3A?e=CPaK1s",
    "1969-senators": "EQF6ozAtGeZNqyahdlMh4-EB-HtvpXbh5P4VwQ3O97jy2Q?e=YDH9kc",
    "1973-deputies": "Ec7cppq27GJDiw9KCL08YH4BRZB_YloirdO0j-Qdlcqjcg?e=sI6s1r",
    "1973-senators": "Edb8kzAY3RlJvvK6CGqNC5sB3Z028t0UJipiW3tpHVBvSQ?e=g3Pxp2",
}

# Election days, for volumes that never print theirs; each matches the day the other chamber's volume prints.
DAYS = {
    1925: date(1925, 11, 22),
    1930: date(1930, 3, 2),
    1932: date(1932, 10, 30),
    1937: date(1937, 3, 7),
    1941: date(1941, 3, 2),
    1945: date(1945, 3, 4),
    1949: date(1949, 3, 6),
    1953: date(1953, 3, 1),
    1957: date(1957, 3, 3),
    1961: date(1961, 3, 5),
    1965: date(1965, 3, 7),
    1969: date(1969, 3, 2),
    1973: date(1973, 3, 4),
}

# Volumes whose tables by department print the count before the Tribunal Calificador rectified it at the grouping
# level: they publish a set of their own at the official stage, beside the rectified total, which stays the default.
UNRECTIFIED = {
    "1957-senators": (
        "Read by eye from the volume's typewritten table of the grouping's votes by department, one value per "
        "department (Santiago by district), each with its page and a box on the scan. The table prints each "
        "department's count before the Tribunal Calificador's rectification, which the volume applies only to the "
        "grouping's total; this set therefore holds the departments as counted, and the grouping's rectified "
        "total stays the contest's default. Seats and members elected belong to that rectified count."
    ),
}

# What a reader must know about a grouping's table by commune, by volume and grouping.
# Values that differ from the glyph on the page are listed here, each with the sums that settle it.
FINE_NOTES: dict[tuple[str, int], str] = {
    ("1949-senators", 8): "Mulchén's Radical Doctrinario cell shows only 72, its first digit lost; 172 is stored, "
    "which closes Mulchén's row, the department total (printed with a damaged first digit, read as 210) and the "
    "printed Bío-Bío total of 1,144.",
    ("1953-senators", 3): "The scan cuts off most digits of the Socialista Popular column in the Quillota "
    "department; each commune's figure is its printed valid votes less its other lists, which agrees with every "
    "visible leading digit, with the men's and women's columns, and with the printed province (7,334) and "
    "circunscripción (9,751) totals.",
    ("1953-senators", 9): "Riachuelo's Conservador Tradicionalista total reads 27; 37 is stored, as its men's and "
    "women's columns (32 + 5), the department total and the row's valid votes require.",
    ("1965-senators", 4): "Cartagena prints 225 votes for Rosende; 255 is stored, as its men's and women's columns "
    "(131 + 124), the list total and the San Antonio department total require. Santo Domingo's total cast has an "
    "illegible last digit; 1,698 is the sum of its column.",
    ("1965-senators", 8): "Cunco's figure for Loyola is an illegible mark; 6 is its men's and women's columns and "
    "what its list total requires.",
    ("1969-senators", 3): "Viña del Mar prints 1,117 blank votes; 942 is stored, its men's and women's columns "
    "(486 + 456), which alone closes its total cast (55,612) and the Valparaíso department's blank votes.",
    ("1973-senators", 10): "Three cells are stored as their men's and women's columns where the total column "
    "disagrees: Cisnes, Carlos Morales Abarzúa 2 (printed 1); Coyhaique, the Confederación's list 5,244 (printed "
    "5,241, its candidates also add to 5,244); and Coyhaique's total cast 8,654 (printed 5,654), which the "
    "Coyhaique department total requires.",
}

# Findings the volumes' own figures cause, by election day.
ACCEPTED: dict[date, dict[str, str]] = {
    date(1932, 10, 30): {
        "outcomes.list_members": "Valparaíso and Quillota (6.ª agrupación): Lista 20 won five seats, but the page "
        "holding the proclamation is cut off and names only Manuel Muñoz Cornejo, its first member elected.",
    },
    date(1945, 3, 4): {
        "outcomes.list_members": "Santiago's first district: the volume's table gives the Democrático party one seat "
        "and the Radical party four, while its list of elected deputies names two Democráticos and three Radicales; "
        "both are kept as printed.",
    },
    date(1953, 3, 1): {
        "accounting.votes_valid": "Osorno: the volume prints 15,786 valid votes, but its lists add to 15,706, the "
        "total cast closes with 15,706, and the national summary on page 34 agrees only with 15,706; the printed "
        "figure is kept.",
        "accounting.ballots_sum": "Osorno: with the printed 15,786 valid votes, valid plus 197 null and blank "
        "ballots is 80 more than the 15,903 cast; the volume's valid figure is a misprint for 15,706, kept as printed.",
    },
    date(1957, 3, 3): {
        "accounting.votes_valid": "Santiago's first district: the volume prints the Liberal party's total as 13,642 "
        "while its men's and women's columns add to 13,643, so the parties sum to one vote more than the printed "
        "valid total of 112,782.",
        "outcomes.seats_magnitude": "Magallanes elected two deputies, but the volume names only one, elected by "
        "majority, and prints 147 deputies elected in all.",
    },
    date(1965, 3, 7): {
        "accounting.ballots_sum": "Senate, Atacama and Coquimbo: the Tribunal Calificador's table prints the "
        "Socialist list as 15,809 while the Registro's own pages print 15,709 and a total cast of 116,664, so valid "
        "plus null and blank ballots is 100 more than the ballots cast.",
    },
}

# Findings the volumes' tables by commune cause, by election day; each joins the day's reason for the same code.
FINE_ACCEPTED: dict[date, dict[str, str]] = {
    date(1957, 3, 3): {
        "accounting.votes_valid": "Senate, Curepto department (6.ª agrupación): the table's list-total row prints "
        "Lista N° 4 as 227 while its candidate row and the Talca province total give 277, so the lists add to 1,883 "
        "against the printed 1,933 valid votes; kept as printed.",
    },
    date(1961, 3, 5): {
        "accounting.declared": "Senate, 7.ª circunscripción, Lista G: the commune of Rafael (Tomé) prints 95, so "
        "the communes add to 27,641, while Rafael's own row total (648) and the Tomé department total (2,095) imply "
        "93 and the grouping total prints 27,639; kept as printed.",
    },
    date(1965, 3, 7): {
        "accounting.declared": "Senate: the declared figures are the Tribunal Calificador's grouping totals, and the "
        "Registro's tables by commune differ from them by their own printed figures. 2.ª agrupación: the tables print "
        "the Socialist list as 15,709 and Alejandro Chelén Rojas as 7,721 against the Tribunal's 15,809 and 7,821, and "
        "the Combarbalá communes add to one null more and one blank fewer than every printed total above them. 4.ª: "
        "the four district totals printed with the communes add to 234,465 votes for José Musalem Saffie against "
        "234,460. 6.ª: the commune of Hualañé prints the Christian Democrats' list as 495 and 1,711 votes cast where "
        "its candidates and the Mataquito department total give 695 and 1,911, so the communes fall 200 short. 8.ª: "
        "the three province totals printed with the communes add to 359 votes for Gonzalo Quinteros Tricot (his list "
        "7,702) and 165,500 cast against the Tribunal's 357, 7,700 and 165,496. All kept as printed.",
    },
    date(1969, 3, 2): {
        "accounting.declared": "Senate, 10.ª agrupación: the commune of Lago Verde prints 2 blank votes, so its "
        "figures add to 77 against its printed total of 75, and the Aysén department (85), province (237) and "
        "grouping (1,259) blank totals all imply 0; kept as printed, so the communes add to 1,261 blank votes.",
    },
    date(1973, 3, 4): {
        "accounting.declared": "Senate, 2.ª agrupación: the commune of Huasco prints 2,182 votes cast while its own "
        "figures add to 2,162, and the Freirina department (4,220), Atacama province and grouping totals (190,529) "
        "all imply 2,162; kept as printed, so the communes add to 190,549.",
    },
}

LICENSE = (
    "No license stated: official returns of the Dirección del Registro Electoral, published by the Servicio Electoral "
    "de Chile (Servel) in its historical archive; facts published with attribution"
)
CHAMBERS = {
    "deputies": ("lower_house", "Cámara de Diputados", "Chamber of Deputies", "departamental"),
    "senators": ("upper_house", "Senado", "Senate", "provincial"),
}

METHOD = (
    "Read by eye from the scanned returns of the Dirección del Registro Electoral that Servel's historical archive "
    "publishes, one volume per chamber and election year. Each value is the grouping's total as the volume prints "
    "it (the agrupación departamental for deputies, the agrupación provincial for senators), and each carries the "
    "page and a box on the scan. A list's votes are its total; where the volume prints them, each candidate's "
    "preference votes and the list's votes without a preference are stored too. Seats and the members elected are "
    "as the volume declares them."
)


def url(volume: str) -> str:
    return SHARE + VOLUMES[volume]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
BODIES = {chamber: key("body", chamber) for chamber in CHAMBERS}


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", plain).upper().split())


# ---- raw files ---------------------------------------------------------------------------------------------------


def fetch() -> None:
    """Add the volumes to the raw cache. Servel's file host refuses scripted downloads, so the archive's own machines
    saved them; this reads them from the archive's inbox (`EA_INBOX_URL`, `EA_INBOX_USER`, `EA_INBOX_PASSWORD`)."""
    have = raw.keys(SOURCE)
    if all(url(volume) in have for volume in VOLUMES):
        return
    base = os.environ["EA_INBOX_URL"] + "/fetched/4569/"
    auth = (os.environ["EA_INBOX_USER"], os.environ["EA_INBOX_PASSWORD"])
    index = httpx.get(base + "index.json", auth=auth, timeout=60).raise_for_status().json()
    for volume in VOLUMES:
        entry = index[url(volume)]
        if url(volume) in have:
            continue
        content = httpx.get(base + entry["file"], auth=auth, timeout=600).raise_for_status().content
        stored = raw.store(
            SOURCE,
            entry.get("source", url(volume)),
            content,
            suffix=".pdf",
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
        if stored.sha256 != entry["sha256"]:
            raise ValueError(f"{volume}: inbox file does not match its hash")


@cache
def sizes(volume: str) -> dict[int, tuple[int, int]]:
    """Each page's size in pixels at the resolution the boxes were read at."""
    out = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "9999", str(raw.cached(SOURCE, url(volume)).path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = {}
    for number, width, height in re.findall(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+) pts", out):
        found[int(number)] = (round(float(width) * DPI / 72), round(float(height) * DPI / 72))
    return found


# ---- transcriptions ----------------------------------------------------------------------------------------------


class Candidate(NamedTuple):
    name: str
    party: str | None
    votes: int | None
    elected: bool
    page: int | None
    box: tuple[float, float, float, float] | None


class Slate(NamedTuple):
    label: str
    party: str | None
    votes: int | None
    unassigned: int | None
    seats: int | None
    page: int
    box: tuple[float, float, float, float] | None
    candidates: tuple[Candidate, ...]


class Grouping(NamedTuple):
    number: int
    title: str
    areas: tuple[str, ...]
    seats: int | None
    accounting: dict[int, int]
    page: int
    box: tuple[float, float, float, float] | None
    slates: tuple[Slate, ...]
    notes: str


class Volume(NamedTuple):
    name: str
    chamber: str
    year: int
    day: date
    groupings: tuple[Grouping, ...]
    notes: str


def clean_name(text: str) -> str:
    """A name as printed. Letters the scan clips but the line shows were read in brackets (`[M]anuel`) and keep their
    letters; letters no one could read (`[...]`, `[illegible]`) become an ellipsis; a reader's `[?]` is dropped."""
    text = re.sub(r"\s*\[\?\]", "", text)
    text = re.sub(r"\[(?:\.\.\.|…|il+egible)\]", "…", text, flags=re.IGNORECASE)
    text = re.sub(r"\[([^\]]*)\]", r"\1", text)
    return " ".join(text.split())


def _box(value: Any) -> tuple[float, float, float, float] | None:
    return None if value is None else (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _accounting(entry: dict[str, Any]) -> dict[int, int]:
    found = {}
    for field, measure in (("registered", REGISTERED), ("cast", CAST), ("valid", VALID), ("blank", BLANK)):
        if entry.get(field) is not None:
            found[measure] = int(entry[field])
    if entry.get("invalid") is not None:
        found[INVALID] = int(entry["invalid"])
    elif entry.get("null_blank") is not None and BLANK not in found:
        found[INVALID] = int(entry["null_blank"])
    return found


def volumes() -> list[Volume]:
    out = []
    for name in VOLUMES:
        path = RETURNS / f"{name}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        groupings = []
        for c in data["contests"]:
            slates = []
            for s in c["lists"]:
                candidates = tuple(
                    Candidate(
                        clean_name(x["name"]),
                        x.get("party"),
                        x.get("votes"),
                        bool(x.get("elected")),
                        x.get("page"),
                        _box(x.get("box")),
                    )
                    for x in s.get("candidates", [])
                )
                slates.append(
                    Slate(
                        " ".join(str(s["label"]).split()),
                        s.get("party"),
                        s.get("votes"),
                        s.get("unassigned"),
                        s.get("seats"),
                        int(s.get("page") or c["page"]),
                        _box(s.get("box")),
                        candidates,
                    )
                )
            groupings.append(
                Grouping(
                    int(c["number"]),
                    " ".join(str(c["title"]).split()),
                    tuple(" ".join(a.split()) for a in c["areas"]),
                    c.get("seats"),
                    _accounting(c),
                    int(c["page"]),
                    _box(c.get("box")),
                    tuple(slates),
                    c.get("notes") or "",
                )
            )
        out.append(
            Volume(
                name,
                data["chamber"],
                int(data["year"]),
                date.fromisoformat(data["day"]) if data.get("day") else DAYS[int(data["year"])],
                tuple(groupings),
                data.get("notes") or "",
            )
        )
    return out


def days() -> dict[date, list[Volume]]:
    found: dict[date, list[Volume]] = {}
    for volume in volumes():
        found.setdefault(volume.day, []).append(volume)
    return dict(sorted(found.items()))


# ---- transcriptions by commune -----------------------------------------------------------------------------------


class Fine(NamedTuple):
    """One unit a volume's table by commune prints, with the figures on its row (or column)."""

    id: str
    code: str
    label: str
    level: str
    names: tuple[str | None, ...]
    values: dict[str, int]
    pages: dict[str, int]
    boxes: dict[str, tuple[float, float, float, float]]


# The figures of a table by commune, by label, as the measure and the part of the option they fill.
ACCOUNT_LABELS = {
    "nulos": INVALID,
    "nulos y blancos": INVALID,
    "blancos": BLANK,
    "objetados": OBJECTED,
    "validos": VALID,
    "total": CAST,
}
FINE_LEVELS = ("unit", "subtotal", "commune", "district", "department", "province", "grouping", "national")


@cache
def fine(volume: str) -> dict[int, list[Fine]]:
    """Each grouping's printed rows by commune, every level, in the volume's order. A row printed across two facing
    pages, its lists on one and the rest on the other, is read as two halves and joined here."""
    path = COMMUNES / f"{volume}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    layouts = {page["page"]: page for page in data["pages"]}
    found: dict[int, list[Fine]] = {}
    halves: dict[tuple[object, ...], Fine] = {}
    codes: Counter[str] = Counter()
    for row in data["units"]:
        if row["level"] not in FINE_LEVELS:
            raise ValueError(f"{volume}: unknown level {row['level']!r}")
        if row["level"] == "national" or row.get("grouping") is None:
            continue
        number = int(row["grouping"])
        page = int(row["page"])
        layout = layouts[page]
        lo, hi = (float(v) for v in row["span"])
        boxes = {}
        for label in row["values"]:
            a, b = (float(v) for v in layout["axis"][label])
            boxes[label] = (a, lo, b, hi) if layout["layout"] == "rows" else (lo, a, hi, b)
        names = (row.get("province"), row.get("department"), row.get("district"), row.get("commune"), row["name"])
        ident = (number, row["level"], *(fold(n) if n else "" for n in names))
        half = halves.get(ident)
        if (
            half is not None
            and abs(page - next(iter(half.pages.values()))) == 1
            and not set(half.values) & set(row["values"])
        ):
            half.values.update(row["values"])
            half.pages.update(dict.fromkeys(row["values"], page))
            half.boxes.update(boxes)
            del halves[ident]
            continue
        code = ":".join([volume[:4], str(number), *(fold(n).lower().replace(" ", "_") for n in names if n)])
        if row["level"] == "unit":
            codes[code] += 1
            if codes[code] > 1:
                code = f"{code}:{codes[code]}"
        label = " ".join(row["name"].split())
        if row.get("commune") and fold(row["commune"]) != fold(row["name"]):
            label = f"{label} ({' '.join(row['commune'].split())})"
        unit = Fine(
            key("unit", "fine", code),
            code,
            label,
            row["level"],
            names,
            dict(row["values"]),
            dict.fromkeys(row["values"], page),
            boxes,
        )
        halves[ident] = unit
        found.setdefault(number, []).append(unit)
    return found


def leaves(volume: Volume, grouping: Grouping) -> list[Fine]:
    return [unit for unit in fine(volume.name).get(grouping.number, []) if unit.level == "unit"]


# ---- areas -------------------------------------------------------------------------------------------------------


def scheme(chamber: str) -> str:
    return f"cl.registro_agrupacion_{CHAMBERS[chamber][3]}"


UNIT_SCHEME = "cl.registro_agrupacion_votes"
FINE_SCHEME = "cl.registro_comuna_votes"


def composition(chamber: str, grouping: Grouping) -> str:
    return f"{grouping.number}:" + "|".join(sorted(fold(area) for area in grouping.areas))


@cache
def areas() -> dict[tuple[str, str], tuple[str, Grouping, int]]:
    """Each distinct grouping (its number and the departments or provinces it joins) and the first year it voted."""
    found: dict[tuple[str, str], tuple[str, Grouping, int]] = {}
    for volume in sorted(volumes(), key=lambda v: v.year):
        for grouping in volume.groupings:
            ident = (volume.chamber, composition(volume.chamber, grouping))
            if ident not in found:
                found[ident] = (f"{grouping.number}/{volume.year}", grouping, volume.year)
    return found


def area_id(chamber: str, grouping: Grouping) -> str:
    return key("area", chamber, composition(chamber, grouping))


def unit_id(chamber: str, grouping: Grouping) -> str:
    return key("unit", chamber, composition(chamber, grouping))


def joined(names: tuple[str, ...]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def area_name(chamber: str, grouping: Grouping) -> str:
    """Named by the departments or provinces it joins: the volumes number Santiago's districts inconsistently."""
    return f"Agrupación {CHAMBERS[chamber][3]} de {joined(grouping.areas)}"


def geo() -> Package:
    p = Package(f"{SOURCE}/geo", SOURCE, "Chile's electoral groupings for the Chamber and the Senate, 1925-1973")
    p.external.add(CHILE)
    for chamber, (_, _, _, word) in CHAMBERS.items():
        p.add(
            "scheme",
            code=scheme(chamber),
            polity_id=CHILE,
            kind="electoral_district",
            rank=1,
            noun=f"agrupación {word} (1925–1973)",
            code_system=None,
        )
    for (chamber, _), (code, grouping, _) in areas().items():
        id = area_id(chamber, grouping)
        p.add("area", id=id, scheme_code=scheme(chamber))
        p.add("area_code", area_id=id, code=code)
        p.name(id, area_name(chamber, grouping), "es", "official")
    return p


# ---- registry ----------------------------------------------------------------------------------------------------


def party_id(canonical: str) -> str:
    return key("party", canonical)


INDEPENDENT = re.compile(r"^(INDEPENDIENTES?|IND|SIN PARTIDO)$")
# Spellings and abbreviations of one party's name, as the volumes print them, to the name they stand for.
ALIASES = {
    "FALANGA NACIONAL": "FALANGE NACIONAL",
    "FALANGE NAC": "FALANGE NACIONAL",
    "FALANGE": "FALANGE NACIONAL",
    "MOV NAC SOCIALISTA": "MOVIMIENTO NACIONAL SOCIALISTA",
    "MOV NACI SOCIALISTA": "MOVIMIENTO NACIONAL SOCIALISTA",
    "DEMOCRATA SOC": "DEMOCRATA SOCIALISTA",
    "SOC CHILENO": "SOCIALISTA CHILENO",
    "RADICALES": "RADICAL",
    "DEMOCRACIA CRISTIANA": "DEMOCRATA CRISTIANO",
    "CODE": "CONFEDERACION DE LA DEMOCRACIA",
    "UP": "UNIDAD POPULAR",
    "DE LA UNIDAD POPULAR": "UNIDAD POPULAR",
    "USOPO": "UNION SOCIALISTA POPULAR",
}
# The federations of 1973, under which several parties presented one list.
FEDERATIONS = {"CONFEDERACION DE LA DEMOCRACIA", "UNIDAD POPULAR"}


def canonical(name: str | None) -> str | None:
    """The key of the one party a printed label names, or None for an independent or for a pact of several parties
    (`Conservador y Liberal`, `Partidos Radical, Democrático y Progresista Nacional`), which is a list, not a party."""
    if name is None:
        return None
    folded = fold(name)
    if not folded or INDEPENDENT.match(folded) or "," in name or " Y " in f" {folded} ":
        return None
    folded = re.sub(r"^PARTIDOS? ", "", folded)
    return ALIASES.get(folded, folded)


def party_of(slate: Slate) -> str | None:
    return canonical(slate.party)


@cache
def parties() -> dict[str, str]:
    """Each party the lists name, with the first label printed for it."""
    found: dict[str, str] = {}
    for volume in volumes():
        for grouping in volume.groupings:
            for slate in grouping.slates:
                folded = party_of(slate)
                if folded is not None and slate.party is not None:
                    found.setdefault(folded, " ".join(slate.party.split()).rstrip("."))
    return dict(sorted(found.items()))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Dirección del Registro Electoral's returns as a source, with the grouping units, bodies, and parties they name",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(CHILE)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Servicio Electoral de Chile (Servel)",
        title="Dirección del Registro Electoral, resultados electorales 1925-1973",
        url=PAGE,
        license=LICENSE,
        license_url=None,
        attribution="Servicio Electoral de Chile (Servel), Archivo Histórico",
    )
    p.add("scheme", code=UNIT_SCHEME, polity_id=CHILE, kind="precinct", rank=2, noun="grouping count", code_system=None)
    for (chamber, _), (code, grouping, _) in areas().items():
        p.add(
            "reporting_unit",
            id=unit_id(chamber, grouping),
            polity_id=CHILE,
            scheme_code=UNIT_SCHEME,
            code=f"{chamber}:{code}",
            area_relation="equals",
            area_id=area_id(chamber, grouping),
            kind="standard",
        )
    if any(fine(volume.name) for volume in volumes()):
        p.add(
            "scheme", code=FINE_SCHEME, polity_id=CHILE, kind="precinct", rank=3, noun="commune count", code_system=None
        )
    for volume in volumes():
        for grouping in volume.groupings:
            for unit in leaves(volume, grouping):
                p.add(
                    "reporting_unit",
                    id=unit.id,
                    polity_id=CHILE,
                    scheme_code=FINE_SCHEME,
                    code=unit.code,
                    parent_id=unit_id(volume.chamber, grouping),
                    area_relation="within",
                    area_id=area_id(volume.chamber, grouping),
                    kind="standard",
                )
                p.name(unit.id, unit.label, "es", "official")
    for chamber, (kind, es, en, _) in CHAMBERS.items():
        id = BODIES[chamber]
        p.add("body", id=id, polity_id=CHILE, kind=kind)
        p.name(id, es, "es", "official")
        p.name(id, en, "en", "official", False)
    for folded, name in parties().items():
        id = party_id(folded)
        p.add("party", id=id, kind="federation" if folded in FEDERATIONS else "party", polity_id=CHILE)
        p.add("external_id", entity_id=id, scheme="cl.registro.party", value=folded)
        p.name(id, name, "es", "official")
    return p


# ---- elections ---------------------------------------------------------------------------------------------------


def build(day: date) -> Package:
    year = day.year
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Chile's {year} Chamber and Senate elections by electoral grouping, from the Dirección del Registro "
        "Electoral's returns",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(CHILE)
    reasons = dict(ACCEPTED.get(day, {}))
    for code, reason in FINE_ACCEPTED.get(day, {}).items():
        reasons[code] = f"{reasons[code]} Also: {reason}" if code in reasons else reason
    for code, reason in reasons.items():
        p.accept(code, reason)
    election = p.id("election", day)
    p.add(
        "election",
        id=election,
        polity_id=CHILE,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{year} Chilean parliamentary election", "en", "official")
    p.name(election, f"Elección ordinaria de Congreso Nacional {year}", "es", "native", False)
    base = f"cl/{day}-parliamentary-by-grouping"
    p.add("slug", path=base, entity_id=election, is_current=True)
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    pages: set[tuple[str, int]] = set()
    for volume in days()[day]:
        artifact = raw.cached(SOURCE, url(volume.name))
        artifact_id = p.artifact(artifact, SOURCE_ID)
        for grouping in volume.groupings:
            parent, path = election, base
            if extraordinary(grouping):
                parent, path = by_election(p, volume, grouping, election, base)
            _grouping(p, volume, grouping, parent, path, values, locators, pages)
        for name, number in sorted(pages):
            if name != volume.name:
                continue
            width, height = sizes(name)[number]
            p.add(
                "source_page",
                id=key("page", name, number),
                artifact_id=artifact_id,
                page_number=number,
                image_key=f"pages/{SOURCE}/{artifact.sha256}/{number}.png",
                width=width,
                height=height,
            )
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(DIGITIZED, pl.Int16)),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "page_id": pl.String,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), source_id=pl.lit(SOURCE_ID)),
    )
    return p


def extraordinary(grouping: Grouping) -> bool:
    """A grouping that filled a vacancy on the same day, rather than its ordinary renewal."""
    return "EXTRAORDINARIA" in fold(grouping.title)


def by_election(p: Package, volume: Volume, grouping: Grouping, general: str, base: str) -> tuple[str, str]:
    day = volume.day
    id = p.id("election", day, "extraordinary", volume.chamber, grouping.number)
    p.add(
        "election",
        id=id,
        polity_id=CHILE,
        parent_id=general,
        kind="by_election",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    where = joined(grouping.areas)
    chamber = "Senate" if volume.chamber == "senators" else "Chamber"
    p.name(id, f"{day.year} Chilean {chamber} by-election in {where}", "en", "official")
    word = "senadores" if volume.chamber == "senators" else "diputados"
    p.name(id, f"Elección extraordinaria de {word} {day.year}, {where}", "es", "native", False)
    path = f"{base}/extraordinaria-{word}-{grouping.number}"
    p.add("slug", path=path, entity_id=id, is_current=True)
    return id, path


def tacit(grouping: Grouping) -> bool:
    """A grouping whose members were proclaimed without a vote, as every grouping was in 1930 under Ley 4763, when
    the recognized parties presented exactly as many candidates as seats."""
    return not grouping.accounting and all(s.votes is None for s in grouping.slates)


def _grouping(
    p: Package,
    volume: Volume,
    grouping: Grouping,
    election: str,
    base: str,
    values: list[dict[str, object]],
    locators: list[dict[str, object]],
    pages: set[tuple[str, int]],
) -> None:
    chamber = volume.chamber
    ident = (chamber, grouping.number)
    contest = p.id("contest", volume.day, *ident)
    result_set = p.id("result_set", volume.day, *ident)
    unit = unit_id(chamber, grouping)
    seats = grouping.seats
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=BODIES[chamber],
        district_area_id=area_id(chamber, grouping),
        magnitude=seats,
        round_number=1,
        electoral_system_id=None,
        default_result_kind="tacit" if tacit(grouping) else "counted",
    )
    title = f"{CHAMBERS[chamber][1]}, {area_name(chamber, grouping)}"
    p.name(contest, title, "es", "official")
    word = "diputados" if chamber == "deputies" else "senadores"
    p.add("slug", path=f"{base}/{word}-{grouping.number}", entity_id=contest, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="certified",
        kind="tacit" if tacit(grouping) else "counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=not leaves(volume, grouping),
        completeness=None if tacit(grouping) else 1.0,
    )
    note = METHOD
    if grouping.notes:
        note += f"\n\nOn this grouping's pages: {grouping.notes}"
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")

    def put(option: str | None, measure: int, value: int, page: int | None, box: tuple[float, ...] | None) -> None:
        values.append(
            {"result_set_id": result_set, "unit_id": unit, "option_id": option, "measure": measure, "value": value}
        )
        if page is not None and box is not None:
            width, height = sizes(volume.name)[page]
            pages.add((volume.name, page))
            locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "measure": measure,
                    "page_id": key("page", volume.name, page),
                    "x0": round(box[0] * width, 1),
                    "y0": round(box[1] * height, 1),
                    "x1": round(box[2] * width, 1),
                    "y1": round(box[3] * height, 1),
                }
            )

    for measure, value in grouping.accounting.items():
        put(None, measure, value, grouping.page, grouping.box)
    options: dict[str, str] = {}
    choices: dict[tuple[str, str], str] = {}
    outcomes: list[dict[str, object]] = []
    seen: Counter[str] = Counter()
    for position, slate in enumerate(grouping.slates, start=1):
        tag = fold(slate.label) or str(position)
        seen[tag] += 1
        if seen[tag] > 1:
            tag = f"{tag} {seen[tag]}"
        option = p.id("option", volume.day, *ident, tag)
        options[slate.label] = option
        name = party_of(slate)
        member = party_id(name) if name is not None else None
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=member,
            ballot_position=position,
            ballot_label=slate.label
            if slate.party is None or fold(slate.party) == fold(slate.label)
            else f"{slate.label} ({slate.party})",
        )
        if slate.votes is not None:
            put(option, VOTES, slate.votes, slate.page, slate.box)
        if slate.unassigned is not None:
            put(option, LEGEND, slate.unassigned, slate.page, slate.box)
        won = slate.seats if slate.seats is not None else sum(c.elected for c in slate.candidates)
        if won:
            outcomes.append({"option_id": option, "kind": "won", "seats": won})
        names: Counter[str] = Counter()
        for order, candidate in enumerate(slate.candidates, start=1):
            folded = fold(candidate.name)
            names[folded] += 1
            who = folded if names[folded] == 1 else f"{folded} {names[folded]}"
            person = p.id("person", volume.day, *ident, tag, who)
            p.add("person", id=person)
            p.name(person, candidate.name, "es", "official")
            own = canonical(candidate.party) if candidate.party is not None else name
            candidacy = p.id("candidacy", volume.day, *ident, tag, who)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=order,
                ballot_name=candidate.name,
                member_of_party_id=party_id(own) if own in parties() else None,
                is_independent=candidate.party is not None and INDEPENDENT.match(fold(candidate.party)) is not None,
            )
            if candidate.votes is not None:
                choice = p.id("option", volume.day, *ident, tag, who)
                p.add(
                    "contest_option",
                    id=choice,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=option,
                    party_id=member,
                    ballot_position=order,
                    ballot_label=candidate.name,
                )
                choices[(slate.label, candidate.name)] = choice
                put(choice, PREFERENCE, candidate.votes, candidate.page or slate.page, candidate.box)
            if candidate.elected:
                outcomes.append({"option_id": option, "candidacy_id": candidacy, "kind": "elected"})
    finer = p.id("result_set", volume.day, *ident, "commune") if leaves(volume, grouping) else None
    holders = (result_set,) if volume.name in UNRECTIFIED else (result_set, finer)
    for outcome in outcomes:
        for target in holders:
            if target is not None:
                p.add("outcome", result_set_id=target, **outcome)
    if finer is not None:
        _fine(p, volume, grouping, election, contest, result_set, finer, options, choices, values, locators, pages)


FINE_METHOD = (
    "Read by eye from the same volume's table of the grouping's votes by commune, one value per printed unit (the "
    "commune, or the civil circumscription or department where that is what the volume prints), each with its page "
    "and a box on the scan. Where the table prints men's, women's and total columns, the total is stored. The "
    "grouping's own totals, as the volume prints them, are the declared figures the units are checked against; the "
    "seats and members elected are those of the grouping's count."
)


def label_measure(label: str) -> tuple[str | None, str | None, int]:
    """The list, the candidate, and the measure a printed label of a table by commune stands for."""
    if label in ACCOUNT_LABELS:
        return None, None, ACCOUNT_LABELS[label]
    if " / " not in label:
        return label, None, VOTES
    head, name = label.split(" / ", 1)
    return (head, None, LEGEND) if name == "(lista)" else (head, name, PREFERENCE)


def disagreements(volume: Volume, grouping: Grouping) -> list[str]:
    """Where the units' sum differs from the grouping's figure the volume prints."""
    declared = dict(declared_figures(grouping))
    summed: Counter[tuple[str | None, str | None, int]] = Counter()
    for unit in leaves(volume, grouping):
        for label, value in unit.values.items():
            summed[label_measure(label)] += value
    out = []
    for (head, name, measure), value in sorted(summed.items(), key=str):
        if measure == INVALID and (None, None, BLANK) in summed and (None, None, BLANK) not in declared:
            continue
        want = declared.get((head, name, measure))
        if want is not None and want != value:
            what = (
                name
                or head
                or {INVALID: "null", BLANK: "blank", OBJECTED: "objected", VALID: "valid", CAST: "cast"}[measure]
            )
            if measure == LEGEND:
                what += " (votes without a preference)"
            out.append(f"{what}: units add to {value:,}, grouping total {want:,}")
    return out


def declared_figures(grouping: Grouping) -> list[tuple[tuple[str | None, str | None, int], int]]:
    found: list[tuple[tuple[str | None, str | None, int], int]] = [
        ((None, None, measure), value) for measure, value in grouping.accounting.items()
    ]
    for slate in grouping.slates:
        if slate.votes is not None:
            found.append(((slate.label, None, VOTES), slate.votes))
        if slate.unassigned is not None:
            found.append(((slate.label, None, LEGEND), slate.unassigned))
        for candidate in slate.candidates:
            if candidate.votes is not None:
                found.append(((slate.label, candidate.name, PREFERENCE), candidate.votes))
    return found


def _fine(
    p: Package,
    volume: Volume,
    grouping: Grouping,
    election: str,
    contest: str,
    coarse: str,
    result_set: str,
    options: dict[str, str],
    choices: dict[tuple[str, str], str],
    values: list[dict[str, object]],
    locators: list[dict[str, object]],
    pages: set[tuple[str, int]],
) -> None:
    units = leaves(volume, grouping)
    before = volume.name in UNRECTIFIED
    if before:
        valid = sum(unit.values.get("validos", 0) for unit in units)
        whole = grouping.accounting.get(VALID)
        completeness = round(valid / whole, 4) if whole else None
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official" if before else "certified",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=not before,
        supersedes_id=None if before else coarse,
        completeness=completeness if before else 1.0,
    )
    note = UNRECTIFIED[volume.name] if before else FINE_METHOD
    if FINE_NOTES.get((volume.name, grouping.number)):
        note += f"\n\nOn this grouping's pages: {FINE_NOTES[(volume.name, grouping.number)]}"
    off = disagreements(volume, grouping)
    if off:
        note += "\n\nWhere the units do not add to the grouping's printed totals: " + "; ".join(off) + "."
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
    present = {label_measure(label) for unit in units for label in unit.values}
    split = (None, None, BLANK) in present and BLANK not in grouping.accounting
    for (head, name, measure), value in [] if before else declared_figures(grouping):
        if (head, name, measure) not in present or (split and measure == INVALID):
            continue
        option = None if head is None else options[head] if name is None else choices[(head, name)]
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    width_height = sizes(volume.name)
    for unit in units:
        for label, value in unit.values.items():
            head, name, measure = label_measure(label)
            if head is not None and head not in options:
                raise ValueError(f"{volume.name} {grouping.number} {unit.label}: no list {head!r}")
            if name is not None and (head, name) not in choices:
                raise ValueError(f"{volume.name} {grouping.number} {unit.label}: no candidate {name!r} on {head!r}")
            option = None if head is None else options[head] if name is None else choices[(head, name)]
            values.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit.id,
                    "option_id": option,
                    "measure": measure,
                    "value": value,
                }
            )
            page = unit.pages[label]
            width, height = width_height[page]
            box = unit.boxes[label]
            pages.add((volume.name, page))
            locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit.id,
                    "option_id": option,
                    "measure": measure,
                    "page_id": key("page", volume.name, page),
                    "x0": round(box[0] * width, 1),
                    "y0": round(box[1] * height, 1),
                    "x1": round(box[2] * width, 1),
                    "y1": round(box[3] * height, 1),
                }
            )


# ---- checks and entry points -------------------------------------------------------------------------------------


def reconcile() -> list[str]:
    """Compare each grouping's figures the way the volume adds them up, and each volume's groupings with the national
    totals it prints."""
    report = []
    for volume in volumes():
        off = []
        for grouping in volume.groupings:
            listed = sum(s.votes or 0 for s in grouping.slates)
            valid = grouping.accounting.get(VALID)
            if valid is not None and listed != valid:
                off.append(f"{grouping.number}: lists {listed:,} against valid {valid:,}")
            for slate in grouping.slates:
                prefs = [c.votes for c in slate.candidates if c.votes is not None]
                if prefs and slate.votes is not None and sum(prefs) + (slate.unassigned or 0) != slate.votes:
                    off.append(f"{grouping.number} {slate.label}: preferences do not add up to the list")
        report.append(
            f"{volume.name}: {len(volume.groupings)} groupings, {sum(len(g.slates) for g in volume.groupings)} lists; "
            + ("all sums close" if not off else "; ".join(off))
        )
        for grouping in volume.groupings:
            units = leaves(volume, grouping)
            if units:
                fine_off = disagreements(volume, grouping)
                report.append(
                    f"{volume.name} {grouping.number}: {len(units)} units by commune; "
                    + ("all add to the grouping totals" if not fine_off else "; ".join(fine_off))
                )
    return report


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def parse() -> None:
    print("\n".join(reconcile()), flush=True)
    geo().write()
    registry().write()
    for day in days():
        build(day).write()


def emit() -> None:
    package.run("load", packages())
