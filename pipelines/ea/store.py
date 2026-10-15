import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())

type Connection = psycopg.Connection[tuple]


def connect(*, autocommit: bool = False) -> Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=autocommit)
