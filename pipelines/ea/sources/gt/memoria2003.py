"""Guatemala's 2003 general election by municipality, from the memoria's per-municipality PDFs.

The Tribunal Supremo Electoral's 2003 memoria is one two-page PDF for each municipality it lists. Page 1 gives the
municipality's registered voters, its municipal electoral board, its mayoral candidates, and the posts of its
municipal corporation adjudicated to each organization. Page 2 gives the results: the presidential first and second
rounds, the deputies by national list, by district, and to the Central American Parliament, and the mayoral race.
Each number is read with its word box from the PDF's text layer.
"""

import re
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path
from urllib.parse import unquote

from ea import raw
from ea.sources.gt import memoria as base
from ea.sources.gt.memoria import Contest, Grid, Word, fold, is_number, label, lines

SOURCE = base.SOURCE
YEAR = 2003
INDEX = f"{base.SITE}/publicaciones/memorias/memorias-electorales/memoria-electoral-2003"
FOLDER = f"{base.SITE}/images/memoriaselec/memoria_electoral_2003/"
LINK = re.compile(r'href="[^"]*?(memoria_electoral_2003/[^"]+?\.pdf)"')


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    raw.fetch_many(SOURCE, files(), suffix=".pdf")


UNLINKED = [
    f"{FOLDER}San%20Marcos/{name}.pdf"
    for name in (
        "AyutlaTecunUman",
        "Catarina",
        "Comitancillo",
        "ConcepcionTutuapa",
        "ElQuetzal",
        "ElTumbador",
        "EsquipulasPaloGordo",
        "Ixchiguan",
        "LaReforma",
        "Malacatan",
        "Ocos",
        "Pajapita",
        "RioBlanco",
        "SanJoseOjetenan",
        "SanJoseelRodeo",
        "SanLorenzo",
        "SanMarcos",
        "SanMiguelIxtahuacan",
        "SanPablo",
        "SanPedroSacatepequezSM",
        "Sibinal",
        "Sipacapa",
        "Tacana",
        "Tajumulco",
        "Tejutla",
    )
]
"""Municipal files the Tribunal serves in the memoria's folder that its index page does not link. The index links no
file for San Marcos; these were found by asking the folder for each missing municipality's name, spelled the way the
linked files spell theirs. No such file answered for the other municipalities the index leaves out."""


@cache
def files() -> list[str]:
    """The URL of each municipality's PDF: the files the memoria's index page links, in the order of the department
    and municipality names, then the unlinked files its folder serves."""
    page = raw.cached(SOURCE, INDEX).path.read_text(encoding="utf-8", errors="replace")
    linked = {f"{base.SITE}/images/memoriaselec/{m}" for m in LINK.findall(page)}
    if not linked:
        raise ValueError("the 2003 memoria's index page links no municipality")
    return sorted(linked) + [url for url in UNLINKED if url not in linked]


@cache
def used() -> list[raw.Artifact]:
    """The raw files the contests are read from: every municipality's file that holds a text layer."""
    read()
    return [raw.cached(SOURCE, url) for url in files() if url not in SKIPPED]


@cache
def pages() -> dict[int, tuple[raw.Artifact, int]]:
    """Each page the 2003 words are numbered by, mapped to its file and its page number there.

    A word's page is its file's number in `files()` times ten, plus the page within the file.
    """
    out: dict[int, tuple[raw.Artifact, int]] = {}
    read()
    for index, url in enumerate(files(), 1):
        if url in SKIPPED:
            continue
        artifact = raw.cached(SOURCE, url)
        for local in (1, 2):
            out[index * 10 + local] = (artifact, local)
    return out


def department_name(url: str) -> str:
    return unquote(url.removeprefix(FOLDER).split("/")[0])


def town_name(url: str) -> str:
    return unquote(url.removeprefix(FOLDER).split("/")[1].removesuffix(".pdf"))


PRINTED: dict[tuple[int, float, float], str] = {}
"""The text the memoria prints for a number that was read differently, by its page and its box's corner."""
THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")


def _repair(found: list[Word]) -> list[Word]:
    """The page's words with each number the memoria misprints read as printed digits.

    Five typos recur: a letter O for a zero ("4,14O"), a number split in two words ("20," and "033", or "3" and ",436"), a period for the thousands
    comma ("2.624"), a comma after the last digit ("9397,"), and a comma set after the wrong digit ("21,45"), which
    is read as its digits.
    """
    out: list[Word] = []
    for w in sorted(found, key=lambda w: (round(w.yc / 3), w.x0)):
        before = out[-1] if out else None
        if (
            before is not None
            and abs(before.yc - w.yc) <= 3
            and w.x0 - before.x1 < 8
            and (
                (before.text.endswith(",") and is_number(w.text))
                or (before.text.isdigit() and re.fullmatch(r",\d{3}", w.text))
            )
        ):
            out.pop()
            joined = before._replace(
                text=before.text + w.text,
                x1=w.x1,
                box=(before.box[0], min(before.box[1], w.box[1]), w.box[2], max(before.box[3], w.box[3])),
            )
            PRINTED[(joined.page, joined.box[0], joined.box[1])] = f"{before.text} {w.text}"
            out.append(joined)
            continue
        out.append(w)
    fixed: list[Word] = []
    for w in out:
        text_ = w.text
        if "O" in text_ and re.search(r"\d", text_) and is_number(text_.replace("O", "0")):
            PRINTED[(w.page, w.box[0], w.box[1])] = text_
            text_ = text_.replace("O", "0")
            w = w._replace(text=text_)
        if re.fullmatch(r"\d{4,},", text_):
            PRINTED[(w.page, w.box[0], w.box[1])] = text_
            w = w._replace(text=f"{int(text_[:-1]):,}")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text_) or (
            re.fullmatch(r"\d+,\d+", text_) and not THOUSANDS.fullmatch(text_)
        ):
            PRINTED[(w.page, w.box[0], w.box[1])] = text_
            w = w._replace(text=text_.replace(".", "").replace(",", ""))
            w = w._replace(text=f"{int(w.text):,}")
        fixed.append(w)
    return fixed


def _page(path: Path, local: int, number: int) -> list[Word]:
    """A page's words, renumbered to `number`, without the sideways strip of letters down its margin and the printed page number."""
    found = base.words(path, local, local)[local]
    strip = Counter(round(w.x0 / 2) for w in found if len(w.text) == 1 and w.text.isupper())
    height = base.SIZES[(path, local)][1]
    kept = [
        w
        for w in found
        if not (len(w.text) == 1 and w.text.isupper() and strip[round(w.x0 / 2)] >= 5) and w.y0 < height - 60
    ]
    return _repair([w._replace(page=number) for w in kept])


def _cluster(found: list[Word], tolerance: float = 3) -> list[list[Word]]:
    """Words grouped into rows by the center of each, left to right."""
    rows: list[list[Word]] = []
    for w in sorted(found, key=lambda w: w.yc):
        if rows and abs(rows[-1][-1].yc - w.yc) <= tolerance:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w.x0) for r in rows]


def _heading(page: list[Word], *needles: str) -> Word:
    for row in lines(page, tolerance=3):
        folded = [fold(w.text) for w in row]
        if all(any(f.startswith(n) for f in folded) for n in needles):
            return next(w for w, f in zip(row, folded, strict=True) if f.startswith(needles[0]))
    raise ValueError(f"page {page[0].page}: no heading with {needles}")


def _rows(labels: list[Word], numbers: list[Word]) -> list[tuple[str, list[Word]]]:
    """Each label's row of the printed text with the numbers beside it: a number belongs to the nearest label."""
    rows = _cluster(labels, 3)
    out: list[tuple[str, list[Word]]] = [(" ".join(w.text for w in r), []) for r in rows]
    centers = [sum(w.yc for w in r) / len(r) for r in rows]
    for n in numbers:
        near = min(range(len(rows)), key=lambda i: abs(centers[i] - n.yc))
        if abs(centers[near] - n.yc) > 7:
            raise ValueError(f"page {n.page}: the number {n.text} has no label beside it")
        out[near][1].append(n)
    return out


NAMES = {"SanFranciscoelAlto": "San Francisco el Alto", "LaLibertad": "La Libertad"}
COPIES = {f"{FOLDER}Huehuetenango/LaLibertad.pdf"}
"""Files that are byte for byte another municipality's file: Huehuetenango's La Libertad holds the file of Petén's.
The roll it prints, 21,720 registered voters, fits Petén's La Libertad (31,201 in the 2007 memoria) and not
Huehuetenango's (16,636), so it is read as Petén's and the copy is left out."""
SKIPPED: dict[str, str] = {}
"""Files left out, each with why: a copy of another file, or pages with no text layer."""


def _put(grid: Grid, name: str, w: Word, code: str) -> None:
    """A cell into a contest's grid, with a note where the memoria misprints it."""
    grid.put(name, w, grid.cells.setdefault(code, {}))
    printed = PRINTED.get((w.page, w.box[0], w.box[1]))
    if printed is not None:
        grid.notes.append(
            f'In {base._town(code)} the memoria prints {name.strip()} as "{printed}"; it is read as {w.text}.'
        )


def _read(path: Path, index: int, url: str, grids: dict[str, Grid]) -> str | None:
    """One municipality's two pages into the contests' grids; the municipality's code, or None for a file that
    holds no text layer."""
    if url in COPIES:
        SKIPPED[url] = "holds a copy of the file of another municipality of the same name, which is read once"
        return None
    if len(base.words(path, 2, 2)[2]) < 20:
        SKIPPED[url] = "is set as outlines with no text to read"
        return None
    one = _page(path, 1, index * 10 + 1)
    two = _page(path, 2, index * 10 + 2)
    title = next(
        (
            r
            for r in lines(two, tolerance=3)
            if [fold(w.text) for w in r[:2]] == ["RESULTADOS", "MUNICIPIO"]
            or [fold(w.text) for w in r[:3]] == ["RESULTADOS", "DISTRITO", "CENTRAL"]
        ),
        None,
    )
    if title is None:
        raise ValueError(f"{url}: no results title")
    name = (
        " ".join(w.text for w in title[1:])
        if fold(title[1].text) == "DISTRITO"
        # "Resultados Municipio de X", or a few without the "de".
        else " ".join(w.text for w in title[3 if fold(title[2].text) in ("DE", "DEL") else 2 :])
    )
    dep = base.department_code(department_name(url))
    code = base.match(name, base.departments_by_code()[dep])
    if code is None or int(code[:2]) != dep:
        raise ValueError(f"{url}: no municipality for {name!r} in department {dep}")
    central = code == "0101"
    district = "district:00" if central else f"district:{dep:02d}"
    # The regions of the results page.
    president = _heading(two, "PRESIDENTE")
    deputies = _heading(two, "RESULTADOS", "MUNICIPALES")
    mayor = _heading(two, "ALCALDE")
    top = min(deputies.y0, mayor.y0) - 1
    # Presidential rounds: two numeric columns under the two "Resultados" headings.
    heads = sorted(
        (w for w in two if fold(w.text) == "RESULTADOS" and president.y0 < w.y0 < president.y0 + 45), key=lambda w: w.x0
    )
    if len(heads) != 2:
        raise ValueError(f"{url}: {len(heads)} presidential result columns")
    region = [w for w in two if heads[0].y1 < w.y0 and w.y1 < top]
    texts = [w for w in region if not is_number(w.text) and "%" not in w.text]
    name_x = Counter(round(w.x0) for w in texts if w.x0 > 175).most_common(1)[0][0]
    labels = [w for w in texts if w.x1 <= name_x - 4]
    numbers = [w for w in region if is_number(w.text)]
    centers = [w.xc for w in heads]
    for text_, cells in _rows(labels, numbers):
        if fold(text_).startswith("ABSTENC"):
            continue
        for w in cells:
            column = min((0, 1), key=lambda i: abs(centers[i] - w.xc))
            kind = "president" if column == 0 else "president2"
            grid = grids[kind]
            if label(text_) in grid.cells.setdefault(code, {}):
                raise ValueError(f"{url}: {text_} twice in {kind}")
            _put(grid, text_, w, code)
    # The deputies: three numeric columns, set against the headings of each.
    split = 300
    header = [
        w
        for w in two
        if deputies.y1 < w.y0 < mayor.y0 + 200
        and w.x0 < split
        and fold(w.text) in ("PARTIDO", "POLITICO", "LISTA", "NACIONAL", "DISTRITALES", "PARLAMENTO", "CENTROAMERICANO")
        and w.y0 < deputies.y1 + 60
    ]
    bottom = max(w.y1 for w in header)
    groups = {
        "national": ("LISTA", "NACIONAL"),
        "district": ("DISTRITALES",),
        "parlacen": ("PARLAMENTO", "CENTROAMERICANO"),
    }
    cols: dict[str, float] = {}
    for kind, needles in groups.items():
        found = [w for w in two if fold(w.text) in needles and deputies.y1 < w.y0 < bottom]
        if len(found) != len(needles):
            # A column the page does not print.
            continue
        cols[kind] = sum(w.xc for w in found) / len(found)
    if not cols:
        raise ValueError(f"{url}: no deputies columns")
    region = [w for w in two if w.y0 > bottom - 1]
    texts = [w for w in region if w.x0 < split and not is_number(w.text) and "%" not in w.text]
    numbers = [w for w in region if is_number(w.text) and w.x0 < 480]
    order = list(cols)
    for text_, cells in _rows(texts, numbers):
        for w in cells:
            column = min(order, key=lambda k: abs(cols[k] - w.xc))
            kind = district if column == "district" else column
            grid = grids[kind]
            _put(grid, text_, w, code)
            if column == "district" and central:
                grid.totals.setdefault(0, grid.cells[code])
    # The mayoral race.
    mayor_head = _heading([w for w in two if w.y0 > mayor.y0], "RESULTADOS")
    region = [w for w in two if w.y0 > mayor_head.y1 and w.x0 >= split]
    texts = [
        w
        for w in region
        if not is_number(w.text)
        and "%" not in w.text
        and w.x0 < 480
        and not (fold(w.text) in ("PARLAMENTO", "CENTROAMERICANO") and w.y1 <= bottom + 2)
    ]
    numbers = [w for w in region if is_number(w.text) and w.x0 >= 480]
    grid = grids[f"municipal:{code}"]
    for text_, cells in _rows(texts, numbers):
        for w in cells:
            _put(grid, text_, w, code)
    # Page 1: the registered voters and the corporation's adjudicated posts.
    registered = _registered(one, url, code)
    for kind in ("president", "president2"):
        if any(base.number(w) for w in grids[kind].cells[code].values()):
            _put(grids[kind], "Padron", registered, code)
    if MISMATCH.get(code):
        grids["president"].notes.append(
            f"In {base._town(code)} the memoria's registered men and women add up to {MISMATCH[code][0]:,}, not to its "
            f"printed total of {MISMATCH[code][1]:,}, which is stored."
        )
    grid.seats = base._adjudicated(_posts(one, url), set(grid.printed), url)
    if central:
        seats = grids[district]
        seats.seats = base._adjudicated(_deputies(one, url), set(seats.printed), url)
    return code


MISMATCH: dict[str, tuple[int, int]] = {}
"""Municipalities whose printed registered men and women do not add up to the printed total, with both."""


def _registered(page: list[Word], url: str, code: str) -> Word:
    """The registered voters printed under "TOTAL" of the "Cantidad de Empadronados" box on page 1."""
    head = next(
        (r for r in lines(page, tolerance=3) if {"HOMBRES", "MUJERES", "TOTAL"} <= {fold(w.text) for w in r}), None
    )
    if head is None:
        raise ValueError(f"{url}: no registered voters")
    left = min(w.x0 for w in head if fold(w.text) in ("HOMBRES", "MUJERES", "TOTAL")) - 20
    right = max(w.x1 for w in head if fold(w.text) == "TOTAL") + 30
    # One municipality prints each count with a stray percent sign.
    row = sorted(
        (
            w._replace(text=w.text.removesuffix("%"))
            for w in page
            if is_number(w.text.removesuffix("%")) and head[0].y1 < w.y0 < head[0].y1 + 25 and left <= w.x0 <= right
        ),
        key=lambda w: w.x0,
    )
    if len(row) != 3:
        raise ValueError(f"{url}: registered voters {[w.text for w in row]}")
    if sum(base.number(w) for w in row[:2]) != base.number(row[2]):
        MISMATCH[code] = (sum(base.number(w) for w in row[:2]), base.number(row[2]))
    return row[2]


def _posts(page: list[Word], url: str) -> list[list[str]]:
    """The titular posts adjudicated on page 1, each as the label tokens of its organization."""
    head = _heading(page, "CORPORACION", "MUNICIPAL")
    party = next(w for w in page if fold(w.text) == "PARTIDO" and w.y0 > head.y0 and w.x0 > head.x0 - 30)
    cargo = next(w for w in page if fold(w.text) == "CARGO" and w.y0 > head.y0)
    posts: list[list[str]] = []
    for row in _cluster([w for w in page if w.y0 > cargo.y1 and w.x0 > head.x0 - 60], 3):
        first = next((w for w in row if fold(w.text) in base.POSTS and w.x0 >= cargo.x0 - 25), None)
        if first is None or any(fold(w.text) == "SUPLENTE" for w in row):
            continue
        tokens = [label(w.text) for w in row if party.x0 - 10 <= w.x0 and w.x1 <= first.x0 + 1 and w is not first]
        posts.append(tokens)
    if not posts:
        raise ValueError(f"{url}: no adjudicated posts")
    return posts


def _deputies(page: list[Word], url: str) -> list[list[str]]:
    """The Distrito Central's district deputies adjudicated on page 1, each as the label tokens of its organization."""
    head = _heading(page, "ADJUDICACIONES", "DIPUTADOS")
    name = next(w for w in page if fold(w.text) == "NOMBRE" and w.y0 > head.y0 and w.x0 < 300)
    corporation = _heading(page, "CORPORACION", "MUNICIPAL")
    rows = _cluster([w for w in page if w.y0 > name.y1 and w.x1 < corporation.x0 - 40], 3)
    party = next(w for w in page if fold(w.text) == "POLITICO" and abs(w.y0 - name.y0) < 4 and w.x0 < name.x0)
    return [
        [label(w.text) for w in row if w.x1 <= party.x1 + 3] for row in rows if any(w.x0 > party.x1 + 3 for w in row)
    ]


@cache
def read() -> dict[str, Contest]:
    grids: dict[str, Grid] = defaultdict(Grid)
    for index, url in enumerate(files(), 1):
        _read(raw.cached(SOURCE, url).path, index, url, grids)
    contests = read_contests(grids)
    left = [
        f"{NAMES.get(town_name(url), town_name(url))} ({department_name(url)}) {why}" for url, why in SKIPPED.items()
    ]
    unlinked = sum(url in UNLINKED for url in files())
    note = (
        f"The memoria holds a file for {len(files())} of the country's municipalities ({unlinked} of them, San Marcos's, "
        f"served in its folder but not linked from its index page), and these results are read from "
        f"{len(files()) - len(SKIPPED)} of them."
    )
    if left:
        note += f" The file of {'; the file of '.join(left)}; each is left out."
    for contest in contests.values():
        contest.notes.insert(0, note)
    return contests


PARTS = {"PSN", "PP", "MR"}
"""Parties that ran with a municipality's alliance under their own acronyms."""


def alliances() -> set[str]:
    """The labels of mayoral tickets that are one national party or an alliance of national parties."""
    contests = read()
    national = {label(c) for k, ct in contests.items() if not k.startswith("municipal") for c in ct.columns}
    found: set[str] = set()
    for key_, ct in contests.items():
        if not key_.startswith("municipal"):
            continue
        for column in ct.columns:
            printed = " ".join(column.replace("- ", "-").split())
            parts = [label(part) for part in printed.split("-")]
            if label(column) not in national and all(part in national | PARTS for part in parts):
                found.add(label(column))
    return found


def read_contests(grids: dict[str, Grid]) -> dict[str, Contest]:
    return {k: base._contest(k, k.partition(":")[0], g) for k, g in grids.items()}
