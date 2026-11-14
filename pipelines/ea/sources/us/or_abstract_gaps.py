import difflib
import re
import struct
import subprocess
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl
from PIL import Image, ImageOps

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.or_pri_abstract import NUMBER, lines_of
from ea.store import DATA, connect

SOURCE = "us.or_abstract_gaps"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
INBOX = DATA / "inbox" / "manual"
PUBLISHER = "Oregon Secretary of State"
FILES = {
    2002: ("2002 Primary Election Official Results.PDF", 6873551, date(2002, 5, 21), False, "Primary Election", None),
    2008: ("2008 General Election Official Results.PDF", 6873598, date(2008, 11, 4), False, "General Election", {3}),
    2010: ("2010 Primary Election Official Results.PDF", 6873663, date(2010, 5, 18), False, "Primary Election", None),
    2012: (
        "2012 Special General Election Official Results.PDF",
        6873732,
        date(2012, 1, 31),
        True,
        "Special Election",
        None,
    ),
    2024: (
        "2024 November General Election Official Results(2).PDF",
        13735458,
        date(2024, 11, 5),
        False,
        "General Election",
        {5},
    ),
}
SCANNED = {
    1978: ("1978 General.PDF", 8411046, date(1978, 11, 7), False, "General Election", {8, 9, 10}),
}
NAMES = {
    ("senate", None): ["COOK, VERN", "HATFIELD, MARK O."],
    ("house", 1): ["AUCOIN, LES", "BUNICK, NICK"],
    ("house", 2): ["HICKS, TERRY L.", "ULLMAN, AL"],
    ("house", 3): ["DUNCAN, ROBERT B.", "SIMON, MARTIN"],
    ("house", 4): ["LAUSMANN, JERRY S.", "WEAVER, JIM"],
    ("governor", None): ["ATIYEH, VICTOR", "STRAUB, BOB"],
}
FIXED = {
    (("senate", None), 0, "harney"): "1,439",
    (("senate", None), 1, "hoodriver"): "3,463",
    (("senate", None), 2, "umatilla"): "11",
    (("house", 1), 1, "columbia"): "3,728",
    (("house", 2), 0, "grant"): "778",
    (("house", 2), 0, "union"): "2,553",
    (("house", 2), 1, "jefferson"): "2,235",
    (("house", 2), 1, "sherman"): "734",
    (("house", 3), 0, "multnomah"): "132,265",
    (("house", 4), 0, "linn"): "5,466",
    (("house", 4), 1, "benton"): "488",
    (("governor", None), 0, "josephine"): "13,515",
    (("governor", None), 0, "malheur"): "5,546",
    (("governor", None), 2, "union"): "11",
}
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz "
REGIONS = [
    (8, ("senate", None), (60, 170), [(540, 655), (685, 790), (830, 890)], (263, 875)),
    (8, ("house", 1), (1030, 1135), [(1520, 1610), (1665, 1750), (1805, 1865)], (318, 530)),
    (8, ("house", 2), (1035, 1130), [(1530, 1610), (1660, 1745), (1810, 1865)], (680, 1080)),
    (9, ("house", 3), (30, 125), [(525, 595), (665, 730), (795, 855)], (228, 290)),
    (9, ("house", 4), (30, 125), [(525, 595), (665, 730), (795, 855)], (428, 590)),
    (10, ("governor", None), (70, 170), [(565, 645), (685, 785), (825, 890)], (300, 1025)),
]
ONLY = {2008: 1, 2024: 5}
ALL = {**FILES, **SCANNED}
TITLE = re.compile(
    r"(UNITED STATES SENATOR|REPRESENTATIVE IN CONGRESS, (\d+)(?:ST|ND|RD|TH) DISTRICT)(?: (DEMOCRAT|REPUBLICAN))?"
)
PARTY = re.compile(r"(Democrat|Republican)", re.IGNORECASE)
TAG = re.compile(r"\s*\([A-Z]+\)$")


def url(year: int) -> str:
    return f"https://records.sos.state.or.us/ORSOSWebDrawer/Record/{ALL[year][1]}/File/document"


def fetch() -> None:
    for year, (name, *_) in ALL.items():
        try:
            raw.cached(SOURCE, url(year))
        except LookupError:
            raw.store(
                SOURCE,
                url(year),
                (INBOX / ("MF-667" if year in SCANNED else "MF-032") / name).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def ocr(image: Image.Image, whitelist: str, votes: int = 1) -> str:
    seen: list[str] = []
    for factor, mode in [(3, "7"), (4, "7"), (3, "8"), (4, "8"), (5, "7"), (2, "7")][:votes]:
        big = ImageOps.expand(
            image.resize((image.width * factor, image.height * factor), Image.LANCZOS), border=20, fill=255
        )
        with tempfile.NamedTemporaryFile(suffix=".png") as handle:
            big.save(handle.name)
            text = subprocess.run(
                ["tesseract", handle.name, "-", "--psm", mode, "-c", f"tessedit_char_whitelist={whitelist}"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        seen.append(text.strip(","))
    return max(seen, key=lambda t: (seen.count(t), bool(re.fullmatch(r"\d{1,3}(,\d{3})*", t))))


def bands(gray: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> list[tuple[int, int, int, int]]:
    ink = gray[y0:y1, x0:x1] < 150
    rows = ink.sum(axis=1) > 0
    found: list[tuple[int, int, int, int]] = []
    start = None
    for y, on in enumerate([*rows, False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            columns = np.nonzero(ink[start:y].sum(axis=0))[0]
            box = (x0 + int(columns[0]), y0 + start, x0 + int(columns[-1]) + 1, y0 + y)
            if y - start <= 4 and columns[-1] - columns[0] >= 21:
                start = None
                continue
            if found and box[1] - found[-1][3] <= 1 and min(y - start, found[-1][3] - found[-1][1]) < 5:
                last = found.pop()
                box = (min(box[0], last[0]), last[1], max(box[2], last[2]), box[3])
            found.append(box)
            start = None
    return found


def read_scanned(year: int, codes: dict[str, str]) -> list[dict[str, object]]:
    _, _, day, special, _, _ = SCANNED[year]
    pdf = raw.cached(SOURCE, url(year)).path
    counties = {name: code for name, code in codes.items()}
    out: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for number, key, label, columns, (top, bottom) in REGIONS:
            image = Path(tmp) / f"p{number}"
            if not image.with_suffix(".png").exists():
                subprocess.run(
                    ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf), str(image)],
                    check=True,
                )
            picture = Image.open(image.with_suffix(".png")).convert("L")
            gray = np.asarray(picture)
            width, height = picture.size
            scale = width / 2000
            top, bottom = round(top * scale), round(bottom * scale)
            names = []
            for box in bands(gray, round(label[0] * scale), round(label[1] * scale), top - round(12 * scale), bottom):
                if box[2] - box[0] < 30:
                    continue
                strokes = (gray[box[1] : box[3], box[0] : box[2]] < 150).sum(axis=0) >= 4
                end = box[0] + int(np.nonzero(strokes)[0][-1]) + 3
                text = ocr(picture.crop((box[0] - 2, box[1] - 2, end, box[3] + 2)), LETTERS)
                names.append(difflib.get_close_matches(re.sub(r"[^a-z]", "", text.lower()), list(counties), 1, 0.6))
            cells = [bands(gray, round(a * scale), round(b * scale), top, bottom) for a, b in columns]
            total = [column.pop() for column in cells]
            printed = [int(ocr(picture.crop(box), "0123456789,").replace(",", "") or 0) for box in total]
            names = [n[0] if n else None for n in names[:-1]]
            while names and names[0] is None:
                names.pop(0)
            if None in names:
                raise ValueError(f"{key}: unread county among {names}")
            if any(len(column) != len(names) for column in cells):
                raise ValueError(f"{key}: {len(names)} counties against {[len(c) for c in cells]} rows")
            candidates = NAMES[key]
            read = [
                [ocr(picture.crop((b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2)), "0123456789,", 6) for b in column]
                for column in cells
            ]
            read = [
                [FIXED.get((key, index, county), text) for county, text in zip(names, column, strict=True)]
                for index, column in enumerate(read)
            ]
            for index, want in enumerate(printed):
                got = sum(int(v.replace(",", "") or 0) for v in read[index])
                if got != want:
                    raise ValueError(f"{key} column {index}: {got} against {want}")
            for index, column in enumerate(cells):
                for county, box, text in zip(names, column, read[index], strict=True):
                    if not text:
                        continue
                    out.append(
                        {
                            "year": year,
                            "chamber": key[0],
                            "day": day,
                            "special": special,
                            "state": "OR",
                            "district": key[1],
                            "code": counties[county],
                            "candidate": "WRITE-INS" if index == len(candidates) else candidates[index],
                            "measure": 1,
                            "writein": index == len(candidates),
                            "votes": int(text.replace(",", "")),
                            "kind": "county",
                            "source": "OR",
                            "file": 0,
                            "page": number,
                            "x0": box[0] / width,
                            "y0": box[1] / height,
                            "x1": box[2] / width,
                            "y1": box[3] / height,
                        }
                    )
    return out


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '41%%'"""
            ).fetchall()
        }
    out: list[dict[str, object]] = []
    for year in SCANNED:
        out.extend(read_scanned(year, codes))
    for year, (_, _, day, special, stage, pages) in FILES.items():
        primary = stage == "Primary Election"
        office = district = party = None
        collecting = after_total = False
        header: list[list[tuple[str, float, float]]] = []
        table: list[tuple[str, int, float, float, list[tuple[int, float, float, float, float]]]] = []
        rows: list[tuple[int, float, float, list[tuple[str, float, float, float, float]]]] = []
        for line in lines_of(raw.cached(SOURCE, url(year)).path):
            if (
                rows
                and all(NUMBER.fullmatch(w[0]) for w in line[3])
                and not any(NUMBER.fullmatch(w[0]) for w in rows[-1][3])
            ):
                rows[-1] = (line[0], line[1], line[2], sorted([*rows[-1][3], *line[3]], key=lambda w: w[1]))
            elif (
                rows
                and all(NUMBER.fullmatch(w[0]) for w in rows[-1][3])
                and not any(NUMBER.fullmatch(w[0]) for w in line[3])
            ):
                rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2], sorted([*rows[-1][3], *line[3]], key=lambda w: w[1]))
            else:
                rows.append(line)
        for page, width, height, row in rows:
            if pages is not None and page not in pages:
                continue
            text = " ".join(w[0] for w in row)
            if not collecting:
                found = TITLE.fullmatch(text.upper())
                lone = PARTY.fullmatch(text)
                ordinal = re.fullmatch(r"(\d+)(?:st|nd|rd|th) District", text)
                start = False
                if found:
                    office = "senate" if found[1].startswith("UNITED") else "house"
                    district = int(found[2]) if found[2] else None
                    party = found[3].lower().replace("democrat", "democratic") if found[3] else None
                    start = not primary or party is not None
                elif text == "US Representative":
                    office, district, party = "house", None, None
                elif ordinal and office == "house" and district is None:
                    district = int(ordinal[1])
                    start = not primary
                elif lone and office is not None and (after_total or party is None):
                    party = lone[1].lower().replace("democrat", "democratic")
                    start = True
                elif after_total:
                    office = None
                after_total = False
                if start:
                    collecting, header, table = True, [], []
                continue
            if row[0][0].lower() == "total":
                values = [w for w in row if NUMBER.fullmatch(w[0])]
                printed = [int(w[0].replace(",", "")) for w in values]
                for index, want in enumerate(printed):
                    got = sum(cells[index][0] for _, _, _, _, cells in table)
                    if got != want:
                        raise ValueError(f"{year} {office} {district} {party} column {index}: {got} against {want}")
                slots: list[list[str]] = [[] for _ in printed[:-1]]
                for line in header:
                    for text_, center, right in line:
                        slot = min(
                            range(len(slots)),
                            key=lambda i: min(
                                abs((values[i][1] + values[i][3]) / 2 - center), abs(values[i][3] - right)
                            ),
                        )
                        slots[slot].append(text_)
                names = []
                for index, found_words in enumerate(slots):
                    if not found_words:
                        raise ValueError(f"{year} {office} {district} {party}: no header for column {index}")
                    surname = found_words[0].strip("*")
                    given = TAG.sub("", " ".join(found_words[1:]))
                    names.append(f"{surname}, {given}".upper())
                for county, pg, w, h, cells in table if ONLY.get(year, district) == district else []:
                    for index, (value, x0, y0, x1, y1) in enumerate(cells):
                        misc = index == len(names)
                        out.append(
                            {
                                "year": year,
                                "chamber": f"{office}:{party}" if primary else office,
                                "day": day,
                                "special": special,
                                "state": "OR",
                                "district": district,
                                "code": codes[re.sub(r"[^a-z]", "", county.lower())],
                                "candidate": "WRITE-INS" if misc else names[index],
                                "measure": 1,
                                "writein": misc,
                                "votes": value,
                                "kind": "county",
                                "source": "OR",
                                "file": 0,
                                "page": pg,
                                "x0": x0 / w,
                                "y0": y0 / h,
                                "x1": x1 / w,
                                "y1": y1 / h,
                            }
                        )
                collecting, after_total = False, True
                continue
            numbers = [w for w in row if NUMBER.fullmatch(w[0])]
            if not numbers:
                line: list[list[tuple[str, float, float]]] = []
                for w in row:
                    if w[0].lower() in {"county", "misc.", "misc"}:
                        continue
                    if line and w[1] - line[-1][-1][2] < 6:
                        line[-1].append((w[0], w[1], w[3]))
                    else:
                        line.append([(w[0], w[1], w[3])])
                header.append([(" ".join(t[0] for t in g), (g[0][1] + g[-1][2]) / 2, g[-1][2]) for g in line])
                continue
            table.append(
                (
                    " ".join(w[0] for w in row if not NUMBER.fullmatch(w[0])),
                    page,
                    width,
                    height,
                    [(int(w[0].replace(",", "")), w[1], w[2], w[3], w[4]) for w in numbers],
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(out, infer_schema_length=None).with_columns(
        year=pl.col("year").cast(pl.Int32),
        district=pl.col("district").cast(pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon's 2002 and 2010 U.S. Senate and House primaries, 1978 general governor, U.S. Senate, and House, 2008 and 2024 general House seats, and the 2012 special, by county from the Secretary of State's official results",
            polity="US-OR",
        )
        sources: dict[str, object] = {}
        pages: dict[tuple[int, int], str] = {}
        for year, (_, _, day, _, stage, _) in ALL.items():
            title = f"{day:%B %-d, %Y} {stage} Official Results"
            source = cs.source(PUBLISHER, title)
            sources[f"OR{year}"] = source
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url(year),
                license="Public record",
            )
            pdf = raw.cached(SOURCE, url(year))
            artifact = cs.artifact(pdf, source)
            PAGES.mkdir(parents=True, exist_ok=True)
            for number in sorted(set(frame.filter(pl.col("year") == year)["page"])):
                image = PAGES / f"{pdf.sha256}-{number}.png"
                if not image.exists():
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(pdf.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                page = cs.page(artifact, number)
                pages[(year, number)] = str(page)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        frame = frame.with_columns(
            page_id=pl.struct("year", "page").map_elements(
                lambda s: pages[(s["year"], s["page"])], return_dtype=pl.String
            ),
            source=pl.format("OR{}", pl.col("year")),
        ).with_columns(page=pl.lit(1))
        medsl.attach(cs, conn, frame, sources, strict=True)
        return cs.write()
