import re
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.oh_legislature_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 5, 5)
INBOX = DATA / "inbox" / "manual" / "MF-144"
PAGE = "https://data.ohiosos.gov/portal/past-election-results"
PUBLISHER = "Ohio Secretary of State"
TITLE = "May 5, 2026 primary election official canvass: results by county by party"
PARTIES = {
    "democratic": ("D", "dp", "democratic-party", "Democratic"),
    "republican": ("R", "rp", "republican-party", "Republican"),
    "libertarian": ("L", "liberp", "libertarian-party", "Libertarian"),
}


def url(party: str) -> str:
    return f"{PAGE}#summary-level-official-results-2026-primary---{party}.xlsx"


def fetch() -> None:
    for party in PARTIES:
        try:
            raw.cached(SOURCE, url(party))
        except LookupError:
            raw.store(
                SOURCE,
                url(party),
                (INBOX / f"summary-level-official-results-2026-primary---{party}.xlsx").read_bytes(),
                suffix=".xlsx",
                retrieved_at=datetime.now(UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def parse() -> None:
    rows = []
    for party, (letter, *_) in PARTIES.items():
        table = pl.read_excel(
            raw.cached(SOURCE, url(party)).path, sheet_name="General Assembly", has_header=False, infer_schema_length=0
        ).rows()
        label = ""
        for column in range(6, len(table[1])):
            label = " ".join((table[0][column] or label).split())
            office = re.fullmatch(r"State (Senator|Representative) - District (\d+)", label)
            candidate = re.fullmatch(rf"(.+?)\s*(\(WI\)\*)?\s*\({letter}\)", " ".join((table[1][column] or "").split()))
            if office is None or candidate is None:
                raise ValueError(f"{party} column {column}: {label!r} {table[1][column]!r}")
            counties = [(row[0], int(row[column] or 0)) for row in table[4:] if row[0]]
            if len(counties) != 88 or sum(votes for _, votes in counties) != int(table[2][column]):
                raise ValueError(f"{party} {label} {candidate[1]}: the counties do not add up to the total")
            rows.extend(
                {
                    "party": party,
                    "chamber": "senate" if office[1] == "Senator" else "house",
                    "district": int(office[2]),
                    "candidate": candidate[1],
                    "write_in": candidate[2] is not None,
                    "county": county,
                    "votes": votes,
                }
                for county, votes in counties
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's May 5, 2026 primaries for the Ohio Senate and Ohio House of Representatives, by county, from the Secretary of State's official canvass",
            polity="US-OH",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=PAGE,
                license="Public record",
            )
        for party in PARTIES:
            cs.artifact(raw.cached(SOURCE, url(party)), source)
        election = cs.key("us.election", f"{DAY}:OH:primary")
        if election in cs.minted:
            raise ValueError("the 2026-05-05 Ohio primary election must exist")
        system = cs.key("us.electoral_system", "fptp")
        templates = {
            value: row
            for value, *row in conn.execute(
                """select x.value, co.body_id, co.district_area_id, co.magnitude, co.term
                   from contest co join external_id x on x.entity_id = co.id and x.scheme = 'us.contest'
                   where x.value ~ '^2026-11-03:OH:(house|senate):[0-9]+:g$'"""
            ).fetchall()
        }
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): unit
            for unit, text in conn.execute(
                """select u.id, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39___'"""
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
               where x.value ~ '^20[0-2][0-9]-[0-9-]+:OH:(house|senate):'"""
        ).fetchall():
            words = [
                word
                for word in re.sub(r"[^a-z ]", "", text.lower()).split()
                if word not in ("jr", "sr", "ii", "iii", "iv")
            ]
            if len(words) > 1:
                pool[(words[0], words[-1])].add(person)
        counts = {"contests": 0, "candidates": 0, "matched": 0, "skipped": 0}
        grouped = frame.group_by("party", "chamber", "district", "candidate", "write_in").agg(
            pl.col("county"), pl.col("votes")
        )
        for (party, chamber, district), contest_rows in sorted(
            grouped.group_by("party", "chamber", "district"), key=lambda entry: entry[0]
        ):
            _, scope, party_key, label = PARTIES[party]
            template = templates.get(f"2026-11-03:OH:{chamber}:{district}:g")
            if template is None:
                print("no 2026 general contest for", chamber, district)
                counts["skipped"] += 1
                continue
            body, area, magnitude, term = template
            contest_key = f"{DAY}:OH:{chamber}:{district}:{scope}"
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
            chamber_name = "Ohio Senate" if chamber == "senate" else "Ohio House of Representatives"
            name(contest, f"2026 {chamber_name} {label} primary, District {district}")
            cs.add("slug", path=f"us/oh/{DAY}-primary/state-{chamber}-{district}-{party}", entity_id=contest)
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
                body_md="Results by county from the Ohio Secretary of State's official canvass of the May 5, 2026 primary. Declared write-in candidates are listed by name; the candidate with the most votes is shown as the nominee.",
            )
            totals = []
            for candidate, write_in, by_county, votes in contest_rows.select(
                "candidate", "write_in", "county", "votes"
            ).iter_rows():
                option = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                words = [
                    word
                    for word in re.sub(r"[^a-z ]", "", candidate.lower()).split()
                    if word not in ("jr", "sr", "ii", "iii", "iv")
                ]
                found = pool.get((words[0], words[-1]), set())
                if len(found) == 1:
                    person = next(iter(found))
                    counts["matched"] += 1
                else:
                    person = cs.key(f"{SOURCE}.person", f"OH:{slug(candidate)}")
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
                        unit_id=counties[re.sub(r"[^a-z]", "", county.lower())],
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
