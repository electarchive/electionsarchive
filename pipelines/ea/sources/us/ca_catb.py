import base64
import json
import re
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
EARLY_PROMPT = "2"
URL = "https://v3.californiatargetbook.com/docs/SoV/{}.pdf"
TMP = DATA / "tmp" / "catb"
PARSED = DATA / "parsed" / SOURCE
BROWSER = {"User-Agent": "Mozilla/5.0"}
VOLUMES = [f"{kind}{year}" for year in range(1970, 1990, 2) for kind in "gp"] + ["s1973", "s1979"]
EARLY = (
    [f"g{year}" for year in range(1908, 1970, 2)]
    + ["p1944", "p1950", "p1962", "p1966", "p1968"]
    + ["s1911", "s1915", "s1932", "s1933", "s1935", "s1939", "s1949"]
)
HEADING = re.compile(r"proposition|proposed\s+law|constitutional\s+amendment|initiative|referendum", re.IGNORECASE)
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
EARLY_SYSTEM = (
    "You transcribe one page of a California Secretary of State Statement of Vote from 1908 to 1968, a scanned table "
    "of statewide constitutional amendments, initiative measures, referendums, bond laws, or propositions by county. "
    "The scan may have blemishes: never guess a digit. Find the statewide totals line at the bottom of the county "
    "table (printed Totals, State Totals, or Total). Each measure has two columns: Yes or For, then No or Against. "
    "Give one entry per measure, in column order, with the label printed in its column heading (for example "
    "S. C. A. No. 3, Proposition No. 4, Initiative Measure, Referendum Petition, or a ballot number) and a title "
    "of up to eight words taken from the heading text, then the Yes and No totals as integers (digits exactly as "
    "printed, no separators). If the page has no statewide totals line, return an empty list. Use null for any "
    "number you cannot read."
)
EARLY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "measures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "title": {"type": "string"},
                    "yes": {"type": ["integer", "null"]},
                    "no": {"type": ["integer", "null"]},
                },
                "required": ["label", "title", "yes", "no"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["measures"],
    "additionalProperties": False,
}
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
    for volume in [*VOLUMES, *EARLY]:
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


def county_pages(pdf: Path, early: bool = False) -> list[int]:
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    found = []
    for page, body in enumerate(text.split("\f"), 1):
        named = sum(name in body for name in COUNTIES)
        if early:
            if named >= 15 and HEADING.search(body[:1500]):
                found.append(page)
            continue
        low = body.lower()
        if "proposition" not in low and "propostion" not in low and "prop." not in low:
            continue
        if named >= 40 and "tota" in low:
            found.append(page)
    if early and found:
        return list(range(max(1, found[0] - 1), min(found[-1] + 1, text.count("\f")) + 1))
    return found


def request(
    pdf: Path, folder: Path, name: str, page: int, dpi: int, system: str, schema: dict[str, object]
) -> MessageCreateParamsNonStreaming:
    image = folder / name
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            str(dpi),
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
    return MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=2000,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    ImageBlockParam(
                        type="image",
                        source={"type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode()},
                    ),
                    TextBlockParam(type="text", text="Transcribe the statewide totals."),
                ],
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp())
    steps = [
        ("totals", "pages.json", PROMPT, VOLUMES, False, 170, SYSTEM, SCHEMA),
        ("early", "pages-early.json", EARLY_PROMPT, EARLY, True, 200, EARLY_SYSTEM, EARLY_SCHEMA),
    ]
    for job, listing, prompt, volumes, early, dpi, system, schema in steps:
        wanted: dict[str, batch.Want] = {}
        pages: dict[str, list[int]] = {}
        for volume in volumes:
            pdf = raw.cached(SOURCE, url(volume)).path
            pages[volume] = county_pages(pdf, early)
            for page in pages[volume]:
                params = request(pdf, folder, f"{volume}-{page}", page, dpi, system, schema)
                wanted[f"anthropic-batch:{MODEL}/{prompt}/{volume}/{page}"] = (params, 0.004)
        (PARSED / listing).write_text(json.dumps(pages))
        print(batch.ensure(SOURCE, job, wanted)[1])
