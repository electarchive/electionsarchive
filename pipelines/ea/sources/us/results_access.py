from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid5

from ea.changeset import NAMESPACE, Changeset
from ea.store import connect

SOURCE = "us.results_access"
VERSION = "1"
COLUMNS = (
    "polity_id",
    "serves",
    "platform",
    "url_pattern",
    "format",
    "access",
    "first_date",
    "last_date",
    "checked_at",
    "evidence",
)
STATEWIDE = [
    {
        "iso": "US-GA",
        "serves": "administered",
        "platform": "clarity",
        "url_pattern": "https://results.enr.clarityelections.com/GA/",
        "format": "json",
        "access": "open",
        "first_date": date(2012, 7, 31),
        "last_date": date(2024, 6, 18),
        "checked_at": datetime.fromisoformat("2026-11-10T12:41:58Z"),
        "evidence": "93 elections; https://results.enr.clarityelections.com/GA/elections.json",
    }
]


def key(row: Mapping[str, object]) -> UUID:
    return uuid5(NAMESPACE, f"results_access\x1f{row['polity_id']}\x1f{row['platform']}\x1f{row['url_pattern']}")


def current() -> list[dict[str, object]]:
    with connect() as conn:
        cursor = conn.execute(
            f"select {', '.join(f'r.{c}' for c in COLUMNS)} from results_access r"
            " join changeset c on c.id = r.changeset_id where c.pipeline = %s",
            [SOURCE],
        )
        return [dict(zip(COLUMNS, values, strict=True)) for values in cursor.fetchall()]


def write(rows: list[dict[str, object]]) -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Where US county and statewide election results appear online: each results portal's platform, URL, format, access from a script, and the span of elections it lists, as last probed",
            polity="US",
        )
        cs.manifest["scope"] = {"polity": "US", "sweeps": ["results_access"]}
        merged = {key(row): row for row in rows}
        for row in STATEWIDE:
            spec: dict[str, object] = {k: v for k, v in row.items() if k != "iso"} | {
                "polity_id": cs.polity(str(row["iso"]))
            }
            merged[key(spec)] = spec
        tally: Counter[str] = Counter()
        for id, row in sorted(merged.items()):
            cs.add("results_access", id=id, **{column: row.get(column) for column in COLUMNS})
            tally[str(row["access"])] += 1
        print(dict(tally))
        return cs.write()


def emit() -> Path:
    return write(current())
