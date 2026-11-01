import base64
import hashlib
import io
import json
import subprocess
import tempfile
from pathlib import Path

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw

SOURCE = "us.ia_sos"
MODEL = "claude-haiku-5-5"
READS = {"a": 100, "b": 130}
URL = "https://sos.iowa.gov/elections/pdf/results/80s/{}gencanv.pdf"
URL_1978 = "https://web.archive.org/web/2020if_/https://sos.iowa.gov/elections/pdf/results/70s/1978gencanv.pdf"
URL_1998 = "https://www.legis.iowa.gov/docs/shelves/redbooks/Redbook-1999-2000%20(78GA).pdf"
SCANS: dict[str, list[tuple[str, tuple[int, ...]]]] = {
    "1982-11-02": [
        ("secretary-of-state", (10, 11)),
        ("auditor", (12, 13)),
        ("treasurer", (14, 15)),
        ("secretary-of-agriculture", (16, 17)),
        ("attorney-general", (18, 19)),
    ],
    "1986-11-04": [
        ("secretary-of-state", (12, 13)),
        ("auditor", (14, 15)),
        ("treasurer", (16, 17)),
        ("secretary-of-agriculture", (18, 19)),
        ("attorney-general", (20, 21)),
    ],
    "1978-11-07": [("secretary-of-state", (4,))],
    "1998-11-03": [
        ("senate", (417,)),
        ("governor", (420,)),
        ("secretary-of-state", (421,)),
        ("auditor", (422,)),
        ("treasurer", (423,)),
        ("secretary-of-agriculture", (424,)),
        ("attorney-general", (425,)),
    ],
}
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "county": {"type": "string"},
                    "values": {"type": "array", "items": {"type": ["string", "null"]}},
                },
                "required": ["county", "values"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["columns", "rows"],
    "additionalProperties": False,
}
SYSTEM = (
    "You transcribe printed election returns from scanned pages into JSON. Copy every figure exactly as printed, "
    "digits only without thousands separators or spaces. Never compute, correct, round, or fill in a number. A digit "
    "you cannot read is still your best reading of what is printed."
)
ASK = (
    "This page is one half of a county table of Iowa votes for one statewide office, 1982 or 1986. First return "
    "columns: the heading of each figure column left to right as printed (the candidate's surname, or SC, or "
    "TOTALS; if this page has no headings, return an empty array). Then return one row per printed county line, "
    "top to bottom, with the county name as printed (the last row of the second page is TOTALS) and one entry in "
    "values for each figure column in order. A figure cell that is blank on that line is null. Do not skip lines "
    "and do not add any."
)


ASK_PAGE = (
    "This page is a county table of Iowa votes for one statewide office in the 1998 general election. First return "
    "columns: the heading of each figure column left to right as printed (the candidate's surname, or SC, or "
    "TOTALS). Then return one row per printed county line, top to bottom, with the county name as printed (the "
    "last row is TOTALS) and one entry in values for each figure column in order. A figure cell that is blank on "
    "that line is null. Do not skip lines and do not add any."
)
ASK_RIGHT = (
    "This page holds two county tables side by side: Lieutenant Governor on the left and Secretary of State on the "
    "right. Transcribe only the Secretary of State table on the right. First return columns: the heading of each "
    "figure column left to right as printed (the candidate's surname, or Scattering, or Totals). Then return one "
    "row per printed county line of that table, top to bottom, with the county name as printed, and one entry in "
    "values for each figure column in order. A figure cell that is blank on that line is null. Do not skip lines "
    "and do not add any. The table may end before the last county; stop where the page ends."
)
LAYOUTS: dict[str, tuple[str, str, str, str, dict[str, int]]] = {
    "1982": (SOURCE, URL, "1", ASK, READS),
    "1986": (SOURCE, URL, "1", ASK, READS),
    "1978": (SOURCE, URL_1978, "3", ASK_RIGHT, {"a": 130, "b": 170}),
    "1998": ("us.ia_redbook", URL_1998, "2", ASK_PAGE, {"a": 170, "b": 220}),
}


def image(path: Path, page: int, dpi: int) -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        stem = Path(folder) / "page"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(path), str(stem)],
            check=True,
        )
        with Image.open(stem.with_suffix(".png")) as rendered:
            buffer = io.BytesIO()
            rendered.convert("L").save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def key(day: str, page: int, reader: str, sha: str) -> str:
    prompt = LAYOUTS[day[:4]][2]
    digest = hashlib.sha256(f"{prompt}{reader}{sha}{page}".encode()).hexdigest()
    return f"anthropic-batch:{MODEL}/ia-canvass/{prompt}/{reader}/{digest}"


def requests(
    scans: dict[str, list[tuple[str, tuple[int, ...]]]],
) -> dict[str, tuple[MessageCreateParamsNonStreaming, float]]:
    out: dict[str, tuple[MessageCreateParamsNonStreaming, float]] = {}
    for day, parts in scans.items():
        source, url, _, ask, dpis = LAYOUTS[day[:4]]
        stored = raw.cached(source, url.format(day[:4]))
        for _, pages in parts:
            for page in pages:
                for reader, dpi in dpis.items():
                    block = {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(image(stored.path, page, dpi)).decode(),
                        },
                    }
                    params = MessageCreateParamsNonStreaming(
                        model=MODEL,
                        max_tokens=6000,
                        system=SYSTEM,
                        messages=[{"role": "user", "content": [block, {"type": "text", "text": ask}]}],
                        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                    )
                    out[key(day, page, reader, stored.sha256)] = (params, 0.004)
    return out


def reads(
    scans: dict[str, list[tuple[str, tuple[int, ...]]]],
) -> dict[tuple[str, int, str], dict[str, object]]:
    wanted = requests(scans)
    ready, note = batch.ensure(SOURCE, "canvass-scans", wanted)
    print(note)
    if not ready:
        raise SystemExit(note)
    found: dict[tuple[str, int, str], dict[str, object]] = {}
    for day, parts in scans.items():
        source, url, _, _, dpis = LAYOUTS[day[:4]]
        sha = raw.cached(source, url.format(day[:4])).sha256
        for _, pages in parts:
            for page in pages:
                for reader in dpis:
                    response = json.loads(raw.cached(SOURCE, key(day, page, reader, sha)).path.read_text())
                    text = next(block["text"] for block in response["content"] if block["type"] == "text")
                    found[(day, page, reader)] = json.loads(text)
    return found
