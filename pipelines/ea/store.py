import os
import subprocess
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())

LAUNCHED_AT = os.environ.get("EA_LAUNCHED_AT") or None

type Connection = psycopg.Connection[tuple]


def connect(*, autocommit: bool = False) -> Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=autocommit)


def checkout() -> dict[str, str | None]:
    try:
        lines = subprocess.run(
            ["git", "rev-parse", "HEAD", "--abbrev-ref", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.split()
    except OSError, subprocess.SubprocessError:
        lines = []
    return {
        "sha": lines[0] if len(lines) == 2 else None,
        "branch": lines[1] if len(lines) == 2 else None,
        "path": str(ROOT),
    }


def provenance(step: str, changeset_id: str, status: str) -> None:
    ran = checkout()
    try:
        with (DATA / "logs" / "provenance.tsv").open("a", encoding="utf-8") as log:
            log.write(
                f"{time.time():.0f}\t{step}\t{changeset_id}\t{status}\t{ran['sha'] or ''}\t{ran['branch'] or ''}\t{ran['path']}\n"
            )
    except OSError:
        pass
