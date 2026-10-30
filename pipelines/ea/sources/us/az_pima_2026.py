import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
from ea.store import DATA, connect

SOURCE = "us.az_pima_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Pima County Proposition 425: Permanent Base Adjustment, Ballot Language"
PIMA = "https://content.civicplus.com/api/assets/az-pimacounty/55aa379c-bc22-47fe-8b7c-d10dab0ade8c/permanent-base-adjustment-proposition-pdf.pdf"
COUNTIES = {"04019": (PIMA, "Pima County Board of Supervisors")}
ROWS = [
    {
        "county": "04019",
        "entity": "Pima County",
        "label": "Proposition 425: Permanent Base Adjustment",
        "wording": "A resolution proposing a permanent adjustment to the state-imposed 1979-80 expenditure base for Pima County. Pursuant to the Arizona State Constitution, Pima County seeks voter approval to permanently adjust the expenditure base of the County as determined by the Economic Estimates Commission. The County seeks an adjustment of $70,000,000. A \u201cYES\u201d vote shall have the effect of allowing Pima County to adjust its 1979-80 base expenditure limit. A \u201cNO\u201d vote shall have the effect of not allowing Pima County to adjust its 1979-80 base expenditure limit.",
    },
]


def fetch() -> None:
    for url, _ in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            content = subprocess.run(["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True).stdout
            raw.store(SOURCE, url, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame([row | {"url": COUNTIES[row["county"]][0]} for row in ROWS])
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Pima County Arizona Proposition 425 on the November 3, 2026 ballot, with official wording",
            polity="US-AZ",
        )
        civil = civmint.Civil(conn, cs, "04", "AZ")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (url, publisher) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:AZ")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            county_key = f"us.county:{row['county']}"
            source = sources[row["county"]]
            option = None
            if re.match(r"(?i)(city|town|village) of ", entity):
                option = civil.place(re.sub(r"(?i)^(city|town|village) of ", "", entity))
            elif county_key in found and entity == found[county_key][2]:
                option = county_key
            elif entity.endswith(" Township"):
                option = civil.town(entity, row["county"][:5])
            elif re.search(r"School District|CUSD", entity):
                option = civil.school(entity)
            elif f"special:{row['county']}:{entity}" in found:
                option = f"special:{row['county']}:{entity}"
            elif county_key in found:
                county, county_area, _, county_path, county_code = found[county_key]
                minted = govmint.special(conn, cs, "AZ", (county, county_area, county_path, county_code), entity, civil.slugs)
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:AZ:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add("name", entity_id=election, text=f"{DAY.year} {place} general election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["label"]
            contest_key = f"{DAY}:AZ:{code}:measure:{slug(label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            if civil.slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            civil.slugs[contest_path] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add("name", entity_id=contest, text=f"{DAY.year} {place}: {label}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("citation", entity_id=contest, source_id=source)
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["wording"],
                url=row["url"],
                source_id=source,
            )
        print(f"{kept} of {frame.height} referenda")
        return cs.write()
