import csv
import io
import re
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl, ok_county
from ea.sources.us.oe_primaries_2026 import plain, title
from ea.sources.us.ok_leg_primaries import NOTE, PARTIES, PUBLISHER, TITLE
from ea.store import connect

SOURCE = "us.ok_leg_specials"
VERSION = "1"
PRIMARY_DAYS = [date(2014, 6, 24), date(2014, 8, 26)]
SPECIAL_DAYS = [
    date(*day)
    for day in (
        (2015, 4, 7),
        (2015, 7, 14),
        (2015, 9, 8),
        (2015, 11, 10),
        (2016, 1, 12),
        (2017, 3, 7),
        (2017, 5, 9),
        (2017, 7, 11),
        (2017, 8, 8),
        (2017, 9, 12),
        (2017, 11, 14),
        (2017, 12, 12),
        (2018, 1, 9),
        (2018, 2, 13),
        (2018, 3, 6),
        (2021, 2, 9),
        (2021, 4, 6),
        (2023, 10, 10),
        (2023, 12, 12),
        (2024, 2, 13),
        (2025, 3, 4),
        (2025, 4, 1),
        (2025, 5, 13),
        (2025, 6, 10),
        (2025, 12, 9),
        (2026, 1, 13),
        (2026, 2, 10),
    )
]
LINE = r"^FOR STATE (REPRESENTATIVE|SENATOR) DISTRICT (\d+)( \(UNEXPIRED TERM\))?$"
LIBERTARIAN = "2024-02-13:OK:house:39:s"


def url(day: date) -> str:
    return ok_county.url(day)


def fetch() -> None:
    for day in [*PRIMARY_DAYS, *SPECIAL_DAYS]:
        try:
            raw.cached(SOURCE, url(day))
        except LookupError:
            raw.store(
                SOURCE,
                url(day),
                (ok_county.DOWNLOADS / f"{day:%Y%m%d}_CountyResults_csv.zip").read_bytes(),
                suffix=".zip",
                retrieved_at=datetime.now(UTC),
                media_type="application/zip",
            )


def races(day: date, special: bool) -> dict[tuple[str, int, str], dict[str, tuple[str, dict[str, int]]]]:
    with zipfile.ZipFile(raw.cached(SOURCE, url(day)).path) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf8", errors="replace")
    found: dict[tuple[str, int, str], dict[str, tuple[str, dict[str, int]]]] = defaultdict(dict)
    for row in csv.DictReader(io.StringIO(text)):
        match = re.fullmatch(LINE, row["race_description"].upper())
        if match is None or bool(match[3]) != special:
            continue
        chamber = "senate" if match[1] == "SENATOR" else "house"
        _, votes = found[(chamber, int(match[2]), row["race_party"])].setdefault(
            row["cand_name"].upper().strip(), (row["cand_party"], defaultdict(int))
        )
        votes[row["county"]] += int(row["cand_tot_votes"])
    return found


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2014 state legislative primaries and 2015 through 2026 special elections by county from the State Election Board's county level results",
            polity="US-OK",
        )
        counties = {
            plain(text): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
        contests = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall())

        def shape(day: date, chamber: str, district: int) -> tuple:
            rows = conn.execute(
                """select left(x.value, 10), c.office_id, c.body_id, c.district_area_id, c.seat_id, c.boundary_set_id,
                          c.magnitude
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value ~ %s order by x.value""",
                [rf"^\d{{4}}-11-\d\d:OK:{chamber}:{district}:gs?$"],
            ).fetchall()
            before = [row for row in rows if date.fromisoformat(row[0]) <= day]
            if not rows:
                raise ValueError(f"no general template for {chamber} {district}")
            return (before[-1] if before else rows[0])[1:]

        scope: dict[str, tuple] = {}
        for party, (word, _, _) in PARTIES.items():
            scope[party] = conn.execute(
                """select c.party_scope_id, (select o.party_id from contest_option o where o.contest_id = c.id limit 1)
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value = %s""",
                [f"2018-06-26:OK:house:1:{word}"],
            ).fetchone()
        parties = {"REP": scope["REP"][1], "DEM": scope["DEM"][1]}
        parties["LIB"] = conn.execute(
            """select o.party_id from contest_option o where o.contest_id = %s and o.ballot_label = 'Richard Prawdzienski'""",
            [contests[LIBERTARIAN]],
        ).fetchone()[0]
        primary_system = conn.execute(
            "select electoral_system_id from contest where id = %s", [contests["2014-06-24:OK:house:1:rpfsettled"]]
        ).fetchone()[0]
        special_system = conn.execute(
            "select electoral_system_id from contest where id = %s", [contests[LIBERTARIAN]]
        ).fetchone()[0]
        pool: dict[str, set[UUID]] = defaultdict(set)
        for person, text in conn.execute(
            """select cy.person_id, n.text from candidacy cy join contest_option o on o.id = cy.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.value ~ '^20(1[4-9]|2[0-9])-[0-9-]+:OK:'"""
        ).fetchall():
            pool[plain(text)].add(person)

        def person_of(text: str) -> UUID:
            found = pool.get(plain(text), set())
            if len(found) == 1:
                return next(iter(found))
            id = cs.key(f"{SOURCE}.person", f"OK:{slug(text)}")
            if id in cs.minted:
                cs.add("person", id=id)
                cs.add("name", entity_id=id, text=text, lang="en", kind="official", is_preferred=True)
            return id

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://results.okelections.gov/OKER/",
                license="Public record",
            )
        for day in [*PRIMARY_DAYS, *SPECIAL_DAYS]:
            cs.artifact(raw.cached(SOURCE, url(day)), source)

        attached = []
        for day in PRIMARY_DAYS:
            for (chamber, district, party), by_candidate in races(day, False).items():
                if not party:
                    continue
                etype = PARTIES[party][1]
                suffixes = ["runoff"] if day == PRIMARY_DAYS[1] else ["fsettled", "funsettled"]
                key = next(
                    (k for suffix in suffixes if (k := f"{day}:OK:{chamber}:{district}:{etype}{suffix}") in contests),
                    None,
                )
                if key is None:
                    print(f"{day} {chamber} {district} {party}: no contest")
                    continue
                for name, (_, by_county) in by_candidate.items():
                    for county, votes in by_county.items():
                        attached.append((day, key.split(f"{day}:OK:", 1)[1], name, county, votes))

        for day in PRIMARY_DAYS:
            for (chamber, district, party), by_candidate in races(day, True).items():
                for name, (_, by_county) in by_candidate.items():
                    for county, votes in by_county.items():
                        key = f"{chamber}:{district}:{PARTIES[party][1]}pfsettled"
                        attached.append((day, key, name, county, votes))

        timeline: dict[tuple[str, int], list[tuple[date, str, dict]]] = defaultdict(list)
        for day in SPECIAL_DAYS:
            for (chamber, district, party), by_candidate in races(day, True).items():
                timeline[(chamber, district)].append((day, party, by_candidate))
        minted_races = []
        for (chamber, district), stages in timeline.items():
            for day, party, by_candidate in stages:
                if not party:
                    key = f"{day}:OK:{chamber}:{district}:s"
                    if key in contests:
                        for name, (_, by_county) in by_candidate.items():
                            for county, votes in by_county.items():
                                attached.append((day, f"{chamber}:{district}:s", name, county, votes))
                        continue
                    minted_races.append((day, chamber, district, "", 1, None, by_candidate, set()))
                    continue
                earlier = [d for d, p, _ in stages if p == party and d < day]
                later = [(d, c) for d, p, c in stages if p == party and d > day]
                advanced = {name for _, c in later for name in c}
                minted_races.append(
                    (
                        day,
                        chamber,
                        district,
                        party,
                        2 if earlier else 1,
                        earlier[0] if earlier else None,
                        by_candidate,
                        advanced,
                    )
                )

        elections: dict[date, UUID] = {}
        made = 0
        for day, chamber, district, party, round_number, first, by_candidate, advanced in minted_races:
            election = elections[day] = cs.key("us.election", f"{day}:OK:special")
            if election in cs.minted and all(row["id"] != election for row in cs.rows.get("election", [])):
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-OK"),
                    kind="special",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{day:%B} {day.day}, {day.year} Oklahoma special election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/ok/{day}-special", entity_id=election)
            word, etype, label = PARTIES[party] if party else ("", "", "")
            suffix = "" if not party else f"{etype}s" + ("runoff" if round_number == 2 else "")
            contest_key = f"{day}:OK:{chamber}:{district}:{suffix or 's'}"
            body = "House of Representatives" if chamber == "house" else "Senate"
            shaped = shape(day, chamber, district)
            contest = cs.key("us.contest", contest_key)
            previous = cs.key("us.contest", f"{first}:OK:{chamber}:{district}:{etype}s") if first else None
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                office_id=shaped[0],
                body_id=shaped[1],
                district_area_id=shaped[2],
                seat_id=shaped[3],
                boundary_set_id=shaped[4],
                magnitude=shaped[5],
                party_scope_id=scope[party][0] if party else None,
                term="remainder",
                round_number=round_number,
                previous_round_id=previous,
                electoral_system_id=primary_system if party else special_system,
                default_result_kind="counted",
            )
            label_text = (
                f"{day.year} Oklahoma {body} special election, District {district}"
                if not party
                else f"{day.year} Oklahoma {body} {label} special primary{' runoff' if round_number == 2 else ''}, District {district}"
            )
            cs.add("name", entity_id=contest, text=label_text, lang="en", kind="official", is_preferred=True)
            tail = (
                "special-election"
                if not party
                else f"{word}-special-primary" + ("-runoff" if round_number == 2 else "")
            )
            cs.add("slug", path=f"us/ok/{day}-special/state-{chamber}-{district}-{tail}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            totals = {name: sum(by_county.values()) for name, (_, by_county) in by_candidate.items()}
            ranked = sorted(by_candidate, key=lambda name: -totals[name])
            options = {}
            for name in ranked:
                text = title(name)
                cand_party = by_candidate[name][0]
                options[name] = cs.key("us.option", f"{contest_key}:{slug(text)}")
                cs.add(
                    "contest_option",
                    id=options[name],
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties.get(cand_party),
                    ballot_label=text,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(text)}"),
                    person_id=person_of(text),
                    option_id=options[name],
                    role="head",
                    ballot_name=text,
                    is_independent=cand_party not in parties,
                )
            for rank, name in enumerate(ranked):
                if name in advanced:
                    kind = "advanced"
                elif rank == 0 and not advanced:
                    kind = "won"
                else:
                    continue
                cs.add("outcome", result_set_id=result, option_id=options[name], kind=kind)
            by_place: dict[str, int] = defaultdict(int)
            rows = []
            for name, (_, by_county) in by_candidate.items():
                for county, votes in by_county.items():
                    fips = counties[plain(county)]
                    by_place[fips] += votes
                    rows.append((str(result), str(cs.unit("us.county", fips)), str(options[name]), 0, 1, 0, 0, votes))
            rows += [
                (str(result), str(cs.unit("us.county", fips)), None, 0, 4, 0, 0, votes)
                for fips, votes in by_place.items()
            ]
            cs.frame(
                "result_value",
                pl.DataFrame(
                    rows,
                    schema={
                        "result_set_id": pl.String,
                        "unit_id": pl.String,
                        "option_id": pl.String,
                        "channel": pl.Int16,
                        "measure": pl.Int16,
                        "count_round": pl.Int16,
                        "flags": pl.Int16,
                        "value": pl.Int64,
                    },
                    orient="row",
                ),
            )
            made += 1

        frame = (
            pl.DataFrame(
                attached,
                schema={
                    "day": pl.Date,
                    "chamber": pl.String,
                    "candidate": pl.String,
                    "county": pl.String,
                    "votes": pl.Int64,
                },
                orient="row",
            )
            .with_columns(
                year=pl.col("day").dt.year().cast(pl.Int32),
                special=pl.lit(False),
                state=pl.lit("OK"),
                district=pl.lit(None, pl.Int16),
                code=pl.col("county").str.to_lowercase().str.replace_all(r"[^a-z]", "").replace_strict(counties),
            )
            .with_columns(
                measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
                writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            )
            .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
            .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
            .agg(pl.col("writein").any(), pl.col("votes").sum())
            .with_columns(kind=pl.lit("county"), source=pl.lit("OK"))
        )
        medsl.attach(cs, conn, frame, {"OK": source}, strict=True)
        print(made, len(attached))
        return cs.write()
