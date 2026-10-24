import difflib
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.sc_usrep"
BOOK_SOURCE = "us.sc_report"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "South Carolina State Election Commission"
LANDING = "https://scvotes.gov/election-results/"
URL = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_{}.pdf"
DPI = 200
BOOKS = {1974: "1974-1975", 1976: "1976-1977", 1990: "1990-1991", 1992: "1992-1993", 1994: "1994-1995", 1996: "1995-1996", 1998: "1997-1998"}
DAYS = {1974: date(1974, 11, 5), 1976: date(1976, 11, 2), 1990: date(1990, 11, 6), 1992: date(1992, 11, 3), 1994: date(1994, 11, 8), 1996: date(1996, 11, 5), 1998: date(1998, 11, 3)}
PAGE_RANGES = {1974: (45, 47), 1976: (33, 35), 1990: (95, 95), 1992: (82, 84), 1994: (33, 34), 1996: (44, 45), 1998: (46, 47)}
PAGE_ORDER = {1976: (34, 33, 35)}
HALVES = {1974: 322.0, 1976: 322.0, 1990: 306.0}
FIXES = str.maketrans({"&": "6", "l": "1", "I": "1", "O": "0", "o": "0", "S": "5", "B": "8", "Z": "2", "|": "1"})
COUNTIES = [
    "Abbeville", "Aiken", "Allendale", "Anderson", "Bamberg", "Barnwell", "Beaufort", "Berkeley", "Calhoun",
    "Charleston", "Cherokee", "Chester", "Chesterfield", "Clarendon", "Colleton", "Darlington", "Dillon",
    "Dorchester", "Edgefield", "Fairfield", "Florence", "Georgetown", "Greenville", "Greenwood", "Hampton", "Horry",
    "Jasper", "Kershaw", "Lancaster", "Laurens", "Lee", "Lexington", "McCormick", "Marion", "Marlboro", "Newberry",
    "Oconee", "Orangeburg", "Pickens", "Richland", "Saluda", "Spartanburg", "Sumter", "Union", "Williamsburg", "York",
]
KEYS = {re.sub(r"[^a-z]", "", name.lower()): name for name in COUNTIES}
ALIASES: dict[tuple[int, int, str], str] = {(1998, 6, "ester"): "Dorchester", (1998, 6, "nce"): "Florence", (1998, 6, "l"): "Lee"}
OVERRIDES: dict[tuple[int, int, str, int], int] = {
    (1976, 1, "Beaufort", 0): 8633,
    (1976, 1, "Berkeley", 0): 13192,
    (1976, 1, "Charleston", 0): 41695,
    (1976, 1, "Colleton", 0): 6994,
    (1976, 1, "Dorchester", 0): 10620,
    (1976, 1, "Hampton", 0): 4992,
    (1976, 1, "Jasper", 0): 3765,
    (1976, 1, "Beaufort", 1): 3414,
    (1976, 1, "Berkeley", 1): 3603,
    (1976, 1, "Charleston", 1): 26741,
    (1976, 1, "Colleton", 1): 1509,
    (1976, 1, "Dorchester", 1): 4145,
    (1976, 1, "Hampton", 1): 768,
    (1976, 1, "Jasper", 1): 418,
    (1976, 2, "Allendale", 0): 2072,
    (1976, 2, "Bamberg", 0): 2538,
    (1976, 2, "Barnwell", 0): 2980,
    (1976, 2, "Calhoun", 0): 1764,
    (1976, 2, "Lexington", 0): 9125,
    (1976, 2, "Orangeburg", 0): 11590,
    (1976, 2, "Richland", 0): 30533,
    (1976, 2, "Allendale", 1): 1528,
    (1976, 2, "Bamberg", 1): 2602,
    (1976, 2, "Barnwell", 1): 3570,
    (1976, 2, "Calhoun", 1): 1653,
    (1976, 2, "Lexington", 1): 27509,
    (1976, 2, "Orangeburg", 1): 10353,
    (1976, 2, "Richland", 1): 36211,
    (1976, 2, "Allendale", 2): 45,
    (1976, 2, "Bamberg", 2): 51,
    (1976, 2, "Barnwell", 2): 0,
    (1976, 2, "Calhoun", 2): 25,
    (1976, 2, "Lexington", 2): 479,
    (1976, 2, "Orangeburg", 2): 97,
    (1976, 2, "Richland", 2): 253,
    (1976, 3, "Abbeville", 0): 6227,
    (1976, 3, "Aiken", 0): 23384,
    (1976, 3, "Anderson", 0): 25349,
    (1976, 3, "Edgefield", 0): 4940,
    (1976, 3, "Greenwood", 0): 15221,
    (1976, 3, "McCormick", 0): 2395,
    (1976, 3, "Newberry", 0): 9863,
    (1976, 3, "Oconee", 0): 11714,
    (1976, 3, "Pickens", 0): 13957,
    (1976, 3, "Saluda", 0): 4690,
    (1976, 4, "Greenville", 0): 56144,
    (1976, 4, "Spartanburg", 0): 35567,
    (1976, 4, "Greenville", 1): 20188,
    (1976, 4, "Spartanburg", 1): 12961,
    (1976, 5, "Cherokee", 0): 7194,
    (1976, 5, "Chester", 0): 4222,
    (1976, 5, "Chesterfield", 0): 6644,
    (1976, 5, "Fairfield", 0): 4001,
    (1976, 5, "Kershaw", 0): 5436,
    (1976, 5, "Lancaster", 0): 6487,
    (1976, 5, "Laurens", 0): 6453,
    (1976, 5, "Sumter", 0): 8696,
    (1976, 5, "Union", 0): 5323,
    (1976, 5, "York", 0): 11617,
    (1976, 5, "Cherokee", 1): 4509,
    (1976, 5, "Chester", 1): 4022,
    (1976, 5, "Chesterfield", 1): 3582,
    (1976, 5, "Fairfield", 1): 2037,
    (1976, 5, "Kershaw", 1): 6997,
    (1976, 5, "Lancaster", 1): 6911,
    (1976, 5, "Laurens", 1): 6348,
    (1976, 5, "Sumter", 1): 10884,
    (1976, 5, "Union", 1): 4528,
    (1976, 5, "York", 1): 12248,
    (1976, 5, "Cherokee", 2): 0,
    (1976, 5, "Chester", 2): 18,
    (1976, 5, "Chesterfield", 2): 15,
    (1976, 5, "Fairfield", 2): 13,
    (1976, 5, "Kershaw", 2): 42,
    (1976, 5, "Lancaster", 2): 38,
    (1976, 5, "Laurens", 2): 33,
    (1976, 5, "Sumter", 2): 52,
    (1976, 5, "Union", 2): 38,
    (1976, 5, "York", 2): 49,
    (1976, 6, "Clarendon", 0): 5001,
    (1976, 6, "Darlington", 0): 10139,
    (1976, 6, "Dillon", 0): 4584,
    (1976, 6, "Florence", 0): 13442,
    (1976, 6, "Georgetown", 0): 6909,
    (1976, 6, "Horry", 0): 13888,
    (1976, 6, "Lee", 0): 3658,
    (1976, 6, "Marion", 0): 5708,
    (1976, 6, "Marlboro", 0): 4951,
    (1976, 6, "Williamsburg", 0): 7636,
    (1976, 6, "Clarendon", 1): 3628,
    (1976, 6, "Darlington", 1): 7562,
    (1976, 6, "Dillon", 1): 3076,
    (1976, 6, "Florence", 1): 15659,
    (1976, 6, "Georgetown", 1): 4607,
    (1976, 6, "Horry", 1): 10993,
    (1976, 6, "Lee", 1): 2616,
    (1976, 6, "Marion", 1): 3267,
    (1976, 6, "Marlboro", 1): 2431,
    (1976, 6, "Williamsburg", 1): 6449,
    (1976, 6, "Clarendon", 2): 25,
    (1976, 6, "Darlington", 2): 21,
    (1976, 6, "Dillon", 2): 31,
    (1976, 6, "Florence", 2): 216,
    (1976, 6, "Georgetown", 2): 61,
    (1976, 6, "Horry", 2): 238,
    (1976, 6, "Lee", 2): 0,
    (1976, 6, "Marion", 2): 35,
    (1976, 6, "Marlboro", 2): 37,
    (1976, 6, "Williamsburg", 2): 24,
    (1974, 1, "Beaufort", 0): 5342,
    (1974, 1, "Berkeley", 0): 8062,
    (1974, 1, "Charleston", 0): 34188,
    (1974, 1, "Colleton", 0): 4278,
    (1974, 1, "Dorchester", 0): 6390,
    (1974, 1, "Hampton", 0): 2969,
    (1974, 1, "Jasper", 0): 1882,
    (1974, 1, "Beaufort", 1): 1967,
    (1974, 1, "Berkeley", 1): 2423,
    (1974, 1, "Charleston", 1): 13821,
    (1974, 1, "Colleton", 1): 1248,
    (1974, 1, "Dorchester", 1): 2288,
    (1974, 1, "Hampton", 1): 450,
    (1974, 1, "Jasper", 1): 253,
    (1974, 1, "Beaufort", 2): 82,
    (1974, 1, "Berkeley", 2): 53,
    (1974, 1, "Charleston", 2): 413,
    (1974, 1, "Colleton", 2): 30,
    (1974, 1, "Dorchester", 2): 74,
    (1974, 1, "Hampton", 2): 14,
    (1974, 1, "Jasper", 2): 21,
    (1974, 1, "Beaufort", 3): 60,
    (1974, 1, "Berkeley", 3): 20,
    (1974, 1, "Charleston", 3): 309,
    (1974, 1, "Colleton", 3): 76,
    (1974, 1, "Dorchester", 3): 34,
    (1974, 1, "Hampton", 3): 21,
    (1974, 1, "Jasper", 3): 10,
    (1974, 2, "Allendale", 0): 1667,
    (1974, 2, "Bamberg", 0): 1848,
    (1974, 2, "Barnwell", 0): 2206,
    (1974, 2, "Calhoun", 0): 1204,
    (1974, 2, "Lexington", 0): 6401,
    (1974, 2, "Orangeburg", 0): 7939,
    (1974, 2, "Richland", 0): 23940,
    (1974, 2, "Allendale", 1): 1037,
    (1974, 2, "Bamberg", 1): 1977,
    (1974, 2, "Barnwell", 1): 2085,
    (1974, 2, "Calhoun", 1): 1375,
    (1974, 2, "Lexington", 1): 18582,
    (1974, 2, "Orangeburg", 1): 7871,
    (1974, 2, "Richland", 1): 26009,
    (1974, 2, "Allendale", 2): 15,
    (1974, 2, "Bamberg", 2): 51,
    (1974, 2, "Barnwell", 2): 23,
    (1974, 2, "Calhoun", 2): 11,
    (1974, 2, "Lexington", 2): 337,
    (1974, 2, "Orangeburg", 2): 52,
    (1974, 2, "Richland", 2): 461,
    (1974, 5, "Cherokee", 0): 4523,
    (1974, 5, "Chester", 0): 3706,
    (1974, 5, "Chesterfield", 0): 3834,
    (1974, 5, "Fairfield", 0): 2811,
    (1974, 5, "Kershaw", 0): 4811,
    (1974, 5, "Lancaster", 0): 4886,
    (1974, 5, "Laurens", 0): 4658,
    (1974, 5, "Sumter", 0): 5825,
    (1974, 5, "Union", 0): 4395,
    (1974, 5, "York", 0): 8168,
    (1974, 5, "Cherokee", 1): 1900,
    (1974, 5, "Chester", 1): 1766,
    (1974, 5, "Chesterfield", 1): 1397,
    (1974, 5, "Fairfield", 1): 1126,
    (1974, 5, "Kershaw", 1): 4079,
    (1974, 5, "Lancaster", 1): 4338,
    (1974, 5, "Laurens", 1): 2797,
    (1974, 5, "Sumter", 1): 4740,
    (1974, 5, "Union", 1): 2092,
    (1974, 5, "York", 1): 5059,
    (1974, 5, "Cherokee", 2): 41,
    (1974, 5, "Chester", 2): 33,
    (1974, 5, "Chesterfield", 2): 52,
    (1974, 5, "Fairfield", 2): 18,
    (1974, 5, "Kershaw", 2): 88,
    (1974, 5, "Lancaster", 2): 57,
    (1974, 5, "Laurens", 2): 87,
    (1974, 5, "Sumter", 2): 64,
    (1974, 5, "Union", 2): 69,
    (1974, 5, "York", 2): 128,
    (1974, 6, "Clarendon", 0): 3271,
    (1974, 6, "Darlington", 0): 5408,
    (1974, 6, "Dillon", 0): 2364,
    (1974, 6, "Florence", 0): 7874,
    (1974, 6, "Georgetown", 0): 4647,
    (1974, 6, "Horry", 0): 8027,
    (1974, 6, "Lee", 0): 2722,
    (1974, 6, "Marion", 0): 2817,
    (1974, 6, "Marlboro", 0): 2647,
    (1974, 6, "Williamsburg", 0): 5619,
    (1974, 6, "Clarendon", 1): 2170,
    (1974, 6, "Darlington", 1): 5052,
    (1974, 6, "Dillon", 1): 2266,
    (1974, 6, "Florence", 1): 11605,
    (1974, 6, "Georgetown", 1): 2679,
    (1974, 6, "Horry", 1): 8009,
    (1974, 6, "Lee", 1): 2175,
    (1974, 6, "Marion", 1): 2499,
    (1974, 6, "Marlboro", 1): 1823,
    (1974, 6, "Williamsburg", 1): 3704,
    (1990, 2, "Calhoun", 0): 2162,
    (1990, 2, "Lexington", 0): 34572,
    (1990, 2, "Orangeburg", 0): 14337,
    (1990, 2, "Richland", 0): 36767,
    (1990, 2, "Orangeburg", 1): 1333,
    (1990, 2, "Lexington", 2): 62,
    (1990, 2, "Richland", 2): 13,
    (1990, 4, "Greenville", 0): 45406,
    (1990, 4, "Spartanburg", 0): 30516,
    (1990, 4, "Union", 0): 6005,
    (1990, 4, "Greenville", 1): 35065,
    (1990, 4, "Union", 1): 2103,
    (1990, 4, "Greenville", 2): 253,
    (1992, 2, "Lexington", 0): 53541,
    (1992, 3, "Abbeville", 1): 5729,
    (1992, 4, "Greenville", 2): 46775,
    (1992, 5, "Kershaw", 2): 172,
    (1992, 6, "Florence", 0): 23468,
    (1994, 5, "Darlington", 0): 6394,
    (1996, 5, "Chesterfield", 2): 52,
    (1998, 6, "Bamberg", 2): 1029,
    (1998, 6, "Williamsburg", 2): 2679,
}
LAYOUT: dict[tuple[int, int], tuple[list[float], list[int]]] = {
    (1976, 1): ([175.0, 216.0], [89891, 40598]),
    (1976, 2): ([499.0, 540.0, 580.0], [60602, 83426, 950]),
    (1976, 3): ([216.0], [117740]),
    (1976, 4): ([539.0, 579.0], [91711, 33149]),
    (1976, 5): ([171.0, 212.0, 252.0], [66073, 62066, 298]),
    (1976, 6): ([495.0, 535.0, 576.0], [75916, 60288, 688]),
    (1990, 2): ([396.0, 428.0, 456.0], [90054, 11101, 189]),
    (1990, 4): ([395.0, 425.0, 456.0], [81927, 51338, 270]),
}
ORDERED: dict[tuple[int, int], list[str]] = {
    (1974, 6): ["Clarendon", "Darlington", "Dillon", "Florence", "Georgetown", "Horry", "Lee", "Marion", "Marlboro", "Williamsburg"],
    (1976, 1): ['Beaufort', 'Berkeley', 'Charleston', 'Colleton', 'Dorchester', 'Hampton', 'Jasper'],
    (1976, 2): ['Allendale', 'Bamberg', 'Barnwell', 'Calhoun', 'Lexington', 'Orangeburg', 'Richland'],
    (1976, 3): ['Abbeville', 'Aiken', 'Anderson', 'Edgefield', 'Greenwood', 'McCormick', 'Newberry', 'Oconee', 'Pickens', 'Saluda'],
    (1976, 4): ['Greenville', 'Spartanburg'],
    (1976, 5): ['Cherokee', 'Chester', 'Chesterfield', 'Fairfield', 'Kershaw', 'Lancaster', 'Laurens', 'Sumter', 'Union', 'York'],
    (1976, 6): ['Clarendon', 'Darlington', 'Dillon', 'Florence', 'Georgetown', 'Horry', 'Lee', 'Marion', 'Marlboro', 'Williamsburg'],
}
ROWNAMES: dict[tuple[int, int], list[str]] = {
    (1990, 2): ["Bamberg", "Calhoun", "Lexington", "Orangeburg", "Richland"],
    (1990, 4): ["Greenville", "Spartanburg", "Union"],
}
PRINTED: dict[tuple[int, int], list[int]] = {
    (1974, 1): [60142, 22000, 673, 509],
    (1974, 2): [45205, 58936, 950],
    (1974, 5): [47617, 29294, 637],
    (1974, 6): [45396, 41982],
}
OMITTED: dict[tuple[int, int], str] = {(1974, 1): "Hampton"}
EXTRA: dict[tuple[int, int], dict[int, tuple[str, str]]] = {(1998, 4): {1: ("Peter J. Ashy (Patriot)", "PT"), 2: ("Peter J. Ashy (Reform)", "RF")}}
SUMMARY = "South Carolina's U.S. House general elections of 1990 and 1992 through 1998 by county from the State Election Commission's election reports"
METHOD = (
    "County totals are read from the State Election Commission's printed election reports (scans with an OCR text layer). "
    "Each candidate's county sum equals the state total printed under the table, and each cell carries its page and box."
)


def book_path(year: int) -> tuple[Path, str]:
    book = raw.cached(BOOK_SOURCE, URL.format(BOOKS[year]))
    return book.path, book.sha256


def render(year: int, page: int) -> Path:
    path, sha = book_path(year)
    image = PAGES / f"{sha}-{page}.png"
    if not image.exists():
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(path), str(image.with_suffix(""))],
            check=True,
        )
    return image


def lines_of(year: int, page: int, half: int | None = None) -> list[list[tuple[float, float, float, float, str]]]:
    path, _ = book_path(year)
    xml = subprocess.run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(path), "-"], capture_output=True, text=True, check=True).stdout
    namespace = {"h": "http://www.w3.org/1999/xhtml"}
    words = [
        (float(w.get("xMin")), float(w.get("yMin")), float(w.get("xMax")), float(w.get("yMax")), w.text or "")
        for w in ElementTree.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", xml)).findall(".//h:word", namespace)
    ]
    if half is not None:
        words = [w for w in words if (w[0] >= HALVES[year]) == (half == 1)]
    words.sort(key=lambda w: (w[1] + w[3]) / 2)
    lines: list[list[tuple[float, float, float, float, str]]] = []
    for word in words:
        if lines and abs((word[1] + word[3]) / 2 - sum((w[1] + w[3]) / 2 for w in lines[-1]) / len(lines[-1])) <= 3.5:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line) for line in lines]


def parse() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    problems: list[str] = []
    unresolved: set[tuple[int, int]] = set()
    for year, (first, last) in PAGE_RANGES.items():
        last_district = 0
        done = [0, 0]
        block: list[tuple[int, list[tuple[float, float, float, float, str]]]] = []
        for page, half in [(page, half) for page in PAGE_ORDER.get(year, range(first, last + 1)) for half in ((0, 1) if year in HALVES else (None,))]:
            render(year, page)
            if half is not None:
                block = []
            for line in lines_of(year, page, half):
                text = " ".join(w[4] for w in line)
                is_total = re.match(r"^\W*(?:[A-Za-z]+\s+){0,2}TOTALS?\b", text, re.IGNORECASE) is not None and re.search(r"\d", text) is not None
                if not is_total:
                    block.append((page, line))
                    continue
                numbers = [w for w in line if re.search(r"\d", w[4])]
                edges = [w[2] for w in numbers]
                printed = [int(re.sub(r"\D", "", w[4])) for w in numbers]
                head = " ".join(" ".join(w[4] for w in item[1]) for item in block)
                if half is not None and (year, 2 * done[half] + half + 1) in LAYOUT:
                    edges, printed = LAYOUT[(year, 2 * done[half] + half + 1)]
                match = re.search(r"DISTRICT\s*0*(\d)", head, re.IGNORECASE)
                district = int(match[1]) if match is not None else last_district + 1
                if half is not None:
                    district = 2 * done[half] + half + 1
                    done[half] += 1
                last_district = district
                printed = PRINTED.get((year, district), printed)
                cells: list[tuple[str, list[int | None], list[list[float]]]] = []
                scored: list[tuple[float, int, int, int, list[tuple[float, float, float, float, str]]]] = []
                nameless = iter(ROWNAMES.get((year, district), []))
                ordered = iter(ORDERED.get((year, district), []))
                for index, (page_number, row) in enumerate(block):
                    county_words = []
                    for word in row:
                        if re.search(r"\d", word[4]) and word[0] > 90:
                            break
                        county_words.append(word[4])
                    key = re.sub(r"[^a-z]", "", "".join(county_words).lower())
                    if not any(re.search(r"\d", w[4]) and w[0] > 90 for w in row) or re.search(r"DISTRICT|WRITE|WINNER", " ".join(w[4] for w in row), re.IGNORECASE):
                        continue
                    alias = ALIASES.get((year, district, key))
                    if (year, district) in ORDERED:
                        alias = next(ordered, None)
                    if not key and sum(1 for w in row if re.search(r"\d", w[4])) >= 2 and (year, district) in ROWNAMES:
                        alias = next(nameless, None)
                    best = COUNTIES.index(alias) if alias else max(range(len(COUNTIES)), key=lambda i: difflib.SequenceMatcher(None, key, COUNTIES[i].lower()).ratio())
                    ratio = 1.0 if alias else difflib.SequenceMatcher(None, key, COUNTIES[best].lower()).ratio() if len(key) >= 3 else 0.0
                    if ratio < 0.5:
                        continue
                    if scored and scored[-1][1] >= best:
                        if scored[-1][0] >= ratio:
                            continue
                        scored.pop()
                    scored.append((ratio, best, page_number, index, row))
                scored.sort(key=lambda item: item[3])
                candidates = [(item[0], item[1], item[2], item[4]) for item in scored]
                for _, best, page_number, row in candidates:
                    close = [COUNTIES[best].lower()]
                    values: list[int | None] = [None] * len(edges)
                    boxes: list[list[float]] = [[]] * len(edges)
                    merged: list[list[object]] = []
                    for word in row:
                        if word[0] <= 90 and not re.search(r"\d", word[4]):
                            continue
                        if re.fullmatch(r"[A-Za-z]{3,}", word[4]) and word[0] < 160:
                            continue
                        if merged and word[4][:1] in ",." and word[0] - float(merged[-1][2]) < 6:
                            merged[-1][4] = str(merged[-1][4]) + word[4]
                            merged[-1][2] = word[2]
                        else:
                            merged.append(list(word))
                    for x0, y0, x1, y1, token in merged:
                        column = min(range(len(edges)), key=lambda i: abs(edges[i] - float(x1)))
                        if abs(edges[column] - float(x1)) > 18:
                            continue
                        fixed = str(token).translate(FIXES)
                        value: int | None = int(fixed.replace(",", "")) if re.fullmatch(r"\d[\d,]*", fixed) else None
                        values[column] = value if value is not None else -1
                        boxes[column] = [float(x0), float(y0), float(x1), float(y1)]
                    for column in range(len(edges)):
                        if values[column] is None and (year, district, KEYS[close[0]], column) in OVERRIDES:
                            values[column] = OVERRIDES[(year, district, KEYS[close[0]], column)]
                            boxes[column] = [edges[column] - 40, min(w[1] for w in row), edges[column], max(w[3] for w in row)]
                    cells.append((KEYS[close[0]], values, boxes))
                    rows.extend(
                        {
                            "year": year,
                            "district": district,
                            "county": cells[-1][0],
                            "column": column,
                            "votes": value,
                            "printed": printed[column],
                            "page": page_number,
                            "box": [round(v * DPI / 72) for v in boxes[column]],
                        }
                        for column, value in enumerate(values)
                        if value is not None
                    )
                for item in rows:
                    key = (int(item["year"]), int(item["district"]), str(item["county"]), int(item["column"]))
                    if key in OVERRIDES and int(item["year"]) == year and int(item["district"]) == district:
                        item["votes"] = OVERRIDES[key]
                for column in range(len(edges)):
                    mine = [r for r in rows if r["year"] == year and r["district"] == district and r["column"] == column]
                    summed = sum(int(r["votes"]) for r in mine if r["county"] != OMITTED.get((year, district)))
                    bad = [r["county"] for r in mine if int(r["votes"]) < 0]
                    if summed != printed[column] or bad:
                        unresolved.add((year, district))
                        problems.append(
                            f"{year} d{district} col{column}: sum {summed} printed {printed[column]} (diff {printed[column] - summed}) unreadable {bad} counties {len(mine)}"
                        )
                block = []
    frame = pl.DataFrame(
        [row for row in rows if (row["year"], row["district"]) not in unresolved], schema_overrides={"box": pl.List(pl.Int64)}
    )
    frame.write_parquet(PARSED / "lines.parquet")
    print("\n".join(problems))
    print(frame.group_by("year", "district").agg(pl.col("county").n_unique(), pl.col("column").n_unique()).sort("year", "district"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="US")
        sources: dict[int, object] = {}
        artifacts: dict[int, object] = {}
        pages: dict[tuple[int, int], tuple[object, int, int]] = {}
        for year, number in sorted(set(zip(frame["year"], frame["page"], strict=True))):
            if year not in sources:
                title = f"{year} Election Report"
                sources[year] = cs.source(PUBLISHER, title)
                if sources[year] in cs.minted:
                    cs.add("source", id=sources[year], kind="official_body", publisher=PUBLISHER, title=title, url=LANDING, license="Public record")
                artifacts[year] = cs.artifact(raw.cached(BOOK_SOURCE, URL.format(BOOKS[year])), sources[year])
            image = render(year, number)
            with Image.open(image) as rendered:
                width, height = rendered.size
            page = cs.page(artifacts[year], number)
            pages[(year, number)] = (page, width, height)
            cs.add("source_page", id=page, artifact_id=artifacts[year], page_number=number, image_key=f"pages/{SOURCE}/{image.name}", width=width, height=height)
        units = dict(conn.execute("select code, id from reporting_unit where scheme_code = 'us.county' and code like '45%%'").fetchall())
        areas = dict(conn.execute("select code, area_id from reporting_unit where scheme_code = 'us.county' and code like '45%%'").fetchall())
        names = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
        contests = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':SC:house:'"))
        made: set[object] = set()
        for (year, district), rows in frame.group_by("year", "district", maintain_order=True):
            day = DAYS[year]
            key = f"{day}:SC:house:{district}"
            contest = contests.get(key)
            if contest is None:
                print(f"{key}: no contest in the archive")
                continue
            old = conn.execute(
                """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness, supersedes_id
                   from result_set where contest_id = %s and is_default""",
                [contest],
            ).fetchall()
            if len(old) != 1:
                print(f"{key}: {len(old)} default result sets")
                continue
            ((county_rows,),) = conn.execute(
                """select count(*) from result_value v join result_set r on r.k = v.result_set_k
                   join reporting_unit u on u.k = v.unit_k
                   where r.id = %s and u.scheme_code in ('us.county', 'us.county_part')""",
                [old[0][0]],
            ).fetchall()
            if county_rows:
                continue
            row = old[0]
            roots = conn.execute(
                """select o.id, o.ballot_label, o.kind, v.value from contest_option o
                   join result_value v on v.option_k = o.k join result_set r on r.k = v.result_set_k
                   where o.contest_id = %s and o.parent_option_id is null and r.id = %s""",
                [contest, row[0]],
            ).fetchall()
            totals = {column: int(g["votes"].sum()) for (column,), g in rows.group_by("column")}
            targets: dict[int, object] = {}
            for column, total in totals.items():
                found = [option for option, _, _, value in roots if int(value) == total and option not in targets.values()]
                if len(found) == 1:
                    targets[column] = found[0]
            leftover = [column for column in totals if column not in targets]
            writein = [option for option, _, kind, _ in roots if kind == "write_in" and option not in targets.values()]
            extra = EXTRA.get((year, district), {})
            mismatch = False
            inexact = False
            apart: list[int] = []
            for column in leftover:
                near = sorted(
                    (abs(int(value) - totals[column]), option)
                    for option, _, kind, value in roots
                    if option not in targets.values() and kind != "write_in"
                )
                if column not in extra and near and near[0][0] * 25 <= totals[column]:
                    targets[column] = near[0][1]
                    inexact = True
                    apart.append(near[0][0])
                    print(f"{key}: column {column} {totals[column]} against FEC {near[0][0]} apart")
                elif column in extra:
                    label, party = extra[column]
                    candidate = cs.key(f"{SOURCE}.person", f"SC:{re.sub(r'[^a-z]+', '-', label.lower())}")
                    if candidate in cs.minted and candidate not in made:
                        made.add(candidate)
                        cs.add("person", id=candidate)
                        cs.add("name", entity_id=candidate, text=label.split(" (")[0], lang="en", kind="official", is_preferred=True)
                    option = cs.key("us.option", f"{key}:{candidate}:{column}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=label)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{candidate}:{column}:head"),
                        person_id=candidate,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=True,
                    )
                    targets[column] = option
                elif len(writein) == 1 and totals[column] * 50 <= sum(totals.values()):
                    targets[column] = writein[0]
                    mismatch = True
            covered = {option for option in targets.values()}
            if len(targets) != len(totals) or any(option not in covered for option, _, kind, _ in roots if kind != "write_in"):
                print(f"{key}: columns {totals} against FEC {[(label, kind, int(value)) for _, label, kind, value in roots]}")
                continue
            if mismatch:
                merged = sum(total for column, total in totals.items() if targets[column] in writein)
                mismatch = merged != next(int(value) for option, _, _, value in roots if option in writein)
                if mismatch:
                    print(f"{key}: write-ins {merged} against FEC; non-default")
            unprinted = sum(int(value) for option, _, kind, value in roots if kind == "write_in" and option not in covered)
            own = all(int(g["votes"].sum()) == int(g["printed"][0]) for _, g in rows.group_by("column"))
            mismatch = mismatch or (inexact and not own)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                **dict(zip(("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id"), row, strict=True)),
                is_default=mismatch,
            )
            cs.add(
                "result_set",
                id=result_set,
                election_id=row[1],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=not mismatch,
                supersedes_id=row[0] if not mismatch else None,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD + (f" The Federal Election Commission's compilation differs by {', '.join(str(v) for v in apart)} votes in a candidate's total." if apart else "") + (f" The Federal Election Commission's compilation adds {unprinted} write-in votes that the book does not print." if unprinted else ""))
            for option, kind, seats in conn.execute("select option_id, kind, seats from outcome where result_set_id = %s", [row[0]]).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            unit_ids = dict(units)
            ((district_area,),) = conn.execute("select district_area_id from contest where id = %s", [contest]).fetchall()
            split = {county for (county,), g in frame.filter(pl.col("year") == year).group_by("county") if g["district"].n_unique() > 1}
            for county in rows["county"].unique():
                code = names[re.sub(r"[^a-z]", "", county.lower())]
                if county not in split:
                    unit_ids[county] = units[code]
                    continue
                part = f"{year}:{code}:{district}"
                unit_ids[county] = cs.unit("us.county_part", part)
                if unit_ids[county] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[county],
                        polity_id=cs.polity("US-SC"),
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=units[code],
                        area_relation="within",
                        area_id=areas[code],
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                    cs.add("reporting_unit_area", unit_id=unit_ids[county], area_id=district_area, valid_from=day, valid_to=day + timedelta(days=1))
            merged: dict[tuple[str, object], list[int]] = {}
            for county, column, votes, number, box in rows.select("county", "column", "votes", "page", "box").iter_rows():
                cell = merged.setdefault((county, targets[column]), [0, number, *box])
                if cell[0]:
                    cell[2], cell[3], cell[4], cell[5] = min(cell[2], box[0]), min(cell[3], box[1]), max(cell[4], box[2]), max(cell[5], box[3])
                else:
                    cell[2:] = box
                cell[0] += votes
            for (county, option), (votes, number, x0, y0, x1, y1) in merged.items():
                code = names[re.sub(r"[^a-z]", "", county.lower())]
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit_ids[county] if county in split else units[code],
                    "option_id": option,
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                page, width, height = pages[(year, number)]
                cs.add("value_locator", **fact, page_id=page, source_id=None, x0=x0 / width, y0=y0 / height, x1=x1 / width, y1=y1 / height)
        return cs.write()
