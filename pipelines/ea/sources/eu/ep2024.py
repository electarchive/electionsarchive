import csv
import io
import json
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw, scans
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "eu.ep2024"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
EP = "https://results.elections.europa.eu/data-sheets/csv/2024-2029/election-results"
FILES = {
    "FR": "https://static.data.gouv.fr/resources/resultats-des-elections-europeennes-du-9-juin-2024/20240613-154902/resultats-definitifs-par-circonscription.csv",
    "PL": "https://danewyborcze.kbw.gov.pl/dane/2024/parlament_eu/wyniki_gl_na_listy_po_okregach_csv.zip",
    "NL": "https://data.overheid.nl/sites/default/files/dataset/050597d1-bee3-46e8-bca0-a4a8d0589a2e/resources/EP2024.csv",
    "CZ": "https://www.volby.cz/pls/ep2024/vysledky",
    "SK": "https://volby.statistics.sk/ep/ep2024/files/EP2024_SK_csv.zip",
    "HR": "https://www.izbori.hr/site/UserDocsImages/2024/rezultati_eup.zip",
    "LV": "https://data.gov.lv/dati/dataset/da829174-dea8-4c5e-8b17-91dc84df14ba/resource/03809e72-bfc4-407b-a1aa-913e367564cb/download/allresults-20240614105815_fixed_all_departments.json",
    "AT": "https://www.bmi.gv.at/412/europawahlen/europawahl_2024/files/endgueltiges_ergebnis_europawahl_26062024.xlsx",
    "ES": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2024-13092",
    "BE": "https://elections.fgov.be/sites/2024.elections.fgov.be/files/inline-files/EU_CEListes_2024.xlsx",
    "LU": "https://download.data.public.lu/resources/elections-legislatives-2024-donnees-officielles/20240624-100852/20240617-elec-ue-2024-tableau-resultats-officiels.xlsx",
    "DK": "https://www.dst.dk/valg/Valg2546524/valgopg/valgopgHL.htm",
    "SE": "https://www.val.se/download/18.162047b519a91d053311500c/1718379028498/val-till-europaparlamentet-2024-beslut-och-protokollsbilagor.pdf",
    "PT": "https://www.cne.pt/sites/default/files/dl/eleicoes/2024_pe/docs_geral/2024_pe_mapa_oficial_resultados_dr.pdf",
    "GR": "https://ekloges.ypes.gr/current/dyn1/e/epik_1.js",
    "BG": "https://web.archive.org/web/20240724100318id_/https://results.cik.bg/europe2024/rezultati/",
    "HU": "https://vtr.valasztas.hu/ep2024/data/06201531/szavossz/ListasJkv.json",
    "FI": "https://tulospalvelu.vaalit.fi/EPV-2024/epv-2024_puo_maa.xml.zip",
    "SI": "https://web.archive.org/web/20250407151347id_/https://volitve.dvk-rs.si/ep2024/data/rezultati.json",
    "CY": "https://results.elections.moi.gov.cy/api/greek/Results?electionId=163&districtId=&areaId=&pollingstationId=&ballotboxId=",
    "LT": "https://www.vrk.lt/lt/2024-m-birzelio-9-d-rinkimai-i-europos-parlamenta/rinkimu-rezultatai#DG_1546_1_2146.csv",
    "MT": "https://electoral.gov.mt/ElectionResults/MEP",
    "EE": "https://ep2024.valimised.ee/et/election-result/index.html",
    "RO": "https://europarlamentare2024.bec.ro/wp-content/uploads/2024/06/PVCVAM_1.pdf",
}
SUFFIXES = {
    "FR": ".csv",
    "PL": ".zip",
    "NL": ".csv",
    "CZ": ".xml",
    "SK": ".zip",
    "HR": ".zip",
    "LV": ".json",
    "AT": ".xlsx",
    "LU": ".xlsx",
    "ES": ".html",
    "DK": ".html",
    "SE": ".pdf",
    "PT": ".pdf",
    "GR": ".json",
    "BG": ".html",
    "HU": ".json",
    "FI": ".zip",
    "SI": ".json",
    "CY": ".json",
    "LT": ".csv",
    "MT": ".html",
    "EE": ".html",
    "RO": ".pdf",
}
CHECKS = [
    "parties",
    "groups",
    *(
        f"seats-breakdown-parties/{code}"
        for code in (
            "at",
            "be",
            "fbe",
            "gbe",
            "wbe",
            "bg",
            "cy",
            "cz",
            "dk",
            "ee",
            "el",
            "es",
            "fi",
            "fr",
            "hr",
            "hu",
            "it",
            "lt",
            "lu",
            "lv",
            "mt",
            "nl",
            "pl",
            "pt",
            "ro",
            "se",
            "si",
            "sk",
        )
    ),
]


def fetch() -> None:
    for iso, url in FILES.items():
        raw.fetch(SOURCE, url, suffix=SUFFIXES[iso])
    for name in CHECKS:
        raw.fetch(SOURCE, f"{EP}/{name}.csv", suffix=".csv")
    for url, _ in DECLARED.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix or ".html")
    for url in ELECTED.values():
        raw.fetch(SOURCE, url, suffix=".json")


DECLARED = {
    "PL": (
        "https://pe2024.pkw.gov.pl/pe2024/statics/PKW_AKTUALNOSCI/uploaded_files/1718049863_obwieszczenie.pdf",
        {
            "KOALICYJNY KOMITET WYBORCZY KOALICJA OBYWATELSKA": 21,
            "KOMITET WYBORCZY PRAWO I SPRAWIEDLIWOŚĆ": 20,
            "KOMITET WYBORCZY KONFEDERACJA WOLNOŚĆ I NIEPODLEGŁOŚĆ": 6,
            "KOALICYJNY KOMITET WYBORCZY TRZECIA DROGA POLSKA 2050 SZYMONA HOŁOWNI - POLSKIE STRONNICTWO LUDOWE": 3,
            "KOALICYJNY KOMITET WYBORCZY LEWICA": 3,
        },
    ),
    "HR": (
        "https://www.izbori.hr/arhiva-izbora/data/euparlament/2024/izabrani/i_14_000_0000.pdf",
        {
            "HRVATSKA DEMOKRATSKA ZAJEDNICA - HDZ": 6,
            "DOMOVINSKI POKRET - DP": 1,
            "MOŽEMO! - POLITIČKA PLATFORMA": 1,
            "SOCIJALDEMOKRATSKA PARTIJA HRVATSKE - SDP": 4,
        },
    ),
    "AT": (
        "https://www.bmi.gv.at/412/europawahlen/europawahl_2024/start.html",
        {"FPÖ": 6, "ÖVP": 5, "SPÖ": 5, "GRÜNE": 2, "NEOS": 2},
    ),
}
PAGES = {
    "IT": "https://elezionistorico.interno.gov.it/index.php?tpel=E&dtel=09/06/2024&tpa=Y&tpe=A&lev0=0&levsut0=0&es0=S&ms=S",
    "GR": "https://ekloges.ypes.gr/current/e/home/dist/model.el.js",
    "HU": "https://vtr.valasztas.hu/ep2024/data/06091753/ver/euListakEsJeloltek.json",
    "FI": "https://tulospalvelu.vaalit.fi/EPV-2024/epv-2024_alu_maa.xml.zip",
}
ELECTED = {
    "LV": "https://data.gov.lv/dati/dataset/da829174-dea8-4c5e-8b17-91dc84df14ba/resource/daf2d2c6-7671-40fb-8147-73bba030bc53/download/elected-20240614105815.json",
}


def number(text: str | None) -> int | None:
    if text is None:
        return None
    text = str(text).strip()
    digits = re.sub(r"[^\d]", "", text.split(".")[0] if re.fullmatch(r"\d+\.0+", text) else text)
    return int(digits) if digits else None


class Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None and self.rows:
            self.rows[-1].append(" ".join("".join(self.cell).split()))
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


def rows(iso: str) -> list[list[str]]:
    reader = Rows()
    reader.feed(raw.cached(SOURCE, PAGES.get(iso) or FILES[iso]).path.read_text(encoding="utf-8"))
    return [row for row in reader.rows if row]


def italy() -> tuple[list[dict], dict]:
    found = rows("IT")
    value = lambda label: number(next(row for row in found if row[0] == label)[1])
    lists = [
        {"label": row[1], "votes": number(row[2]), "seats": number(row[4]) or 0, "position": position}
        for position, row in enumerate(
            (row for row in found if len(row) == 5 and row[1] and re.fullmatch(r"[\d.]+", row[2])), start=1
        )
    ]
    invalid = value("Non valide (bianche incl.)") or 0
    blank = value("Bianche") or 0
    return lists, {
        "registered": value("Elettori"),
        "cast": value("Votanti"),
        "blank": blank,
        "invalid": invalid - blank,
        "valid": sum(int(entry["votes"] or 0) for entry in lists),
    }


GROUPS = {
    "EPP": "PPE",
    "SD": "S&D",
    "Renew": "Renew",
    "GREENSEFA": "Verts/ALE",
    "Theleft": "The Left",
    "ECR": "ECR",
    "PfE": "PfE",
    "ESN": "ESN",
    "NI": "NI",
}
COLLEGES = {
    "L12025": ("fbe", "Dutch-speaking electoral college"),
    "L92094": ("wbe", "French-speaking electoral college"),
    "L63023": ("gbe", "German-speaking electoral college"),
}


def belgium() -> tuple[list[dict], dict]:
    sheets = pl.read_excel(raw.cached(SOURCE, FILES["BE"]).path, sheet_id=0, has_header=False, infer_schema_length=0)
    lists = []
    for sheet, (college, _) in COLLEGES.items():
        frame = sheets[f"Seat_{sheet}"]
        header = frame.row(0)
        votes = next(row for row in frame.iter_rows() if (row[0] or "").startswith("Chiffre électoral"))
        seats = next(row for row in frame.iter_rows() if (row[0] or "").startswith("Sièges"))
        for index in range(1, len(header)):
            if header[index] is None:
                continue
            number_text, label = [part.strip() for part in header[index].split("\n-\n", 1)]
            lists.append(
                {
                    "college": college,
                    "label": label,
                    "votes": number(votes[index]),
                    "seats": number(seats[index]) or 0,
                    "position": int(number_text),
                }
            )
    return lists, {}


def spain() -> tuple[list[dict], dict]:
    found = rows("ES")
    totals = [index for index, row in enumerate(found) if row[0] == "Total estatal."]
    accounting = [number(cell) or 0 for cell in found[totals[0]][1:]]
    lists = []
    for index in totals[1:]:
        cells = found[index][1:]
        seated = len(cells) % 2 == 1 and (number(cells[0]) or 0) <= 96
        header = next(
            found[back]
            for back in range(index - 1, 0, -1)
            if found[back][0] == "" and len(found[back]) > 1 and (found[back][1] == "Total escaños") == seated
        )
        if seated:
            pairs = [(cells[1 + 2 * at], cells[2 + 2 * at]) for at in range(len(header) - 2)]
            names = header[2:]
        else:
            pairs = [(cell, "0") for cell in cells]
            names = header[1:]
        for label, (votes, seats) in zip(names, pairs, strict=True):
            lists.append(
                {"label": label, "votes": number(votes), "seats": number(seats) or 0, "position": len(lists) + 1}
            )
    registered, cast, valid, _, blank, invalid = accounting
    return lists, {"registered": registered, "cast": cast, "valid": valid, "blank": blank, "invalid": invalid}


def france() -> tuple[list[dict], dict]:
    reader = csv.reader(io.StringIO(raw.cached(SOURCE, FILES["FR"]).path.read_text(encoding="utf-8")), delimiter=";")
    header = next(reader)
    at = {name: index for index, name in enumerate(header)}
    lists: dict[int, dict] = {}
    totals = defaultdict(int)
    for row in reader:
        for measure, column in (
            ("registered", "Inscrits"),
            ("cast", "Votants"),
            ("valid", "Exprimés"),
            ("blank", "Blancs"),
            ("invalid", "Nuls"),
        ):
            totals[measure] += int(row[at[column]])
        for index in range(1, 39):
            panel = row[at[f"Numéro de panneau {index}"]] if f"Numéro de panneau {index}" in at else ""
            if not panel:
                continue
            entry = lists.setdefault(
                int(panel),
                {
                    "label": row[at[f"Libellé de liste {index}"]],
                    "votes": 0,
                    "seats": int(row[at[f"Sièges {index}"]] or 0),
                },
            )
            entry["votes"] += int(row[at[f"Voix {index}"]])
    return [
        {"label": entry["label"], "votes": entry["votes"], "seats": entry["seats"], "position": panel}
        for panel, entry in sorted(lists.items())
    ], dict(totals)


def poland() -> tuple[list[dict], dict]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, FILES["PL"]).path)
    rows = list(csv.reader(io.StringIO(archive.read(archive.namelist()[0]).decode("utf-8-sig")), delimiter=";"))
    header, body = rows[0], [row for row in rows[1:] if row and row[0]]
    total = lambda index: sum(int(row[index] or 0) for row in body)
    seats = DECLARED["PL"][1]
    lists = [
        {"label": header[index], "votes": total(index), "seats": seats.get(header[index], 0), "position": index - 29}
        for index in range(30, len(header))
        if header[index]
    ]
    return lists, {"registered": total(4), "cast": total(21), "valid": total(29), "invalid": total(23) + total(25)}


def netherlands() -> tuple[list[dict], dict]:
    frame = pl.read_csv(raw.cached(SOURCE, FILES["NL"]).path, separator=";", infer_schema_length=0).filter(
        pl.col("Regio") == "Nederland"
    )
    lists = []
    for row in frame.filter(pl.col("VeldType") == "LijstAantalStemmen").iter_rows(named=True):
        seats = frame.filter(
            (pl.col("VeldType") == "LijstAantalZetels") & (pl.col("LijstNummer") == row["LijstNummer"])
        )["Waarde"]
        lists.append(
            {
                "label": row["LijstNaam"] or row["Partij"],
                "votes": int(row["Waarde"]),
                "seats": int(seats[0]) if len(seats) else 0,
                "position": int(row["LijstNummer"]),
            }
        )
    value = lambda kind: int(frame.filter((pl.col("VeldType") == kind) & pl.col("LijstNummer").is_null())["Waarde"][0])
    return lists, {
        "registered": value("Kiesgerechtigden"),
        "cast": value("Opkomst"),
        "valid": value("AantalGeldigeStemmen"),
        "blank": value("AantalBlancoStemmen"),
        "invalid": value("AantalOngeldigeStemmen"),
    }


def czechia() -> tuple[list[dict], dict]:
    root = ET.parse(raw.cached(SOURCE, FILES["CZ"]).path).getroot()
    space = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    nation = next(root.iter(f"{space}CR"))
    turnout = next(nation.iter(f"{space}UCAST")).attrib
    lists = []
    for party in nation.iter(f"{space}STRANA"):
        seats = party.find(f"{space}MANDATY_STRANA")
        lists.append(
            {
                "label": party.get("NAZ_STR"),
                "votes": int(next(party.iter(f"{space}HLASY_STRANA")).get("HLASY", "0")),
                "seats": int(seats.get("MANDATY", "0")) if seats is not None else 0,
                "position": int(party.get("ESTRANA", "0")),
            }
        )
    return lists, {
        "registered": int(turnout["ZAPSANI_VOLICI"]),
        "cast": int(turnout["ODEVZDANE_OBALKY"]),
        "valid": int(turnout["PLATNE_HLASY"]),
    }


def slovakia() -> tuple[list[dict], dict]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, FILES["SK"]).path)
    read = lambda name: list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
    turnout = read("EP2024_SK_tab01.csv")[0]
    lists = [
        {
            "label": row["Názov politického subjektu"],
            "votes": int(row["Počet platných hlasov"]),
            "seats": int(row["Počet získaných mandátov"] or 0),
            "position": int(row["Číslo politického subjektu"]),
        }
        for row in read("EP2024_SK_tab03a.csv")
    ]
    return lists, {
        "registered": int(turnout["Počet zapísaných voličov"]),
        "cast": int(turnout["Počet odovzdaných obálok"]),
        "valid": int(turnout["Počet platných hlasov spolu"]),
    }


def croatia() -> tuple[list[dict], dict]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, FILES["HR"]).path)
    rows = list(
        csv.reader(io.StringIO(archive.read("Rezultati/CSV/rezultati_eupa_liste.csv").decode("cp1250")), delimiter=";")
    )
    lines = [[" ".join(line.split()) for line in cell.splitlines() if line.strip()] for cell in rows[0]]
    header, body = [", ".join(cell) for cell in lines], [row for row in rows[1:] if len(row) == len(rows[0])]
    total = lambda index: sum(int(row[index] or 0) for row in body)
    at = {name: index for index, name in enumerate(header)}
    first = at["Nevažeći listići"] + 1
    seats = DECLARED["HR"][1]
    lists = [
        {
            "label": header[index],
            "votes": total(index),
            "seats": seats.get(lines[index][0], 0),
            "position": (index - first) // 2 + 1,
        }
        for index in range(first, len(header), 2)
    ]
    return lists, {
        "registered": total(at["Ukupno birača"]),
        "cast": total(at["Glasovalo birača (po listićima)"]),
        "valid": total(at["Važeći listići"]),
        "invalid": total(at["Nevažeći listići"]),
    }


def latvia() -> tuple[list[dict], dict]:
    stats = json.loads(raw.cached(SOURCE, FILES["LV"]).path.read_text())["departmentStats"]
    elected = json.loads(raw.cached(SOURCE, ELECTED["LV"]).path.read_text())
    seats = defaultdict(int)
    for person in elected["electedCandidates"]:
        seats[person["partyName"]] += 1
    lists = [
        {
            "label": party["name"],
            "votes": party["paperGood"],
            "seats": seats.get(party["name"], 0),
            "position": party["npk"],
        }
        for party in stats["partiesStats"]
    ]
    return lists, {"cast": stats["envelopeGood"], "valid": stats["paperGood"]}


def austria() -> tuple[list[dict], dict]:
    frame = pl.read_excel(raw.cached(SOURCE, FILES["AT"]).path, has_header=False, infer_schema_length=0)
    header = frame.row(0)
    nation = next(row for row in frame.iter_rows() if row[0] == "G00000")
    seats = DECLARED["AT"][1]
    first = header.index("gültig") + 1 if "gültig" in header else 6
    lists = [
        {
            "label": header[index],
            "votes": number(nation[index]),
            "seats": seats.get(header[index], 0),
            "position": position,
        }
        for position, index in enumerate(range(first, len(header), 2), start=1)
    ]
    return lists, {
        "registered": number(nation[2]),
        "cast": number(nation[3]),
        "invalid": number(nation[4]),
        "valid": number(nation[5]),
    }


def luxembourg() -> tuple[list[dict], dict]:
    frame = pl.read_excel(
        raw.cached(SOURCE, FILES["LU"]).path, sheet_name="Total", has_header=False, infer_schema_length=0
    )
    found = list(frame.iter_rows())
    seats = {"CSV": 2, "DP – DEMOKRATESCH PARTEI": 1, "LSAP": 1, "ADR": 1, "déi gréng": 1}
    labels = [
        (block[0][column], number(block[-1][column + 4]))
        for block in (found[:10], found[10:20])
        for column in range(0, len(block[0]), 6)
        if block[0][column]
    ]
    return [
        {"label": label, "votes": votes, "seats": seats.get(label, 0), "position": position}
        for position, (label, votes) in enumerate(labels, start=1)
    ], {}


def denmark() -> tuple[list[dict], dict]:
    found = rows("DK")
    value = lambda label: number(next(row for row in found if row[0] == label)[1])
    lists = [
        {"label": row[0].split(". ", 1)[1], "votes": number(row[1]), "seats": number(row[3]) or 0, "position": position}
        for position, row in enumerate((row for row in found if len(row) == 4 and re.match(r"\w\. ", row[0])), start=1)
    ]
    return lists, {
        "cast": value("I alt afgivne stemmer"),
        "valid": value("I alt gyldige stemmer"),
        "blank": value("Blanke stemmer"),
        "invalid": value("Andre ugyldige stemmer"),
    }


def text(iso: str) -> list[str]:
    path = raw.cached(SOURCE, FILES[iso]).path
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()


def sweden() -> tuple[list[dict], dict]:
    lines = text("SE")
    start = next(
        index for index, line in enumerate(lines) if "Röster och mandat för partierna" in line and "Bilaga" not in line
    )
    seats = {}
    accounting = {}
    keys = {
        "Summa giltiga röster": "valid",
        "varav blanka": "blank",
        "varav övriga ogiltiga": "invalid",
        "Summa avgivna röster": "cast",
        "Röstberättigade": "registered",
    }
    for line in lines[start:]:
        match = re.match(r"\s+(\S.*?)\s{2,}(\d[\d ]*\d)\s+[\d,]+(?:\s+[\d ]+\d\s+[\d,]+\s+(\d+))?\s*$", line)
        if match:
            seats[match[1]] = int(match[3] or 0)
        for label, key in keys.items():
            found = re.match(rf"\s+{label}\s+(\d{{1,3}}(?: \d{{3}})*)\b", line)
            if found and key not in accounting:
                accounting[key] = number(found[1])
        if "Röstberättigade" in line:
            break
    begin = next(
        index
        for index, line in enumerate(lines)
        if "Förteckning över antalet röster per parti och lista" in line and "Bilaga" not in line
    )
    end = next(index for index in range(begin, len(lines)) if "Bilaga 5" in lines[index])
    lists = []
    for line in lines[begin:end]:
        match = re.match(r"\s+([^\d\s].*?)\s{2,}(\d[\d ]*)$", re.sub(r"^(?:12|01|VAL 0003)(?=\s)", "", line.rstrip()))
        if (
            match
            and not match[1].startswith(("Sida", "Listnumret", "Summa", "Ogiltiga", "varav", "Ej anmälda"))
            and re.search(r"[^\W\d]", match[1])
        ):
            lists.append(
                {
                    "label": match[1],
                    "votes": number(match[2]),
                    "seats": seats.get(match[1], 0),
                    "position": len(lists) + 1,
                }
            )
    return lists, accounting


def portugal() -> tuple[list[dict], dict]:
    lines = text("PT")
    keys = {
        "Inscritos": "registered",
        "Votantes": "cast",
        "Brancos": "blank",
        "Nulos": "invalid",
        "Votos Val. Exp. (VVE)": "valid",
    }
    accounting = {}
    lists = []
    for line in lines:
        match = re.match(r"(\S.*?)\s{2,}(\d[\d ]*\d|\d)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$", line)
        if not match:
            continue
        if match[1] in keys:
            accounting[keys[match[1]]] = number(match[2])
        elif "valid" in accounting:
            lists.append(
                {
                    "label": match[1],
                    "votes": number(match[2]),
                    "seats": number(match[6]) or 0,
                    "position": len(lists) + 1,
                }
            )
    return lists, accounting


def greece() -> tuple[list[dict], dict]:
    data = json.loads(raw.cached(SOURCE, FILES["GR"]).path.read_text(encoding="utf-8"))
    model = raw.cached(SOURCE, PAGES["GR"]).path.read_text(encoding="utf-8")
    names = dict(re.findall(r'(\d+):\{id:\1,name:"([^"]+)",shortName:"[^"]*",color:', model))
    lists = [
        {
            "label": names[str(entry["PARTY_ID"])],
            "votes": entry["VOTES"],
            "seats": entry["EdresEpik"] or 0,
            "position": position,
        }
        for position, entry in enumerate(data["party"], start=1)
    ]
    return lists, {
        "registered": data["Gramenoi"],
        "valid": data["Egkyra"],
        "invalid": data["Akyra"],
        "blank": data["Leyka"],
    }


def bulgaria() -> tuple[list[dict], dict]:
    found = rows("BG")
    end = next(index for index in range(1, len(found)) if found[index][0] == "№")
    lists = [
        {
            "label": row[1].removesuffix(" разпределение на предпочитанията"),
            "votes": number(row[2]),
            "seats": number(row[4]) or 0,
            "position": int(row[0]),
        }
        for row in found[1:end]
        if len(row) >= 5 and row[0].isdigit()
    ]
    return lists, {}


def hungary() -> tuple[list[dict], dict]:
    nation = json.loads(raw.cached(SOURCE, FILES["HU"]).path.read_text(encoding="utf-8"))["list"][0]
    names = {
        entry["szavlap_sorsz"]: entry["jlcs_nev"]
        for entry in json.loads(raw.cached(SOURCE, PAGES["HU"]).path.read_text(encoding="utf-8"))["list"]
        if "szavlap_sorsz" in entry
    }
    lists = [
        {
            "label": names[entry["szavlap_sorsz"]],
            "votes": entry["szavazat"],
            "seats": entry["mandatum"] or 0,
            "position": entry["szavlap_sorsz"],
        }
        for entry in nation["tetelek"]
    ]
    return lists, {
        "registered": nation["vp_osszes"],
        "cast": nation["szavazott_osszesen"],
        "valid": nation["szl_ervenyes"],
        "invalid": nation["szl_ervenytelen"],
    }


def finnish(url: str, tag: str) -> list[ET.Element]:
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive, archive.open(archive.namelist()[0]) as handle:
        found = []
        for _, element in ET.iterparse(handle):
            if element.tag == tag:
                found.append(element)
            if element.tag == "country-data":
                return found
    return found


def finland() -> tuple[list[dict], dict]:
    lists = [
        {
            "label": element.get("name"),
            "votes": int(element.get("total-votes") or 0),
            "seats": int(element.get("seats") or 0),
            "position": int(element.get("nominator-number") or 0),
        }
        for element in finnish(FILES["FI"], "nominator")
    ]
    basic = finnish(PAGES["FI"], "basic-voting-data")[0]
    supplemental = finnish(PAGES["FI"], "supplemental-voting-data")[0]
    return lists, {
        "registered": number(basic.get("eligible-voters-total")),
        "cast": number(supplemental.get("total-votes")),
        "valid": number(supplemental.get("approved-total-votes")),
        "invalid": number(supplemental.get("invalid-total-votes")),
    }


def slovenia() -> tuple[list[dict], dict]:
    data = json.loads(raw.cached(SOURCE, FILES["SI"]).path.read_text(encoding="utf-8"))
    lists = [
        {"label": entry["naz"], "votes": entry["gl"], "seats": entry["man"] or 0, "position": position}
        for position, entry in enumerate(data["slovenija"], start=1)
    ]
    return lists, {"cast": data["glas"], "valid": data["velj"], "invalid": data["nev"]}


def cyprus() -> tuple[list[dict], dict]:
    result = json.loads(raw.cached(SOURCE, FILES["CY"]).path.read_text(encoding="utf-8"))["CurrentResult"]
    seats = {
        "ΔΗΜΟΚΡΑΤΙΚΟΣ ΣΥΝΑΓΕΡΜΟΣ": 2,
        "ΑΚΕΛ (Α.Κ.Ε.Λ.) ΑΡΙΣΤΕΡΑ - ΚΟΙΝΩΝΙΚΗ ΣΥΜΜΑΧΙΑ": 1,
        "ΠΑΝΑΓΙΩΤΟΥ ΦΕΙΔΙΑΣ": 1,
        "ΕΘΝΙΚΟ ΛΑΪΚΟ ΜΕΤΩΠΟ (Ε.ΛΑ.Μ.)": 1,
        "ΔΗΜΟΚΡΑΤΙΚΟ ΚΟΜΜΑ": 1,
    }
    lists = [
        {
            "label": entry["Candidate"]["Name"],
            "votes": entry["BallotsCount"],
            "seats": seats.get(entry["Candidate"]["Name"], 0),
            "position": position,
        }
        for position, entry in enumerate(result["CandidateResults"], start=1)
    ]
    return lists, {
        "registered": result["RegisteredVotersTotal"],
        "cast": result["VotersVotedTotal"],
        "valid": result["ValidBallots"],
        "invalid": result["InvalidBallots"],
        "blank": result["BlankBallots"],
    }


def lithuania() -> tuple[list[dict], dict]:
    seats = {14: 3, 2: 2, 1: 1, 5: 1, 6: 1, 8: 1, 9: 1, 13: 1}
    votes: dict[tuple[int, str], int] = defaultdict(int)
    with raw.cached(SOURCE, FILES["LT"]).path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            votes[(int(row["SARASO_NUMERIS"]), row["SARASO_PAVADINIMAS"])] += int(row["BALSU_VISO"] or 0)
    lists = [
        {"label": label, "votes": total, "seats": seats.get(position, 0), "position": position}
        for (position, label), total in sorted(votes.items())
    ]
    return lists, {"valid": sum(votes.values())}


def malta() -> tuple[list[dict], dict]:
    seats = {"Partit Laburista": 3, "Partit Nazzjonalista": 3}
    page = raw.cached(SOURCE, FILES["MT"]).path.read_text(encoding="utf-8")
    reader = Rows()
    reader.feed(page)
    found = [row for row in reader.rows if row]
    start = max(index for index, row in enumerate(found) if row[:2] == ["", "Count 1"])
    lists: list[dict] = []
    party = None
    for row in found[start + 1 :]:
        if row[0] == "Non-Transferrable Votes":
            break
        if len(row) == 1:
            party = row[0]
            continue
        if party == "Kandidat Indipendenti" or not lists or lists[-1]["label"] != party:
            label = row[0] if party == "Kandidat Indipendenti" else party
            lists.append({"label": label, "votes": 0, "seats": seats.get(label, 0), "position": len(lists) + 1})
        lists[-1]["votes"] += number(row[1])
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    value = lambda label: number(re.search(label + r"(?: [\d.]+%)? ([\d,]+)", text)[1])
    return lists, {
        "registered": value("Registered Voters"),
        "cast": value("Votes Cast"),
        "valid": value("Valid Votes"),
        "invalid": value("Invalid Votes"),
    }


def estonia() -> tuple[list[dict], dict]:
    found = rows("EE")
    start = found.index(["Erakond või üksikkandidaat", "Hääli", "Protsent häältest", "Mandaate"])
    lists = [
        {"label": row[0], "votes": number(row[1]), "seats": int(row[3].split()[0]), "position": position}
        for position, row in enumerate((row for row in found[start + 1 :] if len(row) == 4), start=1)
    ]
    text = raw.cached(SOURCE, FILES["EE"]).path.read_text(encoding="utf-8")
    return lists, {"valid": number(re.search(r"Hääli kokku:\s*([\d\s]+?|[\d&nbsp;]+?)\s*<", text)[1])}


ROMANIA = {
    1: "UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA",
    2: "ALIANȚA ELECTORALĂ PSD PNL",
    3: "PARTIDUL REÎNNOIM PROIECTUL EUROPEAN AL ROMÂNIEI",
    4: "ALIANȚA AUR",
    5: "PARTIDUL UMANIST SOCIAL LIBERAL",
    6: "ALIANȚA DREAPTA UNITĂ USR - PMP - FORȚA DREPTEI",
    7: "ROMÂNIA SOCIALISTĂ",
    8: "PARTIDUL ROMÂNIA MARE",
    9: "PARTIDUL PATRIOȚILOR",
    10: "PARTIDUL ALTERNATIVA DREAPTĂ",
    11: "PARTIDUL S.O.S. ROMÂNIA",
    12: "PARTIDUL DIASPORA UNITĂ",
    13: "PÎRVĂNESCU PAULA-MARINELA",
    14: "GHEORGHE VLAD-DAN",
    15: "ȘTEFĂNUȚĂ NICOLAE-BOGDĂNEL",
    16: "ȘOȘOACĂ DUMITRU-SILVESTRU",
}
SCANS = DATA / "parsed" / "us.scans" / "ro-ep-2024" / "tables.parquet"


def romania() -> tuple[list[dict], dict]:
    read = pl.read_parquet(SCANS)
    cells = {(row["contest"], row["code"]): row for row in read.iter_rows(named=True)}
    seated = {2: "1", 4: "2", 6: "3", 11: "4", 1: "5", 15: "6"}
    value = lambda contest, code: cells[(contest, code)]["votes"]
    accounting = {code: value("accounting", code) for code in ("a", "a1", "a2", "b", "b1", "b2", "b3", "e", "f")}
    lists = [
        {
            "label": label,
            "votes": value("lists", str(position)),
            "seats": value("seats", seated[position]) if position in seated else 0,
            "position": position,
        }
        for position, label in ROMANIA.items()
    ]
    wrong = [
        check
        for check, holds in (
            ("a = a1 + a2", accounting["a"] == accounting["a1"] + accounting["a2"]),
            ("b = b1 + b2 + b3", accounting["b"] == accounting["b1"] + accounting["b2"] + accounting["b3"]),
            ("e = the lists' sum", accounting["e"] == sum(entry["votes"] for entry in lists)),
            ("33 seats", sum(entry["seats"] for entry in lists) == 33),
        )
        if not holds
    ]
    if wrong:
        raise ValueError(f"RO procès-verbal fails {wrong}")
    measures = {"a": 2, "b": 3, "e": 4, "f": 5}
    pl.DataFrame(
        [
            {
                "iso": "RO",
                "position": None if contest == "accounting" else position,
                "measure": measures[code] if contest == "accounting" else 1 if contest == "lists" else 16,
                **{key: cells[(contest, code)][key] for key in ("page", "x0", "y0", "x1", "y1")},
            }
            for contest, code, position in (
                *(("accounting", code, None) for code in measures),
                *(("lists", str(position), position) for position in ROMANIA),
                *(("seats", code, position) for position, code in seated.items()),
            )
        ],
        schema_overrides={"position": pl.Int64},
    ).write_parquet(PARSED / "locators.parquet")
    return lists, {
        "registered": accounting["a"],
        "cast": accounting["b"],
        "valid": accounting["e"],
        "invalid": accounting["f"],
    }


PARSERS = {
    "FR": france,
    "IT": italy,
    "ES": spain,
    "BE": belgium,
    "PL": poland,
    "NL": netherlands,
    "CZ": czechia,
    "SK": slovakia,
    "HR": croatia,
    "LV": latvia,
    "AT": austria,
    "LU": luxembourg,
    "DK": denmark,
    "SE": sweden,
    "PT": portugal,
    "GR": greece,
    "BG": bulgaria,
    "HU": hungary,
    "FI": finland,
    "SI": slovenia,
    "CY": cyprus,
    "LT": lithuania,
    "MT": malta,
    "EE": estonia,
    "RO": romania,
}


def parse() -> None:
    lists: list[dict] = []
    totals: list[dict] = []
    for iso, parser in PARSERS.items():
        found, accounting = parser()
        if accounting.get("valid") is not None and sum(entry["votes"] for entry in found) not in (
            accounting["valid"],
            accounting["valid"] - accounting.get("blank", 0),
        ):
            print(f"{iso}: lists sum to {sum(entry['votes'] for entry in found)}, valid {accounting['valid']}")
        lists += [{"iso": iso, "college": "", **entry} for entry in found]
        if not accounting:
            continue
        totals.append(
            {
                "iso": iso,
                "college": "",
                **{key: accounting.get(key) for key in ("registered", "cast", "valid", "blank", "invalid")},
            }
        )
    frame = pl.DataFrame(lists)
    check(frame)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lists.parquet")
    pl.DataFrame(totals).write_parquet(PARSED / "totals.parquet")
    print(frame.group_by("iso").agg(pl.len(), pl.col("votes").sum(), pl.col("seats").sum()).sort("iso"))


def check(frame: pl.DataFrame) -> None:
    parties = list(
        csv.DictReader(
            io.StringIO(raw.cached(SOURCE, f"{EP}/parties.csv").path.read_text(encoding="utf-8-sig")), delimiter=";"
        )
    )
    coalesced = {row["ID"] for row in parties if row["MEMBER_OF_COALITION"]}
    wrong = []
    for (iso, college), rows in frame.group_by(["iso", "college"]):
        code = {"GR": "el"}.get(iso, iso.lower()) if not college else college
        declared = list(
            csv.DictReader(
                io.StringIO(
                    raw.cached(SOURCE, f"{EP}/seats-breakdown-parties/{code}.csv").path.read_text(encoding="utf-8-sig")
                ),
                delimiter=";",
            )
        )
        summed = defaultdict(int)
        for row in declared:
            if row["PARTY_ID"] not in coalesced:
                summed[row["PARTY_ID"]] += int(row["SEATS_TOTAL"])
        expected = sorted((seats for seats in summed.values() if seats), reverse=True)
        found = sorted((seats for seats in rows["seats"] if seats), reverse=True)
        if found != expected:
            wrong.append(f"{iso} {college}: seats {found} differ from the European Parliament's {expected}")
    if wrong:
        raise ValueError("\n".join(wrong))


AUTHORITIES = {
    "FR": (
        "Ministère de l'Intérieur",
        "Résultats définitifs des élections européennes du 9 juin 2024 par circonscription",
        "https://www.data.gouv.fr/fr/datasets/resultats-des-elections-europeennes-du-9-juin-2024/",
        "Licence Ouverte 2.0",
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "IT": (
        "Ministero dell'Interno",
        "Eligendo Archivio: elezioni europee del 9 giugno 2024, Italia ed estero",
        PAGES["IT"],
        None,
        date(2024, 6, 8),
        date(2024, 6, 9),
    ),
    "PL": (
        "Państwowa Komisja Wyborcza",
        "Wybory do Parlamentu Europejskiego 2024: wyniki głosowania na listy po okręgach",
        "https://danewyborcze.kbw.gov.pl/",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "NL": (
        "Kiesraad",
        "Verkiezingsuitslag Europees Parlement 2024",
        "https://data.overheid.nl/dataset/verkiezingsuitslag-europees-parlement-2024",
        "CC0 1.0",
        date(2024, 6, 6),
        date(2024, 6, 6),
    ),
    "CZ": (
        "Český statistický úřad",
        "Volby do Evropského parlamentu 2024: výsledky (otevřená data)",
        "https://www.volby.cz/opendata/ep2024/EP2024_XML.htm",
        None,
        date(2024, 6, 7),
        date(2024, 6, 8),
    ),
    "SK": (
        "Štatistický úrad Slovenskej republiky",
        "Voľby do Európskeho parlamentu 2024: výsledky",
        "https://volby.statistics.sk/ep/ep2024/",
        None,
        date(2024, 6, 8),
        date(2024, 6, 8),
    ),
    "HR": (
        "Državno izborno povjerenstvo Republike Hrvatske",
        "Izbori članova u Europski parlament iz Republike Hrvatske 2024: rezultati",
        "https://www.izbori.hr/",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "LV": (
        "Centrālā vēlēšanu komisija",
        "Eiropas Parlamenta vēlēšanas 2024: rezultāti (atvērtie dati)",
        "https://data.gov.lv/dati/lv/dataset/ep-velesanas-2024",
        "CC0 1.0",
        date(2024, 6, 8),
        date(2024, 6, 8),
    ),
    "AT": (
        "Bundesministerium für Inneres",
        "Europawahl 2024: endgültiges Ergebnis",
        "https://www.bmi.gv.at/412/europawahlen/europawahl_2024/start.html",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "ES": (
        "Junta Electoral Central",
        "Acuerdo de 27 de junio de 2024, de la Junta Electoral Central, por el que se procede a la publicación de los resultados de las elecciones de diputados y diputadas al Parlamento Europeo",
        FILES["ES"],
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "BE": (
        "SPF Intérieur",
        "Élections européennes 2024: chiffres électoraux et répartition des sièges par collège",
        "https://elections.fgov.be/",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "LU": (
        "Gouvernement du Grand-Duché de Luxembourg",
        "Élections européennes du 9 juin 2024: tableau des résultats officiels",
        "https://data.public.lu/fr/datasets/elections-legislatives-2024-donnees-officielles/",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "DK": (
        "Danmarks Statistik",
        "Europa-Parlamentsvalg søndag 9. juni 2024: resultater, hele landet",
        FILES["DK"],
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "SE": (
        "Valmyndigheten",
        "Val till Europaparlamentet 2024: beslut och protokollsbilagor (VAL-246-2024)",
        FILES["SE"],
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "GR": (
        "Υπουργείο Εσωτερικών",
        "Ευρωεκλογές 2024: επικράτεια, οριστικά αποτελέσματα",
        "https://ekloges.ypes.gr/current/e/home/",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "BG": (
        "Централна избирателна комисия",
        "Избори за членове на Европейския парламент от Република България 9 юни 2024: обобщени данни",
        "https://results.cik.bg/europe2024/rezultati/",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "HU": (
        "Nemzeti Választási Iroda",
        "Európai parlamenti választás 2024: országos listás jegyzőkönyv",
        "https://vtr.valasztas.hu/ep2024",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "FI": (
        "Oikeusministeriö",
        "Europarlamenttivaalit 2024: puolueiden tulokset ja äänestysaktiivisuus, koko maa",
        "https://tulospalvelu.vaalit.fi/EPV-2024/fi/ladattavat_tiedostot.html",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "SI": (
        "Državna volilna komisija",
        "Volitve poslancev iz Republike Slovenije v Evropski parlament 2024: rezultati",
        "https://volitve.dvk-rs.si/ep2024/",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "CY": (
        "Υπουργείο Εσωτερικών, Υπηρεσία Εκλογών",
        "Ευρωεκλογές 2024: επίσημα αποτελέσματα, παγκύπρια; ανακήρυξη εκλεγέντων μελών του Ευρωπαϊκού Κοινοβουλίου",
        "https://results.elections.moi.gov.cy/greek/european_elections_2024_163/islandwide",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "PT": (
        "Comissão Nacional de Eleições",
        "Mapa Oficial n.º 4/2024: resultado da eleição para o Parlamento Europeu de 9 de junho de 2024",
        FILES["PT"],
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "LT": (
        "Vyriausioji rinkimų komisija",
        "2024 m. birželio 9 d. rinkimai į Europos Parlamentą: rinkimų rezultatai pagal apylinkes",
        "https://www.vrk.lt/lt/2024-m-birzelio-9-d-rinkimai-i-europos-parlamenta/rinkimu-rezultatai",
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "MT": (
        "Electoral Commission of Malta",
        "MEP Election 2024: count results, national",
        FILES["MT"],
        None,
        date(2024, 6, 8),
        date(2024, 6, 8),
    ),
    "EE": (
        "Vabariigi Valimiskomisjon",
        "Euroopa Parlament 2024: hääletamis- ja valimistulemus, Eesti Vabariik; otsus nr 16, 25.06.2024 (RT III, 27.06.2024, 3)",
        FILES["EE"],
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
    "RO": (
        "Biroul Electoral Central",
        "Proces-verbal privind centralizarea voturilor și atribuirea mandatelor la alegerile pentru membrii din România în Parlamentul European din data de 09 iunie 2024",
        FILES["RO"],
        None,
        date(2024, 6, 9),
        date(2024, 6, 9),
    ),
}
NOTES = {
    "LT": "Votes are the Central Electoral Commission's polling-station file summed by list; their total, 678,319, is the commission's count of valid votes. Seats are as the commission declared them.",
    "MT": "Malta elects its six members by single transferable vote. Each party's votes are its candidates' first preferences, summed from the Electoral Commission's count sheet, and each independent candidate is shown on their own. Seats are the candidates each party had elected, as the European Parliament's results datasheet gives them.",
    "EE": "Votes are from the electoral service's results page as of 11 June 2024, with all 378 polling stations, electronic votes, and paper votes from abroad counted. Seats are the members the National Electoral Committee registered on 25 June 2024.",
    "RO": "The figures were read from a scan of the Central Electoral Bureau's signed procès-verbal and checked against its own arithmetic: the lists' votes add up to the printed total of valid votes, and the seats add up to 33. The boxes that locate each figure on the page are approximate.",
}
COUNTRIES = {
    "FR": "France",
    "IT": "Italy",
    "PL": "Poland",
    "NL": "the Netherlands",
    "CZ": "Czechia",
    "SK": "Slovakia",
    "HR": "Croatia",
    "LV": "Latvia",
    "AT": "Austria",
    "ES": "Spain",
    "BE": "Belgium",
    "LU": "Luxembourg",
    "DK": "Denmark",
    "SE": "Sweden",
    "PT": "Portugal",
    "GR": "Greece",
    "BG": "Bulgaria",
    "HU": "Hungary",
    "FI": "Finland",
    "SI": "Slovenia",
    "CY": "Cyprus",
    "LT": "Lithuania",
    "MT": "Malta",
    "EE": "Estonia",
    "RO": "Romania",
}
NAMES = {
    ("BE", "LES ENGAGÉS"): "Les Engagés",
    ("ES", "SUMAR"): "Sumar",
    ("ES", "PODEMOS"): "Podemos",
    ("ES", "COALICIÓN POR UNA EUROPA SOLIDARIA"): "Coalición por una Europa Solidaria",
    ("HR", "MOŽEMO! - POLITIČKA PLATFORMA"): "Možemo! – politička platforma",
    ("HR", "DOMOVINSKI POKRET - DP"): "Domovinski pokret",
    ("HU", "TISZA"): "Tisztelet és Szabadság Párt",
    ("LU", "DP – DEMOKRATESCH PARTEI"): "Demokratesch Partei",
    ("LV", "LATVIJA PIRMAJĀ VIETĀ"): "Latvija pirmajā vietā",
    ("PL", "KOALICYJNY KOMITET WYBORCZY KOALICJA OBYWATELSKA"): "Koalicja Obywatelska",
    ("PL", "KOALICYJNY KOMITET WYBORCZY LEWICA"): "Lewica",
    (
        "PL",
        "KOALICYJNY KOMITET WYBORCZY TRZECIA DROGA POLSKA 2050 SZYMONA HOŁOWNI - POLSKIE STRONNICTWO LUDOWE",
    ): "Trzecia Droga",
    ("PL", "KOMITET WYBORCZY KONFEDERACJA WOLNOŚĆ I NIEPODLEGŁOŚĆ"): "Konfederacja Wolność i Niepodległość",
    ("PT", "AD — ALIANÇA DEMOCRÁTICA"): "Aliança Democrática",
    ("PT", "CHEGA"): "Chega",
    ("RO", "PARTIDUL S.O.S. ROMÂNIA"): "S.O.S. România",
    ("RO", "ALIANȚA AUR"): "Alianța AUR",
    ("RO", "ALIANȚA ELECTORALĂ PSD PNL"): "Alianța Electorală PSD PNL",
    ("RO", "ȘTEFĂNUȚĂ NICOLAE-BOGDĂNEL"): "Nicolae Ștefănuță",
    ("RO", "UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA"): "Uniunea Democrată Maghiară din România",
    ("RO", "ALIANȚA DREAPTA UNITĂ USR - PMP - FORȚA DREPTEI"): "Alianța Dreapta Unită",
    ("SI", "NOVA SLOVENIJA - KRŠČANSKI DEMOKRATI"): "Nova Slovenija – Krščanski demokrati",
    ("SI", "SOCIALNI DEMOKRATI - SD"): "Socialni demokrati",
    ("SI", "VESNA - ZELENA STRANKA"): "Vesna – zelena stranka",
    ("SI", "SLOVENSKA DEMOKRATSKA STRANKA - SDS"): "Slovenska demokratska stranka",
    ("SI", "GIBANJE SVOBODA"): "Gibanje Svoboda",
    ("SK", "REPUBLIKA"): "Republika",
}
SHORTS = {
    ("BG", "ВЪЗРАЖДАНЕ"): "Vazrazhdane",
    ("BG", "КОАЛИЦИЯ ПРОДЪЛЖАВАМЕ ПРОМЯНАТА – ДЕМОКРАТИЧНА БЪЛГАРИЯ"): "PP–DB",
    ("BG", "ПП ИМА ТАКЪВ НАРОД"): "ITN",
    ("CY", "ΑΚΕΛ (Α.Κ.Ε.Λ.) ΑΡΙΣΤΕΡΑ - ΚΟΙΝΩΝΙΚΗ ΣΥΜΜΑΧΙΑ"): "AKEL",
    ("CY", "ΔΗΜΟΚΡΑΤΙΚΟΣ ΣΥΝΑΓΕΡΜΟΣ"): "DISY",
    ("CY", "ΔΗΜΟΚΡΑΤΙΚΟ ΚΟΜΜΑ"): "DIKO",
    ("CY", "ΕΘΝΙΚΟ ΛΑΪΚΟ ΜΕΤΩΠΟ (Ε.ΛΑ.Μ.)"): "ELAM",
    ("CZ", "SPOLU (ODS, KDU-ČSL, TOP 09)"): "SPOLU",
    ("CZ", "PŘÍSAHA a MOTORISTÉ"): "Přísaha–Motoristé",
    ("CZ", "STAČILO!, KSČM, SD-SN, ČSNS"): "Stačilo!",
    ("CZ", "Starostové a osobn. pro Evropu"): "STAN",
    ("CZ", "SPD a Trikolora"): "SPD–Trikolora",
    ("DK", "SF - Socialistisk Folkeparti"): "SF",
    ("DK", "Venstre, Danmarks Liberale Parti"): "Venstre",
    ("DK", "Danmarksdemokraterne - Inger Støjberg"): "DD",
    ("DK", "Dansk Folkeparti"): "DF",
    ("DK", "Det Konservative Folkeparti"): "KF",
    ("DK", "Enhedslisten - De Rød-Grønne"): "Enhedslisten",
    ("DK", "Liberal Alliance"): "LA",
    ("DK", "Radikale Venstre"): "RV",
    ("EE", "Sotsiaaldemokraatlik Erakond"): "SDE",
    ("EE", "Eesti Reformierakond"): "Reform",
    ("EE", "ISAMAA Erakond"): "Isamaa",
    ("EE", "Eesti Keskerakond"): "Kesk",
    ("EE", "Eesti Konservatiivne Rahvaerakond"): "EKRE",
    ("ES", "Agrupación de electores «SE ACABÓ LA FIESTA»"): "SALF",
    ("ES", "Ahora Repúblicas (ERC-EH BILDU- BNG-ARA MÉS)"): "Ahora Repúblicas",
    ("ES", "COALICIÓN POR UNA EUROPA SOLIDARIA"): "CEUS",
    ("FI", "Kansallinen Kokoomus"): "KOK",
    ("FI", "Vasemmistoliitto"): "VAS",
    ("FI", "Suomen Keskusta"): "KESK",
    ("FI", "Suomen Sosialidemokraattinen Puolue"): "SDP",
    ("FI", "Vihreä liitto"): "VIHR",
    ("FI", "Perussuomalaiset"): "PS",
    ("FI", "Suomen ruotsalainen kansanpuolue"): "SFP",
    ("GR", "ΣΥΝΑΣΠΙΣΜΟΣ ΡΙΖΟΣΠΑΣΤΙΚΗΣ ΑΡΙΣΤΕΡΑΣ-ΠΡΟΟΔΕΥΤΙΚΗ ΣΥΜΜΑΧΙΑ"): "SYRIZA",
    ("GR", "ΕΛΛΗΝΙΚΗ ΛΥΣΗ-ΚΥΡΙΑΚΟΣ ΒΕΛΟΠΟΥΛΟΣ"): "Elliniki Lysi",
    ("GR", "ΚΟΜΜΟΥΝΙΣΤΙΚΟ ΚΟΜΜΑ ΕΛΛΑΔΑΣ"): "KKE",
    ("GR", "ΔΗΜΟΚΡΑΤΙΚΟ ΠΑΤΡΙΩΤΙΚΟ ΚΙΝΗΜΑ «ΝΙΚΗ»"): "Niki",
    ("GR", "ΠΛΕΥΣΗ ΕΛΕΥΘΕΡΙΑΣ-ΖΩΗ ΚΩΝΣΤΑΝΤΟΠΟΥΛΟΥ"): "Plefsi Eleftherias",
    ("GR", "ΦΩΝΗ ΛΟΓΙΚΗΣ ΑΦΡΟΔΙΤΗ ΛΑΤΙΝΟΠΟΥΛΟΥ"): "Foni Logikis",
    (
        "HR",
        'SOCIJALDEMOKRATSKA PARTIJA HRVATSKE - SDP, "DALIJA OREŠKOVIĆ I LJUDI S IMENOM I PREZIMENOM" - "DO i SIP", CENTAR, HRVATSKA SELJAČKA STRANKA - HSS, GRAĐANSKO-LIBERALNI SAVEZ - GLAS',
    ): "SDP",
    ("HR", "DOMOVINSKI POKRET - DP"): "DP",
    ("HR", "MOŽEMO! - POLITIČKA PLATFORMA"): "Možemo!",
    ("HU", "TISZA"): "Tisza",
    ("HU", "DK-MSZP-Párbeszéd- ZÖLDEK"): "DK–MSZP–P",
    ("LT", "Tėvynės sąjunga-Lietuvos krikščionys demokratai"): "TS–LKD",
    ("LT", "Lietuvos socialdemokratų partija"): "LSDP",
    ("LT", "Demokratų sąjunga „Vardan Lietuvos“"): "DSVL",
    ("LT", "Laisvės partija"): "LP",
    ("LT", "Liberalų sąjūdis"): "LS",
    ("LT", "Lietuvos lenkų rinkimų akcija-Krikščioniškų šeimų sąjunga"): "LLRA–KŠS",
    ("LT", "Lietuvos valstiečių ir žaliųjų sąjunga"): "LVŽS",
    ("LT", "Tautos ir teisingumo sąjunga (centristai, tautininkai)"): "TTS",
    ("LU", "DP – DEMOKRATESCH PARTEI"): "DP",
    ("LV", "Jaunā VIENOTĪBA"): "JV",
    ("LV", '"APVIENOTAIS SARAKSTS - Latvijas Zaļā partija, Latvijas Reģionu Apvienība, Liepājas partija"'): "AS",
    ("LV", '"Latvijas attīstībai"'): "LA",
    ("LV", '"PROGRESĪVIE"'): "Progresīvie",
    ("LV", "LATVIJA PIRMAJĀ VIETĀ"): "LPV",
    ("MT", "Partit Laburista"): "PL",
    ("MT", "Partit Nazzjonalista"): "PN",
    ("NL", "GROENLINKS / Partij van de Arbeid (PvdA)"): "GL–PvdA",
    ("NL", "Volt Nederland"): "Volt",
    ("NL", "Staatkundig Gereformeerde Partij (SGP)"): "SGP",
    ("PL", "KOMITET WYBORCZY KONFEDERACJA WOLNOŚĆ I NIEPODLEGŁOŚĆ"): "Konfederacja",
    ("PL", "KOALICYJNY KOMITET WYBORCZY LEWICA"): "Lewica",
    (
        "PL",
        "KOALICYJNY KOMITET WYBORCZY TRZECIA DROGA POLSKA 2050 SZYMONA HOŁOWNI - POLSKIE STRONNICTWO LUDOWE",
    ): "Trzecia Droga",
    ("PT", "Partido Socialista"): "PS",
    ("PT", "AD — ALIANÇA DEMOCRÁTICA"): "AD",
    ("PT", "Iniciativa Liberal"): "IL",
    ("PT", "Bloco de Esquerda"): "BE",
    ("PT", "CDU — Coligação Democrática Unitária"): "CDU",
    ("RO", "ALIANȚA ELECTORALĂ PSD PNL"): "PSD–PNL",
    ("RO", "ALIANȚA AUR"): "AUR",
    ("RO", "ALIANȚA DREAPTA UNITĂ USR - PMP - FORȚA DREPTEI"): "ADU",
    ("RO", "PARTIDUL S.O.S. ROMÂNIA"): "S.O.S.",
    ("RO", "UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA"): "UDMR",
    ("SE", "Arbetarepartiet-Socialdemokraterna"): "S",
    ("SE", "Moderaterna"): "M",
    ("SE", "Miljöpartiet de gröna"): "MP",
    ("SE", "Sverigedemokraterna"): "SD",
    ("SE", "Centerpartiet"): "C",
    ("SE", "Vänsterpartiet"): "V",
    ("SE", "Kristdemokraterna"): "KD",
    ("SE", "Liberalerna (tidigare Folkpartiet)"): "L",
    ("SI", "SLOVENSKA DEMOKRATSKA STRANKA - SDS"): "SDS",
    ("SI", "GIBANJE SVOBODA"): "GS",
    ("SI", "NOVA SLOVENIJA - KRŠČANSKI DEMOKRATI"): "NSi",
    ("SI", "SOCIALNI DEMOKRATI - SD"): "SD",
    ("SI", "VESNA - ZELENA STRANKA"): "Vesna",
    ("SK", "HLAS - sociálna demokracia"): "Hlas",
    ("SK", "Progresívne Slovensko"): "PS",
}
COLORS = {
    ("AT", "ÖVP"): "#63c3d0",
    ("AT", "SPÖ"): "#ce000c",
    ("AT", "FPÖ"): "#0056a2",
    ("AT", "GRÜNE"): "#88b626",
    ("AT", "NEOS"): "#e84188",
    ("BE", "N-VA"): "#ffed00",
    ("BE", "VLAAMS BELANG"): "#ffe500",
    ("BE", "MR"): "#0047ab",
    ("BE", "PS"): "#ff0000",
    ("BE", "Vooruit"): "#ff2900",
    ("BE", "cd&v"): "#ff9900",
    ("BE", "Open Vld Renew EU"): "#003d6d",
    ("BE", "GROEN"): "#83de62",
    ("BE", "ECOLO"): "#3cb043",
    ("BE", "PVDA"): "#aa0000",
    ("BE", "PTB"): "#aa0000",
    ("BE", "LES ENGAGÉS"): "#00c9c9",
    ("BG", "ГЕРБ-СДС"): "#0054a6",
    ("BG", "БСП за БЪЛГАРИЯ"): "#db0f28",
    ("BG", "ВЪЗРАЖДАНЕ"): "#c09f2e",
    ("CY", "ΔΗΜΟΚΡΑΤΙΚΟΣ ΣΥΝΑΓΕΡΜΟΣ"): "#1569c7",
    ("CY", "ΑΚΕΛ (Α.Κ.Ε.Λ.) ΑΡΙΣΤΕΡΑ - ΚΟΙΝΩΝΙΚΗ ΣΥΜΜΑΧΙΑ"): "#d4202a",
    ("CY", "ΔΗΜΟΚΡΑΤΙΚΟ ΚΟΜΜΑ"): "#f39200",
    ("CY", "ΕΘΝΙΚΟ ΛΑΪΚΟ ΜΕΤΩΠΟ (Ε.ΛΑ.Μ.)"): "#1c2f5b",
    ("CZ", "ANO 2011"): "#261060",
    ("CZ", "SPOLU (ODS, KDU-ČSL, TOP 09)"): "#23438b",
    ("CZ", "Starostové a osobn. pro Evropu"): "#5d8c00",
    ("CZ", "Česká pirátská strana"): "#000000",
    ("DK", "Socialdemokratiet"): "#a82721",
    ("DK", "SF - Socialistisk Folkeparti"): "#e07ea8",
    ("DK", "Venstre, Danmarks Liberale Parti"): "#254264",
    ("DK", "Radikale Venstre"): "#733280",
    ("DK", "Det Konservative Folkeparti"): "#96b226",
    ("DK", "Dansk Folkeparti"): "#eac73e",
    ("DK", "Danmarksdemokraterne - Inger Støjberg"): "#7896d2",
    ("DK", "Liberal Alliance"): "#3fb2be",
    ("DK", "Enhedslisten - De Rød-Grønne"): "#e6801a",
    ("DK", "Moderaterne"): "#842990",
    ("EE", "Sotsiaaldemokraatlik Erakond"): "#e10600",
    ("EE", "Eesti Reformierakond"): "#ffde00",
    ("EE", "ISAMAA Erakond"): "#009ce2",
    ("EE", "Eesti Keskerakond"): "#007557",
    ("EE", "Eesti Konservatiivne Rahvaerakond"): "#0f4dad",
    ("ES", "Partido Popular"): "#1d84ce",
    ("ES", "Partido Socialista Obrero Español"): "#e30613",
    ("ES", "VOX"): "#63be21",
    ("ES", "SUMAR"): "#e51c55",
    ("ES", "PODEMOS"): "#6c1e74",
    ("ES", "Junts i Lliures per Europa"): "#00c3b2",
    ("FI", "Kansallinen Kokoomus"): "#006288",
    ("FI", "Vasemmistoliitto"): "#f00a64",
    ("FI", "Suomen Keskusta"): "#01954b",
    ("FI", "Suomen Sosialidemokraattinen Puolue"): "#f54b4b",
    ("FI", "Vihreä liitto"): "#61bf1a",
    ("FI", "Perussuomalaiset"): "#ffde55",
    ("FI", "Suomen ruotsalainen kansanpuolue"): "#ffdd93",
    ("GR", "ΝΕΑ ΔΗΜΟΚΡΑΤΙΑ"): "#0f62ac",
    ("GR", "ΣΥΝΑΣΠΙΣΜΟΣ ΡΙΖΟΣΠΑΣΤΙΚΗΣ ΑΡΙΣΤΕΡΑΣ-ΠΡΟΟΔΕΥΤΙΚΗ ΣΥΜΜΑΧΙΑ"): "#e2006a",
    ("GR", "ΠΑΣΟΚ - Κίνημα Αλλαγής"): "#3ea34a",
    ("GR", "ΚΟΜΜΟΥΝΙΣΤΙΚΟ ΚΟΜΜΑ ΕΛΛΑΔΑΣ"): "#ce0000",
    ("HR", "HRVATSKA DEMOKRATSKA ZAJEDNICA - HDZ"): "#0e3a91",
    (
        "HR",
        'SOCIJALDEMOKRATSKA PARTIJA HRVATSKE - SDP, "DALIJA OREŠKOVIĆ I LJUDI S IMENOM I PREZIMENOM" - "DO i SIP", CENTAR, HRVATSKA SELJAČKA STRANKA - HSS, GRAĐANSKO-LIBERALNI SAVEZ - GLAS',
    ): "#e3000f",
    ("HU", "FIDESZ-KDNP"): "#fd8204",
    ("HU", "TISZA"): "#ee3d44",
    ("HU", "DK-MSZP-Párbeszéd- ZÖLDEK"): "#0067aa",
    ("HU", "Mi Hazánk"): "#688d1b",
    ("LT", "Tėvynės sąjunga-Lietuvos krikščionys demokratai"): "#00a588",
    ("LT", "Lietuvos socialdemokratų partija"): "#e10514",
    ("LT", "Lietuvos valstiečių ir žaliųjų sąjunga"): "#00a651",
    ("LT", "Liberalų sąjūdis"): "#f48022",
    ("LU", "CSV"): "#f8a000",
    ("LU", "LSAP"): "#ff0000",
    ("LU", "DP – DEMOKRATESCH PARTEI"): "#00529c",
    ("LU", "déi gréng"): "#83c441",
    ("LV", "Jaunā VIENOTĪBA"): "#9fc54a",
    ("LV", 'Nacionālā apvienība "Visu Latvijai!"-"Tēvzemei un Brīvībai/LNNK"'): "#942525",
    ("MT", "Partit Nazzjonalista"): "#0047ab",
    ("MT", "Partit Laburista"): "#e2001a",
    ("NL", "GROENLINKS / Partij van de Arbeid (PvdA)"): "#df111a",
    ("NL", "PVV (Partij voor de Vrijheid)"): "#002759",
    ("NL", "VVD"): "#ff7709",
    ("NL", "D66"): "#01af40",
    ("NL", "CDA - Europese Volkspartij"): "#007b5f",
    ("NL", "BBB"): "#94c11f",
    ("NL", "Volt Nederland"): "#502379",
    ("NL", "Partij voor de Dieren"): "#006c2e",
    ("NL", "Staatkundig Gereformeerde Partij (SGP)"): "#f96a11",
    ("PL", "KOALICYJNY KOMITET WYBORCZY KOALICJA OBYWATELSKA"): "#f68f2d",
    ("PL", "KOMITET WYBORCZY PRAWO I SPRAWIEDLIWOŚĆ"): "#263778",
    ("PL", "KOMITET WYBORCZY KONFEDERACJA WOLNOŚĆ I NIEPODLEGŁOŚĆ"): "#122746",
    ("PL", "KOALICYJNY KOMITET WYBORCZY LEWICA"): "#ac145a",
    (
        "PL",
        "KOALICYJNY KOMITET WYBORCZY TRZECIA DROGA POLSKA 2050 SZYMONA HOŁOWNI - POLSKIE STRONNICTWO LUDOWE",
    ): "#f9c013",
    ("PT", "Partido Socialista"): "#ff66ff",
    ("PT", "AD — ALIANÇA DEMOCRÁTICA"): "#ff8c00",
    ("PT", "CHEGA"): "#202056",
    ("PT", "Iniciativa Liberal"): "#00aeef",
    ("PT", "Bloco de Esquerda"): "#c90535",
    ("PT", "CDU — Coligação Democrática Unitária"): "#e2001a",
    ("RO", "ALIANȚA ELECTORALĂ PSD PNL"): "#ed2128",
    ("RO", "ALIANȚA AUR"): "#f4c300",
    ("RO", "ALIANȚA DREAPTA UNITĂ USR - PMP - FORȚA DREPTEI"): "#00a6ff",
    ("RO", "UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA"): "#15803c",
    ("SE", "Arbetarepartiet-Socialdemokraterna"): "#e8112d",
    ("SE", "Moderaterna"): "#52bdec",
    ("SE", "Miljöpartiet de gröna"): "#83cf39",
    ("SE", "Sverigedemokraterna"): "#dddd00",
    ("SE", "Vänsterpartiet"): "#da291c",
    ("SE", "Centerpartiet"): "#009933",
    ("SE", "Kristdemokraterna"): "#000077",
    ("SE", "Liberalerna (tidigare Folkpartiet)"): "#006ab3",
    ("SI", "SLOVENSKA DEMOKRATSKA STRANKA - SDS"): "#ffdd00",
    ("SI", "GIBANJE SVOBODA"): "#00b6dd",
    ("SI", "NOVA SLOVENIJA - KRŠČANSKI DEMOKRATI"): "#0070c0",
    ("SI", "SOCIALNI DEMOKRATI - SD"): "#e2001a",
    ("SK", "Progresívne Slovensko"): "#00bdff",
    ("SK", "SMER - sociálna demokracia"): "#d82222",
}
PARTIES = {
    ("ES", "Partido Popular"): "da48502c-6236-5a46-a8dc-8fc85e82630d",
    ("ES", "Partido Socialista Obrero Español"): "2fb658c3-582f-5475-88fa-826e073a3b30",
    ("ES", "VOX"): "65512255-9d76-5e56-bf79-12dc23e156cf",
    ("ES", "Junts i Lliures per Europa"): "e1f5227e-ed9e-54a1-9142-ab18103afef5",
    ("BE", "VLAAMS BELANG"): "43e6be0f-d303-5374-95a9-5fc103983104",
    ("BE", "N-VA"): "4da8e982-1fa0-5351-8c9a-1361d02eafc0",
    ("BE", "Open Vld Renew EU"): "21652220-013a-5dfc-85f3-113ff9b05cea",
    ("BE", "PVDA"): "a3dbbbaf-0682-5824-b3d8-304f42a0350e",
    ("BE", "PTB"): "a3dbbbaf-0682-5824-b3d8-304f42a0350e",
    ("BE", "GROEN"): "fa03d96c-2a1c-5e93-b699-5c1021036fd1",
    ("BE", "cd&v"): "da8585c1-0968-5e57-83a8-124dd8b31290",
    ("BE", "MR"): "b6713794-667c-5353-8439-d50a6d47b4bb",
    ("BE", "PS"): "92a16bbc-1af4-59a1-b428-4d4019780dc3",
    ("BE", "ECOLO"): "05616f96-f761-5f53-bc1e-ca595148651b",
    ("BE", "CSP"): "a23feeb0-759d-56d3-86f0-276c9e6e067f",
    ("AT", "FPÖ"): "f9b05d7d-dc2b-557f-8296-45827fdc2111",
    ("AT", "ÖVP"): "c32688f9-f814-546d-a870-02c0c38fdfb0",
    ("AT", "SPÖ"): "2d8102f1-47a7-519e-9d2c-212b3cadbcb7",
    ("AT", "GRÜNE"): "c3e3077e-68ea-55c9-a2b1-a0a1204feab1",
    ("AT", "NEOS"): "1e889da8-cb33-5c5f-b189-aac47353d4a3",
    ("CZ", "ANO 2011"): "262ad2b5-1bf4-5487-8201-ddf8e000b556",
    ("CZ", "Česká pirátská strana"): "f97e97c0-5ec8-50e9-9849-5d3eb6609036",
    ("FR", "LA FRANCE REVIENT ! AVEC JORDAN BARDELLA ET MARINE LE PEN"): "6e3a553f-9b1f-5a04-86fe-c52913e4dd6f",
    ("FR", "BESOIN D'EUROPE"): "30193ee0-3050-5f91-aa5b-da1b19748c14",
    ("FR", "RÉVEILLER L'EUROPE"): "9fab4680-3011-5c3a-bd33-bdc6e96693aa",
    ("FR", "LA DROITE POUR FAIRE ENTENDRE LA VOIX DE LA FRANCE EN EUROPE"): "a9e91bb9-8c0e-5ad8-bc9f-2b7d6dcbee12",
    (
        "FR",
        "LA FRANCE FIERE, MENEE PAR MARION MARECHAL ET SOUTENUE PAR ÉRIC ZEMMOUR",
    ): "0c388226-b799-55a3-94a3-7196b0e8f7c4",
    ("HU", "FIDESZ-KDNP"): "bd1f0f84-9ecd-5b5b-8eb1-7a0c56c5a859",
    ("GR", "ΝΕΑ ΔΗΜΟΚΡΑΤΙΑ"): "902fab40-b8be-5a48-8b3e-7ee9d481bc46",
    ("GR", "ΠΑΣΟΚ - Κίνημα Αλλαγής"): "a27f2266-4eae-5537-8c6c-0e89b09c1b16",
    ("CY", "ΔΗΜΟΚΡΑΤΙΚΟΣ ΣΥΝΑΓΕΡΜΟΣ"): "19a45f23-1ddb-5f8b-83dc-ebfef7c062e6",
    ("CY", "ΑΚΕΛ (Α.Κ.Ε.Λ.) ΑΡΙΣΤΕΡΑ - ΚΟΙΝΩΝΙΚΗ ΣΥΜΜΑΧΙΑ"): "e7532986-78e5-5c6b-bc1c-467d2334db0e",
    ("CY", "ΔΗΜΟΚΡΑΤΙΚΟ ΚΟΜΜΑ"): "3afa9fd8-546d-522d-b2d5-710c8d6f7d1d",
    ("BG", "ГЕРБ-СДС"): "0c4b54b3-f9af-5e5d-8c1d-8f46cc136389",
    ("BG", "Движение за права и свободи – ДПС"): "b0632691-314e-5e0b-8262-87d09ab2fa13",
    ("BG", "БСП за БЪЛГАРИЯ"): "e7daa94d-7424-5897-aba1-66797a4f26f3",
    ("EE", "Eesti Reformierakond"): "d3ec8416-9e8b-5bbd-85e5-cc695105d91c",
    ("EE", "ISAMAA Erakond"): "5930d099-46c8-5066-acf8-40c4c75e2c53",
    ("EE", "Sotsiaaldemokraatlik Erakond"): "50a616f2-a170-5522-b7ab-9ae19c56e669",
    ("LT", "Tėvynės sąjunga-Lietuvos krikščionys demokratai"): "39abea74-147a-5c10-b1ca-73bcac88b9ca",
    ("LT", "Lietuvos socialdemokratų partija"): "da5f33c2-ada3-57eb-a9d5-1c6366db2102",
    ("IT", "ALLEANZA VERDI E SINISTRA"): "01a13bf7-300c-7784-acd9-f60f3ecf846c",
    ("LV", '"PROGRESĪVIE"'): "1a055a9d-089b-53a5-87bd-faa37cacf5fa",
    ("FR", "LA FRANCE INSOUMISE - UNION POPULAIRE"): "9669c097-0cc7-5768-8612-04d04e503458",
    ("FR", "EUROPE ÉCOLOGIE"): "c51441df-526d-5109-b48b-d69af65f7dd7",
    ("HR", "HRVATSKA DEMOKRATSKA ZAJEDNICA - HDZ"): "3f363d1b-38dd-5ecd-88f0-44d553996fd3",
    ("IT", "FRATELLI D'ITALIA"): "01a13bf7-300c-7784-acd9-f60922c39888",
    ("IT", "PARTITO DEMOCRATICO"): "e2dd275d-8eff-5620-8364-588dd9a80a10",
    ("IT", "MOVIMENTO 5 STELLE"): "01a13bf7-300c-7784-acd9-f60b9930672c",
    ("IT", "LEGA SALVINI PREMIER"): "01a13bf7-300c-7784-acd9-f60cef597cca",
    ("IT", "FORZA ITALIA - NOI MODERATI - PPE"): "a9493fc6-1e51-5c1f-8082-629d9d26ba4d",
    ("IT", "SUDTIROLER VOLKSPARTEI (SVP)"): "64e4d6a9-f3a2-5eda-bd8f-59803a21612e",
    ("LV", 'Nacionālā apvienība "Visu Latvijai!"-"Tēvzemei un Brīvībai/LNNK"'): "ebe3c5cd-7d65-578a-8647-36cb90071375",
    ("LV", '"Saskaņa" sociāldemokrātiskā partija'): "ffe0265a-339a-5e8d-86b3-e4bf8fc98364",
    ("LV", "Zaļo un Zemnieku savienība"): "2a9cc172-a41f-59de-b80c-4bec79b50d54",
    ("NL", "PVV (Partij voor de Vrijheid)"): "7ba1e1ba-9921-5ad9-bdc0-b7037d76c5c9",
    ("NL", "VVD"): "eddc59c6-1865-57a2-a3b3-8f3f01b59a6a",
    ("NL", "CDA - Europese Volkspartij"): "ea056f9f-78f8-588d-b327-2ce01df86882",
    ("NL", "D66"): "c5c0dfd7-8424-5e05-bafd-f7df45431ff7",
    ("NL", "Partij voor de Dieren"): "822dc25c-e90b-551c-9787-0559d33c8309",
    ("NL", "Forum voor Democratie"): "ff26f82a-31c7-5503-94f9-50164ca2425c",
    ("NL", "SP (Socialistische Partij)"): "d1fe8817-9318-54d5-90ea-1b0739d3fb66",
    ("PL", "KOMITET WYBORCZY PRAWO I SPRAWIEDLIWOŚĆ"): "5ac70265-719b-51a9-8e31-69a1d6e541c7",
    ("SK", "SMER - sociálna demokracia"): "b64c8320-810f-5e8b-b647-b55723e0362c",
    ("SK", "Kresťanskodemokratické hnutie"): "31f2b5fd-86b8-52e3-930b-0edd1fde5f19",
    ("SK", "Sloboda a Solidarita"): "999724ab-e3d7-5faa-9840-60de2c452bb0",
}


def fold(text: str) -> str:
    return re.sub(
        r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    ).strip("-")


def emit() -> Path:
    lists = pl.read_parquet(PARSED / "lists.parquet")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The 2024 European Parliament election in each member state from its electoral authority's final "
            "results: votes and seats for every list, with registered voters and ballots",
            polity="EU",
        )
        umbrella = cs.key("eu.election", "2024-06-06:european-parliament")
        parliament = cs.key("eu.body", "european-parliament")
        if {umbrella, parliament} & cs.minted:
            raise ValueError("publish eu.eurlex before the 2024 member elections")
        known = {
            str(id)
            for (id,) in conn.execute(
                "select id from party where id = any(%s::uuid[])", [list(PARTIES.values())]
            ).fetchall()
        }
        if missing := set(PARTIES.values()) - known:
            raise ValueError(f"unknown parties {missing}")
        merged = dict(conn.execute("select entity_id::text, into_id::text from entity_merge").fetchall())
        published = dict(
            conn.execute(
                """select x.value, x.entity_id::text from external_id x where x.scheme = 'eu.ep2024.party'
                   and not exists (select from entity_merge m where m.entity_id = x.entity_id)"""
            ).fetchall()
        )

        def name(entity, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=preferred,
                valid_from=None,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
                source_id=None,
            )

        def sheet(name: str) -> list[dict[str, str]]:
            return list(
                csv.DictReader(
                    io.StringIO(raw.cached(SOURCE, f"{EP}/{name}.csv").path.read_text(encoding="utf-8-sig")),
                    delimiter=";",
                )
            )

        def norm(text: str | None) -> str:
            text = "".join(
                c for c in unicodedata.normalize("NFKD", (text or "").replace(".", "")) if not unicodedata.combining(c)
            )
            return " " + re.sub(r"[\W_]+", " ", text.casefold()).strip() + " "

        ep_title = "European elections 2024: results data sheets"
        ep_source = cs.source("European Parliament", ep_title)
        cs.add(
            "source",
            id=ep_source,
            kind="official_body",
            publisher="European Parliament",
            title=ep_title,
            url="https://results.elections.europa.eu/en/data-sheets/",
            license="European Parliament legal notice: reuse is authorized provided the source is acknowledged",
            license_url="https://www.europarl.europa.eu/legal-notice/en/",
            attribution="© European Union – Source: European Parliament",
        )
        for check in CHECKS:
            cs.artifact(raw.cached(SOURCE, f"{EP}/{check}.csv"), ep_source)
        ep_parties = {row["ID"]: row for row in sheet("parties")}
        groups = {key: cs.key("ep.group", key) for key in GROUPS.values()}
        for code, key in GROUPS.items():
            if groups[key] in cs.minted:
                cs.add("party", id=groups[key], polity_id=cs.polity("EU"), kind="parliamentary_group")
                english = next(row for row in sheet("groups") if row["ID"] == code and row["LANGUAGE_ID"] == "EN")
                for kind, text in (("official", english["LABEL"]), ("abbreviation", english["ACRONYM"])):
                    cs.add(
                        "name",
                        entity_id=groups[key],
                        text=text.strip(),
                        lang="en",
                        kind=kind,
                        is_preferred=kind == "official",
                        valid_from=date(2024, 7, 16),
                        valid_from_precision="day",
                        valid_to=None,
                        valid_to_precision="day",
                        source_id=ep_source,
                    )
        held = {
            party
            for (party,) in conn.execute(
                "select distinct a.party_id::text from party_affiliation a join changeset c on c.id = a.changeset_id"
                " where a.kind = 'ep_group' and c.pipeline <> %s and (a.valid_to is null or a.valid_to > '2024-06-06')",
                [SOURCE],
            ).fetchall()
        }
        blocs: dict[UUID, Counter[str]] = defaultdict(Counter)
        painted: set[tuple[str, str]] = set()
        frames = []
        located: list[pl.DataFrame] = []
        for iso, (publisher, title, url, license, start, end) in AUTHORITIES.items():
            if iso not in COUNTRIES or lists.filter(pl.col("iso") == iso).height == 0:
                continue
            source = cs.source(publisher, title)
            cs.add(
                "source", id=source, kind="official_body", publisher=publisher, title=title, url=url, license=license
            )
            artifacts = {
                artifact_url: cs.artifact(raw.cached(SOURCE, artifact_url), source)
                for artifact_url in [FILES.get(iso), PAGES.get(iso), ELECTED.get(iso), DECLARED.get(iso, (None,))[0]]
                if artifact_url is not None
            }
            if iso == "MT":
                artifacts[f"{EP}/seats-breakdown-parties/mt.csv"] = cs.artifact(
                    raw.cached(SOURCE, f"{EP}/seats-breakdown-parties/mt.csv"), source
                )
            pages: dict[int, str] = {}
            if iso == "RO":
                manifest = scans.load("ro-ep-2024")
                for spec in manifest["pages"]:
                    image = scans.PAGES / f"{raw.cached(SOURCE, FILES['RO']).sha256}-{spec['page']}.png"
                    if not image.exists():
                        image.parent.mkdir(parents=True, exist_ok=True)
                        scans.render(manifest, spec, 100).save(image)
                    width, height = Image.open(image).size
                    page = cs.page(artifacts[FILES["RO"]], spec["page"])
                    pages[spec["page"]] = str(page)
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifacts[FILES["RO"]],
                        page_number=spec["page"],
                        image_key=f"pages/{scans.SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
            polity = cs.polity(iso)
            election = cs.key("eu.election", f"{iso}:2024:european-parliament")
            path = f"{iso.lower()}/{start}-european-parliament"
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=umbrella,
                kind="general",
                date_from=start,
                date_to=end,
                date_precision="day",
                original_text=None,
                status="held",
            )
            name(election, f"2024 European Parliament election in {COUNTRIES[iso]}")
            cs.add("slug", path=path, entity_id=election)
            area = cs.area("eu.member_state", iso)
            if area in cs.minted:
                raise ValueError(f"no eu.member_state area for {iso}")
            unit = cs.unit("eu.member_state", iso)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="eu.member_state",
                    code=iso,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=date(1979, 6, 7),
                )
            for (college,), rows in (
                lists.filter(pl.col("iso") == iso).sort("position").group_by(["college"], maintain_order=True)
            ):
                label = dict(COLLEGES.values()).get(college)
                contest = cs.key("eu.contest", f"{iso}:2024:eu.member_state:{iso}" + (f":{college}" if college else ""))
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=parliament,
                    district_area_id=area,
                    magnitude=int(rows["seats"].sum()),
                    electoral_system_id=None,
                    default_result_kind="counted",
                )
                name(
                    contest, f"2024 European Parliament election in {COUNTRIES[iso]}" + (f", {label}" if label else "")
                )
                cs.add("slug", path=f"{path}/{fold(label) if label else 'european-parliament'}", entity_id=contest)
                result_set = cs.key("eu.ep2024.result_set", f"{iso}:2024" + (f":{college}" if college else ""))
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                if iso in NOTES:
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTES[iso])
                declared = sheet(
                    f"seats-breakdown-parties/{ ({'GR': 'el'}.get(iso, iso.lower()) if not college else college) }"
                )
                units: dict[str, tuple[int, Counter[str], set[str]]] = {}
                for entry in declared:
                    info = ep_parties.get(entry["PARTY_ID"], {})
                    if info.get("MEMBER_OF_COALITION"):
                        continue
                    members = [entry["PARTY_ID"], *(info.get("COALITION_MEMBERS") or "").split("|")]
                    tally: Counter[str] = Counter()
                    texts = set()
                    for other in declared:
                        if other["PARTY_ID"] in members and other["GROUP_ID"]:
                            tally[other["GROUP_ID"]] += int(other["SEATS_TOTAL"])
                    for member in members:
                        for field in ("ACRONYM", "LABEL"):
                            for part in (
                                (ep_parties.get(member, {}).get(field) or "").removeprefix("Coalition ").split("/")
                            ):
                                if len(norm(part.split("(")[0]).strip()) >= 2:
                                    texts.add(norm(part.split("(")[0]))
                    units[entry["PARTY_ID"]] = (
                        sum(int(other["SEATS_TOTAL"]) for other in declared if other["PARTY_ID"] == entry["PARTY_ID"]),
                        tally,
                        texts,
                    )
                seated = [row for row in rows.iter_rows(named=True) if row["seats"]]
                scored = []
                for row in seated:
                    label = norm(row["label"])
                    for key, (seats, _, texts) in units.items():
                        text = max((len(t) for t in texts if t in label or label in t), default=0)
                        if text:
                            scored.append((text + 1000 * (seats == row["seats"]), row["position"], key))
                matched: dict[int, str] = {}
                for _, position, key in sorted(scored, reverse=True):
                    if position not in matched and key not in matched.values():
                        matched[position] = key
                for row in seated:
                    free = [
                        key for key, unit in units.items() if key not in matched.values() and unit[0] == row["seats"]
                    ]
                    rivals = [
                        other for other in seated if other["position"] not in matched and other["seats"] == row["seats"]
                    ]
                    if row["position"] not in matched and len(free) == 1 and len(rivals) == 1:
                        matched[row["position"]] = free[0]
                values = []
                options: dict[int, str] = {}
                for row in rows.iter_rows(named=True):
                    mapped = PARTIES.get((iso, row["label"]))
                    party = (
                        cs.key("eu.ep2024.party", f"{iso}:{row['label']}")
                        if mapped is None
                        else merged.get(mapped, mapped)
                    )
                    proper = NAMES.get((iso, row["label"]))
                    if mapped is None and party in cs.minted:
                        cs.add("party", id=party, polity_id=polity)
                    if mapped is None and (party in cs.minted or proper is not None):
                        name(party, proper or row["label"], "und", "official", True)
                    if mapped is None and (iso, row["label"]) in SHORTS:
                        name(party, SHORTS[iso, row["label"]], "und", "abbreviation", False)
                    if (iso, row["label"]) in COLORS:
                        cs.add("party_color", party_id=party, color=COLORS[iso, row["label"]], valid_from=None)
                        painted.add((iso, row["label"]))
                    if mapped is not None and (old := published.get(f"{iso}:{row['label']}")) is not None:
                        cs.merge(UUID(old), UUID(str(party)))
                    option = cs.key(
                        "eu.ep2024.option",
                        f"{iso}:2024:{college + ':' if college else ''}{row['position']}:{fold(row['label'])}",
                    )
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party,
                        ballot_position=row["position"],
                        ballot_label=row["label"],
                    )
                    if row["seats"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row["seats"])
                        if row["position"] in matched and str(party) not in held:
                            blocs[party].update(units[matched[row["position"]]][1])
                    options[row["position"]] = str(option)
                    values += [(str(option), 1, row["votes"]), (str(option), 16, row["seats"])]
                for accounting in totals.filter((pl.col("iso") == iso) & (pl.col("college") == college)).iter_rows(
                    named=True
                ):
                    for measure, key in ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"), (6, "blank")):
                        if accounting[key] is not None:
                            values.append((None, measure, accounting[key]))
                frames.append(
                    pl.DataFrame(
                        {
                            "result_set_id": [str(result_set)] * len(values),
                            "unit_id": [str(unit)] * len(values),
                            "option_id": [value[0] for value in values],
                            "channel": [0] * len(values),
                            "measure": [value[1] for value in values],
                            "count_round": [0] * len(values),
                            "flags": [0] * len(values),
                            "value": [float(value[2]) for value in values],
                        },
                        schema={
                            "result_set_id": pl.String,
                            "unit_id": pl.String,
                            "option_id": pl.String,
                            "channel": pl.Int16,
                            "measure": pl.Int16,
                            "count_round": pl.Int16,
                            "flags": pl.Int16,
                            "value": pl.Float64,
                        },
                    )
                )
                if pages:
                    located.append(
                        pl.read_parquet(PARSED / "locators.parquet").select(
                            result_set_id=pl.lit(str(result_set)),
                            unit_id=pl.lit(str(unit)),
                            option_id=pl.col("position").replace_strict(options, default=None, return_dtype=pl.String),
                            channel=pl.lit(0, pl.Int16),
                            measure=pl.col("measure").cast(pl.Int16),
                            count_round=pl.lit(0, pl.Int16),
                            page_id=pl.col("page").replace_strict(pages, return_dtype=pl.String),
                            source_id=pl.lit(None, pl.String),
                            x0="x0",
                            y0="y0",
                            x1="x1",
                            y1="y1",
                        )
                    )
        for party, tally in blocs.items():
            if len(tally) == 1:
                cs.add(
                    "party_affiliation",
                    party_id=party,
                    parent_party_id=groups[GROUPS[next(iter(tally))]],
                    kind="ep_group",
                    valid_from=date(2024, 6, 6),
                    valid_from_precision="unknown",
                    valid_to=None,
                    valid_to_precision="unknown",
                )
        print(
            f"ep_group affiliations: {sum(1 for tally in blocs.values() if len(tally) == 1)};"
            f" split: {[(str(party), dict(tally)) for party, tally in blocs.items() if len(tally) != 1]}"
        )
        if unpainted := set(COLORS) - painted:
            raise ValueError(f"COLORS keys match no list: {sorted(unpainted)}")
        cs.frame("result_value", pl.concat(frames))
        if located:
            cs.frame("value_locator", pl.concat(located))
        return cs.write()
