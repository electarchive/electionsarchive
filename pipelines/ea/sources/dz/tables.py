"""Reader for the result tables that the Constitutional Council (and, since 2022, the Constitutional Court) prints in the
Journal officiel to proclaim an election of the Council of the Nation.

Each proclamation has one table with a row for each wilaya (electors registered, voting, abstaining, the participation
rate, valid and null ballots) and, at the right, the elected member and the votes the member obtained. The Journal sets
the table in a full-width block, so the reader works from word positions: the wilaya is the text left of the first
numeric column, and the members are the words right of the last one."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from ea.sources.bt.pdfwords import Word, lines, pages

WILAYAS = (
    "Adrar",
    "Chlef",
    "Laghouat",
    "Oum El Bouaghi",
    "Batna",
    "Béjaïa",
    "Biskra",
    "Béchar",
    "Blida",
    "Bouira",
    "Tamanrasset",
    "Tébessa",
    "Tlemcen",
    "Tiaret",
    "Tizi Ouzou",
    "Alger",
    "Djelfa",
    "Jijel",
    "Sétif",
    "Saïda",
    "Skikda",
    "Sidi Bel Abbès",
    "Annaba",
    "Guelma",
    "Constantine",
    "Médéa",
    "Mostaganem",
    "M'Sila",
    "Mascara",
    "Ouargla",
    "Oran",
    "El Bayadh",
    "Illizi",
    "Bordj Bou Arréridj",
    "Boumerdès",
    "El Tarf",
    "Tindouf",
    "Tissemsilt",
    "El Oued",
    "Khenchela",
    "Souk Ahras",
    "Tipaza",
    "Mila",
    "Aïn Defla",
    "Naâma",
    "Aïn Témouchent",
    "Ghardaïa",
    "Relizane",
    "Timimoun",
    "Bordj Badji Mokhtar",
    "Ouled Djellal",
    "Béni Abbès",
    "In Salah",
    "In Guezzam",
    "Touggourt",
    "Djanet",
    "El M'Ghair",
    "El Meniaa",
)
ARABIC = (
    "أدرار",
    "الشلف",
    "الأغواط",
    "أم البواقي",
    "باتنة",
    "بجاية",
    "بسكرة",
    "بشار",
    "البليدة",
    "البويرة",
    "تمنراست",
    "تبسة",
    "تلمسان",
    "تيارت",
    "تيزي وزو",
    "الجزائر",
    "الجلفة",
    "جيجل",
    "سطيف",
    "سعيدة",
    "سكيكدة",
    "سيدي بلعباس",
    "عنابة",
    "قالمة",
    "قسنطينة",
    "المدية",
    "مستغانم",
    "المسيلة",
    "معسكر",
    "ورقلة",
    "وهران",
    "البيض",
    "إليزي",
    "برج بوعريريج",
    "بومرداس",
    "الطارف",
    "تندوف",
    "تيسمسيلت",
    "الوادي",
    "خنشلة",
    "سوق أهراس",
    "تيبازة",
    "ميلة",
    "عين الدفلى",
    "النعامة",
    "عين تموشنت",
    "غرداية",
    "غليزان",
    "تيميمون",
    "برج باجي مختار",
    "أولاد جلال",
    "بني عباس",
    "عين صالح",
    "عين قزام",
    "تقرت",
    "جانت",
    "المغير",
    "المنيعة",
)
ALIASES = {
    "tamenghasset": 11,
    "tamanghasset": 11,
    "tamenrasset": 11,
    "bejaia": 6,
    "elmeghaier": 57,
    "elmghair": 57,
    "elmeniaa": 58,
    "elmenia": 58,
}
NEW = range(49, 59)
MONTHS = re.compile(
    r"janvier|février|fevrier|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|"
    r"moharram|safar|rabie|joumada|rajab|chaâbane|chaabane|ramadhan|chaoual|dhou|hidja|kaada",
    re.IGNORECASE,
)
NUMBER = re.compile(r"^\d+$")
PERCENT = re.compile(r"^\d+(?:[.,]\d+)?\s*%?$")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


KEYS = {norm(name): index + 1 for index, name in enumerate(WILAYAS)}
KEYS.update(ALIASES)


def resolve(text: str, after: int) -> int:
    """The code of the wilaya a row names, searching only wilayas after the previous row's."""
    key = norm(text)
    if key in KEYS and KEYS[key] > after:
        return KEYS[key]
    best, found = 0.0, 0
    for name, code in KEYS.items():
        if code <= after:
            continue
        score = SequenceMatcher(None, key, name).ratio()
        if score > best:
            best, found = score, code
    if best < 0.8:
        raise ValueError(f"no wilaya after {after} matches {text!r} (best {best:.2f})")
    return found


@dataclass
class Member:
    name: str
    votes: int


@dataclass
class Row:
    code: int
    registered: int | None = None
    voting: int | None = None
    abstaining: int | None = None
    valid: int | None = None
    null: int | None = None
    annulled: bool = False
    members: list[Member] = field(default_factory=list)


def number(text: str) -> int:
    return int(re.sub(r"[ .,]", "", text))


ANNULLED = re.compile(r"annul", re.IGNORECASE)
COUNT = re.compile(r"^[\d.,]+%?$")
GAP = 6.0


def figures(line: Sequence[Word]) -> list[Word]:
    """The numeric words of a line, with the pieces of one figure joined: a space can separate thousands ("1 254") and
    the participation rate's percent sign ("97,70 %")."""
    out: list[Word] = []
    for word in line:
        if not (COUNT.match(word.text) or word.text == "%"):
            continue
        if out and word.x - out[-1].x1 < GAP:
            out[-1] = Word(out[-1].x, out[-1].y, out[-1].text + word.text, out[-1].page, word.x1)
        else:
            out.append(word)
    return out


def counts(line: Sequence[Word]) -> list[int] | None:
    """The six figures of a wilaya row or of the total row, with the participation rate left as 0, or None if the
    line does not start with six figures whose fourth is a percentage."""
    found = figures(line)[:6]
    if len(found) < 6 or "%" not in found[3].text:
        return None
    return [0 if i == 3 else number(w.text) for i, w in enumerate(found)]


def parse(
    path: Path, *, nulls_first: bool, new_seats: int = 1, first_page: int = 1
) -> tuple[list[Row], dict[str, int]]:
    """Read the wilaya table from every page of the issue that carries its header, and the printed total row."""
    rows: list[Row] = []
    names: list[str] = []
    members: list[Member] = []
    totals: dict[str, int] = {}
    done = False
    for number_, words in pages(path):
        if done or number_ < first_page:
            continue
        grid = lines(words)
        heads = [i for i, line in enumerate(grid) if any(w.text == "Inscrits" for w in line)]
        if not heads:
            continue
        body = grid[heads[0] + 1 :]
        found = [(line, counts(line)) for line in body if line[0].text.lower() != "total"]
        numeric = [figures(line) for line, values in found if values]
        if not numeric:
            continue
        left_limit = min(line[0].x for line in numeric) - 4
        right_limit = max(line[5].x1 for line in numeric) + 2
        previous = -1
        for line in body:
            text = " ".join(w.text for w in line)
            if re.match(r"^total\b", text, re.IGNORECASE):
                values = counts(line)
                if values:
                    first, second = values[4], values[5]
                    totals = {
                        "registered": values[0],
                        "voting": values[1],
                        "abstaining": values[2],
                        "valid": second if nulls_first else first,
                        "null": first if nulls_first else second,
                    }
                done = True
                break
            if MONTHS.search(text) and len(line) < 8:
                continue
            values = counts(line)
            left = [w for w in line if w.x < left_limit and not ANNULLED.match(w.text)]
            annulled = bool(left) and any(ANNULLED.match(w.text) for w in line)
            right = [w for w in line if w.x > right_limit]
            if values or annulled:
                row = Row(0, annulled=annulled)
                if values:
                    row.registered, row.voting, row.abstaining = values[0], values[1], values[2]
                    first, second = values[4], values[5]
                    row.null, row.valid = (first, second) if nulls_first else (second, first)
                rows.append(row)
                names.append(" ".join(w.text for w in left))
                previous = len(rows) - 1
            elif left and previous >= 0 and not [w for w in figures(line) if w.x <= right_limit]:
                names[previous] += " " + " ".join(w.text for w in left)
            if right:
                if NUMBER.match(right[-1].text):
                    members.append(Member(" ".join(w.text for w in right[:-1]), int(right[-1].text)))
                elif members:
                    members[-1].name += " " + " ".join(w.text for w in right)
    after = 0
    for row, name in zip(rows, names, strict=True):
        row.code = after = resolve(name, after)
    cursor = 0
    for row in rows:
        if row.annulled:
            continue
        seats = new_seats if row.code in NEW else 1
        row.members = members[cursor : cursor + seats]
        cursor += seats
    if cursor != len(members):
        raise ValueError(f"{len(members)} members read but the rows take {cursor}")
    return rows, totals
