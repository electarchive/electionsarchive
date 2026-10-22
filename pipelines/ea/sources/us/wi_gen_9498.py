import csv
import re
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.wi_gen_9498"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-026"
PUBLISHER = "Wisconsin Elections Board"
BOOKS = {
    "1995-96": ("AYPHDP7TNI56WH8E", date(1994, 11, 8), "Wisconsin Blue Book 1995-1996"),
    "1997-98": ("AV57G6QVOUSNPG8T", date(1996, 11, 5), "Wisconsin Blue Book 1997-1998"),
    "1999-2000": ("A4K4QRBLSRUNJE8R", date(1998, 11, 3), "Wisconsin Blue Book 1999-2000"),
}
PAGE_LIST = {
    "1995-96": {916: 1, 917: 6, 918: 9},
    "1997-98": {881: 1, 882: 4, 883: 8},
    "1999-2000": {862: 1, 863: 4, 864: 8},
}
TABLE = Path(__file__).with_suffix(".tsv")
LANDING = "https://search.library.wisc.edu/digital/"
SUMMARY = "Wisconsin's U.S. House general elections of 1994, 1996, and 1998 by county, read from page scans of the Wisconsin Blue Books"
METHOD = (
    "County totals are read from page scans of the Wisconsin Blue Book's vote tables, saved from the UW Digital Collections by a "
    "person in a browser. Each candidate's county sum equals the district total printed on the page, and each value carries its page box. "
    "Scattered votes are omitted in the book."
)
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})*")
CONFUSABLE = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8", "'": ","})


def url(book: str, page: int) -> str:
    return f"https://search.library.wisc.edu/digital/{BOOKS[book][0]}?page={page}"


def source_name(book: str, page: int) -> str:
    return f"WI-Gen-Blue-Book-{book}_p{page}.jpg"


def key(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower().replace("st.", "saint"))


def fetch() -> None:
    for book, pages in PAGE_LIST.items():
        for page in pages:
            try:
                raw.cached(SOURCE, url(book, page))
            except LookupError:
                raw.store(
                    SOURCE,
                    url(book, page),
                    (INBOX / source_name(book, page)).read_bytes(),
                    suffix=".jpg",
                    retrieved_at=datetime(2026, 10, 22, tzinfo=UTC),
                    media_type="image/jpeg",
                )


def clean(path: Path, target: Path) -> None:
    image = np.array(Image.open(path).convert("L"))
    dark = image < 150
    out = image.copy()
    for y in range(dark.shape[0]):
        edges = np.diff(np.concatenate([[0], dark[y].astype(np.int8), [0]]))
        for start, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0], strict=True):
            if end - start >= 220:
                out[y, start:end] = 255
    for x in np.where(dark.sum(axis=0) > 2500)[0]:
        out[:, x] = 255
    half = Image.fromarray(out)
    half.resize((half.width // 2, half.height // 2), Image.LANCZOS).save(target)


def words(path: Path) -> list[tuple[int, int, int, int, str]]:
    found: list[tuple[int, int, int, int, str]] = []
    with tempfile.TemporaryDirectory() as folder:
        small = Path(folder) / "half.png"
        clean(path, small)
        for mode in (4, 11):
            subprocess.run(
                ["tesseract", str(small), str(Path(folder) / f"o{mode}"), "--psm", str(mode), "-l", "eng", "tsv"],
                capture_output=True,
                check=True,
            )
            with (Path(folder) / f"o{mode}.tsv").open() as handle:
                for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
                    text = (row["text"] or "").strip()
                    if text and float(row["conf"]) >= 0:
                        found.append((int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"]), text))
    kept: list[tuple[int, int, int, int, str]] = []
    for word in sorted(found, key=lambda w: (w[1], w[0])):
        if any(abs(word[0] - k[0]) < 12 and abs(word[1] - k[1]) < 10 and word[4] == k[4] for k in kept):
            continue
        kept.append(word)
    return kept


def number(text: str) -> int | None:
    cleaned = re.sub(r"[^\w,]", "", text.translate(CONFUSABLE)).rstrip(",")
    return int(cleaned.replace(",", "")) if NUMBER.fullmatch(cleaned) else None


def page_png(book: str, page: int) -> Path:
    artifact = raw.cached(SOURCE, url(book, page))
    target = PAGES / f"{artifact.sha256}.png"
    if not target.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        Image.open(artifact.path).convert("L").save(target)
    return target


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        codes = {
            key(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '55%%'"""
            ).fetchall()
        }
    table: dict[tuple[int, int], list[tuple[str, bool, list[int]]]] = {}
    printed: dict[tuple[int, int], list[int]] = {}
    with TABLE.open() as handle:
        for record in csv.DictReader(handle, delimiter="\t"):
            slot = (int(record["year"]), int(record["district"]))
            votes = [int(v) for v in record["votes"].split(",")]
            if record["county"] == "TOTAL":
                printed[slot] = votes
            else:
                label = record["county"]
                table.setdefault(slot, []).append((codes[key(label.replace("(part)", ""))], "(part)" in label, votes))
    rows: list[dict[str, object]] = []
    approximate = 0
    for book, pages in PAGE_LIST.items():
        year = BOOKS[book][1].year
        for page, first in pages.items():
            ws = words(raw.cached(SOURCE, url(book, page)).path)
            headings: list[int] = []
            for word in sorted((w for w in ws if w[4] == "Congressional"), key=lambda w: w[1]):
                if not headings or word[1] - headings[-1] > 100:
                    headings.append(word[1])
            totals: list[int] = []
            for word in sorted((w for w in ws if w[4].upper() == "TOTAL" and w[0] < 700), key=lambda w: w[1]):
                if not totals or word[1] - totals[-1] > 60:
                    totals.append(word[1])
            if len(headings) != len(totals):
                raise ValueError(f"{year} p{page}: {len(headings)} headings, {len(totals)} totals")
            for index, (top, bottom) in enumerate(zip(headings, totals, strict=True)):
                district = first + index
                slot = (year, district)
                body = table[slot]
                for column, total in enumerate(printed[slot]):
                    if sum(votes[column] for _, _, votes in body) != total:
                        raise ValueError(f"{year} d{district} column {column}: counties do not sum to {total}")
                block = [(w, number(w[4])) for w in ws if top < w[1] <= bottom + 20 and w[0] > 600]
                block = [(w, v) for w, v in block if v is not None]
                edges = sorted({w[0] + w[2] for w, _ in block})
                groups: list[list[int]] = []
                for edge in edges:
                    if groups and edge - groups[-1][-1] <= 40:
                        groups[-1].append(edge)
                    else:
                        groups.append([edge])
                weight = [sum(1 for w, _ in block if min(g) - 2 <= w[0] + w[2] <= max(g) + 2) for g in groups]
                keep = sorted(sorted(range(len(groups)), key=lambda i: -weight[i])[: len(printed[slot])])
                right = [sum(groups[i]) / len(groups[i]) for i in keep]
                found: dict[tuple[int, int], tuple[int, int, int, int]] = {}
                points: list[tuple[int, float]] = []
                for n, (_, _, votes) in enumerate(body):
                    for column, value in enumerate(votes):
                        near = [
                            w
                            for w, v in block
                            if v == value and abs(w[0] + w[2] - right[column]) <= 35 and w[1] < bottom - 8
                        ]
                        if len(near) == 1:
                            points.append((n, near[0][1] + near[0][3] / 2))
                if len(points) >= 2 and len({n for n, _ in points}) >= 2:
                    mean_n = sum(n for n, _ in points) / len(points)
                    mean_y = sum(y for _, y in points) / len(points)
                    slope = sum((n - mean_n) * (y - mean_y) for n, y in points) / sum(
                        (n - mean_n) ** 2 for n, _ in points
                    )
                    base = mean_y - slope * mean_n
                else:
                    slope, base = 0.0, (points[0][1] if points else (top + bottom) / 2)
                height = sum(w[3] for w, _ in block) / max(len(block), 1)
                for n, (_, _, votes) in enumerate(body):
                    center = base + slope * n
                    for column, value in enumerate(votes):
                        near = [
                            w
                            for w, v in block
                            if v == value
                            and abs(w[0] + w[2] - right[column]) <= 35
                            and abs(w[1] + w[3] / 2 - center) <= 10
                        ]
                        if near:
                            w = near[0]
                            found[(n, column)] = (w[0], w[1], w[0] + w[2], w[1] + w[3])
                        else:
                            approximate += 1
                            span = 8 * len(f"{value:,}")
                            found[(n, column)] = (
                                round(right[column] - span),
                                round(center - height / 2),
                                round(right[column]),
                                round(center + height / 2),
                            )
                for n, (code, part, votes) in enumerate(body):
                    for column, value in enumerate(votes):
                        rows.append(
                            {
                                "year": year,
                                "district": district,
                                "county": code,
                                "part": part,
                                "column": column,
                                "votes": value,
                                "page": page,
                                "book": book,
                                "box": [2 * v for v in found[(n, column)]],
                            }
                        )
    frame = pl.DataFrame(rows, schema_overrides={"box": pl.List(pl.Int64)})
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        f"{frame.height} cells in {frame.select('year', 'district').unique().height} contests; {approximate} boxes estimated from row geometry"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="US-WI")
        sources: dict[int, object] = {}
        pages: dict[tuple[int, int], tuple[object, int, int]] = {}
        for book, (item, day, title) in BOOKS.items():
            sources[day.year] = cs.source(PUBLISHER, title)
            if sources[day.year] in cs.minted:
                cs.add(
                    "source",
                    id=sources[day.year],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=f"{LANDING}{item}",
                    license="Public record",
                )
            for number in PAGE_LIST[book]:
                artifact = cs.artifact(raw.cached(SOURCE, url(book, number)), sources[day.year])
                image = page_png(book, number)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, number)
                pages[(day.year, number)] = (page, width, height)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
        units = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '55%%'"
            ).fetchall()
        )
        areas = dict(
            conn.execute(
                "select code, area_id from reporting_unit where scheme_code = 'us.county' and code like '55%%'"
            ).fetchall()
        )
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':WI:house:'"
            )
        )
        for (year, district), rows in frame.group_by("year", "district", maintain_order=True):
            day = BOOKS[next(b for b, (_, d, _) in BOOKS.items() if d.year == year)][1]
            key = f"{day}:WI:house:{district}"
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
                   join reporting_unit u on u.k = v.unit_k where r.id = %s and u.scheme_code in ('us.county', 'us.county_part')""",
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
                matches = [
                    option for option, _, _, value in roots if int(value) == total and option not in targets.values()
                ]
                if len(matches) == 1:
                    targets[column] = matches[0]
            if len(targets) != len(totals) or any(
                option not in targets.values() and label != "Scattered" for option, label, _, _ in roots
            ):
                print(
                    f"{key}: columns {totals} against FEC {[(label, kind, int(value)) for _, label, kind, value in roots]}"
                )
                continue
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                **dict(
                    zip(
                        (
                            "id",
                            "election_id",
                            "contest_id",
                            "stage",
                            "kind",
                            "status",
                            "source_id",
                            "as_of",
                            "completeness",
                            "supersedes_id",
                        ),
                        row,
                        strict=True,
                    )
                ),
                is_default=False,
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
                is_default=True,
                supersedes_id=row[0],
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            for option, kind, seats in conn.execute(
                "select option_id, kind, seats from outcome where result_set_id = %s", [row[0]]
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            ((district_area,),) = conn.execute(
                "select district_area_id from contest where id = %s", [contest]
            ).fetchall()
            unit_ids = dict(units)
            for code in rows.filter(pl.col("part"))["county"].unique():
                part = f"{year}:{code}:{district}"
                unit_ids[code] = cs.unit("us.county_part", part)
                if unit_ids[code] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[code],
                        polity_id=cs.polity("US-WI"),
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=units[code],
                        area_relation="within",
                        area_id=areas[code],
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=unit_ids[code],
                        area_id=district_area,
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
            for county, column, votes, number, box in rows.select(
                "county", "column", "votes", "page", "box"
            ).iter_rows():
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit_ids[county],
                    "option_id": targets[column],
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                page, width, height = pages[(year, number)]
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=page,
                    source_id=None,
                    x0=box[0] / width,
                    y0=box[1] / height,
                    x1=box[2] / width,
                    y1=box[3] / height,
                )
        return cs.write()
