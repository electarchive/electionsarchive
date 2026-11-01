import base64
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

from ea import batch, raw
from ea.store import DATA

SOURCE = "us.ca_catb"
MODEL = "claude-haiku-5-5"
PROMPT = "1"
URL = "https://v3.californiatargetbook.com/docs/SoV/{}.pdf"
TMP = DATA / "tmp" / "catb"
PARSED = DATA / "parsed" / SOURCE
BROWSER = {"User-Agent": "Mozilla/5.0"}
VOLUMES = [f"{kind}{year}" for year in range(1970, 1990, 2) for kind in "gp"] + ["s1973", "s1979"]
COUNTIES = [
    "Alameda",
    "Alpine",
    "Amador",
    "Butte",
    "Calaveras",
    "Colusa",
    "Contra",
    "Del",
    "Norte",
    "Dorado",
    "Fresno",
    "Glenn",
    "Humboldt",
    "Imperial",
    "Inyo",
    "Kern",
    "Kings",
    "Lake",
    "Lassen",
    "Angeles",
    "Madera",
    "Marin",
    "Mariposa",
    "Mendocino",
    "Merced",
    "Modoc",
    "Mono",
    "Monterey",
    "Napa",
    "Nevada",
    "Orange",
    "Placer",
    "Plumas",
    "Riverside",
    "Sacramento",
    "Benito",
    "Bernardino",
    "Diego",
    "Francisco",
    "Joaquin",
    "Obispo",
    "Mateo",
    "Barbara",
    "Clara",
    "Cruz",
    "Shasta",
    "Sierra",
    "Siskiyou",
    "Solano",
    "Sonoma",
    "Stanislaus",
    "Sutter",
    "Tehama",
    "Trinity",
    "Tulare",
    "Tuolumne",
    "Ventura",
    "Yolo",
    "Yuba",
]
SYSTEM = (
    "You transcribe one page of a California Secretary of State Statement of Vote from the 1970s or 1980s, a scanned "
    "table of statewide ballot propositions by county. Find the statewide totals line at the bottom of the county "
    "table (printed Totals, State Totals, or Total) and the proposition numbers printed in the column headings. Each "
    "proposition has two columns: For or Yes, then Against or No. Give one entry per proposition, in column order, "
    "with the proposition number as printed, the For and Against totals as integers (digits exactly as printed, no "
    "separators), and the two percentages printed under them, or null where none is printed. If the page instead "
    "prints a summary table of votes for and against measures, give one entry per row. If the page has no statewide "
    "totals, return an empty list. Use null for any number you cannot read; never guess a digit."
)
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "propositions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "yes": {"type": ["integer", "null"]},
                    "no": {"type": ["integer", "null"]},
                    "yes_percent": {"type": ["number", "null"]},
                    "no_percent": {"type": ["number", "null"]},
                },
                "required": ["number", "yes", "no", "yes_percent", "no_percent"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["propositions"],
    "additionalProperties": False,
}


def url(volume: str) -> str:
    return URL.format(volume)


def fetch() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    for volume in VOLUMES:
        try:
            raw.cached(SOURCE, url(volume))
            continue
        except LookupError:
            pass
        path = TMP / f"{volume}.pdf"
        if not path.exists():
            response = httpx.get(url(volume), headers=BROWSER, follow_redirects=True, timeout=300)
            response.raise_for_status()
            path.write_bytes(response.content)
        raw.store(
            SOURCE,
            url(volume),
            path.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            media_type="application/pdf",
        )


def county_pages(pdf: Path) -> list[int]:
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    found = []
    for page, body in enumerate(text.split("\f"), 1):
        low = body.lower()
        if "proposition" not in low and "propostion" not in low and "prop." not in low:
            continue
        if sum(name in body for name in COUNTIES) >= 40 and "tota" in low:
            found.append(page)
    return found


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    wanted: dict[str, batch.Want] = {}
    pages: dict[str, list[int]] = {}
    folder = Path(tempfile.mkdtemp())
    for volume in VOLUMES:
        pdf = raw.cached(SOURCE, url(volume)).path
        pages[volume] = county_pages(pdf)
        for page in pages[volume]:
            image = folder / f"{volume}-{page}"
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "170",
                    "-gray",
                    "-png",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    str(pdf),
                    str(image),
                ],
                check=True,
            )
            data = image.with_suffix(".png").read_bytes()
            wanted[f"anthropic-batch:{MODEL}/{PROMPT}/{volume}/{page}"] = (
                MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=2000,
                    system=SYSTEM,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                ImageBlockParam(
                                    type="image",
                                    source={
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": base64.b64encode(data).decode(),
                                    },
                                ),
                                TextBlockParam(type="text", text="Transcribe the statewide totals."),
                            ],
                        }
                    ],
                    output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                ),
                0.004,
            )
    (PARSED / "pages.json").write_text(json.dumps(pages))
    print(batch.ensure(SOURCE, "totals", wanted)[1])
