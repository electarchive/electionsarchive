import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import mi_cenr_statewide
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.mi_cenr_judicial"
VERSION = "1"
CACHED = [1998, 2000, 2002, 2004, 2006, 2008, 2010, 2012, 2014]
FETCHED = [2016, 2018, 2020]
TITLE = "Justice of the Supreme Court"
PARTIAL = re.compile(r"Partial Term Ending (\d\d)/(\d\d)/(\d{4})")
SEATS = re.compile(r"\((\d+)\) Position")
NOTE = (
    "County totals from the Michigan Department of State's official general election results by county. "
    "Candidates are nominated by party conventions but stand on the nonpartisan section of the ballot, and voters "
    "choose as many candidates as there are seats; the winners are the candidates with the most votes."
)


def flat(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).lower().replace("saint ", "st ")
    return re.sub(r"[^a-z]", "", re.sub(r"^gd\.? ", "grand ", text))


def url(year: int) -> str:
    return mi_cenr_statewide.FILE.format(year)


def path(year: int) -> Path:
    source = mi_cenr_statewide.SOURCE if year in CACHED else SOURCE
    return raw.cached(source, url(year)).path


def fetch() -> None:
    for year in FETCHED:
        raw.fetch(SOURCE, url(year), suffix=".txt")


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for year in [*CACHED, *FETCHED]:
        frame = pl.read_csv(path(year), separator="\t", infer_schema=False, quote_char=None).filter(
            (pl.col("OfficeCode(Text)") == "13") & pl.col("CandidateVotes").is_not_null()
        )
        for row in frame.iter_rows(named=True):
            if (row["WriteIn(W)/Uncommitted(Z)"] or "").strip() == "W":
                continue
            name = " ".join(
                part.strip()
                for part in (row["CandidateFirstName"], row["CandidateMiddleName"], row["CandidateLastName"])
                if part is not None and part.strip()
            )
            description = row["OfficeDescription"]
            partial = PARTIAL.search(description)
            found.append(
                {
                    "day": date.fromisoformat(row["ElectionDate"][:10]),
                    "ends": int(partial[3]) if partial else None,
                    "magnitude": int(SEATS.search(description)[1]),
                    "candidate": re.sub(r"\s+", " ", name).upper(),
                    "county": row["CountyName"],
                    "votes": int(row["CandidateVotes"]),
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
            summary="Michigan Supreme Court general election contests 1998–2020 by county, "
            "from the Department of State's official results",
            polity="US-MI",
        )
        source = cs.source(mi_cenr_statewide.PUBLISHER, mi_cenr_statewide.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=mi_cenr_statewide.PUBLISHER,
            title=mi_cenr_statewide.TITLE,
            url="https://miboecfr.nictusa.com/election/results/",
            license="Public record",
        )
        for year in [*CACHED, *FETCHED]:
            raw_source = mi_cenr_statewide.SOURCE if year in CACHED else SOURCE
            cs.artifact(raw.cached(raw_source, url(year)), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '26%%'"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-MI'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/mi/%%'").fetchall()}
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block")}

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

        groups: dict[tuple[date, int | None], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], row["ends"])].append(row)
        office = cs.key("us.office", f"{slug(TITLE)}:MI")
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-MI"), kind="other")
            name(office, f"{TITLE} of Michigan")
        persons: set[str] = set()
        made = 0
        for (day, ends), group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or 0)):
            magnitude = int(group[0]["magnitude"])
            suffix = f"partial-term-ending-{ends}" if ends else ""
            contest_key = f"{day}:MI:{slug(TITLE)}" + (f":{suffix}" if suffix else "")
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:MI")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=state_area,
                term="remainder" if ends else "full",
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp"] if magnitude == 1 else systems["block"],
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Michigan {TITLE}" + (f" (term ending {ends})" if ends else ""))
            if contest in cs.minted:
                base = f"us/mi/{day}-general/{slug(TITLE)}" + (f"-{suffix}" if suffix else "")
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
            for row in group:
                by_choice[str(row["candidate"])][str(row["county"])] += int(row["votes"])
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            for position, choice in enumerate(by_choice):
                first, last = identity(choice)
                person_key = f"MI:{slug(first)}-{slug(last)}"
                person = cs.key("us.retention.person", person_key)
                if person_key not in persons:
                    persons.add(person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                    name(person, display(choice))
                options[choice] = cs.key("us.option", f"{contest_key}:{slug(choice)}")
                cs.add(
                    "contest_option",
                    id=options[choice],
                    contest_id=contest,
                    kind="candidate",
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
            for winner in sorted(totals, key=totals.__getitem__, reverse=True)[:magnitude]:
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
            print(contest_key, sorted(totals.items(), key=lambda item: -item[1])[:3])
        print(made)
        return cs.write()
