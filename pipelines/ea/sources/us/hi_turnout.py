import re
from datetime import date
from html import unescape
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.hi_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Hawaii Office of Elections"
URL = "https://elections.hawaii.gov/resources/registration-voter-turnout-statistics/"
TITLE = "Registration and Turnout Statistics"
UNITS = {
    "Statewide": "15",
    "County of Hawaii": "15001",
    "City & County of Honolulu": "15003",
    "County of Kauai": "15007",
    "County of Maui": "15009",
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".html")


def parse() -> None:
    text = raw.cached(SOURCE, URL).path.read_text(encoding="utf8", errors="replace")
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", text)
    text = re.sub(r"</(tr|h\d|p|table|div)>", "\n", text)
    text = unescape(re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", text)))
    rows = []
    for stage, heading in (("primary", "Primary Elections (1959-2024)"), ("general", "General Elections (1959-2024)")):
        start = text.rindex(heading)
        ends = [
            e
            for e in (
                text.find("Elections (1959-2024)", start + len(heading)),
                text.find("Office of Hawaiian Affairs", start),
            )
            if e > 0
        ]
        block = text[start : min(ends, default=len(text))]
        parts = re.split(
            r"\n\s*(Statewide|County of Hawaii|County of Maui|County of Kauai|City & County of Honolulu)\s*\n", block
        )
        for name, body in zip(parts[1::2], parts[2::2]):
            for year, registered, voters in re.findall(r"\b((?:19|20)\d\d) ([\d,]+) ([\d,]+) [\d.]+%", body):
                rows.append(
                    {
                        "stage": stage,
                        "unit": name,
                        "year": int(year),
                        "registered": int(registered.replace(",", "")),
                        "voters": int(voters.replace(",", "")),
                    }
                )
    frame = pl.DataFrame(rows)
    counts = frame.group_by("stage", "unit").len().sort("stage", "unit")
    print(counts)
    state = frame.filter(pl.col("unit") == "Statewide").select(
        "stage", "year", pl.col("registered").alias("sr"), pl.col("voters").alias("sv")
    )
    summed = (
        frame.filter(pl.col("unit") != "Statewide")
        .group_by("stage", "year")
        .agg(pl.col("registered").sum(), pl.col("voters").sum(), pl.len().alias("n"))
        .join(state, on=["stage", "year"])
    )
    print(
        summed.filter((pl.col("n") != 4) | (pl.col("registered") != pl.col("sr")) | (pl.col("voters") != pl.col("sv")))
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Hawaii's registered voters and voter turnout by county for its primary and general elections 1959–2024, from the Office of Elections' registration and turnout statistics",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str, date]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':HI(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            elections.setdefault((start.year, kind), []).append((value, entity_id, start))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
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
        for (year, stage), group in frame.group_by(["year", "stage"], maintain_order=True):
            found = elections.get((year, stage), [])
            if len(found) > 1 and stage == "general":
                first = date(year, 11, 1)
                day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                found = [f for f in found if f[2] == day]
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
                continue
            value_key, election_id, _ = found[0]
            counties = group.filter(pl.col("unit") != "Statewide")
            state = group.filter(pl.col("unit") == "Statewide")
            by_county = (
                counties.height == 4
                and counties["registered"].sum() == state["registered"][0]
                and abs(counties["voters"].sum() - state["voters"][0]) <= state["voters"][0] // 200
            )
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
            for name, registered, voters in (
                (counties if by_county else state).select("unit", "registered", "voters").iter_rows()
            ):
                for measure, value in ((2, registered), (3, voters)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", UNITS[name]) if by_county else cs.unit("us.state", "15"),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
