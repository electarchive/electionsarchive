import re
import subprocess
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ar_cert, ar_pri_cert
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.ar_judicial"
VERSION = "1"
PRIMARY = (
    ar_pri_cert.SOURCE,
    ar_pri_cert.BASE + "2004_Preferential_Primary_and_Non-Partisan_Judicial_Election_Certification_Report.pdf",
    date(2004, 5, 18),
    "2004-05-18:AR:primary",
    "primary",
)
RUNOFF = (
    ar_cert.SOURCE,
    ar_cert.BASE + ar_cert.FILES[2004][0],
    date(2004, 11, 2),
    "2004-11-02:AR",
    "general",
)
HEADING = re.compile(r"^State Supreme Court (Chief|Associate) Justice (Position \d+)(?: \((.+?) County\))?\s*$")
CANDIDATE = re.compile(r"^(.+?) - Non Partisan Judicial\s+([\d,]+)\s+[\d.]+%\s*$")
ALIASES = {"Jim Gunter": "James Gunter"}
TITLES = {"Chief": "Chief Justice of the Supreme Court", "Associate": "Justice of the Supreme Court"}
NOTE = "County totals from the Secretary of State's certification report; the statewide page agrees with their sum."


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()).replace("saint", "st")


def read(source: str, url: str) -> dict[tuple[str, str], dict[str | None, dict[str, int]]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(source, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    found: dict[tuple[str, str], dict[str | None, dict[str, int]]] = defaultdict(dict)
    current: tuple[tuple[str, str], str | None] | None = None
    block: dict[str, int] = {}

    def close() -> None:
        if current is None or not block:
            return
        place = found[current[0]]
        if current[1] in place and place[current[1]] != block:
            raise ValueError(f"{current}: sections disagree")
        place[current[1]] = dict(block)

    for line in text.splitlines():
        line = line.strip()
        if match := HEADING.match(line):
            close()
            current, block = ((match[1], match[2]), match[3]), {}
            continue
        if current is None:
            continue
        if match := CANDIDATE.match(line):
            name = re.sub(r"^(Justice|Judge)\s+", "", match[1].strip())
            block[ALIASES.get(name, name)] = int(match[2].replace(",", ""))
        elif line and not re.match(r"(Over|Under)", line):
            close()
            current, block = None, {}
    close()
    return found


def emit() -> Path:
    contests = []
    for source, url, day, election_key, kind in (PRIMARY, RUNOFF):
        for (court, seat), places in read(source, url).items():
            if court == "Chief" and day.month == 11:
                continue
            if None not in places:
                raise ValueError(f"{day} {court} {seat}: no statewide page")
            totals = places.pop(None)
            summed: dict[str, int] = defaultdict(int)
            for place in places.values():
                for name, votes in place.items():
                    summed[name] += votes
            if dict(summed) != totals or len(places) != 75:
                raise ValueError(f"{day} {court} {seat}: counties {len(places)} {dict(summed)} differ from {totals}")
            contests.append((source, url, day, election_key, kind, court, seat, places))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arkansas Supreme Court 2004 nonpartisan contests by county: the May 18 preferential primary "
            "for Chief Justice and Associate Justice Position 4, and the November 2 runoff for Position 4, "
            "from the Secretary of State's certification reports",
            polity="US-AR",
        )
        sources: dict[str, UUID] = {}
        for source, url, day, _, kind, *_rest in contests:
            if url in sources:
                continue
            title = f"{day.year} {'preferential primary and nonpartisan judicial election' if kind == 'primary' else 'general election and nonpartisan judicial runoff'} certification report"
            sources[url] = cs.source("Arkansas Secretary of State", title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher="Arkansas Secretary of State",
                title=title,
                url="https://www.sos.arkansas.gov/elections/research/election-results",
                license="Public record",
            )
            cs.artifact(raw.cached(source, url), sources[url])
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%%' and u.valid @> '2004-06-01'::date"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-AR'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/ar/%%'").fetchall()}
        fptp = cs.key("us.electoral_system", "fptp")
        persons: set[str] = set()
        first_round: dict[tuple[str, str], UUID] = {}
        for _, url, day, election_key, kind, court, seat, places in sorted(contests, key=lambda item: item[2]):
            title = TITLES[court]
            office = cs.key("us.office", f"{slug(title)}:AR")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=cs.polity("US-AR"), kind="other")
                cs.add(
                    "name",
                    entity_id=office,
                    text=f"{title} of Arkansas",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
            election = cs.key("us.election", election_key)
            contest_key = f"{day}:AR:{slug(title)}:{slug(seat)}"
            contest = cs.key("us.contest", contest_key)
            runoff = kind == "general"
            previous = first_round.get((court, seat)) if runoff else None
            first_round[(court, seat)] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=state_area,
                term="full",
                magnitude=1,
                round_number=2 if runoff else 1,
                previous_round_id=previous,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            label = f"{title} ({seat})" + (" runoff" if runoff else "")
            cs.add(
                "name",
                entity_id=contest,
                text=f"{day.year} Arkansas {label}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            base = f"us/ar/{day}-{'general' if runoff else 'primary'}/{slug(title)}-{slug(seat)}"
            path, count = base, 1
            while path in taken:
                count += 1
                path = f"{base}-{count}"
            taken.add(path)
            cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[url])
            result = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=NOTE)
            names = list(next(iter(places.values())))
            totals = {name: sum(place.get(name, 0) for place in places.values()) for name in names}
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            for position, name in enumerate(names):
                first, last = identity(name)
                person_key = f"AR:{slug(first)}-{slug(last)}"
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add(
                            "name",
                            entity_id=person,
                            text=display(name),
                            lang="en",
                            kind="official",
                            is_preferred=True,
                        )
                options[name] = cs.key("us.option", f"{contest_key}:{slug(name)}")
                cs.add(
                    "contest_option",
                    id=options[name],
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position + 1,
                    ballot_label=display(name),
                )
                candidacies[name] = cs.key("us.candidacy", f"{contest_key}:{slug(name)}")
                cs.add(
                    "candidacy",
                    id=candidacies[name],
                    person_id=person,
                    option_id=options[name],
                    role="head",
                    ballot_name=display(name),
                    is_independent=False,
                )
            ranked = sorted(totals, key=totals.__getitem__, reverse=True)
            decided = runoff or court == "Chief" or totals[ranked[0]] * 2 > sum(totals.values())
            for name in ranked[: 1 if decided else 2]:
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options[name],
                    candidacy_id=candidacies[name],
                    kind="elected" if decided else "advanced",
                    seats=1,
                )
            values = []
            for place, votes in places.items():
                unit = counties[flat(place)]
                for name, count_ in votes.items():
                    values.append((str(result), str(unit), str(options[name]), 0, 1, 0, 0, count_))
                values.append((str(result), str(unit), None, 0, 4, 0, 0, sum(votes.values())))
            cs.frame(
                "result_value",
                pl.DataFrame(
                    values,
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
            print(contest_key, totals)
        return cs.write()
