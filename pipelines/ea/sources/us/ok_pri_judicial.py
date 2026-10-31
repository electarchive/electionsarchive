import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ok_pri_pdf, ok_pri_state
from ea.sources.us.oe_primaries_2026 import plain, title
from ea.store import connect

SOURCE = "us.ok_pri_judicial"
VERSION = "1"
PUBLISHER = ok_pri_pdf.PUBLISHER
TITLE = ok_pri_pdf.TITLE
NOTE = ok_pri_state.NOTE
EXTRA = [
    ("2002-election-results/2002-primary-election-results/02nonpri.pdf", "02nonpri.pdf"),
    ("2006-election-results/2006-primary-election-results/06nonpri.pdf", "06nonpri.pdf"),
    ("2006-election-results/2008-special-election-asst--district-judge/08adj75.pdf", "08adj75.pdf"),
]
HEADER = re.compile(r"(?:Special )?Primary Election\s+\W+\s+(\w+) (\d+), (\d{4})")
OFFICE = re.compile(
    r"^\s{10,}(DISTRICT JUDGE|ASSOCIATE DISTRICT JUDGE)\s*(?:\W+\s*DISTRICT (\d+), OFFICE (\d+)|,\s*(.+?) COUNTY)\s*$"
)
JUDGE_2010 = re.compile(r"^DISTRICT JUDGE, DIST NO\. (\d+) OFF NO\. (\d+)(?: ELEC DIV NO\. (\d+))?$")
ASSOCIATE_2010 = re.compile(r"^ASSOCIATE DISTRICT JUDGE, (.+?) COUNTY$")
SPECIAL = date(2007, 2, 13)
PARTIES = {
    "democratic": ("democratic", "dp", "Democratic"),
    "republican": ("republican", "rp", "Republican"),
}


def fetch() -> None:
    for path, name in EXTRA:
        link = ok_pri_pdf.BASE + path
        try:
            raw.cached(SOURCE, link)
        except LookupError:
            file = ok_pri_state.INBOX / name
            raw.store(
                SOURCE,
                link,
                file.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(file.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def nonpartisan_key(office: re.Match[str], header: re.Match[str]) -> tuple[str, int | None, str, date]:
    day = date(int(header[3]), ok_pri_state.MONTHS[header[1]], int(header[2]))
    if office[4]:
        return ("ASSOCIATE DISTRICT JUDGE", None, office[4], day)
    return ("DISTRICT JUDGE", int(office[2]), f"Office {office[3]}", day)


def legal() -> dict[tuple[str, int | None, str, str, date], dict]:
    found: dict[tuple[str, int | None, str, str, date], dict] = {}
    for link, _, _ in ok_pri_state.files():
        for (office, district, party, day), block in ok_pri_state.read(link).items():
            if office == "DISTRICT ATTORNEY" and party in PARTIES:
                found[("DISTRICT ATTORNEY", district, "", party, day)] = block
            elif party == "nonpartisan":
                if judge := JUDGE_2010.match(office):
                    seat = f"Office {judge[2]}" + (f", Election Division {judge[3]}" if judge[3] else "")
                    found[("DISTRICT JUDGE", int(judge[1]), seat, party, day)] = block
                elif assoc := ASSOCIATE_2010.match(office):
                    found[("ASSOCIATE DISTRICT JUDGE", None, assoc[1], party, day)] = block
    for path, _ in EXTRA:
        blocks = ok_pri_state.blocks_old(ok_pri_pdf.BASE + path, SOURCE, HEADER, OFFICE, nonpartisan_key)
        for (office, district, seat, day), block in blocks.items():
            found[(office, district, seat, "nonpartisan", day)] = block
    return {key: ok_pri_state.checked(key, block) for key, block in found.items()}


def ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def emit() -> Path:
    loaded = legal()
    days = sorted({key[4] for key in loaded})
    firsts = {year: min(day for day in days if day.year == year) for year in {day.year for day in days}}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2002 to 2010 district attorney, district judge, and associate district judge primaries and runoffs by county from the State Election Board's results PDFs",
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
        state_area = conn.execute(
            """select u.area_id from reporting_unit u join polity p on p.id = u.polity_id
               where p.iso_code = 'US-OK' and u.scheme_code = 'us.state'"""
        ).fetchone()[0]
        scope: dict[str, tuple] = {}
        for party, key in (
            ("democratic", "2018-06-26:OK:house:1:democratic"),
            ("republican", "2018-06-26:OK:house:1:republican"),
        ):
            scope[party] = conn.execute(
                """select c.party_scope_id, (select o.party_id from contest_option o where o.contest_id = c.id limit 1)
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where x.value = %s""",
                [key],
            ).fetchone()
        runoff_system = conn.execute(
            """select c.electoral_system_id from contest c join external_id x on x.entity_id = c.id
               where x.scheme = 'us.contest' and x.value = '2014-06-24:OK:house:1:rpfsettled'"""
        ).fetchone()[0]
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
        for link, _, _ in ok_pri_state.files():
            cs.artifact(raw.cached(ok_pri_state.SOURCE, link), source)
        for path, _ in EXTRA:
            cs.artifact(raw.cached(SOURCE, ok_pri_pdf.BASE + path), source)

        elections: dict[date, UUID] = {}
        for day in days:
            kind = "primary" if firsts[day.year] == day else "runoff"
            election = elections[day] = cs.key("us.election", f"{day}:OK:{kind}")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=state_polity,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                special = "special " if day == SPECIAL else ""
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{day:%B} {day.day}, {day.year} Oklahoma {special}primary {'runoff ' if kind == 'runoff' else ''}election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/ok/{day}-{kind}", entity_id=election)

        districts: dict[str, tuple[UUID, UUID]] = {}

        def district(prefix: str, number: int) -> tuple[UUID, UUID]:
            code = f"OK:{prefix}-{number}"
            if code in districts:
                return districts[code]
            label = f"{ordinal(number)} {'Prosecutorial' if prefix == 'da' else 'Judicial'} District"
            area = cs.area("us.judicial_district", code)
            polity = cs.key("us.polity", f"judicial:{code}")
            districts[code] = (polity, area)
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="judicial_district", iso_code=None)
                cs.add(
                    "name", entity_id=polity, text=f"Oklahoma {label}", lang="en", kind="official", is_preferred=True
                )
                cs.add("polity_parent", polity_id=polity, parent_id=state_polity)
                path = f"us/ok/{slug(label)}"
                if path in taken:
                    raise ValueError(f"{label} would take {path}")
                taken.add(path)
                cs.add("slug", path=path, entity_id=polity)
                cs.add("area", id=area, scheme_code="us.judicial_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=state_area)
                cs.add("name", entity_id=area, text=f"{label}, OK", lang="en", kind="official", is_preferred=True)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add(
                    "reporting_unit",
                    id=cs.unit("us.judicial_district", code),
                    polity_id=polity,
                    scheme_code="us.judicial_district",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            return districts[code]

        offices: set[UUID] = set()
        persons: set[UUID] = set()
        memberships: set[tuple[UUID, UUID, date]] = set()
        made = 0
        for (office, number, seat, party, day), by_candidate in loaded.items():
            first = firsts[day.year]
            runoff = day != first
            partisan = party in PARTIES
            if office == "DISTRICT ATTORNEY":
                polity, area = district("da", number)
                office_name, where = "District Attorney", f"District {number}"
                contest_key = f"{day}:OK:district-attorney:{number}:{PARTIES[party][1]}" + ("runoff" if runoff else "")
                previous = f"{first}:OK:district-attorney:{number}:{PARTIES[party][1]}"
                path = f"district-attorney-{number}-{party}-primary" + ("-runoff" if runoff else "")
                office_key = f"district-attorney:OK:{number}"
                heading = f"Oklahoma District Attorney, District {number}"
            elif office == "DISTRICT JUDGE":
                polity, area = district("dj", number)
                office_name, where = "District Judge", f"District {number}, {seat}"
                contest_key = f"{day}:OK:district-judge:{number}:{slug(seat)}"
                previous = None
                path = f"district-judge-{number}-{slug(seat)}-primary"
                office_key = f"district-judge:OK:{number}"
                heading = f"Oklahoma District Judge, District {number}"
            else:
                fips, area = counties[plain(seat)]
                polity = state_polity
                office_name, where = (
                    "Associate District Judge",
                    f"{seat.title()} County",
                )
                contest_key = f"{day}:OK:associate-district-judge:{fips}"
                previous = None
                path = f"associate-district-judge-{slug(seat)}-county-primary"
                office_key = f"associate-district-judge:OK:{fips}"
                heading = f"Oklahoma Associate District Judge, {seat.title()} County"
            office_id = cs.key("us.office", office_key)
            if office_id not in offices:
                offices.add(office_id)
                cs.add("office", id=office_id, polity_id=polity, kind="other")
                if office_id in cs.minted:
                    cs.add("name", entity_id=office_id, text=heading, lang="en", kind="official", is_preferred=True)
            label = f" {PARTIES[party][2]}" if partisan else ""
            name = f"{day.year} Oklahoma {office_name}{label} primary{' runoff' if runoff else ''}, {where}"
            if runoff and ("DISTRICT ATTORNEY", number, seat, party, first) not in loaded:
                raise ValueError(f"{contest_key}: no first round in {first}")
            advanced = {
                candidate
                for (o, d, s, p, other), found in loaded.items()
                if (o, d, s, p) == (office, number, seat, party)
                and other != day
                and other.year == day.year
                and not runoff
                for candidate in found
            }
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[day],
                kind="office",
                office_id=office_id,
                district_area_id=area,
                party_scope_id=scope[party][0] if partisan else None,
                term="full",
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=cs.key("us.contest", previous) if runoff else None,
                electoral_system_id=runoff_system if partisan else fptp,
                default_result_kind="counted",
            )
            cs.add("name", entity_id=contest, text=name, lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                base = f"us/ok/{day}-{'runoff' if runoff else 'primary'}/{path}"
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
                election_id=elections[day],
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
            options = {}
            for candidate in ranked:
                text = title(candidate)
                options[candidate] = cs.key("us.option", f"{contest_key}:{slug(text)}")
                cs.add(
                    "contest_option",
                    id=options[candidate],
                    contest_id=contest,
                    kind="candidate",
                    party_id=scope[party][1] if partisan else None,
                    ballot_label=text,
                )
                person = cs.key(f"{SOURCE}.person", f"OK:{slug(text)}")
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
                    is_independent=False,
                )
            if partisan:
                for rank, candidate in enumerate(ranked):
                    if candidate in advanced:
                        kind = "advanced"
                    elif rank == 0 and not advanced:
                        kind = "won"
                    else:
                        continue
                    cs.add("outcome", result_set_id=result, option_id=options[candidate], kind=kind)
            by_place: dict[str, int] = defaultdict(int)
            rows = []
            for candidate, by_county in by_candidate.items():
                for county, votes in by_county.items():
                    fips, _ = counties[plain(county)]
                    unit = cs.unit("us.county", fips)
                    if office != "ASSOCIATE DISTRICT JUDGE":
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
        print(made, "contests,", len(districts), "districts")
        return cs.write()
