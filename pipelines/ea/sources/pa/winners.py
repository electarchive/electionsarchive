"""Panama's Tribunal Electoral: the candidates it declared elected, read from its proclamation tables and lists.

The parsers here return what each table prints and nothing more. `ea.sources.pa.te` ties each winner to the contest
and party option it already holds for the same place.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

NUMBER = r"\d[\d,]*"


@dataclass
class Winner:
    name: str
    party: str  # the party, alliance, or free-nomination label as printed
    votes: int | None
    how: str | None = None  # how the seat was won in a plurinominal circuit, as printed


@dataclass
class Place:
    """The winners the Tribunal proclaimed for one place, keyed by the labels the table prints for it."""

    path: tuple[str, ...]  # a circuit code; a province and district; or a province, district, and corregimiento
    winners: list[Winner]
    seats: int | None = None  # the circuit's seats when the table prints them
    kind: str | None = None  # "Plurinominal" or "Uninominal"
    note: str | None = None


def number(text: str) -> int:
    return int(text.replace(",", ""))


def clean(text: str) -> str:
    return " ".join(str(text).replace("\xa0", " ").split())


def cell_text(cell: object) -> str:
    return "" if cell is None else clean(str(cell))


def cell_number(cell: object) -> int | None:
    text = cell_text(cell).replace(",", "")
    return int(float(text)) if re.fullmatch(r"\d+(\.0+)?", text) else None


# 2024 workbooks --------------------------------------------------------------------------------------------------


# The columns of Cuadro 6 holding the circuit code, its type, and each deputy's name, votes, party, and how the seat
# was won. The 2004 and 2019 tables print a column of the circuit's voters before the code; 2019 adds a valid-votes
# column before the names.
DEPUTY_COLUMNS_2024 = (0, 1, 7, 8, 10, 11)
DEPUTY_COLUMNS_2019 = (1, 2, 8, 9, 11, 12)
DEPUTY_COLUMNS_2004 = (1, 2, 7, 8, 10, 11)


def xlsx_deputies(grid: list[list[object]], columns: tuple[int, ...] = DEPUTY_COLUMNS_2024) -> list[Place]:
    """Cuadro 6: the deputies proclaimed in each circuit, with how each won the seat."""
    at_code, at_kind, at_name, at_votes, at_party, at_how = columns
    places: dict[str, Place] = {}
    code = kind = ""
    for row in grid:
        cells = [*row, *([None] * 14)]
        if re.fullmatch(r"\d+\.\d+", cell_text(cells[at_code])):
            code, kind = cell_text(cells[at_code]), cell_text(cells[at_kind])
            if code in places:
                code += "0"  # a code stored as a number: circuit 8.10 reads as 8.1, after 8.9
        name, votes = cell_text(cells[at_name]), cell_number(cells[at_votes])
        if not code or not name or votes is None:
            continue
        place = places.get(code)
        if place is None:
            match = re.fullmatch(r"(Plurinominal|Uninominal)\s*(?:\((\d+)\))?", kind)
            if match is None:
                raise ValueError(f"circuit {code}: unknown type {kind!r}")
            seats = int(match[2]) if match[2] else 1
            place = places[code] = Place((code,), [], seats, match[1])
        place.winners.append(Winner(name, cell_text(cells[at_party]), votes, cell_text(cells[at_how]) or None))
    return list(places.values())


def xlsx_mayors(grid: list[list[object]]) -> list[Place]:
    """Cuadro 10: the mayor proclaimed in each district."""
    places: list[Place] = []
    province = ""
    for row in grid:
        cells = [*row, *([None] * 5)]
        votes = cell_number(cells[3])
        if cell_text(cells[0]):
            province = cell_text(cells[0])
        if not cell_text(cells[1]) or not cell_text(cells[2]) or votes is None:
            continue
        places.append(Place((province, cell_text(cells[1])), [Winner(cell_text(cells[2]), cell_text(cells[4]), votes)]))
    return places


def xlsx_representatives(grid: list[list[object]], is_province: Callable[[str, int], bool]) -> list[Place]:
    """Cuadro 13: the representative proclaimed in each corregimiento, under its province and district.

    The table prints some districts flush left, so a line with no candidate is a province only when `is_province`
    says so for its text and indent. A comarca's corregimientos with no district line carry an empty district, and a
    comarca printed flush left with its own representative (the Kuna comarcas of 2004 and 2019) is its own
    corregimiento. A corregimiento with no proclamation (2004) lists its candidates on the lines below it, the last of
    them beside the next district's name.
    """
    places: list[Place] = []
    province = district = ""
    unproclaimed: int | None = None  # the indent of a corregimiento whose candidates follow, none proclaimed
    for row in grid:
        cells = [*row, *([None] * 4)]
        label = "" if cells[0] is None else str(cells[0]).replace("\xa0", " ").rstrip()
        if not label.strip():
            continue
        indent = len(label) - len(label.lstrip())
        name, votes = cell_text(cells[1]), cell_number(cells[3])
        if name.upper().startswith("SIN PROCLAMACI"):
            unproclaimed = indent
            continue
        if unproclaimed is not None:
            if indent < unproclaimed and name:
                name, votes = "", None
                cells = [cells[0], None, None, None]
            unproclaimed = None
        if name and votes is not None and indent == 0 and clean(label).lower().startswith("comarca"):
            places.append(Place((clean(label), "", clean(label)), [Winner(name, cell_text(cells[2]), votes)]))
        elif name and votes is not None:
            places.append(Place((province, district, clean(label)), [Winner(name, cell_text(cells[2]), votes)]))
        elif not name and not cell_text(cells[2]) and not cell_text(cells[3]):
            if is_province(clean(label), len(label) - len(label.lstrip())):
                province, district = clean(label), ""
            elif province:
                district = clean(label)
    return [p for p in places if p.path[0]]


# 1994 and 1999 tables --------------------------------------------------------------------------------------------

TAIL = re.compile(rf"(?P<name>\S.*?)\s+(?P<votes>{NUMBER})\s+\(?\s*(?P<pct>\d+\.\d)%\)?\s+(?P<party>[^\s].*?)\s*$")


def candidate(text: str) -> Winner | None:
    """A candidate's name, votes, share, and party off the end of a printed line."""
    match = TAIL.search(text)
    if match is None:
        return None
    party, *rest = re.split(r"\s{2,}", match["party"])
    return Winner(clean(match["name"]), clean(party), number(match["votes"]), clean(" ".join(rest)) or None)


CIRCUIT = re.compile(
    rf"^\s*(?P<code>\d+\.\d+)\s+(?P<kind>Plurinominal|Uninominal)\s+(?P<pop>{NUMBER})\s+\d+\.\d%?\s+\d+\.\d%?\s+"
    rf"(?P<valid>{NUMBER})\s+(?P<rest>.*)$"
)
RANK = re.compile(r"^\s*\(\d+\)\s*")
HOW = ("Medio Cuociente", "Cuociente", "Residuo", "Candidato más votado", "Candidato Más Votado")


def pdf_deputies(lines: list[str]) -> list[Place]:
    """The 1994 and 1999 tables of legislators elected: each circuit prints its type, its winners, and its seats.

    A 1994 circuit prints its seats at the end of its first line, a 1999 circuit under its type.
    """
    places: list[Place] = []
    current: Place | None = None
    for line in lines:
        head = CIRCUIT.match(line)
        if head:
            current = Place((head["code"],), [], None, head["kind"])
            places.append(current)
            text = head["rest"]
            first = True
        elif current and re.match(r"^\s{20,}\S", line):
            text = line
            first = False
            lead = re.match(r"^\s+(\d{1,2})(?:\s{8,}(.*))?$", line)
            if lead and current.seats is None:
                current.seats = int(lead[1])
                text = lead[2] or ""
        else:
            continue
        text = RANK.sub("", text.strip())
        how = next((h for h in HOW if text.endswith(h)), None)
        if how:
            text = text[: -len(how)].rstrip()
        if first and current.kind == "Plurinominal" and (trailing := re.search(r"\s{2,}(\d{1,2})$", text)):
            current.seats = int(trailing[1])
            text = text[: trailing.start()]
        winner = candidate(text) if text else None
        if winner is None:
            continue
        winner.how = how or winner.how
        current.winners.append(winner)
    for place in places:
        if place.kind == "Uninominal":
            place.seats = 1
        extra = [w for w in place.winners if w.how == "Llanero Solitario"]
        if extra and place.seats is not None and len(place.winners) == place.seats + len(extra):
            place.seats = len(place.winners)
            place.note = (
                f"The table prints {place.seats - len(extra)} seats for this circuit and marks "
                f"{extra[0].name} as an additional winner, a \u201cLlanero Solitario\u201d."
            )
    return places


MAYOR = re.compile(rf"^(?P<left>.*?)\s+(?P<pop>{NUMBER})\s+(?P<valid>{NUMBER})\s+(?P<rest>\S.*)$")


def pdf_mayors(lines: list[str]) -> list[Place]:
    """The 1994 and 1999 tables of mayors proclaimed: each row has a district, its roll, its valid votes, and a winner."""
    places: list[Place] = []
    province = ""
    for line in lines:
        match = MAYOR.match(line)
        if match is None or not re.match(r"^[A-ZÁÉÍÓÚÑ]|^\s{10,}\S", line):
            continue
        winner = candidate(match["rest"])
        if winner is None:
            continue
        parts = re.split(r"\s{2,}", match["left"].strip())
        if len(parts) == 2:
            province, district = parts
        else:
            district = parts[0]
        if not province or not district:
            continue
        places.append(Place((province, district), [winner]))
    return places


# 2014 tables ------------------------------------------------------------------------------------------------------

PROCLAIMED = re.compile(
    rf"^(?P<label>\s*\S.*?)\s{{2,}}(?P<name>\S.*?)\s{{2,}}(?P<party>\S.*?)\s{{2,}}(?P<votes>{NUMBER})$"
)
TIE = re.compile(
    rf"^(?P<label>\s*\S.*?)\s*\(EMPATE\)\s{{2,}}(?P<names>\S.*?)\s{{2,}}(?P<party>\S.*?)\s{{2,}}(?P<votes>{NUMBER})-(?P=votes)$"
)
WRAPPED = re.compile(rf"^(?P<label>\s*\S.*?)\s{{2,}}(?P<party>\S.*?)\s{{2,}}(?P<votes>{NUMBER})$")


def party(text: str) -> str:
    """An alliance printed as `CD / MOLIRENA`, written as the other tables write one: `CD, MOLIRENA`."""
    return ", ".join(clean(part) for part in text.split("/"))


def pdf_representatives(lines: list[str]) -> list[Place]:
    """The 2014 Cuadro 13: the representative proclaimed in each corregimiento, under its province or comarca, printed
    in capitals, and its district. A comarca of one corregimiento prints its winner on its own line, and the
    corregimientos of a comarca with no districts carry an empty district. A tie prints both candidates and no
    winner; its place has no winners and a note that says so."""
    places: list[Place] = []
    province = district = ""
    body = False
    for line in lines:
        text = line.rstrip()
        if "OBTENIDOS" in text:
            body = True
            continue
        if not body or not text.strip():
            continue
        if text.strip().startswith(("FUENTE", "FECHA", "TRIBUNAL ELECTORAL")):
            body = False
            continue
        if places and places[-1].winners and not places[-1].winners[0].name and not re.search(r"\d", text):
            places[-1].winners[0].name = clean(text)  # a name the table wraps onto the next line
        elif match := TIE.match(text):
            names = " and ".join(clean(n) for n in match["names"].split("/"))
            note = (
                f"The Tribunal's table of representatives proclaimed prints a tie here between {names}, with "
                f"{number(match['votes']):,} votes each, and gives their parties as \u201c{clean(match['party'])}\u201d. "
                "It names no single winner, so no winner is recorded."
            )
            places.append(Place((province, district, clean(match["label"])), [], note=note))
        elif match := PROCLAIMED.match(text) or WRAPPED.match(text):
            label = clean(match["label"])
            winner = Winner(clean(match.groupdict().get("name") or ""), party(match["party"]), number(match["votes"]))
            if label.isupper():
                places.append(Place((label, "", label), [winner]))
            else:
                places.append(Place((province, district, label), [winner]))
        elif re.search(r"\d", text):
            continue  # a page number
        elif clean(text).isupper():
            province, district = clean(text), ""
        else:
            district = clean(text)
    return places


# 2014 proclamation lists ----------------------------------------------------------------------------------------


def pdf_list(lines: list[str], place: str) -> list[Place]:
    """The 2014 lists of candidates proclaimed: each block names its place and gives each winner's postulation."""
    places: dict[tuple[str, ...], Place] = {}
    province = district = circuit = ""
    name = ""
    for line in lines:
        text = line.strip()
        if match := re.match(r"PROVINCIA:\s*(.+)$", text):
            province = clean(match[1])
        elif match := re.match(r"DISTRITO:\s*(.+)$", text):
            district = clean(match[1])
        elif match := re.match(r"CIRCUITO:\s*(\d+)-(\d+)$", text):
            circuit = f"{match[1]}.{match[2]}"
        elif match := re.match(r"Principal:?\s+(.+?)\s+C[ée]d:", text):
            name = clean(match[1])
        elif match := re.match(r"Postulado\(a\) por:\s*(.+)$", text):
            if not name:
                raise ValueError(f"a postulation with no candidate under {province} {district or circuit}")
            path = (circuit,) if place == "circuit" else (province, district)
            party = clean(re.sub(r"\(inscrito\)", "", match[1]))
            places.setdefault(path, Place(path, [])).winners.append(Winner(name, party, None))
            name = ""
    return list(places.values())


# Seats by party ---------------------------------------------------------------------------------------------------


def xlsx_party_seats(grid: list[list[object]]) -> dict[str, int]:
    """The seats each party won, from the 2024 table of seats by party and proclamation type."""
    seats: dict[str, int] = {}
    lines = [(cell_text(row[0]) if row else "", cell_number([*row, None, None, None, None][3])) for row in grid]
    for (label, won), (after, after_won) in zip(lines, [*lines[1:], ("", None)], strict=True):
        if not label or won is None or label.lower() in ("total", "totales"):
            continue
        if label.startswith("*") and after and not after.startswith("*") and after_won is not None:
            continue  # an alliance's line; its parties follow
        seats[clean(label.strip("* "))] = won
    return seats


def pdf_party_seats(lines: list[str]) -> dict[str, int]:
    """The seats each party won, from the 1994 and 1999 tables of seats by party."""
    seats: dict[str, int] = {}
    for line in lines:
        match = re.match(
            rf"^\s*(?P<name>[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .]+?)\s{{2,}}(?P<votes>{NUMBER})\s+[\d.]+\s+(?P<seats>\d+)\s+[\d.]+\s",
            line,
        )
        if match and not match["name"].startswith(("TOTAL", "CINCO")):
            seats[clean(match["name"])] = int(match["seats"])
    return seats


def pdf_president(lines: list[str]) -> tuple[Winner, Winner | None]:
    """The president and vice president the Tribunal proclaimed, from its list of candidates in force."""
    head = mate = None
    party = ""
    for line in lines:
        if match := re.match(r"\s*PRESIDENTE\s+(.+?)\s+\d+-\d+-\d+\s*$", line):
            head = clean(match[1])
        elif match := re.match(r"\s*VICEPRESIDENT[EA]\s+(.+?)\s+\d+-\d+-\d+\s*$", line):
            mate = clean(match[1])
        elif match := re.match(r"\s*Postulado\(a\) por:\s*(.+?)\s*$", line):
            party = clean(match[1])
    if head is None or not party:
        raise ValueError("no president in the list")
    return Winner(head, party, None), Winner(mate, party, None) if mate else None


def pdf_president_list(lines: list[str]) -> tuple[Winner, Winner | None]:
    """The president and vice president from the Tribunal's 2019 list of winners, which prints the names they ran
    under and the parties that postulated them."""
    head = mate = None
    for line in lines:
        if match := re.match(r"\s*PRESIDENTE\s{2,}(.+?)\s{2,}(.+?)\s*$", line):
            head = Winner(clean(match[1]), clean(match[2]).replace(" ,", ","), None)
        elif match := re.match(r"\s*VICEPRESIDENT[EA]\s{2,}(.+?)\s{2,}(.+?)\s*$", line):
            mate = Winner(clean(match[1]), clean(match[2]).replace(" ,", ","), None)
    if head is None:
        raise ValueError("no president in the list")
    return head, mate
