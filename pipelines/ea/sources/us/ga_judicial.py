import re
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.pa_judicial import number, sheet
from ea.sources.us.retention import display, identity
from ea.store import DATA, connect

SOURCE = "us.ga_judicial"
VERSION = "1"
INBOX = DATA / "inbox" / "manual" / "MF-455"
URL = "https://results.sos.ga.gov/"
PUBLISHER = "Georgia Secretary of State"
TITLE = "Election results: statewide judicial contests"
FILES = {
    date(2012, 5, 22): "2012/Total Votes Results_7ace7d3c-3060-4025-92ea-193579a26afd.xlsx",
    date(2014, 5, 20): "2014/Total Votes Results_594f6967-4eb3-426a-a13d-c2de98f7aac4.xlsx",
    date(2016, 5, 24): "2016/Total Votes Results_a32dd413-cf78-4bc9-bac8-6525bd423da6.xlsx",
    date(2018, 5, 22): "2018/Total Votes Results_e81e4672-ebaa-4a56-9f3d-615d369f7815.xlsx",
    date(2020, 6, 9): "2020/Total Votes Results_434f7487-b57e-46b2-8cdb-86b60a34f65a.xlsx",
    date(2022, 5, 24): "2022/Total Votes Results_fb34a8c4-3127-4742-8c5b-691de9c22401.xlsx",
    date(2024, 5, 21): "2024/Total Votes Results_dd4a2851-411f-4720-abea-3ce4cf813d1f.xlsx",
}
COURTS = {"Supreme": "Justice of the Supreme Court", "Appeals": "Judge of the Court of Appeals"}
NOTE = (
    "Statewide totals from the Georgia Secretary of State's results report for the nonpartisan election held with the "
    "general primary. A judge needs a majority of the votes cast to win outright; a runoff is held otherwise."
)
UNCONTESTED = "The judge was unopposed; write-in votes are included where the report lists them."


def fetch() -> None:
    for day, name in FILES.items():
        try:
            raw.cached(SOURCE, f"{URL}#{day}")
        except LookupError:
            raw.store(
                SOURCE,
                f"{URL}#{day}",
                (INBOX / name).read_bytes(),
                suffix=".xlsx",
                retrieved_at=datetime.now(UTC),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def clean(text: str) -> str:
    text = re.sub(r"\((?:I|I_|I\))\)?|\(I_", "", text)
    text = re.sub(r'""', '"', text)
    return re.sub(r"\s+", " ", text).strip().rstrip(",")


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for day in FILES:
        for cells in sheet(raw.cached(SOURCE, f"{URL}#{day}").path)[1:]:
            office = cells.get("A", "")
            court = next((title for key, title in COURTS.items() if key in office), None)
            if court is None or "Superior" in office:
                continue
            found.append(
                {
                    "day": day,
                    "office": office,
                    "court": court,
                    "candidate": clean(cells["C"]),
                    "votes": number(cells["F"]),
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
            summary="Georgia Supreme Court and Court of Appeals nonpartisan elections 2012–2024, statewide, "
            "from the Secretary of State's results reports",
            polity="US-GA",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record",
        )
        for day in FILES:
            cs.artifact(raw.cached(SOURCE, f"{URL}#{day}"), source)
        state_unit, state_area, polity = conn.execute(
            """select u.id, u.area_id, p.id from polity p
               join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-GA'"""
        ).fetchone()
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/ga/%%'").fetchall()}
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
        elections: set[UUID] = set()
        groups: dict[tuple[date, str, str], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], str(row["court"]), str(row["office"]))].append(row)
        made = 0
        for (day, title, _), group in sorted(groups.items()):
            office = cs.key("us.office", f"{slug(title)}:GA")
            if office not in offices:
                offices.add(office)
                if office in cs.minted:
                    cs.add("office", id=office, polity_id=polity, kind="other")
                    cs.add(
                        "name",
                        entity_id=office,
                        text=f"{title} of Georgia",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
            election = cs.key("us.election", f"{day}:GA:primary")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} Georgia primary election")
                cs.add("slug", path=f"us/ga/{day}-primary", entity_id=election)
            people = [row for row in group if row["candidate"] not in ("Total Votes", "Write-in")]
            surnames = "-".join(slug(identity(str(row["candidate"]))[1]) for row in people)
            contest_key = f"{day}:GA:{slug(title)}:{surnames}"
            contest = cs.key("us.contest", contest_key)
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
            shown = " and ".join(display(str(row["candidate"])) for row in people)
            name(contest, f"{day.year} Georgia {title} election: {shown}")
            if contest in cs.minted:
                base = f"us/ga/{day}-primary/{slug(title)}-{surnames}"
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
            cs.add(
                "note",
                entity_id=result,
                kind="methodology",
                body_md=NOTE if len(people) > 1 else f"{NOTE} {UNCONTESTED}",
            )
            total = next(int(row["votes"]) for row in group if row["candidate"] == "Total Votes")
            options: dict[str, UUID] = {}
            candidacies: dict[str, UUID] = {}
            values = []
            for position, row in enumerate(row for row in group if row["candidate"] != "Total Votes"):
                choice = str(row["candidate"])
                if choice == "Write-in":
                    options[choice] = cs.key("us.option", f"{contest_key}:write-in")
                    cs.add(
                        "contest_option",
                        id=options[choice],
                        contest_id=contest,
                        kind="write_in",
                        ballot_position=position + 1,
                        ballot_label="Write-in",
                    )
                else:
                    first, last = identity(choice)
                    person_key = f"GA:{slug(first)}-{slug(last)}"
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
                values.append((str(result), str(state_unit), str(options[choice]), 0, 1, 0, 0, int(row["votes"])))
            values.append((str(result), str(state_unit), None, 0, 4, 0, 0, total))
            top = max(people, key=lambda row: int(row["votes"]))
            if int(top["votes"]) * 2 > total:
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options[str(top["candidate"])],
                    candidacy_id=candidacies[str(top["candidate"])],
                    kind="elected",
                    seats=1,
                )
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
            print(contest_key, total)
        print(made)
        return cs.write()
