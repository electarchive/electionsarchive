from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "ca.bc"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 10, 24)
FIRST = date(2024, 10, 19)
PAGE = "https://elections.bc.ca/2026-provincial-election/candidate-list/"
CANDIDATES = "https://elections.bc.ca/2026election/2026-provincial-general-election-candidates.csv"
UNAFFILIATED = {"Independent", "Other"}
NOTE = (
    "Elections BC counts mail-in and absentee ballots at the final count, planned for 6 to 10 November 2026, and "
    "the writs are returned on 18 November. Results are recorded here once they are official."
)


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    raw.fetch(SOURCE, CANDIDATES, suffix=".csv")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.read_csv(
        raw.cached(SOURCE, CANDIDATES).path, encoding="windows-1252", infer_schema_length=0, null_values=[""]
    ).select(
        district=pl.col("ED Code"),
        district_name=pl.col("Electoral District"),
        ballot_name=pl.col("Candidate Ballot Name").str.strip_chars(),
        affiliation=pl.col("Affiliation").str.strip_chars(),
    ).write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 British Columbia general election: 93 districts and their candidates, results pending",
            polity="CA-BC",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        bc = cs.polity("CA-BC")
        province = cs.area("ca.province", "59")
        if {bc, province} & cs.minted:
            raise ValueError("run ca.elections first: British Columbia and its province area must exist")
        source = cs.source("Elections BC", "2026 Provincial General Election candidate list")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Elections BC",
            title="2026 Provincial General Election candidate list",
            url=PAGE,
        )
        for url in (PAGE, CANDIDATES):
            cs.artifact(raw.cached(SOURCE, url), source)
        cs.add("scheme", code="ca.bc", polity_id=bc, kind="electoral_district", rank=2, noun="electoral district")
        cs.add("boundary_set", scheme_code="ca.bc", vintage="2023", valid_from=FIRST, valid_to=None, as_of=FIRST)
        assembly = cs.key(f"{SOURCE}.body", "legislative_assembly")
        cs.add("body", id=assembly, polity_id=bc, kind="unicameral")
        name(assembly, "Legislative Assembly of British Columbia")
        cs.add("body_size", body_id=assembly, seats=93, valid_from=FIRST, valid_to=None)
        fptp = cs.key(f"{SOURCE}.electoral_system", "fptp")
        cs.add("electoral_system", id=fptp, polity_id=bc, rules={"formula": "fptp"}, rules_version=1)
        name(fptp, "First past the post")
        parties: dict[str, UUID] = {}
        for affiliation in sorted(set(candidates["affiliation"].drop_nulls()) - UNAFFILIATED):
            parties[affiliation] = cs.key(f"{SOURCE}.party", affiliation)
            cs.add("party", id=parties[affiliation], kind="party", polity_id=bc)
            name(parties[affiliation], affiliation)
        election = cs.key(f"{SOURCE}.election", str(DAY))
        cs.add("election", id=election, polity_id=bc, kind="general", date_from=DAY, date_to=DAY, status="held")
        name(election, "2026 British Columbia general election")
        cs.add("slug", path=f"ca/bc/{DAY}-general", entity_id=election)
        cs.add("note", entity_id=election, kind="context", body_md=NOTE)
        for (code, district_name), rows in sorted(candidates.group_by("district", "district_name")):
            area = cs.area("ca.bc", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="ca.bc")
                cs.add("area_code", area_id=area, code=code, valid_from=FIRST, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=province)
                name(area, district_name)
            contest = cs.key(f"{SOURCE}.contest", f"{DAY}:{code}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=assembly,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"2026 election in {district_name}")
            cs.add("slug", path=f"ca/bc/{DAY}-general/{slug(district_name)}", entity_id=contest)
            for position, (ballot_name, affiliation) in enumerate(
                rows.select("ballot_name", "affiliation").iter_rows(), 1
            ):
                person = cs.key(f"{SOURCE}.person", f"{DAY}:{code}:{ballot_name}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, ballot_name)
                option = cs.key(f"{SOURCE}.option", f"{DAY}:{code}:{ballot_name}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties.get(affiliation),
                    ballot_position=position,
                    ballot_label=ballot_name,
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{DAY}:{code}:{ballot_name}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=ballot_name,
                )
        return cs.write()
