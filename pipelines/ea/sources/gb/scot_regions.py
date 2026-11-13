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


def read_key(sha256: str, page: int, side: str = "") -> str:
    return f"anthropic-batch:{MODEL}/{COLUMN_PROMPT if side else PROMPT}/{sha256}/{page}{side}"


def vision(sha256: str, page: int, side: str = "") -> dict:
    message = json.loads(raw.cached(SOURCE, read_key(sha256, page, side)).path.read_text())
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


def fetch() -> None:
    reads: dict[str, batch.Want] = {}
    columns: dict[str, batch.Want] = {}
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
            middle = gutter(image)
            for side, box in (("L", (0, 0, middle, image.height)), ("R", (middle, 0, image.width, image.height))):
                columns[read_key(artifact.sha256, page, side)] = request(
                    image.crop(box), COLUMN_SYSTEM, "Read the councils and divisions in this column."
                )
            reads[read_key(artifact.sha256, page)] = request(
                image, READ_SYSTEM, "Read the councils and divisions on this page."
            )
    print(batch.ensure(SOURCE, "volumes", reads)[1])
    print(batch.ensure(SOURCE, "columns", columns)[1])


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


def councils(volume: str) -> list[tuple[str, str, str]]:
    if VOLUMES[volume]["tier"] == "region":
        return [(key, label, key) for key, label, _ in REGIONS]
    return [(slug(label), label, region) for region, labels in DISTRICTS.items() for label in labels]


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
                item = {**division, "page": page, "parsed": parsed}
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


def assigned(volume: str, sides: tuple[str, ...]) -> tuple[list[dict], int]:
    index = -1
    merged: list[dict] = []
    for item in ordered(volume, sides):
        parsed = item["parsed"]
        if parsed is None or (merged and merged[-1]["parsed"] == parsed):
            if not merged:
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
        if parsed == (1, ""):
            index += 1
        merged.append({**item, "index": index})
    return merged, index + 1


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
        if abs(computed - division["turnout"]) > 0.15:
            found.append(f"turnout {computed:.1f}%, printed {division['turnout']}")
    return found


def figures(division: dict, lines: list[dict]) -> tuple:
    return (
        division["electorate"],
        division["rejected"],
        tuple(sorted((leh.fold(line["surname"]), line["votes"]) for line in lines)),
    )


def parse() -> None:
    corrections = pl.read_csv(CORRECTIONS, separator="\t", infer_schema_length=0).iter_rows(named=True)
    fixes: dict[tuple[str, int, str], list[dict]] = {}
    for fix in corrections:
        fixes.setdefault((fix["volume"], int(fix["page"]), fix["number"]), []).append(fix)
    divisions: list[dict] = []
    candidates: list[dict] = []
    problems: list[str] = []
    for volume in VOLUMES:
        bodies = councils(volume)
        columns, found = assigned(volume, ("L", "R"))
        if found != len(bodies):
            raise ValueError(f"{volume}: the column reading finds {found} councils, not {len(bodies)}")
        pages, _ = assigned(volume, ("",))
        unmatched = {id(item) for item in pages}
        for item in columns:
            key, label, region = bodies[item["index"]]
            division, lines = normal(volume, item)
            where = f"{volume}\t{item['page']}\t{division['number']}\t{label}"
            surnames = {leh.fold(c["surname"]) for c in item["candidates"]}
            twin = max(
                (
                    other
                    for other in pages
                    if id(other) in unmatched
                    and other["parsed"] == item["parsed"]
                    and abs(other["page"] - item["page"]) <= 1
                ),
                key=lambda other: len(surnames & {leh.fold(c["surname"]) for c in other["candidates"]}),
                default=None,
            )
            mine = issues(division, lines)
            if twin is not None:
                unmatched.discard(id(twin))
                other, other_lines = normal(volume, twin)
                theirs = issues(other, other_lines)
                if figures(division, lines) != figures(other, other_lines):
                    if len(theirs) < len(mine):
                        division, lines, mine = other, other_lines, theirs
                    elif len(theirs) == len(mine):
                        mine = [*mine, f"readings differ: {figures(division, lines)} and {figures(other, other_lines)}"]
                division["electorate"] = division["electorate"] or other["electorate"]
                division["name"] = division["name"] or other["name"]
            for fix in fixes.pop((volume, item["page"], division["number"]), []):
                if fix["surname"]:
                    line = next(line for line in lines if line["surname"] == fix["surname"])
                    line[fix["field"]] = int(fix["value"]) if fix["field"] == "votes" else fix["value"]
                elif fix["field"] == "drop":
                    lines = [line for line in lines if line["surname"] != fix["value"]]
                else:
                    division[fix["field"]] = int(fix["value"]) if fix["value"] else None
                mine = [] if fix["field"] == "checked" else issues(division, lines)
            problems += [f"{where}\t{issue}" for issue in mine]
            division |= {"council": key, "region": region}
            divisions.append(division)
            for order, line in enumerate(lines):
                candidates.append(
                    {"volume": volume, "council": key, "number": division["number"], "order": order, **line}
                )
        for item in pages:
            if id(item) in unmatched and item["candidates"]:
                problems.append(
                    f"{volume}\t{item['page']}\t{item['number']}\t?\tonly in the page reading: "
                    + ", ".join(c["surname"] for c in item["candidates"])
                )
    if fixes:
        raise ValueError(f"corrections that match nothing: {sorted(fixes)}")
    frame = pl.DataFrame(divisions, infer_schema_length=None)
    duplicates = frame.group_by("volume", "council", "number").len().filter(pl.col("len") > 1)
    for row in duplicates.iter_rows(named=True):
        problems.append(f"{row['volume']}\t\t{row['number']}\t{row['council']}\trepeated")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "divisions.parquet")
    pl.DataFrame(candidates, infer_schema_length=None).write_parquet(PARSED / "candidates.parquet")
    (PARSED / "problems.tsv").write_text("".join(f"{problem}\n" for problem in problems))
    print(f"{len(divisions)} divisions, {len(candidates)} candidates, {len(problems)} problems")
    if problems:
        raise SystemExit(f"resolve the problems in {PARSED / 'problems.tsv'} with {CORRECTIONS.name}")


def kind(volume: str) -> str:
    return "region" if VOLUMES[volume]["tier"] == "region" else "district"


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
    divisions = pl.read_parquet(PARSED / "divisions.parquet")
    lines = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish regional and islands council elections 1978 to 1994 and district council elections "
            "1977 to 1992 by electoral division and ward, from Bochel and Denver's results volumes, with the nine "
            "regional and 53 district councils as bodies",
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
        for volume, spec in VOLUMES.items():
            title = f"The Scottish {kind(volume).title()} Elections {spec['day'].year}: Results and Statistics"
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
                path, council = conn.execute(
                    "select (select path from slug where entity_id = %s order by is_current desc limit 1),"
                    " (select text from name where entity_id = %s and is_preferred limit 1)",
                    [polity, body],
                ).fetchall()[0]
                bodies[key] = (polity, body, path, council)
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

        for volume, spec in VOLUMES.items():
            day = spec["day"]
            year = day.year
            tier = kind(volume)
            national = cs.key("gb.election", f"{day}:local:sct")
            cs.add("election", id=national, polity_id=scotland, kind="local", date_from=day, date_to=day, status="held")
            name(
                national,
                f"{year} Scottish regional and islands council elections"
                if tier == "region"
                else f"{year} Scottish district council elections",
            )
            cs.add("slug", path=f"gb/scotland/{day}-local", entity_id=national)
            held = divisions.filter(pl.col("volume") == volume)
            for (key,), rows in held.group_by("council", maintain_order=True):
                polity, body, path, council_name = bodies[key]
                region = rows["region"][0]
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
                empty = []
                for row in rows.iter_rows(named=True):
                    standing = lines.filter(
                        (pl.col("volume") == volume) & (pl.col("council") == key) & (pl.col("number") == row["number"])
                    ).sort("order")
                    shown = f"{noun.title()} {row['number']}" + (f" ({display(row['name'])})" if row["name"] else "")
                    if standing.height == 0:
                        empty.append(shown)
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
