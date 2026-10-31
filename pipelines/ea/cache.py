import hashlib
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ea import raw, wikipedia
from ea.store import DATA, connect

RAW = DATA / "raw"
RETIRED = DATA / "retired" / "raw"

# Sources whose files may never be redistributed, whatever their source rows say.
RESTRICTED = {
    "us.icpsr": "ICPSR terms of use bar redistribution",
    "thea": "awaiting THEA's permission",
    "us.algara": "compiled from CQ Press and ICPSR's United States Historical Election Returns",
    "us.algara_federal": "compiled from CQ Press and ICPSR's United States Historical Election Returns",
}
# The first rule a license matches sets its class; a file cited under several licenses takes the strictest.
CLASSES = [
    ("restricted", re.compile(r"ICPSR|All rights reserved|permission .*pending|^Pending:", re.IGNORECASE)),
    ("non-commercial", re.compile(r"non-?commercial", re.IGNORECASE)),
    ("revocable", re.compile(r"revocable", re.IGNORECASE)),
    ("share-alike", re.compile(r"ShareAlike|BY-SA|ODbL|Open Database License", re.IGNORECASE)),
    (
        "open",
        re.compile(
            r"CC0|public domain|excluded from copyright|freely reproduced|open by default|\bMIT\b", re.IGNORECASE
        ),
    ),
    (
        "attribution",
        re.compile(
            r"CC BY|Creative Commons Attribution|Licence Ouverte|Open Government|Open Parliament|Datenlizenz"
            r"|reuse is authorized|2011/833/EU|Open use|free use|free to distribute|free to copy|freely reproducible"
            r"|City of Chicago Data Portal Terms of Use|Scottish Boundary Commissions Historical Electoral Data terms of use",
            re.IGNORECASE,
        ),
    ),
    ("public-record", re.compile(r"public record", re.IGNORECASE)),
    ("unknown", re.compile(r"^No license stated")),
]
STRICTNESS = [
    "open",
    "attribution",
    "public-record",
    "share-alike",
    "unknown",
    "revocable",
    "non-commercial",
    "restricted",
]
SHAREABLE = {"open", "attribution", "public-record", "share-alike"}
FRESH = timedelta(days=1)


def _class(license: str | None) -> str:
    for name, pattern in CLASSES:
        if license and pattern.search(license):
            return name
    return "unknown"


def _sources(source: str | None) -> list[Path]:
    return [RAW / source] if source else sorted(path for path in RAW.iterdir() if path.is_dir())


def _files(folder: Path) -> set[str]:
    return {path.name for path in folder.iterdir() if path.is_file() and not path.name.startswith(".")} - {"index.json"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 24):
            digest.update(chunk)
    return digest.hexdigest()


def verify(source: str | None = None, *, quick: bool = False) -> int:
    """Check every index entry's file exists with its recorded size and SHA-256 (sizes only when quick), and report
    unindexed files, entries no license stamp covers, and Wikipedia page fetches not pinned to a revision. Returns
    the number of broken entries (missing or changed files)."""
    broken = 0
    totals = {"entries": 0, "unindexed": 0, "unlicensed": 0, "unpinned": 0}
    for folder in _sources(source):
        _, index = raw._index(folder.name)
        files = _files(folder)
        if not index and not files:
            continue
        problems = []
        for key, entry in index.items():
            path = folder / str(entry["file"])
            if not path.exists():
                problems.append(f"missing {entry['file']} ({key})")
            elif path.stat().st_size != entry["bytes"]:
                problems.append(f"size {entry['file']}: {path.stat().st_size} != {entry['bytes']}")
            elif not quick and _sha256(path) != entry["sha256"]:
                problems.append(f"sha256 {entry['file']}")
            if "shareable" not in entry:
                totals["unlicensed"] += 1
            if "wikipedia_revid" not in entry and wikipedia._title(key, b"") is not None:
                totals["unpinned"] += 1
        unindexed = sorted(files - {str(entry["file"]) for entry in index.values()})
        totals["entries"] += len(index)
        totals["unindexed"] += len(unindexed)
        broken += len(problems)
        if problems or unindexed:
            print(f"{folder.name}: {len(index)} entries, {len(problems)} broken, {len(unindexed)} unindexed")
            for problem in problems:
                print(f"  {problem}")
            for name in unindexed[:10]:
                print(f"  unindexed {name}")
            if len(unindexed) > 10:
                print(f"  … and {len(unindexed) - 10} more unindexed")
    print(
        f"{totals['entries']} entries, {broken} broken, {totals['unindexed']} unindexed files, "
        f"{totals['unlicensed']} without a license stamp, {totals['unpinned']} Wikipedia pages unpinned"
    )
    return broken


def license() -> str:
    """Stamp each index entry with the licenses of the sources that cite it, their strictest class, and whether the
    file may be redistributed. Entries no published source cites are stamped unknown, so never shareable."""
    cited: dict[str, set[str | None]] = {}
    with connect() as conn:
        for key, text in conn.execute(
            "select a.storage_key, s.license from source_artifact a join source s on s.id = a.source_id"
        ):
            cited.setdefault(key, set()).add(text)
    counts: dict[str, int] = {}
    unknown: set[str] = set()
    for folder in _sources(None):
        if not (folder / "index.json").exists():
            continue
        with raw.locked(folder.name) as index:
            for entry in index.values():
                licenses = cited.get(f"raw/{folder.name}/{entry['file']}", {None})
                kind = max((_class(text) for text in licenses), key=STRICTNESS.index)
                if folder.name in RESTRICTED:
                    kind = "restricted"
                entry["license"] = " | ".join(sorted(text for text in licenses if text)) or None
                entry["license_class"] = kind
                entry["shareable"] = kind in SHAREABLE
                counts[kind] = counts.get(kind, 0) + 1
                unknown |= {text for text in licenses if text and not any(rule.search(text) for _, rule in CLASSES)}
    for text in sorted(unknown):
        print(f"no class for license: {text}")
    return ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items(), key=lambda item: -item[1]))


def pin(source: str | None = None) -> str:
    """Pin cached Wikipedia page fetches to the revision current at their retrieval time."""
    pinned = mismatched = failed = 0
    for folder in _sources(source):
        _, index = raw._index(folder.name)
        found = {}
        for key, entry in index.items():
            if "wikipedia_revid" in entry or wikipedia._title(key, b"") is None:
                continue
            content = (folder / str(entry["file"])).read_bytes()
            try:
                found[key] = wikipedia.revision(key, content, datetime.fromisoformat(str(entry["retrieved_at"])))
            except Exception as error:
                print(f"{folder.name}: {key}: {error}")
                failed += 1
                continue
            time.sleep(1)
        if not found:
            continue
        with raw.locked(folder.name) as current:
            for key, revision in found.items():
                if key in current and revision:
                    current[key].update(revision)
                    pinned += 1
                    mismatched += revision["wikipedia_verified"] is False
    return f"{pinned} pinned ({mismatched} not matching their revision's SHA-1), {failed} failed"


def retire() -> str:
    """Move files no index entry points at into data/retired/raw/{source}/, leaving anything written in the last day
    alone in case a fetch is still indexing it. Files already indexed elsewhere under the same hash are copies."""
    known = {str(entry["sha256"]) for folder in _sources(None) for entry in raw._index(folder.name)[1].values()}
    copies = others = 0
    cutoff = datetime.now(UTC) - FRESH
    for folder in _sources(None):
        indexed = {str(entry["file"]) for entry in raw._index(folder.name)[1].values()}
        for name in sorted(_files(folder) - indexed):
            path = folder / name
            if datetime.fromtimestamp(path.stat().st_mtime, UTC) > cutoff:
                continue
            copy = _sha256(path) in known
            target = RETIRED / folder.name / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError(target)
            shutil.move(path, target)
            print(f"{folder.name}/{name}: {'copy of an indexed file' if copy else 'not indexed'}")
            copies += copy
            others += not copy
    return f"retired {copies} copies of indexed files and {others} other unindexed files to {RETIRED}"


def main(command: str, source: str | None, *, quick: bool) -> None:
    match command:
        case "verify":
            if verify(source, quick=quick):
                raise SystemExit(1)
        case "license":
            print(license())
        case "pin":
            print(pin(source))
        case "retire":
            print(retire())
