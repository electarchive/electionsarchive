import base64
import difflib
import hashlib
import io
import itertools
import json
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Literal, NamedTuple, NotRequired, TypedDict
from uuid import UUID

import anthropic
import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from PIL import Image
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats, medsl
from ea.store import DATA, connect


class Column(TypedDict):
    label: str
    contest: str | None
    candidate: str
    official: NotRequired[int]
    writein: NotRequired[bool]


class Page(TypedDict):
    page: int
    id: NotRequired[str]
    columns: NotRequired[list[Column]]
    discover: NotRequired[str]
    hint: NotRequired[str]
    crop: NotRequired[list[float]]
    dpi: NotRequired[int]
    bands: NotRequired[int]
    header: NotRequired[list[float]]
    turn: NotRequired[int]
    orientation: NotRequired[str]


class Contest(TypedDict):
    year: int
    chamber: str
    day: str
    special: bool
    state: str
    district: int | None


class Manifest(TypedDict):
    name: str
    polity: str
    publisher: str
    title: str
    file: dict[str, str]
    contests: dict[str, Contest]
    pages: list[Page]
    kind: NotRequired[str]
    license: NotRequired[str]
    raw_source: NotRequired[str]
    unit_codes: NotRequired[dict[str, str]]
    unit_scheme: NotRequired[Literal["town"]]


class Header(TypedDict):
    label: str
    x0: int
    x1: int


class Row(TypedDict):
    label: str
    y0: int
    y1: int
    values: list[str]


class Table(TypedDict):
    header: list[Header]
    rows: list[Row]


class Batch(TypedDict):
    id: str
    keys: dict[str, str]


class Reader(NamedTuple):
    model: str
    effort: Literal["low", "medium"]
    price_in: float
    price_out: float


@dataclass
class Read:
    custom: str
    section: str
    span: tuple[float, float]
    digest: str
    pixels: int
    key: str
    params: MessageCreateParamsNonStreaming


@dataclass
class Series:
    pages: list[str]
    printed: int | None
    official: int | None
    total: int = 0
    status: str = ""
    errors: list[str] = field(default_factory=list)
    cells: dict[tuple[str, str], tuple[int, list[float]]] = field(default_factory=dict)


SOURCE = "us.scans"
VERSION = "1"
PROMPT = "1"
MANIFESTS = Path(__file__).resolve().parents[1] / "scans"
WORK = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
READERS = {
    "first": Reader("claude-haiku-5-5", "low", 0.05, 0.25),
    "second": Reader("claude-sonnet-5-5", "medium", 1.0, 5.0),
}
LEDGER = DATA / "scans-ledger.json"
BUDGET = 190.0
IMAGE_LIMIT = 4_500_000
BATCH_LIMIT = 200_000_000
TOTAL = re.compile(r"^(state |grand |county )?totals?\b|^all counties$")
SUFFIX_WORDS = {"jr", "sr", "ii", "iii", "iv"}
WARD = re.compile(r"[\s,.:\-]*\b(wards?|precincts?)\b.*$", re.IGNORECASE)
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "header": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "x0": {"type": "integer"}, "x1": {"type": "integer"}},
                "required": ["label", "x0", "x1"],
                "additionalProperties": False,
            },
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "y0": {"type": "integer"},
                    "y1": {"type": "integer"},
                    "values": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "y0", "y1", "values"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["header", "rows"],
    "additionalProperties": False,
}
SYSTEM = (
    "You transcribe printed election returns tables from scanned pages into JSON. Copy every cell exactly as "
    "printed: digits with the thousands separators the page uses, and any mark that stands in for a number "
    "(X, *, a dash, a dot, or blank as an empty string) unchanged. Never compute, correct, round, or fill in a "
    "number, and never take a value from a total. A digit you cannot read is still your best reading of what is "
    "printed; do not leave one out. Row and column labels are copied as printed. Coordinates are integers from 0 "
    "to 1000 across the width and height of the last image, measured from its top left corner."
)


def client() -> anthropic.Anthropic:
    if "ANTHROPIC_API_KEY" not in os.environ:
        raise SystemExit("BLOCKED: ANTHROPIC_API_KEY")
    return anthropic.Anthropic()


def key_of(contest: Contest) -> str:
    chamber, day, state, district = contest["chamber"], contest["day"], contest["state"], contest["district"]
    base = (
        f"{day}:{state}-{district:02}:president"
        if chamber == "president" and district is not None
        else f"{day}:{state}:{chamber}"
        if district is None
        else f"{day}:{state}:house:{district}{chamber.removeprefix('house')}"
    )
    return f"{base}:special" if contest["special"] else base


def load(name: str) -> Manifest:
    manifest: Manifest = json.loads((MANIFESTS / f"{name}.json").read_text())
    manifest["name"] = name
    found = WORK / name / "columns.json"
    if found.exists():
        resolved: dict[str, list[Column]] = json.loads(found.read_text())["columns"]
        for spec in manifest["pages"]:
            if "discover" in spec and str(spec.get("id", spec["page"])) in resolved:
                spec["columns"] = resolved[str(spec.get("id", spec["page"]))]
    return manifest


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("saint", "st")).strip()


def encode(image: Image.Image) -> tuple[str, bytes]:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    if buffer.tell() > IMAGE_LIMIT:
        buffer = io.BytesIO()
        image.convert("L").save(buffer, "JPEG", quality=90)
        return "image/jpeg", buffer.getvalue()
    return "image/png", buffer.getvalue()


def artifact(manifest: Manifest, spec: Page) -> raw.Artifact:
    url = manifest["file"]["url"].format(page=spec["page"])
    try:
        return raw.cached(manifest.get("raw_source", SOURCE), url)
    except LookupError:
        return raw.fetch(SOURCE, url, suffix=".pdf" if url.lower().endswith(".pdf") else ".png")


def render(manifest: Manifest, spec: Page, dpi: int) -> Image.Image:
    source = artifact(manifest, spec)
    if source.path.suffix == ".pdf":
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(spec["page"]), "-l", str(spec["page"])]
                + ["-singlefile", str(source.path), f"{folder}/p"],
                check=True,
            )
            image = Image.open(f"{folder}/p.png").convert("L")
            image.load()
    else:
        image = Image.open(source.path).convert("L")
    if "turn" in spec:
        image = image.rotate(spec["turn"], expand=True)
    return image


@cache
def bands(
    name: str, reader: str
) -> list[tuple[str, str, tuple[float, float], list[ImageBlockParam | TextBlockParam], str, int]]:
    manifest = load(name)
    folder = WORK / name / "crops"
    folder.mkdir(parents=True, exist_ok=True)
    out: list[tuple[str, str, tuple[float, float], list[ImageBlockParam | TextBlockParam], str, int]] = []
    for spec in manifest["pages"]:
        image = render(manifest, spec, spec.get("dpi", 300))
        width, height = image.size
        x0, y0, x1, y1 = spec.get("crop", [0, 0, 1, 1])
        crop = image.crop((round(x0 * width), round(y0 * height), round(x1 * width), round(y1 * height)))
        count = spec.get("bands", 1)
        overlap = round(crop.height * 0.02) if count > 1 else 0
        header = None
        if count > 1 and "header" in spec:
            top, bottom = spec["header"]
            header = crop.crop((0, round(top * crop.height), crop.width, round(bottom * crop.height)))
        labelled = "columns" in spec and not ("discover" in spec and reader == "first")
        labels = json.dumps([column["label"] for column in spec.get("columns", [])])
        for band in range(count):
            start = max(0, round(band * crop.height / count) - overlap)
            stop = min(crop.height, round((band + 1) * crop.height / count) + overlap)
            parts = [
                part
                for part in (header if band > 0 else None, crop.crop((0, start, crop.width, stop)))
                if part is not None
            ]
            blocks: list[ImageBlockParam | TextBlockParam] = []
            digest = hashlib.sha256()
            pixels = 0
            for position, part in enumerate(parts):
                media, data = encode(part)
                (folder / f"p{spec.get('id', spec['page'])}-b{band}-{position}.{media.split('/')[1]}").write_bytes(data)
                blocks.append(
                    ImageBlockParam(
                        type="image",
                        source={
                            "type": "base64",
                            "media_type": "image/jpeg" if media == "image/jpeg" else "image/png",
                            "data": base64.b64encode(data).decode(),
                        },
                    )
                )
                digest.update(data)
                pixels += part.width * part.height
            across = spec.get("orientation") == "counties_across"
            unit = "towns" if manifest.get("unit_scheme") == "town" else "counties"
            if labelled:
                text = (
                    f"The columns are the {unit} and the rows are the candidates' lines, one row per line in this "
                    f"order: {labels}. Skip heading lines that carry no cells. Put the {unit[:-1]} names in header, "
                    "left to right, including a total column if one is printed. Each row's values has one string "
                    "per header entry."
                    if across
                    else f"The rows are the {unit}, and a total row if one is printed. The data columns, left to "
                    f"right, are: {labels}. Header has one entry per data column and each row's values has one "
                    "string per data column; leave out any column not in this list."
                )
            else:
                text = (
                    f"The columns are the {unit} and the rows are the candidates' lines. Put the {unit[:-1]} names "
                    "in header, left to right, including a total column if one is printed. Each row's label is the "
                    "candidate as printed, with party if printed, and its values has one string per header entry."
                    if across
                    else f"The rows are the {unit}, and a total row if one is printed. Header has one entry per "
                    "printed data column, left to right, labeled exactly as printed (the candidate's name, with "
                    "party if printed), including a total column if one is printed. Each row's values has one "
                    "string per header entry."
                )
            if unit == "towns":
                text += (
                    " A city or town name followed by indented ward or precinct lines is a heading: do not make it "
                    "a row, and begin each indented line's label with the heading, as in 'Fall River: Wards 1, 2, 3'."
                )
            if "hint" in spec:
                text += f" {spec['hint']}"
            if count > 1:
                text += (
                    f" This is band {band + 1} of {count} of the table: read only the rows visible in the last image."
                )
            blocks.append(TextBlockParam(type="text", text=text))
            digest.update(text.encode())
            out.append(
                (
                    f"p{spec.get('id', spec['page'])}-b{band}",
                    str(spec.get("id", spec["page"])),
                    (start / crop.height, stop / crop.height),
                    blocks,
                    digest.hexdigest(),
                    pixels,
                )
            )
    return out


def requests(name: str, reader: str) -> dict[str, Read]:
    model, effort, _, _ = READERS[reader]
    return {
        custom: Read(
            custom=custom,
            section=page,
            span=span,
            digest=digest,
            pixels=pixels,
            key=f"anthropic-batch:{model}/{PROMPT}/{effort}/{digest}",
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=32000,
                system=SYSTEM,
                messages=[{"role": "user", "content": blocks}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            ),
        )
        for custom, page, span, blocks, digest, pixels in bands(name, reader)
    }


def cached(key: str) -> dict[str, object] | None:
    try:
        return json.loads(raw.cached(SOURCE, key).path.read_text())
    except LookupError:
        return None


def parse(response: dict[str, object] | None) -> Table | str:
    if response is None:
        return "not read yet"
    if response.get("stop_reason") != "end_turn":
        return f"stopped on {response.get('stop_reason')}"
    content = response["content"]
    text = (
        next((block["text"] for block in content if block["type"] == "text"), None)
        if isinstance(content, list)
        else None
    )
    if text is None:
        return "no text block"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return "invalid JSON"


def counties(manifest: Manifest) -> dict[str, str]:
    if manifest.get("unit_scheme") == "town":
        with zipfile.ZipFile(raw.cached("us.elstats", elstats.GAZETTEER).path) as archive:
            gazetteer = pl.read_csv(
                io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
            ).filter(pl.col("USPS") == manifest["polity"].removeprefix("US-"))
        towns: dict[str, str] = {}
        for geoid, name in (
            gazetteer.sort(pl.col("NAME").str.contains(r" (gore|grant|location|purchase|UT)$"))
            .select("GEOID", "NAME")
            .iter_rows()
        ):
            towns.setdefault(elstats.plain(name), geoid[:5])
            towns.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
        return towns
    with connect() as conn:
        found = conn.execute(
            """select n.text, u.code from reporting_unit u
               join name n on n.entity_id = u.area_id
               join polity p on p.id = u.polity_id
               where u.scheme_code = 'us.county' and p.iso_code = %s""",
            [manifest["polity"]],
        ).fetchall()
    return {
        slug(
            re.sub(
                r"\s+(county|parish|borough|census area|city and borough|municipio)\b.*$", "", text, flags=re.IGNORECASE
            )
        ).replace(" ", ""): code
        for text, code in found
    }


def cells(
    manifest: Manifest, plan: dict[str, Read], names: dict[str, str]
) -> tuple[dict[tuple[str, str, int], tuple[int, list[float]]], dict[tuple[str, int], int], dict[str, str]]:
    values: dict[tuple[str, str, int], tuple[int, list[float]]] = {}
    margin: dict[tuple[str, str, int], int] = {}
    printed: dict[tuple[str, int], int] = {}
    errors: dict[str, str] = {}
    extra = {slug(label).replace(" ", ""): code for label, code in manifest.get("unit_codes", {}).items()}
    towns = manifest.get("unit_scheme") == "town"
    home: dict[str, str] = {}
    for spec in manifest["pages"]:
        across = spec.get("orientation") == "counties_across"
        x0, y0, x1, y1 = spec.get("crop", [0, 0, 1, 1])
        number = str(spec.get("id", spec["page"]))
        for custom, request in plan.items():
            if request.section != number:
                continue
            table = parse(cached(request.key))
            if isinstance(table, str):
                errors[number] = f"{custom}: {table}"
                continue
            header, rows = table["header"], table["rows"]
            if "columns" not in spec:
                errors[number] = f"{custom}: columns not discovered"
                continue
            if len(rows if across else header) != len(spec["columns"]):
                errors[number] = (
                    f"{custom}: {len(rows if across else header)} {'rows' if across else 'columns'} "
                    f"for {len(spec['columns'])}"
                )
                continue
            if any(after["y0"] < before["y0"] for before, after in itertools.pairwise(rows)):
                errors[number] = f"{custom}: row bands are not ascending"
                continue
            start, stop = request.span
            heading = ""
            for r, row in enumerate(rows):
                if towns and not any(row["values"]):
                    heading = row["label"]
                    continue
                named = (
                    f"{heading} {row['label']}"
                    if towns and re.match(r"\s*(wards?|precincts?)\b", row["label"], re.IGNORECASE)
                    else row["label"]
                )
                heading = WARD.sub("", named)
                if len(row["values"]) != len(header):
                    errors[number] = f"{custom}: row {row['label']!r} has {len(row['values'])} cells for {len(header)}"
                    break
                for c, text in enumerate(row["values"]):
                    series, label = (r, header[c]["label"]) if across else (c, named)
                    digits = re.sub(r"[,\s.]", "", text)
                    value = int(digits) if digits.isdigit() else None
                    if TOTAL.match(slug(label)):
                        if value is not None:
                            printed[(number, series)] = value
                        continue
                    if value is None:
                        continue
                    stem = slug(WARD.sub("", label) if towns else label).replace(" ", "")
                    code = extra.get(stem) or names.get(stem)
                    if code is None:
                        close = difflib.get_close_matches(stem, list(names), n=2, cutoff=0.84)
                        starts = {names[name] for name in names if name.startswith(stem) and len(stem) > 4}
                        code = names[close[0]] if len(close) == 1 else starts.pop() if len(starts) == 1 else None
                    if code is None:
                        errors[number] = (
                            f"{custom}: {'town' if towns else 'county'} {label!r} is not in {manifest['polity']}"
                        )
                        break
                    unit = slug(label).replace(" ", "") if towns else code
                    home[unit] = code
                    box = [
                        x0 + (x1 - x0) * header[c]["x0"] / 1000,
                        y0 + (y1 - y0) * (start + (stop - start) * row["y0"] / 1000),
                        x0 + (x1 - x0) * header[c]["x1"] / 1000,
                        y0 + (y1 - y0) * (start + (stop - start) * row["y1"] / 1000),
                    ]
                    edge = min(row["y0"], 1000 - row["y1"])
                    if (number, unit, series) not in values or edge > margin[(number, unit, series)]:
                        values[(number, unit, series)] = (value, box)
                        margin[(number, unit, series)] = edge
                else:
                    continue
                break
    if towns:
        folded: dict[tuple[str, str, int], tuple[int, list[float]]] = {}
        for (number, unit, series), (value, box) in values.items():
            before = folded.get((number, home[unit], series))
            folded[(number, home[unit], series)] = (
                (value + before[0], before[1]) if before is not None else (value, box)
            )
        values = folded
    return values, printed, errors


def evaluate(manifest: Manifest, reader: str, names: dict[str, str], held: dict[str, int | None]) -> dict[str, Series]:
    values, printed, errors = cells(manifest, requests(manifest["name"], reader), names)
    series: dict[str, Series] = {}
    joined: set[tuple[str, str]] = set()
    for spec in manifest["pages"]:
        page = str(spec.get("id", spec["page"]))
        for index, column in enumerate(spec.get("columns", [])):
            if column["contest"] is None:
                continue
            name = f"{column['contest']}|{column['candidate']}"
            entry = series.setdefault(name, Series(pages=[], printed=None, official=held.get(name)))
            entry.pages.append(page)
            if page in errors:
                entry.errors.append(errors[page])
            again = (name, page) in joined
            joined.add((name, page))
            if (page, index) in printed:
                entry.printed = (entry.printed or 0) + printed[(page, index)] if again else printed[(page, index)]
            for (found, code, position), (value, box) in values.items():
                if found == page and position == index:
                    entry.total += value
                    before = entry.cells.get((page, code))
                    entry.cells[(page, code)] = (value + before[0], before[1]) if before is not None else (value, box)
    for entry in series.values():
        entry.status = (
            "failed"
            if entry.errors
            else "unchecked"
            if entry.printed is None and entry.official is None
            else "tied"
            if entry.total == entry.printed
            or (
                entry.printed is None
                and entry.official is not None
                and abs(entry.total - entry.official) <= max(50, entry.official * 0.01)
            )
            else "failed"
        )
    return series


def pending(name: str, reader: str, pages: set[str] | None) -> dict[str, Read]:
    return {
        custom: request
        for custom, request in requests(name, reader).items()
        if (pages is None or request.section in pages) and cached(request.key) is None
    }


def guess(reader: str, request: Read) -> float:
    _, _, price_in, price_out = READERS[reader]
    return (min(request.pixels, 3_500_000) / 750 * price_in + 6000 * price_out) / 1e6


def ledger() -> dict[str, dict[str, float]]:
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {"spent": {}, "pending": {}}


def submit(reader: str, wanted: dict[str, Read], state: dict[str, dict[str, list[Batch]]]) -> list[str]:
    api = client()
    groups: list[list[Read]] = [[]]
    size = 0
    for request in wanted.values():
        weight = len(json.dumps(request.params))
        if groups[-1] and size + weight > BATCH_LIMIT:
            groups.append([])
            size = 0
        groups[-1].append(request)
        size += weight
    books = ledger()
    projected = sum(guess(reader, request) for request in wanted.values())
    total = sum(books["spent"].values()) + sum(books["pending"].values())
    if total + projected > BUDGET:
        raise SystemExit(
            f"BLOCKED: budget; ${total:.2f} spent or pending plus ${projected:.2f} projected passes ${BUDGET}"
        )
    print(f"projected ${projected:.3f}; ${total:.2f} spent or pending of ${BUDGET}")
    ids: list[str] = []
    for group in groups:
        created = api.messages.batches.create(
            requests=[Request(custom_id=request.custom, params=request.params) for request in group]
        )
        state.setdefault("batches", {}).setdefault(reader, []).append(
            {"id": created.id, "keys": {request.custom: request.key for request in group}}
        )
        books["pending"][created.id] = sum(guess(reader, request) for request in group)
        ids.append(created.id)
    LEDGER.write_text(json.dumps(books, indent=2))
    return ids


def collect(reader: str, state: dict[str, dict[str, list[Batch]]]) -> str:
    api = client()
    notes: list[str] = []
    remaining = []
    for batch in state["batches"][reader]:
        status = api.messages.batches.retrieve(batch["id"])
        if status.processing_status != "ended":
            notes.append(f"{batch['id']} {status.processing_status}, {status.request_counts.processing} processing")
            remaining.append(batch)
            continue
        books = ledger()
        _, _, price_in, price_out = READERS[reader]
        dollars = 0.0
        for result in api.messages.batches.results(batch["id"]):
            if result.result.type == "succeeded":
                usage = result.result.message.usage
                dollars += (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
                raw.store(
                    SOURCE,
                    batch["keys"][result.custom_id],
                    result.result.message.model_dump_json().encode(),
                    suffix=".json",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/json",
                )
            else:
                notes.append(f"{result.custom_id} {result.result.type}")
        books["pending"].pop(batch["id"], None)
        books["spent"][batch["id"]] = dollars
        LEDGER.write_text(json.dumps(books, indent=2))
    state["batches"][reader] = remaining
    return "; ".join(notes)


def options(conn: Connection, contest: UUID | None) -> list[tuple[str, str, int]]:
    if contest is None:
        return []
    return [
        (label or text, text, total)
        for label, text, total in conn.execute(
            """select coalesce(o.ballot_label, ''), coalesce(n.text, ''),
                      (select sum(v.value)::bigint from result_value v
                       where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                         and v.count_round = (select min(count_round) from result_value
                                              where result_set_k = rs.k))
               from contest_option o
               join result_set rs on rs.contest_id = o.contest_id and rs.is_default
               left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               left join name n on n.entity_id = cy.person_id and n.is_preferred
               where o.contest_id = %s and o.parent_option_id is null""",
            [contest],
        ).fetchall()
        if total
    ]


def official_totals(manifest: Manifest) -> dict[str, int | None]:
    held: dict[str, int | None] = {}
    with connect() as conn:
        ids = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall())
        for spec in manifest["pages"]:
            for column in spec.get("columns", []):
                if column["contest"] is None:
                    continue
                name = f"{column['contest']}|{column['candidate']}"
                contest = ids.get(key_of(manifest["contests"][column["contest"]]))
                words = [word for word in slug(column["candidate"]).split() if len(word) > 1]
                if "official" in column:
                    held[name] = column["official"]
                elif contest is None or not words:
                    held[name] = None
                else:
                    surname = words[0] if "," in column["candidate"] else words[-1]
                    found = [
                        total
                        for label, text, total in options(conn, contest)
                        if surname in slug(f"{label} {text}").split()
                    ]
                    held[name] = found[0] if len(found) == 1 else None
    return held


def discover(manifest: Manifest, folder: Path) -> str:
    found = folder / "columns.json"
    state: dict[str, dict[str, object]] = (
        json.loads(found.read_text()) if found.exists() else {"columns": {}, "unmatched": {}}
    )
    plan = requests(manifest["name"], "first")
    problems: list[str] = []
    with connect() as conn:
        ids = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall())
        for spec in manifest["pages"]:
            number = str(spec.get("id", spec["page"]))
            if "discover" not in spec or number in state["columns"]:
                continue
            first = next(request for request in plan.values() if request.section == number)
            table = parse(cached(first.key))
            if isinstance(table, str):
                problems.append(f"{number}: {table}")
                continue
            labels = (
                [row["label"] for row in table["rows"]]
                if spec.get("orientation") == "counties_across"
                else [entry["label"] for entry in table["header"]]
            )
            held = options(conn, ids.get(key_of(manifest["contests"][spec["discover"]])))
            columns: list[Column] = []
            unmatched: list[str] = []
            surnames: list[tuple[str, set[str]]] = []
            for name, text, _ in held:
                found_surnames = set()
                for form in (name, text):
                    forms = [word for word in slug(form).split() if len(word) > 1 and word not in SUFFIX_WORDS]
                    if forms:
                        found_surnames.add(forms[0] if "," in form else forms[-1])
                surnames.append((name, found_surnames))
            for label in labels:
                words = set(slug(label).split())
                found_options = [name for name, options_ in surnames if options_ & words]
                if not found_options:
                    found_options = [
                        name
                        for name, options_ in surnames
                        if any(difflib.get_close_matches(word, options_, n=1, cutoff=0.85) for word in words)
                    ]
                if len(found_options) == 1:
                    columns.append({"label": label, "contest": spec["discover"], "candidate": found_options[0]})
                else:
                    columns.append({"label": label, "contest": None, "candidate": ""})
                    unmatched.append(label)
            state["columns"][number] = columns
            state["unmatched"][number] = unmatched
    found.write_text(json.dumps(state, indent=2))
    return "; ".join(problems)


def prepare(name: str) -> str:
    manifest = load(name)
    folder = WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    held = official_totals(manifest)
    (folder / "official.json").write_text(json.dumps(held, indent=2))
    out = []
    for reader, (model, _, price_in, price_out) in READERS.items():
        plan = requests(name, reader)
        out.append(
            f"{reader} ({model}): {len(plan)} requests, about ${sum(guess(reader, r) for r in plan.values()):.3f} if every page goes"
        )
    unmatched = [column for column, total in held.items() if total is None]
    return "\n".join(out + [f"no official total held for {len(unmatched)} of {len(held)} columns", *unmatched[:20]])


def run(name: str) -> str:
    manifest = load(name)
    folder = WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    state: dict[str, dict[str, list[Batch]]] = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not (folder / "official.json").exists():
        (folder / "official.json").write_text(json.dumps(official_totals(manifest), indent=2))
    held: dict[str, int | None] = json.loads((folder / "official.json").read_text())
    names = counties(manifest)
    message = ""
    outcome: dict[str, dict[str, Series]] = {}
    redo: set[str] | None = None
    for reader in ("first", "second"):
        if reader == "second":
            redo = {page for entry in outcome["first"].values() if entry.status != "tied" for page in entry.pages}
            if not redo:
                break
        if state.get("batches", {}).get(reader):
            message = collect(reader, state) or message
        wanted = pending(name, reader, redo)
        if wanted and not state.get("batches", {}).get(reader):
            message = f"{reader}: submitted {len(wanted)} requests in {', '.join(submit(reader, wanted, state))}"
        state_path.write_text(json.dumps(state, indent=2))
        if pending(name, reader, redo):
            return message or f"{reader}: waiting for the batch"
        if reader == "first" and any("discover" in spec and "columns" not in spec for spec in manifest["pages"]):
            problems = discover(manifest, folder)
            manifest = load(name)
            bands.cache_clear()
            held = official_totals(manifest)
            (folder / "official.json").write_text(json.dumps(held, indent=2))
            if problems:
                return f"discovery failed: {problems}"
        outcome[reader] = evaluate(manifest, reader, names, held)
    return finalize(manifest, folder, outcome["first"], outcome.get("second"), held)


def finalize(
    manifest: Manifest,
    folder: Path,
    first: dict[str, Series],
    second: dict[str, Series] | None,
    held: dict[str, int | None],
) -> str:
    overrides = json.loads((folder / "overrides.json").read_text()) if (folder / "overrides.json").exists() else {}
    accepted: dict[str, str] = overrides.get("accept", {})
    columns = {
        f"{column['contest']}|{column['candidate']}": column
        for spec in manifest["pages"]
        for column in spec.get("columns", [])
        if column["contest"] is not None
    }
    numbers = {str(spec.get("id", spec["page"])): spec["page"] for spec in manifest["pages"]}
    passed: list[dict[str, object]] = []
    review: list[dict[str, object]] = []
    for name, one in first.items():
        two = second[name] if second is not None and one.status != "tied" else None
        chosen, how = one, "first"
        if one.status != "tied":
            if two is not None and two.status == "tied":
                chosen, how = two, "second"
            elif (
                two is not None
                and one.status == "unchecked"
                and not one.errors
                and not two.errors
                and {k: v[0] for k, v in one.cells.items()} == {k: v[0] for k, v in two.cells.items()}
            ):
                chosen, how = two, "agreed"
            else:
                chosen, how = two or one, "review"
        cellmap = {key: (value, box) for key, (value, box) in chosen.cells.items()}
        fixes = [item for item in overrides.get("cells", []) if item["series"] == name]
        if how == "review" and fixes:
            for item in fixes:
                key = (str(item.get("section", item.get("page"))), str(item["county"]))
                if key in cellmap:
                    cellmap[key] = (int(item["value"]), cellmap[key][1])
            target = chosen.printed if chosen.printed is not None else held.get(name)
            slack = 0 if chosen.printed is not None else max(50, (target or 0) * 0.01)
            if target is not None and abs(sum(value for value, _ in cellmap.values()) - target) <= slack:
                how = "settled"
        if how == "review" and name in accepted:
            how = "accepted"
        if how == "review":
            other = two.cells if two is not None else {}
            differing = {
                f"{page}:{code}"
                for page, code in set(one.cells) | set(other)
                if one.cells.get((page, code), (None,))[0] != other.get((page, code), (None,))[0]
            }
            review.append(
                {
                    "series": name,
                    "pages": chosen.pages,
                    "first": {"sum": one.total, "status": one.status, "errors": one.errors},
                    "second": None if two is None else {"sum": two.total, "status": two.status, "errors": two.errors},
                    "printed": chosen.printed,
                    "official": held.get(name),
                    "differing": sorted(differing)[:40],
                }
            )
            continue
        contest, candidate = name.split("|", 1)
        passed.extend(
            {
                "series": name,
                "contest": contest,
                "candidate": candidate,
                "writein": columns[name].get("writein", False),
                "page": numbers[page],
                "section": page,
                "code": code,
                "votes": value,
                "x0": box[0],
                "y0": box[1],
                "x1": box[2],
                "y1": box[3],
                "how": how,
                "note": accepted.get(name),
            }
            for (page, code), (value, box) in cellmap.items()
        )
    (folder / "review.json").write_text(json.dumps(review, indent=2))
    if passed:
        pl.DataFrame(passed).write_parquet(folder / "tables.parquet")
    lines = [f"# {manifest['name']}", ""]
    for reader, (model, _, price_in, price_out) in READERS.items():
        seen: dict[str, tuple[int, int]] = {}
        for request in requests(manifest["name"], reader).values():
            response = cached(request.key)
            if response is not None and isinstance(usage := response["usage"], dict):
                before = seen.get(request.section, (0, 0))
                seen[request.section] = (before[0] + usage["input_tokens"], before[1] + usage["output_tokens"])
        if seen:
            dollars = sum(i * price_in + o * price_out for i, o in seen.values()) / 1e6
            lines += [
                f"## {reader} ({model}): {len(seen)} pages, ${dollars:.4f}, ${dollars / len(seen):.4f} per page",
                "",
            ]
            lines += [f"- {page}: {i} in, {o} out" for page, (i, o) in sorted(seen.items())] + [""]
    counts: dict[tuple[str, str], int] = {}
    for row in passed:
        pair = (str(row["series"]), str(row["how"]))
        counts[pair] = counts.get(pair, 0) + 1
    lines += ["## passed", "", *[f"- {series}: {how}, {count} cells" for (series, how), count in counts.items()]]
    if (folder / "columns.json").exists():
        unmatched: dict[str, list[str]] = json.loads((folder / "columns.json").read_text())["unmatched"]
        lines += ["", "## unmatched columns", ""]
        lines += [f"- {page}: {', '.join(labels)}" for page, labels in unmatched.items() if labels]
    lines += ["", "## review", ""]
    for item in review:
        lines.append(
            f"- {item['series']}: first {item['first']}, second {item['second']}, printed {item['printed']}, official {item['official']}"
        )
    (folder / "report.md").write_text("\n".join(lines))
    return f"{len({row['series'] for row in passed})} series passed, {len(review)} in review; {folder / 'report.md'}"


def status() -> str:
    out = []
    for path in sorted(MANIFESTS.glob("*.json")):
        folder = WORK / path.stem
        state = json.loads((folder / "state.json").read_text()) if (folder / "state.json").exists() else {}
        review = json.loads((folder / "review.json").read_text()) if (folder / "review.json").exists() else None
        waiting = {reader: len(batches) for reader, batches in state.get("batches", {}).items() if batches}
        out.append(
            f"{path.stem}: {f'batches waiting {waiting}, ' if waiting else ''}"
            f"{'tables' if (folder / 'tables.parquet').exists() else 'no tables yet'}"
            f"{'' if review is None else f', {len(review)} in review'}"
        )
    books = ledger()
    out.append(
        f"spent ${sum(books['spent'].values()):.2f}, pending ${sum(books['pending'].values()):.2f}, budget ${BUDGET}"
    )
    return "\n".join(out)


def emit(name: str) -> Path:
    manifest = load(name)
    contests = manifest["contests"]
    frame = pl.read_parquet(WORK / name / "tables.parquet")
    towns = manifest.get("unit_scheme") == "town"
    if towns:
        frame = frame.group_by("contest", "candidate", "code", maintain_order=True).agg(
            pl.col("votes").sum(), pl.exclude("contest", "candidate", "code", "votes").first()
        )
    frame = frame.with_columns(
        **{
            name_: pl.col("contest").replace_strict({key: value[name_] for key, value in contests.items()})
            for name_ in ("year", "chamber", "day", "special", "state", "district")
        },
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit(f"SC{name}"),
        file=pl.lit(0),
    ).with_columns(
        year=pl.col("year").cast(pl.Int32),
        day=pl.col("day").str.to_date(),
        district=pl.col("district").cast(pl.Int16),
    )
    caveats = {
        key_of(contests[contest]): note
        for contest, note in frame.filter(pl.col("note").is_not_null()).select("contest", "note").unique().iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"{manifest['title']}: county tables read from the scan by a vision model and checked against the printed and held totals",
            polity=manifest["polity"],
        )
        source = cs.source(manifest["publisher"], manifest["title"])
        cs.add(
            "source",
            id=source,
            kind=manifest.get("kind", "official_body"),
            publisher=manifest["publisher"],
            title=manifest["title"],
            url=manifest["file"].get("item", manifest["file"]["url"]),
            license=manifest.get("license", "Public record"),
        )
        PAGES.mkdir(parents=True, exist_ok=True)
        pages: dict[int, str] = {}
        each = "{page}" in manifest["file"]["url"]
        file: UUID | None = None
        specs = {spec["page"]: spec for spec in manifest["pages"]}
        for number in sorted(set(frame["page"])):
            stored = artifact(manifest, specs[number])
            if each or file is None:
                file = cs.artifact(stored, source)
            image = PAGES / f"{stored.sha256}-{number}.png"
            if not image.exists():
                render(manifest, specs[number], 100).save(image)
            width, height = Image.open(image).size
            page = cs.page(file, 1 if each else number)
            pages[number] = str(page)
            cs.add(
                "source_page",
                id=page,
                artifact_id=file,
                page_number=1 if each else number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        frame = frame.with_columns(page_id=pl.col("page").replace_strict(pages, return_dtype=pl.String), page=pl.lit(1))
        medsl.attach(cs, conn, frame, {f"SC{name}": source}, strict=True)
        for row in list(cs.rows.get("result_set", [])):
            found = conn.execute(
                "select value from external_id where scheme = 'us.contest' and entity_id = %s", [row["contest_id"]]
            ).fetchone()
            cs.add(
                "note",
                entity_id=row["id"],
                kind="caveat",
                body_md="Read from the scan by a vision model. Each cell's box is its column's band by its row's band as the model reported them, so it is approximate."
                + (
                    " Each county's votes are the sum of its towns' returns, towns placed in counties by the 2020 Census Gazetteer, and the box is the first town's."
                    if towns
                    else ""
                )
                + (f" {caveats[found[0]]}" if found is not None and found[0] in caveats else ""),
            )
        return cs.write()
