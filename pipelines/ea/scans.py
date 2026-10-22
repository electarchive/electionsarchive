import base64
import hashlib
import io
import itertools
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import anthropic
import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.scans"
VERSION = "1"
PROMPT = "1"
MANIFESTS = Path(__file__).resolve().parents[1] / "scans"
WORK = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
READERS = {
    "first": ("claude-haiku-5-5", "low", 0.05, 0.25),
    "second": ("claude-sonnet-5-5", "medium", 1.0, 5.0),
}
IMAGE_LIMIT = 4_500_000
BATCH_LIMIT = 200_000_000
TOTAL = re.compile(r"^(state |grand |county )?totals?\b|^all counties$")
SCHEMA = {
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


def key_of(contest: dict[str, object]) -> str:
    chamber, day, state, district = str(contest["chamber"]), contest["day"], contest["state"], contest["district"]
    base = (
        f"{day}:{state}-{int(str(district)):02}:president"
        if chamber == "president" and district is not None
        else f"{day}:{state}:{chamber}"
        if district is None
        else f"{day}:{state}:house:{district}{chamber.removeprefix('house')}"
    )
    return f"{base}:special" if contest["special"] else base


def load(name: str) -> dict[str, object]:
    manifest = json.loads((MANIFESTS / f"{name}.json").read_text())
    manifest["name"] = name
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


def artifact(manifest: dict[str, object]) -> raw.Artifact:
    file = dict(manifest["file"])
    try:
        return raw.cached(str(manifest.get("raw_source", SOURCE)), file["url"])
    except LookupError:
        return raw.fetch(SOURCE, file["url"], suffix=".pdf" if file["url"].lower().endswith(".pdf") else ".png")


def render(manifest: dict[str, object], spec: dict[str, object], dpi: int) -> Image.Image:
    source = artifact(manifest)
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
    if spec.get("turn"):
        image = image.rotate(spec["turn"], expand=True)
    return image


@cache
def bands(name: str) -> list[dict[str, object]]:
    manifest = load(name)
    folder = WORK / name / "crops"
    folder.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, object]] = []
    for spec in manifest["pages"]:
        image = render(manifest, spec, int(spec.get("dpi", 300)))
        width, height = image.size
        x0, y0, x1, y1 = spec.get("crop", [0, 0, 1, 1])
        crop = image.crop((round(x0 * width), round(y0 * height), round(x1 * width), round(y1 * height)))
        count = int(spec.get("bands", 1))
        across = spec.get("orientation", "counties_down") == "counties_across"
        overlap = round(crop.height * 0.02) if count > 1 else 0
        header = None
        if count > 1 and spec.get("header"):
            top, bottom = spec["header"]
            header = crop.crop((0, round(top * crop.height), crop.width, round(bottom * crop.height)))
        labels = [column.get("label", column["candidate"]) for column in spec["columns"]]
        for band in range(count):
            start = max(0, round(band * crop.height / count) - overlap)
            stop = min(crop.height, round((band + 1) * crop.height / count) + overlap)
            parts = [
                part
                for part in (header if band > 0 else None, crop.crop((0, start, crop.width, stop)))
                if part is not None
            ]
            blocks: list[dict[str, object]] = []
            digest = hashlib.sha256()
            pixels = 0
            for position, part in enumerate(parts):
                media, data = encode(part)
                (folder / f"p{spec['page']}-b{band}-{position}.{media.split('/')[1]}").write_bytes(data)
                blocks.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media, "data": base64.b64encode(data).decode()},
                    }
                )
                digest.update(data)
                pixels += part.width * part.height
            text = (
                f"The columns are the counties and the rows are the candidates' lines, one row per line in this order: "
                f"{json.dumps(labels)}. Put the county names in header, left to right, including a total column if one "
                "is printed. Each row's values has one string per header entry."
                if across
                else f"The rows are the counties, and a total row if one is printed. The {len(labels)} data columns, left "
                f"to right, are: {json.dumps(labels)}. Header has one entry per data column and each row's values has "
                "one string per data column; leave out any column not in this list."
            )
            if count > 1:
                text += (
                    f" This is band {band + 1} of {count} of the table: read only the rows visible in the last image."
                )
            blocks.append({"type": "text", "text": text})
            digest.update(text.encode())
            out.append(
                {
                    "custom": f"p{spec['page']}-b{band}",
                    "page": int(spec["page"]),
                    "span": (start / crop.height, stop / crop.height),
                    "blocks": blocks,
                    "digest": digest.hexdigest(),
                    "pixels": pixels,
                }
            )
    return out


def requests(name: str, reader: str) -> dict[str, dict[str, object]]:
    model, effort, _, _ = READERS[reader]
    return {
        band["custom"]: {
            **band,
            "key": f"anthropic-batch:{model}/{PROMPT}/{effort}/{band['digest']}",
            "params": {
                "model": model,
                "max_tokens": 32000,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": band["blocks"]}],
                "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            },
        }
        for band in bands(name)
    }


def cached(key: str) -> dict[str, object] | None:
    try:
        return json.loads(raw.cached(SOURCE, key).path.read_text())
    except LookupError:
        return None


def parse(response: dict[str, object]) -> dict[str, object] | str:
    if response.get("stop_reason") != "end_turn":
        return f"stopped on {response.get('stop_reason')}"
    text = next((block["text"] for block in response["content"] if block["type"] == "text"), None)
    if text is None:
        return "no text block"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return "invalid JSON"


def counties(polity: str) -> dict[str, str]:
    with connect() as conn:
        found = conn.execute(
            """select n.text, u.code from reporting_unit u
               join name n on n.entity_id = u.area_id
               join polity p on p.id = u.polity_id
               where u.scheme_code = 'us.county' and p.iso_code = %s""",
            [polity],
        ).fetchall()
    return {
        slug(
            re.sub(
                r"\s+(county|parish|borough|census area|city and borough|municipio)\b.*$", "", text, flags=re.IGNORECASE
            )
        ): code
        for text, code in found
    }


def cells(
    manifest: dict[str, object], plan: dict[str, dict[str, object]], names: dict[str, str]
) -> tuple[dict[tuple[int, str, int], tuple[int, list[float]]], dict[tuple[int, int], int], dict[int, str]]:
    values: dict[tuple[int, str, int], tuple[int, list[float]]] = {}
    printed: dict[tuple[int, int], int] = {}
    errors: dict[int, str] = {}
    extra = {slug(label): code for label, code in dict(manifest.get("unit_codes", {})).items()}
    for spec in manifest["pages"]:
        across = spec.get("orientation", "counties_down") == "counties_across"
        x0, y0, x1, y1 = spec.get("crop", [0, 0, 1, 1])
        number = int(spec["page"])
        for custom, request in plan.items():
            if request["page"] != number:
                continue
            response = cached(str(request["key"]))
            table = parse(response) if response is not None else "not read yet"
            if isinstance(table, str):
                errors[number] = f"{custom}: {table}"
                continue
            header, rows = table["header"], table["rows"]
            if len(rows if across else header) != len(spec["columns"]):
                errors[number] = (
                    f"{custom}: {len(rows if across else header)} {'rows' if across else 'columns'} for {len(spec['columns'])}"
                )
                continue
            if any(after["y0"] < before["y0"] for before, after in itertools.pairwise(rows)):
                errors[number] = f"{custom}: row bands are not ascending"
                continue
            start, stop = request["span"]
            for r, row in enumerate(rows):
                if len(row["values"]) != len(header):
                    errors[number] = f"{custom}: row {row['label']!r} has {len(row['values'])} cells for {len(header)}"
                    break
                for c, text in enumerate(row["values"]):
                    series, label = (r, header[c]["label"]) if across else (c, row["label"])
                    digits = re.sub(r"[,\s.]", "", text)
                    value = int(digits) if digits.isdigit() else None
                    if TOTAL.match(slug(label)):
                        if value is not None:
                            printed[(number, series)] = value
                        continue
                    if value is None:
                        continue
                    code = extra.get(slug(label)) or names.get(slug(label))
                    if code is None:
                        errors[number] = f"{custom}: county {label!r} is not in {manifest['polity']}"
                        break
                    box = [
                        x0 + (x1 - x0) * header[c]["x0"] / 1000,
                        y0 + (y1 - y0) * (start + (stop - start) * row["y0"] / 1000),
                        x0 + (x1 - x0) * header[c]["x1"] / 1000,
                        y0 + (y1 - y0) * (start + (stop - start) * row["y1"] / 1000),
                    ]
                    if (number, code, series) in values and values[(number, code, series)][0] != value:
                        errors[number] = f"{custom}: {label!r} read twice with different values"
                        break
                    values.setdefault((number, code, series), (value, box))
                else:
                    continue
                break
    return values, printed, errors


def evaluate(
    manifest: dict[str, object], reader: str, names: dict[str, str], held: dict[str, int | None]
) -> dict[str, dict[str, object]]:
    values, printed, errors = cells(manifest, requests(str(manifest["name"]), reader), names)
    series: dict[str, dict[str, object]] = {}
    for spec in manifest["pages"]:
        for index, column in enumerate(spec["columns"]):
            if column["contest"] is None:
                continue
            name = f"{column['contest']}|{column['candidate']}"
            entry = series.setdefault(
                name,
                {"sum": 0, "printed": None, "official": held.get(name), "pages": [], "errors": [], "cells": {}},
            )
            entry["pages"].append(int(spec["page"]))
            if int(spec["page"]) in errors:
                entry["errors"].append(errors[int(spec["page"])])
            if (int(spec["page"]), index) in printed:
                entry["printed"] = printed[(int(spec["page"]), index)]
            for (page, code, position), (value, box) in values.items():
                if page == int(spec["page"]) and position == index:
                    entry["sum"] += value
                    entry["cells"][(page, code)] = (value, box)
    for entry in series.values():
        total, official = entry["printed"], entry["official"]
        entry["status"] = (
            "failed"
            if entry["errors"]
            else "unchecked"
            if total is None and official is None
            else "tied"
            if entry["sum"] == total or (total is None and abs(entry["sum"] - official) <= max(50, official * 0.01))
            else "failed"
        )
    return series


def pending(name: str, reader: str, pages: set[int] | None) -> dict[str, dict[str, object]]:
    return {
        custom: request
        for custom, request in requests(name, reader).items()
        if (pages is None or request["page"] in pages) and cached(str(request["key"])) is None
    }


def submit(reader: str, wanted: dict[str, dict[str, object]], state: dict[str, object]) -> list[str]:
    api = client()
    groups: list[list[tuple[str, dict[str, object]]]] = [[]]
    size = 0
    for custom, request in wanted.items():
        weight = len(json.dumps(request["params"]))
        if groups[-1] and size + weight > BATCH_LIMIT:
            groups.append([])
            size = 0
        groups[-1].append((custom, request))
        size += weight
    ids: list[str] = []
    for group in groups:
        created = api.messages.batches.create(
            requests=[{"custom_id": custom, "params": request["params"]} for custom, request in group]
        )
        state.setdefault("batches", {}).setdefault(reader, []).append(
            {"id": created.id, "keys": {custom: request["key"] for custom, request in group}}
        )
        ids.append(created.id)
    return ids


def collect(reader: str, state: dict[str, object]) -> str:
    api = client()
    notes: list[str] = []
    remaining = []
    for batch in state["batches"][reader]:
        status = api.messages.batches.retrieve(batch["id"])
        if status.processing_status != "ended":
            notes.append(f"{batch['id']} {status.processing_status}, {status.request_counts.processing} processing")
            remaining.append(batch)
            continue
        for result in api.messages.batches.results(batch["id"]):
            if result.result.type == "succeeded":
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
    state["batches"][reader] = remaining
    return "; ".join(notes)


def official_totals(manifest: dict[str, object]) -> dict[str, int | None]:
    held: dict[str, int | None] = {}
    contests = dict(manifest["contests"])
    with connect() as conn:
        ids = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall())
        for spec in manifest["pages"]:
            for column in spec["columns"]:
                if column["contest"] is None:
                    continue
                name = f"{column['contest']}|{column['candidate']}"
                contest = ids.get(key_of(contests[column["contest"]]))
                words = [word for word in slug(column["candidate"]).split() if len(word) > 1]
                if "official" in column:
                    held[name] = column["official"]
                elif contest is None or not words:
                    held[name] = None
                else:
                    surname = words[0] if "," in column["candidate"] else words[-1]
                    found = [
                        total
                        for label, text, total in conn.execute(
                            """select o.ballot_label, coalesce(n.text, ''),
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
                        if total and surname in slug(f"{label or ''} {text}").split()
                    ]
                    held[name] = found[0] if len(found) == 1 else None
    return held


def prepare(name: str) -> str:
    manifest = load(name)
    folder = WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    held = official_totals(manifest)
    (folder / "official.json").write_text(json.dumps(held, indent=2))
    out = []
    for reader, (model, _, price_in, price_out) in READERS.items():
        plan = requests(name, reader)
        pixels = sum(min(int(request["pixels"]), 3_500_000) for request in plan.values())
        guess = (pixels / 750 * price_in + len(plan) * 6000 * price_out) / 1e6
        out.append(f"{reader} ({model}): {len(plan)} requests, about ${guess:.3f} if every page goes")
    unmatched = [column for column, total in held.items() if total is None]
    return "\n".join(out + [f"no official total held for {len(unmatched)} of {len(held)} columns", *unmatched[:20]])


def run(name: str) -> str:
    manifest = load(name)
    folder = WORK / name
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    state: dict[str, object] = json.loads(state_path.read_text()) if state_path.exists() else {}
    if not (folder / "official.json").exists():
        (folder / "official.json").write_text(json.dumps(official_totals(manifest), indent=2))
    held: dict[str, int | None] = json.loads((folder / "official.json").read_text())
    names = counties(str(manifest["polity"]))
    message = ""
    outcome: dict[str, dict[str, dict[str, object]] | None] = {"first": None, "second": None}
    redo: set[int] | None = None
    for reader in ("first", "second"):
        if reader == "second":
            redo = {
                page
                for entry in (outcome["first"] or {}).values()
                if entry["status"] != "tied"
                for page in entry["pages"]
            }
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
        outcome[reader] = evaluate(manifest, reader, names, held)
    return finalize(manifest, folder, outcome["first"] or {}, outcome["second"], held)


def finalize(
    manifest: dict[str, object],
    folder: Path,
    first: dict[str, dict[str, object]],
    second: dict[str, dict[str, object]] | None,
    held: dict[str, int | None],
) -> str:
    overrides = json.loads((folder / "overrides.json").read_text()) if (folder / "overrides.json").exists() else {}
    accepted: dict[str, str] = overrides.get("accept", {})
    columns = {
        f"{column['contest']}|{column['candidate']}": column
        for spec in manifest["pages"]
        for column in spec["columns"]
        if column["contest"] is not None
    }
    passed: list[dict[str, object]] = []
    review: list[dict[str, object]] = []
    for name, one in first.items():
        two = second[name] if second is not None and one["status"] != "tied" else None
        chosen, how = one, "first"
        if one["status"] != "tied":
            if two is not None and two["status"] == "tied":
                chosen, how = two, "second"
            elif (
                two is not None
                and one["status"] == "unchecked"
                and not one["errors"]
                and not two["errors"]
                and {k: v[0] for k, v in one["cells"].items()} == {k: v[0] for k, v in two["cells"].items()}
            ):
                chosen, how = two, "agreed"
            else:
                chosen, how = two or one, "review"
        cellmap = {key: list(value) for key, value in chosen["cells"].items()}
        fixes = [item for item in overrides.get("cells", []) if item["series"] == name]
        if how == "review" and fixes:
            for item in fixes:
                if (int(item["page"]), str(item["county"])) in cellmap:
                    cellmap[(int(item["page"]), str(item["county"]))][0] = int(item["value"])
            total = sum(value for value, _ in cellmap.values())
            target = chosen["printed"] if chosen["printed"] is not None else held.get(name)
            slack = 0 if chosen["printed"] is not None else max(50, (target or 0) * 0.01)
            if target is not None and abs(total - target) <= slack:
                how = "settled"
        if how == "review" and name in accepted:
            how = "accepted"
        if how == "review":
            differing = {
                f"{page}:{code}"
                for page, code in set(one["cells"]) | set(two["cells"] if two else {})
                if one["cells"].get((page, code), (None,))[0]
                != (two["cells"] if two else {}).get((page, code), (None,))[0]
            }
            review.append(
                {
                    "series": name,
                    "pages": chosen["pages"],
                    "first": {"sum": one["sum"], "status": one["status"], "errors": one["errors"]},
                    "second": None
                    if two is None
                    else {"sum": two["sum"], "status": two["status"], "errors": two["errors"]},
                    "printed": chosen["printed"],
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
                "writein": bool(columns[name].get("writein", False)),
                "page": page,
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
        seen: dict[int, tuple[int, int]] = {}
        for request in requests(str(manifest["name"]), reader).values():
            response = cached(str(request["key"]))
            if response is not None:
                before = seen.get(int(request["page"]), (0, 0))
                seen[int(request["page"])] = (
                    before[0] + response["usage"]["input_tokens"],
                    before[1] + response["usage"]["output_tokens"],
                )
        if seen:
            dollars = sum(i * price_in + o * price_out for i, o in seen.values()) / 1e6
            lines += [
                f"## {reader} ({model}): {len(seen)} pages, ${dollars:.4f}, ${dollars / len(seen):.4f} per page",
                "",
            ]
            lines += [f"- p{page}: {i} in, {o} out" for page, (i, o) in sorted(seen.items())] + [""]
    counts: dict[tuple[str, str], int] = {}
    for row in passed:
        counts[(str(row["series"]), str(row["how"]))] = counts.get((str(row["series"]), str(row["how"])), 0) + 1
    lines += ["## passed", "", *[f"- {series}: {how}, {count} cells" for (series, how), count in counts.items()]]
    lines += ["", "## review", ""]
    lines += [
        f"- {item['series']}: first {item['first']['sum']}, second {item['second'] and item['second']['sum']}, "
        f"printed {item['printed']}, official {item['official']}"
        for item in review
    ]
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
    return "\n".join(out)


def emit(name: str) -> Path:
    manifest = load(name)
    contests = dict(manifest["contests"])
    frame = pl.read_parquet(WORK / name / "tables.parquet")
    frame = frame.with_columns(
        **{
            field: pl.col("contest").replace_strict({key: value[field] for key, value in contests.items()})
            for field in ("year", "chamber", "day", "special", "state", "district")
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
            polity=str(manifest["polity"]),
        )
        source = cs.source(str(manifest["publisher"]), str(manifest["title"]))
        cs.add(
            "source",
            id=source,
            kind=manifest.get("kind", "official_body"),
            publisher=manifest["publisher"],
            title=manifest["title"],
            url=dict(manifest["file"])["url"],
            license=manifest.get("license", "Public record"),
        )
        stored = artifact(manifest)
        file = cs.artifact(stored, source)
        PAGES.mkdir(parents=True, exist_ok=True)
        pages: dict[int, str] = {}
        specs = {int(spec["page"]): spec for spec in manifest["pages"]}
        for number in sorted(set(frame["page"])):
            image = PAGES / f"{stored.sha256}-{number}.png"
            if not image.exists():
                render(manifest, specs[number], 100).save(image)
            width, height = Image.open(image).size
            page = cs.page(file, number)
            pages[number] = str(page)
            cs.add(
                "source_page",
                id=page,
                artifact_id=file,
                page_number=number,
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
                + (f" {caveats[found[0]]}" if found is not None and found[0] in caveats else ""),
            )
        return cs.write()
