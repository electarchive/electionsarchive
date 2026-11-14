import base64
import io
import json
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset
from ea.store import connect

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


SUMMARY = {
    "VOTES EXCLUDED": "excluded",
    "TOTAL CONTINUING": "continuing",
    "VOTES NEEDED": "needed",
    "NON-TRANSFERABLE": "exhausted",
    "TOTALS": "total",
}
CELL = re.compile(r"(-?\d+(?:\.\d+)?)?\s*-?(X|-)?\s*(Elected)?", re.IGNORECASE)


class Count:
    def __init__(self, heading: str) -> None:
        self.heading = heading
        self.stv = False
        self.votes: dict[str, list[float | None]] = {}
        self.out: dict[str, int] = {}
        self.elected: list[str] = []
        self.withdrew: list[str] = []
        self.summary: dict[str, list[float | None]] = {}

    @property
    def rounds(self) -> int:
        return max(len(values) for values in self.votes.values())

    def problems(self, seats: int) -> list[str]:
        found = []
        width = self.rounds
        for label, values in self.votes.items():
            values = values + [None] * (width - len(values))
            seen = [index for index, value in enumerate(values) if value is not None]
            if seen and seen != list(range(seen[-1] + 1)):
                found.append(f"{label}: gap in {values}")
            if label in self.out and seen and self.out[label] < seen[-1]:
                found.append(f"{label}: value after exclusion {values}")
            if not self.stv and any(
                values[i] is not None and values[i + 1] is not None and values[i + 1] < values[i]
                for i in range(width - 1)
            ):
                found.append(f"{label}: falls {values}")
        for index in range(width):
            total = sum(values[index] or 0 for values in self.votes.values() if index < len(values))
            if self.stv:
                exhausted = self.summary.get("exhausted", [])
                grand = self.summary.get("total", [])
                spent = (exhausted[index] if index < len(exhausted) else None) or 0
                if index < len(grand) and grand[index] is not None and round(total + spent, 4) != grand[index]:
                    found.append(f"round {index + 1}: {total} + {spent} != {grand[index]}")
            else:
                continuing = self.summary.get("continuing", [])
                stage = continuing[index] if index < len(continuing) else None
                if stage is None:
                    continue
                if stage != total:
                    found.append(f"round {index + 1}: candidates {total} != continuing {stage}")
                needed = self.summary.get("needed", [])
                quota = needed[index] if index < len(needed) else None
                if quota is not None and quota not in (-(-stage // 2), stage // 2 + 1):
                    found.append(f"round {index + 1}: needed {quota} of {stage}")
        if self.stv and len(self.elected) != seats:
            found.append(f"elected {self.elected} for {seats} seats")
        return found


def counts(tables: list[dict]) -> list[Count]:
    out: list[Count] = []
    for table in tables:
        columns = [column.strip() for column in table["columns"]]
        fresh = not out or re.match(r"(1st|first)", columns[0], re.IGNORECASE) is not None
        if fresh:
            count = Count(table["heading"])
            out.append(count)
        count = out[-1]
        stv = any(
            re.sub(r"\s+", " ", row["label"]).strip().upper().startswith(("TOTALS", "NON-TRANSFERABLE"))
            for row in table["rows"]
        )
        count.stv = count.stv or stv
        keep = [
            index
            for index, column in enumerate(columns)
            if not stv or index == 0 and fresh or column.lower().endswith("total") or column.lower() in ("result", "")
        ]
        if not stv and any(column.lower().endswith("total") for column in columns):
            keep = [index for index in keep if not columns[index].lower().endswith("transfer")]
        offset = len(next(iter(count.votes.values()), [])) if not fresh else 0
        for row in table["rows"]:
            label = re.sub(r"\s+", " ", row["label"]).strip()
            kind = next((name for prefix, name in SUMMARY.items() if label.upper().startswith(prefix)), None)
            if not label:
                continue
            values: list[float | None] = []
            for index in keep:
                text = row["cells"][index].strip() if index < len(row["cells"]) else ""
                if stv and columns[index].lower() in ("result", ""):
                    if "elected" in text.lower():
                        count.elected.append(label)
                    continue
                if text.lower().startswith("withdr"):
                    count.withdrew.append(label)
                    continue
                if text.upper() == "N/A":
                    text = ""
                match = CELL.fullmatch(text.replace(" ", "") if "elected" not in text.lower() else text)
                if match is None:
                    raise ValueError(f"{table['heading']} {label}: cell {text!r}")
                number, mark, elected = match.groups()
                if elected:
                    count.elected.append(label)
                values.append(None if number is None else float(number))
                if mark and kind is None and label not in count.out:
                    count.out[label] = offset + len(values) - (1 if number is not None else 2)
            target = count.summary if kind else count.votes
            key = kind or label
            target.setdefault(key, [None] * offset)
            target[key] = target[key][:offset] + [None] * (offset - len(target[key])) + values
    for count in out:
        for values in [*count.votes.values(), *count.summary.values()]:
            while values and values[-1] is None:
                values.pop()
    return out


TOKEN = re.compile(r"-?\d+(?:\.\d+)?X?|X|-")
HEADERS = ("candidate", "candidates", "votes", "preference", "1st", "first", "transfers", "after")
NEW = re.compile(r"^[A-Z][\w' -]+, (L|E|V|M|D|C)\.|^[A-Z][. ]+[A-Z][a-z]|^(VOTES|TOTAL)\b")


def layered(text: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    pending: list[str] = []
    last: dict[str, Any] | None = None
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        tail = 0
        while tail < len(words) and TOKEN.fullmatch(words[-1 - tail]):
            tail += 1
        label, values = " ".join(words[: len(words) - tail]), words[len(words) - tail :]
        header = all(word.lower().strip("-") in HEADERS for word in label.split())
        numbers = [int(word) for word in values if word.isdigit()]
        if (
            header
            and len(numbers) == len(values) >= 2
            and numbers == list(range(numbers[0], numbers[0] + len(numbers)))
        ):
            columns = [*(["1st preference"] if numbers[0] == 1 else []), *map(str, numbers)]
            if not tables or tables[-1]["columns"] != columns:
                tables.append({"heading": "", "columns": columns, "rows": []})
            pending, last = [], None
            continue
        if not tables or label and header and not values:
            continue
        if not values:
            if last is not None and not last["own"] and not NEW.match(label):
                last["label"] = f"{last['label']} {label}"
                last["own"] = True
            elif tables[-1]["columns"][0] != "1st preference" and NEW.match(label):
                tables[-1]["rows"].append({"label": label, "cells": [], "own": True})
            else:
                pending.append(label)
            last = None if pending else last
            continue
        row = {"label": " ".join([*pending, label]).strip(), "cells": values, "own": bool(label)}
        pending = []
        tables[-1]["rows"].append(row)
        last = row
    for table in tables:
        width = len(table["columns"])
        for row in table["rows"]:
            row.pop("own", None)
            row["cells"] = (row["cells"] + [""] * width)[:width]
    return tables


def signature(count: Count) -> tuple:
    return (
        [(values, count.out.get(label)) for label, values in count.votes.items()],
        {kind: values for kind, values in count.summary.items() if kind != "excluded"},
        count.elected,
        count.withdrew,
    )


def readings(path: Path, sha256: str, seats: int) -> dict[str, tuple[list[Count], list[str]]]:
    found: dict[str, tuple[list[Count], list[str]]] = {}
    for read, tables in (
        ("image", reading("image", sha256)["tables"]),
        ("text", reading("text", sha256)["tables"]),
        ("layer", layered(layout(path))),
    ):
        try:
            parsed = counts(tables)
        except ValueError as error:
            found[read] = ([], [str(error)])
            continue
        found[read] = (
            parsed,
            [problem for count in parsed for problem in count.problems(seats if len(parsed) == 1 else 1)],
        )
    return found


ACCEPTED = {
    ("2017-03-21", "house"): ["round 26: candidates 251.0 != continuing 250.0"],
    ("2023-09-19", "crossbench"): ["round 2: 21.97 + 0.03 != 23.0"],
}


def settle(day: str, group: str, seats: int, path: Path, sha256: str) -> tuple[str, list[Count]]:
    found = readings(path, sha256, seats)
    accepted = ACCEPTED.get((day, group), [])
    passing = {read: parsed for read, (parsed, problems) in found.items() if parsed and problems == accepted}
    if not passing:
        raise ValueError(f"{day} {group}: no read passes: {[found[read][1][:3] for read in found]}")
    deepest = max(sum(count.rounds for count in parsed) for parsed in passing.values())
    passing = {read: parsed for read, parsed in passing.items() if sum(c.rounds for c in parsed) == deepest}
    for read in ("text", "image", "layer"):
        if read in passing:
            chosen = passing[read]
            for other, parsed in passing.items():
                if other != "layer" and [signature(c)[:1] for c in parsed] != [signature(c)[:1] for c in chosen]:
                    raise ValueError(f"{day} {group}: {read} and {other} pass but differ")
            text = layout(path)
            printed = {float(token.rstrip("X")) for token in TOKEN.findall(text) if token not in ("X", "-")}
            for count in chosen:
                for label, values in count.votes.items():
                    missing = [value for value in values if value is not None and value not in printed]
                    if missing:
                        raise ValueError(f"{day} {group} {label}: {missing} not in the text layer")
            return read, chosen
    raise AssertionError


PROSE: dict[tuple[str, str], tuple[int | None, int | None, int | None, str, tuple[str, ...]]] = {
    ("2004-05-13", "conservative"): (45, 0, 48, "to replace the late Lord Vivian", ("Viscount Trenchard",)),
    ("2005-01-13", "liberal_democrat"): (4, None, 4, "to replace the late Earl Russell", ("Earl of Glasgow",)),
    ("2005-03-10", "conservative"): (42, 0, 47, "to replace the late Lord Burnham", ("Lord De Mauley",)),
    ("2005-03-22", "house"): (348, 4, 687, "to replace the late Lord Aberdare", ("Viscount Eccles",)),
    ("2005-06-23", "crossbench"): (
        28,
        0,
        29,
        "to replace the late Baroness Strange",
        ("Viscount Montgomery of Alamein",),
    ),
    ("2007-03-07", "conservative"): (43, 0, 47, "following the death of Lord Mowbray and Stourton", ("Earl Cathcart",)),
    ("2008-05-22", "crossbench"): (26, 0, 29, "following the death of Baroness Darcy de Knayth", ("Earl of Stair",)),
    ("2009-07-15", "crossbench"): (27, 0, 29, "following the death of Viscount Bledisloe", ("Lord Aberdare",)),
    ("2010-06-23", "conservative"): (
        45,
        0,
        47,
        "following the death of the Earl of Northesk",
        ("Viscount Younger of Leckie",),
    ),
    ("2010-06-23", "crossbench"): (
        26,
        0,
        29,
        "following the death of Viscount Colville of Culross",
        ("Earl of Clancarty",),
    ),
    ("2011-03-22", "house"): (414, 2, 769, "following the death of Lord Strabolgi", ("Viscount Hanworth",)),
    ("2011-05-10", "crossbench"): (27, 0, 28, "following the death of Lord Monson", ("Earl of Lytton",)),
    ("2011-07-20", "conservative"): (45, 0, 47, "following the death of the Earl of Onslow", ("Lord Ashton of Hyde",)),
    ("2011-07-20", "house"): (313, 3, 769, "following the death of Lord Ampthill", ("Viscount Colville of Culross",)),
    ("2013-02-06", "conservative"): (46, 0, 48, "following the death of Earl Ferrers", ("Viscount Ridley",)),
    ("2013-07-16", "house"): (334, 2, 753, "following the death of Lord Reay", ("Lord Borwick",)),
    ("2014-04-09", "crossbench"): (27, 0, 29, "following the death of Lord Moran", ("Lord Cromwell",)),
    ("2014-10-21", "house"): (283, 0, 776, "following the death of Lord Methuen", ("Earl of Oxford and Asquith",)),
    ("2014-12-10", "crossbench"): (
        25,
        None,
        27,
        "following the death of Viscount Allenby of Megiddo and the retirement of Lord Cobbold",
        ("Lord Russell of Liverpool", "Duke of Somerset"),
    ),
    ("2015-02-02", "crossbench"): (
        26,
        None,
        28,
        "following the retirements of Lord Chorley and Lady Saltoun of Abernethy",
        ("Lord Thurlow", "Earl of Kinnoull"),
    ),
    ("2015-07-07", "crossbench"): (25, None, 28, "following the retirement of Viscount Tenby", ("Lord Mountevans",)),
    ("2015-09-16", "conservative"): (41, None, 48, "following the retirement of Lord Luke", ("Duke of Wellington",)),
    ("2015-10-20", "crossbench"): (
        26,
        None,
        29,
        "following the retirement of Viscount Montgomery of Alamein",
        ("Lord Oaksey",),
    ),
    ("2015-11-24", "conservative"): (
        44,
        None,
        48,
        "following the death of Lord Montagu of Beaulieu",
        ("Lord Fairfax of Cameron",),
    ),
    ("2016-04-19", "liberal_democrat"): (3, None, 3, "following the death of Lord Avebury", ("Viscount Thurso",)),
    ("2016-07-12", "crossbench"): (
        27,
        None,
        31,
        "following Lord Bridges ceasing to be a member of the House of Lords",
        ("Earl of Cork and Orrery",),
    ),
    ("2017-03-21", "house"): (346, None, 803, "following the death of Lord Lyell", ("Lord Colgrain",)),
    ("2017-07-19", "crossbench"): (27, None, 31, "following Lord Walpole's retirement", ("Lord Vaux of Harrowden",)),
    ("2018-07-04", "crossbench"): (
        26,
        None,
        31,
        "following the retirement of Earl Baldwin of Bewdley",
        ("Earl of Devon",),
    ),
    ("2018-07-18", "conservative"): (43, None, 48, "following Lord Glentoran's retirement", ("Lord Bethell",)),
    ("2018-11-28", "crossbench"): (29, None, 31, "following the retirement of Lord Northbourne", ("Lord Carrington",)),
    ("2019-01-22", "house"): (259, None, 785, "following the death of Lord Skelmersdale", ("Lord Reay",)),
    ("2019-03-26", "crossbench"): (28, None, 31, "following the death of Viscount Slim", ("Lord Ravensdale",)),
    ("2021-06-14", "conservative"): (
        36,
        None,
        43,
        (
            "following the retirement of the Earl of Selborne, the retirement of Lord Denham, and the non-attendance "
            "of Lord Selsdon during the 2019-21 session"
        ),
        ("Lord Sandhurst", "Earl of Leicester", "Lord Altrincham"),
    ),
    ("2021-06-15", "house"): (
        317,
        None,
        None,
        "following the retirement of the Countess of Mar",
        ("Lord Londesborough",),
    ),
    ("2021-07-09", "labour"): (None, None, None, "following the death of Lord Rea", ("Viscount Stansgate",)),
    ("2021-07-13", "house"): (237, None, None, "following the retirement of Lord Elton", ("Lord Harlech",)),
    ("2021-11-09", "house"): (264, None, None, "following the death of Viscount Simon", ("Lord Hacking",)),
    ("2022-02-08", "conservative"): (
        36,
        None,
        45,
        "following the retirement of Viscount Ridley",
        ("Lord Strathcarron",),
    ),
    ("2022-03-29", "conservative"): (
        37,
        None,
        46,
        "following the retirement of Lord Rotherwick",
        ("Viscount Camrose",),
    ),
    ("2022-07-05", "conservative"): (
        41,
        None,
        45,
        "following the retirement of Lord Brabazon of Tara and the death of Lord Swinfen",
        ("Lord Remnant", "Lord Wrottesley"),
    ),
    ("2022-10-17", "house"): (
        190,
        None,
        None,
        "following the retirements of Lord Colwyn and Viscount Ullswater",
        ("Lord Roborough", "Earl of Minto"),
    ),
    ("2022-10-19", "crossbench"): (22, None, 30, "following the retirement of the Earl of Listowel", ("Lord Hampton",)),
    ("2022-10-20", "conservative"): (
        37,
        None,
        41,
        "following the retirement of Lord Astor of Hever and the death of the Earl of Home",
        ("Earl of Effingham", "Lord Ashcombe"),
    ),
    ("2023-06-12", "house"): (212, None, None, "following the retirement of Viscount Falkland", ("Earl Russell",)),
    ("2023-09-19", "crossbench"): (
        23,
        None,
        32,
        "following the death of Lord Palmer and the retirement of Lord Hylton",
        ("Lord Meston", "Lord De Clifford"),
    ),
    ("2023-11-21", "house"): (259, None, None, "following the death of Lord Brougham and Vaux", ("Lord Camoys",)),
}


VERSION = "1"
GROUPS = {
    "house": ("Whole-House", "the whole House"),
    "conservative": ("Conservative", "the Conservative hereditary peers"),
    "crossbench": ("Crossbench", "the Crossbench hereditary peers"),
    "liberal_democrat": ("Liberal Democrat", "the Liberal Democrat hereditary peers"),
    "labour": ("Labour", "the Labour hereditary peers"),
}
PARTIES = {
    "Conservative": ("wikidata", "Q9626"),
    "Labour": ("wikidata", "Q9630"),
    "Liberal Democrat": ("gb.party", "liberal-democrats"),
    "UKIP": ("gb.party", "uk-independence-party"),
}
MANUAL = {
    ("2005-01-13", "liberal_democrat"): {"E. Carlisle": 0, "E. Glasgow": 4, "L. Kennet": 0},
    ("2021-07-09", "labour"): {"Viscount Stansgate": None},
}
VOTES, REGISTERED, VALID, INVALID, QUOTA, EXHAUSTED = 1, 2, 4, 5, 26, 21
AV = (
    "The alternative vote, under Standing Order 9 and the arrangements of the Clerk of the Parliaments. Voters rank "
    "as many candidates as they wish. A candidate whose votes equal or exceed those of all other continuing "
    "candidates combined is elected; otherwise the candidate or candidates with the fewest votes are excluded and "
    "their papers pass to the next preference. Count round 1 holds first preferences and round n + 1 the totals "
    "after the nth transfer; the quota measure holds the printed votes needed in order to be elected at each stage."
)
STV = (
    "The single transferable vote, used from 2021 when a by-election filled more than one seat. The quota is the "
    "valid votes divided by the seats plus one, without rounding or adding one. Surpluses pass on at fractional "
    "value in proportion to the next preferences on all of the elected candidate's papers, and the candidate with "
    "the fewest votes is otherwise excluded. Count round n holds the totals printed after stage n; the exhausted "
    "measure holds the printed non-transferable papers."
)
TWICE = (
    "Two seats were filled from one ballot paper counted twice under the alternative vote. The second count "
    "discounted every preference for the candidate elected at the first count and passed it to the next preference."
)
BLANK = (
    "The notice leaves the stage 2 total blank for Lord Aldington, who held 1 first-preference vote until his "
    "exclusion at stage 3, so the printed stage 2 totals sum to 22.97 with 0.03 non-transferable, against 23 valid "
    "votes."
)
SHORT = "The notice prints 108 votes for Lord Bethell and 143 for Lord Colgrain at the last stage, which sum to 251 against 250 continuing votes."
MISTITLED = (
    "The notice is headed Conservative hereditary peers' by-election, but every member of the House was eligible "
    "to vote, so it was a whole-House by-election."
)
CAVEATS = {("2017-03-21", "house"): SHORT, ("2023-09-19", "crossbench"): BLANK}


def core(label: str) -> str:
    text = re.sub(r"\(.*?\)", "", label).strip(" ,")
    if "," in text:
        return text.split(",")[0].strip()
    return re.sub(r"^[A-Z]\.\s+", "", text).strip()


def party(label: str, group: str) -> str | None:
    if group != "house":
        return {"conservative": "Conservative", "labour": "Labour", "liberal_democrat": "Liberal Democrat"}.get(group)
    found = re.findall(r"\(([^()]+)\)", label)
    return found[-1] if found and found[-1] in PARTIES else None


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Lords hereditary peers' by-elections 2004-2023, from the Clerk of the Parliaments' result notices",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        lords = cs.key("gb.body", "house-of-lords")
        if lords in cs.minted:
            cs.add("body", id=lords, polity_id=gb, kind="upper_house")
            name(lords, "House of Lords")
            cs.add("external_id", entity_id=lords, scheme="wikidata", value="Q11007")
        parties: dict[str, UUID] = {}
        for label, (scheme, value) in PARTIES.items():
            parties[label] = cs.key(scheme, value)
            if parties[label] in cs.minted:
                raise ValueError(f"{label}: no party for {scheme} {value}")
        systems = {}
        for code, label, note, rules in (
            ("av", "Alternative vote (House of Lords by-elections)", AV, {"formula": "irv"}),
            ("stv", "Single transferable vote (House of Lords by-elections)", STV, {"formula": "stv"}),
        ):
            systems[code] = cs.key("gb.electoral_system", f"lords-{code}")
            cs.add("electoral_system", id=systems[code], polity_id=gb, rules=rules, rules_version=1)
            name(systems[code], label)
            cs.add("note", entity_id=systems[code], kind="methodology", body_md=note)
        cs.add("scheme", code="gb.lords_electorate", polity_id=gb, kind="electoral_district", rank=0, noun="electorate")
        units: dict[str, UUID] = {}
        for group, (label, _) in GROUPS.items():
            units[group] = cs.unit("gb.lords_electorate", group)
            cs.add(
                "reporting_unit",
                id=units[group],
                polity_id=gb,
                scheme_code="gb.lords_electorate",
                code=group,
                area_relation="unlocated",
                kind="aggregate",
            )
            name(units[group], "House of Lords" if group == "house" else f"{label} hereditary peers")
        values: list[tuple[UUID, UUID, UUID | None, int, int, float]] = []
        titles: set[str] = set()
        for day, until, group, seats, stamp, url in FILES:
            valid, spoilt, eligible, vacancy, winners = PROSE[(day, group)]
            artifact = raw.cached(SOURCE, wayback(stamp, url))
            text = layout(artifact.path)
            heading = re.search(
                r"(?:(?:Liberal Democrat|Conservative|Crossbench|Labour|Whole-House) )?hereditary peers’ by-elections?, [\w ]+?: results?",
                " ".join(text.split()),
                re.IGNORECASE,
            )
            if heading is None:
                raise ValueError(f"{day} {group}: no heading")
            title = heading[0]
            if title in titles:
                raise ValueError(f"{title}: two notices")
            titles.add(title)
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=url, license=LICENSE
            )
            cs.artifact(artifact, source)
            for number in (valid, spoilt, eligible):
                if number is not None and number > 9 and str(number) not in text:
                    raise ValueError(f"{day} {group}: {number} is not printed")
            held, closed = date.fromisoformat(day), date.fromisoformat(until)
            noun, electorate = GROUPS[group]
            key = f"{day}:lords:{group}"
            election = cs.key("gb.election", key)
            cs.add(
                "election", id=election, polity_id=gb, kind="by_election", date_from=held, date_to=closed, status="held"
            )
            name(election, f"{noun} hereditary peers' by-election, {held:%-d %B %Y}")
            path = f"gb/{day}-lords-{group.replace('_', '-')}-by-election"
            cs.add("slug", path=path, entity_id=election)
            seat_noun = "seat" if seats == 1 else f"{seats} seats"
            cs.add(
                "note",
                entity_id=election,
                kind="context",
                body_md=f"An election by {electorate} to {seat_noun} in the House of Lords reserved for hereditary "
                f"peers under section 2 of the House of Lords Act 1999, held {vacancy}.",
            )
            if (day, until, group) == ("2021-07-13", "2021-07-14", "house"):
                cs.add("note", entity_id=election, kind="caveat", body_md=MISTITLED)
            persons: dict[str, UUID] = {}
            manual = MANUAL.get((day, group))
            if manual is not None:
                count = Count("")
                count.votes = {label: [] if votes is None else [float(votes)] for label, votes in manual.items()}
                tallies = [count]
            else:
                _, tallies = settle(day, group, seats, artifact.path, artifact.sha256)
            twice = len(tallies) > 1
            for index, count in enumerate(tallies):
                stv = count.stv
                contest_key = key if not twice else f"{key}:{index + 1}"
                contest = cs.key("gb.contest", contest_key)
                magnitude = 1 if twice else seats
                tacit = manual is not None and all(not votes for votes in count.votes.values())
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=lords,
                    magnitude=magnitude,
                    electoral_system_id=systems["stv" if stv else "av"],
                    round_number=1,
                    default_result_kind="tacit" if tacit else "counted",
                )
                if twice:
                    name(contest, f"{('First', 'Second')[index]} count")
                    cs.add("slug", path=f"{path}/{('first', 'second')[index]}-count", entity_id=contest)
                    cs.add("note", entity_id=contest, kind="context", body_md=TWICE)
                else:
                    name(contest, f"{noun} hereditary peers ({seat_noun})")
                    cs.add("slug", path=f"{path}/count", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=None if tacit else 1,
                )
                cs.add("citation", entity_id=result_set, source_id=source)
                if (day, group) in CAVEATS:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEATS[(day, group)])
                won = list(winners[index : index + 1] if twice else winners)
                if tacit:
                    elected = list(count.votes)
                elif stv:
                    elected = count.elected
                else:
                    final = count.rounds - 1
                    elected = [
                        max(
                            count.votes,
                            key=lambda label: (len(count.votes[label]) > final and count.votes[label][final]) or 0,
                        )
                    ]
                if len(elected) != len(won) or any(
                    sum(core(label).lower() in printed.lower() for label in elected) != 1 for printed in won
                ):
                    raise ValueError(f"{day} {group}: elected {elected}, notice says {won}")
                labels = list(dict.fromkeys([*count.votes, *count.withdrew]))
                for position, label in enumerate(labels, 1):
                    option_key = f"{contest_key}:{label}"
                    option = cs.key(f"{SOURCE}.option", option_key)
                    member = party(label, group)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=position,
                        party_id=parties.get(member or ""),
                        ballot_label=label,
                    )
                    identity = re.sub(
                        r"\s*\((Conservative|Crossbench|Labour|Liberal Democrat|UKIP|Undeclared|No party/group affiliation declared)\)",
                        "",
                        label,
                    )
                    if identity not in persons:
                        persons[identity] = cs.key(f"{SOURCE}.person", f"{key}:{identity}")
                        if persons[identity] in cs.minted:
                            cs.add("person", id=persons[identity])
                            name(persons[identity], identity)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=persons[identity],
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        member_of_party_id=parties.get(member or ""),
                    )
                    tally = count.votes.get(label, [])
                    if label in count.withdrew:
                        outcome = "withdrew"
                    elif label in elected:
                        outcome = "elected"
                    elif len(tally) == count.rounds and tally[-1] is not None:
                        outcome = "lost"
                    else:
                        outcome = "eliminated"
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
                    for number, votes in enumerate(tally, 1):
                        if votes is not None:
                            values.append((result_set, units[group], option, VOTES, number, votes))
                if tacit:
                    continue
                for measure, number, stage in ((VALID, valid, 1), (INVALID, spoilt, 0), (REGISTERED, eligible, 0)):
                    if number is not None:
                        values.append((result_set, units[group], None, measure, stage, number))
                for kind, measure in (("needed", QUOTA), ("exhausted", EXHAUSTED)):
                    for number, figure in enumerate(count.summary.get(kind, []), 1):
                        if figure is not None and (kind != "exhausted" or stv):
                            values.append((result_set, units[group], None, measure, number, figure))
        for result_set, unit, option, measure, number, value in values:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=number,
                flags=0,
                value=value,
            )
        return cs.write()
