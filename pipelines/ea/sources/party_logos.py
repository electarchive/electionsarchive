import csv
import hashlib
import io
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urlencode
from uuid import uuid5

import httpx
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.raw import Artifact
from ea.sources.wikidata_photos import _credit, _free, _text
from ea.store import DATA, connect

SOURCE = "party_logos"
VERSION = "5"
TITLE = "Party logos"
LOGOS = DATA / "logos"
ITEMS = DATA / "parsed" / SOURCE / "items.json"
DATED = Path(__file__).with_suffix(".tsv")
STATEMENTS = """SELECT ?item ?statement ?file ?rank ?inception ?inceptionPrecision ?start ?startPrecision ?end ?endPrecision ?began ?ended ?dissolved WHERE {{
  VALUES ?item {{ {items} }}
  ?item p:P154 ?statement . ?statement ps:P154 ?file ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL {{ ?statement pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }}
  OPTIONAL {{ ?statement pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }}
  OPTIONAL {{ ?statement pq:P580 ?began }}
  OPTIONAL {{ ?statement pq:P582 ?ended }}
  OPTIONAL {{ ?item wdt:P576 ?dissolved }}
  OPTIONAL {{ ?item p:P571/psv:P571 [ wikibase:timeValue ?inception ; wikibase:timePrecision ?inceptionPrecision ] }}
}}"""
SCOPE = "select x.value from external_id x join party p on p.id = x.entity_id where x.scheme = 'wikidata'"
CHUNK = 300
TITLES = 50
HEIGHT = 96
MARK = 192
LOCKUP = 96
PAD = 0.08
LONGEST = 5
WORDMARK = 1.7
PAPER = (255, 255, 255, 255)
INK = (17, 19, 21)
REVIEW = DATA / "review" / SOURCE
PRECISIONS = {11: "day", 10: "month", 9: "year"}
UNDATED: tuple[date | None, str] = (None, "unknown")
EMBLEMS = re.compile(r"cockade|\bflag\b|bandeira|bandiera|drapeau|banner", re.IGNORECASE)
NOT_LOGOS = {
    ("Q2453869", "Logo PSTU.png"),
    ("Q108700", "A-Block-for-the-Wigs-Gillray.jpeg"),
    ("Q6517143", "Newhome masthead.gif"),
    ("Q42183", "1848whigbanner.jpg"),
    ("Q2653287", "Bandeira-democracia-cristã.png"),
}


@dataclass
class Logo:
    statement: str | None
    file: str
    dissolved: bool
    start: tuple[date | None, str] = UNDATED
    end: tuple[date | None, str] = UNDATED
    dated_by: str | None = None
    preferred: bool = False
    open: bool = True
    default: bool = False
    dated_url: str | None = None
    inception: tuple[date | None, str] = UNDATED


def _items() -> list[str]:
    return json.loads(ITEMS.read_text(encoding="utf-8"))


def _sparql(items: list[str]) -> list[str]:
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": STATEMENTS.format(items=" ".join(f"wd:{item}" for item in items[start : start + CHUNK])),
                "format": "json",
            }
        )
        for start in range(0, len(items), CHUNK)
    ]


def _commons(files: list[str]) -> list[str]:
    return [
        "https://commons.wikimedia.org/w/api.php?"
        + urlencode(
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
                "prop": "imageinfo",
                "iiprop": "timestamp|url|extmetadata",
                "iiurlheight": str(HEIGHT * 2),
                "iiextmetadatafilter": "License|LicenseShortName|Artist|Credit|ImageDescription",
                "titles": "|".join(f"File:{file}" for file in files[start : start + TITLES]),
            }
        )
        for start in range(0, len(files), TITLES)
    ]


def _get(url: str, suffix: str, pause: float) -> Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    for _ in range(3):
        try:
            artifact = raw.fetch(SOURCE, url, suffix=suffix)
        except httpx.TransportError as error:
            print(f"{error!r}, waiting 60s")
            time.sleep(60)
            continue
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in (429, 503):
                if error.response.status_code < 500:
                    raise
                break
            wait = int(error.response.headers.get("retry-after", "60"))
            print(f"{error.response.status_code} from {error.request.url.host}, waiting {wait}s")
            time.sleep(wait)
            continue
        time.sleep(pause)
        return artifact
    print(f"skipped {url}")
    return None


def _when(value: str | None, precision: str | None) -> tuple[date | None, str]:
    if value is None or precision is None or int(precision) not in PRECISIONS:
        return None, "unknown"
    found = re.match(r"(\d{4})-(\d\d)-(\d\d)", value)
    if found is None:
        return None, "unknown"
    kind = PRECISIONS[int(precision)]
    year, month, day = int(found[1]), int(found[2]), int(found[3])
    return date(year, month if kind != "year" else 1, day if kind == "day" else 1), kind


def _statements(items: list[str]) -> dict[str, list[Logo]]:
    parties: dict[str, dict[str, Logo]] = {}
    founded: dict[str, tuple[date | None, str]] = {}
    for url in _sparql(items):
        for row in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]:
            item = row["item"]["value"].rsplit("/", 1)[1]
            statement = row["statement"]["value"].rsplit("/", 1)[1]
            file = unquote(row["file"]["value"].rsplit("/", 1)[1]).replace("_", " ")
            if (item, file) in NOT_LOGOS or EMBLEMS.search(file):
                continue
            began = _when(row.get("inception", {}).get("value"), row.get("inceptionPrecision", {}).get("value"))
            if began[0] is not None and (
                founded.get(item, UNDATED)[0] is None or str(began[0]) < str(founded[item][0])
            ):
                founded[item] = began
            begun = _when(row.get("start", {}).get("value"), row.get("startPrecision", {}).get("value"))
            ended = _when(row.get("end", {}).get("value"), row.get("endPrecision", {}).get("value"))
            if any(row.get(key, {}).get("value", "").startswith("http") for key in ("began", "ended")):
                begun = ended = UNDATED
            parties.setdefault(item, {})[statement] = Logo(
                statement=statement,
                file=file,
                dissolved="dissolved" in row,
                start=begun,
                end=ended,
                dated_by="wikidata" if begun[0] is not None or ended[0] is not None else None,
                preferred=row["rank"]["value"].endswith("PreferredRank"),
                open="ended" not in row,
            )
    for item, statements in parties.items():
        logos = list(statements.values())
        for logo in logos:
            logo.inception = founded.get(item, UNDATED)
        preferred = [logo for logo in logos if logo.preferred]
        unended = [logo for logo in logos if logo.open]
        chosen = (
            preferred if len(preferred) == 1 else unended if len(unended) == 1 else logos if len(logos) == 1 else []
        )
        for logo in chosen:
            logo.default = True
    return {item: list(statements.values()) for item, statements in parties.items()}


def _dated() -> list[dict[str, str]]:
    with DATED.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _infos(files: list[str]) -> dict[str, dict[str, str]]:
    infos: dict[str, dict[str, str]] = {}
    for url in _commons(files):
        query = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["query"]
        renamed = {entry["from"]: entry["to"] for entry in [*query.get("normalized", []), *query.get("redirects", [])]}
        pages = {page["title"]: page for page in query["pages"]}
        for title in re.findall(r"File:([^|]+)", dict(httpx.URL(url).params)["titles"]):
            resolved = f"File:{title}"
            while resolved in renamed:
                resolved = renamed[resolved]
            page = pages.get(resolved)
            if page is None or "imageinfo" not in page:
                continue
            info = page["imageinfo"][0]
            meta = {key: str(value["value"]) for key, value in (info.get("extmetadata") or {}).items()}
            infos[title] = {
                "thumb": info.get("thumburl", info["url"]),
                "page": info["descriptionurl"],
                "code": meta.get("License", "").lower(),
                "license": _text(meta.get("LicenseShortName", "")),
                "artist": _credit(meta.get("Artist", "")) or _credit(meta.get("Credit", "")),
                "description": _text(meta.get("ImageDescription", "")),
                "uploaded": info.get("timestamp", ""),
            }
    return infos


def _logos(items: list[str]) -> dict[str, list[Logo]]:
    parties = _statements(items)
    for row in _dated():
        logos = parties.setdefault(row["item"], [])
        match = next((logo for logo in logos if logo.file == row["file"]), None)
        if match is None:
            match = Logo(statement=None, file=row["file"], dissolved=any(logo.dissolved for logo in logos))
            logos.append(match)
        if match.dated_by == "wikidata":
            continue
        match.start = (date.fromisoformat(row["valid_from"]) if row["valid_from"] else None, row["from_precision"])
        match.end = (date.fromisoformat(row["valid_to"]) if row["valid_to"] else None, row["to_precision"])
        match.dated_by = "source" if row["url"] else "file"
        match.dated_url = row["url"] or None
    for logos in parties.values():
        starts = sorted((logo.start for logo in logos if logo.start[0] is not None), key=lambda begun: str(begun[0]))
        for logo in logos:
            if logo.start[0] is None or logo.end[0] is not None:
                continue
            following = next((begun for begun in starts if str(begun[0]) > str(logo.start[0])), None)
            if following is not None:
                logo.end = following
                logo.dated_by = "next_logo"
        for logo in logos:
            if logo.default and logo.dated_by is None and logo.inception[0] is not None:
                logo.start = logo.inception
                logo.dated_by = "inception"
    return parties


def _oklab(rgb: np.ndarray) -> np.ndarray:
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    lms = np.cbrt(
        linear
        @ np.array(
            [
                [0.4122214708, 0.2119034982, 0.0883024619],
                [0.5363325363, 0.6806995451, 0.2817188376],
                [0.0514459929, 0.1073969566, 0.6299787005],
            ]
        )
    )
    return lms @ np.array(
        [
            [0.2104542553, 1.9779984951, 0.0259040371],
            [0.793617785, -2.428592205, 0.7827717662],
            [-0.0040720468, 0.4505937099, -0.808675766],
        ]
    )


def _beside(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1)
    return padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:]


def _clean(image: Image.Image) -> tuple[np.ndarray, bool] | None:
    pixels = np.asarray(image.convert("RGBA"), dtype=np.float64) / 255
    ink = pixels[..., 3] > 0.03
    if not ink.any():
        return None
    rows = np.flatnonzero(ink.any(axis=1))
    cols = np.flatnonzero(ink.any(axis=0))
    border = np.zeros(ink.shape, dtype=bool)
    border[[rows[0], rows[-1]], cols[0] : cols[-1] + 1] = True
    border[rows[0] : rows[-1] + 1, [cols[0], cols[-1]]] = True
    plate = False
    solid = border & (pixels[..., 3] > 0.5)
    if solid.sum() > 0.85 * border.sum():
        lab = _oklab(pixels[..., :3])
        field = np.median(lab[solid], axis=0)
        near = np.linalg.norm(lab - field, axis=-1)
        uniform = (near[solid] < 0.06).mean() >= 0.75
        if uniform and field[0] >= 0.9 and np.hypot(field[1], field[2]) <= 0.035:
            region = solid & (near < 0.08)
            while True:
                grown = (near < 0.08) & (region | _beside(region))
                if (grown == region).all():
                    break
                region = grown
            keep = np.where(region, 0.0, np.where(_beside(region), np.clip((near - 0.02) / 0.2, 0, 1), 1.0))
            white = np.median(pixels[solid][:, :3], axis=0)
            rgb = (pixels[..., :3] - (1 - keep)[..., None] * white) / np.maximum(keep, 1e-6)[..., None]
            pixels = np.dstack([np.clip(rgb, 0, 1), pixels[..., 3] * keep])
            ink = pixels[..., 3] > 0.03
            if not ink.any():
                return None
            rows = np.flatnonzero(ink.any(axis=1))
            cols = np.flatnonzero(ink.any(axis=0))
        else:
            plate = bool(uniform)
    return pixels[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1], plate


def _place(pixels: np.ndarray, plate: bool, scale: float, size: tuple[int, int], shift: float) -> Image.Image:
    height, width = pixels.shape[:2]
    art = (
        Image.fromarray(np.round(pixels * 255).astype(np.uint8), "RGBA")
        .convert("RGBa")
        .resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
        .convert("RGBA")
    )
    if plate:
        mask = Image.new("L", (art.width * 4, art.height * 4), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, mask.width - 1, mask.height - 1), radius=round(min(mask.size) * 0.16), fill=255
        )
        art.putalpha(ImageChops.multiply(art.getchannel("A"), mask.resize(art.size, Image.Resampling.LANCZOS)))
    alpha = np.asarray(art.getchannel("A"), dtype=np.float64)
    mass = alpha.sum() or 1.0
    lean = (
        (alpha.sum(axis=0) @ np.arange(art.width)) / mass - (art.width - 1) / 2,
        (alpha.sum(axis=1) @ np.arange(art.height)) / mass - (art.height - 1) / 2,
    )
    margin = PAD * min(size)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(
        art,
        (
            round((size[0] - art.width) / 2 - float(np.clip(lean[0] * shift, -margin / 2, margin / 2))),
            round((size[1] - art.height) / 2 - float(np.clip(lean[1] * shift, -margin / 2, margin / 2))),
        ),
    )
    return canvas


def _renditions(image: Image.Image) -> tuple[Image.Image, Image.Image, str, bool, bool] | None:
    cleaned = _clean(image)
    if cleaned is None:
        return None
    pixels, plate = cleaned
    light = False
    for _ in range(2):
        height, width = pixels.shape[:2]
        weight = width * height * float(np.sqrt(pixels[..., 3].mean()))
        inner = MARK * (1 - 2 * PAD)
        mark = _place(pixels, plate, min(inner / max(width, height), 0.94 * inner / np.sqrt(weight)), (MARK, MARK), 0.5)
        if plate or light or not _light(mark):
            break
        light = plate = True
        margin = round(0.14 * min(height, width))
        tile = np.ones((height + 2 * margin, width + 2 * margin, 4))
        tile[..., :3] = np.array(INK) / 255
        alpha = pixels[..., 3:]
        inside = tile[margin : margin + height, margin : margin + width]
        inside[..., :3] = pixels[..., :3] * alpha + inside[..., :3] * (1 - alpha)
        pixels = tile
    tall = LOCKUP * (1 - 2 * PAD)
    scale = min(tall / height, LONGEST * tall / width, 0.94 * np.sqrt(3) * tall / np.sqrt(weight))
    lockup = _place(pixels, plate, scale, (round(width * scale + 2 * PAD * LOCKUP), LOCKUP), 0.5)
    return mark, lockup, "wordmark" if width / height > WORDMARK else "mark", plate, light


def _light(image: Image.Image) -> bool:
    pixels = np.asarray(image, dtype=np.float64) / 255
    lab = _oklab(pixels[..., :3])
    bright = (lab[..., 0] >= 0.92) & (np.hypot(lab[..., 1], lab[..., 2]) <= 0.04)
    opaque = pixels[..., 3] >= 0.5
    edge = opaque & _beside(~opaque)
    return (
        bool(opaque.any())
        and bool(edge.any())
        and float(bright[edge].mean()) > 0.6
        and float(bright[opaque].mean()) > 0.6
    )


def _sheet(entries: list[tuple[str, Image.Image, Image.Image, str, bool, bool]]) -> list[Path]:
    REVIEW.mkdir(parents=True, exist_ok=True)
    for old in REVIEW.glob("sheet-*.png"):
        old.unlink()
    font = ImageFont.load_default(size=11)
    columns, rows, cell = 4, 16, (360, 132)
    pages = []
    for start in range(0, len(entries), columns * rows):
        page = Image.new("RGBA", (columns * cell[0], rows * cell[1]), PAPER)
        draw = ImageDraw.Draw(page)
        for index, (label, mark, lockup, shape, plate, light) in enumerate(entries[start : start + columns * rows]):
            x = (index % columns) * cell[0] + 12
            y = (index // columns) * cell[1] + 12
            draw.rectangle((x - 1, y - 1, x + MARK // 2, y + MARK // 2), outline=(229, 229, 229, 255))
            page.alpha_composite(mark.resize((MARK // 2, MARK // 2), Image.Resampling.LANCZOS), (x, y))
            wide = lockup.resize((lockup.width // 2, LOCKUP // 2), Image.Resampling.LANCZOS)
            draw.rectangle(
                (x + 111, y - 1, x + 112 + min(wide.width, 224), y + LOCKUP // 2), outline=(229, 229, 229, 255)
            )
            page.alpha_composite(wide.crop((0, 0, min(wide.width, 224), wide.height)), (x + 112, y))
            draw.text(
                (x + 112, y + 58),
                f"{shape}{', plate' if plate else ''}{', light' if light else ''}",
                fill=(110, 110, 110, 255),
                font=font,
            )
            draw.text((x, y + 102), label[:52], fill=(20, 20, 20, 255), font=font)
        path = REVIEW / f"sheet-{start // (columns * rows) + 1:02d}.png"
        page.convert("RGB").save(path)
        pages.append(path)
    return pages


def fetch() -> None:
    with connect() as conn:
        items = sorted((value for (value,) in conn.execute(SCOPE).fetchall()), key=lambda item: int(item[1:]))
    ITEMS.parent.mkdir(parents=True, exist_ok=True)
    ITEMS.write_text(json.dumps(items), encoding="utf-8")
    print(f"{len(items)} parties with Wikidata items in scope")
    for url in _sparql(items):
        _get(url, ".json", 2)
    parties = _logos(items)
    files = sorted({logo.file for logos in parties.values() for logo in logos})
    print(f"{len(parties)} parties with {len(files)} logo files")
    for url in _commons(files):
        _get(url, ".json", 1)
    infos = _infos(files)
    kept = [title for title in files if title in infos and _free(infos[title])]
    print(f"{len(kept)} freely licensed logos to fetch")
    for title in kept:
        _get(infos[title]["thumb"], Path(infos[title]["thumb"]).suffix.lower(), 1)


def emit() -> Path:
    items = _items()
    parties = _logos(items)
    infos = _infos(sorted({logo.file for logos in parties.values() for logo in logos}))
    skipped: Counter[str] = Counter()
    with connect() as conn:
        known = {
            value: entity
            for value, entity in conn.execute(
                "select x.value, x.entity_id from external_id x join party p on p.id = x.entity_id "
                "where x.scheme = 'wikidata'"
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Party logos from Wikimedia Commons for every party with a Wikidata logo, limited to public domain, "
                "CC0, CC BY, and CC BY-SA files, trimmed and cleared of white fields, as a 96-pixel-high WebP lockup "
                "and a 192-pixel square mark, dated from Wikidata's start and end qualifiers or from what the file "
                "itself states"
            ),
            polity=None,
        )
        cs.manifest["scope"] = {"polity": None, "sweeps": ["party_image"]}
        source = cs.source("Wikimedia Commons", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikimedia Commons",
            title=TITLE,
            url="https://commons.wikimedia.org/",
            license="Public domain, CC0, CC BY, or CC BY-SA, per file",
            license_url="https://commons.wikimedia.org/wiki/Commons:Licensing",
            attribution="Each logo credits its author and license as its Wikimedia Commons file page gives them",
        )
        for url in _sparql(items) + _commons(sorted({logo.file for logos in parties.values() for logo in logos})):
            cs.artifact(raw.cached(SOURCE, url), source)
        LOGOS.mkdir(parents=True, exist_ok=True)
        added = 0
        thumbs: set[str] = set()
        sheet: list[tuple[str, Image.Image, Image.Image, str, bool, bool]] = []
        for item, logos in parties.items():
            if item not in known:
                continue
            for logo in logos:
                if logo.dated_by is None and not logo.default:
                    skipped["undated and not the default"] += 1
                    continue
                info = infos.get(logo.file)
                if info is None:
                    skipped["missing on Commons"] += 1
                    continue
                if not _free(info):
                    skipped[f"license {info['license'] or info['code'] or 'none'}"] += 1
                    continue
                try:
                    artifact = raw.cached(SOURCE, info["thumb"])
                except LookupError:
                    skipped["not fetched"] += 1
                    continue
                if info["thumb"] not in thumbs:
                    cs.artifact(artifact, source)
                    thumbs.add(info["thumb"])
                made = _renditions(Image.open(artifact.path))
                if made is None:
                    skipped["blank"] += 1
                    continue
                mark, lockup, shape, plate, light = made
                names = []
                for rendition in (lockup, mark):
                    buffer = io.BytesIO()
                    rendition.save(buffer, "WEBP", quality=90, method=6)
                    content = buffer.getvalue()
                    names.append(f"{hashlib.sha256(content).hexdigest()}.webp")
                    (LOGOS / names[-1]).write_bytes(content)
                sheet.append((f"{item} {logo.file}", mark, lockup, shape, plate, light))
                party = known[item]
                cs.add(
                    "party_image",
                    id=uuid5(NAMESPACE, f"{SOURCE}.image:{logo.statement or f'{item}:{logo.file}'}"),
                    party_id=cs.survivors.get(party, party),
                    path=f"logos/{names[0]}",
                    width=lockup.width,
                    height=lockup.height,
                    mark_path=f"logos/{names[1]}",
                    shape=shape,
                    is_plate=plate,
                    license=info["license"],
                    artist=info["artist"] or None,
                    source_url=info["page"],
                    source_id=source,
                    valid_from=logo.start[0],
                    valid_from_precision=logo.start[1],
                    valid_to=logo.end[0],
                    valid_to_precision=logo.end[1],
                    dated_by=logo.dated_by,
                    dated_url=logo.dated_url,
                    is_light=light,
                    uploaded_at=datetime.fromisoformat(info["uploaded"]) if info["uploaded"] else None,
                )
                added += 1
        print(f"{added} logos for {len(parties)} parties; skipped: {dict(skipped.most_common())}")
        print(f"contact sheets: {', '.join(str(path) for path in _sheet(sheet))}")
        return cs.write()
