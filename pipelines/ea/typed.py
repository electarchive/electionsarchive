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


def required(found: re.Match[str] | None) -> re.Match[str]:
    if found is None:
        raise LookupError("the pattern did not match")
    return found


def present[T](value: T | None, name: str) -> T:
    if value is None:
        raise LookupError(f"{name} is missing")
    return value
