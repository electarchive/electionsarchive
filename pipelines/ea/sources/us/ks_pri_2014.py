import difflib
import io
import itertools
import re
import struct
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from uuid import UUID

import numpy as np
import polars as pl
from PIL import Image, ImageFilter, ImageOps

from ea import raw, scan
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_pri_2014"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
URL = "https://sos.ks.gov/elections/14elec/2014_Official_Primary_Election_Results_by_county.pdf"
PUBLISHER = "Kansas Secretary of State"
TITLE = "2014 primary election official results by county"
LANDING = "https://sos.ks.gov/elections/election-results.html"
DAY = date(2014, 8, 5)
WIDTH, HEIGHT = 2550, 3300
DISTRICTS = {
    7: (1, ["James E. Sherow", "Bryan R. Whitney", "Tim Huelskamp", "Alan LaPolice"], "DDRR"),
    8: (1, ["James E. Sherow", "Bryan R. Whitney", "Tim Huelskamp", "Alan LaPolice"], "DDRR"),
    9: (2, ["Margie Wakefield", "Lynn Jenkins", "Joshua Joel Tucker"], "DRR"),
    10: (3, ["Kelly Kultala", "Reginald Marselus", "Kevin Yoder"], "DDR"),
    11: (4, ["Perry L. Schuckman", "Mike Pompeo", "Todd Tiahrt"], "DRR"),
}
PASSES = ((2, "7"), (2, "8"), (2, "13"), (3, "7"), (3, "8"), (4, "7"), (4, "8"))
RULES = [949, 1247, 1544, 1840, 2137]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def ocr() -> None:
    pdf = raw.cached(SOURCE, URL).path
    with connect() as conn:
        counties = {
            name: code
            for code, name in conn.execute(
                """select u.code, upper(n.text) from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    totals: dict[tuple[int, int], int] = {}
    seen: dict[int, set[str]] = {1: set(), 2: set(), 3: set(), 4: set()}
    with tempfile.TemporaryDirectory() as folder:
        for page, (district, names, parties) in DISTRICTS.items():
            sheet = Image.open(scan.rendered(pdf, page, Path(folder))).convert("L")
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "600",
                    "-gray",
                    "-png",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    str(pdf),
                    f"{folder}/fine",
                ],
                check=True,
            )
            fine_sheet = Image.open(f"{folder}/fine.png").convert("L")
            words = scan.words(pdf, page, PARSED / f"{page}.tsv")
            anchors = []
            for line in scan.lines(words, scan.skew(words)):
                label = " ".join(w[0] for w in line if w[1][2] < 900).upper().strip(".,: ")
                if difflib.get_close_matches(label, counties, n=1, cutoff=0.9):
                    first = [w for w in line if w[1][2] < 900]
                    anchors.append(((min(w[1][1] for w in first) + max(w[1][3] for w in first)) / 2, label))
            origin = anchors[0][0]
            pitch = float(
                np.median([(b[0] - a[0]) / round((b[0] - a[0]) / 60.7) for a, b in itertools.pairwise(anchors)])
            )
            centers = [origin + pitch * k for k in range(int((3060 - origin) / pitch) + 1)] + [3135.0]
            for center in centers:
                zone = np.asarray(sheet.crop((957, int(center) - 32, 2130, int(center) + 32))) < 170
                zone[zone.mean(axis=1) > 0.2] = False
                profile = np.flatnonzero(zone.sum(axis=1) > 2)
                if len(profile):
                    runs = np.split(profile, np.flatnonzero(np.diff(profile) > 3) + 1)
                    near = min(runs, key=lambda run: abs((run[0] + run[-1]) / 2 - 32))
                    if len(near) > 8:
                        center = int(center) - 32 + (near[0] + near[-1]) / 2
                    else:
                        print(f"page {page}: no digits at {center:.0f} (run {len(near)})")
                        continue
                else:
                    print(f"page {page}: no ink at {center:.0f}")
                    continue
                strip = np.asarray(sheet.crop((140, int(center) - 24, 780, int(center) + 24))) < 170
                strip[strip.mean(axis=1) > 0.3] = False
                letters = np.flatnonzero(strip.any(axis=1))
                label = ""
                if len(letters):
                    runs = np.split(letters, np.flatnonzero(np.diff(letters) > 3) + 1)
                    keep = max(runs, key=len)
                    strip[: keep[0]] = False
                    strip[keep[-1] + 1 :] = False
                    ys, xs = np.nonzero(strip)
                    cut = Image.fromarray(np.where(strip, 0, 255).astype("uint8")).crop(
                        (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
                    )
                    cut = ImageOps.expand(cut, 30, 255)
                    for scale in (3, 2, 4):
                        enlarged = cut.resize((cut.width * scale, cut.height * scale), Image.Resampling.LANCZOS)
                        buffer = io.BytesIO()
                        enlarged.save(buffer, "PNG")
                        text = re.sub(
                            r"[^A-Z ]",
                            "",
                            subprocess.run(
                                ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng"],
                                input=buffer.getvalue(),
                                capture_output=True,
                                check=True,
                            )
                            .stdout.decode()
                            .upper(),
                        ).strip()
                        label = text
                        if difflib.get_close_matches(text, [*counties, "TOTAL"], n=1, cutoff=0.75):
                            break
                if len(label) < 3:
                    print(f"page {page}: short label {label!r} at {center:.0f}")
                    continue
                if difflib.SequenceMatcher(None, label, "TOTAL").ratio() >= 0.8:
                    label = "TOTAL"
                elif not (hit := difflib.get_close_matches(label, counties, n=1, cutoff=0.75)):
                    print(f"page {page}: {label!r} at {center:.0f} matches no county")
                    continue
                elif hit[0] in seen[district]:
                    print(f"page {page}: {label!r} repeats {hit[0]}")
                    continue
                else:
                    seen[district].add(hit[0])
                top, bottom = center - 14, center + 14
                pending: list[dict[str, object]] = []
                pending_totals: dict[int, int] = {}
                for index in range(len(names)):
                    box = (RULES[index] + 8, top - 14, RULES[index + 1] - 5, bottom + 10)
                    ink = np.asarray(sheet.crop(box)) < 170
                    ink[ink.mean(axis=1) > 0.2] = False
                    density = ink.mean(axis=0)
                    ink[:, density > 0.7] = False
                    for edge in (slice(0, 14), slice(-14, None)):
                        ink[:, edge][:, density[edge] > 0.3] = False
                    present = np.flatnonzero(ink.any(axis=1))
                    if len(present):
                        runs = np.split(present, np.flatnonzero(np.diff(present) > 3) + 1)
                        keep = max(runs, key=len)
                        ink[: keep[0]] = False
                        ink[keep[-1] + 1 :] = False
                    ys, xs = np.nonzero(ink)
                    if not len(xs):
                        print(f"page {page}: {label} column {index} is empty")
                        if label != "TOTAL":
                            seen[district].discard(hit[0])
                        pending, pending_totals = [], {}
                        break
                    tight = (box[0] + xs.min(), box[1] + ys.min(), box[0] + xs.max() + 1, box[1] + ys.max() + 1)
                    mask = Image.fromarray((ink * 255).astype("uint8")).crop(
                        (tight[0] - box[0], tight[1] - box[1], tight[2] - box[0], tight[3] - box[1])
                    )
                    mask = mask.resize((mask.width * 2, mask.height * 2)).filter(ImageFilter.MaxFilter(7))
                    fine = fine_sheet.crop((tight[0] * 2, tight[1] * 2, tight[2] * 2, tight[3] * 2))
                    clean = Image.composite(fine, Image.new("L", fine.size, 255), mask)
                    padded = Image.new("L", (clean.width + 120, clean.height + 120), 255)
                    padded.paste(clean, (60, 60))
                    reads = []
                    for scale, psm in PASSES:
                        if psm == "10" and padded.width > 200:
                            continue
                        enlarged = padded.resize(
                            (padded.width * scale // 2, padded.height * scale // 2), Image.Resampling.LANCZOS
                        )
                        buffer = io.BytesIO()
                        enlarged.save(buffer, "PNG")
                        reads.append(
                            subprocess.run(
                                [
                                    "tesseract",
                                    "stdin",
                                    "stdout",
                                    "--psm",
                                    psm,
                                    "-l",
                                    "eng",
                                    "-c",
                                    "tessedit_char_whitelist=0123456789,",
                                ],
                                input=buffer.getvalue(),
                                capture_output=True,
                                check=True,
                            )
                            .stdout.decode()
                            .strip()
                        )
                    votes = [value for text in reads for value, weight in scan.numbers(text).items() if weight == 2]
                    printed: list[int] = []
                    votes += printed[:1]
                    value = max(set(votes), key=votes.count) if votes else None
                    if value is None or (votes.count(value) < 2 and (len(set(votes)) > 1 or value > 99)):
                        raise ValueError(
                            f"page {page} {label} column {index}: cell reads {reads}, words read {printed}"
                        )
                    if label == "TOTAL":
                        pending_totals[index] = value
                        continue
                    pending.append(
                        {
                            "district": district,
                            "party": "democratic" if parties[index] == "D" else "republican",
                            "candidate": names[index].upper(),
                            "code": counties[hit[0]],
                            "votes": value,
                            "page": page,
                            "x0": tight[0] / WIDTH,
                            "y0": tight[1] / HEIGHT,
                            "x1": tight[2] / WIDTH,
                            "y1": tight[3] / HEIGHT,
                        }
                    )
                rows.extend(pending)
                for index, value in pending_totals.items():
                    totals[(district, index)] = totals.get((district, index), 0) + value
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "ocr.parquet")
    pl.DataFrame(
        [(d, i, v) for (d, i), v in totals.items()], schema=["district", "index", "printed"], orient="row"
    ).write_parquet(PARSED / "totals.parquet")


TRANSCRIBED = """
1 Barton 225 241 1840 1656
1 Chase 53 28 283 209
1 Cheyenne 33 11 282 123
1 Clark 21 32 245 144
1 Clay 76 14 626 1098
1 Cloud 154 59 467 926
1 Decatur 40 20 301 169
1 Dickinson 252 71 1384 1177
1 Ellis 418 277 1205 1030
1 Ellsworth 117 46 388 408
1 Finney 124 153 1211 1342
1 Ford 170 169 1319 854
1 Geary 355 71 824 537
1 Gove 34 23 371 201
1 Graham 27 20 236 214
1 Grant 27 47 602 347
1 Gray 45 52 451 258
1 Greeley 5 9 210 99
1 Hamilton 14 11 220 107
1 Haskell 14 16 322 169
1 Hodgeman 26 20 241 105
1 Jewell 15 5 191 217
1 Kearny 21 19 409 210
1 Lane 19 11 196 158
1 Lincoln 39 20 267 246
1 Logan 39 20 367 213
1 Lyon 432 240 1070 1196
1 Marion 106 92 1085 740
1 Marshall 128 24 303 216
1 McPherson 322 236 2723 1783
1 Meade 18 34 475 254
1 Mitchell 101 28 368 420
1 Morris 135 21 507 376
1 Morton 17 25 289 130
1 Ness 29 22 254 143
1 Norton 40 17 381 306
1 Osborne 66 19 333 232
1 Ottawa 120 45 654 545
1 Pawnee 86 67 450 321
1 Phillips 38 14 420 534
1 Pottawatomie 307 27 1773 998
1 Rawlins 31 14 445 210
1 Reno 748 806 3508 2312
1 Republic 119 30 363 648
1 Rice 104 96 716 543
1 Riley 1065 86 1875 2409
1 Rooks 35 21 575 358
1 Rush 47 28 322 214
1 Russell 124 62 612 585
1 Saline 980 417 3267 3342
1 Scott 38 23 431 311
1 Seward 58 89 1099 617
1 Sheridan 48 28 221 150
1 Sherman 52 36 541 311
1 Smith 59 22 441 344
1 Stanton 8 10 132 98
1 Stevens 23 29 445 298
1 Thomas 108 44 612 566
1 Trego 50 31 214 164
1 Wabaunsee 140 15 605 443
1 Wallace 8 8 255 76
1 Washington 45 12 469 609
1 Wichita 11 10 156 89
2 Allen 201 1212 476
2 Anderson 152 477 157
2 Atchison 414 847 245
2 Bourbon 424 988 362
2 Brown 141 882 401
2 Cherokee 522 859 469
2 Coffey 107 892 348
2 Crawford 1074 1248 934
2 Doniphan 132 928 258
2 Douglas 4479 4093 2002
2 Franklin 470 2417 988
2 Jackson 282 1025 386
2 Jefferson 350 1186 559
2 Labette 378 953 391
2 Leavenworth 1352 3377 1173
2 Linn 163 767 306
2 Marshall 161 406 134
2 Miami 444 1477 480
2 Montgomery 477 1895 1386
2 Nemaha 191 1029 344
2 Neosho 266 1096 514
2 Osage 358 1126 618
2 Shawnee 5667 11555 5351
2 Wilson 95 816 297
2 Woodson 37 299 101
3 Johnson 8224 4631 43276
3 Miami 131 81 1242
3 Wyandotte 5834 1812 2631
4 Barber 72 488 234
4 Butler 1141 5026 3312
4 Chautauqua 38 305 300
4 Comanche 27 233 96
4 Cowley 720 2045 1513
4 Edwards 52 350 139
4 Elk 87 341 200
4 Greenwood 109 650 339
4 Harper 124 658 314
4 Harvey 624 3039 1816
4 Kingman 191 882 377
4 Kiowa 35 324 158
4 Pawnee 5 34 16
4 Pratt 172 762 610
4 Sedgwick 7474 26292 15411
4 Stafford 66 543 200
4 Sumner 471 1592 942
"""
PRINTED = {
    1: [8209, 4293, 42847, 35108],
    2: [18337, 41850, 18680],
    3: [14189, 6524, 47149],
    4: [11408, 43564, 25977],
}


def parse() -> None:
    with connect() as conn:
        codes = {
            name: code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    read = pl.read_parquet(PARSED / "ocr.parquet")
    names = {number: names for number, names, _ in DISTRICTS.values()}
    rows = []
    for line in TRANSCRIBED.strip().splitlines():
        district, county, *values = line.split()
        for name, value in zip(names[int(district)], values, strict=True):
            rows.append((int(district), name.upper(), codes[county], int(value)))
    typed = pl.DataFrame(rows, schema=["district", "candidate", "code", "typed"], orient="row")
    for district, printed in PRINTED.items():
        for name, total in zip(names[district], printed, strict=True):
            added = typed.filter((pl.col("district") == district) & (pl.col("candidate") == name.upper()))[
                "typed"
            ].sum()
            if added != total:
                raise ValueError(
                    f"district {district} {name}: the typed counties add to {added}, the page prints {total}"
                )
    merged = read.join(typed, on=["district", "candidate", "code"], how="full", validate="1:1")
    if merged["votes"].is_null().any() or merged["typed"].is_null().any():
        raise ValueError(merged.filter(pl.col("votes").is_null() | pl.col("typed").is_null()))
    print(
        merged.filter(pl.col("votes") != pl.col("typed")).height,
        "of",
        merged.height,
        "cells where the OCR and the typed reading disagree",
    )
    merged.with_columns(ocr=pl.col("votes"), votes=pl.col("typed")).drop("typed").write_parquet(
        PARSED / "counties.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    pdf = raw.cached(SOURCE, URL)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's 2014 House primaries by county (Secretary of State)",
            polity="US-KS",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="Public record",
        )
        artifact = cs.artifact(pdf, source)
        pages: dict[int, UUID] = {}
        for number in sorted(frame["page"].unique().to_list()):
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
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
        medsl.attach(
            cs,
            conn,
            frame.with_columns(
                year=pl.lit(2014, pl.Int32),
                chamber=pl.lit("house:") + pl.col("party"),
                day=pl.lit(DAY),
                special=pl.lit(False),
                state=pl.lit("KS"),
                district=pl.col("district").cast(pl.Int16),
                measure=pl.lit(1, pl.Int16),
                writein=pl.lit(False),
                kind=pl.lit("county"),
                source=pl.lit("KS"),
                page_id=pl.col("page").replace_strict({n: str(i) for n, i in pages.items()}, return_dtype=pl.String),
            ),
            {"KS": source},
            strict=True,
        )
        return cs.write()
