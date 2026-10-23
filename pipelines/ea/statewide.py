import base64
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

from ea import scans

PROMPT = "1"
SYSTEM = (
    "You transcribe statewide election returns printed in a scanned volume into JSON. Copy every name, party, and "
    "vote count exactly as printed, with the digits and separators the page uses. Never compute, correct, or fill "
    "in a number, and never take a value from a total. Scattering, blank, and void lines are candidates named as "
    "printed."
)
BLANK = re.compile(r"blank|void|defective|scatter|all others|^others?$", re.IGNORECASE)
RESIDENCE = re.compile(r"\s+of\s+[A-Z][\w .']*$")
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer"},
                    "office": {"type": "string"},
                    "title": {"type": "string"},
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "party": {"type": "string"},
                                "votes": {"type": "string"},
                            },
                            "required": ["name", "party", "votes"],
                            "additionalProperties": False,
                        },
                    },
                    "printed_total": {"type": "string"},
                },
                "required": ["year", "office", "title", "candidates", "printed_total"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tables"],
    "additionalProperties": False,
}


class Anchor(TypedDict):
    contest: str
    candidate: str
    official: int


class Volume(TypedDict):
    kind: str
    polity: str
    publisher: str
    title: str
    license: str
    file: dict[str, str]
    raw_source: str
    offices: dict[str, str]
    leaves: list[int]
    year: NotRequired[int]
    day: NotRequired[str]
    days: NotRequired[dict[str, str]]
    layout: NotRequired[str]
    dpi: NotRequired[int]
    anchors: NotRequired[list[Anchor]]
    create: NotRequired[bool]
    autorotate: NotRequired[bool]
    hint: NotRequired[str]


def offices(volume: Volume) -> dict[str, str]:
    return {
        **{
            anchor["contest"].rpartition(":")[2]: anchor["contest"].rpartition(":")[2].title()
            for anchor in volume.get("anchors", [])
        },
        **volume["offices"],
    }


def number(text: str) -> int | None:
    digits = re.sub(r"[,\s.]|(?<=\d)[-~'](?=\d)", "", text)
    return int(digits) if digits.isdigit() else None


def requests(name: str, volume: Volume, reader: str, leaves: list[int]) -> dict[str, scans.Read]:
    model, effort, _, _ = scans.READERS[reader]
    manifest = cast(
        scans.Manifest,
        {
            "file": volume["file"],
            "raw_source": volume["raw_source"],
            "polity": volume["polity"],
            "autorotate": volume.get("autorotate", False),
        },
    )
    wanted = "; ".join(f"{key}: {text}" for key, text in offices(volume).items())
    text = (
        f"This page comes from {volume['title']}. List every table of statewide returns for these offices (office "
        f"key: description): {wanted}. A table for any other office is office 'other'. For each table: year is the "
        "election year the table's returns are for (the year of the whole page when it prints one election); title "
        "is its heading as printed; candidates lists every row of the table in order with the name as printed (without a town of residence), the "
        "party as printed ('' if none), and the votes as printed; printed_total is the table's printed total or "
        "'Total vote' line exactly as printed ('' when it prints none). A candidate printed in several rows (one per "
        "party line) is one row per printed line. A volume that prints years down the page gives one table per "
        "year and office."
        + (f" {volume['hint']}" if "hint" in volume else "")
    )
    out: dict[str, scans.Read] = {}
    for leaf in leaves:
        image = scans.render(manifest, {"page": leaf}, volume.get("dpi", 200))
        media, data = scans.encode(image)
        digest = hashlib.sha256(data + text.encode()).hexdigest()
        blocks: list[ImageBlockParam | TextBlockParam] = [
            ImageBlockParam(
                type="image",
                source={
                    "type": "base64",
                    "media_type": "image/jpeg" if media == "image/jpeg" else "image/png",
                    "data": base64.b64encode(data).decode(),
                },
            ),
            TextBlockParam(type="text", text=text),
        ]
        custom = f"{reader[0]}{leaf}"
        out[custom] = scans.Read(
            custom=custom,
            section=str(leaf),
            span=(0.0, 1.0),
            digest=digest,
            pixels=image.width * image.height,
            key=f"anthropic-batch:{model}/sw{PROMPT}/{effort}/{digest}",
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=32000,
                system=SYSTEM,
                messages=[{"role": "user", "content": blocks}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            ),
        )
    return out


def tables(volume: Volume, reads: dict[str, scans.Read]) -> dict[tuple[int, int, str], dict[str, object]]:
    found: dict[tuple[int, int, str], dict[str, object]] = {}
    for request in reads.values():
        loaded = scans.parse(scans.cached(request.key))
        if isinstance(loaded, str):
            continue
        for table in cast(list[dict[str, object]], cast(dict[str, object], loaded)["tables"]):
            office = str(table["office"])
            if office not in offices(volume):
                continue
            year = int(cast(int, table["year"])) or volume.get("year", 0)
            merged: dict[str, dict[str, object]] = {}
            bad = False
            for row in cast(list[dict[str, str]], table["candidates"]):
                votes = number(row["votes"])
                if votes is None:
                    bad = True
                    continue
                name = RESIDENCE.sub("", row["name"].strip())
                words = re.sub(r"[^A-Za-z ]", " ", name).split()
                who = f"{words[-1].lower()} {words[0][0].lower()}" if len(words) > 1 else name.lower()
                entry = merged.setdefault(who, {"name": name, "party": row["party"], "votes": 0})
                if len(name) > len(str(entry["name"])):
                    entry["name"] = name
                entry["votes"] = cast(int, entry["votes"]) + votes
            found.setdefault(
                (int(request.section), year, office),
                {
                    "candidates": list(merged.values()),
                    "printed": number(str(table["printed_total"])),
                    "bad": bad,
                },
            )
    return found


def surname(text: str) -> str:
    words = re.sub(r"[^a-z ]", " ", text.lower()).split()
    return words[-1] if words else ""


def verdict(volume: Volume, table: dict[str, object], year: int, anchored: dict[int, bool]) -> str:
    candidates = cast(list[dict[str, object]], table["candidates"])
    total = sum(cast(int, row["votes"]) for row in candidates)
    named = sum(cast(int, row["votes"]) for row in candidates if not BLANK.search(str(row["name"])))
    printed = cast(int | None, table["printed"])
    if table["bad"] or not candidates:
        return "unreadable"
    if printed is not None:
        return "tied" if printed in (total, named) else "failed"
    return "unchecked" if anchored.get(year, False) else "failed"


def anchors(volume: Volume, found: dict[tuple[int, int, str], dict[str, object]]) -> dict[int, bool]:
    result: dict[int, bool] = {}
    for anchor in volume.get("anchors", []):
        day, _, office = anchor["contest"].rpartition(":")
        year = int(day[:4])
        for (_, seen, kind), table in found.items():
            if seen != year or kind != office:
                continue
            for row in cast(list[dict[str, object]], table["candidates"]):
                if surname(str(row["name"])) == surname(anchor["candidate"]):
                    result[year] = result.get(year, True) and row["votes"] == anchor["official"]
    return result


def step(name: str, mode: str = "auto") -> str:
    volume: Volume = json.loads((scans.MANIFESTS / "volumes" / f"{name}.json").read_text())
    folder = scans.WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    state: dict[str, dict[str, list[scans.Batch]]] = json.loads(state_path.read_text()) if state_path.exists() else {}
    leaves = volume["leaves"]
    reads: dict[str, dict[str, scans.Read]] = {}
    outcome: dict[str, dict[tuple[int, int, str], dict[str, object]]] = {}
    message = ""
    for reader in ("second", "third")[: int(os.environ.get("EA_SCANS_READERS", "3")) - 1]:
        plan = requests(name, volume, reader, leaves)
        message = scans.settle(reader, state, mode) or message
        wanted = {custom: request for custom, request in plan.items() if scans.cached(request.key) is None}
        message = scans.dispatch(reader, wanted, state, mode) or message
        state_path.write_text(json.dumps(state, indent=2))
        if any(scans.cached(request.key) is None for request in plan.values()):
            return message or f"{reader}: waiting for the batch"
        reads[reader] = plan
        outcome[reader] = tables(volume, plan)
        anchored = anchors(volume, outcome[reader])
        leaves = sorted(
            {
                leaf
                for (leaf, year, _), table in outcome[reader].items()
                if verdict(volume, table, year, anchored) != "tied"
            }
            | {
                int(request.section)
                for request in plan.values()
                if not any(k[0] == int(request.section) for k in outcome[reader])
            }
        )
        if not leaves:
            break
    return finalize(name, volume, folder, outcome, message)


def finalize(
    name: str,
    volume: Volume,
    folder: Path,
    outcome: dict[str, dict[tuple[int, int, str], dict[str, object]]],
    message: str,
) -> str:
    overrides = json.loads((folder / "overrides.json").read_text()) if (folder / "overrides.json").exists() else {}
    accepted: list[str] = overrides.get("accept", [])
    keys = sorted({key for tables_ in outcome.values() for key in tables_})
    passed: list[dict[str, object]] = []
    review: list[str] = []
    state = volume["polity"].removeprefix("US-")
    for key in keys:
        leaf, year, office = key
        if office not in volume["offices"]:
            continue
        label = f"{leaf}:{year}:{office}"
        first = outcome["second"].get(key)
        second = outcome.get("third", {}).get(key)
        chosen: dict[str, object] | None = None
        how = ""
        anchored_first = anchors(volume, outcome["second"])
        anchored_second = anchors(volume, outcome.get("third", {}))
        if first is not None and verdict(volume, first, year, anchored_first) == "tied":
            chosen, how = first, "first"
        elif second is not None and verdict(volume, second, year, anchored_second) == "tied":
            chosen, how = second, "second"
        elif (
            first is not None
            and second is not None
            and first["printed"] is None
            and anchored_first.get(year, False)
            and anchored_second.get(year, False)
            and not first["bad"]
            and not second["bad"]
            and {r["name"]: r["votes"] for r in cast(list[dict[str, object]], first["candidates"])}
            == {r["name"]: r["votes"] for r in cast(list[dict[str, object]], second["candidates"])}
        ):
            chosen, how = second, "agreed"
        elif label in accepted and (second or first) is not None:
            chosen, how = cast(dict[str, object], second or first), "accepted"
        if chosen is None:
            review.append(label)
            continue
        day = volume.get("days", {}).get(str(year)) or volume.get("day")
        if day is None:
            review.append(f"{label} (no election day)")
            continue
        candidates = [
            row for row in cast(list[dict[str, object]], chosen["candidates"]) if not BLANK.search(str(row["name"]))
        ]
        passed.extend(
            {
                "source": name,
                "state": state,
                "date": day,
                "office": office,
                "title": office.replace("-", " ").title(),
                "name": row["name"],
                "party": row["party"],
                "votes": row["votes"],
                "page": leaf,
                "how": how,
            }
            for row in candidates
        )
    if passed:
        pl.DataFrame(passed).write_parquet(folder / "tables.parquet")
    lines = [f"# {name}", ""]
    for reader in ("second", "third"):
        _, _, price_in, price_out = scans.READERS[reader]
        spent = 0.0
        pages = 0
        for request in requests(name, volume, reader, volume["leaves"]).values():
            response = scans.cached(request.key)
            if response is not None and isinstance(usage := response["usage"], dict):
                spent += (usage["input_tokens"] * price_in + usage["output_tokens"] * price_out) / 1e6
                pages += 1
        if pages:
            lines += [f"## {reader}: {pages} pages, ${spent:.4f} at batch prices", ""]
    counts = Counter(str(row["how"]) for row in passed)
    lines += [f"- {how}: {count} rows" for how, count in counts.items()]
    lines += ["", "## review", "", *[f"- {label}" for label in review]]
    (folder / "report.md").write_text("\n".join(lines))
    contests = len({(row["office"], row["date"]) for row in passed})
    return f"{message}; {contests} contests passed, {len(review)} tables in review; {folder / 'report.md'}"


def emit(name: str) -> Path:
    volume: Volume = json.loads((scans.MANIFESTS / "volumes" / f"{name}.json").read_text())
    frame = pl.read_parquet(scans.WORK / name / "tables.parquet").select(
        "source", "state", "date", "office", "title", "name", "party", "votes", "page"
    )
    manifest = cast(
        scans.Manifest,
        {key: volume[key] for key in ("polity", "publisher", "title", "license", "file") if key in volume}
        | {"name": name},
    )
    return scans.write_totals(manifest, frame)
