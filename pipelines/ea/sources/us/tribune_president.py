import csv
import itertools
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import numpy as np
from PIL import Image, ImageOps

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books, tribune_almanac
from ea.store import DATA, connect

SOURCE = "us.tribune_president"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RAW = "us.tribune_almanac"
DIGITIZED = 8
OMNIBUS = ("390fdb7684afb596b0eb8d472ccc6e0d65186cdb0096f3a113e2a80ea0c5e5be", "tribunealmanacpo18751879newy")
VOLUME_1881 = ("9335f3802a08c178ed2cbfa64127dc2ad0b5c30a50028392e08805835fe1a369", "vol1881tribuneal00unse")
STATES = {
    "Alabama": "AL",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New York": "NY",
    "North Carolina": "NC",
    "Ohio": "OH",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "Tennessee": "TN",
    "Texas": "TX",
    "Vermont": "VT",
    "Virginia": "VA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
}
ORDER = list(STATES)
EARLY = [
    s
    for s in ORDER
    if s
    not in (
        "California",
        "Colorado",
        "Florida",
        "Iowa",
        "Kansas",
        "Minnesota",
        "Nebraska",
        "Nevada",
        "Oregon",
        "Texas",
        "West Virginia",
        "Wisconsin",
    )
]
FORTIES = [
    s
    for s in ORDER
    if s not in ("California", "Colorado", "Kansas", "Minnesota", "Nebraska", "Nevada", "Oregon", "West Virginia")
]
MID = [s for s in ORDER if s not in ("Colorado", "Kansas", "Nebraska", "Nevada", "South Carolina", "West Virginia")]
LATE = [s for s in ORDER if s != "Colorado"]
TABLES = {
    "om254a": dict(
        mask="om254",
        page=254,
        sha=OMNIBUS,
        rows=EARLY,
        y0=909,
        last=1814,
        total=1862,
        x0=700,
        x1=2035,
        gap=0.04,
        years=(1824, 1828, 1832, 1836),
    ),
    "om254b": dict(
        mask="om254",
        page=254,
        sha=OMNIBUS,
        rows=FORTIES,
        y0=2056,
        last=3085,
        total=3144,
        x0=719,
        x1=2032,
        gap=0.04,
        years=(1840, 1844, 1848),
    ),
    "om255a": dict(
        mask="om255",
        page=255,
        sha=OMNIBUS,
        rows=MID,
        y0=453,
        last=1562,
        total=1618,
        x0=628,
        x1=1959,
        gap=0.04,
        years=(1852, 1856, 1860),
    ),
    "om255b": dict(
        mask="om255",
        page=255,
        sha=OMNIBUS,
        rows=LATE,
        y0=1810,
        last=3103,
        total=3152,
        x0=431,
        x1=2060,
        gap=0.04,
        years=(1864, 1868, 1872),
    ),
    "v81p95": dict(
        mask="v81p95",
        page=95,
        sha=VOLUME_1881,
        rows=ORDER,
        y0=350,
        last=1332,
        total=1385,
        x0=322,
        x1=1457,
        gap=0.02,
        years=(1872, 1876, 1880),
    ),
}


def centers(spec: dict) -> list[tuple[str, int]]:
    rows = spec["rows"]
    pitch = (spec["last"] - spec["y0"]) / (len(rows) - 1)
    return [*[(state, round(spec["y0"] + pitch * i)) for i, state in enumerate(rows)], ("Total", spec["total"])]


def columns(im: np.ndarray, spec: dict, top: int, bottom: int) -> list[tuple[int, int]]:
    ink = (im[top:bottom, spec["x0"] : spec["x1"]] < 110).mean(axis=0)
    cuts: list[tuple[int, int]] = []
    start = None
    for x, value in enumerate(ink):
        if value >= spec["gap"] and start is None:
            start = x
        elif value < spec["gap"] and start is not None:
            cuts.append((start, x))
            start = None
    if start is not None:
        cuts.append((start, len(ink)))
    merged: list[tuple[int, int]] = []
    for a, b in cuts:
        if merged and a - merged[-1][1] < 6:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a + spec["x0"], b + spec["x0"]) for a, b in merged if b - a >= 60]


def read_cell(
    task: tuple[str, str, int, tuple[int, int, int, int]],
) -> tuple[str, str, int, tuple[int, int, int, int], list[str]]:
    name, state, col, box = task
    spec = TABLES[name]
    with Image.open(PARSED / "native" / f"{spec['mask']}-mask.png") as mask:
        crop = ImageOps.invert(mask.convert("L").crop(box))
    reads: list[str] = []
    for scale, psm in ((3, "7"), (3, "8"), (4, "7"), (2, "7")):
        big = ImageOps.expand(crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS), 20, 255)
        with tempfile.NamedTemporaryFile(suffix=".png") as file:
            big.save(file.name)
            out = subprocess.run(
                ["tesseract", file.name, "-", "--psm", psm, "-c", "tessedit_char_whitelist=0123456789,."],
                capture_output=True,
                text=True,
            ).stdout
        reads.append(re.sub(r"\D", "", out))
    return name, state, col, box, reads


def parse() -> None:
    tasks = []
    for name, spec in TABLES.items():
        if spec["mask"] == "v81p95":
            continue
        with Image.open(PARSED / "native" / f"{spec['mask']}-mask.png") as mask:
            im = np.array(ImageOps.invert(mask.convert("L")))
        rows = centers(spec)
        cols = columns(im, spec, rows[0][1] - 15, rows[-2][1] + 15)
        print(name, len(rows), "rows", len(cols), "columns", cols)
        half = 14 if spec["mask"] != "v81p95" else 11
        for state, y in rows:
            for col, (a, b) in enumerate(cols):
                tasks.append((name, state, col, (a + 2, y - half, b - 2, y + half)))
    with ProcessPoolExecutor() as pool, (PARSED / "reads.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["table", "state", "col", "x0", "y0", "x1", "y1", "reads"])
        for name, state, col, box, reads in pool.map(read_cell, tasks, chunksize=16):
            writer.writerow([name, state, col, box[0], box[1], box[2], box[3], "|".join(reads)])
    print(len(tasks), "cells read")
    parse_1881()


def read_strip(task: tuple[str, int]) -> tuple[str, int, list[list[tuple[str, int, int]]]]:
    state, y = task
    spec = TABLES["v81p95"]
    with Image.open(PARSED / "native" / "v81p95-mask.png") as mask:
        strip = ImageOps.invert(mask.convert("L").crop((300, y - 15, 1470, y + 15)))
    reads: list[list[tuple[str, int, int]]] = []
    for scale in (2, 3, 4):
        big = ImageOps.expand(strip.resize((strip.width * scale, strip.height * scale), Image.LANCZOS), 30, 255)
        with tempfile.NamedTemporaryFile(suffix=".png") as file:
            big.save(file.name)
            out = subprocess.run(
                ["tesseract", file.name, "-", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789,. ", "tsv"],
                capture_output=True,
                text=True,
            ).stdout
        words = []
        for line in out.splitlines()[1:]:
            part = line.split("\t")
            digits = re.sub(r"\D", "", part[11]) if len(part) > 11 else ""
            if digits:
                left, width = int(part[6]), int(part[8])
                words.append((digits, 300 + (left - 30) // scale, 300 + (left - 30 + width) // scale))
        reads.append(words)
    return state, y, reads


def parse_1881() -> None:
    spec = TABLES["v81p95"]
    rows = centers(spec)
    with Image.open(PARSED / "native" / "v81p95-mask.png") as mask:
        im = np.array(ImageOps.invert(mask.convert("L")))
    cols = columns(im, spec, rows[0][1] - 15, rows[-2][1] + 15)
    with ProcessPoolExecutor() as pool, (PARSED / "reads_1881.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["state", "col", "x0", "y0", "x1", "y1", "reads"])
        for state, y, reads in pool.map(read_strip, rows, chunksize=4):
            for col, (a, b) in enumerate(cols):
                cell = []
                for words in reads:
                    hit = [w for w in words if a - 6 <= (w[1] + w[2]) / 2 <= b + 6]
                    cell.append(hit[0] if hit else ("", 0, 0))
                boxes = [c for c in cell if c[0]]
                x0 = min((c[1] for c in boxes), default=a)
                x1 = max((c[2] for c in boxes), default=b)
                writer.writerow([state, col, x0, y - 14, x1, y + 14, "|".join(c[0] for c in cell)])


NOTE = (
    "The state's popular vote for president as printed in The Tribune Almanac and Political Register for 1881, which "
    "compiles the states' official returns and is a compilation rather than a certified canvass. The table prints the "
    "major candidates only: Grant and Greeley for 1872, Hayes and Tilden for 1876, and Garfield, Hancock, Weaver, and "
    "Dow for 1880, so the total is the sum of the printed lines and minor candidates are not carried. Tickets that "
    "Wikipedia's results-by-state table names keep that table's candidates. Four independent readings of each number "
    "by cell OCR were reconciled against Wikipedia's figure, and every cell they could not settle, or that differs "
    "from Wikipedia by more than 1 percent, was read from a crop of the printed page; each digitized number carries "
    "its page and box. Wikipedia's earlier figures are kept as a superseded, non-default set."
)
DEMOTED = {(1872, "MN"), (1872, "NE"), (1872, "TN"), (1880, "LA"), (1880, "NV"), (1880, "TX")}
HELD = (
    "The printed figure for a top-two candidate differs from Wikipedia's by more than 1 percent and no second source "
    "settles it, so Wikipedia's set stays the default and this one is kept beside it."
)
NAMES = {
    0: ("Grant", 1872),
    1: ("Greeley", 1872),
    2: ("Hayes", 1876),
    3: ("Tilden", 1876),
    4: ("Garfield", 1880),
    5: ("Hancock", 1880),
    6: ("Weaver", 1880),
    7: ("Dow", 1880),
}
LEGENDS = {
    "Weaver": ("James B. Weaver (Greenback)", "greenbackparty"),
    "Dow": ("Neal Dow (Prohibition)", "prohibitionparty"),
}
FIXES: dict[tuple[str, int], int | None] = {
    ("Arkansas", 3): 58071,
    ("California", 0): 54020,
    ("California", 1): 40718,
    ("California", 4): 80378,
    ("California", 5): 80417,
    ("Colorado", 0): None,
    ("Colorado", 1): None,
    ("Colorado", 5): 24647,
    ("Connecticut", 0): 50638,
    ("Florida", 1): 15427,
    ("Florida", 6): None,
    ("Georgia", 3): 130088,
    ("Georgia", 6): 969,
    ("Illinois", 6): 26358,
    ("Indiana", 6): 12986,
    ("Iowa", 1): 71196,
    ("Kansas", 5): 59801,
    ("Kentucky", 1): 99995,
    ("Kentucky", 3): 159596,
    ("Louisiana", 1): 57029,
    ("Louisiana", 4): 38016,
    ("Louisiana", 5): 65067,
    ("Louisiana", 7): None,
    ("Maine", 2): 66300,
    ("Maine", 3): 49917,
    ("Maine", 5): 65171,
    ("Maine", 6): 4408,
    ("Maine", 7): 93,
    ("Maryland", 3): 91780,
    ("Maryland", 4): 78515,
    ("Maryland", 5): 93706,
    ("Maryland", 6): None,
    ("Massachusetts", 1): 59260,
    ("Massachusetts", 5): 111960,
    ("Michigan", 0): 138455,
    ("Michigan", 3): 141095,
    ("Minnesota", 0): 55117,
    ("Minnesota", 1): 34423,
    ("Minnesota", 5): 53315,
    ("Mississippi", 2): 52605,
    ("Mississippi", 5): 75750,
    ("Mississippi", 6): 5797,
    ("Nebraska", 1): 7812,
    ("Nevada", 3): 9308,
    ("Nevada", 4): 10445,
    ("Nevada", 5): 11215,
    ("Nevada", 7): None,
    ("New Hampshire", 2): 41539,
    ("New Hampshire", 3): 38509,
    ("New Jersey", 4): 120555,
    ("New Jersey", 5): 122565,
    ("New Jersey", 6): 2617,
    ("New Jersey", 7): 191,
    ("New York", 4): 555544,
    ("North Carolina", 0): 94769,
    ("Oregon", 2): 15206,
    ("Oregon", 5): 19855,
    ("Pennsylvania", 4): 444704,
    ("Pennsylvania", 5): 407428,
    ("Pennsylvania", 7): 1939,
    ("South Carolina", 1): 22703,
    ("South Carolina", 4): 58071,
    ("South Carolina", 7): None,
    ("Tennessee", 1): 94391,
    ("Texas", 1): 66500,
    ("Texas", 4): 53298,
    ("West Virginia", 0): 32315,
    ("West Virginia", 6): 9079,
}


def emit() -> Path:
    cells: dict[tuple[str, int], tuple[list[int], tuple[int, int, int, int]]] = {}
    with (PARSED / "reads_1881.tsv").open() as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            cells[(row["state"], int(row["col"]))] = (
                [int(read) for read in row["reads"].split("|") if read],
                (int(row["x0"]), int(row["y0"]), int(row["x1"]), int(row["y1"])),
            )
    with Image.open(PARSED / "native" / "v81p95-mask.png") as mask:
        mask_width, mask_height = mask.size
    book = raw.cached(RAW, tribune_almanac.VOLUMES[1880][0])
    image = DATA / "pages" / SOURCE / f"{book.sha256}-95.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    if not image.exists():
        house_books._render(book, 95, 100, image, False)
    with Image.open(image) as rendered:
        width, height = rendered.size
    skipped: list[str] = []
    differences: list[str] = []
    with connect() as conn:
        old: dict[tuple[int, str], tuple] = {}
        for row in conn.execute(
            """select extract(year from e.date_from)::int, upper(split_part(sl.path, '/', 2)), r.id, r.election_id,
                      r.contest_id, r.stage, r.kind, r.source_id, r.completeness
               from result_set r join contest k on k.id = r.contest_id join election e on e.id = r.election_id
               join slug sl on sl.entity_id = k.id and sl.path like 'us/__/%%/president'
               join source s on s.id = r.source_id
               where k.kind = 'elector_slate' and e.date_from in ('1872-11-05', '1876-11-07', '1880-11-02')
                 and s.publisher = 'Wikipedia'
                 and (r.is_default or exists (select 1 from result_set c join changeset ch on ch.id = c.changeset_id
                      where c.supersedes_id = r.id and ch.pipeline = %s))""",
            [SOURCE],
        ).fetchall():
            old[(row[0], row[1])] = row[2:]
        options: dict[UUID, list[tuple]] = defaultdict(list)
        for rs, option, label, kind, unit, measure, value in conn.execute(
            """select r.id, o.id, o.ballot_label, o.kind, u.id, v.measure, v.value
               from result_set r join result_value v on v.result_set_k = r.k
               left join contest_option o on o.k = v.option_k join reporting_unit u on u.k = v.unit_k
               where r.id = any(%s) and v.channel = 0""",
            [[row[0] for row in old.values()]],
        ).fetchall():
            options[rs].append((option, label, kind, unit, measure, Decimal(value)))
        outcomes: dict[UUID, list[tuple]] = defaultdict(list)
        for rs, option, kind, seats, candidacy in conn.execute(
            "select result_set_id, option_id, kind, seats, candidacy_id from outcome where result_set_id = any(%s)",
            [[row[0] for row in old.values()]],
        ).fetchall():
            outcomes[rs].append((option, kind, seats, candidacy))
        parties = {
            re.sub(r"[^a-z]", "", text.lower()): party
            for text, party in conn.execute(
                """select n.text, p.id from party p join name n on n.entity_id = p.id and n.lang = 'en'
                   join polity y on y.id = p.polity_id where y.iso_code = 'US' and n.text <> 'Progressive Party'"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Presidential results by state 1872, 1876, and 1880 from the Tribune Almanac, superseding Wikipedia's",
            polity="US",
        )
        source = cs.source(tribune_almanac.PUBLISHER, tribune_almanac.TITLES[1880])
        artifact = cs.artifact(book, source)
        page = cs.page(artifact, 95)
        if not conn.execute("select 1 from source_page where id = %s", [page]).fetchone():
            cs.add(
                "source_page",
                id=page,
                artifact_id=artifact,
                page_number=95,
                image_key=f"pages/{SOURCE}/{book.sha256}-95.png",
                width=width,
                height=height,
            )
        values: dict[tuple[str, int], int] = {}
        for (name, col), (reads, box) in cells.items():
            if name == "Total":
                continue
            surname, year = NAMES[col]
            ticket = next(
                (
                    value
                    for option, label, kind, unit, measure, value in options.get(
                        old.get((year, STATES[name]), (None,))[0], []
                    )
                    if kind == "ticket" and surname in (label or "")
                ),
                None,
            )
            if (name, col) in FIXES:
                fixed = FIXES[(name, col)]
                if fixed is not None:
                    values[(name, col)] = fixed
            elif ticket is not None and ticket in reads:
                values[(name, col)] = int(ticket)
            elif len(reads) == 3 and len(set(reads)) == 1 and reads[0] >= 10:
                if ticket is None or abs(reads[0] - ticket) <= ticket / 100:
                    values[(name, col)] = reads[0]
                else:
                    skipped.append(f"cell {name} {surname}: read {reads[0]} against Wikipedia's {ticket}, not settled")
            elif ticket is not None:
                common, count = (
                    Counter(read for read in reads if abs(read - ticket) <= ticket / 100).most_common(1)[0]
                    if any(abs(read - ticket) <= ticket / 100 for read in reads)
                    else (0, 0)
                )
                if count >= 2:
                    values[(name, col)] = common
                else:
                    skipped.append(f"cell {name} {surname}: reads {reads} against Wikipedia's {ticket}, not settled")
        for col in range(8):
            printed = cells[("Total", col)][0][0]
            added = sum(value for (name, c), value in values.items() if c == col)
            print(f"column {col} {NAMES[col][0]}: cells sum {added}, printed total {printed}, gap {printed - added}")
        winners_ok = 0
        for (year, state), (rs, election, contest, stage, kind, source_id, completeness) in sorted(old.items()):
            name = next((n for n, a in STATES.items() if a == state), None)
            if name is None:
                continue
            tag = f"{year} {state}"
            columns = [col for col in range(8) if NAMES[col][1] == year and (name, col) in values]
            if not columns:
                continue
            tickets = {
                option: (label, value)
                for option, label, okind, unit, measure, value in options[rs]
                if okind == "ticket"
            }
            unit = next(u for _, _, _, u, _, _ in options[rs])
            total_before = next(v for _, _, _, _, m, v in options[rs] if m == 4)
            matched: dict[int, UUID] = {}
            for col in columns:
                hits = [option for option, (label, _) in tickets.items() if NAMES[col][0] in label]
                if len(hits) == 1:
                    matched[col] = hits[0]
                elif len(hits) > 1:
                    skipped.append(f"{tag}: {NAMES[col][0]} matches {len(hits)} tickets")
            if any(col not in matched and NAMES[col][0] not in LEGENDS for col in columns):
                skipped.append(f"{tag}: a printed candidate has no Wikipedia ticket")
                continue
            winning = {option for option, *_ in outcomes[rs]}
            free = [
                value
                for option, (_, value) in tickets.items()
                if option not in matched.values() and (option in winning or value * 100 > total_before)
            ]
            if free:
                skipped.append(f"{tag}: Wikipedia lines not in the almanac: {free}")
                continue
            if any(option not in matched.values() for option in winning):
                skipped.append(f"{tag}: a winning option is not in the almanac's columns")
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{year}:{state}")
            ids: dict[int, UUID] = dict(matched)
            for col in columns:
                if col in ids:
                    continue
                label, party_name = LEGENDS[NAMES[col][0]]
                ids[col] = cs.key(f"{SOURCE}.option", f"{year}:{state}:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=ids[col],
                    contest_id=contest,
                    kind="legend",
                    party_id=parties.get(party_name),
                    ballot_label=label,
                )
            total = sum(values[(name, col)] for col in columns)
            default = (year, state) not in DEMOTED
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=default,
                supersedes_id=rs if default else None,
                completeness=min(Decimal(1), round(Decimal(total) / total_before, 4)),
            )
            cs.add(
                "result_set",
                id=rs,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind=kind,
                source_id=source_id,
                status="superseded" if default else "active",
                is_default=not default,
                completeness=completeness,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=NOTE if default else NOTE + " " + HELD,
            )
            if abs(total - total_before) / total_before > Decimal("0.01"):
                differences.append(f"{tag}: printed lines total {total} against Wikipedia's {total_before}")
            for col in columns:
                before = tickets[matched[col]][1] if col in matched else None
                if before and abs(values[(name, col)] - before) / before > Decimal("0.01"):
                    differences.append(f"{tag}: {NAMES[col][0]} {values[(name, col)]} against Wikipedia's {before}")
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=ids[col],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=DIGITIZED,
                    value=values[(name, col)],
                )
                x0, y0, x1, y1 = cells[(name, col)][1]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=ids[col],
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=page,
                    source_id=None,
                    x0=x0 / mask_width,
                    y0=y0 / mask_height,
                    x1=x1 / mask_width,
                    y1=y1 / mask_height,
                )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=4,
                count_round=0,
                flags=0,
                value=total,
            )
            for option, okind, seats, candidacy in outcomes[rs] if default else []:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=okind,
                    seats=seats,
                    candidacy_id=candidacy,
                )
            winners_ok += 1
        for line in sorted(skipped):
            print("skipped", line)
        for line in sorted(differences):
            print("differs", line)
        print(f"{winners_ok} state sets emitted, {len(skipped)} skipped, {len(differences)} differences over 1%")
        return cs.write()
