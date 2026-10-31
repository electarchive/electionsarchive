import base64
import hashlib
import itertools
import json
from datetime import date, timedelta
from math import ceil
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

from ea import scans
from ea.store import connect

PROMPT = "2"
VOLUMES = scans.MANIFESTS / "volumes"
SYSTEM = (
    "You describe the layout of one scanned page from a volume of election returns, so that a second reader can "
    "transcribe each table band by band. Report what is printed; never compute or infer a number. Coordinates are "
    "integers from 0 to 1000 across the width and height of the image, measured from its top left corner."
)


class LayoutColumn(TypedDict):
    label: str
    role: str
    name: str
    office: str
    party: str


class LayoutTable(TypedDict):
    title: str
    office: str
    district: int
    party: str
    special: bool
    continued: bool
    concluded: bool
    x0: int
    x1: int
    y0: int
    y1: int
    header_y1: int
    unit: str
    orientation: str
    columns: list[LayoutColumn]


class LayoutPage(TypedDict):
    kind: str
    notes: str
    tables: list[LayoutTable]


class Volume(TypedDict):
    year: int
    day: str
    polity: str
    publisher: str
    title: str
    license: str
    file: dict[str, str]
    raw_source: str
    unit_scheme: NotRequired[str]
    unit_codes: NotRequired[dict[str, str]]
    offices: dict[str, str]
    leaves: list[int]
    create: NotRequired[bool]
    autorotate: NotRequired[bool]
    turn: NotRequired[int]


def schema(offices: list[str]) -> dict[str, object]:
    table: dict[str, object] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "office": {"type": "string", "enum": [*offices, "mixed", "other"]},
            "district": {"type": "integer"},
            "party": {"type": "string"},
            "special": {"type": "boolean"},
            "continued": {"type": "boolean"},
            "concluded": {"type": "boolean"},
            "x0": {"type": "integer"},
            "x1": {"type": "integer"},
            "y0": {"type": "integer"},
            "y1": {"type": "integer"},
            "header_y1": {"type": "integer"},
            "unit": {"type": "string", "enum": ["town", "county", "ward", "other"]},
            "orientation": {"type": "string", "enum": ["units_down", "units_across"]},
            "columns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "role": {"type": "string", "enum": ["candidate", "blanks", "total", "other"]},
                        "name": {"type": "string"},
                        "office": {"type": "string", "enum": [*offices, "other"]},
                        "party": {"type": "string"},
                    },
                    "required": ["label", "role", "name", "office", "party"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "title",
            "office",
            "district",
            "party",
            "special",
            "continued",
            "concluded",
            "x0",
            "x1",
            "y0",
            "y1",
            "header_y1",
            "unit",
            "orientation",
            "columns",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["returns", "other"]},
            "notes": {"type": "string"},
            "tables": {"type": "array", "items": table},
        },
        "required": ["kind", "notes", "tables"],
        "additionalProperties": False,
    }


def instructions(volume: Volume) -> str:
    wanted = "; ".join(f"{key}: {text}" for key, text in volume["offices"].items())
    return (
        f"This page comes from {volume['title']} ({volume['year']}). List every returns table on it for these "
        f"offices, top to bottom (office key: description): {wanted}. A page of another kind, or a table for any other "
        "office, is kind 'other' or office 'other'.\n"
        "For each table: title is its heading as printed; district is the district number (0 when the office has "
        "none); party is the party named in the heading of a primary table, lowercase ('' for a general election "
        "table); special is true for a special election; continued is true when the table began on an earlier page "
        "and this page shows its later rows; concluded is true when its Total row is on this page. x0 and x1 are the "
        "table's left and right edges: a table printed in two side-by-side panels is two tables, one per panel, "
        "each with its own edges and columns and the right one marked continued. y0 is the top of "
        "the table (its heading line, or its first row when continued) and y1 the bottom of its last row on this "
        "page, Total row included; a table under another in the same panel has a y0 below that table's y1. header_y1 is the bottom of "
        "the column heading block (0 when this page shows no column headings for the table). unit is what each row "
        "is; orientation is units_down when the rows are units and units_across when the columns are. columns lists "
        "every data column left to right, leaving out the column of unit names: label is the heading as printed "
        "(candidate name and party, read turned if it is printed sideways), and role is candidate for a named "
        "candidate or an all-others or scattering column, blanks for blank or void ballots, total for a total of "
        "ballots, and other for anything else; name is the candidate's name alone ('' for a column that is not a "
        "candidate), office is the office key the column belongs to (a table with columns under several office "
        "headings has office 'mixed'), and party is the party printed for the candidate ('' if none). In a table that continues, repeat the columns even if the heading is "
        "not reprinted."
    )


def requests(volume: Volume, reader: str, leaves: list[int]) -> dict[str, scans.Read]:
    model, effort = scans.READERS[reader]
    manifest = cast(
        scans.Manifest,
        {
            "file": volume["file"],
            "raw_source": volume["raw_source"],
            "polity": volume["polity"],
            "autorotate": volume.get("autorotate", False),
        },
    )
    text = instructions(volume)
    out: dict[str, scans.Read] = {}
    for leaf in leaves:
        image = scans.render(manifest, {"page": leaf, **({"turn": volume["turn"]} if "turn" in volume else {})}, 200)
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
        out[f"{reader[0]}{leaf}"] = scans.Read(
            custom=f"{reader[0]}{leaf}",
            section=str(leaf),
            span=(0.0, 1.0),
            digest=digest,
            pixels=image.width * image.height,
            image=data,
            key=scans.key(reader, f"layout{PROMPT}", digest),
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=16000,
                system=SYSTEM,
                messages=[{"role": "user", "content": blocks}],
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": schema(list(volume["offices"]))},
                },
            ),
        )
    return out


def day_of(year: int) -> str:
    first = date(year, 11, 1)
    return (first + timedelta(days=(-first.weekday()) % 7 + 1)).isoformat()


def sound(page: LayoutPage) -> bool:
    found = sorted((table for table in page["tables"] if table["office"] != "other"), key=lambda table: table["y0"])
    return all(
        0 <= table["x0"] < table["x1"] <= 1000
        and 0 <= table["y0"] < table["y1"] <= 1000
        and 0 <= table["header_y1"] <= 1000
        for table in found
    ) and all(
        before["y1"] <= after["y0"] + 10 or before["x1"] <= after["x0"] + 10 or after["x1"] <= before["x0"] + 10
        for before, after in itertools.combinations(found, 2)
    )


def step(name: str) -> str:
    volume: Volume = json.loads((VOLUMES / f"{name}.json").read_text())
    folder = scans.WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    pages: dict[int, LayoutPage] = {}
    leaves = volume["leaves"]
    for reader in ("layout", "third"):
        plan = requests(volume, reader, leaves)
        waiting = scans.read(f"{name}/layout-{reader}", reader, plan)
        if waiting is not None:
            return waiting
        leaves = []
        for request in plan.values():
            loaded = scans.parse(scans.cached(request.key))
            if isinstance(loaded, str):
                return f"{reader} {request.custom}: {loaded}"
            pages[int(request.section)] = cast(LayoutPage, loaded)
            if not sound(pages[int(request.section)]):
                leaves.append(int(request.section))
        if not leaves:
            break
    return generate(name, volume, pages, folder)


def generate(name: str, volume: Volume, pages: dict[int, LayoutPage], folder: Path) -> str:
    state = volume["polity"].removeprefix("US-")
    contests: dict[str, scans.Contest] = {}
    sections: list[scans.Page] = []
    cache: dict[str, list[LayoutColumn]] = {}
    unmatched: dict[str, list[str]] = {}
    notes: list[str] = []
    turns: dict[int, int] = {}
    open_table: scans.Page | None = None
    source = cast(
        scans.Manifest,
        {"file": volume["file"], "raw_source": volume["raw_source"], "polity": volume["polity"]},
    )
    with connect() as conn:
        ids = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall())
        for leaf in volume["leaves"]:
            page = pages[leaf]
            if "turn" in volume:
                turns[leaf] = volume["turn"]
            elif volume.get("autorotate"):
                turns[leaf] = scans.orientation(source, leaf, scans.render(source, {"page": leaf, "turn": 0}, 150))
            if page["notes"]:
                notes.append(f"{leaf}: {page['notes']}")
            found = sorted(
                (
                    table
                    for table in page["tables"]
                    if table["office"] != "other" and not (table["continued"] and not table["title"])
                ),
                key=lambda table: table["y0"],
            )
            if not found and open_table is not None:
                left, right = open_table["crop"][0], open_table["crop"][2]
                sections.append(
                    {
                        **open_table,
                        "id": f"{leaf}-0",
                        "page": leaf,
                        "crop": [left, 0.0, right, 1.0],
                        "hint": "This page continues the table of the previous page and prints no headings.",
                    }
                )
                sections[-1].pop("bands", None)
                sections[-1].pop("turn", None)
                if turns.get(leaf):
                    sections[-1]["turn"] = turns[leaf]
                sections[-1].pop("header", None)
                notes.append(f"{leaf}: continuation of {open_table['id']} with its columns")
                continue
            open_table = None
            for index, table in enumerate(found):
                if table["party"] or table["special"]:
                    notes.append(f"{leaf}: {table['title']!r} skipped ({table['party'] or 'special'})")
                    continue
                if table["office"] == "mixed" and not volume.get("create"):
                    notes.append(f"{leaf}: {table['title']!r} mixes offices")
                    continue
                if not table["columns"] and table["office"] in cache:
                    table["columns"] = cache[table["office"]]
                cache[table["office"]] = table["columns"]
                if left := [
                    c["label"] for c in table["columns"] if c["role"] == "candidate" and c["office"] == "other"
                ]:
                    notes.append(f"{leaf}: {table['title']!r} columns for other offices: {'; '.join(left)}")
                built: list[scans.Column] = []
                for office in dict.fromkeys(
                    column["office"]
                    for column in table["columns"]
                    if column["role"] == "candidate" and column["office"] != "other"
                ):
                    label = f"{office}{table['district'] or ''}"
                    contest: scans.Contest = {
                        "year": volume["year"],
                        "chamber": office,
                        "day": volume["day"],
                        "special": False,
                        "state": state,
                        "district": table["district"] or None,
                    }
                    key = scans.key_of(contest)
                    candidates = [c for c in table["columns"] if c["role"] == "candidate" and c["office"] == office]
                    if key in ids:
                        named, unmatched[label] = scans.match(
                            [column["label"] for column in candidates], scans.options(conn, ids[key]), label
                        )
                        matched = {column["label"]: column for column in named}
                    elif volume.get("create"):
                        contest["create"] = True
                        matched = {
                            column["label"]: scans.Column(
                                label=column["label"], contest=label, candidate=column["name"], party=column["party"]
                            )
                            for column in candidates
                        }
                    else:
                        notes.append(f"{leaf}: {table['title']!r} has no contest {key}")
                        continue
                    contests[label] = contest
                    built.extend(matched.values())
                if not built:
                    continue
                by_label = {column["label"]: column for column in built}
                cols = [
                    by_label.get(column["label"]) or {"label": column["label"], "contest": None, "candidate": ""}
                    for column in table["columns"]
                ]
                beside = [
                    other
                    for other in found
                    if other is not table and other["x0"] < table["x1"] and table["x0"] < other["x1"]
                ]
                before = max((other["y1"] for other in beside if other["y0"] < table["y0"]), default=None)
                after = min((other["y0"] for other in beside if other["y0"] > table["y0"]), default=None)
                top = table["y0"] if before is None else (before + table["y0"]) // 2
                bottom = table["y1"] if after is None else (table["y1"] + after) // 2
                if not table["concluded"] and after is None:
                    bottom = 1000
                top = max(0, top - (0 if table["continued"] else 4)) / 1000
                bottom = min(1000, bottom + 4) / 1000
                left, right = max(0, table["x0"] - 8) / 1000, min(1000, table["x1"] + 8) / 1000
                section: scans.Page = {
                    "page": leaf,
                    "id": f"{leaf}-{index}",
                    "columns": cols,
                    "orientation": "counties_across" if table["orientation"] == "units_across" else "counties_down",
                    "crop": [round(left, 4), round(top, 4), round(right, 4), round(bottom, 4)],
                    "hint": f"Read only the table headed {table['title']!r}; ignore any other table visible in the image.",
                }
                if turns.get(leaf):
                    section["turn"] = turns[leaf]
                count = max(1, ceil((bottom - top) / 0.45))
                if count > 1 and table["header_y1"] > 0:
                    section["bands"] = count
                    section["header"] = [0.0, round((table["header_y1"] / 1000 - top) / (bottom - top), 4)]
                sections.append(section)
                open_table = None if table["concluded"] else section
    manifest = {
        key: value
        for key, value in volume.items()
        if key in ("polity", "publisher", "title", "license", "file", "raw_source", "unit_scheme", "unit_codes")
    }
    manifest["contests"] = contests
    manifest["pages"] = sections
    (scans.MANIFESTS / f"{name}.json").write_text(json.dumps(manifest, indent=2))
    report = [f"# layout {name}", "", f"{len(contests)} contests, {len(sections)} sections", ""]
    report += [f"- {label}: unmatched columns {missed}" for label, missed in unmatched.items() if missed]
    report += ["", *[f"- {note}" for note in notes]]
    (folder / "layout.md").write_text("\n".join(report))
    return f"{name}: {len(contests)} contests, {len(sections)} sections, {len(notes)} notes; {folder / 'layout.md'}"
