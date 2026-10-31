import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ok_pri_judicial, ok_pri_pdf, ok_pri_state
from ea.sources.us.oe_primaries_2026 import plain, title
from ea.store import connect

SOURCE = "us.ok_judicial_generals"
VERSION = "1"
PUBLISHER = ok_pri_pdf.PUBLISHER
TITLE = "Official results: district attorney, district judge, and associate district judge generals by county"
NOTE = (
    "Results by county from the Oklahoma State Election Board's general election results. The statewide total for "
    "each candidate is the sum of the county votes, which equals the printed state total."
)
BASE = ok_pri_pdf.BASE + "{0}-election-results/{0}-general-election-results/{1}.pdf"
URL_2010 = BASE.format(2010, "ge-results-county-20101102")
FILES = {2002: ("02da", "02dj", "02assoc"), 2006: ("06da", "06dj", "06assoc")}
HEADER = re.compile(r"General Election\s+\W+\s+(\w+) (\d+), (\d{4})")
OFFICE = re.compile(
    r"^\s{10,}(DISTRICT ATTORNEY|DISTRICT JUDGE|ASSOCIATE DISTRICT JUDGE)\s*"
    r"(?:\W+\s*DISTRICT (\d+)(?:, OFFICE (\d+))?|,\s*DISTRICT (\d+)|,\s*(.+?) COUNTY)\s*$"
)
RACE_2010 = re.compile(
    r"^(DISTRICT ATTORNEY, DISTRICT NO\. (\d+)|DISTRICT JUDGE, DIST\. NO\. (\d+) OFF NO\. (\d+)|ASSOCIATE DISTRICT JUDGE, (.+?) COUNTY)$"
)
LETTER = re.compile(r"\s*\(([A-Z])\)$")
PARTY_LETTERS = {"R": "republican", "D": "democratic"}


def links() -> list[tuple[str, Path]]:
    found = [
        (BASE.format(year, name), ok_pri_state.INBOX / f"{name}.pdf") for year, names in FILES.items() for name in names
    ]
    return [*found, (URL_2010, ok_pri_state.INBOX / "ge-results-county-20101102.pdf")]


def fetch() -> None:
    for link, file in links():
        try:
            raw.cached(SOURCE, link)
        except LookupError:
            raw.store(
                SOURCE,
                link,
                file.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def old_key(office: re.Match[str], header: re.Match[str]) -> tuple[str, int | None, str, date]:
    day = date(int(header[3]), ok_pri_state.MONTHS[header[1]], int(header[2]))
    if office[1] == "DISTRICT ATTORNEY":
        return (office[1], int(office[2] or office[4]), "", day)
    if office[5]:
        return (office[1], None, office[5], day)
    return (office[1], int(office[2]), f"Office {office[3]}", day)


def legal() -> dict[tuple[str, int | None, str, date], dict]:
    found: dict[tuple[str, int | None, str, date], dict] = {}
    for link, _ in links()[:-1]:
        found.update(ok_pri_state.blocks_old(link, SOURCE, HEADER, OFFICE, old_key))
    day = date(2010, 11, 2)
    for (office, district, _, _), block in ok_pri_state.blocks_2010(URL_2010, day, SOURCE).items():
        text = office if district is None else f"{office}, DISTRICT NO. {district}"
        if not (race := RACE_2010.match(text)):
            continue
        if race[2]:
            key = ("DISTRICT ATTORNEY", int(race[2]), "", day)
        elif race[3]:
            key = ("DISTRICT JUDGE", int(race[3]), f"Office {race[4]}", day)
        else:
            key = ("ASSOCIATE DISTRICT JUDGE", None, race[5], day)
        found[key] = block
    return {key: ok_pri_state.checked(key, block) for key, block in found.items()}


def emit() -> Path:
    loaded = legal()
    nonpartisan = {(*key[:3], key[4].year): key for key in ok_pri_judicial.legal() if key[3] == "nonpartisan"}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2002, 2006, and 2010 district attorney, district judge, and associate district judge generals by county from the State Election Board's results PDFs",
            polity="US-OK",
        )
        counties = {
            plain(text): (code, area)
            for code, area, text in conn.execute(
                """select u.code, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
        state_polity = cs.polity("US-OK")
        parties: dict[str, UUID] = {}
        for party, key in (
            ("republican", "2018-06-26:OK:house:1:republican"),
            ("democratic", "2018-06-26:OK:house:1:democratic"),
        ):
            parties[party] = conn.execute(
                """select (select o.party_id from contest_option o where o.contest_id = c.id limit 1)
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value = %s""",
                [key],
            ).fetchone()[0]
        state_area = conn.execute(
            """select u.area_id from reporting_unit u join polity p on p.id = u.polity_id
               where p.iso_code = 'US-OK' and u.scheme_code = 'us.state'"""
        ).fetchone()[0]
        districts: dict[str, tuple[UUID, UUID]] = {}
        fptp = cs.key("us.electoral_system", "fptp")
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/ok/%%'").fetchall()}

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://oklahoma.gov/elections/elections-results/election-results.html",
                license="Public record",
            )
        for link, _ in links():
            cs.artifact(raw.cached(SOURCE, link), source)

        offices: set[UUID] = set()
        persons: set[UUID] = set()
        memberships: set[tuple[UUID, UUID, date]] = set()
        made = 0
        for (office, number, seat, day), by_candidate in loaded.items():
            if office == "DISTRICT ATTORNEY":
                prefix, office_name, where = "da", "District Attorney", f"District {number}"
                contest_key = f"{day}:OK:district-attorney:{number}"
                path = f"district-attorney-{number}"
                office_key = f"district-attorney:OK:{number}"
                previous = None
            elif office == "DISTRICT JUDGE":
                prefix, office_name, where = "dj", "District Judge", f"District {number}, {seat}"
                contest_key = f"{day}:OK:district-judge:{number}:{slug(seat)}"
                path = f"district-judge-{number}-{slug(seat)}"
                office_key = f"district-judge:OK:{number}"
                first = nonpartisan.get((office, number, seat, day.year))
                previous = first and f"{first[4]}:OK:district-judge:{number}:{slug(seat)}"
            else:
                fips, area = counties[plain(seat)]
                office_name, where = "Associate District Judge", f"{seat.title()} County"
                contest_key = f"{day}:OK:associate-district-judge:{fips}"
                path = f"associate-district-judge-{slug(seat)}-county"
                office_key = f"associate-district-judge:OK:{fips}"
                first = nonpartisan.get((office, number, seat, day.year))
                previous = first and f"{first[4]}:OK:associate-district-judge:{fips}"
            if number is not None:
                polity, area = ok_pri_judicial.place(cs, districts, taken, state_polity, state_area, prefix, number)
            else:
                polity = state_polity
            office_id = cs.key("us.office", office_key)
            if office_id not in offices:
                offices.add(office_id)
                cs.add("office", id=office_id, polity_id=polity, kind="other")
                if office_id in cs.minted:
                    cs.add(
                        "name",
                        entity_id=office_id,
                        text=f"Oklahoma {office_name}, {where.split(',')[0]}",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=cs.key("us.election", f"{day}:OK"),
                kind="office",
                office_id=office_id,
                district_area_id=area,
                party_scope_id=None,
                term="full",
                magnitude=1,
                round_number=2 if previous else 1,
                previous_round_id=cs.key("us.contest", previous) if previous else None,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{day.year} Oklahoma {office_name} general, {where}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                base = f"us/ok/{day}-general/{path}"
                final, count = base, 1
                while final in taken:
                    count += 1
                    final = f"{base}-{count}"
                taken.add(final)
                cs.add("slug", path=final, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=cs.key("us.election", f"{day}:OK"),
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            ranked = sorted(by_candidate, key=lambda candidate: -sum(by_candidate[candidate].values()))
            tops = [sum(by_candidate[candidate].values()) for candidate in ranked[:2]]
            if len(tops) > 1 and tops[0] == tops[1]:
                raise ValueError(f"{contest_key}: tied for first")
            options = {}
            for rank, candidate in enumerate(ranked):
                letter = LETTER.search(candidate)
                text = title(LETTER.sub("", candidate))
                options[candidate] = cs.key("us.option", f"{contest_key}:{slug(text)}")
                cs.add(
                    "contest_option",
                    id=options[candidate],
                    contest_id=contest,
                    kind="candidate",
                    party_id=parties.get(PARTY_LETTERS.get(letter[1], "")) if letter else None,
                    ballot_label=text,
                )
                person = cs.key(f"{ok_pri_judicial.SOURCE}.person", f"OK:{slug(text)}")
                if person not in persons:
                    persons.add(person)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(text)}"),
                    person_id=person,
                    option_id=options[candidate],
                    role="head",
                    ballot_name=text,
                    is_independent=bool(letter and letter[1] == "I"),
                )
                if rank == 0:
                    cs.add("outcome", result_set_id=result, option_id=options[candidate], kind="won")
            by_place: dict[str, int] = defaultdict(int)
            rows = []
            for candidate, by_county in by_candidate.items():
                for county, votes in by_county.items():
                    fips, _ = counties[plain(county)]
                    unit = cs.unit("us.county", fips)
                    if number is not None:
                        memberships.add((unit, area, day))
                    by_place[fips] += votes
                    rows.append((str(result), str(unit), str(options[candidate]), 0, 1, 0, 0, votes))
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
        for unit, area, day in memberships:
            cs.add("reporting_unit_area", unit_id=unit, area_id=area, valid_from=day, valid_to=day + timedelta(days=1))
        print(made, "contests")
        return cs.write()
