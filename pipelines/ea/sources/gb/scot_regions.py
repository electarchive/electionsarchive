import base64
import io
import json
import subprocess
from datetime import date

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.store import DATA

SOURCE = "gb.scot_regions"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://www.electionscentre.co.uk/wp-content/uploads/2015/10"
MODEL = "claude-haiku-5-5"
PROMPT = "1"
LONGEST = 1568
VOLUMES: dict[str, dict] = {
    "r1978": {"tier": "region", "day": date(1978, 5, 2), "file": "Scottish-Regional-Elections-1978", "pages": (27, 63)},
    "r1982": {"tier": "region", "day": date(1982, 5, 6), "file": "Scottish-Regional-Elections-1982", "pages": (25, 64)},
    "r1986": {"tier": "region", "day": date(1986, 5, 8), "file": "Scottish-Regional-Elections-1986", "pages": (19, 44)},
    "r1990": {"tier": "region", "day": date(1990, 5, 3), "file": "Scottish-Regional-Elections-1990", "pages": (19, 60)},
    "r1994": {"tier": "region", "day": date(1994, 5, 5), "file": "Scottish-Regional-Elections-1994", "pages": (21, 65)},
    "d1977": {
        "tier": "district",
        "day": date(1977, 5, 3),
        "file": "Scottish-District-Elections-1977",
        "pages": (32, 100),
    },
    "d1980": {
        "tier": "district",
        "day": date(1980, 5, 1),
        "file": "Scottish-District-Elections-1980",
        "pages": (19, 83),
    },
    "d1984": {
        "tier": "district",
        "day": date(1984, 5, 3),
        "file": "Scottish-District-Elections-1984",
        "pages": (21, 91),
    },
    "d1988": {
        "tier": "district",
        "day": date(1988, 5, 5),
        "file": "Scottish-District-Elections-1988",
        "pages": (25, 104),
    },
    "d1992": {
        "tier": "district",
        "day": date(1992, 5, 7),
        "file": "Scottish-District-Elections-1992",
        "pages": (21, 117),
    },
}
READ_SYSTEM = (
    "You read one page of a printed book of Scottish local election results by Bochel and Denver. Each page lists "
    "results by electoral division or ward, usually in two columns: read the left column from top to bottom, then "
    "the right column. A council heading is the name of a region, islands area, or district printed as a title "
    "(such as Lothian, Orkney Islands, or City of Edinburgh District), sometimes with the region it belongs to; "
    "report it once where it appears. Each division or ward starts with its number and name in capitals, and has "
    "an electorate, and, when contested, the turnout, the rejected ballots, the candidates with their votes and "
    "percentages, and the majority. Each candidate line has an optional marker printed before the name (=, *, +, "
    "x, or similar), an optional title such as Ms, the initials, the surname, the party label, the votes, and the "
    "percentage. Unopposed candidates have the word Unopposed or Returned unopposed instead of votes. Copy names, "
    "markers, and party labels exactly as printed, and copy every figure exactly as printed, digits only, without "
    "thousands separators. Leave a figure null when it is not printed. Skip page numbers, running titles, and "
    "commentary text."
)
READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "councils": {"type": "array", "items": {"type": "string"}},
        "divisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "council": {"type": ["string", "null"]},
                    "number": {"type": ["string", "null"]},
                    "name": {"type": "string"},
                    "electorate": {"type": ["string", "null"]},
                    "turnout": {"type": ["string", "null"]},
                    "rejected": {"type": ["string", "null"]},
                    "majority": {"type": ["string", "null"]},
                    "unopposed": {"type": "boolean"},
                    "note": {"type": ["string", "null"]},
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "marker": {"type": ["string", "null"]},
                                "title": {"type": ["string", "null"]},
                                "initials": {"type": ["string", "null"]},
                                "surname": {"type": "string"},
                                "party": {"type": "string"},
                                "votes": {"type": ["string", "null"]},
                                "percent": {"type": ["string", "null"]},
                            },
                            "required": ["marker", "title", "initials", "surname", "party", "votes", "percent"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "council",
                    "number",
                    "name",
                    "electorate",
                    "turnout",
                    "rejected",
                    "majority",
                    "unopposed",
                    "note",
                    "candidates",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["councils", "divisions"],
    "additionalProperties": False,
}


def url(volume: str) -> str:
    return f"{BASE}/{VOLUMES[volume]['file']}.pdf"


def read_key(sha256: str, page: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{sha256}/{page}"


def vision(sha256: str, page: int) -> dict:
    message = json.loads(raw.cached(SOURCE, read_key(sha256, page)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def fetch() -> None:
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for volume, spec in VOLUMES.items():
        artifact = raw.fetch(SOURCE, url(volume), suffix=".pdf")
        first, last = spec["pages"]
        for page in range(first, last + 1):
            png = subprocess.run(
                ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                + [str(artifact.path)],
                check=True,
                capture_output=True,
            ).stdout
            image = Image.open(io.BytesIO(png))
            image.thumbnail((LONGEST, LONGEST))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            params = MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=12000,
                system=READ_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            ImageBlockParam(
                                type="image",
                                source={
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64.b64encode(buffer.getvalue()).decode(),
                                },
                            ),
                            TextBlockParam(type="text", text="Read the councils and divisions on this page."),
                        ],
                    }
                ],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
            )
            wanted[read_key(artifact.sha256, page)] = (
                params,
                (image.width * image.height / 750 * price_in + 6000 * price_out) / 1e6,
            )
    print(batch.ensure(SOURCE, "volumes", wanted)[1])
