"""The shapes the Luxembourg parsers produce, whatever the file they read."""

import re
import unicodedata
from dataclasses import dataclass, field


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold().replace("’", "'").replace("‘", "'"))
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in text if not unicodedata.combining(c)))


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return re.sub(r"[^a-z0-9]+", "-", "".join(c for c in text if not unicodedata.combining(c))).strip("-")


def number(text: str | None) -> int | None:
    return int(text) if text not in (None, "") else None


@dataclass
class Stats:
    registered: int | None = None
    cast: int | None = None
    blank: int | None = None
    invalid: int | None = None
    valid: int | None = None


@dataclass
class Candidate:
    number: int
    name: str
    votes: int | None
    elected: bool


@dataclass
class Entry:
    number: int
    name: str
    sigle: str
    total: int | None = None
    listed: int | None = None
    personal: int | None = None
    seats: int | None = None
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Place:
    name: str
    canton: str | None
    stats: Stats
    entries: list[Entry] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Race:
    name: str
    seats: int | None
    mode: str
    stats: Stats
    entries: list[Entry]
    candidates: list[Candidate]
    places: list[Place]
    printed: bool = True  # whether the source prints this contest's own totals, apart from its communes' rows
