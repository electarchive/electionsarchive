import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

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
    index_path, index = _index(source)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    (index_path.parent / f"{digest}{suffix}").write_bytes(content)
    index[url] = {
        "sha256": digest,
        "file": f"{digest}{suffix}",
        "retrieved_at": retrieved_at.isoformat(),
        "media_type": media_type,
        "bytes": len(content),
    }
    index_path.write_text(json.dumps(index, indent=2))
    return _artifact(source, url, index[url])


def fetch(source: str, url: str, *, suffix: str, body: dict[str, object] | None = None) -> Artifact:
    index_path, index = _index(source)
    key = _key(url, body)
    if key in index:
        return _artifact(source, key, index[key])
    folder = index_path.parent
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
    index[key] = {
        "sha256": digest.hexdigest(),
        "file": name,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "media_type": media_type,
        "bytes": size,
        **({} if body is None else {"body": json.dumps(body, sort_keys=True)}),
    }
    index_path.write_text(json.dumps(index, indent=2))
    return _artifact(source, key, index[key])
