import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.elstats import plain
from ea.sources.us.statewide import CIVERA
from ea.store import DATA, connect

SOURCE = "us.eda"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2020, 11, 3)
ACCESS = "https://dataverse.harvard.edu/api/access/datafile"
FILES = {
    "AK": 5006659,
    "AL": 5006678,
    "AR": 5006663,
    "AS": 5006654,
    "AZ": 5006633,
    "CA": 5006656,
    "CO": 5006630,
    "CT": 5006637,
    "DC": 5006673,
    "DE": 5006643,
    "FL": 5006650,
    "GA": 5006660,
    "GU": 5006636,
    "HI": 5006658,
    "IA": 5006652,
    "ID": 5006661,
    "IL": 5006653,
    "IN": 5006671,
    "KS": 5006645,
    "KY": 5006664,
    "LA": 5006649,
    "MA": 5006670,
    "MD": 5006631,
    "ME": 5006644,
    "MI": 5006647,
    "MN": 5006676,
    "MO": 5006641,
    "MP": 5006638,
    "MS": 5006679,
    "MT": 5006625,
    "NC": 5006626,
    "ND": 5006629,
    "NE": 5006648,
    "NH": 5006655,
    "NJ": 5006635,
    "NM": 5006646,
    "NV": 5006642,
    "NY": 5006665,
    "OH": 5006669,
    "OK": 5006628,
    "OR": 5006675,
    "PA": 5006627,
    "PR": 5006651,
    "RI": 5006657,
    "SC": 5006639,
    "SD": 5006674,
    "TN": 5006680,
    "TX": 5006632,
    "UT": 5006662,
    "VA": 5006667,
    "VI": 5006668,
    "VT": 5006640,
    "WA": 5006677,
    "WI": 5006666,
    "WV": 5006672,
    "WY": 5006634,
}

PUBLISHER = "Election Data Analysis"
TITLE = "2020 official county-level results"
STATEWIDE = (
    r"Governor|Attorney General|Secretary of State|Treasurer|Auditor|Commissioner of|Insurance Commissioner|"
    r"Superintendent|Chief Justice|Corporation Commissioner|Public Utilities Commissioner|Public Auditor"
)
OFFICE_NAMES = {
    "State Treasurer": "Treasurer",
    "State Auditor": "Auditor",
    "State Superintendent": "Superintendent of Public Instruction",
    "Supreme Court Chief Justice": "Chief Justice of the Supreme Court",
}
SEATS = {("AZ", "Corporation Commissioner"): 3}
PARTIES = {
    "Democratic Party": "democratic-party",
    "Republican Party": "republican-party",
    "Libertarian Party": "libertarian-party",
    "Green Party": "green-party",
    "Constitution Party": "constitution-party",
    "Independent American Party": "UT:independent-american-party",
    "Partido Nuevo Progresista": "PR:new-progressive-party",
    "Partido Popular Democrático": "PR:popular-democratic-party",
    "Partido Independentista Puertorriqueño": "PR:puerto-rican-independence-party",
    "Movimiento Victoria Ciudadana": "PR:citizens-victory-movement",
    "Proyecto Dignidad": "PR:dignity-project",
}
INDEPENDENT = {"Independent Party", "IND", "Candidato Independiente", "Unaffiliated Party"}
POOLED = {"Write-in", "Scatter"}


def fetch() -> None:
    for file in FILES.values():
        raw.fetch(SOURCE, f"{ACCESS}/{file}", suffix=".tab")


def parse() -> None:
    with connect() as conn:
        units = {
            (state, plain(text)): code
            for state, code, text in conn.execute(
                """select substr(p.iso_code, 4), u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
                   join polity p on p.id = s.polity_id
                   where u.scheme_code = 'us.county'"""
            ).fetchall()
        }
    frames = [
        pl.read_csv(raw.cached(SOURCE, f"{ACCESS}/{file}").path, separator="\t", infer_schema=False).with_columns(
            state=pl.lit(state)
        )
        for state, file in FILES.items()
        if state not in CIVERA and state != "MA"
    ]
    frame = (
        pl.concat(frames, how="diagonal")
        .filter(
            (pl.col("VoteType") == "total")
            & pl.col("Contest").str.contains(STATEWIDE)
            & ~pl.col("Contest").is_in(["US Resident Commissioner (PR)", "IN Auditor of State"])
        )
        .with_columns(
            office=pl.col("Contest").str.replace(r"^[A-Z]{2} ", "").replace(OFFICE_NAMES),
            place=pl.col("ReportingUnit").str.split(";").list.get(1, null_on_oob=True),
            candidate=pl.col("Selection").str.replace(r"^([^,]+), (.+)$", "$2 $1"),
            votes=pl.col("Count").cast(pl.Int64),
        )
    )
    codes = []
    for state, place in frame.select("state", "place").unique().iter_rows():
        text = re.sub(r"\b(county|municipio|district( of)?|parish)\b", "", (place or "").lower())
        code = units.get((state, plain(text)))
        if state == "GU":
            code = "66010"
        elif place == "(no district)":
            code = None
        elif code is None:
            raise ValueError(f"no county for {state} {place}")
        codes.append((state, place, code))
    frame = frame.join(
        pl.DataFrame(codes, schema=["state", "place", "code"], orient="row"), on=["state", "place"], nulls_equal=True
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.group_by("state", "office", "candidate", "Party", "code")
        .agg(pl.col("votes").sum(), preliminary=(pl.col("Preliminary") == "True").any())
        .rename({"Party": "party"})
        .sort("state", "office", "candidate", "code")
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2020 statewide executive and judicial general elections by county from Election Data Analysis",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.7910/DVN/RV80FW",
            license="CC0 1.0",
        )
        for state, file in FILES.items():
            if state in set(lines["state"]):
                cs.artifact(raw.cached(SOURCE, f"{ACCESS}/{file}"), source)
        states: dict[str, tuple[UUID, UUID, str]] = {
            abbreviation: (unit, area, text)
            for abbreviation, unit, area, text in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id, n.text from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        parties_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        offices_known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        others: dict[str, list[tuple[UUID, set[str]]]] = {}
        for key, option, label in conn.execute(
            """select e.value, o.id, o.ballot_label from external_id e
               join contest c on c.id = e.entity_id join changeset s on s.id = c.changeset_id
               join contest_option o on o.contest_id = c.id and o.parent_option_id is null
               where e.scheme = 'us.contest' and e.value like %s and s.pipeline <> %s""",
            [f"{DAY}:%", SOURCE],
        ).fetchall():
            others.setdefault(key, []).append((option, set(slug(label or "").split("-"))))
        fptp, block = cs.key("us.electoral_system", "fptp"), cs.key("us.electoral_system", "block")
        if {fptp, block} & cs.minted:
            raise ValueError("the fptp and block systems must exist")
        persons: dict[tuple[str, str], UUID] = {}

        def party(state: str, label: str) -> UUID | None:
            if label in INDEPENDENT or label == "none or unknown" or label == "MTN":
                return None
            key = PARTIES.get(label) or f"{state}:{slug(label)}"
            id = cs.key("us.party", key)
            if key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=id, kind="party", polity_id=cs.polity(f"US-{state}"))
                name(id, label)
            return id

        for (state, office), group in lines.group_by("state", "office", maintain_order=True):
            unit, area, state_name = states[state]
            election = cs.key("us.election", f"{DAY}:{state}")
            office_slug = slug(office)
            office_key = f"{office_slug}:{state}"
            office_id = cs.key("us.office", office_key)
            if office_key not in offices_known:
                offices_known.add(office_key)
                cs.add(
                    "office",
                    id=office_id,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="governor" if office == "Governor" else "other",
                )
                name(office_id, f"{office} of {state_name}")
            seats = SEATS.get((state, office), 1)
            contest_key = f"{DAY}:{state}:{office_slug}"
            contest = cs.key("us.contest", contest_key)
            reused = contest_key in others
            if not reused:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    district_area_id=area,
                    term="full",
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=fptp if seats == 1 else block,
                    default_result_kind="counted",
                )
                name(contest, f"2020 {state_name} {office} election")
                cs.add("slug", path=f"us/{state.lower()}/{DAY}-general/{office_slug}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary" if group["preliminary"].any() else "certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=not reused,
                completeness=1,
            )
            totals: dict[UUID, int] = {}
            for (candidate, label), cells in group.group_by("candidate", "party", maintain_order=True):
                if candidate in POOLED:
                    option = cs.key("us.option", f"{contest_key}:other")
                    if option in cs.minted:
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                else:
                    surname = [word for word in slug(candidate).split("-") if word not in ("jr", "sr", "ii", "iii")][-1]
                    option = next((option for option, words in others.get(contest_key, []) if surname in words), None)
                    if option is None:
                        if (state, candidate) not in persons:
                            persons[(state, candidate)] = cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
                            if persons[(state, candidate)] in cs.minted:
                                cs.add("person", id=persons[(state, candidate)])
                                name(persons[(state, candidate)], candidate)
                        person = persons[(state, candidate)]
                        option = cs.key("us.option", f"{contest_key}:{person}")
                        party_id = party(state, label)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=party_id,
                            ballot_label=candidate,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=party_id is None,
                        )
                    totals[option] = int(cells["votes"].sum())
                for code, votes in cells.group_by("code").agg(pl.col("votes").sum()).iter_rows():
                    if result_set not in cs.minted:
                        break
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit if code is None else cs.unit("us.county", code),
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            if reused:
                continue
            for option in sorted(totals, key=totals.__getitem__, reverse=True)[:seats]:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        return cs.write()
