import itertools
import json
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.portland_me_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Portland, Maine City Clerk"
ASSET = "https://content.civicplus.com/api/assets/me-portland/{}"
REPORTS = {
    "2022-11-08": (
        "8424fb91-852d-4aca-8f0b-21916b135ce5",
        "November 8, 2022 General Municipal & Gubernatorial Election",
    ),
    "2023-11-07": ("3710c940-6b78-4948-901c-49ea624d6c4b", "November 7, 2023 General Municipal Election"),
    "2024-11-05": ("eed038bf-1dd9-4e8c-ad1a-2c0478e5e839", "November 5, 2024 (Presidential & General Municipal)"),
}
SEATS = {
    "School Board District 3": "school-board:district-3",
    "Mayor": "mayor",
    "City Council District 2": "council:district-2",
    "City Council At Large": "council:at-large",
}
METHOD = "Transcribed from the city clerk's official round-by-round table and ballot accounting. Overvotes and undervotes (voters who skipped the race) are counted as invalid; ballots that stop counting in later rounds are counted as exhausted. Write-ins are grouped into a single line."


def fetch() -> None:
    for asset, _ in REPORTS.values():
        raw.fetch(SOURCE, ASSET.format(asset), suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests = {}
    for day, (asset, _) in REPORTS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, ASSET.format(asset)).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for block in re.split(r"(?m)^\f?(?=Contest )", text.replace("\f", "\n"))[1:]:
            title = block.splitlines()[0].removeprefix("Contest").strip()
            key = f"{day}:{SEATS[title]}"
            if key in contests:
                continue
            table = block[block.index("\nCandidate") + 1 :]
            rounds: list[dict[str, int]] = []
            for line in table.splitlines()[1:]:
                match = re.fullmatch(
                    r"(\S.*?)\s{2,}((?:[\d,]+|-)(?:\s+(?:[\d,]+|-))*)(?:\s+[\d.]+%)?(?:\s{2,}.*)?", line
                )
                if match is None:
                    if rounds:
                        break
                    continue
                label = match[1].strip()
                if label.startswith(("Total votes", "Inactive")):
                    continue
                if "," in label:
                    last, first = label.split(", ", 1)
                    label = f"{first} {last}"
                label = {"Write-ins": "Write-in"}.get(label, label)
                cells = [0 if cell == "-" else int(cell.replace(",", "")) for cell in match[2].split()]
                while len(rounds) < len(cells):
                    rounds.append({})
                for number, votes in enumerate(cells):
                    rounds[number][label] = votes
            labels = list(rounds[0])
            rounds = [{label: tally.get(label, 0) for label in labels} for tally in rounds]
            accounting = {
                label.casefold(): int(value.replace(",", ""))
                for label, value in re.findall(
                    r"(Valid votes|Overvotes|Undervotes|Cast Vote Records)[^\n\d]*?([\d,]+)", block
                )
            }
            for value in re.findall(r"\[Blanks = voter skipped race\]\s+([\d,]+)", block):
                accounting["undervotes"] = int(value.replace(",", ""))
            valid = accounting["valid votes"]
            ballots = accounting["cast vote records"]
            invalid = accounting["overvotes"] + accounting["undervotes"]
            if sum(rounds[0].values()) != valid or valid + invalid != ballots:
                raise ValueError(f"{key}: round 1, overvotes, and undervotes do not add up to the cast vote records")
            for earlier, later in itertools.pairwise(rounds):
                if sum(later.values()) > sum(earlier.values()):
                    raise ValueError(f"{key}: continuing votes grew")
                if any(later[label] < votes for label, votes in earlier.items() if later[label]):
                    raise ValueError(f"{key}: a continuing candidate lost votes")
                if sum(1 for label in earlier if earlier[label] and not later[label]) == 0:
                    raise ValueError(f"{key}: a round eliminated nobody")
            final = rounds[-1]
            winner = re.search(r"Winner\s+(.+?), (.+?) \(([\d,]+) votes", block)
            if winner is None or final.get(f"{winner[2]} {winner[1]}") != int(winner[3].replace(",", "")):
                raise ValueError(f"{key}: winner line disagrees with the final round")
            if 2 * max(final.values()) <= sum(final.values()):
                raise ValueError(f"{key}: no final-round majority")
            contests[key] = {"ballots": ballots, "invalid": invalid, "rounds": rounds}
    if len(contests) != len(SEATS):
        raise ValueError(f"expected {len(SEATS)} tabulations, not {len(contests)}")
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Portland, Maine's ranked-choice races that went past round 1 in 2022, 2023, and 2024 (school "
            "board District 3, mayor, and the council's District 2 and at-large seats), from the city clerk's "
            "official results",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        city = cs.area("us.place", "2360545")
        if city in cs.minted:
            raise ValueError("Portland, Maine must already exist")
        ((polity,),) = conn.execute("select polity_id from polity_territory where area_id = %s", [city]).fetchall()
        ((mayor,),) = conn.execute(
            """select distinct k.office_id from contest k join election e on e.id = k.election_id
               where e.polity_id = %s and k.kind = 'office'""",
            [polity],
        ).fetchall()
        ((council,),) = conn.execute(
            """select distinct k.body_id from contest k join election e on e.id = k.election_id
               where e.polity_id = %s and k.kind = 'body_seats'""",
            [polity],
        ).fetchall()
        ((state,),) = conn.execute(
            """select p.polity_id from election e join election p on p.id = e.parent_id
               where e.polity_id = %s and e.date_from = '2020-11-03'""",
            [polity],
        ).fetchall()
        school = cs.key("us.body", "ME:2360545:school-board")
        cs.add("body", id=school, polity_id=polity, kind="council")
        name(school, "Portland Board of Public Education")
        irv = cs.key("us.electoral_system", "portland-me:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Portland, Maine ranked-choice voting")

        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id where e.polity_id = %s""",
            [polity],
        ).fetchall():
            words = spell(text)
            people.setdefault((words[0], words[-1]), set()).add(person)

        done: set[object] = set()
        for key, record in contests.items():
            day = date.fromisoformat(key[:10])
            seat = key[11:]
            asset, title = REPORTS[key[:10]]
            election = cs.key("us.election", f"{day}:ME:2360545")
            if election in cs.minted and election not in done:
                done.add(election)
                ((parent,),) = conn.execute(
                    """select e.id from election e join name n on n.entity_id = e.id
                       where e.date_from = %s and e.polity_id = %s and n.text = %s""",
                    [day, state, f"{day.year} Maine general election"],
                ).fetchall()
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} Portland local election")
                cs.add("slug", path=f"us/me/portland/{day}-local", entity_id=election)
                source = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=ASSET.format(asset),
                    license="Public record",
                )
                cs.artifact(raw.cached(SOURCE, ASSET.format(asset)), source)
            area, top = city, cs.unit("us.place", "2360545")
            if seat.endswith("district-2"):
                code = "23:2360545:council:district-2"
                area, top = cs.area("us.local_district", code), cs.unit("us.local_district", code)
                if {area, top} & cs.minted:
                    raise ValueError(f"{code} must already exist")
            if seat.startswith("school-board"):
                code = "23:2360545:school-board:district-3"
                area, top = cs.area("us.local_district", code), cs.unit("us.local_district", code)
                cs.add("area", id=area, scheme_code="us.local_district")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=city, valid_from=None)
                name(area, "Portland Board of Public Education, District 3")
                cs.add(
                    "reporting_unit",
                    id=top,
                    polity_id=polity,
                    scheme_code="us.local_district",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                )
            heading = {
                "mayor": f"{day.year} Portland mayoral election",
                "council:district-2": f"{day.year} Portland City Council election, District 2",
                "council:at-large": f"{day.year} Portland City Council election, At-Large",
                "school-board:district-3": f"{day.year} Portland Board of Public Education election, District 3",
            }[seat]
            contest = cs.key("us.contest", f"{day}:ME:2360545:{seat}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office" if seat == "mayor" else "body_seats",
                office_id=mayor if seat == "mayor" else None,
                body_id=None if seat == "mayor" else school if seat.startswith("school") else council,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=irv,
                default_result_kind="counted",
                term="full",
            )
            name(contest, heading)
            cs.add("slug", path=f"us/me/portland/{day}-local/{slug(seat)}", entity_id=contest)
            rounds = record["rounds"]
            options: dict[str, UUID] = {}
            for label in rounds[0]:
                if label == "Write-in":
                    options[label] = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add("contest_option", id=options[label], contest_id=contest, kind="write_in", ballot_label=label)
                    continue
                words = spell(label)
                matches = people.get((words[0], words[-1]), set())
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(f"{words[0]} {words[-1]}"))
                    if person not in done:
                        done.add(person)
                        cs.add("person", id=person)
                        name(person, label)
                    people[words[0], words[-1]] = {person}
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
            result = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=cs.source(PUBLISHER, title),
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
            valid = record["ballots"] - record["invalid"]
            values = [(None, 3, 0, record["ballots"]), (None, 5, 0, record["invalid"]), (None, 4, 1, valid)]
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, valid - sum(tally.values())))
                values.extend(
                    (options[label], 1, number, votes)
                    for label, votes in tally.items()
                    if number == 1 or rounds[number - 2][label]
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
