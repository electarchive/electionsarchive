import csv
import difflib
import re
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.tn_gen_98"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Tennessee Secretary of State"
TITLE = "U.S. House of Representatives, November 3, 1998 - General Election"
URL = "http://sharetngov.tnsosfiles.com/sos/election/results/1998-11/us-house.pdf"
WAYBACK = "https://web.archive.org/web/2020id_/" + URL
DAY = date(1998, 11, 3)
SUMMARY = "Tennessee's U.S. House general election of 1998 by county, read from the Secretary of State's typed returns"
METHOD = (
    "County totals are read from the Secretary of State's typed election returns (Form SS-3050, run 8 March 1999), a three-page "
    "scan. Each column's county sum equals the district total printed on the page, and each value carries its page box."
)
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})*")
CONFUSABLE = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8", "'": ","})
OVERRIDES: dict[tuple[int, str, int], int] = {
    (1, "cocke", 0): 1031,
    (1, "hawkins", 5): 11,
    (3, "grundy", 2): 11,
    (4, "hamblen", 5): 0,
    (4, "hardin", 5): 0,
    (9, "shelby", 2): 775,
}


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        content = subprocess.run(
            ["curl", "-sfL", "--resolve", "web.archive.org:443:207.241.237.2", WAYBACK], capture_output=True, check=True
        ).stdout
        raw.store(SOURCE, URL, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def page_png(page: int) -> Path:
    artifact = raw.cached(SOURCE, URL)
    target = PAGES / f"{artifact.sha256}-{page}.png"
    if not target.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", "300", "-f", str(page), "-l", str(page), "-png", "-singlefile", str(artifact.path), str(target.with_suffix(""))],
            check=True,
        )
    return target


def number(text: str) -> int | None:
    if ")" in text or "(" in text:
        return None
    cleaned = re.sub(r"[^\w,]", "", text.translate(CONFUSABLE)).rstrip(",")
    return int(cleaned.replace(",", "")) if NUMBER.fullmatch(cleaned) else None


def words(image: Path) -> list[tuple[int, int, int, int, str]]:
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(["tesseract", str(image), str(Path(folder) / "o"), "--psm", "6", "tsv"], capture_output=True, check=True)
        with (Path(folder) / "o.tsv").open() as handle:
            return [
                (int(r["left"]), int(r["top"]), int(r["width"]), int(r["height"]), r["text"].strip())
                for r in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
                if (r["text"] or "").strip() and float(r["conf"]) >= 0
            ]


def reread(image: Path, word: tuple[int, int, int, int, str]) -> int:
    with Image.open(image) as page, tempfile.TemporaryDirectory() as folder:
        crop = page.convert("L").crop((word[0] - 25, word[1] - 15, word[0] + word[2] + 25, word[1] + word[3] + 15))
        crop = crop.resize((crop.width * 3, crop.height * 3))
        target = Path(folder) / "c.png"
        crop.save(target)
        out = subprocess.run(
            ["tesseract", str(target), "-", "--psm", "8", "-c", "tessedit_char_whitelist=0123456789,"], capture_output=True, text=True
        ).stdout.strip()
    return int(out.replace(",", "")) if NUMBER.fullmatch(out) else 0


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '47%%'"""
            ).fetchall()
        }
    rows: list[dict[str, object]] = []
    failed = False
    for page in (1, 2, 3):
        image = page_png(page)
        found = words(image)
        lines: list[list[tuple[int, int, int, int, str]]] = []
        for word in sorted(found, key=lambda w: w[1] + w[3] / 2):
            if lines and abs((word[1] + word[3] / 2) - (lines[-1][0][1] + lines[-1][0][3] / 2)) < 18:
                lines[-1].append(word)
            else:
                lines.append([word])
        merged: list[list[tuple[int, int, int, int, str]]] = []
        for line in lines:
            labeled = any(re.fullmatch(r"[A-Z][A-Z .'-]+", w[4]) and w[0] < 400 for w in line)
            if merged and not any(number(w[4]) is not None for w in merged[-1]) and any(
                re.fullmatch(r"[A-Z]{3,}", w[4]) and w[0] < 400 for w in merged[-1]
            ) and not labeled and any(number(w[4]) is not None for w in line):
                merged[-1] = merged[-1] + line
            else:
                merged.append(line)
        lines = merged
        district = 0
        centers: list[float] = []
        block: list[dict[str, object]] = []
        for line in lines:
            line.sort(key=lambda w: w[0])
            text = " ".join(w[4] for w in line)
            if heading := re.match(r"\W*DISTRICT\s+(\d)\d?\W*(?:\|\s*)?$", text):
                district = int(heading[1])
            elif re.search(r"\bCOUNTY\b", text):
                digits = [w for w in line if w[4] in {"1", "2", "3", "4", "5"}]
                if len(digits) != 5:
                    raise ValueError(f"page {page} district {district}: header columns {text}")
                centers = [w[0] + w[2] / 2 for w in digits]
                centers.append(next(w[0] + w[2] / 2 for w in line if w[4].startswith("Write")))
                block = []
            elif "DISTRICT TOTAL" in text:
                printed = [
                    OVERRIDES.get((district, "TOTAL", c), number(w[4]) or (reread(image, w) if c == 5 else 0))
                    for c, w in enumerate(w for w in line if centers[0] - 120 < w[0] < centers[5] + 150 and w[4] not in {"|", ":", ";"})
                ]
                sums = [sum(int(r["votes"]) for r in block if r["column"] == c) for c in range(6)]
                if len(printed) != 6 or sums != printed:
                    print(f"page {page} district {district}: columns sum to {sums}, printed {printed} ({text})")
                    failed = True
                    print([(r["label"], r["column"], r["votes"]) for r in block if r["column"] in {0, 5}])
                rows.extend(block)
                block = []
            elif centers and "CANDIDATES" not in text and not re.search(r"\bRDA\b|\bForm\b", text) and re.match(r"^\W{0,3}[A-Z][A-Z .'-]+", text):
                label = "".join(w[4] for w in line if w[0] < centers[0] - 120 and re.search(r"[A-Za-z]", w[4]))
                label = re.sub(r"[^a-z]", "", label.lower())
                if label not in codes and (close := difflib.get_close_matches(label, codes, 1, 0.75)):
                    label = close[0]
                if label not in codes:
                    raise ValueError(f"page {page} district {district}: unknown county {label!r} in {text}")
                cells: dict[int, tuple[int, tuple[int, int, int, int]]] = {}
                for word in line:
                    if word[0] < centers[0] - 120 or word[0] > centers[5] + 150 or word[4] in {"|", ":", ";", ","}:
                        continue
                    right = word[0] + word[2]
                    column = min(range(6), key=lambda c: abs(word[0] + word[2] / 2 - centers[c]))
                    value = number(word[4])
                    if value is None and column == 5:
                        value = reread(image, word)
                    cells.setdefault(column, (OVERRIDES.get((district, label, column), value or 0), (word[0], word[1], right, word[1] + word[3])))
                for column in range(6):
                    value, box = cells.get(column, (OVERRIDES.get((district, label, column), 0), (0, 0, 0, 0)))
                    block.append(
                        {"district": district, "county": codes[label], "label": label, "column": column, "votes": value, "page": page, "box": list(box)}
                    )
    if failed:
        raise ValueError("district sums differ from the printed totals")
    frame = pl.DataFrame(rows, schema_overrides={"box": pl.List(pl.Int64)})
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("district").agg(pl.col("county").n_unique(), pl.col("votes").sum()).sort("district"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="US")
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license="Public record")
        artifact = cs.artifact(raw.cached(SOURCE, URL), source)
        pages: dict[int, tuple[object, int, int]] = {}
        for number_ in (1, 2, 3):
            image = page_png(number_)
            with Image.open(image) as rendered:
                width, height = rendered.size
            page = cs.page(artifact, number_)
            pages[number_] = (page, width, height)
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=number_,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        units = dict(conn.execute("select code, id from reporting_unit where scheme_code = 'us.county' and code like '47%%'").fetchall())
        areas = dict(conn.execute("select code, area_id from reporting_unit where scheme_code = 'us.county' and code like '47%%'").fetchall())
        contests = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':TN:house:'"))
        for (district,), rows in frame.group_by("district", maintain_order=True):
            key = f"{DAY}:TN:house:{district}"
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
            writein = [option for option, _, kind, _ in roots if kind == "write_in"]
            totals = {int(c): int(g["votes"].sum()) for (c,), g in rows.group_by("column")}
            targets: dict[int, object] = {}
            for column, total in totals.items():
                if total == 0:
                    continue
                found = [option for option, _, _, value in roots if int(value) == total and option not in targets.values()]
                if len(found) == 1:
                    targets[column] = found[0]
                elif column == 5 and len(writein) == 1:
                    targets[column] = writein[0]
            wanted = {option for option, _, _, value in roots if int(value) != 0}
            if {*targets.values()} != wanted and {*targets.values()} != wanted - {*writein}:
                print(f"{key}: {totals} against FEC {[(label, kind, int(value)) for _, label, kind, value in roots]}")
                continue
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                **dict(zip(("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id"), row, strict=True)),
                is_default=False,
            )
            cs.add(
                "result_set",
                id=result_set,
                election_id=row[1],
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                supersedes_id=row[0],
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            for option, kind, seats in conn.execute("select option_id, kind, seats from outcome where result_set_id = %s", [row[0]]).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            ((district_area,),) = conn.execute("select district_area_id from contest where id = %s", [contest]).fetchall()
            unit_ids = dict(units)
            for code in rows["county"].unique():
                if frame.filter(pl.col("county") == code)["district"].n_unique() < 2:
                    continue
                part = f"{DAY.year}:{code}:{district}"
                unit_ids[code] = cs.unit("us.county_part", part)
                if unit_ids[code] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit_ids[code],
                        polity_id=cs.polity("US-TN"),
                        scheme_code="us.county_part",
                        code=part,
                        parent_id=units[code],
                        area_relation="within",
                        area_id=areas[code],
                        kind="standard",
                        valid_from=DAY,
                        valid_to=DAY + timedelta(days=1),
                    )
                    cs.add("reporting_unit_area", unit_id=unit_ids[code], area_id=district_area, valid_from=DAY, valid_to=DAY + timedelta(days=1))
            for county, column, votes, number_, box in rows.select("county", "column", "votes", "page", "box").iter_rows():
                if column not in targets:
                    continue
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit_ids[county],
                    "option_id": targets[column],
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                page, width, height = pages[number_]
                cs.add("value_locator", **fact, page_id=page, source_id=None, x0=box[0] / width, y0=box[1] / height, x1=box[2] / width, y1=box[3] / height)
        return cs.write()
