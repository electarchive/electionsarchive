"""The Azores regional elections of 2000 to 2020, by electoral circle, from the National Elections Commission's maps.

After each election the National Elections Commission (CNE) publishes the official map of results (mapa oficial) in the
Diário da República and on its own site. The map gives, for each of the nine island circles, the registered voters, the
voters, the blank and invalid ballots, each list's votes and its seats, and, from 2008, the seats of the regional
compensation circle. It does not go below the circle. The Ministry's results site carries only 2007 onward for Madeira
and 2024 for the Azores, so these six elections have no parish-level source here.

Each PDF has a text layer. `pdftotext -bbox` gives each word with its box; the words of a row become tokens, and every
stored number keeps the page and the box of the token it was read from.
"""

import re
import struct
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.pt.mai import (
    AZORES_ASSEMBLY,
    PORTUGAL,
    SYSTEM_LIST,
    circle_area,
    district_area,
)
from ea.store import DATA

SOURCE = "pt.cne_acores"
CNE = "https://www.cne.pt/sites/default/files/dl"
VOTES, REGISTERED, CAST, INVALID, BLANK = 1, 2, 3, 5, 6

ISLAND_NAMES = {
    "49": ("Corvo", "Corvo"),
    "47": ("Faial", "Faial"),
    "48": ("Flores", "Flores"),
    "44": ("Graciosa", "Graciosa"),
    "46": ("Pico", "Pico"),
    "41": ("Santa Maria", "Santa Maria"),
    "45": ("São Jorge", "São Jorge"),
    "42": ("São Miguel", "São Miguel"),
    "43": ("Terceira", "Terceira"),
}
# The order of the 2020 map's columns.
COLUMNS_2020 = ["49", "47", "48", "44", "46", "41", "45", "42", "43"]


@dataclass(frozen=True)
class Year:
    year: int
    day: date
    pdf: str
    # Leading cells of a circle's row: i registered, v voters, b blank, n invalid, e valid votes, p a percentage.
    lead: str
    # Percentage cells after each list's votes.
    percents: int
    # Lists in the order of the columns, one tuple per block of the map; a list is its label on the map.
    blocks: tuple[tuple[str, ...], ...]
    magnitude_column: bool = False


YEARS = [
    Year(
        2000,
        date(2000, 10, 15),
        "resultados_alraa_2000.pdf",
        "ivpbpnp",
        1,
        (("BE", "CDS-PP", "PCP-PEV", "PPD/PSD", "PPM/PDA", "PS"),),
    ),
    Year(
        2004,
        date(2004, 10, 17),
        "resultados_alraa_2004.pdf",
        "ivpbpnp",
        1,
        (("BE", "MPT", "PCP-PEV", "PDA", "PSD/PPD.CDS-PP", "PPM", "PS"),),
        True,
    ),
    Year(
        2008,
        date(2008, 10, 19),
        "resultados_alraacores_2008.pdf",
        "ivpbpnp",
        1,
        (("BE", "CDS-PP", "MPT", "PCP-PEV", "PDA", "PPD/PSD", "PPM", "PS"),),
        True,
    ),
    Year(
        2012,
        date(2012, 10, 14),
        "resultados_alraa_2012_mapa.pdf",
        "ivpbpnpe",
        2,
        (
            ("BE", "CDS-PP", "MPT", "PAN", "PCP-PEV"),
            ("PCTP/MRPP", "PDA", "PPD/PSD", "PPM", "PPM-PDA", "PS", "PTP"),
        ),
        True,
    ),
    Year(
        2016,
        date(2016, 10, 16),
        "alraa_2016_mapa_oficial_resultados_6_2016.pdf",
        "ivpebpnp",
        2,
        (
            ("BE", "CDS-PP", "L/TDA", "MAS", "MPT", "PAN"),
            ("PCP-PEV", "PCTP/MRPP", "PDR", "PPD/PSD", "PPM", "PS", "PURP"),
        ),
    ),
    Year(2020, date(2020, 10, 25), "2020_alraa_mapa_oficial_resultados.pdf", "", 0, ()),
]

# Party names from each map's legend.
NAMES = {
    "A": "Aliança",
    "BE": "Bloco de Esquerda",
    "CDS-PP": "CDS — Partido Popular",
    "CH": "Chega",
    "IL": "Iniciativa Liberal",
    "L": "LIVRE",
    "L/TDA": "LIVRE/Tempo de Avançar",
    "MAS": "Movimento Alternativa Socialista",
    "MPT": "Partido da Terra",
    "PAN": "Pessoas-Animais-Natureza",
    "PCP-PEV": "Coligação Democrática Unitária",
    "PCTP/MRPP": "Partido Comunista dos Trabalhadores Portugueses",
    "PDA": "Partido Democrático Atlântico",
    "PDR": "Partido Democrático Republicano",
    "PPD/PSD": "Partido Social Democrata",
    "PPM": "Partido Popular Monárquico",
    "PPM-PDA": "Plataforma de Cidadania",
    "PPM.CDS-PP": "Mais Corvo",
    "PPM/PDA": "Convergência Democrática Açoriana",
    "PS": "Partido Socialista",
    "PSD/PPD.CDS-PP": "Coligação Açores",
    "PTP": "Partido Trabalhista Português",
    "PURP": "Partido Unido dos Reformados e Pensionistas",
}
BLOCS = {"PCP-PEV", "PPM/PDA", "PPM-PDA", "PPM.CDS-PP", "PSD/PPD.CDS-PP"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CNE_SOURCE = key("source", "cne")
COMPENSATION = "compensation"


# ---------------------------------------------------------------------------------------------------------------------
# Reading the maps


@dataclass
class Word:
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Token:
    kind: str  # int, pct, dash, nc, dec, text
    text: str
    page: int
    box: tuple[float, float, float, float]

    @property
    def value(self) -> int:
        return int(self.text.replace(" ", ""))


@dataclass
class Cell:
    value: int
    page: int
    box: tuple[float, float, float, float]


@dataclass
class Circle:
    cells: dict[str, Cell] = field(default_factory=dict)
    votes: dict[str, Cell] = field(default_factory=dict)
    seats: dict[str, int] = field(default_factory=dict)


WORD = re.compile(
    r'<page width="([\d.]+)" height="([\d.]+)">|<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>'
)


def words(path: Path) -> tuple[list[Word], dict[int, tuple[float, float]]]:
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    found: list[Word] = []
    sizes: dict[int, tuple[float, float]] = {}
    page = 0
    for match in WORD.finditer(out):
        if match.group(1):
            page += 1
            sizes[page] = (float(match.group(1)), float(match.group(2)))
            continue
        text = match.group(7).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        found.append(
            Word(text, page, float(match.group(3)), float(match.group(4)), float(match.group(5)), float(match.group(6)))
        )
    return found, sizes


def rows(found: list[Word]) -> list[list[Word]]:
    """Words grouped into printed lines, by page and the middle of the word's height, each line left to right."""
    lines: list[list[Word]] = []
    for word in sorted(found, key=lambda w: (w.page, (w.y0 + w.y1) / 2, w.x0)):
        middle = (word.y0 + word.y1) / 2
        for line in reversed(lines[-6:]):
            if line[0].page == word.page and abs((line[0].y0 + line[0].y1) / 2 - middle) <= 3.3:
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


INT = re.compile(r"^\(?\d{1,3}\)?$|^\d+$")
GROUP = re.compile(r"^\d{3}$")
PCT = re.compile(r"^\(?\d+,\d{1,2}\)?$")
DEC = re.compile(r"^\(?\d+,\d{3,}\)?$")
DASHES = {"–", "-", "—", "−"}


def tokens(line: list[Word]) -> list[Token]:
    out: list[Token] = []
    i = 0
    while i < len(line):
        w = line[i]
        text = w.text.strip("()")
        box = (w.x0, w.y0, w.x1, w.y1)
        if w.text in DASHES:
            out.append(Token("dash", w.text, w.page, box))
        elif w.text == "n." and i + 1 < len(line) and line[i + 1].text == "c.":
            nxt = line[i + 1]
            out.append(Token("nc", "n.c.", w.page, (w.x0, w.y0, nxt.x1, nxt.y1)))
            i += 1
        elif w.text in ("n.c.", "n.c"):
            out.append(Token("nc", "n.c.", w.page, box))
        elif re.fullmatch(r"\d+", text):
            x1 = w.x1
            joined = text
            # Thousands are printed with a space: "13 013".
            while (
                i + 1 < len(line)
                and re.fullmatch(r"\d{3}", line[i + 1].text.strip("()"))
                and len(joined) <= 3
                and line[i + 1].x0 - x1 < 4.0
            ):
                joined += line[i + 1].text.strip("()")
                x1 = line[i + 1].x1
                i += 1
            out.append(Token("int", joined, w.page, (w.x0, w.y0, x1, w.y1)))
        elif PCT.match(w.text):
            out.append(Token("pct", text, w.page, box))
        elif DEC.match(w.text):
            out.append(Token("dec", text, w.page, box))
        elif re.fullmatch(r"\d{1,3}(,\d+)?", text):
            out.append(Token("pct", text, w.page, box))
        else:
            out.append(Token("text", w.text, w.page, box))
        i += 1
    return out


def label_of(line: list[Word]) -> tuple[str, int]:
    """The row's label (words before the first number) and the index where its cells begin."""
    parts: list[str] = []
    for i, word in enumerate(line):
        if re.fullmatch(r"[\d.,()–-]+", word.text) and word.text != ".":
            return " ".join(parts), i
        if word.text in ("n.", "n.c.", "n.c"):
            return " ".join(parts), i
        if word.text != ".":
            parts.append(word.text)
    return " ".join(parts), len(line)


def fold(text: str) -> str:
    return re.sub(r"[^a-zãçéíóú ]", "", text.lower()).strip()


def circle_of(label: str) -> str | None:
    name = fold(label)
    for island, (english, _) in ISLAND_NAMES.items():
        if name == fold(english):
            return island
    if name.startswith(("compensa", "círculo de compensa", "circulo")):
        return COMPENSATION
    if name.startswith("totais"):
        return "total"
    return None


def parse_row(
    spec: Year, block: int, line: list[Word], first: bool
) -> tuple[dict[str, Cell], dict[str, Cell], dict[str, int]]:
    label, start = label_of(line)
    toks = tokens(line[start:])
    cells: dict[str, Cell] = {}
    votes: dict[str, Cell] = {}
    seats: dict[str, int] = {}
    i = 0
    if first:
        for kind in spec.lead:
            tok = toks[i]
            if kind == "p":
                if tok.kind != "pct":
                    raise ValueError(f"{spec.year} {label}: expected a percentage, found {tok.text!r}")
                i += 1
                continue
            if tok.kind != "int":
                raise ValueError(f"{spec.year} {label}: expected a number for {kind}, found {tok.text!r}")
            cells[kind] = Cell(tok.value, tok.page, tok.box)
            i += 1
    parties = spec.blocks[block]
    for n, party in enumerate(parties):
        last = n == len(parties) - 1
        if i >= len(toks):
            raise ValueError(f"{spec.year} {label}: ran out of cells at {party}")
        tok = toks[i]
        if tok.kind == "nc":
            i += 1
            while i < len(toks) and toks[i].kind in ("dash", "pct"):
                i += 1
            continue
        if tok.kind != "int":
            raise ValueError(f"{spec.year} {label}: expected votes for {party}, found {tok.text!r}")
        votes[party] = Cell(tok.value, tok.page, tok.box)
        i += 1
        taken = 0
        while taken < spec.percents and i < len(toks) and toks[i].kind in ("pct", "dash", "dec"):
            i += 1
            taken += 1
        seat = 0
        if i < len(toks):
            if toks[i].kind == "dash":
                i += 1
            elif toks[i].kind == "int":
                after = toks[i + 1] if i + 1 < len(toks) else None
                if last or after is None or after.kind not in ("pct", "dash"):
                    seat = toks[i].value
                    i += 1
        seats[party] = seat
    return cells, votes, seats


def parse_rows(spec: Year, path: Path) -> dict[str, Circle]:
    found, _ = words(path)
    circles: dict[str, Circle] = {}
    block = -1
    for line in rows(found):
        label, start = label_of(line)
        which = circle_of(label)
        if which is None or start >= len(line):
            continue
        if which == "49":
            block += 1
        if block < 0 or block >= len(spec.blocks):
            continue
        if which == COMPENSATION:
            continue
        cells, votes, seats = parse_row(spec, block, line, block == 0)
        circle = circles.setdefault(which, Circle())
        circle.cells.update(cells)
        circle.votes.update(votes)
        circle.seats.update(seats)
    return circles


def parse_2020(path: Path) -> dict[str, Circle]:
    """The 2020 map is turned on its side: one row per list, one column per circle."""
    found, _ = words(path)
    columns = [*COLUMNS_2020, COMPENSATION, "total"]
    circles = {c: Circle() for c in columns}
    lead = {"inscritos": "i", "votantes": "v", "brancos": "b", "nulos": "n", "votos": "e"}
    seen: set[str] = set()
    party: str | None = None
    for line in rows(found):
        head = line[0].text
        at = next((i for i, w in enumerate(line) if w.text == "Número"), None)
        start = at + 1 if at is not None else None
        if head == "MD" and party is not None:
            cells = [t for t in tokens(line[1:]) if t.kind in ("int", "dash")][:11]
            if len(cells) == 11:
                for column, tok in zip(columns, cells, strict=True):
                    circles[column].seats[party] = tok.value if tok.kind == "int" else 0
            continue
        if head.lower() == "inscritos":
            start, name = 1, "inscritos"
        elif at is not None:
            name = " ".join(w.text for w in line[:at] if w.text != ".").rstrip(".").strip()
        else:
            continue
        cells = [t for t in tokens(line[start:]) if t.kind in ("int", "nc", "dash", "dec", "pct")]
        if len(cells) < 11:
            continue
        cells = cells[:11]
        first = fold(name).split(" ")[0] if name else ""
        name = name.replace(" ", "")
        if first in lead:
            kind = lead[first]
            if kind in seen:
                continue
            seen.add(kind)
            for column, tok in zip(columns, cells, strict=True):
                if tok.kind == "int":
                    circles[column].cells[kind] = Cell(tok.value, tok.page, tok.box)
            continue
        party = "BE" if name in ("B.E", "B.E.") else name.rstrip(".")
        if party in seen:
            party = None
            continue
        seen.add(party)
        for column, tok in zip(columns, cells, strict=True):
            if tok.kind == "int":
                circles[column].votes[party] = Cell(tok.value, tok.page, tok.box)
    return circles


def circles_of(spec: Year) -> dict[str, Circle]:
    path = artifact(spec).path
    return parse_2020(path) if spec.year == 2020 else parse_rows(spec, path)


def compensation_seats(spec: Year, circles: dict[str, Circle]) -> dict[str, int]:
    """Seats of the compensation circle: printed in its 2020 column; before that, the printed national seats less the
    island circles' seats."""
    if spec.year < 2008:
        return {}
    if spec.year == 2020:
        return {party: n for party, n in circles[COMPENSATION].seats.items() if n}
    out: dict[str, int] = {}
    for party, n in circles["total"].seats.items():
        left = n - sum(circles[code].seats.get(party, 0) for code in ISLAND_NAMES)
        if left < 0:
            raise ValueError(f"{spec.year}: {party} has more island seats than the national total")
        if left:
            out[party] = left
    return out


def label_party(label: str) -> str:
    return key("party", label)


def check(spec: Year, circles: dict[str, Circle]) -> list[str]:
    """Where the maps' own figures disagree: each circle's votes against its valid votes, and the island circles
    against the printed national totals."""
    problems: list[str] = []
    for code in ISLAND_NAMES:
        c = circles[code]
        valid = c.cells["v"].value - c.cells["b"].value - c.cells["n"].value
        listed = sum(cell.value for cell in c.votes.values())
        if listed != valid:
            problems.append(f"{spec.year} {ISLAND_NAMES[code][0]}: lists add up to {listed}, valid votes are {valid}")
    total = circles.get("total")
    if total is not None and spec.year != 2004:
        for kind in "ivbn":
            summed = sum(circles[code].cells[kind].value for code in ISLAND_NAMES)
            if summed != total.cells[kind].value:
                problems.append(
                    f"{spec.year} {kind}: circles add up to {summed}, the map prints {total.cells[kind].value}"
                )
        for party, cell in total.votes.items():
            summed = sum(circles[code].votes[party].value for code in ISLAND_NAMES if party in circles[code].votes)
            if summed != cell.value:
                problems.append(f"{spec.year} {party}: circles add up to {summed}, the map prints {cell.value}")
    return problems


def fetch() -> None:
    for spec in YEARS:
        raw.fetch(SOURCE, f"{CNE}/{spec.pdf}", suffix=".pdf")


def artifact(spec: Year) -> raw.Artifact:
    return raw.cached(SOURCE, f"{CNE}/{spec.pdf}")


# ---------------------------------------------------------------------------------------------------------------------
# Packages

METHOD = (
    "Counted by electoral circle: one for each of the nine islands, as published in the National Elections "
    "Commission's official map of results (mapa oficial), which the Commission also prints in the Diário da "
    "República. The map goes no finer than the circle. Registered voters are the voters on the roll, the voters are "
    "those who voted, and blank and invalid (nulos) ballots are printed apart from the votes for each list. Seats "
    "are as the map declares them."
)
COMPENSATION_METHOD = (
    "The regional compensation circle elects the seats that bring the lists' totals closer to proportionality "
    "across the region. It has no ballots of its own: it works from each list's votes in the whole region, which "
    "the map prints in its row of regional totals and which are stored here as one count for the region. The map's "
    "own figures for the circle are those totals divided by the divisors the allocation reached, not votes, and are "
    "not stored. The seats are those the map declares: printed in the circle's column in 2020, and in earlier "
    "years the printed national seats less the seats the nine island circles won."
)
SYSTEM_NOTE = "d'Hondt in each island circle and, from 2008, in the regional compensation circle."


def artifact_id(spec: Year) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CNE_SOURCE}\x1f{artifact(spec).storage_key}"))


DPI = 110


def render(spec: Year) -> dict[int, tuple[str, int, int]]:
    """Each page as a grayscale image, with its key and size in pixels."""
    source = artifact(spec)
    folder = DATA / "pages" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / f"{source.sha256}"
    subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", "-png", str(source.path), str(stem)], check=True)
    out: dict[int, tuple[str, int, int]] = {}
    for image in sorted(folder.glob(f"{source.sha256}-*.png")):
        width, height = struct.unpack(">II", image.read_bytes()[16:24])
        out[int(image.stem.rsplit("-", 1)[1])] = (f"pages/{SOURCE}/{image.name}", width, height)
    return out


def day_key(spec: Year) -> str:
    return f"{SOURCE}/{spec.day}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(day_key(spec) for spec in YEARS)]


def labels() -> list[str]:
    seen: dict[str, None] = {}
    for spec in YEARS:
        for block in spec.blocks:
            seen.update(dict.fromkeys(block))
        if spec.year == 2020:
            seen.update(dict.fromkeys(circles_of(spec)["total"].votes))
    return list(seen)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The parties and coalitions of the Azores regional elections of 2000 to 2020, and the National Elections "
        "Commission as their source",
        depends=["pt.mai/geo", "pt.mai/registry"],
    )
    p.external.add(PORTUGAL)
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Comissão Nacional de Eleições",
        title="Mapas oficiais dos resultados das eleições para a Assembleia Legislativa da Região Autónoma dos Açores",
        url="https://www.cne.pt/content/eleicoes-referendos-resultados-oficiais#acores",
        license="Official publication in the Diário da República; reuse terms are not stated",
        attribution="Comissão Nacional de Eleições",
    )
    for spec in YEARS:
        p.artifact(artifact(spec), CNE_SOURCE)
    for label in labels():
        id = label_party(label)
        p.add("party", id=id, kind="bloc" if label in BLOCS else "party", polity_id=PORTUGAL)
        p.add("external_id", entity_id=id, scheme="pt.cne_acores.party", value=label)
        p.name(id, NAMES[label], "pt", "official")
        p.name(id, label, "pt", "abbreviation", False)
    return p


def election(spec: Year) -> Package:
    day = spec.day
    p = Package(
        day_key(spec),
        SOURCE,
        f"The Azores regional election of {day} by electoral circle, from the National Elections Commission's "
        "official map of results",
        depends=[f"{SOURCE}/registry", "pt.mai/geo", "pt.mai/registry"],
    )
    p.external.add(PORTUGAL)
    p.uses(artifact(spec))
    circles = circles_of(spec)
    problems = check(spec, circles)
    seats = compensation_seats(spec, circles)
    election_id = key("election", day)
    path = f"pt/{day}-azores-regional"
    p.add("election", id=election_id, polity_id=PORTUGAL, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{day.year} Azores regional election", "en", "official")
    p.name(election_id, f"Eleições legislativas regionais dos Açores de {day.year}", "pt", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    page_ids: dict[int, str] = {}
    for number, (image, width, height) in render(spec).items():
        page_ids[number] = key("page", day, number)
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id(spec),
            page_number=number,
            image_key=image,
            width=width,
            height=height,
        )
    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    parties = list(circles["total"].votes) if "total" in circles and spec.year != 2004 else []
    order = [label for block in spec.blocks for label in block] if spec.blocks else parties
    if spec.year == 2020:
        order = list(circles["total"].votes)
    ran = [label for label in order if any(label in circles[code].votes for code in ISLAND_NAMES)]
    for code, (english, portuguese) in sorted(ISLAND_NAMES.items(), key=lambda item: item[1][0]):
        circle = circles[code]
        contest = key("contest", day, code)
        magnitude = sum(circle.seats.values())
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=AZORES_ASSEMBLY,
            district_area_id=circle_area(code),
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, english, "en", "official")
        p.add("slug", path=f"{path}/{changeset_slug(english)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, code)
        _result_set(p, result_set, election_id, contest, METHOD)
        unit = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PORTUGAL,
            scheme_code="pt.circulo",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=circle_area(code),
            kind="aggregate",
        )
        p.name(unit, portuguese, "pt", "official")
        options: dict[str, str] = {}
        for position, label in enumerate((l for l in order if l in circle.votes), 1):
            option = options[label] = key("option", day, code, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=label_party(label),
                ballot_position=position,
                ballot_label=label,
            )
        measured = [
            (REGISTERED, circle.cells["i"]),
            (CAST, circle.cells["v"]),
            (BLANK, circle.cells["b"]),
            (INVALID, circle.cells["n"]),
        ]
        for measure, cell in measured:
            _value(values, locators, page_ids, result_set, unit, None, measure, cell)
        for label, option in options.items():
            _value(values, locators, page_ids, result_set, unit, option, VOTES, circle.votes[label])
            if circle.seats.get(label):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=circle.seats[label])
    if seats:
        code = COMPENSATION
        contest = key("contest", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=AZORES_ASSEMBLY,
            district_area_id=district_area("40"),
            magnitude=sum(seats.values()),
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, "Regional compensation circle", "en", "official")
        p.name(contest, "Círculo Regional de Compensação", "pt", "native", False)
        p.add("slug", path=f"{path}/regional-compensation-circle", entity_id=contest, is_current=True)
        result_set = key("result_set", day, code)
        _result_set(p, result_set, election_id, contest, COMPENSATION_METHOD)
        region = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=region,
            polity_id=PORTUGAL,
            scheme_code="pt.circulo",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=district_area("40"),
            kind="aggregate",
        )
        p.name(region, "Região Autónoma dos Açores", "pt", "official")
        whole = circles["total"]
        for measure, kind in ((REGISTERED, "i"), (CAST, "v"), (BLANK, "b"), (INVALID, "n")):
            _value(values, locators, page_ids, result_set, region, None, measure, whole.cells[kind])
        for position, label in enumerate(ran, 1):
            option = key("option", day, code, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=label_party(label),
                ballot_position=position,
                ballot_label=label,
            )
            if label in whole.votes:
                _value(values, locators, page_ids, result_set, region, option, VOTES, whole.votes[label])
            if seats.get(label):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats[label])
    unknown = set(seats) - set(ran)
    if unknown:
        raise ValueError(f"{day}: compensation seats for lists that ran nowhere: {sorted(unknown)}")
    _frames(p, values, locators)
    for problem in problems:
        print(problem)
        p.add(
            "note",
            id=key("note", day, problem),
            entity_id=key("result_set", day, next(c for c, (n, _) in ISLAND_NAMES.items() if n in problem)),
            kind="caveat",
            body_md=f"The map's own figures do not add up here: {problem[5:]}. They are stored as printed.",
            lang="en",
        )
    return p


def changeset_slug(text: str) -> str:
    from ea import changeset

    return changeset.slug(text)


def _result_set(p: Package, id: str, election_id: str, contest: str, method: str) -> None:
    p.add(
        "result_set",
        id=id,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CNE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")


def _value(
    values: list[tuple[str, str, str | None, int, int]],
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]],
    page_ids: dict[int, str],
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    cell: Cell,
) -> None:
    values.append((result_set, unit, option, measure, cell.value))
    scale = DPI / 72
    x0, y0, x1, y1 = (round(c * scale, 1) for c in cell.box)
    locators.append((result_set, unit, option, measure, page_ids[cell.page], x0, y0, x1, y1))


def _frames(
    p: Package,
    values: list[tuple[str, str, str | None, int, int]],
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]],
) -> None:
    key_schema = {"result_set_id": pl.String(), "unit_id": pl.String(), "option_id": pl.String(), "measure": pl.Int16()}
    p.frame(
        "result_value",
        pl.DataFrame(values, schema={**key_schema, "value": pl.Int64()}, orient="row").with_columns(
            channel=pl.lit(0, pl.Int16()), count_round=pl.lit(0, pl.Int16()), flags=pl.lit(0, pl.Int16())
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema={
                **key_schema,
                "page_id": pl.String(),
                "x0": pl.Float64(),
                "y0": pl.Float64(),
                "x1": pl.Float64(),
                "y1": pl.Float64(),
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16()), count_round=pl.lit(0, pl.Int16())),
    )


def parse() -> None:
    registry().write()
    for spec in YEARS:
        election(spec).write()


def emit() -> None:
    package.run("load", packages())
