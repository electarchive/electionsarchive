import base64
import io
import json
import re
import subprocess
from pathlib import Path

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw

SOURCE = "gb.lords_peers"
PUBLISHER = "Clerk of the Parliaments"
LICENSE = "Open Parliament Licence v3.0"
INDEX = (
    "https://www.parliament.uk/mps-lords-and-offices/offices/lords/house-of-lords-external-communications/by-elections/"
)
MODEL = "claude-haiku-5-5"
PROMPT = "1"
LONGEST = 1568
FILES: list[tuple[str, str, str, int, str, str]] = [
    (
        "2004-05-13",
        "2004-05-13",
        "conservative",
        1,
        "20101013130041",
        "https://www.parliament.uk/documents/lords-information-office/hoflpressnotice130504.pdf",
    ),
    (
        "2005-01-13",
        "2005-01-13",
        "liberal_democrat",
        1,
        "20101013130039",
        "https://www.parliament.uk/documents/lords-information-office/hoflpressnotice130105.pdf",
    ),
    (
        "2005-03-10",
        "2005-03-10",
        "conservative",
        1,
        "20101013130039",
        "https://www.parliament.uk/documents/lords-information-office/hoflpressnotice100305.pdf",
    ),
    (
        "2005-03-22",
        "2005-03-22",
        "house",
        1,
        "20101013130039",
        "http://www.parliament.uk/documents/lords-information-office/hoflpressnotice230305.pdf",
    ),
    (
        "2005-06-23",
        "2005-06-23",
        "crossbench",
        1,
        "20101013130038",
        "https://www.parliament.uk/documents/lords-information-office/hoflnotice230605.pdf",
    ),
    (
        "2007-03-07",
        "2007-03-07",
        "conservative",
        1,
        "20100613014940",
        "https://www.parliament.uk/documents/lords-information-office/holnotice070307.pdf",
    ),
    (
        "2008-05-22",
        "2008-05-22",
        "crossbench",
        1,
        "20101013130033",
        "https://www.parliament.uk/documents/lords-information-office/byelectionresults080522.pdf",
    ),
    (
        "2009-07-15",
        "2009-07-15",
        "crossbench",
        1,
        "20101013130033",
        "https://www.parliament.uk/documents/lords-information-office/byelectionresults090715.pdf",
    ),
    (
        "2010-06-23",
        "2010-06-23",
        "conservative",
        1,
        "20220128161049",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/conservative-byelection-results.pdf",
    ),
    (
        "2010-06-23",
        "2010-06-23",
        "crossbench",
        1,
        "20101013130032",
        "https://www.parliament.uk/documents/lords-information-office/crossbench-byelection-results.pdf",
    ),
    (
        "2011-03-22",
        "2011-03-22",
        "house",
        1,
        "20210714104643",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/hol-byelection-20110323.pdf",
    ),
    (
        "2011-05-10",
        "2011-05-10",
        "crossbench",
        1,
        "20200928192646",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/holbyelectionresult10052011.pdf",
    ),
    (
        "2011-07-20",
        "2011-07-20",
        "conservative",
        1,
        "20200928190359",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2011/result-by-election-e-onslow.pdf",
    ),
    (
        "2011-07-20",
        "2011-07-20",
        "house",
        1,
        "20220128154408",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2011/result-by-election-ampthill.pdf",
    ),
    (
        "2013-02-06",
        "2013-02-06",
        "conservative",
        1,
        "20210704171229",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2013/conservative-hereditary-peers-by-election-result-earl-ferrers.pdf",
    ),
    (
        "2013-07-16",
        "2013-07-16",
        "house",
        1,
        "20210516070613",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2013/hereditary-peers-by-election-lord-reay-july-2013.pdf",
    ),
    (
        "2014-04-09",
        "2014-04-09",
        "crossbench",
        1,
        "20210714084515",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2014/lords-notice-crossbench-hereditary-peers-by-election-result-apr-14.pdf",
    ),
    (
        "2014-10-21",
        "2014-10-21",
        "house",
        1,
        "20201024160605",
        "https://www.parliament.uk/globalassets/documents/lords-finance-office/2013-14/hereditary-peers-by-election-result-methuen.pdf",
    ),
    (
        "2014-12-10",
        "2014-12-10",
        "crossbench",
        2,
        "20210505235651",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2014/lords-notice-hereditary-peers-by-election-dec-2014-result-allenby-cobbold.pdf",
    ),
    (
        "2015-02-02",
        "2015-02-02",
        "crossbench",
        2,
        "20210504183924",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2014/lords-notice-crossbench-hereditary-peers-by-election-result-feb-15.pdf",
    ),
    (
        "2015-07-07",
        "2015-07-07",
        "crossbench",
        1,
        "20150711180120",
        "https://www.parliament.uk/documents/lords-information-office/Hereditary-peers-by-election-result-july2015.pdf",
    ),
    (
        "2015-09-16",
        "2015-09-16",
        "conservative",
        1,
        "20150927084559",
        "https://www.parliament.uk/documents/lords-information-office/2015/Hereditary-peers-by-election-result-Luke.pdf",
    ),
    (
        "2015-10-20",
        "2015-10-20",
        "crossbench",
        1,
        "20151112144959",
        "https://www.parliament.uk/documents/lords-information-office/2015/Hereditary-peers-by-election-result-Montgomery-of-Alamein.pdf",
    ),
    (
        "2015-11-24",
        "2015-11-24",
        "conservative",
        1,
        "20151125012456",
        "https://www.parliament.uk/documents/lords-information-office/2015/Result-Conservative-hereditary-peers-by-election-result-Montagu-of-Beaulieu.pdf",
    ),
    (
        "2016-04-19",
        "2016-04-19",
        "liberal_democrat",
        1,
        "20160419234109",
        "http://www.parliament.uk/documents/publications-records/House-of-Lords-Publications/By-elections/Result-Liberal-Democrat-hereditary-peers-by-election-result-Avebury.pdf.pdf",
    ),
    (
        "2016-07-12",
        "2016-07-12",
        "crossbench",
        1,
        "20250915090020",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2016/hereditary-peers-by-election-result-bridges.pdf",
    ),
    (
        "2017-03-21",
        "2017-03-21",
        "house",
        1,
        "20170328015902",
        "http://www.parliament.uk/documents/lords-information-office/2017/Hereditary-peers-by-election-result-(Lyell).pdf",
    ),
    (
        "2017-07-19",
        "2017-07-19",
        "crossbench",
        1,
        "20171109022713",
        "http://www.parliament.uk/documents/lords-information-office/2017/Hereditary-peers-by-election-result-(Walpole).pdf",
    ),
    (
        "2018-07-04",
        "2018-07-04",
        "crossbench",
        1,
        "20180704214155",
        "https://www.parliament.uk/documents/lords-information-office/2018/Result-by-election-04-07-18.pdf",
    ),
    (
        "2018-07-18",
        "2018-07-18",
        "conservative",
        1,
        "20240916222353",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2018/hereditary-peers-by-election-result-glentoran.pdf",
    ),
    (
        "2018-11-28",
        "2018-11-28",
        "crossbench",
        1,
        "20181128210914",
        "https://www.parliament.uk/documents/lords-information-office/2018/Result-by-election-28-11-18.pdf",
    ),
    (
        "2019-01-22",
        "2019-01-22",
        "house",
        1,
        "20190213183741",
        "https://www.parliament.uk/documents/lords-information-office/2019/Result-by-election-23-01-19.pdf",
    ),
    (
        "2019-03-26",
        "2019-03-26",
        "crossbench",
        1,
        "20190327161836",
        "https://www.parliament.uk/documents/lords-information-office/2019/Result-by-election-27-03-19.pdf",
    ),
    (
        "2021-06-14",
        "2021-06-14",
        "conservative",
        3,
        "20210624195830",
        "https://www.parliament.uk/contentassets/89574e5a22664ce6b21bd10898717cdc/hereditary-peers-by-election-result-selborne-et-al.pdf",
    ),
    (
        "2021-06-15",
        "2021-06-16",
        "house",
        1,
        "20210624195920",
        "https://www.parliament.uk/contentassets/89574e5a22664ce6b21bd10898717cdc/hereditary-peers-by-election-result-mar.pdf",
    ),
    (
        "2021-07-09",
        "2021-07-09",
        "labour",
        1,
        "20210710161948",
        "https://www.parliament.uk/globalassets/documents/lords-communications/by-elections/hereditary-peers-by-election-result-rea.pdf",
    ),
    (
        "2021-07-13",
        "2021-07-14",
        "house",
        1,
        "20210715132214",
        "https://www.parliament.uk/globalassets/documents/lords-communications/by-elections/hereditary-peers-by-election-result-elton.pdf",
    ),
    (
        "2021-11-09",
        "2021-11-10",
        "house",
        1,
        "20211115155816",
        "https://www.parliament.uk/globalassets/documents/lords-communications/by-elections/hereditary-peers-by-election-result-simon.pdf",
    ),
    (
        "2022-02-08",
        "2022-02-08",
        "conservative",
        1,
        "20220210024632",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2022/hereditary-peers-by-election-result-ridley.pdf",
    ),
    (
        "2022-03-29",
        "2022-03-29",
        "conservative",
        1,
        "20220331170758",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2022/hereditary-peers-by-election-result-rotherwick.pdf",
    ),
    (
        "2022-07-05",
        "2022-07-05",
        "conservative",
        2,
        "20220706155106",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2022/hereditary-peers-by-election-result-brabazon-swinfen.pdf",
    ),
    (
        "2022-10-17",
        "2022-10-18",
        "house",
        2,
        "20221019213400",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2022/hereditary-peers-by-election-result-colwyn-ullswater.pdf",
    ),
    (
        "2022-10-19",
        "2022-10-19",
        "crossbench",
        1,
        "20221020113257",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2022/hereditary-peers-by-election-result-listowel.pdf",
    ),
    (
        "2022-10-20",
        "2022-10-20",
        "conservative",
        2,
        "20221024155548",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2022/hereditary-peers-by-election-result-astor-of-hever-and-home.pdf",
    ),
    (
        "2023-06-12",
        "2023-06-13",
        "house",
        1,
        "20230614174524",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2023/hereditary-peers-by-election-result-falkland.pdf",
    ),
    (
        "2023-09-19",
        "2023-09-19",
        "crossbench",
        2,
        "20230926085400",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2023/hereditary-peers-by-election-result-palmer-hylton.pdf",
    ),
    (
        "2023-11-21",
        "2023-11-22",
        "house",
        1,
        "20231123124149",
        "https://www.parliament.uk/globalassets/documents/lords-information-office/2023/hereditary-peers-by-election-result-brougham-and-vaux.pdf",
    ),
]
READ_SYSTEM = (
    "You transcribe the result notice of a House of Lords hereditary peers' by-election, issued by the Clerk of the "
    "Parliaments. Copy every table of votes in the notice (the distribution of votes at each count or stage; some "
    "notices have one table per count) exactly as printed, without correcting, adding, or computing anything. For each "
    "table give its heading as printed, the headers of its value columns in order (join a header that wraps across "
    "lines; when one stage heading spans a transfer column and a running-total column, list them as two columns, "
    "'<stage> transfer' and '<stage> total'), and every row in order, the candidate rows and the summary rows alike "
    "(votes excluded or non-transferable, total continuing votes, totals, votes needed or quota). For each row give the "
    "label as printed (keep the rank abbreviation and any title or party group in parentheses) and one cell per value "
    "column, as printed: a number with its sign and decimals, X, -, 0X or 3X when a figure and an X are printed "
    "together, a word such as Elected, or an empty string when the cell is blank. Never shift a figure into a "
    "neighboring column: a blank cell stays blank. Leave out footnote markers. When the notice has no table (one "
    "candidate, or the votes are only in the text), return no tables."
)
CELLS = {"type": "array", "items": {"type": "string"}}
READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "columns": CELLS,
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"label": {"type": "string"}, "cells": CELLS},
                            "required": ["label", "cells"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["heading", "columns", "rows"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tables"],
    "additionalProperties": False,
}
READS = {
    "image": "Transcribe the tables in these page images of the notice.",
    "text": "Transcribe the tables in this text layer of the notice, extracted with its layout kept.",
}


def wayback(stamp: str, url: str) -> str:
    return f"https://web.archive.org/web/{stamp}id_/{url}"


def read_key(read: str, sha256: str) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{read}/{sha256}"


def reading(read: str, sha256: str) -> dict:
    message = json.loads(raw.cached(SOURCE, read_key(read, sha256)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def layout(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def images(path: Path) -> list[Image.Image]:
    info = subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, text=True).stdout
    found = re.search(r"Pages:\s+(\d+)", info)
    if found is None:
        raise ValueError(f"{path} has no page count")
    out = []
    for page in range(1, int(found[1]) + 1):
        png = subprocess.run(
            ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", str(path)],
            check=True,
            capture_output=True,
        ).stdout
        image = Image.open(io.BytesIO(png))
        image.thumbnail((LONGEST, LONGEST))
        out.append(image)
    return out


def fetch() -> None:
    raw.fetch(SOURCE, wayback("20251221232954", INDEX), suffix=".html")
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for *_, stamp, url in FILES:
        artifact = raw.fetch(SOURCE, wayback(stamp, url), suffix=".pdf")
        pages = images(artifact.path)
        blocks: list[ImageBlockParam] = []
        pixels = 0
        for image in pages:
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            pixels += image.width * image.height
            blocks.append(
                ImageBlockParam(
                    type="image",
                    source={
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(buffer.getvalue()).decode(),
                    },
                )
            )
        text = layout(artifact.path)
        for read, ask in READS.items():
            content: list[ImageBlockParam | TextBlockParam] = (
                [*blocks, TextBlockParam(type="text", text=ask)]
                if read == "image"
                else [TextBlockParam(type="text", text=f"{ask}\n\n{text}")]
            )
            params = MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=32000,
                system=READ_SYSTEM,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
            )
            tokens = pixels / 750 if read == "image" else len(text) / 3
            wanted[read_key(read, artifact.sha256)] = (params, (tokens * price_in + 8000 * price_out) / 1e6)
    print(batch.ensure(SOURCE, "results", wanted)[1])
