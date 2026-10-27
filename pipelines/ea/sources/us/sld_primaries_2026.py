import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.oe_primaries_2026 import FIPS
from ea.store import DATA, connect

SOURCE = "us.sld_primaries_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
STATES = {
    "MI": (
        date(2026, 8, 4),
        "Michigan Board of State Canvassers",
        "August 4, 2026 primary election official results by county",
        "https://mielections.us/election/results/2026PRI_CENR.html#2026STATE_PRIMARY_MI_CENR_BY_COUNTY.txt",
        ("Michigan Senate", "Michigan House of Representatives"),
    ),
}
PARTIES = {
    "democratic": ("dp", "democratic-party", "Democratic"),
    "republican": ("rp", "republican-party", "Republican"),
}


def fetch() -> None:
    for state, (*_, address, _) in STATES.items():
        try:
            raw.cached(SOURCE, address)
        except LookupError:
            held = raw.cached("us.oe_primaries_2026", address)
            raw.store(
                SOURCE,
                address,
                held.path.read_bytes(),
                suffix=held.path.suffix,
                retrieved_at=held.retrieved_at,
                media_type=held.media_type,
            )


def parse() -> None:
    frames = []
    for state, (*_, address, _) in STATES.items():
        if state == "MI":
            frames.append(
                pl.read_csv(
                    raw.cached(SOURCE, address).path,
                    separator="\t",
                    skip_rows=1,
                    infer_schema=False,
                    quote_char=None,
                    truncate_ragged_lines=True,
                )
                .filter(pl.col("OfficeCode(text)").is_in(["9", "10"]))
                .select(
                    state=pl.lit(state),
                    party=pl.col("PartyDescription").str.to_lowercase(),
                    chamber=pl.when(pl.col("OfficeCode(text)") == "9").then(pl.lit("senate")).otherwise(pl.lit("house")),
                    district=pl.col("DistrictCode(Text)").cast(pl.Int32),
                    candidate=pl.concat_str(
                        "CandidateFirstName", "CandidateMiddleName", "CandidateLastName", separator=" ", ignore_nulls=True
                    ).str.replace_all(r"\s+", " "),
                    write_in=pl.col("WriteIn(W)/Uncommitted(Z)").fill_null("").is_in(["W", "Y"]),
                    county=pl.col("CountyName"),
                    votes=pl.col("CandidateVotes").str.replace_all(",", "").cast(pl.Int64),
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 state legislative primaries by county from the states' official canvasses: Michigan",
            polity="US",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        system = cs.key("us.electoral_system", "fptp")
        counts = {"contests": 0, "candidates": 0, "matched": 0, "skipped": 0}
        for state, (day, publisher, title, address, chambers) in STATES.items():
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=address.split("#")[0],
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, address), source)
            election = cs.key("us.election", f"{day}:{state}:primary")
            if election in cs.minted:
                raise ValueError(f"the {day} {state} primary election must exist")
            templates = {
                value: row
                for value, *row in conn.execute(
                    """select x.value, co.body_id, co.district_area_id, co.magnitude, co.term
                       from contest co join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                       where x.value ~ %s""",
                    [f"^2026-11-03:{state}:(house|senate):[0-9]+:g$"],
                ).fetchall()
            }
            counties = {
                re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st ")): unit
                for unit, text in conn.execute(
                    """select u.id, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                       where u.scheme_code = 'us.county' and u.code like %s""",
                    [f"{FIPS[state]}___"],
                ).fetchall()
            }
            pool: dict[tuple[str, str], set[object]] = defaultdict(set)
            for person, text in conn.execute(
                """select distinct cy.person_id, n.text from candidacy cy
                   join contest_option o on o.id = cy.option_id
                   join contest co on co.id = o.contest_id
                   join name b on b.entity_id = co.body_id and b.is_preferred and b.text !~ 'United States'
                   join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where x.value ~ %s""",
                [f"^20[0-2][0-9]-[0-9-]+:{state}:(house|senate):"],
            ).fetchall():
                words = [w for w in re.sub(r"[^a-z ]", "", text.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")]
                if len(words) > 1:
                    pool[(words[0], words[-1])].add(person)
            grouped = frame.filter(pl.col("state") == state).group_by(
                "party", "chamber", "district", "candidate", "write_in"
            ).agg(pl.col("county"), pl.col("votes"))
            for (party, chamber, district), contest_rows in sorted(
                grouped.group_by("party", "chamber", "district"), key=lambda entry: entry[0]
            ):
                scope, party_key, label = PARTIES[party]
                template = templates.get(f"2026-11-03:{state}:{chamber}:{district}:g")
                if template is None:
                    print("no 2026 general contest for", state, chamber, district)
                    counts["skipped"] += 1
                    continue
                body, area, magnitude, term = template
                contest_key = f"{day}:{state}:{chamber}:{district}:{scope}"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=magnitude,
                    term=term,
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                    party_scope_id=cs.key("us.party", party_key),
                )
                name(contest, f"2026 {chambers[chamber == 'house']} {label} primary, District {district}")
                cs.add(
                    "slug", path=f"us/{state.lower()}/{day}-primary/state-{chamber}-{district}-{party}", entity_id=contest
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
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"Results by county from the {publisher}'s official results. Declared write-in candidates are listed by name; the candidate with the most votes is shown as the nominee.",
                )
                totals = []
                for candidate, write_in, by_county, votes in contest_rows.select(
                    "candidate", "write_in", "county", "votes"
                ).iter_rows():
                    option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                    words = [
                        w for w in re.sub(r"[^a-z ]", "", candidate.lower()).split() if w not in ("jr", "sr", "ii", "iii", "iv")
                    ]
                    found = pool.get((words[0], words[-1]), set())
                    if len(found) == 1:
                        person = next(iter(found))
                        counts["matched"] += 1
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{state}:{slug(candidate)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, candidate)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party_key),
                        ballot_label=f"{candidate} (write-in)" if write_in else candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=False,
                    )
                    for county, value in zip(by_county, votes, strict=True):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=counties[re.sub(r"[^a-z]", "", county.lower().replace("saint ", "st "))],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
                    totals.append((sum(votes), option))
                    counts["candidates"] += 1
                totals.sort(key=lambda entry: -entry[0])
                if totals[0][0] > 0 and (len(totals) == 1 or totals[0][0] > totals[1][0]):
                    cs.add("outcome", result_set_id=result_set, option_id=totals[0][1], kind="won", seats=None)
                counts["contests"] += 1
        print(counts)
        return cs.write()
