import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.tx_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Texas Secretary of State"
URL = "https://www.sos.state.tx.us/elections/historical/70-92.shtml"
TITLE = "Turnout and Voter Registration Figures (1970-current)"
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}
HEADING = re.compile(r"^((?:19|20)\d\d)\s*[–-]\s*(.*)$")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def number(line: str) -> int | None:
    digits = re.sub(r"\D", "", line)
    return int(digits) if digits else None


def parse() -> None:
    text = raw.cached(SOURCE, URL).path.read_text(encoding="utf8", errors="replace")
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", text)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rows = []
    for i, line in enumerate(lines):
        match = HEADING.match(line)
        if match is None:
            continue
        year, rest = int(match.group(1)), match.group(2).lower()
        registered = voters = None
        for j in range(i + 1, min(i + 14, len(lines))):
            if HEADING.match(lines[j]):
                break
            label = lines[j].lower()
            if label.startswith("registered voters"):
                registered = number(lines[j + 1])
            elif label.startswith("turnout"):
                voters = number(lines[j + 1])
        month = next((m for name, m in MONTHS.items() if name in rest), None)
        stage = (
            "runoff"
            if "runoff" in rest
            else "primary"
            if "primary" in rest
            else "special"
            if "special" in rest and month != 11
            else "general"
        )
        if stage == "general":
            month = 11
        rows.append(
            {"year": year, "month": month, "stage": stage, "heading": line, "registered": registered, "voters": voters}
        )
    frame = pl.DataFrame(rows, schema_overrides={"month": pl.Int64}).filter(pl.col("voters").is_not_null())
    frame = (
        frame.group_by("year", "month", "stage")
        .agg(pl.col("registered").max(), pl.col("voters").sum(), pl.col("heading").first(), pl.len().alias("blocks"))
        .sort("year", "month", "stage")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Texas's registered voters and turnout statewide for every general, primary, runoff, and special election "
            "from 1970 on, from the Secretary of State's turnout and registration figures",
            polity="US",
        )
        elections: dict[tuple[int, int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':TX(:[a-z]+)?$' and e.kind in ('general', 'primary', 'runoff', 'special')"""
        ):
            elections.setdefault((start.year, start.month, kind), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        fips = "48"
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for year, month, stage, registered, voters in frame.select(
            "year", "month", "stage", "registered", "voters"
        ).iter_rows():
            found = [
                f
                for (y, m, k), items in elections.items()
                if y == year and k == stage and month in (None, m)
                for f in items
            ]
            if len(found) > 1 and stage == "general":
                first = date(year, 11, 1)
                day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                found = [f for f in found if f[0].startswith(day.isoformat())]
            if len(found) != 1:
                print(f"{year}-{month} {stage}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
            result_set = cs.key(f"{SOURCE}.result_set", value_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=1,
            )
            for measure, value in ((2, registered), (14, voters)):
                if value is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.state", fips),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
