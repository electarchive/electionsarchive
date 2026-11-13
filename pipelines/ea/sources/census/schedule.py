import os
from datetime import UTC, datetime
from pathlib import Path

from ea.census_calendar import fixed_rules, horizon, occurrences
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "census.schedule"
VERSION = "1"


def fetch() -> None:
    pass


def parse() -> None:
    pass


def emit() -> Path:
    country = os.environ.get("EA_SCHEDULE_POLITY", "US")
    today = datetime.now(UTC).date()
    until = horizon(today)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scheduled elections for the next 18 months computed from fixed cadence rows that give a day rule, one per polity, date, and kind, each citing the statute of the rule that dates it",
            polity=country,
        )
        found: dict[tuple, list[dict]] = {}
        for rule in fixed_rules(conn, today, country):
            kind = (
                "local"
                if rule["government_kind"] not in (None, "national")
                or rule["polity_kind"] not in ("country", "federal_unit", "sovereign_state")
                else "general"
            )
            for when in occurrences(
                rule["day_rule"],
                rule["period_years"],
                rule["phase"],
                today,
                until,
                rule["valid_from"],
                rule["valid_to"],
            ):
                found.setdefault((rule["polity_id"], when, kind), []).append(rule)
        listed = 0
        for (polity, when, kind), rules in sorted(found.items(), key=lambda item: (item[0][1], str(item[0][0]))):
            near = conn.execute(
                """select 1 from election e join entity en on en.id = e.id and en.retired_by is null
                   where e.polity_id = %s and e.date_from between %s and %s""",
                [polity, when, when],
            ).fetchone()
            if near is not None:
                continue
            named = conn.execute(
                """select n.text, s.path from polity p
                   join name n on n.entity_id = p.id and n.lang = 'en' and n.is_preferred
                   join slug s on s.entity_id = p.id and s.is_current where p.id = %s""",
                [polity],
            ).fetchone()
            if named is None:
                continue
            place, path = named
            election = cs.key("census.election", f"{polity}:{when}:{kind}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=None,
                kind=kind,
                date_from=when,
                date_to=when,
                status="scheduled",
            )
            cs.add(
                "name",
                entity_id=election,
                text=f"{when:%B} {when.day}, {when.year} {place} {kind} elections",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"{path}/{when}-{kind}", entity_id=election)
            for cited in {(r["source_id"], r["locator"]) for r in rules}:
                cs.add("citation", entity_id=election, source_id=cited[0], locator={"section": cited[1]})
            listed += 1
        print(f"{listed} scheduled elections from fixed cadence rows through {until}")
        return cs.write()
