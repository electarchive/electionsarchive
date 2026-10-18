import csv
import difflib
import io
import re
import statistics
import subprocess
import tempfile
import unicodedata
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

DIGITIZED = 8
DERIVED = 1


def _artifact(book: ModuleType, year: int) -> raw.Artifact:
    url = book.VOLUMES[year][0]
    try:
        return raw.cached(book.RAW, url)
    except LookupError:
        return raw.fetch(book.RAW, url, suffix=".pdf")


def fetch(book: ModuleType) -> None:
    for year in book.VOLUMES:
        _artifact(book, year)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def _render(book: raw.Artifact, page: int, dpi: int, image: Path, turned: bool) -> None:
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
        + [str(book.path), str(image.with_suffix(""))],
        check=True,
    )
    if turned:
        with Image.open(image) as rendered:
            rendered.rotate(-90, expand=True).save(image)


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


def parse(source: ModuleType) -> None:
    parsed = DATA / "parsed" / source.SOURCE
    rows: list[dict[str, object]] = []
    failed: list[str] = []
    columns_by: dict[tuple[int, object, int], list[int]] = {}
    printed: dict[tuple[int, object, int], int] = {}
    labels: dict[tuple[int, object], list[str]] = {}
    for year, (_, _, _, pages) in source.VOLUMES.items():
        book = _artifact(source, year)
        firsts = _tables(parsed / "model" / f"{year}.txt")
        seconds = _tables(parsed / "model" / f"{year}.v2.txt")
        if [(t["page"], t["district"], t["columns"], [r[0] for r in t["rows"]]) for t in firsts] != [
            (t["page"], t["district"], t["columns"], [r[0] for r in t["rows"]]) for t in seconds
        ]:
            raise ValueError(f"{year}: the two reads lay out different tables")
        for page in pages:
            tokens: list[tuple[str, tuple[int, int, int, int]]] = []
            words: list[tuple[str, tuple[int, int, int, int]]] = []
            for dpi, psm, threshold in (
                (300, "6", None),
                (300, "11", None),
                (600, "6", None),
                (600, "11", None),
                *((300, psm, threshold) for threshold in source.THRESHOLDS for psm in ("6", "11")),
            ):
                cache = (
                    parsed
                    / "ocr"
                    / f"{book.sha256}-{page}-{dpi}-{psm}{'' if threshold is None else f'-{threshold}'}.tsv"
                )
                if not cache.exists():
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory() as folder:
                        image = Path(folder) / "page.png"
                        _render(book, page, dpi, image, (year, page) in source.TURNED)
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
            reach: list[tuple[float, float, float]] = []
            pitch: list[float] = []
            for table, second in zip(firsts, seconds, strict=True):
                if table["page"] != page:
                    continue
                columns = table["columns"]
                known = labels.setdefault((year, table["district"]), [])
                known.extend(label for label in columns if label not in known)
                cells = [
                    (r, c, county, value, again[c])
                    for r, ((county, votes), (_, again)) in enumerate(zip(table["rows"], second["rows"], strict=True))
                    for c, value in enumerate(votes)
                ]
                lines = [r for r, (county, _) in enumerate(table["rows"]) if county != "Totals"]
                total = next((r for r, (county, _) in enumerate(table["rows"]) if county == "Totals"), None)
                settled: dict[tuple[int, int], int] = {(r, c): v for r, c, _, v, w in cells if v == w}
                for r, c, _, value, again in cells:
                    if (r, c) not in settled:
                        failed.append(f"{year} district {table['district']} row {r} column {c}: {value} or {again}")
                for r in range(len(table["rows"])):
                    top = sorted((settled[(r, k)] for k in range(1, len(columns)) if (r, k) in settled), reverse=True)
                    if (
                        (year, table["district"]) not in source.ERRATA
                        and columns[0] == "Plurality"
                        and (r, 0) in settled
                        and len(top) == len(columns) - 1
                        and settled[(r, 0)] not in (top[0], top[0] - (top[1] if len(top) > 1 else 0))
                    ):
                        failed.append(f"{year} district {table['district']} row {r}: the plurality does not fit")
                for c in range(int(columns[0] == "Plurality"), len(columns)):
                    column = columns_by.setdefault((year, table["district"], known.index(columns[c])), [])
                    column.extend(settled.get((k, c), -(10**9)) for k in lines)
                    if total is not None:
                        printed[(year, table["district"], known.index(columns[c]))] = settled.get((total, c), -1)
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
                    same = [c for c in range(len(columns)) if (lines[0], c) in settled]
                    for value in {settled[(lines[0], c)] for c in same}:
                        repeated = [c for c in same if settled[(lines[0], c)] == value]
                        found_ = boxes.get((lines[0], repeated[0]), [])
                        if len(found_) < 2:
                            continue
                        lowest = max(b[1] for b in found_)
                        bottom = sorted(b for b in found_ if lowest - b[1] < 12)
                        if len(bottom) == len(repeated):
                            for c, box in zip(repeated, bottom, strict=True):
                                anchors[(lines[0], c)] = box
                heads = {
                    c: min(pairs)[1]
                    for c in range(len(columns))
                    if len(columns) > 1
                    and (
                        pairs := [
                            (k[1] - h[3], h)
                            for name, h in words
                            if name == _plain(columns[c])
                            or (len(name) >= 5 and _plain(columns[c]).startswith(name))
                            or name in source.ALIASES.get(columns[c], ())
                            for place, k in words
                            if place == _plain(table["rows"][lines[0]][0])
                            and 0 < k[1] - h[3] < 400
                            and not any(
                                k[0] + 50 < b[0] < h[0] and abs(b[1] - h[1]) < 200
                                for name_, b in words
                                if name_ in source.BESIDE
                            )
                        ]
                    )
                }
                anchors = {
                    (r, c): b
                    for (r, c), b in anchors.items()
                    if c not in heads or (b[2] > heads[c][0] - 60 and b[0] < heads[c][2] + 150)
                }
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
                    for r, c, county, *_ in cells:
                        near = {
                            b
                            for b in boxes.get((r, c), [])
                            for name, k in words
                            if name == _plain(county)
                            and abs((k[1] + k[3]) / 2 - (b[1] + b[3]) / 2) < 12
                            and 0 < b[0] - k[2] < 700
                            and any(name_ == _plain(columns[c]) and h[3] < k[1] < h[3] + 1200 for name_, h in words)
                        }
                        if len(near) == 1:
                            anchors[(r, c)] = near.pop()
                guessed = set()
                if not anchors and reach:
                    offset = statistics.median(r_[0] for r_ in reach)
                    for r, c, county, *_ in cells:
                        if r == total or (r, c) not in settled:
                            continue
                        heads_ = [h for name, h in words if name == _plain(columns[c])]
                        near = sorted(
                            (k[1] - h[3], k)
                            for name, k in words
                            if name == _plain(county)
                            for h in heads_
                            if 0 < k[1] - h[3] < 1200 and abs(k[0] - h[0]) < 150
                        )
                        if near:
                            k = near[0][1]
                            width = statistics.median(r_[2] for r_ in reach) * len(str(settled[(r, c)]))
                            middle, half = (k[1] + k[3]) / 2, statistics.median(r_[1] for r_ in reach) / 2
                            anchors[(r, c)] = (
                                round(k[0] + offset - width),
                                round(middle - half),
                                round(k[0] + offset),
                                round(middle + half),
                            )
                            guessed.add((r, c))
                if not anchors:
                    raise ValueError(f"{year} page {page} district {table['district']}: no cell found on the page")
                for (r, c), b in anchors.items():
                    lefts = [
                        k
                        for name, k in words
                        if name == _plain(table["rows"][r][0])
                        and k[2] < b[0]
                        and abs((k[1] + k[3]) / 2 - (b[1] + b[3]) / 2) < 12
                    ]
                    if lefts and (r, c) not in guessed:
                        k = max(lefts, key=lambda k: k[2])
                        reach.append((b[2] - k[0], b[3] - b[1], (b[2] - b[0]) / len(str(settled[(r, c)]))))
                ys = {
                    r: statistics.median((b[1] + b[3]) / 2 for (rr, _), b in anchors.items() if rr == r)
                    for r in {r for r, _ in anchors}
                }
                xs = {
                    c: statistics.median(b[2] for (_, cc), b in anchors.items() if cc == c)
                    for c in {c for _, c in anchors}
                }
                for c in sorted(set(range(len(columns))) - set(xs)):
                    lo = max((xs[k] for k in xs if k < c), default=None)
                    hi = min((b[0] for (_, k), b in anchors.items() if k > c), default=None)
                    for r in lines:
                        near = [
                            b
                            for b in boxes.get((r, c), [])
                            if r in ys
                            and abs((b[1] + b[3]) / 2 - ys[r]) < 12
                            and (lo is None or b[0] > lo)
                            and (hi is None or b[2] < hi)
                        ]
                        if len(near) == 1:
                            anchors[(r, c)] = near[0]
                    if any(k == c for _, k in anchors):
                        xs[c] = statistics.median(b[2] for (_, k), b in anchors.items() if k == c)
                height = statistics.median(b[3] - b[1] for b in anchors.values())
                digit = statistics.median((b[2] - b[0]) / len(str(settled[key])) for key, b in anchors.items())
                pitch.extend((ys[b] - ys[a]) / (b - a) for a, b in zip(sorted(ys), sorted(ys)[1:]))
                for r in set(lines) - set(ys):
                    below, above = [k for k in ys if k < r], [k for k in ys if k > r]
                    if below and above:
                        lo, hi = max(below), min(above)
                        ys[r] = ys[lo] + (ys[hi] - ys[lo]) * (r - lo) / (hi - lo)
                    elif len(ys) >= 2 or (ys and pitch):
                        step = (
                            statistics.median((ys[b] - ys[a]) / (b - a) for a, b in zip(sorted(ys), sorted(ys)[1:]))
                            if len(ys) >= 2
                            else statistics.median(pitch)
                        )
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
                        step = statistics.median((xs[b] - xs[a]) / (b - a) for a, b in zip(sorted(xs), sorted(xs)[1:]))
                        xs[c] = (
                            xs[min(right)] - step * (min(right) - c)
                            if right
                            else xs[max(left)] + step * (c - max(left))
                        )
                for r, c, county, value, again in cells:
                    if r == total or columns[c] == "Plurality" or (r, c) not in settled:
                        continue
                    votes = settled[(r, c)]
                    box, how = None, None
                    if (r, c) in anchors:
                        box, how = anchors[(r, c)], "placed" if (r, c) in guessed else "unique"
                    elif pairs := sorted(
                        (k[1] - h[3], h, k)
                        for name, h in words
                        if name == _plain(columns[c])
                        or (len(name) >= 5 and _plain(columns[c]).startswith(name))
                        or name in source.ALIASES.get(columns[c], ())
                        for place, k in words
                        if place == _plain(county)
                        and k[1] > h[3]
                        and not any(
                            k[0] + 50 < b[0] < h[0] and abs(b[1] - h[1]) < 200
                            for name_, b in words
                            if name_ in source.BESIDE
                        )
                    ):
                        _, h, k = pairs[0]
                        right = xs[c] if any(cc == c for _, cc in anchors) else (h[0] + h[2]) / 2 + 25
                        down = (k[1] + k[3]) / 2
                        if r in ys and (len(columns) == 1 or abs(ys[r] - down) < 15):
                            down = ys[r]
                        near = sorted(
                            (
                                b
                                for b in boxes.get((r, c), [])
                                if abs((b[1] + b[3]) / 2 - down) < 12 and abs(b[2] - right) < 80
                            ),
                            key=lambda b: abs(b[2] - right),
                        )
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
                            "column": known.index(columns[c]),
                            "label": columns[c],
                            "votes": votes,
                            "box": None if box is None else list(box),
                            "located": how,
                        }
                    )
    for (year, district, c), column in columns_by.items():
        if (
            (year, district) not in source.ERRATA
            and (year, district, c) in printed
            and sum(column) != printed[(year, district, c)]
        ):
            failed.append(f"{year} district {district} column {c}: the column does not add up")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    print(frame.group_by("located").len().sort("located"))
    if failed:
        for line in failed:
            print(line)
        raise ValueError(f"{len(failed)} cells or sums do not reconcile")
    missing = frame.filter(pl.col("box").is_null())
    if missing.height:
        raise ValueError(f"no box for {missing.select('year', 'district', 'county', 'column').rows()}")
    frame.write_parquet(parsed / "values.parquet")


def emit(source: ModuleType) -> Path:
    frame = pl.read_parquet(DATA / "parsed" / source.SOURCE / "values.parquet")
    folder = DATA / "pages" / source.SOURCE
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=source.SOURCE,
            version=source.VERSION,
            summary=source.SUMMARY,
            polity="US",
        )
        counties: dict[str, tuple[str, UUID, UUID]] = {
            _plain(text): (code, unit, area)
            for code, unit, area, text in conn.execute(
                """select u.code, u.id, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like %s""",
                [f"{source.FIPS}%"],
            ).fetchall()
        }
        state = cs.polity(f"US-{source.STATE}")
        contests: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value like %s",
                [f"%:{source.STATE}:house:%"],
            ).fetchall()
        )
        folder.mkdir(parents=True, exist_ok=True)
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], tuple[UUID, int, int]] = {}
        for year, (_, landing, day, numbers) in source.VOLUMES.items():
            title = source.TITLE.format(day=day)
            if (made := cs.source(source.PUBLISHER, title)) not in sources.values():
                cs.add(
                    "source",
                    id=made,
                    kind="official_body",
                    publisher=source.PUBLISHER,
                    title=title,
                    url=landing,
                    license=source.LICENSE,
                )
            sources[year] = made
            book = _artifact(source, year)
            artifact = cs.artifact(book, made)
            for number in numbers:
                image = folder / f"{book.sha256}-{number}.png"
                if not image.exists():
                    _render(book, number, 100, image, (year, number) in source.TURNED)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                page = cs.page(artifact, number)
                pages[(year, number)] = (page, width, height)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{source.SOURCE}/{book.sha256}-{number}.png",
                    width=width,
                    height=height,
                )
        split = {
            (int(year), str(county))
            for (year, county), districts in frame.group_by("year", "county")
            if districts["district"].n_unique() > 1 or _plain(str(county)) == "cook"
        }
        for (year, district), lines in frame.group_by("year", "district", maintain_order=True):
            day = source.VOLUMES[int(year)][2]
            key = f"{day}:{source.STATE}:house:{district}"
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
                elif summed == 0:
                    lines = lines.filter(pl.col("column") != column)
                else:
                    print(f"{key}: no option for {label} ({summed})")
            result_set = cs.key(
                f"{source.SOURCE}.result_set", f"{key}:county" + (f":{source.REVISION}" if source.REVISION > 1 else "")
            )
            previous = (
                cs.key(f"{source.RENAMED}.result_set", f"{key}:county")
                if source.RENAMED is not None
                else cs.key(
                    f"{source.SOURCE}.result_set",
                    f"{key}:county" + (f":{source.REVISION - 1}" if source.REVISION > 2 else ""),
                )
                if source.REVISION > 1
                else None
            )
            if result_set not in cs.minted:
                continue
            if len(picked) < lines["column"].n_unique():
                rest = int(lines.filter(~pl.col("column").is_in(list(picked)))["votes"].sum())
                lumped = [
                    o
                    for o, k, _, t in known
                    if k in ("write_in", "others")
                    and t
                    == rest
                    + int(lines.filter(pl.col("column").is_in([c for c, p in picked.items() if p == o]))["votes"].sum())
                ]
                empty = [o for o, k, _, t in known if k == "others" and t is None]
                if len(lumped) == 1:
                    pool = lumped[0]
                elif not lumped and len(empty) == 1:
                    pool = empty[0]
                else:
                    pool = cs.key("us.option", f"{key}:others:{source.SOURCE.removeprefix('us.')}")
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
            for id, status in ((previous, "superseded"), (result_set, "active")):
                if id is not None:
                    cs.add(
                        "result_set",
                        id=id,
                        election_id=election,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status=status,
                        source_id=sources[int(year)],
                        is_default=False,
                        supersedes_id=previous if id == result_set else None,
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
                        polity_id=state,
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
                    flags=(DIGITIZED if "box" in frame.columns else 0) | (DERIVED if cells.height > 1 else 0),
                    value=int(cells["votes"].sum()),
                )
                if "box" not in frame.columns:
                    continue
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
