import fcntl
import hashlib
import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from ea import wikipedia
from ea.store import DATA

USER_AGENT = "electionsarchive/0.1 (+https://electionsarchive.com)"


@dataclass(frozen=True)
class Artifact:
    source: str
    url: str
    sha256: str
    path: Path
    retrieved_at: datetime
    media_type: str | None
    bytes: int

    @property
    def storage_key(self) -> str:
        return f"raw/{self.source}/{self.path.name}"


def _index(source: str) -> tuple[Path, dict[str, dict[str, str | int | None]]]:
    path = DATA / "raw" / source / "index.json"
    return path, json.loads(path.read_text()) if path.exists() else {}


@contextmanager
def locked(source: str) -> Generator[dict[str, dict[str, str | int | None]]]:
    """Yield the source's index under an exclusive lock and write it back atomically, so concurrent fetches keep
    each other's entries."""
    path, _ = _index(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".index.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _, index = _index(source)
        yield index
        partial = path.parent / f".{uuid4()}.json.part"
        partial.write_text(json.dumps(index, indent=2))
        partial.replace(path)


def _artifact(source: str, url: str, entry: dict[str, str | int | None]) -> Artifact:
    return Artifact(
        source=source,
        url=url,
        sha256=str(entry["sha256"]),
        path=DATA / "raw" / source / str(entry["file"]),
        retrieved_at=datetime.fromisoformat(str(entry["retrieved_at"])),
        media_type=None if entry["media_type"] is None else str(entry["media_type"]),
        bytes=int(entry["bytes"] or 0),
    )


def _key(url: str, body: dict[str, object] | None) -> str:
    if body is None:
        return url
    return f"{url}#{hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]}"


def cached(source: str, url: str, body: dict[str, object] | None = None) -> Artifact:
    _, index = _index(source)
    key = _key(url, body)
    if key not in index:
        raise LookupError(f"{key} is not in the {source} raw cache; run `ea fetch {source}` first")
    return _artifact(source, key, index[key])


def store(source: str, url: str, content: bytes, *, suffix: str, retrieved_at: datetime, media_type: str) -> Artifact:
    folder = DATA / "raw" / source
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    (folder / f"{digest}{suffix}").write_bytes(content)
    entry: dict[str, str | int | None] = {
        "sha256": digest,
        "file": f"{digest}{suffix}",
        "retrieved_at": retrieved_at.isoformat(),
        "media_type": media_type,
        "bytes": len(content),
    }
    with locked(source) as index:
        index[url] = entry
    return _artifact(source, url, entry)


def fetch(source: str, url: str, *, suffix: str, body: dict[str, object] | None = None) -> Artifact:
    _, index = _index(source)
    key = _key(url, body)
    if key in index:
        return _artifact(source, key, index[key])
    folder = DATA / "raw" / source
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / f".{uuid4()}.part"
    digest = hashlib.sha256()
    size = 0
    with (
        httpx.stream(
            "GET" if body is None else "POST",
            url,
            json=body,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=600,
        ) as response,
        partial.open("wb") as file,
    ):
        response.raise_for_status()
        media_type = response.headers.get("content-type")
        for chunk in response.iter_bytes():
            digest.update(chunk)
            size += len(chunk)
            file.write(chunk)
    name = f"{digest.hexdigest()}{suffix}"
    partial.rename(folder / name)
    retrieved_at = datetime.now(UTC)
    entry: dict[str, str | int | None] = {
        "sha256": digest.hexdigest(),
        "file": name,
        "retrieved_at": retrieved_at.isoformat(),
        "media_type": media_type,
        "bytes": size,
        **({} if body is None else {"body": json.dumps(body, sort_keys=True)}),
        **wikipedia.pin(url, folder / name, retrieved_at),
    }
    with locked(source) as index:
        index[key] = entry
    return _artifact(source, key, entry)
