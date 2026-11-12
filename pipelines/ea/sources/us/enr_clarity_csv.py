import csv
import io
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ea import raw
from ea.sources.us import enr_clarity
from ea.store import DATA, connect

SOURCE = "us.enr_clarity_csv"
VERSION = "1"
INBOX = DATA / "inbox" / "manual"
TENANT = "https://results.enr.clarityelections.com/TX/Travis/"
FILES = (
    (
        "MF-628",
        "travis-127492-summary.csv",
        "127492",
        date(2026, 11, 3),
        "2026 November Joint General and Special Elections",
        "48453",
        "Travis",
    ),
)
CANVASS_NAMES = {
    "Gregory Harrington": "Gregory L. Harrington",
    "Oscar Mitchell": "Oscar R. Mitchell",
    "Robert 'Bobby' Hidrogo": "Robert Hidrogo",
}
VOTE_FOR = re.compile(r"\s*\(Vote For (\d+)\)\s*$")
TAG = re.compile(r"\s*\((?:REP|DEM|LIB|GRN|IND|NON)\)\s*$")
BOARD = frozenset({"board", "directors", "director", "permanent", "trustees", "trustee", "three", "places", "2026"})
PURPOSE = re.compile(r"(City of Austin Proposition [A-J]) - (Full|Limited) Purpose$")
PROPOSITION = re.compile(r"^(.*?)[:,]? Proposition ([A-Z])(?: \((Existing|To Be Annexed)\))?(?::.*)?$")
EXPANDED = {
    "MUD": "municipal utility district",
    "MMD": "municipal management district",
    "ESD": "emergency services district",
    "WCID": "water control and improvement district",
    "ISD": "independent school district",
    "ACC": "austin community college district",
}


def rows() -> list[dict[str, str]]:
    return [
        {
            "state": "TX",
            "county_fips": fips,
            "county": county,
            "platform": "clarity_csv",
            "tenant": TENANT,
            "status": "listed",
            "evidence": TENANT,
        }
        for *_, fips, county in FILES
    ]


def link(election: str) -> str:
    return f"{TENANT}{election}/summary.csv"


def fetch() -> None:
    for folder, name, election, *_ in FILES:
        path = INBOX / folder / name
        raw.store(
            SOURCE,
            link(election),
            path.read_bytes(),
            suffix=".csv",
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            media_type="text/csv",
        )
    enr_clarity.fetch_shapes(SOURCE, {fips[:2] for *_, fips, _ in FILES})


def _read(registry: Mapping[str, Mapping[str, str]]) -> Iterator[dict[str, Any]]:
    for _, _, election, day, title, *_ in FILES:
        text = raw.cached(SOURCE, link(election)).path.read_bytes().decode("cp1252")
        contests: dict[str, dict[str, Any]] = {}
        for row in csv.DictReader(io.StringIO(text)):
            name = row["contest name"]
            vote_for = VOTE_FOR.search(name)
            node = contests.setdefault(
                PURPOSE.sub(r"\1", VOTE_FOR.sub("", name)),
                {
                    "key": str(len(contests) + 1),
                    "text": PURPOSE.sub(r"\1", VOTE_FOR.sub("", name)),
                    "vote_for": int(vote_for[1]) if vote_for else 1,
                    "choices": [],
                },
            )
            label = TAG.sub("", row["choice name"])
            label = CANVASS_NAMES.get(label, label) if "Pflugerville" in name or "Del Valle" in name else label
            if kept := next((choice for choice in node["choices"] if choice["text"] == label), None):
                kept["total"] = str(int(kept["total"]) + int(row["total votes"]))
                continue
            node["choices"].append(
                {"text": label, "party": row["party name"] or None, "total": row["total votes"], "places": []}
            )
        yield {
            "tenant": TENANT,
            "election": election,
            "day": day,
            "title": title,
            "official": True,
            "primary": False,
            "contests": list(contests.values()),
        }


def _words(text: str) -> frozenset[str]:
    text = re.sub(r"\bof Travis County\b", " ", text)
    text = re.sub(r"[^A-Za-z0-9 ]", " ", text)
    return frozenset(
        word
        for token in text.split()
        for word in EXPANDED.get(token, token).lower().split()
        if word not in {"no", "of", "the", "city", "village"}
    )


def _remap() -> None:
    path = DATA / "parsed" / SOURCE / "contests.parquet"
    contests = pl.read_parquet(path)
    with connect() as conn:
        keys = {
            value: name
            for value, name in conn.execute(
                """select x.value, n.text from external_id x join name n on n.entity_id = x.entity_id and n.is_preferred
                   where x.scheme = 'us.contest' and x.value like '2026-11-03:TX:%%'"""
            ).fetchall()
        }
    tails: dict[str, set[str]] = {}
    boards: dict[frozenset[str], set[str]] = {}
    questions: dict[tuple[frozenset[str], str, str], list[str]] = {}
    for value, name in keys.items():
        if value.count(":") >= 3:
            tails.setdefault(value.split(":", 3)[3], set()).add(value)
        if ":measure:" not in value and value.startswith("2026-11-03:TX:48453"):
            boards.setdefault(_words(re.sub(r"^2026 | election", "", name)) - BOARD, set()).add(value)
        found = re.match(r"^2026 (.+?): Proposition ([A-Z])(?:\b.*)?$", name)
        if found and ":measure:" in value:
            qualifier = (
                "To Be Annexed" if "to-be-annexed" in value else "Existing" if "within-the-district" in value else ""
            )
            questions.setdefault((_words(found[1]), found[2], qualifier), []).append(value)
    mapped: dict[str, str] = {}
    for key, text in contests.select("contest_key", "text").iter_rows():
        if key in keys:
            continue
        found = PROPOSITION.match(text)
        if found and ":question:" in key:
            words = _words(found[1])
            options = questions.get((words, found[2], found[3] or ""), [])
            if not options and found[3] == "Existing":
                options = questions.get((words, found[2], ""), [])
        else:
            tail = key.split(":", 3)[-1]
            options = []
            for guess in (
                re.sub(r":special(?=$|:)", ":board", tail),
                re.sub(r":special:place-(\d+)$", r":trustee-place-\1", tail),
                tail.replace(":region-", ":district-"),
                tail.replace(":remainder", ""),
            ):
                options = [value for value in tails.get(guess, ()) if value.startswith(key[:11])]
                if not tail.startswith("college:"):
                    options = [value for value in options if value.split(":")[2] == key.split(":")[2]]
                if options:
                    break
            else:
                options = list(boards.get(_words(text) - BOARD, ()))
        if len(set(options)) == 1:
            mapped[key] = next(iter(options))
    print(f"{len(mapped)} contests take the key of one already minted")
    contests.with_columns(contest_key=pl.col("contest_key").replace(mapped)).write_parquet(path)


def parse() -> None:
    enr_clarity.parse(SOURCE, "clarity_csv", _read, rows())
    _remap()


def emit() -> Path:
    return enr_clarity.emit(
        SOURCE,
        "clarity_csv",
        lambda url, tenant: url.startswith(tenant) and url.endswith("summary.csv"),
        rows=rows(),
        keep_foreign=True,
        canvass_names=CANVASS_NAMES,
    )
