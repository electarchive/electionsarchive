"""Indonesia's 2014 presidential election by regency and city, from provincial recapitulations.

The contest, its two tickets, and the province set come from `id.kpu_pilpres`. This package adds a finer,
non-default result set to that contest, valued per regency and city (`id.kabkota`) for the provinces whose provincial
recapitulation (Model DC PPWP) a published document reproduces: West Sumatra and the Riau Islands in the Constitutional
Court's 2014 ruling, and Yogyakarta in KPU Yogyakarta's book of 2014 results. Every table is read from the PDFs' text
layer and checked against its own totals and against the province rows of KPU's national recapitulation, which the
same ruling reproduces.
"""

import csv
import io
import re
import struct
import subprocess
from collections.abc import Iterable
from pathlib import Path

from ea import package, raw
from ea.package import Package
from ea.sources.id import kpu_pilpres
from ea.store import DATA, connect

SOURCE = "id.kpu_pilpres_2014"
KEY = f"{SOURCE}/kabkota"
RULING = kpu_pilpres.RULING_2014[0]
BOOK = "https://drive.google.com/uc?export=download&id=16BwThFUdoLXwtgjWnW8rhKn8ZptjrdVW"
# Rows id.kpu_pilpres and id.kpu_dpr_2014 hold: the 2014 contest, its election and tickets, and the sources of the
# ruling and the Yogyakarta book. This package cites its own cached copies of both files under those sources.
CONTEST = "5bafe8e1-69ab-53fb-9ea8-0fa7ee8ec3f4"
ELECTION = "90b61357-4f6a-51b5-93ca-130b35cbf32c"
TICKETS = {1: "fe9aca96-1ac2-589e-935f-088f1540ebd9", 2: "c14c6bb9-08e1-5a5d-8b82-e3808e64e2a2"}
RULING_SOURCE = "9756d72d-cb69-5c14-82a4-dd3adfef1df9"
BOOK_SOURCE = "1999e756-d924-5a93-9353-8d3b357f184f"
SUMBAR_VOTES = (4985, 4986)
SUMBAR_TURNOUT = (4989, 4990)
KEPRI = 4674
DIY = 187
DIY_SECTIONS = (188, 191)
DIY_COLUMNS = ("Kota Yogyakarta", "Kab. Bantul", "Kab. Kulon Progo", "Kab. Sleman", "Kab. Gunung Kidul")
PROVINCES = {"13": "SUMATERA BARAT", "21": "KEPULAUAN RIAU", "34": "DAERAH ISTIMEWA YOGYAKARTA"}
ALIASES = {"KABUPATENANAMBAS": "KABUPATENKEPULAUANANAMBAS"}
MEASURES = {"votes": 1, "registered": 2, "ballots": 3, "valid": 4, "invalid": 5, "voters": 14}
DOTTED = re.compile(r"\d{1,3}(?:\.\d{3})+|\d{1,3}")
COMMA = re.compile(r"\d{1,3}(?:,\d{3})+")
# Cells the source misprints, as (printed, corrected), each proven by its row and column totals. Agam's ticket 2 is
# printed as 39.392, but Agam's valid votes (214.306, which the turnout table repeats) less ticket 1 give 38.392, and
# only 38.392 makes ticket 2's column sum to the province's 539.308.
CORRECTIONS = {("Kab. Agam", "2"): (39392, 38392)}
DERIVED = 1  # result_value.flags: a figure the archive computed from the source's, not one the source prints
METHOD = (
    "Ticket votes by regency and city for three provinces, from the provincial recapitulations of 18 July 2014 "
    "(Model DC PPWP) as two published documents reproduce them. West Sumatra (19 regencies and cities) and the Riau "
    "Islands (7) come from the Constitutional Court's ruling 1/PHPU.PRES-XII/2014, where the provincial election "
    "supervisors set out KPU's provincial tables (pages 4674, 4985 and 4986 of the ruling's PDF); West Sumatra's "
    "registered voters, voters, valid and invalid votes, and ballots come from the supervisors' table of the "
    "regencies' Model DB1 PPWP (pages 4989 and 4990). Registered voters are the permanent voter list (DPT), on which "
    "the table's turnout percentages are based; voters also include those on the transfer, special, and additional "
    "lists. Yogyakarta (5) comes from table 4.6 of KPU Yogyakarta's book Data Hasil Pemilu 2014 (page 187), with "
    "valid and invalid votes, and each regency's ticket votes match the Model DB1 PPWP figures the book restates on "
    "pages 188 to 191. The Riau Islands table gives no valid or invalid votes. In each province the regencies sum to "
    "the province row of KPU's national recapitulation (pages 4580 and 4581 of the ruling), which the default set "
    "holds; the default set also holds the outcome. One cell is corrected: the ruling prints Agam's votes for "
    "Joko Widodo and Jusuf Kalla as 39,392, but Agam's valid votes less Prabowo Subianto and Hatta Rajasa's give "
    "38,392, and only 38,392 makes the column sum to the province's 539,308."
)


def fetch() -> None:
    raw.fetch(SOURCE, RULING, suffix=".pdf")
    raw.fetch(SOURCE, BOOK, suffix=".pdf")


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    regencies().write()


def emit() -> None:
    package.run("load", packages())


Word = dict[str, str]


def _words(path: Path, pages: Iterable[int]) -> list[Word]:
    words: list[Word] = []
    for number in pages:
        out = subprocess.run(
            ["pdftotext", "-f", str(number), "-l", str(number), "-tsv", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
            if word["level"] == "5":
                words.append(word | {"page_num": str(number)})
    return words


def _lines(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda word: (int(word["page_num"]), float(word["top"]))):
        first = lines[-1][0] if lines else None
        if first and first["page_num"] == word["page_num"] and abs(float(word["top"]) - float(first["top"])) < 4:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda word: float(word["left"])) for line in lines]


def _number(word: Word) -> int:
    return int(re.sub(r"[.,]", "", word["text"]))


def _key(label: str) -> str:
    text = label.upper().replace("KAB.", "KABUPATEN ").replace("KEP.", "KEPULAUAN ").replace("50", "LIMA PULUH")
    text = re.sub(r"[^A-Z]", "", text)
    return ALIASES.get(text, text)


Cell = tuple[int, Word]


def _sumbar(path: Path) -> dict[str, dict[str, Cell]]:
    words = _words(path, SUMBAR_VOTES)
    rows: dict[str, dict[str, Cell]] = {}
    totals = None
    for line in _lines(words):
        numbers = [word for word in line if DOTTED.fullmatch(word["text"])]
        if len(numbers) != 3 or line[-1] is not numbers[-1]:
            continue
        label = " ".join(
            word["text"] for word in line if word not in numbers and not re.fullmatch(r"\d+\.", word["text"])
        )
        cells = dict(zip(("1", "2", "valid"), ((_number(word), word) for word in numbers), strict=True))
        if label == "Jumlah Akhir":
            totals = cells
        elif re.fullmatch(r"\d+\.", line[0]["text"]):
            rows[label] = cells
    if len(rows) != 19 or totals is None:
        raise ValueError(f"West Sumatra votes: {len(rows)} rows, total row {'found' if totals else 'missing'}")
    by_valid = {cells["valid"][0]: label for label, cells in rows.items()}
    if len(by_valid) != len(rows):
        raise ValueError("West Sumatra: two regencies share a valid-vote count, so the turnout table cannot be joined")
    words = _words(path, SUMBAR_TURNOUT)
    joined: set[str] = set()
    sums = dict.fromkeys(("registered", "voters", "valid", "invalid", "ballots"), 0)
    printed = None
    for line in _lines(words):
        numbers = [word for word in line if re.fullmatch(r"[\d.,]+", word["text"])]
        if len(numbers) < 9 or "," not in numbers[-4]["text"]:
            continue
        registered, _, _, _, voters, share, valid, invalid, ballots = numbers[-9:]
        cells = {
            "registered": (_number(registered), registered),
            "voters": (_number(voters), voters),
            "valid": (_number(valid), valid),
            "invalid": (_number(invalid), invalid),
            "ballots": (_number(ballots), ballots),
        }
        if line[0]["text"] == "JUMLAH":
            printed = {measure: value for measure, (value, _) in cells.items()}
            continue
        if cells["valid"][0] + cells["invalid"][0] != cells["ballots"][0] or cells["ballots"][0] != cells["voters"][0]:
            raise ValueError(f"West Sumatra turnout row {line[0]['text']}: valid, invalid, and voters disagree")
        if f"{100 * cells['voters'][0] / cells['registered'][0]:.2f}".replace(".", ",") != share["text"]:
            raise ValueError(f"West Sumatra turnout row {line[0]['text']}: {share['text']} percent is not voters/DPT")
        label = by_valid.get(cells["valid"][0])
        if label is None or label in joined:
            raise ValueError(f"West Sumatra turnout row {line[0]['text']}: no single regency with its valid votes")
        joined.add(label)
        rows[label] |= {measure: cell for measure, cell in cells.items() if measure != "valid"}
        for measure in sums:
            sums[measure] += cells[measure][0]
    if len(joined) != 19 or printed != sums:
        raise ValueError(f"West Sumatra turnout: {len(joined)} rows joined, sums {sums}, printed {printed}")
    for (label, column), (printed_value, value) in CORRECTIONS.items():
        if rows[label][column][0] != printed_value:
            raise ValueError(f"West Sumatra {label} {column}: the ruling no longer prints {printed_value}")
        rows[label][column] = (value, rows[label][column][1])
    for label, cells in rows.items():
        if cells["1"][0] + cells["2"][0] != cells["valid"][0]:
            raise ValueError(f"West Sumatra {label}: ticket votes do not sum to the valid votes")
    for measure in ("1", "2", "valid"):
        if sum(cells[measure][0] for cells in rows.values()) != totals[measure][0]:
            raise ValueError(f"West Sumatra {measure}: regencies do not sum to the Jumlah Akhir row")
    return rows


def _kepri(path: Path) -> dict[str, dict[str, Cell]]:
    words = _words(path, (KEPRI,))
    rows: dict[str, dict[str, Cell]] = {}
    totals = None
    for line in _lines(words):
        numbers = [word for word in line if DOTTED.fullmatch(word["text"]) and "." in word["text"]]
        if len(numbers) != 2:
            continue
        label = " ".join(word["text"] for word in line if word not in numbers and not word["text"].isdigit())
        cells = dict(zip(("1", "2"), ((_number(word), word) for word in numbers), strict=True))
        if not label:
            totals = cells
        elif re.fullmatch(r"(Kabupaten|Kota) .+", label):
            rows[label] = cells
    if len(rows) != 7 or totals is None:
        raise ValueError(f"Riau Islands: {len(rows)} rows, total row {'found' if totals else 'missing'}")
    for measure in ("1", "2"):
        if sum(cells[measure][0] for cells in rows.values()) != totals[measure][0]:
            raise ValueError(f"Riau Islands ticket {measure}: regencies do not sum to the province row")
    return rows


def _diy(path: Path) -> dict[str, dict[str, Cell]]:
    words = _words(path, (DIY,))
    lines = [[word for word in line if COMMA.fullmatch(word["text"])] for line in _lines(words)]
    lines = [line for line in lines if len(line) == len(DIY_COLUMNS) + 1]
    if len(lines) != 4:
        raise ValueError(f"Yogyakarta table 4.6: {len(lines)} rows of figures, expected 4")
    rows: dict[str, dict[str, Cell]] = {label: {} for label in DIY_COLUMNS}
    for measure, line in zip(("1", "2", "valid", "invalid"), lines, strict=True):
        *cells, total = line
        if sum(_number(word) for word in cells) != _number(total):
            raise ValueError(f"Yogyakarta table 4.6 {measure}: regencies do not sum to the Jumlah column")
        for label, word in zip(DIY_COLUMNS, cells, strict=True):
            rows[label][measure] = (_number(word), word)
    sections = subprocess.run(
        ["pdftotext", "-f", str(DIY_SECTIONS[0]), "-l", str(DIY_SECTIONS[1]), "-layout", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    stated = re.findall(r"Data DB1 PPWP (.+?)\s{2}.*?Paslon 1 = ([\d.]+).*?Paslon 2 = ([\d.]+)", sections, re.DOTALL)
    restated = {_key(label): (int(a.replace(".", "")), int(b.replace(".", ""))) for label, a, b in stated}
    for label, cells in rows.items():
        if cells["1"][0] + cells["2"][0] != cells["valid"][0]:
            raise ValueError(f"Yogyakarta {label}: ticket votes do not sum to the valid votes")
        if restated.get(_key(label)) != (cells["1"][0], cells["2"][0]):
            raise ValueError(f"Yogyakarta {label}: table 4.6 differs from the book's DB1 PPWP figures")
    return rows


def _units() -> dict[str, tuple[str, str]]:
    """Each regency's name key to its reporting unit id and code, for the three provinces."""
    with connect() as conn:
        rows = conn.execute(
            """select u.code, u.id::text, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.is_preferred and n.lang = 'id'
               where u.scheme_code = 'id.kabkota' and split_part(u.code, '.', 1) = any(%s)
                 and (u.valid is null or u.valid @> '2014-07-09'::date)""",
            [list(PROVINCES)],
        ).fetchall()
        found = {
            number: str(option)
            for number, option in conn.execute(
                "select ballot_position, id from contest_option where contest_id = %s", [CONTEST]
            ).fetchall()
        }
    if found != TICKETS:
        raise ValueError(f"the 2014 contest's options are {found}, expected {TICKETS}")
    units: dict[str, tuple[str, str]] = {}
    for code, unit, text in rows:
        if _key(text) in units:
            raise ValueError(f"two regency units are named {text}")
        units[_key(text)] = (unit, code)
    return units


def _page(p: Package, artifact: raw.Artifact, artifact_id: str, number: int) -> tuple[str, int, int]:
    image = DATA / "pages" / SOURCE / f"{artifact.sha256}-{number}.png"
    if not image.exists():
        image.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
            + [str(artifact.path), str(image.with_suffix(""))],
            check=True,
        )
    width, height = struct.unpack(">II", image.read_bytes()[16:24])
    page = p.id("page", artifact.sha256, number)
    p.add(
        "source_page",
        id=page,
        artifact_id=artifact_id,
        page_number=number,
        image_key=f"pages/{SOURCE}/{image.name}",
        width=width,
        height=height,
    )
    return page, width, height


def regencies() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "Indonesia's 2014 presidential election by regency and city in West Sumatra, the Riau Islands, and Yogyakarta, "
        "from the provincial recapitulations the Constitutional Court's ruling and KPU Yogyakarta's results book reproduce",
    )
    ruling = raw.cached(SOURCE, RULING)
    book = raw.cached(SOURCE, BOOK)
    if raw.cached(kpu_pilpres.SOURCE, RULING).sha256 != ruling.sha256:
        raise ValueError("the ruling's copy differs from the one id.kpu_pilpres read its province rows from")
    artifacts = {"ruling": p.artifact(ruling, RULING_SOURCE), "book": p.artifact(book, BOOK_SOURCE)}
    p.external.update({CONTEST, ELECTION, *TICKETS.values(), RULING_SOURCE, BOOK_SOURCE})
    national = {
        (row["unit"], row["measure"], row["ticket"]): int(str(row["value"])) for row in kpu_pilpres._ruling_2014()
    }
    tables = {"13": _sumbar(ruling.path), "21": _kepri(ruling.path), "34": _diy(book.path)}
    units = _units()
    result_set = p.id("result_set", "2014-07-09:presiden", "kabkota")
    p.add(
        "result_set",
        id=result_set,
        election_id=ELECTION,
        contest_id=CONTEST,
        stage="certified",
        kind="counted",
        source_id=RULING_SOURCE,
        status="active",
        is_default=False,
        completeness=round(
            sum(cells[ticket][0] for rows in tables.values() for cells in rows.values() for ticket in ("1", "2"))
            / national[kpu_pilpres.TOTAL, "valid", None],
            6,
        ),
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    pages: dict[tuple[str, int], tuple[str, int, int]] = {}
    seen: set[str] = set()
    for province, rows in tables.items():
        label = PROVINCES[province]
        for measure, ticket in (("votes", 1), ("votes", 2), ("valid", None), ("invalid", None)):
            column = str(ticket) if ticket else measure
            if not all(column in cells for cells in rows.values()):
                continue
            if sum(cells[column][0] for cells in rows.values()) != national[label, measure, ticket]:
                raise ValueError(f"{label} {column}: regencies do not sum to the national recapitulation's row")
        artifact, artifact_id = (book, artifacts["book"]) if province == "34" else (ruling, artifacts["ruling"])
        for name, cells in rows.items():
            found = units.get(_key(name))
            if found is None or not found[1].startswith(f"{province}."):
                raise ValueError(f"{label}: no regency unit named {name}")
            unit, _ = found
            if unit in seen:
                raise ValueError(f"{label}: two rows name the regency of {name}")
            seen.add(unit)
            p.external.add(unit)
            for column, (value, word) in cells.items():
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": TICKETS[int(column)] if column.isdigit() else None,
                    "channel": 0,
                    "measure": MEASURES["votes" if column.isdigit() else column],
                    "count_round": 0,
                }
                corrected = province == "13" and (name, column) in CORRECTIONS
                p.add("result_value", **fact, flags=DERIVED if corrected else 0, value=value)
                number = int(word["page_num"])
                if (artifact.sha256, number) not in pages:
                    pages[artifact.sha256, number] = _page(p, artifact, artifact_id, number)
                page, width, height = pages[artifact.sha256, number]
                left, top = float(word["left"]), float(word["top"])
                right, bottom = left + float(word["width"]), top + float(word["height"])
                scale = 100 / 72
                p.add(
                    "value_locator",
                    **fact,
                    page_id=page,
                    x0=left * scale / width,
                    y0=top * scale / height,
                    x1=right * scale / width,
                    y1=bottom * scale / height,
                )
    print(f"{KEY}: {len(seen)} regencies and cities")
    return p
