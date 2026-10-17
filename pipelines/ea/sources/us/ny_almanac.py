import csv
import difflib
import io
import re
import statistics
import struct
import subprocess
import tempfile
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ny_almanac"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Albany Evening Journal"
ITEM = "https://archive.org/details/{item}"
FILE = "https://archive.org/download/{item}/{item}.pdf"
DIGITIZED = 8
DERIVED = 1
VOLUMES = {
    1870: ("eveningjournalal1873slsn", 1873, date(1870, 11, 8), [71, 72]),
    1872: ("eveningjournalal1873slsn", 1873, date(1872, 11, 5), [70, 71]),
    1874: ("eveningjournalal1875slsn", 1875, date(1874, 11, 3), [89, 90, 91, 92]),
    1882: ("eveningjournalal1883slsn", 1883, date(1882, 11, 7), [80, 81]),
    1884: ("eveningjournalal1885slsn", 1885, date(1884, 11, 4), [84, 85, 86]),
    1886: ("eveningjournalal1887slsn", 1887, date(1886, 11, 2), [96, 97]),
    1890: ("eveningjournalal1891slsn", 1891, date(1890, 11, 4), [84, 85]),
    1892: ("eveningjournalal1895slsn", 1895, date(1892, 11, 8), [95, 96, 97, 98]),
    1894: ("eveningjournalal1895slsn", 1895, date(1894, 11, 6), [95, 96, 97, 98]),
}
JOINT = {"fultonandhamilton": ("Fulton and Hamilton", ["36035", "36041"])}
CORRECTED: dict[tuple[int, int, str, int], int] = {}
UNBALANCED = {
    (1870, 12, 0),
    (1870, 13, 1),
    (1872, 17, 0),
    (1886, 15, 2),
    (1886, 17, 1),
    (1886, 21, 1),
    (1890, 20, 1),
    (1892, 17, 1),
    (1892, 18, 2),
    (1892, 21, 2),
    (1892, 25, 1),
}
ANCHORS = {
    (1886, 96, 11, 0, 0): (551, 2610, 632, 2625),
    (1886, 96, 11, 0, 1): (753, 2606, 789, 2629),
    (1892, 98, 33, 0, 0): (955, 508, 1030, 532),
    (1892, 98, 33, 0, 2): (1293, 511, 1326, 529),
    (1892, 98, 34, 0, 0): (963, 641, 1017, 661),
    (1892, 98, 34, 0, 1): (1115, 643, 1170, 663),
    (1894, 98, 34, 0, 2): (832, 635, 864, 654),
}
WITHHELD = {(1882, 3)}
MISPRINTED = {(1870, 22, 1), (1870, 26, 0), (1872, 32, 0), (1874, 26, 1), (1882, 21, 1), (1892, 30, 0), (1892, 30, 1)}
GAP = (
    "The county figures printed for {candidate} sum to {counted} votes, but the almanac gives a district total of "
    "{printed}. We show the county figures as they were printed."
)


def _artifact(year: int) -> raw.Artifact:
    url = FILE.format(item=VOLUMES[year][0])
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return raw.fetch(SOURCE, url, suffix=".pdf")


def fetch() -> None:
    for year in VOLUMES:
        _artifact(year)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def _tables(path: str) -> list[dict[str, object]]:
    tables: list[dict[str, object]] = []
    page = 0
    for line in (PARSED / "model" / path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@"):
            page = int(line[1:])
            continue
        if line == "~":
            tables.append({**tables[-1], "rows": []})
            continue
        head, rest = line.split("|", 1)
        head, rest = head.strip(), rest.replace("|", " ").strip()
        if head.isdigit():
            tables.append(
                {"page": page, "district": int(head), "columns": [c.strip() for c in rest.split(",")], "rows": []}
            )
            continue
        votes = [None if cell == "-" else int(cell.replace(",", "")) for cell in rest.split()]
        if len(votes) != len(tables[-1]["columns"]):
            raise ValueError(f"{path}: {line}")
        tables[-1]["rows"].append((head, votes, page))
    return tables


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year, (_, _, _, pages) in VOLUMES.items():
        book = _artifact(year)
        firsts = _tables(f"{year}.txt")
        seconds = _tables(f"{year}.v2.txt")
        if [(t["district"], [row[2] for row in t["rows"]]) for t in firsts] != [
            (t["district"], [row[2] for row in t["rows"]]) for t in seconds
        ]:
            raise ValueError(f"{year}: the two reads lay out different tables")
        for page in pages:
            cache = PARSED / "ocr" / f"{book.sha256}-{page}.tsv"
            cache.parent.mkdir(parents=True, exist_ok=True)
            words: list[tuple[str, tuple[int, int, int, int]]] = []
            for dpi in (300, 600):
                sparse = PARSED / "ocr" / f"{book.sha256}-{page}.sparse{'' if dpi == 300 else dpi}.tsv"
                if not sparse.exists():
                    with tempfile.TemporaryDirectory() as folder:
                        image = Path(folder) / "page"
                        subprocess.run(
                            ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page)]
                            + ["-singlefile", str(book.path), str(image)],
                            check=True,
                        )
                        sparse.write_text(
                            subprocess.run(
                                ["tesseract", str(image.with_suffix(".png")), "stdout", "--psm", "11", "tsv"],
                                capture_output=True,
                                text=True,
                                check=True,
                            ).stdout
                        )
                scale = dpi // 300
                words += [
                    (
                        row["text"],
                        (
                            int(row["left"]) // scale,
                            int(row["top"]) // scale,
                            (int(row["left"]) + int(row["width"])) // scale,
                            (int(row["top"]) + int(row["height"])) // scale,
                        ),
                    )
                    for row in csv.DictReader(io.StringIO(sparse.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE)
                    if row["level"] == "5" and row["text"].strip()
                ]
            pool = [(w[0], w[1]) for w in scan.words(book.path, page, cache)] + scan.layer(book.path, page) + words
            pool += [
                (head + tail, (one[0], min(one[1], two[1]), two[2], max(one[3], two[3])))
                for head, one in pool
                if re.fullmatch(r"\d{1,3}[,.]", head)
                for tail, two in pool
                if re.fullmatch(r"\d{3}\W?", tail) and 0 <= two[0] - one[2] < 40 and abs(two[1] - one[1]) < 10
            ]
            tokens = [
                (digits, box)
                for text, box in pool
                if re.search(r"\d", text)
                for digits in {re.sub(r"\D", "", text), re.sub(r"\D", "", text.translate(scan.CONFUSABLE))}
            ]
            for table, second in zip(firsts, seconds, strict=True):
                cells = [
                    (r, c, county, value, again[c])
                    for r, ((county, votes, at), (_, again, _)) in enumerate(
                        zip(table["rows"], second["rows"], strict=True)
                    )
                    if at == page
                    for c, value in enumerate(votes)
                ]
                if not cells:
                    continue
                found: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
                for r, c, _, value, _ in cells:
                    if value is not None:
                        boxes: list[tuple[int, int, int, int]] = []
                        for digits, box in sorted(tokens, key=lambda token: token[1][2] - token[1][0]):
                            if digits == str(value) and not any(
                                abs(box[0] - b[0]) < 30 and abs(box[1] - b[1]) < 30 for b in boxes
                            ):
                                boxes.append(box)
                        found[(r, c)] = boxes
                anchors = {key: boxes[0] for key, boxes in found.items() if len(boxes) == 1}
                if not anchors:
                    for r, c, _, value, _ in cells:
                        if value is None or len(str(value)) < 4:
                            continue
                        close = {
                            box
                            for digits, box in tokens
                            if abs(len(digits) - len(str(value))) <= 2
                            and difflib.SequenceMatcher(None, digits, str(value)).ratio() >= 0.7
                        }
                        if len(close) == 1:
                            anchors[(r, c)] = close.pop()
                for _ in range(2):
                    ys = {
                        r: statistics.median((b[1] + b[3]) / 2 for (rr, _), b in anchors.items() if rr == r)
                        for r in {r for r, _ in anchors}
                    }
                    xs = {
                        c: statistics.median(b[2] for (_, cc), b in anchors.items() if cc == c)
                        for c in {c for _, c in anchors}
                    }
                    anchors = {
                        (r, c): b
                        for (r, c), b in anchors.items()
                        if abs((b[1] + b[3]) / 2 - ys[r]) < 40 and abs(b[2] - xs[c]) < 120
                    }
                anchors |= {
                    (r, c): box
                    for (y, p, d, r, c), box in ANCHORS.items()
                    if (y, p, d) == (year, page, table["district"])
                }
                if not anchors:
                    raise ValueError(f"{year} page {page} district {table['district']}: no cell found on the page")
                ys = {
                    r: statistics.median((b[1] + b[3]) / 2 for (rr, _), b in anchors.items() if rr == r)
                    for r in {r for r, _ in anchors}
                }
                xs = {
                    c: statistics.median(b[2] for (_, cc), b in anchors.items() if cc == c)
                    for c in {c for _, c in anchors}
                }
                height = statistics.median(b[3] - b[1] for b in anchors.values())
                digit = statistics.median(
                    (b[2] - b[0]) / len(str(v)) for (r, c), b in anchors.items() for v in [table["rows"][r][1][c]]
                )
                for r in {r for r, *_ in cells} - set(ys):
                    below = [k for k in ys if k < r]
                    above = [k for k in ys if k > r]
                    if below and above:
                        lo, hi = max(below), min(above)
                        ys[r] = ys[lo] + (ys[hi] - ys[lo]) * (r - lo) / (hi - lo)
                for c in {c for _, c, *_ in cells} - set(xs):
                    if c - 1 in xs and c + 1 in xs:
                        xs[c] = (xs[c - 1] + xs[c + 1]) / 2
                for r, c, county, value, again in cells:
                    if value is None and again is None:
                        continue
                    box = None
                    if (r, c) in anchors:
                        box, how = anchors[(r, c)], "unique"
                    elif r in ys and c in xs:
                        near = [
                            b
                            for b in found.get((r, c), [])
                            if abs((b[1] + b[3]) / 2 - ys[r]) < 40 and abs(b[2] - xs[c]) < 120
                        ]
                        if near:
                            box, how = near[0], "aligned"
                        else:
                            width = digit * len(str(value if value is not None else again))
                            box = (
                                round(xs[c] - width),
                                round(ys[r] - height / 2),
                                round(xs[c]),
                                round(ys[r] + height / 2),
                            )
                            how = "placed"
                    if box is None and value is not None:
                        near = sorted(
                            (
                                abs((b[1] + b[3]) / 2 - ys[r]) if r in ys else 0,
                                abs(b[2] - xs[c]) if c in xs else 0,
                                b,
                            )
                            for digits, b in tokens
                            if abs(len(digits) - len(str(value))) <= 1
                            and difflib.SequenceMatcher(None, digits, str(value)).ratio() >= 0.75
                            and (r in ys or c in xs)
                            and (r not in ys or abs((b[1] + b[3]) / 2 - ys[r]) < 40)
                            and (c not in xs or abs(b[2] - xs[c]) < 120)
                            and min(a[1] for a in anchors.values()) - 400
                            < b[1]
                            < max(a[3] for a in anchors.values()) + 400
                        )
                        if near:
                            box, how = near[0][2], "similar"
                    rows.append(
                        {
                            "year": year,
                            "page": page,
                            "district": table["district"],
                            "county": county,
                            "row": r,
                            "column": c,
                            "label": table["columns"][c],
                            "model": value,
                            "second": again,
                            "votes": value,
                            "box": None if box is None else list(box),
                            "located": None if box is None else how,
                            "status": "agreed" if value == again else "extra" if value is None else "doubtful",
                        }
                    )
    frame = pl.DataFrame(rows, infer_schema_length=None)
    extra = frame.filter(pl.col("status") == "extra")
    if extra.height:
        raise ValueError(f"read as blank only once: {extra.select('year', 'district', 'county', 'column').rows()}")
    for (year, district, county, column), votes in CORRECTED.items():
        match = (
            (pl.col("year") == year)
            & (pl.col("district") == district)
            & (pl.col("county") == county)
            & (pl.col("column") == column)
        )
        frame = frame.with_columns(
            votes=pl.when(match).then(votes).otherwise("votes"),
            status=pl.when(match).then(pl.lit("corrected")).otherwise("status"),
        )
    failed = []
    gaps: list[dict[str, object]] = []
    for (year, district, column), lines in frame.group_by("year", "district", "column", maintain_order=True):
        total = lines.filter(pl.col("county") == "Totals")
        counties = lines.filter(pl.col("county") != "Totals")
        match = (pl.col("year") == year) & (pl.col("district") == district) & (pl.col("column") == column)
        if total.height == 0:
            continue
        printed = int(total["votes"][0])
        counted = int(counties["votes"].fill_null(0).sum())
        if counted == printed:
            frame = frame.with_columns(
                status=pl.when(match & (pl.col("status") == "doubtful")).then(pl.lit("balanced")).otherwise("status")
            )
        elif (year, district, column) in MISPRINTED:
            frame = frame.with_columns(
                status=pl.when(match & (pl.col("county") == "Totals")).then(pl.lit("misprinted")).otherwise("status")
            )
        elif (year, district, column) in UNBALANCED:
            gaps.append(
                {"year": year, "district": district, "label": lines["label"][0], "counted": counted, "printed": printed}
            )
        else:
            failed.append((year, district, column, f"counties {counted}, printed {printed}"))
    print(frame.group_by("status").len().sort("status"))
    print(frame.group_by("located").len().sort("located"))
    doubtful = frame.filter(pl.col("status") == "doubtful")
    for line in doubtful.select("year", "page", "district", "county", "column", "model", "second").rows():
        print("doubtful:", line)
    if failed:
        for line in failed:
            print(line)
        raise ValueError(f"{len(failed)} columns disagree with their printed totals")
    if doubtful.height:
        raise ValueError(f"{doubtful.height} cells read differently and no printed total settles them")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "values.parquet")
    pl.DataFrame(gaps, schema=["year", "district", "label", "counted", "printed"]).write_parquet(
        PARSED / "gaps.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet").filter(pl.col("county") != "Totals")
    gaps = pl.read_parquet(PARSED / "gaps.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's House results by county from the Albany Evening Journal Almanac",
            polity="US",
        )
        counties: dict[str, tuple[str, UUID, UUID]] = {
            _plain(text): (code, unit, area)
            for code, unit, area, text in conn.execute(
                """select u.code, u.id, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
        newyork = cs.polity("US-NY")
        state, state_area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and code = '36'"
        ).fetchone()
        joint: dict[str, UUID] = {}
        for plain, (label, codes) in JOINT.items():
            unit = cs.unit("us.aggregate", f"NY:{plain}")
            joint[plain] = unit
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=newyork,
                scheme_code="us.aggregate",
                code=f"NY:{plain}",
                parent_id=state,
                area_relation="within",
                area_id=state_area,
                kind="aggregate",
                valid_from=None,
            )
            cs.add("name", entity_id=unit, text=label, lang="en", kind="official", is_preferred=True)
            for code in codes:
                cs.add(
                    "reporting_unit_area",
                    unit_id=unit,
                    area_id=next(area for c, _, area in counties.values() if c == code),
                )
        contests: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value like '%%:NY:house:%%'"
            ).fetchall()
        )
        PAGES.mkdir(parents=True, exist_ok=True)
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for item, volume in dict.fromkeys((item, volume) for item, volume, _, _ in VOLUMES.values()):
            title = f"The Evening Journal Almanac, {volume}"
            source = cs.source(PUBLISHER, title)
            years = [year for year, (other, *_) in VOLUMES.items() if other == item]
            sources |= dict.fromkeys(years, source)
            numbers = sorted({number for year in years for number in VOLUMES[year][3]})
            cs.add(
                "source",
                id=source,
                kind="compendium",
                publisher=PUBLISHER,
                title=title,
                url=ITEM.format(item=item),
                license="Public domain",
            )
            book = _artifact(years[0])
            artifact = cs.artifact(book, source)
            for number in numbers:
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-r",
                            "100",
                            "-png",
                            "-f",
                            str(number),
                            "-l",
                            str(number),
                            "-singlefile",
                            str(book.path),
                            str(image.with_suffix("")),
                        ],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                page = cs.page(artifact, number)
                pages |= {(year, number): (page, width, height) for year in years}
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{book.sha256}-{number}.png",
                    width=width,
                    height=height,
                )
        for (year, district), lines in frame.group_by("year", "district", maintain_order=True):
            day = VOLUMES[int(year)][2]
            key = next(
                (k for k in (f"{day}:NY:house:{district}", f"{day:%Y-%m}:NY:house:{district}") if k in contests), None
            )
            if key is None:
                print(f"{year} district {district}: no contest")
                continue
            if (int(year), int(district)) in WITHHELD:
                continue
            contest = contests[key]
            election, district_area, first, official = conn.execute(
                """select c.election_id, c.district_area_id, coalesce(min(v.count_round), 0),
                          coalesce(sum(v.value) filter (where v.measure = 4 and v.option_k = 0),
                                   sum(v.value) filter (where v.measure = 1))::bigint
                   from contest c join result_set rs on rs.contest_id = c.id and rs.is_default
                   left join result_value v on v.result_set_k = rs.k and v.measure in (1, 4)
                   where c.id = %s group by 1, 2""",
                [contest],
            ).fetchone()
            known = conn.execute(
                """select o.id, o.kind, coalesce(o.ballot_label, ''),
                          (select sum(v.value)::bigint from result_value v
                           where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1)
                   from contest_option o join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   where o.contest_id = %s""",
                [contest],
            ).fetchall()
            picked: dict[str, UUID] = {}
            for label in lines["label"].unique(maintain_order=True):
                surname = _plain(label)
                match = [
                    option
                    for option, kind, name, _ in known
                    if kind == "candidate"
                    and (words := [w for w in name.replace(",", " ").split() if _plain(w) not in ("jr", "sr")])
                    and difflib.SequenceMatcher(None, surname, _plain(words[-1])).ratio() >= 0.8
                ]
                if len(match) == 1:
                    picked[label] = match[0]
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            if result_set not in cs.minted:
                continue
            if len(picked) < lines["label"].n_unique():
                pool = cs.key("us.option", f"{key}:others:ny_almanac")
                cs.add("contest_option", id=pool, contest_id=contest, kind="others", ballot_label="Others")
                picked = {label: picked.get(label, pool) for label in lines["label"].unique()}
            lines = lines.filter(pl.col("votes").is_not_null()).with_columns(
                option=pl.col("label").replace_strict({label: str(option) for label, option in picked.items()})
            )
            counted = int(lines["votes"].sum())
            for option, kind, name, total in known:
                sum_ = int(lines.filter(pl.col("option") == str(option))["votes"].sum())
                if total and sum_ != total:
                    print(f"{key}: {name or kind} {sum_} by county, {total} in the default set")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[int(year)],
                is_default=False,
                completeness=round(min(counted / official, 1), 4) if official else None,
            )
            for label, summed, printed in (
                gaps.filter((pl.col("year") == year) & (pl.col("district") == district))
                .select("label", "counted", "printed")
                .iter_rows()
            ):
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=GAP.format(
                        candidate=next(
                            (name for option, _, name, _ in known if option == picked[str(label)]), str(label)
                        ),
                        counted=f"{summed:,}",
                        printed=f"{printed:,}",
                    ),
                )
            units: dict[str, UUID] = {}
            for county in lines["county"].unique(maintain_order=True):
                plain = _plain(county)
                if plain in joint:
                    units[county] = joint[plain]
                elif plain.startswith("partof"):
                    code, parent, area = counties[plain.removeprefix("partof")]
                    part = f"{year}:{code}:{district}"
                    units[county] = cs.unit("us.county_part", part)
                    cs.add(
                        "reporting_unit",
                        id=units[county],
                        polity_id=newyork,
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=parent,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=units[county],
                        area_id=district_area,
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                else:
                    units[county] = counties[plain][1]
            for (county, option), cells in lines.group_by("county", "option", maintain_order=True):
                unit = units[str(county)]
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=first,
                    flags=DIGITIZED | (DERIVED if cells.height > 1 else 0),
                    value=int(cells["votes"].sum()),
                )
                boxes = [list(box) for box in cells["box"] if box is not None]
                if not boxes or cells["page"].n_unique() > 1:
                    continue
                page, width, height = pages[(int(year), int(cells["page"][0]))]
                scale = 3
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=UUID(str(option)),
                    channel=0,
                    measure=1,
                    count_round=first,
                    page_id=page,
                    source_id=None,
                    x0=min(box[0] for box in boxes) / width / scale,
                    y0=min(box[1] for box in boxes) / height / scale,
                    x1=max(box[2] for box in boxes) / width / scale,
                    y1=max(box[3] for box in boxes) / height / scale,
                )
        return cs.write()
