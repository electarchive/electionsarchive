import csv
import io
import re
import statistics
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl
from PIL import Image

from ea import scan
from ea.sources.us import house_books
from ea.store import DATA

SOURCE = "us.nj_house"
RAW = "us.sri"
VERSION = "2"
PUBLISHER = "New Jersey Division of Elections"
TITLE = "State of New Jersey Results of the General Election, {day:%B %-d, %Y}"
LICENSE = "Public record"
SUMMARY = "New Jersey's House results by county from the Division of Elections' general election results"
STATE = "NJ"
FIPS = "34"
ITEMS = {
    1982: ("micro_IA40706913_0156", date(1982, 11, 2), (5, 6, 7)),
    1984: ("micro_IA40706919_0252", date(1984, 11, 6), (7, 8, 9, 10, 11)),
    1986: ("micro_IA40706925_0446", date(1986, 11, 4), (5, 6, 7, 8, 9)),
    1988: ("micro_IA40706932_0272", date(1988, 11, 8), tuple(range(8, 16))),
    1990: ("micro_IA40706939_0170", date(1990, 11, 6), (3, 4, 5, 6, 7, 8, 9, 10)),
    1992: ("micro_IA40706946_0066", date(1992, 11, 3), tuple(range(6, 19))),
}
NJ96 = "https://www.nj.gov/state/elections/assets/pdf/election-results/1996/1996-general-election-results-house.pdf"
NJ98 = "https://www.nj.gov/state/elections/assets/pdf/election-results/1998/1998-gen-elect-results-us--house.pdf"
VOLUMES = {
    year: (f"https://archive.org/download/{item}/{item}.pdf", f"https://archive.org/details/{item}", day, pages)
    for year, (item, day, pages) in ITEMS.items()
} | {
    1996: (NJ96, NJ96, date(1996, 11, 5), tuple(range(1, 14))),
    1998: (NJ98, NJ98, date(1998, 11, 3), tuple(range(1, 15))),
}
TITLES = {
    1996: "Official List: General Election Returns for the Office of House of Representatives, November 5, 1996",
    1998: "Official List: Candidate Returns for House of Representatives, November 1998 General Election",
}
VERTICAL = {1998}
KEYS = {
    (1988, 103): "1988-11-08:NJ:house:3:special",
    (1990, 101): "1990-11-06:NJ:house:1:special",
}
ERRATA = {(1982, 6)}
REVISION = 1
RENAMED: str | None = None
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 190, 230)


def fetch() -> None:
    house_books.fetch(sys.modules[__name__])


def _tables(path: Path) -> list[dict[str, object]]:
    tables: list[dict[str, object]] = []
    page = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@"):
            page = int(line[1:])
            continue
        head, rest = (part.strip() for part in line.split("|", 1))
        if head.isdigit():
            tables.append(
                {"page": page, "district": int(head), "counties": [c.strip() for c in rest.split(",")], "rows": []}
            )
            continue
        votes = [int(cell) for cell in rest.split()]
        if len(votes) != len(tables[-1]["counties"]) + 1:
            raise ValueError(f"{path.name}: {line}")
        tables[-1]["rows"].append((head, votes))
    return tables


def parse() -> None:
    parsed = DATA / "parsed" / SOURCE
    rows: list[dict[str, object]] = []
    failed: list[str] = []
    for year, (_, _, _, pages) in VOLUMES.items():
        first = _tables(parsed / "model" / f"{year}.txt")
        second = _tables(parsed / "model" / f"{year}.v2.txt")
        if len(first) != len(second):
            raise ValueError(f"{year}: the two reads have different tables")
        for table, again in zip(first, second, strict=True):
            if (table["page"], table["district"], table["counties"], [n for n, _ in table["rows"]]) != (
                again["page"],
                again["district"],
                again["counties"],
                [n for n, _ in again["rows"]],
            ):
                raise ValueError(f"{year} district {table['district']}: the two reads lay out different tables")
            for (name, votes), (_, other) in zip(table["rows"], again["rows"], strict=True):
                for k, (v, w) in enumerate(zip(votes, other, strict=True)):
                    if v != w:
                        failed.append(f"{year} district {table['district']} {name} cell {k}: {v} or {w}")
                if (year, table["district"]) not in ERRATA and sum(votes[:-1]) != votes[-1]:
                    failed.append(f"{year} district {table['district']} {name}: {sum(votes[:-1])} against {votes[-1]}")
        if failed:
            continue
        book = house_books._artifact(sys.modules[__name__], year)
        for page in pages:
            tokens: list[tuple[str, tuple[int, int, int, int]]] = []
            words: list[tuple[str, tuple[int, int, int, int]]] = []
            for psm in ("6", "11"):
                for threshold in (None, *THRESHOLDS):
                    cache = (
                        parsed
                        / "ocr"
                        / f"{book.sha256}-{page}-300-{psm}{'' if threshold is None else f'-{threshold}'}.tsv"
                    )
                    if not cache.exists():
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        with tempfile.TemporaryDirectory() as folder:
                            image = Path(folder) / "page.png"
                            house_books._render(book, page, 300, image, False)
                            if threshold is not None:
                                with Image.open(image) as rendered:
                                    rendered.point(lambda v, cut=threshold: 255 if v > cut else 0).save(image)
                            cache.write_text(
                                subprocess.run(
                                    ["tesseract", str(image), "stdout", "--psm", psm, "tsv"],
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                ).stdout
                            )
                    for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
                        if row["level"] != "5" or not row["text"].strip():
                            continue
                        left, top = int(row["left"]), int(row["top"])
                        box = (left, top, left + int(row["width"]), top + int(row["height"]))
                        if plain := house_books._plain(row["text"]):
                            words.append((plain, box))
                        if re.search(r"\d", row["text"]):
                            for digits in {
                                re.sub(r"\D", "", row["text"]),
                                re.sub(r"\D", "", row["text"].translate(scan.CONFUSABLE)),
                            }:
                                tokens.append((digits, box))
            boxes: dict[str, list[tuple[int, int, int, int]]] = {}
            for digits, box in sorted(tokens, key=lambda token: token[1][2] - token[1][0]):
                merged = boxes.setdefault(digits, [])
                if not any(abs(box[0] - b[0]) < 15 and abs(box[1] - b[1]) < 15 for b in merged):
                    merged.append(box)
            offsets: list[float] = []
            shifts: list[float] = []
            height_: list[float] = []
            digit_: list[float] = []
            for table in [t for t in first if t["page"] == page]:
                counties = [*table["counties"], "Total"]
                offset = sum(len(t["rows"]) for t in first[: first.index(table)] if t["district"] == table["district"])
                cells = [(i, k, v) for i, (_, votes) in enumerate(table["rows"]) for k, v in enumerate(votes)]
                anchors = {(i, k): boxes[str(v)][0] for i, k, v in cells if len(boxes.get(str(v), [])) == 1}
                if year in VERTICAL:
                    if not anchors:
                        raise ValueError(f"{year} page {page} district {table['district']}: no cell found on the page")
                    right = statistics.median(b[2] for b in anchors.values())
                    kept = sorted((c, b) for c, b in anchors.items() if abs(b[2] - right) < 40)
                    kept = [
                        (c, b)
                        for n, (c, b) in enumerate(kept)
                        if all(p[1] < b[1] for _, p in kept[:n]) and all(b[1] < q[1] for _, q in kept[n + 1 :])
                    ]
                    order = {(i, k): n for n, (i, k, _) in enumerate(cells)}
                    height_.extend(b[3] - b[1] for _, b in kept)
                    digit_.extend((b[2] - b[0]) / len(f"{table['rows'][i][1][k]:,}") for (i, k), b in kept)
                    height, digit = statistics.median(height_), statistics.median(digit_)
                    for i, k, v in cells:
                        if k == len(counties) - 1:
                            continue
                        before = [(c, b) for c, b in kept if c < (i, k)]
                        after = [(c, b) for c, b in kept if c > (i, k)]
                        near = [
                            b
                            for b in boxes.get(str(v), [])
                            if abs(b[2] - right) < 40
                            and (not before or before[-1][1][3] <= b[1])
                            and (not after or b[3] <= after[0][1][1])
                        ]
                        if dict(kept).get((i, k)) is not None:
                            box, how = dict(kept)[(i, k)], "unique"
                        elif len(near) == 1:
                            box, how = near[0], "aligned"
                        elif before and after:
                            (low, top_), (high, bottom) = before[-1], after[0]
                            middle = (top_[1] + top_[3]) / 2 + ((bottom[1] + bottom[3]) - (top_[1] + top_[3])) / 2 * (
                                order[(i, k)] - order[low]
                            ) / (order[high] - order[low])
                            box, how = (
                                (
                                    round(right - digit * len(f"{v:,}")),
                                    round(middle - height / 2),
                                    round(right),
                                    round(middle + height / 2),
                                ),
                                "placed",
                            )
                        else:
                            failed.append(
                                f"{year} page {page} district {table['district']} {table['rows'][i][0]} {counties[k]}: no box"
                            )
                            continue
                        rows.append(
                            {
                                "year": year,
                                "page": page,
                                "district": table["district"],
                                "county": counties[k],
                                "column": i + offset,
                                "label": re.sub(
                                    r",? (jr|sr|iii|ii)\.?$", "", table["rows"][i][0], flags=re.IGNORECASE
                                ).split()[-1],
                                "votes": v,
                                "box": list(box),
                                "located": how,
                            }
                        )
                    continue
                for i, (_, votes) in enumerate(table["rows"]):
                    for v in {v for v in votes if votes.count(v) > 1}:
                        found = boxes.get(str(v), [])
                        if len(found) == votes.count(v) and max(b[1] for b in found) - min(b[1] for b in found) < 15:
                            spots = [k for k, w in enumerate(votes) if w == v]
                            for k, b in zip(spots, sorted(found), strict=True):
                                anchors[(i, k)] = b
                for _ in range(2):
                    if not anchors:
                        break
                    ys = {
                        i: statistics.median((b[1] + b[3]) / 2 for (ii, _), b in anchors.items() if ii == i)
                        for i in {i for i, _ in anchors}
                    }
                    xs = {
                        k: statistics.median(b[2] for (_, kk), b in anchors.items() if kk == k)
                        for k in {k for _, k in anchors}
                    }
                    anchors = {
                        (i, k): b
                        for (i, k), b in anchors.items()
                        if abs((b[1] + b[3]) / 2 - ys[i]) < 15 and abs(b[2] - xs[k]) < 40
                    }
                if not anchors:
                    raise ValueError(f"{year} page {page} district {table['district']}: no cell found on the page")
                ys = {
                    i: statistics.median((b[1] + b[3]) / 2 for (ii, _), b in anchors.items() if ii == i)
                    for i in {i for i, _ in anchors}
                }
                xs = {
                    k: statistics.median(b[2] for (_, kk), b in anchors.items() if kk == k)
                    for k in {k for _, k in anchors}
                }
                top = min(ys.values())
                for i, (name, _) in enumerate(table["rows"]):
                    surname = house_books._plain(
                        re.sub(r",? (jr|sr|iii|ii)\.?$", "", name, flags=re.IGNORECASE).split()[-1]
                    )
                    spots = [w for plain, w in words if plain == surname and w[2] < min(xs.values())]
                    if i in ys and spots:
                        offsets.append(
                            ys[i]
                            - (
                                min(spots, key=lambda w: abs(ys[i] - w[1]))[1]
                                + min(spots, key=lambda w: abs(ys[i] - w[1]))[3]
                            )
                            / 2
                        )
                for i, (name, _) in enumerate(table["rows"]):
                    if i in ys:
                        continue
                    surname = house_books._plain(
                        re.sub(r",? (jr|sr|iii|ii)\.?$", "", name, flags=re.IGNORECASE).split()[-1]
                    )
                    spots = [w for plain, w in words if plain == surname and w[2] < min(xs.values())]
                    if spots and offsets:
                        guess = (
                            top
                            + (i - min(ys))
                            * statistics.median((ys[b] - ys[a]) / (b - a) for a, b in zip(sorted(ys), sorted(ys)[1:]))
                            if len(ys) > 1
                            else top
                        )
                        spot = min(spots, key=lambda w: abs((w[1] + w[3]) / 2 + statistics.median(offsets) - guess))
                        ys[i] = (spot[1] + spot[3]) / 2 + statistics.median(offsets)
                    elif len(ys) > 1:
                        step = statistics.median((ys[b] - ys[a]) / (b - a) for a, b in zip(sorted(ys), sorted(ys)[1:]))
                        near = min(ys, key=lambda k: abs(k - i))
                        ys[i] = ys[near] + step * (i - near)
                for k, county in enumerate(counties):
                    head = house_books._plain(county.split()[0])
                    spots = [w for plain, w in words if plain == head and w[3] < top]
                    if not spots:
                        continue
                    spot = max(spots, key=lambda w: w[1])
                    if k in xs:
                        shifts.append(xs[k] - (spot[0] + spot[2]) / 2)
                    elif shifts:
                        xs[k] = (spot[0] + spot[2]) / 2 + statistics.median(shifts)
                height_.extend(b[3] - b[1] for b in anchors.values())
                digit_.extend((b[2] - b[0]) / len(f"{table['rows'][i][1][k]:,}") for (i, k), b in anchors.items())
                height, digit = statistics.median(height_), statistics.median(digit_)
                for i, k, v in cells:
                    if k == len(counties) - 1:
                        continue
                    if (i, k) in anchors:
                        box, how = anchors[(i, k)], "unique"
                    elif i in ys and k in xs:
                        near = [
                            b
                            for b in boxes.get(str(v), [])
                            if abs((b[1] + b[3]) / 2 - ys[i]) < 15 and abs(b[2] - xs[k]) < 40
                        ]
                        if near:
                            box, how = near[0], "aligned"
                        else:
                            width = digit * len(f"{v:,}")
                            box, how = (
                                (
                                    round(xs[k] - width),
                                    round(ys[i] - height / 2),
                                    round(xs[k]),
                                    round(ys[i] + height / 2),
                                ),
                                "placed",
                            )
                    else:
                        failed.append(
                            f"{year} page {page} district {table['district']} {table['rows'][i][0]} {counties[k]}: no box"
                        )
                        continue
                    rows.append(
                        {
                            "year": year,
                            "page": page,
                            "district": table["district"],
                            "county": counties[k],
                            "column": i + offset,
                            "label": re.sub(
                                r",? (jr|sr|iii|ii)\.?$", "", table["rows"][i][0], flags=re.IGNORECASE
                            ).split()[-1],
                            "votes": v,
                            "box": list(box),
                            "located": how,
                        }
                    )
    for line in failed:
        print(line)
    if failed:
        raise ValueError(f"{len(failed)} cells or sums do not reconcile")
    frame = pl.DataFrame(rows)
    print(frame.group_by("year", "located").len().sort("year", "located"))
    frame.write_parquet(parsed / "values.parquet")


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
