import difflib
import io
import itertools
import json
import re
import statistics
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.ca_pres_sov"
RAW = "us.ca_sov"
VERSION = "1"
PUBLISHER = "California Secretary of State"
LICENSE = "Public record"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
TITLE = "Statement of Vote, Primary Election, {day:%B %-d, %Y}"
COUNTIES = [
    "Alameda", "Alpine", "Amador", "Butte", "Calaveras", "Colusa", "Contra Costa", "Del Norte", "El Dorado",
    "Fresno", "Glenn", "Humboldt", "Imperial", "Inyo", "Kern", "Kings", "Lake", "Lassen", "Los Angeles", "Madera",
    "Marin", "Mariposa", "Mendocino", "Merced", "Modoc", "Mono", "Monterey", "Napa", "Nevada", "Orange", "Placer",
    "Plumas", "Riverside", "Sacramento", "San Benito", "San Bernardino", "San Diego", "San Francisco",
    "San Joaquin", "San Luis Obispo", "San Mateo", "Santa Barbara", "Santa Clara", "Santa Cruz", "Shasta",
    "Sierra", "Siskiyou", "Solano", "Sonoma", "Stanislaus", "Sutter", "Tehama", "Trinity", "Tulare", "Tuolumne",
    "Ventura", "Yolo", "Yuba",
]  # fmt: skip
PARTIES = {
    "D": ("Democratic", "democratic-party"),
    "R": ("Republican", "republican-party"),
    "AI": ("American Independent", "CA:american-independent-party"),
    "PF": ("Peace and Freedom", "CA:peace-and-freedom-party"),
}
ITEMS = {
    1976: ("statementofvote1976cali", date(1976, 6, 8)),
    1980: ("statementofvote31980cali", date(1980, 6, 3)),
    1984: ("statementofvote51984cali", date(1984, 6, 5)),
    1988: ("statementofvote71988cali", date(1988, 6, 7)),
}
D76 = [
    "D|Edmund G. Brown, Jr.",
    "D|Jimmy Carter",
    "D|Frank Church",
    "D|Morris Udall",
    "D|George Wallace",
    "D|Uncommitted delegation",
    "D|Henry Jackson",
    "D|Ellen McCormack",
    "D|Fred R. Harris",
    "D|Birch Bayh",
]
D80 = [
    "D|Edward M. Kennedy",
    "D|Unpledged delegation",
    "D|Lyndon H. LaRouche, Jr.",
    "D|Jimmy Carter",
    "D|Edmund G. Brown, Jr.",
]
TABLES: dict[int, list[tuple[int, list[str]]]] = {
    1976: [
        (15, D76),
        (20, ["R|Ronald Reagan", "R|Gerald Ford"]),
        (21, ["AI|George J. Dreskovich", "AI|John R. Haas", "AI|Andrew J. Gentile", "AI|Alberta M. Price",
              "AI|Thomas M. Ziegler", "AI|Delegates expressing no preference"]),
        (22, ["PF|Margaret Wright", "PF|Frank P. Zeidler", "PF|Delegates preferring Margaret Wright"]),
    ],
    1980: [
        (19, D80),
        (24, ["R|John B. Anderson", "R|Ronald Reagan", "R|Phil Crane", "R|Benjamin Fernandez", "R|George Bush"]),
        (25, ["AI|Sean Morton Downey, Jr.", "AI|John Rarick", "AI|Delegates expressing no preference"]),
        (26, ["PF|Gus Hall", "PF|David McReynolds", "PF|Benjamin Spock", "PF|Deirdre Griswold"]),
    ],
    1984: [
        (82, ["R|Ronald Reagan", "AI|Charles R. Glenn", "AI|A. J. Lowery", "AI|Gordon \"Jack\" Mohr",
              "AI|Gerald Willis", "PF|Tom Condit", "PF|Gavrielle Holmes", "PF|Sonia Johnson", "PF|Dennis L. Serrette"]),
    ],
    1988: [
        (29, ["D|Michael S. Dukakis", "D|Al Gore", "D|Jesse Jackson", "D|Lyndon LaRouche", "D|Paul Simon",
              "R|George Bush", "R|Bob Dole", "R|Pat Robertson"]),
        (30, ["AI|James C. Griffin", "AI|James \"Bo\" Gritz", "PF|Lenora B. Fulani", "PF|Al Hamburg",
              "PF|Larry Holmes", "PF|Shirley Isaacson", "PF|Willa Kenoyer", "PF|Herb Lewin"]),
    ],
}  # fmt: skip
SPLITS: dict[tuple[int, int], tuple[str, str]] = {}
DEBUG = False
LEFT: dict[tuple[int, int], float] = {(1976, 22): 0.3}
FIXES: dict[tuple[int, str, str], int] = {
    (1976, "Tehama", "D|Henry Jackson"): 44,
    (1976, "Siskiyou", "AI|John R. Haas"): 11,
    (1976, "Trinity", "AI|John R. Haas"): 11,
    (1976, "Calaveras", "R|Ronald Reagan"): 1789,
    (1976, "Contra Costa", "R|Ronald Reagan"): 88732,
    (1976, "El Dorado", "R|Ronald Reagan"): 5922,
    (1976, "Glenn", "R|Ronald Reagan"): 1729,
    (1976, "Inyo", "R|Ronald Reagan"): 1838,
    (1976, "Ventura", "R|Ronald Reagan"): 35501,
    (1976, "Mono", "R|Gerald Ford"): 303,
    (1980, "Contra Costa", "R|Ronald Reagan"): 63985,
    (1980, "El Dorado", "R|Ronald Reagan"): 9942,
    (1980, "San Benito", "AI|John Rarick"): 7,
    (1980, "Tuolumne", "AI|John Rarick"): 17,
    (1980, "TOTAL", "AI|John Rarick"): 10358,
    (1980, "Butte", "PF|Gus Hall"): 8,
    (1980, "Humboldt", "PF|Gus Hall"): 39,
    (1980, "Los Angeles", "PF|Gus Hall"): 701,
    (1984, "Amador", "AI|Gerald Willis"): 1,
    (1988, "TOTAL", "D|Michael S. Dukakis"): 1910808,
    (1988, "TOTAL", "D|Al Gore"): 56645,
}
IOWA = {1976: date(1976, 1, 19), 1980: date(1980, 1, 21), 1984: date(1984, 2, 20), 1988: date(1988, 2, 8)}
LAST = {1976: date(1976, 6, 8), 1980: date(1980, 6, 3), 1984: date(1984, 6, 12), 1988: date(1988, 6, 14)}


def url(year: int) -> str:
    item = ITEMS[year][0]
    return f"https://archive.org/download/{item}/{item}.pdf"


def fetch() -> None:
    for year in ITEMS:
        raw.fetch(RAW, url(year), suffix=".pdf")


Word = tuple[str, float, float, float, float]


def words_of(year: int, page: int) -> tuple[list[Word], float, float]:
    out = subprocess.run(
        ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(raw.cached(RAW, url(year)).path), "-"],
        check=True,
        capture_output=True,
    ).stdout.decode()
    out = re.sub(r"<!DOCTYPE[^>]*>", "", out).replace(' xmlns="http://www.w3.org/1999/xhtml"', "")
    tree = ET.fromstring(out)
    sheet = tree.find(".//page")
    assert sheet is not None
    found = [
        (
            word.text or "",
            float(word.get("xMin", 0)),
            float(word.get("yMin", 0)),
            float(word.get("xMax", 0)),
            float(word.get("yMax", 0)),
        )
        for word in tree.iter("word")
    ]
    found.sort(key=lambda w: (round(middle(w) / 3), w[1]))
    joined: list[Word] = []
    for word in found:
        last = joined[-1] if joined else None
        if (
            last is not None
            and abs(middle(last) - middle(word)) < 3
            and 0 <= word[1] - last[3] < 8
            and re.fullmatch(r"\d{1,3}", last[0])
            and re.fullmatch(r",\d{3}", word[0])
        ):
            joined[-1] = (last[0] + word[0], last[1], min(last[2], word[2]), word[3], max(last[4], word[4]))
        else:
            joined.append(word)
    return joined, float(sheet.get("width", 1)), float(sheet.get("height", 1))


NUMBER = re.compile(r"^[\dOolI,.\-']+$")
DIGITS = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1"})


def numeric(text: str) -> bool:
    return bool(NUMBER.match(text)) and any(c.isdigit() for c in text)


def county_of(label: str) -> str | None:
    text = " ".join(re.sub(r"[^A-Za-z ]", " ", label).split()).title()
    if not text:
        return None
    if re.search(r"(State|Grand)\s*Totals?", text) or text in ("Totals", "Total"):
        return "TOTAL"
    squashed = text.replace(" ", "").lower()
    names = {name.replace(" ", "").lower(): name for name in COUNTIES}
    match = difflib.get_close_matches(squashed, names, n=1, cutoff=0.8)
    if not match:
        match = [name for name in names if squashed.startswith(name) and len(name) > 3][:1]
    return names[match[0]] if match else None


def middle(word: Word) -> float:
    return (word[2] + word[4]) / 2


def anchors(words: list[Word], count: int, left: float) -> list[float]:
    edges = sorted(w[3] for w in words if numeric(w[0]) and w[3] > left)
    clusters: list[list[float]] = []
    for edge in edges:
        if clusters and edge - clusters[-1][-1] < 6:
            clusters[-1].append(edge)
        else:
            clusters.append([edge])
    big = [c for c in clusters if len(c) >= 12]
    if len(big) != count:
        raise ValueError(f"{len(big)} columns found, {count} wanted: {[(round(c[0]), len(c)) for c in big]}")
    return [sorted(c)[len(c) // 2] for c in big]


def rows_of(words: list[Word], left: float) -> dict[str, float]:
    labels = sorted((w for w in words if w[1] < left and re.search(r"[A-Za-z]{2}", w[0])), key=middle)
    lines: list[list[Word]] = []
    for word in labels:
        if lines and abs(middle(word) - middle(lines[-1][-1])) < 0.4 * (word[4] - word[2]):
            lines[-1].append(word)
        else:
            lines.append([word])
    found: dict[str, float] = {}
    for line in lines:
        county = county_of(" ".join(w[0] for w in sorted(line, key=lambda w: w[1])))
        if county is not None and county not in found:
            found[county] = sum(map(middle, line)) / len(line)
    return found


def table(year: int, page: int, columns: list[str], counties: list[str]) -> list[dict[str, object]]:
    words, width, height = words_of(year, page)
    found = rows_of(words, width * LEFT.get((year, page), 0.2))
    order = [*counties, "TOTAL"]
    known = [(i, found[name]) for i, name in enumerate(order) if name in found]
    if len(known) < 2:
        raise ValueError(f"{year} p{page}: rows not found")
    ys: dict[str, float] = {}
    for i, name in enumerate(order):
        if name in found:
            ys[name] = found[name]
            continue
        before = [k for k in known if k[0] < i][-1:] or known[:2]
        after = [k for k in known if k[0] > i][:1] or known[-2:]
        (i0, y0), (i1, y1) = before[0], after[-1]
        if i0 == i1:
            (i0, y0), (i1, y1) = known[-2], known[-1]
        ys[name] = y0 + (y1 - y0) * (i - i0) / (i1 - i0)
        print(f"{year} p{page}: {name} row placed at {ys[name]:.1f}")
    pitch = statistics.median(b - a for a, b in itertools.pairwise(sorted(ys.values())))
    rights = anchors(words, len(columns), width * LEFT.get((year, page), 0.2))
    step = min((b - a for a, b in itertools.pairwise(rights)), default=100)
    tokens: dict[int, list[Word]] = {}
    for word in words:
        if not numeric(word[0]) or word[3] < rights[0] - step * 0.6:
            continue
        column = min(range(len(rights)), key=lambda i: abs(rights[i] - word[3]))
        if abs(rights[column] - word[3]) <= step * 0.5:
            tokens.setdefault(column, []).append(word)
    if DEBUG:
        print(year, page, len(found), "labels", {c: len(t) for c, t in sorted(tokens.items())})
    lattice = sorted(ys.values())
    center = (lattice[0] + lattice[-1]) / 2

    def miss(y: float) -> float:
        return min(min(abs(y - row) for row in lattice), pitch * 0.5)

    cells: dict[tuple[str, int], list[Word]] = {}
    shifts: dict[int, tuple[float, float]] = {}
    for column, found_ in tokens.items():
        shift = min(
            ((pitch * a / 20, b / 1000) for a in range(-12, 13) for b in range(-20, 21, 4)),
            key=lambda ab: sum(miss(middle(w) - ab[0] - ab[1] * (middle(w) - center)) for w in found_),
        )
        shifts[column] = shift
        for word in found_:
            y = middle(word) - shift[0] - shift[1] * (middle(word) - center)
            name = min(ys, key=lambda n: abs(ys[n] - y))
            if abs(ys[name] - y) <= pitch * 0.5:
                cells.setdefault((name, column), []).append(word)
    widths = {
        column: max(w[3] - w[1] for (_, c), found_ in cells.items() if c == column for w in found_ if len(found_) == 1)
        for column in range(len(columns))
    }

    def at(name: str, column: int) -> float:
        a, b = shifts.get(column, (0.0, 0.0))
        return (ys[name] + a - b * center) / (1 - b)

    out: list[dict[str, object]] = []
    for name in order:
        for column, label in enumerate(columns):
            parts = sorted(cells.get((name, column), []), key=lambda w: w[1])
            text = "".join(w[0] for w in parts)
            digits = re.sub(r"[^\d]", "", text.translate(DIGITS))
            fixed = FIXES.get((year, name, label))
            box = (
                [parts[0][1] / width, min(w[2] for w in parts) / height, parts[-1][3] / width,
                 max(w[4] for w in parts) / height]
                if parts
                else None
            )  # fmt: skip
            out.append(
                {
                    "year": year,
                    "page": page,
                    "county": name,
                    "column": label,
                    "votes": (int(digits) if digits else 0) if fixed is None else fixed,
                    "text": text,
                    "fixed": fixed is not None,
                    "box": box,
                    "cell": [
                        (rights[column] - widths[column] - step * 0.2) / width,
                        (at(name, column) - pitch * 0.45) / height,
                        (rights[column] + 3) / width,
                        (at(name, column) + pitch * 0.42) / height,
                    ],
                }
            )
    return out


def image_of(year: int, page: int) -> Path:
    book = raw.cached(RAW, url(year))
    image = PAGES / f"{book.sha256}-{page}-400.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        house_books._render(book, page, 400, image, False)
    return image


def read_cell(page: Image.Image, box: tuple[int, int, int, int]) -> str:
    buffer = io.BytesIO()
    crop = page.crop(box)
    padded = Image.new("L", (crop.width + 40, crop.height + 40), 255)
    padded.paste(crop, (20, 20))
    padded.save(buffer, format="PNG")
    done = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789,"],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
    )
    return done.stdout.decode().strip()


def reread(year: int, page: int, cells: list[list[float]]) -> list[int | None]:
    image = image_of(year, page)
    cache = image.with_suffix(".cells.json")
    known: dict[str, str] = json.loads(cache.read_text()) if cache.exists() else {}
    with Image.open(image) as opened:
        sheet = opened.convert("L")
    boxes = {
        json.dumps(cell): (
            int(cell[0] * sheet.width),
            int(cell[1] * sheet.height),
            int(cell[2] * sheet.width),
            int(cell[3] * sheet.height),
        )
        for cell in cells
    }
    wanted = [key for key in boxes if key not in known]
    if wanted:
        with ThreadPoolExecutor(8) as pool:
            known.update(zip(wanted, pool.map(lambda key: read_cell(sheet, boxes[key]), wanted), strict=True))
        cache.write_text(json.dumps(known))
    out: list[int | None] = []
    for cell in cells:
        text = known[json.dumps(cell)].replace(" ", "")
        out.append(
            int(text.replace(",", "")) if re.fullmatch(r"\d{1,3}(,\d{3})*|\d{1,4}", text) else None if text else 0
        )
    return out


def reconcile(frame: pl.DataFrame) -> pl.DataFrame:
    parts = []
    for (year, column), group in frame.group_by("year", "column", maintain_order=True):
        counties = group.filter(pl.col("county") != "TOTAL")
        printed = group.filter(pl.col("county") == "TOTAL")["votes"].to_list()
        if printed == [int(counties["votes"].sum())] or len(printed) != 1:
            parts.append(group)
            continue
        group = group.with_columns(second=pl.Series([None] * len(group), dtype=pl.Int64))
        seconds: list[int | None] = []
        for (page,), block in group.group_by("page", maintain_order=True):
            seconds.extend(reread(int(year), int(page), block["cell"].to_list()))
        group = group.with_columns(second=pl.Series(seconds, dtype=pl.Int64))
        names = group["county"].to_list()
        fixed = group["fixed"].to_list()
        votes = [
            w if not t and w is not None and not f else v
            for v, w, t, f in zip(group["votes"], seconds, group["text"], fixed, strict=True)
        ]
        target = printed[0]
        alt = [v if w is None else w for v, w in zip(votes, seconds, strict=True)]
        differ = [
            i for i, (v, w) in enumerate(zip(votes, seconds, strict=True))
            if names[i] != "TOTAL" and not fixed[i] and w is not None and w != v
        ]  # fmt: skip
        current = sum(v for v, n in zip(votes, names, strict=True) if n != "TOTAL")
        chosen: tuple[int, ...] | None = None
        for size in range(1, min(len(differ), 4) + 1):
            for combo in itertools.combinations(differ, size):
                if current + sum(alt[i] - votes[i] for i in combo) == target:
                    chosen = combo
                    break
            if chosen:
                break
        if chosen is None and differ:
            whole = current + sum(alt[i] - votes[i] for i in differ)
            if whole == target:
                chosen = tuple(differ)
        for i in chosen or ():
            votes[i] = alt[i]
        group = group.with_columns(votes=pl.Series(votes, dtype=pl.Int64))
        parts.append(group)
    return pl.concat(parts, how="diagonal_relaxed")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year, pages in TABLES.items():
        for page, columns in pages:
            first, last = SPLITS.get((year, page), (COUNTIES[0], COUNTIES[-1]))
            counties = COUNTIES[COUNTIES.index(first) : COUNTIES.index(last) + 1]
            try:
                rows.extend(table(year, page, columns, counties))
            except ValueError as error:
                print(f"{year} p{page}: {error}")
    frame = reconcile(pl.DataFrame(rows))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    check(frame)


def check(frame: pl.DataFrame) -> None:
    for (year, column), group in frame.group_by("year", "column", maintain_order=True):
        counties = group.filter(pl.col("county") != "TOTAL")
        missing = set(COUNTIES) - set(counties["county"])
        twice = counties.group_by("county").len().filter(pl.col("len") > 1)
        if missing or len(twice):
            print(f"{year} {column}: missing {sorted(missing)}, twice {sorted(twice['county'])}")
        total = int(counties["votes"].sum())
        printed = group.filter(pl.col("county") == "TOTAL")["votes"].to_list()
        if printed != [total]:
            print(f"{year} {column}: printed {printed}, counted {total}")
            seconds = counties["second"] if "second" in counties.columns else [None] * len(counties)
            for (county, votes, text, page), second in zip(
                counties.select("county", "votes", "text", "page").iter_rows(), seconds, strict=True
            ):
                if (text and not re.fullmatch(r"\d{1,3}(,\d{3})*", text)) or second != votes:
                    print(f"  p{page} {county}: {votes} from {text!r}, second {second}")


CAVEATS = {(1976, "D|Morris Udall"): 50}


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet").filter(~pl.col("column").str.contains(r"\|Delegates "))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's presidential primaries by county from the Secretary of State's Statements of Vote, "
            + ", ".join(str(year) for year in sorted(ITEMS)),
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ((state_unit, state_area),) = conn.execute(
            """select u.id, u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-CA'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = dict(
            conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            ).fetchall()
        )
        PAGES.mkdir(parents=True, exist_ok=True)
        for year in sorted(ITEMS):
            item, day = ITEMS[year]
            lines = frame.filter(pl.col("year") == year)
            title = TITLE.format(day=day)
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"https://archive.org/details/{item}",
                license=LICENSE,
            )
            book = raw.cached(RAW, url(year))
            artifact = cs.artifact(book, source)
            pages: dict[int, UUID] = {}
            for (number,) in lines.select("page").unique().sort("page").iter_rows():
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    house_books._render(book, number, 100, image, False)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                pages[number] = cs.page(artifact, number)
                cs.add(
                    "source_page",
                    id=pages[number],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            election = cs.key("us.election", f"{day}:CA:primary")
            cs.add(
                "election",
                id=election,
                polity_id=cs.polity("US-CA"),
                kind="primary",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day:%B} {day.day}, {year} California primary election")
            cs.add("slug", path=f"us/ca/{day}-primary", entity_id=election)
            lines = lines.with_columns(owner=pl.col("column").str.split("|").list.first())
            for (owner,), block in lines.group_by("owner", maintain_order=True):
                label, scope = PARTIES[str(owner)]
                national = None
                if owner in ("D", "R"):
                    word = scope.removesuffix("-party")
                    start = min(IOWA[year], day)
                    umbrella = cs.key("us.election", f"{start}:{word}-presidential-primaries")
                    national = cs.key("us.contest", f"{start}:president:{word}")
                    if umbrella in cs.minted:
                        cs.add(
                            "election",
                            id=umbrella,
                            polity_id=cs.polity("US"),
                            kind="primary",
                            date_from=start,
                            date_to=LAST[year],
                            status="held",
                        )
                        name(umbrella, f"{year} {word.title()} Party presidential primaries")
                        cs.add("slug", path=f"us/{start}-{word}-presidential-primaries", entity_id=umbrella)
                    if national in cs.minted:
                        cs.add(
                            "contest",
                            id=national,
                            election_id=umbrella,
                            kind="delegate_allocation",
                            office_id=cs.key("us.office", "president"),
                            party_scope_id=cs.key("us.party", scope),
                            round_number=1,
                            default_result_kind="counted",
                        )
                        name(national, f"{year} {word.title()} presidential nomination")
                        cs.add("slug", path=f"us/{start}-{word}-presidential-primaries/president", entity_id=national)
                contest_key = f"{day}:CA:president:{slug(label)}"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="delegate_allocation",
                    office_id=cs.key("us.office", "president"),
                    district_area_id=state_area,
                    party_scope_id=cs.key("us.party", scope),
                    round_number=1,
                    allocation_parent_id=national,
                    default_result_kind="counted",
                )
                name(contest, f"{year} California {label} presidential primary")
                cs.add("slug", path=f"us/ca/{day}-primary/president-{slug(label)}", entity_id=contest)
                sets = {
                    grain: cs.key(f"{SOURCE}.result_set", f"{contest_key}:{grain}") for grain in ("state", "county")
                }
                for grain, result_set in sets.items():
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=grain == "county",
                        completeness=1,
                    )
                for (when, column), off in CAVEATS.items():
                    if when == year and column.startswith(f"{owner}|"):
                        cs.add(
                            "note",
                            entity_id=sets["county"],
                            kind="caveat",
                            body_md=f"The Statement of Vote prints {column.split('|')[1]}'s statewide total "
                            f"{off:,} votes above the sum of the county lines; the county lines are stored as printed.",
                        )
                totals: dict[str, int] = {}
                for (column,), cells in block.group_by("column", maintain_order=True):
                    display = str(column).split("|")[1]
                    kind = "uncommitted" if display.endswith("delegation") else "candidate"
                    option_key = f"{contest_key}:{kind}:{slug(display)}"
                    option = cs.key("us.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind=kind,
                        party_id=cs.key("us.party", scope) if kind == "candidate" else None,
                        ballot_label=display,
                    )
                    if kind == "candidate":
                        person = cs.key(f"{SOURCE}.person", f"CA:{year}:{slug(display)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, display)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{option_key}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=display,
                            is_independent=False,
                        )
                    for county, votes, page, box in cells.select("county", "votes", "page", "box").iter_rows():
                        grain = "state" if county == "TOTAL" else "county"
                        unit = state_unit if county == "TOTAL" else counties[county]
                        if grain == "county":
                            totals[county] = totals.get(county, 0) + votes
                        fact = {
                            "result_set_id": sets[grain],
                            "unit_id": unit,
                            "option_id": option,
                            "channel": 0,
                            "measure": 1,
                            "count_round": 0,
                        }
                        cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                        if box is not None:
                            cs.add(
                                "value_locator",
                                **fact,
                                page_id=pages[page],
                                source_id=None,
                                x0=box[0],
                                y0=box[1],
                                x1=box[2],
                                y1=box[3],
                            )
                printed = int(block.filter(pl.col("county") == "TOTAL")["votes"].sum())
                for grain, unit, value in [
                    ("state", state_unit, printed),
                    *(("county", counties[county], votes) for county, votes in totals.items()),
                ]:
                    cs.add(
                        "result_value",
                        result_set_id=sets[grain],
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=4,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
