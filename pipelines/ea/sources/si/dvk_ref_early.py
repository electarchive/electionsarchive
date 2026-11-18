"""Slovenian referendums of 1996-2011 (https://www.dvk-rs.si/volitve-in-referendumi/referendumi/arhiv-referendumov/).

Each vote day is one package, `si.dvk/ref-<date>`, built on `si.dvk/geo` and `si.dvk/registry` from
`ea.sources.si.dvk`, with one contest for each question put to the voters that day. Each package keeps the grain
its source has:

- 1996 and 1999: the commission's pages for the vote, by electoral unit (volilna enota).
- 2001: the commission's table by electoral district (volilni okraj), with its report of the result.
- 2004: the commission's table by polling station, with its report of the result.
- 2010 and 2011: the archived result sites (https://www.dvk-rs.si/arhivi/), by electoral district, with the
  special polling stations (OMNIA and those open to disabled voters) as their own units.
- 2003, 2005, and 2007: the commission's reports of the result, national figures only.

A district or electoral unit is a reporting unit that equals its area. Where the commission published nothing
finer than the nation, the nation is one aggregate unit that holds the declared figures.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.sources.si.dvk import (
    DVK_SOURCE,
    ELECTORAL_UNITS,
    SLOVENIA,
    SOURCE,
    SYSTEMS,
    area,
    clean,
    key,
    station_kind,
)

SITE = "https://www.dvk-rs.si"
ARCHIVE = f"{SITE}/arhivi"
PAGES = f"{SITE}/volitve-in-referendumi/referendumi/volitve-referenduma"
FILES = f"{SITE}/fileadmin/user_upload/dokumenti/volitve"
DISTRICTS = 11
FIELDS = ("registered", "voters", "cast", "invalid", "valid")
MEASURES = {"registered": 2, "voters": 14, "cast": 3, "invalid": 5, "valid": 4}


@dataclass
class Unit:
    """One reporting unit's figures for one question."""

    code: str
    name: str
    scheme: str
    """The unit's own scheme: `si.vo`, `si.ve`, or `si.volisce`; `si.ve` also holds the national aggregate."""
    area_scheme: str | None = None
    """The scheme of the area the unit covers, or None for a unit with no area."""
    area_code: str | None = None
    relation: str = "unlocated"
    """`equals` when the unit is its area, `within` when it lies inside it, `unlocated` otherwise."""
    kind: str = "standard"
    counts: dict[str, int] = field(default_factory=dict)
    """The accounting figures the source prints, and `for` and `against`."""


def district_unit(unit: int, seat: int, name: str, counts: dict[str, int]) -> Unit:
    code = f"{unit}.{seat}"
    return Unit(code, name, "si.vo", "si.vo", code, "equals", counts=counts)


@dataclass
class Question:
    key: str
    english: str
    native: str
    national: dict[str, int]
    units: list[Unit] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    advisory: bool = False
    outcome: bool = True
    """Whether to store which answer carried the vote."""


@dataclass
class Day:
    day: date
    english: str
    native: str
    grain: str
    """`electoral unit`, `electoral district`, `polling station`, or `nation`."""
    questions: list[Question] = field(default_factory=list)
    page: str = ""
    note: str = ""


ROMAN = re.compile(r"^[IVX]+$")


def _name(text: str) -> str:
    """A district's name in title case, with Roman numerals kept."""
    return re.sub(r"[^\W\d_]+", lambda m: m[0] if ROMAN.fullmatch(m[0]) else m[0].capitalize(), clean(text))


# Reading helpers -----------------------------------------------------------------------------------------------------


def _int(text: str) -> int:
    return int(text.replace(".", "").replace(",", "").replace("\xa0", ""))


def _fix(text: str) -> str:
    """The June 2011 site stores UTF-8 text that its pages were decoded as Windows-1250 once already."""
    if re.search(r"[ĹÄ][^\s]", text):
        try:
            return text.encode("cp1250").decode("utf-8")
        except UnicodeEncodeError, UnicodeDecodeError:
            return text
    return text


def _decode(path: Path) -> str:
    body = path.read_bytes()
    for charset in ("utf-8", "cp1250"):
        try:
            return _fix(body.decode(charset))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: unreadable text")


def _plain(url: str) -> str:
    """The text of a page without markup."""
    html = _decode(raw.cached(SOURCE, url).path)
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return clean(re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " "))


def _pdf_text(url: str) -> str:
    with pymupdf.open(raw.cached(SOURCE, url).path) as doc:
        return clean(" ".join(page.get_text() for page in doc))


def _find(pattern: str, text: str) -> int:
    found = re.search(pattern, text)
    if found is None:
        raise LookupError(f"no match for {pattern}")
    return _int(found.group(1))


def _report(text: str) -> dict[str, int]:
    """The national figures a commission report or its web page prints in words."""
    return {
        "registered": _find(r"imelo pravico (?:glasovati|voliti) skupaj\s+([\d.]+)", text),
        "voters": _find(r"Glasovalo je skupaj\s+([\d.]+)", text),
        "cast": _find(r"Oddanih je bilo\s+([\d.]+)", text),
        "invalid": _find(r"bilo\s+([\d.]+)\s*(?:\([^)]*\)\s*)?glasovnic\s+neveljavnih", text),
        "valid": _find(r"Veljavnih je bilo\s+([\d.]+)", text),
        "for": _find(r"[\"“”„]\s*ZA\s*[\"“”„]\s*odgovorilo\s+([\d.]+)", text),
        "against": _find(r"[\"“”„]\s*PROTI\s*[\"“”„]\s*([\d.]+)\s*(?:\([^)]*\)\s*)?volivcev", text),
    }


def _lines(url: str) -> list[list[str]]:
    """The words of a PDF's pages as rows: words that share a baseline form one row, left to right."""
    rows: list[list[str]] = []
    with pymupdf.open(raw.cached(SOURCE, url).path) as doc:
        for page in doc:
            words = page.get_text("words")
            by_line: dict[int, list[tuple[float, str]]] = defaultdict(list)
            for x0, y0, _x1, y1, word, *_ in words:
                by_line[round((y0 + y1) / 2 / 3)].append((x0, word))
            for line in sorted(by_line):
                rows.append([word for _, word in sorted(by_line[line])])
    return rows


# Archive sites of 2010 and 2011 --------------------------------------------------------------------------------------

ROW = re.compile(
    r"(?:(?<=PROTI)|(?<=%))\s+(?P<name>[^\d%]+?)\s+(?P<cast>\d[\d.]*)\s+(?P<invalid>\d[\d.]*)\s+\d+,\d+\s*%\s+"
    r"(?P<yes>\d[\d.]*)\s+\d+,\d+\s*%\s+(?P<no>\d[\d.]*)\s+\d+,\d+\s*%"
)
TURNOUT = re.compile(
    r"(?:(?<=%)|(?<=udeležbe))\s+(?P<name>[^\d%]+?)\s+(?P<voters>\d[\d.]*)\s+(?P<registered>\d[\d.]*)\s+\d+,\d+\s*%"
)
SPECIAL = re.compile(r"(Volišča (?:OMNIA|dostopna invalidom))\s+(\d[\d.]*)\s+(\d[\d.]*)(?=\s|$)")
SPECIAL_ROW = re.compile(
    r"Volišča (?P<label>OMNIA|dostopna invalidom)\s+(?P<cast>\d[\d.]*)\s+(?P<invalid>\d[\d.]*)\s+\d+,\d+\s*%\s+"
    r"(?P<yes>\d[\d.]*)\s+\d+,\d+\s*%\s+(?P<no>\d[\d.]*)\s+\d+,\d+\s*%"
)


def _okraj_units(prefix: str, turnout: str, results: str, national: str) -> tuple[list[Unit], dict[str, int]]:
    """The 88 districts and the special polling stations of one question on an archived result site."""
    base = f"{ARCHIVE}/{prefix}"
    voted = _plain(f"{base}{turnout}")
    start = voted.index("Volilna enota 1 - ")
    rows = [(clean(m["name"]), _int(m["voters"]), _int(m["registered"])) for m in TURNOUT.finditer(voted[start:])]
    counted = _plain(f"{base}{results}")
    outcomes = list(ROW.finditer(counted[counted.index("Volilna enota 1 - ") :]))[:88]
    if len(rows) != 88 or len(outcomes) != 88:
        raise ValueError(f"{prefix}: {len(rows)} turnout rows and {len(outcomes)} result rows, expected 88")
    units: list[Unit] = []
    index = 0
    for unit in ELECTORAL_UNITS:
        for seat in range(1, DISTRICTS + 1):
            name, voters, registered = rows[index]
            found = outcomes[index]
            if clean(found["name"]).upper() != name.upper():
                raise ValueError(
                    f"{prefix}: district {unit}.{seat} is {name!r} in turnout, {found['name']!r} in results"
                )
            cast, invalid = _int(found["cast"]), _int(found["invalid"])
            yes, no = _int(found["yes"]), _int(found["no"])
            counts = {
                "registered": registered,
                "voters": voters,
                "cast": cast,
                "invalid": invalid,
                "valid": yes + no,
                "for": yes,
                "against": no,
            }
            units.append(district_unit(unit, seat, _name(name), counts))
            index += 1
    # Special polling stations: the turnout page holds their voters, the result page their ballots and answers.
    special = {m.group(1): (_int(m.group(2)), _int(m.group(3))) for m in SPECIAL.finditer(voted)}
    for found in SPECIAL_ROW.finditer(counted):
        label = found["label"]
        full = f"Volišča {label}"
        if full not in special:
            raise ValueError(f"{prefix}: no turnout row for {full}")
        voters, registered = special[full]
        yes, no = _int(found["yes"]), _int(found["no"])
        counts = {
            "voters": voters,
            "cast": _int(found["cast"]),
            "invalid": _int(found["invalid"]),
            "valid": yes + no,
            "for": yes,
            "against": no,
        }
        if registered:
            counts["registered"] = registered
        units.append(Unit(f"special:{label}", full, "si.ve", kind="aggregate", counts=counts))
    page = _plain(f"{base}{national}")
    total = {
        "registered": _find(r"pravico glasovati skupaj\s+([\d.]+)", page),
        "voters": _find(r"Do 19:00 je glasovalo skupaj\s+([\d.]+)", page),
        "cast": _find(r"Oddanih glasov\s+(\d+)", page),
        "invalid": _find(r"Neveljavni\s+(\d+)", page),
        "for": _find(r"Za\s+(\d+)", page),
        "against": _find(r"Proti\s+(\d+)", page),
    }
    total["valid"] = total["for"] + total["against"]
    return units, total


def _archive_question(qkey: str, english: str, native: str, prefix: str, pages: tuple[str, str, str]) -> Question:
    turnout, results, national = pages
    units, total = _okraj_units(prefix, turnout, results, national)
    urls = [f"{ARCHIVE}/{prefix}{page}" for page in pages]
    return Question(qkey, english, native, total, units, urls)


# 1996 and 1999: the commission's pages for the vote, by electoral unit ----------------------------------------------

UNIT_WORDS = {
    1: "Kranj",
    2: "Postojna",
    3: "Ljubljana Center",
    4: "Ljubljana Bežigrad",
    5: "Celje",
    6: "Novo mesto",
    7: "Maribor",
    8: "Ptuj",
}


def _electoral_unit(number: int, counts: dict[str, int]) -> Unit:
    return Unit(str(number), UNIT_WORDS[number], "si.ve", "si.ve", str(number), "equals", counts=counts)


def _page_1996(slug: str) -> list[Question]:
    text = _plain(f"{PAGES}/{slug}/")
    url = f"{PAGES}/{slug}/"
    nation = {
        "registered": _find(r"pravico glasovati\s+(\d+)", text),
        "voters": _find(r"Glasovalo je\s+(\d+)", text),
        "cast": _find(r"Preštetih je bilo\s+(\d+)", text),
        "invalid": _find(r"Neveljavnih glasovnic:\s+(\d+)", text),
    }
    answers: dict[str, dict[str, int]] = {}
    for letter in "abc":
        found = re.search(
            rf"Referendumsko vprašanje {letter}:\s+(\d+)\s+glasov\s+za\s+\([^)]*\)\s+(\d+)\s+glasov\s+proti", text
        )
        if found is None:
            raise LookupError(f"no national figures for question {letter}")
        answers[letter] = {"for": int(found.group(1)), "against": int(found.group(2))}
    head = text.index("Udeležba za volilne enote")
    turnout = {
        int(m.group(1)): (int(m.group(3)), int(m.group(4)))
        for m in re.finditer(
            r"(\d) - ([A-Za-zČŠŽčšž ]+?)\s+(\d+)\s+(\d+)\s+\d+\.\d", text[head : text.index("IZIDI GLASOVANJA")]
        )
    }
    tail = text[text.index("Izidi glasovanja za volilne enote") :]
    tally: dict[int, list[int]] = {}
    for m in re.finditer(r"(?:^|\s)(\d)\s+(\d{5})((?:\s+\d+\s+\d+\.\d+%){8})", tail):
        values = [int(v) for v in re.findall(r"(\d+)\s+\d+\.\d+%", m.group(3))]
        tally[int(m.group(1))] = [int(m.group(2)), *values]
    if sorted(turnout) != list(ELECTORAL_UNITS) or sorted(tally) != list(ELECTORAL_UNITS):
        raise ValueError(f"1996: {sorted(turnout)} turnout units, {sorted(tally)} result units")
    questions = []
    for index, letter in enumerate("abc"):
        units = []
        for number in ELECTORAL_UNITS:
            cast, *values = tally[number]
            counts = {
                "registered": turnout[number][0],
                "voters": turnout[number][1],
                "cast": cast,
                "invalid": values[7],
                "for": values[2 * index],
                "against": values[2 * index + 1],
            }
            units.append(_electoral_unit(number, counts))
        questions.append(
            Question(
                letter,
                f"Question {letter.upper()} of the 1996 referendum on the electoral system",
                f"Referendumsko vprašanje {letter}",
                {**nation, **answers[letter]},
                units,
                [url],
                outcome=False,
            )
        )
    return questions


def _page_1999(slug: str) -> list[Question]:
    url = f"{PAGES}/{slug}/"
    text = _plain(url)
    nation = {
        "registered": _find(r"imelo pravico voliti\s+([\d.]+)", text),
        "voters": _find(r"Glasovalo je\s+([\d.]+)", text),
        "cast": _find(r"Število preštetih glasovnic:\s+([\d.]+)", text),
        "invalid": _find(r"Število neveljavnih glasovnic:\s+([\d.]+)", text),
        "valid": _find(r"Število veljavnih glasovnic:\s+([\d.]+)", text),
        "for": _find(r"ZA\s+([\d.]+)\s+\d+,\d+ %\s+PROTI", text),
        "against": _find(r"PROTI\s+([\d.]+)\s+\d+,\d+ %\s+Izidi", text),
    }
    head = text.index("Podatki za volilne enote")
    # The page prints the voters before the registered voters.
    turnout = {
        int(m.group(1)): (_int(m.group(4)), _int(m.group(3)))
        for m in re.finditer(
            r"(\d)\. ([A-ZČŠŽ\- ]+?) ([\d.]+) ([\d.]+) \d+,\d+", text[head : text.index("IZIDI GLASOVANJA")]
        )
    }
    tail = text[text.index("Izidi glasovanja za volilne enote") :]
    units = []
    for m in re.finditer(r"(\d)\. ([A-ZČŠŽ\- ]+?) \d+,\d+ % ([\d.]+) \d+,\d+ % ([\d.]+) \d+,\d+ % ([\d.]+)", tail):
        number = int(m.group(1))
        yes, no, invalid = _int(m.group(3)), _int(m.group(4)), _int(m.group(5))
        counts = {
            "registered": turnout[number][0],
            "voters": turnout[number][1],
            "cast": yes + no + invalid,
            "invalid": invalid,
            "valid": yes + no,
            "for": yes,
            "against": no,
        }
        units.append(_electoral_unit(number, counts))
    if [u.code for u in units] != [str(n) for n in ELECTORAL_UNITS]:
        raise ValueError("1999: electoral units not found")
    return [
        Question(
            "tet3",
            "1999 Slovenian referendum on financing the construction of the Trbovlje thermal power plant (TET 3)",
            "Zakonodajni referendum o financiranju izgradnje TET 3",
            nation,
            units,
            [url],
        )
    ]


# 2001: the commission's table by electoral district ---------------------------------------------------------------

OLD_FONT = str.maketrans({"[": "Š", "]": "Ć", "^": "Č", "@": "Ž", "{": "š", "}": "ć", "~": "č", "`": "ž"})
DIGITS = re.compile(r"\d+")


def _units_2001(url: str) -> list[Unit]:
    """The districts of the 2001 table. Its first four figures and last five sit on separate pages, in one order."""
    first: list[tuple[int, int, str, list[int]]] = []
    second: list[list[int]] = []
    for row in _lines(url):
        if (
            len(row) >= 8
            and all(DIGITS.fullmatch(cell) for cell in row[:3])
            and all(DIGITS.fullmatch(c) for c in row[-4:])
        ):
            numbers = [_int(cell) for cell in row[-4:]]
            name = " ".join(row[3:-4]).translate(OLD_FONT)
            first.append((int(row[0]), int(row[1]), name, numbers))
        elif len(row) == 5 and all(DIGITS.fullmatch(cell) for cell in row):
            second.append([_int(cell) for cell in row])
    if len(first) != 88 or len(second) != 88:
        raise ValueError(f"2001: {len(first)} turnout rows and {len(second)} result rows, expected 88")
    units = []
    for (unit, seat, name, (roll, by_roll, certificates, voters)), (cast, valid, yes, no, invalid) in zip(
        first, second, strict=True
    ):
        if by_roll + certificates != voters or yes + no != valid or cast - invalid != valid:
            raise ValueError(f"2001: district {unit}.{seat} does not add up")
        counts = {
            "registered": roll,
            "voters": voters,
            "cast": cast,
            "invalid": invalid,
            "valid": valid,
            "for": yes,
            "against": no,
        }
        units.append(district_unit(unit, seat, _name(name), counts))
    return units


# 2004: the commission's table by polling station -----------------------------------------------------------------


def _units_2004(url: str, day: date) -> tuple[list[Unit], dict[str, int]]:
    units: list[Unit] = []
    total: dict[str, int] = {}
    seen: set[str] = set()
    for row in _lines(url):
        if (
            len(row) >= 12
            and all(DIGITS.fullmatch(cell) for cell in row[:3])
            and all(DIGITS.fullmatch(c) for c in row[-9:])
        ):
            ve, vo, stv = int(row[0]), int(row[1]), int(row[2])
            place = clean(" ".join(row[3:-9]))
            roll, certified, by_roll, with_certificate, abroad, cast, invalid, yes, no = (int(c) for c in row[-9:])
            code = f"{ve}:{vo}:{stv}"
            if code in seen:
                raise ValueError(f"2004: polling station {code} repeats")
            seen.add(code)
            unit_no = ve // 1000 if ve >= 1000 else ve
            seat = vo % 1000 if vo >= 1000 else vo
            area_scheme: str | None = None
            area_code: str | None = None
            if unit_no in ELECTORAL_UNITS:
                if seat in range(1, DISTRICTS + 1):
                    area_scheme, area_code = "si.vo", f"{unit_no}.{seat}"
                else:
                    area_scheme, area_code = "si.ve", str(unit_no)
            counts = {
                "voters": by_roll + with_certificate + abroad,
                "cast": cast,
                "invalid": invalid,
                "valid": yes + no,
                "for": yes,
                "against": no,
            }
            if roll + certified > 0 or not cast:
                counts["registered"] = roll + certified
            if cast - invalid != yes + no:
                raise ValueError(f"2004: station {code} does not add up")
            relation = "unlocated" if area_scheme is None else "within"
            units.append(Unit(code, place, "si.volisce", area_scheme, area_code, relation, station_kind(place), counts))
        elif len(row) == 9 and all(DIGITS.fullmatch(cell) for cell in row):
            roll, certified, by_roll, with_certificate, abroad, cast, invalid, yes, no = (int(c) for c in row)
            total = {
                "registered": roll + certified,
                "voters": by_roll + with_certificate + abroad,
                "cast": cast,
                "invalid": invalid,
                "valid": yes + no,
                "for": yes,
                "against": no,
            }
    if not total:
        raise ValueError(f"{day}: no printed total")
    return units, total


# The vote days -------------------------------------------------------------------------------------------------------

REPORT_WORDS = "the commission's report of the result"


def _national(url: str, english: str, native: str, qkey: str, *, text: str | None = None) -> Question:
    figures = _report(text if text is not None else _pdf_text(url))
    unit = Unit("national", "Slovenija", "si.ve", kind="aggregate", counts=dict(figures))
    return Question(qkey, english, native, figures, [unit], [url])


def _fetch_list() -> list[tuple[str, str]]:
    """Every file the packages cite, with its suffix."""
    urls: list[tuple[str, str]] = [
        (f"{PAGES}/referendum-za-volitve-v-drzavni-zbor-rs/", ".html"),
        (f"{PAGES}/zakonodajni-referendum-o-financiranju-izgradnje-tet-3/", ".html"),
        (f"{PAGES}/zavarovalnice/", ".html"),
    ]
    for name in (
        "referendum_2001/Vol1250.pdf",
        "referendum_2001/Izp_okr.pdf",
        "referendum_2001/ref-obmp.pdf",
        "referendum_2003_2/porocilo-o-referendumu---TELEFONIJA.pdf",
        "referendum_2003_2/porocilo-o-referendumu---ZELEZNICE.pdf",
        "referendum_2003_1/porocilo-o-referendumu-EU.pdf",
        "referendum_2003_1/porocilo-o-referendumu-NATO.pdf",
        "referendum_2003/porocilo-o-referendumu---trgovina.pdf",
        "referendum_2004/KONCNI-REZULTATI-TEHNICNI.pdf",
        "referendum_2004/porocilo-o-izidu-referenduma-tehnicni-zakon.pdf",
        "referendum_2005/porocilo-o-izidu-referenduma-RTVS.pdf",
    ):
        urls.append((f"{FILES}/{name}", ".pdf"))
    for prefix, pages in ARCHIVES:
        urls.extend((f"{ARCHIVE}/{prefix}{page}", ".html") for page in pages)
    return urls


ARCHIVES: list[tuple[str, tuple[str, str, str]]] = [
    ("as2010/", ("index2.html", "index5.html", "index3.html")),
    ("rtv2010/", ("index2.html", "index5.html", "index3.html")),
    ("md2011/", ("index2.html", "index5.html", "index3.html")),
    ("junij2011/", ("index7.html", "index10.html", "index8.html")),
    ("junij2011/", ("index11.html", "index14.html", "index12.html")),
    ("junij2011/", ("index15.html", "index18.html", "index16.html")),
]


def days() -> list[Day]:
    report = f"{FILES}/referendum_2003_1/porocilo-o-referendumu"
    found = [
        Day(
            date(1996, 12, 8),
            "1996 Slovenian referendum on the electoral system",
            "Referendum o volilnem sistemu 1996",
            "electoral unit",
            _page_1996("referendum-za-volitve-v-drzavni-zbor-rs"),
        ),
        Day(
            date(1999, 1, 10),
            "1999 Slovenian referendum on financing the Trbovlje thermal power plant (TET 3)",
            "Zakonodajni referendum o financiranju izgradnje TET 3 1999",
            "electoral unit",
            _page_1999("zakonodajni-referendum-o-financiranju-izgradnje-tet-3"),
        ),
    ]
    two = _units_2001(f"{FILES}/referendum_2001/Vol1250.pdf")
    declared = f"{FILES}/referendum_2001/ref-obmp.pdf"
    figures = _report(_pdf_text(declared))
    found.append(
        Day(
            date(2001, 6, 17),
            "2001 Slovenian referendum on the Act on Infertility Treatment and Biomedically Assisted Fertilization",
            "Naknadni zakonodajni referendum o zdravljenju neplodnosti in postopkih oploditve z biomedicinsko pomočjo 2001",
            "electoral district",
            [
                Question(
                    "law",
                    "2001 Slovenian referendum on amendments to the Act on Infertility Treatment and Biomedically "
                    "Assisted Fertilization",
                    "Naknadni zakonodajni referendum o zakonu o spremembah in dopolnitvah zakona o zdravljenju "
                    "neplodnosti in postopkih oploditve z biomedicinsko pomočjo",
                    figures,
                    two,
                    [f"{FILES}/referendum_2001/Vol1250.pdf", declared, f"{FILES}/referendum_2001/Izp_okr.pdf"],
                )
            ],
        )
    )
    jan = f"{FILES}/referendum_2003_2/porocilo-o-referendumu---"
    found.append(
        Day(
            date(2003, 1, 19),
            "2003 Slovenian prior referendums on telephone investments and the railways",
            "Predhodna zakonodajna referenduma o vračanju vlaganj v telekomunikacijsko omrežje in o Slovenskih "
            "železnicah 2003",
            "nation",
            [
                _national(
                    f"{jan}TELEFONIJA.pdf",
                    "2003 Slovenian prior referendum on the return of investments in the public telecommunications "
                    "network",
                    "Predhodni zakonodajni referendum o zakonu o spremembah in dopolnitvah zakona o vračanju vlaganj "
                    "v javno telekomunikacijsko omrežje",
                    "telephone",
                ),
                _national(
                    f"{jan}ZELEZNICE.pdf",
                    "2003 Slovenian prior referendum on the transformation and privatization of Slovenske železnice",
                    "Predhodni zakonodajni referendum o zakonu o preoblikovanju in privatizaciji javnega podjetja "
                    "Slovenske železnice",
                    "railways",
                ),
            ],
        )
    )
    found.append(
        Day(
            date(2003, 3, 23),
            "2003 Slovenian referendums on accession to the European Union and NATO",
            "Referenduma o pristopu k Evropski uniji in k zvezi NATO 2003",
            "nation",
            [
                _national(
                    f"{report}-EU.pdf",
                    "2003 Slovenian referendum on accession to the European Union",
                    "Referendum o pristopu Republike Slovenije k Evropski uniji",
                    "eu",
                ),
                _national(
                    f"{report}-NATO.pdf",
                    "2003 Slovenian referendum on accession to NATO",
                    "Referendum o pristopu Republike Slovenije k Organizaciji Severnoatlantske pogodbe (NATO)",
                    "nato",
                ),
            ],
        )
    )
    found.append(
        Day(
            date(2003, 9, 21),
            "2003 Slovenian prior referendum on the Trade Act",
            "Predhodni zakonodajni referendum o spremembi zakona o trgovini 2003",
            "nation",
            [
                _national(
                    f"{FILES}/referendum_2003/porocilo-o-referendumu---trgovina.pdf",
                    "2003 Slovenian prior referendum on the amendment of the Trade Act",
                    "Predhodni zakonodajni referendum v zvezi s predlogom zakona o spremembi zakona o trgovini",
                    "law",
                )
            ],
        )
    )
    stations = f"{FILES}/referendum_2004/KONCNI-REZULTATI-TEHNICNI.pdf"
    units, total = _units_2004(stations, date(2004, 4, 4))
    word = f"{FILES}/referendum_2004/porocilo-o-izidu-referenduma-tehnicni-zakon.pdf"
    found.append(
        Day(
            date(2004, 4, 4),
            "2004 Slovenian referendum on the act implementing the Constitutional Court's decision on the erased",
            "Naknadni zakonodajni referendum o zakonu o izvršitvi 8. točke odločbe Ustavnega sodišča 2004",
            "polling station",
            [
                Question(
                    "law",
                    "2004 Slovenian referendum on the Act implementing point 8 of Constitutional Court decision "
                    "U-I-246/02-28",
                    "Naknadni zakonodajni referendum o zakonu o izvršitvi 8. točke odločbe Ustavnega sodišča "
                    "št. U-I-246/02-28",
                    _report(_pdf_text(word)),
                    units,
                    [stations, word],
                )
            ],
        )
    )
    del total
    found.append(
        Day(
            date(2005, 9, 25),
            "2005 Slovenian referendum on the Radiotelevizija Slovenija Act",
            "Naknadni zakonodajni referendum o zakonu o Radioteleviziji Slovenija 2005",
            "nation",
            [
                _national(
                    f"{FILES}/referendum_2005/porocilo-o-izidu-referenduma-RTVS.pdf",
                    "2005 Slovenian referendum on the Radiotelevizija Slovenija Act (ZRTVS-1)",
                    "Naknadni zakonodajni referendum o zakonu o Radioteleviziji Slovenija (ZRTVS-1)",
                    "law",
                )
            ],
        )
    )
    page = f"{PAGES}/zavarovalnice/"
    found.append(
        Day(
            date(2007, 11, 11),
            "2007 Slovenian referendum on the Insurance Companies Privatization Act",
            "Zakonodajni referendum o zakonu o lastninskem preoblikovanju zavarovalnic 2007",
            "nation",
            [
                _national(
                    page,
                    "2007 Slovenian referendum on amendments to the Act on the Ownership Transformation of "
                    "Insurance Companies (ZLPZ-1A)",
                    "Zakonodajni referendum o Zakonu o spremembah in dopolnitvah Zakona o lastninskem "
                    "preoblikovanju zavarovalnic (ZLPZ-1A)",
                    "law",
                    text=_plain(page),
                )
            ],
        )
    )
    names = {
        "as2010/": (
            "law",
            "2010 Slovenian referendum on the Arbitration Agreement Act",
            "Referendum o zakonu o arbitražnem sporazumu",
        ),
        "rtv2010/": (
            "law",
            "2010 Slovenian referendum on the Radiotelevizija Slovenija Act",
            "Referendum o zakonu o RTV Slovenija",
        ),
        "md2011/": ("law", "2011 Slovenian referendum on the Mini Jobs Act", "Referendum o zakonu o malem delu"),
    }
    for (prefix, pages), qkey, english, native in [
        (ARCHIVES[0], *names["as2010/"]),
        (ARCHIVES[1], *names["rtv2010/"]),
        (ARCHIVES[2], *names["md2011/"]),
    ]:
        year = 2010 if "2010" in prefix else 2011
        question = _archive_question(qkey, english, native, prefix, pages)
        found.append(
            Day(
                date(*_DATES[prefix]),
                english,
                f"{native} {year}",
                "electoral district",
                [question],
                f"{ARCHIVE}/{prefix}",
            )
        )
    june = [
        (
            ARCHIVES[3],
            "undeclared-work",
            "2011 Slovenian referendum on the Act on Preventing Undeclared Work and Employment",
            "Referendum o zakonu o preprečevanju dela in zaposlovanja na črno",
        ),
        (
            ARCHIVES[4],
            "pensions",
            "2011 Slovenian referendum on the Pension and Disability Insurance Act",
            "Referendum o zakonu o pokojninskem in invalidskem zavarovanju",
        ),
        (
            ARCHIVES[5],
            "archives",
            "2011 Slovenian referendum on amendments to the Act on the Protection of Documentary and Archival Material and Archives",
            "Referendum o zakonu o spremembah in dopolnitvah zakona o varstvu dokumentarnega in arhivskega gradiva ter arhivih",
        ),
    ]
    found.append(
        Day(
            date(2011, 6, 5),
            "2011 Slovenian referendums on undeclared work, pensions, and archives",
            "Referendumi o delu na črno, pokojninah in arhivih 2011",
            "electoral district",
            [_archive_question(qkey, english, native, *page) for page, qkey, english, native in june],
            f"{ARCHIVE}/junij2011/",
        )
    )
    return sorted(found, key=lambda d: d.day)


_DATES = {"as2010/": (2010, 6, 6), "rtv2010/": (2010, 12, 19), "md2011/": (2011, 4, 10)}


# Packages ------------------------------------------------------------------------------------------------------------


def slug(day: date) -> str:
    return f"si.dvk/ref-{day}"


def packages() -> list[str]:
    return [slug(day) for day in DAYS]


def fetch() -> None:
    for url, suffix in _fetch_list():
        raw.fetch(SOURCE, url, suffix=suffix)


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    for found in days():
        print(f"{slug(found.day)}: {reconcile(found)}", flush=True)
        election(found).write()


DAYS = [
    date(1996, 12, 8),
    date(1999, 1, 10),
    date(2001, 6, 17),
    date(2003, 1, 19),
    date(2003, 3, 23),
    date(2003, 9, 21),
    date(2004, 4, 4),
    date(2005, 9, 25),
    date(2007, 11, 11),
    date(2010, 6, 6),
    date(2010, 12, 19),
    date(2011, 4, 10),
    date(2011, 6, 5),
]


def _sums(question: Question) -> dict[str, int]:
    return {name: sum(unit.counts.get(name, 0) for unit in question.units) for name in (*FIELDS, "for", "against")}


def reconcile(found: Day) -> dict[str, object]:
    """Compare the sum of each question's unit rows with the national figures the commission prints."""
    report: dict[str, object] = {}
    for question in found.questions:
        sums = _sums(question)
        report[question.key] = {
            name: sums[name] - question.national[name]
            for name in sums
            if name in question.national and sums[name] != question.national[name]
        } or "equal"
    return report


def _short(found: Day, question: Question) -> dict[str, int]:
    """How far the unit rows fall short of the national figures, by measure."""
    sums = _sums(question)
    return {
        name: question.national[name] - sums[name]
        for name in sums
        if name in question.national and sums[name] != question.national[name]
    }


def _source_words(found: Day) -> str:
    if found.grain == "electoral unit":
        return "on its page for the vote"
    if found.grain == "electoral district" and found.page:
        return "on its archived result site for the vote"
    if found.grain == "electoral district":
        return "in its table of the result by electoral district"
    if found.grain == "polling station":
        return "in its table of the result by polling station"
    return "in its report of the result"


def _method(found: Day, question: Question) -> str:
    kinds = {
        "electoral unit": (
            "by electoral unit (volilna enota), the finest level it published. Each of the 8 electoral units is a "
            "reporting unit that equals its electoral unit area"
        ),
        "electoral district": (
            "by electoral district (volilni okraj), the finest level it published. Each of the 88 districts is a "
            "reporting unit, numbered within its electoral unit in the order the source lists them and tied to the "
            "district area of that number"
        ),
        "polling station": (
            "by polling station (volišče), the finest level it published. Each station is a reporting unit that "
            "lies within its electoral district; the postal votes, the votes cast with a certificate, and the "
            "special electoral roll are units of their own, as the source prints them"
        ),
        "nation": (
            "for the whole country only; the commission published no result by electoral unit or district for it, "
            "so there is one aggregate reporting unit for Slovenia, which holds the declared figures"
        ),
    }
    text = (
        f"Published by the State Election Commission (Državna volilna komisija) {_source_words(found)}, "
        f"{kinds[found.grain]}. Registered voters are the voters on the roll, voters are those who voted, ballots "
        "cast are the ballots counted, and valid votes are the votes for and against."
    )
    if found.grain == "electoral district" and found.page:
        text += (
            " The two special polling station groups, the stations open to disabled voters and the OMNIA stations "
            "where any voter may vote, are aggregate units without an area."
        )
    short = _short(found, question) if found.page else {}
    if short:
        figures = ", ".join(f"{name.replace('_', ' ')} by {value:,}" for name, value in short.items())
        text += (
            f" The unit rows fall short of the printed national figures in {figures}; the source prints no unit "
            "for the remainder, so it is in the declared national figures only."
        )
    if found.day == date(2001, 6, 17):
        text += (
            " The district table prints 1,592,597 registered voters and 567,898 voters; the commission's report "
            "declares 1,592,650 and 567,878, and those are the declared figures."
        )
    if found.day.year == 1996:
        text += (
            " Voters cast one ballot with three questions, A, B, and C, each answered for or against, and the "
            "commission prints no count of valid ballots, so none is stored."
        )
    if found.day == date(2010, 12, 19):
        text += " The result site prints these as the provisional official figures."
    return text


def election(found: Day) -> Package:
    p = Package(
        slug(found.day),
        SOURCE,
        f"The {found.english}, by {found.grain}"
        if found.grain != "nation"
        else f"The {found.english}, national figures",
        depends=["si.dvk/geo", "si.dvk/registry"],
    )
    p.external.add(SLOVENIA)
    cited: set[str] = set()
    for question in found.questions:
        for url in question.urls:
            if url not in cited:
                cited.add(url)
                p.artifact(raw.cached(SOURCE, url), DVK_SOURCE)
    election_id = key("election", "ref", found.day)
    p.add(
        "election",
        id=election_id,
        polity_id=SLOVENIA,
        kind="referendum",
        date_from=found.day,
        date_to=found.day,
        status="held",
    )
    p.name(election_id, found.english, "en", "official")
    p.name(election_id, found.native, "sl", "native", False)
    p.add("slug", path=f"si/{found.day}-referendum", entity_id=election_id, is_current=True)
    units = _units(p, found)
    for question in found.questions:
        contest = key("contest", found.day, question.key)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question",
            round_number=1,
            electoral_system_id=SYSTEMS["ref"],
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(contest, question.english, "en", "official")
        p.name(contest, question.native, "sl", "native", False)
        p.add("slug", path=f"si/{found.day}-referendum/{question.key}", entity_id=contest, is_current=True)
        result_set = key("result_set", found.day, question.key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=DVK_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=_method(found, question),
            lang="en",
        )
        options = {}
        for position, (name, label) in enumerate((("for", "For"), ("against", "Against")), start=1):
            option = options[name] = key("option", found.day, question.key, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        for name in FIELDS:
            if name in question.national:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=None,
                    measure=MEASURES[name],
                    value=question.national[name],
                )
        for name, option in options.items():
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=question.national[name])
        _values(p, result_set, question, units, options)
        if question.outcome:
            rejected = question.national["against"] > question.national["for"]
            p.add("outcome", result_set_id=result_set, option_id=options["for"], kind="lost" if rejected else "won")
            p.add("outcome", result_set_id=result_set, option_id=options["against"], kind="won" if rejected else "lost")
    _accept(p, found)
    return p


def _accept(p: Package, found: Day) -> None:
    if found.grain == "nation":
        return
    if not any(_short(found, question) for question in found.questions):
        return
    reason = (
        "the source's unit rows do not add up to its printed national figures, and it prints no unit for the "
        "remainder; both are stored as printed"
    )
    if found.day == date(2001, 6, 17):
        reason = (
            "the district table prints 53 fewer registered voters and 20 more voters than the commission's report "
            "declares; each is stored as printed"
        )
    for code in ("accounting.declared",):
        p.accept(code, reason)


def _units(p: Package, found: Day) -> dict[str, str]:
    """One reporting unit for each distinct unit code of the day, shared by its questions."""
    ids: dict[str, str] = {}
    for question in found.questions:
        for unit in question.units:
            if unit.code in ids:
                continue
            code = f"{found.day}:{unit.code}"
            ids[unit.code] = key("unit", code)
            place = area(unit.area_scheme, unit.area_code) if unit.area_scheme and unit.area_code else None
            p.add(
                "reporting_unit",
                id=ids[unit.code],
                polity_id=SLOVENIA,
                scheme_code=unit.scheme,
                code=code,
                area_relation=unit.relation,
                area_id=place,
                kind=unit.kind,
            )
            p.name(ids[unit.code], unit.name, "sl", "official")
    return ids


def _values(p: Package, result_set: str, question: Question, ids: dict[str, str], options: dict[str, str]) -> None:
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []
    for unit in question.units:
        for name in FIELDS:
            if name in unit.counts:
                sets.append(result_set)
                unit_ids.append(ids[unit.code])
                option_ids.append(None)
                measures.append(MEASURES[name])
                values.append(unit.counts[name])
        for name, option in options.items():
            sets.append(result_set)
            unit_ids.append(ids[unit.code])
            option_ids.append(option)
            measures.append(1)
            values.append(unit.counts[name])
    size = len(values)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": sets,
                "unit_id": unit_ids,
                "option_id": option_ids,
                "channel": [0] * size,
                "measure": measures,
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": values,
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )
