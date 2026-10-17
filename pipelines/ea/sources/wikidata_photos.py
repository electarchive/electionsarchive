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
SCOPE = "select x.value from external_id x join person p on p.id = x.entity_id where x.scheme = 'wikidata'"
CHUNK = 400
TITLES = 50
WIDTH = 256
FETCHED = 330
FREE = re.compile(r"pd|cc0|cc-by(-sa)?-[0-9.]+(-[a-z]+)?")
FREE_NAME = re.compile(r"Public domain|CC0( 1\.0)?|CC BY(-SA)? [0-9.]+( [A-Za-z]+)?")
HOSTS = {
    "bioguide.congress.gov": "Biographical Directory of the United States Congress",
    "parl.gc.ca": "Library of Parliament",
    "lop.parl.ca": "Library of Parliament",
    "house.gov": "U.S. House of Representatives",
    "senate.gov": "U.S. Senate",
    "president.gov.ua": "Office of the President of Ukraine",
    "kremlin.ru": "Presidential Press and Information Office of Russia",
}
CREDITS = {
    "PRESIDENT OF UKRAINE VOLODYMYR ZELENSKYY Official website": "Office of the President of Ukraine",
    "Пресс-служба Президента Российской Федерации": "Presidential Press and Information Office of Russia",
    "Қазақстан Республикасы Парламентінің Мәжілісі": "Mazhilis of the Parliament of Kazakhstan",
}
BOILERPLATE = [
    r"Transferred from \S+ to Commons[^.]*\.",
    r"This image is available from .*",
    r"This tag does not indicate .*",
    r"The factual accuracy of this .*",
    r"Reproduced on web sites .*",
    r"^w:Public domain from ",
    r"^(Source|Artist|Photographer|Author)\s*:\s*",
    r"\b(Facebook|Twitter|Instagram|YouTube)\s*:\s*(?=https?://)",
    r"\s*\bOfficial (web)?site$",
]
UNKNOWN = re.compile(
    r"(unknown|anonymous|unidentified)( (unknown )?(author|photographer|artist))?( (unknown )?(author|photographer|artist))?"
    r"|(author|photographer|artist) (unknown|unidentified|not credited)",
    re.IGNORECASE,
)


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


def _get(url: str, suffix: str, pause: float) -> Artifact | None:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    for _ in range(3):
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
    print(f"skipped {url}")
    return None


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
                "artist": _credit(meta.get("Artist", "")) or _credit(meta.get("Credit", "")),
            }
    return infos


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _credit(markup: str) -> str:
    text = _text(re.sub(r"<(style|figure)\b.*?</\1>", " ", markup, flags=re.DOTALL)).lstrip(" .")
    text = re.sub(r"(?:(?<=[a-z]{3})|(?<=[)\"”]))\s*\.$", "", text)
    if text in CREDITS:
        return CREDITS[text]
    uploaded = re.fullmatch(
        r"The original uploader was (.+?) at \S+ Wikipedia ?\.?(?: \( Original text: (.+) \))?", text
    )
    if uploaded:
        text = uploaded.group(2) or uploaded.group(1)
    for pattern in BOILERPLATE:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    urls = re.findall(r"(?:https?://|www\.)\S+", text)
    text = re.sub(r"(?:https?://|www\.)\S+", "", text).strip(" ,;:-–(")
    if (not text or text.lower() == "from") and urls:
        host = re.sub(r"^(?:https?://)?(?:www\d?\.)?", "", urls[0]).split("/")[0]
        return next((name for domain, name in HOSTS.items() if host.endswith(domain)), host)
    text = re.sub(r"^(.+?) \1\b", r"\1", text)
    if len(text) > 120:
        text = re.split(r"(?<=[a-z)])\. |; ", text)[0]
    if UNKNOWN.fullmatch(text.strip(" .")):
        return "Unknown author"
    roles = r",\s*(photographer|publisher|author|editor|engraver|artist|compiler)s?\.?$"
    while re.search(roles, text):
        text = re.sub(roles, "", text)
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8 and len(letters) > 8:
        text = re.sub(r"\b[A-Z]{4,}\b", lambda match: match.group().capitalize(), text)
        text = re.sub(r"\b(OF|AND|THE|FOR)\b", lambda match: match.group().lower(), text)
    text = re.sub(r"\s+([,.;:])", r"\1", text).strip(" ,;:")
    if len(text) > 160:
        text = text[:160].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return text


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
        _get(infos[title]["thumb"], Path(infos[title]["thumb"]).suffix.lower(), 1)
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
                "Portraits from Wikimedia Commons for every person with a Wikidata image, limited to public domain, CC0, "
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
            try:
                artifact = raw.cached(SOURCE, info["thumb"])
            except LookupError:
                continue
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
