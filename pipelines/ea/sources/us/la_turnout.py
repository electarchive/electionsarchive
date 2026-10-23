import json
from datetime import date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.la_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Louisiana Secretary of State"
DATA_URL = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob="
DATES = DATA_URL + "ElectionDates.htm"


def blob(day: date, name: str) -> str:
    return f"{DATA_URL}{day:%Y%m%d}/{name}"


def days() -> list[date]:
    listed = json.loads(raw.cached(SOURCE, DATES).path.read_text())["Dates"]["Date"]
    return sorted(datetime.strptime(entry["ElectionDate"], "%m/%d/%Y").date() for entry in listed)


def fetch() -> None:
    raw.fetch(SOURCE, DATES, suffix=".json")
    for day in days():
        for name in ("StatsParish.htm", "StatsCategories.htm"):
            try:
                raw.cached(SOURCE, blob(day, name))
            except LookupError:
                try:
                    raw.fetch(SOURCE, blob(day, name), suffix=".json")
                except Exception as error:
                    print(f"{day} {name}: {error}")


def parse() -> None:
    rows = []
    for day in days():
        try:
            stats = json.loads(raw.cached(SOURCE, blob(day, "StatsParish.htm")).path.read_text())["Stats"]["Parish"]
        except (LookupError, ValueError, KeyError):
            print(f"{day}: no stats")
            continue
        found = {}
        for entry in [stats] if isinstance(stats, dict) else stats:
            if "All_Voted" not in entry or int(entry["All_Voted"]) == 0:
                continue
            registered, voted = int(entry["All"]), int(entry["All_Voted"])
            if voted > registered:
                raise ValueError(f"{day} parish {entry['Option']}: {voted} voted of {registered}")
            found[f"22{2 * int(entry['Option']) - 1:03d}"] = (registered, voted)
        print(f"{day}: {len(found)} parishes {sum(v[0] for v in found.values())}/{sum(v[1] for v in found.values())}")
        for code, (registered, voted) in found.items():
            rows.append({"date": day, "code": code, "registered": registered, "voters": voted})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "parishes.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "parishes.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana's registered voters and voters by parish for every election the Secretary of State's results portal publishes statistics for",
            polity="US",
        )
        elections: dict[date, list[tuple[str, str]]] = {}
        for value, entity_id, start in conn.execute(
            """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':LA(:[a-z]+)?$'"""
        ):
            elections.setdefault(start, []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        title = "Official election results"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url="https://voterportal.sos.la.gov/", license="Public record")
        for (day,), rows in frame.group_by("date", maintain_order=True):
            if day not in elections:
                print(f"{day}: no election")
                continue
            cs.artifact(raw.cached(SOURCE, blob(day, "StatsParish.htm")), source)
            for value_key, election_id in elections[day]:
                result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:parish")
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
                    completeness=round(rows.height / 64, 4),
                )
                for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                    for measure, value in ((2, registered), (14, voters)):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.county", code),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
