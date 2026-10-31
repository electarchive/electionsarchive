import base64
import json

from ea import raw

SOURCE = "us.mo_directory"
PUBLISHER = "Missouri Secretary of State"
IMAGE = "https://cdm16795.contentdm.oclc.org/digital/iiif/bluebook/{}/full/full/0/default.jpg"
VOLUMES: dict[str, list[range]] = {
    "1879": [range(538, 540)],
    "1881": [range(77, 81)],
    "1883": [range(233, 235), range(251, 253)],
    "1885": [range(636, 648)],
    "1887": [range(1964, 1973), range(2029, 2038)],
}
PROMPTS = {
    "a": (
        "This is one page of a printed Missouri election-returns volume from the 1880s. Transcribe every table on the "
        'page exactly as printed. Reply with only a JSON object: {"title": the printed headings, "blocks": a list '
        'with one entry per table or district block, each {"heading": the block heading, "columns": the column '
        'headings in printed order, "rows": [[row label, then one entry per column]]}}. Use an integer for each '
        "printed number, null for a blank or dotted cell, and include the printed total, majority, and plurality rows "
        "as rows with their labels. Copy every digit as printed; do not add, correct, or infer numbers."
    ),
    "b": (
        "Read the table or tables on this scanned page of Missouri election returns, line by line from the top. "
        'Output only JSON of the form {"title": string, "blocks": [{"heading": string, "columns": [string], '
        '"rows": [[label, value, value, ...]]}]}. Each value is the integer printed in that column of that line, or '
        "null where the cell is blank or filled with dots. Keep total, majority, and plurality lines as rows. Never "
        "correct or compute a number; if a digit is unclear, still give your best reading of what is printed."
    ),
}


def pages() -> list[int]:
    return [item for ranges in VOLUMES.values() for span in ranges for item in span]


def fetch() -> None:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for item in pages():
        image = raw.fetch(SOURCE, IMAGE.format(item), suffix=".jpg")
        data = base64.b64encode(image.path.read_bytes()).decode()
        for variant, prompt in PROMPTS.items():
            params = MessageCreateParamsNonStreaming(
                model="claude-haiku-5-5",
                max_tokens=16000,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            wanted[f"read:{item}:{variant}"] = (params, (4500 * price_in + 6000 * price_out) / 1e6)
    print(batch.ensure(SOURCE, "pages", wanted)[1])


def reading(item: int, variant: str) -> dict[str, object]:
    text = json.loads(raw.cached(SOURCE, f"read:{item}:{variant}").path.read_text())["content"][0]["text"]
    return json.loads(text[text.index("{") : text.rindex("}") + 1])
