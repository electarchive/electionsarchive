import fcntl
import hashlib
import inspect
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO
from uuid import uuid4

import httpx

from ea import wikipedia
from ea.store import DATA

USER_AGENT = "electionsarchive/0.1 (+https://electionsarchive.com)"
HOST, _, ROOT = os.environ.get("EA_REMOTE", "").partition(":")
SSH = [
    "ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=20",
    "-o",
    "ControlMaster=auto",
    "-o",
    "ControlPath=~/.ssh/ea-%C",
    "-o",
    "ControlPersist=10m",
]
MERGE = """
import fcntl, json, sys, uuid
from pathlib import Path

for source, entries in json.load(sys.stdin).items():
    folder = Path(sys.argv[1]) / source
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".index.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / "index.json"
        index = json.loads(path.read_text()) if path.exists() else {}
        for key, entry in entries.items():
            index[key] = _merged(index.get(key), entry)
        partial = folder / f".{uuid.uuid4()}.json.part"
        partial.write_text(json.dumps(index, indent=2))
        partial.replace(path)
"""


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


def _merged(old: dict[str, str | int | None] | None, new: dict[str, str | int | None]) -> dict[str, str | int | None]:
    """Combine two index entries for one key: the same file keeps both sides' stamps, a refetch keeps the newer."""
    if old is None or old["sha256"] == new["sha256"]:
        return {**(old or {}), **new}
    return max(old, new, key=lambda entry: str(entry["retrieved_at"]))


def ssh(
    command: str, *, stdin: IO[bytes] | None = None, stdout: IO[bytes] | int | None = None, input: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([*SSH, HOST, command], stdin=stdin, stdout=stdout, input=input, check=True, timeout=3600)


def merge_remote(entries: dict[str, dict[str, dict[str, str | int | None]]]) -> None:
    """Merge {source: {key: entry}} into the remote indexes, each under its own lock."""
    script = inspect.getsource(_merged) + MERGE
    ssh(f"python3 -c {shlex.quote(script)} {shlex.quote(f'{ROOT}/raw')}", input=json.dumps(entries).encode())


def _push(source: str, key: str, entry: dict[str, str | int | None]) -> None:
    """Write a new file and its entry through to the remote store; a failure leaves both for `ea raw push`."""
    if not HOST:
        return
    folder = shlex.quote(f"{ROOT}/raw/{source}")
    name = shlex.quote(str(entry["file"]))
    partial = shlex.quote(f".{uuid4()}.part")
    try:
        with (DATA / "raw" / source / str(entry["file"])).open("rb") as file:
            ssh(
                f"mkdir -p {folder} && cd {folder} && "
                f"if test -e {name}; then cat > /dev/null; else cat > {partial} && mv {partial} {name}; fi",
                stdin=file,
            )
        merge_remote({source: {key: entry}})
    except (subprocess.SubprocessError, OSError):
        print(f"{source}: {key} is not on {HOST} yet; run `ea raw push {source}`", file=sys.stderr)


def _artifact(source: str, url: str, entry: dict[str, str | int | None]) -> Artifact:
    path = DATA / "raw" / source / str(entry["file"])
    if HOST and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.parent / f".{uuid4()}.part"
        with partial.open("wb") as file:
            ssh(f"cat {shlex.quote(f'{ROOT}/raw/{source}/{path.name}')}", stdout=file)
        with partial.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != entry["sha256"]:
                partial.unlink()
                raise ValueError(f"{HOST} holds a changed {source}/{path.name}")
        partial.rename(path)
    return Artifact(
        source=source,
        url=url,
        sha256=str(entry["sha256"]),
        path=path,
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
    _push(source, url, entry)
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
    _push(source, key, entry)
    return _artifact(source, key, entry)


def push(source: str | None = None) -> str:
    """Copy the raw files and page renders the remote store lacks, then merge the local indexes into its own."""
    if not HOST:
        raise SystemExit("set EA_REMOTE in .env, such as host:electionsarchive/data")
    for tree in ("raw", "pages"):
        path = f"{tree}/{source}/" if source else f"{tree}/"
        if (DATA / path).is_dir():
            ssh(f"mkdir -p {shlex.quote(f'{ROOT}/{path}')}")
            subprocess.run(
                ["rsync", "-a", "--ignore-existing", "--exclude", ".*", "--exclude", "index.json"]
                + ["-e", " ".join(SSH), f"{DATA}/{path}", f"{HOST}:{ROOT}/{path}"],
                check=True,
            )
    folders = [DATA / "raw" / source] if source else sorted((DATA / "raw").iterdir())
    indexes = {folder.name: _index(folder.name)[1] for folder in folders if (folder / "index.json").exists()}
    merge_remote(indexes)
    return f"pushed {sum(len(index) for index in indexes.values())} entries in {len(indexes)} sources to {HOST}"


def pull(source: str | None = None) -> str:
    """Merge the remote indexes into the local ones, then copy the raw files and page renders the cache lacks."""
    if not HOST:
        raise SystemExit("set EA_REMOTE in .env, such as host:electionsarchive/data")
    script = (
        "import json, sys\nfrom pathlib import Path\n"
        "print(json.dumps({p.parent.name: json.loads(p.read_text()) for p in Path(sys.argv[1]).glob(sys.argv[2])}))"
    )
    pattern = f"{source or '*'}/index.json"
    found = ssh(
        f"python3 -c {shlex.quote(script)} {shlex.quote(f'{ROOT}/raw')} {shlex.quote(pattern)}", stdout=subprocess.PIPE
    )
    indexes: dict[str, dict[str, dict[str, str | int | None]]] = json.loads(found.stdout)
    for name, entries in indexes.items():
        with locked(name) as index:
            for key, entry in entries.items():
                index[key] = _merged(index.get(key), entry)
    for tree in ("raw", "pages"):
        path = f"{tree}/{source}/" if source else f"{tree}/"
        (DATA / path).mkdir(parents=True, exist_ok=True)
        ssh(f"mkdir -p {shlex.quote(f'{ROOT}/{path}')}")
        subprocess.run(
            ["rsync", "-a", "--ignore-existing", "--exclude", ".*", "--exclude", "index.json"]
            + ["-e", " ".join(SSH), f"{HOST}:{ROOT}/{path}", f"{DATA}/{path}"],
            check=True,
        )
    return f"pulled {sum(len(index) for index in indexes.values())} entries in {len(indexes)} sources from {HOST}"
