import re

import psycopg


def one(cursor: psycopg.Cursor[tuple]) -> tuple:
    row = cursor.fetchone()
    if row is None:
        raise LookupError("the query returned no row")
    return row


def search(pattern: str | re.Pattern[str], text: str, flags: int = 0) -> re.Match[str]:
    found = re.search(pattern, text, flags)
    if found is None:
        raise LookupError(f"{pattern!r} does not match {text[:80]!r}")
    return found
