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
from PIL import Image

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.raw import Artifact
from ea.sources.wikidata_photos import _credit, _free, _text
from ea.store import DATA, connect

SOURCE = "party_logos"
VERSION = "3"
TITLE = "Party logos"
LOGOS = DATA / "logos"
ITEMS = DATA / "parsed" / SOURCE / "items.json"
DATED = Path(__file__).with_suffix(".tsv")
STATEMENTS = """SELECT ?item ?statement ?file ?rank ?start ?startPrecision ?end ?endPrecision ?began ?ended ?dissolved WHERE {{
  VALUES ?item {{ {items} }}
  ?item p:P154 ?statement . ?statement ps:P154 ?file ; wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL {{ ?statement pqv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?startPrecision ] }}
  OPTIONAL {{ ?statement pqv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?endPrecision ] }}
  OPTIONAL {{ ?statement pq:P580 ?began }}
  OPTIONAL {{ ?statement pq:P582 ?ended }}
  OPTIONAL {{ ?item wdt:P576 ?dissolved }}
}}"""
SCOPE = "select x.value from external_id x join party p on p.id = x.entity_id where x.scheme = 'wikidata'"
CHUNK = 300
TITLES = 50
HEIGHT = 96
WIDEST = 480
PRECISIONS = {11: "day", 10: "month", 9: "year"}
UNDATED: tuple[date | None, str] = (None, "unknown")
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
    for url in _sparql(items):
        for row in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]:
            item = row["item"]["value"].rsplit("/", 1)[1]
            statement = row["statement"]["value"].rsplit("/", 1)[1]
            file = unquote(row["file"]["value"].rsplit("/", 1)[1]).replace("_", " ")
            if (item, file) in NOT_LOGOS:
                continue
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
    for statements in parties.values():
        logos = list(statements.values())
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
    return parties


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
                "CC0, CC BY, and CC BY-SA files, as WebP images 96 pixels high, dated from Wikidata's start and end "
                "qualifiers or from what the file itself states"
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
                image = Image.open(artifact.path).convert("RGBA")
                width = min(WIDEST, round(image.width * HEIGHT / image.height))
                height = round(image.height * width / image.width)
                image = image.resize((width, height), Image.Resampling.LANCZOS)
                pixels = np.asarray(image, dtype=np.float64) / 255
                linear = np.where(
                    pixels[..., :3] <= 0.04045, pixels[..., :3] / 12.92, ((pixels[..., :3] + 0.055) / 1.055) ** 2.4
                )
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
                lab = lms @ np.array(
                    [
                        [0.2104542553, 1.9779984951, 0.0259040371],
                        [0.793617785, -2.428592205, 0.7827717662],
                        [-0.0040720468, 0.4505937099, -0.808675766],
                    ]
                )
                bright = (lab[..., 0] >= 0.92) & (np.hypot(lab[..., 1], lab[..., 2]) <= 0.04)
                opaque = pixels[..., 3] >= 0.5
                padded = np.pad(opaque, 1, constant_values=False)
                edge = opaque & ~(padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:])
                light = (
                    bool(opaque.mean() < 0.95)
                    and bool(edge.any())
                    and float(bright[edge].mean()) > 0.6
                    and float(bright[opaque].mean()) > 0.6
                )
                buffer = io.BytesIO()
                image.save(buffer, "WEBP", quality=90, method=6)
                content = buffer.getvalue()
                name = f"{hashlib.sha256(content).hexdigest()}.webp"
                (LOGOS / name).write_bytes(content)
                party = known[item]
                cs.add(
                    "party_image",
                    id=uuid5(NAMESPACE, f"{SOURCE}.image:{logo.statement or f'{item}:{logo.file}'}"),
                    party_id=cs.survivors.get(party, party),
                    path=f"logos/{name}",
                    width=width,
                    height=height,
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
        return cs.write()
