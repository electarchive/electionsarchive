import re
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.wa_archive"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.sos.wa.gov/sites/default/files/2026-04/Washington%20State%20Elections%20Results%20Archive.xlsx"
LANDING = (
    "https://www.sos.wa.gov/elections/data-research/election-data-and-maps/election-results-and-voters-pamphlets/"
    "washington-state-election-results-archive"
)
PUBLISHER = "Washington Secretary of State"
TITLE = "Washington State Elections Results Archive"
LICENSE = "Public record"
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
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "Green": "green-party",
    "Natural Law": "natural-law-party",
    "Constitutional": "constitution-party",
    "Reform": "reform-party",
    "Prohibition": "prohibition-party",
    "American Independent": "american-independent-party",
    "Socialist Workers": "socialist-workers-party",
    "Socialist Labor": "socialist-labor-party-of-america",
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xlsx")


def parse() -> None:
    frame = pl.read_excel(raw.cached(SOURCE, URL).path)
    frame = frame.filter(
        (pl.col("OfficeType") == "Statewide")
        & (pl.col("ElectionTypeID") == "General")
        & pl.col("OfficeName").is_in(list(OFFICES))
    ).select(
        year=pl.col("Description").str.extract(r"^(\d{4}) General$", 1).cast(pl.Int64),
        office="OfficeName",
        name=pl.col("BallotName").str.replace_all(r"\s+", " ").str.strip_chars(),
        party="PartyName",
        votes="Votes",
        order="BallotOrder",
    )
    if frame["year"].null_count():
        raise ValueError("unexpected election descriptions")
    doubled = frame.group_by("year", "office", "name").len().filter(pl.col("len") > 1)
    if doubled.height:
        raise ValueError(f"names printed twice: {doubled}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("year").agg(pl.col("office").n_unique(), pl.col("votes").sum()).sort("year"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington's lieutenant governor, secretary of state, attorney general, treasurer, auditor, "
            "commissioner of public lands, insurance commissioner, and superintendent elections 1900–2004 by state "
            "from the Secretary of State's results archive",
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
        cs.add(
            "source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=LANDING, license=LICENSE
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        ((area, state_code),) = conn.execute(
            """select u.area_id, u.code from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-WA'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ ':WA:' and value ~ '^[0-9]{4}-'"
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
        filled = 0
        for (year, office), lines in frame.sort("year", "office").group_by("year", "office", maintain_order=True):
            first = date(int(year), 11, 1)
            first_monday = first + timedelta(days=(7 - first.weekday()) % 7)
            day = first_monday + timedelta(days=1)
            base, title = OFFICES[str(office)]
            special = f"{day}:WA" not in elections
            key = f"{day}:WA:{base}" + (":special" if special else "")
            if special:
                election = cs.key("us.election", f"{day}:WA")
                elections[f"{day}:WA"] = election
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-WA"),
                    kind="special",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day:%B} {day.day}, {day.year} Washington special election")
                cs.add("slug", path=f"us/wa/{day}-special", entity_id=election)
            election = elections[f"{day}:WA"]
            if key in contests:
                continue
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("us.office", f"{base}:WA"),
                district_area_id=area,
                term="remainder" if special else "full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{year} Washington {title} {'special election' if special else 'election'}")
            cs.add("slug", path=f"us/wa/{day}-{'special' if special else 'general'}/{base}", entity_id=contest)
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
            for candidate, party, votes in lines.sort("order").select("name", "party", "votes").iter_rows():
                probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                found = {person for person, seen in people.get(probe, []) if abs(seen - int(year)) <= 16}
                person = (
                    next(iter(found)) if len(found) == 1 else cs.key(f"{SOURCE}.person", f"WA:{slug(candidate)}:{year}")
                )
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                    people.setdefault(probe, []).append((person, int(year)))
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
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.state", state_code),
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
                totals[option] = votes
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="elected",
                seats=1,
            )
            filled += 1
        print(filled, "contests")
        return cs.write()
