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

SOURCE = "us.boulder_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Boulder County Clerk and Recorder, Elections Division"
PAGE = "https://electionresults.bouldercounty.gov/ElectionResults2023C/Home/RcvContest/1.html"
REPORT = "https://electionresults.bouldercounty.gov/ElectionResults2023C/Home/GetRcvFile/COBMayorRCVShortPDF.pdf"
DAY = date(2023, 11, 7)
METHOD = "Official rounds from Boulder County's ranked-choice results report for the city's first directly elected mayor. Round 1 is the tally of first choices. Nicole Speer and Paul Tweedlie were eliminated together after round 1, and their ballots moved to each voter's next continuing choice. Each candidate's value is a running total; an eliminated candidate shows 0 in the next round. Invalid counts are blank ballots and ballots overvoted at the first choice. Exhausted counts are valid ballots with no continuing choice, including ballots overvoted at a later choice."
TOTALS = ("Continuing Ballots Total", "Blanks", "Exhausted", "Overvotes", "Non Transferable Total", "Threshold")


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".htm")
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    page = raw.cached(SOURCE, PAGE).path.read_text(encoding="utf-8")
    if "Official Results" not in page or "COBMayorRCVShortPDF.pdf" not in page:
        raise ValueError("the county page no longer lists the official mayoral report")
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, REPORT).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    rows: dict[str, list[int]] = {}
    for line in text[text.index("Candidate") :].splitlines():
        match = re.fullmatch(r"\s*([A-Za-z].*?)\s{2,}((?:-?[\d,]+(?:\.\d+%)?\s*)+)", line)
        if match:
            rows[match[1].strip()] = [int(item.replace(",", "")) for item in match[2].split() if not item.endswith("%")]
    names = [label for label in rows if label not in TOTALS]
    continuing = rows["Continuing Ballots Total"]
    dropped = rows["Non Transferable Total"]
    rounds = [{label: rows[label][2 * number] for label in names} for number in range(len(continuing))]
    ballots = {left + right for left, right in zip(continuing, dropped, strict=True)}
    if len(ballots) != 1 or len(names) != 4:
        raise ValueError(f"{names} and {ballots}")
    for number, tally in enumerate(rounds):
        if sum(tally.values()) != continuing[number]:
            raise ValueError(f"round {number + 1} does not sum")
    if 2 * max(rounds[-1].values()) <= continuing[-1]:
        raise ValueError("no majority in the final round")
    record = {"ballots": ballots.pop(), "invalid": rows["Blanks"][0] + rows["Overvotes"][0], "rounds": rounds}
    (PARSED / "official.json").write_text(json.dumps(record))


def emit() -> Path:
    record = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Boulder, Colorado's 2023 ranked-choice mayoral election, from Boulder County's official round report",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "0807850")
        top = cs.unit("us.place", "0807850")
        mayor = cs.key("us.office", "CO:0807850:mayor")
        if {city, top, mayor} & cs.minted:
            raise ValueError("Boulder and its mayor must already exist")
        ((polity,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchall()
        ((parent,),) = conn.execute(
            """select e.id from election e where e.date_from = %s and e.parent_id is null
               and e.polity_id = (select parent_id from polity_parent where polity_id = %s)""",
            [DAY, polity],
        ).fetchall()
        irv = cs.key("us.electoral_system", "boulder:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Boulder ranked-choice voting")
        election = cs.key("us.election", f"{DAY}:CO:0807850")
        cs.add(
            "election",
            id=election,
            polity_id=polity,
            parent_id=parent,
            kind="local",
            date_from=DAY,
            date_to=DAY,
            date_precision="day",
            status="held",
        )
        name(election, "2023 Boulder local election")
        cs.add("slug", path=f"us/co/boulder/{DAY}-local", entity_id=election)
        contest = cs.key("us.contest", f"{DAY}:CO:0807850:mayor")
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=mayor,
            body_id=None,
            district_area_id=city,
            magnitude=1,
            round_number=1,
            electoral_system_id=irv,
            default_result_kind="counted",
            term="full",
        )
        name(contest, "2023 Boulder mayoral election")
        cs.add("slug", path=f"us/co/boulder/{DAY}-local/mayor", entity_id=contest)

        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s""",
            [polity],
        ).fetchall():
            words = spell(text)
            people.setdefault((words[0], words[-1]), set()).add(person)
        rounds = record["rounds"]
        options: dict[str, UUID] = {}
        for label in rounds[0]:
            words = spell(label)
            matches = people.get((words[0], words[-1]), set())
            if len(matches) == 1:
                (person,) = matches
            else:
                person = cs.key(f"{SOURCE}.person", f"{words[0]}-{words[-1]}")
                cs.add("person", id=person)
                name(person, label)
            options[label] = cs.key("us.option", f"{contest}:{person}")
            cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=label)
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                person_id=person,
                option_id=options[label],
                role="head",
                ballot_name=label,
                member_of_party_id=None,
                is_independent=False,
                is_incumbent=None,
            )

        source = cs.source(
            PUBLISHER, "2023 Coordinated Election: City of Boulder Mayoral Candidates, RCV results report"
        )
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title="2023 Coordinated Election: City of Boulder Mayoral Candidates, RCV results report",
            url=REPORT,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, REPORT), source)
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        result = cs.key(f"{SOURCE}.result_set", "2023:mayor")
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
        cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
        valid = sum(rounds[0].values())
        if record["ballots"] - record["invalid"] != valid:
            raise ValueError("ballots, invalid, and valid disagree")
        values = [(None, 3, 0, record["ballots"]), (None, 5, 0, record["invalid"]), (None, 4, 1, valid)]
        for number, tally in enumerate(rounds, 1):
            values.append((None, 21, number, valid - sum(tally.values())))
            values.extend(
                (options[label], 1, number, votes)
                for label, votes in tally.items()
                if not any(earlier[label] == 0 for earlier in rounds[: number - 1])
            )
        for option, measure, number, votes in values:
            cs.add(
                "result_value",
                result_set_id=result,
                unit_id=top,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=number,
                flags=0,
                value=votes,
            )
        final = rounds[-1]
        winner = max(final, key=lambda label: final[label])
        for label, option in options.items():
            for outcome in ("won", "elected") if label == winner else ("lost" if final[label] else "eliminated",):
                cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
