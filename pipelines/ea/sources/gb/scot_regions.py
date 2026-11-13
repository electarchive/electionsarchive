import base64
import io
import json
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.names import display
from ea.sources.gb import leh
from ea.store import DATA, connect

SOURCE = "gb.scot_regions"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CORRECTIONS = Path(__file__).with_name("scot_regions_corrections.tsv")
BASE = "https://www.electionscentre.co.uk/wp-content/uploads/2015/10"
MODEL = "claude-haiku-5-5"
PROMPT = "1"
COLUMN_PROMPT = "2"
CHECK_PROMPT = "3"
NAMES_PROMPT = "3"
LONGEST = 1568
VOLUMES: dict[str, dict] = {
    "b1974": {
        "tier": "both",
        "day": date(1974, 5, 7),
        "file": "Scottish-Regional-and-District-Elections-1974",
        "pages": (10, 75),
    },
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
COLUMN_SYSTEM = (
    "You read one column of a page of a printed book of Scottish local election results by Bochel and Denver. "
    "Read every entry in the column from top to bottom and leave none out: the column may start in the middle of an "
    "entry carried over from the previous column, and it may hold several council headings, each followed by its "
    "own divisions or wards numbered from 1. A council heading is the name of a region, islands area, or district "
    "printed in capitals as a title. Each division or ward starts with its number (sometimes with a letter, such as "
    "18 (B)) and, in some books, its name. Each has an electorate and, when contested, the turnout, the rejected "
    "ballots, the candidates with their votes and percentages, and the majority. A candidate line has an optional "
    "marker (=, *, +, or similar), an optional title such as Ms, the initials, the surname, the party label, the "
    "votes, and the percentage. Unopposed candidates have Unopposed instead of votes; a ward with no candidates says "
    "No candidate or No nominations, which goes in note. For an entry carried over from the previous column, leave "
    "number null and give what is printed. Copy names, markers, and party labels exactly as printed, and every "
    "figure exactly as printed, digits only, without thousands separators. Leave a figure null when it is not "
    "printed. Put a line such as LAB gain from IND in note. Skip page numbers and running titles."
)
READ_SYSTEM_1974 = (
    "You read one spread of a printed book of the 1974 Scottish regional and district elections by Bochel and "
    "Denver. The spread holds two printed pages, each with two columns: read each column from top to bottom, "
    "left page first. A council heading is a title in capitals such as GRAMPIAN REGION or ISLANDS AREAS, or a "
    "district name such as Kirkcaldy District; report it once where it appears and set council on each division "
    "to the latest heading in reading order, null when none appears on this spread. Each division starts at the "
    "left margin with its number printed with a full stop, such as 12., then, in some councils, its place name "
    "(such as Moat/Polwarth or St. Magnus, which may wrap onto the line above), then the word Electorate and the "
    "electorate. Put the printed number in number, digits only and never the electorate, and the place name in "
    "name, or an empty string when none is printed. Candidate names are listed at the left in order, and the "
    "parties, votes, and percentages are listed at the right in the same order; pair them line by line. Each "
    "candidate line has an optional title such as Lord or Sir, the initials or given names, and the surname; set "
    "party to the label printed at the right (Con, Lab, SNP, Ind, Lib, Comm, or similar). Unopposed candidates have "
    "the word Unopposed instead of votes. Majority is the figure after Maj. and turnout the figure after Turnout, "
    "without the percent sign. Copy names and every figure exactly as printed, digits only, without thousands "
    "separators, and a share printed as .6 as 0.6. Leave a figure null when it is not printed. Skip page numbers, "
    "running titles, and commentary text."
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


ROUNDS = [volume for volume, spec in VOLUMES.items() if spec["tier"] != "both"]


def url(volume: str) -> str:
    return f"{BASE}/{VOLUMES[volume]['file']}.pdf"


def check_key(sha256: str, page: int, side: str, council: str, division: str) -> str:
    return f"anthropic-batch:{MODEL}/{CHECK_PROMPT}/{sha256}/{page}{side}/{council}/{division}"


def read_key(sha256: str, page: int, side: str = "", prompt: str = "") -> str:
    return f"anthropic-batch:{MODEL}/{prompt or (COLUMN_PROMPT if side else PROMPT)}/{sha256}/{page}{side}"


def vision(sha256: str, page: int, side: str = "", prompt: str = "") -> dict:
    message = json.loads(raw.cached(SOURCE, read_key(sha256, page, side, prompt)).path.read_text())
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


def request(image: Image.Image, system: str, ask: str) -> tuple[MessageCreateParamsNonStreaming, float]:
    price_in, price_out = batch.PRICES[MODEL]
    image.thumbnail((LONGEST, LONGEST))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    params = MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=12000,
        system=system,
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
                    TextBlockParam(type="text", text=ask),
                ],
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
    )
    return params, (image.width * image.height / 750 * price_in + 6000 * price_out) / 1e6


def gutter(image: Image.Image) -> int:
    light = list(image.convert("L").resize((image.width, 1), Image.Resampling.BOX).tobytes())
    low, high = int(image.width * 0.4), int(image.width * 0.6)
    return max(range(low, high), key=lambda x: sum(light[x - 6 : x + 7]))


def rendered(artifact: raw.Artifact, page: int) -> Image.Image:
    png = subprocess.run(
        ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
        + [str(artifact.path)],
        check=True,
        capture_output=True,
    ).stdout
    return Image.open(io.BytesIO(png))


def halves(image: Image.Image) -> dict[str, Image.Image]:
    middle = gutter(image)
    return {
        "": image,
        "L": image.crop((0, 0, middle, image.height)),
        "R": image.crop((middle, 0, image.width, image.height)),
    }


def fetch() -> None:
    reads: dict[str, batch.Want] = {}
    names: dict[str, batch.Want] = {}
    columns: dict[str, batch.Want] = {}
    for volume, spec in VOLUMES.items():
        artifact = raw.fetch(SOURCE, url(volume), suffix=".pdf")
        first, last = spec["pages"]
        for page in range(first, last + 1):
            image = rendered(artifact, page)
            if volume != "b1974":
                for side, crop in halves(image).items():
                    if side:
                        columns[read_key(artifact.sha256, page, side)] = request(
                            crop, COLUMN_SYSTEM, "Read the councils and divisions in this column."
                        )
            if volume == "b1974":
                names[read_key(artifact.sha256, page, prompt=NAMES_PROMPT)] = request(
                    image, READ_SYSTEM_1974, "Read the councils and divisions on this spread."
                )
            reads[read_key(artifact.sha256, page)] = request(
                image,
                READ_SYSTEM_1974 if volume == "b1974" else READ_SYSTEM,
                "Read the councils and divisions on this page.",
            )
    print(batch.ensure(SOURCE, "volumes", reads)[1])
    print(batch.ensure(SOURCE, "names", names)[1])
    print(batch.ensure(SOURCE, "columns", columns)[1])
    doubts = PARSED / "doubts.tsv"
    if not doubts.exists():
        return
    checks: dict[str, batch.Want] = {}
    for (volume, page), rows in pl.read_csv(doubts, separator="\t", infer_schema_length=0).group_by(
        "volume", "page", maintain_order=True
    ):
        artifact = raw.cached(SOURCE, url(str(volume)))
        crops = halves(rendered(artifact, int(str(page))))
        for row in rows.iter_rows(named=True):
            label = next(name for key, name, _ in councils(row["volume"]) if key == row["council"])
            noun = "electoral division" if VOLUMES[row["volume"]]["tier"] == "region" else "ward"
            where = "column" if row["side"] else "page"
            checks[check_key(artifact.sha256, int(row["page"]), row["side"] or "", row["council"], row["number"])] = (
                request(
                    crops[row["side"] or ""].copy(),
                    COLUMN_SYSTEM,
                    f"Read only {noun} {row['number']} of {label} on this {where}, and return that one entry in "
                    "divisions. Read every figure slowly and check that the votes match the printed majority and "
                    "percentages.",
                )
            )
    print(batch.ensure(SOURCE, "checks", checks)[1])


REGIONS = [
    ("highland", "Highland", None),
    ("grampian", "Grampian", None),
    ("tayside", "Tayside", None),
    ("fife", "Fife", None),
    ("lothian", "Lothian", None),
    ("central", "Central", None),
    ("borders", "Borders", None),
    ("strathclyde", "Strathclyde", None),
    ("dumfries-and-galloway", "Dumfries and Galloway", None),
    ("orkney", "Orkney Islands", "S12000023"),
    ("shetland", "Shetland Islands", "S12000027"),
    ("western-isles", "Western Isles", "S12000013"),
]
DISTRICTS = {
    "highland": [
        "Caithness",
        "Sutherland",
        "Ross and Cromarty",
        "Skye and Lochalsh",
        "Lochaber",
        "Inverness",
        "Badenoch and Strathspey",
        "Nairn",
    ],
    "grampian": ["Moray", "Banff and Buchan", "Gordon", "City of Aberdeen", "Kincardine and Deeside"],
    "tayside": ["Angus", "City of Dundee", "Perth and Kinross"],
    "fife": ["Kirkcaldy", "North East Fife", "Dunfermline"],
    "lothian": ["West Lothian", "City of Edinburgh", "Midlothian", "East Lothian"],
    "central": ["Clackmannan", "Stirling", "Falkirk"],
    "borders": ["Tweeddale", "Ettrick and Lauderdale", "Roxburgh", "Berwickshire"],
    "strathclyde": [
        "Argyll and Bute",
        "Dumbarton",
        "City of Glasgow",
        "Clydebank",
        "Bearsden and Milngavie",
        "Strathkelvin",
        "Cumbernauld and Kilsyth",
        "Monklands",
        "Motherwell",
        "Hamilton",
        "East Kilbride",
        "Eastwood",
        "Clydesdale",
        "Renfrew",
        "Inverclyde",
        "Cunninghame",
        "Kilmarnock and Loudoun",
        "Kyle and Carrick",
        "Cumnock and Doon Valley",
    ],
    "dumfries-and-galloway": ["Wigtown", "Stewartry", "Nithsdale", "Annandale and Eskdale"],
}
VOTES, REGISTERED, INVALID = 1, 2, 5
FORMED = date(1975, 5, 16)
ABOLISHED = date(1996, 4, 1)
PUBLISHER = "J. M. Bochel and D. T. Denver"
SOURCE_NOTE = (
    "Results and statistics volumes compiled at the University of Dundee from the returning officers' figures, one "
    "per round, scanned and published by the Elections Centre. Each division or ward is listed by number (with its "
    "name in some volumes), with its electorate, turnout, rejected ballots, candidates' votes and shares, and "
    "majority; unopposed returns are marked."
)
METHOD = (
    "Read from the scanned volume twice, once page by page and once column by column, and checked against the "
    "printed majorities, shares, and turnouts; where the two readings differed, the reading that agrees with the "
    "printed totals is used, and the rest were read by hand. The book does not mark the winner: the candidate with "
    "the most votes is shown elected."
)
WIKIDATA = {
    "LAB": "Q9630",
    "CON": "Q9626",
    "SNP": "Q10658",
    "SDP/LIB": "Q7389478",
    "LIB/SDP": "Q7389478",
    "SLD": "Q9624",
    "LIBDEM": "Q9624",
    "CPGB": "Q592068",
    "CPB": "Q1120576",
    "ECOL": "Q15980875",
    "ECOLOGY": "Q15980875",
    "WRP": "Q4051005",
    "RCP": "Q7318763",
    "NLP": "Q125552291",
}
TITLES = {"Ms", "Mrs", "Miss", "Dr", "Mr"}


def figure(text: str | None) -> int | None:
    if text is None:
        return None
    match = re.match(r"\s*([\d,]+)", text)
    return int(match[1].replace(",", "")) if match else None


def share(text: str | None) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(match[0]) if match else None


BOOK_1974 = {
    "argyll-and-bute": "Argyll",
    "strathkelvin": "Bishopbriggs and Kirkintilloch",
    "cumbernauld-and-kilsyth": "Cumbernauld",
    "clydesdale": "Lanark",
    "wigtown": "Merrick",
    "stewartry": "Stewarry",
    "ettrick-and-lauderdale": "Ettrick &",
}
RENAMED_1974 = ("argyll-and-bute", "strathkelvin", "clydesdale", "wigtown")
ORDER_1974 = [
    "caithness", "sutherland", "ross-and-cromarty", "skye-and-lochalsh", "lochaber", "inverness",
    "badenoch-and-strathspey", "nairn", "moray", "banff-and-buchan", "gordon", "city-of-aberdeen",
    "kincardine-and-deeside", "angus", "city-of-dundee", "perth-and-kinross", "kirkcaldy", "north-east-fife",
    "dunfermline", "west-lothian", "city-of-edinburgh", "midlothian", "east-lothian", "clackmannan", "stirling",
    "falkirk", "tweeddale", "ettrick-and-lauderdale", "roxburgh", "berwickshire", "argyll-and-bute", "dumbarton",
    "city-of-glasgow", "clydebank", "bearsden-and-milngavie", "strathkelvin", "cumbernauld-and-kilsyth",
    "monklands", "motherwell", "hamilton", "east-kilbride", "eastwood", "clydesdale", "renfrew", "inverclyde",
    "cunninghame", "kilmarnock-and-loudoun", "kyle-and-carrick", "cumnock-and-doon-valley", "wigtown",
    "stewartry", "nithsdale", "annandale-and-eskdale",
]  # fmt: skip


def councils(volume: str) -> list[tuple[str, str, str]]:
    regions = [(key, label, key) for key, label, _ in REGIONS]
    districts = {slug(label): (label, region) for region, labels in DISTRICTS.items() for label in labels}
    if VOLUMES[volume]["tier"] == "region":
        return regions
    if VOLUMES[volume]["tier"] == "both":
        return regions + [(key, districts[key][0], districts[key][1]) for key in ORDER_1974]
    return [(key, label, region) for key, (label, region) in districts.items()]


def number(text: str | None) -> tuple[int, str] | None:
    match = re.fullmatch(r"\s*(\d+)\s*\(?\s*([A-Z]?)\s*\)?\s*[*.]*\s*", text or "")
    return (int(match[1]), match[2]) if match else None


def candidate(line: dict) -> dict:
    marker = (line["marker"] or "").strip()
    title = line["title"]
    initials = (line["initials"] or "").strip()
    rest = re.sub(r"^[^A-Za-z]*", "", marker).strip()
    if rest in TITLES:
        title = title or rest
    elif rest and not initials.startswith(rest):
        initials = f"{rest} {initials}".strip()
    return {
        "title": title,
        "initials": " ".join(re.findall(r"[A-Za-z]+", initials)) or None,
        "surname": line["surname"].strip(),
        "party": line["party"].strip(),
        "votes": figure(line["votes"]),
        "percent": share(line["percent"]),
    }


def ordered(volume: str, sides: tuple[str, ...]) -> list[dict]:
    pages = VOLUMES[volume]["pages"]
    sha = raw.cached(SOURCE, url(volume)).sha256
    out: list[dict] = []
    for page in range(pages[0], pages[1] + 1):
        chains: list[list[dict]] = []
        for side in sides:
            for division in vision(sha, page, side)["divisions"]:
                parsed = number(division["number"])
                item = {**division, "page": page, "parsed": parsed, "side": side}
                if parsed is None:
                    (chains[-1] if chains else out).append(item)
                    continue
                fits = [
                    chain
                    for chain in chains
                    if chain[-1]["parsed"] is not None and chain[-1]["parsed"][0] in (parsed[0], parsed[0] - 1)
                ]
                if fits and not (parsed == (1, "") and fits[-1][-1]["parsed"] != parsed):
                    fits[-1].append(item)
                else:
                    chains.append([item])
        for chain in chains:
            out.extend(chain)
    return out


def folded(text: str) -> str:
    text = re.sub(r"[^A-Z ]", " ", text.upper().replace("&", " AND "))
    text = re.sub(r"\b(DISTRICT|REGIONAL|REGION|COUNCIL|ISLANDS AREA|ISLANDS|AREA|THE|CITY OF)\b", " ", text)
    return " ".join(text.split())


ALIASES = {
    "NAIRNSHIRE": "nairn",
    "LANARK": "clydesdale",
    "KILMARNOCK AND LOUDON": "kilmarnock-and-loudoun",
    "EILEAN SIAR": "western-isles",
    "COMHAIRLE NAN EILEAN": "western-isles",
}


def heading(volume: str, text: str | None, used: set[str]) -> str | None:
    text = folded(text or "")
    if not text:
        return None
    if text in ALIASES:
        return ALIASES[text]
    bodies = councils(volume)
    exact = next((key for key, label, _ in bodies if folded(label) == text), None)
    if exact is not None:
        return exact
    inside = [key for key, label, _ in bodies if re.search(rf"\b{folded(label)}\b", text)]
    if inside:
        return max(inside, key=len)
    if VOLUMES[volume]["tier"] == "district":
        region = next((key for key, label, _ in REGIONS if re.search(rf"\b{folded(label)}\b", text)), None)
        return next((key for key, _, home in bodies if home == region and key not in used), None)
    return None


def assigned(volume: str, sides: tuple[str, ...]) -> list[dict]:
    order = [key for key, _, _ in councils(volume)]
    current: str | None = None
    lost = False
    used: set[str] = set()
    merged: list[dict] = []
    for item in ordered(volume, sides):
        parsed = item["parsed"]
        if parsed is None or (merged and merged[-1]["parsed"] == parsed):
            if not merged or lost:
                continue
            previous = merged[-1]
            for field in ("electorate", "turnout", "rejected", "majority", "note"):
                previous[field] = previous[field] or item[field]
            previous["name"] = previous["name"] or item["name"]
            seen = {(c["surname"], c["votes"]) for c in previous["candidates"]}
            previous["candidates"] = previous["candidates"] + [
                c for c in item["candidates"] if (c["surname"], c["votes"]) not in seen
            ]
            continue
        guessed = False
        if parsed == (1, "") or current is None:
            named = heading(volume, item["council"], used)
            guessed = named is None
            after = order[order.index(current) + 1 :] if current in order else order
            current = named or next((key for key in [*after, *order] if key not in used), order[-1])
            used.add(current)
            lost = False
        elif merged and merged[-1]["council_key"] == current and parsed[0] < merged[-1]["parsed"][0]:
            lost = True
        if lost:
            continue
        merged.append({**item, "council_key": current, "guessed": guessed})
    return merged


def normal(volume: str, item: dict) -> tuple[dict, list[dict]]:
    parsed = item["parsed"]
    note = item["note"] or ""
    division = {
        "volume": volume,
        "tier": VOLUMES[volume]["tier"],
        "number": f"{parsed[0]}{parsed[1]}",
        "name": (item["name"] or "").strip() or None,
        "page": item["page"],
        "electorate": figure(item["electorate"]),
        "turnout": share(item["turnout"]),
        "rejected": 0
        if re.search(r"no rejected", (item["rejected"] or "") + note, re.IGNORECASE)
        else figure(item["rejected"]),
        "majority": figure(item["majority"]),
        "note": item["note"],
    }
    return division, [candidate(line) for line in item["candidates"]]


def issues(division: dict, lines: list[dict]) -> list[str]:
    votes = [line["votes"] for line in lines]
    if not lines:
        return (
            [] if re.search(r"no (candidate|nomination)", division["note"] or "", re.IGNORECASE) else ["no candidates"]
        )
    if all(v is None for v in votes):
        return [] if len(lines) == 1 else [f"{len(lines)} candidates without votes"]
    if any(v is None for v in votes):
        return ["votes missing for some candidates"]
    found = []
    total = sum(votes)
    ranked = sorted(votes, reverse=True)
    if len(ranked) == 1:
        found.append("one candidate with votes")
    elif division["majority"] is not None and ranked[0] - ranked[1] != division["majority"]:
        found.append(f"majority {division['majority']}, votes {ranked[:2]}")
    for line in lines:
        bases = (total, total + (division["rejected"] or 0))
        if line["percent"] is not None and all(abs(line["votes"] / b * 100 - line["percent"]) > 0.15 for b in bases):
            found.append(f"{line['surname']} {line['votes']} against {line['percent']}%")
    if division["electorate"] and division["turnout"] is not None:
        computed = (total + (division["rejected"] or 0)) / division["electorate"] * 100
        slack = 2.5 if division["rejected"] is None and division["volume"] == "b1974" else 0.15
        if not -0.15 <= division["turnout"] - computed <= slack:
            found.append(f"turnout {computed:.1f}%, printed {division['turnout']}")
    return found


def figures(division: dict, lines: list[dict]) -> tuple:
    return (
        division["electorate"],
        division["rejected"],
        tuple(sorted((leh.fold(line["surname"]), line["votes"] or 0) for line in lines)),
    )


def aligned(readings: list[tuple[dict, list[dict]]]) -> None:
    for division, lines in readings:
        for other, other_lines in readings:
            if division["rejected"] is None and other["rejected"] is not None:
                division["rejected"] = other["rejected"]
            for line in lines:
                for twin in other_lines:
                    short, long = leh.fold(twin["surname"]), leh.fold(line["surname"])
                    if line["votes"] == twin["votes"] and short and long.endswith(f" {short}"):
                        line["surname"] = twin["surname"]


def settle(readings: list[tuple[dict, list[dict]]]) -> tuple[dict, list[dict], list[str]]:
    aligned(readings)
    groups: dict[tuple, list[tuple[dict, list[dict]]]] = {}
    for reading in readings:
        groups.setdefault(figures(*reading), []).append(reading)
    ranked = sorted(groups.values(), key=lambda group: (-len(group), len(issues(*group[0]))))
    best = ranked[0][0]
    found = issues(*best)
    for division, _ in readings:
        best[0]["electorate"] = best[0]["electorate"] or division["electorate"]
        best[0]["name"] = best[0]["name"] or division["name"]
    if len(ranked) == 1 or len(ranked[0]) > len(ranked[1]):
        return best[0], best[1], found
    clean = [group[0] for group in ranked if not issues(*group[0])]
    if len(clean) == 1:
        return clean[0][0], clean[0][1], []
    return best[0], best[1], [*found, "readings differ: " + " and ".join(str(key) for key in groups)]


def checked(volume: str, item: dict) -> dict | None:
    key = check_key(
        raw.cached(SOURCE, url(volume)).sha256, item["page"], item["side"], item["council_key"], item["number"]
    )
    if key not in raw.keys(SOURCE):
        return None
    message = json.loads(raw.cached(SOURCE, key).path.read_text())
    read = json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))
    wanted = number(item["number"])
    return next(
        (
            {**division, "page": item["page"], "parsed": wanted}
            for division in read["divisions"]
            if number(division["number"]) == wanted
        ),
        None,
    )


def sequence_1974(volume: str) -> tuple[list[dict], list[str]]:
    sha = raw.cached(SOURCE, url(volume)).sha256
    first, last = VOLUMES[volume]["pages"]
    items: list[dict] = []
    gaps: list[str] = []
    previous = 0
    for page in range(first, last + 1):
        divisions = vision(sha, page)["divisions"]
        twins = vision(sha, page, prompt=NAMES_PROMPT)["divisions"]
        if len(twins) != len(divisions):
            gaps.append(f"{volume}\t{page}\t\tthe two readings find {len(divisions)} and {len(twins)} divisions")
            twins = [None] * len(divisions)
        for division, twin in zip(divisions, twins, strict=True):
            parsed = number(division["number"])
            expected = previous + 1
            other = number(twin["number"]) if twin else None
            if (parsed is None or parsed[0] > 120) and other is not None and other[0] == expected:
                parsed = other
            elif parsed is None or parsed[0] > 120:
                gaps.append(f"{volume}\t{page}\t{expected}\tnumber read as {division['number']}")
                parsed = (expected, "")
            elif parsed[0] not in (1, expected) or parsed[1]:
                gaps.append(f"{volume}\t{page}\t{division['number']}\tfollows {previous}")
            previous = parsed[0]
            items.append({**division, "page": page, "parsed": parsed})
            if twin is not None and other == parsed:
                items[-1]["twin"] = {**twin, "page": page, "parsed": parsed}
    return items, gaps


def fixed(division: dict, lines: list[dict], fix: dict) -> list[dict]:
    field, value = fix["field"], fix["value"]
    if fix["surname"]:
        line = next((line for line in lines if line["surname"] in fix["surname"].split("|")), None)
        if line is None:
            raise ValueError(f"correction matches no candidate: {fix}")
        line[field] = int(value) if field == "votes" else float(value) if field == "percent" else value
    elif field == "drop":
        lines = [line for line in lines if line["surname"] != value]
    elif field == "note":
        division[field] = value
    elif field != "checked":
        division[field] = float(value) if field == "turnout" else int(value) if value else None
    return lines


def parse_1974(volume: str, fixes: dict[tuple[str, int, str], list[dict]]) -> tuple[list[dict], list[dict], list[str]]:
    bodies = councils(volume)
    items, gaps = sequence_1974(volume)
    chains: list[list[dict]] = []
    for item in items:
        if item["parsed"][0] == 1:
            chains.append([])
        chains[-1].append(item)
    if len(chains) != len(bodies):
        raise ValueError(f"{volume}: the reading finds {len(chains)} councils, not {len(bodies)}")
    divisions: list[dict] = []
    candidates: list[dict] = []
    problems = list(gaps)
    for index, (chain, (key, label, region)) in enumerate(zip(chains, bodies, strict=True)):
        printed = BOOK_1974.get(key, label)
        heading = chain[0]["council"]
        if heading is not None and printed.lower().split()[0] not in heading.lower():
            problems.append(f"{volume}\t{chain[0]['page']}\t1\t{label}\theading read as {heading}")
        first = len(divisions)
        for item in chain:
            readings = [normal(volume, one) for one in (item, item.get("twin")) if one]
            division, lines, found = settle(readings)
            for fix in fixes.pop((volume, item["page"], f"{key}:{division['number']}"), []):
                lines = fixed(division, lines, fix)
                found = [] if fix["field"] == "checked" else issues(division, lines)
            division |= {"council": key, "region": region, "tier": "region" if index < len(REGIONS) else "district"}
            problems += [f"{volume}\t{item['page']}\t{division['number']}\t{label}\t{issue}" for issue in found]
            divisions.append(division)
            candidates += [
                {"volume": volume, "council": key, "number": division["number"], "order": order, **line}
                for order, line in enumerate(lines)
            ]
        if sum(1 for division in divisions[first:] if division["name"]) * 3 < len(chain):
            for division in divisions[first:]:
                division["name"] = None
    return divisions, candidates, problems


def parse_book(volume: str, fixes: dict[tuple[str, int, str], list[dict]]) -> None:
    try:
        divisions, candidates, problems = parse_1974(volume, fixes)
    except ValueError as error:
        divisions, candidates, problems = [], [], [str(error)]
    folder = PARSED / volume
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.parquet"):
        old.unlink()
    frame = pl.DataFrame(divisions, infer_schema_length=None)
    if divisions:
        duplicates = frame.group_by("volume", "council", "number").len().filter(pl.col("len") > 1)
        for row in duplicates.iter_rows(named=True):
            problems.append(f"{row['volume']}\t\t{row['number']}\t{row['council']}\trepeated")
        frame.write_parquet(folder / "divisions.parquet")
        pl.DataFrame(candidates, infer_schema_length=None).write_parquet(folder / "candidates.parquet")
    (folder / "problems.tsv").write_text("".join(f"{problem}\n" for problem in problems))
    if fixes:
        raise ValueError(f"corrections that match nothing: {sorted(fixes)}")
    print(f"{volume}: {len(divisions)} divisions, {len(candidates)} candidates, {len(problems)} problems")


def parse() -> None:
    corrections = pl.read_csv(CORRECTIONS, separator="\t", infer_schema_length=0).iter_rows(named=True)
    fixes: dict[tuple[str, int, str], list[dict]] = {}
    for fix in corrections:
        fixes.setdefault((fix["volume"], int(fix["page"]), fix["number"]), []).append(fix)
    book = {key: fix for key, fix in fixes.items() if key[0] == "b1974"}
    for key in book:
        del fixes[key]
    parse_book("b1974", book)
    divisions: list[dict] = []
    candidates: list[dict] = []
    problems: list[str] = []
    doubts: list[dict] = []
    for volume in ROUNDS:
        bodies = {key: (label, region) for key, label, region in councils(volume)}
        columns = assigned(volume, ("L", "R"))
        pages = assigned(volume, ("",))
        unmatched = {id(item) for item in pages}
        settled: list[tuple[dict, list[dict]]] = []
        for item in columns:
            key = item["council_key"]
            surnames = {leh.fold(c["surname"]) for c in item["candidates"]}
            twin = max(
                (
                    other
                    for other in pages
                    if id(other) in unmatched
                    and other["council_key"] == key
                    and other["parsed"] == item["parsed"]
                    and abs(other["page"] - item["page"]) <= 1
                ),
                key=lambda other: len(surnames & {leh.fold(c["surname"]) for c in other["candidates"]}),
                default=None,
            )
            items = [item]
            if twin is not None:
                unmatched.discard(id(twin))
                items.append(twin)
            settled.append((item, items))
        settled += [(item, [item]) for item in pages if id(item) in unmatched and item["candidates"]]
        for item, items in settled:
            key = item["council_key"]
            label, region = bodies[key]
            third = checked(volume, item)
            readings = [normal(volume, one) for one in [*items, *([third] if third else [])]]
            division, lines, mine = settle(readings)
            if item["guessed"] and all(one["guessed"] for one in items):
                mine.append("council heading not read")
            if (
                len(readings) == 1
                and len(lines) > 1
                and not mine
                and division["majority"] is None
                and all(line["percent"] is None for line in lines)
            ):
                mine = ["read once, with no majority or shares to check it"]
            for fix in fixes.pop((volume, item["page"], division["number"]), []):
                if fix["surname"]:
                    line = next(line for line in lines if line["surname"] == fix["surname"])
                    line[fix["field"]] = int(fix["value"]) if fix["field"] == "votes" else fix["value"]
                elif fix["field"] == "drop":
                    lines = [line for line in lines if line["surname"] != fix["value"]]
                elif fix["field"] != "checked":
                    division[fix["field"]] = int(fix["value"]) if fix["value"] else None
                mine = [] if fix["field"] == "checked" else issues(division, lines)
            if mine:
                problems += [f"{volume}\t{item['page']}\t{division['number']}\t{label}\t{issue}" for issue in mine]
                if third is None:
                    doubts.append(
                        {
                            "volume": volume,
                            "page": item["page"],
                            "side": item["side"],
                            "council": key,
                            "number": item["number"],
                        }
                    )
                continue
            division |= {"council": key, "region": region}
            divisions.append(division)
            for order, line in enumerate(lines):
                candidates.append(
                    {"volume": volume, "council": key, "number": division["number"], "order": order, **line}
                )
        for key in bodies.keys() - {division["council"] for division in divisions if division["volume"] == volume}:
            problems.append(f"{volume}\t\t\t{bodies[key][0]}\tno division read")
    if fixes:
        raise ValueError(f"corrections that match nothing: {sorted(fixes)}")
    frame = pl.DataFrame(divisions, infer_schema_length=None)
    duplicates = frame.group_by("volume", "council", "number").len().filter(pl.col("len") > 1)
    for row in duplicates.iter_rows(named=True):
        problems.append(f"{row['volume']}\t\t{row['number']}\t{row['council']}\trepeated")
    keep = frame.join(duplicates.select("volume", "council", "number"), on=["volume", "council", "number"], how="anti")
    lines = pl.DataFrame(candidates, infer_schema_length=None).join(
        keep.select("volume", "council", "number"), on=["volume", "council", "number"], how="semi"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    keep.write_parquet(PARSED / "divisions.parquet")
    lines.write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(doubts, schema=["volume", "page", "side", "council", "number"]).write_csv(
        PARSED / "doubts.tsv", separator="\t"
    )
    (PARSED / "problems.tsv").write_text("".join(f"{problem}\n" for problem in problems))
    print(
        f"{keep.height} divisions kept, {lines.height} candidates, {len(problems)} problems, {len(doubts)} to read again"
    )


TITLES_BY_TIER = {"region": "Regional", "district": "District", "both": "Regional and District"}


def party_of(label: str, year: int) -> tuple[str, str] | None:
    label = re.sub(r"\s+", " ", label.upper().strip())
    if label in WIKIDATA:
        return ("wikidata", WIKIDATA[label])
    if label in ("LIB", "LIB ALL"):
        return ("wikidata", "Q622441") if year <= 1988 else ("gb.party", "liberal-party-1989")
    if label in ("SDP", "SDP ALL") and year <= 1987:
        return ("wikidata", "Q40052")
    if label in ("GREEN", "GREE"):
        return ("wikidata", "Q15980875" if year < 1990 else "Q1256956")
    if label == "COMM" and year < 1992:
        return ("wikidata", "Q592068")
    if label in ("SML", "SCOTMIL") and year >= 1992:
        return ("wikidata", "Q7437858")
    return None


def emit() -> Path:
    clean = [
        folder
        for folder in (PARSED, PARSED / "b1974")
        if (folder / "problems.tsv").exists() and not (folder / "problems.tsv").read_text()
    ]
    divisions = pl.concat([pl.read_parquet(folder / "divisions.parquet") for folder in clean], how="diagonal_relaxed")
    lines = pl.concat([pl.read_parquet(folder / "candidates.parquet") for folder in clean], how="diagonal_relaxed")
    volumes = {volume: VOLUMES[volume] for volume in divisions["volume"].unique(maintain_order=True)}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish regional, islands, and district council elections 1974 to 1994 by electoral division "
            "and ward, from Bochel and Denver's results volumes, with the nine regional, three islands, and 53 "
            "district councils as bodies",
            polity="GB-SCT",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        scotland = cs.polity("GB-SCT")
        system = cs.key("gb.electoral_system", "fptp")
        regions = {key: cs.area("gb.counting_area", f"sct-region-1974-{key}") for key, _, _ in REGIONS}
        if cs.minted & {system, *regions.values()}:
            raise ValueError("publish gb.parliament and gb.counting_areas first")
        if conn.execute("select 1 from scheme where code = 'gb.scot_division'").fetchone() is None:
            cs.add(
                "scheme",
                code="gb.scot_division",
                polity_id=cs.polity("GB"),
                kind="electoral_district",
                rank=3,
                noun="electoral division",
            )
        parties: dict[tuple[str, str], UUID] = {}
        for volume, label in lines.select("volume", "party").unique().iter_rows():
            link = party_of(label, VOLUMES[volume]["day"].year)
            if link is not None:
                parties[link] = cs.key(*link)
        if cs.minted & set(parties.values()):
            raise ValueError("a party this pipeline links is not in the archive")
        paths = {path for (path,) in conn.execute("select path from slug where path like 'gb/%%'").fetchall()}

        sources: dict[str, UUID] = {}
        for volume, spec in volumes.items():
            title = f"The Scottish {TITLES_BY_TIER[spec['tier']]} Elections {spec['day'].year}: Results and Statistics"
            sources[volume] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[volume],
                kind="compendium",
                publisher=PUBLISHER,
                title=title,
                url=url(volume),
                notes=SOURCE_NOTE,
            )
            cs.artifact(raw.cached(SOURCE, url(volume)), sources[volume])

        bodies: dict[str, tuple[UUID, UUID, str, str]] = {}
        for key, label, code in REGIONS:
            if code is not None:
                polity, body = cs.key("gb.polity", code), cs.key("gb.body", code)
                if cs.minted & {polity, body}:
                    raise ValueError(f"{code}: islands council not in the archive")
                path, label_text = conn.execute(
                    "select (select path from slug where entity_id = %s order by is_current desc limit 1),"
                    " (select text from name where entity_id = %s and is_preferred limit 1)",
                    [polity, body],
                ).fetchall()[0]
                bodies[key] = (polity, body, path, label_text)
                continue
            bodies[key] = council(
                cs,
                name,
                paths,
                scotland,
                regions[key],
                f"region:{key}",
                f"{label} Regional Council",
                f"gb/{key}-region",
            )
        for region, labels in DISTRICTS.items():
            for label in labels:
                key = slug(label)
                bodies[key] = council(
                    cs,
                    name,
                    paths,
                    scotland,
                    None,
                    f"district:{key}",
                    f"{label} District Council",
                    f"gb/{key}-district",
                )

        for volume, spec in volumes.items():
            day = spec["day"]
            year = day.year
            national = cs.key("gb.election", f"{day}:local:sct")
            cs.add("election", id=national, polity_id=scotland, kind="local", date_from=day, date_to=day, status="held")
            name(
                national,
                {
                    "region": f"{year} Scottish regional and islands council elections",
                    "district": f"{year} Scottish district council elections",
                    "both": f"{year} Scottish regional, islands, and district council elections",
                }[spec["tier"]],
            )
            cs.add("slug", path=f"gb/scotland/{day}-local", entity_id=national)
            held = divisions.filter(pl.col("volume") == volume)
            for (key,), rows in held.group_by("council", maintain_order=True):
                polity, body, path, council_name = bodies[key]
                region = rows["region"][0]
                tier = rows["tier"][0]
                scheme = "gb.scot_division" if tier == "region" else "gb.ward"
                noun = "division" if tier == "region" else "ward"
                election = cs.key("gb.election", f"{day}:local:{tier}:{key}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=national,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} {council_name} election")
                cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                cs.add("note", entity_id=election, kind="methodology", body_md=METHOD)
                if volume == "b1974" and key in RENAMED_1974:
                    cs.add(
                        "note",
                        entity_id=election,
                        kind="caveat",
                        body_md=f"The volume heads this council “{BOOK_1974[key]} District”; it took its present "
                        "name after the election.",
                    )
                empty = []
                for row in rows.iter_rows(named=True):
                    standing = lines.filter(
                        (pl.col("volume") == volume) & (pl.col("council") == key) & (pl.col("number") == row["number"])
                    ).sort("order")
                    shown = f"{noun.title()} {row['number']}" + (f" ({display(row['name'])})" if row["name"] else "")
                    if standing.height == 0:
                        printed = row["note"] or ""
                        empty.append(
                            shown if "no candidate" in printed.lower() or not printed else f"{shown} ({printed})"
                        )
                        continue
                    area_key = f"{tier}:{key}:{year}:{row['number']}"
                    area = cs.area(scheme, area_key)
                    if area in cs.minted:
                        cs.add("area", id=area, scheme_code=scheme)
                        cs.add("area_code", area_id=area, code=area_key, valid_from=None, valid_to=None)
                        cs.add("area_parent", area_id=area, parent_id=regions[region], valid_from=None, valid_to=None)
                    name(area, shown)
                    unit = cs.unit(scheme, area_key)
                    if unit in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code=scheme,
                            code=area_key,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                            valid_from=None,
                        )
                    name(unit, shown)
                    tacit = standing["votes"].is_null().all()
                    contest_key = f"{day}:local:{area_key}"
                    contest = cs.key("gb.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="body_seats",
                        body_id=body,
                        district_area_id=area,
                        magnitude=1,
                        round_number=1,
                        electoral_system_id=system,
                        default_result_kind="tacit" if tacit else "counted",
                    )
                    name(contest, f"{shown}, {council_name}, {year}")
                    cs.add("slug", path=f"{path}/{day}-local/{noun}-{slug(row['number'])}", entity_id=contest)
                    result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="tacit" if tacit else "counted",
                        status="active",
                        source_id=sources[volume],
                        is_default=True,
                        completeness=1,
                    )
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=sources[volume],
                        locator={"page": row["page"], noun: row["number"]},
                    )
                    votes = standing["votes"].to_list()
                    top = None if tacit else max(votes)
                    tied = not tacit and votes.count(top) > 1
                    if tied:
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=f"Two candidates have {top:,} votes; the book does not say who was elected.",
                        )
                    for line in standing.iter_rows(named=True):
                        option_key = f"{contest_key}:{line['order']}"
                        label = " ".join(part for part in (line["initials"], display(line["surname"])) if part)
                        printed = re.sub(r"\s+", " ", line["party"].strip())
                        link = party_of(printed, year)
                        owner = parties[link] if link else None
                        independent = printed.upper() == "IND"
                        if owner is None and not independent:
                            cs.add(
                                "note",
                                entity_id=result_set,
                                kind="caveat",
                                body_md=f"{label} is listed as “{printed}”; no party is linked.",
                            )
                        option = cs.key(f"{SOURCE}.option", option_key)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=owner,
                            ballot_label=label,
                        )
                        person = cs.key(f"{SOURCE}.person", option_key)
                        cs.add("person", id=person)
                        name(person, label)
                        cs.add(
                            "candidacy",
                            id=cs.key(f"{SOURCE}.candidacy", option_key),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=label,
                            member_of_party_id=owner,
                            is_independent=independent,
                        )
                        if not tacit:
                            cs.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=option,
                                channel=0,
                                measure=VOTES,
                                count_round=0,
                                flags=0,
                                value=line["votes"],
                            )
                        if tacit or (line["votes"] == top and not tied):
                            cs.add(
                                "outcome",
                                result_set_id=result_set,
                                option_id=option,
                                kind="elected",
                                notes=None if tacit else "Derived from the votes.",
                            )
                    for measure, field in ((REGISTERED, "electorate"), (INVALID, "rejected")):
                        if row[field] is None or (tacit and measure == INVALID):
                            continue
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=row[field],
                        )
                if empty:
                    cs.add(
                        "note",
                        entity_id=election,
                        kind="caveat",
                        body_md=f"The book prints no candidate for {', '.join(empty)}; those seats are left out.",
                    )
        return cs.write()


def council(
    cs: Changeset,
    name: Callable[[UUID, str], None],
    paths: set[str],
    scotland: UUID,
    area: UUID | None,
    key: str,
    label: str,
    path: str,
) -> tuple[UUID, UUID, str, str]:
    polity = cs.key("gb.polity", f"sct-{key}")
    body = cs.key("gb.body", f"sct-{key}")
    if polity in cs.minted:
        cs.add("polity", id=polity, kind="county" if key.startswith("region") else "municipality", iso_code=None)
        cs.add("polity_parent", polity_id=polity, parent_id=scotland, valid_from=FORMED, valid_to=ABOLISHED)
        if area is not None:
            cs.add("polity_territory", polity_id=polity, area_id=area)
        if path in paths:
            raise ValueError(f"{path} is taken")
    cs.add("slug", path=path, entity_id=polity)
    name(polity, label)
    if body in cs.minted:
        cs.add("body", id=body, polity_id=polity, kind="council")
    name(body, label)
    return polity, body, path, label
