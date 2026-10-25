import base64
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages import MessageBatchIndividualResponse
from anthropic.types.messages.batch_create_params import Request
from PIL import Image

from ea import raw, scans
from ea.sources.gb import ni_council
from ea.store import DATA

SOURCE = "gb.ni_scans"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
STATE = PARSED / "state.json"
SHEETS = json.loads((Path(__file__).parent / "ni_scans_sheets.json").read_text())
MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5")
STOP = 4.0
PROMPT = """This is a Northern Ireland local council election count sheet (single transferable vote); the pages are in order. Transcribe it exactly as printed and answer with one JSON object and nothing else:
{"dea": "<printed district electoral area>", "seats": n, "electorate": n, "polled": n, "valid": n, "invalid": n, "quota": n,
 "candidates": [{"name": "<name as printed>", "description": "<printed party description, or empty>", "elected": true or false,
   "stages": [<first preference>, <Result column of stage 2>, <Result column of stage 3>, ...], "page": <page number>, "bbox": [x0, y0, x1, y1]}],
 "non_transferable": [0, <Non-transferable Result column of stage 2>, <stage 3>, ...]}
Candidates are the numbered rows. "elected" is true when the row is marked elected. Take only the running Result (total) column of each stage, never the transfer column. A dash, dot, or blank in a Result cell means 0. Keep numbers as printed, with decimals and without thousands separators. The bbox is the whole candidate row on its page, as integers from 0 to 1000 across the width and height, x0 and y0 at the top left. Do not infer or correct anything."""


def targets() -> dict[str, dict[str, str]]:
    areas = ni_council.area_table()
    units = pl.read_parquet(ni_council.PARSED / "units.parquet")
    found = {}
    for label, url in SHEETS["2019"]:
        found[f"2019:{ni_council.squeeze(label)}"] = {"year": "2019", "label": label, "url": url}
    held = set(units.filter(pl.col("year") == 2014)["dea_code"])
    for label, url in SHEETS["2014"]:
        key = ni_council.squeeze(re.sub(r"\s*\(XLS\)|[-\s]*results.*$", "", label, flags=re.IGNORECASE))
        if key in areas and areas[key][0] not in held:
            found[f"2014:{key}"] = {"year": "2014", "label": label, "url": url}
    held = {row["dea_code"]: row for row in units.filter(pl.col("year") == 2023).iter_rows(named=True)}
    sheets = [f"{ni_council.EONI}{path}" for path in ni_council.links(ni_council.PAGE_2023, r'href="(/media/[^"]+\.pdf)"') if "result-sheet" in path]
    for key, (code, name, _) in areas.items():
        row = held.get(code)
        if row is not None:
            if row["source_url"].endswith(".pdf") and ("does not add up" in (row["note"] or "") or "stops before" in (row["note"] or "")):
                found[f"2023:{key}"] = {"year": "2023", "label": name, "url": row["source_url"]}
            continue
        urls = [url for url in sheets if key in ni_council.squeeze(url.rsplit("/", 1)[-1])]
        if len(urls) == 1:
            found[f"2023:{key}"] = {"year": "2023", "label": name, "url": urls[0]}
    return found


def document(url: str) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        return raw.cached(ni_council.SOURCE, url)


def fetch() -> None:
    for target in targets().values():
        try:
            document(target["url"])
            continue
        except LookupError:
            pass
        for attempt in range(1, 6):
            time.sleep(4)
            try:
                raw.fetch(SOURCE, target["url"], suffix=".pdf")
                break
            except Exception as error:
                print(target["label"], attempt, repr(error)[:100])
                time.sleep(60 * attempt)


def grid(url: str) -> str | None:
    path = document(url).path
    if path.read_bytes()[:2] not in (b"\xd0\xcf", b"PK"):
        return None
    sheet = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0)
    return "\n".join("\t".join(cell or "" for cell in row).rstrip() for row in sheet.iter_rows() if any(row))


def images(url: str) -> list[tuple[Path, int, int]]:
    pdf = document(url)
    folder = PAGES / re.sub(r"\W+", "-", url)[-80:]
    if not folder.exists():
        folder.mkdir(parents=True)
        subprocess.run(["pdftoppm", "-r", "150", "-gray", "-png", str(pdf.path), str(folder / "p")], check=True)
        for path in sorted(folder.glob("p-*.png")):
            image = Image.open(path)
            if image.height > image.width * 1.1:
                image.rotate(-90, expand=True).save(path)
    return [(path, *Image.open(path).size) for path in sorted(folder.glob("p-*.png"))]


def state() -> dict[str, object]:
    return json.loads(STATE.read_text()) if STATE.exists() else {"batches": [], "reads": {}}


def submit() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    current = state()
    wanted = targets()
    books = scans.ledger()
    api = scans.client()
    for position, model in enumerate(MODELS):
        todo = [
            key
            for key in wanted
            if f"{model}:{key}" not in current["reads"]
            and (position == 0 or current["reads"].get(f"{MODELS[position - 1]}:{key}") is False)
            and not any(key in batch["keys"].values() and batch["model"] == model for batch in current["batches"])
        ]
        if not todo:
            continue
        requests, keys, projected = [], {}, 0.0
        price_in, price_out = scans.PRICES[model]
        for key in todo:
            content: list[dict[str, object]] = []
            text = grid(wanted[key]["url"])
            if text is not None:
                content.append({"type": "text", "text": f"Spreadsheet, tab separated, page 1:\n{text}"})
            try:
                pages = [] if text is not None else images(wanted[key]["url"])
            except subprocess.CalledProcessError:
                print("unreadable PDF", key)
                continue
            for number, (path, _, _) in enumerate(pages, start=1):
                content.append({"type": "text", "text": f"Page {number}"})
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(path.read_bytes()).decode()}})
            content.append({"type": "text", "text": PROMPT})
            custom = re.sub(r"[^A-Za-z0-9_-]", "-", key)
            keys[custom] = key
            projected += (len(content) / 2 * 1800 * price_in + 3500 * price_out) / 1e6
            requests.append(Request(custom_id=custom, params=MessageCreateParamsNonStreaming(model=model, max_tokens=16000, thinking={"type": "between_tools"}, messages=[{"role": "user", "content": content}])))
        total = scans.committed(books)
        if projected > STOP or total + projected > scans.BUDGET:
            raise SystemExit(f"BLOCKED: {model} projects ${projected:.2f}; ledger ${total:.2f}; stop ${STOP}")
        created = api.messages.batches.create(requests=requests)
        current["batches"].append({"id": created.id, "model": model, "keys": keys})
        books["pending"][created.id] = projected
        books.setdefault("when", {})[created.id] = time.time()
        scans.LEDGER.write_text(json.dumps(books, indent=2))
        STATE.write_text(json.dumps(current))
        print(f"{created.id} {model}: {len(requests)} requests, projected ${projected:.2f}")


def collect() -> None:
    current = state()
    api = scans.client()
    remaining = []
    for batch in current["batches"]:
        status = api.messages.batches.retrieve(batch["id"])
        if status.processing_status != "ended":
            print(batch["id"], status.processing_status)
            remaining.append(batch)
            continue
        books = scans.ledger()
        spent = 0.0
        for line in api.get(f"/v1/messages/batches/{batch['id']}/results", cast_to=str, options={"headers": {"Accept": "application/binary"}}).splitlines():
            result = MessageBatchIndividualResponse.model_validate_json(line)
            key = f"{batch['model']}:{batch['keys'][result.custom_id]}"
            if result.result.type != "succeeded":
                print(key, result.result.type)
                continue
            message = result.result.message
            spent += scans.dollars(message, 1.0)
            raw.store(SOURCE, f"read:{key}", message.model_dump_json().encode(), suffix=".json", retrieved_at=datetime.now(UTC), media_type="application/json")
            current["reads"][key] = False
        books["pending"].pop(batch["id"], None)
        books["spent"][batch["id"]] = spent
        scans.LEDGER.write_text(json.dumps(books, indent=2))
        print(batch["id"], f"ended, ${spent:.3f}")
    current["batches"] = remaining
    STATE.write_text(json.dumps(current))


def surname_first(printed: str) -> str:
    name = re.sub(r"^[,\s]+", "", printed.strip())
    if "," in name:
        return name
    words = name.split()
    lead = 0
    while lead < len(words) - 1 and len(words[lead]) > 2 and re.search(r"[A-Z]{2}", words[lead]):
        lead += 1
    return f"{' '.join(words[:lead])}, {' '.join(words[lead:])}" if lead else name


def read(model: str, key: str, target: dict[str, str], areas: dict[str, tuple[str, str, str]]) -> tuple[dict[str, object], list[dict[str, object]]]:
    message = json.loads(raw.cached(SOURCE, f"read:{model}:{key}").path.read_text())
    text = "".join(block["text"] for block in message["content"] if block["type"] == "text")
    sheet = json.loads(text[text.index("{") : text.rindex("}") + 1])
    sizes = [] if grid(target["url"]) is not None else images(target["url"])
    candidates = [
        {
            "name": surname_first(candidate["name"]),
            "party": (candidate["description"] or "").strip(),
            "elected": bool(candidate["elected"]),
            "totals": [float(value or 0) for value in candidate["stages"]],
        }
        for candidate in sheet["candidates"]
    ]
    width = min(len(candidate["totals"]) for candidate in candidates)
    for candidate in candidates:
        candidate["totals"] = candidate["totals"][:width]
    exhausted = ([float(value or 0) for value in sheet["non_transferable"]] + [0.0] * width)[:width]
    valid = float(str(sheet["valid"]).replace(",", ""))
    sums = {stage: sum(candidate["totals"][stage - 1] for candidate in candidates) + exhausted[stage - 1] for stage in range(1, width + 1)}
    tied = ni_council.tie(sums, valid, len(candidates))
    if not tied:
        raise ValueError(f"stage 1 sums to {sums[1]} against {valid} valid")
    for candidate in candidates:
        candidate["totals"] = candidate["totals"][: len(tied)]
    facts = {name: float(str(sheet[name]).replace(",", "")) for name in ("seats", "electorate", "polled", "valid", "invalid", "quota") if sheet.get(name) is not None}
    unit, rows = ni_council.finish(int(target["year"]), re.sub(r"\s*\(XLS\)", "", target["label"]), facts, candidates, exhausted[: len(tied)], width, target["url"], areas)
    for row in rows:
        row["page"] = row["x0"] = row["y0"] = row["x1"] = row["y1"] = None
        if row["number"] > 0 and sizes:
            located = sheet["candidates"][row["number"] - 1]
            page = int(located.get("page") or 1)
            _, pixel_width, pixel_height = sizes[min(page, len(sizes)) - 1]
            x0, y0, x1, y1 = located["bbox"]
            row |= {"page": page, "x0": x0 / 1000 * pixel_width, "y0": y0 / 1000 * pixel_height, "x1": x1 / 1000 * pixel_width, "y1": y1 / 1000 * pixel_height}
    if sizes:
        unit["note"] = " ".join(filter(None, [unit["note"], "Each value's box is the candidate's whole row on the scanned sheet, the same for every stage; the model read rows, not cells."]))
    unit["reader"] = model
    return unit, rows


def parse() -> None:
    current = state()
    wanted = targets()
    areas = ni_council.area_table()
    units, totals, failed = [], [], []
    for key, target in wanted.items():
        for model in MODELS:
            if f"{model}:{key}" not in current["reads"]:
                continue
            try:
                unit, rows = read(model, key, target, areas)
            except (ValueError, KeyError, IndexError, TypeError) as error:
                failed.append(f"{model}:{key}: {error}")
                current["reads"][f"{model}:{key}"] = False
                continue
            current["reads"][f"{model}:{key}"] = True
            units.append(unit)
            totals.extend(rows)
            break
    STATE.write_text(json.dumps(current))
    pl.DataFrame(units, infer_schema_length=None).write_parquet(PARSED / "units.parquet")
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(PARSED / "totals.parquet")
    print(f"{len(units)} of {len(wanted)} sheets tie")
    for line in failed:
        print(line)
