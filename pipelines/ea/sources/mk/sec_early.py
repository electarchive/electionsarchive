"""North Macedonia: the Assembly elections of 2002, 2006, 2008, and 2011 from the State Election Commission's reports.

After each election the Commission reported to the Assembly on the results in each electoral unit: the registered
voters, the voters who voted, the invalid ballots, each list's votes, the seats each list won, and, in 2008 and 2011,
the deputies elected. Its archive (izbornaarhiva.mk) keeps these reports:

- 2002 and 2006: the reports as scans. Their figures were read twice, by optical character recognition and from the
  page images, and are stored in ``sec_scans_early.json`` with the box of each figure on its page. The 2006 national
  summary of 25 July 2006 gives each list's national votes, which the units' figures add up to exactly.
- 2008 and 2011: the reports as Word documents, parsed here. The 2008 report counts the voting repeated on 15 June
  2008; the voting repeated on 29 June 2008 at five polling stations, which moved no seat, is not in it.

These elections are built by the Assembly pipeline in ``sec.py``, which calls the functions here.
"""

import json
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import cast

from ea.package import Package
from ea.sources.mk import sec

FILES = sec.FILES
DAYS = sec.DAYS


def stored() -> dict[str, dict[str, object]]:
    return json.loads((Path(__file__).parent / "sec_scans_early.json").read_text(encoding="utf-8"))


# The lists. Each list is keyed within its election; a party that stood on its own in several elections shares the
# key the Assembly pipeline gives it (dpa, dui). Lists of 2002 and 2008 are numbered on each unit's ballot, those of
# 2006 by the national summary, and those of 2011 on the national ballot.

LISTS_2002 = {
    "pp": ("party", "Прогресивна партија"),
    "srm": ("party", "Сојуз на Ромите од Македонија"),
    "nd": ("party", "Нова демократија"),
    "smrp": ("party", "Семакедонска работничка партија"),
    "rpm": ("party", "Републиканска партија на Македонија"),
    "dpa": ("party", "Демократска партија на Албанците"),
    "ma": ("list_vehicle", "Македонска алијанса и Македонска народна партија"),
    "ds": ("party", "Демократски сојуз"),
    "dui": ("party", "Демократска унија за интеграција"),
    "dom": ("list_vehicle", "Движење за обнова на Македонија и Партија на пензионерите на Република Македонија"),
    "pvm": (
        "list_vehicle",
        (
            "Партија на Власите од Македонија, Лига за демократија, Демократски сојуз на Србите во Македонија, "
            "Демократска муслиманска партија, Партија за демократско движење на Египќаните во Македонија и Странка "
            "на демократска акција на Македонија"
        ),
    ),
    "pcer": ("party", "Партија за целосна еманципација на Ромите од Македонија"),
    "spm": ("party", "Социјалистичка партија на Македонија"),
    "ndp": ("party", "Национална демократска партија"),
    "vmro_vmro": (
        "list_vehicle",
        (
            "ВМРО – Вистинска македонска реформска опција, Демократска партија за православното единство на Србите "
            "и Македонците и Македонска христијанска партија"
        ),
    ),
    "nv": ("party", "Народна волја"),
    "ppr": ("party", "Партија на правдата"),
    "vmro": (
        "list_vehicle",
        "ВМРО-ДПМНЕ (ВМРО – Демократска партија за македонско национално единство) и Либерална партија на Македонија",
    ),
    "ndm": ("party", "Народно движење за Македонија"),
    "maak": ("party", "МААК – Единствена македонска опција"),
    "vmro_mak": ("party", "ВМРО – Македонска"),
    "pdp": ("party", "Партија за демократски просперитет"),
    "zmz": (
        "list_vehicle",
        "Социјалдемократски сојуз на Македонија, Либерално-демократска партија и други (За Македонија заедно)",
    ),
    "pomni": ("party", "Преродба и обединување на македонските национални идеали"),
    "kpm": ("party", "Комунистичка партија на Македонија"),
    "da": ("party", "Демократска алтернатива"),
    "vmro_ob": ("party", "ВМРО – Обединета"),
    "dc": ("list_vehicle", "Демократски центар и Партија на зелените"),
    "sdpm": ("party", "Социјал-демократска партија на Македонија"),
    "forca": ("party", "Демократска партија „Напред Македонијо – ФОРЦА“"),
    "dpm": ("party", "Демократска партија на Македонија"),
    "dssm": ("party", "Демократски сојуз на Србите во Македонија"),
    "bdp": ("party", "Бошњачка демократска партија"),
    "peru": ("party", "Партија за единство на Ромите во Македонија"),
    "gi1_12": ("independent_group", "Група избирачи – Игор Џамбазов"),
    "gi5_10": ("independent_group", "Група избирачи – Горан Николовски"),
    "gi5_12": ("independent_group", "Група избирачи – Христо Ѓорѓиевски"),
    "gi5_17": ("independent_group", "Група избирачи – Дејан Панделески"),
    "gi6_19": ("independent_group", "Група избирачи – Младен Михајловски"),
}

LISTS_2006 = {
    "1": ("independent_group", "Група избирачи – ПМ"),
    "2": ("independent_group", "Група избирачи – ЌТ"),
    "3": ("party", "Партија за економска обнова"),
    "4": ("party", "Демократско-републиканска унија на Македонија – ДРУМ"),
    "5": ("party", "ТМРО"),
    "6": ("party", "Национална алтернатива"),
    "7": ("party", "Радикална странка на Србите во Македонија"),
    "8": ("list_vehicle", "Коалиција Леви сили на Македонија"),
    "9": ("party", "Центар на демократски сили"),
    "10": (
        "list_vehicle",
        "Коалиција Нови демократски сили (НДС) и Демократска алијанса на Албанците во Македонија (ДААМ)",
    ),
    "11": ("party", "Национална демократска партија"),
    "12": ("party", "Демократска партија „Напред Македонијо – ФОРЦА“"),
    "13": ("list_vehicle", "Коалиција „Обединети Македонци“"),
    "14": ("party", "Лига за демократија"),
    "15": ("party", "Демократска партија на Македонија (ДПМ) – Тетово"),
    "16": ("party", "ВМРО – Народна партија"),
    "17": ("party", "ТМОРО-ВЕП"),
    "18": ("party", "Партија за демократска иднина – ПДИ"),
    "19": (
        "list_vehicle",
        "Коалиција Демократска унија за интеграција (ДУИ) и Партија за демократски просперитет (ПДП)",
    ),
    "20": ("party", "Нова социјалдемократска партија"),
    "21": ("party", "Македонска партија"),
    "22": ("party", "Комунистичка партија на Македонија"),
    "23": ("party", "Движење за национално единство на Турците"),
    "24": ("party", "ДОМ – Демократска обнова на Македонија"),
    "25": ("party", "Земјоделска народна партија на Македонија"),
    "26": ("party", "Демократска партија на Албанците (ДПА)"),
    "27": ("party", "ВМРО – ДП (ВМРО – Демократска партија)"),
    "28": ("party", "Партија за европска иднина – ПЕИ"),
    "29": ("list_vehicle", "Коалиција ВМРО-ДПМНЕ, Либерална партија на Македонија и други"),
    "30": ("party", "Сојуз на титови леви сили"),
    "31": ("party", "Демократска алтернатива"),
    "32": ("list_vehicle", "Коалиција „За Македонија заедно“"),
    "33": ("party", "Социјалдемократска партија на Македонија (СДПМ)"),
}
# The lists of 2008, with words that pick each out of the labels printed in the units' tables (checked in order).
LISTS_2008 = {
    "vmro_dp": ("party", "ВМРО – ДП (ВМРО – Демократска партија)", "ВМРО – ДП (ВМРО"),
    "vmro": ("list_vehicle", "Коалиција ВМРО-ДПМНЕ (За подобра Македонија)", "ДПМНЕ"),
    "sonce": ("list_vehicle", "СОНЦЕ – Коалиција за Европа", "СОНЦЕ"),
    "dui": ("party", "Демократска унија за интеграција – ДУИ", "УНИЈА ЗА ИНТЕГРАЦИЈА"),
    "dpa": ("party", "Демократска партија на Албанците – ДПА", "ПАРТИЈА НА АЛБАНЦИТЕ"),
    "dua": ("party", "Демократска унија на Албанците – ДУА", "УНИЈА НА АЛБАНЦИТЕ"),
    "ndu": ("party", "Национална демократска унија – НДУ", "НАЦИОНАЛНА ДЕМОКРАТСКА УНИЈА"),
    "stls": ("party", "Сојуз на титови леви сили", "ТИТОВИ"),
    "pei": ("party", "Партија за европска иднина – ПЕИ", "ЕВРОПСКА ИДНИНА"),
    "sdpm": ("party", "Социјал-демократска партија на Македонија – СДПМ", "СДПМ"),
    "psd": ("party", "Партија на слободни демократи – ПСД", "СЛОБОДНИ ДЕМОКРАТИ"),
    "dnet": ("party", "Движење за национално единство на Турците", "ЕДИНСТВО НА ТУРЦИТЕ"),
    "rssm": ("party", "Радикална странка на Србите во Македонија – РССМ", "РАДИКАЛНА СТРАНКА"),
    "tmoro": ("party", "ТМОРО – ВЕП", "ТАТКОВИНСКА"),
    "pdp": ("party", "Партија за демократски просперитет – ПДП", "ПРОСПЕРИТЕТ"),
    "gi_pg": ("independent_group", "Група избирачи ПГ", "ИЗБИРАЧИ ПГ"),
    "gi_pm": ("independent_group", "Група избирачи ПМ", "ИЗБИРАЧИ ПМ"),
    "tmro": ("party", "Трајно македонско радикално обединување – ТМРО", "ТМРО"),
}
LISTS_2011 = {
    "1": ("party", "Демокрациа е ре – Партија за нова демократија"),
    "2": ("party", "Партија на обединети демократи на Македонија – ПОДЕМ"),
    "3": ("party", "Социјалдемократска унија – СДУ"),
    "4": ("party", "Демократска унија на Албанците – ДУА"),
    "5": ("list_vehicle", "ВМРО-ДПМНЕ и други (За подобра Македонија)"),
    "6": ("party", "ВМРО – Народна партија (ВМРО – НП)"),
    "7": ("party", "Демократска унија за интеграција – ДУИ"),
    "8": ("party", "Демократска партија на Албанците – ДПА"),
    "9": ("party", "Политичка партија Достоинство – ППД"),
    "10": ("list_vehicle", "СДСМ и други (Коалиција за иднината на Македонија)"),
    "11": ("party", "Национална демократска унија – НДУ"),
    "12": ("party", "Либерално-демократска партија – ЛДП"),
    "13": ("party", "Социјал-демократска партија на Македонија – СДПМ"),
    "14": ("party", "Демократска десница"),
    "15": ("party", "Европска партија на Македонија"),
    "16": ("party", "Партија за демократски просперитет – ПДП"),
    "17": ("party", "Национална демократска преродба – НДП"),
    "18": ("party", "Обединети за Македонија – ОМ"),
}
SHARED = {2002: {"dpa": "dpa", "dui": "dui"}, 2006: {"26": "dpa"}, 2008: {"dpa": "dpa", "dui": "dui"}}
SHARED[2011] = {"7": "dui", "8": "dpa"}


def table(year: int) -> dict[str, tuple[str, str]]:
    if year == 2008:
        return {id: (kind, label) for id, (kind, label, _) in LISTS_2008.items()}
    return {2002: LISTS_2002, 2006: LISTS_2006, 2011: LISTS_2011}[year]


def ballots(year: int) -> list[sec.Ballot]:
    shared = SHARED[year]
    return [
        sec.Ballot(id=id, party=shared.get(id, f"{year}:{id}"), kind=kind, label=label)
        for id, (kind, label) in table(year).items()
    ]


# The reports of 2008 and 2011.


class Blocks(HTMLParser):
    """The text of each paragraph and table cell of a document converted to HTML, one string each."""

    BLOCK = frozenset({"p", "td", "th", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "div", "tr"})

    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []
        self.buffer: list[str] = []

    def flush(self) -> None:
        text = " ".join("".join(self.buffer).replace("\xad", "").split())
        if text:
            self.found.append(text)
        self.buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK:
            self.flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK:
            self.flush()

    def handle_data(self, data: str) -> None:
        self.buffer.append(data)


def doc_lines(name: str) -> list[str]:
    """The paragraphs of a Word report. The plain-text export of these files drops some table rows, so the report is
    converted to HTML and read paragraph by paragraph."""
    source = sec.path(name)
    with tempfile.TemporaryDirectory() as work:
        copy = Path(work) / "report.doc"
        copy.write_bytes(source.read_bytes())
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "html", "--outdir", work, str(copy)],
            check=True,
            capture_output=True,
        )
        parser = Blocks()
        parser.feed((Path(work) / "report.html").read_text(encoding="utf-8"))
        parser.flush()
    joined: list[str] = []
    for text in parser.found:
        # A list's name broken after a dash continues on the next paragraph.
        if joined and re.search(r"\s[–-]$", joined[-1]):
            joined[-1] = f"{joined[-1]} {text}"
        else:
            joined.append(text)
    return joined


@dataclass
class Row:
    position: int
    label: str
    carrier: str
    votes: int


@dataclass
class Unit:
    number: int
    registered: int = 0
    voted: int = 0
    invalid: int = 0
    valid: int | None = None
    rows: list[Row] = field(default_factory=list)
    seats: dict[int, int] = field(default_factory=dict)  # by position
    elected: list[tuple[int, str]] = field(default_factory=list)  # (position, name)


def number(text: str) -> int:
    return int(text.replace(".", "").replace(" ", ""))


def fold(text: str) -> str:
    return re.sub(r"[^А-ЯЀ-ЏA-Z0-9]", "", text.upper().replace("СОЦИЈАДЕМ", "СОЦИЈАЛДЕМ"))


def place(unit: Unit, label: str) -> int:
    """The position of the list whose printed label begins like ``label``."""
    wanted = fold(label)
    hits = [
        row.position
        for row in unit.rows
        if fold(row.label).startswith(wanted[:40]) or wanted.startswith(fold(row.label)[:40])
    ]
    if len(hits) != 1:
        raise ValueError(f"unit {unit.number}: cannot place {label!r} ({hits})")
    return hits[0]


# A deputy in a report's list of those elected: an optional number, the name in capitals, and the list where it changes.
MEMBER = re.compile(r"(?:(\d+)\.\s*)?([А-ЯЀ-Џ][А-ЯЀ-ЏS\- –]*[А-ЯЀ-Џ])\s*(?:,|\.|,? и)?(?: од (.+?))?\s*$")
STATS = {
    "registered": re.compile(r"се запишани ([\d.]+)"),
    "voted": re.compile(r"гласале ([\d.]+)"),
    "invalid": re.compile(r"има ([\d.]+) \(.*?неважечк"),
}


def report(name: str) -> dict[int, Unit]:
    """Each unit's figures, lists, seats, and elected deputies, as the report states them."""
    lines = doc_lines(name)
    units: dict[int, Unit] = {}
    current: Unit | None = None
    mode = ""
    for i, line in enumerate(lines):
        head = re.search(
            r"гласањето за \w+ на кандидати (?:е извршено )?во Изборната единица (?:број )?(\d),? (?:е спроведено )?во",
            line,
        ) or re.search(r"гласањето за кандидатите е извршено во Изборната единица (\d)", line)
        if head:
            current = units.setdefault(int(head[1]), Unit(int(head[1])))
            mode = "stats"
        if current is None:
            continue
        if mode == "stats":
            for key, pattern in STATS.items():
                found = pattern.search(line)
                if found:
                    setattr(current, key, number(found[1]))
        table = re.match(r"Подносител на листа(?:та)? (?:во )?ИЕ (\d)$", line)
        if table:
            if int(table[1]) != current.number:
                raise ValueError(f"the table of unit {table[1]} follows the figures of unit {current.number}")
            mode = "rows"
            continue
        if mode == "rows":
            if line.startswith("Според резултатите од гласањето, а"):
                mode = "seats"
                continue
            position = re.fullmatch(r"(\d+)\.", line)
            if position:
                block: list[str] = []
                for following in lines[i + 1 :]:
                    votes = re.fullmatch(r"добила ([\d.]+) глас\w*", following)
                    if votes:
                        break
                    block.append(following)
                else:
                    raise ValueError(f"unit {current.number}: list {position[1]} has no votes")
                current.rows.append(Row(int(position[1]), " ".join(block[:-1]), block[-1], number(votes[1])))
            continue
        if mode == "seats":
            if "се избрани" in line or MEMBER.match(line):
                mode = "elected"
                if "се избрани" in line:
                    continue
            else:
                continue
        if mode == "elected":
            member = MEMBER.match(line)
            if member is None:
                mode = ""
                continue
            if member[3]:
                current.seats.setdefault(place(current, member[3]), 0)
                list_at = place(current, member[3])
            elif not current.elected:
                raise ValueError(f"unit {current.number}: the first deputy has no list")
            else:
                list_at = current.elected[-1][0]
            # A Latin S stands for the letter Ѕ in one name of 2011.
            current.elected.append((list_at, member[2].strip().replace("S", "Ѕ")))
    seats_text(lines, units)
    for unit in units.values():
        if not unit.rows:
            raise ValueError(f"unit {unit.number}: no lists")
    return units


def seats_text(lines: list[str], units: dict[int, Unit]) -> None:
    """The seats each list won, from the paragraphs listing every list of a unit (units 1 to 6)."""
    text = " ".join(lines)
    for unit in units.values():
        if unit.number > 6:
            continue
        start = re.search(rf"Изборн\w+ единица (?:број )?{unit.number},? листите на кандидати го добиле", text)
        if start is None:
            raise ValueError(f"unit {unit.number}: no paragraph on seats")
        end = text.index("се избрани", start.end())
        for piece in re.split(r"Листата на (?:кандидати )?", text[start.end() : end])[1:]:
            won = re.search(r",?\s+[дД]обила\s+(\d+)\s*\(", piece)
            if won and not re.search(r"\s[нН]е\s+[дД]обила", piece[: won.end()]):
                unit.seats[place(unit, piece[: won.start()])] = int(won[1])
        unit.seats = {k: v for k, v in unit.seats.items() if v}


def dhondt[K: (int, str)](votes: dict[K, int], seats: int) -> dict[K, int]:
    quotients = sorted(((v / d, k) for k, v in votes.items() for d in range(1, seats + 1)), reverse=True)[:seats]
    return dict(Counter(k for _, k in quotients))


def identify_2008(label: str) -> str:
    upper = label.upper().replace("–", "-")
    for id, (_, _, words) in LISTS_2008.items():
        if words.upper().replace("–", "-") in upper:
            return id
    raise ValueError(f"unknown 2008 list {label!r}")


# Building.


def build(p: Package, year: int, election: str, path_: str, lists: dict[str, sec.Ballot]) -> Package:
    # The Assembly, its electoral system, the Commission as a source, and the parties of the DPA and DUI are rows of
    # the registry package; these elections carry their other lists' parties themselves.
    p.depends = [f"{sec.SOURCE}/geo"]
    p.external |= {sec.ASSEMBLY, sec.SYSTEM, sec.SEC}
    for ballot in lists.values():
        id = sec.party(ballot.party)
        if ballot.party in SHARED[year].values():
            p.external.add(id)  # in the registry package, from the later elections
            continue
        p.add("party", id=id, kind=ballot.kind, polity_id=sec.MACEDONIA)
        p.name(id, ballot.label, "mk", "official")
    if year in (2002, 2006):
        return build_scanned(p, year, election, path_, lists)
    return build_report(p, year, election, path_, lists)


def unit_contest(
    p: Package,
    year: int,
    election: str,
    path_: str,
    lists: dict[str, sec.Ballot],
    code: str,
    magnitude: int,
    entries: list[tuple[str, int | None, str | None]],
) -> tuple[str, dict[str, str]]:
    """The contest of one unit with an option for each list on its ballot: (list, ballot position, printed label)."""
    day = DAYS[year]
    cid = sec.key("contest", day, code)
    p.add(
        "contest",
        id=cid,
        election_id=election,
        kind="list_allocation",
        body_id=sec.ASSEMBLY,
        district_area_id=sec.district_area(code),
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=sec.SYSTEM,
        default_result_kind="counted",
    )
    p.name(cid, sec.contest_name(code), "en", "official")
    p.add("slug", path=f"{path_}/unit-{code}", entity_id=cid, is_current=True)
    options: dict[str, str] = {}
    for list_id, position, label in entries:
        if list_id in options:
            raise ValueError(f"{year} unit {code}: list {list_id} twice")
        ballot = lists[list_id]
        options[list_id] = sec.key("option", day, code, list_id)
        p.add(
            "contest_option",
            id=options[list_id],
            contest_id=cid,
            kind="list",
            party_id=sec.party(ballot.party),
            ballot_position=position,
            ballot_label=label or ballot.label,
        )
    return cid, options


def build_scanned(p: Package, year: int, election: str, path_: str, lists: dict[str, sec.Ballot]) -> Package:
    data = cast(dict[str, list[dict[str, object]] | dict[str, dict[str, object]]], stored()[str(year)])
    rows = cast(list[dict[str, object]], data["rows"])
    figures = cast(dict[str, dict[str, object]], data["units"])
    name = "scan02" if year == 2002 else "scan06"
    numbers = {cast(int, row["page"]) for row in rows} | {
        cast(int, unit[f"{measure}_page"])
        for unit in figures.values()
        for measure in ("registered", "voted", "invalid")
    }
    pages = sec.pages(p, year, name, numbers)
    if year == 2006:
        p.artifact(sec.raw.cached(sec.SOURCE, FILES["sum06"]), sec.SEC)
    codes = [str(n) for n in range(1, 7)]
    districts = sec.district_units(p, year, codes)
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    national: Counter[str] = Counter()
    for code in codes:
        mine = [row for row in rows if str(row["unit"]) == code]
        if [row["row"] for row in mine] != list(range(1, len(mine) + 1)):
            raise ValueError(f"{year} unit {code}: lists out of order")
        cid, options = unit_contest(
            p, year, election, path_, lists, code, 20, [(str(row["list"]), cast(int, row["row"]), None) for row in mine]
        )
        rs = sec.result_set(p, year, election, cid, code, "report", default=True, completeness=1.0)
        unit = districts[code]
        figure = figures[code]
        for measure, field_ in ((sec.REGISTERED, "registered"), (sec.BALLOTS, "voted"), (sec.INVALID, "invalid")):
            values.append(sec.fact(rs, unit, None, measure, cast(int, figure[field_]), sec.DIGITIZED))
            locators.append(
                sec.locator(
                    rs,
                    unit,
                    None,
                    pages[cast(int, figure[f"{field_}_page"])],
                    cast(list[float], figure[f"{field_}_box"]),
                    measure,
                )
            )
        seats: dict[str, int] = {}
        for row in mine:
            option = options[str(row["list"])]
            values.append(sec.fact(rs, unit, option, sec.VOTES, cast(int, row["votes"]), sec.DIGITIZED))
            locators.append(
                sec.locator(rs, unit, option, pages[cast(int, row["page"])], cast(list[float], row["votes_box"]))
            )
            national[str(row["list"])] += cast(int, row["votes"])
            if cast(int, row["seats"]):
                seats[str(row["list"])] = cast(int, row["seats"])
                p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=row["seats"])
        if sum(seats.values()) != 20:
            raise ValueError(f"{year} unit {code}: {sum(seats.values())} seats")
        allocated = dhondt({str(row["list"]): cast(int, row["votes"]) for row in mine}, 20)
        if allocated != seats:
            raise ValueError(f"{year} unit {code}: the declared seats differ from the D'Hondt allocation")
        sec.note(
            p,
            rs,
            "methodology",
            f"Each list's votes and seats in the unit, the registered voters, the voters who voted, and the invalid "
            f"ballots, read from the State Election Commission's report to the Assembly on the {year} election (a "
            "scan). The report gives no count of valid ballots.",
        )
    if year == 2006 and national != SUMMARY_2006:
        raise ValueError("the units' list votes do not add up to the national summary of 2006")
    return sec.finish(p, values, locators)


# The national summary of 25 July 2006: each list's votes, by the numbers of LISTS_2006.
SUMMARY_2006 = Counter(
    {
        "1": 6008,
        "2": 156,
        "3": 12718,
        "4": 2745,
        "5": 1605,
        "6": 3325,
        "7": 1413,
        "8": 992,
        "9": 134,
        "10": 4117,
        "11": 4325,
        "12": 87,
        "13": 1324,
        "14": 2663,
        "15": 588,
        "16": 57077,
        "17": 744,
        "18": 1280,
        "19": 113522,
        "20": 56624,
        "21": 2311,
        "22": 598,
        "23": 620,
        "24": 17364,
        "25": 12628,
        "26": 70261,
        "27": 1206,
        "28": 11255,
        "29": 304572,
        "30": 3071,
        "31": 11067,
        "32": 218463,
        "33": 8226,
    }
)


def build_report(p: Package, year: int, election: str, path_: str, lists: dict[str, sec.Ballot]) -> Package:
    name = "doc08" if year == 2008 else "doc11"
    p.artifact(sec.raw.cached(sec.SOURCE, FILES[name]), sec.SEC)
    if year == 2008:
        p.artifact(sec.raw.cached(sec.SOURCE, FILES["fix08"]), sec.SEC)
    units = report(name)
    codes = [str(n) for n in sorted(units)]
    if codes != [str(n) for n in range(1, 7 if year == 2008 else 10)]:
        raise ValueError(f"{year}: units {codes}")
    districts = sec.district_units(p, year, codes)
    values: list[dict[str, object]] = []
    shortfalls: list[str] = []
    for code in codes:
        unit = units[int(code)]
        ids = {row.position: identify_2008(row.label) if year == 2008 else str(row.position) for row in unit.rows}
        magnitude = 20 if unit.number <= 6 else 1
        cid, options = unit_contest(
            p,
            year,
            election,
            path_,
            lists,
            code,
            magnitude,
            [(ids[row.position], row.position, row.label) for row in unit.rows],
        )
        rs = sec.result_set(p, year, election, cid, code, "report", default=True, completeness=1.0)
        here = districts[code]
        values += [
            sec.fact(rs, here, None, sec.REGISTERED, unit.registered),
            sec.fact(rs, here, None, sec.BALLOTS, unit.voted),
            sec.fact(rs, here, None, sec.INVALID, unit.invalid),
        ]
        for row in unit.rows:
            values.append(sec.fact(rs, here, options[ids[row.position]], sec.VOTES, row.votes))
        named = dict(Counter(position for position, _ in unit.elected))
        if unit.number > 6:
            unit.seats = named
        if (year, unit.number) == (2008, 5) and unit.seats.get(11) == 1 and ids[11] == "vmro_dp":
            # The paragraph on seats says the VMRO-DP list won one seat, which would make 21; the deputies the report
            # names, the 20 seats of the unit, and the D'Hondt allocation all leave that list without a seat.
            del unit.seats[11]
            sec.note(
                p,
                rs,
                "caveat",
                "The report's paragraph on the seats of this unit says that the VMRO-DP list won one seat, which would "
                "give the unit 21. The deputies the report names in the unit include none from that list, and the "
                "other lists' seats already make 20, so no seat is stored for it.",
                "seats",
            )
        if any(n > unit.seats.get(position, 0) for position, n in named.items()):
            raise ValueError(f"{year} unit {code}: deputies named {named} exceed the seats {unit.seats}")
        short = sum(unit.seats.values()) - len(unit.elected)
        if sum(unit.seats.values()) != magnitude:
            raise ValueError(f"{year} unit {code}: {sum(unit.seats.values())} seats")
        allocated = dhondt({row.position: row.votes for row in unit.rows}, magnitude)
        if allocated != unit.seats:
            raise ValueError(f"{year} unit {code}: declared seats {unit.seats} differ from D'Hondt {allocated}")
        for position, seats in unit.seats.items():
            p.add("outcome", result_set_id=rs, option_id=options[ids[position]], kind="won", seats=seats)
        members = [
            sec.Deputy(unit.number, ids[position], n, name_, "", 0)
            for n, (position, name_) in enumerate(unit.elected, 1)
        ]
        if short:
            missing = [lists[ids[k]].label for k, v in unit.seats.items() if named.get(k, 0) < v]
            sec.note(
                p,
                rs,
                "caveat",
                f"The report names {len(members)} of the unit's {magnitude} deputies; it names one fewer than the "
                f"seats it gives the list {', '.join(missing)}.",
                "deputies",
            )
            shortfalls.append(f"unit {code}")
        sec.deputies(p, year, rs, options, members, {ids[position]: seats for position, seats in unit.seats.items()})
        sec.note(
            p,
            rs,
            "methodology",
            f"Each list's votes and seats in the unit, the registered voters, the voters who voted, the invalid "
            f"ballots, and the deputies elected, from the State Election Commission's report to the Assembly on the "
            f"{year} election. The report names the deputies by list in the order they were elected but gives no "
            "places on the lists."
            + (" The unit covers the voters abroad and elects one deputy." if unit.number > 6 else ""),
        )
        if year == 2008 and unit.number in (1, 6):
            sec.note(
                p,
                rs,
                "caveat",
                "The report counts the voting of 1 June 2008 and the voting repeated at 187 polling stations on 15 "
                "June. After a ruling of the Supreme Court the voting was repeated again at polling stations 2432, "
                "2445, 2446, and 2458 in Saraj and 2039 in Brvenica; the Commission found that this moved no seat "
                "and did not publish new totals for the unit.",
                "repeat",
            )
        if year == 2008 and unit.number == 6:
            sec.note(
                p,
                rs,
                "methodology",
                "The Commission's letter of 25 June 2008 corrects the report's table for this unit: the 47,308 votes "
                "printed beside the European Future party belong to the DPA, and that party won 1,852. The figures "
                "stored are the corrected ones, as the report's later version prints them.",
                "correction",
            )
    if shortfalls:
        p.accept(
            "outcomes.list_members",
            f"The {year} report names one deputy fewer than the seats it declares for a list in {', '.join(shortfalls)}; "
            "the seats are stored as declared and the deputies as named.",
        )
    return sec.finish(p, values, [])
