import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import al_precincts
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.al_judicial"
VERSION = "1"
OFFICES = {
    "chief-justice-of-the-supreme-court": "Chief Justice of the Supreme Court",
    "justice-of-the-supreme-court": "Justice of the Supreme Court",
    "judge-of-the-court-of-civil-appeals": "Judge of the Court of Civil Appeals",
    "judge-of-the-court-of-criminal-appeals": "Judge of the Court of Criminal Appeals",
}
PARTIES = {"REP": "republican-party", "DEM": "democratic-party", "LIB": "libertarian-party"}
LETTERS = {"R": "REP", "D": "DEM", "L": "LIB"}
OFFICIAL = {date(2000, 11, 7): al_precincts.VOTER_PDFS + "2000/2000g-general.xls"}
OFFICIAL_TITLE = "General election results by county, November 7, 2000"
SUFFIXES = {"JR", "SR", "II", "III", "IV"}
MAIN = 0.03
LAST = 2014
NOTE = (
    "County totals are the sums of the precinct and countywide lines in the Secretary of State's precinct-level "
    "general election files."
)
PARTIAL = " The files have no judicial results for {count} of the state's 67 counties: {names}."


def fetch() -> None:
    for url in OFFICIAL.values():
        raw.fetch(SOURCE, url, suffix=".xls")


def surname(text: str) -> str:
    text = re.sub(r"\s*-\s*[A-Z]$", "", text.upper())
    head = text.split(",")[0]
    words = [word for word in re.findall(r"[A-Z'’]+", head) if word not in SUFFIXES]
    return words[-1] if words else ""


def lines(names: dict[str, str]) -> pl.DataFrame:
    frame = (
        pl.read_parquet(al_precincts.PARSED / "precincts.parquet")
        .filter(
            pl.col("chamber").str.contains("justice|judge")
            & (pl.col("measure") == 1)
            & ~pl.col("day").is_in(list(OFFICIAL))
            & (pl.col("year") <= LAST)
        )
        .with_columns(
            office=pl.col("chamber").str.replace(r":place-.*$", ""),
            place=pl.col("chamber").str.extract(r":place-(.)$"),
            surname=pl.col("candidate").map_elements(surname, return_dtype=pl.String),
        )
    )
    fixed: list[pl.DataFrame] = []
    for (day, office), group in frame.group_by("day", "office", maintain_order=True):
        named = {
            str(place[0]): main_surnames(rows) for place, rows in group.filter(pl.col("place") != "?").group_by("place")
        }
        unknown = group.filter(pl.col("place") == "?")
        best = None
        if unknown.height and named:
            theirs = main_surnames(unknown)
            best = max(named, key=lambda place: len(theirs & named[place]))
            if not theirs & named[best]:
                raise ValueError(f"{day} {office}: place-less candidates {theirs} match no place in {named}")
        fixed.append(
            group.with_columns(place=pl.when(pl.col("place") == "?").then(pl.lit(best, pl.String)).otherwise("place"))
        )
    precincts = pl.concat(fixed)
    return pl.concat([precincts, *(official(day, names, precincts) for day in OFFICIAL)], how="diagonal_relaxed")


def main_surnames(rows: pl.DataFrame) -> set[str]:
    totals = rows.filter(pl.col("candidate") != "WRITE-INS").group_by("surname").agg(pl.col("votes").sum())
    top = int(str(totals["votes"].max() or 0))
    return {name for name, votes in totals.iter_rows() if name and votes >= MAIN * top}


def canon(rows: pl.DataFrame) -> tuple[pl.DataFrame, list[tuple[str, int]]]:
    totals = rows.filter(pl.col("candidate") != "WRITE-INS").group_by("surname").agg(pl.col("votes").sum())
    top = int(str(totals["votes"].max() or 0))
    main = [name for name, votes in totals.iter_rows() if name and votes >= MAIN * top]
    target: dict[str, str] = {}
    for name in totals["surname"]:
        match = name if name in main else None
        if match is None:
            match = next((other for other in main if SequenceMatcher(None, name, other).ratio() >= 0.85), None)
        if match is not None:
            target[name] = match
    forms: dict[str, str] = {}
    for name in main:
        spelled = (
            rows.filter(pl.col("surname").is_in([k for k, v in target.items() if v == name]))
            .group_by("candidate")
            .agg(pl.col("votes").sum())
            .with_columns(words=pl.col("candidate").str.count_matches(r"[A-Z]{2,}"))
            .sort("words", "votes", descending=True)
        )
        forms[name] = spelled["candidate"][0]
    dropped = [(name, int(votes)) for name, votes in totals.iter_rows() if name not in target]
    kept = rows.filter((pl.col("candidate") == "WRITE-INS") | pl.col("surname").is_in(list(target)))
    return (
        kept.with_columns(
            choice=pl.when(pl.col("candidate") == "WRITE-INS")
            .then(pl.lit("WRITE-INS"))
            .otherwise(pl.col("surname").replace_strict(target, default=None).replace_strict(forms, default=None))
        ),
        dropped,
    )


def official(day: date, names: dict[str, str], precincts: pl.DataFrame) -> pl.DataFrame:
    book = pl.read_excel(
        raw.cached(SOURCE, OFFICIAL[day]).path,
        has_header=False,
        infer_schema_length=0,
        sheet_id=0,
        raise_if_empty=False,
    )
    spelled: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for office, candidate in (
        precincts.filter(pl.col("candidate") != "WRITE-INS").select("office", "candidate").unique().iter_rows()
    ):
        if len(re.findall(r"[A-Za-z]{2,}", candidate)) > 1:
            spelled[(office, surname(candidate), identity(candidate)[0][:1])].add(candidate)
    found: list[tuple[date, str, str | None, str, str, str | None, str, int]] = []
    for frame in book.values():
        rows = [[cell or "" for cell in row] for row in frame.iter_rows()]
        if len(rows) < 3:
            continue
        first = 2 if rows[1][0] == "County ID" else 1
        owner = ""
        for position in range(first, len(rows[1])):
            owner = rows[0][position].strip() or owner
            label = rows[1][position].strip()
            office = al_precincts.judicial(re.sub(r"\s+", " ", owner.upper()))
            if office is None or not label:
                continue
            kind, _, place = office.partition(":place-")
            labeled = re.fullmatch(r"(.+?)\s*(?:\(([A-Z])\)|\s*-\s*([A-Z]))", label)
            party = LETTERS.get(labeled[2] or labeled[3]) if labeled else None
            bare = re.sub(r"\s+", " ", labeled[1] if labeled else label).strip()
            if re.fullmatch(r"(?i)w/i|write-ins?", bare):
                candidate = "WRITE-INS"
            else:
                initial = identity(bare)[0][:1]
                options = {
                    form
                    for (kind_, last, first_), forms in spelled.items()
                    if kind_ == kind and last == surname(bare) and first_.startswith(initial)
                    for form in forms
                }
                people = {identity(form) for form in options}
                candidate = max(options, key=len) if len(people) == 1 else bare
            for row in rows[2:]:
                county = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", row[first - 1]).lower())
                if county in names and row[position].strip():
                    found.append(
                        (
                            day,
                            kind,
                            place or None,
                            candidate,
                            surname(candidate),
                            party,
                            names[county],
                            int(row[position]),
                        )
                    )
    return pl.DataFrame(
        found,
        schema={
            "day": pl.Date,
            "office": pl.String,
            "place": pl.String,
            "candidate": pl.String,
            "surname": pl.String,
            "party": pl.String,
            "county_code": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )


def emit() -> Path:
    with connect() as conn:
        counties = {
            code: (unit, name)
            for code, unit, name in conn.execute(
                """select u.code, u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
        frame = lines({re.sub(r"[^a-z]", "", name.lower()): code for code, (_, name) in counties.items()})
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's statewide court contests in the November general elections of 2000 to 2014 by county, "
            "from the Secretary of State's precinct files",
            polity="US-AL",
        )
        source = cs.source(al_precincts.PUBLISHER, al_precincts.TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=al_precincts.PUBLISHER,
                title=al_precincts.TITLE,
                url=al_precincts.LANDING,
                license="Public record",
            )
        years = sorted({int(day.year) for day in frame["day"].unique()})
        for day, url in al_precincts.FILES:
            if day.year in years:
                cs.artifact(raw.cached(al_precincts.SOURCE, url), source)
        polity = cs.polity("US-AL")
        state_area = (
            conn.execute(
                """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
                   where p.iso_code = 'US-AL'"""
            ).fetchone()
            or (None,)
        )[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/al/%%'").fetchall()}
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

        offices: set[UUID] = set()
        persons: set[str] = set()
        made = 0
        for (day, office, place), rows in sorted(
            frame.group_by("day", "office", "place"), key=lambda item: (item[0][0], item[0][1], item[0][2] or "")
        ):
            title = OFFICES[office]
            office_id = cs.key("us.office", f"{office}:AL")
            if office_id not in offices:
                offices.add(office_id)
                if office_id in cs.minted:
                    cs.add("office", id=office_id, polity_id=polity, kind="other")
                    cs.add(
                        "name",
                        entity_id=office_id,
                        text=f"{title} of Alabama",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            seat = f"{office}:place-{place}" if place else office
            contest_key = f"{day}:AL:{seat}"
            election = cs.key("us.election", f"{day}:AL")
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office_id,
                district_area_id=state_area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Alabama {title} election" + (f" (Place {place})" if place else ""))
            if contest in cs.minted:
                base = f"us/al/{day}-general/{slug(seat.replace(':', '-'))}"
                path, count = base, 1
                while path in taken:
                    count += 1
                    path = f"{base}-{count}"
                taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            kept, dropped = canon(rows)
            by_choice: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            parties: dict[str, str] = {}
            for choice, code, party, votes in kept.select("choice", "county_code", "party", "votes").iter_rows():
                by_choice[choice][code] += votes
                if party in PARTIES:
                    parties[choice] = PARTIES[party]
            present = {code for by_county in by_choice.values() for code in by_county}
            absent = sorted(counties[code][1] for code in counties if code not in present)
            note = NOTE + (PARTIAL.format(count=len(absent), names=", ".join(absent)) if absent else "")
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
                completeness=round(len(present) / len(counties), 4),
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=note)
            totals = {choice: sum(by_county.values()) for choice, by_county in by_choice.items()}
            people = [choice for choice in by_choice if choice != "WRITE-INS"]
            winner = max(people, key=lambda choice: totals[choice])
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            for position, choice in enumerate(by_choice):
                options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                if choice == "WRITE-INS":
                    cs.add(
                        "contest_option",
                        id=options[choice],
                        contest_id=contest,
                        kind="write_in",
                        ballot_position=position + 1,
                        ballot_label="Write-in",
                    )
                    continue
                first, last = identity(choice)
                person_key = f"AL:{slug(first)}-{slug(last)}"
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, display(choice))
                party = parties.get(choice)
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
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options[winner],
                candidacy_id=candidacies[winner],
                kind="elected",
                seats=1,
            )
            by_unit: dict[str, int] = defaultdict(int)
            values: list[tuple[str, str, str | None, int, int, int, int, int]] = []
            for choice, by_county in by_choice.items():
                for code, votes in by_county.items():
                    unit = str(counties[code][0])
                    by_unit[unit] += votes
                    values.append((str(result), unit, str(options[choice]), 0, 1, 0, 0, votes))
            values += [(str(result), unit, None, 0, 4, 0, 0, votes) for unit, votes in by_unit.items()]
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
            print(contest_key, {choice: totals[choice] for choice in by_choice}, len(present), dropped)
        print(made, file=sys.stderr)
        return cs.write()
