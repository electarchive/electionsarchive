import io
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ok_primaries
from ea.store import DATA, connect

SOURCE = "us.leg_gaps"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OK_RUNOFF = date(2014, 8, 26)
CITATIONS = {
    "OK": (ok_primaries.PUBLISHER, ok_primaries.TITLE, ok_primaries.SOURCE, ok_primaries.url(OK_RUNOFF)),
}
BODIES = {"house": ("House of Representatives", "us.sldl"), "senate": ("Senate", "us.sldu")}
PARTIES = {"REP": ("republican-party", "Republican", "r"), "DEM": ("democratic-party", "Democratic", "d")}
COLUMNS = ["day", "state", "chamber", "district", "party", "code", "candidate", "votes", "cited"]


def fetch() -> None:
    for _, _, source, url in CITATIONS.values():
        raw.cached(source, url)


def oklahoma(counties: dict[str, str], held: set[str]) -> list[tuple[object, ...]]:
    with zipfile.ZipFile(raw.cached(ok_primaries.SOURCE, ok_primaries.url(OK_RUNOFF)).path) as archive:
        text = archive.read(archive.namelist()[0])
    frame = pl.read_csv(io.BytesIO(text), infer_schema=False, encoding="utf8-lossy", truncate_ragged_lines=True)
    rows: list[tuple[object, ...]] = []
    for county, race, party, candidate, votes, reporting, precincts in frame.select(
        "county",
        "race_description",
        "race_party",
        "cand_name",
        "cand_tot_votes",
        "race_prec_reporting",
        "tot_race_prec",
    ).iter_rows():
        found = re.fullmatch(r"FOR STATE (REPRESENTATIVE|SENATOR) DISTRICT (\d+)", race.upper())
        if found is None:
            continue
        chamber = "house" if found[1] == "REPRESENTATIVE" else "senate"
        key = f"{OK_RUNOFF}:OK:{chamber}:{found[2]}:{PARTIES[party][2]}prunoff"
        if key in held:
            continue
        if reporting != precincts:
            raise ValueError(f"{key}: {county} reports {reporting} of {precincts} precincts")
        code = counties[re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", county).lower())]
        rows.append((OK_RUNOFF, "OK", chamber, found[2], party, code, candidate.strip(), int(votes), "OK"))
    return rows


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
        held = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join changeset c on c.id = x.changeset_id
                   where x.scheme = 'us.contest' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
    frame = pl.DataFrame(oklahoma(counties, held), schema=COLUMNS, orient="row")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    with pl.Config(tbl_rows=60):
        print(
            frame.group_by("state", "chamber", "district", "party", "candidate")
            .agg(pl.col("votes").sum())
            .sort("state", "chamber", "district")
        )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "returns.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2014 Republican state house and senate primary runoffs by county from the State "
            "Election Board's county results, chained to their first rounds",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for key, (publisher, title, source, url) in CITATIONS.items():
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                raise ValueError(f"{key}: the source {title} must exist")
            cs.artifact(raw.cached(source, url), sources[key])
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )
        own = {
            value
            for (value,) in conn.execute(
                """select x.value from external_id x join changeset c on c.id = x.changeset_id
                   where x.scheme = 'us.contest' and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        }
        system = cs.key("us.electoral_system", "two_round")
        for (day, state, chamber, district, party), group in frame.group_by(
            "day", "state", "chamber", "district", "party", maintain_order=True
        ):
            party_key, party_name, letter = PARTIES[party]
            body_name, scheme = BODIES[chamber]
            contest_key = f"{day}:{state}:{chamber}:{district}:{letter}prunoff"
            if contest_key in own:
                continue
            body = cs.key("us.body", f"{state}:{chamber}")
            area = cs.area(scheme, f"{state}:{district}")
            election = cs.key("us.election", f"{day}:{state}:runoff")
            party_id = cs.key("us.party", party_key)
            if cs.minted & {body, area, election, party_id, system}:
                raise ValueError(f"{contest_key}: its body, district, election, party, and system must exist")
            ((previous, first_key),) = conn.execute(
                """select c.id, x.value from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   join election e on e.id = c.election_id
                   where c.body_id = %s and c.district_area_id = %s and c.party_scope_id = %s
                     and e.date_from < %s and e.date_from > %s::date - 90 and c.round_number = 1""",
                [body, area, party_id, day, day],
            ).fetchall()
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=1,
                party_scope_id=party_id,
                term="full",
                round_number=2,
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} {states[state]} {body_name} {party_name} primary runoff, District {district}")
            cs.add(
                "slug",
                path=f"us/{state.lower()}/{day}-runoff/state-{chamber}-{district}-{slug(party_name)}-primary-runoff",
                entity_id=contest,
            )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[group["cited"][0]],
                is_default=True,
                completeness=1,
            )
            ranked = []
            for (candidate,), rows in group.group_by("candidate", maintain_order=True):
                words = [word.strip('".,') for word in re.sub(r",? (Jr\.|Sr\.|III|II|IV)$", "", candidate).split()]
                found = conn.execute(
                    """select distinct cy.person_id, n.text from candidacy cy
                       join contest_option o on o.id = cy.option_id and o.contest_id = %s
                       join name n on n.entity_id = cy.person_id and n.is_preferred
                       where cy.role = 'head' and regexp_replace(lower(n.text), '[^a-z]', '', 'g') like %s""",
                    [previous, f"%{re.sub(r'[^a-z]', '', words[-1].lower())}%"],
                ).fetchall()
                if len(found) != 1:
                    raise ValueError(f"{contest_key}: {candidate} is not one candidate of {first_key}")
                option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=found[0][1],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head"),
                    person_id=found[0][0],
                    option_id=option,
                    role="head",
                    ballot_name=found[0][1],
                    is_independent=False,
                )
                for code, votes in rows.select("code", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
                ranked.append((int(rows["votes"].sum()), option))
            ranked.sort(key=lambda pair: -pair[0])
            if ranked[0][0] == ranked[1][0]:
                raise ValueError(f"{contest_key}: tie")
            cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="won", seats=None)
        return cs.write()
