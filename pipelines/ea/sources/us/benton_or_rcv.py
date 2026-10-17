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

SOURCE = "us.benton_or_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Benton County Records and Elections"
REPORT = "https://re.bentoncountyor.gov/wp-content/uploads/2023/12/2022Nov08-RCV.pdf"
TITLE = "General Election, November 8, 2022, ranked choice voting summary reports"
DAY = date(2022, 11, 8)
SEATS = {"Corvallis Mayor": "mayor", "Corvallis Council Ward 9": "council:ward-9"}
METHOD = "From the county's official ranked-choice summary report. Ballots that were overvoted or left blank in round 1 are counted as invalid; ballots that ran out of choices, overvoted, or skipped rankings in later rounds are counted as exhausted. Write-ins are grouped into a single line."


def fetch() -> None:
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, REPORT).path), "-"], check=True, capture_output=True, text=True
    ).stdout.replace("‐", "-")
    contests = {}
    for page in text.split("\f"):
        found = re.findall(r"(?m)^Office Title\s+(.+)$", page)
        if not found:
            continue
        (title,) = found
        rows: dict[str, list[int]] = {}
        for line in page[page.index("Candidate Names") :].splitlines()[1:]:
            match = re.fullmatch(r"\s*(\S.*?)\s{2,}(-?\d+(?:\s+(?:-?\d+|[\d.]+%))*)\s*", line)
            if match is not None:
                numbers = [int(cell) for cell in match.group(2).split() if not cell.endswith("%")]
                rows[match.group(1)] = numbers if match.group(1).startswith("Winning") else numbers[0::2]
        thresholds = rows.pop("Winning threshold by round")
        accounting = {
            label: rows.pop(label) for label in list(rows) if label.startswith(("By ", "Continuing", "TOTAL"))
        }
        rounds = [{label: values[number] for label, values in rows.items()} for number in range(len(thresholds))]
        for number, tally in enumerate(rounds):
            continuing = accounting["Continuing Ballots"][number]
            if sum(tally.values()) != continuing or thresholds[number] != continuing // 2 + 1:
                raise ValueError(f"{title}: round {number + 1} does not reconcile")
            spent = sum(accounting[label][number] for label in accounting if label.startswith("By "))
            if spent + continuing != accounting["TOTAL"][number]:
                raise ValueError(f"{title}: round {number + 1} ballots do not add up")
        invalid = accounting["By Overvotes"][0] + accounting["By Undervotes"][0] + accounting["By Exhausted Choices"][0]
        contests[SEATS[title.strip()]] = {"ballots": accounting["TOTAL"][0], "invalid": invalid, "rounds": rounds}
    if len(contests) != len(SEATS):
        raise ValueError(f"expected {len(SEATS)} contests, found {list(contests)}")
    (PARSED / "contests.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "contests.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Corvallis, Oregon's 2022 mayoral and Ward 9 council races, round by round from Benton County's "
            "ranked-choice summary reports",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "4115800")
        top = cs.unit("us.place", "4115800")
        mayor = cs.key("us.office", "OR:4115800:mayor")
        council = cs.key("us.body", "OR:4115800:council")
        if {city, top, mayor, council} & cs.minted:
            raise ValueError("Corvallis, its mayor, and its council must already exist")
        ((polity,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchall()
        ((parent,),) = conn.execute(
            """select e.id from election e join name n on n.entity_id = e.id
               where e.date_from = %s and n.text = '2022 Oregon general election' and e.polity_id = %s""",
            [DAY, cs.polity("US-OR")],
        ).fetchall()
        irv = cs.key("us.electoral_system", "corvallis:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Corvallis ranked-choice voting")
        election = cs.key("us.election", f"{DAY}:OR:4115800")
        cs.add(
            "election",
            id=election,
            polity_id=polity,
            parent_id=parent,
            kind="general",
            date_from=DAY,
            date_to=DAY,
            date_precision="day",
            status="held",
        )
        name(election, "2022 Corvallis general election")
        cs.add("slug", path=f"us/or/corvallis/{DAY}-general", entity_id=election)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=REPORT,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, REPORT), source)

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

        for seat, record in contests.items():
            contest = cs.key("us.contest", f"{DAY}:OR:4115800:{seat}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office" if seat == "mayor" else "body_seats",
                office_id=mayor if seat == "mayor" else None,
                body_id=None if seat == "mayor" else council,
                district_area_id=city,
                magnitude=1,
                round_number=1,
                electoral_system_id=irv,
                default_result_kind="counted",
                term="full",
            )
            heading = (
                "2022 Corvallis mayoral election" if seat == "mayor" else "2022 Corvallis City Council election, Ward 9"
            )
            name(contest, heading)
            cs.add("slug", path=f"us/or/corvallis/{DAY}-general/{seat.replace(':', '-')}", entity_id=contest)
            result = cs.key(f"{SOURCE}.result_set", f"{DAY}:{seat}")
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
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, valid - sum(tally.values())))
            for label in rounds[0]:
                if label == "Write-in":
                    option = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    words = spell(label)
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
