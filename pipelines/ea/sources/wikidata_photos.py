import hashlib
import html
import io
import json
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlencode
from uuid import uuid5

import httpx
from PIL import Image, ImageOps

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.raw import Artifact
from ea.store import DATA, connect

SOURCE = "wikidata_photos"
VERSION = "1"
TITLE = "Photos of people"
PHOTOS = DATA / "photos"
ITEMS = DATA / "parsed" / SOURCE / "items.json"
IMAGES = (
    """SELECT ?item (MIN(?image) AS ?file) WHERE {{ VALUES ?item {{ {items} }} ?item wdt:P18 ?image }} GROUP BY ?item"""
)
SCOPE = """
with scoped as (
  select c.id from contest c
  join election e on e.id = c.election_id
  join polity p on p.id = e.polity_id
  where p.kind in ('sovereign_state', 'supranational', 'historical_state')
        and c.kind in ('office', 'body_seats', 'list_allocation')
     or p.kind in ('federal_unit', 'region') and c.kind = 'office'
), votes as (
  select o.id as option_id, o.contest_id, max(r.value) as value
  from scoped s
  join result_set rs on rs.contest_id = s.id and rs.is_default
  join result_rollup r on r.result_set_k = rs.k and r.measure = 1 and r.channel = 0
  join contest_option o on o.k = r.option_k
  group by o.id, o.contest_id
), finishers as (
  select option_id from (
    select option_id, rank() over (partition by contest_id order by value desc) as place from votes
  ) ranked where place <= 2
  union
  select oc.option_id from scoped s
  join result_set rs on rs.contest_id = s.id and rs.is_default
  join outcome oc on oc.result_set_id = rs.id and oc.kind in ('elected', 'won')
)
select distinct x.value from candidacy cd
join external_id x on x.entity_id = cd.person_id and x.scheme = 'wikidata'
where cd.option_id in (select option_id from finishers)
"""
CHUNK = 400
TITLES = 50
WIDTH = 256
FETCHED = 330
FREE = re.compile(r"pd|cc0|cc-by(-sa)?-[0-9.]+(-[a-z]+)?")
FREE_NAME = re.compile(r"Public domain|CC0( 1\.0)?|CC BY(-SA)? [0-9.]+( [A-Za-z]+)?")


def _items() -> list[str]:
    return json.loads(ITEMS.read_text(encoding="utf-8"))


def _sparql(items: list[str]) -> list[str]:
    return [
        "https://query.wikidata.org/sparql?"
        + urlencode(
            {
                "query": IMAGES.format(items=" ".join(f"wd:{item}" for item in items[start : start + CHUNK])),
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
                "iiprop": "url|extmetadata",
                "iiurlwidth": str(FETCHED),
                "iiextmetadatafilter": "License|LicenseShortName|Artist|Credit",
                "titles": "|".join(f"File:{file}" for file in files[start : start + TITLES]),
            }
        )
        for start in range(0, len(files), TITLES)
    ]


def _get(url: str, suffix: str, pause: float) -> Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    while True:
        try:
            artifact = raw.fetch(SOURCE, url, suffix=suffix)
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in (429, 503):
                raise
            wait = int(error.response.headers.get("retry-after", "60"))
            print(f"{error.response.status_code} from {error.request.url.host}, waiting {wait}s")
            time.sleep(wait)
            continue
        time.sleep(pause)
        return artifact


def _files(items: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for url in _sparql(items):
        for binding in json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))["results"]["bindings"]:
            files[binding["item"]["value"].rsplit("/", 1)[1]] = unquote(
                binding["file"]["value"].rsplit("/", 1)[1]
            ).replace("_", " ")
    return files


def _infos(files: dict[str, str]) -> dict[str, dict[str, str]]:
    infos: dict[str, dict[str, str]] = {}
    for url in _commons(sorted(set(files.values()))):
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
            meta = {key: str(value["value"]) for key, value in info.get("extmetadata", {}).items()}
            infos[title] = {
                "thumb": info.get("thumburl", info["url"]),
                "page": info["descriptionurl"],
                "code": meta.get("License", "").lower(),
                "license": _text(meta.get("LicenseShortName", "")),
                "artist": _text(meta.get("Artist", "")) or _text(meta.get("Credit", "")),
            }
    return infos


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _free(info: dict[str, str]) -> bool:
    if re.search(r"\bnc\b|-nc-|\bnd\b|-nd-", info["code"]) or re.search(r"\bNC\b|\bND\b", info["license"]):
        return False
    return bool(FREE.fullmatch(info["code"]) or FREE_NAME.fullmatch(info["license"]))


def fetch() -> None:
    with connect() as conn:
        items = sorted((value for (value,) in conn.execute(SCOPE).fetchall()), key=lambda item: int(item[1:]))
    ITEMS.parent.mkdir(parents=True, exist_ok=True)
    ITEMS.write_text(json.dumps(items), encoding="utf-8")
    print(f"{len(items)} people with Wikidata items in scope")
    for url in _sparql(items):
        _get(url, ".json", 2)
    files = _files(items)
    print(f"{len(files)} with an image")
    for url in _commons(sorted(set(files.values()))):
        _get(url, ".json", 1)
    infos = _infos(files)
    kept = sorted({title for title in files.values() if title in infos and _free(infos[title])})
    print(f"{len(kept)} freely licensed images to fetch")
    for count, title in enumerate(kept, 1):
        _get(infos[title]["thumb"], Path(infos[title]["thumb"]).suffix.lower(), 0.5)
        if count % 250 == 0:
            print(f"{count}/{len(kept)}")


def emit() -> Path:
    items = _items()
    files = _files(items)
    infos = _infos(files)
    rejected: Counter[str] = Counter()
    with connect() as conn:
        people = {
            value: entity
            for value, entity in conn.execute(
                "select x.value, x.entity_id from external_id x join person p on p.id = x.entity_id "
                "where x.scheme = 'wikidata'"
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Portraits from Wikimedia Commons for people with a Wikidata image, limited to public domain, CC0, "
                "CC BY, and CC BY-SA files, as 256-pixel-wide thumbnails with their license and author"
            ),
            polity=None,
        )
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
            attribution="Each photo credits its author and license as its Wikimedia Commons file page gives them",
        )
        for url in _sparql(items) + _commons(sorted(set(files.values()))):
            cs.artifact(raw.cached(SOURCE, url), source)
        PHOTOS.mkdir(parents=True, exist_ok=True)
        added = 0
        thumbs: set[str] = set()
        for item in items:
            title = files.get(item)
            if title is None or title not in infos or item not in people:
                continue
            info = infos[title]
            if not _free(info):
                rejected[info["license"] or info["code"] or "none"] += 1
                continue
            artifact = raw.cached(SOURCE, info["thumb"])
            if info["thumb"] not in thumbs:
                cs.artifact(artifact, source)
                thumbs.add(info["thumb"])
            image = ImageOps.exif_transpose(Image.open(artifact.path))
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            image = image.convert("RGB")
            if image.width > WIDTH:
                image = image.resize((WIDTH, round(image.height * WIDTH / image.width)), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=85, optimize=True, progressive=True)
            content = buffer.getvalue()
            name = f"{hashlib.sha256(content).hexdigest()}.jpg"
            (PHOTOS / name).write_bytes(content)
            person = people[item]
            cs.add(
                "person_image",
                id=uuid5(NAMESPACE, f"{SOURCE}.image:{item}"),
                person_id=cs.survivors.get(person, person),
                path=f"photos/{name}",
                license=info["license"],
                artist=info["artist"] or None,
                source_url=info["page"],
                source_id=source,
            )
            added += 1
        print(f"{added} photos for {len(items)} people; rejected licenses: {dict(rejected.most_common())}")
        return cs.write()
