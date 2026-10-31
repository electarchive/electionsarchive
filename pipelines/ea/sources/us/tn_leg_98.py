import re
import subprocess
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections, tn_gen_98
from ea.store import DATA, connect

SOURCE = "us.tn_leg_98"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-332"
DAY = date(1998, 11, 3)
PUBLISHER = "Tennessee Secretary of State, Division of Elections"
TITLE = "November 3, 1998 general election results: State Senate and State House of Representatives by county"
LANDING = "https://sos.tn.gov/elections/results"
FILES = {
    "senate": (
        "https://tnelections.tnsosfiles.com/sharetngov/archived/election/results/1998-11/senate.pdf",
        "senate.pdf",
    ),
    "house": (
        "https://tnelections.tnsosfiles.com/sharetngov/archived/election/results/1998-11/house.pdf",
        "house.pdf",
    ),
}
ONLY = {"senate": {11, 15}, "house": set(range(1, 100))}
CITATION = (
    "Source: the Secretary of State's typed 1998 returns (Form SS-3050), whose county rows add up to each printed "
    "district total."
)
HEADING = re.compile(r"^\W*DISTRICT\s+(\d{1,2})\W*$")
CANDIDATE = re.compile(r"^(?:CANDIDATES:\s*)?([123])[.:]\s*(.*)$")
Row = tuple[str, tuple[int, int, int, int]]
MANUAL: dict[tuple[str, int], list[tuple[int, dict[int, str], list[Row]]]] = {
    ("house", 5): [
        (
            23,
            {1: "William Robert Moses", 2: "Bob McKee"},
            [("mcminn", (2016, 6665, 0, 0)), ("monroe", (314, 933, 0, 0))],
        ),
        (24, {1: "Tommy L. Barrett", 2: "Dewayne Bunch"}, [("bradley", (3238, 7022, 0, 2))]),
        (25, {2: "Raymond B. Walker"}, [("cumberland", (0, 6159, 0, 0)), ("rhea", (0, 3109, 0, 3))]),
        (26, {2: "Bobby Wood"}, [("hamilton", (0, 8574, 0, 243))]),
        (27, {2: "Bill McAfee"}, [("hamilton", (0, 8870, 0, 289))]),
        (28, {1: "Tommy F. Brown", 3: "Ronnie Moore"}, [("hamilton", (4408, 0, 991, 42))]),
    ],
    ("house", 18): [
        (92, {1: "Henri E. Brooks"}, [("shelby", (6846, 0, 0, 0))]),
        (93, {1: "Mike Kernell", 2: "Bill Underwood", 3: "Christopher Meredith"}, [("shelby", (5837, 2906, 196, 0))]),
        (94, {2: "Larry Scroggs"}, [("shelby", (0, 13131, 0, 0))]),
        (95, {2: "Curry Todd"}, [("shelby", (0, 9715, 0, 1))]),
        (96, {2: "Joyce B. Hassell"}, [("shelby", (0, 11581, 0, 2))]),
        (97, {2: "Tre Hargett"}, [("shelby", (0, 9581, 0, 1))]),
    ],
}
LAYOUT = {
    ("house", 5): ([871, 1478, 2123, 2724, 3332], 3886, [667, 948, 1230, 1500]),
    ("house", 18): ([915, 1521, 2123, 2726, 3325], 3870, [660, 942, 1223, 1500]),
}
DISAGREE = {("house", 65)}
OVERRIDES: dict[tuple[str, int, str, int], int] = {
    ("house", 38, "scott", 0): 1835,
    ("house", 38, "scott", 1): 1854,
    ("house", 74, "perry", 1): 484,
    ("house", 77, "gibson", 0): 1331,
    ("house", 38, "clay", 3): 0,
}
DPI = 300
HEADINGS: dict[tuple[str, int, str], int] = {
    ("house", 10, "DISTRICT S2"): 52,
    ("house", 16, "DISTRICT 861"): 81,
    ("house", 16, "DISTRICT 63"): 83,
    ("house", 17, "DISTRICT 69"): 89,
}
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})*")


def fetch() -> None:
    for url, file in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE,
                url,
                (INBOX / file).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp((INBOX / file).stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def page_png(chamber: str, page: int) -> Path:
    artifact = raw.cached(SOURCE, FILES[chamber][0])
    target = PAGES / f"{artifact.sha256}-{page}.png"
    if not target.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-f", str(page), "-l", str(page), "-png", "-singlefile"]
            + [str(artifact.path), str(target.with_suffix(""))],
            check=True,
        )
    return target


def digits(image: Path, box: tuple[int, int, int, int]) -> int | None:
    with Image.open(image) as page, tempfile.TemporaryDirectory() as folder:
        left, top, width, height = box
        crop = page.convert("L").crop((left - 25, top - 15, left + width + 25, top + height + 15))
        crop = crop.resize((crop.width * 3, crop.height * 3))
        target = Path(folder) / "c.png"
        crop.save(target)
        for mode in ("8", "7"):
            out = subprocess.run(
                ["tesseract", str(target), "-", "--psm", mode, "-c", "tessedit_char_whitelist=0123456789,"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if NUMBER.fullmatch(out):
                return int(out.replace(",", ""))
    return None


def value(image: Path, word: tuple[int, int, int, int, str]) -> int | None:
    parsed = tn_gen_98.number(word[4].replace("§", "5"))
    if parsed is not None:
        return parsed
    if word[4] in {"i", "l", "I", "|", "!", "j"}:
        return 1
    if len(word[4]) <= 4 and not re.search(r"[1-9]", word[4]):
        return 0
    return digits(image, word[:4])


def lines_of(image: Path) -> list[list[tuple[int, int, int, int, str]]]:
    lines: list[list[tuple[int, int, int, int, str]]] = []
    for word in sorted(
        (w for w in tn_gen_98.words(image) if re.search(r"[A-Za-z0-9)\]}]", w[4])), key=lambda w: w[1] + w[3] / 2
    ):
        if lines and abs((word[1] + word[3] / 2) - (lines[-1][0][1] + lines[-1][0][3] / 2)) < 18:
            lines[-1].append(word)
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w[0])
    return lines


def read_cells(
    image: Path,
    line: list[tuple[int, int, int, int, str]],
    centers: list[float],
    key: tuple[str, int, str],
    named: set[int],
) -> dict[int, tuple[int | None, tuple[int, int, int, int]]]:
    cells: dict[int, tuple[int | None, tuple[int, int, int, int]]] = {}
    for word in line:
        if word[0] + word[2] < centers[0] - 100:
            continue
        column = min(range(4), key=lambda c: abs(word[0] + word[2] / 2 - centers[c]))
        if column in cells:
            continue
        cells[column] = (
            OVERRIDES.get((*key, column), value(image, word)),
            (word[0], word[1], word[0] + word[2], word[1] + word[3]),
        )
    top = min(w[1] for w in line)
    height = max(w[1] + w[3] for w in line) - top
    for column in range(4):
        if column not in cells and column not in named:
            cells[column] = (0, (0, 0, 0, 0))
        elif column not in cells:
            box = (int(centers[column]) - 50, top, 100, height)
            cells[column] = (
                OVERRIDES.get((*key, column), digits(image, box)),
                (box[0], box[1], box[0] + box[2], box[1] + box[3]),
            )
    return cells


class Cursor:
    def __init__(self) -> None:
        self.district = 0
        self.names: dict[int, str] = {}
        self.centers: list[float] = []
        self.last: list[float] = []
        self.block: list[dict[str, object]] = []

    def copy(self) -> Cursor:
        other = Cursor()
        other.district, other.names, other.centers = self.district, dict(self.names), list(self.centers)
        other.last, other.block = list(self.last), list(self.block)
        return other


def read_page(
    chamber: str, page: int, image: Path, size: tuple[int, int], cursor: Cursor, fips: dict[str, str]
) -> tuple[list[dict[str, object]], list[str]]:
    width, height = size
    done: list[dict[str, object]] = []
    problems: list[str] = []
    if (chamber, page) in MANUAL:
        rules, last_row, centers = LAYOUT[(chamber, page)]
        for index, (district, names, rows) in enumerate(MANUAL[(chamber, page)]):
            cursor.district, cursor.names, cursor.centers, cursor.last = district, dict(names), centers, centers
            cursor.block = []
            for position, (label, counts) in enumerate(rows):
                top = rules[index] - 55 - 50 * (len(rows) - 1 - position) if index < len(rules) else last_row
                for column, votes in enumerate(counts):
                    name = names.get(column + 1) if column < 3 else "WRITE-INS"
                    if name is None:
                        continue
                    right = centers[column] + 44
                    left = right - 17 * len(f"{votes:,}") + 3
                    cursor.block.append(
                        {
                            "chamber": chamber,
                            "district": district,
                            "code": fips[label],
                            "column": column,
                            "candidate": name,
                            "votes": votes,
                            "page": page,
                            "x0": (left - 6) / width,
                            "y0": (top - 14) / height,
                            "x1": (right + 6) / width,
                            "y1": (top + 14) / height,
                        }
                    )
            if index < len(rules) and district in ONLY[chamber]:
                done.extend(cursor.block)
                cursor.block = []
        return done, problems
    for line in lines_of(image):
        text = " ".join(w[4] for w in line)
        district = cursor.district
        if (chamber, page, text) in HEADINGS or HEADING.match(text):
            cursor.district = HEADINGS.get((chamber, page, text)) or int(HEADING.match(text)[1])
            cursor.centers, cursor.block = [], []
            if chamber == "house" and cursor.district != district + 1:
                problems.append(f"house page {page}: district {cursor.district} follows {district} ({text})")
        elif re.match(r"^(PAGE|STATE OF|HOUSE OF|NOVEMBER|CONSTITUTIONAL)\b", text):
            continue
        elif re.match(r"^DISTRICT\s+T\w{2}AL", text):
            named = {c - 1 for c in cursor.names} | {3}
            cells = (
                read_cells(image, line, cursor.centers, (chamber, district, "TOTAL"), named) if cursor.centers else {}
            )
            printed = [cells[c][0] if c in cells else None for c in range(4)]
            for row in cursor.block:
                if printed[int(row["column"])] == 0:
                    row["votes"] = 0
            sums = [sum(int(r["votes"]) for r in cursor.block if r["column"] == c) for c in range(4)]
            if sums != printed and (chamber, district) not in DISAGREE:
                where = [
                    (r["code"], r["column"], r["votes"], round(float(r["y0"]) * height))
                    for r in cursor.block
                    if sums[int(r["column"])] != printed[int(r["column"])]
                ]
                problems.append(
                    f"{chamber} {district} page {page}: columns sum to {sums}, printed {printed} ({text}) {where} total y {line[0][1]}"
                )
            if district in ONLY[chamber]:
                done.extend(cursor.block)
            cursor.block = []
        elif (found := CANDIDATE.match(text)) and not cursor.centers:
            label = re.sub(r"\s*\((?:Write|Write-?in\)?)\)?$", "", found[2].strip(), flags=re.IGNORECASE)
            if label:
                cursor.names[int(found[1])] = label
        elif re.match(r"^COUNTY\b", text):
            heads = [w for w in line if w[4] in {"1", "2", "3"}]
            mark = next((w for w in line if w[4].startswith("Write")), None)
            if len(heads) == 3 and mark is not None:
                cursor.centers = [w[0] + w[2] / 2 for w in heads] + [mark[0] + mark[2] / 2 + 30]
                cursor.last = cursor.centers
            elif cursor.last:
                cursor.centers = cursor.last
            else:
                problems.append(f"{chamber} page {page}: header columns {text}")
        elif (
            cursor.centers
            and re.match(r"^\W{0,3}[A-Z][A-Za-z .'-]+", text)
            and not re.search(r"\bRDA\b|\bForm\b|\.frm", text)
        ):
            label = "".join(
                w[4] for w in line if w[0] + w[2] < cursor.centers[0] - 100 and re.search(r"[A-Za-z]", w[4])
            )
            label = re.sub(r"[^a-z]", "", label.lower())
            if not label:
                continue
            if label not in fips:
                problems.append(f"{chamber} page {page} district {district}: unknown county {label!r} in {text}")
                continue
            named = {c - 1 for c in cursor.names} | {3}
            cells = read_cells(image, line, cursor.centers, (chamber, district, label), named)
            for column in range(4):
                votes, box = cells[column]
                name = cursor.names.get(column + 1) if column < 3 else "WRITE-INS"
                if votes is None:
                    problems.append(f"{chamber} {district} page {page} {label} column {column}: unreadable")
                if name is None:
                    if votes:
                        problems.append(f"{chamber} {district} {label}: {votes} votes in unnamed column {column + 1}")
                    continue
                cursor.block.append(
                    {
                        "chamber": chamber,
                        "district": district,
                        "code": fips[label],
                        "column": column,
                        "candidate": name,
                        "votes": votes or 0,
                        "page": page,
                        "x0": box[0] / width,
                        "y0": box[1] / height,
                        "x1": box[2] / width,
                        "y1": box[3] / height,
                    }
                )
    return done, problems


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["TN"]
    rows: list[dict[str, object]] = []
    failed = False
    for chamber, (url, _) in FILES.items():
        artifact = raw.cached(SOURCE, url)
        pages = int(
            next(
                line.split()[1]
                for line in subprocess.run(
                    ["pdfinfo", str(artifact.path)], capture_output=True, text=True, check=True
                ).stdout.splitlines()
                if line.startswith("Pages:")
            )
        )
        cursor = Cursor()
        for page in range(1, pages + 1):
            image = page_png(chamber, page)
            with Image.open(image) as rendered:
                size = rendered.size
            cursor, done, problems = cursor, *read_page(chamber, page, image, size, cursor, fips)
            rows.extend(done)
            if problems:
                failed = True
                print(*problems, sep="\n")
        if cursor.block:
            print(f"{chamber} {cursor.district}: no district total after the last row")
            failed = True
    if failed:
        raise ValueError("county rows differ from the printed district totals")
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("chamber").agg(pl.col("district").n_unique(), pl.col("votes").sum()))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Tennessee's State House and two State Senate general elections of November 3, 1998 by county, read from the Secretary of State's typed returns",
            polity="US-TN",
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
        pages: dict[tuple[str, int], UUID] = {}
        for chamber, (url, _) in FILES.items():
            artifact = cs.artifact(raw.cached(SOURCE, url), source)
            for number in sorted(frame.filter(pl.col("chamber") == chamber)["page"].unique().to_list()):
                image = page_png(chamber, number)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                pages[(chamber, number)] = cs.page(artifact, number)
                cs.add(
                    "source_page",
                    id=pages[(chamber, number)],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        keyed = pl.DataFrame(
            [(c, n, str(i)) for (c, n), i in pages.items()],
            schema=["chamber", "page", "page_id"],
            orient="row",
        )
        medsl.attach(
            cs,
            conn,
            frame.join(keyed, on=["chamber", "page"]).select(
                state=pl.lit("TN"),
                day=pl.lit(DAY),
                chamber=pl.concat_str(
                    pl.when(pl.col("chamber") == "senate").then(pl.lit("senate")).otherwise(pl.lit("house")),
                    pl.col("district").cast(pl.String),
                    pl.lit("g"),
                    separator=":",
                ),
                code=pl.col("code"),
                candidate=pl.col("candidate"),
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("candidate") == "WRITE-INS",
                votes=pl.col("votes").cast(pl.Int64),
                year=pl.lit(1998, pl.Int32),
                special=pl.lit(False),
                district=pl.lit(None, pl.Int16),
                kind=pl.lit("county"),
                source=pl.lit("TN"),
                page_id=pl.col("page_id"),
                x0=pl.col("x0"),
                y0=pl.col("y0"),
                x1=pl.col("x1"),
                y1=pl.col("y1"),
            ),
            {"TN": source},
            extend=True,
            authoritative=CITATION,
        )
        return cs.write()
