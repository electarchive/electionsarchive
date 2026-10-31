import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ncsbe
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.nc_judicial"
VERSION = "1"
PARTIES = {"DEM": "democratic-party", "REP": "republican-party"}
TITLES = {
    "chief": "Chief Justice of the Supreme Court",
    "justice": "Justice of the Supreme Court",
    "appeals": "Judge of the Court of Appeals",
}
SEAT = re.compile(r"\(\s*(.+?)(?: Seat)?\s*\)\s*$|- (.+?) Seat\s*$", re.IGNORECASE)
NOTE = "County totals summed from the North Carolina State Board of Elections' official precinct results file for the general election."


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for day in ncsbe.DAYS:
        frame = ncsbe.read(day)
        party = next(column for column in ("party_cd", "party", "choice party") if column in frame.columns)
        frame = frame.filter(
            pl.col("contest").str.to_uppercase().str.contains("SUPREME|CHIEF JUSTICE|ASSOC JUSTICE|APPEALS")
            & ~pl.col("contest").str.contains("IRV")
        )
        for contest, choice, code, county, votes in (
            frame.group_by("contest", "choice", party, "county")
            .agg(pl.col("votes").cast(pl.Int64, strict=False).fill_null(0).sum())
            .select("contest", "choice", party, "county", "votes")
            .iter_rows()
        ):
            upper = contest.upper()
            court = "chief" if "CHIEF" in upper else "appeals" if "APPEALS" in upper else "justice"
            match = SEAT.search(contest)
            seat = ""
            if court != "chief" and match is not None:
                seat = (match[1] or match[2]).strip().title()
            found.append(
                {
                    "day": day,
                    "court": court,
                    "seat": seat,
                    "candidate": re.sub(r"\s+", " ", choice).strip(),
                    "party": code.strip().strip("\x00"),
                    "county": county,
                    "votes": votes,
                }
            )
    return found


def emit() -> Path:
    rows = lines()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina Supreme Court and Court of Appeals general election contests 2000–2014 by county, "
            "from the State Board of Elections' official precinct results",
            polity="US-NC",
        )
        source = cs.source(ncsbe.PUBLISHER, ncsbe.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=ncsbe.PUBLISHER,
            title=ncsbe.TITLE,
            url="https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
            license="Public record",
        )
        for day in ncsbe.DAYS:
            cs.artifact(raw.cached(ncsbe.SOURCE, ncsbe.FILES.format(day=day)), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%%'"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-NC'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/nc/%%'").fetchall()}
        fptp = cs.key("us.electoral_system", "fptp")

        def name(entity: UUID, text: str) -> None:
            found = conn.execute(
                """select n.id, n.text, c.pipeline from name n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.is_preferred""",
                [entity],
            ).fetchone()
            if found is not None and (found[1] == text or found[2] != SOURCE):
                return
            if found is not None:
                cs.add("name_drop", name_id=found[0], entity_id=entity, text=found[1], reason="Name restyled")
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        groups: dict[tuple[date, str, str], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], row["court"], row["seat"])].append(row)
        offices: set[UUID] = set()
        persons: set[str] = set()
        made = 0
        for (day, court, seat), group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
            title = TITLES[court]
            office_key = f"{slug(title)}:NC"
            office = cs.key("us.office", office_key)
            if office not in offices:
                offices.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=cs.polity("US-NC"), kind="other")
                    cs.add(
                        "name",
                        entity_id=office,
                        text=f"{title} of North Carolina",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            suffix = f":{slug(seat)}" if seat else ""
            contest_key = f"{day}:NC:{slug(title)}{suffix}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:NC")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=state_area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            label = f"{title}" + (f" ({seat} seat)" if seat else "")
            name(contest, f"{day.year} North Carolina {label}")
            if contest in cs.minted:
                base = f"us/nc/{day}-general/{slug(title)}" + (f"-{slug(seat)}" if seat else "")
                path, count = base, 1
                while path in taken:
                    count += 1
                    path = f"{base}-{count}"
                taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
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
            by_choice: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            parties: dict[str, str] = {}
            for row in group:
                by_choice[str(row["candidate"])][str(row["county"])] += int(row["votes"])
                parties[str(row["candidate"])] = str(row["party"])
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            for position, choice in enumerate(by_choice):
                first, last = identity(choice)
                person_key = f"NC:{slug(first)}-{slug(last)}"
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, display(choice))
                party = PARTIES.get(parties[choice])
                options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "contest_option",
                    id=options[choice],
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", party) if party else None,
                    ballot_position=position + 1,
                    ballot_label=display(choice),
                )
                candidacies[choice] = cs.key("us.candidacy", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "candidacy",
                    id=candidacies[choice],
                    person_id=person,
                    option_id=options[choice],
                    role="head",
                    ballot_name=display(choice),
                    is_independent=False,
                )
            totals = {choice: sum(by_unit.values()) for choice, by_unit in by_choice.items()}
            winner = max(totals, key=totals.__getitem__)
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options[winner],
                candidacy_id=candidacies[winner],
                kind="elected",
                seats=1,
            )
            by_place: dict[str, int] = defaultdict(int)
            values = []
            for choice, by_unit in by_choice.items():
                for place, votes in by_unit.items():
                    unit = counties[flat(place)]
                    by_place[str(unit)] += votes
                    values.append((str(result), str(unit), str(options[choice]), 0, 1, 0, 0, votes))
            values += [(str(result), unit, None, 0, 4, 0, 0, votes) for unit, votes in by_place.items()]
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
            made += 1
            print(contest_key, totals)
        print(made)
        return cs.write()
