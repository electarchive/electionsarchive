import io
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import fl_sos
from ea.sources.us.retention import display, identity
from ea.store import connect

SOURCE = "us.fl_judicial"
VERSION = "1"
TITLE_OFFICE = "Justice of the Supreme Court"
SHALL = re.compile(r"Shall Justice (.+?) be retained in office\?", re.IGNORECASE)
METHOD = (
    "A retention election asks whether a sitting justice should stay in office, with Yes and No as the only answers. "
    "The justice keeps the seat when a majority votes Yes."
)
NOTE = "County totals from the Florida Division of Elections' official results extract for the general election."


def flat(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())


def lines() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for day in fl_sos.DAYS:
        if day.year < 2000:
            continue
        text = raw.cached(fl_sos.SOURCE, fl_sos.EXTRACT.format(day=day)).path.read_bytes().decode("cp1252")
        frame = pl.read_csv(io.BytesIO(text.encode()), separator="\t", infer_schema_length=0, quote_char=None)
        for race, desc, county, answer, votes in (
            frame.filter(pl.col("RaceCode").str.contains(r"^SC\d$"))
            .select("RaceCode", "OfficeDesc", "CountyName", "CanNameLast", "CanVotes")
            .iter_rows()
        ):
            match = SHALL.fullmatch(desc.strip())
            if match is None:
                raise ValueError(f"{day} {race}: {desc}")
            found.append(
                {
                    "day": day,
                    "judge": re.sub(r"\s+", " ", match[1]).strip(),
                    "county": county,
                    "answer": "yes" if answer.strip().lower().startswith("yes") else "no",
                    "votes": int(votes or 0),
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
            summary="Florida Supreme Court retention elections 2000–2012 by county, from the Division of Elections' official results extracts",
            polity="US-FL",
        )
        source = cs.source(fl_sos.PUBLISHER, fl_sos.TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=fl_sos.PUBLISHER,
            title=fl_sos.TITLE,
            url="https://results.elections.myflorida.com/",
            license="Public record",
        )
        for day in fl_sos.DAYS:
            if day.year >= 2000:
                cs.artifact(raw.cached(fl_sos.SOURCE, fl_sos.EXTRACT.format(day=day)), source)
        counties = {
            flat(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '12%%'"""
            ).fetchall()
        }
        state_area = conn.execute(
            """select u.area_id from polity p join reporting_unit u on u.polity_id = p.id and u.scheme_code = 'us.state'
               where p.iso_code = 'US-FL'"""
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/fl/%%'").fetchall()}
        retention_system = cs.key("us.electoral_system", "retention")

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

        office_key = f"{slug(TITLE_OFFICE)}:FL"
        office = cs.key("us.office", office_key)
        if office in cs.minted:
            cs.add("office", id=office, polity_id=cs.polity("US-FL"), kind="other")
            cs.add(
                "name",
                entity_id=office,
                text=f"{TITLE_OFFICE} of Florida",
                lang="en",
                kind="official",
                is_preferred=True,
            )
        groups: dict[tuple[date, str], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            groups[(row["day"], str(row["judge"]))].append(row)
        persons: set[str] = set()
        for (day, judge), group in sorted(groups.items()):
            first, last = identity(judge)
            person_key = f"FL:{slug(first)}-{slug(last)}"
            suffix = person_key.split(":")[1]
            shown = display(judge)
            contest_key = f"{day}:FL:{slug(TITLE_OFFICE)}:{suffix}"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:FL")
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
                electoral_system_id=retention_system,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Florida {TITLE_OFFICE} retention: {shown}")
            if contest in cs.minted:
                base = f"us/fl/{day}-general/{slug(TITLE_OFFICE)}-{suffix}"
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
            cs.add("note", entity_id=result, kind="methodology", body_md=f"{METHOD} {NOTE}")
            person = cs.key("us.retention.person", person_key)
            if person_key not in persons:
                persons.add(person_key)
                if person in cs.minted:
                    cs.add("person", id=person)
                name(person, shown)
            options: dict[str, UUID] = {}
            for position, answer in enumerate(("yes", "no")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.capitalize(),
                )
            candidacy = cs.key("us.candidacy", f"{contest_key}:yes:head")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options["yes"],
                role="head",
                ballot_name=shown,
                is_independent=False,
            )
            by_unit: dict[str, dict[str, int]] = defaultdict(lambda: {"yes": 0, "no": 0})
            for row in group:
                place = "miamidade" if flat(str(row["county"])) == "dade" else flat(str(row["county"]))
                by_unit[str(counties[place])][str(row["answer"])] += int(row["votes"])
            yes = sum(counts["yes"] for counts in by_unit.values())
            no = sum(counts["no"] for counts in by_unit.values())
            retained = yes > no
            cs.add(
                "outcome",
                result_set_id=result,
                option_id=options["yes"],
                candidacy_id=candidacy,
                kind="elected" if retained else "lost",
                seats=1 if retained else None,
            )
            values = []
            for unit, counts in by_unit.items():
                values.append((str(result), unit, str(options["yes"]), 0, 1, 0, 0, counts["yes"]))
                values.append((str(result), unit, str(options["no"]), 0, 1, 0, 0, counts["no"]))
                values.append((str(result), unit, None, 0, 4, 0, 0, counts["yes"] + counts["no"]))
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
            print(contest_key, yes, no)
        return cs.write()
