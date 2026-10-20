import csv
import io
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wa_results"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2008, 11, 4)
SITE = "https://results.vote.wa.gov/results/20081104/export/20081104_{}.csv"
LANDING = "https://results.vote.wa.gov/results/20081104/"
PUBLISHER = "Washington Secretary of State"
TITLE = "November 4, 2008 General Election Results"
LICENSE = "Public record"
COUNTIES = (
    "Adams Asotin Benton Chelan Clallam Clark Columbia Cowlitz Douglas Ferry Franklin Garfield Grant GraysHarbor "
    "Island Jefferson King Kitsap Kittitas Klickitat Lewis Lincoln Mason Okanogan Pacific PendOreille Pierce SanJuan "
    "Skagit Skamania Snohomish Spokane Stevens Thurston Wahkiakum WallaWalla Whatcom Whitman Yakima"
).split()
OFFICES = {
    "Lt. Governor": ("lieutenant-governor", "Lieutenant Governor"),
    "Commissioner of Public Lands": ("commissioner-of-public-lands", "Commissioner of Public Lands"),
    "Secretary of State": ("secretary-of-state", "Secretary of State"),
    "Attorney General": ("attorney-general", "Attorney General"),
    "Insurance Commissioner": ("insurance-commissioner", "Insurance Commissioner"),
    "Superintendent of Public Instruction": (
        "superintendent-of-public-instruction",
        "Superintendent of Public Instruction",
    ),
    "State Treasurer": ("treasurer", "Treasurer"),
    "State Auditor": ("auditor", "Auditor"),
}
PARTIES = {"Democratic": "democratic-party", "Republican": "republican-party", "G.O.P.": "republican-party"}


def fetch() -> None:
    for name in ("StateExecutive", *COUNTIES):
        raw.fetch(SOURCE, SITE.format(name), suffix=".csv")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for name in COUNTIES:
        text = raw.cached(SOURCE, SITE.format(name)).path.read_text(encoding="utf-8-sig")
        for line in csv.DictReader(io.StringIO(text)):
            race = re.sub(r"^Washington State ", "", line["Race"])
            if race not in OFFICES:
                continue
            party = re.search(r"Prefers (.+?) Party", line["Party"])
            rows.append(
                {
                    "county": name,
                    "office": race,
                    "name": re.sub(r"\s+", " ", line["Candidate"]).strip(),
                    "party": party[1] if party else "",
                    "votes": int(line["Votes"]),
                }
            )
    frame = pl.DataFrame(rows)
    printed = {
        (row["Race"], row["Candidate"].strip()): int(row["Votes"])
        for row in csv.DictReader(io.StringIO(raw.cached(SOURCE, SITE.format("StateExecutive")).path.read_text(encoding="utf-8-sig")))
        if row["Race"] in OFFICES
    }
    sums = frame.group_by("office", "name").agg(pl.col("votes").sum(), pl.col("county").n_unique())
    if sums.height != len(printed):
        raise ValueError(f"{sums.height} candidates, {len(printed)} printed")
    for office, name, votes, counties in sums.iter_rows():
        if counties != 39 or printed[(office, name)] != votes:
            raise ValueError(f"{office} {name}: {votes} over {counties} counties, printed {printed[(office, name)]}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(sums.sort("office", "votes"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's 2008 lieutenant governor, secretary of state, attorney general, treasurer, auditor, "
            "commissioner of public lands, insurance commissioner, and superintendent elections by county from the "
            "Secretary of State's results exports",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [
                word
                for word in re.sub(r"\([^)]*\)|\"[^\"]*\"", " ", text).replace(",", " ").split()
                if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}
            ]
            return plain(words[-1])

        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=LANDING, license=LICENSE)
        for county in ("StateExecutive", *COUNTIES):
            cs.artifact(raw.cached(SOURCE, SITE.format(county)), source)
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-WA'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '53%%'"""
            ).fetchall()
        }
        election = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall())[
            f"{DAY}:WA"
        ]
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^2008-11-04:WA:'"
            )
        }
        people: dict[str, list[tuple[UUID, int]]] = {}
        for person, text, year in conn.execute(
            """select distinct cd.person_id, n.text, extract(year from e.date_from)::int from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id
               join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.district_area_id = %s""",
            [area],
        ).fetchall():
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", []).append((person, year))
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must already exist")
        made: set[UUID] = set()
        for office, lines in frame.sort("office").group_by("office", maintain_order=True):
            base, title = OFFICES[str(office[0])]
            key = f"{DAY}:WA:{base}"
            if key in contests:
                print(key, "is already in the archive")
                continue
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", f"{base}:WA"),
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{DAY.year} Washington {title} election")
            cs.add("slug", path=f"us/wa/{DAY}-general/{base}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            totals: dict[UUID, int] = {}
            for (candidate, party), cells in lines.group_by("name", "party", maintain_order=True):
                probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                found = {person for person, seen in people.get(probe, []) if abs(seen - DAY.year) <= 16}
                person = next(iter(found)) if len(found) == 1 else cs.key(f"{SOURCE}.person", f"WA:{slug(candidate)}")
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                    people.setdefault(probe, []).append((person, DAY.year))
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties.get(party),
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=party not in PARTIES,
                )
                totals[option] = int(cells["votes"].sum())
                for county, votes in cells.select("county", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[plain(county)],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            cs.add("outcome", result_set_id=result_set, option_id=max(totals, key=totals.__getitem__), kind="elected", seats=1)
        return cs.write()
