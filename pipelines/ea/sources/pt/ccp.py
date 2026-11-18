"""Elections for the Council of Portuguese Communities (Conselho das Comunidades Portuguesas), 1997 to 2023.

The Council is the consultative body of Portuguese citizens abroad. Its members are elected by consular electoral
circle, each circle a group of countries or consular posts. The National Elections Commission's site, www.cne.pt,
republishes the official results of each of the five elections held: the 1997 and 2003 maps, the list of councillors
elected in 2008, and the 2015 and 2023 tables of results and councillors elected. All five are PDFs.

The files differ. 1997 prints registered voters, ballots cast, blank and null ballots, and each list's votes and seats
for 21 circles, but no names. 2003 prints the same figures in prose for each circle, with the list titles and the
councillors elected. 2008 prints only the councillors elected in each circle. 2015 prints seats, blank and null
ballots, each list's votes, and the councillors elected. 2023 prints registered voters, seats, each list's votes where it
gives them, blank and null ballots, and the councillors elected.
"""

import html
import re
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "pt.ccp"
HOST = "https://www.cne.pt/sites/default/files/dl"
PORTUGAL = str(uuid5(package.NAMESPACE, "polity\x1fPT"))
VOTES, REGISTERED, CAST, INVALID, BLANK = 1, 2, 3, 5, 6

FILES: dict[int, str] = {
    1997: "resultados_ccp1997.pdf",
    2003: "resultados_ccp2003.pdf",
    2008: "ccp_2008_conselheiros-eleitos_mne.pdf",
    2015: "ccp_2015_resultados_conselheiros-eleitos.pdf",
    2023: "eleicoes/2023_concelho_comunidades_portuguesas/2023_ccp_resultados_gerais_eleicao.pdf",
}
SEAT_MAP_2008 = "md_ccp_2008.pdf"  # the annex to Portaria 112/2008: the seats of each circle
DAYS = {
    1997: date(1997, 4, 27),
    2003: date(2003, 3, 30),
    2008: date(2008, 4, 20),
    2015: date(2015, 9, 6),
    2023: date(2023, 11, 26),
}
# The printed national figures the circles are compared with: registered voters, ballots cast, blank, null.
NATIONAL: dict[int, dict[int, int]] = {
    1997: {REGISTERED: 2557260, CAST: 34522, BLANK: 1385, INVALID: 607},
    2023: {REGISTERED: 1575799},
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SRC = key("source", "cne")
BODY = key("body", "ccp")
SYSTEM = key("electoral_system", "dhondt")
SCHEME = "pt.ccp_circulo"


def url(name: str) -> str:
    return f"{HOST}/{name}"


def fetch() -> None:
    for name in (*FILES.values(), SEAT_MAP_2008):
        raw.fetch(SOURCE, url(name), suffix=".pdf")


def artifact(year: int) -> raw.Artifact:
    return raw.cached(SOURCE, url(FILES[year]))


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


# ---------------------------------------------------------------------------------------------------------------------
# What the files say


@dataclass
class Line:
    label: str  # the list's letter, or "Única" for a sole list
    votes: int | None
    seats: int | None = None
    title: str | None = None


@dataclass
class Elected:
    name: str
    label: str | None  # the list the councillor was elected on, when the file says


@dataclass
class Circle:
    code: str
    name: str
    seats: int | None = None  # the number of councillors the circle elects
    held: bool = True
    registered: int | None = None
    cast: int | None = None
    blank: int | None = None
    invalid: int | None = None
    lines: list[Line] = field(default_factory=list)
    elected: list[Elected] = field(default_factory=list)
    group: str | None = None  # the country subtotal the circle belongs to
    caveat: str | None = None


def _text(path: object, *flags: str) -> str:
    done = subprocess.run(["pdftotext", *flags, str(path), "-"], capture_output=True, check=True, text=True)
    return done.stdout


def _number(text: str) -> int:
    return int(re.sub(r"[ . ]", "", text))


# --- 1997 -------------------------------------------------------------------------------------------------------------

NUMBERS_1997 = re.compile(r"^\s*(\d+)\s+(\d+)\s+[\d.,]+%?\s+(\d+)\s+[\d.,]+%?\s+(\d+)\s+[\d.,]+%?(?:\s+Lista|\s*$)")
LIST_1997 = re.compile(r"Lista ([A-Z])\s+(\d+)\s+[\d.,]+%?\s+(\d+)\s*$")


def _join(fragments: list[str]) -> str:
    out = ""
    for fragment in fragments:
        out = out + fragment if out.endswith("-") else f"{out} {fragment}".strip()
    return out


def circles_1997() -> list[Circle]:
    lines = _text(artifact(1997).path, "-layout").splitlines()
    blocks: list[list[str]] = []
    for line in lines:
        if "Votantes (nº)" in line and not line.startswith(" " * 40):
            blocks.append([line])
        elif blocks and not line.strip().startswith("TOTAL"):
            blocks[-1].append(line)
        elif blocks:
            break
    out = []
    for index, block in enumerate(blocks, 1):
        fragments: list[str] = []
        figures: tuple[int, ...] | None = None
        circle_lines: list[Line] = []
        for line in block:
            header = line.split("Votantes (nº)")[0] if "Votantes (nº)" in line else None
            found = LIST_1997.search(line)
            left = line[: found.start()] if found else (header if header is not None else line)
            if found:
                label, votes, seats = found.groups()
                circle_lines.append(Line(label, int(votes), int(seats)))
            numbers = NUMBERS_1997.match(line)
            if numbers and figures is None:
                figures = tuple(int(n) for n in numbers.groups())
            elif left.strip() and not re.match(r"^\s*\d", left):
                fragments.append(left.strip())
        if figures is None:
            raise ValueError(f"1997: no figures for {_join(fragments)}")
        registered, cast, blank, invalid = figures
        out.append(
            Circle(
                f"{index:02d}",
                _join(fragments),
                seats=sum(line.seats or 0 for line in circle_lines),
                registered=registered,
                cast=cast,
                blank=blank,
                invalid=invalid,
                lines=circle_lines,
            )
        )
    return out


# --- 2003 -------------------------------------------------------------------------------------------------------------

GROUPS_2003 = {"ALEMANHA": 2, "FRANÇA": 5, "BRASIL": 6, "CANADÁ": 3, "ESTADOS UNIDOS DA AMÉRICA": 3, "ÁFRICA DO SUL": 3}
COUNTRY_2003 = re.compile(r"^([A-ZÀ-Ý][A-ZÀ-Ý ]+?)\s*:?\s*\(\s*Resultados definitivos\s*\)\s*$")
LIST_2003 = re.compile(r"^Votos (?:na )?Lista (.*?)\s*:\s*(?:-\s*ÚNICA:\s*)?([\d.]+)\s*(.*)$")


def _unique(label: str) -> str:
    first = label.split()[0] if label.split() else label
    return "Única" if first.lower() == "única" else first


def circles_2003() -> tuple[list[Circle], dict[str, dict[int, int]]]:
    """The circles and the printed country subtotals (registered voters and ballots cast)."""
    text = _text(artifact(2003).path, "-layout")
    blocks: list[list[str]] = []
    country: str | None = None
    left = 0
    totals: dict[str, dict[int, int]] = {}
    groups: list[str | None] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = COUNTRY_2003.match(line)
        if header:
            country = header.group(1).strip()
            left = GROUPS_2003[country]
            totals[country] = {}
            blocks.append([])  # closes the block before it
            continue
        if re.match(r"^[A-ZÀ-Ý ]{4,}$", line) and not line.startswith("-"):
            blocks.append([])
            continue
        if re.match(r"^N\.º de eleitores inscritos:", line) and country:
            totals[country][REGISTERED] = _number(line.split(":")[1])
            continue
        if re.match(r"^N\.º de votantes:", line) and country:
            totals[country][CAST] = _number(line.split(":")[1])
            continue
        if re.match(r"^Percentagem de votantes", line) and country and len(blocks) and not blocks[-1]:
            continue
        if re.match(r"^- ?Círculo", line):
            blocks.append([line])
            groups.append(country if left > 0 else None)
            if left > 0:
                left -= 1
            continue
        if blocks:
            blocks[-1].append(line)
    out = []
    members = iter(groups)
    for block in blocks:
        if not block:
            continue
        group = next(members)
        head, *rest = block
        name = head
        while rest and not rest[0].startswith(("Sede", "Não se realizaram")):
            name += " " + rest.pop(0)
        name = re.sub(r"^- ?Círculo\s*(da |de )?-?\s*", "", name)
        name = re.sub(r"\s*\(\s*Resultados\s+definitivos\s*\)\s*:?\s*$", "", name).strip(" :")
        circle = Circle(f"{len(out) + 1:02d}", re.sub(r"\s*/\s*", " / ", name), group=group)
        out.append(circle)
        names_text = ""
        collecting = False
        for line in rest:
            if line.startswith("Não se realizaram"):
                circle.held = False
                break
            if collecting:
                names_text += " " + line
                continue
            field_ = line.split(":")[0].lower()
            value = line.split(":", 1)[1].strip() if ":" in line else ""
            if field_.startswith("total de inscritos"):
                circle.registered = _number(value)
            elif field_.startswith("total de votantes"):
                circle.cast = _number(value)
            elif field_ == "votos brancos":
                circle.blank = _number(value)
            elif field_ == "votos nulos":
                circle.invalid = _number(value)
            elif field_.startswith("votos"):
                found = LIST_2003.match(line)
                if found is None:
                    raise ValueError(f"2003: cannot read {line!r}")
                label, votes, tail = found.groups()
                title = re.search(r"[“\"](.+?)[”\"]", tail)
                circle.lines.append(Line(_unique(label), _number(votes), None, title.group(1) if title else None))
            elif field_.startswith("conselheiros eleitos"):
                circle.seats = int(value)
            elif field_ in ("nome", "nomes"):
                names_text = value
                collecting = True
        if circle.held:
            _attribute_2003(circle, names_text)
    return out, totals


def _attribute_2003(circle: Circle, names_text: str) -> None:
    sole = circle.lines[0].label if len(circle.lines) == 1 else None
    every = re.search(r"todos da Lista (\w)", names_text)
    names_text = re.sub(r"\((?:todos da Lista \w)\)", "", names_text)
    for piece in names_text.split(";"):
        piece = piece.strip().rstrip(".").strip()
        if not piece:
            continue
        found = re.search(r"\(Lista (\w)\)\s*$", piece)
        label = found.group(1) if found else (every.group(1) if every else sole)
        name = re.sub(r"\s*\(Lista \w\)\s*$", "", piece).strip()
        circle.elected.append(Elected(re.sub(r"\s+", " ", name), label))
    if circle.seats is not None and circle.seats != len(circle.elected):
        raise ValueError(f"2003 {circle.name}: {circle.seats} seats and {len(circle.elected)} names")
    if all(person.label for person in circle.elected):
        counts = Counter(person.label for person in circle.elected)
        for line in circle.lines:
            line.seats = counts.get(line.label, 0)
    else:
        circle.caveat = (
            "The map names the councillors elected but not the lists they were elected on, so the seats of each "
            "list are not stored. Elected: " + "; ".join(person.name for person in circle.elected) + "."
        )


# --- 2008 -------------------------------------------------------------------------------------------------------------

# The circles of the annex to Portaria 112/2008 with their seats, in the order the map prints them.
CIRCLES_2008: tuple[tuple[str, int], ...] = (
    ("Alemanha: Berlim / Hamburgo", 1),
    ("Alemanha: Estugarda / Dusseldorf / Frankfurt", 3),
    ("Andorra", 1),
    ("Bélgica", 1),
    ("Holanda", 1),
    ("Suécia / Dinamarca / Finlândia / Noruega / Estónia / Letónia / Lituânia / Polónia", 1),
    ("Espanha", 1),
    ("França: Bordéus / Toulouse", 1),
    ("França: Lyon / Clermont-Ferrand", 1),
    ("França: Marselha / Ajaccio", 1),
    ("França: Estrasburgo", 1),
    ("França: Paris / Lille / Nantes", 4),
    ("Suíça / Itália / Grécia / Áustria", 4),
    ("Reino Unido / Irlanda", 4),
    ("Luxemburgo", 1),
    ("Argentina", 1),
    ("Uruguai / Colômbia / México / Peru / Chile", 1),
    ("Brasil: Brasília / Belém", 1),
    ("Brasil: Recife / São Salvador da Baía / Fortaleza", 1),
    ("Brasil: Curitiba / Porto Alegre / São Paulo", 3),
    ("Brasil: Rio de Janeiro / Belo Horizonte", 3),
    ("Canadá: Montreal / Otawa / Toronto", 3),
    ("Canadá: Vancouver", 1),
    ("Estados Unidos da América: New Bedford / Boston / Providence", 2),
    ("Estados Unidos da América: Newark / Nova Iorque / Washington / Orlando", 2),
    ("Estados Unidos da América: São Francisco", 1),
    ("Venezuela: Caracas", 4),
    ("Venezuela: Valência", 1),
    ("África do Sul / Namíbia", 4),
    ("Angola / República Popular do Congo", 2),
    ("Cabo Verde / Guiné-Bissau / São Tomé e Príncipe / Senegal", 1),
    ("Moçambique / Quénia / Zimbabué", 1),
    ("Austrália / Timor / Filipinas", 1),
    ("China / Japão / Tailândia", 3),
    ("Índia", 1),
)
APPOINTED = "appointed"
SOURCE_ERRORS: dict[tuple[int, str], str] = {
    (1997, "15"): (
        "The map prints 9 seats for list A, which has 199 votes. The other lists' seats add up to 10 and follow the "
        "D'Hondt order of their votes, so the 9 looks like a misprint. Stored as printed, which makes 19 seats."
    ),
}


def circles_2008() -> list[Circle]:
    """The 2008 circles with their councillors; a circle with no councillors elected had them appointed instead."""
    stream: list[str] = []
    for line in _text(artifact(2008).path, "-layout").splitlines():
        found = re.search(r"(?:^|\s{2,})- (\S.*)$", line)
        if found:
            stream.append(found.group(1).strip())
        elif "A nomear nos termos" in line:
            stream.append(APPOINTED)
    out = []
    at = 0
    for index, (name, seats) in enumerate(CIRCLES_2008, 1):
        if at < len(stream) and stream[at] == APPOINTED:
            out.append(Circle(f"{index:02d}", name, seats=seats, held=False))
            at += 1
            continue
        names = stream[at : at + seats]
        if len(names) != seats or APPOINTED in names:
            raise ValueError(f"2008 {name}: {seats} seats and names {names}")
        at += seats
        out.append(Circle(f"{index:02d}", name, seats=seats, elected=[Elected(n, None) for n in names]))
    if at != len(stream):
        raise ValueError(f"2008: {len(stream) - at} names left over")
    return out


# --- 2015 -------------------------------------------------------------------------------------------------------------

# The circles of the 2015 table in its order, with the seats printed beside each; the parse checks both.
CIRCLES_2015: tuple[tuple[str, int], ...] = (
    ("África do Sul: Joanesburgo e Pretória", 3),
    ("África do Sul: Cabo", 1),
    ("Alemanha: Dusseldorf, Hamburgo e Berlim (e Varsóvia)", 2),
    ("Alemanha: Estugarda", 2),
    ("Andorra", 1),
    ("Angola", 1),
    ("Argentina", 1),
    ("Austrália: Sidney", 1),
    ("Austrália: Melbourne", 1),
    ("Bélgica", 1),
    ("Brasil: Brasília", 1),
    ("Brasil: Belém", 1),
    ("Brasil: Belo Horizonte", 1),
    ("Brasil: Porto Alegre", 1),
    ("Brasil: Recife e Salvador", 1),
    ("Brasil: Fortaleza", 1),
    ("Brasil: Rio de Janeiro", 3),
    ("Brasil: São Paulo", 3),
    ("Brasil: Santos", 1),
    ("Cabo Verde", 1),
    ("Canadá: Toronto", 3),
    ("Canadá: Montreal e Otava", 1),
    ("Canadá: Vancouver", 1),
    ("China, Macau e Hong Kong", 3),
    ("Espanha", 1),
    ("Estados Unidos: Washington", 1),
    ("Estados Unidos: Miami e Orlando (e Palm Coast)", 1),
    ("Estados Unidos: Boston, Providence e New Bedford", 2),
    ("Estados Unidos: Newark e Nova Iorque", 2),
    ("Estados Unidos: São Francisco", 1),
    ("França: Paris", 5),
    ("França: Bordéus e Toulouse", 2),
    ("França: Lyon e Marselha", 2),
    ("França: Estrasburgo", 1),
    ("Grã-Bretanha: Londres e Manchester (e Dublin)", 3),
    ("Grã-Bretanha: Bermuda", 1),
    ("Guiné-Bissau", 1),
    ("Índia (Goa e Nova Deli)", 1),
    ("Luxemburgo", 2),
    ("Moçambique (e Harare)", 1),
    ("Namíbia", 1),
    ("Países Baixos (Haia)", 1),
    ("Curaçau", 1),
    ("São Tomé e Príncipe", 1),
    ("Suécia (e Copenhaga, Oslo e Helsínquia)", 1),
    ("Suíça (e Roma e Viena)", 4),
    ("Timor-Leste", 1),
    ("Uruguai", 1),
    ("Venezuela: Caracas (e Bogotá)", 4),
    ("Venezuela: Valência", 2),
)
REPEATED_2015 = {
    "Alemanha: Dusseldorf, Hamburgo e Berlim (e Varsóvia)",
    "Estados Unidos: Boston, Providence e New Bedford",
}
Word = tuple[int, float, float, str]  # page, x, y, text


def _words(path: object) -> list[Word]:
    out: list[Word] = []
    page = 0
    for line in _text(path, "-bbox").splitlines():
        if line.lstrip().startswith("<page "):
            page += 1
        found = re.match(r'\s*<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="[\d.]+" yMax="[\d.]+">(.*)</word>', line)
        if found:
            out.append((page, float(found.group(1)), float(found.group(2)), html.unescape(found.group(3))))
    return out


def _lines(words: list[Word], tolerance: float = 3.0) -> list[list[Word]]:
    """Group words into lines by page and height, each line left to right."""
    out: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w[0], w[2], w[1])):
        if out and out[-1][0][0] == word[0] and abs(word[2] - out[-1][0][2]) <= tolerance:
            out[-1].append(word)
        else:
            out.append([word])
    return [sorted(line, key=lambda w: w[1]) for line in out]


def _in(word: Word, low: float, high: float) -> bool:
    return low <= word[1] < high


def circles_2015() -> list[Circle]:
    words = _words(artifact(2015).path)
    rows: list[tuple[int, float, int, int | None, int | None]] = []  # page, y, seats, blank, null
    by_row = _lines([w for w in words if w[1] >= 280 and w[1] < 410 and re.fullmatch(r"\d+|-", w[3])], 4)
    seats = [(line[0][0], line[0][2], line) for line in by_row if any(_in(w, 280, 300) for w in line)]
    for page, y, line in seats:
        figure = {col: next((w[3] for w in line if _in(w, *col)), None) for col in ((280, 300), (335, 365), (385, 405))}
        count = figure[(280, 300)]
        if count is None or count == "-":
            raise ValueError(f"2015: bad seats on page {page} at {y}")

        def number(text: str | None) -> int | None:
            return None if text in (None, "-") else int(str(text))

        rows.append((page, y, int(count), number(figure[(335, 365)]), number(figure[(385, 405)])))
    # lists: a letter column and a votes column
    letters = sorted(
        (w for w in words if _in(w, 425, 455) and re.fullmatch(r"[A-F]|-", w[3])), key=lambda w: (w[0], w[2])
    )
    votes = [w for w in words if _in(w, 465, 500) and re.fullmatch(r"\d+|-", w[3])]
    groups: list[list[tuple[str, int | None]]] = []
    for letter in letters:
        if letter[3] in ("A", "-") or not groups:
            groups.append([])
        near = [v for v in votes if v[0] == letter[0] and abs(v[2] - letter[2]) <= 6]
        value = None if not near or near[0][3] == "-" else int(near[0][3])
        if letter[3] != "-":
            groups[-1].append((letter[3], value))
    names: list[tuple[str, str]] = []
    for line in _lines([w for w in words if w[1] >= 510 and (w[0] > 1 or w[2] > 215)], 2.5):
        text = " ".join(w[3] for w in line)
        found = re.match(r"^(.*\S)\s+\(([A-F])\)$", text)
        if found:
            names.append((found.group(1), found.group(2)))
    if len(rows) != len(CIRCLES_2015) or len(groups) != len(CIRCLES_2015):
        raise ValueError(f"2015: {len(rows)} rows and {len(groups)} list groups for {len(CIRCLES_2015)} circles")
    out = []
    at = 0
    for index, ((name, expected), (_, _, count, blank, null), group) in enumerate(
        zip(CIRCLES_2015, rows, groups, strict=True), 1
    ):
        if count != expected:
            raise ValueError(f"2015 {name}: {count} seats, expected {expected}")
        circle = Circle(f"{index:02d}", name, seats=count, held=bool(group), blank=blank, invalid=null)
        if group:
            taken = names[at : at + count]
            at += count
            if len(taken) != count:
                raise ValueError(f"2015 {name}: {count} seats, names {taken}")
            circle.elected = [Elected(n, label) for n, label in taken]
            tally = Counter(label for _, label in taken)
            circle.lines = [Line(label, votes_, tally.get(label, 0)) for label, votes_ in group]
            if missing := set(tally) - {label for label, _ in group}:
                raise ValueError(f"2015 {name}: elected on unlisted {missing}")
        if name in REPEATED_2015:
            circle.caveat = "The vote in this circle was repeated on 11 October 2015; the table prints the repeat."
        out.append(circle)
    if at != len(names):
        raise ValueError(f"2015: {len(names) - at} names left over")
    return out


# --- 2023 -------------------------------------------------------------------------------------------------------------


def circles_2023() -> list[Circle]:
    words = [w for w in _words(artifact(2023).path) if w[1] < 740 and (w[0] > 1 or w[2] > 255)]
    out: list[Circle] = []
    labels: list[list[str]] = []
    current: Circle | None = None
    for line in _lines(words):
        if line[0][0] == 1 and line[0][2] < 255:
            continue
        text = " ".join(w[3] for w in line)
        if text.startswith(("Portaria", "SGMAI", "DGACCP")):
            continue
        label = [w[3] for w in line if w[1] < 195]
        if label:
            if re.fullmatch(r"\d\d", label[0]):
                labels.append(label)
            elif labels:
                labels[-1].extend(label)
        registered = [w[3] for w in line if _in(w, 195, 245) and w[3].isdigit()]
        seats = [w[3] for w in line if _in(w, 245, 295) and w[3].isdigit()]
        if registered and seats:
            current = Circle(f"{len(out) + 1:02d}", "", seats=int(seats[0]), registered=int("".join(registered)))
            out.append(current)
        if current is None:
            continue
        cells = {
            "votes": [w[3] for w in line if _in(w, 300, 350)],
            "invalid": [w[3] for w in line if _in(w, 350, 390)],
            "blank": [w[3] for w in line if _in(w, 390, 430)],
            "letter": [w[3] for w in line if _in(w, 430, 470) and re.fullmatch(r"[A-F]", w[3])],
            "name": " ".join(w[3] for w in line if w[1] >= 470),
        }
        if cells["invalid"] and cells["invalid"][0].isdigit():
            current.invalid = int(cells["invalid"][0])
        if cells["blank"] and cells["blank"][0].isdigit():
            current.blank = int(cells["blank"][0])
        if "s/candidatura" in cells["name"]:
            current.held = False
            continue
        if cells["name"] and not cells["letter"] and current.lines:
            cells["letter"] = [current.lines[-1].label]  # a councillor's row that repeats the list above it
        if cells["letter"]:
            letter = cells["letter"][0]
            figure = int(cells["votes"][0]) if cells["votes"] and cells["votes"][0].isdigit() else None
            known = next((x for x in current.lines if x.label == letter), None)
            if known is None:
                known = Line(letter, None, 0)
                current.lines.append(known)
            if figure is not None and known.votes is None:
                known.votes = figure
            if cells["name"]:
                current.elected.append(Elected(cells["name"], letter))
                known.seats = (known.seats or 0) + 1
    if len(out) != len(labels) or len(out) != 52:
        raise ValueError(f"2023: {len(out)} rows and {len(labels)} labels")
    for circle, label in zip(out, labels, strict=True):
        text = " ".join(label)
        printed = re.match(r"^(\d\d) [-–] (.*)$", text)
        if printed is None:
            raise ValueError(f"2023: unreadable circle label {text!r}")
        number, name = printed.groups()
        if number != circle.code:
            raise ValueError(f"2023: circle {number} is row {circle.code}")
        circle.name = re.sub(r"\s+", " ", name).replace(" – ", " - ")
        if circle.held and circle.seats != len(circle.elected):
            raise ValueError(f"2023 {circle.name}: {circle.seats} seats and {len(circle.elected)} names")
    return out


def circles(year: int) -> list[Circle]:
    if year == 1997:
        return circles_1997()
    if year == 2003:
        return circles_2003()[0]
    if year == 2008:
        return circles_2008()
    if year == 2015:
        return circles_2015()
    return circles_2023()


# ---------------------------------------------------------------------------------------------------------------------
# Packages


def area(year: int, code: str) -> str:
    return key("area", year, code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The consular electoral circles of the Council of Portuguese Communities, as each election's results list them",
    )
    p.external.add(PORTUGAL)
    p.add("scheme", code=SCHEME, polity_id=PORTUGAL, kind="electoral_district", rank=1, noun="consular circle")
    for year in DAYS:
        for circle in circles(year):
            if not circle.held:
                continue
            id = area(year, circle.code)
            p.add("area", id=id, scheme_code=SCHEME)
            p.add("area_code", area_id=id, code=f"{year}:{circle.code}")
            p.name(id, circle.name, "pt", "official")
    return p


METHOD = {
    1997: (
        "The official results map of the 27 April 1997 election, published by the Secretary of State for the "
        "Portuguese Communities (Aviso 2394/97, Diário da República, II series, 26 June 1997) and republished on the "
        "National Elections Commission's site. It gives each circle's registered voters, voters (ballots cast), blank "
        "and null ballots, and each list's votes and seats. The lists carry letters only; the map prints no names of "
        "lists or councillors. Valid votes are not printed and are not stored."
    ),
    2003: (
        "The results of the 30 March 2003 election as published for the Council of Portuguese Communities and "
        "republished on the National Elections Commission's site, circle by circle with the list titles where printed "
        "and the councillors elected. Valid votes are not printed and are not stored."
    ),
    2008: (
        "The map of the councillors elected on 20 April 2008, published on the Ministry of Foreign Affairs' site "
        "after the election and republished on the National Elections Commission's site. It names the councillors "
        "elected in each circle and prints no votes, lists, or turnout. The seats of each circle are from the annex to "
        "Portaria 112/2008."
    ),
    2015: (
        "The table of results and councillors elected in the 6 September 2015 election, prepared from information the "
        "Ministry of Foreign Affairs provided and republished on the National Elections Commission's site. It gives "
        "each circle's seats, blank and null ballots, and each list's votes, with the councillors elected and their "
        "lists. It prints no registered voters or ballots cast. Valid votes are not printed and are not stored."
    ),
    2023: (
        "The final results and councillors elected by circle of the 26 November 2023 election, as the Directorate-"
        "General for Consular Affairs and Portuguese Communities published them (updated 5 January 2024) and the "
        "National Elections Commission republished them. Registered voters are the roll of the Ministry of Internal "
        "Administration. A list's votes are stored where the table prints a figure on that list's row; where it prints "
        "none, none is stored. Valid votes are not printed and are not stored."
    ),
}


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Council of Portuguese Communities, its electoral system, and the National Elections Commission's "
        "republication of its election results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(PORTUGAL)
    p.add(
        "source",
        id=SRC,
        kind="official_body",
        publisher="Comissão Nacional de Eleições",
        title="Elections for the Council of Portuguese Communities: official results and councillors elected (cne.pt)",
        url="https://www.cne.pt/content/eleicoes-referendos-resultados-oficiais",
        license="Official publication; reuse terms are not stated",
        attribution="Comissão Nacional de Eleições",
    )
    for year in DAYS:
        p.artifact(artifact(year), SRC)
    p.artifact(raw.cached(SOURCE, url(SEAT_MAP_2008)), SRC)
    p.add("body", id=BODY, polity_id=PORTUGAL, kind="council")
    p.name(BODY, "Council of Portuguese Communities", "en", "official")
    p.name(BODY, "Conselho das Comunidades Portuguesas", "pt", "native", False)
    p.add("electoral_system", id=SYSTEM, polity_id=PORTUGAL, rules={"formula": "dhondt"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Closed lists of candidates; the seats of each consular circle go to the lists by the D'Hondt method "
            "(Lei n.º 66-A/2007, art. 10.º)."
        ),
        lang="en",
    )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{DAYS[year]}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def election(year: int) -> Package:
    day = DAYS[year]
    found = circles(year)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} election for the Council of Portuguese Communities by consular circle, from the National "
        "Elections Commission's republication of the official results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(PORTUGAL)
    p.uses(artifact(year))
    if year == 2008:
        p.uses(raw.cached(SOURCE, url(SEAT_MAP_2008)))
    election_id = key("election", year)
    path = f"pt/{day}-portuguese-communities-council"
    p.add("election", id=election_id, polity_id=PORTUGAL, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{year} Council of Portuguese Communities election", "en", "official")
    p.name(election_id, f"Eleição para o Conselho das Comunidades Portuguesas de {year}", "pt", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    slugs = {path}
    values: list[tuple[str, str, str | None, int, int]] = []
    for circle in found:
        if not circle.held:
            continue
        _circle(p, year, circle, election_id, path, slugs, values)
    if values:
        sets, units, options, measures, numbers = zip(*values, strict=True)
        p.frame(
            "result_value",
            pl.DataFrame(
                {
                    "result_set_id": sets,
                    "unit_id": units,
                    "option_id": options,
                    "channel": [0] * len(numbers),
                    "measure": measures,
                    "count_round": [0] * len(numbers),
                    "flags": [0] * len(numbers),
                    "value": numbers,
                },
                schema={
                    "result_set_id": pl.String(),
                    "unit_id": pl.String(),
                    "option_id": pl.String(),
                    "channel": pl.Int16(),
                    "measure": pl.Int16(),
                    "count_round": pl.Int16(),
                    "flags": pl.Int16(),
                    "value": pl.Int64(),
                },
            ),
        )
    return p


def _circle(
    p: Package,
    year: int,
    circle: Circle,
    election_id: str,
    path: str,
    slugs: set[str],
    values: list[tuple[str, str, str | None, int, int]],
) -> None:
    day = DAYS[year]
    contest = key("contest", year, circle.code)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=BODY,
        district_area_id=area(year, circle.code),
        magnitude=circle.seats,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, circle.name, "pt", "official")
    base = f"{path}/{changeset.slug(circle.name) or 'circle'}"
    slug, n = base, 2
    while slug in slugs:
        slug, n = f"{base}-{n}", n + 1
    slugs.add(slug)
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    result_set = key("result_set", year, circle.code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SRC,
        status="active",
        is_default=True,
        completeness=0.0 if year == 2008 else 1.0,  # the 2008 map prints no counts, only who was elected
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD[year], lang="en")
    caveats = [text for text in (circle.caveat, SOURCE_ERRORS.get((year, circle.code))) if text]
    listed = [line.votes for line in circle.lines]
    if circle.cast is not None and None not in listed and circle.blank is not None and circle.invalid is not None:
        total = circle.blank + circle.invalid + sum(v for v in listed if v is not None)
        if total != circle.cast:
            caveats.append(
                f"The map prints {circle.cast:,} ballots cast, but its blank ({circle.blank:,}), null "
                f"({circle.invalid:,}), and list ({total - circle.blank - circle.invalid:,}) figures add up to "
                f"{total:,}. All are stored as printed."
            )
    for n, text in enumerate(caveats):
        p.add(
            "note",
            id=key("note", result_set, "caveat", n),
            entity_id=result_set,
            kind="caveat",
            body_md=text,
            lang="en",
        )
    lines = circle.lines
    if year == 2008:
        lines = [Line("Eleitos", None)]
    options: dict[str, str] = {}
    for position, line in enumerate(lines, 1):
        option = options[line.label] = key("option", year, circle.code, line.label)
        label = "Lista " + line.label if len(line.label) == 1 else f"Lista {line.label}"
        if line.title:
            label += f" ({line.title})"
        if year == 2008:
            label = "Conselheiros eleitos"
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=position,
            ballot_label=label,
        )
    if year != 2008:
        unit = key("unit", year, circle.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PORTUGAL,
            scheme_code=SCHEME,
            code=f"{day}:{circle.code}",
            area_relation="equals",
            area_id=area(year, circle.code),
            kind="aggregate",
        )
        p.name(unit, circle.name, "pt", "official")
        for measure, value in (
            (REGISTERED, circle.registered),
            (CAST, circle.cast),
            (BLANK, circle.blank),
            (INVALID, circle.invalid),
        ):
            if value is not None:
                values.append((result_set, unit, None, measure, value))
        for line in lines:
            if line.votes is not None:
                values.append((result_set, unit, options[line.label], VOTES, line.votes))
    for line in lines:
        if line.seats:
            p.add("outcome", result_set_id=result_set, option_id=options[line.label], kind="won", seats=line.seats)
    for position, person in enumerate(circle.elected, 1):
        label = "Eleitos" if year == 2008 else person.label
        if label is None:
            continue
        who = key("person", year, circle.code, position)
        p.add("person", id=who)
        p.name(who, person.name, "pt", "official")
        candidacy = key("candidacy", year, circle.code, position)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=who,
            option_id=options[label],
            role="list_member",
            list_position=position,
            ballot_name=person.name,
        )
        p.add("outcome", result_set_id=result_set, option_id=options[label], candidacy_id=candidacy, kind="elected")
