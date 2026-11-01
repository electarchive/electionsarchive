import base64
import json
import re
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image, ImageOps

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.nc_abstract_1992"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
WORK = DATA / "work" / "nc92"
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Abstract of votes cast for Council of State at the general election held on November 3, 1992"
RECORD = "https://digital.ncdcr.gov/Documents/Detail/4199403"
RECORD_GOVERNOR = "https://digital.ncdcr.gov/Documents/Detail/4199520"
RECORD_LIEUTENANT = "https://digital.ncdcr.gov/Documents/Detail/4199518"
RECORDS = ((RECORD, 0), (RECORD_GOVERNOR, 12), (RECORD_LIEUTENANT, 14))
DAY = date(1992, 11, 3)
MANUAL = {4: {"WATAUGA": [8267, 8564, 690, 8593, 8209, 867]}}
PASSES = [
    (120, "6", 300),
    (150, "6", 300),
    (135, "4", 300),
    (100, "6", 300),
    (110, "6", 300),
    (160, "6", 300),
    (120, "4", 300),
    (170, "6", 300),
]
SEEN = {13, 14, 15, 16}
VARIANTS = (200, 300)
PROMPT = (
    "This is a typed table of county vote totals from a North Carolina State Board of Elections abstract. "
    "Transcribe every county row and the final TOTAL row, if present on this page, exactly as printed. "
    'Answer with only a JSON array of arrays: ["COUNTY NAME", votes for first column, second column, third column]. '
    "Votes are integers without commas."
)
SHEETS = {
    (1, 2): [
        (
            "Secretary of State",
            [
                ("Rufus L. Edmisten", "DEMOCRATIC"),
                ("John H. Carrington", "REPUBLICAN"),
                ("H. R. (Dick) Parker, Jr.", "LIBERTARIAN"),
            ],
        ),
        ("Auditor", [("Ralph Campbell", "DEMOCRATIC"), ("J. Vernon Abernethy", "REPUBLICAN")]),
    ],
    (3, 4): [
        (
            "Treasurer",
            [("Harlan E. Boyles", "DEMOCRATIC"), ("Betsy Young Justus", "REPUBLICAN"), ("Ron Holland", "LIBERTARIAN")],
        ),
        (
            "Superintendent of Public Instruction",
            [("Bob Etheridge", "DEMOCRATIC"), ("Teena S. Little", "REPUBLICAN"), ("Anna Lyon", "LIBERTARIAN")],
        ),
    ],
    (5, 6): [
        (
            "Attorney General",
            [("Mike Easley", "DEMOCRATIC"), ("Joe Dean", "REPUBLICAN"), ("Lawrence Berry", "LIBERTARIAN")],
        )
    ],
    (7, 8): [
        (
            "Commissioner of Agriculture",
            [("James A. (Jim) Graham", "DEMOCRATIC"), ("Leo Tew", "REPUBLICAN"), ("Bob McQuigg", "LIBERTARIAN")],
        )
    ],
    (9, 10): [
        (
            "Commissioner of Insurance",
            [("Jim Long", "DEMOCRATIC"), ("Mike Causey", "REPUBLICAN"), ("Kenneth Wayne Day", "LIBERTARIAN")],
        )
    ],
    (13, 14): [
        (
            "Governor",
            [
                ("James B. (Jim) Hunt, Jr.", "DEMOCRATIC"),
                ("Jim Gardner", "REPUBLICAN"),
                ("Scott Raleigh McLaughlin", "LIBERTARIAN"),
            ],
        )
    ],
    (15, 16): [
        (
            "Lieutenant Governor",
            [("Dennis A. Wicker", "DEMOCRATIC"), ("Art Pope", "REPUBLICAN"), ("Jeanette C. Small", "LIBERTARIAN")],
        )
    ],
    (11, 12): [
        (
            "Commissioner of Labor",
            [
                ("Harry E. Payne, Jr.", "DEMOCRATIC"),
                ("Nelson F. Dollar", "REPUBLICAN"),
                ("Craig Springer", "LIBERTARIAN"),
            ],
        )
    ],
}
METHOD = (
    "County totals come from the State Board of Elections' abstract of votes cast for governor, lieutenant governor, and the Council of State at the 1992 "
    "general election (a typed scan), read by two OCR passes with disagreements settled by the printed state total "
    "of each column."
)


def url(page: int) -> str:
    record, offset = next(entry for entry in reversed(RECORDS) if page > entry[1])
    return f"{record}#page-{page - offset:02d}"


def addresses() -> list[str]:
    return [url(page) for pair in SHEETS for page in pair]


def fetch() -> None:
    for record, offset in RECORDS:
        detail = subprocess.run(
            ["curl", "-sL", "-m", "60", "-A", "Mozilla/5.0", record], capture_output=True, text=True, check=True
        ).stdout
        items = json.loads(re.search(r"var viewerItems = (\[.*?\]);\n", detail, re.DOTALL)[1])
        for item in items:
            number = int(item["path"].rsplit("-", 1)[1]) + offset
            pdf = subprocess.run(
                [
                    "curl",
                    "-sL",
                    "-m",
                    "120",
                    "-A",
                    "Mozilla/5.0",
                    item["pdfUrl"].replace("[IMAGETOKEN]", item["imageToken"]),
                ],
                capture_output=True,
                check=True,
            ).stdout
            raw.store(
                SOURCE, url(number), pdf, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
            )
    print(scan())


def read(page: int, cut: int, mode: str, dpi: int, width: int) -> list[tuple[str, list[int]]]:
    cache = (
        WORK
        / "reads"
        / (f"{page:02d}-{cut}-{mode}-{width}.json" if dpi == 300 else f"{page:02d}-{cut}-{mode}-{dpi}-{width}.json")
    )
    if cache.exists():
        return [(name, values) for name, values in json.loads(cache.read_text())]
    with tempfile.TemporaryDirectory() as folder:
        prefix = Path(folder) / "page"
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                str(dpi),
                "-gray",
                "-png",
                "-singlefile",
                str(raw.cached(SOURCE, url(page)).path),
                str(prefix),
            ],
            check=True,
        )
        with Image.open(prefix.with_suffix(".png")) as rendered:
            gray = ImageOps.autocontrast(rendered.convert("L"), cutoff=2)
            (gray.point(lambda v: 0 if v < cut else 255) if cut > 0 else gray).save(prefix.with_suffix(".png"))
        out = subprocess.run(
            ["tesseract", str(prefix.with_suffix(".png")), "stdout", "--psm", mode],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    rows = []
    for line in out.splitlines():
        line = re.sub(r"(\d)[.:]\s?(\d{3})\b", r"\1,\2", re.sub(r"(\d),\s+(\d)", r"\1,\2", line.strip()))
        match = re.match(r"^([A-Z][A-Z .]+?)\s+((?:[\d,]+\s*)+)$", line)
        if match is None:
            continue
        values = [int(v.replace(",", "")) for v in match[2].split() if re.fullmatch(r"\d[\d,]*", v)]
        if len(values) == width and match[1] != "COUNTY":
            rows.append((match[1].strip(), values))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows))
    return rows


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, str, str, str, int]] = []
    for pair, offices in SHEETS.items():
        width = sum(len(candidates) for _, candidates in offices)
        merged: list[tuple[str, list[Counter[int]]]] = []
        for page in pair:
            readings: dict[str, list[list[int]]] = {}
            passes = [(dpi, seen(page, dpi)) for dpi in VARIANTS] if page in SEEN else []
            passes += [(0, read(page, cut, mode, dpi, width)) for cut, mode, dpi in PASSES if page not in SEEN]
            for _, rows in passes:
                for name, values in rows:
                    if len(values) != width:
                        continue
                    if name != "TOTAL" and re.sub(r"[^a-z]", "", name.lower()) not in codes:
                        continue
                    readings.setdefault(re.sub(r"[^A-Z]", "", name), []).append(values)
            for name, values in MANUAL.get(page, {}).items():
                readings[name] = [values] * 3
            for name, options in readings.items():
                merged.append((name, [Counter(values[column] for values in options) for column in range(width)]))
        total = next(values for name, values in merged if name == "TOTAL")
        body = [(name, values) for name, values in merged if name != "TOTAL"]
        if len(body) != 100:
            raise ValueError(f"pages {pair}: {len(body)} counties")
        chosen: list[list[int]] = [[0] * width for _ in body]
        for column in range(width):
            printed = total[column].most_common(1)[0][0]
            if len(total[column]) > 1:
                print("total", pair, column, dict(total[column]))
            weak = [r for r, (_, values) in enumerate(body) if len(values[column]) > 1]
            fixed = sum(values[column].most_common(1)[0][0] for r, (_, values) in enumerate(body) if r not in weak)
            for r, (_, values) in enumerate(body):
                chosen[r][column] = values[column].most_common(1)[0][0]
            found = [()]
            for r in weak:
                found = [(*prefix, value) for prefix in found for value in sorted(body[r][1][column])]
                if len(found) > 100000:
                    raise ValueError(f"pages {pair} column {column}: too many weak cells")
            fits = [combo for combo in found if fixed + sum(combo) == printed]
            score = lambda combo: sum(body[r][1][column][value] for r, value in zip(weak, combo, strict=True))
            if len(fits) > 1:
                best = max(score(combo) for combo in fits)
                fits = [combo for combo in fits if score(combo) == best]
            if len(fits) != 1:
                raise ValueError(
                    f"pages {pair} column {column}: {len(fits)} readings fit the printed total {printed} ({[body[r][0] for r in weak]})"
                )
            for r, value in zip(weak, fits[0], strict=True):
                chosen[r][column] = value
        body = [(name, chosen[r]) for r, (name, _) in enumerate(body)]
        start = 0
        for office, candidates in offices:
            for offset, (candidate, party) in enumerate(candidates):
                for name, values in body:
                    rows.append(
                        (office, candidate, party, codes[re.sub(r"[^a-z]", "", name.lower())], values[start + offset])
                    )
            start += len(candidates)
    frame = pl.DataFrame(rows, schema=["office", "candidate", "party", "county", "votes"], orient="row").select(
        year=pl.lit(1992, pl.Int32),
        day=pl.lit(str(DAY)),
        state=pl.lit("NC"),
        title="office",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("candidate").str.to_uppercase(),
        party="party",
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(100),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate").agg(pl.col("votes").sum()).sort("title", "votes").rows())


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=RECORD,
                license="Public record",
            )
        for address in addresses():
            cs.artifact(raw.cached(SOURCE, address), source)
        return {"NC": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="North Carolina's 1992 governor, lieutenant governor, secretary of state, auditor, treasurer, superintendent of public instruction, "
        "attorney general, and commissioners of agriculture, insurance, and labor by county from the State Board of "
        "Elections' abstract of votes cast",
        cite=cite,
        method=METHOD,
        supersede=True,
    )


def key(page: int, dpi: int) -> str:
    return f"seen:{page}:{dpi}"


def scan() -> str:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for page in sorted(SEEN):
        for dpi in VARIANTS:
            with tempfile.TemporaryDirectory() as folder:
                prefix = Path(folder) / "page"
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        str(dpi),
                        "-scale-to",
                        "7900",
                        "-gray",
                        "-png",
                        "-singlefile",
                        str(raw.cached(SOURCE, url(page)).path),
                        str(prefix),
                    ],
                    check=True,
                )
                data = base64.b64encode(prefix.with_suffix(".png").read_bytes()).decode()
            params = MessageCreateParamsNonStreaming(
                model="claude-haiku-5-5",
                max_tokens=4000,
                thinking={"type": "disabled"},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                            {"type": "text", "text": PROMPT},
                        ],
                    }
                ],
            )
            wanted[key(page, dpi)] = (params, (6000 * price_in + 2500 * price_out) / 1e6)
    return batch.ensure(SOURCE, "seen", wanted)[1]


def seen(page: int, dpi: int) -> list[tuple[str, list[int]]]:
    blocks = json.loads(raw.cached(SOURCE, key(page, dpi)).path.read_text())["content"]
    text = next(block["text"] for block in blocks if block.get("type") == "text")
    found = re.search(r"\[.*\]", text, re.DOTALL)
    return [
        (str(row[0]).upper(), [int(str(v).replace(",", "")) for v in row[1:]])
        for row in (json.loads(found[0]) if found else [])
    ]
