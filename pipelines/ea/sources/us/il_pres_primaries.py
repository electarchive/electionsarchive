import csv
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

SOURCE = "us.il_pres_primaries"
VERSION = "1"
PUBLISHER = "Illinois State Board of Elections"
LICENSE = "Public domain"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
TITLE = "State of Illinois Official Vote Cast at the Primary Election, {day:%B %-d, %Y}"
ITEMS = {
    1976: ("stateofillinoiso1976illi", date(1976, 3, 16)),
    1980: ("stateofillinoiso1980illi", date(1980, 3, 18)),
    1984: ("stateofillinoiso1984illi", date(1984, 3, 20)),
    1988: ("stateofillinoiso1988illi", date(1988, 3, 15)),
}
IOWA = {1976: date(1976, 1, 19), 1980: date(1980, 1, 21), 1984: date(1984, 2, 20), 1988: date(1988, 2, 8)}
LAST = {1976: date(1976, 6, 8), 1980: date(1980, 6, 3), 1984: date(1984, 6, 12), 1988: date(1988, 6, 14)}
PARTIES = {
    "D": ("Democratic", "democratic-party"),
    "R": ("Republican", "republican-party"),
    "SOL": ("Solidarity", "IL:solidarity"),
}
R76 = ["R|Ronald Reagan", "R|Lar (America First) Daly", "R|Gerald R. Ford", "R|Write-ins"]
D76 = ["D|George C. Wallace", "D|Jimmy Carter", "D|Fred R. Harris", "D|Sargent Shriver", "D|Write-ins"]
D80 = [
    "D|Edmund G. Brown, Jr.",
    "D|Jimmy Carter",
    "D|Edward M. Kennedy",
    "D|Lyndon H. LaRouche, Jr.",
    "D|Write-ins",
    "D|John B. Anderson (write-in)",
]
R80 = [
    "R|John B. Anderson",
    "R|Howard H. Baker, Jr.",
    "R|George Bush",
    "R|John B. Connally",
    "R|Philip M. Crane",
    "R|Robert Dole",
    "R|V. A. Kelley",
    "R|Ronald Reagan",
    "R|Write-ins",
    "R|Gerald Ford (write-in)",
]
A84 = [
    "D|Reubin Askew",
    "D|Alan Cranston",
    "D|John Glenn",
    "D|Gary Hart",
    "D|Jesse L. Jackson",
    "D|George McGovern",
    "D|Walter F. Mondale",
    "D|Betty Jean Williams",
    "D|Write-ins",
    "R|Ronald Reagan",
    "R|Write-ins",
]
A88 = ["D|Paul Simon", "D|Jesse L. Jackson", "D|Michael S. Dukakis", "D|Al Gore", "D|Dick Gephardt", "D|Gary Hart"]
B88 = [
    "D|Lyndon H. LaRouche, Jr.",
    "D|Bruce Babbitt",
    "R|George Bush",
    "R|Bob Dole",
    "R|Pat Robertson",
    "R|Jack F. Kemp",
]
C88 = ["R|Pete Du Pont", "R|Alexander M. Haig, Jr.", "SOL|Lenora B. Fulani"]
TABLES: dict[int, list[tuple[int, bool, list[str], list[int]]]] = {
    1976: [
        (10, False, R76, [1179, 1410, 1640, 1871]),
        (11, False, R76, [1129, 1360, 1591, 1820]),
        (12, False, D76, [1042, 1273, 1503, 1733, 1964]),
        (13, False, D76, [879, 1112, 1344, 1576, 1808]),
    ],
    1980: [
        (14, False, D80, [795, 1028, 1258, 1489, 1720, 1952]),
        (15, False, D80, [790, 1022, 1252, 1484, 1715, 1947]),
        (16, True, R80, [974, 1205, 1437, 1669, 1900, 2132, 2364, 2596, 2827, 3059]),
        (17, True, R80, [969, 1200, 1433, 1665, 1897, 2130, 2361, 2593, 2825, 3056]),
        (18, True, R80, [970, 1200, 1433, 1664, 1896, 2128, 2358, 2590, 2822, 3053]),
    ],
    1984: [
        (14, True, A84, [859, 1073, 1289, 1503, 1717, 1932, 2148, 2363, 2577, 2791, 3007]),
        (15, True, A84, [863, 1077, 1293, 1508, 1723, 1938, 2153, 2368, 2583, 2798, 3014]),
        (16, True, A84, [888, 1102, 1318, 1532, 1747, 1961, 2176, 2392, 2606, 2820, 3033]),
        (17, True, A84, [887, 1103, 1317, 1533, 1748, 1963, 2178, 2394, 2608, 2823, 3037]),
    ],
    1988: [
        (17, False, A88, [677, 921, 1164, 1400, 1636, 1876]),
        (18, False, A88, [742, 985, 1228, 1463, 1698, 1939]),
        (19, False, B88, [671, 912, 1162, 1397, 1639, 1874]),
        (20, False, B88, [719, 961, 1211, 1446, 1686, 1921]),
        (21, False, C88, [1090, 1455, 1806]),
        (22, False, C88, [1147, 1512, 1861]),
    ],
}
FIXES: dict[tuple[int, str, str], int] = {
    (1976, "Cook", "R|Lar (America First) Daly"): 4198,
    (1976, "Williamson", "R|Lar (America First) Daly"): 8,
    (1976, "Lawrence", "R|Lar (America First) Daly"): 7,
    (1976, "Macoupin", "R|Gerald R. Ford"): 1434,
    (1976, "Iroquois", "D|Sargent Shriver"): 222,
    (1976, "Kankakee", "D|Fred R. Harris"): 251,
    (1976, "Knox", "D|Jimmy Carter"): 1934,
    (1976, "Logan", "D|Fred R. Harris"): 77,
    (1976, "Macon", "D|Jimmy Carter"): 8492,
    (1976, "McHenry", "D|Write-ins"): 77,
    (1976, "Jo Daviess", "D|Fred R. Harris"): 77,
    (1976, "Whiteside", "D|George C. Wallace"): 777,
    (1976, "Livingston", "D|Sargent Shriver"): 358,
    (1976, "Hamilton", "D|Sargent Shriver"): 84,
    (1976, "Ford", "R|Lar (America First) Daly"): 7,
    (1976, "Jasper", "R|Lar (America First) Daly"): 2,
    (1976, "McHenry", "R|Write-ins"): 7,
}
DIGITS = str.maketrans(
    {"l": "1", "I": "1", "|": "1", "]": "1", "}": "1", "O": "0", "o": "0", "$": "5", "S": "5", "§": "5"}
)
READY = {1976}
CAVEATS = {
    (1976, "D|Jimmy Carter"): 300,
    (1976, "D|Write-ins"): 239,
}
PRINTED = {
    1988: {
        "D|Paul Simon": 635219,
        "D|Jesse L. Jackson": 484233,
        "D|Michael S. Dukakis": 245289,
        "D|Al Gore": 77265,
        "D|Dick Gephardt": 35108,
        "D|Gary Hart": 12769,
        "D|Lyndon H. LaRouche, Jr.": 6094,
        "D|Bruce Babbitt": 4953,
        "R|George Bush": 469151,
        "R|Bob Dole": 309253,
        "R|Pat Robertson": 59087,
        "R|Jack F. Kemp": 12687,
        "R|Pete Du Pont": 4653,
        "R|Alexander M. Haig, Jr.": 3806,
        "SOL|Lenora B. Fulani": 170,
    }
}
COUNTIES = [
    "Adams", "Alexander", "Bond", "Boone", "Brown", "Bureau", "Calhoun", "Carroll", "Cass", "Champaign",
    "Christian", "Clark", "Clay", "Clinton", "Coles", "Cook", "Crawford", "Cumberland", "DeKalb", "De Witt",
    "Douglas", "DuPage", "Edgar", "Edwards", "Effingham", "Fayette", "Ford", "Franklin", "Fulton", "Gallatin",
    "Greene", "Grundy", "Hamilton", "Hancock", "Hardin", "Henderson", "Henry", "Iroquois", "Jackson", "Jasper",
    "Jefferson", "Jersey", "Jo Daviess", "Johnson", "Kane", "Kankakee", "Kendall", "Knox", "Lake", "LaSalle",
    "Lawrence", "Lee", "Livingston", "Logan", "Macon", "Macoupin", "Madison", "Marion", "Marshall", "Mason",
    "Massac", "McDonough", "McHenry", "McLean", "Menard", "Mercer", "Monroe", "Montgomery", "Morgan", "Moultrie",
    "Ogle", "Peoria", "Perry", "Piatt", "Pike", "Pope", "Pulaski", "Putnam", "Randolph", "Richland",
    "Rock Island", "Saline", "Sangamon", "Schuyler", "Scott", "Shelby", "Stark", "St. Clair", "Stephenson",
    "Tazewell", "Union", "Vermilion", "Wabash", "Warren", "Washington", "Wayne", "White", "Whiteside", "Will",
    "Williamson", "Winnebago", "Woodford",
]  # fmt: skip

Word = tuple[str, int, int, int, int]


def url(year: int) -> str:
    return f"https://archive.org/download/{ITEMS[year][0]}/{ITEMS[year][0]}.pdf"


def fetch() -> None:
    for year in ITEMS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def image_of(year: int, page: int, turned: bool, dpi: int = 400) -> Path:
    book = raw.cached(SOURCE, url(year))
    image = PARSED / f"{book.sha256}-{page}{'' if dpi == 400 else f'-{dpi}'}.png"
    if not image.exists():
        PARSED.mkdir(parents=True, exist_ok=True)
        house_books._render(book, page, dpi, image, turned)
    return image


def tesseract_words(image: Path, scale: float) -> list[Word]:
    tsv = image.with_suffix(".tsv")
    if not tsv.exists():
        subprocess.run(["tesseract", str(image), str(image.with_suffix("")), "--psm", "6", "tsv"], check=True)
    words: list[Word] = []
    with tsv.open() as handle:
        for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            left, top, width, height = (int(row[f]) * scale for f in ("left", "top", "width", "height"))
            if row["level"] == "5" and row["text"].strip():
                words.append((row["text"], int(left), int(top), int(left + width), int(top + height)))
    return words


def layer_words(year: int, page: int) -> list[Word]:
    book = raw.cached(SOURCE, url(year))
    cache = PARSED / f"{book.sha256}-{page}.xml"
    if not cache.exists():
        subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(book.path), str(cache)], check=True)
    scale = 400 / 72
    return [
        (
            word.text or "",
            int(float(word.attrib["xMin"]) * scale),
            int(float(word.attrib["yMin"]) * scale),
            int(float(word.attrib["xMax"]) * scale),
            int(float(word.attrib["yMax"]) * scale),
        )
        for word in ET.parse(cache).getroot().iter("{http://www.w3.org/1999/xhtml}word")
    ]


def lines_of(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w[2] + w[4]) / 2):
        middle = (word[2] + word[4]) / 2
        if lines and abs(statistics.mean((w[2] + w[4]) / 2 for w in lines[-1]) - middle) < 12:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w[1]) for line in lines]


def read_cell(page: Image.Image, box: tuple[int, int, int, int]) -> str:
    buffer = io.BytesIO()
    page.crop(box).save(buffer, format="PNG")
    done = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789,"],
        input=buffer.getvalue(),
        capture_output=True,
        check=True,
    )
    return done.stdout.decode().strip()


def reread(image: Path, boxes: list[tuple[int, int, int, int]]) -> dict[tuple[int, int, int, int], str]:
    cache = image.with_suffix(".cells.json")
    known = {(a, b, c, d): text for a, b, c, d, text in json.loads(cache.read_text())} if cache.exists() else {}
    wanted = [box for box in boxes if box not in known]
    if wanted:
        with Image.open(image) as opened:
            page = opened.convert("L")
        with ThreadPoolExecutor(8) as pool:
            known.update(zip(wanted, pool.map(lambda box: read_cell(page, box), wanted), strict=True))
        cache.write_text(json.dumps([[*box, text] for box, text in known.items()]))
    return {box: known[box] for box in boxes}


def value(text: str) -> int | None:
    text = text.translate(DIGITS).strip("()~'\"-.,")
    text = re.sub(r"(?<=\d)[.,](?=\d{3}$)", ",", text)
    text = re.sub(r",(?=\d{1,2}$)|[)\]]$", "", text)
    if re.fullmatch(r"\d{1,3}(,\d{3})+|\d{1,7}", text):
        return int(text.replace(",", ""))
    return None


def cell_text(words: list[str]) -> str:
    return "".join(w for w in words if re.search(r"\d", w) and not re.search(r"\.\.|[a-z]{2}", w))


def county_of(label: str) -> int | None:
    if len(label) < 4 or label.startswith(("total", "downstate", "cookcounty")):
        return None
    scores = sorted(
        (difflib.SequenceMatcher(None, key(county), label[: len(key(county))]).ratio(), len(county), i)
        for i, county in enumerate(COUNTIES)
    )
    score, _, index = scores[-1]
    return index if score >= 0.8 else None


def place(found: list[tuple[str, float]], step: float) -> dict[int, int]:
    ys = sorted(y for _, y in found)
    gaps = sorted(b - a for a, b in itertools.pairwise(ys) if 1.4 * step < b - a < 3 * step)
    extra = (gaps[len(gaps) // 2] - step) / step if gaps else 0.0

    def x(i: int) -> float:
        return i + extra * (i // 5)

    anchors = sorted(
        ((index, y) for label, y in found if (index := county_of(label)) is not None), key=lambda pair: pair[1]
    )
    good = [
        (i, y)
        for n, (i, y) in enumerate(anchors)
        if sum(
            1
            for j, z in anchors[max(0, n - 3) : n + 4]
            if j != i and abs((z - y) - step * (x(j) - x(i))) < step * 0.35 * max(1, abs(j - i) / 8)
        )
        >= 2
    ]
    if not good:
        return {}
    placed: dict[int, int] = {}
    order = sorted(range(len(found)), key=lambda n: found[n][1])
    for low, high in itertools.pairwise(good):
        between = [n for n in order if low[1] < found[n][1] < high[1]]
        if len(between) == high[0] - low[0] - 1:
            placed.update({n: low[0] + 1 + k for k, n in enumerate(between)})
    for n, (_, y) in enumerate(found):
        if n in placed or y < good[0][1] - 4 * step:
            continue
        below = [pair for pair in good if pair[1] <= y]
        above = [pair for pair in good if pair[1] > y]
        low = below[-1] if below else above[0]
        high = above[0] if above and below else None
        scale = (high[1] - low[1]) / (x(high[0]) - x(low[0])) if high and x(high[0]) > x(low[0]) else step
        i, miss = min(
            ((i, abs(low[1] + scale * (x(i) - x(low[0])) - y)) for i in range(len(COUNTIES) + 3)),
            key=lambda pair: pair[1],
        )
        if miss < scale * 0.4:
            placed[n] = i
    return placed


Row = dict[int, tuple[str, tuple[int, int, int, int]]]


def rows_of(words: list[Word], rights: list[int], width: float) -> tuple[dict[str, tuple[float, Row]], float]:
    start = rights[0] - width
    found: list[tuple[str, float]] = []
    reads: list[Row] = []
    for line in lines_of(words):
        cells: dict[int, list[Word]] = {}
        for word in line:
            if word[1] < start - 10:
                continue
            column = min(range(len(rights)), key=lambda i: abs(rights[i] - word[3]))
            if word[3] - rights[column] > 40 or word[3] < rights[column] - width * 0.75:
                continue
            cells.setdefault(column, []).append(word)
        row = {
            column: (
                cell_text([w[0] for w in got]),
                (min(w[1] for w in got), min(w[2] for w in got), max(w[3] for w in got), max(w[4] for w in got)),
            )
            for column, got in cells.items()
        }
        if not any(text for text, _ in row.values()):
            continue
        found.append((key(" ".join(w[0] for w in line if w[3] < start + 20)), (line[0][2] + line[0][4]) / 2))
        reads.append(row)
    ys = sorted(y for _, y in found)
    gaps = sorted(b - a for a, b in itertools.pairwise(ys) if b - a > 12)
    step = gaps[len(gaps) // 4] if gaps else 33.0
    placed = place(found, step)
    ends = [found[n][1] for n, i in placed.items() if i == len(COUNTIES) - 1]
    tail = sorted(
        (n for n in range(len(found)) if ends and found[n][1] > ends[0] + 10 and placed.get(n, 999) >= len(COUNTIES)),
        key=lambda n: found[n][1],
    )
    out: dict[str, tuple[float, Row]] = {}
    for order, n in enumerate(tail):
        label = found[n][0]
        if "total" in label or label.startswith("tot"):
            name = "TOTAL"
        elif label.startswith("down"):
            name = "Downstate"
        elif label.startswith("cook"):
            name = "Cook County"
        else:
            name = ["TOTAL", "Downstate", "Cook County"][min(order, 2)]
        out.setdefault(name, (found[n][1], reads[n]))
    for n, i in placed.items():
        if i < len(COUNTIES):
            out.setdefault(COUNTIES[i], (found[n][1], reads[n]))
    return out, step


def table(year: int, page: int, turned: bool, columns: list[str], rights: list[int]) -> list[dict[str, object]]:
    image = image_of(year, page, turned)
    with Image.open(image) as opened:
        size = opened.size
    width = min(b - a for a, b in zip([rights[0] - 300, *rights], rights, strict=False))
    first, step = rows_of(tesseract_words(image, 1), rights, width)
    other = tesseract_words(image_of(year, page, turned, 300), 4 / 3) if turned else layer_words(year, page)
    second, _ = rows_of(other, rights, width)
    at = sorted((COUNTIES.index(c), y) for c, (y, _) in {**second, **first}.items() if c in COUNTIES)
    if at:
        for i in range(at[0][0] + 1, at[-1][0]):
            if COUNTIES[i] not in first and COUNTIES[i] not in second:
                low = max(pair for pair in at if pair[0] < i)
                high = min(pair for pair in at if pair[0] > i)
                first[COUNTIES[i]] = (low[1] + (high[1] - low[1]) * (i - low[0]) / (high[0] - low[0]), {})
    names = [*dict.fromkeys([*first, *second])]
    boxes = {
        (name, column): (int(right - width * 0.85), int(y - step * 0.5), int(right + 25), int(y + step * 0.5))
        for name in names
        for y in [(first.get(name) or second[name])[0]]
        for column, right in enumerate(rights)
    }
    pairs = {
        (name, column): [
            (value(text) if text else 0, text, box)
            for found in (first, second)
            if name in found
            for text, box in [found[name][1].get(column, ("", boxes[(name, column)]))]
        ]
        for name in names
        for column in range(len(columns))
    }
    third = reread(
        image,
        [boxes[spot] for spot, reads in pairs.items() if len(reads) < 2 or reads[0][0] != reads[1][0]],
    )
    out: list[dict[str, object]] = []
    for name in names:
        for column, label in enumerate(columns):
            reads = pairs[(name, column)]
            box = boxes[(name, column)]
            if len(reads) == 2 and reads[0][0] == reads[1][0]:
                votes, text, box = reads[0]
                also = votes
            else:
                text = third[box]
                reads.append((value(text) if text else 0, text, box))
                counts = [r[0] for r in reads]
                agreed = [r for r in reads if r[0] is not None and counts.count(r[0]) >= 2]
                nonzero = [r for r in reads[:2] if r[0]]
                votes, text, box = agreed[0] if agreed else nonzero[0] if len(nonzero) == 1 else reads[0]
                also = None if agreed else reads[1][0] if len(reads) > 1 else None
            fixed = FIXES.get((year, name, label))
            out.append(
                {
                    "year": year,
                    "page": page,
                    "turned": turned,
                    "county": name,
                    "column": label,
                    "votes": votes if fixed is None else fixed,
                    "text": text,
                    "second": also,
                    "fixed": fixed is not None,
                    "box": [box[0] / size[0], box[1] / size[1], box[2] / size[0], box[3] / size[1]],
                    "cell": list(boxes[(name, column)]),
                }
            )
    return out


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year, pages in TABLES.items():
        for page, turned, columns, rights in pages:
            rows.extend(table(year, page, turned, columns, rights))
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "lines.parquet")
    check(frame)


def check(frame: pl.DataFrame) -> None:
    for row in frame.filter(pl.col("votes").is_null()).iter_rows(named=True):
        print(f"unread {row['year']} p{row['page']} {row['county']} {row['column']}: {row['text']!r}")
    for (year, column), group in frame.group_by("year", "column", maintain_order=True):
        counties = group.filter(pl.col("county").is_in(COUNTIES))
        missing = set(COUNTIES) - set(counties["county"])
        twice = counties.group_by("county").len().filter(pl.col("len") > 1)
        if missing or len(twice):
            print(f"{year} {column}: missing {sorted(missing)}, twice {sorted(twice['county'])}")
        total = int(counties["votes"].sum())
        cook = int(counties.filter(pl.col("county") == "Cook")["votes"].sum())
        wanted = {"TOTAL": total, "Downstate": total - cook, "Cook County": cook}
        bad = False
        for special, votes in group.filter(~pl.col("county").is_in(COUNTIES)).select("county", "votes").iter_rows():
            if votes != wanted[special]:
                bad = True
                print(f"{year} {column} {special}: printed {votes}, counted {wanted[special]}")
        if year in PRINTED and PRINTED[year][column] != total:
            bad = True
            print(f"{year} {column} summary: printed {PRINTED[year][column]}, counted {total}")
        if bad:
            for county, votes, second, page in (
                counties.filter(pl.col("second").is_null() & ~pl.col("fixed"))
                .select("county", "votes", "second", "page")
                .iter_rows()
            ):
                print(f"  {year} p{page} {county} {column}: {votes}")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet").filter(pl.col("year").is_in(READY))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's presidential primaries by county from the State Board of Elections' official vote, "
            + ", ".join(str(year) for year in sorted(READY)),
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ((state_unit, state_area),) = conn.execute(
            """select u.id, u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-IL'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = dict(
            conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            ).fetchall()
        )
        PAGES.mkdir(parents=True, exist_ok=True)
        for year in sorted(READY):
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
            book = raw.cached(SOURCE, url(year))
            artifact = cs.artifact(book, source)
            pages: dict[int, UUID] = {}
            for number, turned in lines.select("page", "turned").unique().sort("page").iter_rows():
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    house_books._render(book, number, 100, image, turned)
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
            election_key = f"{day}:IL:primary"
            election = cs.key("us.election", election_key)
            cs.add(
                "election",
                id=election,
                polity_id=cs.polity("US-IL"),
                kind="primary",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day:%B} {day.day}, {year} Illinois presidential primary election")
            cs.add("slug", path=f"us/il/{day}-primary", entity_id=election)
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
                contest_key = f"{day}:IL:president:{slug(label)}"
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
                name(contest, f"{year} Illinois {label} presidential primary")
                cs.add("slug", path=f"us/il/{day}-primary/president-{slug(label)}", entity_id=contest)
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
                notes = [
                    f"The book prints {PARTIES[column.split('|')[0]][0]} {column.split('|')[1]} at "
                    f"{off:,} more votes statewide and downstate than the sum of its county lines; "
                    "the county lines are stored as printed."
                    for (when, column), off in CAVEATS.items()
                    if when == year and column.startswith(f"{owner}|")
                ]
                for body in notes:
                    cs.add("note", entity_id=sets["county"], kind="caveat", body_md=body)
                totals: dict[str, int] = {}
                for (column,), cells in block.group_by("column", maintain_order=True):
                    display = str(column).split("|")[1]
                    kind = "write_in" if display == "Write-ins" or display.endswith("(write-in)") else "candidate"
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
                        person = cs.key(f"{SOURCE}.person", f"IL:{year}:{slug(display)}")
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
                        if county in ("Downstate", "Cook County"):
                            continue
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
