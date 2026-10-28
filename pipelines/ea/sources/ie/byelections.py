import json
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.ie.dail import BALLOTS, EXHAUSTED, INVALID, PARTY_CODES, QUOTA, REGISTERED, VALID, VOTES, fold
from ea.store import DATA, connect

SOURCE = "ie.byelections"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
DAIL = date(2026, 5, 22)
SEANAD = date(2026, 11, 6)
RESULTS = "https://data.oireachtas.ie/ie/oireachtas/electoralProcess/electionResults"
DECLARATIONS = {
    "Dublin Central": f"{RESULTS}/dail/2026/2026-07-10_bye-election-to-the-34th-dail-which-was-held-on-22nd-may-2026-constituency-of-dublin-central_en.pdf",
    "Galway West": f"{RESULTS}/dail/2026/2026-07-10_bye-election-to-the-34th-dail-which-was-held-on-22nd-may-2026-constituency-of-galway-west_en.pdf",
}
CANDIDATES = f"{RESULTS}/seanad/2026/2026-10-16_seanad-bye-elections-2026-list-of-candidates_en.pdf"
LABELS = {
    "Green Party - Comhaontas Glas": "Green Party",
    "People Before Profit / Solidarity": "People Before Profit–Solidarity",
}
LICENSE = "Oireachtas (Open Data) PSI Licence"
LICENSE_URL = "https://www.oireachtas.ie/en/open-data/license/"
SEANAD_CANDIDATES = (
    ("Ray McAdam", "FG"),
    ("Caitlín Ní Bhroin", None),
    ("Maeve Maria O'Rourke", None),
    ("Patrick Paul Walsh", None),
)
SEANAD_NOTE = (
    "Seán Kyne's seat on the Cultural and Educational Panel became vacant when he was elected to the Dáil for Galway "
    "West on 22 May 2026. The four candidates were nominated on the panel's nominating bodies sub-panel. The "
    "electorate is the members of the Dáil and the Seanad, who vote by post, and the poll closes at 11:00 on "
    "6 November 2026."
)
CAUSES = {
    "Dublin Central": "The by-election filled the seat Paschal Donohoe vacated when he resigned from the Dáil.",
    "Galway West": "The by-election filled the seat Catherine Connolly vacated when she was elected President.",
}
VACATED = {"Dublin Central": "Paschal Donohoe", "Galway West": "Catherine Connolly"}


def fetch() -> None:
    for url in [*DECLARATIONS.values(), CANDIDATES]:
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    facts = json.loads((DATA / "tmp" / "nh-ie" / "facts.json").read_text())
    (PARSED / "dail.json").write_text(json.dumps(facts["dail"], ensure_ascii=False, indent=1))


def emit() -> Path:
    dail = json.loads((PARSED / "dail.json").read_text())
    roster = {
        (row[0], row[1]): row[2]
        for row in pl.read_parquet(DATA / "parsed" / "ie.dail" / "roster.parquet")
        .filter(pl.col("year") == 2024)
        .select("constituency", "name", "person")
        .iter_rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 Dáil by-elections in Dublin Central and Galway West with every count, and the Seanad Cultural and Educational Panel by-election",
            polity="IE",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ireland = cs.polity("IE")
        stv = cs.key("ie.electoral_system", "dail-eireann")
        dail_body = cs.key("ie.body", "dail-eireann")
        if {stv, dail_body} & cs.minted:
            raise ValueError("run ie.dail first")
        sources: dict[str, UUID] = {}
        for label, url in DECLARATIONS.items():
            title = f"Bye-election to the 34th Dáil held on 22nd May 2026, constituency of {label}"
            sources[label] = cs.source("Houses of the Oireachtas", title)
            cs.add(
                "source",
                id=sources[label],
                kind="official_body",
                publisher="Houses of the Oireachtas",
                title=title,
                url=url,
                license=LICENSE,
                license_url=LICENSE_URL,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[label])
        election = cs.key(f"{SOURCE}.election", f"{DAIL}:dail")
        cs.add(
            "election", id=election, polity_id=ireland, kind="by_election", date_from=DAIL, date_to=DAIL, status="held"
        )
        name(election, "2026 Dáil by-elections")
        cs.add("slug", path=f"ie/{DAIL}-by-election", entity_id=election)
        for race in dail:
            label = race["constituency"]
            code = f"2023:{slug(label)}"
            area, unit = cs.area("ie.dail", code), cs.unit("ie.dail", code)
            if {area, unit} & cs.minted:
                raise ValueError(f"{code} is not on record")
            contest = cs.key("ie.contest", f"{DAIL}:dail-eireann:{slug(label)}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=dail_body,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=stv,
                default_result_kind="counted",
            )
            name(contest, f"2026 {label} by-election")
            cs.add("slug", path=f"ie/{DAIL}-by-election/{slug(label)}", entity_id=contest)
            cs.add("note", entity_id=contest, kind="context", body_md=CAUSES[label])
            general = conn.execute(
                "select entity_id from slug where path = %s and is_current",
                [f"ie/2024-11-29-general/{slug(label)}"],
            ).fetchone()
            if general is None:
                raise ValueError(f"no 2024 contest for {label}")
            cs.add(
                "tenure",
                id=cs.key(f"{SOURCE}.tenure", f"{DAIL}:{slug(label)}"),
                person_id=cs.key("ie.dail.person", roster[label, fold(VACATED[label])]),
                body_id=dail_body,
                from_date=date(2024, 11, 29),
                to_date=date(2025, 1, 1),
                to_precision="year",
                acquired_via="elected",
                contest_id=general[0],
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{DAIL}:{slug(label)}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources[label],
                is_default=True,
                completeness=1,
            )
            if race["poll"] - race["spoiled"] != race["valid"]:
                raise ValueError(f"{label}: poll less spoiled is not the valid poll")
            values = []
            for measure, amount in (
                (REGISTERED, race["electorate"]),
                (BALLOTS, race["poll"]),
                (VALID, race["valid"]),
                (INVALID, race["spoiled"]),
                (QUOTA, race["quota"]),
            ):
                values.append((None, measure, 0, amount))
            counts = race["counts"]
            if sum(counts[0]["values"].values()) != race["valid"]:
                raise ValueError(f"{label}: first preferences do not sum to the valid poll")
            for count in counts[1:]:
                exhausted = race["non_transferable_by_count"][str(count["count"])]
                if sum(count["values"].values()) + exhausted != race["valid"]:
                    raise ValueError(f"{label}: count {count['count']} does not tie")
                values.append((None, EXHAUSTED, count["count"], exhausted))
            winner = race["elected"].split(" (")[0]
            for position, candidate in enumerate(race["candidates"], 1):
                party_label = LABELS.get(candidate["party"], candidate["party"])
                party = cs.key("ie.party", PARTY_CODES[party_label]) if party_label in PARTY_CODES else None
                if candidate["party"] != "Non-Party" and party is None:
                    raise ValueError(f"no party code for {candidate['party']}")
                key = f"{DAIL}:dail-eireann:{slug(label)}:{fold(candidate['name'])}"
                earlier = roster.get((label, fold(candidate["name"])))
                person = cs.key("ie.dail.person", earlier) if earlier else cs.key(f"{SOURCE}.person", key)
                if person in cs.minted and earlier is None:
                    cs.add("person", id=person)
                    name(person, candidate["name"])
                option = cs.key("ie.option", key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=position,
                    ballot_label=candidate["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("ie.candidacy", key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate["name"],
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                last = 0
                for count in counts:
                    if candidate["name"] in count["values"]:
                        last = count["count"]
                        values.append((option, VOTES, count["count"], count["values"][candidate["name"]]))
                kind = "elected" if candidate["name"] == winner else "lost" if last == len(counts) else "eliminated"
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            for option, measure, count_round, amount in values:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=count_round,
                    flags=0,
                    value=amount,
                )

        candidates = cs.source("Houses of the Oireachtas", "Seanad bye-elections 2026: list of candidates")
        cs.add(
            "source",
            id=candidates,
            kind="official_body",
            publisher="Houses of the Oireachtas",
            title="Seanad bye-elections 2026: list of candidates",
            url=CANDIDATES,
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(raw.cached(SOURCE, CANDIDATES), candidates)
        seanad = cs.key("ie.body", "seanad-eireann")
        if seanad in cs.minted:
            cs.add("body", id=seanad, polity_id=ireland, kind="upper_house")
            name(seanad, "Seanad Éireann")
            cs.add("body_size", body_id=seanad, seats=60, valid_from=date(1938, 4, 27), valid_to=None)
        by_election = cs.key(f"{SOURCE}.election", f"{SEANAD}:seanad")
        cs.add(
            "election",
            id=by_election,
            polity_id=ireland,
            kind="by_election",
            date_from=SEANAD,
            date_to=SEANAD,
            status="scheduled",
        )
        name(by_election, "2026 Seanad by-election")
        cs.add("slug", path=f"ie/{SEANAD}-by-election", entity_id=by_election)
        contest = cs.key("ie.contest", f"{SEANAD}:seanad-eireann:cultural-and-educational")
        cs.add(
            "contest",
            id=contest,
            election_id=by_election,
            kind="body_seats",
            body_id=seanad,
            magnitude=1,
            round_number=1,
            electoral_system_id=stv,
            default_result_kind="counted",
        )
        name(contest, "Cultural and Educational Panel by-election")
        cs.add("slug", path=f"ie/{SEANAD}-by-election/cultural-and-educational-panel", entity_id=contest)
        cs.add("note", entity_id=contest, kind="context", body_md=SEANAD_NOTE)
        for position, (candidate, code) in enumerate(SEANAD_CANDIDATES, 1):
            key = f"{SEANAD}:seanad-eireann:{fold(candidate)}"
            person = cs.key(f"{SOURCE}.person", key)
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, candidate)
            party = cs.key("ie.party", code) if code else None
            option = cs.key("ie.option", key)
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=candidate,
            )
            cs.add(
                "candidacy",
                id=cs.key("ie.candidacy", key),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate,
                member_of_party_id=party,
            )
        return cs.write()
