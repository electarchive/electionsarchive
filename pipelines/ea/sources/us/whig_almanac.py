import base64
import io
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from anthropic.types import ImageBlockParam, MessageParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.sources.us import house_books

SOURCE = "us.whig_almanac"
RAW = "us.whig_almanac"
VERSION = "1"
KIND = "compendium"
PUBLISHER = "Horace Greeley and Company"
TITLE = "The Whig Almanac and United States Register for 1849"
LICENSE = "Public domain"
SUMMARY = "Presidential results of 1844 and 1848 by county from the Whig Almanac for 1849"
STATE = None
FIPS = None
ITEM = "vol1849whigalman00whig"
PDF = f"https://archive.org/download/{ITEM}/{ITEM}.pdf"
LANDING = f"https://archive.org/details/{ITEM}"
PAGES = tuple(range(53, 64))
ITEMS = {
    1844: (ITEM, 1849, date(1844, 11, 1), PAGES),
    1848: (ITEM, 1849, date(1848, 11, 7), PAGES),
}
VOLUMES = {year: (PDF, LANDING, day, pages) for year, (_, _, day, pages) in ITEMS.items()}
ERRATA: set[tuple[int, tuple[str, int]]] = set()
NAMES: dict[tuple[str, str], str] = {}
FORMER: dict[tuple[str, str], tuple[str, str, date]] = {}
ALIASES: dict[str, tuple[str, ...]] = {}
BESIDE: tuple[str, ...] = ()
REVISION = 1
RENAMED: str | None = None
COMMAS = False
TURNED: set[tuple[int, int]] = set()
THRESHOLDS = (150, 190, 230)
MODEL = "claude-haiku-5-5"
READS = {"a": 300, "b": 400}
PROMPT = (
    "This is one column of a page of the Whig Almanac for 1849: the presidential vote by county, 1848 on the left "
    "and 1844 on the right. Transcribe every county row exactly as printed, digit for digit; do not correct, "
    "estimate, or fill in anything. Output plain text only, in this form. When a state heading or its column "
    "heading is visible, write a line `# XX` with the state's two-letter postal code, then a line "
    "`HEAD | name, name, name | name, name, name` that lists the presidential candidate columns for 1848, then for "
    "1844, as printed (Taylor, Cass, Van Buren for 1848; Clay, Polk, Birney for 1844; a few states omit a column "
    "or print other names, so copy what the page prints). Ignore governor, congressional, and every other "
    "column that is not for President. A continuation of a state from the previous page or column starts "
    "without a heading: write only its rows and no `#` line. Each county is one line, "
    "`County | votes votes votes | votes votes votes` (one number per candidate column), with the 1848 votes before the bar and the 1844 votes "
    "after it, using no thousands separators, and `-` for a cell that is empty, dashed, or marked `New Co.`. "
    "Leave out district numbers, brackets, totals, majorities, and prose. Skip any line you cannot read rather "
    "than guessing."
)


def fetch() -> None:
    raw.fetch(RAW, PDF, suffix=".pdf")


def crops(name: str) -> dict[str, tuple[bytes, tuple[int, int]]]:
    book = raw.cached(RAW, PDF)
    out: dict[str, tuple[bytes, tuple[int, int]]] = {}
    for page in PAGES:
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                ["pdftoppm", "-r", str(READS[name]), "-gray", "-png", "-f", str(page), "-l", str(page)]
                + ["-singlefile", str(book.path), f"{folder}/p"],
                check=True,
            )
            image = Image.open(f"{folder}/p.png").convert("L")
        width, height = image.size
        for side, (left, right) in {"l": (0.035, 0.515), "r": (0.485, 0.965)}.items():
            part = image.crop((round(left * width), round(0.03 * height), round(right * width), round(0.97 * height)))
            buffer = io.BytesIO()
            part.save(buffer, "PNG")
            out[f"p{page}{side}"] = (buffer.getvalue(), part.size)
    return out


def key(name: str, label: str) -> str:
    return f"{SOURCE}/{name}/{label}"


def ensure() -> bool:
    wanted: dict[str, batch.Want] = {}
    for name in READS:
        for label, (data, (width, height)) in crops(name).items():
            side = "left" if label[-1] == "l" else "right"
            content: list[ImageBlockParam | TextBlockParam] = [
                ImageBlockParam(
                    type="image",
                    source={"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()},
                ),
                TextBlockParam(type="text", text=f"{PROMPT} (Page {label[1:-1]}, {side} column.)"),
            ]
            params = MessageCreateParamsNonStreaming(
                model=MODEL, max_tokens=16000, messages=[MessageParam(role="user", content=content)]
            )
            wanted[key(name, label)] = (params, (width * height / 750 * 0.05 + 6000 * 0.25) / 1e6)
    ready, note = batch.ensure(SOURCE, "reads", wanted)
    print(note)
    return ready


def parse() -> None:
    if not ensure():
        return
    raise SystemExit("the reads are cached; the model files are built from them next")


def emit() -> Path:
    return house_books.emit(sys.modules[__name__])
