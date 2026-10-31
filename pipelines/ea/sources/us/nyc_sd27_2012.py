import base64
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "us.nyc_sd27_2012"
SCAN = DATA / "raw" / "us.nyc_recap" / "b9fb1f89d9362ef0ebbdc1c846f58bc1d46b788018e1f6c6a2acc92c2695b675.pdf"
IMAGES = DATA / "tmp" / "nyc27"
PAGES = range(1, 20)
PROMPT = (
    "This page is from the Board of Elections' certified returns for the March 20, 2012 special election for 27th "
    "State Senate, Kings County. Rows are election districts, grouped under 'AD nn' headings. Return only JSON: "
    '{"page": <printed page number or null>, "title": <heading text above the table or null>, "columns": [the column '
    'headers as printed, in order], "rows": [{"ad": <assembly district number as the nearest preceding AD heading, or '
    'null>, "label": <row label exactly as printed, for example "052(048/41)" or "Total">, "values": [the numeric '
    'cells in column order as integers], "write_in_names": <text of the write-in names cell or "">}]}. Include '
    "every row, including AD total rows and zero rows, and read every digit with care."
)


def pages() -> list[Path]:
    IMAGES.mkdir(parents=True, exist_ok=True)
    if not (IMAGES / "p-01.png").exists():
        subprocess.run(["pdftoppm", "-r", "200", "-png", str(SCAN), str(IMAGES / "p")], check=True)
    return [IMAGES / f"p-{page:02d}.png" for page in PAGES]


def fetch() -> None:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for page, image in enumerate(pages(), 1):
        data = base64.b64encode(image.read_bytes()).decode()
        for read in ("a", "b"):
            params = MessageCreateParamsNonStreaming(
                model="claude-haiku-5-5",
                max_tokens=8000,
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
            wanted[f"read:{page}:{read}"] = (params, (3500 * price_in + 3500 * price_out) / 1e6)
    print(batch.ensure(SOURCE, "pages", wanted)[1])


def reading(page: int, read: str) -> dict[str, object]:
    blocks = json.loads(raw.cached(SOURCE, f"read:{page}:{read}").path.read_text())["content"]
    text = "".join(str(block["text"]) for block in blocks if block.get("type") == "text")
    return json.loads(text[text.index("{") : text.rindex("}") + 1])


URL = "https://www.vote.nyc/sites/default/files/downloads/pdf/results/2012/27StateSenate/Certified27StateSenateSpecialResults.pdf"
PARSED = DATA / "parsed" / SOURCE
VERSION = "1"
OFFICE = "STATE SENATE (27)"
DAY = "2012-03-20"
LINES = (
    ("LEWIS A FIDLER", "DEMOCRATIC"),
    ("DAVID STOROBIN", "REPUBLICAN"),
    ("DAVID STOROBIN", "CONSERVATIVE"),
    ("LEWIS A FIDLER", "INDEPENDENCE"),
)
CHECKED = {(46, "064"): (182, 91, 74, 10, 5, 2, 0, 0)}
NOTE = (
    "Returns from the Board of Elections' certified report for the March 20, 2012 special election for the 27th State "
    "Senate district, by election district within each assembly district, one line per ballot line as New York's fusion "
    "ballot prints them. The report is an image scan; each page was read twice by machine vision and the figures kept "
    "only where both reads agree. Every election district's columns add to the assembly district total the Board "
    "printed under them, and the district totals match the report's summary page. Election districts combined into "
    "another district print no votes and are omitted."
)


def tail(values: list[int | None]) -> tuple[int, int, int]:
    cells = values[-3:] if len(values) in (3, 8) else [values[0], 0, 0] if len(values) == 1 else values[-3:]
    return tuple(0 if v is None else int(v) for v in cells)


def clean(page: int, read: str) -> dict[tuple[int, str], tuple[int, ...]]:
    found: dict[tuple[int, str], tuple[int, ...]] = {}
    last: tuple[int, str] | None = None
    for row in reading(page, read)["rows"]:
        label = str(row["label"]).strip()
        values = row["values"]
        if label.startswith("AD") or row["ad"] is None:
            continue
        if not label:
            if last is not None:
                extra = tail(values)
                found[last] = (*found[last][:5], *(x + y for x, y in zip(found[last][5:], extra, strict=True)))
            continue
        if len(values) == 9:
            values = [*values[:5], *values[6:]]
        if len(values) != 8 or any(v is None for v in values[:5]):
            continue
        last = (int(row["ad"]), label)
        found[last] = (*(int(v) for v in values[:5]), *(0 if v is None else int(v) for v in values[5:]))
    return found


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: dict[tuple[int, str], tuple[int, ...]] = {}
    bad = []
    for page in PAGES:
        if page == 19:
            continue
        a, b = clean(page, "a"), clean(page, "b")
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key) and CHECKED.get(key) not in (a.get(key), b.get(key)):
                bad.append(f"page {page} {key}: {a.get(key)} against {b.get(key)}")
        rows.update(a)
        rows.update({key: CHECKED[key] for key in CHECKED if key in a or key in b})
    districts: dict[int, list[tuple[int, ...]]] = {}
    for (ad, label), values in rows.items():
        if label != "Total":
            districts.setdefault(ad, []).append(values)
    for ad in sorted(districts):
        total = rows.get((ad, "Total"))
        got = tuple(sum(v[i] for v in districts[ad]) for i in range(8))
        if total != got:
            bad.append(f"AD {ad}: districts add to {got}, printed {total}")
    grand = [sum(rows[(ad, "Total")][i] for ad in districts) for i in (1, 2, 3, 4, 5)]
    print("ads", sorted(districts), "districts", sum(len(v) for v in districts.values()), "grand", grand)
    if grand[0] + grand[3] != 11084 or grand[1] + grand[2] != 11097 or grand[4] != 36:
        bad.append(f"AD totals {grand} do not match the summary page (11084, 11097, 36)")
    if bad:
        print(*bad, sep="\n")
        raise SystemExit(f"{len(bad)} disagreements")
    entries = []
    ballots = []
    totals = []
    for (ad, label), values in sorted(rows.items()):
        if label == "Total":
            continue
        number = re.match(r"\d+", label)
        if "(" in label or number is None:
            if any(values):
                raise SystemExit(f"{label} in AD {ad} prints votes")
            continue
        bid = f"{ad:02d}{int(number[0]):03d}"
        head = (URL, DAY, "Special", "Kings", "Kings County")
        for (name, line), votes in zip(LINES, values[1:5], strict=True):
            entries.append((*head, OFFICE, "ed", bid, name, line, votes))
        entries.append((*head, OFFICE, "ed", bid, "WRITE-INS", "WRITE-IN", values[5]))
        ballots.append((*head, OFFICE, "ed", bid, values[0]))
    head = (URL, DAY, "Special", "Kings", "Kings County")
    totals.append((*head, OFFICE, "total", "TOTAL", sum(e[-1] for e in entries)))
    head_schema = {"url": pl.String, "day": pl.String, "kind": pl.String, "county": pl.String, "scope": pl.String}
    pl.DataFrame(
        entries,
        schema={
            **head_schema,
            "office": pl.String,
            "bkind": pl.String,
            "bid": pl.String,
            "name": pl.String,
            "party": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "entries.parquet")
    for name, found in (("totals", totals), ("ballots", ballots)):
        pl.DataFrame(
            found,
            schema={**head_schema, "office": pl.String, "bkind": pl.String, "bid": pl.String, "votes": pl.Int64},
            orient="row",
        ).write_parquet(PARSED / f"{name}.parquet")
    print(len(ballots), "election districts", len(entries), "lines")


def emit() -> Path:
    from ea.sources.us import nyc_recap

    if URL not in raw.keys(SOURCE):
        raw.store(
            SOURCE,
            URL,
            SCAN.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(SCAN.stat().st_mtime, UTC),
            media_type="application/pdf",
        )
    return nyc_recap.emit(
        pipeline=SOURCE,
        parsed=PARSED,
        version=VERSION,
        summary="New York's March 20, 2012 27th State Senate special election by election district",
        note=NOTE,
        strict=False,
        questions=False,
        fuller=True,
        grain="ed",
    )
