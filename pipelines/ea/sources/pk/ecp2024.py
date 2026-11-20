"""Pakistan's general election of 8 February 2024, by constituency, from the Election Commission's scanned Forms 49.

The Commission's page for the election links one shared folder per province holding the scanned result forms of each
constituency: the result of the count at each polling station (Form 45), the ballot paper account (Form 46), the
consolidated statement of the returning officer (Form 48), and the final consolidated result (Form 49). Each Form 49
is typed in English and names the constituency, its registered voters by sex, each contesting candidate with their
party and valid votes, and the valid and invalid votes in all.

The scans carry no usable text layer, so each form was read twice. A multimodal model transcribed every page image;
the transcription is `ecp2024.json`. Tesseract read each page again at 300 dpi, and its words give the box of every
figure. The candidates' votes of each transcribed form must sum to the valid votes it prints; forms that did not were
read again from zoomed crops. A form whose printed figures still disagree after that second reading is stored as
printed, with a caveat, and a form whose figures could not be read with certainty is left out."""

import csv
import html
import io
import json
import os
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl
from PIL import Image

from ea import package, raw
from ea.package import Package
from ea.raw import Artifact
from ea.sources.pk import ecp

SOURCE = "pk.ecp"
LISTING = "https://drive.google.com/embeddedfolderview?id={}"
DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download"
PAGE = "https://web.archive.org/web/20241219093147/https://ecp.gov.pk/general-elections-2024"
# The shared folders of Forms 45, 46, 48, and 49 that the Commission's page links, by province.
FOLDERS = {
    "Punjab": "1HcoKAkH-0RZDdIKvQfTmlpFqYQOr-WsT",
    "Sindh": "14X4qrGjOUUVti-R1u0Tq95KEkogDiqUt",
    "Khyber Pakhtunkhwa": "1tCQNFIyI5sI72IE5UbDC2RRmD9MVlEKc",
    "Balochistan": "1VcMHcyaDN1rn3AD4_7NbIX77L2ESGhWz",
}
ENTRY = re.compile(
    r'<div class="flip-entry" id="entry-([^"]+)".*?<a href="([^"]+)".*?<div class="flip-entry-title">(.*?)</div>',
    re.DOTALL,
)
FORM_49 = re.compile(r"form[-_ ]?49", re.IGNORECASE)
SEAT = re.compile(r"\b(NA|PP|PS|PK|PB)[\s_.-]*0*(\d+)", re.IGNORECASE)
SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}
# Drive files in the folders that hold nothing: PS-104's Form 49.
EMPTY = {"1fx-Zpj0EyTgt2QzuOo-EfppaJbHh7h0X"}
FORMS: dict[str, dict] = json.loads((Path(__file__).parent / "ecp2024.json").read_text())
# Constituencies read from another form than the Form 49: the grand-total row of the returning officer's Form 48, or the
# provisional Form 47, by seat code, with the Drive file, the figures, and the candidates' votes in column order.
TOTALS_ONLY: dict[str, dict] = json.loads((Path(__file__).parent / "ecp2024_totals.json").read_text())


def entries(folder: str) -> list[tuple[str, bool, str]]:
    """The id, whether it is a folder, and the title of each entry of a cached Drive folder listing."""
    text = raw.cached(SOURCE, LISTING.format(folder)).path.read_text()
    return [(id, "/folders/" in href, html.unescape(title)) for id, href, title in ENTRY.findall(text)]


def _not_html(path: Path) -> None:
    if path.stat().st_size == 0:
        raise ValueError("the host returned an empty file")
    if path.read_bytes()[:64].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("the host returned a web page, not the scan")


def crawl(fetching: bool) -> list[tuple[str, str, str]]:
    """Every Form 49 file in the provincial folders: its seat code, its Drive id, and its path of folder titles. The
    folders of later by-elections are skipped."""
    found: list[tuple[str, str, str]] = []

    def walk(folder: str, path: str, inside: bool) -> None:
        if fetching:
            raw.fetch(SOURCE, LISTING.format(folder), suffix=".html")
        for id, is_folder, title in entries(folder):
            here = f"{path}/{title}"
            if "by election" in title.lower() or "bye election" in title.lower():
                continue
            if is_folder:
                if inside or FORM_49.search(title):
                    walk(id, here, True)
            elif inside and Path(title.lower()).suffix in SUFFIXES and id not in EMPTY:
                seat = next((m for part in reversed(here.split("/")) if (m := SEAT.search(part))), None)
                if seat is None:
                    raise ValueError(f"no constituency in the path {here}")
                found.append((f"{seat.group(1).upper()}-{int(seat.group(2))}", id, here))

    for province, folder in FOLDERS.items():
        walk(folder, province, False)
    return found


def fetch() -> None:
    raw.cached(SOURCE, PAGE)
    for _, id, path in crawl(True):
        raw.fetch(SOURCE, DRIVE.format(id), suffix=Path(path.lower()).suffix, check=_not_html)
    for entry in TOTALS_ONLY.values():
        raw.fetch(SOURCE, DRIVE.format(entry["id"]), suffix=".pdf", check=_not_html)


PAGES = raw.DATA / "pages" / SOURCE
CONFUSABLE = str.maketrans({"l": "1", "I": "1", "i": "1", "O": "0", "o": "0", "S": "5", "s": "5", "§": "5", "B": "8"})
NUMBER = re.compile(r"\d[\d,]*")
FILLER = re.compile(r"^[|{}\[\]()_\-—–~=:;.,'‘’\"“”*!]+$")


class Word(NamedTuple):
    text: str
    box: tuple[int, int, int, int]
    line: tuple[int, int, int]


class Row(NamedTuple):
    serial: int | None
    name: str
    party: str
    votes: int
    page: int
    box: tuple[float, float, float, float]


class Read(NamedTuple):
    """What Tesseract reads from one constituency's Form 49."""

    code: str
    name: str
    figures: dict[str, tuple[int, int, tuple[float, float, float, float]]]  # field: value, page, box
    rows: list[Row]


def page_count(artifact: Artifact) -> int:
    if artifact.path.suffix != ".pdf":
        return 1
    info = subprocess.run(["pdfinfo", str(artifact.path)], capture_output=True, text=True, check=True).stdout
    found = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    if found is None:
        raise ValueError(f"pdfinfo prints no page count for {artifact.path}")
    return int(found.group(1))


def render(artifact: Artifact, page: int) -> tuple[Path, int, int]:
    """The page image at 150 dpi, a PNG named for the file's hash and page number, and its size."""
    image = PAGES / f"{artifact.sha256}-{page}.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        if artifact.path.suffix == ".pdf":
            with tempfile.TemporaryDirectory() as folder:
                subprocess.run(
                    ["pdftoppm", "-r", "150", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                    + [str(artifact.path), f"{folder}/p"],
                    check=True,
                )
                Image.open(f"{folder}/p.png").convert("RGB").save(image)
        else:
            picture = Image.open(artifact.path).convert("RGB")
            picture.thumbnail((1240, 1754))
            picture.save(image)
    width, height = Image.open(image).size
    return image, width, height


def words(artifact: Artifact, page: int) -> tuple[list[Word], int, int]:
    """Tesseract's words on a page read at 300 dpi, with the size of the image they were read from. The reading is
    cached beside the page images, since it is derived from the raw file alone."""
    cache = PAGES / f"{artifact.sha256}-{page}.json"
    if not cache.exists():
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "p.png"
            if artifact.path.suffix == ".pdf":
                subprocess.run(
                    ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                    + [str(artifact.path), str(image.with_suffix(""))],
                    check=True,
                )
            else:
                picture = Image.open(artifact.path).convert("L")
                scale = 3508 / max(picture.size)
                picture.resize((round(picture.width * scale), round(picture.height * scale))).save(image)
            width, height = Image.open(image).size
            tsv = subprocess.run(
                ["tesseract", str(image), "stdout", "--psm", "4", "-l", "eng", "tsv"],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                timeout=600,
            ).stdout
        PAGES.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"width": width, "height": height, "tsv": tsv}))
    data = json.loads(cache.read_text())
    found = [
        Word(
            row["text"].strip(),
            (
                int(row["left"]),
                int(row["top"]),
                int(row["left"]) + int(row["width"]),
                int(row["top"]) + int(row["height"]),
            ),
            (int(row["block_num"]), int(row["par_num"]), int(row["line_num"])),
        )
        for row in csv.DictReader(io.StringIO(data["tsv"]), delimiter="\t", quoting=csv.QUOTE_NONE)
        if row["level"] == "5" and row["text"].strip()
    ]
    return found, data["width"], data["height"]


def number(text: str) -> int | None:
    """A count as printed, with the letters Tesseract reads for digits put right; None when it is not one."""
    text = text.strip("|{}[]()_-—–~=:;.'‘’\"“”*!").translate(CONFUSABLE).replace(",", "")
    return int(text) if text.isdigit() else None


SEAT_NAME = re.compile(r"constituency\W*(NA|PP|PS|PK|PB)\s*[-.]?\s*0*(\d+)\W*(.*)$", re.IGNORECASE)
VOTERS = re.compile(r"Male\W*([\dOoIl,]+)\W*Female\W*([\dOoIl,]+)\W*Total\W*([\dOoIl,]+)", re.IGNORECASE)
TOTALS = {
    "invalid": re.compile(r"invalid\s+votes\W*([\dOoIl,]+)", re.IGNORECASE),
    "valid": re.compile(r"(?<!in)valid\s+votes\s+polled\W*([\dOoIl,]+)", re.IGNORECASE),
}


def joined(picked: list[Word]) -> str:
    return " ".join(w.text for w in picked).strip("|{}[]_—-~:;‘’'\" ").strip()


def read_form(artifact: Artifact) -> Read:
    """Read one constituency's Form 49 from Tesseract's words: the seat, the registered voters, each candidate's row,
    and the valid and invalid votes in all."""
    code, name = "", ""
    figures: dict[str, tuple[int, int, tuple[float, float, float, float]]] = {}
    rows: list[Row] = []
    for page in range(1, page_count(artifact) + 1):
        found, width, height = words(artifact, page)

        def box(*picked: Word) -> tuple[float, float, float, float]:
            return (
                round(min(w.box[0] for w in picked) / width, 4),
                round(min(w.box[1] for w in picked) / height, 4),
                round(max(w.box[2] for w in picked) / width, 4),
                round(max(w.box[3] for w in picked) / height, 4),
            )

        lines: dict[tuple[int, int, int], list[Word]] = {}
        for word in found:
            lines.setdefault(word.line, []).append(word)
        ordered = sorted((sorted(line, key=lambda w: w.box[0]) for line in lines.values()), key=lambda l: l[0].box[1])
        independents = sorted(w.box[0] for w in found if re.fullmatch(r"\W*[Ii1l]?nd[eo]pend[eo]nt\W*", w.text))
        party_x = independents[len(independents) // 2] if independents else None
        tops = [
            line[0].box[1]
            for line in ordered
            if re.search(r"contesting|candidate", " ".join(w.text for w in line), re.IGNORECASE)
        ]
        ends = [line[0].box[1] for line in ordered if TOTALS["valid"].search(" ".join(w.text for w in line))]
        top, end = (tops[0] if tops else 0), (ends[0] if ends else height)
        for line in ordered:
            text = " ".join(w.text for w in line)

            def at(value: str, line: list[Word] = line) -> list[Word]:
                return [w for w in line if value in w.text.translate(CONFUSABLE).replace(",", "")]

            if (match := SEAT_NAME.search(text)) and not code:
                code, name = f"{match.group(1).upper()}-{int(match.group(2))}", match.group(3).strip()
                continue
            if match := VOTERS.search(text):
                for field, raw_value in zip(("male", "female", "registered"), match.groups(), strict=True):
                    value = number(raw_value)
                    hits = at(str(value))
                    if value is not None and hits:
                        figures[field] = (value, page, box(hits[-1]))
                continue
            matched = False
            for field, pattern in TOTALS.items():
                if match := pattern.search(text):
                    value = number(match.group(1))
                    hits = at(str(value))
                    if value is not None and hits:
                        figures[field] = (value, page, box(hits[-1]))
                    matched = True
            if matched or not top < line[0].box[1] < end:
                continue
            tokens = [w for w in line if not FILLER.match(w.text)]
            if len(tokens) < 2:
                continue
            serial, votes = number(tokens[0].text), number(tokens[-1].text)
            middle = tokens[1:-1] if serial is not None else tokens[:-1]
            if votes is None or not any(re.search(r"[A-Za-z]{2}", w.text) for w in middle):
                continue
            if party_x is not None:
                split = next((i for i, w in enumerate(middle) if w.box[0] >= party_x - 20), len(middle))
            else:
                gaps = [middle[i + 1].box[0] - middle[i].box[2] for i in range(len(middle) - 1)]
                split = gaps.index(max(gaps)) + 1 if gaps else len(middle)
            rows.append(Row(serial, joined(middle[:split]), joined(middle[split:]), votes, page, box(tokens[-1])))
    return Read(code, name, figures, rows)


class Candidate(NamedTuple):
    serial: int
    name: str
    party: str
    votes: int
    page: int
    box: tuple[float, float, float, float]


class Form(NamedTuple):
    """One constituency's Form 49 as stored: its file, the figures it prints with their pages and boxes, its
    candidates, and how many of the candidates' votes Tesseract read the same as the transcription."""

    code: str
    name: str
    url: str
    figures: dict[str, tuple[int, int, tuple[float, float, float, float]]]
    candidates: list[Candidate]
    agreed: int
    notes: str
    stage: str = "official"
    kind: str = "49"


def squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def party(text: str) -> str:
    """A party's name as the Commission registers it, from the name a form prints; some forms print the election
    symbol after a slash."""
    text = text.split("/")[0]
    if squash(text) in ("independent", "ind", ""):
        return "Independent"
    name = PARTY_NAMES.get(squash(text))
    if name is None:
        raise ValueError(f"unknown party {text!r}")
    return name


def files() -> dict[str, str]:
    """The Drive download URL of each constituency's Form 49, by its seat code."""
    found: dict[str, str] = {}
    for code, id, path in crawl(False):
        if code in found:
            raise ValueError(f"two Forms 49 for {code}: {path}")
        found[code] = DRIVE.format(id)
    return found


type Box = tuple[float, float, float, float]


def estimate(known: dict[int, tuple[int, Box]], serial: int) -> tuple[int, Box]:
    """The page and box of a candidate's votes that Tesseract did not find, from the nearest rows it found on the
    same page, one row height apart."""
    if not known:
        return 1, WHOLE
    nearest = sorted(known, key=lambda s: (abs(s - serial), s))
    page, box = known[nearest[0]]
    same = [s for s in nearest[1:] if known[s][0] == page]
    if same:
        other = known[same[0]][1]
        step = (box[1] - other[1]) / (nearest[0] - same[0])
    else:
        step = box[3] - box[1]
    shift = step * (serial - nearest[0])
    return page, (box[0], round(box[1] + shift, 4), box[2], round(box[3] + shift, 4))


def boxes(read: Read, votes: list[int]) -> tuple[list[tuple[int, Box]], int]:
    """The page and box of each candidate's votes: the box of the number Tesseract read in that candidate's row,
    found by matching the rows Tesseract read to the transcribed rows in order, and the count of rows whose votes
    Tesseract read the same as the transcription."""
    rows = read.rows
    known: dict[int, tuple[int, Box]] = {}
    agreed = 0
    # Rows Tesseract read with the transcribed votes, matched in order.
    cursor = 0
    for index, value in enumerate(votes):
        for at in range(cursor, len(rows)):
            if rows[at].votes == value and (rows[at].serial in (None, index + 1) or rows[at].votes >= 10):
                known[index + 1] = (rows[at].page, rows[at].box)
                agreed += 1
                cursor = at + 1
                break
    # Rows Tesseract read with its own serial but other votes still mark the candidate's cell.
    for row in rows:
        if row.serial is not None and 0 < row.serial <= len(votes) and row.serial not in known:
            known[row.serial] = (row.page, row.box)
    placed = [known.get(serial) or estimate(known, serial) for serial in range(1, len(votes) + 1)]
    return placed, agreed


def form(code: str, url: str) -> Form:
    """One constituency's Form 49: the figures transcribed in `ecp2024.json`, each with the page and box Tesseract
    found it in, checked against the totals the form prints."""
    artifact = raw.cached(SOURCE, url)
    read = read_form(artifact)
    entry = FORMS[code]
    figures: dict[str, tuple[int, int, Box]] = {}
    for field in ("registered", "valid", "invalid"):
        value = entry.get(field)
        if value is None:
            continue
        if field in read.figures:
            figures[field] = (value, read.figures[field][1], read.figures[field][2])
        else:
            figures[field] = (value, 1 if field == "registered" else page_count(artifact), WHOLE)
    printed = entry["candidates"]
    placed, agreed = boxes(read, [row[3] for row in printed])
    candidates = [
        Candidate(serial, tidy(name), party(text), votes, page, box)
        for (serial, name, text, votes), (page, box) in zip(printed, placed, strict=True)
    ]
    checked = Form(code, place(entry["name"]), url, figures, candidates, agreed, entry.get("notes", ""))
    problems = check(checked, entry)
    if problems:
        raise ValueError(f"{code}: {'; '.join(problems)}")
    return checked


def totals_form(code: str) -> Form:
    """One constituency's result from the typed grand-total row of its Form 48, or from its Form 47, as transcribed in
    `ecp2024_totals.json`. Each figure is stored with the page it is on and the whole page as its box. Neither form
    prints the candidates' parties on its grand-total row, and a Form 48 prints no registered voters."""
    entry = TOTALS_ONLY[code]
    url = DRIVE.format(entry["id"])
    artifact = raw.cached(SOURCE, url)
    last = page_count(artifact)
    split = entry.get("split")
    candidates = [
        Candidate(
            serial,
            tidy(name),
            text if text in PARTY_NAMES.values() or not text else party(text),
            votes,
            1 if split and serial <= split else last,
            WHOLE,
        )
        for serial, name, text, votes in entry["candidates"]
    ]
    first = 1 if entry["form"] == "47" else last
    figures: dict[str, tuple[int, int, Box]] = {
        field: (entry[field], first, WHOLE) for field in ("registered", "valid", "invalid") if field in entry
    }
    checked = Form(
        code, place(entry["name"]), url, figures, candidates, len(candidates), "", "preliminary", entry["form"]
    )
    problems = check(checked, entry)
    if problems:
        raise ValueError(f"{code}: {'; '.join(problems)}")
    return checked


# The box of a whole page, for a figure on a scan Tesseract finds no line of (one rotated or upside down).
WHOLE: Box = (0.0, 0.0, 1.0, 1.0)


def check(f: Form, entry: dict) -> list[str]:
    problems: list[str] = []
    serials = [c.serial for c in f.candidates]
    if serials != list(range(1, len(serials) + 1)):
        problems.append(f"serials {serials}")
    for field in ("valid", "invalid"):
        if field not in f.figures and "missing" not in ACCOUNTED.get(f.code, {}):
            problems.append(f"no {field}")
    votes = sum(c.votes for c in f.candidates)
    if "valid" in f.figures and votes != f.figures["valid"][0] and "votes" not in ACCOUNTED.get(f.code, {}):
        problems.append(f"votes sum to {votes}, valid {f.figures['valid'][0]}")
    male, female = entry.get("male"), entry.get("female")
    registered = f.figures.get("registered")
    differs = male is not None and female is not None and registered is not None and male + female != registered[0]
    if differs and "registered" not in ACCOUNTED.get(f.code, {}):
        problems.append("male and female voters do not sum to the registered voters")
    return problems


def tidy(name: str) -> str:
    """A candidate's name as printed, with its spaces collapsed."""
    return " ".join(name.replace("\u00a0", " ").split()).strip(" .,:;")


def place(name: str) -> str:
    """A constituency's printed name in capitals and small letters, with its roman numerals kept."""

    def word(match: re.Match[str]) -> str:
        text = match.group(0)
        if text.upper() in ROMAN:
            return text.upper()
        return text.lower() if text.lower() == "cum" else text.capitalize()

    text = re.sub(r"\s*-\s*", "-", " ".join(name.strip(" .:;").split()))
    return re.sub(r"[A-Za-z]+", word, text)


ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII"}
ROMAN |= {"XVIII", "XIX", "XX", "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX"}
# The registered name of each party, by the squashed spellings the forms print for it.
PARTY_NAMES: dict[str, str] = {
    "aamadmitehreekpakistan": "Aam Admi Tehreek Pakistan",
    "aamloegittehad": "Aam Loeg Ittehad",
    "aamlogpartypakistan": "Aam Log Party Pakistan",
    "allpakistankissanittehad": "All Pakistan Kissan Ittehad",
    "allpakistanminoritymovement": "All Pakistan Minority Movement",
    "allpakistanmuslimleaguejinnah": "All Pakistan Muslim League (Jinnah)",
    "amlp": "Awami Muslim League Pakistan",
    "amuntaraqqiparty": "Amun Taraqqi Party",
    "anp": "Awami National Party",
    "apnipartypakistan": "Apni Party Pakistan",
    "awamidemocraticpartypakistan": "Awami Democratic Party Pakistan",
    "awamifalahiparty": "Awami Falahi Party",
    "awamijusticepartypakistan": "Awami Justice Party Pakistan",
    "awamimuslimleaguepakistan": "Awami Muslim League Pakistan",
    "awaminationalparty": "Awami National Party",
    "awamitehreek": "Awami Tehreek",
    "awamitehreekeilamparty": "Awami Tehreek-e-Ilam Party",
    "awamiworkersparty": "Awami Workers Party",
    "awamleague": "Awam League",
    "awamlogpartypakistan": "Awam Log Party Pakistan",
    "balochistanawamiparty": "Balochistan Awami Party",
    "balochistannationalparty": "Balochistan National Party",
    "balochistannationalpartyawami": "Balochistan National Party (Awami)",
    "barabripartypakistan": "Barabri Party Pakistan",
    "bnp": "Balochistan National Party",
    "frontnationalpakistan": "Front National (Pakistan)",
    "granddemocraticalliance": "Grand Democratic Alliance",
    "greendemocraticparty": "Green Democratic Party",
    "haqdotehreekbalochistan": "Haq Do Tehreek Balochistan",
    "haqooqkhalqparty": "Haqooq Khalq Party",
    "hazarademocraticparty": "Hazara Democratic Party",
    "hazaraqaumimahazpakistan": "Hazara Qaumi Mahaz Pakistan",
    "islamijamhooriittehadpakistan": "Islami Jamhoori Ittehad Pakistan",
    "islamitebreekpakistan": "Islami Tehreek Pakistan",
    "islamitehreekpakistan": "Islami Tehreek Pakistan",
    "islamithreekpakistan": "Islami Tehreek Pakistan",
    "istehkamepakistanparty": "Istehkam-e-Pakistan Party",
    "istehkamepakistantehreek": "Istehkam-e-Pakistan Tehreek",
    "istehkampakistanparty": "Istehkam-e-Pakistan Party",
    "jamaateeislamipakistan": "Jamaat-e-Islami Pakistan",
    "jamaateislamipakistan": "Jamaat-e-Islami Pakistan",
    "jamateislami": "Jamaat-e-Islami Pakistan",
    "jamateislamipak": "Jamaat-e-Islami Pakistan",
    "jamateislamipakistan": "Jamaat-e-Islami Pakistan",
    "jamateislamipaksitan": "Jamaat-e-Islami Pakistan",
    "jamatislamipakistan": "Jamaat-e-Islami Pakistan",
    "jameeatulmaeislampakistan": "Jamiat Ulama-e-Islam Pakistan",
    "jamhooriwatanparty": "Jamhoori Wattan Party",
    "jamhooriwattanparty": "Jamhoori Wattan Party",
    "jamiatulamaeislam": "Jamiat Ulama-e-Islam Pakistan",
    "jamiatulamaeislamnazaryatipakistan": "Jamiat Ulema-e-Islam Nazaryati Pakistan",
    "jamiatulamaeislampakistan": "Jamiat Ulama-e-Islam Pakistan",
    "jamiatulamaeislampakistans": "Jamiat Ulama-e-Islam Pakistan (S)",
    "jamiatulamaepakistannoorani": "Jamiat Ulema-e-Pakistan (Noorani)",
    "jamiatulamaepakistans": "Jamiat Ulama-e-Pakistan (S)",
    "jamiatulamaislampakistan": "Jamiat Ulama-e-Islam Pakistan",
    "jamiatulameislampakistan": "Jamiat Ulama-e-Islam Pakistan",
    "jamiatulemaeislamnazaryatipakistan": "Jamiat Ulema-e-Islam Nazaryati Pakistan",
    "jamiatulemaeislamnazayatipakistan": "Jamiat Ulema-e-Islam Nazaryati Pakistan",
    "jamiatulemaeislampakistan": "Jamiat Ulama-e-Islam Pakistan",
    "jamiatulemaepakistannoorani": "Jamiat Ulema-e-Pakistan (Noorani)",
    "jamiatulmaepakistanimamnoorani": "Jamiat Ulma-e-Pakistan (Imam Noorani)",
    "jammateislamipakistan": "Jamaat-e-Islami Pakistan",
    "jamoteqaumimovement": "Jamote Qaumi Movement",
    "jamotqaumimovement": "Jamote Qaumi Movement",
    "ji": "Jamaat-e-Islami Pakistan",
    "jip": "Jamaat-e-Islami Pakistan",
    "jqm": "Jamote Qaumi Movement",
    "juinazaryatipakistan": "Jamiat Ulema-e-Islam Nazaryati Pakistan",
    "juinazriatipakistan": "Jamiat Ulema-e-Islam Nazaryati Pakistan",
    "juip": "Jamiat Ulama-e-Islam Pakistan",
    "juipakistan": "Jamiat Ulama-e-Islam Pakistan",
    "jumaateislamipakistan": "Jamaat-e-Islami Pakistan",
    "jupnoorani": "Jamiat Ulema-e-Pakistan (Noorani)",
    "jwp": "Jamhoori Wattan Party",
    "khadmeenesindh": "Khadmeen-e-Sindh",
    "kissanittehad": "Kissan Ittehad",
    "kissanittehadawamiparty": "Kissan Ittehad Awami Party",
    "majlisewehdatulmuslimeenpakistan": "Majlis Wahdat-e-Muslimeen Pakistan",
    "majliswahdatemuslimeenpakistan": "Majlis Wahdat-e-Muslimeen Pakistan",
    "markazijamiatahlehadithpakistan": "Markazi Jamiat Ahl-e-Hadith Pakistan",
    "markazijimiatahlehadithpakistan": "Markazi Jamiat Ahl-e-Hadith Pakistan",
    "mazloomolasitahreekpakistan": "Mazloom Olsi Tehreek Pakistan",
    "mazloomolasitehreekpakistan": "Mazloom Olsi Tehreek Pakistan",
    "mazloomolsitehreekpakistan": "Mazloom Olsi Tehreek Pakistan",
    "mohajirqaumimovementpakistan": "Mohajir Qaumi Movement Pakistan",
    "mqmp": "Muttahida Qaumi Movement Pakistan",
    "mustaqbilpakistan": "Mustaqbil Pakistan",
    "muttahidaqomimovementpakistan": "Muttahida Qaumi Movement Pakistan",
    "nationaldemocraticmovement": "National Democratic Movement",
    "nationalparty": "National Party",
    "nazriapakistancouncil": "Nazria Pakistan Council",
    "np": "National Party",
    "pakdefenceqoumimovement": "Pak Defence Qoumi Movement",
    "pakisatanpeoplespartparliamentarians": "Pakistan Peoples Party Parliamentarians",
    "pakistaaawamitehreek": "Pakistan Awami Tehreek",
    "pakistanaamadmimovement": "Pakistan Aam Admi Movement",
    "pakistanamantehreek": "Pakistan Aman Tehreek",
    "pakistanawamiinqelabileague": "Pakistan Awami Inqelabi League",
    "pakistanawamileague": "Pakistan Awami League",
    "pakistanawamiquwat": "Pakistan Awami Quwat",
    "pakistanawamitehreek": "Pakistan Awami Tehreek",
    "pakistanconservativeparty": "Pakistan Conservative Party",
    "pakistanfalahparty": "Pakistan Falah Party",
    "pakistanfreedommovement": "Pakistan Freedom Movement",
    "pakistanislamicrepublicanparty": "Pakistan Islamic Republican Party",
    "pakistankissanittehadchanwar": "Pakistan Kissan Ittehad(Ch. Anwar)",
    "pakistanmarkazimulsimleague": "Pakistan Markazi Muslim League",
    "pakistanmarkazimuslimleague": "Pakistan Markazi Muslim League",
    "pakistanmasihaparty": "Pakistan Masiha Party",
    "pakistanmuhafizpartynational": "Pakistan Muhafiz Party (National)",
    "pakistanmuslimalliance": "Pakistan Muslim Alliance",
    "pakistanmuslimleague": "Pakistan Muslim League",
    "pakistanmuslimleaguecouncil": "Pakistan Muslim League Council",
    "pakistanmuslimleaguej": "Pakistan Muslim League (J)",
    "pakistanmuslimleaguekqk": "Pakistan Muslim League (KQK)",
    "pakistanmuslimleaguen": "Pakistan Muslim League (N)",
    "pakistanmuslimleagueorganization": "Pakistan Muslim League Organization",
    "pakistanmuslimleaguesherebangalakfazalulhaq": "Pakistan Muslim League (Sher-e-Bangal) A.K. Fazal-Ul-Haq",
    "pakistanmuslimleagueziaulhaqshaheed": "Pakistan Muslim League (Zia-ul-Haq Shaheed)",
    "pakistanmuslimleguen": "Pakistan Muslim League (N)",
    "pakistanmuslimparty": "Pakistan Muslim Party",
    "pakistannazriyatiparty": "Pakistan Nazriyati Party",
    "pakistanpeoplepartyparliamentarians": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoplepartyparlimentarian": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoples": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoplesalliancea": "Pakistan People's Alliance (A)",
    "pakistanpeoplespartyparliamentarians": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoplespartyparlimentarians": "Pakistan Peoples Party Parliamentarians",
    "pakistanpeoplespartyshaheedbhutto": "Pakistan Peoples Party (Shaheed Bhutto)",
    "pakistanrahehaqparty": "Pakistan Rah-e-Haq Party",
    "pakistansiraikipartyt": "Pakistan Siraiki Party(T)",
    "pakistansupremedemocraticparty": "Pakistan Supreme Democratic Party",
    "pakistantehreekeemaan": "Pakistan Tehreek-e-Emaan",
    "pakistantehreekeinsafnazriati": "Pakistan Tehreek-e-Insaf (Nazriati)",
    "pakistantehreekeinsafparliamentarianptip": "Pakistan Tehreek-e-Insaf Parliamentarian",
    "pakistantehreekeinsafparlimentarianptip": "Pakistan Tehreek-e-Insaf Parliamentarian",
    "pakistantehreekeinsaniat": "Pakistan Tehreek-e-Insaniat",
    "pakistantehreekeshadbad": "Pakistan Tehreek-e-Shadbad",
    "pakistantehreekeshariatparty": "Pakistan Tehreek-e-Shariat Party",
    "pakistanwelfareparty": "Pakistan Welfare Party",
    "pakistsanpeoplespartyparlimentarians": "Pakistan Peoples Party Parliamentarians",
    "pakleague": "Pak League",
    "paksitanmuslimleaguen": "Pakistan Muslim League (N)",
    "pasbandemocraticparty": "Pasban Democratic Party",
    "pashtoonkhowamilliawamiparty": "Pashtoonkhwa Milli Awami Party",
    "pashtoonkhowanationalawamiparty": "Pashtoonkhwa National Awami Party",
    "pashtoonkhwamiliawamiparty": "Pashtoonkhwa Milli Awami Party",
    "pashtoonkhwamilliawami": "Pashtoonkhwa Milli Awami Party",
    "pashtoonkhwamilliawamiparty": "Pashtoonkhwa Milli Awami Party",
    "pashtoonkhwanationalawamiparty": "Pashtoonkhwa National Awami Party",
    "pkmap": "Pashtoonkhwa Milli Awami Party",
    "pmln": "Pakistan Muslim League (N)",
    "pmml": "Pakistan Markazi Muslim League",
    "pnap": "Pashtoonkhwa National Awami Party",
    "pppp": "Pakistan Peoples Party Parliamentarians",
    "qaumiwatanparty": "Qaumi Watan Party",
    "rabitajamiatulamaeislamrjui": "Rabita Jamiat Ulma-e-Islam",
    "rabitajamiatulmaeislam": "Rabita Jamiat Ulma-e-Islam",
    "rabitajamiatulmaeislamrjui": "Rabita Jamiat Ulma-e-Islam",
    "rabitajamiatulmaislamrjui": "Rabita Jamiat Ulma-e-Islam",
    "rjui": "Rabita Jamiat Ulma-e-Islam",
    "saraikistandemocraticparty": "Saraikistan Democratic Party",
    "sulaimankhelqabailmovement": "Sulaiman Khel Qabail Movement",
    "sunniittehadcouncil": "Sunni Ittehad Council",
    "tehreekeazmatepakistan": "Tehreek-e-Azmat-e-Pakistan",
    "tehreekedarveshanpakistan": "Tehreek-e-Darveshan Pakistan",
    "tehreekedifaepakistan": "Tehreek-e-Difa-e-Pakistan",
    "tehreekeinqilabpoliticalmovement": "Tehreek-e-Inqilab Political Movement",
    "tehreekeinsafparliamentarianptip": "Pakistan Tehreek-e-Insaf Parliamentarian",
    "tehreekelabaikpakistan": "Tehreek Labbaik Pakistan",
    "tehreekelabbaikislam": "Tehreek-e-Labbaik Islam",
    "tehreekesubahazarapakistan": "Tehreek-e-Suba Hazara Pakistan",
    "tehreeketahafuzepakistan": "Tehreek-e-Tahafuz-e-Pakistan",
    "tehreeketarraqiokamal": "Tehreek-e-Tarraqi-o-Kamal",
    "tehreekjawananpakistan": "Tehreek Jawanan Pakistan",
    "tehreeklabaikpakistan": "Tehreek Labbaik Pakistan",
    "tehreeklabbaikpakistan": "Tehreek Labbaik Pakistan",
    "tehreektabdilinizamepakistan": "Tehreek Tabdili Nizam-e-Pakistan",
    "tehrikjawananpakistan": "Tehreek Jawanan Pakistan",
    "tehriklabaikpakistan": "Tehreek Labbaik Pakistan",
    "tlp": "Tehreek Labbaik Pakistan",
}
# Forms whose own figures do not add up, by the check each fails and the reason, disclosed in a caveat.
ACCOUNTED: dict[str, dict[str, str]] = {
    "NA-234": {
        "votes": "The candidates' votes printed on the form sum to 173,087, 30 fewer than the 173,117 valid votes it prints. Both are stored as printed."
    },
    "NA-266": {
        "votes": "The candidates' votes printed on the form sum to 201,874, 702 fewer than the 202,576 valid votes it prints. Both are stored as printed. The gap equals the invalid votes it prints, so its valid votes may be the total votes polled."
    },
    "PK-112": {
        "votes": "The candidates' votes printed on the form sum to 92,501, 50 fewer than the 92,551 valid votes it prints. Both are stored as printed."
    },
    "PK-21": {
        "votes": "The candidates' votes printed on the form sum to 39,827, 93 more than the 39,734 valid votes it prints. Both are stored as printed."
    },
    "PK-29": {
        "votes": "The candidates' votes printed on the form sum to 51,526, 300 fewer than the 51,826 valid votes it prints. Both are stored as printed."
    },
    "PP-105": {
        "votes": "The candidates' votes printed on the form sum to 139,171, 1 fewer than the 139,172 valid votes it prints. Both are stored as printed."
    },
    "PP-155": {
        "votes": "The candidates' votes printed on the form sum to 87,492, 2,274 fewer than the 89,766 valid votes it prints. Both are stored as printed."
    },
    "PP-44": {
        "votes": "The candidates' votes printed on the form sum to 142,057, 734 fewer than the 142,791 valid votes it prints. Both are stored as printed."
    },
    "PP-45": {
        "votes": "The candidates' votes printed on the form sum to 140,018, 993 fewer than the 141,011 valid votes it prints. Both are stored as printed."
    },
    "PP-48": {
        "votes": "The candidates' votes printed on the form sum to 148,515, 1,497 fewer than the 150,012 valid votes it prints. Both are stored as printed."
    },
    "PP-49": {
        "votes": "The candidates' votes printed on the form sum to 136,668, 3,563 fewer than the 140,231 valid votes it prints. Both are stored as printed."
    },
    "PP-55": {
        "votes": "The candidates' votes printed on the form sum to 115,916, 84 fewer than the 116,000 valid votes it prints. Both are stored as printed."
    },
    "PB-8": {
        "registered": "The form prints 63,326 male and 50,697 female registered voters, which sum to 114,023, and a total of 113,999. The printed total is stored."
    },
    "NA-263": {
        "missing": "The scan is cut off below the candidates, so the valid and invalid votes the form prints are not stored."
    },
}


DAY = date(2024, 2, 8)
STAMP = DAY.isoformat()
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5
DIGITIZED = 8
PACKAGES = [f"{SOURCE}/registry-2024", f"{SOURCE}/{STAMP}"]
ECP_2024 = ecp.key("source", "ecp", "2024")
# The seat prefix of each assembly in 2024, and its key in ecp.ASSEMBLIES; Khyber Pakhtunkhwa's assembly is the
# North-West Frontier Province's, renamed in 2010.
PREFIXES = {"NA": "NA", "PP": "PP", "PS": "PS", "PK": "PF", "PB": "PB"}
SEATS = {"NA": 266, "PP": 297, "PS": 130, "PK": 115, "PB": 51}
TITLES = {
    "NA": "National Assembly",
    "PP": "Punjab Provincial Assembly",
    "PS": "Sindh Provincial Assembly",
    "PK": "Khyber Pakhtunkhwa Provincial Assembly",
    "PB": "Balochistan Provincial Assembly",
}
# Constituencies whose poll of 8 February did not stand.
NOT_POLLED = {
    "NA-8": "its poll was postponed on the death of a contesting candidate",
    "PK-22": "its poll was postponed on the death of a contesting candidate",
    "PK-91": "its poll was postponed on the death of a contesting candidate",
    "PP-266": "its poll was postponed on the death of a contesting candidate",
}


def party_id(name: str) -> str:
    return ecp.key("party", "2024", name.casefold())


def forms() -> list[Form]:
    order = list(PREFIXES)
    found = [form(code, url) for code, url in files().items() if code not in NOT_POLLED and code not in TOTALS_ONLY]
    found += [totals_form(code) for code in TOTALS_ONLY]
    return sorted(found, key=lambda f: (order.index(f.code.split("-")[0]), int(f.code.split("-")[1])))


def packages() -> list[str]:
    return PACKAGES


def parse() -> None:
    found = forms()
    registry(found).write()
    build(found).write()


def emit() -> None:
    package.run("load", PACKAGES)


def listings() -> list[str]:
    """The cached Drive folder listings the Forms 49 were found through."""
    return sorted(url for url in raw.keys(SOURCE) if url.startswith(LISTING.format("")))


def registry(found: list[Form]) -> Package:
    p = Package(
        f"{SOURCE}/registry-2024",
        SOURCE,
        "The parties of the 2024 Pakistani general election, and the Election Commission's scanned Forms 49 of its "
        "constituencies",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(ecp.PAKISTAN)
    p.add(
        "source",
        id=ECP_2024,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="General Elections 2024: scanned Forms 45, 46, 47, 48, and 49",
        url="https://ecp.gov.pk/general-elections-2024",
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    p.artifact(raw.cached(SOURCE, PAGE), ECP_2024)
    for url in listings():
        p.artifact(raw.cached(SOURCE, url), ECP_2024)
    for f in found:
        p.artifact(raw.cached(SOURCE, f.url), ECP_2024)
    for name in sorted({c.party for f in found for c in f.candidates} - {"Independent", ""}):
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=ecp.PAKISTAN)
        p.name(id, name, "en", "official")
    return p


def build(found: list[Form]) -> Package:
    p = Package(
        f"{SOURCE}/{STAMP}",
        SOURCE,
        "The Pakistani general election of 2024 for the National Assembly and the four provincial assemblies by "
        "constituency, from the Election Commission's scanned Forms 49",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/registry-2024"],
    )
    p.external.add(ecp.PAKISTAN)
    p.uses(raw.cached(SOURCE, PAGE))
    election = ecp.key("election", STAMP)
    p.add("election", id=election, polity_id=ecp.PAKISTAN, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=f"pk/{STAMP}-general", entity_id=election, is_current=True)
    p.name(election, "2024 Pakistani general election", "en", "official")
    have = {f.code for f in found}
    missing = [
        f"{prefix}-{n}" for prefix, count in SEATS.items() for n in range(1, count + 1) if f"{prefix}-{n}" not in have
    ]
    if sorted(missing) != sorted(NOT_POLLED):
        raise ValueError(f"constituencies without a Form 49: {missing}")
    listed = "; ".join(f"{code} ({why})" for code, why in NOT_POLLED.items())
    p.add(
        "note",
        id=ecp.key("note", election, "left-out"),
        entity_id=election,
        kind="context",
        body_md="The National Assembly and the provincial assemblies of the Punjab, Sindh, Khyber Pakhtunkhwa, and "
        "Balochistan were elected on the same day. The poll did not stand in "
        f"{len(NOT_POLLED)} constituencies: {listed}. Seven constituencies whose Form 49 is missing or unreadable "
        f"({', '.join(TOTALS_ONLY)}) are read from the grand-total row of their Form 48, or from their Form 47, and "
        "stored at the preliminary stage.",
        lang="en",
    )
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    for f in found:
        add_seat(p, f, election, values, locators)
    p.accept(
        "accounting.votes_valid",
        f"On {sum('votes' in a for a in ACCOUNTED.values())} Forms 49 the candidates' votes the returning officer printed "
        "do not sum to the valid votes printed on the same form, though every figure was read twice; both are stored as "
        "printed, with a caveat on each.",
    )
    p.frame(
        "result_value",
        pl.DataFrame(values, schema_overrides={"option_id": pl.String}).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(locators, schema_overrides={"option_id": pl.String}).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            source_id=pl.lit(None, pl.String),
        ),
    )
    return p


def add_seat(
    p: Package, f: Form, election: str, values: list[dict[str, object]], locators: list[dict[str, object]]
) -> None:
    prefix = f.code.split("-")[0]
    body = ecp.ASSEMBLIES[PREFIXES[prefix]]
    contest = ecp.key("contest", STAMP, f.code)
    label = f"{f.code} {f.name}".strip()
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=body.body,
        magnitude=1,
        round_number=1,
        electoral_system_id=ecp.SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"2024 Pakistani {TITLES[prefix]} election, {label}", "en", "official")
    p.add("slug", path=f"pk/{STAMP}-general/{f.code.lower()}", entity_id=contest, is_current=True)
    result_set = ecp.key("result_set", STAMP, f.code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage=f.stage,
        kind="counted",
        source_id=ECP_2024,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=ecp.key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD if f.kind == "49" else TOTALS_METHOD[f.kind],
        lang="en",
    )
    for index, caveat in enumerate(ACCOUNTED.get(f.code, {}).values()):
        p.add(
            "note",
            id=ecp.key("note", result_set, "caveat", index),
            entity_id=result_set,
            kind="caveat",
            body_md=caveat,
            lang="en",
        )
    unit = ecp.key("unit", STAMP, f.code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ecp.PAKISTAN,
        scheme_code=body.scheme,
        code=f"{STAMP}:{f.code}",
        area_relation="unlocated",
        kind="standard",
    )
    p.name(unit, label, "en", "official")
    artifact = raw.cached(SOURCE, f.url)
    p.uses(artifact)
    stored = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{ECP_2024}\x1f{artifact.storage_key}"))
    pages: dict[int, str] = {}
    for number in sorted({c.page for c in f.candidates} | {page for _, page, _ in f.figures.values()}):
        image, width, height = render(artifact, number)
        pages[number] = ecp.key("page", STAMP, f.code, number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=stored,
            page_number=number,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )

    def put(option: str | None, measure: int, value: int, page: int, box: tuple[float, float, float, float]) -> None:
        values.append(
            {"result_set_id": result_set, "unit_id": unit, "option_id": option, "measure": measure, "value": value}
        )
        locators.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "measure": measure,
                "page_id": pages[page],
                "x0": box[0],
                "y0": box[1],
                "x1": box[2],
                "y1": box[3],
            }
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)

    for field, measure in (("registered", REGISTERED), ("valid", VALID), ("invalid", INVALID)):
        if field not in f.figures:
            continue
        value, page, box = f.figures[field]
        put(None, measure, value, page, box)
    for c in f.candidates:
        option = ecp.key("option", STAMP, f.code, c.serial)
        person = ecp.key("person", STAMP, f.code, c.serial)
        independent = c.party == "Independent"
        p.add("person", id=person)
        p.name(person, c.name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=c.serial,
            ballot_label=c.name,
        )
        p.add(
            "candidacy",
            id=ecp.key("candidacy", STAMP, f.code, c.serial),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=None if independent or not c.party else party_id(c.party),
            is_independent=independent,
        )
        put(option, VOTES, c.votes, c.page, c.box)


TOTALS_METHOD = {
    "48": "The constituency's Form 49 is missing or cannot be read, so this set is read from the grand-total row of the "
    "returning officer's consolidated statement of the count (Form 48) for the general election of 8 February 2024, "
    "typed in English, from the Election Commission's scanned form. The row gives each candidate's valid votes, "
    "tendered and challenged votes included, polling stations and postal ballots together, and the valid and invalid "
    "votes. The form prints no party for a candidate and no registered voters, so none are stored. The scan has no "
    "usable text layer, so each figure was transcribed from the page image and checked against the totals the row "
    "prints; each is stored with its page, and the whole page stands for its box. The form does not name the "
    "returned candidate, and none is stored. A final result (Form 49) would supersede it.",
    "47": "The constituency has no Form 49 or Form 48 in the Commission's folders, so this set is read from the "
    "provisional consolidated result (Form 47) of the returning officer for the general election of 8 February 2024, "
    "written in Urdu, from the Election Commission's scanned form. The form gives the registered voters, the valid "
    "and invalid votes, and each candidate's party and votes. Each figure was transcribed from the page image and "
    "checked against the totals the form prints; each is stored with its page, and the whole page stands for its "
    "box. The form does not name the returned candidate, and none is stored. A final result (Form 49) would "
    "supersede it.",
}
METHOD = (
    "The final consolidated result of the constituency (Form 49) issued by its returning officer for the general "
    "election of 8 February 2024, read from the Election Commission's scanned form. The scan has no usable text layer, "
    "so each figure was transcribed from the page image and read again with optical character recognition, which "
    "gives the page and box each figure is stored with. Registered voters are the total the form prints for the "
    "constituency, and valid and invalid votes are the totals it prints. The form does not name the returned "
    "candidate, and none is stored."
)
