import base64
import json
import subprocess
from pathlib import Path

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
    "cells in column order as integers], \"write_in_names\": <text of the write-in names cell or \"\">}]}. Include "
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
