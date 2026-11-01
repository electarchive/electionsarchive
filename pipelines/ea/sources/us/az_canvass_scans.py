import base64
import json
import re
import subprocess

from PIL import Image

from ea import raw
from ea.store import DATA

SOURCE = "us.az_canvass_scans"
PAGES: dict[int, tuple[int, int]] = {
    1974: (7, 9),
    1976: (6, 8),
    1978: (8, 9),
    1980: (12, 14),
    1982: (9, 11),
    1984: (11, 15),
    1986: (9, 11),
    1988: (10, 12),
    1990: (9, 12),
    1992: (10, 12),
    1994: (9, 14),
    1996: (17, 20),
}
VARIANTS = {"a": (300, 2.0), "b": (300, 1.5)}
PROMPT = (
    "This is a page of the Arizona Secretary of State's official general election canvass. Pages from before 1992 may "
    "be rotated; read them upright. Find every ballot proposition on the page. Each proposition has a line that starts "
    "with YES and a line that starts with NO, each followed by one vote count per county in the order of the column "
    "headings, and then the statewide total as the last number. Reply with only a JSON list, one object per "
    'proposition, like {"proposition": "100", "yes": [..], "no": [..]}, where yes and no each hold every count on the '
    "line from left to right including the total. Use null for a number you cannot read. Ignore everything that is "
    "not a proposition, such as judicial retention and candidate rows. Copy the digits exactly as printed; do not "
    "correct, add, or infer numbers. If the page has no propositions, reply []."
)


def pdf(year: int):
    return DATA / "inbox" / "manual" / "MF-556" / f"canvass{year}ge.pdf"


def image(year: int, page: int, variant: str):
    folder = DATA / "tmp" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{year}-{page}{variant}.png"
    if not out.exists():
        dpi, scale = VARIANTS[variant]
        stem = folder / f".{year}-{page}"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-singlefile", "-f", str(page), "-l", str(page)]
            + [str(pdf(year)), str(stem)],
            check=True,
        )
        picture = Image.open(stem.with_suffix(".png"))
        if year <= 1990:
            picture = picture.rotate(-90, expand=True)
        picture.resize((int(picture.width * scale / 3), int(picture.height * scale / 3))).save(out)
        stem.with_suffix(".png").unlink()
    return out


def read() -> str:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for year, (first, last) in PAGES.items():
        for page in range(first, last + 1):
            for variant in VARIANTS:
                params = MessageCreateParamsNonStreaming(
                    model="claude-haiku-5-5",
                    max_tokens=1500,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": base64.b64encode(image(year, page, variant).read_bytes()).decode(),
                                    },
                                },
                                {"type": "text", "text": PROMPT},
                            ],
                        }
                    ],
                )
                wanted[f"read:{year}:{page}:{variant}"] = ((params), (3000 * price_in + 1000 * price_out) / 1e6)
    return batch.ensure(SOURCE, "propositions", wanted)[1]


def seen(year: int, page: int, variant: str) -> list[dict[str, object]]:
    text = json.loads(raw.cached(SOURCE, f"read:{year}:{page}:{variant}").path.read_text())["content"]
    text = next(block["text"] for block in text if block.get("type") == "text")
    found = re.search(r"\[.*\]", text, re.DOTALL)
    return json.loads(found[0]) if found else []
