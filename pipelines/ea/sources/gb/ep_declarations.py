import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.ep_declarations"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DECLARATIONS = {
    "london-2019": (
        "https://haringey.gov.uk/sites/default/files/2023-10/epe_2019_overall_london_votes.pdf",
        "GB:2019:gb.ep_constituency:london",
        "Regional Returning Officer, London Region",
        "Declaration of result, European Parliamentary Election, 23 May 2019, London Region",
    ),
}
PARTIES = {
    "Animal Welfare Party": "animal-welfare-party",
    "Change UK": "the-independent-group-for-change",
    "Conservatives": "conservative",
    "Green Party": "green-party",
    "Labour Party": "labour",
    "Liberal Democrats": "liberal-democrats",
    "The Brexit Party": "reform-uk",
    "UKEUP": "uk-european-union-party",
    "UKIP": "uk-independence-party",
    "Women's Equality Party": "women-s-equality-party",
}
EXISTING = {"conservative": ("wikidata", "Q9626"), "labour": ("wikidata", "Q9630")}
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
LINE = re.compile(r"^\s*(\S.*?\S)\s{3,}([\d,]+)\s*$")


def fetch() -> None:
    for url, *_ in DECLARATIONS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows = []
    for declaration, (url, *_) in DECLARATIONS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        votes, rejected = text.split("rejected ballot papers", 1)
        for line in votes.splitlines():
            if (found := LINE.match(line)) and "Number of Votes" not in line:
                rows.append({"declaration": declaration, "line": found[1], "kind": "option", "value": found[2]})
        for line in rejected.splitlines():
            if (found := LINE.match(line)) and found[1] == "TOTAL":
                rows.append({"declaration": declaration, "line": "rejected", "kind": "total", "value": found[2]})
        totals = re.search(r"([\d,]{7,})\s+([\d,]{7,})\s+[\d.]+%", rejected)
        if totals is None:
            raise ValueError(f"{declaration}: no electorate and ballot paper totals")
        rows.append({"declaration": declaration, "line": "electorate", "kind": "total", "value": totals[1]})
        rows.append({"declaration": declaration, "line": "ballots", "kind": "total", "value": totals[2]})
    frame = pl.DataFrame(rows).with_columns(pl.col("value").str.replace_all(",", "").cast(pl.Int64))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    print(frame.group_by("declaration", "kind").len())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="European Parliament results in Great Britain from the regional returning officers' declarations",
            polity="GB",
        )
        parties = {}
        for label, key in PARTIES.items():
            parties[label] = cs.key(*EXISTING[key]) if key in EXISTING else cs.key("gb.party", key)
            if parties[label] in cs.minted:
                raise ValueError(f"{key} is not in the database")
        for declaration, lines in frame.group_by("declaration", maintain_order=True):
            url, contest_key, publisher, title = DECLARATIONS[declaration[0]]
            contest = cs.key("eu.contest", contest_key)
            if contest in cs.minted:
                raise ValueError(f"no contest {contest_key}")
            (election, area) = conn.execute(
                "select election_id, district_area_id from contest where id = %s", [contest]
            ).fetchone()
            (unit,) = conn.execute(
                "select id from reporting_unit where area_id = %s and area_relation = 'equals' limit 1", [area]
            ).fetchone()
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
            )
            cs.artifact(raw.cached(SOURCE, url), source)
            result_set = cs.key(f"{SOURCE}.result_set", declaration[0])
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=1,
            )
            totals = dict(lines.filter(pl.col("kind") == "total").select("line", "value").iter_rows())
            options = lines.filter(pl.col("kind") == "option")
            valid = int(options["value"].sum())
            if valid + totals["rejected"] != totals["ballots"]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The declaration's valid votes ({valid:,}) and rejected ballot papers "
                    f"({totals['rejected']:,}) add to {valid + totals['rejected']:,}, not its printed total of "
                    f"{totals['ballots']:,} ballot papers. All three figures are stored as printed.",
                )
            for measure, value in (
                (REGISTERED, totals["electorate"]),
                (BALLOTS, totals["ballots"]),
                (VALID, valid),
                (INVALID, totals["rejected"]),
            ):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
            for position, (label, votes) in enumerate(options.select("line", "value").iter_rows(), start=1):
                option = cs.key(f"{SOURCE}.option", f"{declaration[0]}:{slug(label)}")
                party = parties.get(label)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list" if party is not None else "candidate",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=label,
                )
                if party is None:
                    person = cs.key(f"{SOURCE}.person", f"{declaration[0]}:{slug(label)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{declaration[0]}:{slug(label)}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=True,
                    )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
