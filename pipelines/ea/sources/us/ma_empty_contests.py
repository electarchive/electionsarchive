import re
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.sources.us import ma_empty_primaries
from ea.store import connect

SOURCE = "us.ma_empty_contests"
VERSION = "1"
COLUMNS = [
    "election_id",
    "kind",
    "body_id",
    "office_id",
    "district_area_id",
    "seat_id",
    "term",
    "magnitude",
    "boundary_set_id",
    "round_number",
    "previous_round_id",
    "electoral_system_id",
    "allocation_parent_id",
    "default_result_kind",
    "rules_override",
]
LABELS = {
    "democratic": "Democratic",
    "republican": "Republican",
    "green-rainbow": "Green-Rainbow",
    "libertarian": "Libertarian",
    "united-independent-party": "United Independent Party",
}


def emit() -> Path:
    frame = pl.read_parquet(ma_empty_primaries.PARSED / "counties.parquet").select("body", "party", "day", "district").unique()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Massachusetts's congressional primaries that drew no candidate, as contests for the write-in town "
            "rows to attach to",
            polity="US-MA",
        )
        held = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ ':MA:(house:[0-9]+|senate):[a-z-]+$'"
            ).fetchall()
        }
        scopes = {
            value.rsplit(":", 1)[1]: party
            for value, party in conn.execute(
                """select x.value, c.party_scope_id from contest c join external_id x on x.entity_id = c.id
                   where x.scheme = 'us.contest' and x.value ~ ':MA:(house:[0-9]+|senate):[a-z-]+$'
                     and c.party_scope_id is not null"""
            ).fetchall()
        }
        scopes["united-independent-party"] = cs.key("us.party", "MA:united-independent-party")
        if scopes["united-independent-party"] in cs.minted:
            cs.add("party", id=scopes["united-independent-party"], kind="party", polity_id=cs.polity("US-MA"))
            cs.add(
                "name",
                entity_id=scopes["united-independent-party"],
                text="United Independent Party",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        made = 0
        for body, party, day, district in frame.sort("day", "body", "district", "party").iter_rows():
            key = f"{day}:MA:house:{district}:{party}" if body == "house" else f"{day}:MA:senate:{party}"
            if key in held or (body == "senate" and party == "green-rainbow" and f"{day}:MA:senate:green" in held):
                continue
            like = f"^{day}:MA:house:{district}:[a-z-]+$" if body == "house" else f"^{day}:MA:senate:[a-z-]+$"
            sibling = conn.execute(
                f"""select c.{", c.".join(COLUMNS)}, (select text from name where entity_id = c.id and is_preferred limit 1),
                           (select path from slug where entity_id = c.id limit 1), x.value
                    from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                    where x.value ~ %s order by x.value limit 1""",
                [like],
            ).fetchone()
            if sibling is None:
                raise ValueError(f"{key}: no sibling contest to copy the district from")
            *values, title, path, sibling_key = sibling
            old = sibling_key.rsplit(":", 1)[1]
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                party_scope_id=scopes[party],
                **dict(zip(COLUMNS, values, strict=True)),
            )
            cs.add(
                "name",
                entity_id=contest,
                text=re.sub(r"(\d{4} U\.S\. (?:House|Senate) ).+? primary", rf"\g<1>{LABELS[party]} primary", title),
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=path.removesuffix(old) + party, entity_id=contest)
            made += 1
        print(made, "contests")
        return cs.write()
