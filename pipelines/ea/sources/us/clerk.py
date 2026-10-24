import csv
import difflib
import io
import itertools
import json
import re
import struct
import subprocess
import tempfile
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
import polars as pl
from PIL import Image
from psycopg.types.range import Range

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.sources.us.fec import INDEPENDENT, NATIONAL, OPEN_PRIMARY, PARTIES
from ea.store import DATA, connect

SOURCE = "us.clerk"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Clerk of the House of Representatives"
DERIVED = 1
DIGITIZED = 8
YEARS = range(1920, 1981, 2)
BOOK = "https://clerk.house.gov/member_info/electionInfo/{year}election.pdf"
LEGISLATORS = "https://unitedstates.github.io/congress-legislators/legislators-{kind}.json"
STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO",
    "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME",
    "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO",
    "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN",
    "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY",
}  # fmt: skip
SPECIAL = r"(?i)unexpired|vacanc|short term|to fill|balance of|term ending"
BLANK = r"(?i)^(blanks?|void|blank,? (and )?void)$"
UNNAMED = r"(?i)^(republican|democrat|democratic|socialist)( party)?$"
AGGREGATE = r"(?i)^(write-?ins?|others?|all others|scattering|scattered|independent)$"
DISTRICTS = {
    (1926, 4, 38): 5,
    (1930, 20, 40): 8,
    (1940, 21, 20): 14,
    (1940, 21, 21): 14,
    (1940, 21, 22): 14,
    (1964, 4, 3): 3,
    (1964, 4, 4): 3,
    (1936, 6, 2): 3,
    (1936, 6, 3): 1,
    (1936, 6, 4): 1,
    (1954, 21, 6): 2,
    (1954, 21, 7): 2,
    (1954, 21, 8): 2,
    (1954, 21, 12): 1,
    (1954, 21, 13): 1,
    (1954, 21, 14): 1,
}
DROPPED = {(1960, 47, "VA")}
MEMBERS = {
    (1928, "KY", 3): "R000294",
    (1930, "MO", 7): "M000074",
    (1974, "CA", 15): "S000454",
}
SWAPPED = {(1958, "OH", 23), (1970, "CA", 1), (1972, "OR", 4)}
UNSEATED = {(1920, "CA", 9), (1922, "NY", 19), (1926, "IL", None), (1932, "VT", 0), (1934, "IN", 2), (1960, "WY", None)}
TRANSCRIBED: dict[tuple[int, str, str, int | None], list[int]] = {
    (1948, "NY", "house", 13): [57997, 8114, 44718, 14440],
    (1948, "NY", "house", 14): [64752, 25734, 13190, 29502],
    (1948, "NY", "house", 15): [66220, 28608, 21703],
    (1948, "NY", "house", 16): [48420, 2765, 45623, 6991],
    (1948, "NY", "house", 17): [74581, 43690, 8411, 13401],
    (1948, "NY", "house", 18): [36278, 31211, 26038, 4861],
    (1948, "NY", "house", 19): [58833, 18593, 20697, 5886, 109],
    (1948, "NY", "house", 20): [63764, 10102, 34819, 15727],
    (1948, "NY", "house", 21): [45820, 21247, 49972, 14682],
    (1948, "NY", "house", 22): [51552, 11971, 14012, 5583],
    (1948, "NY", "house", 23): [76337, 28814, 16372, 24903],
    (1948, "NY", "house", 24): [51262, 13904, 9805, 43933],
    (1948, "NY", "house", 25): [75697, 47372, 15637, 30112],
    (1948, "NY", "house", 26): [81098, 10358, 57061, 18379],
    (1948, "NY", "house", 27): [81144, 64283, 3258, 7109],
    (1948, "NY", "house", 28): [88822, 43299, 3036, 6614],
}
MISSED = {
    (1964, 4): [(1, "E. C. Gathings"), (2, "Wilbur D. Mills"), (4, "Oren Harris")],
    (1968, 4): [(2, "Wilbur D. Mills"), (4, "David Pryor")],
}
CORRECTED = {(1978, 41, "Raymond B. Whitaker"): 50456, (1978, 41, "Alan Kooi Simpson"): 82908}
JUNGLE = {1978: date(1978, 9, 16), 1980: date(1980, 9, 13)}
RUNOFFS = {(1978, "LA", 4)}
HELD = {(1958, "AK"): date(1958, 11, 25)}
LABELS = {
    "Prohibitionist": "Prohibition", "Farmer Labor": "Farmer-Labor", "Soc.-Lab.": "Socialist Labor",
    "Socialist-Labor": "Socialist Labor", "Soc. Lab.": "Socialist Labor", "Soc.-Labor": "Socialist Labor",
    "Com. Land": "Commonwealth Land", "Nat. Prohib.": "National Prohibition", "Am. P.": "American",
    "Indus.": "Industrial", "Ind. Dem.": "Independent Democratic", "Soc.-Farm-Lab.": "Socialist Farmer-Labor",
    "Soc.-Farm.-Lab.": "Socialist Farmer-Labor", "Soc.-Far.-Lab.": "Socialist Farmer-Labor",
    "Socialist Worker": "Socialist Workers", "Socialist-Worker": "Socialist Workers",
    "Socialist-Workers": "Socialist Workers", "Socialist Worker's Party": "Socialist Workers", "by petition": "By Petition",
    "Socialsit": "Socialist", "Republicsons": "Republican", "Democrzt": "Democratic", "Dem": "Democratic",
    "Progressvie Labor": "Progressive Labor", "Independents for Goldy Government": "Independents for Godly Government",
    "Peo.": "People's", "Am.-Lab.": "American Labor",
}  # fmt: skip
PIECES = {
    "Dem.": "Democratic", "Dem": "Democratic", "Rep.": "Republican", "Prohib.": "Prohibition", "Proh.": "Prohibition",
    "Prog.": "Progressive", "Lab.": "Labor", "Soc.": "Socialist", "Farm": "Farmer", "Farm.": "Farmer", "Far.": "Farmer",
    "Ind.": "Independent", "Lib.": "Liberal", "Am.": "American", "Nonp.": "Nonpartisan",
    "Indus.": "Industrial", "Pres.": "Preservation",
}  # fmt: skip
FUSED = {
    "Republican", "Democratic", "Prohibition", "Progressive", "Socialist", "Liberal", "Conservative", "Union", "Labor",
    "Townsend", "Law Preservation", "Commonwealth", "Commonwealth Land", "Nonpartisan", "American Labor",
    "Farmer-Labor", "Socialist Labor", "Wet Modification",
}  # fmt: skip
CANONICAL = {"Prohibition": "Prohibition Party", "Socialist Workers": "Socialist Workers Party"}
OPEN_RUNOFF = (
    "No candidate won a majority in Louisiana's open primary on {date}, so the top two met again on {november}. The "
    "Clerk's statistics report only this second round."
)
NOVOTES = {
    (1924, "PA", 20): (
        "The Clerk's statistics print no votes for this race. A footnote says the returns were delayed by an election "
        "contest. The winner shown is the member who took the seat."
    ),
    (1964, "PA", 6): (
        "The Clerk's statistics print no votes for this race. A footnote says the vote in this district was not "
        "available. The winner shown is the member who took the seat."
    ),
}
MISNAMED = {
    (1950, "AR", "senate", None): (
        (
            "The Clerk's statistics name John L. McClellan as the candidate in this race; Arkansas elected J. William "
            "Fulbright to this seat in 1950, and McClellan held the state's other seat until 1955. We show Fulbright as "
            "the winner, using the vote counts printed beside McClellan's name."
        ),
        "F000401",
        "J. William Fulbright",
    )
}
MISNAMED_NOTE = UUID("01a143c8-3322-7054-b36b-7dbd4af0c9ef")
CLASSES = {(1950, "AR"): 3}
RESEATED = {
    "1950-11-07:AR:senate": "1950-11-07:AR:senate:special",
    "1936-11-03:FL:senate:class-3:special": "1936-11-03:FL:senate:special",
    "1954-11-02:NE:senate:class-2:special": "1954-11-02:NE:senate:special",
}
SWAPPED_NOTE = (
    "The Clerk's statistics print these two candidates' votes against each other's names. We swapped them back to "
    "match the state's summary table and the member who took the seat."
)
TRANSCRIBED_NOTE = (
    "On this page the printed votes sit one line below the candidates' names. We read each value by hand and checked "
    "it against the state's summary table."
)


def fetch() -> None:
    for url, suffix in (
        *((BOOK.format(year=year), ".pdf") for year in YEARS),
        *((LEGISLATORS.format(kind=kind), ".json") for kind in ("historical", "current")),
    ):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.fetch(SOURCE, url, suffix=suffix)
            time.sleep(1)


def pages(pdf: Path) -> int:
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    return int(next(line.split()[-1] for line in info.splitlines() if line.startswith("Pages:")))


def columns(image: Image.Image) -> list[int]:
    dark = np.asarray(image) < 128
    height, width = dark.shape
    middle = dark[height // 5 : height * 23 // 25].mean(axis=0)[width * 2 // 5 : width * 3 // 5]
    blank = np.flatnonzero(middle < 0.01)
    runs = np.split(blank, np.flatnonzero(np.diff(blank) > 2) + 1) if blank.size else []
    if runs and (widest := max(runs, key=lambda run: run[-1] - run[0]))[-1] - widest[0] >= 25:
        return [0, int(widest[0] + widest[-1]) // 2 + width * 2 // 5, width]
    return [0, width]


def read(pdf: Path, sha256: str, page: int) -> list[scan.Word]:
    cache = PARSED / "ocr" / f"{sha256}-{page}.tsv"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            image = Image.open(scan.rendered(pdf, page, Path(folder))).convert("L")
            cuts = columns(image)
            while True:
                parts = []
                for left, right in itertools.pairwise(cuts):
                    buffer = io.BytesIO()
                    image.crop((left, 0, right, image.height)).save(buffer, "PNG")
                    text = subprocess.run(
                        ["tesseract", "stdin", "stdout", "--psm", "6", "-l", "eng", "tsv"],
                        input=buffer.getvalue(),
                        capture_output=True,
                        check=True,
                    ).stdout.decode()
                    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))
                    for row in rows:
                        if row["level"] == "5" and row["text"].strip():
                            parts.append(
                                [
                                    str(left),
                                    row["text"],
                                    str(int(row["left"]) + left),
                                    row["top"],
                                    str(int(row["left"]) + left + int(row["width"])),
                                    str(int(row["top"]) + int(row["height"])),
                                    row["block_num"],
                                    row["par_num"],
                                    row["line_num"],
                                    row["conf"],
                                ]
                            )
                lefts = Counter(
                    int(part[2]) // 12
                    for part in parts
                    if image.width * 2 // 5 <= int(part[2]) <= image.width * 3 // 5
                    and any(character.isalpha() for character in part[1])
                )
                peak = min((bin for bin in lefts if lefts[bin] + lefts[bin + 1] >= 12), default=None)
                if len(cuts) > 2 or peak is None:
                    break
                cuts = [0, peak * 12 - 20, image.width]
            cache.write_text("\n".join("\t".join(part) for part in parts))
    return [
        (
            text,
            (int(x0), int(y0), int(x1), int(y1)),
            (int(column), int(block) * 1000 + int(paragraph), int(line)),
            float(confidence),
        )
        for column, text, x0, y0, x1, y1, block, paragraph, line, confidence in (
            entry.split("\t") for entry in cache.read_text().splitlines() if entry
        )
    ]


def trailing(line: list[scan.Word]) -> tuple[int, scan.Box] | None:
    tail: list[scan.Word] = []
    for word in reversed(line):
        text = word[0].translate(scan.CONFUSABLE)
        if re.fullmatch(r"[\d,.]*\d[\d,.]*", text):
            tail.insert(0, word)
            continue
        if (glued := re.search(r"\d[\d,]*$", text)) is not None and re.fullmatch(r".*[.\-_ ]", text[: glued.start()]):
            tail.insert(0, (glued.group(), (word[1][2] - 25 * len(glued.group()), *word[1][1:]), word[2], word[3]))
        break
    digits = "".join(re.sub(r"\D", "", word[0].translate(scan.CONFUSABLE)) for word in tail)
    if not digits:
        return None
    return int(digits), (
        min(w[1][0] for w in tail),
        min(w[1][1] for w in tail),
        max(w[1][2] for w in tail),
        max(w[1][3] for w in tail),
    )


def near(a: int, b: int) -> bool:
    x, y = str(a), str(b)
    if len(x) == len(y):
        return sum(p != q for p, q in zip(x, y, strict=True)) <= 2
    short, long = sorted((x, y), key=len)
    return len(long) - len(short) == 1 and any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def align(model: list[int], ocr: list[int]) -> dict[int, int]:
    score = [[0] * (len(ocr) + 1) for _ in range(len(model) + 1)]
    for i in range(len(model) - 1, -1, -1):
        for j in range(len(ocr) - 1, -1, -1):
            gain = 3 if model[i] == ocr[j] else 1 if near(model[i], ocr[j]) else -1
            score[i][j] = max(score[i + 1][j], score[i][j + 1], score[i + 1][j + 1] + gain if gain > 0 else -1)
    pairs: dict[int, int] = {}
    i = j = 0
    while i < len(model) and j < len(ocr):
        gain = 3 if model[i] == ocr[j] else 1 if near(model[i], ocr[j]) else -1
        if gain > 0 and score[i][j] == score[i + 1][j + 1] + gain:
            pairs[i] = j
            i, j = i + 1, j + 1
        elif score[i][j] == score[i + 1][j]:
            i += 1
        else:
            j += 1
    return pairs


def reread(pdf: Path, sha256: str, page: int, boxes: list[scan.Box]) -> list[list[str]]:
    cache = PARSED / "reread" / f"{sha256}-{page}.json"
    known: dict[str, list[str]] = json.loads(cache.read_text()) if cache.exists() else {}
    missing = [box for box in boxes if str(box) not in known]
    if missing:
        with tempfile.TemporaryDirectory() as folder:
            image = scan.rendered(pdf, page, Path(folder))
            wide = [(box[0] - 40, box[1] - 8, box[2] + 25, box[3] + 10) for box in missing]
            for box, texts in zip(missing, scan.read(image, [(box, True) for box in wide], "eng"), strict=True):
                known[str(box)] = texts
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known))
    return [known[str(box)] for box in boxes]


def reconcile(year: int, pdf: Path, sha256: str, page: int) -> list[dict[str, object]]:
    first, second = (PARSED / "model" / f"{year}-{page:02d}{suffix}.json" for suffix in ("", ".v2"))
    entries: list[dict[str, object]] = json.loads((second if second.exists() else first).read_text())["entries"]
    others: dict[tuple[object, ...], list[int]] = {}
    if second.exists():
        for entry in json.loads(first.read_text())["entries"]:
            if isinstance(entry.get("votes"), int):
                others.setdefault(
                    (
                        entry.get("state"),
                        entry.get("district"),
                        plain(str(entry.get("name"))),
                        plain(str(entry.get("party"))),
                    ),
                    [],
                ).append(int(str(entry["votes"])))
    words = read(pdf, sha256, page)
    found: list[tuple[int, scan.Box]] = []
    for column in sorted({word[2][0] for word in words}):
        mine = [word for word in words if word[2][0] == column]
        for line in scan.lines(mine, scan.skew(mine)):
            if (hit := trailing(line)) is not None:
                found.append(hit)
    voted = [index for index, entry in enumerate(entries) if isinstance(entry.get("votes"), int)]
    pairs = align([int(str(entries[index]["votes"])) for index in voted], [value for value, _ in found])
    doubtful = [
        (index, found[pairs[k]][1])
        for k, index in enumerate(voted)
        if k in pairs and found[pairs[k]][0] != entries[index]["votes"]
    ]
    readings = dict(
        zip(
            [index for index, _ in doubtful],
            reread(pdf, sha256, page, [box for _, box in doubtful]),
            strict=True,
        )
    )
    rows: list[dict[str, object]] = []
    for k, index in enumerate(voted):
        entry = entries[index]
        model = int(str(entry["votes"]))
        again = others.get(
            (entry.get("state"), entry.get("district"), plain(str(entry.get("name"))), plain(str(entry.get("party")))),
            [],
        )
        entry["second"] = again.pop(0) if again else None
        if k not in pairs:
            entry |= {"status": "confirmed" if entry["second"] == model else "model_only", "box": None, "ocr": None}
            continue
        value, box = found[pairs[k]]
        if value == model:
            entry |= {"status": "agreed", "box": list(box), "ocr": value}
            continue
        tally: dict[int, int] = {model: 1}
        tally[value] = tally.get(value, 0) + 1
        if entry["second"] is not None:
            tally[entry["second"]] = tally.get(entry["second"], 0) + 1
        votes = Counter(
            max(options, key=lambda n: options[n]) for text in readings[index] if (options := scan.numbers(text))
        )
        if votes:
            best = votes.most_common(1)[0][0]
            tally[best] = tally.get(best, 0) + 1
        top = max(tally, key=lambda n: tally[n])
        entry |= {
            "status": "settled" if tally[top] >= 3 else "disputed",
            "votes": top,
            "box": list(box),
            "ocr": value,
            "model": model,
            "readings": readings[index],
        }
    for index, entry in enumerate(entries):
        rows.append({"year": year, "page": page, "line": index, **{key: entry.get(key) for key in COLUMNS}})
    return rows


COLUMNS = (
    "state",
    "office",
    "heading",
    "district",
    "kind",
    "name",
    "party",
    "votes",
    "unsure",
    "status",
    "box",
    "ocr",
    "model",
    "readings",
    "second",
)


def winners(year: int) -> dict[tuple[str, str, int | None], list[dict]]:
    start = date(year + 1, 3, 4) if year <= 1932 else date(year + 1, 1, 3)
    found: dict[tuple[str, str, int | None], dict[str, dict]] = {}
    for kind in ("historical", "current"):
        for person in json.loads(raw.cached(SOURCE, LEGISLATORS.format(kind=kind)).path.read_text()):
            for term in person["terms"]:
                began = date.fromisoformat(term["start"])
                if began > start + timedelta(days=330) or date.fromisoformat(term["end"]) < date(year, 9, 1):
                    continue
                chamber = "senate" if term["type"] == "sen" else "house"
                district = None if chamber == "senate" else max(int(term.get("district", 0)), 0)
                regular = (
                    (date(year, 9, 1) if chamber == "senate" else start - timedelta(days=5))
                    <= began
                    < start + timedelta(days=30)
                )
                seen = found.setdefault((chamber, term["state"], district), {}).get(person["id"]["bioguide"])
                if seen is None or (regular and not seen["regular"]):
                    found[(chamber, term["state"], district)][person["id"]["bioguide"]] = {
                        "ids": person["id"],
                        "last": person["name"]["last"],
                        "full": person["name"].get("official_full")
                        or " ".join(
                            part
                            for part in (person["name"]["first"], person["name"].get("middle"), person["name"]["last"])
                            if part
                        ),
                        "start": began,
                        "party": term.get("party"),
                        "class": term.get("class"),
                        "regular": regular,
                    }
    return {key: list(people.values()) for key, people in found.items()}


def balance(rows: list[dict], target: int) -> list[tuple[tuple[tuple[int, int], int], ...]]:
    choices: list[tuple[tuple[int, int], int, list[int]]] = []
    for row in rows:
        current = row["votes"] if isinstance(row["votes"], int) else 0
        others = {
            value
            for value in (
                row["model"],
                row["ocr"],
                row["second"],
                *(number for text in row["readings"] or [] for number in scan.numbers(str(text))),
                *row.get("alternatives", []),
            )
            if isinstance(value, int)
        }
        if len(text := str(current)) >= 4 and text[0] in "12":
            others.add(int(text[1:]))
        choices.append(((int(row["page"]), int(row["line"])), current, sorted(others - {current})))
    gap = sum(current for _, current, _ in choices) - target
    if gap == 0:
        return [()]
    for size in (1, 2):
        found = [
            tuple((key, value) for (key, _, _), value in zip(combo, values, strict=True))
            for combo in itertools.combinations(choices, size)
            for values in itertools.product(*(others for _, _, others in combo))
            if sum(current - value for (_, current, _), value in zip(combo, values, strict=True)) == gap
        ]
        if found:
            return found
    return []


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def parse() -> None:
    books = [raw.cached(SOURCE, BOOK.format(year=year)) for year in YEARS]
    jobs = [(book, page) for book in books for page in range(1, pages(book.path) + 1)]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda job: read(job[0].path, job[0].sha256, job[1]), jobs))
    rows = [
        row
        for book, year in zip(books, YEARS, strict=True)
        for page in range(1, pages(book.path) + 1)
        for row in reconcile(year, book.path, book.sha256, page)
    ]
    rows += [
        {
            "year": year,
            "page": page,
            "line": 900 + index,
            **dict.fromkeys(COLUMNS),
            "state": "Arkansas",
            "office": "representative",
            "heading": "For Representatives",
            "district": district,
            "kind": "candidate",
            "name": name,
            "party": "Democrat",
            "status": "transcribed",
        }
        for (year, page), missed in MISSED.items()
        for index, (district, name) in enumerate(missed)
    ]
    lines = pl.DataFrame(rows, infer_schema_length=None).with_columns(
        state=pl.col("state")
        .str.to_uppercase()
        .str.replace_all(r"[^A-Z ]", " ")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
        .str.replace(r" CONTINUED$", "")
        .replace_strict(STATES, default=None),
        chamber=pl.when(pl.col("heading").fill_null("").str.contains(r"(?i)^\W*at[ -]large\W*$"))
        .then(pl.lit("house"))
        .otherwise(pl.col("office").replace_strict({"senator": "senate", "representative": "house"}, default=None)),
        special=pl.col("heading").fill_null("").str.contains(SPECIAL),
        kind=pl.when(pl.col("kind").is_in(["scattering", "write-in"]) & pl.col("name").str.contains(BLANK))
        .then(pl.lit("blank"))
        .when(pl.col("kind") == "write-in")
        .then(pl.lit("scattering"))
        .otherwise(pl.col("kind")),
    )
    lines = (
        lines.with_columns(
            district=pl.col("district")
            .cast(pl.String)
            .str.replace(r"(?i)^at[ -]large$", "0")
            .cast(pl.Int64, strict=False)
        )
        .with_columns(
            district=pl.when(
                (pl.col("chamber") == "house")
                & pl.col("district").is_null()
                & pl.col("heading").fill_null("").str.contains(r"(?i)at[ -]large")
            )
            .then(0)
            .otherwise(pl.col("district"))
        )
        .with_columns(
            district=pl.when(
                (pl.col("chamber") == "house")
                & pl.col("district").is_null()
                & pl.col("district").filter(pl.col("chamber") == "house").max().over("year", "state").is_null()
            )
            .then(0)
            .when(
                (pl.col("chamber") == "house")
                & ~pl.col("special")
                & (
                    pl.col("district")
                    .filter((pl.col("chamber") == "house") & ~pl.col("special"))
                    .n_unique()
                    .over("year", "state")
                    == 1
                )
            )
            .then(0)
            .when((pl.col("chamber") == "senate") & pl.col("heading").fill_null("").str.contains(r"\(For term [AB]\)"))
            .then(pl.col("heading").str.extract(r"term ([AB])").replace_strict({"A": 1, "B": 2}).cast(pl.Int64))
            .otherwise(pl.col("district"))
        )
    )
    lines = lines.filter(
        ~pl.struct("year", "page", "state").map_elements(
            lambda row: (row["year"], row["page"], row["state"]) in DROPPED, return_dtype=pl.Boolean
        )
    )
    for (year, page, line), district in DISTRICTS.items():
        lines = lines.with_columns(
            district=pl.when((pl.col("year") == year) & (pl.col("page") == page) & (pl.col("line") == line))
            .then(district)
            .otherwise(pl.col("district"))
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    for (year, page, name), votes in CORRECTED.items():
        lines = lines.with_columns(
            votes=pl.when((pl.col("year") == year) & (pl.col("page") == page) & (pl.col("name") == name))
            .then(votes)
            .otherwise(pl.col("votes"))
        )
    race = ["year", "state", "chamber", "district", "special"]
    lines = lines.with_columns(person=pl.col("name").map_elements(plain, return_dtype=pl.String))
    lines = (
        lines.join(
            lines.filter(pl.col("kind") == "candidate").select(*race, "person").unique().with_columns(fused=True),
            on=[*race, "person"],
            how="left",
            nulls_equal=True,
        )
        .with_columns(
            kind=pl.when((pl.col("kind") == "subtotal") & pl.col("fused").is_null())
            .then(pl.lit("candidate"))
            .otherwise(pl.col("kind"))
        )
        .drop("fused")
        .sort("year", "page", "line")
    )
    recap: dict[tuple[int, str, str, int | None], int] = {}
    for path in sorted((PARSED / "model").glob("*.v2.json")):
        for row in json.loads(path.read_text()).get("recap", []):
            label = str(row.get("row")).lower()
            values = [value for value in (row.get("values") or {}).values() if isinstance(value, int)]
            total = row["total"] if isinstance(row.get("total"), int) else sum(values)
            state = STATES.get(re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", str(row.get("state")).upper())).strip())
            if state is None or "total" in label:
                continue
            if "senat" in label:
                key = (int(path.name[:4]), state, "senate", None)
            elif "large" in label:
                key = (int(path.name[:4]), state, "house", 0)
            elif (number := re.match(r"\s*(\d+)", label)) is not None and "district" in label:
                key = (int(path.name[:4]), state, "house", int(number.group(1)))
            else:
                continue
            recap[key] = recap.get(key, 0) + total
    records = {(row["year"], row["page"], row["line"]): row for row in lines.to_dicts()}
    races: dict[tuple[int, str, str, int | None], list[dict]] = {}
    for row in records.values():
        if row["chamber"] is not None and row["kind"] in ("candidate", "scattering", "blank", "total", "subtotal"):
            races.setdefault(
                (row["year"], row["state"], row["chamber"], row["district"] if row["chamber"] == "house" else None), []
            ).append(row)
    for (year, state, chamber, district), rows in list(races.items()):
        after = (year, state, chamber, district + 1) if chamber == "house" and district is not None else None
        if after not in races or (year, state, chamber, district) not in recap or after not in recap:
            continue
        for moved, source, target in ((rows[-1], rows, races[after]), (races[after][0], races[after], rows)):
            here = sum(row["votes"] or 0 for row in rows if row["kind"] in ("candidate", "scattering"))
            there = sum(row["votes"] or 0 for row in races[after] if row["kind"] in ("candidate", "scattering"))
            shift = (moved["votes"] or 0) * (1 if source is rows else -1)
            if (
                moved["kind"] == "candidate"
                and here != recap[(year, state, chamber, district)]
                and here - shift == recap[(year, state, chamber, district)]
                and there + shift == recap[after]
            ):
                source.remove(moved)
                target.append(moved) if target is races[after] else target.insert(len(target), moved)
                moved["district"] = after[3] if target is races[after] else district
                break
    balanced: set[tuple[int, int, int]] = set()

    def settle(year: int, readings: list[list[dict]], target: int) -> None:
        found: list[tuple[tuple[tuple[int, int], int], ...]] = []
        for rows in readings:
            solutions = balance(rows, target)
            if solutions == [()]:
                return
            if solutions and (not found or len(solutions[0]) < len(found[0])):
                found = solutions
            elif solutions and len(solutions[0]) == len(found[0]):
                found += solutions
        if len(set(found)) == 1:
            for (page, line), value in found[0]:
                records[(year, page, line)]["votes"] = value
                balanced.add((year, page, line))

    for year, state, district in SWAPPED:
        first, second = (row for row in races[(year, state, "house", district)] if row["kind"] == "candidate")
        first["votes"], second["votes"] = second["votes"], first["votes"]
        first["box"], second["box"] = second["box"], first["box"]
        first["status"] = second["status"] = "swapped"
    boxes = {(row["year"], row["page"], row["ocr"]): row["box"] for row in records.values() if row["ocr"] is not None}
    for key, values in TRANSCRIBED.items():
        rows = [row for row in races[key] if row["kind"] in ("candidate", "scattering")]
        if len(rows) != len(values):
            raise ValueError(f"{key}: {len(rows)} lines, {len(values)} transcribed")
        for row, value in zip(rows, values, strict=True):
            row |= {"votes": value, "status": "transcribed", "box": boxes.get((row["year"], row["page"], value))}
    for key, rows in races.items():
        if key in TRANSCRIBED:
            continue
        for row in rows:
            if row["votes"] is None and row["kind"] == "candidate":
                row["alternatives"] = [
                    other["votes"]
                    for other in rows
                    if other["kind"] == "subtotal" and other["person"] == row["person"] and other["votes"] is not None
                ]
        counted = [row for row in rows if row["kind"] in ("candidate", "scattering")]
        blanks = [row for row in rows if row["kind"] == "blank"]
        for total in (row for row in rows if row["kind"] == "total" and row["votes"] is not None):
            part = [row for row in counted if row["special"] == total["special"]]
            settle(key[0], [part, part + [row for row in blanks if row["special"] == total["special"]]], total["votes"])
        if key in recap:
            regular = [row for row in counted if not row["special"]]
            special = [row for row in counted if row["special"]]
            settle(key[0], [counted, counted + blanks, regular, regular + blanks, special], recap[key])
    for key in balanced:
        records[key]["status"] = "balanced"
    lines = pl.DataFrame(
        [{column: row[column] for column in lines.columns} for row in records.values()], schema=lines.schema
    )
    lines.write_parquet(PARSED / "lines.parquet")
    members = {year: winners(year) for year in YEARS}
    contests: list[dict[str, object]] = []
    problems: list[str] = []
    voting = lines.filter(pl.col("state").is_not_null() & pl.col("chamber").is_not_null())
    for (year, state, chamber, district, special), group in voting.group_by(
        "year", "state", "chamber", "district", "special", maintain_order=True
    ):
        entries = group.filter(pl.col("kind").is_in(["candidate", "scattering"])).to_dicts()
        if not entries:
            continue
        known = members[year].get((chamber, state, district if chamber == "house" else None), [])
        if (
            not known
            and chamber == "house"
            and district == 0
            and voting.filter((pl.col("year") == year) & (pl.col("state") == state) & (pl.col("chamber") == "house"))[
                "district"
            ].max()
            == 0
        ):
            known = [
                member for key, people in members[year].items() if key[:2] == ("house", state) for member in people
            ]
        leader = max(
            (entry for entry in entries if entry["kind"] == "candidate"),
            key=lambda entry: entry["votes"] or 0,
            default=None,
        )
        magnitude = (
            sum(1 for entry in entries if entry["kind"] == "candidate" and entry["party"] == leader["party"])
            if chamber == "house"
            and district == 0
            and not special
            and leader is not None
            and leader["party"] is not None
            else 1
        )
        people: dict[str, list[int]] = {}
        for index, entry in enumerate(entries):
            if entry["kind"] == "candidate":
                people.setdefault(plain(str(entry["name"])), []).append(index)
        ranked = sorted(people, key=lambda person: -sum(entries[index]["votes"] or 0 for index in people[person]))
        voted = any(entry["votes"] is not None for entry in entries)
        chosen = (
            ranked[:magnitude]
            if voted or len(ranked) == magnitude
            else [person for person in ranked if any(plain(str(member["last"])) in person for member in known)]
        )
        elected = {index for person in chosen for index in people[person]}
        for person in chosen:
            entry = entries[people[person][0]]
            printed = [plain(token) for token in str(entry["name"]).split()]
            match = [
                member
                for member in known
                if plain(str(member["last"])) in person
                or any(
                    difflib.SequenceMatcher(None, plain(str(member["last"])), token).ratio() >= 0.8 for token in printed
                )
                or any(len(token) >= 5 and token in printed for token in map(plain, str(member["full"]).split()))
            ]
            if len(match) > 1:
                match = [member for member in match if member["regular"] != special] or match
            if len(match) > 1:
                match = [
                    max(
                        match,
                        key=lambda member: difflib.SequenceMatcher(None, plain(str(member["full"])), person).ratio(),
                    )
                ]
            if (year, state, district) in MEMBERS:
                match = [
                    member
                    for people in members[year].values()
                    for member in people
                    if member["ids"]["bioguide"] == MEMBERS[(year, state, district)]
                ][:1]
            if not match and (district == 0 or not known):
                match = [
                    member
                    for key, people in members[year].items()
                    if key[:2] == (chamber, state)
                    for member in people
                    if len(plain(str(member["last"]))) >= 4 and plain(str(member["last"])) in person
                ]
                match = match if len(match) == 1 else []
            if not match and (year, state, district) not in UNSEATED:
                problems.append(
                    f"{year} {state} {chamber} {district} {'special ' if special else ''}{entry['name']!r} "
                    f"({sum(entries[index]['votes'] or 0 for index in people[person])}): "
                    f"members {[member['full'] for member in known]}"
                )
            for index in people[person]:
                entries[index]["member"] = json.dumps(match[0]["ids"]) if match else None
                entries[index]["member_name"] = match[0]["full"] if match else None
                entries[index]["seat_class"] = match[0]["class"] if match else None
        for index, entry in enumerate(entries):
            contests.append(
                {
                    **{
                        key: entry[key]
                        for key in (
                            "year",
                            "page",
                            "line",
                            "state",
                            "chamber",
                            "district",
                            "special",
                            "kind",
                            "name",
                            "party",
                            "votes",
                            "status",
                            "box",
                        )
                    },
                    "magnitude": magnitude,
                    "elected": index in elected,
                    "member": entry.get("member"),
                    "member_name": entry.get("member_name"),
                    "seat_class": entry.get("seat_class"),
                }
            )
    pl.DataFrame(contests, infer_schema_length=None).write_parquet(PARSED / "contests.parquet")
    valid = pl.col("kind") != "blank"
    counted = (
        lines.filter(pl.col("kind").is_in(["candidate", "scattering", "blank"]) & pl.col("chamber").is_not_null())
        .group_by("year", "state", "chamber", "district")
        .agg(
            pl.col("votes").filter(valid).sum(),
            pl.col("votes").sum().alias("blanks"),
            pl.col("votes").filter(valid & ~pl.col("special")).sum().alias("regular"),
            pl.col("votes").filter(~pl.col("special")).sum().alias("regular_blanks"),
            pl.col("votes").filter(valid & pl.col("special")).sum().alias("specials"),
            sure=(
                pl.col("status").is_in(["agreed", "confirmed", "balanced", "transcribed", "swapped"])
                | ((pl.col("model") == pl.col("votes")) & (pl.col("second") == pl.col("votes"))).fill_null(False)
                | ~valid
            ).all(),
        )
        .filter(~pl.col("sure"))
        .drop("sure")
    )
    for year, state, chamber, district, votes, *others in counted.iter_rows():
        if (key := (year, state, chamber, district if chamber == "house" else None)) in recap and recap[key] not in (
            votes,
            *others,
        ):
            problems.append(f"recap {year} {state} {chamber} {district}: lines {votes}, table {recap[key]}")
    (PARSED / "problems.txt").write_text("\n".join(problems))
    print(f"{len(problems)} winners without a member; see {PARSED / 'problems.txt'}")


def emit() -> Path:
    frame = (
        pl.read_parquet(PARSED / "contests.parquet")
        .with_columns(
            party=pl.when(pl.col("year").is_between(1936, 1940))
            .then(pl.col("party").str.replace_all(r"(^|[,;/]\s*)Union(\s*(?:[,;/]|$))", "${1}Union Party of 1936${2}"))
            .otherwise(pl.col("party")),
            relabeled=(pl.col("kind") == "candidate")
            & (pl.col("name").str.contains(UNNAMED) | pl.col("name").str.contains(AGGREGATE)),
        )
        .with_columns(
            party=pl.when(pl.col("relabeled") & pl.col("name").str.contains(UNNAMED))
            .then(pl.col("party").fill_null(pl.col("name").str.replace(r"(?i) party$", "")))
            .otherwise(pl.col("party")),
            kind=pl.when(pl.col("relabeled") & pl.col("name").str.contains(UNNAMED))
            .then(pl.lit("party"))
            .when(pl.col("relabeled"))
            .then(pl.lit("scattering"))
            .otherwise(pl.col("kind")),
        )
    )
    days = {year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in YEARS}
    with connect() as conn:
        claimed: set[UUID] = {
            contest
            for (contest,) in conn.execute(
                """select rs.contest_id from result_set rs join changeset c on c.id = rs.changeset_id
                   where rs.is_default and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        states: dict[str, tuple[UUID, UUID, str, str]] = {
            iso.removeprefix("US-"): (unit, area, text, code)
            for text, iso, unit, area, code in conn.execute(
                """select n.text, p.iso_code, u.id, u.area_id, u.code from reporting_unit u
                   join polity p on p.id = u.polity_id
                   join name n on n.entity_id = p.id and n.is_preferred
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        districts: dict[str, list[tuple[UUID, UUID, str, Range[date]]]] = {}
        for code, area, unit, text, valid in conn.execute(
            """select u.code, u.area_id, u.id, n.text, u.valid from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'official'
               where u.scheme_code = 'us.cd'"""
        ).fetchall():
            districts.setdefault(code, []).append((area, unit, text, valid))
        plans: list[tuple[UUID, date, date | None]] = conn.execute(
            "select id, valid_from, valid_to from boundary_set where scheme_code = 'us.cd'"
        ).fetchall()
        known: dict[str, dict[str, UUID]] = {}
        for scheme, value, entity in conn.execute(
            """select scheme, value, entity_id from external_id
               where scheme in ('fec.candidate', 'wikidata', 'bioguide', 'us.fec.person')"""
        ).fetchall():
            known.setdefault(scheme, {})[value] = entity
        identified = {(scheme, entity) for scheme, values in known.items() for entity in values.values()}
        foreign: set[UUID] = {
            entity
            for (entity,) in conn.execute(
                """select x.entity_id from external_id x join result_set r on r.id = x.entity_id
                   join changeset c on c.id = r.changeset_id where x.scheme = %s and c.pipeline <> %s""",
                [f"{SOURCE}.result_set", SOURCE],
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House and Senate general results, 1920–1980, digitized from the Clerk's statistics",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        elections: dict[str, UUID] = {}

        def election(
            key: str, polity: UUID, kind: str, day: date, title: str, path: str, parent: UUID | None = None
        ) -> UUID:
            if key not in elections:
                elections[key] = cs.key("us.election", key)
                cs.add(
                    "election",
                    id=elections[key],
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(elections[key], title)
                cs.add("slug", path=path, entity_id=elections[key])
            return elections[key]

        us = cs.polity("US")
        bodies = {kind: cs.key("us.body", kind) for kind in ("house", "senate")}
        systems = {kind: cs.key("us.electoral_system", kind) for kind in ("fptp", "block", "two_round")}
        if cs.minted:
            raise ValueError("the House, the Senate, and their electoral systems must already exist")
        artifacts: dict[int, UUID] = {}
        for year in YEARS:
            title = (
                f"Statistics of the {'Presidential and ' if year % 4 == 0 else ''}Congressional Election of "
                f"{days[year]:%B %-d, %Y}"
            )
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=BOOK.format(year=year),
                license="Public domain",
            )
            artifacts[year] = cs.artifact(raw.cached(SOURCE, BOOK.format(year=year)), source)
        PAGES.mkdir(parents=True, exist_ok=True)
        sizes: dict[tuple[int, int], tuple[int, int]] = {}
        for year, number in (
            frame.filter(pl.col("box").is_not_null()).select("year", "page").unique().sort("*").iter_rows()
        ):
            book = raw.cached(SOURCE, BOOK.format(year=year))
            image = PAGES / f"{book.sha256}-{number}.png"
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(book.path), str(image.with_suffix(""))],
                    check=True,
                )
            sizes[(year, number)] = struct.unpack(">II", image.read_bytes()[16:24])
            cs.add(
                "source_page",
                id=cs.page(artifacts[year], number),
                artifact_id=artifacts[year],
                page_number=number,
                image_key=f"pages/{SOURCE}/{book.sha256}-{number}.png",
                width=sizes[(year, number)][0],
                height=sizes[(year, number)][1],
            )

        def labels(text: str | None) -> tuple[list[str], bool]:
            if text is None:
                return [], False
            written = re.search(r"(?i)write|written", text) is not None
            found: list[str] = []
            for part in re.split(r"[,;/]\s*", re.sub(r"(?i)\s*\(?\b(write-?ins?|written in)\b\)?", "", text)):
                part = re.sub(r"\bDemocrat\b", "Democratic", LABELS.get(part.strip(), part.strip()))
                if not part:
                    continue
                pieces = [PIECES.get(piece, piece) for piece in re.split(r"\s*-\s*|\s+and\s+", part)]
                segments: list[str] = []
                start = 0
                while len(pieces) > 1 and start < len(pieces):
                    step = next(
                        (
                            (stop, joined)
                            for stop in range(len(pieces), start, -1)
                            for joined in ("-".join(pieces[start:stop]), " ".join(pieces[start:stop]))
                            if joined in FUSED or (joined == "Independent" and start > 0)
                        ),
                        None,
                    )
                    if step is None:
                        segments = []
                        break
                    start, joined = step
                    segments.append(joined)
                if part not in PARTIES and len(segments) > 1:
                    found += segments
                else:
                    found.append(re.sub(r"[A-Za-z]+\.?", lambda token: PIECES.get(token[0], token[0]), part))
            return found, written

        parties: dict[tuple[str, str], UUID | None] = {}
        created: set[UUID] = set()
        for state, text in frame.select("state", "party").unique().drop_nulls().sort("*").iter_rows():
            for label in labels(text)[0]:
                if (state, label) in parties:
                    continue
                if label in INDEPENDENT:
                    parties[(state, label)] = None
                    continue
                canonical = CANONICAL.get(label, PARTIES.get(label, label))
                if re.search(r"Minnesota|North Dakota", canonical) and state not in ("MN", "ND"):
                    canonical = label
                national = canonical in NATIONAL or canonical == "Union Party of 1936"
                party = cs.key("us.party", slug(canonical) if national else f"{state}:{slug(canonical)}")
                parties[(state, label)] = party
                if party in cs.minted and party not in created:
                    created.add(party)
                    cs.add("party", id=party, kind="party", polity_id=us if national else cs.polity(f"US-{state}"))
                    name(party, "Union Party" if canonical == "Union Party of 1936" else canonical)

        persons: dict[str, UUID] = {}
        by_state: dict[tuple[str, str], UUID] = {}
        for state, text, member, full in (
            frame.filter(pl.col("member").is_not_null())
            .select("state", "name", "member", "member_name")
            .unique(maintain_order=True)
            .iter_rows()
        ):
            ids = json.loads(member)
            if ids["bioguide"] not in persons:
                person = next(
                    (known["fec.candidate"][fec] for fec in ids.get("fec", []) if fec in known["fec.candidate"]), None
                )
                if person is None:
                    person = (
                        cs.key("wikidata", ids["wikidata"])
                        if "wikidata" in ids
                        else cs.key("bioguide", ids["bioguide"])
                    )
                if person in cs.minted and person not in created:
                    created.add(person)
                    cs.add("person", id=person)
                    name(person, full)
                for scheme, value in (("wikidata", ids.get("wikidata")), ("bioguide", ids["bioguide"])):
                    if (
                        value is not None
                        and value not in known.get(scheme, {})
                        and (scheme, person) not in identified
                        and (scheme, value) not in cs._keyed
                    ):
                        cs.add("external_id", entity_id=person, scheme=scheme, value=value)
                        identified.add((scheme, person))
                persons[ids["bioguide"]] = person
            for form in (text, full):
                by_state.setdefault((state, slug(form)), persons[ids["bioguide"]])

        def person_of(row: dict) -> UUID:
            if row["member"] is not None:
                return persons[json.loads(str(row["member"]))["bioguide"]]
            key = f"{row['state']}:{slug(str(row['name']))}"
            if (str(row["state"]), slug(str(row["name"]))) in by_state:
                return by_state[(str(row["state"]), slug(str(row["name"])))]
            if int(str(row["year"])) >= 1970 and key in known.get("us.fec.person", {}):
                return known["us.fec.person"][key]
            person = cs.key(f"{SOURCE}.person", key)
            if person in cs.minted and person not in created:
                created.add(person)
                cs.add("person", id=person)
                name(person, str(row["name"]))
            by_state[(str(row["state"]), slug(str(row["name"])))] = person
            return person

        for (year, state, chamber, district, special), group in frame.group_by(
            "year", "state", "chamber", "district", "special", maintain_order=True
        ):
            rows = group.rows(named=True)
            unit, area, place, fips = states[state]
            polity = cs.polity(f"US-{state}")
            umbrella = election(
                str(days[year]),
                us,
                "general",
                days[year],
                f"{year} United States general election",
                f"us/{days[year]}-general",
            )
            jungle = state == "LA" and year in JUNGLE
            runoff = chamber == "house" and (year, state, district) in RUNOFFS
            if jungle and not runoff:
                day = JUNGLE[year]
            elif state == "ME" and year <= 1958:
                day = date(year, 9, 1) + timedelta(days=(-date(year, 9, 1).weekday()) % 7 + 7)
            else:
                day = HELD.get((year, state), days[year])
            parent = election(
                f"{JUNGLE[year] if jungle else day}:{state}",
                polity,
                "general",
                JUNGLE[year] if jungle else day,
                f"{year} {place} general election"
                if day == days[year] and not jungle
                else f"{JUNGLE[year] if jungle else day:%B %Y} {place} general election",
                f"us/{state.lower()}/{JUNGLE[year] if jungle else day}-general",
                umbrella,
            )
            if runoff:
                parent = election(
                    f"{day}:{state}:runoff",
                    polity,
                    "runoff",
                    day,
                    f"{year} {place} runoff election",
                    f"us/{state.lower()}/{day}-runoff",
                    parent,
                )
            elected = [row for row in rows if row["elected"]]
            regular = (3, 1, 2)[(year - 2004) % 6 // 2]
            number = next((row["seat_class"] for row in elected if row["seat_class"] is not None), None)
            if chamber == "senate" and number is None and special and district is not None:
                number = district
            if chamber == "senate" and number is None and (special or district is not None):
                raise ValueError(f"{year} {state} senate: no class for the seat")
            number = CLASSES.get((year, state), regular if number is None else number)
            special = special or (chamber == "senate" and district is None and number != regular)
            on = day if special else date(year + 1, 3, 4) if year <= 1932 else date(year + 1, 1, 3)
            plan = next(id for id, start, end in plans if start <= on and (end is None or on < end))
            if chamber == "senate":
                seat: UUID | None = cs.key("us.seat", f"senate:{state}:{number}")
                if seat in cs.minted:
                    raise ValueError(f"{year} {state}: no Senate class {number} seat")
                part = f"senate-class-{number}" if district is not None else "senate"
                contest_key = f"{day}:{state}:senate" + (f":class-{number}" if district is not None else "")
                title_place = place
            else:
                code = f"{fips}{district:02d}"
                matches = [entry for entry in districts.get(code, []) if on in entry[3]]
                if matches == [] and district == 0:
                    title_place = f"{place} at large"
                elif len(matches) == 1:
                    area, unit, title_place, _ = matches[0]
                else:
                    raise ValueError(f"{year} {state}-{district}: {len(matches)} districts {code} on {on}")
                seat = None
                part = f"house-{district}" if district else "house-at-large"
                contest_key = f"{day}:{state}:house:{district}"
            contest_key += ":special" if special else ""
            contest_key = RESEATED.get(contest_key, contest_key)
            magnitude = int(rows[0]["magnitude"])
            missing = (year, state, district) in NOVOTES and chamber == "house"
            tacit = not missing and all(row["votes"] is None for row in rows)
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=parent,
                kind="body_seats",
                body_id=bodies[chamber],
                district_area_id=area,
                seat_id=seat,
                term="remainder" if special else "full",
                boundary_set_id=None if chamber == "senate" else plan,
                magnitude=magnitude,
                round_number=2 if runoff else 1,
                electoral_system_id=systems[
                    "two_round" if jungle or (state == "GA" and year >= 1964) else "block" if magnitude > 1 else "fptp"
                ],
                default_result_kind="tacit" if tacit else "counted",
            )
            name(
                contest,
                f"{year} U.S. {'Senate' if chamber == 'senate' else 'House'} "
                f"{'special election' if special else 'runoff' if runoff else 'election'} in {title_place}"
                + (f" (Class {'I' * number})" if chamber == "senate" and district is not None else ""),
            )
            cs.add(
                "slug",
                path=f"us/{state.lower()}/{day}-{'runoff' if runoff else 'general'}/{part}{'-special' if special else ''}",
                entity_id=contest,
            )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            if result_set in foreign:
                rows = [{**row, "kind": "candidate", "relabeled": False} if row["relabeled"] else row for row in rows]
            cs.add(
                "result_set",
                id=result_set,
                election_id=parent,
                contest_id=contest,
                stage="certified",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=cs.source(
                    PUBLISHER,
                    f"Statistics of the {'Presidential and ' if year % 4 == 0 else ''}Congressional Election of "
                    f"{days[year]:%B %-d, %Y}",
                ),
                is_default=contest not in claimed,
                completeness=None if tacit else 0 if missing else 1,
            )
            for applies, kind, body in (
                (
                    jungle and not runoff,
                    "context",
                    OPEN_PRIMARY.format(date=f"{JUNGLE[year]:%B %-d, %Y}") if jungle else "",
                ),
                (
                    runoff,
                    "context",
                    OPEN_RUNOFF.format(date=f"{JUNGLE[year]:%B %-d, %Y}", november=f"{day:%B %-d, %Y}")
                    if jungle
                    else "",
                ),
                (missing, "caveat", NOVOTES.get((year, state, district), "")),
                (
                    (year, state, chamber, district) in MISNAMED,
                    "caveat",
                    MISNAMED.get((year, state, chamber, district), ("",))[0],
                ),
                (chamber == "house" and (year, state, district) in SWAPPED, "caveat", SWAPPED_NOTE),
                (
                    chamber == "house" and (year, state, "house", district) in TRANSCRIBED,
                    "methodology",
                    TRANSCRIBED_NOTE,
                ),
            ):
                if applies and body == MISNAMED.get((year, state, chamber, district), ("",))[0]:
                    cs.add("note", id=MISNAMED_NOTE, entity_id=result_set, kind=kind, body_md=body)
                elif applies:
                    cs.add("note", entity_id=result_set, kind=kind, body_md=body)
            values: list[tuple[UUID | None, int | None, int, dict | None]] = []
            roots: dict[UUID, UUID] = {}
            seen: set[UUID] = set()
            people: dict[UUID, list[dict]] = {}
            for row in rows:
                if row["kind"] == "candidate":
                    people.setdefault(person_of(row), []).append(row)
            misnamed = MISNAMED.get((year, state, chamber, district))
            for printed, lines in people.items():
                option_key = f"{contest_key}:{printed}"
                person = printed
                printed_name = lines[0]["name"]
                if misnamed is not None and any(row["elected"] for row in lines):
                    person, printed_name = persons[misnamed[1]], misnamed[2]
                root = cs.key("us.option", option_key)
                found = [labels(row["party"]) for row in lines]
                every = [label for found_labels, _ in found for label in found_labels]
                nominated = list(
                    dict.fromkeys(party for label in every if (party := parties[(state, label)]) is not None)
                )
                written = all(flag for _, flag in found)
                cs.add(
                    "contest_option",
                    id=root,
                    contest_id=contest,
                    kind="write_in" if written else "candidate",
                    party_id=(nominated[0] if nominated else None) if len(lines) == 1 else None,
                    ballot_label=printed_name,
                )
                head = cs.key("us.candidacy", f"{option_key}:head")
                cs.add(
                    "candidacy",
                    id=head,
                    person_id=person,
                    option_id=root,
                    role="head",
                    ballot_name=printed_name,
                    is_independent=not nominated and any(label in INDEPENDENT for label in every),
                    is_incumbent=None,
                )
                for party in nominated:
                    cs.add("candidacy_nomination", candidacy_id=head, party_id=party, kind="nominated")
                roots[printed] = root
                for row, (row_labels, row_written) in zip(lines, found, strict=True):
                    option = root
                    if len(lines) > 1:
                        label = f"{row['name']} ({row['party']})" if row["party"] else str(row["name"])
                        option = cs.key("us.option", f"{option_key}:{slug(label)}:{row['page']}:{row['line']}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="write_in" if row_written else "candidate",
                            party_id=next(
                                (party for label in row_labels if (party := parties[(state, label)]) is not None), None
                            ),
                            ballot_label=label,
                        )
                    values.append((option, row["votes"], DIGITIZED, row))
            for row in rows:
                if row["kind"] not in ("scattering", "party"):
                    continue
                kind = (
                    "party"
                    if row["kind"] == "party"
                    else "others"
                    if re.search(r"(?i)blank|void", str(row["name"]))
                    or (row["relabeled"] and re.search(r"(?i)^(others?|independent)$", str(row["name"])))
                    else "write_in"
                )
                option = cs.key("us.option", f"{contest_key}:{kind}:{slug(str(row['name']))}")
                if option in seen:
                    option = cs.key(
                        "us.option", f"{contest_key}:{kind}:{slug(str(row['name']))}:{row['page']}:{row['line']}"
                    )
                seen.add(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate" if kind == "party" else kind,
                    party_id=parties[(state, labels(row["party"])[0][0])] if kind == "party" else None,
                    ballot_label=row["name"],
                )
                values.append((option, row["votes"], DIGITIZED, row))
            counted = [votes for _, votes, _, _ in values if votes is not None]
            if counted:
                values.append((None, sum(counted), DERIVED, None))
            if result_set in cs.minted or contest_key in RESEATED.values() or any(row["relabeled"] for row in rows):
                for option, votes, flags, row in values:
                    if votes is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1 if option else 4,
                        count_round=0,
                        flags=flags,
                        value=votes,
                    )
                    if row is not None and row["box"] is not None:
                        width, height = sizes[(year, int(str(row["page"])))]
                        box = list(row["box"])
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=1 if option else 4,
                            count_round=0,
                            page_id=cs.page(artifacts[year], int(str(row["page"]))),
                            source_id=None,
                            x0=box[0] / width / 3,
                            y0=box[1] / height / 3,
                            x1=box[2] / width / 3,
                            y1=box[3] / height / 3,
                        )
            winners = {person for person, lines in people.items() if any(row["elected"] for row in lines)}
            if len(winners) != magnitude:
                raise ValueError(f"{contest_key}: {len(winners)} elected for {magnitude} seats")
            for person in winners:
                cs.add("outcome", result_set_id=result_set, option_id=roots[person], kind="elected", seats=1)
        reason = "A party label or write-in line in the Clerk's statistics read as a candidate's name"
        junk = conn.execute(
            """select c.id from candidacy c join changeset ch on ch.id = c.changeset_id
               join name n on n.entity_id = c.person_id and n.is_preferred
               join contest_option o on o.id = c.option_id
               where ch.pipeline = %s and (n.text ~ %s or n.text ~ %s)
                 and not exists (
                   select 1 from result_set r join external_id x on x.entity_id = r.id and x.scheme = %s
                   where r.contest_id = o.contest_id and r.id = any(%s::uuid[]))""",
            [SOURCE, UNNAMED, AGGREGATE, f"{SOURCE}.result_set", list(foreign)],
        ).fetchall()
        if not junk:
            junk = conn.execute(
                """select x.entity_id from external_id x join contest_option o on o.id = x.entity_id
                   where x.scheme = 'us.option' and x.value ~ ':[0-9a-f-]{36}$'
                     and (o.ballot_label ~ %s or o.ballot_label ~ %s)
                     and not exists (select 1 from candidacy c where c.option_id = o.id)
                     and o.changeset_id in (select id from changeset where pipeline = %s)
                   union
                   select x.entity_id from external_id x join name n on n.entity_id = x.entity_id and n.is_preferred
                   where x.scheme = %s and (n.text ~ %s or n.text ~ %s)
                     and not exists (select 1 from candidacy c where c.person_id = x.entity_id)""",
                [UNNAMED, AGGREGATE, SOURCE, f"{SOURCE}.person", UNNAMED, AGGREGATE],
            ).fetchall()
        for (entity,) in junk:
            cs.add("entity_retirement", entity_id=entity, reason=reason)
        return cs.write()
