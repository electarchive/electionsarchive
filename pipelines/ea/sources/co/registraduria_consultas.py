"""Colombia's party consultations of 14 March 2010 for the presidency, by department, from the scrutiny forms.

On the day of the 2010 congressional elections the Partido Conservador Colombiano and the Partido Verde chose their
presidential candidates in internal consultations (consultas internas) run by the Registraduría Nacional del Estado
Civil. The statistics CD of 2010 that the Observatorio republishes holds the congressional and presidential tables
only, so these results come from the departmental scrutiny forms: the E-26, a one-page certificate of each
department's totals that prints every figure in numerals and in words, and the E-24, a table by municipality (by
scrutiny commission in Bogotá) whose last column holds the department's totals and whose last two rows print the
votes for the candidates and all the votes. The forms on hand cover 13 departments for the Conservative consultation
and 12 for the Green one; the rest were not saved, so each contest holds those departments only.

Most forms are scans. Each figure is read twice: by OCR of the page (Tesseract), and from the page image together
with the check the form itself carries (the figure in words on an E-26, the two printed sums on an E-24). The two
readings are compared here, and a figure the OCR misread is listed in `REVIEWED` with what the page shows. Each
stored number keeps its page and its box on the page.
"""

import csv
import io
import re
import statistics
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.co import registraduria as rnec
from ea.store import DATA

SOURCE = "co.registraduria_consultas"
DAY = date(2010, 3, 14)
KEY = f"{SOURCE}/{DAY}"
SAVED = "browser://20261119-013157-d148b3/"
"""The forms were saved by hand from the Registraduría's site in a browser and are kept under these keys."""
SCRATCH = DATA / "tmp" / SOURCE
PAGES = DATA / "pages" / SOURCE
DPI = 300
COLOMBIA = rnec.COLOMBIA
COUNTRY = rnec.COUNTRY
PRESIDENCY = rnec.PRESIDENCY
VOTES, INVALID, BLANK = 1, 5, 6


def key(kind: str, *parts: object) -> str:
    return package.Package(SOURCE, SOURCE, "").id(kind, *parts)


@dataclass(frozen=True)
class Consultation:
    code: str
    party: str
    slug: str
    english: str
    candidates: tuple[tuple[str, str], ...]
    """Each candidate's ballot number and name as the forms print them, with a pattern that finds the row."""


CONSULTATIONS = {
    "PCC": Consultation(
        "PCC",
        "Consulta interna del Partido Conservador Colombiano",
        "partido-conservador-colombiano",
        "Colombian Conservative Party presidential consultation",
        (
            ("MARTHA LUCIA RAMIREZ", r"RAM[I1]REZ|LUC[I1]A"),
            ("JOSE GALAT NOUMER", r"GALAT|NOU"),
            ("ALVARO LEYVA DURAN", r"LEYVA|DURAN"),
            ("NOEMI SANIN", r"SAN[I1]N"),
            ("ANDRES FELIPE ARIAS", r"AR[I1]AS|FELIPE"),
        ),
    ),
    "PV": Consultation(
        "PV",
        "Consulta interna del Partido Verde",
        "partido-verde",
        "Green Party presidential consultation",
        (("MOCKUS", r"MOCKUS"), ("PEÑALOSA", r"ALOSA"), ("LUCHO", r"LUCHO")),
    ),
}
SPECIAL = ((INVALID, "VOTOS NULOS", r"NULOS"), (BLANK, "NO MARCADOS", r"MARCAD|MARCAL"))
TOTALS = (r"POR CANDIDATOS", r"TOTAL DE VOTOS(?! POR)")


@dataclass(frozen=True)
class Form:
    file: str
    consultation: str
    dep: str
    page: int
    values: tuple[int, ...]
    """The department's figures in printed row order: each candidate, then null and unmarked ballots."""
    totals: tuple[int, int] | None = None
    """An E-24's printed votes for the candidates and all votes, which the figures must add up to."""
    stored: bool = True
    layer: bool = False
    """Read from the PDF's text layer instead of by OCR (the Bogotá table, which prints no row labels on its
    last page)."""

    @property
    def url(self) -> str:
        return SAVED + self.file

    @property
    def kind(self) -> str:
        return self.file[:4]


FORMS = [
    # Partido Conservador Colombiano: E-26 certificates
    Form("E-26_PCC_Boyaca.PDF", "PCC", "07", 1, (7855, 7048, 2220, 39720, 38204, 1670, 11699)),
    Form("E-26_PCC_Caqueta.PDF", "PCC", "44", 1, (1086, 327, 554, 5577, 7763, 253, 1782)),
    Form("E-26_PCC_Cordoba.PDF", "PCC", "13", 1, (3344, 1475, 1481, 21161, 25242, 933, 15713)),
    Form("E-26_PCC_Narino.PDF", "PCC", "23", 1, (5605, 3085, 1920, 42405, 38855, 1959, 13202)),
    Form("E-26_PCC_Norte_de_Santander.PDF", "PCC", "25", 1, (8426, 2567, 2724, 53581, 52801, 2661, 19575)),
    Form("E-26_PCC_Putumayo.PDF", "PCC", "64", 1, (575, 374, 260, 4838, 2777, 122, 1144)),
    Form("E-26_PCC_Valle.PDF", "PCC", "31", 1, (21630, 7278, 6958, 127064, 89563, 4901, 26140)),
    # Partido Conservador Colombiano: E-24 tables, the department's column
    Form("E-24_PCC_Amazonas.PDF", "PCC", "60", 1, (200, 44, 91, 920, 702, 16, 271), (1957, 2244)),
    Form(
        "E-24_PCC_Antioquia.PDF",
        "PCC",
        "01",
        16,
        (43769, 8178, 7141, 157140, 153084, 7115, 37489),
        (369312, 413916),
    ),
    Form("E-24_PCC_Arauca.PDF", "PCC", "40", 1, (650, 217, 164, 2624, 3181, 116, 695), (6836, 7647)),
    Form("E-24_PCC_Cauca.PDF", "PCC", "11", 6, (4560, 1451, 1314, 25148, 16928, 1591, 8557), (49401, 59549)),
    Form("E-24_PCC_La_Guajira.PDF", "PCC", "48", 3, (2318, 789, 1087, 17781, 11563, 744, 9204), (33538, 43486)),
    Form("E-24_PCC_Risaralda.PDF", "PCC", "24", 2, (4636, 1697, 1331, 26792, 20236, 1081, 4819), (54692, 60592)),
    Form(
        "E-24_PCC_Valle.PDF",
        "PCC",
        "31",
        6,
        (21630, 7278, 6958, 127064, 89563, 4901, 26140),
        (252493, 283534),
        stored=False,
    ),
    # Partido Verde: E-26 certificates
    Form("E-26_PV_Amazonas.PDF", "PV", "60", 1, (651, 384, 199, 8, 248)),
    Form("E-26_PV_Atlantico.PDF", "PV", "03", 1, (27579, 19876, 12095, 2272, 25745)),
    Form("E-26_PV_Caldas.PDF", "PV", "09", 1, (13049, 8343, 3412, 1052, 2777)),
    Form("E-26_PV_Cundinamarca.PDF", "PV", "15", 1, (47532, 24958, 16368, 3252, 11709)),
    Form("E-26_PV_Meta.PDF", "PV", "52", 1, (15705, 6424, 3866, 678, 2499)),
    Form("E-26_PV_Narino.PDF", "PV", "23", 1, (17297, 10540, 7442, 864, 7011)),
    Form("E-26_PV_Santander.PDF", "PV", "27", 1, (55860, 23476, 15253, 1760, 6927)),
    # Partido Verde: E-24 tables, the department's column
    Form("E-24_PV_Bogota.PDF", "PV", "16", 3, (342034, 187314, 102182, 8542, 22433), (631530, 662505), layer=True),
    Form("E-24_PV_Cesar.PDF", "PV", "12", 4, (10746, 9139, 5446, 722, 7884), (25331, 33937)),
    Form("E-24_PV_Norte_de_Santander.PDF", "PV", "25", 6, (15911, 10814, 5553, 1101, 5962), (32278, 39341)),
    Form("E-24_PV_Vaupes.PDF", "PV", "68", 1, (207, 115, 47, 0, 126), (369, 495)),
    Form("E-24_PV_Vichada.PDF", "PV", "72", 1, (366, 294, 162, 19, 616), (822, 1457)),
]
REVIEWED = {
    ("E-26_PCC_Caqueta.PDF", 2): "the OCR reads 854; the page prints 554, in words QUINIENTOS CINCUENTA Y CUATRO",
    ("E-26_PCC_Narino.PDF", 1): "the OCR reads 3985; the page prints 3085, in words TRES MIL OCHENTA Y CINCO",
    ("E-26_PCC_Narino.PDF", 4): "the OCR reads 38895; the page prints 38855, in words ...OCHOCIENTOS CINCUENTA...",
    ("E-24_PCC_Amazonas.PDF", 3): "the OCR reads 520; the page prints 920, which the printed sums confirm",
    ("E-24_PCC_Amazonas.PDF", 6): "the OCR reads 21; the page prints 271, which the printed sums confirm",
    ("E-24_PCC_Arauca.PDF", 1): "the OCR reads 397; the page prints 217, which the printed sums confirm",
    ("E-24_PCC_Arauca.PDF", 3): "the OCR reads 2324; the page prints 2624, which the printed sums confirm",
    ("E-24_PCC_Arauca.PDF", 4): "the OCR reads 318; the page prints 3181, which the printed sums confirm",
    ("E-24_PCC_Arauca.PDF", 6): "the OCR reads 895; the page prints 695, which the printed sums confirm",
    ("E-24_PV_Norte_de_Santander.PDF", 6): "the OCR reads 39241; the page prints 39341, the sum of the column",
    ("E-24_PCC_Risaralda.PDF", 8): "the OCR reads 80592; the page prints 60592, the sum of the column",
}
"""Figures where the OCR and the page disagree, by form and row, with what the page shows."""
DEPARTMENTS = rnec.DEPARTMENTS


def artifact(form: Form) -> raw.Artifact:
    return raw.cached(SOURCE, form.url)


def fetch() -> None:
    """The forms are no longer linked from a page a plain request reaches; they were saved from the Registraduría's
    site in a browser and are added to the raw cache with `raw.store` under their `browser://` keys."""
    for form in FORMS:
        artifact(form)


# Reading ------------------------------------------------------------------------------------------------------------


Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Token:
    text: str
    box: Box
    """In points from the page's top left."""


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()


def rendered(form: Form, dpi: int) -> Path:
    path = artifact(form).path
    out = (PAGES if dpi != DPI else SCRATCH) / path.stem / f"{form.page}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png", "-f", str(form.page), "-l", str(form.page), "-singlefile"]
            + (["-gray"] if dpi == DPI else [])
            + [str(path), str(out.with_suffix(""))],
            check=True,
        )
    return out


def size(form: Form) -> tuple[int, int]:
    """The page's width and height in points, as rendered (after any rotation the PDF sets)."""
    from PIL import Image

    with Image.open(rendered(form, DPI)) as image:
        width, height = image.size
    return round(width * 72 / DPI), round(height * 72 / DPI)


def ocr_lines(form: Form) -> list[list[Token]]:
    image = rendered(form, DPI)
    cache_file = image.with_suffix(".tsv")
    if not cache_file.exists():
        cache_file.write_text(
            subprocess.run(
                ["tesseract", str(image), "stdout", "--psm", "6", "-l", "spa", "tsv"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
    lines: dict[tuple[str, str, str], list[Token]] = {}
    scale = 72 / DPI
    for row in csv.DictReader(io.StringIO(cache_file.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] != "5" or not row["text"].strip():
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        lines.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append(
            Token(row["text"], (x * scale, y * scale, (x + w) * scale, (y + h) * scale))
        )
    return list(lines.values())


def layer_tokens(form: Form) -> list[Token]:
    xhtml = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(form.page), "-l", str(form.page), str(artifact(form).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        Token(text, (float(x0), float(y0), float(x1), float(y1)))
        for x0, y0, x1, y1, text in re.findall(
            r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', xhtml
        )
    ]


def numeric(token: Token) -> int | None:
    digits = re.sub(r"[.,]", "", token.text.strip(".,:;'\"|"))
    return int(digits) if re.fullmatch(r"\d+", digits) else None


@dataclass(frozen=True)
class Reading:
    value: int
    box: Box
    ocr: int | None
    """What the OCR read at that place, or None where it found no figure."""


def patterns(form: Form) -> list[str]:
    c = CONSULTATIONS[form.consultation]
    rows = [pattern for _, pattern in c.candidates] + [pattern for _, _, pattern in SPECIAL]
    return rows + list(TOTALS) if form.totals else rows


def printed(form: Form) -> tuple[int, ...]:
    return form.values + (form.totals or ())


def _layer_readings(form: Form) -> list[Reading]:
    """The Bogotá table's last page: its TOTAL column is the rightmost figure on each row, top to bottom."""
    words = layer_tokens(form)
    head = next(t.box for t in words if t.text == "TOTAL")
    middle = (head[0] + head[2]) / 2
    tokens = [t for t in words if numeric(t) is not None and t.box[1] > head[3]]
    column = sorted((t for t in tokens if abs((t.box[0] + t.box[2]) / 2 - middle) < 6), key=lambda t: t.box[1])
    wanted = printed(form)
    if len(column) != len(wanted):
        raise ValueError(f"{form.file}: {len(column)} figures in the total column, {len(wanted)} expected")
    return [Reading(v, t.box, numeric(t)) for v, t in zip(wanted, column, strict=True)]


def _ocr_readings(form: Form) -> list[Reading]:
    rows = patterns(form)
    found: dict[int, Token | None] = {}
    heights: dict[int, Box] = {}
    for line in ocr_lines(form):
        text = fold(" ".join(t.text for t in line))
        for index, pattern in enumerate(rows):
            if index in found or not re.search(pattern, text):
                continue
            label = next((t for t in line if re.search(pattern, fold(t.text))), line[0])
            after = [t for t in line if t.box[0] > label.box[2] and numeric(t) is not None]
            if not after:
                after = [t for t in line if t.box[0] > label.box[2] and re.search(r"\d", t.text)]
            if form.kind == "E-24":
                token = max(after, key=lambda t: t.box[2]) if after else None
            else:
                token = min(after, key=lambda t: t.box[0]) if after else None
            found[index] = token
            heights[index] = (label.box[0], min(t.box[1] for t in line), label.box[2], max(t.box[3] for t in line))
            break
    placed = {i: t for i, t in found.items() if t is not None}
    if form.kind == "E-24":
        left = statistics.median(t.box[0] for t in placed.values())
        right = statistics.median(t.box[2] for t in placed.values())
        placed = {i: t for i, t in placed.items() if t.box[0] < right and t.box[2] > left}
    if len(placed) < 3:
        raise ValueError(f"{form.file}: the OCR placed only {len(placed)} rows")
    x0 = statistics.median(t.box[0] for t in placed.values())
    x1 = statistics.median(t.box[2] for t in placed.values())
    tall = statistics.median(t.box[3] - t.box[1] for t in placed.values())
    known = sorted((i, (t.box[1] + t.box[3]) / 2) for i, t in placed.items())
    readings = []
    for index, value in enumerate(printed(form)):
        token = placed.get(index)
        if token is not None:
            readings.append(Reading(value, token.box, numeric(token)))
            continue
        if index in heights:
            middle = (heights[index][1] + heights[index][3]) / 2
        else:
            middle = _between(known, index)
        width = x1 - x0
        if form.kind == "E-26":
            width = max(width, len(str(value)) * tall * 0.6)
        readings.append(Reading(value, (x0, middle - tall / 2, x0 + width, middle + tall / 2), None))
    return readings


def _between(known: list[tuple[int, float]], index: int) -> float:
    """A row's middle, from the rows on either side of it (or the two nearest, when it lies at an end)."""
    before = [k for k in known if k[0] < index]
    after = [k for k in known if k[0] > index]
    pair = (before[-1], after[0]) if before and after else (known[0], known[1]) if after else (known[-2], known[-1])
    (i0, y0), (i1, y1) = pair
    return y0 + (y1 - y0) * (index - i0) / (i1 - i0)


@cache
def readings(form: Form) -> list[Reading]:
    found = _layer_readings(form) if form.layer else _ocr_readings(form)
    disagree = [
        (index, r.ocr, r.value)
        for index, r in enumerate(found)
        if r.ocr is not None and r.ocr != r.value and (form.file, index) not in REVIEWED
    ]
    if disagree:
        raise ValueError(f"{form.file}: OCR and reading differ (row, OCR, reading): {disagree}")
    stale = [index for (file, index) in REVIEWED if file == form.file and found[index].ocr == found[index].value]
    if stale:
        raise ValueError(f"{form.file}: rows {stale} are listed as reviewed but the OCR now agrees")
    return found


def check(form: Form) -> None:
    c = CONSULTATIONS[form.consultation]
    if len(form.values) != len(c.candidates) + len(SPECIAL):
        raise ValueError(f"{form.file}: {len(form.values)} figures")
    if form.totals:
        candidates = sum(form.values[: len(c.candidates)])
        if (candidates, sum(form.values)) != form.totals:
            raise ValueError(f"{form.file}: rows sum to {candidates}, {sum(form.values)}; printed {form.totals}")


def forms() -> list[Form]:
    stored: dict[tuple[str, str], Form] = {}
    for form in FORMS:
        check(form)
        if not form.stored:
            continue
        if (form.consultation, form.dep) in stored:
            raise ValueError(f"{form.file}: two forms for one department")
        stored[form.consultation, form.dep] = form
    for form in FORMS:
        if not form.stored and stored[form.consultation, form.dep].values != form.values:
            raise ValueError(f"{form.file}: differs from {stored[form.consultation, form.dep].file}")
    return list(stored.values())


# The package --------------------------------------------------------------------------------------------------------

LICENSE = rnec.LICENSE
METHOD = (
    "Read from the departmental scrutiny forms of the Registraduría Nacional del Estado Civil: the E-26, which "
    "certifies a department's totals and prints each figure in numerals and in words, and, where no E-26 was saved, "
    "the department's total column of the E-24, the table of the scrutiny by municipality (by scrutiny commission in "
    "Bogotá). Each figure was read by OCR and again from the page image, against the figure in words on an E-26 and "
    "against the two sums each E-24 prints (the votes for the candidates, and all votes); every figure agrees with "
    "both. Null votes (votos nulos) are stored as invalid ballots and unmarked ballots (no marcados) as blank "
    "ballots, as for Colombia's other elections in the archive. The ballot had no blank-vote option. Each stored "
    "number keeps its page and its box on the page."
)


def caveat(held: list[str]) -> str:
    missing = sorted(DEPARTMENTS[d] for d in DEPARTMENTS if d not in held and d != "88")
    return (
        f"Only {len(held)} of the 33 departmental scrutinies (32 departments and Bogotá) are held here, because only "
        f"their forms were saved: {', '.join(sorted(DEPARTMENTS[d] for d in held))}. The departments not held "
        f"({', '.join(missing)}) and the votes cast abroad are left out, so the sum of the departments held is not "
        "the national result, and no winner is recorded here."
    )


def packages() -> list[str]:
    return [KEY]


def election() -> Package:
    held = forms()
    p = Package(
        KEY,
        SOURCE,
        "Colombia's party consultations for the presidency of 14 March 2010, by department, from the Registraduría's "
        "departmental scrutiny forms",
    )
    p.external |= {COLOMBIA, COUNTRY, PRESIDENCY, "co.departamento"}
    source = key("source", "forms")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Registraduría Nacional del Estado Civil",
        title="Consultas internas del 14 de marzo de 2010: actas de escrutinio departamental (formularios E-24 y E-26)",
        license=LICENSE,
        attribution="Registraduría Nacional del Estado Civil, Colombia",
    )
    election_id = key("election", DAY)
    p.add("election", id=election_id, polity_id=COLOMBIA, kind="primary", date_from=DAY, date_to=DAY, status="held")
    p.name(election_id, "2010 Colombian party consultations", "en", "official")
    p.name(election_id, "Consultas internas de partidos de 2010", "es", "native", False)
    base = f"co/{DAY}-primaries"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    pages: dict[str, str] = {}
    artifacts: dict[str, str] = {}
    for form in held:
        a = artifact(form)
        artifacts[form.file] = p.artifact(a, source)
        rendered(form, 150)
        width, height = size(form)
        pages[form.file] = key("page", form.file, form.page)
        p.add(
            "source_page",
            id=pages[form.file],
            artifact_id=artifacts[form.file],
            page_number=form.page,
            image_key=f"pages/{SOURCE}/{a.path.stem}/{form.page}.png",
            width=width,
            height=height,
        )
    units: dict[str, str] = {}
    for dep in sorted({form.dep for form in held}):
        units[dep] = key("unit", DAY, dep)
        area = rnec.area("co.departamento", dep)
        p.external.add(area)
        p.add(
            "reporting_unit",
            id=units[dep],
            polity_id=COLOMBIA,
            scheme_code="co.departamento",
            code=f"{DAY}:{dep}",
            area_relation="equals",
            area_id=area,
            kind="aggregate",
        )
    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    for c in CONSULTATIONS.values():
        mine = [form for form in held if form.consultation == c.code]
        contest = key("contest", DAY, c.code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=PRESIDENCY,
            district_area_id=COUNTRY,
            magnitude=1,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, c.party, "es", "official")
        p.name(contest, f"2010 {c.english}", "en", "official", False)
        p.add("slug", path=f"{base}/{c.slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, c.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        p.add(
            "note",
            id=key("note", result_set, "coverage"),
            entity_id=result_set,
            kind="caveat",
            body_md=caveat([form.dep for form in mine]),
            lang="en",
        )
        options: list[str | None] = []
        for position, (label, _) in enumerate(c.candidates, start=1):
            option = key("option", DAY, c.code, position)
            name = rnec.title(label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            person = key("person", c.code, position)
            p.add("person", id=person)
            p.name(person, name, "es", "official")
            p.add(
                "candidacy",
                id=key("candidacy", c.code, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
            )
            options.append(option)
        options += [None] * len(SPECIAL)
        measures = [VOTES] * len(c.candidates) + [measure for measure, _, _ in SPECIAL]
        for form in mine:
            unit = units[form.dep]
            for index, (option, measure) in enumerate(zip(options, measures, strict=True)):
                reading = readings(form)[index]
                values.append((result_set, unit, option, measure, reading.value))
                locators.append((result_set, unit, option, measure, pages[form.file], *reading.box))
    facts = pl.DataFrame(
        values,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "value": pl.Int64,
        },
        orient="row",
    )
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value="value",
        ),
    )
    located = pl.DataFrame(
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
    )
    p.frame(
        "value_locator",
        located.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            page_id="page_id",
            source_id=pl.lit(source),
            x0="x0",
            y0="y0",
            x1="x1",
            y1="y1",
        ),
    )
    return p


def totals() -> dict[str, dict[str, int]]:
    """Each consultation's sums over the departments held, by row."""
    out: dict[str, dict[str, int]] = {}
    for c in CONSULTATIONS.values():
        labels = [label for label, _ in c.candidates] + [label for _, label, _ in SPECIAL]
        mine = [form for form in forms() if form.consultation == c.code]
        out[c.code] = {label: sum(form.values[i] for form in mine) for i, label in enumerate(labels)}
    return out


def parse() -> None:
    for form in FORMS:
        found = readings(form)
        guessed = [i for i, r in enumerate(found) if r.ocr is None]
        print(f"{form.file}: {len(found)} figures, placed without an OCR figure: {guessed}", flush=True)
    for code, sums in totals().items():
        print(f"{code}: {sums}", flush=True)
    election().write()


def emit() -> None:
    package.run("load", packages())
