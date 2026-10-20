import json
import re
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


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) parish$", "", name).lower())


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
    with connect() as conn:
        codes = {
            plain(name): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '22%'"""
            )
        }
    rows = []
    for day in days():
        try:
            stats = json.loads(raw.cached(SOURCE, blob(day, "StatsParish.htm")).path.read_text())["Stats"]["Parish"]
            categories = json.loads(raw.cached(SOURCE, blob(day, "StatsCategories.htm")).path.read_text())["Dropdown"]["Category"]
        except (LookupError, ValueError, KeyError):
            print(f"{day}: no stats")
            continue
        names = next(c["Option"] for c in categories if c["CategoryName"] == "Parish")
        found = {}
        for entry in [stats] if isinstance(stats, dict) else stats:
            if not isinstance(entry, dict) or "All_Voted" not in entry:
                continue
            index = int(entry["Option"]) - 1
            code = codes.get(plain(names[index])) if index < len(names) else None
            registered, voted = int(entry["All"]), int(entry["All_Voted"])
            if code is None or registered == 0 or voted > registered:
                continue
            found[code] = (registered, voted)
        print(f"{day}: {len(found)} parishes {sum(v[0] for v in found.values())}/{sum(v[1] for v in found.values())}")
        if len(found) >= 60 and sum(v[1] for v in found.values()) > 0:
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
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
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
