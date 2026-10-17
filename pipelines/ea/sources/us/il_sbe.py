import csv
import difflib
import io
import re
import statistics
import subprocess
import tempfile
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.il_sbe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PUBLISHER = "Illinois State Board of Elections"
ITEM = "https://archive.org/details/{item}"
FILE = "https://archive.org/download/{item}/{item}.pdf"
DIGITIZED = 8
DERIVED = 1
VOLUMES = {
    1992: ("stateofillinois1992illi", date(1992, 11, 3), range(59, 72)),
    1994: ("stateofillinoiso1994illi", date(1994, 11, 8), range(39, 52)),
    1996: ("stateofillinois1996illi", date(1996, 11, 5), range(32, 45)),
    1998: ("officialvoteof1998illi", date(1998, 11, 3), range(33, 45)),
}


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


def _render(book: raw.Artifact, page: int, dpi: int, image: Path) -> None:
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
        + [str(book.path), str(image.with_suffix(""))],
        check=True,
    )


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
                {"page": page, "district": int(head), "columns": [c.strip() for c in rest.split(",")], "rows": []}
            )
            continue
        votes = [int(cell) for cell in rest.split()]
        if len(votes) != len(tables[-1]["columns"]):
            raise ValueError(f"{path.name}: {line}")
        tables[-1]["rows"].append((head, votes))
    return tables


def parse() -> None:
    rows: list[dict[str, object]] = []
    failed: list[str] = []
    for year, (_, _, pages) in VOLUMES.items():
        book = _artifact(year)
        firsts = _tables(PARSED / "model" / f"{year}.txt")
        seconds = _tables(PARSED / "model" / f"{year}.v2.txt")
        if [(t["page"], t["district"], t["columns"], [r[0] for r in t["rows"]]) for t in firsts] != [
            (t["page"], t["district"], t["columns"], [r[0] for r in t["rows"]]) for t in seconds
        ]:
            raise ValueError(f"{year}: the two reads lay out different tables")
        for page in pages:
            tokens: list[tuple[str, tuple[int, int, int, int]]] = []
            words: list[tuple[str, tuple[int, int, int, int]]] = []
            for dpi, psm in ((300, "6"), (300, "11"), (600, "6"), (600, "11")):
                cache = PARSED / "ocr" / f"{book.sha256}-{page}-{dpi}-{psm}.tsv"
                if not cache.exists():
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory() as folder:
                        image = Path(folder) / "page.png"
                        _render(book, page, dpi, image)
                        cache.write_text(
                            subprocess.run(
                                ["tesseract", str(image), "stdout", "--psm", psm, "tsv"],
                                capture_output=True,
                                text=True,
                                check=True,
                            ).stdout
                        )
                scale = dpi // 300
                previous: tuple[tuple[str, ...], str, tuple[int, int, int, int]] | None = None
                for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
                    if row["level"] != "5":
                        continue
                    box = (
                        int(row["left"]) // scale,
                        int(row["top"]) // scale,
                        (int(row["left"]) + int(row["width"])) // scale,
                        (int(row["top"]) + int(row["height"])) // scale,
                    )
                    line = (row["block_num"], row["par_num"], row["line_num"])
                    if dpi == 300 and (plain := _plain(row["text"])):
                        words.append((plain, box))
                        if previous is not None and previous[0] == line:
                            words.append(
                                (
                                    previous[1] + plain,
                                    (previous[2][0], min(previous[2][1], box[1]), box[2], max(previous[2][3], box[3])),
                                )
                            )
                        previous = (line, plain, box)
                    if not re.search(r"\d", row["text"]):
                        continue
                    for digits in {
                        re.sub(r"\D", "", row["text"]),
                        re.sub(r"\D", "", row["text"].translate(scan.CONFUSABLE)),
                    }:
                        tokens.append((digits, box))
            for table, second in zip(firsts, seconds, strict=True):
                if table["page"] != page:
                    continue
                columns = table["columns"]
                cells = [
                    (r, c, county, value, again[c])
                    for r, ((county, votes), (_, again)) in enumerate(zip(table["rows"], second["rows"], strict=True))
                    for c, value in enumerate(votes)
                ]
                lines = [r for r, (county, _) in enumerate(table["rows"]) if county != "Totals"]
                total = next(r for r, (county, _) in enumerate(table["rows"]) if county == "Totals")
                settled: dict[tuple[int, int], int] = {(r, c): v for r, c, _, v, w in cells if v == w}
                for r, c, _, value, again in cells:
                    if (r, c) not in settled:
                        failed.append(f"{year} district {table['district']} row {r} column {c}: {value} or {again}")
                for r in range(len(table["rows"])):
                    top = sorted((settled[(r, k)] for k in range(1, len(columns)) if (r, k) in settled), reverse=True)
                    if (
                        (r, 0) in settled
                        and len(top) == len(columns) - 1
                        and settled[(r, 0)] != top[0] - (top[1] if len(top) > 1 else 0)
                    ):
                        failed.append(f"{year} district {table['district']} row {r}: the plurality does not fit")
                for c in range(1, len(columns)):
                    if (
                        all((k, c) in settled for k in [*lines, total])
                        and sum(settled[(k, c)] for k in lines) != (settled[(total, c)])
                    ):
                        failed.append(f"{year} district {table['district']} {columns[c]}: the column does not add up")
                found = {
                    (r, c): list(
                        dict.fromkeys(
                            box
                            for digits, box in sorted(tokens, key=lambda token: token[1][2] - token[1][0])
                            if digits == str(settled[(r, c)])
                        )
                    )
                    for r, c, *_ in cells
                    if r != total and (r, c) in settled
                }
                boxes: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
                for key, candidates in found.items():
                    merged: list[tuple[int, int, int, int]] = []
                    for box in candidates:
                        if not any(abs(box[0] - b[0]) < 15 and abs(box[1] - b[1]) < 15 for b in merged):
                            merged.append(box)
                    boxes[key] = merged
                anchors = {key: merged[0] for key, merged in boxes.items() if len(merged) == 1}
                for r in lines:
                    same = [c for c in range(len(columns)) if (r, c) in settled]
                    for value in {settled[(r, c)] for c in same}:
                        repeated = [c for c in same if settled[(r, c)] == value]
                        if len(repeated) > 1 and len(boxes[(r, repeated[0])]) == len(repeated):
                            for c, box in zip(repeated, sorted(boxes[(r, repeated[0])]), strict=True):
                                anchors[(r, c)] = box
                if len(lines) == 1:
                    for c in range(len(columns)):
                        pair = sorted(boxes.get((lines[0], c), []), key=lambda b: b[1])
                        if len(pair) == 2:
                            anchors[(lines[0], c)] = pair[1]
                for _ in range(2):
                    if not anchors:
                        break
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
                        if abs((b[1] + b[3]) / 2 - ys[r]) < 12 and abs(b[2] - xs[c]) < 40
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
                digit = statistics.median((b[2] - b[0]) / len(str(settled[key])) for key, b in anchors.items())
                for r in set(lines) - set(ys):
                    below, above = [k for k in ys if k < r], [k for k in ys if k > r]
                    if below and above:
                        lo, hi = max(below), min(above)
                        ys[r] = ys[lo] + (ys[hi] - ys[lo]) * (r - lo) / (hi - lo)
                    elif len(ys) >= 2:
                        step = statistics.median(ys[b] - ys[a] for a, b in zip(sorted(ys), sorted(ys)[1:]))
                        ys[r] = (
                            ys[max(below)] + step * (r - max(below))
                            if below
                            else ys[min(above)] - step * (min(above) - r)
                        )
                for c in set(range(len(columns))) - set(xs):
                    left, right = [k for k in xs if k < c], [k for k in xs if k > c]
                    if left and right:
                        lo, hi = max(left), min(right)
                        xs[c] = xs[lo] + (xs[hi] - xs[lo]) * (c - lo) / (hi - lo)
                    elif len(xs) >= 2:
                        step = statistics.median(xs[b] - xs[a] for a, b in zip(sorted(xs), sorted(xs)[1:]))
                        xs[c] = (
                            xs[min(right)] - step * (min(right) - c)
                            if right
                            else xs[max(left)] + step * (c - max(left))
                        )
                for r, c, county, value, again in cells:
                    if r == total or c == 0 or (r, c) not in settled:
                        continue
                    votes = settled[(r, c)]
                    box, how = None, None
                    if (r, c) in anchors:
                        box, how = anchors[(r, c)], "unique"
                    elif pairs := sorted(
                        (k[1] - h[3], h, k)
                        for name, h in words
                        if name == _plain(columns[c])
                        for place, k in words
                        if place == _plain(county) and k[1] > h[3]
                    ):
                        _, h, k = pairs[0]
                        right, down = xs.get(c, (h[0] + h[2]) / 2 + 25), (k[1] + k[3]) / 2
                        if r in ys and abs(ys[r] - down) < 15:
                            down = ys[r]
                        near = [
                            b
                            for b in boxes.get((r, c), [])
                            if abs((b[1] + b[3]) / 2 - down) < 12 and abs(b[2] - right) < 40
                        ]
                        if near:
                            box, how = near[0], "aligned"
                        else:
                            width = digit * len(str(votes))
                            box = (
                                round(right - width),
                                round(down - height / 2),
                                round(right),
                                round(down + height / 2),
                            )
                            how = "placed"
                    elif r in ys and c in xs:
                        near = [
                            b
                            for b in boxes.get((r, c), [])
                            if abs((b[1] + b[3]) / 2 - ys[r]) < 12 and abs(b[2] - xs[c]) < 40
                        ]
                        if near:
                            box, how = near[0], "aligned"
                        else:
                            width = digit * len(str(votes))
                            box = (
                                round(xs[c] - width),
                                round(ys[r] - height / 2),
                                round(xs[c]),
                                round(ys[r] + height / 2),
                            )
                            how = "placed"
                    if box is not None and box[3] - box[1] > 1.6 * height and r in ys:
                        box = (box[0], round(ys[r] - height / 2), box[2], round(ys[r] + height / 2))
                    if box is not None and box[2] - box[0] > 1.6 * digit * len(str(votes)) + 10 and c in xs:
                        box = (round(xs[c] - digit * len(str(votes))), box[1], round(xs[c]), box[3])
                    rows.append(
                        {
                            "year": year,
                            "page": page,
                            "district": table["district"],
                            "county": county,
                            "column": c,
                            "label": columns[c],
                            "votes": votes,
                            "box": None if box is None else list(box),
                            "located": how,
                        }
                    )
    frame = pl.DataFrame(rows, infer_schema_length=None)
    print(frame.group_by("located").len().sort("located"))
    if failed:
        for line in failed:
            print(line)
        raise ValueError(f"{len(failed)} cells or sums do not reconcile")
    missing = frame.filter(pl.col("box").is_null())
    if missing.height:
        raise ValueError(f"no box for {missing.select('year', 'district', 'county', 'column').rows()}")
    frame.write_parquet(PARSED / "values.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "values.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's House results by county from the State Board of Elections' official vote",
            polity="US",
        )
        counties: dict[str, tuple[str, UUID, UUID]] = {
            _plain(text): (code, unit, area)
            for code, unit, area, text in conn.execute(
                """select u.code, u.id, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '17%%'"""
            ).fetchall()
        }
        illinois = cs.polity("US-IL")
        contests: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value like '%%:IL:house:%%'"
            ).fetchall()
        )
        PAGES.mkdir(parents=True, exist_ok=True)
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for year, (item, _, numbers) in VOLUMES.items():
            title = f"Official Vote Cast at the General Election, November {VOLUMES[year][1]:%-d, %Y}"
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=ITEM.format(item=item),
                license="Public domain",
            )
            book = _artifact(year)
            artifact = cs.artifact(book, sources[year])
            for number in numbers:
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    _render(book, number, 100, image)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, number)
                pages[(year, number)] = (page, width, height)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{book.sha256}-{number}.png",
                    width=width,
                    height=height,
                )
        split = {
            (int(year), str(county))
            for (year, county), districts in frame.group_by("year", "county")
            if districts["district"].n_unique() > 1 or _plain(str(county)) == "cook"
        }
        for (year, district), lines in frame.group_by("year", "district", maintain_order=True):
            day = VOLUMES[int(year)][1]
            key = f"{day}:IL:house:{district}"
            if key not in contests:
                print(f"{year} district {district}: no contest")
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
            picked: dict[int, UUID] = {}
            for (column, label), cells in lines.group_by("column", "label", maintain_order=True):
                summed = int(cells["votes"].sum())
                surname = _plain(str(label))
                named = [
                    (option, total)
                    for option, kind, name, total in known
                    if kind in ("candidate", "write_in")
                    and (words := [w for w in re.sub(r"\(.*\)", "", name).split() if _plain(w) not in ("jr", "sr")])
                    and (
                        surname in _plain(" ".join(words))
                        or difflib.SequenceMatcher(None, surname, _plain(words[-1])).ratio() >= 0.8
                    )
                ]
                exact = [option for option, total in named if total == summed]
                if len(exact) == 1:
                    picked[int(column)] = exact[0]
                elif len({option for option, _ in named}) == 1:
                    picked[int(column)] = named[0][0]
                elif (
                    not named
                    and len(same := [o for o, k, _, t in known if k in ("candidate", "write_in") and t == summed]) == 1
                ):
                    picked[int(column)] = same[0]
                else:
                    print(f"{key}: no option for {label} ({summed})")
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            if result_set not in cs.minted:
                continue
            if len(picked) < lines["column"].n_unique():
                pool = cs.key("us.option", f"{key}:others:il_sbe")
                cs.add("contest_option", id=pool, contest_id=contest, kind="others", ballot_label="Others")
                picked = {int(column): picked.get(int(column), pool) for column in lines["column"].unique()}
            lines = lines.with_columns(
                option=pl.col("column").replace_strict({column: str(option) for column, option in picked.items()})
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
            units: dict[str, UUID] = {}
            for county in lines["county"].unique(maintain_order=True):
                code, parent, area = counties[_plain(county)]
                if (int(year), county) in split:
                    part = f"{year}:{code}:{district}"
                    units[county] = cs.unit("us.county_part", part)
                    cs.add(
                        "reporting_unit",
                        id=units[county],
                        polity_id=illinois,
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
                    units[county] = parent
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
                boxes = [list(box) for box in cells["box"]]
                page, width, height = pages[(int(year), int(cells["page"][0]))]
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
                    x0=min(box[0] for box in boxes) / width / 3,
                    y0=min(box[1] for box in boxes) / height / 3,
                    x1=max(box[2] for box in boxes) / width / 3,
                    y1=max(box[3] for box in boxes) / height / 3,
                )
        return cs.write()
