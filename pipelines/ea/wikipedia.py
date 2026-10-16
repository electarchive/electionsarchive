import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx

from ea import raw


def _title(url: str, content: bytes) -> str | None:
    """The page a content fetch read: a parse of a page (redirects resolved from the response) or a raw wikitext
    read. Lookups (search, pageprops) and batched revision queries, whose responses already carry their revision
    ids, return None."""
    parts = urlsplit(url)
    if not (parts.hostname or "").endswith(".wikipedia.org"):
        return None
    query = dict(parse_qsl(parts.query))
    if query.get("action") == "raw" and "title" in query:
        return query["title"]
    if query.get("action") == "parse" and "page" in query:
        try:
            return json.loads(content)["parse"]["title"]
        except ValueError, KeyError, TypeError:
            return query["page"]
    return None


def _text(url: str, content: bytes) -> bytes | None:
    """The revision's wikitext as fetched, when the response holds it, to check against the revision's SHA-1."""
    if dict(parse_qsl(urlsplit(url).query)).get("action") == "raw":
        return content
    try:
        wikitext = json.loads(content)["parse"]["wikitext"]
    except ValueError, KeyError, TypeError:
        return None
    return (wikitext["*"] if isinstance(wikitext, dict) else wikitext).encode()


def revision(url: str, content: bytes, at: datetime) -> dict[str, str | int | None]:
    """Pin a Wikipedia page fetch to the revision that was current when it was retrieved: its title, revision id, and
    whether the fetched wikitext matches that revision's SHA-1 (None when the response is rendered HTML)."""
    title = _title(url, content)
    if title is None:
        return {}
    parts = urlsplit(url)
    response = httpx.get(
        f"{parts.scheme}://{parts.hostname}/w/api.php",
        params={
            "action": "query",
            "prop": "revisions",
            "titles": title,
            "rvlimit": 1,
            "rvdir": "older",
            "rvstart": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rvprop": "ids|sha1|timestamp",
            "format": "json",
            "formatversion": 2,
        },
        headers={"User-Agent": raw.USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    page = response.json()["query"]["pages"][0]
    if "revisions" not in page:
        return {}
    found = page["revisions"][0]
    text = _text(url, content)
    return {
        "wikipedia_title": page["title"],
        "wikipedia_revid": found["revid"],
        "wikipedia_verified": None if text is None else hashlib.sha1(text).hexdigest() == found["sha1"],
    }


def pin(url: str, path: Path, at: datetime) -> dict[str, str | int | None]:
    """`revision` for a file just fetched, reading it only for Wikipedia URLs. A failed lookup leaves the fetch
    unpinned for `ea raw pin` to retry rather than failing it."""
    if not (urlsplit(url).hostname or "").endswith(".wikipedia.org"):
        return {}
    try:
        return revision(url, path.read_bytes(), at)
    except httpx.HTTPError, KeyError, IndexError, ValueError:
        return {}
