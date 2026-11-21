"""County and city contests in the Secretary of State's certified Georgia export, matched to the archive's contests.

The export's local results carry contests that appear in only some counties: county commission and school board seats,
solicitors general, soil and water supervisors, city council and mayor races, and local referendums. The archive
already holds the county elections of November 3, 2026 with the candidates the counties certified for the ballot;
this module matches each portal contest to one of them by the county and the candidates' names and attaches the
certified votes by county and by precinct.
"""

import re
from collections import defaultdict
from typing import Any, NamedTuple

import duckdb

from ea.store import DATA

REFERENCE = DATA / "reference" / "us"
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv)\b\.?")


def person(label: str) -> str:
    """A candidate's name reduced to letters and digits, without the incumbent and party marks the portal appends."""
    text = re.sub(r"\([^)]*\)", "", label.lower())
    return re.sub(r"[^a-z0-9]", "", SUFFIX.sub("", text))


class Local(NamedTuple):
    id: str
    name: str
    pipeline: str
    election: str
    options: dict[str, str]  # normalized ballot label -> option id


def county_contests() -> dict[str, list[Local]]:
    """The archive's November 3, 2026 contests in Georgia's county elections, by county FIPS code."""
    con = duckdb.connect()
    path = {
        name: str(REFERENCE / f"{name}.parquet")
        for name in ("contest", "contest_option", "election", "external_id", "result_set")
    }
    found: dict[str, dict[str, Local]] = defaultdict(dict)
    for fips, contest, name, pipeline, election, option, label in con.execute(
        f"""select substr(x.value, 8), c.id, c.name, c.pipeline, c.election_id, o.id, o.ballot_label
            from '{path["contest"]}' c
            join '{path["election"]}' e on e.id = c.election_id
            join '{path["external_id"]}' x on x.entity_id = e.polity_id and x.scheme = 'us.polity'
            join '{path["contest_option"]}' o on o.contest_id = c.id
            where e.date_from = date '2026-11-03' and x.value like 'county:13%'"""
    ).fetchall():
        entry = found[fips].setdefault(contest, Local(contest, name, pipeline, election, {}))
        entry.options[person(label)] = option
    return {fips: list(by.values()) for fips, by in found.items()}


def held(contests: list[str]) -> set[str]:
    """The contests that already hold a result set another pipeline wrote."""
    con = duckdb.connect()
    path = str(REFERENCE / "result_set.parquet")
    marks = ",".join("?" for _ in contests)
    return {
        found
        for (found,) in con.execute(
            f"select distinct contest_id from '{path}' where contest_id in ({marks})", contests
        ).fetchall()
    }


# Referendums the archive holds, by county FIPS code and the portal's item id. The portal's Butts County homestead
# referendums name two acts and the archive's name two exemption amounts, so those two are not matched.
MEASURES = {
    ("13067", "48"): "2026 Cobb County: Special Purpose Local Option Sales Tax (SPLOST)",
    ("13121", "56"): "2026 Fulton County: Transportation SPLOST",
    ("13255", "23"): "2026 Spalding County: Special Election: County 1% Sales and Use Tax",
}


def match(fips: str, item: dict[str, Any], contests: list[Local], taken: set[str]) -> Local | None:
    """The archive contest for a portal item: by its candidates, or for a referendum by the table above."""
    if (fips, item["id"]) in MEASURES:
        hits = [c for c in contests if c.name == MEASURES[fips, item["id"]]]
        return hits[0] if len(hits) == 1 and hits[0].id not in taken else None
    return match_candidates(item, contests, taken)


def match_candidates(item: dict[str, Any], contests: list[Local], taken: set[str]) -> Local | None:
    """The one archive contest whose candidates include every option on the portal's contest."""
    options = {person(option["name"]) for option in item["ballotOptions"]}
    if not options or options & {"yes", "no"}:
        return None
    hits = [c for c in contests if c.id not in taken and options <= set(c.options)]
    return hits[0] if len(hits) == 1 else None
