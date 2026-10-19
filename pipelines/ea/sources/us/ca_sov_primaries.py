import difflib
import itertools
import json
import re
import statistics
import struct
import subprocess
from datetime import date
from pathlib import Path
from typing import TypedDict
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ca_sov_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
SITE = "https://elections.cdn.sos.ca.gov/sov/"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote"
FILES = {
    (date(1994, 6, 7), "house"): "1994-primary/us-representative.pdf",
    (date(1994, 6, 7), "senate"): "1994-primary/us-senator.pdf",
    (date(1996, 3, 26), "house"): "1996-primary/us-representative.pdf",
    (date(1998, 6, 2), "house"): "1998-primary/us-rep-congress.pdf",
    (date(1998, 6, 2), "senate"): "1998-primary/us-senator.pdf",
}
SEEN = {
    ("1996-03-26:CA:house:25", "gerber"): 101,
    ("1996-03-26:CA:house:28", "levering"): 32279,
    ("1998-06-02:CA:house:42", "hollis"): 1217,
}
CHECKED = {
    ("1994-06-07:CA:house:16", "mcenery"),
    ("1994-06-07:CA:house:16", "dykes"),
    ("1994-06-07:CA:house:25", "gilmartin"),
    ("1994-06-07:CA:house:25", "mckeon"),
    ("1994-06-07:CA:house:27", "gibbs"),
    ("1994-06-07:CA:house:27", "hurst"),
    ("1994-06-07:CA:house:29", "regan"),
    ("1994-06-07:CA:house:30", "weilberg"),
    ("1994-06-07:CA:house:31", "garcia"),
    ("1994-06-07:CA:house:31", "romero"),
    ("1994-06-07:CA:house:34", "torres"),
    ("1994-06-07:CA:house:42", "brown"),
    ("1994-06-07:CA:house:42", "guzman"),
    ("1994-06-07:CA:house:42", "bine"),
    ("1994-06-07:CA:house:42", "sanchez"),
    ("1994-06-07:CA:house:43", "calvert"),
    ("1994-06-07:CA:house:47", "frogue"),
    ("1994-06-07:CA:house:49", "steel"),
    ("1994-06-07:CA:house:50", "krueger"),
    ("1994-06-07:CA:house:50", "ramirez"),
    ("1996-03-26:CA:house:27", "zucker"),
    ("1996-03-26:CA:house:28", "saurenman"),
    ("1996-03-26:CA:house:34", "nunez"),
    ("1996-03-26:CA:house:36", "savodnick"),
    ("1996-03-26:CA:house:37", "murray"),
    ("1996-03-26:CA:house:43", "kimbrough"),
    ("1996-03-26:CA:house:44", "hurst"),
    ("1996-03-26:CA:house:44", "bono"),
    ("1996-03-26:CA:house:46", "sanchez"),
    ("1996-03-26:CA:house:46", "stafford"),
    ("1996-03-26:CA:house:50", "shepard"),
    ("1996-03-26:CA:house:51", "clark"),
}
EDGES = {1994: 0.215, 1996: 0.235, 1998: 0.135}


class Cell(TypedDict):
    county: str
    column: int
    box: list[float]
    page: int
    reads: list[str]


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, SITE + path, suffix=".pdf")


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def page(document: Path, number: int, scanned: bool) -> tuple[Path, list[scan.Word], float]:
    image = PARSED / "pages" / f"{document.stem}-{number}.png"
    if not image.exists():
        image.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
            + [str(document), str(image.with_suffix(""))],
            check=True,
        )
    if not scanned:
        return image, [(text, box, (0, 0, 0), 100.0) for text, box in scan.layer(document, number)], 0.0
    found = scan.words(document, number, image.with_suffix(".tsv"))
    return image, found, scan.skew(found)


def blocks(
    key: tuple[date, str], counties: list[str], districts: dict[int, list[str]]
) -> list[tuple[int, list[str], list[Cell], dict[int, Cell]]]:
    document = raw.cached(SOURCE, SITE + FILES[key]).path
    scanned = key[0].year != 1998
    layer = key[0].year == 1994
    pages = int(
        next(
            line.split()[1]
            for line in subprocess.run(
                ["pdfinfo", str(document)], capture_output=True, text=True, check=True
            ).stdout.splitlines()
            if line.startswith("Pages:")
        )
    )
    lookup = {plain(name): name for name in counties}
    district = 0
    found: list[tuple[int, list[str], list[Cell], dict[int, Cell]]] = []
    shown: list[tuple[Path, list[str]]] = []
    sheets: dict[int, tuple[Path, list[tuple[str, scan.Box]], float, int]] = {}
    pending: list[
        tuple[int, int, list[scan.Word], list[tuple[str | None, list[scan.Word]]], list[scan.Word] | None]
    ] = []
    for number in range(1, pages + 1):
        image, words, slope = page(document, number, scanned)
        width = Image.open(image).width
        edge = width * EDGES[key[0].year]
        sheets[number] = (image, scan.layer(document, number) if layer else [], slope, width)
        header: list[scan.Word] = []
        rows: list[tuple[str | None, list[scan.Word]]] = []
        totals: list[scan.Word] | None = None

        for line in scan.lines(words, slope):
            label = " ".join(word[0] for word in line if word[1][0] < edge)
            values = [word for word in line if word[1][0] >= edge]
            name = plain(label)
            text = " ".join(word[0] for word in line)
            if "Representative" not in text and any(
                difflib.SequenceMatcher(None, plain(word[0]), "congressional").ratio() > 0.7 for word in line
            ):
                pending.append((district, number, header, rows, totals))
                header, rows, totals = [], [], None
                printed = re.match(r"\d+", text.split()[0])
                district = (
                    int(printed[0]) if printed and int(printed[0]) in (district + 1, district + 2) else district + 1
                )
                continue
            if name.startswith("per") or any("%" in word[0] for word in values) or re.fullmatch(r"Page \d+", text):
                continue
            if values and ("tot" in name or difflib.SequenceMatcher(None, name[-6:], "totals").ratio() > 0.6):
                pending.append((district, number, header, rows, values))
                header, rows, totals = [], [], None
                continue
            if sum(bool(re.fullmatch(r"\d{1,3}[.,]\d\d\D{0,3}", word[0])) for word in values) * 2 > len(values):
                continue
            match = difflib.get_close_matches(name, lookup, n=1, cutoff=0.55) if name else []
            digits = sum(any(c.isdigit() for c in word[0]) for word in values)
            if values and (match and (digits or scanned) or digits * 2 > len(values)):
                rows.append((lookup[match[0]] if match else None, values))
                continue
            if rows:
                pending.append((district, number, header, rows, totals))
                header, rows, totals = [], [], None
            header.extend(line)
        pending.append((district, number, header, rows, totals))
    for district, number, header, rows, totals in pending:
        image, texts, slope, width = sheets[number]
        if rows:
            expected = districts.get(district, counties)
            if len(rows) == len(expected):
                named = list[str | None](expected)
            else:
                named: list[str | None] = [county if county in expected else None for county, _ in rows]
                for _ in range(2):
                    for index in range(len(named)):
                        before = named[index - 1] if index else None
                        after = named[index + 1] if index + 1 < len(named) else None
                        if named[index] is None and before is not None and before in expected[:-1]:
                            named[index] = expected[expected.index(before) + 1]
                        if named[index] is None and after is not None and after in expected[1:]:
                            named[index] = expected[expected.index(after) - 1]
            for (county, line), name in zip(rows, named, strict=True):
                if name is None or county is not None and county != name:
                    print(
                        f"{key[0]} {key[1]} {district} p{number}: row {county} {[w[0] for w in line][:3]} read as {name}"
                    )
        kept = [(name, line) for (_, line), name in zip(rows, named, strict=True) if name is not None] if rows else []
        if kept:
            block = (district, *columns(image, header, kept, totals, texts, slope, number, scanned))
            if scanned:
                every = [*header, *(word for _, line in kept for word in line), *(totals or [])]
                crop = PARSED / "crops" / f"{key[0].year}-{key[1]}-{len(found)}.png"
                crop.parent.mkdir(parents=True, exist_ok=True)
                Image.open(image).crop(
                    (0, min(word[1][1] for word in every) - 30, width, max(word[1][3] for word in every) + 30)
                ).save(crop)
                shown.append((crop, [county for county, _ in kept] + (["District Totals"] if totals else [])))
            found.append(block)
    if scanned:
        (PARSED / "crops" / f"{key[0].year}-{key[1]}.json").write_text(
            json.dumps(
                [
                    {"crop": str(crop), "rows": labels, "columns": len(block[1])}
                    for (crop, labels), block in zip(shown, found, strict=True)
                ],
                indent=1,
            )
        )
        model = PARSED / "reads" / f"{key[0].year}-{key[1]}.jsonl"
        reads = {
            Path(line["crop"]).name: line
            for line in map(json.loads, model.read_text().splitlines() if model.exists() else [])
        }
        for (district, heads, cells, total), (crop, labels) in zip(found, shown, strict=True):
            line = reads.get(crop.name)
            if line is None:
                print(f"{key[0]} {key[1]} {district}: no model read for {crop}")
                continue
            width = {len(row) for row in line["rows"]}
            if len(line["rows"]) != len(labels) or len(width) != 1:
                print(f"{key[0]} {key[1]} {district}: model read of {crop} has another shape")
                continue
            grid = {(cell["county"] or "District Totals", cell["column"]): cell for cell in [*cells, *total.values()]}
            aligned: dict[int, int] = {index: index for index in range(len(heads)) if {len(heads)} == width}
            for index in range(len(heads) if not aligned else 0):
                agree = [
                    sum(
                        line["rows"][row][other] is not None
                        and (label, index) in grid
                        and line["rows"][row][other] in candidates(grid[(label, index)]["reads"], True)
                        for row, label in enumerate(labels)
                    )
                    for other in range(len(line["rows"][0]))
                ]
                best = max(range(len(agree)), key=agree.__getitem__) if agree else None
                if best is not None and agree[best] * 2 >= len(labels) and agree.count(agree[best]) == 1:
                    aligned[index] = best
            if len(set(aligned.values())) != len(aligned):
                print(f"{key[0]} {key[1]} {district}: model read of {crop} aligns ambiguously")
                continue
            for index, other in aligned.items():
                if len(line["headers"]) == len(line["rows"][0]):
                    heads[index] = f"{line['headers'][other].split('|')[0]} {heads[index]}"
                for row, label in enumerate(labels):
                    value = line["rows"][row][other]
                    if (label, index) in grid and value is not None:
                        grid[(label, index)]["reads"] += [str(value), str(value)]
    return found


def columns(
    image: Path,
    header: list[scan.Word],
    rows: list[tuple[str, list[scan.Word]]],
    totals: list[scan.Word] | None,
    texts: list[tuple[str, scan.Box]],
    slope: float,
    number: int,
    scanned: bool,
) -> tuple[list[str], list[Cell], dict[int, Cell]]:
    lines = [*rows, *([("", totals)] if totals else [])]
    rights: list[list[scan.Word]] = []
    for word in sorted((word for _, line in lines for word in line), key=lambda word: word[1][2]):
        if rights and word[1][2] - rights[-1][-1][1][2] < 30:
            rights[-1].append(word)
        else:
            rights.append([word])
    edges = [
        (sorted(word[1][2] for word in group)[len(group) // 2], max(word[1][2] - word[1][0] for word in group))
        for group in rights
    ]
    heads: list[list[scan.Word]] = [[] for _ in edges]
    for word in header:
        middle = (word[1][0] + word[1][2]) / 2
        nearest = min(range(len(edges)), key=lambda i: abs(middle - (edges[i][0] - edges[i][1] / 2)))
        if abs(middle - (edges[nearest][0] - edges[nearest][1] / 2)) < 160:
            heads[nearest].append(word)
    cells: list[tuple[str, int, scan.Box, str]] = []
    for county, line in lines:
        top = statistics.median(word[1][1] for word in line)
        bottom = statistics.median(word[1][3] for word in line)
        for index, (right, wide) in enumerate(edges):
            inside = [word for word in line if abs(word[1][2] - right) < 30]
            if inside:
                box = (
                    min(right - wide - 12, *(word[1][0] - 6 for word in inside)),
                    min(word[1][1] for word in inside) - 8,
                    max(right, *(word[1][2] for word in inside)) + 10,
                    max(word[1][3] for word in inside) + 8,
                )
            else:
                box = (right - wide - 12, round(top) - 8, right + 10, round(bottom) + 8)
            cells.append((county, index, box, " ".join(word[0] for word in inside)))
    cache = image.with_suffix(".cells.json")
    known: dict[str, list[str]] = json.loads(cache.read_text()) if cache.exists() else {}
    missing = [box for _, _, box, _ in cells if scanned and str(box) not in known]
    if missing:
        known |= {
            str(box): read
            for box, read in zip(missing, scan.read(image, [(box, True) for box in missing], "eng"), strict=True)
        }
        cache.write_text(json.dumps(known))
    reads = [known.get(str(box), []) for _, _, box, _ in cells]
    found: list[Cell] = []
    total: dict[int, Cell] = {}
    size = Image.open(image).size
    for (county, index, box, text), more in zip(cells, reads, strict=True):
        laid = " ".join(
            word
            for word, at in texts
            if at[0] >= box[0] - 4 and at[2] <= box[2] + 4 and box[1] - 4 <= (at[1] + at[3]) / 2 <= box[3] + 4
        )
        cell: Cell = {
            "county": county,
            "column": index,
            "box": [box[0] / size[0], box[1] / size[1], box[2] / size[0], box[3] / size[1]],
            "page": number,
            "reads": [text, laid, *more],
        }
        if county:
            found.append(cell)
        else:
            total[index] = cell
    return (
        [
            " ".join(word[0] for word in sorted(group, key=lambda word: (scan.level(word, slope) // 25, word[1][0])))
            for group in heads
        ],
        found,
        total,
    )


def candidates(reads: list[str], scanned: bool) -> dict[int, int]:
    if not scanned:
        text = reads[0].replace(",", "")
        return {int(text): 1} if text.isdigit() else {}
    score: dict[int, int] = {}
    for read in reads:
        for value, weight in scan.numbers(read).items():
            score[value] = score.get(value, 0) + weight
    return score


def solve(cells: list[dict[int, int]], printed: dict[int, int], filed: int | None) -> list[int] | None:
    best = [max(cell, key=cell.__getitem__) if cell else 0 for cell in cells]
    if not printed:
        return best if sum(best) == filed else None
    targets = {*printed, *([filed] if filed is not None else [])}
    if sum(best) in targets:
        return best
    tried: list[list[int]] = []
    for index, cell in enumerate(cells):
        for value in cell:
            if value != best[index] and sum(best) - best[index] + value in targets:
                tried.append([*best[:index], value, *best[index + 1 :]])
    if tried:
        scored = sorted(
            (sum(cell.get(value, 0) for cell, value in zip(cells, fixed, strict=True)), fixed) for fixed in tried
        )
        if len(scored) == 1 or scored[-1][0] > scored[-2][0]:
            return scored[-1][1]
    if not tried:
        for first, second in itertools.combinations(range(len(cells)), 2):
            for one in cells[first]:
                for two in cells[second]:
                    if (
                        one != best[first]
                        and two != best[second]
                        and sum(best) - best[first] - best[second] + one + two in targets
                    ):
                        fixed = list(best)
                        fixed[first], fixed[second] = one, two
                        tried.append(fixed)
        if len(tried) == 1:
            return tried[0]
    return None


def surname(text: str) -> str:
    if plain(text).startswith("noneof"):
        return "noneof"
    words = [word for word in re.split(r"[\s,]+", text) if plain(word) not in {"jr", "sr", "ii", "iii", "iv", ""}]
    return plain(words[-1]) if words else ""


def uncast(head: str) -> bool:
    return (
        sum(
            any(difflib.SequenceMatcher(None, plain(word), term).ratio() >= 0.75 for term in ("votes", "cast", "race"))
            for word in head.split()
        )
        >= 2
    )


def parse() -> None:
    with connect() as conn:
        counties = [
            text
            for (text,) in conn.execute(
                """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            )
        ]
        options = conn.execute(
            """select x.value, o.id::text, o.ballot_label, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ '^199[468]-0[36]-..:CA:(house|senate):'
               group by 1, 2, 3"""
        ).fetchall()
    if len(counties) != 58:
        raise ValueError("California needs 58 counties")
    counties.sort()
    districts = {
        district: [str(cell["county"]) for cell in found if cell["column"] == 0]
        for district, _, found, _ in blocks((date(1998, 6, 2), "house"), counties, {})
    }
    rows: list[dict[str, object]] = []
    failed: set[str] = set()
    for key, path in FILES.items():
        scanned = key[0].year != 1998
        pool: dict[int, list[tuple[str, str, str, int]]] = {}
        for contest, option, label, votes in options:
            day, _, office, *rest = contest.split(":")
            if day == key[0].isoformat() and office == key[1]:
                pool.setdefault(int(rest[0]) if office == "house" else 0, []).append((contest, option, label, votes))
        cells: dict[tuple[int, str], list[Cell]] = {}
        totals: dict[tuple[int, str], Cell] = {}
        anchor: list[tuple[float, str]] = []
        for district, heads, found, total in blocks(key, counties, districts):
            picked: dict[int, str] = {}
            scores = sorted(
                (
                    (
                        max(
                            difflib.SequenceMatcher(None, plain(word), surname(label)).ratio()
                            for word in [*head.split(), "noneof" if "none of" in head.lower() else ""]
                        ),
                        index,
                        option,
                    )
                    for index, head in enumerate(heads)
                    if head.split() and not uncast(head)
                    for _, option, label, _ in pool.get(district, [])
                ),
                reverse=True,
            )
            for score, index, option in scores:
                if score >= 0.75 and index not in picked and option not in picked.values():
                    picked[index] = option
            rights = {
                index: statistics.median(cell["box"][2] for cell in found if cell["column"] == index)
                for index in {cell["column"] for cell in found}
            }
            if key[1] == "senate" and len(picked) * 2 >= len(heads):
                anchor = [(rights[index], option) for index, option in picked.items() if index in rights]
            elif key[1] == "senate":
                for index, right in rights.items():
                    near = [option for edge, option in anchor if abs(edge - right) < 0.015]
                    if index not in picked and len(near) == 1 and near[0] not in picked.values():
                        picked[index] = near[0]
            for index, head in enumerate(heads):
                if index not in picked and head and not uncast(head):
                    print(f"{key[0]} {key[1]} {district}: no option for column {head!r}")
            for cell in found:
                if cell["column"] in picked:
                    cells.setdefault((district, picked[cell["column"]]), []).append(cell)
            for index, cell in total.items():
                if index in picked:
                    totals[(district, picked[index])] = cell
        for district, entries in sorted(pool.items()):
            for contest, option, label, votes in entries:
                column = cells.get((district, option))
                if column is None:
                    failed.add(contest)
                    print(f"{key[0]} {key[1]} {district}: {label} ({votes}) has no column")
                    continue
                total = totals.get((district, option))
                printed = candidates(total["reads"], scanned) if total else {}
                seen = (contest.rsplit(":", 1)[0], surname(label))
                if not printed and len(column) == 1 and (seen in SEEN or seen in CHECKED):
                    values = [SEEN.get(seen, votes)]
                else:
                    values = solve([candidates(cell["reads"], scanned) for cell in column], printed, votes)
                if values is None:
                    failed.add(contest)
                    print(
                        f"{key[0]} {key[1]} {district}: {label} unsolved, filed {votes}, printed {printed}, "
                        f"cells {[(cell['county'], cell['reads']) for cell in column]}"
                    )
                    continue
                if printed and sum(values) not in printed or sum(values) != votes:
                    print(
                        f"{key[0]} {key[1]} {district}: {label} sums to {sum(values)}, printed {printed}, filed {votes}"
                    )
                for cell, value in zip(column, values, strict=True):
                    rows.append(
                        {
                            "file": path,
                            "contest": contest,
                            "option": option,
                            "county": cell["county"],
                            "votes": value,
                            "page": cell["page"],
                            "box": cell["box"],
                        }
                    )
    print(f"{len(failed)} contests left out: {sorted(failed)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([row for row in rows if row["contest"] not in failed]).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's U.S. House and Senate primaries by county in 1994, 1996, and 1998, "
            "from the Secretary of State's Statements of Vote",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
            license="Public record",
        )
        pages: dict[tuple[str, int], UUID] = {}
        artifacts: dict[str, UUID] = {}
        for path, number in frame.select("file", "page").unique().sort("file", "page").iter_rows():
            pdf = raw.cached(SOURCE, SITE + path)
            if path not in artifacts:
                artifacts[path] = cs.artifact(pdf, source)
            artifact = artifacts[path]
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            pages[(path, number)] = cs.page(artifact, number)
            cs.add(
                "source_page",
                id=pages[(path, number)],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        counties: dict[str, UUID] = {
            text: unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            ).fetchall()
        }
        contests: dict[str, UUID] = dict(
            conn.execute(
                """select value, entity_id from external_id
                   where scheme = 'us.contest' and value ~ '^199[468]-0[36]-..:CA:(house|senate):'"""
            ).fetchall()
        )
        columns = (
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
        )
        for (key,), lines in frame.group_by("contest", maintain_order=True):
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            contest = contests[key]
            ((election,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            labels = dict(conn.execute("select id, ballot_label from contest_option where contest_id = %s", [contest]))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            totals = {
                UUID(option): votes for option, votes in lines.group_by("option").agg(pl.col("votes").sum()).iter_rows()
            }
            differs = [
                f"{labels[option]}: {votes:,} in the Statement of Vote, {on_file[option]:,} in the FEC's report"
                for option, votes in totals.items()
                if on_file.get(option) is not None and on_file[option] != votes
            ]
            if differs:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The Secretary of State's certified totals differ from the FEC's: "
                    + "; ".join(differs)
                    + ".",
                )
            for path, option, county, votes, number, box in lines.select(
                "file", "option", "county", "votes", "page", "box"
            ).iter_rows():
                fact = {
                    "result_set_id": result_set,
                    "unit_id": counties[county],
                    "option_id": UUID(option),
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=votes)
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=pages[(path, number)],
                    source_id=None,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                )
            for option, kind, seats in outcomes:
                if option in totals:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
        return cs.write()
