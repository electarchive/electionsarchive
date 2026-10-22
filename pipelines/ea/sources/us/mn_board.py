import difflib
import html
import re
import subprocess
import tempfile
from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset
from ea.sources.us import medsl, mn_sos
from ea.store import DATA, connect

SOURCE = "us.mn_board"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = {2006: range(6, 21), 2010: range(6, 16)}
WANTED = {2006: {"senate", 5, 7, 8}, 2010: {1, 5, 6, 7, 8}}
PARTIES = {
    "IPR",
    "PF",
    "R",
    "DFL",
    "IP",
    "GP",
    "WI",
    "CP",
    "UP",
    "LIB",
    "IND",
    "MGP",
    "TRP",
    "EDP",
    "SWP",
    "SW",
    "GR",
    "NNT",
    "TP",
    "RP",
}
DIGITS = str.maketrans({"I": "1", "l": "1", "|": "1", "O": "0", "o": "0"})
DPI = 100
PUBLISHER = "Minnesota State Canvassing Board"
TITLE = "State canvassing board reports"
WRITE_INS = (
    "Write-in votes are not included in these county results. The scanned State Canvassing Board report prints them "
    "by county, but those columns could not be read reliably."
)
GAP = (
    "In the State Canvassing Board report, {name}'s county votes add up to {counted:,}, but the report's printed total "
    "is {printed:,}. These results follow the county rows as printed."
)


def fetch() -> None:
    for url in mn_sos.REPORTS.values():
        cached = raw.cached(mn_sos.SOURCE, url)
        raw.store(
            SOURCE,
            url,
            cached.path.read_bytes(),
            suffix=".pdf",
            retrieved_at=cached.retrieved_at,
            media_type="application/pdf",
        )


def parse() -> None:
    with connect() as conn:
        counties = {
            name.upper().removesuffix(" COUNTY, MN").replace("ST. ", "SAINT "): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.is_preferred
                   where u.scheme_code = 'us.county' and u.code like '27%' and u.valid @> '2006-11-07'::date"""
            ).fetchall()
        }
    order = sorted(counties, key=lambda county: re.sub(r"^MC", "MAC", county))
    rows = []
    totals = []
    failed = []
    folder = Path(tempfile.mkdtemp())
    for year, numbers in PAGES.items():
        pdf = raw.cached(SOURCE, mn_sos.REPORTS[year]).path
        blocks: list[dict] = []
        contest: str | None = None
        current: dict | None = None
        sizes = {}
        for number in numbers:
            page = subprocess.run(
                ["pdftotext", "-bbox", "-f", str(number), "-l", str(number), str(pdf), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            width, height = (
                float(value) for value in re.search(r'<page width="([\d.]+)" height="([\d.]+)"', page).groups()
            )
            sizes[number] = (width, height)
            words = sorted(
                (
                    (float(x0), float(y0), float(x1), float(y1), html.unescape(text))
                    for x0, y0, x1, y1, text in re.findall(
                        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', page
                    )
                    if float(y0) < height * 0.9
                ),
                key=lambda word: word[1],
            )
            lines: list[list[tuple[float, float, float, float, str]]] = []
            for word in words:
                if lines and word[1] - max(other[1] for other in lines[-1]) < 4:
                    lines[-1].append(word)
                else:
                    lines.append([word])
            for line in lines:
                line.sort()
                text = " ".join(word[4] for word in line)
                title = re.match(r"(US|STATE) (REPRESENTATIVE|SENATOR),? ?(?:DISTRICT (\w+))?", text)
                if title:
                    contest = (
                        None
                        if title[1] == "STATE"
                        else "senate"
                        if title[2] == "SENATOR"
                        else str(int(title[3].translate(DIGITS)))
                    )
                    contest = contest if contest in {str(wanted) for wanted in WANTED[year]} else None
                    if current is None or current["contest"] != contest:
                        current = None
                    continue
                if contest is None:
                    continue
                if all(re.fullmatch(r"[A-Z]{1,4}", word[4]) for word in line) and any(
                    word[4] in PARTIES for word in line
                ):
                    if current is not None and not current["lines"] and not any(current["names"]):
                        current["columns"] = sorted(
                            [*current["columns"], *((word[0], word[2], word[4]) for word in line)]
                        )
                    else:
                        current = {
                            "contest": contest,
                            "block": sum(block["contest"] == contest for block in blocks) + 1,
                            "columns": [(word[0], word[2], word[4]) for word in line],
                            "lines": [],
                        }
                        blocks.append(current)
                    current["names"] = [[] for _ in current["columns"]]
                    continue
                if current is None:
                    continue
                columns = current["columns"]
                cells = {}
                for word in line:
                    digits = word[4].translate(DIGITS).replace(",", "")
                    if word[0] < columns[0][0] - 60 or not digits.isdigit():
                        continue
                    distance = [min(abs(x0 - word[0]), abs(x1 - word[2])) for x0, x1, _ in columns]
                    index = distance.index(min(distance))
                    if distance[index] > 40 or index in cells:
                        raise ValueError(f"{year} {contest}, page {number}: stray number {word[4]} in {text!r}")
                    cells[index] = (int(digits), word[:4])
                left = " ".join(
                    word[4]
                    for word in line
                    if word[0] < columns[0][0] - 20 and word[:4] not in [cell[1] for cell in cells.values()]
                )
                if not current["lines"] and not cells:
                    groups = [[line[0]]]
                    for previous, word in pairwise(line):
                        if word[0] - previous[2] > 12:
                            groups.append([])
                        groups[-1].append(word)
                    for place, group in enumerate(groups):
                        index = (
                            place
                            if len(groups) == len(columns)
                            else min(range(len(columns)), key=lambda i: abs(columns[i][0] - group[0][0]))
                        )
                        current["names"][index].extend(word[4] for word in group)
                    continue
                if cells or re.search(r"[A-Z]{3}", left):
                    current["lines"].append((number, line[0][1], left, cells))
        for block in blocks:
            key = (year, block["contest"])
            columns = block["columns"]
            candidates = [re.sub(r"[*~+]+", "", " ".join(name)).strip() for name in block["names"]]
            numbered = [line for line in block["lines"] if line[3]]
            merged = {id(line): [line[2]] for line in numbered}
            loose = []
            for line in block["lines"]:
                if line[3]:
                    continue
                near = min(
                    (other for other in numbered if other[0] == line[0] and abs(other[1] - line[1]) <= 10),
                    key=lambda other: (bool(other[2]), abs(other[1] - line[1])),
                    default=None,
                )
                if near is not None:
                    merged[id(near)].insert(0 if line[1] < near[1] else len(merged[id(near)]), line[2])
                else:
                    loose.append(line)
            lines = sorted(
                [(line[0], line[1], " ".join(" ".join(merged[id(line)]).split()), line[3]) for line in numbered]
                + [(line[0], line[1], line[2], {}) for line in loose],
            )
            for index in range(len(lines) - 1, 0, -1):
                joined = f"{lines[index - 1][2]} {lines[index][2]}"
                if not lines[index][3] and joined in counties:
                    lines[index - 1] = (*lines[index - 1][:2], joined, lines[index - 1][3])
                    del lines[index]
            total = lines.pop() if lines[-1][2] in ("", "TOTAL") else None
            names: list[str | None] = []
            for _, _, label, _ in lines:
                match = difflib.get_close_matches(label.upper().replace("ST. ", "SAINT "), counties, n=1, cutoff=0.6)
                names.append(match[0] if match else None)
            for index, name in enumerate(names):
                before = next((order.index(names[i]) for i in range(index - 1, -1, -1) if names[i]), -1)
                after = next((order.index(names[i]) for i in range(index + 1, len(names)) if names[i]), len(order))
                if name is not None and before < order.index(name) < after:
                    continue
                between = order[before + 1 : after]
                guess = difflib.get_close_matches(lines[index][2].upper(), between, n=1, cutoff=0.3)
                names[index] = between[0] if len(between) == 1 else guess[0] if guess else None
                if names[index] is None:
                    raise ValueError(f"{key} block {block['block']}: no county for {lines[index][2]!r} among {between}")
            everything = [*lines, *([total] if total else [])]

            def ocr(
                cells: list[tuple[int, int]], everything=everything, lines=lines, columns=columns, pdf=pdf
            ) -> dict[tuple[int, int], tuple[int, tuple] | None]:
                read = {}
                for number in {everything[row][0] for row, _ in cells}:
                    here = [(row, index) for row, index in cells if everything[row][0] == number]
                    boxes = []
                    for row, index in here:
                        found = [line[3][index][1] for line in lines if index in line[3]]
                        left = min((box[0] for box in found), default=columns[index][0] - 20)
                        right = max((box[2] for box in found), default=columns[index][1] + 40)
                        boxes.append((left - 6, everything[row][1] - 4, right + 6, everything[row][1] + 15))
                    image = scan.rendered(pdf, number, folder)
                    scaled = [tuple(round(value * 300 / 72) for value in box) for box in boxes]
                    passes = scan.read(image, [(box, True) for box in scaled], "eng")
                    for cell, box, pixels, texts in zip(here, boxes, scaled, passes, strict=True):
                        values = [text.translate(scan.CONFUSABLE).replace(",", "").replace(".", "") for text in texts]
                        counted = Counter(value for value in values if value.isdigit())
                        if len(counted) != 1 or sum(counted.values()) < 2:
                            with Image.open(image) as page:
                                values += [
                                    scan._ocr(page.convert("L"), pixels, True, scale, "8", "eng")
                                    .replace(",", "")
                                    .replace(".", "")
                                    for scale in (2, 3)
                                ]
                            counted = Counter(value for value in values if value.isdigit())
                        if len(counted) == 1 and sum(counted.values()) >= 3:
                            read[cell] = (int(next(iter(counted))), box)
                        elif not any(values):
                            read[cell] = None
                return read

            read = ocr(
                [
                    (row, index)
                    for row, line in enumerate(everything)
                    for index in range(len(columns))
                    if index not in line[3]
                ]
            )
            bad = []
            for index, candidate in enumerate(candidates):
                values = [
                    line[3][index] if index in line[3] else read.get((row, index))
                    for row, line in enumerate(everything)
                ]
                printed = values.pop() if total is not None else (0, None)
                printed = printed[0] if printed is not None else 0 if (len(lines), index) in read else None
                if None not in values and printed is not None and sum(value[0] for value in values) != printed:
                    again = ocr([(row, index) for row in range(len(lines))])
                    fixes = [
                        row
                        for row, value in enumerate(values)
                        if again.get((row, index)) is not None
                        and again[(row, index)][0] != value[0]
                        and sum(other[0] for other in values) - value[0] + again[(row, index)][0] == printed
                    ]
                    if len(fixes) == 1:
                        values[fixes[0]] = (again[(fixes[0], index)][0], values[fixes[0]][1])
                counted = sum(value[0] for value in values if value)
                printed = 0 if printed is None and None not in values and counted == 0 else printed
                if None in values or printed is None or counted != printed:
                    loaded = None not in values and printed is not None and columns[index][2] != "WI"
                    loaded = loaded and abs(counted - printed) <= 25
                    bad.append((candidate, counted, printed, values.count(None)))
                    failed.append((year, key[1], candidate, columns[index][2], counted, printed, loaded))
                    if not loaded:
                        continue
                totals.append((year, key[1], block["block"], candidate, printed))
                for name, (votes, (x0, y0, x1, y1)), line in zip(names, values, lines, strict=True):
                    width, height = sizes[line[0]]
                    rows.append(
                        (year, key[1], block["block"], counties[name], candidate, columns[index][2], votes, line[0],
                         x0 / width, y0 / height, x1 / width, y1 / height)
                    )  # fmt: skip
            print(year, key[1], block["block"], len(lines), "counties", len(read), "OCR cells", "failed:", bad)
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "contest": pl.String,
            "block": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
            "page": pl.Int16,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
        },
        orient="row",
    )
    if frame.select("year", "contest", "candidate", "code").is_duplicated().any():
        raise ValueError("a county appears twice in a contest")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    pl.DataFrame(
        failed,
        schema={
            "year": pl.Int32,
            "contest": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
            "printed": pl.Int64,
            "loaded": pl.Boolean,
        },
        orient="row",
    ).write_parquet(PARSED / "failed.parquet")


def emit() -> Path:
    failed = pl.read_parquet(PARSED / "failed.parquet")
    unread = failed.filter(~pl.col("loaded"))
    frame = (
        pl.read_parquet(PARSED / "counties.parquet")
        .join(
            unread.filter(pl.col("party") != "WI").select("year", "contest").unique(),
            on=["year", "contest"],
            how="anti",
        )
        .join(unread.select("year", "contest", "candidate"), on=["year", "contest", "candidate"], how="anti")
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's 2006 and 2010 U.S. House and Senate generals by county from the State Canvassing Board reports",
            polity="US-MN",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://officialdocuments.sos.mn.gov/",
                license="Public record",
            )
        pages = {}
        for year, url in mn_sos.REPORTS.items():
            cached = raw.cached(SOURCE, url)
            artifact = cs.artifact(cached, source)
            for number in frame.filter(pl.col("year") == year)["page"].unique().to_list():
                target = DATA / "pages" / SOURCE / f"{cached.path.stem}-{number}.png"
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-r",
                            str(DPI),
                            "-gray",
                            "-png",
                            "-f",
                            str(number),
                            "-l",
                            str(number),
                            "-singlefile",
                        ]
                        + [str(cached.path), str(target.with_suffix(""))],
                        check=True,
                    )
                with Image.open(target) as rendered:
                    width, height = rendered.size
                pages[(year, number)] = str(cs.page(artifact, number))
                if UUID(pages[(year, number)]) in cs.minted:
                    cs.add(
                        "source_page",
                        id=UUID(pages[(year, number)]),
                        artifact_id=artifact,
                        page_number=number,
                        image_key=f"pages/{SOURCE}/{target.name}",
                        width=width,
                        height=height,
                    )
        medsl.attach(
            cs,
            conn,
            frame.select(
                year="year",
                chamber=pl.when(pl.col("contest") == "senate").then(pl.lit("senate")).otherwise(pl.lit("house")),
                day=pl.when(pl.col("year") == 2006)
                .then(pl.lit(date(2006, 11, 7)))
                .otherwise(pl.lit(date(2010, 11, 2))),
                special=pl.lit(False),
                state=pl.lit("MN"),
                district=pl.when(pl.col("contest") != "senate").then(pl.col("contest").cast(pl.Int16, strict=False)),
                code="code",
                candidate=pl.when(pl.col("candidate").str.replace_all(r"\s", "") == "WRITE-IN")
                .then(pl.lit("WRITE-INS"))
                .otherwise(pl.col("candidate")),
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("party") == "WI",
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("MN"),
                page_id=pl.struct("year", "page").map_elements(
                    lambda key: pages[(key["year"], key["page"])], return_dtype=pl.String
                ),
                x0="x0",
                y0="y0",
                x1="x1",
                y1="y1",
            ),
            {"MN": source},
            strict=True,
        )
        for (year, contest), lines in failed.join(
            frame.select("year", "contest").unique(), on=["year", "contest"]
        ).group_by("year", "contest"):
            day = "2006-11-07" if year == 2006 else "2010-11-02"
            office = "senate" if contest == "senate" else f"house:{contest}"
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:MN:{office}:county")
            if result_set not in cs.minted:
                continue
            if not lines["loaded"].all():
                cs.add("note", entity_id=result_set, kind="caveat", body_md=WRITE_INS)
            for name, counted, printed in (
                lines.filter(pl.col("loaded")).select("candidate", "votes", "printed").iter_rows()
            ):
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=GAP.format(name=name.title(), counted=counted, printed=printed),
                )
        return cs.write()
