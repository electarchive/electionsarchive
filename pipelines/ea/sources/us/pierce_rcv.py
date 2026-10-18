import json
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.pierce_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Pierce County Auditor"
ORIGIN = "http://www.co.pierce.wa.us/xml/abtus/ourorg/aud/Elections/RCV/ranked/{}/summary.pdf"
WAYBACK = "https://web.archive.org/web/{}id_/{}"
DAY = date(2008, 11, 4)
CONTESTS = {
    "exec": ("20120511021028", "County Executive", "alged:53053_2008_11_pierce_WA_County Executive_NA_1"),
    "assess": ("20120511021021", "County Assessor - Treasurer", None),
    "cc2": (
        "20120511021014",
        "County Council - District No. 2",
        "alged:53053_2008_11_pierce_WA_County Legislature_2_1",
    ),
}
METHOD = "Data from the Pierce County Auditor's official ranked-choice summary report, dated November 25, 2008. Ballots that were overvoted or left blank in round 1 are counted as invalid. Ballots that overvoted or ran out of choices in later rounds are counted as exhausted. Write-ins are grouped into a single line."
DEMOTED = "Lists each finalist's total from the last round, as compiled by the American Local Government Elections Database. The county auditor's official round-by-round count is the default."
SPENT = ("Exhausted by Over Votes", "Under Votes", "Exhausted Ballots")


def fetch() -> None:
    for folder, (stamp, _, _) in CONTESTS.items():
        raw.fetch(SOURCE, WAYBACK.format(stamp, ORIGIN.format(folder)), suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests = {}
    for folder, (stamp, title, _) in CONTESTS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, WAYBACK.format(stamp, ORIGIN.format(folder))).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if f"Contest: {title}" not in text or "Official" not in text:
            raise ValueError(f"{folder}: not the official summary for {title}")
        rows: dict[str, list[int]] = {}
        for line in text[text.index("Transfer") :].splitlines()[1:]:
            match = re.fullmatch(r"\s*(\S.*?)\s{2,}([-+]?\d+(?:\s+(?:[-+]?\d+|[\d.]+%))*)\s*", line)
            if match is not None:
                numbers = [int(cell) for cell in match[2].split() if not cell.endswith("%")]
                rows[match[1]] = numbers if match[1] == "Continuing Ballots" else numbers[0::2]
        continuing = rows.pop("Continuing Ballots")
        total = rows.pop("TOTAL")
        spent = {label: rows.pop(label) for label in SPENT}
        rounds = [{label: values[number] for label, values in rows.items()} for number in range(len(continuing))]
        for number, tally in enumerate(rounds):
            if sum(tally.values()) != continuing[number]:
                raise ValueError(f"{folder} round {number + 1}: the candidates do not add up to the continuing ballots")
            if continuing[number] + sum(values[number] for values in spent.values()) != total[number]:
                raise ValueError(f"{folder} round {number + 1}: the ballots do not add up to the total")
        contests[folder] = {
            "ballots": total[0],
            "invalid": sum(values[0] for values in spent.values()),
            "rounds": rounds,
        }
    (PARSED / "contests.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "contests.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Pierce County, Washington's 2008 ranked-choice races for county executive, assessor-treasurer, and "
            "council District 2, round by round from the auditor's official summary reports",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        ((election, polity),) = conn.execute(
            """select e.id, e.polity_id from election e join slug s on s.entity_id = e.id
               where s.path = 'us/wa/pierce-county/2008-11-04-general'"""
        ).fetchall()
        ((office,),) = conn.execute(
            """select o.id from office o join name n on n.entity_id = o.id
               where n.text = 'Pierce County Assessor/Treasurer' and o.polity_id = %s""",
            [polity],
        ).fetchall()
        irv = cs.key("us.electoral_system", "pierce-county-wa:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Pierce County ranked-choice voting")
        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s""",
            [polity],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add(person)
        columns = (
            "election_id",
            "kind",
            "office_id",
            "body_id",
            "district_area_id",
            "seat_id",
            "boundary_set_id",
            "magnitude",
            "party_scope_id",
            "round_number",
            "previous_round_id",
            "allocation_parent_id",
            "rules_override",
            "default_result_kind",
            "term",
        )
        fields = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness")
        fields += ("supersedes_id",)

        for folder, (stamp, title, existing) in CONTESTS.items():
            record = contests[folder]
            heading = f"General Election, November 4, 2008: {title}, ranked choice voting results summary report"
            source = cs.source(PUBLISHER, heading)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=heading,
                url=ORIGIN.format(folder),
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, ORIGIN.format(folder))), source)
            current: dict[str, UUID] = {}
            if existing is None:
                contest = cs.key("us.contest", f"{DAY}:WA:53053:assessor-treasurer")
                ((area, top),) = conn.execute(
                    """select k.district_area_id, u.id from contest k join external_id x on x.entity_id = k.id
                       join result_set r on r.contest_id = k.id join public.result_value v on v.result_set_k = r.k
                       join reporting_unit u on u.k = v.unit_k where x.scheme = 'us.contest' and x.value = %s
                       group by 1, 2""",
                    [CONTESTS["exec"][2]],
                ).fetchall()
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=irv,
                    default_result_kind="counted",
                    term="full",
                )
                name(contest, "2008 Pierce County Assessor-Treasurer election")
                cs.add("slug", path="us/wa/pierce-county/2008-11-04-general/assessor-treasurer", entity_id=contest)
            else:
                ((contest,),) = conn.execute(
                    "select entity_id from external_id where scheme = 'us.contest' and value = %s", [existing]
                ).fetchall()
                (row,) = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchall()
                cs.add("contest", id=contest, **dict(zip(columns, row, strict=True)), electoral_system_id=irv)
                ((top,),) = conn.execute(
                    """select distinct u.id from result_set r join public.result_value v on v.result_set_k = r.k
                       join reporting_unit u on u.k = v.unit_k where r.contest_id = %s""",
                    [contest],
                ).fetchall()
                for values in conn.execute(
                    f"""select {", ".join(fields)} from result_set where contest_id = %s
                        and changeset_id not in (select id from changeset where pipeline = %s)""",
                    [contest, SOURCE],
                ).fetchall():
                    cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
                    cs.add("note", entity_id=values[0], kind="caveat", body_md=DEMOTED)
                for option, label in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall():
                    current[spell(label)[-1]] = option

            result = cs.key(f"{SOURCE}.result_set", f"{DAY}:{folder}")
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
            rounds = record["rounds"]
            valid = record["ballots"] - record["invalid"]
            values: list[tuple[UUID | None, int, int, int]] = [
                (None, 3, 0, record["ballots"]),
                (None, 5, 0, record["invalid"]),
                (None, 4, 1, valid),
            ]
            final = rounds[-1]
            winner = max(final, key=lambda label: final[label])
            if 2 * final[winner] <= sum(final.values()):
                raise ValueError(f"{folder}: no final-round majority")
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, valid - sum(tally.values())))
            for label in rounds[0]:
                if label == "Write-In":
                    option = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    cs.add("outcome", result_set_id=result, option_id=option, kind="eliminated")
                else:
                    words = spell(label)
                    if words[-1] in current:
                        option = current[words[-1]]
                    else:
                        matches = people.get((words[0], words[-1]), set())
                        if len(matches) == 1:
                            (person,) = matches
                        else:
                            person = cs.key(f"{SOURCE}.person", f"{words[0]}-{words[-1]}")
                            cs.add("person", id=person)
                            name(person, label)
                        option = cs.key("us.option", f"{contest}:{person}")
                        cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=label)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=label,
                            member_of_party_id=None,
                            is_independent=False,
                            is_incumbent=None,
                        )
                    for outcome in (
                        ("won", "elected") if label == winner else ("lost" if final[label] else "eliminated",)
                    ):
                        cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
                for number, tally in enumerate(rounds, 1):
                    if number == 1 or rounds[number - 2][label]:
                        values.append((option, 1, number, tally[label]))
            for option, measure, number, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=top,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=1 if measure in (4, 21) else 0,
                    value=votes,
                )
        return cs.write()
