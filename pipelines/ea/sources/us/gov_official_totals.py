import base64
import hashlib
import io
import json
import subprocess
import tempfile
from pathlib import Path

import polars as pl
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "us.gov_official_totals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MODEL = "claude-haiku-5-5"
PROMPT = "1"
READS = {"a": 150, "b": 200}
PAGES = Path(__file__).with_name("gov_official_totals_pages.tsv")
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "party": {"type": "string"},
                    "votes": {"type": "string"},
                },
                "required": ["name", "party", "votes"],
                "additionalProperties": False,
            },
        },
        "printed_total": {"type": ["string", "null"]},
    },
    "required": ["candidates", "printed_total"],
    "additionalProperties": False,
}
SYSTEM = (
    "You transcribe printed election returns from scanned pages into JSON. Copy every figure exactly as printed, "
    "digits only without thousands separators or spaces. Never compute, correct, round, or fill in a number. A digit "
    "you cannot read is still your best reading of what is printed."
)
ASK = {
    "a": (
        "The page or pages show the returns for Governor in {state} in {year}. Find the statewide total for each "
        "candidate for Governor: the row labeled Total, Totals, or State Total at the bottom of the county table, or "
        "the statewide summary. Return one entry per candidate column, in column order, with the name and party as "
        "printed in the column heading and the total votes as printed. Leave out Lieutenant Governor and every other "
        "office. If a printed total of all votes for Governor is shown, return it as printed_total; otherwise null."
    ),
    "b": (
        "This is the official canvass of the {year} general election in {state}. Read the columns of the table for "
        "the office of Governor only, one column at a time. For each column give the candidate's name, the party "
        "abbreviation or name printed with it, and the figure printed on the line for the whole state's total (not a "
        "county line, not a plurality or majority line, not a percentage). Ignore Lieutenant Governor and other "
        "offices. Put the figure for all votes cast for Governor in printed_total only if the page prints one."
    ),
}


def pages() -> pl.DataFrame:
    return pl.read_csv(PAGES, separator="\t", schema_overrides={"year": pl.Int64, "rotate": pl.Int64}).with_columns(
        pl.col("rotate").fill_null(0)
    )


def find(source: str, needle: str) -> raw.Artifact:
    matches = [key for key in raw.keys(source) if needle in key]
    if len(matches) != 1:
        raise LookupError(f"{needle}: {len(matches)} matches in the {source} raw cache")
    return raw.cached(source, matches[0])


def image(path: Path, page: int, dpi: int, rotate: int) -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        stem = Path(folder) / "page"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(path), str(stem)],
            check=True,
        )
        with Image.open(stem.with_suffix(".png")) as rendered:
            buffer = io.BytesIO()
            gray = rendered.convert("L")
            if rotate:
                gray = gray.rotate(-rotate, expand=True)
            gray.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def requests() -> dict[str, tuple[MessageCreateParamsNonStreaming, float]]:
    out: dict[str, tuple[MessageCreateParamsNonStreaming, float]] = {}
    for state, year, origin, needle, numbers, rotate in (
        pages().select("state", "year", "origin", "needle", "pages", "rotate").iter_rows()
    ):
        stored = find(origin, needle)
        for reader, dpi in READS.items():
            blocks: list[dict[str, object]] = []
            digest = hashlib.sha256(f"{PROMPT}{reader}{stored.sha256}{numbers}{rotate or ''}".encode()).hexdigest()
            for number in str(numbers).split(","):
                data = image(stored.path, int(number), dpi, rotate)
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(data).decode(),
                        },
                    }
                )
            blocks.append({"type": "text", "text": ASK[reader].format(state=state, year=year)})
            params = MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=4000,
                system=SYSTEM,
                messages=[{"role": "user", "content": blocks}],
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            )
            out[f"anthropic-batch:{MODEL}/gov-totals/{PROMPT}/{reader}/{digest}"] = (params, 0.01)
    return out


def reads() -> dict[tuple[str, int, str], dict[str, object]]:
    found: dict[tuple[str, int, str], dict[str, object]] = {}
    wanted = requests()
    ready, note = batch.ensure(SOURCE, "reads", wanted)
    print(note)
    if not ready:
        raise SystemExit(note)
    keys = iter(wanted)
    for state, year in pages().select("state", "year").iter_rows():
        for reader in READS:
            response = json.loads(raw.cached(SOURCE, next(keys)).path.read_text())
            text = next(block["text"] for block in response["content"] if block["type"] == "text")
            found[(state, year, reader)] = json.loads(text)
    return found


def fetch() -> None:
    for url in pages()["url"].drop_nulls().unique():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = []
    for (state, year, reader), body in reads().items():
        for position, entry in enumerate(body["candidates"]):
            rows.append(
                (state, year, reader, position, entry["name"], entry["party"], entry["votes"], body["printed_total"])
            )
    pl.DataFrame(
        rows, schema=["state", "year", "reader", "position", "name", "party", "votes", "printed_total"], orient="row"
    ).write_csv(PARSED / "reads.tsv", separator="\t")
    print(len(rows), "lines read")
